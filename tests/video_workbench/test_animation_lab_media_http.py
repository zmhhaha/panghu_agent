"""Real HTTP media transport and frozen-store checks, plus optional codec seek.

The normal transport fixture has synthetic bytes, not a generated-work claim.
The FFmpeg check encodes an independent tiny test video, never an Agent task.
"""
import http.client
import json
import shutil
import subprocess
import threading
from types import SimpleNamespace

import pytest

from video_workbench.animation_experiments import (
    ExperimentStore, canonical, definition, digest, pilot_input, signed)
from video_workbench.animation_lab import LabHTTPServer


PREFIX = '/dev/animations'
HOST = 'video-service.panghuer.top'
USER = 'casdoor:media-test-developer'
MEDIA_PATH = PREFIX + '/api/experiments/identity-tracking/runs/media-test/output.mp4'


class MediaClient:
    def __init__(self, directory, media):
        root = directory / 'runs'
        self.run_dir = root / 'media-test'
        self.run_dir.mkdir(parents=True)
        document = pilot_input()
        report = canonical(dict(schema='video.animation-experiment-report.v1',
            input_sha256=digest(document), agent_job_id='media-transport-fixture'))
        artifacts = {}
        for name, raw, mime in [('output.mp4', media, 'video/mp4'),
                                ('report.json', report, 'application/json')]:
            (self.run_dir / name).write_bytes(raw)
            artifacts[name] = dict(size=len(raw), sha256=digest(raw), mime=mime)
        row = signed(dict(schema='video.animation-experiment-run.v1', id='media-test',
            experiment_id=definition()['id'], experiment_identity=definition()['identity'],
            source='agent', input_role='development', input_document=document,
            input_sha256=digest(document), prompt_text='Media transport fixture only',
            provenance=dict(job_id='media-transport-fixture'), status='rendered',
            artifacts=artifacts))
        (self.run_dir / 'experiment-run.json').write_bytes(canonical(row))
        lab = SimpleNamespace(iteration_store=SimpleNamespace(),
            experiments=ExperimentStore(directory / 'feedback.db', root))
        self.server = LabHTTPServer(lab, 0, proxy_mode=True,
            public_origin='https://' + HOST, base_path=PREFIX, developer_users=[USER])
        self.thread = threading.Thread(target=self.server.serve_forever,
            kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.media = media

    def request(self, method='GET', path=MEDIA_PATH, *, headers=(), authenticated=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.putrequest(method, path, skip_host=True)
            connection.putheader('Host', HOST)
            if authenticated:
                connection.putheader('X-Forwarded-User', USER)
            for name, value in headers:
                connection.putheader(name, value)
            connection.endheaders()
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture(scope='module')
def media_client(tmp_path_factory):
    # Distinct byte values make incorrect offsets and lengths observable.
    client = MediaClient(tmp_path_factory.mktemp('media-http'), bytes(range(256)) * 8)
    yield client
    client.close()


def test_full_video_and_head_advertise_seekable_bytes_with_stable_metadata(media_client):
    status, body, headers = media_client.request()
    assert status == 200 and body == media_client.media
    assert headers['Content-Type'] == 'video/mp4'
    assert headers['Accept-Ranges'] == 'bytes'
    assert headers['Content-Length'] == str(len(body))
    assert headers['Cache-Control'] == 'no-store'
    assert headers['ETag'] == '"' + digest(body) + '"'
    assert 'Content-Range' not in headers and 'Content-Encoding' not in headers
    # HEAD ignores Range as required, but keeps GET's complete representation metadata.
    status, body, head = media_client.request('HEAD', headers=(('Range', 'bytes=100-199'),))
    assert status == 200 and body == b''
    for name in ('Content-Type', 'Content-Length', 'ETag', 'Accept-Ranges', 'Cache-Control'):
        assert head[name] == headers[name]
    assert 'Content-Range' not in head


@pytest.mark.parametrize('requested,start,end', [
    ('bytes=0-', 0, 2047), ('bytes=0-0', 0, 0),
    ('bytes=257-769', 257, 769), ('bytes=1024-', 1024, 2047),
    ('bytes=-57', 1991, 2047), ('bytes=-9999999999999999999999999', 0, 2047),
    ('bytes=2000-999999999999999999999999', 2000, 2047),
    ('bytes=0002-0008', 2, 8),
])
def test_browser_seek_ranges_return_exact_selected_bytes(media_client, requested, start, end):
    status, body, headers = media_client.request(headers=(('Range', requested),
        ('Accept-Encoding', 'gzip, deflate')))
    assert status == 206
    assert body == media_client.media[start:end + 1]
    assert headers['Content-Range'] == f'bytes {start}-{end}/2048'
    assert headers['Content-Length'] == str(end - start + 1)
    assert headers['Accept-Ranges'] == 'bytes'
    assert 'Content-Encoding' not in headers


@pytest.mark.parametrize('requested', ['bytes=2048-', 'bytes=2048-4096', 'bytes=-0',
    'bytes=' + '9' * 5000 + '-'])
def test_unsatisfiable_range_returns_complete_length_without_any_media(media_client, requested):
    status, body, headers = media_client.request(headers=(('Range', requested),))
    assert status == 416 and body == b''
    assert headers['Content-Range'] == 'bytes */2048'
    assert headers['Content-Length'] == '0'
    assert headers['Accept-Ranges'] == 'bytes'


@pytest.mark.parametrize('values', [
    ('items=0-4',), ('bytes=abc-4',), ('bytes=-',), ('bytes=9-4',),
    ('bytes=0-4,10-14',), ('bytes=0-4', 'bytes=10-14'),
])
def test_invalid_or_unsupported_ranges_fall_back_to_complete_representation(media_client, values):
    status, body, headers = media_client.request(headers=tuple(('Range', value) for value in values))
    assert status == 200 and body == media_client.media
    assert 'Content-Range' not in headers


def test_if_range_validates_the_exact_immutable_representation(media_client):
    etag = media_client.request()[2]['ETag']
    for validator, expected in [(etag, 206), ('"other-video"', 200), ('W/' + etag, 200),
                                ('Sat, 01 Jan 2000 00:00:00 GMT', 200)]:
        status, body, _ = media_client.request(headers=(('Range', 'bytes=256-511'),
                                                       ('If-Range', validator)))
        assert status == expected
        assert body == (media_client.media[256:512] if expected == 206 else media_client.media)


@pytest.mark.parametrize('method', ['GET', 'HEAD'])
def test_range_and_head_preserve_developer_authentication_and_fixed_routes(media_client, method):
    headers = (('Range', 'bytes=0-10'),)
    status, body, response = media_client.request(method, headers=headers, authenticated=False)
    assert status == 403 and response['Cache-Control'] == 'no-store'
    assert 'Content-Range' not in response
    if method == 'HEAD':
        assert body == b''
    else:
        assert json.loads(body)['error'] == 'animation_lab_developer_required'
    status, _, response = media_client.request(method, MEDIA_PATH.replace('output.mp4', 'scene.html'),
                                              headers=headers)
    assert status == 404 and 'Content-Range' not in response


def test_json_report_ignores_media_ranges_and_has_bodyless_head(media_client):
    path = MEDIA_PATH.replace('output.mp4', 'report.json')
    status, body, headers = media_client.request(path=path, headers=(('Range', 'bytes=0-10'),))
    assert status == 200 and json.loads(body)['agent_job_id'] == 'media-transport-fixture'
    assert 'Accept-Ranges' not in headers and 'Content-Range' not in headers
    status, body, head = media_client.request('HEAD', path)
    assert status == 200 and body == b'' and head['Content-Length'] == headers['Content-Length']


def test_even_partial_get_and_head_reject_tampered_frozen_media(tmp_path):
    client = MediaClient(tmp_path, b'original-transport-bytes')
    try:
        (client.run_dir / 'output.mp4').write_bytes(b'changed-transport-bytes!')
        for method in ('GET', 'HEAD'):
            status, body, headers = client.request(method, headers=(('Range', 'bytes=0-2'),))
            assert status == 409 and 'Content-Range' not in headers
            if method == 'GET':
                assert json.loads(body)['error'] == 'animation_experiment_artifact_changed'
    finally:
        client.close()


def test_independently_encoded_mp4_decodes_the_requested_timestamp_over_http(tmp_path):
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg is None:
        pytest.skip('Optional local FFmpeg codec check; transport assertions do not require it')
    source = tmp_path / 'codec-fixture.mp4'
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=64x64:rate=8:duration=2', '-c:v', 'libx264', '-g', '4',
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(source)],
        check=True, capture_output=True, timeout=30)
    client = MediaClient(tmp_path / 'served', source.read_bytes())
    try:
        # A real MP4 is served as bytes, including its fast-start header and tail.
        assert client.request(headers=(('Range', 'bytes=0-31'),))[0] == 206
        assert client.request(headers=(('Range', 'bytes=-128'),))[1] == source.read_bytes()[-128:]
        # FFmpeg cannot send the proxy's Host header via the default URI host, so
        # supply the exact same scrubbed developer headers used in production.
        def decode(location, seconds, http=False):
            options = ['-headers', f'Host: {HOST}\r\nX-Forwarded-User: {USER}\r\n'] if http else []
            return subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error',
                '-ss', seconds, *options, '-i', location, '-frames:v', '1',
                '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
                check=True, capture_output=True, timeout=30).stdout
        url = f'http://127.0.0.1:{client.server.server_port}' + MEDIA_PATH
        expected = decode(str(source), '1.25')
        assert len(expected) == 64 * 64 * 3
        assert decode(url, '1.25', http=True) == expected
        assert expected != decode(str(source), '0')
    finally:
        client.close()
