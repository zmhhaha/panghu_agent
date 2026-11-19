"""Remote source storage, frozen evidence and bounded stopped-role reads."""
from copy import deepcopy
import hashlib
import io
import json
from uuid import uuid4

from PIL import Image
import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.picture_learning_service import PictureLearningService
from video_workbench.video_learning_service import VideoLearningService
from test_picture_learning_service import Gateway, Renderer
from test_video_learning_service import Gateway as VideoGateway


URL = 'https://example.com/reference.mp4'
OWNER = 'developer'


class Remote:
    def __init__(self):
        self.describes, self.reads = [], []
        self.fail_once = False
        source = dict(schema='video.remote-reference-source.v1', source_type='video-url',
            provider='https-media', source_url=URL, source_id='https-media:fixture', title='Reference pond',
            width=1920, height=1080, duration_seconds=90., has_audio=False,
            source_identity_scope='provider_metadata', quality=dict(selection='highest_accessible',
                selected=dict(format_id='direct', width=1920, height=1080, fps=24, label='1080p'),
                available=[dict(format_id='direct', width=1920, height=1080, fps=24, label='1080p')]))
        self.source = dict(source, source_snapshot_sha256=digest(source))

    def describe(self, url, *, deadline_at=None):
        self.describes.append(url)
        return deepcopy(self.source)

    def observe(self, source, *, times, crop=None, quality='overview', deadline_at=None):
        self.reads.append(dict(source=deepcopy(source), times=list(times), crop=deepcopy(crop),
            quality=quality, deadline_at=deadline_at))
        if self.fail_once:
            self.fail_once = False
            raise ServiceError('remote_reference_fetch_failed', 502)
        frames = []
        requests = []
        for time in times:
            size = (1280, 720) if quality == 'detail' else (640, 360)
            output = io.BytesIO()
            color = (int(time * 10) % 256, 120 if crop else 60, 130 if quality == 'detail' else 40)
            Image.new('RGB', size, color).save(output, 'JPEG')
            raw = output.getvalue()
            frame_id = 'remote-' + str(int(time * 24)) + '-' + quality
            frames.append(dict(frame_id=frame_id, file_name=frame_id + '.jpg',
                content=raw, mime_type='image/jpeg', sha256=hashlib.sha256(raw).hexdigest(),
                width=size[0], height=size[1], requested_time=time, actual_pts=time,
                pts_ticks=int(time * 24), time_base=[1, 24], crop=deepcopy(crop), quality=quality,
                source_snapshot_sha256=source['source_snapshot_sha256']))
            requests.append(dict(requested_time=time, actual_pts=time, frame_id=frame_id,
                pts_ticks=int(time * 24), time_base=[1, 24], alias=False))
        return dict(source_snapshot=deepcopy(source), frames=frames,
            coverage=dict(scope='sampled_frames_only', source_snapshot_sha256=source['source_snapshot_sha256'],
                requested_samples=len(times), unique_presentation_frames=len(times),
                requests=requests,
                continuous_motion_verified=False, audio_verified=False))


class RequestGateway(Gateway):
    def __init__(self, request=None, **kwargs):
        super().__init__(**kwargs)
        self.request = request
        self.requested = False

    def collect(self, job_id, input_sha256, document):
        if self.request is not None and not self.requested:
            self.requested = True
            return dict(schema='video.picture-learning-result.v1', input_sha256=input_sha256,
                mode=document['mode'], evidence_scope='static_only', source=None,
                review=None, transfer_task=None, method=None, observation_request=deepcopy(self.request))
        return super().collect(job_id, input_sha256, document)


class AnalysisRequestGateway(VideoGateway):
    requested = False

    def collect(self, job_id, input_sha256, document):
        if document['mode'] == 'analyze' and not self.requested:
            self.requested = True
            return dict(schema='video.reference-learning-result.v1', input_sha256=input_sha256,
                mode='analyze', evidence_scope='sampled_frames_only', source=None, review=None,
                transfer_task=None, method=None, observation_request=dict(
                    reference_id=document['reference_sources'][0]['reference_id'],
                    times=[21, 22], quality='detail', reason='Inspect the material around the turn.'))
        return super().collect(job_id, input_sha256, document)


def picture_system(tmp_path, *, author=None):
    remote = Remote()
    service = PictureLearningService(tmp_path / 'pictures', author_gateway=author or RequestGateway(),
        review_gateway=RequestGateway(scores=[85]), renderer=Renderer(tmp_path / 'renders'),
        remote_media_adapter=remote)
    return service, remote


def register(service, *, picture=True, owner=OWNER, **fields):
    body = dict(request_id=uuid4().hex, source_type='video-url', source_url=URL)
    if picture:
        body['timestamp_seconds'] = 2
    body.update(fields)
    return service.upload_reference(owner, body), body


def start_picture(service, reference_id, **fields):
    body = dict(request_id=uuid4().hex, title='Material study', goal='Study the visible brush edge.',
        reference_ids=[reference_id], width=320, height=180, use_wikiskill=False,
        max_rounds=1, max_seconds=900)
    body.update(fields)
    return service.start(OWNER, body)


def test_register_only_retains_descriptor_and_metadata_gets_do_not_fetch(tmp_path):
    service, remote = picture_system(tmp_path)
    ref, body = register(service, crop=dict(x=.2, y=.1, width=.5, height=.6))
    assert ref['url'] is ref['media_url'] is None
    assert ref['source_identity_scope'] == 'provider_metadata'
    assert ref['observation_status'] == 'registered_not_observed'
    assert 'sha256' not in ref and 'observed_sha256' not in ref
    assert list(service.store.blobs.iterdir()) == []
    assert service.upload_reference(OWNER, body)['id'] == ref['id']
    service.catalogue(OWNER)
    assert remote.describes == [URL] and remote.reads == []
    assert service.catalogue(OWNER)['references'][0]['id'] == ref['id']
    with pytest.raises(ServiceError) as error:
        service.upload_reference(OWNER, dict(body, timestamp_seconds=3))
    assert error.value.status == 409
    with pytest.raises(ServiceError) as error:
        service.reference_artifact(OWNER, ref['id'])
    assert error.value.code == 'remote_reference_not_stored_as_media'
    assert remote.reads == []


@pytest.mark.parametrize('fields', [dict(timestamp_seconds=-1), dict(timestamp_seconds=90),
    dict(crop=dict(x=.6, y=0, width=.5, height=1)), dict(crop=dict(x=0, y=0, width=True, height=1))])
def test_remote_locator_rejects_invalid_times_and_crop(tmp_path, fields):
    service, _ = picture_system(tmp_path)
    with pytest.raises(ServiceError):
        register(service, **fields)
    assert service.store.list('references', OWNER) == []


def test_picture_takes_one_detail_frame_and_freezes_it_for_all_role_operations(tmp_path):
    service, remote = picture_system(tmp_path)
    ref, _ = register(service)
    project = start_picture(service, ref['id'])
    assert len(remote.reads) == 1 and remote.reads[0]['quality'] == 'detail'
    view = service.inspect(OWNER, project['id'], project['latest_experiment_id'])
    frozen = view['frozen']
    observed = frozen['remote_reference_observations'][ref['id']]['frames'][0]
    assert observed['width'] == 1280 and 'media_sha256' not in observed
    assert observed['source_snapshot_sha256'] == ref['source_snapshot_sha256']
    shown = project['references'][0]
    assert shown['frame_url'].endswith('reference-' + observed['sha256'])
    path, mime = service.artifact(OWNER, project['id'], 'reference-' + observed['sha256'])
    assert mime == 'image/jpeg' and hashlib.sha256(path.read_bytes()).hexdigest() == observed['sha256']
    with pytest.raises(ServiceError) as error:
        service.artifact('other-owner', project['id'], 'reference-' + observed['sha256'])
    assert error.value.status == 404
    for _ in range(20):
        service.advance(OWNER, project['latest_experiment_id'])
    assert remote.reads == [remote.reads[0]]
    current = service.get(OWNER, project['id'])
    assert current['state'] == 'completed'
    assert service.author.uploads[0][0]['reference_sources'][0]['reference_id'] == ref['id']
    assert all(frame.get('source_identity_scope') == 'provider_metadata'
        for frame in service.author.uploads[0][0]['observation']['frames'])
    coverage = frozen['remote_reference_observations'][ref['id']]['coverage']
    assert coverage['requests'][0]['frame_id'] == observed['frame_id']
    supplied = service.author.uploads[0][0]['observation']['coverage']['supplied_reference_frame_map'][0]
    assert supplied['source_frame_id'] == observed['frame_id']


def test_stopped_author_handoff_reads_detail_and_resumes_same_round(tmp_path):
    author = RequestGateway()
    service, remote = picture_system(tmp_path, author=author)
    ref, _ = register(service)
    author.request = dict(reference_id=ref['id'], times=[3, 4], quality='detail',
        crop=dict(x=.1, y=.1, width=.4, height=.4), reason='Inspect the dry edge in detail.')
    project = start_picture(service, ref['id'])
    eid = project['latest_experiment_id']
    service.advance(OWNER, eid)  # submit first author
    assert len(remote.reads) == 1
    service.advance(OWNER, eid)  # stopped author is collected, then source read
    run = service.store.read('experiments', OWNER, eid)
    assert run['round'] == 1 and run['stage'] == 'author'
    assert len(run['remote_observations']) == 1
    read = service.store.read('operations', OWNER, run['remote_observations'][0]['operation_id'])
    assert read['stage'] == 'reference_observation' and read['status'] == 'completed'
    assert read['stop_proven'] is True
    assert author.submissions == [eid + '-1']
    service.advance(OWNER, eid)
    assert len(author.submissions) == 2
    document = author.uploads[-1][0]
    assert len(document['observation_request_history']) == 1
    assert any(frame.get('crop') for frame in document['observation']['frames'])
    assert document['observation']['coverage']['supplemental_requests'] == 1
    assert len(document['observation']['frames']) <= service.protocol.MAX_FRAMES


def test_fetch_retry_and_stop_do_not_resubmit_model_or_poll_reference_as_a_job(tmp_path):
    author = RequestGateway()
    service, remote = picture_system(tmp_path, author=author)
    ref, _ = register(service)
    author.request = dict(reference_id=ref['id'], times=[3], quality='detail', reason='Inspect a detail.')
    project = start_picture(service, ref['id'])
    eid = project['latest_experiment_id']
    service.advance(OWNER, eid)
    remote.fail_once = True
    service.advance(OWNER, eid)
    run = service.store.read('experiments', OWNER, eid)
    pending = run['remote_observation_pending']['operation_id']
    assert service.store.read('operations', OWNER, pending)['status'] == 'retryable'
    assert len(author.submissions) == 1
    service.stop(OWNER, project['id'])
    service.advance(OWNER, eid)
    final = service.store.read('experiments', OWNER, eid)
    assert final['state'] == 'cancelled' and final['stop_proven'] is True
    assert service.store.read('operations', OWNER, pending)['status'] == 'cancelled'
    assert len(remote.reads) == 2 and len(author.submissions) == 1
    assert author.lookups == []


def test_technical_retry_reuses_evidence_and_fresh_experiment_fetches_again(tmp_path):
    author = RequestGateway()
    service, remote = picture_system(tmp_path, author=author)
    ref, _ = register(service)
    project = start_picture(service, ref['id'])
    eid = project['latest_experiment_id']
    service.advance(OWNER, eid)
    author.receipt_missing = True
    service.advance(OWNER, eid)
    assert service.get(OWNER, project['id'])['state'] == 'failed'
    prior = service.store.read('experiments', OWNER, eid)
    retry = service.experiment(OWNER, project['id'], dict(request_id=uuid4().hex, mode='retry'))
    assert len(remote.reads) == 1
    run = service.store.read('experiments', OWNER, retry['latest_experiment_id'])
    assert run['frozen_sha256'] == prior['frozen_sha256']
    service.stop(OWNER, project['id'])
    service.advance(OWNER, retry['latest_experiment_id'])
    fresh = service.experiment(OWNER, project['id'], dict(request_id=uuid4().hex, mode='fresh'))
    assert len(remote.reads) == 2 and fresh['state'] == 'queued'


def test_video_remote_clip_and_independent_analysis_use_same_source_without_storing_video(tmp_path):
    remote = Remote()
    author, reviewer = VideoGateway(), AnalysisRequestGateway()
    service = VideoLearningService(tmp_path / 'videos', author_gateway=author,
        review_gateway=reviewer, renderer=object(), remote_media_adapter=remote)
    ref, _ = register(service, picture=False)
    project = service.start(OWNER, dict(request_id=uuid4().hex, title='Pond turn', goal='Inspect the turn.',
        reference_ids=[ref['id']], start_seconds=21, end_seconds=29, use_wikiskill=False))
    assert remote.reads[0]['times'] == list(range(21, 29))
    frozen = service.inspect(OWNER, project['id'], project['latest_experiment_id'])['frozen']
    assert len(frozen['reference_observation']['frames']) == 8
    assert frozen['reference_sources'][0]['start_seconds'] == 21
    assert service.catalogue(OWNER)['projects'][0]['reference_url'] is None
    assert all(not path.read_bytes().startswith(b'\x00\x00\x00') for path in service.store.blobs.iterdir())
    analysis = service.analyze_reference(OWNER, ref['id'], dict(request_id=uuid4().hex, max_seconds=900))
    for _ in range(12):
        analysis = service.advance(OWNER, analysis['id'])
        if analysis['state'] in {'failed', 'cancelled', 'completed'}:
            break
    assert analysis['state'] == 'completed', analysis
    assert len(analysis['observation_request_history']) == 1
    assert len(reviewer.submissions) == 2 and reviewer.submissions[0] != reviewer.submissions[1]
    assert reviewer.uploads[-1][0]['observation_request_history'][0]['request']['times'] == [21, 22]
    assert len(reviewer.uploads[-1][0]['observation']['frames']) <= service.protocol.MAX_FRAMES
    observed = analysis['reference_observation']
    path, mime = service.analysis_reference_artifact(OWNER, ref['id'], analysis['id'], observed['observed_sha256'])
    assert mime == 'image/jpeg' and path.is_file()
    with pytest.raises(ServiceError) as error:
        service.analysis_reference_artifact('other-owner', ref['id'], analysis['id'], observed['observed_sha256'])
    assert error.value.status == 404
    with pytest.raises(ServiceError):
        service.analysis_reference_artifact(OWNER, ref['id'], analysis['id'], '0' * 64)


def test_role_view_prioritizes_detail_without_overrunning_existing_frame_contract(tmp_path):
    service, _ = picture_system(tmp_path)
    ref, _ = register(service)
    project = start_picture(service, ref['id'])
    run = service.store.read('experiments', OWNER, project['latest_experiment_id'])
    observed = service._observe_remote(run['frozen']['references'][0], times=list(range(3, 11)), quality='detail')
    run['remote_observations'] = [dict(operation_id='read-1',
        request=dict(reference_id=ref['id'], times=list(range(3, 11)), quality='detail', reason='Inspect detail.'),
        observation=observed)]
    document = service._document(run, 'create')
    assert len(document['observation']['frames']) == 8
    assert document['observation']['coverage']['retained_supplemental_frames'] == 8
    assert any(frame['actual_pts'] == 2 for frame in document['observation']['frames'])
    assert any(frame['actual_pts'] == 10 for frame in document['observation']['frames'])
    service.protocol.validate_input(document)


def test_initial_observation_spends_original_deadline_and_expiry_starts_no_model(tmp_path):
    service, remote = picture_system(tmp_path)
    now = [1000.]
    service.clock = lambda: now[0]
    ref, _ = register(service)
    observe = remote.observe
    def slow_observe(*args, **kwargs):
        assert kwargs['deadline_at'] == 1060
        result = observe(*args, **kwargs)
        now[0] = 1061.
        return result
    remote.observe = slow_observe
    project = start_picture(service, ref['id'], max_seconds=60)
    eid = project['latest_experiment_id']
    assert service.store.read('experiments', OWNER, eid)['deadline_at'] == 1060
    service.advance(OWNER, eid)
    assert service.get(OWNER, project['id'])['state'] == 'completed'
    assert service.author.submissions == []


def test_pending_observation_reconciles_read_without_another_paid_submission(tmp_path):
    author = RequestGateway()
    service, remote = picture_system(tmp_path, author=author)
    ref, _ = register(service)
    author.request = dict(reference_id=ref['id'], times=[3], quality='detail', reason='Inspect a detail.')
    project = start_picture(service, ref['id'])
    eid = project['latest_experiment_id']
    service.advance(OWNER, eid)
    remote.fail_once = True
    service.advance(OWNER, eid)
    assert len(author.submissions) == 1
    service.advance(OWNER, eid)  # completes the same read, without a model POST
    assert len(author.submissions) == 1
    assert len(service.store.read('experiments', OWNER, eid)['remote_observations']) == 1
    service.advance(OWNER, eid)
    assert len(author.submissions) == 2


def test_recent_detail_does_not_discard_other_selected_reference_anchors(tmp_path):
    service, _ = picture_system(tmp_path)
    refs = [register(service, timestamp_seconds=time)[0] for time in (2, 3, 4)]
    project = start_picture(service, refs[0]['id'], reference_ids=[ref['id'] for ref in refs])
    run = service.store.read('experiments', OWNER, project['latest_experiment_id'])
    ref = run['frozen']['references'][1]
    observed = service._observe_remote(ref, times=list(range(5, 13)), quality='detail')
    run['remote_observations'] = [dict(operation_id='read-1',
        request=dict(reference_id=ref['id'], times=list(range(5, 13)), quality='detail', reason='Inspect detail.'),
        observation=observed)]
    document = service._document(run, 'create')
    assert len(document['observation']['frames']) == service.protocol.MAX_FRAMES
    assert {frame['reference_id'] for frame in document['observation']['frames']} == {ref['id'] for ref in refs}
    assert {frame['actual_pts'] for frame in document['observation']['frames']} >= {2, 3, 4, 12}
    service.protocol.validate_input(document)
