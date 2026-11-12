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
from agent_service.deploy import joint_plan_acceptance as admission
from agent_service.deploy.acceptance_probe import candidate_config, harness_sha256, render_probe
from agent_service.deploy.candidate_scenario import module_sha256
from agent_service.profiles import video_joint_plan as planner
from agent_service.worker_entry import HostConfig, accepted_host
from test_joint_plan_catalog import prepare
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


def document():
    from test_joint_plan_profile import document as value
    return value()


def output(context):
    from test_joint_plan_profile import result
    return planner.validate_joint_plan_result(result(context), digest(context), context)


def capture(tmp_path):
    profile=planner.PROFILE
    _,hosts,_=prepare()
    host,policy,binding=candidate_config(hosts[profile])
    value=document()
    files={planner.INPUT_NAME:canonical_bytes(value)}
    scenario_path,sources,scenario=write_scenario(tmp_path/'projected',profile,
        dict(input_sha256=digest(value)),files)
    pins={key:item for key,item in policy.pins().items() if key in ExecutionEnvelope.model_fields}
    envelope=ExecutionEnvelope(**pins,job_id=str(uuid4()),attempt_id=str(uuid4()),fence=1,
        deadline_at=time.time()+600,lease_expires_at=time.time()+300,
        task=dict(input_sha256=digest(value)),inputs=[InlineFile.from_bytes(name,raw) for name,raw in files.items()])
    directory=tmp_path/'joint-plan-candidate'
    root=directory/'candidate-output'
    root.mkdir(parents=True)
    worker_uid,proxy_uid=str(uuid4()),str(uuid4())
    uid=219100000
    artifact=output(value)
    result=WorkerResult(state='completed',artifacts=[InlineFile.from_bytes(planner.RESULT_NAME,canonical_bytes(artifact))])
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
        artifacts=[dict(name=planner.RESULT_NAME,sha256=result.artifacts[0].sha256,bytes=len(canonical_bytes(artifact)))],
        artifact_count=1,model_calls=[dict(body_sha256='f'*64,response_sha256='9'*64,state='complete',status=200)])
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
    save(root/planner.INPUT_NAME,files[planner.INPUT_NAME])
    save(root/planner.RESULT_NAME,artifact)
    save(root/'worker.log',canonical_bytes(dict(status='candidate_probe_finished',engine='pi',profile=profile,
        result_state='completed',lease_expired_http_status=401,production_acceptance_created=False,pod_uid=worker_uid))+b'\n')
    return directory,root,value,artifact


def test_planner_admission_preserves_capture_and_only_creates_disabled_host(tmp_path):
    directory,root,_,artifact=capture(tmp_path)
    before={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir()}
    summary=admission.review_candidate(directory,tmp_path/'admitted')
    approval=read(tmp_path/'admitted/binding-approvals.json')[0]
    target=tmp_path/'admitted'/approval['path']
    host=HostConfig.model_validate(read(target/'config.json'))
    report=read(target/'acceptance-evidence.json')
    assert accepted_host(host,target/'acceptance.json')
    assert summary['accepted_host'] is True and summary['enabled'] is False
    assert summary['model_requests']==summary['uid_allocations']==0
    assert approval['acceptance']['suite']==admission.SUITE=='video.joint-plan-contract.v1'
    assert report['rendering_verified'] is report['subjective_plan_quality_approved'] is False
    assert report['live_http_verified'] is False
    assert report['checks']['normalized_output_sha256']==digest(artifact)
    assert report['checks']['planned_segment_count']==len(artifact['scenes'])
    assert before=={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir()}
    with pytest.raises(ServiceError,match='new_output_required'):
        admission.review_candidate(directory,tmp_path/'admitted')


@pytest.mark.parametrize('name,change',[
    ('candidate-evidence.json',lambda d:d.update(combination_sha256='0'*64)),
    ('candidate-evidence.json',lambda d:d.update(artifact_count=2)),
    ('candidate-evidence.json',lambda d:d.update(candidate_result_sha256='0'*64)),
    ('candidate-evidence.json',lambda d:d.update(model_calls=[])),
    ('candidate-evidence.json',lambda d:d['model_calls'][0].update(status=500)),
    ('candidate-evidence.json',lambda d:d.update(model_calls=d['model_calls']*9)),
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
def test_changed_capture_or_unstopped_writer_cannot_create_host(tmp_path,name,change):
    directory,root,_,_=capture(tmp_path)
    edit(root,name,change)
    with pytest.raises((ServiceError,ValueError,KeyError)):
        admission.review_candidate(directory,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


def test_worker_log_requires_actual_rejected_expired_lease(tmp_path):
    directory,root,_,_=capture(tmp_path)
    finish=json.loads((root/'worker.log').read_bytes())
    finish['lease_expired_http_status']=200
    save(root/'worker.log',canonical_bytes(finish)+b'\n')
    with pytest.raises(ServiceError,match='real_lease_401_missing'):
        admission.review_candidate(directory,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


def test_output_artifact_changed_after_export_cannot_be_admitted(tmp_path):
    directory,root,_,_=capture(tmp_path)
    edit(root,planner.RESULT_NAME,lambda value:value.update(summary='Altered after capture'))
    with pytest.raises(ServiceError,match='artifact_mismatch'):
        admission.review_candidate(directory,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()
