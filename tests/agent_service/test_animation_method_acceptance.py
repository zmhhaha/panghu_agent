"""Synthetic new-protocol captures test admission logic, never live readiness."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

import pytest

from agent_service import worker_entry
from agent_service.contracts import (ExecutionEnvelope, InlineFile, ServiceError, WorkerResult,
    canonical_bytes, digest, sign_receipt)
from agent_service.deploy import animation_method_acceptance as admission
from agent_service.deploy.acceptance_probe import candidate_config, harness_sha256, render_probe
from agent_service.deploy.candidate_scenario import module_sha256
from agent_service.profiles import video_animation_method as method
from agent_service.worker_entry import HostConfig, accepted_host
from test_animation_method_catalog import prepare
from test_animation_review_catalog import IMAGE
from test_candidate_scenario import write_scenario, synthetic_source_root_owner
from test_video_animation_review_profile import PNG


RENDER_IMAGE='registry.example/renderer:latest@sha256:'+'a'*64
SOURCE=dict(engine='html-svg',strategy=dict(description='Synthetic test source',tradeoffs=''),
    files={'scene.html':'<html><body><script>window.__animationExperiment={seek(t){}};</script></body></html>'})


@pytest.fixture(autouse=True)
def operator_reader(monkeypatch):
    # Test actual accepted_host hash/pin checks with a synthetic file reader.
    # Windows cannot attest production root-owned read-only mount permissions.
    monkeypatch.setattr(worker_entry,'_read_operator_json',lambda path,**kwargs:read(path))


def read(path):
    return json.loads(Path(path).read_bytes())


def save(path,value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(value if type(value) is bytes else canonical_bytes(value))


def edit(root,name,change):
    value=read(root/name)
    change(value)
    save(root/name,value)


def document(review=True):
    value=dict(schema=method.INPUT_SCHEMA,mode='review' if review else 'create',
        goal='SYNTHETIC fixture: explain which objects remain after filtering.',
        criteria=[dict(id='legible',description='The retained objects are legible.')],methods=[],
        constraints=dict(width=320,height=180,fps=24,duration_seconds=1,allowed_engines=['html-svg']),
        feedback='',base_source=None,reference=None,comparison=None,assets={},
        observation=dict(frames=[],audio_observed=False,motion_observed=False,limitations=['Still images only']))
    if review:
        value['observation']['frames']=[dict(frame_id='frame-one',file_name='frames/one.png',
            sha256=hashlib.sha256(PNG).hexdigest(),timestamp_seconds=0,candidate_id='candidate-one',media_sha256='d'*64)]
        value['comparison']=dict(candidate_id='candidate-one',baseline_candidate_id=None)
    return value


def output(document):
    review=document['mode']=='review'
    opinion=None
    if review:
        opinion=dict(summary='SYNTHETIC still-image opinion',outcome='goal_met',
            preferred_candidate_id='candidate-one',criteria_results=[dict(criterion_id='legible',
                status='passed',evidence='Only this supplied still image was evaluated.')],
            remaining_issues=[],next_guidance='',limitations=document['observation']['limitations'])
    selected_method=dict(title='Retained identity',problem='Track retained objects',
        guidance='Retain visual identity during filtering',applicability='Small visible collections',
        limitations=['Synthetic fixture'],criteria=document['criteria'],runtime_requirements=dict(engine='html-svg'))
    return dict(schema=method.RESULT_SCHEMA,input_sha256=digest(document),mode=document['mode'],
        criteria=document['criteria'],methods=[] if review else [selected_method],
        source=None if review else SOURCE,sound_events=[],review=opinion)


def capture(tmp_path,review=True):
    profile=method.REVIEW_PROFILE if review else method.AUTHOR_PROFILE
    _,hosts,_=prepare()
    host,policy,binding=candidate_config(hosts[profile])
    value=document(review)
    files={method.INPUT_NAME:canonical_bytes(value)}
    if review:
        files['frames/one.png']=PNG
    scenario_path,sources,scenario=write_scenario(tmp_path/'projected',profile,
        dict(input_sha256=digest(value)),files)
    pins={key:item for key,item in policy.pins().items() if key in ExecutionEnvelope.model_fields}
    envelope=ExecutionEnvelope(**pins,job_id=str(uuid4()),attempt_id=str(uuid4()),fence=1,
        deadline_at=time.time()+600,lease_expires_at=time.time()+300,
        task=dict(input_sha256=digest(value)),inputs=[InlineFile.from_bytes(name,raw) for name,raw in files.items()])
    directory=tmp_path/'method-candidate'
    root=directory/'candidate-output'
    root.mkdir(parents=True)
    worker_uid,proxy_uid=str(uuid4()),str(uuid4())
    uid=219100000
    artifact=output(value)
    result=WorkerResult(state='completed',artifacts=[InlineFile.from_bytes(method.RESULT_NAME,canonical_bytes(artifact))])
    result=result.model_copy(update=dict(stop_receipt=sign_receipt(envelope,result,b'synthetic-only-key'*4,
        ('pod:'+worker_uid,'uid:'+str(uid),'uid:'+str(uid+1)),time.time()-2)))
    observation=dict(harness_sha256=harness_sha256(),scenario=dict(scenario_harness_sha256=module_sha256()),
        capability=dict(engine='pi',package_measured=True,allowed_tools=['read_inputs','write'],
            pi_cage_repository=admission.PI_CAGE_REPOSITORY,pi_cage_revision=admission.PI_CAGE_REVISION,
            pi_version=admission.PI_VERSION,node_version='24.22.1'),
        isolation={**{key:True for key in admission.ISOLATION},'groups':[],'uid':[uid+2]*3,
            'controller_confinement':dict(no_new_privileges=True,seccomp_filtering=True,
                apparmor_label='agent-capsule-inputs (enforce)')},
        model_round_trip=dict(api_mode='responses',status=200,response_sha256='9'*64))
    record=dict(schema_version='agent-candidate-probe.v1',candidate_only=True,candidate_scope_only=True,
        production_acceptance_created=False,production_routes_changed=False,
        engine='pi',profile=profile,profile_revision='1',image_digest=envelope.image_digest,
        binding_id=binding.id,binding_revision=binding.revision,
        combination_sha256=envelope.execution_binding.combination_sha256,
        execution_binding_sha256=envelope.execution_binding.sha256,
        job_id=envelope.job_id,attempt_id=envelope.attempt_id,request_sha256=envelope.request_sha256,
        candidate_result_sha256=digest(result.model_dump(mode='json')),result_state='completed',
        observations=observation,harness_sha256=harness_sha256(),scenario=observation['scenario'],
        lease_expiry_rejected=True,writer_resources=result.stop_receipt.resource_ids,
        writers_observed_stopped_at=result.stop_receipt.observed_at,pod_uid=worker_uid,
        artifacts=[dict(name=method.RESULT_NAME,sha256=result.artifacts[0].sha256,bytes=len(canonical_bytes(artifact)))],
        artifact_count=1,model_calls=[dict(body_sha256='f'*64,response_sha256='9'*64,state='complete',status=200)])
    if review:
        record['model_image_evidence']=dict(schema='agent-model-image-evidence.v1',
            job_id=envelope.job_id,attempt_id=envelope.attempt_id,request_sha256=envelope.request_sha256,
            evidence_scope='completed_upstream_transport',images=[dict(sha256=hashlib.sha256(PNG).hexdigest(),
                model_request_completed=True,model_request_sha256='f'*64,media_type='image/png',
                byte_length=len(PNG),input_item_index=1,part_index=0)])
    manifest=render_probe(host,name=directory.name,worker_image=IMAGE,node='offline-node',uid_start=uid,
        bootstrap_secret='offline-bootstrap',upstream_secret='offline-upstream',scenario=scenario_path,source_dir=sources)
    for role,pod_uid in (('worker',worker_uid),('proxy',proxy_uid)):
        planned=next(row for row in manifest['items'] if row['kind']=='Pod'
            and row['metadata']['name']==directory.name+'-'+role)
        actual=deepcopy(planned)
        actual['metadata']['uid']=pod_uid
        actual['spec']['nodeName']='offline-node'
        actual['status']=dict(phase='Succeeded',containerStatuses=[dict(name=role,restartCount=0,
            imageID='docker-pullable://'+IMAGE,state=dict(terminated=dict(exitCode=0,
                finishedAt=datetime.fromtimestamp(time.time()-1,timezone.utc).isoformat())))])
        save(root/(role+'-pod.json'),actual)
    save(root/'candidate-request.json',dict(schema_version='agent-candidate-request.v1',
        candidate_scope_only=True,envelope=envelope.model_dump(mode='json')))
    save(root/'candidate-result.json',dict(result=result.model_dump(mode='json'),observations=observation))
    save(root/'candidate-evidence.json',record)
    save(root/'uid-reservation.json',dict(name=directory.name,uid_start=uid,uid_count=3,node='offline-node',reusable=False))
    save(root/'host-config.json',hosts[profile])
    save(root/'scenario.json',scenario)
    save(root/'manifest.json',manifest)
    save(root/'operator-summary.json',dict(name=directory.name,status='completed',profile=profile,engine='pi',
        image_identity_valid=True,production_acceptance_created=False,production_routes_changed=False,
        result_state='completed',lease_expiry_rejected=True,model_calls=1))
    save(root/method.INPUT_NAME,files[method.INPUT_NAME])
    save(root/method.RESULT_NAME,artifact)
    save(root/'worker.log',canonical_bytes(dict(status='candidate_probe_finished',engine='pi',profile=profile,
        result_state='completed',lease_expired_http_status=401,production_acceptance_created=False,pod_uid=worker_uid))+b'\n')
    return directory,root,value,artifact


def renderer_capture(root,document,result,*,deadline_at=None,effective_timeout=None):
    """Explicit synthetic files model a complete stopped render capture."""
    from video_workbench.animation_method_renderer import HOST,MethodAnimationRenderer
    renderer=MethodAnimationRenderer(root,RENDER_IMAGE)
    binding=renderer._snapshot('synthetic-admission',result['source'],document,result['sound_events'],
        deadline_at=deadline_at)
    if effective_timeout is not None:
        binding['timeout_seconds']=effective_timeout
    request=binding['request']
    plan=dict(directory=str(root),request_sha256=binding['request_sha256'],image=RENDER_IMAGE,
        repository=renderer.repository,image_digest=renderer.image_digest,
        timeout_seconds=binding['timeout_seconds'],deadline_at=deadline_at)
    start=dict(schema='video.animation-method-render-start.v1',plan=plan,owner_token='a'*32,
        container_name='animation-method-synthetic')
    save(root/'binding.json',binding)
    save(root/'render-plan.json',plan)
    save(root/'render-start.json',start)
    save(root/'input/request.json',request)
    save(root/'host.mjs',HOST.read_bytes())
    for name,text in result['source']['files'].items():
        save(root/'input/scene'/name,text.encode())
    frame=b'\xff\xd8synthetic-not-an-actual-jpeg\xff\xd9'
    frames=[]
    for index in range(25):
        name=f'frame-{index:06d}.jpg' if index<24 else 'reverse-initial.jpg'
        save(root/'work/frames'/name,frame)
        frames.append(dict(name=name,timestamp_seconds=index/24 if index<24 else 0,
            bytes=len(frame),sha256=hashlib.sha256(frame).hexdigest()))
    media=b'0000ftypSYNTHETIC-NOT-REAL-MP4'
    save(root/'work/output.mp4',media)
    save(root/'work/render-receipt.json',dict(schema='video.animation-method-render-receipt.v1',status='rendered',
        decode_verified=True,reverse_seek_verified=True,request_identity=request['identity'],
        request_sha256=binding['request_sha256'],host_sha256=request['host_sha256'],image_digest=binding['image_digest'],
        frame_count=24,frames=frames,output_bytes=len(media),output_sha256=hashlib.sha256(media).hexdigest(),
        audio_sha256=None,sound_events_sha256=digest([]),probe=dict(streams=[dict(codec_type='video',codec_name='h264',
            width=320,height=180,avg_frame_rate='24/1')],format=dict(duration='1'))))
    save(root/'render-outcome.json',dict(schema='video.animation-method-render-outcome.v1',
        request_identity=request['identity'],request_sha256=binding['request_sha256'],image=RENDER_IMAGE,
        status='rendered',error=None,automatic_retry=False,actual_image_id='sha256:'+'b'*64,
        execution_timeout_seconds=binding['timeout_seconds'],
        container_id='c'*64,owner_token=start['owner_token'],container_name=start['container_name'],
        cleanup=dict(container_id='c'*64,status='removed_and_confirmed_absent')))
    return root


def test_review_admission_requires_new_protocol_and_only_creates_disabled_host(tmp_path):
    directory,root,_,_=capture(tmp_path)
    before={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir()}
    summary=admission.review_candidate(directory,tmp_path/'admitted')
    approval=read(tmp_path/'admitted/binding-approvals.json')[0]
    target=tmp_path/'admitted'/approval['path']
    report=read(target/'acceptance-evidence.json')
    assert accepted_host(HostConfig.model_validate(read(target/'config.json')),target/'acceptance.json')
    assert summary['accepted_host'] is True and summary['enabled'] is False
    assert summary['model_requests']==summary['uid_allocations']==0
    assert approval['acceptance']['suite']=='video.animation-method-review-contract.v1'
    assert report['model_frame_transport']['observed_frame_ids']==['frame-one']
    assert report['model_frame_transport']['motion_observed'] is False
    assert report['renderer_proof'] is None and report['live_http_verified'] is False
    assert before=={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir()}
    with pytest.raises(ServiceError,match='new_output_required'):
        admission.review_candidate(directory,tmp_path/'admitted')


def test_author_requires_real_render_capture_bound_to_exact_source_and_sound(tmp_path):
    directory,_,document,result=capture(tmp_path,review=False)
    with pytest.raises(ServiceError,match='actual_render_required'):
        admission.review_candidate(directory,tmp_path/'missing')
    assert not (tmp_path/'missing').exists()
    render_root=renderer_capture(tmp_path/'render',document,result)
    summary=admission.review_candidate(directory,tmp_path/'admitted',renderer_dir=render_root,renderer_image=RENDER_IMAGE)
    approval=read(tmp_path/'admitted/binding-approvals.json')[0]
    report=read(tmp_path/'admitted'/approval['path']/'acceptance-evidence.json')
    assert summary['enabled'] is False and report['renderer_proof']['container_absent_verified'] is True
    assert approval['acceptance']['suite']=='video.animation-method-author-contract.v1'
    assert report['renderer_proof']['frame_count']==24 and report['model_frame_transport'] is None
    assert not (render_root/'method-render-ledger.sqlite3').exists()


def test_admission_preserves_frozen_deadline_timeout_without_recomputing_or_rendering(tmp_path):
    document_value=document(False)
    result=output(document_value)
    root=renderer_capture(tmp_path/'render',document_value,result,deadline_at=time.time()-600,
        effective_timeout=180)
    proof=admission.verify_renderer_capture(root,document_value,result,renderer_image=RENDER_IMAGE)
    assert proof['container_absent_verified'] is True
    assert not (root/'method-render-ledger.sqlite3').exists()


@pytest.mark.parametrize('name,change',[
    ('candidate-evidence.json',lambda d:d.update(combination_sha256='0'*64)),
    ('candidate-evidence.json',lambda d:d.update(artifact_count=2)),
    ('candidate-evidence.json',lambda d:d.update(candidate_result_sha256='0'*64)),
    ('candidate-evidence.json',lambda d:d['model_image_evidence'].update(images=[])),
    ('candidate-evidence.json',lambda d:d['model_image_evidence']['images'][0].update(byte_length=999)),
    ('candidate-evidence.json',lambda d:d['model_image_evidence']['images'][0].update(model_request_sha256='0'*64)),
    ('candidate-evidence.json',lambda d:d['model_image_evidence'].update(evidence_scope='understood')),
    ('candidate-evidence.json',lambda d:d['model_image_evidence'].update(job_id=str(uuid4()))),
    ('scenario.json',lambda d:d['inputs'][0].update(sha256='0'*64)),
    ('host-config.json',lambda d:d['profile'].update(enabled=True)),
    ('worker-pod.json',lambda d:d['spec']['containers'][0]['securityContext'].update(privileged=True)),
    ('worker-pod.json',lambda d:d['status']['containerStatuses'][0].update(state=dict(running={}))),
    ('proxy-pod.json',lambda d:d['spec'].update(hostNetwork=True)),
    ('operator-summary.json',lambda d:d.update(model_calls=2)),
    ('manifest.json',lambda d:next(r for r in d['items'] if r['kind']=='ConfigMap')['data'].update(acceptance_probe='bad')),
])
def test_changed_capture_cannot_create_host(tmp_path,name,change):
    directory,root,_,_=capture(tmp_path)
    edit(root,name,change)
    with pytest.raises((ServiceError,ValueError,KeyError)):
        admission.review_candidate(directory,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


@pytest.mark.parametrize('name,change',[
    ('render-outcome.json',lambda d:d['cleanup'].update(status='unconfirmed')),
    ('render-outcome.json',lambda d:d.update(image='wrong')),
    ('render-start.json',lambda d:d.update(owner_token='b'*32)),
    ('render-plan.json',lambda d:d.update(timeout_seconds=601)),
    ('render-plan.json',lambda d:d.update(deadline_at=0)),
    ('render-outcome.json',lambda d:d.update(execution_timeout_seconds=601)),
    ('binding.json',lambda d:d.update(timeout_seconds=601)),
    ('binding.json',lambda d:d.update(timeout_seconds=180)),
    ('binding.json',lambda d:d['source']['files'].update({'scene.html':'changed'})),
    ('work/render-receipt.json',lambda d:d.update(reverse_seek_verified=False)),
    ('work/render-receipt.json',lambda d:d.update(audio_sha256='a'*64)),
])
def test_renderer_claim_json_cannot_replace_bound_stopped_media_evidence(tmp_path,name,change):
    document_value=document(False)
    result=output(document_value)
    root=renderer_capture(tmp_path/'render',document_value,result)
    edit(root,name,change)
    with pytest.raises(ServiceError):
        admission.verify_renderer_capture(root,document_value,result,renderer_image=RENDER_IMAGE)


@pytest.mark.parametrize('name',[
    'work/output.mp4', 'work/frames/frame-000001.jpg', 'input/scene/scene.html', 'host.mjs'])
def test_renderer_bytes_must_match_source_and_receipt(tmp_path,name):
    document_value=document(False)
    result=output(document_value)
    root=renderer_capture(tmp_path/'render',document_value,result)
    (root/name).write_bytes((root/name).read_bytes()+b'changed')
    with pytest.raises(ServiceError):
        admission.verify_renderer_capture(root,document_value,result,renderer_image=RENDER_IMAGE)
