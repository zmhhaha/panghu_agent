"""HTTP custody tests; fixture MP4 bytes do not claim actual media execution."""
from copy import deepcopy
import hashlib
import json
import threading

import httpx
import pytest

from agent_service.contracts import ServiceError
from video_workbench.film_render_adapter import RemoteFilmRenderer
from test_hyperframes_film_snapshot import snapshot_fixture

TOKEN = 'film-token-' + 't' * 40
VIDEO = b'fixture bytes only, never claimed as a real decoded MP4'


def result_metadata(snapshot):
    count = snapshot['clock']['frame_count']
    return dict(sha256=hashlib.sha256(VIDEO).hexdigest(), bytes=len(VIDEO),
        duration_seconds=count / 24, expected_duration_seconds=count / 24,
        width=snapshot['width'], height=snapshot['height'], fps=24, video_codec='h264', audio_codec='aac',
        audio_sample_rate=48000, audio_channels=2, silent=False, subtitles=False, quality='draft',
        visual_mode='hyperframes-film', snapshot_sha256=snapshot['sha256'],
        runtime_fingerprint=deepcopy(snapshot['runtime_fingerprint']), elapsed_seconds=1,
        film_export=dict(bundle_sha256=snapshot['bundle_sha256'], film_sha256=snapshot['film_sha256'],
            project_manifest_sha256=snapshot['project_manifest_sha256'],
            clock=dict(fps=24, frame_count=count, sample_rate=48000, sample_count=count * 2000),
            export_receipt_sha256='1' * 64, capture_sha256='2' * 64, audio_mix_sha256='3' * 64,
            job_result_sha256='4' * 64, job_binding='5' * 64, admission_sha256='6' * 64,
            concatenated_scene_videos=False))


class Peer:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls, self.exists, self.state = [], False, 'completed'
        self.metadata, self.video = result_metadata(snapshot), VIDEO

    def __call__(self, request):
        self.calls.append(request)
        assert request.headers['Authorization'] == 'Bearer ' + TOKEN
        assert request.headers['X-Video-Owner'] == hashlib.sha256(b'owner').hexdigest()
        path = request.url.path
        if path.endswith('/video'):
            assert request.method == 'GET'
            return httpx.Response(200, content=self.video)
        if request.method == 'GET' and not self.exists:
            return httpx.Response(404)
        if request.method == 'PUT':
            assert json.loads(request.content) == {'snapshot': self.snapshot}
            self.exists = True
        if path.endswith('/cancel'):
            self.state = 'cancelled'
        return httpx.Response(200, json=dict(operation_id='whole-film',
            snapshot_sha256=self.snapshot['sha256'], state=self.state, result=self.metadata))


def client_fixture():
    snapshot, _, release = snapshot_fixture()
    peer = Peer(snapshot)
    client = httpx.Client(transport=httpx.MockTransport(peer))
    renderer = RemoteFilmRenderer('http://executor.invalid', TOKEN, client=client, poll_seconds=0)
    return renderer, peer, snapshot, release


def render(renderer, snapshot, release, destination, **options):
    return renderer.render(snapshot, destination, expected_snapshot_sha256=snapshot['sha256'],
        operation_id='whole-film', owner='owner', **release, **options)


def test_new_snapshot_uses_same_owner_scoped_journal_and_fixed_download(tmp_path):
    renderer, peer, snapshot, release = client_fixture()
    result = render(renderer, snapshot, release, tmp_path)
    assert (tmp_path / 'video.mp4').read_bytes() == VIDEO
    assert result['film_export']['clock']['sample_rate'] == 48000
    assert [request.method for request in peer.calls] == ['GET', 'PUT', 'GET']
    assert [request.url.path for request in peer.calls] == ['/v1/renders/whole-film'] * 2 + ['/v1/renders/whole-film/video']


@pytest.mark.parametrize('change', ['snapshot', 'runtime', 'hosts', 'image'])
def test_external_release_custody_is_checked_before_any_http_or_output(tmp_path, change):
    renderer, peer, snapshot, release = client_fixture()
    if change == 'snapshot': snapshot['production_run_id'] = 'another-run'
    elif change == 'runtime': release['runtime_fingerprint']['browser'] = 'different-browser'
    elif change == 'hosts': release['trusted_host_sha256']['hyperframes_film.mjs'] = 'a' * 64
    else: release['image'] = release['image'].replace(':latest@', ':wrong@')
    with pytest.raises(ServiceError):
        render(renderer, snapshot, release, tmp_path / 'untouched')
    assert peer.calls == [] and not (tmp_path / 'untouched').exists()


@pytest.mark.parametrize('change', ['rate', 'channels', 'duration', 'fps', 'width', 'runtime', 'film', 'bundle', 'clock', 'concat', 'receipt', 'state'])
def test_completed_legacy_or_rebound_receipts_cannot_be_downloaded(tmp_path, change):
    renderer, peer, snapshot, release = client_fixture()
    row = peer.metadata
    if change == 'rate': row['audio_sample_rate'] = 24000
    elif change == 'channels': row['audio_channels'] = 1
    elif change == 'duration': row['duration_seconds'] += .1
    elif change == 'fps': row['fps'] = 30
    elif change == 'width': row['width'] += 2
    elif change == 'runtime': row['runtime_fingerprint']['browser'] = 'other'
    elif change == 'film': row['film_export']['film_sha256'] = 'a' * 64
    elif change == 'bundle': row['film_export']['bundle_sha256'] = 'a' * 64
    elif change == 'clock': row['film_export']['clock']['sample_count'] -= 1
    elif change == 'concat': row['film_export']['concatenated_scene_videos'] = True
    elif change == 'receipt': del row['film_export']['audio_mix_sha256']
    else: peer.state = 'unknown-state'
    with pytest.raises(ServiceError, match='film_render_response_invalid'):
        render(renderer, snapshot, release, tmp_path)
    assert not any(request.url.path.endswith('/video') for request in peer.calls)


def test_cancel_binds_existing_identity_and_waits_for_terminal_cancellation(tmp_path):
    renderer, peer, snapshot, release = client_fixture()
    peer.state = 'running'
    event = threading.Event(); event.set()
    with pytest.raises(ServiceError, match='media_render_cancelled'):
        render(renderer, snapshot, release, tmp_path, cancel_event=event)
    assert [request.method for request in peer.calls] == ['GET', 'PUT', 'POST']
    assert peer.calls[-1].url.path == '/v1/renders/whole-film/cancel'
    assert not (tmp_path / 'video.mp4').exists()


def test_wrong_stream_digest_removes_the_partial_video(tmp_path):
    renderer, peer, snapshot, release = client_fixture()
    peer.video = b'x' * len(VIDEO)
    with pytest.raises(ServiceError, match='film_render_response_invalid'):
        render(renderer, snapshot, release, tmp_path)
    assert not (tmp_path / 'video.mp4').exists()


def test_existing_complete_operation_recovers_without_another_put(tmp_path):
    renderer, peer, snapshot, release = client_fixture()
    peer.exists = True
    render(renderer, snapshot, release, tmp_path)
    assert [request.method for request in peer.calls] == ['GET', 'GET']


def test_lost_admission_response_recovers_the_same_operation_without_a_second_put(tmp_path):
    snapshot, _, release = snapshot_fixture()
    peer = Peer(snapshot)
    def lose_admission_response(request):
        response = peer(request)
        if request.method == 'PUT':
            raise httpx.ReadError('fixture lost response after journal commit', request=request)
        return response
    renderer = RemoteFilmRenderer('http://executor.invalid', TOKEN,
        client=httpx.Client(transport=httpx.MockTransport(lose_admission_response)), poll_seconds=0)
    with pytest.raises(ServiceError, match='motion_render_service_unavailable'):
        render(renderer, snapshot, release, tmp_path)
    assert not (tmp_path / 'video.mp4').exists()
    render(renderer, snapshot, release, tmp_path)
    assert sum(request.method == 'PUT' for request in peer.calls) == 1
    assert (tmp_path / 'video.mp4').read_bytes() == VIDEO


def test_cancel_does_not_finish_until_the_existing_remote_operation_is_terminal(tmp_path):
    snapshot, _, release = snapshot_fixture()
    peer = Peer(snapshot)
    peer.exists, peer.state = True, 'running'
    cancel_requested = False
    def draining(request):
        nonlocal cancel_requested
        if request.method == 'POST':
            peer.calls.append(request)
            cancel_requested = True
            return httpx.Response(200, json=dict(operation_id='whole-film',
                snapshot_sha256=snapshot['sha256'], state='running'))
        if cancel_requested:
            peer.state = 'cancelled'
        return peer(request)
    renderer = RemoteFilmRenderer('http://executor.invalid', TOKEN,
        client=httpx.Client(transport=httpx.MockTransport(draining)), poll_seconds=0)
    event = threading.Event(); event.set()
    with pytest.raises(ServiceError, match='media_render_cancelled'):
        render(renderer, snapshot, release, tmp_path, cancel_event=event)
    assert [request.method for request in peer.calls] == ['GET', 'POST', 'GET']
