"""Real shared store and private source transfer; no model or rendering calls."""
import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from PIL import Image
import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_review as visual
from video_workbench.visual_sampling import VisualSamplingService
from video_workbench.visual_sampling_http import (
    FormalSamplingTransport, VisualSamplingClient, SCHEMA, CHUNK_BYTES, route_private)

OWNER='sampling-owner'


def png(color=(60,100,140)):
    stream=io.BytesIO();Image.new('RGB',(24,16),color).save(stream,format='PNG');return stream.getvalue()


class LocalTransport:
    def __init__(self, service):self.service=service;self.calls=[]
    def _request(self, owner, method, path, body=None, *, artifact=False, response_limit=None):
        result={}
        class Handler:
            server=SimpleNamespace(sampling_transport=self.service,sampling_pump=None)
            def _send(self,value,status=200,*,raw=False,mime='application/json'):
                result.update(value=value,mime=mime,raw=raw,status=status)
        self.calls.append((method,path))
        route_private(Handler(),owner,method,path,body)
        if artifact:
            raw=result['value'] if result['raw'] else canonical_bytes(result['value'])
            assert len(raw)<=response_limit
            return raw,result['mime']
        return result['value']


@pytest.fixture
def env(tmp_path):
    core=VisualSamplingService(tmp_path/'common')
    service=FormalSamplingTransport(tmp_path/'transfer',core)
    transport=LocalTransport(service)
    return SimpleNamespace(root=tmp_path,core=core,service=service,transport=transport,
        client=VisualSamplingClient(transport),deadline=time.time()+300)


def frames():
    raw=png()
    return [dict(raw=raw,mime='image/png',timestamp_seconds=t,frame_id='frame-'+str(i),role='output')
        for i,t in enumerate((0.,1.))]


def prepare(env, operation='retained-1', **kwargs):
    return env.client.prepare_frames(OWNER,operation,dict(owner=OWNER,render_id='render-1',
        capture_sha256='a'*64,source_scope='client_retained'),dict(strategy='provided',max_frames=2),
        frames(),deadline_at=env.deadline,**kwargs)


def test_prepare_queues_then_one_common_service_retains_exact_distinct_time_frames(env):
    result=prepare(env)
    assert result['state']=='queued'
    with pytest.raises(ServiceError,match='visual_sampling_result_unavailable'):
        env.client.read_frames(OWNER,'retained-1')
    env.service.advance(OWNER,'retained-1')
    result=env.client.get(OWNER,'retained-1')
    assert result['state']=='completed' and result['stop_proven']
    assert result['manifest_sha256']==result['sampling_manifest_sha256']
    assert env.client.read_frames(OWNER,'retained-1')==frames()
    manifest=env.client.manifest(OWNER,'retained-1')
    assert len(manifest['frames'])==2
    assert manifest['frames'][0]['sha256']==manifest['frames'][1]['sha256']
    assert manifest['frames'][0]['timestamp_seconds']!=manifest['frames'][1]['timestamp_seconds']
    assert manifest['source']['provenance_scope'].startswith('client_retained')


def test_same_key_replay_and_get_do_not_prepare_or_upload_again(env,monkeypatch):
    prepare(env);env.service.advance(OWNER,'retained-1')
    old=len([c for c in env.transport.calls if c[0]=='POST'])
    monkeypatch.setattr(env.core,'prepare',lambda *a,**k:pytest.fail('GET or completed replay decoded'))
    assert prepare(env)['state']=='completed'
    assert env.client.read_frames(OWNER,'retained-1')==frames()
    assert len([c for c in env.transport.calls if c[0]=='POST'])==old
    restored=FormalSamplingTransport(env.root/'transfer',VisualSamplingService(env.root/'common'))
    assert VisualSamplingClient(LocalTransport(restored)).read_frames(OWNER,'retained-1')==frames()


def test_metadata_does_not_read_blobs_and_one_frame_reads_one_blob(env,monkeypatch):
    prepare(env);env.service.advance(OWNER,'retained-1')
    reads=[];original=env.core._read_blob
    def read(checksum,length):
        reads.append(checksum);return original(checksum,length)
    monkeypatch.setattr(env.core,'_read_blob',read)
    assert len(env.service.frames_metadata(OWNER,'retained-1'))==2
    assert reads==[]
    assert env.service.frame(OWNER,'retained-1',1)==(frames()[1]['raw'],'image/png')
    assert len(reads)==1


def test_large_picture_keeps_original_bytes_and_chunk_resume_survives_restart(env,monkeypatch):
    stream=io.BytesIO();Image.new('RGB',(1220,1220),(60,100,140)).save(stream,format='PNG',compress_level=0)
    raw=stream.getvalue()
    assert 4*1024*1024<len(raw)<8*1024*1024
    rows=[dict(raw=raw,mime='image/png',timestamp_seconds=0.,frame_id='large-picture')]
    original=env.transport._request
    def interrupted(owner,method,path,body=None,**kwargs):
        if method=='POST' and path.endswith('/chunks') and body['offset']>=CHUNK_BYTES:
            raise OSError('client disconnected')
        return original(owner,method,path,body,**kwargs)
    monkeypatch.setattr(env.transport,'_request',interrupted)
    arguments=(OWNER,'large-picture',dict(owner=OWNER),dict(strategy='provided',max_frames=1),rows)
    with pytest.raises(OSError,match='client disconnected'):
        env.client.prepare_frames(*arguments,deadline_at=env.deadline)
    assert env.service.get(OWNER,'large-picture')['received_bytes']==CHUNK_BYTES
    restored=FormalSamplingTransport(env.root/'transfer',env.core)
    transport=LocalTransport(restored);client=VisualSamplingClient(transport)
    assert client.prepare_frames(*arguments,deadline_at=env.deadline)['state']=='queued'
    restored.advance(OWNER,'large-picture')
    assert client.read_frames(OWNER,'large-picture')==rows
    assert restored.capabilities()['max_frame_bytes']==8*1024*1024


def test_owner_and_same_key_source_policy_cannot_be_swapped(env):
    prepare(env)
    with pytest.raises(ServiceError,match='visual_sampling_not_found'):
        env.client.get('other-owner','retained-1')
    with pytest.raises(ServiceError,match='visual_sampling_source_owner_changed'):
        env.client.prepare_frames('other-owner','retained-1',dict(owner=OWNER),dict(strategy='provided'),
            frames(),deadline_at=env.deadline)
    with pytest.raises(ServiceError,match='visual_sampling_operation_conflict'):
        env.client.prepare_frames(OWNER,'retained-1',dict(owner=OWNER,render_id='different'),
            dict(strategy='provided',max_frames=2),frames(),deadline_at=env.deadline)


def test_cancel_queued_is_terminal_without_decoding(env,monkeypatch):
    prepare(env)
    monkeypatch.setattr(env.core,'prepare',lambda *a,**k:pytest.fail('cancelled source decoded'))
    assert env.client.cancel(OWNER,'retained-1')['state']=='cancelled'
    env.service.advance(OWNER,'retained-1')
    assert env.client.get(OWNER,'retained-1')['stop_proven']


def test_source_tampering_rejected_and_failed_without_re_render(env):
    prepare(env)
    path=env.service._source_path(OWNER,'retained-1')
    original=path.read_bytes();path.write_bytes(original[:-1]+bytes([original[-1]^1]))
    env.service.advance(OWNER,'retained-1')
    result=env.client.get(OWNER,'retained-1')
    assert result['state']=='failed' and result['stop_proven']
    assert result['error']=='visual_sampling_source_changed'


def test_storage_and_pending_limits_apply_before_new_transfer(env):
    service=FormalSamplingTransport(env.root/'limited',env.core,max_total_bytes=1)
    with pytest.raises(ServiceError,match='visual_sampling_storage_capacity_exceeded'):
        VisualSamplingClient(LocalTransport(service)).prepare_frames(OWNER,'too-big',dict(owner=OWNER),
            dict(strategy='provided'),frames(),deadline_at=env.deadline)
    service=FormalSamplingTransport(env.root/'pending',env.core,max_pending_per_owner=1)
    client=VisualSamplingClient(LocalTransport(service))
    client.prepare_frames(OWNER,'one',dict(owner=OWNER),dict(strategy='provided'),frames(),deadline_at=env.deadline)
    with pytest.raises(ServiceError,match='visual_sampling_pending_capacity_exceeded'):
        client.prepare_frames(OWNER,'two',dict(owner=OWNER),dict(strategy='provided'),frames(),deadline_at=env.deadline)


def test_chunk_integrity_offset_and_idempotent_replay(env):
    raw=b'abcd'
    request=dict(schema=SCHEMA,operation_id='chunks',kind='formal-media',source=dict(project_id='p',render_id='r',
        production_run_id='u',film_sha256='a'*64,media_sha256=hashlib.sha256(raw).hexdigest()),
        policy=dict(strategy='formal_legacy',clip_id='clip',goal='Visible geometry',source_sha256='a'*64,
            sample_fps=3,frame_limit=24,review_profile=visual.REVIEW_PACKED_PROFILE),
        bytes=4,sha256=hashlib.sha256(raw).hexdigest(),files=[],deadline_at=env.deadline)
    env.service.register(OWNER,request)
    chunk=dict(offset=0,sha256=hashlib.sha256(raw[:2]).hexdigest(),content_base64=base64.b64encode(raw[:2]).decode())
    assert env.service.upload(OWNER,'chunks',chunk)['received_bytes']==2
    assert env.service.upload(OWNER,'chunks',chunk)['received_bytes']==2
    with pytest.raises(ServiceError,match='visual_sampling_chunk_changed'):
        env.service.upload(OWNER,'chunks',dict(chunk,sha256=hashlib.sha256(b'zz').hexdigest(),content_base64='eno='))
    with pytest.raises(ServiceError,match='visual_sampling_chunk_offset_invalid'):
        env.service.upload(OWNER,'chunks',dict(chunk,offset=3))
    last=dict(offset=2,sha256=hashlib.sha256(raw[2:]).hexdigest(),content_base64=base64.b64encode(raw[2:]).decode())
    env.service.upload(OWNER,'chunks',last)
    assert env.service.commit(OWNER,'chunks')['state']=='queued'
    assert env.service.commit(OWNER,'chunks')['state']=='queued'


def fake_decoder(monkeypatch,duration=1.):
    from video_workbench import animation_observation as observation
    calls=[]
    def run(argv,**kwargs):
        calls.append(argv)
        if '-show_entries' in argv:
            return json.dumps(dict(streams=[dict(width=1280,height=720)],format=dict(duration=duration))).encode()
        target=Path(argv[-1]);stream=io.BytesIO();Image.new('RGB',(960,540),(40,90,120)).save(stream,format='JPEG')
        target.write_bytes(stream.getvalue());return b''
    monkeypatch.setattr(observation,'_run',run)
    return calls


def test_formal_transfer_uses_original_decoder_and_exports_original_bundle(env,monkeypatch,tmp_path):
    calls=fake_decoder(monkeypatch)
    raw=b'controlled-original-mp4'
    source=dict(owner=OWNER,project_id='p',render_id='r',production_run_id='run',film_sha256='a'*64,
        media_sha256=hashlib.sha256(raw).hexdigest())
    policy=dict(strategy='formal_legacy',clip_id='formal-clip',goal='Check supplied geometry.',
        source_sha256='a'*64,sample_fps=3,frame_limit=24,review_profile=visual.REVIEW_PACKED_PROFILE)
    assert env.client.prepare_media(OWNER,'formal-1',source,policy,raw,deadline_at=env.deadline)['state']=='queued'
    assert calls==[]
    env.service.advance(OWNER,'formal-1')
    assert env.client.get(OWNER,'formal-1')['state']=='completed'
    profile,document,files,manifest=env.client.export_bundle(OWNER,'formal-1')
    assert profile==visual.REVIEW_PACKED_PROFILE
    assert [f['time_seconds'] for f in document['observation']['frames']]==[0.,.333333,.666667]
    assert 'requested decode sampling grid' in document['observation']['limitations'][1]
    assert files['inputs/'+visual.REVIEW_INPUT_NAME]==canonical_bytes(document)
    assert 'evidence.json' in files and manifest['source']['media_sha256']==source['media_sha256']
    ffmpeg=[x for x in calls if '-show_entries' not in x]
    assert len(ffmpeg)==3
    assert all(x[x.index('-q:v')+1]=='4' and x[x.index('-vf')+1]=="scale='min(960,iw)':-2" for x in ffmpeg)


def test_manifest_clock_and_role_cannot_be_changed_on_read(env,monkeypatch):
    prepare(env);env.service.advance(OWNER,'retained-1')
    original=env.service.frames_metadata
    def changed(*args):
        rows=original(*args);rows[1]['timestamp_seconds']=100.;return rows
    monkeypatch.setattr(env.service,'frames_metadata',changed)
    with pytest.raises(ServiceError,match='visual_sampling_frame_manifest_changed'):
        env.client.read_frames(OWNER,'retained-1')


def test_expired_upload_is_stopped_by_pump_without_decode(env,monkeypatch):
    prepare(env)
    env.service.clock=lambda:env.deadline+1
    env.service.advance(OWNER,'retained-1')
    assert env.client.get(OWNER,'retained-1')['state']=='timed_out'
    assert env.client.get(OWNER,'retained-1')['stop_proven']
    with pytest.raises(ServiceError,match='visual_sampling_deadline_invalid'):
        env.client.prepare_frames(OWNER,'expired',dict(owner=OWNER),dict(strategy='provided'),
            frames(),deadline_at=env.deadline-1000)


def test_cancel_running_does_not_claim_stopped_before_provider_exits(env,monkeypatch):
    prepare(env)
    entered=threading.Event();release=threading.Event()
    original=env.service._provider
    def provider(*args):
        real=original(*args)
        def invoke(owner,source,policy,check):
            entered.set();assert release.wait(5);check();return real(owner,source,policy,check)
        return invoke
    monkeypatch.setattr(env.service,'_provider',provider)
    thread=threading.Thread(target=env.service.advance,args=(OWNER,'retained-1'));thread.start()
    assert entered.wait(5)
    try:
        state=env.client.cancel(OWNER,'retained-1')
        assert state['state']=='running' and not state['stop_proven']
    finally:release.set();thread.join(5)
    assert not thread.is_alive()
    result=env.client.get(OWNER,'retained-1')
    assert result['state']=='cancelled' and result['stop_proven']


def test_concurrent_advances_share_one_provider_and_never_redecode(env,monkeypatch):
    prepare(env)
    entered=threading.Event();release=threading.Event();calls=[]
    original=env.service._provider
    def provider(*args):
        real=original(*args)
        def invoke(owner,source,policy,check):
            calls.append(source);entered.set();assert release.wait(5)
            return real(owner,source,policy,check)
        return invoke
    monkeypatch.setattr(env.service,'_provider',provider)
    first=threading.Thread(target=env.service.advance,args=(OWNER,'retained-1'));first.start()
    assert entered.wait(5)
    try:
        env.service.advance(OWNER,'retained-1')
        assert env.client.get(OWNER,'retained-1')['state']=='running'
        assert len(calls)==1
    finally:release.set();first.join(5)
    assert not first.is_alive()
    assert env.client.get(OWNER,'retained-1')['state']=='completed'
    env.service.advance(OWNER,'retained-1')
    assert len(calls)==1


def test_actual_private_http_reuses_auth_owner_and_never_advances_get(env):
    from video_workbench.animation_creation_http import CreationHTTPServer
    from video_workbench.animation_method_http import MethodLabClient
    from test_film_review_proxy import TOKEN,serving
    with serving(CreationHTTPServer(object(),TOKEN,pump=False,sampling_transport=env.service)) as server:
        transport=MethodLabClient('http://127.0.0.1:'+str(server.server_port),TOKEN)
        client=VisualSamplingClient(transport)
        result=client.prepare_frames(OWNER,'http-1',dict(owner=OWNER,capture_sha256='a'*64),
            dict(strategy='provided',max_frames=2),frames(),deadline_at=env.deadline)
        assert result['state']=='queued'
        assert client.get(OWNER,'http-1')['state']=='queued'
        with pytest.raises(ServiceError,match='visual_sampling_not_found'):
            client.get('other-owner','http-1')
        env.service.advance(OWNER,'http-1')
        assert client.read_frames(OWNER,'http-1')==frames()
        invalid=VisualSamplingClient(MethodLabClient(transport.base_url,'x'*64))
        with pytest.raises(ServiceError,match='animation_creation_unauthorized'):
            invalid.get(OWNER,'http-1')
