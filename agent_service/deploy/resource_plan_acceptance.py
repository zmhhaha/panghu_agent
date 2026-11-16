"""Attest a stopped resource planner against its own contract and measured bundle.

Only original operator captures are accepted. This performs no Agent/model,
renderer, route activation or deployment, and grants no subjective approval.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.deploy.acceptance_probe import candidate_config
from agent_service.deploy.animation_creation_acceptance import (
    CHECKS, ISOLATION, _capture, _directory, _pod_stop, _save, _source_package)
from agent_service.deploy.candidate_scenario import PARENT_FILES, verify_parent
from agent_service.input_tools import (READ_INPUTS_ENVIRONMENT_NAME,
    READ_INPUTS_EXTENSION_SHA256, read_inputs_environment_revision)
from agent_service.pi_cage import PI_CAGE_REPOSITORY, PI_CAGE_REVISION, PI_VERSION
from agent_service.profiles import video_resource_plan as planner
from agent_service.profiles.video import decode_video_json
from agent_service.registry import binding_fingerprint, required_model_capabilities
from agent_service.worker_entry import HostConfig, _host_registry, accepted_host, host_policy
from video_workbench.animation_candidate_import import EXPORT_NAMES, _pod


PROFILES = frozenset({planner.PROFILE})
SUITE = 'video.resource-plan-contract.v1'
NOT_CLAIMED = ['offline_hmac_reverification', 'production_api_dispatcher_end_to_end',
    'business_user_approval', 'subjective_animation_quality', 'model_understanding', 'producer_admission', 'resource_creation',
    'whole_video_watched', 'audio_quality', 'narration_quality', 'method_application', 'other_profiles_or_images']
SHA = re.compile(r'[0-9a-f]{64}\Z')


def require(value, code='resource_plan_admission_invalid'):
    if not value:
        raise ServiceError(code, 422)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _bundle(package, registry, binding, policy):
    handler = planner.VideoResourcePlanProfile()
    contract = registry.contract(binding.profile, policy.revision)
    bundle = registry.bundle(binding.bundle.name, binding.bundle.revision)
    environment = registry.environment(binding.tool_environment.name, binding.tool_environment.revision)
    require(bundle.name == 'video-resource-plan' and bundle.revision == planner.BUNDLE_REVISION
        and set(contract.required_tools) == set(bundle.required_tools) == set(environment.tools)
        == {'read_inputs', 'write'} and environment.name == READ_INPUTS_ENVIRONMENT_NAME
        and environment.revision == read_inputs_environment_revision(environment.model_dump(mode='json')),
        'resource_plan_admission_tools_mismatch')
    require(set(required_model_capabilities(handler)) <= set(binding.model_policy.capabilities),
            'resource_plan_admission_model_policy_mismatch')
    root = package / 'resources' / planner.BUNDLE_REVISION
    raw = _capture(root, 'manifest.json', 65536)
    require(sha(raw) == planner.BUNDLE_MANIFEST_SHA256, 'resource_plan_admission_resource_mismatch')
    manifest = decode_video_json(raw)
    require(manifest.get('bundle_revision') == planner.BUNDLE_REVISION
        and manifest.get('automatic_updates') is False and manifest.get('upstream_content_vendored') is False
        and manifest.get('license') == 'MIT', 'resource_plan_admission_resource_mismatch')
    rows = manifest.get('local_files')
    require(type(rows) is list and len(rows) == len(planner.BUNDLE_FILES)
        and all(type(row) is dict and set(row) == {'path', 'sha256'} for row in rows)
        and {row['path'] for row in rows} == planner.BUNDLE_FILES,
        'resource_plan_admission_resource_mismatch')
    measured = {'manifest.json': sha(raw)}
    for row in rows:
        measured[row['path']] = sha(_capture(root, row['path'], 128 * 1024))
        require(measured[row['path']] == row['sha256'], 'resource_plan_admission_resource_mismatch')
    require({row.name:row.sha256 for row in bundle.resources} == measured
        and sha(_capture(package, 'read_inputs_extension.mjs', 128 * 1024)) == READ_INPUTS_EXTENSION_SHA256,
        'resource_plan_admission_resource_mismatch')
    return measured


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
        'resource_plan_admission_profile_not_allowed')
    profile, proof = envelope.profile, result.stop_receipt
    inputs = {item.name:item.content() for item in envelope.inputs}
    require(len(inputs) == len(envelope.inputs) and planner.INPUT_NAME in inputs,
            'resource_plan_admission_input_mismatch')
    document = planner.validate_input(decode_video_json(inputs[planner.INPUT_NAME]))
    require(inputs[planner.INPUT_NAME] == canonical_bytes(document)
        and envelope.task == {'input_sha256':digest(document)}
        and set(inputs) == {planner.INPUT_NAME}, 'resource_plan_admission_input_mismatch')
    for name in (planner.INPUT_NAME, planner.RESULT_NAME):
        raw[name] = _capture(root, name, planner.INPUT_MAX_BYTES if name == planner.INPUT_NAME else planner.OUTPUT_MAX_BYTES)
    require(raw[planner.INPUT_NAME] == inputs[planner.INPUT_NAME] and len(result.artifacts) == 1
        and result.artifacts[0].name == planner.RESULT_NAME
        and result.artifacts[0].content() == raw[planner.RESULT_NAME],
        'resource_plan_admission_artifact_mismatch')
    artifact = planner.validate_result(decode_video_json(raw[planner.RESULT_NAME]), digest(document), document)
    record, observation = docs['candidate-evidence.json'], docs['candidate-result.json']['observations']
    host, policy, binding = candidate_config(docs['host-config.json'])
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
            name=planner.RESULT_NAME, sha256=sha(raw[planner.RESULT_NAME]), bytes=len(raw[planner.RESULT_NAME]))]
        and proof is not None and proof.fence == envelope.fence and proof.engine == envelope.engine
        and 0 < proof.observed_at <= time.time() + 5 and len(set(proof.resource_ids)) == len(proof.resource_ids),
        'resource_plan_admission_controller_mismatch')
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
        'resource_plan_admission_scenario_mismatch')
    package = _source_package(source_root)
    measured_bundle = _bundle(package, registry, binding, policy)
    harness = sha(_capture(package, 'deploy/acceptance_probe.py').decode().replace('\r\n','\n').encode())
    scenario_harness = sha(_capture(package, 'deploy/candidate_scenario.py').decode().replace('\r\n','\n').encode())
    require(observation.get('harness_sha256') == record.get('harness_sha256') == harness
        and observation.get('scenario') == record.get('scenario')
        and record.get('scenario',{}).get('scenario_harness_sha256') == scenario_harness,
        'resource_plan_admission_harness_mismatch')
    maps = [row for row in docs['manifest.json'].get('items',[]) if row.get('kind') == 'ConfigMap'
            and row.get('metadata',{}).get('name') == directory.name]
    require(len(maps) == 2 and {row['metadata'].get('namespace') for row in maps}
        == {'agent-service','agent-workers'} and all(row.get('immutable') is True for row in maps)
        and maps[0].get('data') == maps[1].get('data') and type(maps[0].get('data')) is dict,
        'resource_plan_admission_harness_mismatch')
    projected = maps[0]['data']
    expected_host = deepcopy(docs['host-config.json'])
    expected_host['model']['proxy_base_url'] = 'http://' + directory.name + '.agent-service.svc.cluster.local:8080/v1'
    require(set(projected) == {'config.json','acceptance_probe.py','candidate_scenario.py','scenario.json'}
        and decode_video_json(projected.get('config.json')) == expected_host
        and sha(projected.get('acceptance_probe.py','').encode()) == harness
        and sha(projected.get('candidate_scenario.py','').encode()) == scenario_harness
        and decode_video_json(projected.get('scenario.json')) == scenario,
        'resource_plan_admission_harness_mismatch')
    capability = observation.get('capability',{})
    require(capability.get('engine') == 'pi' and capability.get('package_measured') is True
        and capability.get('allowed_tools') == ['read_inputs','write']
        and capability.get('pi_cage_repository') == PI_CAGE_REPOSITORY
        and capability.get('pi_cage_revision') == PI_CAGE_REVISION and capability.get('pi_version') == PI_VERSION
        and type(capability.get('node_version')) is str and re.fullmatch(r'24\.\d+\.\d+',capability['node_version']),
        'resource_plan_admission_capability_missing')
    reservation = docs['uid-reservation.json']
    uid, node = reservation.get('uid_start'), reservation.get('node')
    require(type(uid) is int and 65536 <= uid <= 2**31-4 and reservation.get('uid_count') == 3
        and reservation.get('reusable') is False and reservation.get('name') == directory.name
        and type(node) is str and bool(node), 'resource_plan_admission_uid_missing')
    isolation = observation.get('isolation',{})
    confinement = isolation.get('controller_confinement',{})
    require(all(isolation.get(key) is True for key in ISOLATION)
        and isolation.get('groups') == [] and isolation.get('uid') == [uid+2]*3
        and {'uid:'+str(uid),'uid:'+str(uid+1)} <= set(proof.resource_ids)
        and confinement.get('no_new_privileges') is True and confinement.get('seccomp_filtering') is True
        and confinement.get('apparmor_label') == 'agent-capsule-inputs (enforce)',
        'resource_plan_admission_isolation_missing')
    planned = {(row['metadata']['namespace'],row['metadata']['name']):row
        for row in docs['manifest.json'].get('items',[]) if row.get('kind') == 'Pod'}
    require(len(planned) == sum(row.get('kind') == 'Pod' for row in docs['manifest.json']['items']) == 2,
            'resource_plan_admission_pod_mismatch')
    stopped = {}
    for namespace, role in (('agent-workers','worker'),('agent-service','proxy')):
        pod = docs[role+'-pod.json']
        require((namespace,directory.name+'-'+role) in planned,'resource_plan_admission_pod_mismatch')
        _pod(pod, planned[namespace,directory.name+'-'+role], envelope.image_digest, completed_worker=role=='worker')
        stopped[role] = _pod_stop(pod,worker=role=='worker',proof=proof,uid=uid,node=node)
    require(stopped['worker']['pod_uid'] == record.get('pod_uid') != stopped['proxy']['pod_uid'],
            'resource_plan_admission_pod_mismatch')
    calls = record.get('model_calls')
    require(type(calls) is list and 1 <= len(calls) <= 8 and all(type(row) is dict
        and row.get('state') == 'complete' and type(row.get('status')) is int and row['status'] == 200
        and all(type(row.get(key)) is str and SHA.fullmatch(row[key]) for key in
                ('body_sha256','response_sha256')) for row in calls)
        and len({row['body_sha256'] for row in calls}) == len(calls),
        'resource_plan_admission_model_calls_missing')
    trip = observation.get('model_round_trip',{})
    require(trip.get('api_mode') == 'responses' and type(trip.get('status')) is int and trip['status'] == 200
        and trip.get('response_sha256') in {row['response_sha256'] for row in calls},
        'resource_plan_admission_round_trip_missing')
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
        'resource_plan_admission_real_lease_401_missing')
    summary = docs['operator-summary.json']
    require(summary.get('name') == directory.name and summary.get('status') == 'completed'
        and summary.get('profile') == profile and summary.get('engine') == 'pi'
        and summary.get('image_identity_valid') is True and summary.get('production_acceptance_created') is False
        and summary.get('production_routes_changed') is False and type(summary.get('model_calls')) is int
        and summary['model_calls'] == len(calls) and summary.get('result_state') == 'completed'
        and summary.get('lease_expiry_rejected') is True,'resource_plan_admission_operator_mismatch')
    require(all(_capture(root,name) == value for name,value in raw.items()),
            'resource_plan_admission_capture_changed')
    report = dict(schema='video-resource-plan-independent-review.v1',
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
            input_sha256=digest(document),output_sha256=sha(raw[planner.RESULT_NAME]),
            normalized_output_sha256=digest(artifact), planned_resource_count=len(artifact['resource_plan']['nodes'])),
        rendering_verified=False, subjective_plan_quality_approved=False,
        original_candidate_flags_preserved=True,technical_host_admission_only=True,
        production_routes_enabled=False,deployment_performed=False,model_requests=0,uid_allocations=0,
        live_http_verified=False,not_claimed=NOT_CLAIMED)
    return host.model_dump(mode='json'), report


def review_candidate(candidate_dir, output, *, source_root=None):
    """Create a new disabled technically admitted host after all checks pass."""
    directory, output = _directory(candidate_dir), Path(output).absolute()
    require(not output.exists() and not output.is_symlink() and not output.is_relative_to(directory),
            'resource_plan_admission_new_output_required')
    _directory(output.parent)
    host,report = assess_candidate(directory,source_root=source_root)
    combination,profile = report['combination_sha256'],report['profile']
    name = report['binding_id']+'-'+combination[:20]
    require(len(name)<=63,'resource_plan_admission_host_name_invalid')
    output.mkdir(mode=0o700)
    target = output/'worker-configs'/name
    target.mkdir(mode=0o700,parents=True)
    checksum = _save(target/'acceptance-evidence.json',report)
    approval = dict(combination_sha256=combination,suite=SUITE,report_sha256=checksum)
    binding = next(row for row in host['execution_catalog']['execution_bindings']
        if (row['id'],row['revision']) == (report['binding_id'],report['binding_revision']))
    require(binding.get('acceptance') is None,'resource_plan_admission_already_accepted')
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
    require(accepted_host(config,target/'acceptance.json'),'resource_plan_admission_host_rejected')
    _save(output/'binding-approvals.json',[dict(binding_id=report['binding_id'],
        binding_revision=report['binding_revision'],acceptance=approval,host_config_map=name,
        path='worker-configs/'+name)])
    summary = dict(schema='resource-plan-technical-admission.v1',profile=profile,
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
        require(os.name=='posix' and os.geteuid()==0,'resource_plan_admission_server_root_required')
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
