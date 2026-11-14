"""Durable scheduler/API fixtures, not evidence of a real cluster or media."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench import hyperframes_film_execution as E
from video_workbench import hyperframes_film_k8s as K
from video_workbench import motion_render_service as S
from video_workbench.motion_render_k8s import ExecutionJournal, operation_key
from test_hyperframes_film_snapshot import snapshot_fixture
from test_hyperframes_film_execution import arguments, fixture_export

OWNER = hashlib.sha256(b'film-owner').hexdigest()
TOKEN = 'a' * 48


def admission(release):
    value = dict(schema=K.ADMISSION_SCHEMA, status='admitted', scope=K.ADMISSION_SCOPE,
        **deepcopy(release), evidence={name: dict(reference='fixture-only/' + name, sha256='e' * 64)
            for name in ('job', 'media', 'cancellation')})
    value['sha256'] = digest(value)
    return value


class Kube:
    namespace = 'film-tests'
    def __init__(self, journal, snapshot, release, pvc, *, lost=False, commit=True, complete=True):
        self.journal, self.snapshot, self.release, self.pvc = journal, snapshot, release, pvc
        self.lost, self.commit, self.complete = lost, commit, complete
        self.job, self.calls, self.deleting = None, [], False
        self.pods_pending = 0
        self.on_job_get = None
        self.policies = [dict(spec=dict(podSelector={}, policyTypes=['Ingress', 'Egress'], ingress=[], egress=[]))]

    def install(self, body):
        self.job = deepcopy(body)
        self.job['metadata']['uid'] = 'fixture-uid'
        self.job['status'] = {'succeeded': 1} if self.complete else {'active': 1}
        if self.complete:
            inputs = self.pvc / E.INPUT_PREFIX / self.snapshot['sha256']
            directory = self.pvc / E.OUTPUT_PREFIX / self.current_key
            scratch = self.pvc / 'fixture-scratch'
            scratch.mkdir(exist_ok=True)
            E.run_staged_film(inputs, directory, scratch, exporter=lambda *a, **k: fixture_export(self.snapshot),
                **arguments(self.snapshot, self.release))

    def request(self, method, resource, name=None, *, body=None, params=None, logs=False):
        self.calls.append((method, resource, name))
        if resource == 'networkpolicies':
            return {'items': self.policies}
        if method == 'POST':
            assert resource == 'jobs'
            self.current_key = body['metadata']['labels'][E.LABEL + 'execution']
            # The full key is in the existing scene row, never a new journal.
            with self.journal.connect() as db:
                row = dict(db.execute('SELECT * FROM motion_scenes').fetchone())
            assert row['state'] == 'submitting'
            self.current_key = row['key'].removesuffix(':film')
            if self.commit:
                self.install(body)
            if self.lost:
                self.lost = False
                raise ServiceError(K.UNAVAILABLE, 503)
            return deepcopy(self.job)
        if method == 'DELETE':
            assert resource == 'jobs' and body['preconditions']['uid'] == self.job['metadata']['uid']
            self.deleting = True
            return {'status': 'Success'}
        if resource == 'jobs':
            if self.on_job_get is not None:
                self.on_job_get(self)
            if self.deleting:
                self.job = None
            return deepcopy(self.job)
        if resource == 'pods':
            if self.job is None and self.pods_pending <= 0:
                return {'items': []}
            if self.job is None:
                self.pods_pending -= 1
                metadata = self.last_metadata
            else:
                metadata = self.last_metadata = deepcopy(self.job['metadata'])
            state = {'terminated': {'exitCode': 0}} if self.complete else {'running': {}}
            return {'items': [dict(metadata=metadata, status=dict(containerStatuses=[dict(
                name='renderer', imageID='containerd://' + self.release['runtime_fingerprint']['image_digest'],
                state=state)]))]}
        raise AssertionError((method, resource, name))


def setup(tmp_path, monkeypatch, **options):
    snapshot, _, release = snapshot_fixture()
    pvc, root = tmp_path / 'pvc', tmp_path / 'runs'
    pvc.mkdir()
    root.mkdir()
    journal = ExecutionJournal(tmp_path / 'journal.db')
    kube = Kube(journal, snapshot, release, pvc, **options)
    renderer = K.KubeFilmRenderer(journal, kube, image=release['image'], release_id='test-release',
        runtime=release['runtime_fingerprint'], trusted_host_sha256=release['trusted_host_sha256'],
        admission=admission(release), pvc_root=pvc, pvc_name='fixture-pvc', root=root,
        pvc_workload_selector={'app': 'film-pvc-helper'},
        poll_seconds=.001, cleanup_seconds=.01)
    # Explicit nonmedia integrity fixture. The independent decoder boundary is
    # exercised separately below, and actual execution belongs to cluster QA.
    def fake_media(path, snapshot, **kwargs):
        return dict(sha256=E.sha(path.read_bytes()), bytes=path.stat().st_size,
            duration_seconds=snapshot['clock']['frame_count'] / 24)
    monkeypatch.setattr(K, 'verify_film_media', fake_media)
    key = operation_key(OWNER, 'film-1')
    return snapshot, release, journal, kube, renderer, key


def render(snapshot, renderer, key, **options):
    return renderer.render(snapshot, renderer.root / key / 'result', key=key, **options)


def test_lost_create_response_recovers_exact_job_and_delivers_one_whole_film(tmp_path, monkeypatch):
    snapshot, release, journal, kube, renderer, key = setup(tmp_path, monkeypatch, lost=True)
    result = render(snapshot, renderer, key)
    assert [call[:2] for call in kube.calls].count(('POST', 'jobs')) == 1
    assert result['visual_mode'] == 'hyperframes-film' and result['audio_sample_rate'] == 48000
    assert result['audio_channels'] == 2 and result['film_export']['concatenated_scene_videos'] is False
    assert result['film_export']['clock']['sample_count'] == snapshot['clock']['frame_count'] * 2000
    assert result['film_export']['admission_sha256'] == admission(release)['sha256']
    assert journal.scene(key + ':film')['state'] == 'completed' and kube.job is None
    assert (renderer.root / key / 'result' / 'video.mp4').read_bytes() == b'fixture-not-media'
    assert render(snapshot, renderer, key)['sha256'] == result['sha256']
    assert [call[:2] for call in kube.calls].count(('POST', 'jobs')) == 1


def test_unknown_submission_does_not_rerender_or_complete_cancellation_on_404(tmp_path, monkeypatch):
    snapshot, _, journal, kube, renderer, key = setup(tmp_path, monkeypatch,
        lost=True, commit=False, complete=False)
    for _ in range(2):
        with pytest.raises(ServiceError, match=K.UNAVAILABLE):
            render(snapshot, renderer, key)
    assert [call[:2] for call in kube.calls].count(('POST', 'jobs')) == 1
    assert journal.scene(key + ':film')['state'] == 'submitting'
    cancel = SimpleNamespace(is_set=lambda: True)
    with pytest.raises(ServiceError, match=K.UNAVAILABLE):
        render(snapshot, renderer, key, cancel_event=cancel)
    assert journal.scene(key + ':film')['state'] == 'cleanup_pending'
    with pytest.raises(ServiceError, match=K.UNAVAILABLE):
        renderer.cleanup(snapshot, key=key, state='cancelled', error='media_render_cancelled', status=409)
    # The original uncertain POST finally appears. The same recorded identity
    # is drained; no new submission or suspended render/fence is created.
    resources, _, _ = renderer._context(snapshot, key)
    kube.install(resources['job'])
    renderer.cleanup(snapshot, key=key, state='cancelled', error='media_render_cancelled', status=409)
    assert journal.scene(key + ':film')['state'] == 'cancelled'
    assert kube.job is None and [call[:2] for call in kube.calls].count(('POST', 'jobs')) == 1


def test_crashed_service_publish_recovers_partial_owned_copy_without_another_job(tmp_path, monkeypatch):
    snapshot, _, journal, kube, renderer, key = setup(tmp_path, monkeypatch)
    first = render(snapshot, renderer, key)
    (renderer.root / key / 'result/video.pending').write_bytes(b'partial service copy after crash')
    result = render(snapshot, renderer, key)
    assert result['sha256'] == first['sha256']
    assert (renderer.root / key / 'result/video.mp4').read_bytes() == b'fixture-not-media'
    assert not (renderer.root / key / 'result/video.pending').exists()
    assert [call[:2] for call in kube.calls].count(('POST', 'jobs')) == 1
    assert journal.scene(key + ':film')['state'] == 'completed'


def test_cancellation_waits_for_job_and_pods_and_recovers_after_restart(tmp_path, monkeypatch):
    snapshot, _, journal, kube, renderer, key = setup(tmp_path, monkeypatch, complete=False)
    cancelled = {'value': False}
    def cancel_after_submit(instance):
        if instance.job is not None:
            cancelled['value'] = True
            instance.last_metadata = deepcopy(instance.job['metadata'])
            instance.pods_pending = 100
            instance.on_job_get = None
    kube.on_job_get = cancel_after_submit
    with pytest.raises(ServiceError, match=K.UNAVAILABLE):
        render(snapshot, renderer, key, cancel_event=SimpleNamespace(is_set=lambda: cancelled['value']))
    assert kube.job is None and journal.scene(key + ':film')['state'] == 'cleanup_pending'
    kube.pods_pending = 0
    renderer.cleanup(snapshot, key=key, state='cancelled', error='media_render_cancelled', status=409)
    assert journal.scene(key + ':film')['state'] == 'cancelled'


def test_foreign_job_is_never_deleted_or_adopted(tmp_path, monkeypatch):
    snapshot, _, journal, kube, renderer, key = setup(tmp_path, monkeypatch, complete=False)
    resources, handle, _ = renderer._context(snapshot, key)
    journal.scene_intent(handle, resources['binding'], resources['job']['metadata']['name'])
    kube.current_key = key
    kube.install(resources['job'])
    kube.job['metadata']['annotations'][K.ANNOTATION] = 'b' * 64
    with pytest.raises(ServiceError, match=K.UNAVAILABLE):
        render(snapshot, renderer, key)
    assert not any(method == 'DELETE' for method, _, _ in kube.calls)
    assert journal.scene(handle)['state'] == 'cleanup_pending'


def test_matching_allow_policy_blocks_execution_even_with_deny_all(tmp_path, monkeypatch):
    snapshot, _, _, kube, renderer, key = setup(tmp_path, monkeypatch)
    kube.policies.append(dict(spec=dict(podSelector=dict(matchLabels={E.LABEL + 'execution': key[:32]}),
        policyTypes=['Egress'], egress=[{}])))
    with pytest.raises(ServiceError, match='namespace_not_isolated'):
        renderer.check_policy()
    assert not any(method == 'POST' for method, _, _ in kube.calls)


@pytest.mark.parametrize('key', ['job-name', 'batch.kubernetes.io/job-name', 'controller-uid',
    'batch.kubernetes.io/controller-uid'])
def test_job_controller_auto_labels_cannot_bypass_additive_policy_check(tmp_path, monkeypatch, key):
    _, _, _, kube, renderer, _ = setup(tmp_path, monkeypatch)
    kube.policies.append(dict(spec=dict(podSelector=dict(matchExpressions=[dict(key=key, operator='Exists')]),
        policyTypes=['Egress'], egress=[{}])))
    with pytest.raises(ServiceError, match='namespace_not_isolated'):
        renderer.check_policy()


@pytest.mark.parametrize('selector', [
    dict(matchLabels={'job-name': 'a-different-job'}),
    dict(matchExpressions=[dict(key='job-name', operator='In', values=['a-different-job'])]),
    dict(matchExpressions=[dict(key='job-name', operator='NotIn', values=['a-different-job'])]),
])
def test_deny_must_definitely_select_renderer_not_merely_possibly_match(tmp_path, monkeypatch, selector):
    _, _, _, kube, renderer, _ = setup(tmp_path, monkeypatch)
    kube.policies = [dict(spec=dict(podSelector=selector, policyTypes=['Ingress', 'Egress'], ingress=[], egress=[]))]
    with pytest.raises(ServiceError, match='namespace_not_isolated'):
        renderer.check_policy()


def test_admission_requires_release_bound_job_media_and_cancellation_evidence():
    _, _, release = snapshot_fixture()
    for kind in ('missing', 'scope', 'runtime', 'evidence'):
        value = admission(release)
        if kind == 'missing':
            value = None
        elif kind == 'scope':
            value['scope'] = 'human_approved'
        elif kind == 'runtime':
            value['runtime_fingerprint']['browser'] = 'different'
        else:
            value['evidence'].pop('cancellation')
        with pytest.raises(ServiceError, match='admission_required'):
            K.verify_execution_admission(value, **release)


def service_for(journal, renderer, release):
    legacy = SimpleNamespace(scene_renderer=SimpleNamespace(check_policy=lambda: None),
        render=lambda *a, **k: pytest.fail('Film must not use old scene concatenation'))
    return S.MotionExecutionService(journal, legacy, root=renderer.root, token=TOKEN,
        runtime=release['runtime_fingerprint'], image=release['image'], release_id='fixture',
        film_renderer=renderer)


def test_service_dispatches_new_schema_keeps_owner_and_serves_result(tmp_path, monkeypatch):
    snapshot, release, journal, _, renderer, key = setup(tmp_path, monkeypatch)
    service = service_for(journal, renderer, release)
    with TestClient(S.create_app(service, start_worker=False)) as client:
        headers = {'Authorization': 'Bearer ' + TOKEN, 'X-Video-Owner': OWNER}
        assert client.put('/v1/renders/film-1', headers=headers, json={'snapshot': snapshot}).status_code == 200
        service.tick()
        operation = client.get('/v1/renders/film-1', headers=headers).json()
        assert operation['state'] == 'completed'
        assert operation['result']['visual_mode'] == 'hyperframes-film'
        assert client.get('/v1/renders/film-1/video', headers=headers).content == b'fixture-not-media'
        assert client.get('/v1/renders/film-1', headers={**headers, 'X-Video-Owner': 'b' * 64}).status_code == 404
        assert journal.scene(key + ':film')['state'] == 'completed'


def test_intake_busy_rejects_before_reading_or_validating_body(tmp_path, monkeypatch):
    _, release, journal, _, renderer, _ = setup(tmp_path, monkeypatch)
    service = service_for(journal, renderer, release)
    service.intake_lock.acquire()
    try:
        with TestClient(S.create_app(service, start_worker=False)) as client:
            response = client.put('/v1/renders/film-1', headers={
                'Authorization': 'Bearer ' + TOKEN, 'X-Video-Owner': OWNER}, content=b'not JSON')
            assert response.status_code == 429 and response.json()['error'] == 'motion_render_intake_busy'
    finally:
        service.intake_lock.release()


def test_film_schema_disabled_by_default_and_reserves_pending_storage(tmp_path, monkeypatch):
    snapshot, release, journal, _, renderer, _ = setup(tmp_path, monkeypatch)
    service = service_for(journal, renderer, release)
    service.film_renderer = None
    with pytest.raises(ServiceError, match='execution_disabled'):
        service.submit(OWNER, 'film-1', snapshot)
    service.film_renderer = renderer
    service.max_storage_bytes = S.FILM_RESERVATION_BYTES * 2
    service.submit(OWNER, 'film-1', snapshot)
    with pytest.raises(ServiceError, match='storage_exceeded'):
        service.submit(OWNER, 'film-2', snapshot)
    assert service.submit(OWNER, 'film-1', snapshot)['state'] == 'queued'


@pytest.mark.parametrize('tamper', [None, 'width', 'frames', 'fps', 'channels', 'sample_rate', 'start', 'duration'])
def test_independent_probe_validates_frozen_clock_and_audio_before_full_decode(tmp_path, monkeypatch, tamper):
    snapshot, _, _ = snapshot_fixture()
    path = tmp_path / 'fixture.mp4'
    path.write_bytes(b'explicit-nonmedia-process-fixture')
    seconds = snapshot['clock']['frame_count'] / 24
    video = dict(codec_type='video', codec_name='h264', width=snapshot['width'],
        height=snapshot['height'], avg_frame_rate='24/1', nb_frames=str(snapshot['clock']['frame_count']),
        duration=f'{seconds:.6f}', start_time='0.000000')
    audio = dict(codec_type='audio', codec_name='aac', sample_rate='48000', channels=2,
        duration=f'{seconds:.6f}', start_time='0.000000')
    probe = dict(streams=[video, audio], format=dict(duration=f'{seconds:.3f}', start_time='0.000000'))
    if tamper == 'width':
        video['width'] += 2
    elif tamper == 'frames':
        video['nb_frames'] = str(snapshot['clock']['frame_count'] - 1)
    elif tamper == 'fps':
        video['avg_frame_rate'] = '25/1'
    elif tamper == 'channels':
        audio['channels'] = 1
    elif tamper == 'sample_rate':
        audio['sample_rate'] = '44100'
    elif tamper == 'start':
        audio['start_time'] = '0.25'
    elif tamper == 'duration':
        video['duration'] = str(seconds + 1)
    commands = []
    monkeypatch.setattr(K.media, '_binary', lambda name, configured: name)
    def run(command, **kwargs):
        commands.append(command)
        return json.dumps(probe).encode() if command[0] == 'ffprobe' else b''
    monkeypatch.setattr(K.media, '_run', run)
    if tamper is not None:
        with pytest.raises(ServiceError, match='hyperframes_film_media_invalid'):
            K.verify_film_media(path, snapshot)
        assert len(commands) == 1
    else:
        result = K.verify_film_media(path, snapshot)
        assert abs(result['duration_seconds'] - seconds) <= 1e-5
        assert result['sha256'] == E.sha(path.read_bytes())
        assert commands[1][0] == 'ffmpeg' and '-xerror' in commands[1]
