"""Automatic resource-planner sidecars, with synthetic stopped Agent peers."""
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import implementation
from agent_service.profiles.video_joint_plan_contract import validate_joint_plan_result
from agent_service.profiles.video_resource_plan import PROFILE, verified_bundle
from agent_service.profiles.video_resource_plan_contract import INPUT_SCHEMA, RESULT_SCHEMA, validate_input, validate_result
from video_workbench.joint_plan_store import JointPlanStore
from video_workbench.joint_plan_workflow import JointPlanWorkflow
from video_workbench.resource_planning_coordinator import ResourcePlanningCoordinator
from video_workbench.resource_planning_gateway import ResourcePlanningAgentGateway
from video_workbench.store import Store


OWNER = 'owner'


def producers():
    return [dict(id='manim', kind='animation', revision='manim-cairo-1', admitted=True,
        capabilities=['manim', 'transparent-png-sequence', 'local-frame-clock'],
        description='Author a short original Cairo demonstration without TeX.',
        limits=dict(max_frames=60, max_width=640, max_height=360, renderer='cairo', latex_available=False))]


def original_input():
    return dict(schema_version='video-joint-plan-input.v1', project=dict(title='Explain a process',
        script='A request waits in a queue before execution.', style='Clear diagrams', voice='zm_yunjian'),
        scenes=[], brief=dict(audience='', goal='', tone='conversational'), instruction='', preserve_script=False,
        method_delivery=None, allowed_engines=['html-svg', 'canvas', 'three'])


def joint_result(document):
    value = dict(schema_version='video-joint-plan-result.v1', input_sha256=digest(document),
        summary='Follow the request.', understanding_goal='Understand the waiting process.',
        visual_style='One persistent blue request object.', continuity='Keep the same request throughout.',
        scenes=[dict(segment_id='queue', source_scene_ids=[], title='Wait then execute',
            narration=document['project']['script'], visual_description='Follow one request through a queue.',
            duration_seconds=8, pronunciation={}, key_points=['Waiting precedes execution'],
            expression='Track one object through changing relationships.', visual_steps=['Arrive', 'Wait', 'Execute'],
            delivery_note='', source_quotes=[document['project']['script']])], method_decision=None, warnings=[])
    return validate_joint_plan_result(value, digest(document), document)


def planning_input(catalog=None):
    document = original_input()
    result = joint_result(document)
    catalog = producers() if catalog is None else catalog
    return dict(schema_version=INPUT_SCHEMA, joint_input=document, joint_input_sha256=digest(document),
        joint_result=result, joint_result_sha256=digest(result), producer_catalog=catalog,
        producer_catalog_sha256=digest(catalog))


def planning_result(document, *, with_node=True):
    nodes = [dict(id='queue_demo', kind='animation', producer='manim',
        inputs=dict(resource_authoring=dict(kind='manim', width=640, height=360, frame_count=24,
            global_start_frame=0, seed=17), guidance='Show a continuous geometric wait/execute transformation.'),
        dependencies=[], objective='Make the change in waiting state visibly understandable.',
        required_capabilities=['manim'], key_effects=['persistent-subject'], local_timing=dict(fps=24, frame_count=24),
        placement=dict(segment_id='queue', start_frame=12))] if with_node else []
    return dict(schema_version=RESULT_SCHEMA, input_sha256=digest(document),
        resource_plan=dict(nodes=nodes, budget=dict(max_attempts=1, max_attempts_per_node=1)),
        reason='The small geometric transformation supports the original visual objective.' if with_node
            else 'Runtime drawing suffices for this expression.', warnings=[])


class JointGateway:
    def __init__(self):
        self.submissions = 0
    def upload(self, document):
        return dict(input_id=str(uuid4()), sha256=digest(document), path='joint-plan-input.json', role='joint_plan_input')
    def submit(self, operation_id, document, deadline_at, input_ref=None):
        self.submissions += 1
        self.document = deepcopy(document)
        return dict(job_id=str(uuid4()), state='running')
    def poll(self, job_id):
        return dict(job_id=job_id, state='completed')
    def collect(self, job_id, checksum, document):
        return joint_result(document)


class ResourceGateway:
    def __init__(self):
        self.submissions, self.jobs, self.lookup_calls, self.cancel_calls = 0, {}, 0, 0
        self.lose_ack, self.running, self.with_node = False, False, True
        self.not_found, self.proof = False, True
    def upload(self, document):
        return dict(input_id=str(uuid4()), sha256=digest(document), path='resource-plan-input.json', role='resource_plan_input')
    def submit(self, operation_id, document, deadline_at, input_ref=None):
        self.submissions += 1
        self.document = deepcopy(document)
        self.operation_id = operation_id
        self.job_id = str(uuid4())
        self.jobs[operation_id] = self.view()
        if self.lose_ack:
            raise ServiceError('service_timeout', 504)
        return self.jobs[operation_id]
    def view(self, *, cancelled=False):
        attempt_id = getattr(self, 'attempt_id', None) or str(uuid4())
        self.attempt_id = attempt_id
        return dict(job_id=self.job_id, state='cancelled' if cancelled else 'running' if self.running else 'completed',
            pins=dict(profile=PROFILE, profile_revision='1'), attempt=dict(attempt_id=attempt_id, fence=1),
            stop_proven=self.proof and (not self.running or cancelled),
            stop_proof=dict(stop_proven=self.proof, attempt_id=attempt_id, fence=1,
                request_sha256='a' * 64, result_sha256='b' * 64, resource_ids=['synthetic-planner'], observed_at=1000))
    def lookup(self, operation_id):
        self.lookup_calls += 1
        if self.not_found:
            raise ServiceError('job_not_found', 404)
        return self.jobs[operation_id]
    def poll(self, job_id):
        return self.jobs.get(self.operation_id, self.view())
    def collect(self, job_id, checksum, document):
        return planning_result(document, with_node=self.with_node)
    def cancel(self, job_id):
        self.cancel_calls += 1
        self.jobs[self.operation_id] = self.view(cancelled=True)
        return self.jobs[self.operation_id]


@pytest.fixture
def environment(tmp_path):
    business = Store(tmp_path / 'business.sqlite3')
    project = business.create_project(OWNER, dict(title='Requests', script='A request waits in a queue before execution.', style='Clear'))
    store = JointPlanStore(business)
    gateway, planner = JointGateway(), ResourceGateway()
    coordinator = ResourcePlanningCoordinator(store, planner, producer_catalog=lambda owner: producers())
    workflow = JointPlanWorkflow(store, gateway, enabled=True, resource_planner=coordinator)
    op = store.start(OWNER, project['id'], dict(expected_revision=project['revision'],
        brief=dict(audience='', goal='', tone='conversational')), 'joint-planning')
    return business, project, store, gateway, planner, coordinator, workflow, op


def test_profile_is_separate_source_only_and_own_bundle_is_verified():
    profile = implementation(PROFILE)
    assert profile.required_tools == ('read_inputs', 'write')
    assert set(verified_bundle()) == {'CONTRACT.md', 'LICENSE', 'resource-plan-procedure/SKILL.md'}
    assert profile.name != 'video.joint-plan.v1'


def test_resource_plan_is_not_adoptable_until_real_planner_result(environment):
    business, project, store, joint, planner, _, workflow, op = environment
    workflow.tick()
    pending = store.project(OWNER, project['id'])['versions'][0]
    assert pending['resource_planning']['status'] == 'pending' and not pending['adoptable']
    original = deepcopy(pending['result'])
    with pytest.raises(ServiceError, match='resources_not_ready'):
        store.adopt(OWNER, project['id'], dict(expected_revision=business.project(OWNER, project['id'])['revision'],
            version_id=pending['id'], expected_head_version_id=None))
    workflow.tick()
    complete = store.project(OWNER, project['id'])['versions'][0]
    assert complete['adoptable'] and complete['result'] == original
    assert complete['resource_plan']['nodes'][0]['producer'] == 'manim'
    annotation = complete['resource_planning']
    assert annotation['raw_result']['resource_plan'] == complete['resource_plan']
    assert annotation['joint_result_sha256'] == digest(original)
    assert annotation['producer_catalog_sha256'] == digest(producers())
    assert annotation['result_sha256'] == digest(annotation['raw_result'])
    assert joint.submissions == planner.submissions == 1


def test_adoption_projects_segment_ids_without_mutating_raw_agent_results(environment):
    business, project, store, _, _, _, workflow, _ = environment
    workflow.tick()
    workflow.tick()
    candidate = store.project(OWNER, project['id'])['versions'][0]
    adopted = store.adopt(OWNER, project['id'], dict(expected_revision=business.project(OWNER, project['id'])['revision'],
        version_id=candidate['id'], expected_head_version_id=None))
    mapped = adopted['joint_plan']['resource_plan']
    assert mapped['nodes'][0]['placement'] == dict(scene_id=adopted['scenes'][0]['id'], start_frame=12)
    assert candidate['resource_plan']['nodes'][0]['placement']['segment_id'] == 'queue'
    assert adopted['joint_plan']['resource_plan_source']['requirements_sha256'] == digest(mapped)
    assert store.version(OWNER, candidate['id'])['result'] == candidate['result']


def test_empty_plan_is_a_deliberate_agent_choice(environment):
    _, project, store, _, planner, _, workflow, _ = environment
    planner.with_node = False
    workflow.tick()
    workflow.tick()
    candidate = store.project(OWNER, project['id'])['versions'][0]
    assert candidate['adoptable'] and candidate['resource_plan']['nodes'] == []
    assert planner.submissions == 1


def test_unknown_submission_recovers_the_original_child_without_second_charge(environment):
    _, project, store, joint, planner, coordinator, workflow, op = environment
    planner.lose_ack = True
    workflow.tick()
    workflow.tick()
    unknown = store.operation(op['id'])['resource_planning']
    assert unknown['submit_attempted'] and unknown['status'] == 'submission_unknown'
    restarted = JointPlanWorkflow(store, joint, enabled=True, resource_planner=ResourcePlanningCoordinator(
        store, planner, producer_catalog=lambda owner: (_ for _ in ()).throw(AssertionError('must use frozen catalogue'))))
    restarted.tick()
    assert store.project(OWNER, project['id'])['versions'][0]['adoptable']
    assert planner.submissions == planner.lookup_calls == 1


def test_unknown_not_found_does_not_fake_stop_or_resubmit(environment):
    _, _, store, _, planner, _, workflow, op = environment
    planner.lose_ack = planner.not_found = True
    workflow.tick()
    workflow.tick()
    store.cancel(OWNER, op['id'])
    for _ in range(3):
        workflow.tick()
    result = store.operation(op['id'])
    assert result['status'] == 'cancelling' and not result['resource_planning']['stop_proven']
    assert planner.submissions == 1


def test_cancel_before_resource_claim_never_submits_another_agent(environment):
    _, project, store, _, planner, _, workflow, op = environment
    workflow.tick()
    store.cancel(OWNER, op['id'])
    workflow.tick()
    candidate = store.project(OWNER, project['id'])['versions'][0]
    assert planner.submissions == 0 and candidate['resource_planning']['status'] == 'cancelled'
    assert not candidate['adoptable']


def test_claimed_resource_cancel_waits_for_real_stopped_child(environment):
    _, project, store, _, planner, _, workflow, op = environment
    planner.running = True
    workflow.tick()
    workflow.tick()
    store.cancel(OWNER, op['id'])
    workflow.tick()
    workflow.tick()
    result = store.operation(op['id'])
    assert result['status'] == 'cancelled' and result['resource_planning']['stop_proven']
    assert planner.submissions == planner.cancel_calls == 1
    assert not store.project(OWNER, project['id'])['versions'][0]['adoptable']


def test_terminal_without_stop_evidence_cannot_become_adoptable(environment):
    _, project, store, _, planner, _, workflow, op = environment
    planner.proof = False
    workflow.tick()
    workflow.tick()
    assert store.operation(op['id'])['resource_planning']['error'] == 'resource_planning_stop_unproven'
    assert not store.project(OWNER, project['id'])['versions'][0]['adoptable']


@pytest.mark.parametrize('mutation,code', [
    (lambda doc, result: result['resource_plan']['nodes'][0].update(producer='invented'), 'unoffered_producer'),
    (lambda doc, result: result['resource_plan']['nodes'][0]['inputs']['resource_authoring'].update(global_start_frame=24), 'local_clock_required'),
    (lambda doc, result: result['resource_plan']['nodes'][0]['inputs']['resource_authoring'].update(frame_count=61), 'resource_limit'),
    (lambda doc, result: result['resource_plan']['nodes'][0]['placement'].update(segment_id='invented'), 'placement_invalid'),
    (lambda doc, result: result['resource_plan']['nodes'][0].update(dependencies=['queue_demo']), 'dependency_cycle'),
])
def test_resource_planner_cannot_invent_execution_capabilities_or_scene_scope(mutation, code):
    document = planning_input()
    result = planning_result(document)
    mutation(document, result)
    with pytest.raises(ServiceError, match=code):
        validate_result(result, digest(document), document)


def test_empty_actual_catalogue_never_admits_a_manim_request():
    document = planning_input([])
    with pytest.raises(ServiceError, match='unoffered_producer'):
        validate_result(planning_result(document), digest(document), document)
    assert validate_result(planning_result(document, with_node=False), digest(document), document)


def test_original_input_result_and_producer_directory_are_hash_bound():
    original = planning_input()
    for field in ('joint_input_sha256', 'joint_result_sha256', 'producer_catalog_sha256'):
        changed = deepcopy(original)
        changed[field] = 'f' * 64
        with pytest.raises(ServiceError):
            validate_input(changed)


def test_gateway_uses_separate_profile_and_existing_stopped_transport():
    from test_joint_plan_gateway import Peer, JOB, TOKEN
    from agent_service.client import AgentClient
    import httpx
    class ResourcePeer(Peer):
        def __init__(self):
            super().__init__()
            self.profiles[0]['profile'] = PROFILE
        def __call__(self, req):
            if req.url.path == '/v1/inputs':
                assert req.url.params['name'] == 'resource-plan-input.json'
                from test_joint_plan_gateway import INPUT
                return httpx.Response(201, json=dict(input_id=INPUT, name='resource-plan-input.json',
                    size=len(req.content), sha256=__import__('hashlib').sha256(req.content).hexdigest()))
            return super().__call__(req)
    peer = ResourcePeer()
    client = AgentClient('http://agent', TOKEN, transport=httpx.MockTransport(peer))
    gateway = ResourcePlanningAgentGateway(client)
    document = planning_input()
    uploaded = gateway.upload(document)
    import time
    job = gateway.submit('planner-one', document, time.time() + 120, input_ref=uploaded)
    assert job['job_id'] == JOB
    assert peer.request['profile'] == PROFILE and peer.request['business_ref'] == 'video-resource-plan:planner-one'
    assert peer.request['inputs'][0]['role'] == 'resource_plan_input'
    assert gateway.lookup('planner-one')['job_id'] == JOB
    client.close()


def test_source_only_profile_collects_only_stopped_exact_canonical_input(tmp_path):
    from types import SimpleNamespace
    from agent_service.contracts import InlineFile, canonical_bytes
    from agent_service.engines import StopEvidence
    from agent_service.profiles.video_resource_plan import INPUT_NAME, RESULT_NAME
    document = planning_input()
    raw = canonical_bytes(document)
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'inputs' / INPUT_NAME).write_bytes(raw)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(planning_result(document)))
    attempt_id = str(uuid4())
    envelope = SimpleNamespace(profile=PROFILE, profile_revision='1', attempt_id=attempt_id,
        task=dict(input_sha256=digest(document)), inputs=[InlineFile.from_bytes(INPUT_NAME, raw)],
        checkpoint_inputs=[], checkpoint_ref=None)
    handler = implementation(PROFILE)
    proof = StopEvidence(execution_id=attempt_id, all_writers_stopped=True, resource_ids=('synthetic-planner',))
    result = handler.collect_output(tmp_path, envelope, proof)
    assert len(result) == 1 and result[0].name == RESULT_NAME
    assert json.loads(result[0].content()) == planning_result(document)
    bad = StopEvidence(execution_id=attempt_id, all_writers_stopped=False, resource_ids=('synthetic-planner',))
    with pytest.raises(ServiceError, match='writers_not_stopped'):
        handler.collect_output(tmp_path, envelope, bad)
    (tmp_path / 'inputs' / INPUT_NAME).write_text(json.dumps(document, indent=2), encoding='utf-8')
    with pytest.raises(ServiceError, match='input_binding_mismatch'):
        handler.validate_inputs(tmp_path, envelope.task)


def test_gateway_collects_new_profile_artifact_and_rejects_old_profile_custody():
    from types import SimpleNamespace
    from agent_service.artifacts import frozen_manifest
    from agent_service.contracts import canonical_bytes
    from agent_service.profiles.video_resource_plan import RESULT_NAME
    from test_joint_plan_gateway import JOB, ATTEMPT, ARTIFACT
    import hashlib
    document = planning_input()
    result = planning_result(document)
    raw = canonical_bytes(result)
    manifest = frozen_manifest(JOB, ATTEMPT, [dict(artifact_id=ARTIFACT, name=RESULT_NAME,
        size=len(raw), sha256=hashlib.sha256(raw).hexdigest())])
    job = dict(job_id=JOB, state='completed', publication_authorized=False,
        pins=dict(profile=PROFILE, profile_revision='1'), attempt=dict(attempt_id=ATTEMPT, fence=1),
        stop_proven=True, stop_proof=dict(stop_proven=True, attempt_id=ATTEMPT, fence=1,
            request_sha256='a' * 64, result_sha256='b' * 64, resource_ids=['synthetic-planner'], observed_at=1000),
        artifact_manifest_sha256=manifest['sha256'])
    client = SimpleNamespace(get_job=lambda identity: job, artifact_manifest=lambda identity: manifest,
        download_artifact=lambda identity: raw)
    gateway = ResourcePlanningAgentGateway(client)
    assert gateway.collect(JOB, digest(document), document) == result
    job['pins']['profile'] = 'video.joint-plan.v1'
    with pytest.raises(ServiceError):
        gateway.collect(JOB, digest(document), document)
