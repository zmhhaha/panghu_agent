"""Synthetic stopped exports exercise import only; no real Pods or model calls."""
from copy import deepcopy
import hashlib
import json
import shutil
import time
from uuid import UUID

import pytest

from agent_service.contracts import (ExecutionEnvelope, InlineFile, ServiceError, WorkerResult,
    canonical_bytes, digest, sign_receipt)
from agent_service.deploy.acceptance_probe import candidate_config
from agent_service.profiles import video_animation_create as creation
from agent_service.profiles import video_animation_review as visual
from video_workbench.animation_candidate_import import import_phase_one, ImportedCandidateOnlyGateway, validate_candidate
from video_workbench.animation_observation import load_bundle
from video_workbench.animation_polish import AnimationPolishCoordinator
from video_workbench.animation_revision_store import AnimationRevisionStore
from test_animation_review_catalog import prepare, IMAGE
from test_video_animation_review_profile import PNG, input_document, opinion
from test_video_animation_create_profile import source_output, input_document as creation_document


IMAGE_DIGEST = IMAGE.rsplit('@', 1)[1]


def save(path, value):
    path.write_bytes(value if type(value) is bytes else canonical_bytes(value))


def bundle(root, name, document, input_name, profile):
    directory = root / name
    (directory / 'inputs').mkdir(parents=True)
    raw = canonical_bytes(document)
    files = {input_name:raw}
    for frame in document['observation']['frames']:
        files[frame['file_name']] = PNG
    for file_name, content in files.items():
        save(directory / 'inputs' / file_name, content)
    save(directory / 'evidence.json', dict(schema='video.animation-observation-preparation.v1',
        profile=profile, input_name=input_name, input_sha256=digest(document)))
    return files


def artifact(role, document):
    if role == 'create':
        output = source_output()
        return dict(schema=creation.RESULT_SCHEMA, input_sha256=digest(document),
            prompt_revision=document['prompt_revision'], **output, source_sha256=creation.source_sha256(output),
            files_sha256={n:hashlib.sha256(v.encode()).hexdigest() for n,v in output['files'].items()},
            base_source_sha256=document['base_source']['source_sha256'], review_sha256=digest(document['review']))
    review = role == 'review'
    judgment = opinion(review)
    judgment['limitations'] = list(dict.fromkeys(
        document['observation']['limitations'] + judgment['limitations']))
    return dict(schema=visual.REVIEW_RESULT_SCHEMA if review else visual.OBSERVE_RESULT_SCHEMA,
        input_sha256=digest(document), prompt_revision=document['prompt_revision'],
        media_sha256=document['media']['sha256'], coverage='sampled_frames', evidence_scope='visual_only',
        frame_evidence_status='supplied_only', observed_frame_ids=[f['frame_id'] for f in document['observation']['frames']],
        **judgment, **({'source_sha256':document['source_sha256']} if review else {}))


def candidate(root, role, document, files, host_value, *, failed=False, directory_name=None):
    directory = root / (directory_name or ('candidate-animation-' + role + '-p1'))
    output = directory / 'candidate-output'
    output.mkdir(parents=True)
    host, policy, _ = candidate_config(host_value)
    number = {'observe':120,'review':130,'create':140}[role]
    pins = {k:v for k,v in policy.pins().items() if k in ExecutionEnvelope.model_fields}
    envelope = ExecutionEnvelope(**pins, job_id=str(UUID(int=number)), attempt_id=str(UUID(int=number+1)),
        fence=1, deadline_at=time.time()+600, lease_expires_at=time.time()+300,
        task={'input_sha256':digest(document)}, inputs=[InlineFile.from_bytes(n,v) for n,v in files.items()])
    input_name = creation.INPUT_NAME if role == 'create' else visual.REVIEW_INPUT_NAME if role == 'review' else visual.OBSERVE_INPUT_NAME
    result_name = input_name.replace('-input.json', '-result.json')
    value = artifact(role, document)
    result = WorkerResult(state='failed' if failed else 'completed',
        artifacts=[] if failed else [InlineFile.from_bytes(result_name,canonical_bytes(value))])
    result = result.model_copy(update={'stop_receipt':sign_receipt(envelope, result, b'synthetic-only-key'*4,
        ('stopped-offline-writer',), time.time()-1)})
    observations = dict(harness_sha256='d'*64, scenario=dict(scenario_harness_sha256='e'*64))
    record = dict(schema_version='agent-candidate-probe.v1', candidate_only=True, candidate_scope_only=True,
        production_acceptance_created=False, production_routes_changed=False,
        engine='pi', profile=envelope.profile, profile_revision='1', image_digest=IMAGE_DIGEST,
        binding_id=host.binding_id, binding_revision=host.binding_revision,
        combination_sha256=envelope.execution_binding.combination_sha256,
        execution_binding_sha256=envelope.execution_binding.sha256,
        job_id=envelope.job_id, attempt_id=envelope.attempt_id, request_sha256=envelope.request_sha256,
        candidate_result_sha256=digest(result.model_dump(mode='json')), result_state=result.state,
        observations=observations, harness_sha256='d'*64, scenario=observations['scenario'],
        lease_expiry_rejected=True, writer_resources=result.stop_receipt.resource_ids,
        writers_observed_stopped_at=result.stop_receipt.observed_at,
        artifacts=[dict(name=item.name,sha256=item.sha256,bytes=len(item.content())) for item in result.artifacts],
        artifact_count=len(result.artifacts), pod_uid=str(UUID(int=number+2)))
    if role != 'create':
        record['model_image_evidence'] = dict(schema='agent-model-image-evidence.v1',
            job_id=envelope.job_id, attempt_id=envelope.attempt_id, request_sha256=envelope.request_sha256,
            evidence_scope='completed_upstream_transport', images=[dict(sha256=hashlib.sha256(PNG).hexdigest(),
                model_request_completed=True, model_request_sha256='f'*64, media_type='image/png',
                byte_length=len(PNG), input_item_index=1, part_index=0)])
    pods = []
    for namespace,suffix,name in [('agent-workers','-worker','worker'),('agent-service','-proxy','proxy')]:
        spec = dict(securityContext={'seccompProfile':{'type':'RuntimeDefault'}},
            automountServiceAccountToken=False,hostPID=False,hostIPC=False,hostNetwork=False,
            shareProcessNamespace=False,restartPolicy='Never',nodeSelector={'kubernetes.io/hostname':'offline-node'},
            containers=[dict(name=name,image=IMAGE,securityContext={'runAsUser':0,'allowPrivilegeEscalation':False},
                             env=[dict(name='POD_UID',valueFrom={'fieldRef':{'fieldPath':'metadata.uid'}})],
                             volumeMounts=[dict(name='sources',mountPath='/inputs',readOnly=True)])])
        planned = dict(kind='Pod',metadata=dict(namespace=namespace,name=directory.name+suffix),spec=spec)
        pods.append(planned)
        actual = deepcopy(planned)
        actual['metadata']['uid'] = record['pod_uid'] if name == 'worker' else str(UUID(int=number+3))
        actual['spec']['nodeName'] = 'offline-node'
        # Represent normal API output defaults, not the unsent manifest copy.
        for key in ('hostPID','hostIPC','hostNetwork'):
            actual['spec'].pop(key)
        actual['spec']['containers'][0]['env'][0]['valueFrom']['fieldRef']['apiVersion']='v1'
        actual['status'] = dict(containerStatuses=[dict(name=name, restartCount=0,
            imageID='docker-pullable://'+IMAGE,state={'terminated':{'exitCode':2 if failed and name=='worker' else 0}})])
        save(output / (name+'-pod.json'),actual)
    save(output/'manifest.json',dict(kind='List',items=pods))
    save(output/'candidate-request.json',dict(schema_version='agent-candidate-request.v1',candidate_scope_only=True,
                                             envelope=envelope.model_dump(mode='json')))
    save(output/'candidate-result.json',dict(result=result.model_dump(mode='json'),observations=observations))
    save(output/'candidate-evidence.json',record)
    save(output/'host-config.json',host_value)
    save(output/'scenario.json',dict(candidate_scope_only=True,profile=envelope.profile,task=envelope.task,
        inputs=[dict(name=n,sha256=hashlib.sha256(v).hexdigest(),bytes=len(v)) for n,v in files.items()]))
    save(output/'operator-summary.json',dict(status='candidate_failed' if failed else 'completed',
        profile=envelope.profile,engine='pi',image_identity_valid=True,production_acceptance_created=False,
        production_routes_changed=False))
    if not failed:
        save(output/result_name,value)
    return directory, value


def make_pilot(tmp_path, *, review_profile=visual.REVIEW_PROFILE, with_observer=True, candidate_names=None):
    candidate_names = {} if candidate_names is None else candidate_names
    _, hosts, _ = prepare(include_sequence_review=review_profile == visual.REVIEW_SEQUENCE_PROFILE)
    context = creation_document()
    base = dict(source_revision='original-r1',engine=source_output()['engine'],files=source_output()['files'])
    base['source_sha256'] = creation.source_sha256(base)
    save(tmp_path/'base-source.json',base)
    save(tmp_path/'creation-context.json',context)
    review_doc = input_document()
    review_doc['source_sha256'] = base['source_sha256']
    review_files = bundle(tmp_path,'review-original-offset',review_doc,visual.REVIEW_INPUT_NAME,review_profile)
    save(tmp_path/'review-selection.json',{'directory':'review-original-offset'})
    if with_observer:
        observation = input_document(False)
        observe_files = bundle(tmp_path,'observation-reference',observation,visual.OBSERVE_INPUT_NAME,visual.OBSERVE_PROFILE)
        candidate(tmp_path,'observe',observation,observe_files,hosts[visual.OBSERVE_PROFILE],
                  directory_name=candidate_names.get('observe'))
    _, review = candidate(tmp_path,'review',review_doc,review_files,hosts[review_profile],
                          directory_name=candidate_names.get('review'))
    revised = dict(context,base_source=base,review=review)
    candidate(tmp_path,'create',revised,{creation.INPUT_NAME:canonical_bytes(revised)},hosts[creation.PROFILE],
              directory_name=candidate_names.get('create'))
    return dict(root=tmp_path,context=context,base=base,review_doc=review_doc,hosts=hosts)


@pytest.fixture
def pilot(tmp_path):
    return make_pilot(tmp_path)


@pytest.fixture
def sequence_pilot(tmp_path):
    return make_pilot(tmp_path, review_profile=visual.REVIEW_SEQUENCE_PROFILE, with_observer=False)


def run(pilot,stage='all',**kwargs):
    return import_phase_one(pilot['root'],owner='offline-operator',project_id='offline-project',
        expected_image_digest=IMAGE_DIGEST,stage=stage,**kwargs)


def mutate(pilot,file_name,change):
    path=pilot['root']/'candidate-animation-review-p1'/'candidate-output'/file_name
    document=json.loads(path.read_bytes());change(document);save(path,document)


def test_import_preserves_real_ids_origin_and_emits_no_submission_events(pilot):
    receipt=run(pilot)
    assert receipt['review_directory']=='review-original-offset'
    assert receipt['review_input_sha256']==digest(pilot['review_doc'])
    assert receipt['live_http_verified'] is False and receipt['new_model_submissions']==0
    store=AnimationRevisionStore(pilot['root']/'revision-ledger.sqlite3')
    operations=store.operations(receipt['lineage_id'])
    assert [row['stage'] for row in operations]==['review','revision']
    assert [row['job_id'] for row in operations]==[str(UUID(int=130)),str(UUID(int=140))]
    assert all(row['execution_path']=='isolated_candidate_import' for row in operations)
    events=store.events(receipt['lineage_id'])
    assert [row['event'] for row in events].count('isolated_candidate_import')==2
    assert not any(row['event'] in ('submission_claimed','submitted','running') for row in events)
    assert operations[0]['result']['observation']['model_understanding_verified'] is False
    assert operations[1]['result']['base_source_sha256']==pilot['base']['source_sha256']
    assert operations[1]['candidate_origin']['stop_signature_verified_locally'] is False
    assert all(row['media_versions'][0]['review_profile'] == visual.REVIEW_PROFILE
               and row['media_versions'][0]['review_profile_revision'] == '1' for row in operations)
    before_events=store.events(receipt['lineage_id'])
    assert run(pilot)==receipt
    assert store.events(receipt['lineage_id'])==before_events
    assert json.loads((pilot['root']/'import-receipt.json').read_bytes())==receipt


def test_review_import_is_a_real_gate_before_preparing_create(pilot):
    receipt=run(pilot,'review')
    assert receipt['review_status']=='completed' and receipt['revision_operation_id'] is None
    store=AnimationRevisionStore(pilot['root']/'revision-ledger.sqlite3')
    gateway=ImportedCandidateOnlyGateway()
    coordinator=AnimationPolishCoordinator(store,gateway,gateway,object())
    with pytest.raises(ServiceError,match='animation_candidate_import_execution_disabled'):
        coordinator.step(receipt['lineage_id'],review_directory=pilot['root']/'review-original-offset',
            creation_input=pilot['context'],base_source=pilot['base'],deadline_at=time.time()+600)
    # The coordinator's reserved revision has the exact input/id used by import.
    receipt=run(pilot)
    assert receipt['revision_status']=='completed'


def test_all_stage_checks_reference_evidence_before_allocating_any_allowance(pilot):
    path=pilot['root']/'candidate-animation-observe-p1'/'candidate-output'/'candidate-evidence.json'
    value=json.loads(path.read_bytes())
    value['model_image_evidence']['evidence_scope']='model_understood'
    save(path,value)
    with pytest.raises(ServiceError):
        run(pilot,'all')
    assert not (pilot['root']/'revision-ledger.sqlite3').exists()


def test_imported_stages_allow_only_one_renderer_and_reopen_does_not_repeat(pilot):
    receipt=run(pilot)
    class Renderer:
        def __init__(self):self.count=0
        def render(self,operation_id,source,context):
            self.count+=1
            return dict(schema='video.animation-render-verification.v1',operation_id=operation_id,
                source_sha256=creation.source_sha256(source),technical_passed=True,
                media_versions=[dict(version_id='offline-render',sha256='a'*64)],
                checks=[dict(code='offline-fixture-only',status='passed')])
    renderer=Renderer();gateway=ImportedCandidateOnlyGateway()
    kwargs=dict(review_directory=pilot['root']/'review-original-offset',creation_input=pilot['context'],
                base_source=pilot['base'],deadline_at=time.time()+600)
    for _ in range(2):
        store=AnimationRevisionStore(pilot['root']/'revision-ledger.sqlite3')
        result=AnimationPolishCoordinator(store,gateway,gateway,renderer).step(receipt['lineage_id'],**kwargs)
        assert result['state']=='completed' and result['aesthetic_status']=='unreviewed'
    assert renderer.count==1


@pytest.mark.parametrize('change',['job','attempt','input','artifact','image_scope','missing_image','running_pod',
    'wrong_image','changed_privilege','wrong_pod_uid','wrong_scenario','wrong_controller_digest','wrong_stop_digest',
    'binding','command','mount','volume','extra_env_field','different_env_api_version'])
def test_changed_identity_input_or_evidence_cannot_allocate_a_ledger(pilot,change):
    if change=='job':
        mutate(pilot,'candidate-evidence.json',lambda d:d.update(job_id=str(UUID(int=500))))
    elif change=='attempt':
        mutate(pilot,'candidate-evidence.json',lambda d:d['model_image_evidence'].update(attempt_id=str(UUID(int=500))))
    elif change=='input':
        mutate(pilot,'candidate-request.json',lambda d:d['envelope']['task'].update(input_sha256='0'*64))
    elif change=='artifact':
        mutate(pilot,visual.REVIEW_RESULT_NAME,lambda d:d.update(summary='Changed artifact'))
    elif change=='image_scope':
        mutate(pilot,'candidate-evidence.json',lambda d:d['model_image_evidence'].update(evidence_scope='model_understood'))
    elif change=='missing_image':
        mutate(pilot,'candidate-evidence.json',lambda d:d['model_image_evidence'].update(images=[]))
    elif change=='running_pod':
        mutate(pilot,'proxy-pod.json',lambda d:d['status']['containerStatuses'][0].update(state={'running':{}}))
    elif change=='wrong_image':
        mutate(pilot,'worker-pod.json',lambda d:d['status']['containerStatuses'][0].update(imageID='wrong@sha256:'+'0'*64))
    elif change=='changed_privilege':
        mutate(pilot,'worker-pod.json',lambda d:d['spec']['containers'][0]['securityContext'].update(privileged=True))
    elif change=='wrong_pod_uid':
        mutate(pilot,'worker-pod.json',lambda d:d['metadata'].update(uid=str(UUID(int=500))))
    elif change=='wrong_scenario':
        mutate(pilot,'scenario.json',lambda d:d['inputs'][0].update(sha256='0'*64))
    elif change=='wrong_controller_digest':
        mutate(pilot,'candidate-evidence.json',lambda d:d.update(candidate_result_sha256='0'*64))
    elif change=='wrong_stop_digest':
        mutate(pilot,'candidate-result.json',lambda d:d['result']['stop_receipt'].update(result_sha256='0'*64))
    elif change=='binding':
        mutate(pilot,'candidate-evidence.json',lambda d:d.update(binding_id='different-binding'))
    elif change=='command':
        mutate(pilot,'worker-pod.json',lambda d:d['spec']['containers'][0].update(command=['different-command']))
    elif change=='mount':
        mutate(pilot,'worker-pod.json',lambda d:d['spec']['containers'][0]['volumeMounts'].append(
            dict(name='extra',mountPath='/extra',readOnly=True)))
    elif change=='extra_env_field':
        mutate(pilot,'worker-pod.json',lambda d:d['spec']['containers'][0]['env'][0].update(value='changed-source'))
    elif change=='different_env_api_version':
        mutate(pilot,'worker-pod.json',lambda d:d['spec']['containers'][0]['env'][0]['valueFrom']['fieldRef'].update(apiVersion='v2'))
    else:
        mutate(pilot,'worker-pod.json',lambda d:d['spec'].update(volumes=[dict(name='extra',hostPath={'path':'/host'})]))
    with pytest.raises((ServiceError,ValueError)):
        run(pilot)
    assert not (pilot['root']/'revision-ledger.sqlite3').exists()


@pytest.mark.parametrize('file_name', ['candidate-request.json', 'candidate-result.json',
    'candidate-evidence.json', 'worker-pod.json', 'proxy-pod.json', 'operator-summary.json',
    'host-config.json', 'scenario.json', 'manifest.json'])
def test_export_top_level_must_be_an_object(pilot,file_name):
    save(pilot['root']/'candidate-animation-review-p1'/'candidate-output'/file_name,[])
    with pytest.raises(ServiceError):
        run(pilot)
    assert not (pilot['root']/'revision-ledger.sqlite3').exists()


@pytest.mark.parametrize('file_name,change', [
    ('worker-pod.json', lambda d:d['spec'].update(containers=[None])),
    ('proxy-pod.json', lambda d:d['status'].update(containerStatuses=[None])),
    ('worker-pod.json', lambda d:d['spec']['containers'][0].update(volumeMounts=[None])),
    ('scenario.json', lambda d:d.update(inputs=[None])),
    ('manifest.json', lambda d:d.update(items=[None])),
    ('manifest.json', lambda d:d['items'][0].update(metadata=None)),
])
def test_malformed_nested_export_is_rejected_before_ledger(pilot,file_name,change):
    mutate(pilot,file_name,change)
    with pytest.raises(ServiceError):
        run(pilot)
    assert not (pilot['root']/'revision-ledger.sqlite3').exists()


def test_changed_export_after_import_conflicts_instead_of_replacing_provenance(pilot):
    receipt=run(pilot)
    mutate(pilot,'candidate-evidence.json',lambda d:d.update(note='different operator export'))
    with pytest.raises(ServiceError,match='animation_candidate_import_conflict'):
        run(pilot)
    assert json.loads((pilot['root']/'import-receipt.json').read_bytes())==receipt


def test_failed_stopped_candidate_consumes_review_allowance_without_becoming_completed(pilot):
    root=pilot['root'];old=root/'candidate-animation-review-p1'
    # Retain the original fixture separately; the alternate fixture is still a
    # synthetic export and is never a request to execute a replacement attempt.
    old.rename(root/'synthetic-unused-review')
    files={visual.REVIEW_INPUT_NAME:canonical_bytes(pilot['review_doc'])}
    files.update({f['file_name']:PNG for f in pilot['review_doc']['observation']['frames']})
    candidate(root,'review',pilot['review_doc'],files,pilot['hosts'][visual.REVIEW_PROFILE],failed=True)
    receipt=run(pilot,'review')
    assert receipt['review_status']=='failed' and receipt['revision_operation_id'] is None
    store=AnimationRevisionStore(root/'revision-ledger.sqlite3')
    with pytest.raises(ServiceError,match='animation_revision_allowance_consumed'):
        store.reserve(receipt['lineage_id'],'review','different-operation','different-key',digest(pilot['review_doc']))


def test_sequence_review_import_then_revision_without_observe_preserves_bound_identity_and_replay(sequence_pilot):
    pilot = sequence_pilot
    review = run(pilot, 'review')
    assert review['observation_status'] == 'not_imported'
    assert review['review_status'] == 'completed' and review['revision_operation_id'] is None
    store = AnimationRevisionStore(pilot['root'] / 'revision-ledger.sqlite3')
    operation = store.operations(review['lineage_id'])[0]
    assert operation['candidate_origin']['profile'] == visual.REVIEW_SEQUENCE_PROFILE
    assert operation['media_versions'][0]['review_profile'] == visual.REVIEW_SEQUENCE_PROFILE
    assert operation['media_versions'][0]['review_profile_revision'] == '1'
    assert operation['candidate_origin']['execution_path'] == 'isolated_candidate_import'
    assert operation['candidate_origin']['live_http_verified'] is False
    assert operation['result']['review']['schema'] == visual.REVIEW_RESULT_SCHEMA
    revision = run(pilot, 'revision')
    assert revision['lineage_id'] == review['lineage_id']
    assert revision['revision_status'] == 'completed'
    assert revision['observation_status'] == 'not_imported'
    operations = store.operations(review['lineage_id'])
    assert [row['stage'] for row in operations] == ['review', 'revision']
    assert operations[1]['review_result_sha256'] == operations[0]['result_sha256']
    assert operations[1]['result']['review_sha256'] == digest(operations[0]['result']['review'])
    events = store.events(review['lineage_id'])
    assert not any(event['event'] in {'submission_claimed', 'submitted', 'running'} for event in events)
    audits = sorted(path.name for path in (pilot['root'] / 'candidate-import-audit').iterdir())
    assert run(pilot, 'revision') == revision
    assert store.operations(review['lineage_id']) == operations
    assert store.events(review['lineage_id']) == events
    assert sorted(path.name for path in (pilot['root'] / 'candidate-import-audit').iterdir()) == audits


def test_sequence_candidate_needs_explicit_profile_and_cannot_silently_use_the_v1_default(sequence_pilot):
    root = sequence_pilot['root']
    profile, document, files = load_bundle(root / 'review-original-offset')
    assert profile == visual.REVIEW_SEQUENCE_PROFILE
    directory = root / 'candidate-animation-review-p1'
    verified = validate_candidate(directory, role='review', document=document, files=files,
        expected_image_digest=IMAGE_DIGEST, expected_profile=profile)
    assert verified.envelope.profile == verified.provenance['profile'] == profile
    with pytest.raises(ServiceError, match='animation_candidate_input_binding_mismatch'):
        validate_candidate(directory, role='review', document=document, files=files,
                           expected_image_digest=IMAGE_DIGEST)
    assert not (root / 'revision-ledger.sqlite3').exists()


def test_relabelling_a_v1_preparation_as_v2_does_not_relabel_execution_evidence(pilot):
    path = pilot['root'] / 'review-original-offset' / 'evidence.json'
    receipt = json.loads(path.read_bytes())
    receipt['profile'] = visual.REVIEW_SEQUENCE_PROFILE
    save(path, receipt)
    with pytest.raises(ServiceError, match='animation_candidate_input_binding_mismatch'):
        run(pilot, 'review')
    assert not (pilot['root'] / 'revision-ledger.sqlite3').exists()


def reserve_legacy_review(pilot):
    store = AnimationRevisionStore(pilot['root'] / 'revision-ledger.sqlite3')
    base, context = pilot['base'], pilot['context']
    lineage = store.create_lineage('offline-operator', 'offline-project', base['source_revision'],
        base['source_sha256'], 'candidate-import:' + digest(dict(source=base['source_sha256'], context=context)))
    versions = [dict(version_id=base['source_revision'], sha256=pilot['review_doc']['media']['sha256'],
        source_sha256=base['source_sha256'], creation_input_sha256=digest(context))]
    operation_id = lineage['id'] + ':review'
    operation = store.reserve(lineage['id'], 'review', operation_id, operation_id,
                              digest(pilot['review_doc']), versions)
    return store, lineage, operation


def test_import_can_resume_a_legacy_v1_reservation_without_backfilling_historical_identity(pilot):
    store, lineage, legacy = reserve_legacy_review(pilot)
    receipt = run(pilot, 'review')
    assert receipt['lineage_id'] == lineage['id']
    operation = store.operation(legacy['id'])
    assert operation['status'] == 'completed'
    assert operation['media_versions'] == legacy['media_versions']
    assert 'review_profile' not in operation['media_versions'][0]
    frozen = store.operations(lineage['id']), store.events(lineage['id'])
    assert run(pilot, 'review') == receipt
    assert (store.operations(lineage['id']), store.events(lineage['id'])) == frozen
    # A new downstream stage carries identity; the old review remains unchanged.
    run(pilot, 'revision')
    revision = store.operations(lineage['id'])[-1]
    assert revision['stage'] == 'revision'
    assert revision['media_versions'][0]['review_profile'] == visual.REVIEW_PROFILE
    assert store.operation(legacy['id']) == operation


def test_sequence_import_cannot_take_over_a_legacy_unlabelled_review(sequence_pilot):
    store, lineage, _ = reserve_legacy_review(sequence_pilot)
    before = store.operations(lineage['id']), store.events(lineage['id'])
    with pytest.raises(ServiceError, match='animation_revision_idempotency_conflict'):
        run(sequence_pilot, 'review')
    assert (store.operations(lineage['id']), store.events(lineage['id'])) == before
    assert not (sequence_pilot['root'] / 'import-receipt.json').exists()


@pytest.mark.parametrize('file_name', ['candidate-evidence.json', 'operator-summary.json', 'scenario.json'])
def test_sequence_export_cannot_substitute_a_v1_profile_at_any_bound_layer(sequence_pilot, file_name):
    mutate(sequence_pilot, file_name, lambda document: document.update(profile=visual.REVIEW_PROFILE))
    with pytest.raises(ServiceError):
        run(sequence_pilot, 'review')
    assert not (sequence_pilot['root'] / 'revision-ledger.sqlite3').exists()


@pytest.mark.parametrize('role, profile', [
    ('observe', visual.REVIEW_SEQUENCE_PROFILE),
    ('create', visual.REVIEW_SEQUENCE_PROFILE),
    ('review', creation.PROFILE),
    ('review', 'video.animation-review.v3'),
    ('review', {}),
])
def test_expected_profile_cannot_expand_other_roles_or_admit_unknown_profiles(tmp_path, role, profile):
    with pytest.raises(ServiceError, match='animation_candidate_profile_not_allowed'):
        validate_candidate(tmp_path / 'absent', role=role, document={}, files={},
            expected_image_digest=IMAGE_DIGEST, expected_profile=profile)


def test_all_still_requires_observation_even_for_explicit_sequence_review(sequence_pilot):
    with pytest.raises(ServiceError):
        run(sequence_pilot, 'all')
    assert not (sequence_pilot['root'] / 'revision-ledger.sqlite3').exists()


def test_explicit_unique_run_directories_bind_p2_pods_and_replay_without_new_allowance(tmp_path):
    names = dict(review='candidate-animation-review-p2', create='candidate-animation-create-p2')
    pilot = make_pilot(tmp_path, review_profile=visual.REVIEW_SEQUENCE_PROFILE,
                       with_observer=False, candidate_names=names)
    assert not (tmp_path / 'candidate-animation-review-p1').exists()
    review = run(pilot, 'review', candidate_directories=names)
    assert review['review_status'] == 'completed' and review['revision_operation_id'] is None
    assert review['candidate_directories'] == dict(names, observe='candidate-animation-observe-p1')
    assert review['live_http_verified'] is False
    for role in ('review', 'create'):
        exported = json.loads((tmp_path / names[role] / 'candidate-output' / 'worker-pod.json').read_bytes())
        assert exported['metadata']['name'] == names[role] + '-worker'
    revision = run(pilot, 'revision', candidate_directories=names)
    assert revision['lineage_id'] == review['lineage_id'] and revision['revision_status'] == 'completed'
    store = AnimationRevisionStore(tmp_path / 'revision-ledger.sqlite3')
    frozen = store.operations(review['lineage_id']), store.events(review['lineage_id'])
    assert len(frozen[0]) == 2
    assert run(pilot, 'revision', candidate_directories=names) == revision
    assert (store.operations(review['lineage_id']), store.events(review['lineage_id'])) == frozen
    assert revision['candidate_directories'] == review['candidate_directories']


def test_omitted_candidate_mapping_retains_original_paths_and_receipt_shape(pilot):
    receipt = run(pilot, 'review')
    assert receipt['review_status'] == 'completed'
    assert 'candidate_directories' not in receipt
    assert run(pilot, 'review') == receipt


def test_copying_old_exports_to_a_new_run_name_does_not_relabel_pod_evidence(pilot):
    root = pilot['root']
    original = root / 'candidate-animation-review-p1'
    copied = root / 'candidate-animation-review-p2'
    original_manifest = (original / 'candidate-output' / 'manifest.json').read_bytes()
    # Synthetic fixtures only: copied bytes still name the original p1 Pods.
    shutil.copytree(original, copied)
    with pytest.raises(ServiceError, match='animation_candidate_pod_identity_mismatch'):
        run(pilot, 'review', candidate_directories={'review': copied.name})
    assert (original / 'candidate-output' / 'manifest.json').read_bytes() == original_manifest
    assert not (root / 'revision-ledger.sqlite3').exists()


@pytest.mark.parametrize('mapping', [
    {'unknown': 'candidate-animation-review-p2'},
    {'review': '../candidate-animation-review-p2'},
    {'review': '/candidate-animation-review-p2'},
    {'review': 'C:\\candidate-animation-review-p2'},
    {'review': 'nested/candidate-animation-review-p2'},
    {'review': 'nested\\candidate-animation-review-p2'},
    {'review': 'candidate-animation-create-p2'},
    {'review': 'candidate-animation-review-p2', 'create': 'candidate-animation-review-p2'},
    {'review': 'candidate-animation-review-p2.'},
    {'review': 'candidate-animation-review-p2 '},
    {'review': 'candidate-animation-review-P2'},
    {'review': ''},
    {'review': None},
    {'review': 2},
    [],
])
def test_candidate_directory_mapping_rejects_escape_unknown_roles_and_aliases_before_writes(tmp_path, mapping):
    with pytest.raises(ServiceError, match='animation_candidate_directory_'):
        import_phase_one(tmp_path, owner='operator', project_id='project',
            expected_image_digest=IMAGE_DIGEST, candidate_directories=mapping)
    assert list(tmp_path.iterdir()) == []


def test_candidate_directory_symlink_cannot_alias_another_run(tmp_path):
    actual = tmp_path / 'actual'
    actual.mkdir()
    alias = tmp_path / 'candidate-animation-review-p2'
    try:
        alias.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip('Host does not permit creating directory symlinks')
    with pytest.raises(ServiceError, match='animation_candidate_directory_alias'):
        import_phase_one(tmp_path, owner='operator', project_id='project',
            expected_image_digest=IMAGE_DIGEST, candidate_directories={'review': alias.name})
    assert not (tmp_path / 'revision-ledger.sqlite3').exists()
