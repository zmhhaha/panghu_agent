"""Synthetic new-protocol captures test admission logic, never live readiness."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

import pytest

from agent_service import worker_entry
from agent_service.contracts import (ExecutionEnvelope, InlineFile, ServiceError, WorkerResult,
    canonical_bytes, digest, sign_receipt)
from agent_service.deploy import wikiskill_acceptance as admission
from agent_service.deploy.acceptance_probe import candidate_config, harness_sha256, render_probe
from agent_service.deploy.candidate_scenario import module_sha256
from agent_service.deploy.wikiskill_catalog import COMPILER_PROFILES, PROFILES, role as profile_module
from agent_service.profiles import implementation
from agent_service import animation_wikiskill_contract as C
from agent_service.animation_skills_contract import build_skill_package
from agent_service.worker_entry import HostConfig, accepted_host
from test_wikiskill_catalog import prepare
from test_animation_review_catalog import IMAGE
from test_candidate_scenario import write_scenario, synthetic_source_root_owner


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


def compiler_source():
    snapshot=C.make_snapshot('fixture-publisher', [dict(record_id='goal',kind='brief',
        content={'goal':'Explain a cause with a stable object.'})], captured_at=1000.)
    document=C.build_input(snapshot)
    package=build_skill_package(skill_id='causal-staging',version=1,kind='foundation',
        files={'SKILL.md':'---\nname: causal-staging\ndescription: Stage a clear cause and visible consequence.\n---\n'
            'Establish a stable object and show the cause before the consequence.'},
        runtime_requirements={'engines':['html-svg']})
    artifact=C.validate_result(dict(schema=C.RESULT_SCHEMA,input_sha256=digest(document),
        snapshot_sha256=document['snapshot']['sha256'],evidence_scope=C.EVIDENCE_SCOPE,
        summary='Synthetic input only; no new motion validation.',packages=[package],
        index=[dict(skill_id=package['skill_id'],parent_skill_id=None,source_record_ids=['goal'])],
        exclusions=[],limitations=[C.COMPILATION_LIMITATION]),digest(document),document)
    return document,C.input_files(snapshot,document),[InlineFile.from_bytes(C.RESULT_NAME,canonical_bytes(artifact))]


def role_source(tmp_path,profile, *, rendering_foundation=False):
    if profile in COMPILER_PROFILES:
        return compiler_source(),None
    from test_joint_plan_skills_profile import stage,observe
    document,packages,output,envelope,proof=stage(tmp_path/'business',rendering_foundation=rendering_foundation)
    if rendering_foundation:
        from agent_service.profiles.video_joint_plan import planner_task
        envelope.task=planner_task(document,digest(document))
        output['rendering_intent']=dict(schema='video.spatial-rendering-intent.v1',space_mode='planar',
            engine='html-svg',camera_mode='static',required_features=['scale','coverage'],optional_features=[],
            quality=dict(structural_errors='block',optional_degradation='declared'))
    handler=implementation(profile)
    observer=handler.create_tool_observer(tmp_path/'business',envelope.task)
    observe(observer,'skill_search',{'query':'relationships'},'actual-search')
    observe(observer,'skill_read',{'package_sha256':packages[0]['sha256']},'actual-root-read')
    output['skill_decisions']=[dict(package_sha256=packages[0]['sha256'],action='adapt',
        reason='Keep identity through the cause and consequence.',segment_ids=['s1'])]
    save(tmp_path/'business/output.json',output)
    artifacts=handler.collect_output_with_host_evidence(tmp_path/'business',envelope,proof,
        observer.finish(envelope.attempt_id))
    return (document,{row.name:row.content() for row in envelope.inputs},artifacts),envelope.attempt_id


def capture(tmp_path,profile=PROFILES[0], *, rendering_foundation=False):
    module=profile_module(profile)
    _,hosts,_=prepare(profile)
    host,policy,binding=candidate_config(hosts[profile])
    (value,files,artifacts),observed_attempt=role_source(tmp_path,profile,rendering_foundation=rendering_foundation)
    from agent_service.profiles.video_joint_plan import planner_task
    task=({'input_sha256':digest(value)} if profile in COMPILER_PROFILES else planner_task(value,digest(value)))
    scenario_path,sources,scenario=write_scenario(tmp_path/'projected',profile,
        task,files)
    pins={key:item for key,item in policy.pins().items() if key in ExecutionEnvelope.model_fields}
    envelope=ExecutionEnvelope(**pins,job_id=str(uuid4()),attempt_id=observed_attempt or str(uuid4()),fence=1,
        deadline_at=time.time()+600,lease_expires_at=time.time()+300,
        task=task,inputs=[InlineFile.from_bytes(name,raw) for name,raw in files.items()])
    directory=tmp_path/('wikiskill-compiler-candidate' if profile in COMPILER_PROFILES else 'wikiskill-planner-candidate')
    root=directory/'candidate-output'
    root.mkdir(parents=True)
    worker_uid,proxy_uid=str(uuid4()),str(uuid4())
    uid=219100000
    artifact=json.loads(artifacts[0].content())
    result=WorkerResult(state='completed',artifacts=artifacts)
    result=result.model_copy(update=dict(stop_receipt=sign_receipt(envelope,result,b'synthetic-only-key'*4,
        ('pod:'+worker_uid,'uid:'+str(uid),'uid:'+str(uid+1)),time.time()-2)))
    observation=dict(harness_sha256=harness_sha256(),scenario=dict(scenario_harness_sha256=module_sha256()),
        capability=dict(engine='pi',package_measured=True,allowed_tools=list(implementation(profile).required_tools),
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
        artifacts=[dict(name=row.name,sha256=row.sha256,bytes=len(row.content())) for row in artifacts],
        artifact_count=len(artifacts),model_calls=[dict(body_sha256='f'*64,response_sha256='9'*64,state='complete',status=200)])
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
    for name,content in files.items():
        save(root/name,content)
    for row in artifacts:
        save(root/row.name,row.content())
    save(root/'worker.log',canonical_bytes(dict(status='candidate_probe_finished',engine='pi',profile=profile,
        result_state='completed',lease_expired_http_status=401,production_acceptance_created=False,pod_uid=worker_uid))+b'\n')
    return directory,root,value,artifact


@pytest.mark.parametrize('profile',PROFILES)
def test_exact_role_report_and_disabled_admission_preserve_capture(tmp_path,profile):
    directory,root,_,artifact=capture(tmp_path,profile)
    before={path.relative_to(root).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob('*') if path.is_file()}
    report=admission.report_candidate(directory,tmp_path/'report.json')
    assert report['profile']==profile and report['suite']==admission.SUITES[profile]
    assert report['model_requests']==report['uid_allocations']==0
    assert not (tmp_path/'binding-approvals.json').exists()
    assert report['rendering_verified'] is report['subjective_plan_quality_approved'] is False
    assert report['checks']['normalized_output_sha256']==digest(artifact)
    if profile in COMPILER_PROFILES:
        assert report['checks']['compiled_package_count']==1
        assert report['checks']['frozen_record_count']==1
        assert report['checks']['new_media_validation_performed'] is False
    else:
        assert report['checks']['planned_segment_count']==len(artifact['scenes'])
        assert report['checks']['progressive_skill_tools_exercised'] is True
        assert report['checks']['skill_decisions']==artifact['skill_decisions']
    summary=admission.review_candidate(directory,tmp_path/'admitted')
    approval=read(tmp_path/'admitted/binding-approvals.json')[0]
    target=tmp_path/'admitted'/approval['path']
    host=HostConfig.model_validate(read(target/'config.json'))
    assert accepted_host(host,target/'acceptance.json')
    assert summary['accepted_host'] is True and summary['enabled'] is False
    assert summary['model_requests']==summary['uid_allocations']==0
    assert approval['acceptance']['suite']==admission.SUITES[profile]
    assert before=={path.relative_to(root).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob('*') if path.is_file()}
    with pytest.raises(ServiceError,match='new_output_required'):
        admission.review_candidate(directory,tmp_path/'admitted')


def test_progressive_planner_marked_capture_preserves_skill_evidence_and_rendering_intent(tmp_path):
    directory,root,value,artifact=capture(tmp_path,PROFILES[1],rendering_foundation=True)
    assert read(root/'candidate-request.json')['envelope']['task']==dict(
        input_sha256=digest(value),rendering_foundation_revision=1)
    assert artifact['rendering_intent']['camera_mode']=='static'
    report=admission.report_candidate(directory,tmp_path/'report.json')
    assert report['checks']['progressive_skill_tools_exercised'] is True
    summary=admission.review_candidate(directory,tmp_path/'admitted')
    assert summary['accepted_host'] is True and summary['enabled'] is False


@pytest.mark.parametrize('rendering_foundation,marker',[(False,1),(True,None),(True,2),(True,True)])
def test_progressive_planner_capture_rejects_forged_missing_or_mistyped_foundation_marker(
        tmp_path,rendering_foundation,marker):
    directory,root,_,_=capture(tmp_path,PROFILES[1],rendering_foundation=rendering_foundation)
    envelope,result=admission.verify_parent({name:read(root/name) for name in admission.PARENT_FILES.values()})
    task=dict(envelope.task)
    if marker is None:task.pop('rendering_foundation_revision',None)
    else:task['rendering_foundation_revision']=marker
    envelope=envelope.model_copy(update=dict(task=task))
    with pytest.raises(ServiceError,match='input_mismatch|rendering_revision_invalid'):
        admission._role_capture(root,envelope,result,{})


@pytest.mark.parametrize('profile',PROFILES)
@pytest.mark.parametrize('name,change',[
    ('candidate-evidence.json',lambda d:d.update(combination_sha256='0'*64)),
    ('candidate-evidence.json',lambda d:d.update(artifact_count=99)),
    ('candidate-evidence.json',lambda d:d.update(candidate_result_sha256='0'*64)),
    ('candidate-evidence.json',lambda d:d.update(model_calls=[])),
    ('candidate-evidence.json',lambda d:d['model_calls'][0].update(status=500)),
    ('candidate-evidence.json',lambda d:d.update(model_calls=d['model_calls']*65)),
    ('candidate-evidence.json',lambda d:d['model_calls'][0].update(state='running')),
    ('candidate-evidence.json',lambda d:d.update(production_routes_changed=True)),
    ('scenario.json',lambda d:d['inputs'][0].update(sha256='0'*64)),
    ('host-config.json',lambda d:d['profile'].update(enabled=True)),
    ('worker-pod.json',lambda d:d['spec']['containers'][0]['securityContext'].update(privileged=True)),
    ('worker-pod.json',lambda d:d['status']['containerStatuses'][0].update(state=dict(running={}))),
    ('proxy-pod.json',lambda d:d['spec'].update(hostNetwork=True)),
    ('proxy-pod.json',lambda d:d['status']['containerStatuses'][0].update(state=dict(running={}))),
    ('operator-summary.json',lambda d:d.update(model_calls=2)),
    ('operator-summary.json',lambda d:d.update(lease_expiry_rejected=False)),
    ('uid-reservation.json',lambda d:d.update(reusable=True)),
    ('manifest.json',lambda d:next(r for r in d['items'] if r['kind']=='ConfigMap')['data'].update(acceptance_probe='bad')),
])
def test_changed_capture_or_unstopped_writer_cannot_create_host(tmp_path,profile,name,change):
    directory,root,_,_=capture(tmp_path,profile)
    edit(root,name,change)
    with pytest.raises((ServiceError,ValueError,KeyError)):
        admission.review_candidate(directory,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


@pytest.mark.parametrize('profile',PROFILES)
def test_worker_log_requires_actual_rejected_expired_lease(tmp_path,profile):
    directory,root,_,_=capture(tmp_path,profile)
    finish=json.loads((root/'worker.log').read_bytes())
    finish['lease_expired_http_status']=200
    save(root/'worker.log',canonical_bytes(finish)+b'\n')
    with pytest.raises(ServiceError,match='real_lease_401_missing'):
        admission.review_candidate(directory,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


@pytest.mark.parametrize('profile',PROFILES)
def test_output_artifact_changed_after_export_cannot_be_admitted(tmp_path,profile):
    directory,root,_,_=capture(tmp_path,profile)
    edit(root,profile_module(profile).RESULT_NAME,lambda value:value.update(summary='Altered after capture'))
    with pytest.raises(ServiceError,match='artifact_mismatch'):
        admission.review_candidate(directory,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


@pytest.mark.parametrize('name', ['records/000000.json', C.INPUT_NAME])
@pytest.mark.parametrize('profile', COMPILER_PROFILES)
def test_compiler_changed_record_or_manifest_cannot_be_admitted(tmp_path,name,profile):
    directory,root,_,_=capture(tmp_path,profile)
    save(root/name,b'{}')
    with pytest.raises(ServiceError,match='input_mismatch'):
        admission.review_candidate(directory,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


def test_planner_changed_skill_or_host_tool_receipt_cannot_be_admitted(tmp_path):
    directory,root,_,_=capture(tmp_path,PROFILES[1])
    skill=next((root/'animation-skills').rglob('SKILL.md'))
    skill.write_bytes(b'changed')
    with pytest.raises(ServiceError,match='input_mismatch'):
        admission.review_candidate(directory,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


def test_old_planner_capture_cannot_admit_either_new_profile_module(tmp_path):
    from test_joint_plan_acceptance import capture as old_capture
    directory, _, _, _ = old_capture(tmp_path)
    with pytest.raises(ServiceError, match='profile_not_allowed'):
        admission.review_candidate(directory,tmp_path/'rejected-old-role')
    assert not (tmp_path/'rejected-old-role').exists()


@pytest.mark.parametrize('tools',[['read','write','shell'],['read','write','write'],
    ['read'], 'read,write', None, ['read_compilation_input', 'write', 'write'],
    ['read_compilation_input']])
@pytest.mark.parametrize('profile', COMPILER_PROFILES)
def test_compiler_requires_exact_own_tool_capability(tmp_path,tools,profile):
    directory,root,_,_=capture(tmp_path,profile)
    for name in ('candidate-evidence.json','candidate-result.json'):
        edit(root,name,lambda row:row['observations']['capability'].update(allowed_tools=tools))
    with pytest.raises(ServiceError,match='capability_missing'):
        admission.review_candidate(directory,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


@pytest.mark.parametrize('change', ['changed', 'missing'])
def test_v2_compiler_rejects_missing_or_changed_source_extension(tmp_path,change):
    from agent_service.compilation_input_tools import verified_compilation_input_extension
    _,hosts,_=prepare(PROFILES[2])
    host,policy,binding=candidate_config(hosts[PROFILES[2]])
    module=profile_module(PROFILES[2])
    package=tmp_path/'source'
    shutil.copytree(module.BUNDLE_ROOT,package/'resources'/module.BUNDLE_REVISION)
    extension=package/verified_compilation_input_extension().name
    if change=='changed':
        extension.write_bytes(b'altered fixed input reader')
    with pytest.raises(ServiceError,match='tools_mismatch|file'):
        admission._bundle(package,admission._host_registry(host),binding,policy)


def test_planner_business_no_reads_cannot_admit_progressive_tool_profile_module(tmp_path):
    directory,root,_,_=capture(tmp_path,PROFILES[1])
    documents={name:read(root/name) for name in admission.PARENT_FILES.values()}
    envelope,result=admission.verify_parent(documents)
    evidence=read(root/admission.EVIDENCE_NAME)
    evidence['events']=[]
    evidence['sha256']=digest({key:value for key,value in evidence.items() if key!='sha256'})
    output=read(root/profile_module(PROFILES[1]).RESULT_NAME)
    output.update(skill_decisions=[],skill_tool_evidence_sha256=evidence['sha256'])
    save(root/admission.EVIDENCE_NAME,evidence)
    save(root/profile_module(PROFILES[1]).RESULT_NAME,output)
    result=result.model_copy(update=dict(artifacts=[
        InlineFile.from_bytes(profile_module(PROFILES[1]).RESULT_NAME,canonical_bytes(output)),
        InlineFile.from_bytes(admission.EVIDENCE_NAME,canonical_bytes(evidence))]))
    with pytest.raises(ServiceError,match='progressive_read_required'):
        admission._role_capture(root,envelope,result,{})
