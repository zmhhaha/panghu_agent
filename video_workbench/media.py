"""Bounded static-frame movies from an immutable, explicitly adopted snapshot.

Caption estimation is deliberately labelled as an estimate. No speech recognizer,
network model, browser script, or implicit audio-provider retry is involved here.
"""
from __future__ import annotations

import base64
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import threading
import time
from uuid import uuid4
import wave

from agent_service.contracts import ServiceError
from agent_service.profiles.video import validate_scene_source
from .speech import inspect_wav

WIDTH, HEIGHT, FPS, AUDIO_RATE = 1280, 720, 24, 24000
MAX_SCENES = 24
MAX_PROJECT_SECONDS = 1800
MAX_AUDIO_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOT_AUDIO_BYTES = 128 * 1024 * 1024
MAX_VIDEO_BYTES = 256 * 1024 * 1024
RENDER_TIMEOUT = 900
_DIGEST = re.compile(r'[0-9a-f]{64}')
_CSP = ("sandbox; default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; "
        "img-src data:; font-src data:; connect-src 'none'; object-src 'none'; "
        "frame-src 'none'; base-uri 'none'; form-action 'none'")


def _require(value, code='invalid_media_snapshot', status=422):
    if not value:
        raise ServiceError(code, status)


def _number(value, minimum, maximum, code='invalid_media_timing'):
    _require(type(value) in (float, int) and math.isfinite(value)
             and minimum <= value <= maximum, code)
    return float(value)


def _text(value, maximum=10000, *, empty=False):
    _require(type(value) is str and len(value) <= maximum and (empty or value.strip())
             and not any((ord(c) < 32 and c not in '\t\n\r') or ord(c) == 127
                         or 0xD800 <= ord(c) <= 0xDFFF for c in value))
    return value


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def scene_timing(configured_seconds, audio_seconds, leading=0, trailing=0):
    """Preserve all speech and explicit padding; never crop to the old duration."""
    configured = _number(configured_seconds, 1, 120)
    audio = _number(audio_seconds, 0, 300)
    lead, tail = _number(leading, 0, 10), _number(trailing, 0, 10)
    duration = max(configured, lead + audio + tail)
    return dict(configured_seconds=configured, audio_seconds=audio,
                leading_seconds=lead, trailing_seconds=tail,
                minimum_seconds=lead + audio + tail, duration_seconds=duration,
                difference_seconds=duration - configured,
                extends_configured=duration > configured)


def validate_captions(cues, scene_seconds, audio_sha256, *, alignment='manual',
                      leading_seconds=0, audio_seconds=None):
    """Validate ordered scene-relative cues and bind them to the exact audio."""
    duration = _number(scene_seconds, 0.001, 320)
    lead = _number(leading_seconds, 0, 10)
    end_limit = duration
    if audio_seconds is not None:
        end_limit = min(duration, lead + _number(audio_seconds, 0, 300))
    _require(type(audio_sha256) is str and _DIGEST.fullmatch(audio_sha256), 'invalid_captions')
    _require(alignment in {'manual', 'estimated'} and type(cues) is list and len(cues) <= 500,
             'invalid_captions')
    previous, count, result = lead, 0, []
    for cue in cues:
        _require(type(cue) is dict and set(cue) == {'start', 'end', 'text'}, 'invalid_captions')
        start = _number(cue['start'], lead, end_limit, 'invalid_captions')
        end = _number(cue['end'], lead, end_limit, 'invalid_captions')
        text = _text(cue['text'], 500).strip()
        _require(start >= previous and end > start and round(end * 1000) > round(start * 1000)
                 and '\n\n' not in text.replace('\r\n', '\n').replace('\r', '\n'), 'invalid_captions')
        count += len(text)
        _require(count <= 10000, 'invalid_captions')
        previous = end
        result.append(dict(start=start, end=end, text=text))
    return dict(audio_sha256=audio_sha256, alignment=alignment,
                requires_review=alignment == 'estimated', cues=result)


def estimate_captions(text, audio_seconds, leading=0):
    """Split text and distribute time; this is NOT forced alignment."""
    _text(text)
    duration = _number(audio_seconds, 0.001, 300)
    lead = _number(leading, 0, 10)
    parts = [p.strip() for p in re.findall(r'[^。！？!?；;\n]+[。！？!?；;]?|[。！？!?；;]', text)
             if p.strip()]
    units = []
    for part in parts:
        while len(part) > 42:
            cut = max(part.rfind('，', 12, 43), part.rfind(' ', 12, 43),
                      part.rfind(',', 12, 43))
            cut = cut + 1 if cut >= 12 else 42
            units.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            units.append(part)
    _require(bool(units), 'invalid_captions')
    # Avoid more than 500 cues and timestamps too close to express in SRT.
    while len(units) > min(500, max(1, int(duration * 1000))):
        units[-2:] = [units[-2] + units[-1]]
    weights = [max(1, len(re.sub(r'\s', '', item))) for item in units]
    total, elapsed, cues = sum(weights), 0, []
    for part, weight in zip(units, weights):
        start = lead + duration * elapsed / total
        elapsed += weight
        end = lead + duration if elapsed == total else lead + duration * elapsed / total
        cues.append(dict(start=start, end=end, text=part))
    return dict(alignment='estimated', requires_review=True, cues=cues)


def _srt_time(seconds):
    milliseconds = round(seconds * 1000)
    hours, rest = divmod(milliseconds, 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, ms = divmod(rest, 1000)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}'


def _srt(cues, offset=0):
    lines = []
    for index, cue in enumerate(cues, 1):
        # Escape SRT's HTML-like markup; narration is content, not styling.
        text = html.escape(cue['text'], quote=False).replace('\r\n', '\n').replace('\r', '\n')
        lines.append(f'{index}\n{_srt_time(offset + cue["start"])} --> '
                     f'{_srt_time(offset + cue["end"])}\n{text}\n')
    return '\n'.join(lines) + ('\n' if lines else '')


def subtitles_srt(snapshot):
    verify_snapshot(snapshot)
    cues = []
    for scene in snapshot['scenes']:
        for cue in scene['captions']['cues']:
            cues.append(dict(start=scene['start_seconds'] + cue['start'],
                             end=scene['start_seconds'] + cue['end'], text=cue['text']))
    return _srt(cues)


def freeze_snapshot(project_id, revision, title, scenes, *, silent=False, subtitles=True):
    """Freeze current adopted bytes into a JSON-safe render document.

    Each scene supplies visual={id,format,source} and, unless explicitly silent,
    audio={id,data: bytes,sha256,duration_seconds,input_sha256?}. The caller must
    obtain both through owner-scoped store reads and refuse stale adoption.
    """
    _text(project_id, 100)
    _text(title, 300)
    _require(type(revision) is int and revision >= 1 and type(silent) is bool
             and type(subtitles) is bool)
    _require(type(scenes) is list and 1 <= len(scenes) <= MAX_SCENES)
    frozen, elapsed, audio_bytes, ids = [], 0.0, 0, set()
    for item in scenes:
        _require(type(item) is dict)
        scene_id = _text(item.get('id'), 100)
        _require(scene_id not in ids)
        ids.add(scene_id)
        narration = _text(item.get('narration', ''), empty=True)
        visual = item.get('visual')
        _require(type(visual) is dict)
        visual_id = _text(visual.get('id'), 100)
        source = validate_scene_source(visual.get('source'), visual.get('format'))
        frozen_visual = dict(id=visual_id, format=visual['format'], source=source,
                             source_sha256=_sha(source.encode('utf-8')))
        audio = None
        if not silent:
            candidate = item.get('audio')
            _require(type(candidate) is dict and not candidate.get('stale'), 'missing_or_stale_audio', 409)
            data = candidate.get('data')
            _require(type(data) is bytes and 0 < len(data) <= MAX_AUDIO_BYTES)
            info = inspect_wav(data)
            actual_digest = _sha(data)
            _require(candidate.get('sha256') == actual_digest)
            declared = _number(candidate.get('duration_seconds'), 0, 300)
            _require(abs(declared - info.duration_seconds) <= 1 / info.sample_rate)
            audio_bytes += len(data)
            _require(audio_bytes <= MAX_SNAPSHOT_AUDIO_BYTES, 'media_project_too_large', 413)
            audio = dict(id=_text(candidate.get('id'), 100), sha256=actual_digest,
                         duration_seconds=info.duration_seconds, sample_rate=info.sample_rate,
                         channels=info.channels, sample_width=info.sample_width,
                         data_base64=base64.b64encode(data).decode('ascii'))
            if candidate.get('input_sha256') is not None:
                _require(type(candidate['input_sha256']) is str
                         and _DIGEST.fullmatch(candidate['input_sha256']))
                audio['input_sha256'] = candidate['input_sha256']
        timing = scene_timing(item.get('duration_seconds'), audio['duration_seconds'] if audio else 0,
                              item.get('leading_seconds', 0), item.get('trailing_seconds', 0))
        # 24fps and 24kHz share an exact 1000-sample frame. One master PCM track
        # avoids independent AAC encoder padding accumulating between scenes.
        timing['duration_seconds'] = math.ceil(timing['duration_seconds'] * FPS) / FPS
        timing['difference_seconds'] = timing['duration_seconds'] - timing['configured_seconds']
        timing['extends_configured'] = timing['difference_seconds'] > 0
        captions = dict(audio_sha256=audio['sha256'] if audio else None,
                        alignment='none', requires_review=False, cues=[])
        if audio and subtitles:
            supplied = item.get('captions')
            if supplied is None:
                supplied = estimate_captions(narration, audio['duration_seconds'], timing['leading_seconds'])
                supplied['audio_sha256'] = audio['sha256']
            _require(type(supplied) is dict and supplied.get('audio_sha256') == audio['sha256'],
                     'stale_captions', 409)
            captions = validate_captions(supplied.get('cues'), timing['duration_seconds'], audio['sha256'],
                                         alignment=supplied.get('alignment', 'manual'),
                                         leading_seconds=timing['leading_seconds'],
                                         audio_seconds=audio['duration_seconds'])
        duration = timing['duration_seconds']
        frozen.append(dict(id=scene_id, title=_text(item.get('title', ''), 300, empty=True),
                           narration=narration, visual=frozen_visual, audio=audio,
                           timing=timing, captions=captions, start_seconds=elapsed,
                           end_seconds=elapsed + duration))
        elapsed += duration
        _require(elapsed <= MAX_PROJECT_SECONDS, 'media_project_too_long', 422)
    result = dict(schema_version='1', project_id=project_id, project_revision=revision,
                  title=title, silent=silent, subtitles=subtitles and not silent,
                  visual_mode='static', static_renderer=static_renderer(), width=WIDTH, height=HEIGHT, fps=FPS,
                  duration_seconds=elapsed, scenes=frozen)
    result['sha256'] = _sha(_canonical(result))
    return result


def verify_snapshot(snapshot):
    _require(type(snapshot) is dict and snapshot.get('schema_version') == '1')
    expected = snapshot.get('sha256')
    _require(type(expected) is str and _DIGEST.fullmatch(expected))
    unsigned = {key: value for key, value in snapshot.items() if key != 'sha256'}
    try:
        _require(_sha(_canonical(unsigned)) == expected, 'media_snapshot_corrupt', 409)
    except (ValueError, TypeError, UnicodeError):
        raise ServiceError('media_snapshot_corrupt', 409) from None
    _require((snapshot.get('width'), snapshot.get('height'), snapshot.get('fps')) == (WIDTH, HEIGHT, FPS))
    _require(type(snapshot.get('silent')) is bool and type(snapshot.get('subtitles')) is bool
             and snapshot.get('visual_mode') == 'static'
             and snapshot.get('static_renderer') in {'chromium', 'weasyprint'})
    _require(type(snapshot.get('scenes')) is list and 1 <= len(snapshot['scenes']) <= MAX_SCENES)
    _number(snapshot.get('duration_seconds'), 1, MAX_PROJECT_SECONDS)
    return snapshot


def _cancel(event):
    if event is not None and event.is_set():
        raise ServiceError('media_cancelled', 409)


def _child_env(directory):
    # Media subprocesses do not inherit API tokens, cloud credentials or proxy
    # settings. Only runtime/library/font and OS bootstrap settings are retained.
    keys = ('PATH', 'LANG', 'LC_ALL', 'TZ', 'LD_LIBRARY_PATH', 'FONTCONFIG_PATH',
            'SYSTEMROOT', 'WINDIR')
    result = {key: os.environ[key] for key in keys if key in os.environ}
    result.update(XDG_CACHE_HOME=str(Path(directory) / '.cache'),
                  TMPDIR=str(directory), TEMP=str(directory), TMP=str(directory))
    return result


def _run(args, *, cwd, timeout, cancel_event=None):
    """No shell; terminate the entire Linux child process group on abort."""
    _cancel(cancel_event)
    started = time.monotonic()
    try:
        process = subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=_child_env(cwd),
                                   start_new_session=os.name != 'nt')
    except OSError:
        raise ServiceError('media_tool_unavailable', 503) from None
    try:
        while True:
            _cancel(cancel_event)
            if time.monotonic() - started > timeout:
                raise ServiceError('media_render_timeout', 504)
            try:
                stdout, _ = process.communicate(timeout=0.2)
                if process.returncode != 0:
                    # Tool output may include filesystem paths or narration.
                    raise ServiceError('media_tool_failed', 502)
                _require(len(stdout) <= 2 * 1024 * 1024, 'media_tool_failed', 502)
                return stdout
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            if os.name != 'nt':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.communicate(timeout=5)


def _binary(name, configured=None):
    result = shutil.which(configured or name)
    if not result:
        raise ServiceError('media_tool_unavailable', 503)
    return str(result)


def static_renderer():
    selected = os.environ.get('VIDEO_STATIC_RENDERER', 'chromium')
    _require(selected in {'chromium', 'weasyprint'}, 'invalid_static_renderer', 503)
    return selected


def available():
    """Installed dependencies only: each selected renderer needs a live probe."""
    try:
        selected = static_renderer()
    except ServiceError:
        return False
    browser = (os.environ.get('VIDEO_CHROMIUM_BIN') or shutil.which('chromium')
               or shutil.which('chromium-browser') or shutil.which('google-chrome'))
    static = ((browser and shutil.which(browser)) if selected == 'chromium' else
              (importlib.util.find_spec('weasyprint') is not None
               and shutil.which(os.environ.get('VIDEO_PDFTOPPM_BIN') or 'pdftoppm')))
    return bool(static and shutil.which(os.environ.get('VIDEO_FFMPEG_BIN') or 'ffmpeg')
                and shutil.which(os.environ.get('VIDEO_FFPROBE_BIN') or 'ffprobe'))


def screenshot_scene(source, format, destination, *, cancel_event=None, chromium=None):
    if static_renderer() == 'weasyprint':
        return screenshot_weasyprint(source, format, destination, cancel_event=cancel_event)
    return screenshot_chromium(source, format, destination, cancel_event=cancel_event, chromium=chromium)


def screenshot_chromium(source, format, destination, *, cancel_event=None, chromium=None):
    """Screenshot one vetted static page with Chromium's sandbox enabled."""
    validate_scene_source(source, format)
    if format == 'svg':
        page = ('<!doctype html><html><head><meta charset="utf-8"><style>'
                'html,body{margin:0;width:100%;height:100%;background:#0e1420;overflow:hidden}'
                'svg{display:block;width:100%;height:100%;object-fit:contain}'
                '</style></head><body>' + source + '</body></html>')
    else:
        page = source
    content = page.encode('utf-8')
    route = '/' + uuid4().hex

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != route:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Content-Security-Policy', _CSP)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_):
            pass

    destination = Path(destination).resolve()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    browser = (chromium or os.environ.get('VIDEO_CHROMIUM_BIN') or shutil.which('chromium')
               or shutil.which('chromium-browser') or shutil.which('google-chrome'))
    try:
        browser = _binary('chromium', browser)
        profile = destination.parent / ('browser-' + uuid4().hex)
        args = [browser, '--headless', '--disable-gpu', '--no-first-run',
                '--no-default-browser-check', '--disable-background-networking',
                '--disable-component-update', '--disable-sync', '--disable-extensions',
                '--metrics-recording-only', '--hide-scrollbars', '--force-device-scale-factor=1',
                '--blink-settings=scriptEnabled=false', '--host-resolver-rules=MAP * ~NOTFOUND',
                '--proxy-server=http://127.0.0.1:9', f'--proxy-bypass-list=<-loopback>;127.0.0.1:{port}',
                f'--user-data-dir={profile}', f'--window-size={WIDTH},{HEIGHT}',
                '--virtual-time-budget=1000', f'--screenshot={destination}',
                f'http://127.0.0.1:{port}{route}']
        _run(args, cwd=destination.parent, timeout=35, cancel_event=cancel_event)
        _png(destination)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def screenshot_weasyprint(source, format, destination, *, cancel_event=None):
    """Explicit no-JavaScript HTML/SVG -> one-page PDF -> exact-size PNG.

    This engine has its own CSS support and is not represented as Chromium's
    pixel-identical output. All URL fetching is denied inside the child process.
    """
    validate_scene_source(source, format)
    destination = Path(destination).resolve()
    directory = destination.parent
    name = 'static-' + uuid4().hex
    page = directory / (name + '.html')
    pdf = directory / (name + '.pdf')
    if format == 'svg':
        body = '<div class="video-workbench-static-svg" style="width:1280px;height:720px">' + source + '</div>'
    else:
        body = source
    # An inline fixed container clips the same static viewport to one PDF page.
    # Vetted author CSS cannot add @page rules or execute scripts/resource loads.
    content = ('<!doctype html><html><head><meta charset="utf-8"></head><body>'
               '<div style="position:fixed!important;left:0!important;top:0!important;'
               'width:1280px!important;height:720px!important;overflow:hidden!important">'
               + body + '</div></body></html>')
    page.write_text(content, encoding='utf-8')
    worker = Path(__file__).with_name('static_render_worker.py')
    _require(worker.is_file(), 'media_tool_unavailable', 503)
    try:
        _run([os.sys.executable, '-I', str(worker), str(page), str(pdf)],
             cwd=directory, timeout=35, cancel_event=cancel_event)
        _require(pdf.is_file() and not pdf.is_symlink() and 0 < pdf.stat().st_size <= 32 * 1024 * 1024,
                 'media_screenshot_invalid', 502)
        converter = _binary('pdftoppm', os.environ.get('VIDEO_PDFTOPPM_BIN'))
        _run([converter, '-f', '1', '-singlefile', '-png', '-scale-to-x', str(WIDTH),
              '-scale-to-y', str(HEIGHT), str(pdf), str(destination.with_suffix(''))],
             cwd=directory, timeout=20, cancel_event=cancel_event)
        _png(destination)
    finally:
        page.unlink(missing_ok=True)
        pdf.unlink(missing_ok=True)


def _png(path):
    _require(path.is_file() and not path.is_symlink() and 24 <= path.stat().st_size <= 16 * 1024 * 1024,
             'media_screenshot_invalid', 502)
    with path.open('rb') as stream:
        header = stream.read(24)
    _require(header[:8] == b'\x89PNG\r\n\x1a\n'
             and struct.unpack('>II', header[16:24]) == (WIDTH, HEIGHT), 'media_screenshot_invalid', 502)


def render_snapshot(snapshot, output_dir, *, cancel_event=None, screenshotter=None):
    """Render only the frozen bytes. Caller owns and cleans the temporary dir.

    A screenshotter can be injected by local tests; production uses the sandboxed
    static Chromium path above. The result contains a verified MP4 path, not bytes.
    """
    verify_snapshot(snapshot)
    _cancel(cancel_event)
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    _require(not root.is_symlink() and not any(root.iterdir()), 'media_output_not_empty', 409)
    work = root / 'assets'
    work.mkdir()
    ffmpeg = _binary('ffmpeg', os.environ.get('VIDEO_FFMPEG_BIN'))
    ffprobe = _binary('ffprobe', os.environ.get('VIDEO_FFPROBE_BIN'))
    renderer = snapshot['static_renderer'] if screenshotter is None else 'test-injected'
    screenshotter = screenshotter or (screenshot_weasyprint if snapshot['static_renderer'] == 'weasyprint'
                                       else screenshot_chromium)
    started, total_samples, audio_bytes = time.monotonic(), 0, 0
    master = None
    common = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
              '-threads', '2', '-filter_threads', '2', '-filter_complex_threads', '2']

    def run(args, limit=180):
        remaining = RENDER_TIMEOUT - (time.monotonic() - started)
        _require(remaining > 0, 'media_render_timeout', 504)
        return _run(args, cwd=work, timeout=min(limit, remaining), cancel_event=cancel_event)

    try:
        if not snapshot['silent']:
            master = wave.open(str(work / 'master.wav'), 'wb')
            master.setparams((1, 2, AUDIO_RATE, 0, 'NONE', 'not compressed'))
        concat = []
        for index, scene in enumerate(snapshot['scenes']):
            _cancel(cancel_event)
            visual = scene['visual']
            validate_scene_source(visual['source'], visual['format'])
            _require(_sha(visual['source'].encode('utf-8')) == visual['source_sha256'])
            name = f'scene-{index:03d}'
            png = work / (name + '.png')
            screenshotter(visual['source'], visual['format'], png, cancel_event=cancel_event)
            _png(png)
            duration = scene['timing']['duration_seconds']
            _number(duration, 1, 320)
            samples = round(duration * AUDIO_RATE)
            _require(abs(samples / AUDIO_RATE - duration) < 1e-8)
            filters = []
            if snapshot['subtitles'] and scene['captions']['cues']:
                (work / (name + '.srt')).write_text(_srt(scene['captions']['cues']), encoding='utf-8')
                filters.append(f"subtitles={name}.srt:force_style='FontName=Noto Sans CJK SC,FontSize=22,Outline=1,MarginV=18'")
            args = common + ['-loop', '1', '-framerate', str(FPS), '-i', png.name,
                             '-map', '0:v:0', '-an', '-t', f'{duration:.9f}']
            if filters:
                args += ['-vf', ','.join(filters)]
            args += ['-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'stillimage',
                     '-threads', '2', '-pix_fmt', 'yuv420p', '-r', str(FPS), name + '.mp4']
            run(args)
            concat.append(f"file '{name}.mp4'")
            if master:
                audio = scene['audio']
                encoded = audio['data_base64']
                _require(type(encoded) is str and len(encoded) <= (MAX_AUDIO_BYTES + 2) // 3 * 4)
                try:
                    data = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError):
                    raise ServiceError('media_snapshot_corrupt', 409) from None
                audio_bytes += len(data)
                _require(audio_bytes <= MAX_SNAPSHOT_AUDIO_BYTES)
                info = inspect_wav(data)
                _require(_sha(data) == audio['sha256']
                         and abs(info.duration_seconds - audio['duration_seconds']) < 1e-8)
                (work / (name + '-input.wav')).write_bytes(data)
                delay = round(scene['timing']['leading_seconds'] * AUDIO_RATE)
                run(common + ['-i', name + '-input.wav', '-map', '0:a:0', '-vn',
                              '-af', f'aresample={AUDIO_RATE},adelay={delay}S:all=1,apad=whole_len={samples}',
                              '-t', f'{duration:.9f}', '-ac', '1', '-ar', str(AUDIO_RATE),
                              '-c:a', 'pcm_s16le', name + '-padded.wav'])
                with wave.open(str(work / (name + '-padded.wav')), 'rb') as segment:
                    _require(segment.getnframes() == samples and segment.getnchannels() == 1
                             and segment.getsampwidth() == 2 and segment.getframerate() == AUDIO_RATE,
                             'media_audio_timing_failed', 502)
                    while chunk := segment.readframes(65536):
                        master.writeframesraw(chunk)
                total_samples += samples
        if master:
            master.close()
            master = None
            _require(abs(total_samples / AUDIO_RATE - snapshot['duration_seconds']) < 1e-7)
        (work / 'concat.txt').write_text('\n'.join(concat) + '\n', encoding='ascii')
        args = common + ['-protocol_whitelist', 'file,pipe', '-f', 'concat', '-safe', '1', '-i', 'concat.txt']
        if snapshot['silent']:
            args += ['-map', '0:v:0', '-c:v', 'copy', '-an']
        else:
            args += ['-i', 'master.wav', '-map', '0:v:0', '-map', '1:a:0',
                     '-c:v', 'copy', '-c:a', 'aac', '-b:a', '96k', '-ar', str(AUDIO_RATE), '-ac', '1']
        args += ['-t', f'{snapshot["duration_seconds"]:.9f}', '-movflags', '+faststart', 'combined.mp4']
        run(args, 120)
        combined = work / 'combined.mp4'
        _require(combined.is_file() and 0 < combined.stat().st_size <= MAX_VIDEO_BYTES,
                 'media_output_too_large', 413)
        probe_raw = run([ffprobe, '-v', 'error', '-show_entries',
                         'format=duration:stream=codec_type,codec_name,width,height,duration,sample_rate,channels',
                         '-of', 'json', 'combined.mp4'], 15)
        try:
            probe = json.loads(probe_raw)
            videos = [s for s in probe['streams'] if s['codec_type'] == 'video']
            audios = [s for s in probe['streams'] if s['codec_type'] == 'audio']
            duration = float(probe['format']['duration'])
            _require(len(videos) == 1 and videos[0]['codec_name'] == 'h264'
                     and (videos[0]['width'], videos[0]['height']) == (WIDTH, HEIGHT)
                     and math.isfinite(duration)
                     and abs(duration - snapshot['duration_seconds']) <= 0.15,
                     'media_output_invalid', 502)
            if snapshot['silent']:
                _require(not audios, 'media_output_invalid', 502)
            else:
                _require(len(audios) == 1 and audios[0]['codec_name'] == 'aac'
                         and audios[0]['channels'] == 1 and int(audios[0]['sample_rate']) == AUDIO_RATE
                         and float(audios[0]['duration']) >= snapshot['duration_seconds'] - 0.05,
                         'media_output_invalid', 502)
        except (ValueError, TypeError, KeyError, IndexError):
            raise ServiceError('media_output_invalid', 502) from None
        _cancel(cancel_event)
        destination = root / 'video.mp4'
        combined.replace(destination)
        file_hash = hashlib.sha256()
        with destination.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                file_hash.update(chunk)
        result = dict(path=str(destination), sha256=file_hash.hexdigest(),
                      bytes=destination.stat().st_size, duration_seconds=duration,
                      expected_duration_seconds=snapshot['duration_seconds'],
                      width=WIDTH, height=HEIGHT, fps=FPS, video_codec='h264',
                      audio_codec=None if snapshot['silent'] else 'aac', silent=snapshot['silent'],
                      subtitles=snapshot['subtitles'], visual_mode='static',
                      static_renderer=renderer,
                      snapshot_sha256=snapshot['sha256'], elapsed_seconds=time.monotonic() - started)
        (root / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return result
    finally:
        if master is not None:
            master.close()
