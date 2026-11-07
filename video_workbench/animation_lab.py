"""Local developer animation comparison and annotation tool.

Run with explicit, separate data and existing renderer asset directories:
  python -m video_workbench.animation_lab --data-dir DIR --assets-dir DIR

The server only binds loopback. It neither imports the production application
nor calls Agent/TTS, changes project ownership, or publishes animation versions.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import secrets
from urllib.parse import parse_qs, unquote, urlsplit

from .animation_lab_samples import MODULES, catalog, compile_sample
from .animation_lab_store import AnnotationStore
from .motion import FPS, GSAP_VERSION, canonical, sha256


ASSET_NAMES = ('gsap.min.js', 'NotoSansCJK-Regular.ttc')
TAGS = frozenset({'layout', 'typography', 'connector', 'motion', 'pacing',
                  'continuity', 'clarity', 'other'})
MAX_BODY = 32 * 1024
WEB = Path(__file__).with_name('animation_lab_web')
_FIELDS = {'case_id', 'case_identity', 'variant_id', 'kind', 'start_seconds',
           'end_seconds', 'region', 'tags', 'severity', 'comment', 'preference', 'verdict'}


class LabError(ValueError):
    def __init__(self, code, status=422):
        super().__init__(code)
        self.code, self.status = code, status


def _require(value, code, status=422):
    if not value:
        raise LabError(code, status)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def load_assets(directory):
    """Keep the measured bytes in memory so a file change cannot relabel reviews."""
    root = Path(directory).resolve(strict=True)
    result = {}
    for name in ASSET_NAMES:
        path = root / name
        _require(not path.is_symlink() and path.is_file(), 'animation_lab_asset_missing')
        _require(path.stat().st_size <= 32 * 1024 * 1024, 'animation_lab_asset_too_large')
        raw = path.read_bytes()
        _require(bool(raw), 'animation_lab_asset_missing')
        result[name] = raw
    _require(('GSAP ' + GSAP_VERSION).encode('ascii') in result['gsap.min.js'][:1024],
             'animation_lab_gsap_version_mismatch')
    _require(result['NotoSansCJK-Regular.ttc'][:4] == b'ttcf', 'animation_lab_font_invalid')
    return result


class AnimationLab:
    def __init__(self, data_dir, assets, evaluations_dir=None):
        self.assets = dict(assets)
        self.evaluations_dir = Path(evaluations_dir).resolve(strict=True) if evaluations_dir else None
        self.player_script = Path(__file__).with_name('animation_lab_player.mjs').read_bytes()
        self.runtime = dict(schema='video.animation-lab-preview-runtime.v1',
            gsap=GSAP_VERSION, assets={name: sha256(raw) for name, raw in sorted(assets.items())},
            preview_adapter_sha256=sha256(self.player_script), width=1280, height=720, fps=FPS,
            output='browser_preview', production_export_verified=False)
        self.store = AnnotationStore(Path(data_dir) / 'animation-lab.db')
        self.csrf_token = secrets.token_urlsafe(32)
        self.cases, self.bundles = {}, {}
        for case in catalog():
            bundles = [compile_sample(case['id'], row['id']) for row in case['variants']]
            case.update(fps=FPS,
                        note='无旁白技术样例；仅评价画面，静音时钟不证明配音同步。')
            frozen = []
            for variant, bundle in zip(case['variants'], bundles):
                self.bundles[case['id'], variant['id']] = bundle
                points, offset = [], 0
                for clip in bundle['clips']:
                    points.extend(dict(label=clip['id'] + ' · ' + point['label'],
                                       time=offset + point['time']) for point in clip['checkpoints'])
                    offset += clip['duration_seconds']
                variant.update(available=True, preview_url=f"/preview/{case['id']}/{variant['id']}/",
                    identity=bundle['artifact_sha256'], observation_times=points,
                    summary=f"{len(bundle['clips'])} 段 · {bundle['duration_seconds']:.3f} 秒")
                document = {key: deepcopy(value) for key, value in bundle.items() if key != 'clips'}
                document['clips'] = [dict(id=clip['id'], duration_seconds=clip['duration_seconds'],
                    timeline=clip['timeline'], files={name: dict(encoding='base64', sha256=sha256(raw),
                        data=base64.b64encode(raw).decode('ascii'))
                        for name, raw in sorted(clip['files'].items())}) for clip in bundle['clips']]
                frozen.append(document)
            snapshot = dict(schema='video.animation-lab-case.v1',
                case_id=case['id'], module=case['module'], title=case['title'], runtime=self.runtime,
                comparison=dict(baseline=case['variants'][0]['id'], candidate=case['variants'][-1]['id']),
                review_scope='developer_visual_only', variants=frozen)
            # Hash every persisted field: editing a case title must create a new
            # experiment, not prevent reopening an existing annotation database.
            identity = sha256(snapshot)
            case.update(identity=identity, case_identity=identity)
            self.cases[case['id']] = case
            self.store.register_snapshot(dict(snapshot, identity=identity))

    def get_catalog(self):
        cases = deepcopy(list(self.cases.values()))
        history = self.store.list()
        for case in cases:
            active = [row for row in history if row['case_identity'] == case['identity']
                      and not row['retracted']]
            reviews = [row for row in active if row['kind'] == 'review']
            case['annotation_count'] = len(active)
            if reviews:
                case.update(review_verdict=reviews[-1]['verdict'], reviewed_identity=case['identity'])
        return dict(schema='video.animation-lab-catalog.v1',
            modules=[dict(row, label=row['title']) for row in deepcopy(MODULES)],
            cases=cases, runtime=deepcopy(self.runtime),
            evaluations=self.evaluations(),
            csrf_token=self.csrf_token)

    def evaluations(self):
        """Read saved reports only. Page reads never start model/render jobs."""
        if self.evaluations_dir is None:
            return []
        from .animation_lab_evaluate import load_run
        result = []
        for root in sorted(self.evaluations_dir.iterdir()):
            if root.is_symlink() or not root.is_dir() or not re.fullmatch(r'[a-z0-9-]{1,64}', root.name):
                continue
            if not (root / 'report.html').is_file():
                continue
            try:
                run, _ = load_run(root)
                technical = json.loads((root / 'technical.json').read_bytes())
                _require(technical['run_identity'] == run['identity'], 'animation_lab_report_mismatch')
                result.append(dict(id=root.name, title=run['title'], identity=run['identity'],
                    technical_status=technical['status'],
                    model_status='已评审' if (root / 'model-review.json').is_file() else (
                        '失败已停止' if (root / 'model-failure.json').is_file() else (
                            '未配置或凭据不可用' if (root / 'model-unavailable.json').is_file() else '尚未调用')),
                    report_url=f'/evaluations/{root.name}/report.html'))
            except (ValueError, OSError, KeyError):
                continue
        return result

    def evaluation_file(self, run_id, relative):
        _require(self.evaluations_dir is not None and re.fullmatch(r'[a-z0-9-]{1,64}', run_id),
                 'animation_lab_report_not_found', 404)
        root = self.evaluations_dir / run_id
        _require(not root.is_symlink() and root.is_dir(), 'animation_lab_report_not_found', 404)
        allowed = {'report.html': 'text/html; charset=utf-8',
                   'technical.json': 'application/json; charset=utf-8',
                   'review-request.json': 'application/json; charset=utf-8'}
        request = json.loads((root / 'review-request.json').read_bytes())
        for frame in request['private']['frames'].values():
            name = frame['source_id'].split(':')[-1] + '.jpg'
            allowed[f"inputs/{frame['variant_id']}/{frame['clip_id']}/observations/{name}"] = 'image/jpeg'
        _require(relative in allowed, 'animation_lab_report_not_found', 404)
        path = root / relative
        _require(not path.is_symlink() and path.resolve().is_relative_to(root.resolve())
                 and path.is_file() and path.stat().st_size <= 8 * 1024 * 1024,
                 'animation_lab_report_not_found', 404)
        return path.read_bytes(), allowed[relative]

    def annotate(self, body, user_agent=''):
        _require(type(body) is dict and not (set(body) - _FIELDS), 'animation_lab_invalid_fields')
        case_id = body.get('case_id')
        _require(type(case_id) is str and case_id in self.cases, 'animation_lab_unknown_case')
        case = self.cases[case_id]
        _require(body.get('case_identity') == case['identity'], 'animation_lab_case_changed', 409)
        variant_id, kind = body.get('variant_id'), body.get('kind')
        _require(type(kind) is str and kind in {'issue', 'preference', 'review'}, 'animation_lab_invalid_kind')
        choices = {row['id']: row for row in case['variants']}
        _require(type(variant_id) is str and variant_id in {*choices, 'comparison'},
                 'animation_lab_unknown_variant')
        _require(kind != 'preference' or variant_id == 'comparison', 'animation_lab_preference_scope')
        durations = [row['duration_seconds'] for row in choices.values()]
        duration = max(durations) if variant_id == 'comparison' else choices[variant_id]['duration_seconds']
        start, end = body.get('start_seconds'), body.get('end_seconds')
        _require(_number(start) and _number(end) and 0 <= start <= end <= duration,
                 'animation_lab_invalid_interval')
        region = body.get('region')
        if region is not None:
            _require(type(region) is dict and set(region) == {'x', 'y', 'width', 'height'}
                     and all(_number(value) for value in region.values()), 'animation_lab_invalid_region')
            x, y, width, height = (region[key] for key in ('x', 'y', 'width', 'height'))
            _require(0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 and 0 < height <= 1
                     and x + width <= 1 + 1e-9 and y + height <= 1 + 1e-9,
                     'animation_lab_invalid_region')
            _require(variant_id != 'comparison', 'animation_lab_region_needs_variant')
        tags = body.get('tags', [])
        _require(type(tags) is list and len(tags) <= len(TAGS)
                 and all(type(value) is str and value in TAGS for value in tags)
                 and len(set(tags)) == len(tags), 'animation_lab_invalid_tags')
        severity = body.get('severity', 'note')
        _require(type(severity) is str and severity in {'note', 'minor', 'major', 'blocker'},
                 'animation_lab_invalid_severity')
        comment = body.get('comment', '')
        _require(type(comment) is str and len(comment) <= 4000 and '\0' not in comment,
                 'animation_lab_invalid_comment')
        preference, verdict = body.get('preference'), body.get('verdict')
        _require((kind == 'preference' and type(preference) is str
                  and preference in {'baseline', 'candidate', 'tie', 'neither'})
                 or (kind != 'preference' and preference is None), 'animation_lab_invalid_preference')
        _require((kind == 'review' and type(verdict) is str and verdict in {'acceptable', 'needs_work'})
                 or (kind != 'review' and verdict is None), 'animation_lab_invalid_verdict')
        _require(kind != 'issue' or comment.strip() or tags, 'animation_lab_issue_is_empty')
        row = dict(case_id=case_id, case_identity=case['identity'], module=case['module'],
            variant_id=variant_id, kind=kind, start_seconds=start, end_seconds=end,
            start_frame=math.floor(start * FPS + 1e-8), end_frame=math.floor(end * FPS + 1e-8),
            fps=FPS, region=deepcopy(region), tags=list(tags), severity=severity, comment=comment.strip(),
            preference=preference, verdict=verdict, client=dict(user_agent=user_agent[:512]),
            review_scope='developer_visual_only')
        return self.store.append(row)

    def preview(self, case_id, variant_id):
        bundle = self.bundles.get((case_id, variant_id))
        _require(bundle is not None, 'animation_lab_unknown_variant', 404)
        clips, offset = [], 0
        for clip in bundle['clips']:
            clips.append(dict(id=clip['id'], label=clip['id'], offset=offset,
                duration=clip['duration_seconds'],
                url=f"/composition/{case_id}/{variant_id}/{clip['id']}/index.html"))
            offset += clip['duration_seconds']
        payload = html.escape(json.dumps(dict(clips=clips, duration=offset), allow_nan=False), quote=True)
        return ('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>动画样例预览</title><style>
*{box-sizing:border-box}html,body,#player{margin:0;width:100%;height:100%;overflow:hidden;background:#0c1523}
iframe{position:absolute;inset:0;width:1280px;height:720px;border:0;transform-origin:top left}
iframe[hidden]{display:none}#error{position:absolute;inset:0;padding:24px;color:#fff;background:#301b25;font:18px sans-serif}
</style><main id="player" data-config="''' + payload + '''"></main><p id="error" hidden></p>
<script type="module" src="/lab-player.mjs"></script></html>''').encode('utf-8')


class LabHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, lab, port=8766):
        self.lab = lab
        super().__init__(('127.0.0.1', port), LabHandler)
        self.allowed_hosts = {f'127.0.0.1:{self.server_port}', f'localhost:{self.server_port}'}


class LabHandler(BaseHTTPRequestHandler):
    # Route only a fixed catalog of UI and generated files. Never serve data_dir.
    def log_message(self, *_args):
        pass

    def _context(self, write=False):
        host = self.headers.get('Host', '')
        _require(host in self.server.allowed_hosts, 'animation_lab_host_rejected', 403)
        origin = self.headers.get('Origin')
        _require(origin is None or origin == 'http://' + host, 'animation_lab_origin_rejected', 403)
        if write:
            _require(origin == 'http://' + host, 'animation_lab_origin_required', 403)
            token = self.headers.get('X-Animation-Lab-Token', '')
            _require(token.isascii() and secrets.compare_digest(token, self.server.lab.csrf_token),
                     'animation_lab_token_required', 403)
            _require(self.headers.get_content_type() == 'application/json',
                     'animation_lab_json_required', 415)

    def _send(self, raw, content_type, status=200, *, download=False):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; font-src 'self'; frame-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'self'")
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="animation-lab-evaluation.jsonl"')
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, value, status=200):
        self._send(canonical(value), 'application/json; charset=utf-8', status)

    def do_GET(self):
        try:
            self._context()
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            lab = self.server.lab
            if path == '/api/catalog':
                return self._json(lab.get_catalog())
            if path == '/api/annotations':
                case_id = parse_qs(parsed.query).get('case_id', [None])[0]
                _require(case_id is None or case_id in lab.cases, 'animation_lab_unknown_case', 404)
                return self._json(dict(annotations=lab.store.list(case_id)))
            if path == '/api/export':
                _require(parse_qs(parsed.query).get('format', ['jsonl']) == ['jsonl'],
                         'animation_lab_unknown_export')
                return self._send(lab.store.export_jsonl(), 'application/x-ndjson; charset=utf-8', download=True)
            if path in {'/', '/index.html', '/app.mjs', '/model.mjs', '/style.css', '/lab-player.mjs'}:
                name = 'index.html' if path == '/' else path[1:]
                if name == 'lab-player.mjs':
                    return self._send(lab.player_script, 'text/javascript; charset=utf-8')
                source = WEB / name
                mime = {'html': 'text/html', 'mjs': 'text/javascript', 'css': 'text/css'}[name.rsplit('.', 1)[-1]]
                return self._send(source.read_bytes(), mime + '; charset=utf-8')
            pieces = path.strip('/').split('/')
            if len(pieces) >= 3 and pieces[0] == 'evaluations':
                raw, mime = lab.evaluation_file(pieces[1], '/'.join(pieces[2:]))
                return self._send(raw, mime)
            if len(pieces) == 3 and pieces[0] == 'preview':
                return self._send(lab.preview(pieces[1], pieces[2]), 'text/html; charset=utf-8')
            if len(pieces) in (5, 6) and pieces[0] == 'composition':
                _, case_id, variant_id, clip_id, *tail = pieces
                bundle = lab.bundles.get((case_id, variant_id))
                _require(bundle is not None, 'animation_lab_unknown_variant', 404)
                clip = next((row for row in bundle['clips'] if row['id'] == clip_id), None)
                _require(clip is not None, 'animation_lab_unknown_clip', 404)
                name = '/'.join(tail)
                if name in {'assets/' + value for value in ASSET_NAMES}:
                    asset = name.split('/')[1]
                    mime = 'text/javascript' if asset.endswith('.js') else 'font/collection'
                    return self._send(lab.assets[asset], mime)
                _require(name in clip['files'], 'animation_lab_unknown_file', 404)
                mime = 'text/html; charset=utf-8' if name == 'index.html' else (
                    'audio/wav' if name.endswith('.wav') else 'application/json; charset=utf-8')
                return self._send(clip['files'][name], mime)
            raise LabError('animation_lab_not_found', 404)
        except LabError as error:
            self._json(dict(error=error.code), error.status)
        except (FileNotFoundError, KeyError):
            self._json(dict(error='animation_lab_not_found'), 404)

    def do_POST(self):
        try:
            self._context(write=True)
            length = self.headers.get('Content-Length', '')
            _require(length.isdigit() and 0 < int(length) <= MAX_BODY, 'animation_lab_body_size', 413)
            _require(not self.headers.get('Transfer-Encoding'), 'animation_lab_body_size', 413)
            try:
                body = json.loads(self.rfile.read(int(length)))
            except (ValueError, UnicodeError):
                raise LabError('animation_lab_invalid_json', 400) from None
            path = urlsplit(self.path).path
            lab = self.server.lab
            if path == '/api/annotations':
                return self._json(dict(annotation=lab.annotate(body, self.headers.get('User-Agent', ''))), 201)
            pieces = path.strip('/').split('/')
            if len(pieces) == 4 and pieces[:2] == ['api', 'annotations'] and pieces[3] == 'retract':
                _require(type(body) is dict and set(body) == {'reason'} and type(body['reason']) is str
                         and 0 < len(body['reason'].strip()) <= 1000, 'animation_lab_invalid_retraction')
                try:
                    row = lab.store.retract(pieces[2], body['reason'].strip())
                except KeyError:
                    raise LabError('animation_lab_annotation_not_found', 404) from None
                return self._json(dict(annotation=row))
            raise LabError('animation_lab_not_found', 404)
        except LabError as error:
            self._json(dict(error=error.code), error.status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--assets-dir', required=True, type=Path)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--evaluations-dir', type=Path, help='optional read-only saved automatic evaluation runs')
    args = parser.parse_args()
    lab = AnimationLab(args.data_dir, load_assets(args.assets_dir), args.evaluations_dir)
    with LabHTTPServer(lab, args.port) as server:
        print(f'Animation developer lab: http://127.0.0.1:{server.server_port}/', flush=True)
        print('Developer visual review only; Agent/TTS/production writes: 0.', flush=True)
        try:
            server.serve_forever(poll_interval=.25)
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
