"""Private transport custody and scheduling; fixture bytes are not a video."""
from copy import deepcopy
import base64
import hashlib
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from agent_service.contracts import ServiceError,digest
from agent_service.profiles.video_animation_create import source_sha256
from video_workbench.animation_creation_http import CreationHTTPServer
from video_workbench.scene_space_plan import freeze_plan
from video_workbench.scene_space_render_http import (MAX_PREVIEW_FRAME_BYTES,PREFIX,
    SceneSpaceRenderClient,SceneSpaceRenderService)


def image_bytes(size=(1280,720),image_format='PNG'):
    stream=io.BytesIO()
    Image.new('RGB',size,(30,50,70)).save(stream,format=image_format)
    return stream.getvalue()


@pytest.fixture
def render_request():
    plan=json.loads((Path(__file__).resolve().parents[1]/'fixtures/scene_space/plan.json').read_text(encoding='utf-8'))
    source=dict(engine='canvas',strategy=dict(description='Transport fixture',tradeoffs='Not a media acceptance test'),
        files={'scene.html':'<canvas data-scene-space-output></canvas><script src="scene.js"></script>',
            'scene.js':'window.__sceneSpaceArtwork={paintObject(ctx,obj){ctx.fillRect(0,0,10,10)}};',
            'scene.css':'canvas{display:block}'})
    source['source_sha256']=source_sha256(source)
    return dict(source=source,document=dict(constraints=dict(width=1280,height=720,duration_seconds=4)),scene_space=plan)


class FixtureRenderer:
    def __init__(self,root):
        self.root=root;self.calls=[];self.results={};self.samples={};self.frame_calls=[];self.uncertain=False
    def capabilities(self):return dict(admitted=True,engines=['canvas'])
    def validate_request(self,*args,**kwargs):return None
    def render(self,identity,source,document,*,scene_space,**kwargs):
        self.calls.append(identity)
        if self.uncertain:return dict(status='unknown',stop_proven=False)
        raw=b'INERT TRANSPORT FIXTURE';path=self.root/(identity+'.fixture');path.write_bytes(raw)
        result=dict(status='rendered',technical_passed=True,stop_proven=True,
            source_sha256=source['source_sha256'],scene_space_sha256='c'*64,
            scene_space=dict(plan_sha256=freeze_plan(scene_space)['plan_sha256']),
            sha256=hashlib.sha256(raw).hexdigest(),artifact_path=str(path),
            mime='video/mp4' if 'duration_seconds' in document['constraints'] else 'image/png',fps=24,
            frame_count=(kwargs['frame_range']['end_frame']-kwargs['frame_range']['start_frame']) if kwargs.get('frame_range')
                else scene_space['duration_frames'] if 'duration_seconds' in document['constraints'] else 1)
        result.update(width=document['constraints']['width'],height=document['constraints']['height'],
            duration_seconds=result['frame_count']/24)
        self.samples[identity]=[dict(timestamp_seconds=frame/24,mime='image/png',raw=image_bytes())
            for frame in sorted({0,result['frame_count']//2,result['frame_count']-1})]
        self.results[identity]=result;return result
    def lookup(self,identity):return deepcopy(self.results.get(identity))
    def frames(self,identity,max_frames=3):
        self.frame_calls.append((identity,max_frames))
        return deepcopy(self.samples[identity][:max_frames])
    def stopping_proof(self,identity):return dict(stop_proven=identity in self.results)


@pytest.fixture
def service(tmp_path):return SceneSpaceRenderService(tmp_path/'queue',FixtureRenderer(tmp_path))


def test_get_is_read_only_and_exact_post_replay_does_not_render(service,render_request):
    queued=service.start('alice',render_request,'render_request-1')
    assert queued['status']=='queued' and service.renderer.calls==[]
    assert service.get('alice',queued['id'])==queued
    assert service.start('alice',render_request,'render_request-1')==queued
    assert service.renderer.calls==[]
    complete=service.advance('alice',queued['id'])
    assert complete['status']=='completed' and complete['stop_proven']
    assert complete['result']['frame_count']==96 and complete['result']['fps']==24
    assert complete['result']['snapshot_sha256']=='c'*64
    assert complete['usage']['render_seconds']>=0
    assert 'artifact_path' not in complete['result']
    service.advance('alice',queued['id'])
    assert len(service.renderer.calls)==1
    assert hashlib.sha256(service.video('alice',queued['id'])[0]).hexdigest()==complete['result']['sha256']


def test_uncertain_claim_restart_never_launches_a_second_renderer(service,render_request):
    service.renderer.uncertain=True
    queued=service.start('alice',render_request,'render_request-1')
    assert service.advance('alice',queued['id'])['status']=='reconciling'
    restarted=SceneSpaceRenderService(service.root,service.renderer)
    restarted.advance('alice',queued['id'])
    assert service.renderer.calls==[queued['id']]
    assert restarted.get('alice',queued['id'])['stop_proven'] is False


def test_cancel_queued_work_has_no_runtime_cost_and_running_cancel_waits_for_stop(service,render_request):
    queued=service.start('alice',render_request,'cancel-before')
    assert service.cancel('alice',queued['id'])['status']=='cancelled'
    service.advance('alice',queued['id']);assert not service.renderer.calls
    service.renderer.uncertain=True
    running=service.start('alice',render_request,'cancel-after')
    service.advance('alice',running['id'])
    cancelled=service.cancel('alice',running['id'])
    assert cancelled['status']=='cancelling' and not cancelled['stop_proven']


def test_owner_idempotency_and_media_tamper_are_checked(service,render_request):
    queued=service.start('alice',render_request,'same')
    with pytest.raises(ServiceError,match='not_found'):service.get('bob',queued['id'])
    changed=deepcopy(render_request);changed['document']['note']='changed'
    with pytest.raises(ServiceError,match='idempotency_conflict'):service.start('alice',changed,'same')
    service.advance('alice',queued['id'])
    Path(service.renderer.results[queued['id']]['artifact_path']).write_bytes(b'changed')
    with pytest.raises(ServiceError,match='media_changed'):service.video('alice',queued['id'])


def test_completed_replay_retains_old_deadline_and_runtime(service,render_request):
    service.clock=lambda:100
    render_request['deadline_at']=500
    queued=service.start('alice',render_request,'same')
    service.advance('alice',queued['id'])
    service.clock=lambda:1000
    service.renderer.validate_request=lambda *a,**k:(_ for _ in ()).throw(AssertionError('No new admission'))
    assert service.start('alice',render_request,'same')['status']=='completed'


def test_static_preview_range_retains_full_plan_identity(service,render_request):
    render_request['frame_range']=dict(start_frame=95,end_frame=96)
    render_request['document']['constraints'].pop('duration_seconds')
    queued=service.start('alice',render_request,'preview')
    done=service.advance('alice',queued['id'])
    assert done['result']['frame_count']==1
    assert done['plan_sha256']==freeze_plan(render_request['scene_space'])['plan_sha256']


def test_implicit_still_preview_is_one_frame_not_the_whole_plan(service,render_request):
    render_request['document']['constraints'].pop('duration_seconds')
    queued=service.start('alice',render_request,'implicit-static')
    result=service.advance('alice',queued['id'])
    assert result['status']=='completed'
    assert result['result']['frame_count']==1 and result['result']['mime']=='image/png'


def test_real_authenticated_http_queue_round_trip(tmp_path,render_request):
    service=SceneSpaceRenderService(tmp_path/'queue',FixtureRenderer(tmp_path))
    token='private-fixture-token-'+'a'*32
    server=CreationHTTPServer(SimpleNamespace(),token,pump=False,scene_space_service=service)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=SceneSpaceRenderClient('http://127.0.0.1:'+str(server.server_port),token)
    try:
        assert client.lookup('alice','missing') is None
        assert client.capabilities('alice')['admitted']
        queued=client.start('alice',render_request,'render_request-1')
        assert client.get('alice',queued['id'])['status']=='queued'
        assert not service.renderer.calls
        service.advance('alice',queued['id'])
        assert client.get('alice',queued['id'])['status']=='completed'
        assert client.video('alice',queued['id'])[0]==b'INERT TRANSPORT FIXTURE'
        frames=client.frames('alice',queued['id'],max_frames=2)
        assert len(frames)==2 and frames==service.renderer.samples[queued['id']][:2]
        assert service.renderer.frame_calls==[(queued['id'],2)]
        assert all(set(frame)=={'timestamp_seconds','mime','raw'} for frame in frames)
        response,_=client._request('alice','GET',PREFIX+'/'+queued['id']+'/frames',artifact=True)
        assert len(json.loads(response)['frames'])==3
        with pytest.raises(ServiceError) as error:client.frames('bob',queued['id'])
        assert error.value.status==404
        with pytest.raises(ServiceError) as error:
            client._request('alice','GET',PREFIX+'/'+queued['id']+'/frames/4',artifact=True)
        assert error.value.status==422
        with pytest.raises(ServiceError) as error:client.get('bob',queued['id'])
        assert error.value.status==404
        wrong=SceneSpaceRenderClient(client.base_url,'wrong-fixture-token-'+'b'*32)
        with pytest.raises(ServiceError) as error:wrong.capabilities('alice')
        assert error.value.status==403
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def test_late_reconciliation_cannot_overwrite_completed_costs(service,render_request):
    queued=service.start('alice',render_request,'race')
    entered_render=threading.Event();release_render=threading.Event()
    entered_lookup=threading.Event();release_lookup=threading.Event()
    original_render=service.renderer.render
    def render(*args,**kwargs):
        entered_render.set();assert release_render.wait(5)
        return original_render(*args,**kwargs)
    def lookup(_):
        entered_lookup.set();assert release_lookup.wait(5)
        return None
    service.renderer.render=render;service.renderer.lookup=lookup
    worker=threading.Thread(target=service.advance,args=('alice',queued['id']))
    late=threading.Thread(target=service.advance,args=('alice',queued['id']))
    worker.start();assert entered_render.wait(5)
    late.start();assert entered_lookup.wait(5)
    release_render.set();worker.join(timeout=5)
    finished=service.get('alice',queued['id'])
    assert finished['status']=='completed'
    release_lookup.set();late.join(timeout=5)
    assert service.get('alice',queued['id'])==finished
    assert len(service.renderer.calls)==1


def test_queued_operation_cannot_silently_switch_runtime_after_restart(service,render_request):
    queued=service.start('alice',render_request,'frozen-runtime')
    service.renderer.capabilities=lambda:dict(admitted=True,engines=['canvas'],runtime_sha256='new-runtime')
    restarted=SceneSpaceRenderService(service.root,service.renderer)
    stopped=restarted.advance('alice',queued['id'])
    assert stopped['status']=='failed' and stopped['stop_proven']
    assert stopped['error']=='scene_space_render_frozen_runtime_changed'
    assert service.renderer.calls==[]
    assert restarted.start('alice',render_request,'frozen-runtime')['id']==queued['id']


def test_stop_verification_failure_preserves_diagnostics_and_unknown_state_on_restart(service,render_request):
    def fail_render(identity,*args,**kwargs):
        service.renderer.calls.append(identity)
        raise ServiceError('fixture_renderer_failure',503)
    def fail_proof(_):
        raise ServiceError('fixture_stop_proof_unavailable',503)
    service.renderer.render=fail_render
    service.renderer.stopping_proof=fail_proof
    queued=service.start('alice',render_request,'proof-unavailable')
    result=service.advance('alice',queued['id'])
    assert result['status']=='reconciling' and result['stop_proven'] is False
    assert result['error']=='fixture_renderer_failure'
    assert result['stop_verification_error']=='fixture_stop_proof_unavailable'
    assert result['usage']['render_seconds'] is None
    assert service.get('alice',queued['id'])==result
    restarted=SceneSpaceRenderService(service.root,service.renderer)
    service.renderer.lookup=lambda _:(_ for _ in ()).throw(ServiceError('fixture_lookup_failure',503))
    reconciled=restarted.advance('alice',queued['id'])
    assert reconciled['status']=='reconciling' and reconciled['stop_proven'] is False
    assert reconciled['error']=='fixture_lookup_failure'
    assert reconciled['stop_verification_error']=='fixture_stop_proof_unavailable'
    assert reconciled['usage']['render_seconds'] is None
    assert service.renderer.calls==[queued['id']]


@pytest.mark.parametrize('proof',[True,[],{'stop_proven':1},{'stop_proven':'true'}])
def test_malformed_stop_proof_does_not_authorize_terminal_settlement(service,render_request,proof):
    service.renderer.render=lambda *a,**k:(_ for _ in ()).throw(OSError('fixture dispatch uncertainty'))
    service.renderer.stopping_proof=lambda _:proof
    queued=service.start('alice',render_request,'invalid-proof')
    result=service.advance('alice',queued['id'])
    assert result['status']=='reconciling' and result['stop_proven'] is False
    assert result['error']=='scene_space_render_executor_error'
    assert result['usage']['render_seconds'] is None


def test_independent_nondispatch_proof_survives_stop_verifier_failure(service,render_request):
    def reject(*args,**kwargs):
        error=ServiceError('fixture_runtime_binding_changed',409)
        error.not_dispatched=True
        error.stop_proven=True
        raise error
    service.renderer.render=reject
    service.renderer.stopping_proof=lambda _:(_ for _ in ()).throw(OSError('fixture proof store unavailable'))
    queued=service.start('alice',render_request,'pure-rejection')
    result=service.advance('alice',queued['id'])
    assert result['status']=='failed' and result['stop_proven'] is True
    assert result['error']=='fixture_runtime_binding_changed'
    assert result['stop_verification_error']=='scene_space_render_stop_verification_failed'
    assert result['usage']['render_seconds']>=0
    assert service.advance('alice',queued['id'])==result


def complete_preview(service,render_request,request_key='frame-preview'):
    queued=service.start('alice',render_request,request_key)
    complete=service.advance('alice',queued['id'])
    assert complete['status']=='completed'
    return complete


def test_preview_frames_are_owner_scoped_read_only_and_keep_the_original_runtime(service,render_request):
    queued=service.start('alice',render_request,'preview-access')
    with pytest.raises(ServiceError,match='frames_unavailable'):service.frames('alice',queued['id'])
    assert service.renderer.frame_calls==[]
    complete=service.advance('alice',queued['id'])
    with pytest.raises(ServiceError) as error:service.frames('bob',queued['id'])
    assert error.value.status==404 and service.renderer.frame_calls==[]
    service.renderer.capabilities=lambda:dict(admitted=True,runtime_sha256='a newer runtime')
    assert len(service.frames('alice',queued['id']))==3
    assert service.get('alice',queued['id'])==complete
    assert service.renderer.calls==[queued['id']]


def test_video_preview_timestamps_are_relative_to_the_bounded_range(service,render_request):
    render_request['frame_range']=dict(start_frame=24,end_frame=48)
    render_request['document']['constraints']['duration_seconds']=1
    complete=complete_preview(service,render_request)
    frames=service.frames('alice',complete['id'])
    assert [frame['timestamp_seconds'] for frame in frames]==[0,.5,23/24]
    response=service.frames_response('alice',complete['id'])
    assert response['duration_seconds']==1 and response['viewport']==dict(width=1280,height=720)
    assert response['plan_sha256']==freeze_plan(render_request['scene_space'])['plan_sha256']
    service.renderer.samples[complete['id']][0]['timestamp_seconds']=1
    with pytest.raises(ServiceError,match='frame_invalid'):service.frames('alice',complete['id'])


@pytest.mark.parametrize('max_frames',[0,4,True,1.5,'2',None])
def test_preview_sample_limits_are_checked_before_fetching(service,render_request,max_frames):
    complete=complete_preview(service,render_request)
    with pytest.raises(ServiceError,match='frame_limit_invalid'):
        service.frames('alice',complete['id'],max_frames=max_frames)
    client=SceneSpaceRenderClient('http://127.0.0.1:1','unused-fixture-token-'+'a'*32)
    client.get=lambda *args:(_ for _ in ()).throw(AssertionError('Must not make an HTTP request'))
    with pytest.raises(ServiceError,match='frame_limit_invalid'):
        client.frames('alice',complete['id'],max_frames=max_frames)
    assert service.renderer.frame_calls==[]


@pytest.mark.parametrize('lookup_result',[None,{},dict(status='unknown',stop_proven=False),
    dict(status='rendered',technical_passed=True,stop_proven=False)])
def test_completed_queue_does_not_override_unknown_or_unstopped_renderer(service,render_request,lookup_result):
    complete=complete_preview(service,render_request)
    service.renderer.lookup=lambda _:deepcopy(lookup_result)
    with pytest.raises(ServiceError,match='frames_unavailable'):service.frames('alice',complete['id'])
    assert service.renderer.frame_calls==[]
    assert service.get('alice',complete['id'])==complete


@pytest.mark.parametrize('field,value',[
    ('sha256','d'*64),('source_sha256','d'*64),('scene_space_sha256','d'*64),
    ('scene_space',{'plan_sha256':'d'*64}),('scene_space',None),('frame_count',95),
    ('duration_seconds',3),('width',640)])
def test_preview_renderer_identity_or_clock_tampering_is_rejected(service,render_request,field,value):
    complete=complete_preview(service,render_request)
    service.renderer.results[complete['id']][field]=value
    with pytest.raises(ServiceError,match='preview_result_changed'):service.frames('alice',complete['id'])
    assert service.renderer.frame_calls==[]


def test_preview_requires_video_and_exact_queue_stop_proof(service,render_request):
    render_request['document']['constraints'].pop('duration_seconds')
    complete=complete_preview(service,render_request,'static-only')
    with pytest.raises(ServiceError,match='frames_unavailable'):service.frames('alice',complete['id'])
    render_request['document']['constraints']['duration_seconds']=4
    complete=complete_preview(service,render_request,'not-stopped')
    with service._db() as db:
        row=service._load(db,'alice',complete['id']);row['stop_proven']=1;service._put(db,row)
    with pytest.raises(ServiceError,match='frames_unavailable'):service.frames('alice',complete['id'])
    assert service.renderer.frame_calls==[]


@pytest.mark.parametrize('case',['empty','too-many','corrupt','truncated','wrong-size','wrong-mime',
    'too-large','nan','infinity','negative','end-time','path'])
def test_server_validates_bounded_actual_sample_images_and_times(service,render_request,case):
    complete=complete_preview(service,render_request);identity=complete['id']
    frames=deepcopy(service.renderer.samples[identity]);frame=frames[0]
    if case=='empty':frames=[]
    elif case=='too-many':frames.append(deepcopy(frame))
    elif case=='corrupt':frame['raw']=b'not an image'
    elif case=='truncated':frame['raw']=image_bytes(image_format='JPEG')[:500]
    elif case=='wrong-size':frame['raw']=image_bytes((640,360))
    elif case=='wrong-mime':frame['mime']='image/jpeg'
    elif case=='too-large':frame['raw']=b'x'*(MAX_PREVIEW_FRAME_BYTES+1)
    elif case=='nan':frame['timestamp_seconds']=float('nan')
    elif case=='infinity':frame['timestamp_seconds']=float('inf')
    elif case=='negative':frame['timestamp_seconds']=-1
    elif case=='end-time':frame['timestamp_seconds']=4
    elif case=='path':frame['path']='private/sample.jpg'
    service.renderer.frames=lambda *args,**kwargs:frames
    with pytest.raises(ServiceError,match='preview_frame_'):service.frames('alice',identity)


def test_valid_jpeg_is_supported_in_addition_to_png(service,render_request):
    complete=complete_preview(service,render_request)
    frame=service.renderer.samples[complete['id']][0]
    frame.update(mime='image/jpeg',raw=image_bytes(image_format='JPEG'))
    assert service.frames('alice',complete['id'],1)==[frame]


def client_for_payload(service,identity,payload):
    client=SceneSpaceRenderClient('http://127.0.0.1:1','unused-fixture-token-'+'a'*32)
    client.get=lambda *args:service.get('alice',identity)
    client._request=lambda *args,**kwargs:(json.dumps(payload).encode(),'application/json')
    return client


@pytest.mark.parametrize('case',['hash','bytes','base64','corrupt','wrong-size','too-large','timestamp',
    'mime','path','identity','duration','viewport','empty','too-many'])
def test_client_independently_rejects_tampered_frame_transport(service,render_request,case):
    complete=complete_preview(service,render_request);identity=complete['id']
    payload=service.frames_response('alice',identity);frame=payload['frames'][0]
    if case=='hash':frame['sha256']='d'*64
    elif case=='bytes':frame['bytes']+=1
    elif case=='base64':frame['content_base64']='!'*len(frame['content_base64'])
    elif case in ('corrupt','wrong-size'):
        raw=b'corrupt image' if case=='corrupt' else image_bytes((640,360))
        frame.update(content_base64=base64.b64encode(raw).decode(),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    elif case=='too-large':frame['bytes']=MAX_PREVIEW_FRAME_BYTES+1
    elif case=='timestamp':frame['timestamp_seconds']=float('nan')
    elif case=='mime':frame['mime']='image/jpeg'
    elif case=='path':frame['path']='private/sample.jpg'
    elif case=='identity':payload['snapshot_sha256']='d'*64
    elif case=='duration':payload['duration_seconds']=300
    elif case=='viewport':payload['viewport']=dict(width=640,height=360)
    elif case=='empty':payload['frames']=[]
    elif case=='too-many':payload['frames'].append(deepcopy(frame))
    client=client_for_payload(service,identity,payload)
    with pytest.raises(ServiceError,match='preview_'):client.frames('alice',identity)
