"""Real service state and encoded PNG inputs; synthetic model/render outcomes."""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def historical_frozen_contract_fixture(monkeypatch):
    # These synthetic fixtures reconstruct retained classified admissions.
    # New public-request rejection is tested independently in test_native_sampling.
    monkeypatch.setattr('video_workbench.learning_review_reliability.require_existing_review_standard',
        lambda choice:None)

from agent_service.contracts import ServiceError
from agent_service.profiles import video_picture_review_v2 as protocol
from video_workbench.learning_review_reliability import CONTRACT, MODEL_LIMITS
from video_workbench.picture_learning_service import PictureLearningService
from video_workbench.scene_space_renderer import render_capability
from test_picture_learning_service import Gateway, start, finish
from test_scene_space_learning_service import SceneGateway, SceneRenderer
from test_learning_review_reliability import png, route, opinion, receipt


class KnownAuthor(SceneGateway):
    def poll(self, job_id):
        job = super().poll(job_id)
        job['usage'] = dict(completeness='complete', input_tokens=30, output_tokens=20,
            cache_read_tokens=0, cache_write_tokens=0, request_count=1)
        return job


class LosslessRenderer(SceneRenderer):
    noisy = False
    def capabilities(self):
        return dict(super().capabilities(), scene_space=render_capability())
    def render(self, *args, **kwargs):
        value = super().render(*args, **kwargs)
        if value.get('technical_passed') is not True:
            return value
        raw = png(noisy=self.noisy); Path(value['picture_path']).write_bytes(raw)
        value.update(width=1280, height=720, sha256=hashlib.sha256(raw).hexdigest())
        self.results[args[0]] = deepcopy(value)
        return value


class ClassifiedGateway(Gateway):
    profile = protocol.PROFILE
    profile_revision = '1'
    blocked = False
    bad_transport = False
    def prepare_submission(self, operation_id, document, deadline_at, refs, *, model_limits=None):
        assert model_limits == MODEL_LIMITS
        admit = super().prepare_submission(operation_id, document, deadline_at, refs)
        def submit():
            try:
                job = admit()
            finally:
                self.jobs[operation_id]['model_limits'] = deepcopy(model_limits)
            job['model_limits'] = deepcopy(model_limits)
            return job
        return submit
    def preflight(self, _deadline): return route()
    def upload(self, document, files):
        protocol.validate_input(document)
        assert all(hashlib.sha256(files[f['file_name']]).hexdigest()==f['sha256'] for f in document['observation']['frames'])
        self.uploads.append((deepcopy(document), deepcopy(files))); return []
    def collect(self, job_id, checksum, document):
        result = opinion(document, status='unresolved' if self.blocked else 'satisfied')
        protocol.validate_result(result, checksum, document)
        return result
    def model_frame_receipt(self, job_id):
        document, files = next((d, f) for d, f in self.uploads if d == self._job(job_id)['document'])
        blobs = {row['sha256']: files[row['file_name']] for row in document['observation']['frames']}
        value = receipt(document, blobs, job_id)
        if self.bad_transport: value['images'][0]['byte_length'] += 1
        return value


def system(tmp_path):
    author, legacy, classified = KnownAuthor(), Gateway(), ClassifiedGateway()
    renderer = LosslessRenderer(tmp_path/'renders')
    service = PictureLearningService(tmp_path/'pictures', author_gateway=author,
        review_gateway=legacy, classified_review_gateway=classified, renderer=renderer)
    return service, author, legacy, classified, renderer


def begin(service, **changes):
    return start(service, width=1280, height=720, max_rounds=2, max_seconds=3600,
        allow_probes=False, allow_collaboration=False, review_contract=CONTRACT, **changes)[0]


def test_picture_new_contract_uses_original_png_and_advisory_does_not_reauthor(tmp_path):
    service, author, legacy, reviewer, renderer = system(tmp_path)
    project = begin(service)
    completed = finish(service, project)
    assert completed['state']=='completed'
    run = service.store.read('experiments','developer', completed['latest_experiment_id'])
    candidate = run['candidates'][0]
    assert candidate['review_decision']['passed'] is True and candidate['review']['findings'][0]['severity']=='advisory'
    assert len(author.submissions)==len(renderer.calls)==len(reviewer.submissions)==1 and legacy.submissions==[]
    output = next(row for row in reviewer.uploads[0][0]['observation']['frames'] if row['purpose']=='output')
    assert reviewer.uploads[0][1][output['file_name']]==service.store.read_blob(candidate['picture_sha256'])
    assert candidate['observed_sha256']==candidate['picture_sha256']
    assert run['frozen']['classified_review']['profile']==protocol.PROFILE
    assert run['transfer_status']=='awaiting_approval' and completed['adopted_candidate_id'] is None


def test_required_unresolved_is_not_promoted_by_high_score(tmp_path):
    service, author, legacy, reviewer, renderer = system(tmp_path); reviewer.blocked=True
    completed = finish(service, begin(service))
    run=service.store.read('experiments','developer',completed['latest_experiment_id'])
    assert all(row['review_decision']['passed'] is False for row in run['candidates'])
    assert len(reviewer.submissions)==len(renderer.calls)==2 and legacy.submissions==[]
    assert completed['adopted_candidate_id'] is None


def test_lower_score_passing_candidate_displaces_high_score_required_blocker(tmp_path):
    service,_,_,reviewer,_=system(tmp_path);reviews=iter([('unresolved',99),('satisfied',90)])
    def collect(job,checksum,document):
        status,score=next(reviews);result=opinion(document,status=status);result['review']['score']=score
        return result
    reviewer.collect=collect
    completed=finish(service,begin(service));run=service.store.read('experiments','developer',completed['latest_experiment_id'])
    blocked,passed=run['candidates']
    assert blocked['review']['score']==99 and blocked['review_decision']['passed'] is False
    assert passed['review']['score']==90 and passed['review_decision']['passed'] is True
    assert run['best_candidate_id']==completed['best_candidate_id']==passed['id']
    assert run['reproduction_stop_reason']=='classified_required_criteria_satisfied'
    assert completed['adopted_candidate_id'] is None


def test_continue_retains_native_png_baseline_and_does_not_replace_passing_project_best_with_blocker(tmp_path):
    service,_,_,reviewer,_=system(tmp_path)
    initial=finish(service,begin(service));baseline=initial['best_candidate_id']
    reviewer.blocked=True
    collect=reviewer.collect
    def high_score(*args):
        result=collect(*args);result['review']['score']=99;return result
    reviewer.collect=high_score
    continued=service.next_experiment('developer',initial['id'],dict(request_id='continue-native-baseline',mode='continue',
        base_candidate_id=baseline,review_contract=CONTRACT,max_rounds=1))
    finished=finish(service,continued);run=service.store.read('experiments','developer',finished['latest_experiment_id'])
    assert finished['state']=='completed' and finished['best_candidate_id']==baseline
    assert run['candidates'][0]['review']['score']==99 and run['candidates'][0]['review_decision']['passed'] is False
    assert any(row['purpose']=='baseline' and row['file_name'].endswith('.png') for row in reviewer.uploads[-1][0]['observation']['frames'])


def test_manual_transfer_can_explicitly_select_new_review_without_automatic_validation(tmp_path):
    service,_,legacy,reviewer,_=system(tmp_path);initial=finish(service,begin(service))
    assert len(reviewer.submissions)==1
    transferred=service.next_experiment('developer',initial['id'],dict(request_id='approved-new-contract-transfer',
        mode='transfer',approve_candidate=True,base_candidate_id=initial['best_candidate_id'],review_contract=CONTRACT))
    finished=finish(service,transferred);run=service.store.read('experiments','developer',finished['latest_experiment_id'])
    assert finished['state']=='completed' and run['work_kind']=='transfer'
    assert run['frozen']['classified_review']['contract']==CONTRACT
    assert len(reviewer.submissions)==2 and len(legacy.submissions)==1
    assert legacy.jobs[legacy.submissions[0]]['document']['mode']=='transfer_plan'
    assert all(row['purpose']=='output' for row in reviewer.uploads[-1][0]['observation']['frames'])


@pytest.mark.parametrize('cause',['missing_gateway','wrong_profile','missing_capture'])
def test_explicit_selection_rejects_unavailable_contract_before_paid_claim(tmp_path,cause):
    service, author, _, reviewer, renderer = system(tmp_path)
    if cause=='missing_gateway':service.classified_reviewer=None
    elif cause=='wrong_profile':reviewer.profile='video.picture-learning-review.v1'
    else:
        caps=renderer.capabilities();caps['scene_space'].pop('sample_capture')
        renderer.capabilities=lambda:deepcopy(caps)
    with pytest.raises(ServiceError):begin(service)
    assert author.submissions==reviewer.submissions==renderer.calls==[]
    assert service.store.list('experiments','developer')==[]


def test_bad_actual_transport_does_not_create_a_passing_gate(tmp_path):
    service, _, _, reviewer, _ = system(tmp_path); reviewer.bad_transport=True
    completed=finish(service, begin(service))
    run=service.store.read('experiments','developer',completed['latest_experiment_id'])
    assert completed['state']=='failed' and not run['candidates'][0].get('review_decision')


def test_model_oversize_keeps_original_png_without_upload_or_jpeg_fallback(tmp_path):
    service, _, _, reviewer, renderer = system(tmp_path);renderer.noisy=True
    project=begin(service)
    completed = finish(service, project)
    run=service.store.read('experiments','developer',project['latest_experiment_id']);candidate=run['candidates'][0]
    assert completed['state']=='failed' and run['error']['code']=='learning_review_model_image_too_large'
    assert len(service.store.read_blob(candidate['picture_sha256']))>2*1024*1024
    assert candidate['picture_sha256']==candidate['observed_sha256'] and reviewer.uploads==reviewer.submissions==[]


def test_new_v2_technical_retry_retains_the_complete_frozen_contract(tmp_path):
    service, _, legacy, reviewer, _ = system(tmp_path); reviewer.bad_transport=True
    completed=finish(service,begin(service));before=service.store.read('experiments','developer',completed['latest_experiment_id'])
    retried=service.next_experiment('developer',completed['id'],dict(request_id='retry-v2',mode='retry'))
    after=service.store.read('experiments','developer',retried['latest_experiment_id'])
    assert after['frozen']['classified_review']==before['frozen']['classified_review']
    assert after['technical_retry_context']['input']['constraints']['learning_review_binding']==before['frozen']['classified_review']
    reviewer.bad_transport=False
    finished=finish(service,retried)
    assert finished['state']=='completed' and legacy.submissions==[]


def test_legacy_retry_does_not_select_v2_when_a_new_gateway_is_available(tmp_path):
    service, author, legacy, reviewer, _ = system(tmp_path);author.receipt_missing=True
    project,_=start(service,width=1280,height=720,allow_probes=False,allow_collaboration=False,max_rounds=1)
    failed=finish(service,project)
    author.receipt_missing=False
    retried=service.next_experiment('developer',failed['id'],dict(request_id='retry-v1',mode='retry'))
    run=service.store.read('experiments','developer',retried['latest_experiment_id'])
    assert 'classified_review' not in run['frozen']
    finished=finish(service,retried)
    assert finished['state']=='completed' and legacy.submissions and reviewer.submissions==[]


@pytest.mark.parametrize('cause',['capture','route','limits'])
def test_route_revision_or_capture_provenance_change_blocks_before_new_review_upload(tmp_path,cause):
    service, _, _, reviewer, _ = system(tmp_path);project=begin(service)
    for _ in range(20):
        service.advance('developer',project['latest_experiment_id'])
        run=service.store.read('experiments','developer',project['latest_experiment_id'])
        if run['stage']=='review':break
    if cause=='capture':
        run['candidates'][0]['review_image_provenance'][0]['capture']['relative_frame']=1
        service.store.put('experiments',run)
    elif cause=='route':reviewer.preflight=lambda _:dict(route(),profile_revision='2')
    else:reviewer.preflight=lambda _:dict(route(),model_request_limits={})
    finished=finish(service,project)
    assert finished['state']=='failed' and reviewer.uploads==[]


@pytest.fixture(scope='module')
def lossless_video_media(tmp_path_factory):
    import shutil,subprocess
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):pytest.skip('actual FFmpeg required')
    path=tmp_path_factory.mktemp('classified-media')/'reference.mp4'
    result=subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',
        'color=c=gray:size=1280x720:rate=24:duration=1','-c:v','libx264','-pix_fmt','yuv420p',
        '-an','-threads','1',str(path)],capture_output=True,timeout=30)
    assert result.returncode==0
    return path.read_bytes()


def video_system(tmp_path, media):
    from test_video_learning_service import Gateway as VideoGateway,Renderer as VideoRenderer
    from test_scene_space_learning import carrier
    from video_workbench.video_learning_service import VideoLearningService
    from video_workbench.scene_space_capture import SAMPLE_CAPTURE_POLICY
    from agent_service.contracts import digest
    from test_learning_review_reliability import capture
    class Author(VideoGateway):
        def poll(self,job_id):
            job=super().poll(job_id)
            job['usage']=dict(completeness='complete',input_tokens=30,output_tokens=20,
                cache_read_tokens=0,cache_write_tokens=0,request_count=1)
            return job
        def collect(self,job,checksum,document):
            result=super().collect(job,checksum,document)
            if document['mode']=='create':
                offer=document['constraints']['scene_space_learning']
                result['method']['scene_space']=carrier(scope=offer['source_scope'],refs=offer['source_references'],
                    frames=offer['source_frame_ids'],plan=True,viewport=dict(width=1280,height=720),duration_frames=24)
            return result
    class Renderer(VideoRenderer):
        def capabilities(self):return dict(super().capabilities(),scene_space=render_capability())
        def validate_scene_request(self,identity,source,document,*,scene_space,deadline_at=None):
            return dict(request_sha256=digest(dict(source=source,document=document,plan=scene_space)))
        def render(self,identity,source,document,*,scene_space=None,expected_request_sha256=None,deadline_at=None):
            assert scene_space is not None and document['constraints']['sample_capture']==SAMPLE_CAPTURE_POLICY
            assert expected_request_sha256==self.validate_scene_request(identity,source,document,scene_space=scene_space)['request_sha256']
            result=super().render(identity,source,document,deadline_at=deadline_at)
            result.update(width=1280,height=720,duration_seconds=1,sample_capture=deepcopy(SAMPLE_CAPTURE_POLICY),
                sample_frame_range=dict(start_frame=0,end_frame=24))
            self.results[identity]=deepcopy(result)
            return result
        def frames(self,_identity,max_frames=3):
            raw=png()
            return [dict(raw=raw,mime='image/png',timestamp_seconds=absolute/24,capture=capture(raw,absolute))
                    for absolute in [0,12,23]]
    author,legacy,reviewer=Author(),VideoGateway(),ClassifiedGateway()
    renderer=Renderer(tmp_path/'video-renders',media)
    service=VideoLearningService(tmp_path/'videos',author_gateway=author,review_gateway=legacy,
        classified_review_gateway=reviewer,renderer=renderer)
    return service,author,legacy,reviewer,renderer


def test_video_decoded_reference_jpeg_and_original_renderer_png_have_separate_provenance(tmp_path,lossless_video_media,monkeypatch):
    from test_video_learning_service import start as video_start,finish as video_finish
    service,author,legacy,reviewer,renderer=video_system(tmp_path,lossless_video_media)
    calls=[];sample=service.media.sample
    def observed(*args,**kwargs):calls.append(kwargs['media_sha256']);return sample(*args,**kwargs)
    monkeypatch.setattr(service.media,'sample',observed)
    project,_=video_start(service,lossless_video_media,width=1280,height=720,end_seconds=1,
        learn_audio=False,review_contract=CONTRACT,allow_probes=False,allow_collaboration=False)
    completed=video_finish(service,project)
    assert completed['state']=='completed'
    run=service.store.read('experiments','developer',completed['latest_experiment_id']);candidate=run['candidates'][0]
    assert len(calls)==1,'selected output must never be decoded again into JPEG'
    document,files=reviewer.uploads[0]
    assert len(document['observation']['frames'])==6
    for frame in document['observation']['frames']:
        if frame['purpose']=='output':assert frame['file_name'].endswith('.png') and files[frame['file_name']].startswith(b'\x89PNG')
        else:assert frame['file_name'].endswith('.jpg') and files[frame['file_name']].startswith(b'\xff\xd8')
    assert [r['absolute_frame'] for r in candidate['review_image_provenance']]==[0,12,23]
    assert candidate['review_decision']['passed'] is True and legacy.submissions==[]
    assert completed['continuous_motion_verified'] is completed['audio_verified'] is False


@pytest.mark.parametrize('cause',['missing','jpeg','capture_time','malformed_row'])
def test_video_missing_or_lossy_renderer_capture_blocks_before_review_upload(tmp_path,lossless_video_media,cause):
    from test_video_learning_service import start as video_start,finish as video_finish
    service,_,legacy,reviewer,renderer=video_system(tmp_path,lossless_video_media)
    frames=renderer.frames
    def changed(*args,**kwargs):
        rows=frames(*args,**kwargs)
        if cause=='missing':return rows[:-1]
        if cause=='malformed_row':rows[0].pop('capture');return rows
        if cause=='jpeg':rows[0]['mime']='image/jpeg'
        else:rows[0]['timestamp_seconds']=0.2
        return rows
    renderer.frames=changed
    project,_=video_start(service,lossless_video_media,width=1280,height=720,end_seconds=1,
        learn_audio=False,review_contract=CONTRACT,allow_probes=False,allow_collaboration=False)
    completed=video_finish(service,project)
    assert completed['state']=='failed' and reviewer.uploads==reviewer.submissions==legacy.submissions==[]
