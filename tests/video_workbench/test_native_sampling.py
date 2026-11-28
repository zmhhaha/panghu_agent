"""Business sampling preserves supplied bytes, clocks and original opinions."""
from copy import deepcopy
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from agent_service.contracts import ServiceError,canonical_bytes
from video_workbench.native_sampling import prepare_input_frames,read_input_frames,verify_input_delivery
from video_workbench.visual_sampling import VisualSamplingService
from video_workbench.video_learning_media import VideoLearningMedia
from video_workbench.video_learning_store import VideoLearningStore
from test_picture_learning_service import system,start,finish,png
from test_video_learning_service import system as video_system,real_mp4


def receipt(frames,files):
    return dict(evidence_scope='completed_upstream_transport',images=[dict(
        sha256=frame['sha256'],media_type='image/png' if files[frame['file_name']].startswith(b'\x89PNG') else 'image/jpeg',
        byte_length=len(files[frame['file_name']]),model_request_sha256='a'*64,
        input_item_index=0,part_index=index,model_request_completed=True)
        for index,frame in enumerate(frames)])


def test_all_roles_and_repeated_pixels_are_independent_model_inputs(tmp_path):
    sampler=VisualSamplingService(tmp_path/'sampling')
    raw=png();sha=hashlib.sha256(raw).hexdigest()
    frames=[dict(frame_id=role,file_name=role+'.png',sha256=sha,purpose=role,actual_pts=index,
        clip_time=index) for index,role in enumerate(['reference','baseline','output'])]
    document=dict(observation=dict(frames=frames),criteria=['Keep the original criterion.'])
    original=deepcopy(document)
    files={f['file_name']:raw for f in frames}|{'input.json':canonical_bytes(document)}
    reference,copied=prepare_input_frames(sampler,'owner','operation',document,files)
    assert document==original and copied==files
    manifest=sampler.get('owner',reference['operation_id'])
    assert [f['frame_id'] for f in manifest['frames']]==['reference','baseline','output']
    assert [f['timestamp_seconds'] for f in manifest['frames']]==[0,1,2]
    assert len({f['sha256'] for f in manifest['frames']})==1
    assert read_input_frames(sampler,'owner',reference,document,files)==files
    with pytest.raises(ServiceError,match='business_input_changed'):
        read_input_frames(sampler,'owner',reference,document,files,expected_operation_id='another-operation')
    delivered=receipt(frames,files)
    assert verify_input_delivery(sampler,document,files,delivered)['image_parts']==3
    delivered['images'].pop()
    with pytest.raises(ServiceError,match='delivery_incomplete'):
        verify_input_delivery(sampler,document,files,delivered)
    with pytest.raises(ServiceError,match='not_found'):
        sampler.get('other-owner',reference['operation_id'])
    changed=deepcopy(document);changed['observation']['frames'].reverse()
    with pytest.raises(ServiceError,match='operation_conflict'):
        prepare_input_frames(sampler,'owner','operation',changed,files)


def test_learning_decode_runs_once_and_keeps_half_open_pts_and_same_pixel_frames(tmp_path,monkeypatch):
    sampler=VisualSamplingService(tmp_path/'sampling');store=VideoLearningStore(tmp_path/'learning')
    media=VideoLearningMedia(tmp_path/'decoder');source=tmp_path/'source.mp4'
    source.write_bytes(b'fixture media whose decoded frames are supplied below')
    stream=io.BytesIO();Image.new('RGB',(320,180),(20,30,40)).save(stream,format='JPEG')
    calls=[]
    def decode(path,target,requested,*,timeout):
        calls.append(requested);target.write_bytes(stream.getvalue())
        return dict(time_seconds=requested,pts=round(requested*1000),time_base=[1,1000])
    monkeypatch.setattr(media,'_decode',decode)
    args=dict(media_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),start_seconds=0,
        end_seconds=2,store=store,sample_count=4,sampling_service=sampler,
        sampling_owner='owner',sampling_operation_id='reference')
    observed=media.sample(source,**args)
    assert calls==[0,.5,1,1.5]
    assert len(observed['frames'])==4 and observed['coverage']['aliased_samples']==0
    assert len({f['sha256'] for f in observed['frames']})==1
    assert [f['actual_pts'] for f in observed['frames']]==[0,.5,1,1.5]
    assert media.sample(source,**args)==observed and len(calls)==4
    manifest=sampler.get('owner','reference')
    assert manifest['policy']['strategy']=='learning_half_open'
    assert manifest['policy']['bounded_jpeg_qualities']==[85,75,60,45]
    assert [f['original']['evidence_kind'] for f in manifest['frames']]==['decoded_media_observation']*4


def test_picture_pipeline_samples_all_roles_without_changing_original_evaluator(system,tmp_path,monkeypatch):
    service,author,reviewer,renderer=system
    renderer.sampling_service=VisualSamplingService(tmp_path/'shared')
    for gateway in [author,reviewer]:
        def actual_receipt(job_id,gateway=gateway):
            document=gateway._job(job_id)['document']
            files=next(files for candidate,files in gateway.uploads if candidate==document)
            return receipt(document['observation']['frames'],files)
        monkeypatch.setattr(gateway,'model_frame_receipt',actual_receipt)
    project,_=start(service)
    completed=finish(service,project)
    assert completed['state']=='completed'
    assert [c['review']['score'] for c in completed['candidates']]==[60,85]
    assert author.uploads[0][0]['schema']=='video.picture-learning-input.v1'
    assert any(f['purpose']=='baseline' for f in author.uploads[1][0]['observation']['frames'])
    operations=service.store.list('operations','developer')
    sampled=[op for op in operations if op.get('sampling_ref')]
    assert len(sampled)==4
    for op in sampled:
        assert op['input_sha256']==hashlib.sha256(canonical_bytes(op['input'])).hexdigest()
        assert 'sampling_ref' not in op['input']
        assert op['sampling_delivery']['image_parts']==len(op['input']['observation']['frames'])
        assert op['result']['schema']=='video.picture-learning-result.v1'
    candidate=completed['candidates'][0]
    frames=renderer.sampling_service.read_frames('developer',candidate['sampling_ref']['operation_id'])
    assert [f['frame_id'] for f in frames]==['original','model-copy']
    assert frames[0]['raw']==frames[1]['raw']
    assert frames[1]['transformation']=='unchanged'


def test_remote_retained_input_wait_is_only_in_explicit_prepare(tmp_path):
    local=VisualSamplingService(tmp_path/'sampling')
    calls=[]
    class Remote:
        def prepare_frames(self,owner,operation,source,policy,frames,*,deadline_at=None):
            calls.append('prepare')
            self.reference=local.prepare(owner,operation,source,policy,provider=lambda *args:dict(source=source,frames=frames))
            return dict(state='queued')
        def get(self,owner,operation):
            calls.append('get');return dict(state='completed')
        def read_manifest(self,owner,operation):return local.get(owner,operation)
        def read_frames(self,owner,operation):return local.read_frames(owner,operation)
    remote=Remote();raw=png();frame=dict(file_name='a.png',sha256=hashlib.sha256(raw).hexdigest())
    document=dict(observation=dict(frames=[frame]));files={'a.png':raw}
    reference,_=prepare_input_frames(remote,'owner','operation',document,files)
    before=list(calls)
    assert read_input_frames(remote,'owner',reference,document,files)==files
    assert calls==before==['prepare','get']


def test_method_native_sampling_preserves_short_clip_policy_and_reads_retained_bytes(tmp_path,monkeypatch):
    from video_workbench.animation_method_renderer import MethodAnimationRenderer
    from video_workbench import scene_space_renderer
    monkeypatch.setattr(scene_space_renderer,'scene_renderer',lambda owner:SimpleNamespace(lookup=lambda operation:None))
    renderer=MethodAnimationRenderer.__new__(MethodAnimationRenderer)
    renderer.sampling_service=VisualSamplingService(tmp_path/'sampling')
    renderer._directory=lambda operation:tmp_path/'render'
    monkeypatch.setattr(renderer,'lookup',lambda operation:dict(stop_proven=True,technical_passed=True,
        media=dict(sha256='a'*64),evidence_sha256='b'*64))
    monkeypatch.setattr(renderer,'stopping_proof',lambda operation:dict(stop_proven=True,operation_id=operation))
    directory=tmp_path/'render/work/frames';directory.mkdir(parents=True)
    stream=io.BytesIO();Image.new('RGB',(32,18),(20,30,40)).save(stream,format='JPEG')
    raw=stream.getvalue();rows=[]
    for index in range(3):
        name=str(index)+'.jpg';(directory/name).write_bytes(raw);rows.append(dict(name=name))
    (directory.parent/'render-receipt.json').write_bytes(canonical_bytes(dict(frame_count=3,frames=rows)))
    reference=renderer.prepare_sampling('owner','render',max_frames=12)
    manifest=renderer.sampling_service.get('owner',reference['operation_id'])
    assert manifest['policy']['strategy']=='method_uniform'
    assert len(manifest['frames'])==1 and manifest['frames'][0]['timestamp_seconds']==0
    (directory/'0.jpg').write_bytes(b'old renderer file changed after frozen sampling')
    assert renderer.read_sampling('owner',reference)==[dict(raw=raw,mime='image/jpeg',timestamp_seconds=0)]


def test_picture_transform_preserves_original_and_actual_jpeg_copy(tmp_path):
    import base64
    from random import Random
    from video_workbench.picture_learning_service import PictureLearningService
    sampler=VisualSamplingService(tmp_path/'sampling')
    service=PictureLearningService(tmp_path/'picture',author_gateway=None,review_gateway=None,
        renderer=SimpleNamespace(sampling_service=sampler))
    stream=io.BytesIO();Image.frombytes('RGB',(1024,1024),Random(0).randbytes(1024*1024*3)).save(stream,format='PNG')
    raw=stream.getvalue();assert len(raw)>2*1024*1024
    public=service.upload_reference('owner',dict(request_id='upload',file_name='reference.png',
        mime_type='image/png',content_base64=base64.b64encode(raw).decode('ascii')))
    row=service.store.read('references','owner',public['id'])
    rows=sampler.read_frames('owner',row['sampling_ref']['operation_id'])
    assert rows[0]['raw']==raw and rows[0]['mime']=='image/png'
    assert rows[1]['mime']=='image/jpeg' and rows[1]['raw'].startswith(b'\xff\xd8\xff')
    assert rows[1]['raw']==service.store.read_blob(row['observed_sha256'])
    assert rows[1]['derived_from_sha256']==hashlib.sha256(raw).hexdigest()


def test_new_picture_review_contract_is_rejected_without_creating_work(system):
    from video_workbench.learning_review_reliability import CONTRACT
    service,author,reviewer,renderer=system
    with pytest.raises(ServiceError,match='review_standard_change_disabled_use_existing_standard'):
        start(service,review_contract=CONTRACT)
    assert service.store.list('experiments','developer')==[]
    assert author.submissions==reviewer.submissions==renderer.calls==[]


def test_new_effect_review_contract_is_rejected_before_creation(tmp_path):
    from video_workbench.learning_review_reliability import CONTRACT
    from test_effect_classified_review_service import system as effect_system
    from test_animation_method_service import start as effect_start
    service,author,legacy,reviewer,renderer=effect_system(tmp_path)
    with pytest.raises(ServiceError,match='review_standard_change_disabled_use_existing_standard'):
        effect_start(service,review_contract=CONTRACT)
    assert service.commissions.list_all('developer')==[]
    assert author.submissions==legacy.submissions==reviewer.submissions==renderer.calls==[]


def test_video_analysis_uses_shared_sampling_for_overview_and_stopped_role_detail(tmp_path,monkeypatch):
    from uuid import uuid4
    from video_workbench.video_learning_service import VideoLearningService
    from test_remote_reference_services import Remote,AnalysisRequestGateway,register
    from test_picture_learning_service import Renderer,Gateway
    reviewer=AnalysisRequestGateway();remote=Remote();sampler=VisualSamplingService(tmp_path/'sampling')
    renderer=Renderer(tmp_path/'renders');renderer.sampling_service=sampler
    service=VideoLearningService(tmp_path/'videos',author_gateway=Gateway(),review_gateway=reviewer,
        renderer=renderer,remote_media_adapter=remote)
    def actual_receipt(job_id):
        document=reviewer._job(job_id)['document']
        files=next(files for candidate,files in reviewer.uploads if candidate==document)
        return receipt(document['observation']['frames'],files)
    monkeypatch.setattr(reviewer,'model_frame_receipt',actual_receipt)
    ref,_=register(service,picture=False)
    analysis=service.analyze_reference('developer',ref['id'],dict(request_id=uuid4().hex))
    for _ in range(10):
        current=service.advance('developer',analysis['id'])
        if current['state']=='completed':break
    assert current['state']=='completed',current
    assert len(remote.reads)==2 and len(remote.reads[0]['times'])==8
    assert remote.reads[1]['times']==[21,22]
    stored=service.store.analysis('developer',analysis['id'])
    assert stored['role_operations'][0]['sampling_delivery']['image_parts']==8
    assert stored['sampling_delivery']['image_parts']==10
    assert stored['input']['schema']=='video.reference-learning-input.v1'
    assert 'sampling_ref' not in stored['input']
    manifest=sampler.get('developer',stored['sampling_ref']['operation_id'])
    assert len(manifest['frames'])==10
    before=len(remote.reads)
    service.get_analysis('developer',ref['id'],analysis['id'])
    sampler.read_frames('developer',stored['sampling_ref']['operation_id'])
    assert len(remote.reads)==before


def test_complete_effect_uses_original_opinion_with_native_and_model_input_sampling(tmp_path,monkeypatch):
    import json
    from video_workbench import animation_method_renderer as module
    from video_workbench.animation_method_service import AnimationMethodService
    from test_animation_method_renderer import SyntheticExecution,IMAGE
    from test_animation_method_service import Gateway,start as begin,finish as end
    native_execution=SyntheticExecution()
    stream=io.BytesIO();Image.new('RGB',(1280,720),(30,40,50)).save(stream,format='JPEG')
    image=stream.getvalue();image_sha=hashlib.sha256(image).hexdigest()
    def execute(directory):
        native_execution(directory)
        path=Path(directory)/'work/render-receipt.json'
        value=json.loads(path.read_bytes())
        rows=value['frames']+value.get('seek_checks',{}).get('probes',[])
        for frame in rows:
            (path.parent/'frames'/frame['name']).write_bytes(image)
            frame.update(sha256=image_sha,bytes=len(image))
        path.write_bytes(canonical_bytes(value))
    monkeypatch.setattr(module,'execute_method_render',execute)
    outcomes=[]
    for enabled in [False,True]:
        directory=tmp_path/str(enabled)
        sampler=VisualSamplingService(directory/'sampling') if enabled else None
        renderer=module.MethodAnimationRenderer(directory/'renders',IMAGE,enabled=True,sampling_service=sampler)
        author,reviewer=Gateway(),Gateway(True);uploads=[];native_calls=[]
        original_prepare,original_read=renderer.prepare_sampling,renderer.read_sampling
        def prepare(*args,**kwargs):
            native_calls.append('prepare');return original_prepare(*args,**kwargs)
        def read(*args,**kwargs):
            native_calls.append('read');return original_read(*args,**kwargs)
        monkeypatch.setattr(renderer,'prepare_sampling',prepare)
        monkeypatch.setattr(renderer,'read_sampling',read)
        old_upload=reviewer.upload
        def upload(document,files):
            uploads.append((deepcopy(document),deepcopy(files)));return old_upload(document,files)
        monkeypatch.setattr(reviewer,'upload',upload)
        def actual_receipt(job_id):
            document=reviewer._job(job_id)['document']
            files=next(files for candidate,files in uploads if candidate==document)
            return receipt(document['observation']['frames'],files)
        monkeypatch.setattr(reviewer,'model_frame_receipt',actual_receipt)
        service=AnimationMethodService(directory/'effect',author_gateway=author,review_gateway=reviewer,renderer=renderer)
        created=begin(service,duration_seconds=1,use_wikiskill=False)
        completed=end(service,created['id'])
        assert completed['state']=='completed',completed
        run=service.commissions.get('developer',created['id'])
        opinion=next(op for op in run['operations'] if op['stage']=='review')
        assert opinion['input']['schema']=='video.animation-method-input.v1'
        assert opinion['result']['schema']=='video.animation-method-result.v1'
        assert len(opinion['input']['observation']['frames'])==12
        result=deepcopy(opinion['result']['review']);result.pop('preferred_candidate_id')
        outcomes.append((run['stop_reason'],result))
        if enabled:
            assert native_calls==['prepare','read']
            assert opinion['sampling_delivery']['image_parts']==12
            frames=sampler.read_frames('developer',opinion['sampling_ref']['operation_id'])
            assert len(frames)==12 and all(frame['raw']==image for frame in frames)
            assert [frame['timestamp_seconds'] for frame in frames]==[
                frame['timestamp_seconds'] for frame in opinion['input']['observation']['frames']]
            assert 'sampling_ref' not in opinion['input']
        else:
            assert native_calls==['prepare'] and 'sampling_ref' not in opinion
    assert outcomes[0]==outcomes[1] and outcomes[0][0]=='goal_met'


@pytest.mark.parametrize('status',[409,503])
def test_default_picture_sampling_failure_keeps_stopped_render_and_terminates(system,tmp_path,monkeypatch,status):
    service,author,reviewer,renderer=system
    sampler=VisualSamplingService(tmp_path/'sampling');renderer.sampling_service=sampler
    original=sampler.prepare
    def prepare(owner,operation,source,policy,**kwargs):
        if source.get('provider')=='picture-observation' and source['context'].get('origin')=='native_renderer_png':
            raise ServiceError('fixture_picture_sampling_failed',status)
        return original(owner,operation,source,policy,**kwargs)
    monkeypatch.setattr(sampler,'prepare',prepare)
    def delivered(job_id):
        document=author._job(job_id)['document']
        files=next(files for candidate,files in author.uploads if candidate==document)
        return receipt(document['observation']['frames'],files)
    monkeypatch.setattr(author,'model_frame_receipt',delivered)
    project,_=start(service);completed=finish(service,project)
    assert completed['state']=='failed' and completed['experiments'][0]['stop_reason']=='sampling_failed'
    run=service.store.read('experiments','developer',completed['latest_experiment_id'])
    render=next(service.store.read('operations','developer',op) for op in run['operation_ids']
        if service.store.read('operations','developer',op)['stage']=='render')
    assert render['status']=='failed' and render['result']['technical_passed'] is True
    assert render['result']['stop_proven'] is True and render['error']=='fixture_picture_sampling_failed'
    assert completed['candidates'][0]['artifacts']['picture']['sha256']==render['result']['sha256']
    assert len(renderer.calls)==1 and reviewer.submissions==[]
    service.advance('developer',run['id'])
    assert len(renderer.calls)==1


def test_default_video_sampling_failure_keeps_media_and_does_not_reconcile_forever(video_system,real_mp4,tmp_path,monkeypatch):
    from test_video_learning_service import start as begin,finish as end
    service,author,reviewer,renderer=video_system
    renderer.sampling_service=VisualSamplingService(tmp_path/'sampling')
    sample=service._sample_media
    def sampling(owner,operation,*args,**kwargs):
        if operation.startswith('output-'):raise ServiceError('fixture_video_sampling_failed',409)
        return sample(owner,operation,*args,**kwargs)
    monkeypatch.setattr(service,'_sample_media',sampling)
    def delivered(job_id):
        document=author._job(job_id)['document']
        files=next(files for candidate,files in author.uploads if candidate==document)
        return receipt(document['observation']['frames'],files)
    monkeypatch.setattr(author,'model_frame_receipt',delivered)
    project,_=begin(service,real_mp4);completed=end(service,project)
    assert completed['state']=='failed' and completed['experiments'][0]['stop_reason']=='sampling_failed'
    run=service.store.read('experiments','developer',completed['latest_experiment_id'])
    render=next(service.store.read('operations','developer',identity) for identity in run['operation_ids']
        if service.store.read('operations','developer',identity)['stage']=='render')
    assert render['status']=='failed' and render['result']['technical_passed'] is True
    assert render['result']['stop_proven'] is True
    assert completed['candidates'][0]['artifacts']['media']['sha256']==render['result']['sha256']
    assert service.store.read_blob(render['result']['sha256'])==real_mp4
    service.advance('developer',run['id'])
    assert len(renderer.calls)==1 and reviewer.submissions==[]


@pytest.mark.parametrize('remote',[False,True])
def test_reference_sampling_crash_recovers_same_request_identity_deadline_and_completed_frames(tmp_path,monkeypatch,remote):
    import base64
    import sqlite3
    import time
    from uuid import uuid4
    from video_workbench.video_learning_service import VideoLearningService
    from test_remote_reference_services import Remote,register
    from test_picture_learning_service import Renderer
    from test_video_learning_service import Gateway
    sampler=VisualSamplingService(tmp_path/'sampling');renderer=Renderer(tmp_path/'renders')
    renderer.sampling_service=sampler;provider=Remote();media=VideoLearningMedia(tmp_path/'media')
    monkeypatch.setattr(media,'probe',lambda path:dict(width=320,height=180,duration_seconds=2,
        has_audio=False,mime_type='video/mp4'))
    stream=io.BytesIO();Image.new('RGB',(320,180),(20,30,40)).save(stream,format='JPEG')
    decodes=[]
    def decode(path,target,requested,*,timeout):
        decodes.append(requested);target.write_bytes(stream.getvalue())
        return dict(time_seconds=requested,pts=round(requested*1000),time_base=[1,1000])
    monkeypatch.setattr(media,'_decode',decode)
    author,reviewer=Gateway(),Gateway();root=tmp_path/'videos'
    def factory():return VideoLearningService(root,author_gateway=author,review_gateway=reviewer,
        renderer=renderer,media_adapter=media,remote_media_adapter=provider)
    service=factory()
    if remote:ref,_=register(service,picture=False)
    else:
        ref=service.upload_reference('developer',dict(request_id=uuid4().hex,file_name='reference.mp4',
            mime_type='video/mp4',content_base64=base64.b64encode(b'fixture source media').decode('ascii')))
    body=dict(request_id='recover-original-request',title='One original study',goal='Keep the original evidence.',
        reference_ids=[ref['id']],width=320,height=180,start_seconds=0,end_seconds=2,
        use_wikiskill=False,max_rounds=1,max_seconds=3600)
    original_put=service.store.put
    def crash(table,value,**kwargs):
        if table=='projects':raise OSError('Process interrupted after completed reference sampling')
        return original_put(table,value,**kwargs)
    monkeypatch.setattr(service.store,'put',crash)
    with pytest.raises(OSError,match='interrupted'):
        service.start('developer',body)
    assert len(provider.reads)==(1 if remote else 0) and len(decodes)==(0 if remote else 8)
    with sqlite3.connect(service.store.path) as db:
        intent=__import__('json').loads(db.execute('SELECT document FROM video_preparations').fetchone()[0])
    with sqlite3.connect(sampler.path) as db:count=db.execute('SELECT COUNT(*) FROM samples').fetchone()[0]
    restarted=factory();monkeypatch.setattr(restarted,'clock',lambda:intent['created_at']+10)
    project=restarted.start('developer',body)
    run=restarted.store.read('experiments','developer',project['latest_experiment_id'])
    assert run['id']==intent['identities']['experiment_id']
    assert run['created_at']==intent['created_at'] and run['deadline_at']==intent['deadline_at']
    assert len(provider.reads)==(1 if remote else 0) and len(decodes)==(0 if remote else 8)
    with sqlite3.connect(sampler.path) as db:assert db.execute('SELECT COUNT(*) FROM samples').fetchone()[0]==count
    assert author.submissions==reviewer.submissions==renderer.calls==[]
    restarted.start('developer',dict(body,request_id='distinct-learning-request'))
    assert len(provider.reads)==(2 if remote else 0) and len(decodes)==(0 if remote else 16)
    with sqlite3.connect(sampler.path) as db:assert db.execute('SELECT COUNT(*) FROM samples').fetchone()[0]==count+1
