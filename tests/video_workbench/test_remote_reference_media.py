"""Remote pixels, timestamps and transport boundaries; no public media fetched."""
from copy import deepcopy
import hashlib
from http import client as http_client
import io
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time

import pytest

from agent_service.contracts import ServiceError
from video_workbench.remote_reference_media import (
    PublicDNSResolver, PublicHTTPS, RemoteReferenceMedia, _Budget, _RangeProxy, _Response,
    normalize_source_url, validate_crop,
)


class Response:
    def __init__(self, content, budget, *, status=200, headers=None):
        self.data, self.budget = io.BytesIO(content), budget
        self.status, self.headers = status, headers or {'Content-Length': str(len(content))}

    def read(self, size=65536):
        raw = self.data.read(size)
        self.budget.consume(len(raw))
        return raw

    def close(self):
        self.data.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Transport:
    def __init__(self, media, *, change=False):
        self.media, self.calls, self.change = media, [], change

    def open(self, url, *, budget, method='GET', headers=None):
        budget.begin()
        self.calls.append((url, method, headers))
        if 'x/web-interface/view' in url:
            body = dict(code=0, data=dict(bvid='BV1BxWu6SESe', title='A video',
                pages=[dict(cid=123, duration=2)]))
            return Response(json.dumps(body).encode(), budget)
        if 'x/player/playurl' in url:
            formats = [dict(id=80, codecid=7, width=1280, height=720,
                frame_rate='24/1', codecs='avc1.64001f', baseUrl='https://cdn.bilivideo.com/video.mp4'),
                dict(id=32, codecid=7, width=640, height=360, frame_rate='24/1',
                    codecs='avc1.64001f', baseUrl='https://cdn.bilivideo.com/low.mp4')]
            if self.change:
                formats = formats[1:]
            body = dict(code=0, data=dict(accept_quality=[80, 32], accept_description=['720P', '360P'],
                dash=dict(video=formats, audio=[{}])))
            return Response(json.dumps(body).encode(), budget)
        raw, status, response_headers = self.media, 200, {'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes'}
        value = (headers or {}).get('Range')
        if value:
            match = re.fullmatch(r'bytes=(\d+)-(\d*)', value)
            assert match, value
            start = int(match[1])
            end = min(int(match[2]) if match[2] else len(raw) - 1, len(raw) - 1)
            if start >= len(raw):
                return Response(b'', budget, status=416, headers={'Content-Range': 'bytes */%d' % len(raw), 'Content-Length': '0'})
            response_headers['Content-Range'] = 'bytes %d-%d/%d' % (start, end, len(raw))
            raw, status = raw[start:end + 1], 206
        response_headers['Content-Length'] = str(len(raw))
        return Response(b'' if method == 'HEAD' else raw, budget, status=status, headers=response_headers)


@pytest.fixture(scope='module')
def video(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('actual FFmpeg/FFprobe required')
    target = tmp_path_factory.mktemp('remote-fixture') / 'source.mp4'
    result = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=1280x720:rate=24:duration=2', '-c:v', 'libx264', '-pix_fmt',
        'yuv420p', '-movflags', '+faststart', '-an', '-threads', '1', str(target)],
        capture_output=True, timeout=45)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return target.read_bytes()


@pytest.mark.parametrize('url', [
    'http://example.com/a.mp4', 'https://user:secret@example.com/a.mp4',
    'https://example.com:444/a.mp4', 'https://example.com/a.m3u8',
    'https://www.bilibili.com.evil.example/watch', 'https://example.com/a.mp4#frag',
    'https://example.com/a.mp4?access_token=secret', 'https://example.com\\@evil/a.mp4',
])
def test_invalid_source_locators_fail_before_network(url):
    with pytest.raises(ServiceError):
        normalize_source_url(url)


def test_bilibili_locator_ignores_tracking_and_keeps_page():
    assert normalize_source_url('https://www.bilibili.com/video/BV1BxWu6SESe/?spm_id_from=x&p=2') == (
        'https://www.bilibili.com/video/BV1BxWu6SESe/?p=2')


@pytest.mark.parametrize('crop', [
    {'x': .8, 'y': 0, 'width': .3, 'height': 1},
    {'x': -1, 'y': 0, 'width': 1, 'height': 1},
    {'x': 0, 'y': 0, 'width': 0, 'height': 1},
    {'x': False, 'y': 0, 'width': 1, 'height': 1},
])
def test_crop_is_normalized_original_frame_scope(crop):
    with pytest.raises(ServiceError, match='remote_reference_crop_invalid'):
        validate_crop(crop)


@pytest.mark.parametrize('addresses', [
    ['127.0.0.1'], ['10.0.0.1'], ['100.64.0.1'], ['169.254.169.254'],
    ['::1'], ['fc00::1'], ['::ffff:8.8.8.8'], ['224.0.0.1'], ['192.0.2.1'],
    ['64:ff9b::7f00:1'], ['2002:7f00:1::1'],
    ['8.8.8.8', '127.0.0.1'],
])
def test_all_resolved_addresses_must_be_public(addresses):
    transport = PublicHTTPS(resolver=lambda *a, **k: [(None, None, None, None, (ip, 443)) for ip in addresses])
    with pytest.raises(ServiceError, match='remote_reference_destination_rejected'):
        transport._addresses('example.com')


def test_dns_pinned_connection_uses_numeric_address_and_tls_hostname():
    calls = []

    class Sock:
        def getpeername(self):
            return ('8.8.8.8', 443)

    class TLS:
        def wrap_socket(self, raw, *, server_hostname):
            calls.append(('tls', server_hostname))
            return raw

    def connect(address, timeout):
        calls.append(('connect', address))
        return Sock()

    transport = PublicHTTPS(resolver=lambda *a, **k: [(None, None, None, None, ('8.8.8.8', 443))],
        connector=connect, context=TLS())
    connection = transport._connect('example.com', _Budget())
    assert calls == [('connect', ('8.8.8.8', 443)), ('tls', 'example.com')]
    connection.sock = None  # The fixture is not a live socket.


def test_redirect_to_private_address_is_rejected_before_connect(monkeypatch):
    transport = PublicHTTPS()
    connected = []

    class Redirect:
        status = 302
        headers = {'Location': 'https://127.0.0.1/a.mp4'}

        def close(self):
            pass

    class Connection:
        sock = None

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return Redirect()

        def close(self):
            pass

    def connect(host, budget):
        connected.append(host)
        if host == '127.0.0.1':
            transport._addresses(host)
        return Connection()

    monkeypatch.setattr(transport, '_connect', connect)
    with pytest.raises(ServiceError, match='remote_reference_destination_rejected'):
        transport.open('https://example.com/a.mp4', budget=_Budget())
    assert connected == ['example.com', '127.0.0.1']


def test_bilibili_describe_uses_metadata_only_and_omits_playback_urls(tmp_path, video):
    transport = Transport(video)
    adapter = RemoteReferenceMedia(tmp_path, transport=transport)
    source = adapter.describe('https://www.bilibili.com/video/BV1BxWu6SESe/')
    assert len(transport.calls) == 2
    assert source['source_id'] == 'bilibili:BV1BxWu6SESe:p1'
    assert source['width'] == 1280 and source['height'] == 720
    assert source['source_identity_scope'] == 'provider_metadata'
    assert len(source['quality']['available']) == 2
    assert 'cdn.bilivideo.com' not in json.dumps(source)
    assert 'media_sha256' not in source
    assert list(tmp_path.iterdir()) == []


def test_real_remote_range_decode_retains_pts_detail_and_cleans_temporary_files(tmp_path, video):
    from PIL import Image
    transport = Transport(video)
    adapter = RemoteReferenceMedia(tmp_path, transport=transport)
    source = adapter.describe('https://media.example/video.mp4')
    result = adapter.observe(source, times=[.1, 1.1], quality='detail')
    assert len(result['frames']) == 2
    for requested, frame in zip([.1, 1.1], result['frames']):
        assert requested <= frame['actual_pts'] < requested + 1/24 + 1e-6
        assert frame['actual_pts'] == frame['pts_ticks'] * frame['time_base'][0] / frame['time_base'][1]
        assert frame['width'] == 1280 and frame['height'] == 720
        assert frame['sha256'] == hashlib.sha256(frame['content']).hexdigest()
        assert 'media_sha256' not in frame and frame['source_snapshot_sha256'] == source['source_snapshot_sha256']
        with Image.open(io.BytesIO(frame['content'])) as image:
            assert image.format == 'JPEG' and image.size == (1280, 720)
    assert result['coverage']['continuous_motion_verified'] is False
    assert result['coverage']['audio_verified'] is False
    assert any((headers or {}).get('Range') for _, _, headers in transport.calls)
    assert list(tmp_path.iterdir()) == []


def test_crop_uses_native_pixels_before_detail_resize(tmp_path, video):
    adapter = RemoteReferenceMedia(tmp_path, transport=Transport(video))
    source = adapter.describe('https://media.example/video.mp4')
    crop = dict(x=.25, y=.25, width=.5, height=.5)
    detail = adapter.observe(source, times=[.5], crop=crop, quality='detail')['frames'][0]
    overview = adapter.observe(source, times=[.5], quality='overview')['frames'][0]
    assert (detail['width'], detail['height']) == (640, 360)
    assert detail['crop'] == crop and len(detail['content']) <= 2 * 1024 * 1024
    assert overview['width'] <= 640 and overview['height'] <= 480
    assert len(overview['content']) <= 96 * 1024
    assert list(tmp_path.iterdir()) == []


def test_real_whole_source_overview_accepts_24_frames_with_verified_pts(tmp_path, video):
    adapter = RemoteReferenceMedia(tmp_path, transport=Transport(video))
    source = adapter.describe('https://media.example/video.mp4')
    times = [index / 12 for index in range(24)]
    result = adapter.observe(source, times=times, quality='overview', deadline_at=time.time() + 60)
    assert len(result['frames']) == result['coverage']['requested_samples'] == 24
    assert result['coverage']['unique_presentation_frames'] == 24
    assert result['coverage']['aliased_samples'] == 0
    for requested, frame in zip(times, result['frames']):
        assert requested <= frame['actual_pts'] < requested + 1/24 + 1e-6
        assert frame['actual_pts'] == frame['pts_ticks'] * frame['time_base'][0] / frame['time_base'][1]
        assert frame['width'] <= 640 and frame['height'] <= 480
        assert len(frame['content']) <= 96 * 1024
    assert result['coverage']['continuous_motion_verified'] is False
    assert list(tmp_path.iterdir()) == []


def test_25_observation_frames_are_rejected_before_network_or_decoding(tmp_path, video):
    transport = Transport(video)
    adapter = RemoteReferenceMedia(tmp_path, transport=transport)
    source = adapter.describe('https://media.example/video.mp4')
    prior_calls = len(transport.calls)
    with pytest.raises(ServiceError, match='remote_reference_times_invalid'):
        adapter.observe(source, times=[index / 24 for index in range(25)])
    assert len(transport.calls) == prior_calls
    assert list(tmp_path.iterdir()) == []


def test_detected_quality_change_fails_without_decoding_or_saving_media(tmp_path, video):
    transport = Transport(video)
    adapter = RemoteReferenceMedia(tmp_path, transport=transport)
    source = adapter.describe('https://www.bilibili.com/video/BV1BxWu6SESe/')
    transport.change = True
    with pytest.raises(ServiceError, match='remote_reference_source_changed'):
        adapter.observe(source, times=[.5], quality='detail')
    assert len(transport.calls) == 4  # metadata only; no resolved media was fetched
    assert list(tmp_path.iterdir()) == []


def test_deadline_and_byte_caps_are_real(tmp_path, video):
    adapter = RemoteReferenceMedia(tmp_path, transport=Transport(video))
    with pytest.raises(ServiceError, match='remote_reference_deadline_exhausted'):
        adapter.describe('https://media.example/video.mp4', deadline_at=time.time() - 1)
    budget = _Budget(max_bytes=5)
    with pytest.raises(ServiceError, match='remote_reference_byte_limit'):
        budget.consume(6)
    budget = _Budget(max_requests=1)
    budget.begin()
    with pytest.raises(ServiceError, match='remote_reference_request_limit'):
        budget.begin()


def test_failed_decoder_removes_all_temporary_frame_files(tmp_path, video, monkeypatch):
    adapter = RemoteReferenceMedia(tmp_path, transport=Transport(video))
    source = adapter.describe('https://media.example/video.mp4')

    def fail(proxy, budget, target, *args):
        target.write_bytes(b'partial frame')
        raise ServiceError('remote_reference_decode_failed', 422)

    monkeypatch.setattr(adapter, '_decode', fail)
    with pytest.raises(ServiceError, match='remote_reference_decode_failed'):
        adapter.observe(source, times=[.5])
    assert list(tmp_path.iterdir()) == []


def test_doh_cname_chain_cache_and_unrelated_answers_are_scoped():
    calls = []

    class DNS:
        def open(self, url, *, budget, headers):
            budget.begin()
            calls.append(url)
            return Response(json.dumps(dict(Status=0, Answer=[
                dict(name='media.example.', type=5, data='edge.example.', TTL=20),
                dict(name='edge.example.', type=1, data='8.8.8.8', TTL=20),
                dict(name='unrelated.example.', type=1, data='127.0.0.1', TTL=20),
            ])).encode(), budget)

    resolver = PublicDNSResolver('alidns', transport=DNS())
    transport = PublicHTTPS(resolver=resolver)
    budget = _Budget(max_requests=1)
    assert transport._addresses('media.example', budget) == ['8.8.8.8']
    assert transport._addresses('media.example', budget) == ['8.8.8.8']
    assert len(calls) == 1 and calls[0].startswith('https://dns.alidns.com/resolve?')
    assert budget.requests == 1 and budget.bytes > 0


def test_doh_private_answers_are_still_rejected_and_not_connected():
    class DNS:
        def open(self, url, *, budget, headers):
            return Response(json.dumps(dict(Status=0, Answer=[
                dict(name='media.example.', type=1, data='127.0.0.1', TTL=30),
            ])).encode(), budget)

    transport = PublicHTTPS(resolver=PublicDNSResolver('alidns', transport=DNS()))
    with pytest.raises(ServiceError, match='remote_reference_destination_rejected'):
        transport._addresses('media.example', _Budget())


def test_slow_trickling_metadata_obeys_shared_deadline(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    closed = []

    class Slow(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', '1000')
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b' ')
                    self.wfile.flush()
                    time.sleep(.04)
            except OSError:
                pass
            finally:
                closed.append(True)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Slow)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class LoopbackFixtureTransport:
        def open(self, url, *, budget, **kwargs):
            # Test-only upstream; production PublicHTTPS rejects loopback.
            connection = http_client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
            connection.request('GET', '/')
            return _Response(connection, connection.getresponse(), url, budget)

    adapter = RemoteReferenceMedia(tmp_path, transport=LoopbackFixtureTransport())
    started = time.time()
    try:
        with pytest.raises(ServiceError, match='remote_reference_(deadline_exhausted|fetch_failed)'):
            adapter._json('https://example.com/metadata', _Budget(time.time() + .25))
        assert time.time() - started < 1
        assert list(tmp_path.iterdir()) == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_decoder_timeout_kills_subprocess_and_removes_partial_frames(tmp_path, video, monkeypatch):
    import sys
    adapter = RemoteReferenceMedia(tmp_path, transport=Transport(video))
    source = adapter.describe('https://media.example/video.mp4')

    def timeout(proxy, budget, target, *args):
        target.write_bytes(b'partial frame')
        adapter._run([sys.executable, '-c', 'import time;time.sleep(5)'], budget, proxy)

    monkeypatch.setattr(adapter, '_decode', timeout)
    started = time.time()
    with pytest.raises(ServiceError, match='remote_reference_deadline_exhausted'):
        adapter.observe(source, times=[.5], deadline_at=time.time() + .7)
    assert time.time() - started < 2
    assert list(tmp_path.iterdir()) == []


def test_mov_external_track_reference_cannot_bypass_proxy(tmp_path, video):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    visits = []

    class Canary(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            visits.append(self.path)
            self.send_response(200)
            self.send_header('Content-Length', str(len(video)))
            self.end_headers()
            try:
                self.wfile.write(video)
            except OSError:
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Canary)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    external = ('http://127.0.0.1:%d/external-track' % server.server_port).encode() + b'\x00'
    delta = len(external)

    def rewrite(raw):
        result, position = [], 0
        while position < len(raw):
            size, kind = int.from_bytes(raw[position:position + 4], 'big'), raw[position + 4:position + 8]
            assert 8 <= size <= len(raw) - position
            payload = raw[position + 8:position + size]
            if kind in {b'moov', b'trak', b'mdia', b'minf', b'dinf'}:
                payload = rewrite(payload)
            elif kind == b'dref':
                assert payload[4:8] == b'\x00\x00\x00\x01' and payload[12:16] == b'url '
                entry = b'\x00\x00\x00\x00' + external
                payload = payload[:8] + (len(entry) + 8).to_bytes(4, 'big') + b'url ' + entry
            elif kind == b'stco':
                count = int.from_bytes(payload[4:8], 'big')
                payload = payload[:8] + b''.join((int.from_bytes(payload[8 + i*4:12 + i*4], 'big') + delta)
                    .to_bytes(4, 'big') for i in range(count))
            result.append((len(payload) + 8).to_bytes(4, 'big') + kind + payload)
            position += size
        return b''.join(result)

    hostile = rewrite(video)
    assert external in hostile
    adapter = RemoteReferenceMedia(tmp_path, transport=Transport(hostile))
    try:
        source = adapter.describe('https://media.example/video.mp4')
        try:
            adapter.observe(source, times=[.5])
        except ServiceError as error:
            assert error.code in {'remote_reference_decode_failed', 'remote_reference_timestamp_unverified'}
        assert visits == []
        assert list(tmp_path.iterdir()) == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
