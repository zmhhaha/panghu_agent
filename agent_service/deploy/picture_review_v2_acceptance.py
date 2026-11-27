"""Independently verify one v2 cold candidate from original operator captures.

No v1 report, route enablement, model call, HMAC key reconstruction, or business
control stop is borrowed. Provider response bytes and persisted limit-row
exports settle request usage; native custody plus independently captured Pod
termination settle this candidate. Missing raw evidence rejects admission.
"""
from __future__ import annotations
import argparse
import base64
import binascii
from copy import deepcopy
from collections import Counter
import hashlib
import os
from pathlib import Path
import re
import time

from agent_service.contracts import JobRequest,ServiceError, canonical_bytes, digest
from agent_service.deploy.acceptance_probe import candidate_config
from agent_service.deploy.animation_creation_acceptance import (
    CHECKS,ISOLATION,_capture,_directory,_pod_stop,_save,_source_package)
from agent_service.deploy.candidate_scenario import PARENT_FILES,verify_parent
from agent_service.input_tools import READ_INPUTS_EXTENSION_SHA256
from agent_service.picture_learning_tools import EXTENSION_SHA256,environment_revision
from agent_service.pi_cage import PI_CAGE_REPOSITORY,PI_CAGE_REVISION,PI_VERSION
from agent_service.profiles import video_picture_review_v2 as method,implementation
from agent_service.profiles.video import decode_video_json
from agent_service.profiles.video_animation_review import _image_header
from agent_service.registry import binding_fingerprint,required_model_capabilities
from agent_service.worker_entry import HostConfig,WorkerConfigError,_host_registry,accepted_host,host_policy
from video_workbench.animation_candidate_import import EXPORT_NAMES,_pod

PROFILES=frozenset({method.PROFILE})
SHA=re.compile(r'[0-9a-f]{64}\Z')
MODEL_LIMITS=dict(max_requests=8,max_output_tokens=8192)
MAX_RAW_RESPONSES=8*1024*1024
NOT_CLAIMED=['offline_hmac_reverification','production_control_usage_closure',
    'production_api_dispatcher_end_to_end','business_user_approval','subjective_picture_quality',
    'model_understanding','whole_video_watched','audio_quality','other_profiles_or_images']

def require(value,code='picture_review_v2_admission_invalid'):
    if not value:raise ServiceError(code,422)

def sha(raw):return hashlib.sha256(raw).hexdigest()

def _protocol(profile):
    require(profile==method.PROFILE,'picture_review_v2_admission_profile_not_allowed')
    return method

def _bundle(package,registry,binding,policy):
    handler=implementation(method.PROFILE)
    contract=registry.contract(method.PROFILE,method.PROFILE_REVISION)
    bundle=registry.bundle(binding.bundle.name,binding.bundle.revision)
    environment=registry.environment(binding.tool_environment.name,binding.tool_environment.revision)
    tools={'read_inputs','skill_search','skill_read','write'}
    require(binding.profile==method.PROFILE and contract.task_schema==handler.task_schema
        and contract.output_schema==handler.output_schema and bundle.name=='picture-review'
        and bundle.revision==method.BUNDLE_REVISION and environment.name==method.ENVIRONMENT_NAME
        and set(contract.required_tools)==set(bundle.required_tools)==set(environment.tools)==tools
        and set(environment.capabilities)=={handler.output_schema}
        and environment.revision==environment_revision(environment.model_dump(mode='json'))
        and set(required_model_capabilities(handler))<=set(binding.model_policy.capabilities),
        'picture_review_v2_admission_bundle_binding_mismatch')
    root=package/'resources'/method.BUNDLE_REVISION
    raw=_capture(root,'manifest.json',65536)
    require(sha(raw)==method.BUNDLE_MANIFEST_SHA256,'picture_review_v2_admission_resource_mismatch')
    manifest=decode_video_json(raw);rows=manifest.get('local_files')
    require(manifest.get('bundle_revision')==method.BUNDLE_REVISION and manifest.get('license')=='MIT'
        and manifest.get('automatic_updates') is False and manifest.get('upstream_content_vendored') is False
        and type(rows) is list and len(rows)==len(method.BUNDLE_FILES)
        and all(type(row) is dict and set(row)=={'path','sha256'} for row in rows)
        and {row['path'] for row in rows}==method.BUNDLE_FILES,'picture_review_v2_admission_resource_mismatch')
    measured={'manifest.json':sha(raw)}
    for row in rows:
        measured[row['path']]=sha(_capture(root,row['path'],128*1024))
        require(measured[row['path']]==row['sha256'],'picture_review_v2_admission_resource_mismatch')
    require({row.name:row.sha256 for row in bundle.resources}==measured
        and sha(_capture(package,'read_inputs_extension.mjs',128*1024))==READ_INPUTS_EXTENSION_SHA256
        and sha(_capture(package,'picture_learning_extension.mjs',128*1024))==EXTENSION_SHA256,
        'picture_review_v2_admission_tools_mismatch')
    return measured

def _frames(document,inputs,record,envelope,calls,connectivity_body):
    from agent_service.profiles.video_animation_review import _image_header
    receipt=record.get('model_image_evidence')
    require(type(receipt) is dict and set(receipt)=={'schema','job_id','attempt_id','request_sha256','evidence_scope','images'}
        and receipt['schema']=='agent-model-image-evidence.v1' and receipt['evidence_scope']=='completed_upstream_transport'
        and receipt['job_id']==envelope.job_id and receipt['attempt_id']==envelope.attempt_id
        and receipt['request_sha256']==envelope.request_sha256 and type(receipt['images']) is list
        and 1<=len(receipt['images'])<=10000,'picture_review_v2_admission_image_evidence_missing')
    bodies={row['body_sha256'] for row in calls}
    original_images={sha(inputs[frame['file_name']]):dict(bytes=len(inputs[frame['file_name']]),
        mime='image/png' if frame['file_name'].endswith('.png') else 'image/jpeg')
        for frame in document['observation']['frames']}
    for row in receipt['images']:
        require(type(row) is dict and set(row)=={'sha256','model_request_completed','model_request_sha256',
            'media_type','byte_length','input_item_index','part_index'}
            and type(row['sha256']) is str and SHA.fullmatch(row['sha256'])
            and row['model_request_completed'] is True and row['model_request_sha256'] in bodies
            and row['model_request_sha256']!=connectivity_body and row['media_type'] in {'image/png','image/jpeg'}
            and type(row['byte_length']) is int and 0<row['byte_length']<=2*1024*1024
            and all(type(row[key]) is int and 0<=row[key]<2000 for key in ('input_item_index','part_index')),
            'picture_review_v2_admission_image_call_unbound')
        require(row['sha256'] in original_images and row['byte_length']==original_images[row['sha256']]['bytes']
            and row['media_type']==original_images[row['sha256']]['mime'],
            'picture_review_v2_admission_image_bytes_mismatch')
    observed=[]
    for frame in document['observation']['frames']:
        raw=inputs[frame['file_name']];_image_header(raw,frame['file_name'])
        mime='image/png' if frame['file_name'].endswith('.png') else 'image/jpeg'
        require(sha(raw)==frame['sha256'] and any(row['sha256']==sha(raw) and row['byte_length']==len(raw)
            and row['media_type']==mime for row in receipt['images']), 'picture_review_v2_admission_image_bytes_mismatch')
        if frame['purpose']=='output':
            require(mime=='image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n'),
                'picture_review_v2_admission_actual_png_required')
        observed.append(frame.get('frame_id',frame['file_name']))
    require(any(frame['purpose']=='output' for frame in document['observation']['frames']),
        'picture_review_v2_admission_actual_png_required')
    return dict(schema='video.picture-review-v2-observed-frames.v1',input_sha256=digest(document),
        observed_frame_ids=observed,evidence_scope='completed_upstream_transport',
        model_evidence_sha256=digest(receipt),model_understanding_verified=False,
        whole_video_watched=False,audio_observed=False,motion_observed=False)


def verify_model_budget(record,envelope,calls,observation):
    """Recompute exported provider usage; claim no invented control closure."""
    from agent_service.model_proxy import _response_usage
    require(type(calls) is list and 2<=len(calls)<=8
        and all(type(row) is dict and set(row)=={'body_sha256','response_sha256','state','status'}
            and type(row['body_sha256']) is str and SHA.fullmatch(row['body_sha256'])
            and type(row['response_sha256']) is str and SHA.fullmatch(row['response_sha256'])
            and row['state']=='complete' and type(row['status']) is int and row['status']==200 for row in calls)
        and len({row['body_sha256'] for row in calls})==len(calls),
        'picture_review_v2_admission_model_requests_incomplete')
    evidence=record.get('model_budget_evidence');code='picture_review_v2_admission_model_budget_missing'
    require(type(evidence) is dict and set(evidence)=={'schema','job_id','attempt_id','fence',
        'execution_request_sha256','execution_binding_sha256','grant_sha256','job_request','job_request_sha256','model_limits','proxy_limits',
        'request_count','requests','usage_completeness','totals','physical_stop_scope','cost_status','sha256'},code)
    require(evidence['schema']=='agent-candidate-model-budget.v1'
        and evidence['sha256']==digest({k:v for k,v in evidence.items() if k!='sha256'})
        and evidence['job_id']==envelope.job_id and evidence['attempt_id']==envelope.attempt_id
        and type(evidence['fence']) is int and evidence['fence']==envelope.fence
        and evidence['execution_request_sha256']==envelope.request_sha256
        and evidence['execution_binding_sha256']==envelope.execution_binding.sha256
        and type(evidence['grant_sha256']) is str and SHA.fullmatch(evidence['grant_sha256'])
        and evidence['grant_sha256']!='0'*64
        and evidence['model_limits']==MODEL_LIMITS
        and evidence['proxy_limits']==dict(max_calls_per_attempt=8,max_output_tokens=8192)
        and evidence['physical_stop_scope']=='external_operator_required' and evidence['cost_status']=='unknown'
        and evidence['usage_completeness']=='complete','picture_review_v2_admission_model_budget_binding_mismatch')
    for limits in (evidence['model_limits'],evidence['proxy_limits']):
        require(type(limits) is dict and all(type(value) is int for value in limits.values()),
            'picture_review_v2_admission_model_limits_unproven')
    require(type(evidence['job_request']) is dict and evidence['job_request_sha256']==digest(evidence['job_request']),
        'picture_review_v2_admission_job_request_unbound')
    try:job_request=JobRequest.model_validate(evidence['job_request'])
    except ValueError:raise ServiceError('picture_review_v2_admission_job_request_unbound',422) from None
    require(job_request.model_dump(mode='json')==evidence['job_request']
        and job_request.profile==envelope.profile==method.PROFILE
        and job_request.profile_revision==envelope.profile_revision==method.PROFILE_REVISION
        and job_request.task==envelope.task and job_request.checkpoint_ref is None
        and job_request.model_limits is not None and job_request.model_limits.model_dump(mode='json')==MODEL_LIMITS
        and Counter(item.sha256 for item in job_request.inputs)==Counter(item.sha256 for item in envelope.inputs),
        'picture_review_v2_admission_job_request_unbound')
    rows=evidence['requests']
    require(type(rows) is list and type(evidence['request_count']) is int
        and 2<=len(rows)==evidence['request_count']==len(calls)<=8,
        'picture_review_v2_admission_model_requests_incomplete')
    fields={'attempt_id','fence','execution_request_sha256','body_sha256','state','status','response_sha256',
        'response_media_type','response_bytes','response_base64','enforced_model_limits','usage'}
    require(all(type(row) is dict and set(row)==fields and type(row['body_sha256']) is str
        and SHA.fullmatch(row['body_sha256']) for row in rows)
        and len({row['body_sha256'] for row in rows})==len(rows),
        'picture_review_v2_admission_model_requests_incomplete')
    calls_by_body={row['body_sha256']:row for row in calls}
    require(set(calls_by_body)=={row['body_sha256'] for row in rows},
        'picture_review_v2_admission_model_requests_incomplete')
    actual_usage=[];response_bytes=0
    for row in rows:
        require(row['attempt_id']==envelope.attempt_id and type(row['fence']) is int and row['fence']==envelope.fence
            and row['execution_request_sha256']==envelope.request_sha256
            and row['state']=='complete' and type(row['status']) is int and row['status']==200
            and type(row['response_sha256']) is str and SHA.fullmatch(row['response_sha256'])
            and type(row['response_bytes']) is int and 0<row['response_bytes']<=MAX_RAW_RESPONSES
            and type(row['response_media_type']) is str
            and row['response_media_type'].split(';',1)[0].strip() in {'application/json','text/event-stream'}
            and type(row['response_base64']) is str
            and len(row['response_base64'])==4*((row['response_bytes']+2)//3),
            'picture_review_v2_admission_model_response_unbound')
        measured=row['enforced_model_limits']
        require(type(measured) is dict and set(measured)==set(MODEL_LIMITS)
            and all(type(value) is int for value in measured.values())
            and measured['max_requests']==8 and 1<=measured['max_output_tokens']<=8192,
            'picture_review_v2_admission_model_limits_unproven')
        try:raw=base64.b64decode(row['response_base64'],validate=True)
        except (ValueError,binascii.Error):raise ServiceError('picture_review_v2_admission_model_response_unbound',422) from None
        response_bytes+=len(raw)
        require(len(raw)==row['response_bytes'] and sha(raw)==row['response_sha256']
            and response_bytes<=MAX_RAW_RESPONSES,'picture_review_v2_admission_model_response_unbound')
        require(all(calls_by_body[row['body_sha256']].get(key)==row[key]
            for key in ('state','status','response_sha256')),
            'picture_review_v2_admission_model_response_unbound')
        usage=_response_usage(raw,row['response_media_type'])
        require(type(usage) is dict and type(row['usage']) is dict and set(row['usage'])==set(usage)
            and all(type(row['usage'][key]) is type(value) and row['usage'][key]==value for key,value in usage.items())
            and usage['output_tokens']<=measured['max_output_tokens'],
            'picture_review_v2_admission_provider_usage_incomplete')
        actual_usage.append(usage)
    totals={key:sum(row[key] for row in actual_usage) for key in ('input_tokens','output_tokens','total_tokens')}
    for key in ('cache_read_tokens','cache_write_tokens'):
        totals[key]=sum(row[key] for row in actual_usage) if all(row.get(key) is not None for row in actual_usage) else None
    require(all(value is None or type(value) is int and 0<=value<=2**63-1 for value in totals.values()),
        'picture_review_v2_admission_provider_totals_mismatch')
    require(type(evidence['totals']) is dict and set(evidence['totals'])==set(totals)
        and all(type(evidence['totals'][key]) is type(value) and evidence['totals'][key]==value for key,value in totals.items()),
        'picture_review_v2_admission_provider_totals_mismatch')
    trip=observation.get('model_round_trip',{})
    connectivity=[row for row in rows if row['response_sha256']==trip.get('response_sha256')]
    require(len(connectivity)==1,'picture_review_v2_admission_connectivity_unbound')
    return dict(schema='video.picture-review-v2-model-budget-proof.v1',
        evidence_sha256=digest(evidence),job_request_sha256=evidence['job_request_sha256'],
        model_limits=deepcopy(MODEL_LIMITS),request_count=len(rows),
        provider_usage_complete=True,totals=totals,response_bytes_verified=response_bytes,
        connectivity_body_sha256=connectivity[0]['body_sha256'],cost_status='unknown',
        request_usage_scope='all_candidate_requests_including_connectivity',
        stop_scope='external_operator_required',
        production_control_stop_verified=False)

def assess_candidate(candidate_dir, *, source_root=None):
    """Read one exact new-role capture; write nothing and invoke no processes."""
    directory = _directory(candidate_dir)
    root = _directory(directory / 'candidate-output')
    raw = {name:_capture(root, name) for name in (*EXPORT_NAMES, 'uid-reservation.json', 'worker.log')}
    docs = {name:decode_video_json(value) for name,value in raw.items() if name.endswith('.json')}
    require(all(type(value) is dict for value in docs.values()))
    envelope, result = verify_parent({name:docs[name] for name in PARENT_FILES.values()})
    require(envelope.profile in PROFILES and envelope.profile_revision == '1' and envelope.engine == 'pi'
        and not envelope.checkpoint_inputs and envelope.checkpoint_ref is None,
        'picture_review_v2_admission_profile_not_allowed')
    profile, proof = envelope.profile, result.stop_receipt
    method = _protocol(profile)
    inputs = {item.name:item.content() for item in envelope.inputs}
    require(len(inputs) == len(envelope.inputs) and method.INPUT_NAME in inputs,
            'picture_review_v2_admission_input_mismatch')
    document = method.validate_input(decode_video_json(inputs[method.INPUT_NAME]))
    require(inputs[method.INPUT_NAME] == canonical_bytes(document)
        and envelope.task == dict(input_sha256=digest(document))
        and (document['mode'] != 'create') == (profile == method.REVIEW_PROFILE),
        'picture_review_v2_admission_input_mismatch')
    require(set(inputs) == {method.INPUT_NAME, *[f['file_name'] for f in document['observation']['frames']]},
            'picture_review_v2_admission_input_mismatch')
    for frame in document['observation']['frames']:
        require(sha(inputs[frame['file_name']]) == frame['sha256'],
                'picture_review_v2_admission_input_mismatch')
        _image_header(inputs[frame['file_name']], frame['file_name'])
    for name in (method.INPUT_NAME, method.RESULT_NAME):
        raw[name] = _capture(root, name, method.OUTPUT_MAX_BYTES)
    require(raw[method.INPUT_NAME] == inputs[method.INPUT_NAME] and len(result.artifacts) == 1
        and result.artifacts[0].name == method.RESULT_NAME
        and result.artifacts[0].content() == raw[method.RESULT_NAME],
        'picture_review_v2_admission_artifact_mismatch')
    artifact = method.validate_result(decode_video_json(raw[method.RESULT_NAME]), digest(document), document)
    record, observation = docs['candidate-evidence.json'], docs['candidate-result.json']['observations']
    try:host, policy, binding = candidate_config(docs['host-config.json'])
    except WorkerConfigError:raise ServiceError('picture_review_v2_admission_host_config_invalid',422) from None
    registry = _host_registry(host)
    combination = binding_fingerprint(registry.contract(profile, '1'),
        registry.bundle(binding.bundle.name, binding.bundle.revision),
        registry.environment(binding.tool_environment.name, binding.tool_environment.revision), binding)
    require(policy.matches(envelope) and policy.execution_binding == envelope.execution_binding
        and record.get('schema_version') == 'agent-candidate-probe.v1'
        and record.get('candidate_only') is True and record.get('candidate_scope_only') is True
        and record.get('production_acceptance_created') is False and record.get('production_routes_changed') is False
        and record.get('profile_revision') == '1'
        and record.get('binding_id') == binding.id and record.get('binding_revision') == binding.revision
        and record.get('combination_sha256') == combination
        and record.get('execution_binding_sha256') == envelope.execution_binding.sha256
        and record.get('observations') == observation
        and type(record.get('artifact_count')) is int and record['artifact_count'] == 1 and record.get('artifacts') == [dict(
            name=method.RESULT_NAME, sha256=sha(raw[method.RESULT_NAME]), bytes=len(raw[method.RESULT_NAME]))]
        and proof is not None and proof.fence == envelope.fence and proof.engine == envelope.engine
        and 0 < proof.observed_at <= time.time() + 5 and len(set(proof.resource_ids)) == len(proof.resource_ids),
        'picture_review_v2_admission_controller_mismatch')
    scenario = docs['scenario.json']
    require(set(scenario) == {'schema_version','candidate_scope_only','scenario_id','profile',
        'timeout_seconds','task','inputs','business_export','parent'}
        and scenario.get('schema_version') == 'agent-candidate-scenario.v1'
        and scenario.get('candidate_scope_only') is True and scenario.get('profile') == profile
        and scenario.get('task') == envelope.task and scenario.get('business_export') is None
        and scenario.get('parent') is None and type(scenario.get('inputs')) is list
        and len(scenario['inputs']) == len(inputs)
        and {row.get('name'):(row.get('sha256'),row.get('bytes')) for row in scenario['inputs']}
        == {name:(sha(value),len(value)) for name,value in inputs.items()},
        'picture_review_v2_admission_scenario_mismatch')
    package = _source_package(source_root)
    measured_bundle = _bundle(package, registry, binding, policy)
    harness = sha(_capture(package, 'deploy/acceptance_probe.py').decode().replace('\r\n','\n').encode())
    scenario_harness = sha(_capture(package, 'deploy/candidate_scenario.py').decode().replace('\r\n','\n').encode())
    require(observation.get('harness_sha256') == record.get('harness_sha256') == harness
        and observation.get('scenario') == record.get('scenario')
        and record.get('scenario',{}).get('scenario_harness_sha256') == scenario_harness,
        'picture_review_v2_admission_harness_mismatch')
    maps = [row for row in docs['manifest.json'].get('items',[]) if row.get('kind') == 'ConfigMap'
            and row.get('metadata',{}).get('name') == directory.name]
    require(len(maps) == 2 and {row['metadata'].get('namespace') for row in maps}
        == {'agent-service','agent-workers'} and all(row.get('immutable') is True for row in maps)
        and maps[0].get('data') == maps[1].get('data') and type(maps[0].get('data')) is dict,
        'picture_review_v2_admission_harness_mismatch')
    projected = maps[0]['data']
    expected_host = deepcopy(docs['host-config.json'])
    expected_host['model']['proxy_base_url'] = 'http://' + directory.name + '.agent-service.svc.cluster.local:8080/v1'
    require(set(projected) == {'config.json','acceptance_probe.py','candidate_scenario.py','scenario.json'}
        and decode_video_json(projected.get('config.json')) == expected_host
        and sha(projected.get('acceptance_probe.py','').encode()) == harness
        and sha(projected.get('candidate_scenario.py','').encode()) == scenario_harness
        and decode_video_json(projected.get('scenario.json')) == scenario,
        'picture_review_v2_admission_harness_mismatch')
    capability = observation.get('capability',{})
    require(capability.get('engine') == 'pi' and capability.get('package_measured') is True
        and set(capability.get('allowed_tools',[])) == {'read_inputs','skill_search','skill_read','write'}
        and capability.get('pi_cage_repository') == PI_CAGE_REPOSITORY
        and capability.get('pi_cage_revision') == PI_CAGE_REVISION and capability.get('pi_version') == PI_VERSION
        and type(capability.get('node_version')) is str and re.fullmatch(r'24\.\d+\.\d+',capability['node_version']),
        'picture_review_v2_admission_capability_missing')
    reservation = docs['uid-reservation.json']
    uid, node = reservation.get('uid_start'), reservation.get('node')
    require(type(uid) is int and 65536 <= uid <= 2**31-4 and reservation.get('uid_count') == 3
        and reservation.get('reusable') is False and reservation.get('name') == directory.name
        and type(node) is str and bool(node), 'picture_review_v2_admission_uid_missing')
    isolation = observation.get('isolation',{})
    confinement = isolation.get('controller_confinement',{})
    require(all(isolation.get(key) is True for key in ISOLATION)
        and isolation.get('groups') == [] and isolation.get('uid') == [uid+2]*3
        and {'uid:'+str(uid),'uid:'+str(uid+1)} <= set(proof.resource_ids)
        and confinement.get('no_new_privileges') is True and confinement.get('seccomp_filtering') is True
        and confinement.get('apparmor_label') == 'agent-capsule-inputs (enforce)',
        'picture_review_v2_admission_isolation_missing')
    planned = {(row['metadata']['namespace'],row['metadata']['name']):row
        for row in docs['manifest.json'].get('items',[]) if row.get('kind') == 'Pod'}
    require(len(planned) == sum(row.get('kind') == 'Pod' for row in docs['manifest.json']['items']) == 2,
            'picture_review_v2_admission_pod_mismatch')
    stopped = {}
    for namespace, role in (('agent-workers','worker'),('agent-service','proxy')):
        pod = docs[role+'-pod.json']
        require((namespace,directory.name+'-'+role) in planned,'picture_review_v2_admission_pod_mismatch')
        _pod(pod, planned[namespace,directory.name+'-'+role], envelope.image_digest, completed_worker=role=='worker')
        stopped[role] = _pod_stop(pod,worker=role=='worker',proof=proof,uid=uid,node=node)
    require(stopped['worker']['pod_uid'] == record.get('pod_uid') != stopped['proxy']['pod_uid'],
            'picture_review_v2_admission_pod_mismatch')
    calls = record.get('model_calls')
    require(type(calls) is list and 1 <= len(calls) <= 8 and all(type(row) is dict
        and row.get('state') == 'complete' and type(row.get('status')) is int and row['status'] == 200
        and all(type(row.get(key)) is str and SHA.fullmatch(row[key]) for key in
                ('body_sha256','response_sha256')) for row in calls)
        and len({row['body_sha256'] for row in calls}) == len(calls),
        'picture_review_v2_admission_model_calls_missing')
    trip = observation.get('model_round_trip',{})
    require(trip.get('api_mode') == 'responses' and type(trip.get('status')) is int and trip['status'] == 200
        and trip.get('response_sha256') in {row['response_sha256'] for row in calls},
        'picture_review_v2_admission_round_trip_missing')
    budget = verify_model_budget(record,envelope,calls,observation)
    transport = _frames(document,inputs,record,envelope,calls,budget['connectivity_body_sha256'])
    finished = []
    for line in raw['worker.log'].splitlines():
        try:
            row = decode_video_json(line)
        except ServiceError:
            continue
        if type(row) is dict and row.get('status') == 'candidate_probe_finished':
            finished.append(row)
    require(len(finished) == 1 and finished[0].get('engine') == 'pi'
        and finished[0].get('profile') == profile and finished[0].get('result_state') == 'completed'
        and type(finished[0].get('lease_expired_http_status')) is int
        and finished[0]['lease_expired_http_status'] == 401
        and finished[0].get('pod_uid') == stopped['worker']['pod_uid']
        and finished[0].get('production_acceptance_created') is False,
        'picture_review_v2_admission_real_lease_401_missing')
    summary = docs['operator-summary.json']
    require(summary.get('name') == directory.name and summary.get('status') == 'completed'
        and summary.get('profile') == profile and summary.get('engine') == 'pi'
        and summary.get('image_identity_valid') is True and summary.get('production_acceptance_created') is False
        and summary.get('production_routes_changed') is False and type(summary.get('model_calls')) is int
        and summary['model_calls'] == len(calls) and summary.get('result_state') == 'completed'
        and summary.get('lease_expiry_rejected') is True,'picture_review_v2_admission_operator_mismatch')
    require(all(_capture(root,name) == value for name,value in raw.items()),
            'picture_review_v2_admission_capture_changed')
    report = dict(schema='video-picture-review-v2-independent-review.v1',
        evidence_origin='captured_real_candidate',profile=profile,profile_revision='1',engine='pi',
        candidate_name=directory.name,image_digest=envelope.image_digest,runtime_revision=envelope.runtime_revision,
        candidate_job_id=envelope.job_id,candidate_attempt_id=envelope.attempt_id,
        binding_id=binding.id,binding_revision=binding.revision,combination_sha256=combination,
        source_files_sha256={name:sha(value) for name,value in raw.items()},
        measured_bundle_files_sha256=measured_bundle,measured_harness_sha256=harness,
        measured_scenario_harness_sha256=scenario_harness,
        checks=dict(capability=capability,isolation=isolation,model_round_trip=trip,
            model_calls_complete=len(calls),model_proxy_lease=dict(worker_log_http_status=401),
            stopped_worker=stopped['worker'],stopped_proxy=stopped['proxy'],
            input_sha256=digest(document),output_sha256=sha(raw[method.RESULT_NAME])),
        model_frame_transport=transport,model_budget=budget,
        candidate_external_stop_verified=True,production_control_usage_closure_claimed=False,
        original_candidate_flags_preserved=True,technical_host_admission_only=True,
        production_routes_enabled=False,deployment_performed=False,model_requests=0,uid_allocations=0,
        live_http_verified=False,not_claimed=NOT_CLAIMED)
    return host.model_dump(mode='json'), report


def review_candidate(candidate_dir, output, *, source_root=None):
    """Create a new disabled technically admitted host after all checks pass."""
    directory, output = _directory(candidate_dir), Path(output).absolute()
    require(not output.exists() and not output.is_symlink() and not output.is_relative_to(directory),
            'picture_review_v2_admission_new_output_required')
    _directory(output.parent)
    host,report = assess_candidate(directory,source_root=source_root)
    combination,profile = report['combination_sha256'],report['profile']
    name = report['binding_id']+'-'+combination[:20]
    require(len(name)<=63,'picture_review_v2_admission_host_name_invalid')
    output.mkdir(mode=0o700)
    target = output/'worker-configs'/name
    target.mkdir(mode=0o700,parents=True)
    checksum = _save(target/'acceptance-evidence.json',report)
    suite = re.sub(r'\.v([0-9]+)$', r'-contract.v\1', profile)
    approval = dict(combination_sha256=combination,suite=suite,report_sha256=checksum)
    binding = next(row for row in host['execution_catalog']['execution_bindings']
        if (row['id'],row['revision']) == (report['binding_id'],report['binding_revision']))
    require(binding.get('acceptance') is None,'picture_review_v2_admission_already_accepted')
    binding['acceptance']=approval
    config = HostConfig.model_validate(host)
    policy = host_policy(config)
    require(not policy.enabled and all(row.get('enabled') is False for row in host['execution_catalog']['routes'])
        and policy.execution_binding.combination_sha256==combination)
    _save(target/'config.json',host)
    _save(target/'acceptance.json',dict(schema_version=1,engine='pi',profile=profile,profile_revision='1',
        runtime_revision=report['runtime_revision'],image_digest=report['image_digest'],model_api='openai-responses',
        checks={key:True for key in CHECKS},evidence_sha256=checksum,
        execution_binding_sha256=policy.execution_binding.sha256))
    require(accepted_host(config,target/'acceptance.json'),'picture_review_v2_admission_host_rejected')
    _save(output/'binding-approvals.json',[dict(binding_id=report['binding_id'],
        binding_revision=report['binding_revision'],acceptance=approval,host_config_map=name,
        path='worker-configs/'+name)])
    summary = dict(schema='picture-review-v2-technical-admission.v1',profile=profile,
        combination_sha256=combination,report_sha256=checksum,
        accepted_execution_binding_sha256=policy.execution_binding.sha256,host_config_map=name,
        accepted_host=True,enabled=False,technical_host_admission_only=True,deployment_performed=False,
        model_requests=0,uid_allocations=0,source_evidence_modified=False,live_http_verified=False,not_claimed=NOT_CLAIMED)
    _save(output/'summary.json',summary)
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-dir',required=True,type=Path)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    import json
    try:
        require(os.name=='posix' and os.geteuid()==0,'picture_review_v2_admission_server_root_required')
        summary=review_candidate(args.candidate_dir,args.output,source_root=args.source)
    except (OSError,ValueError,KeyError,TypeError,ServiceError,AttributeError) as error:
        reason=error.code if isinstance(error,ServiceError) else 'malformed_capture'
        print(json.dumps(dict(status='technical_admission_rejected',enabled=False,reason=reason,
            deployment_performed=False,model_requests=0,automatic_retry=False)))
        return 1
    print(json.dumps(summary,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
