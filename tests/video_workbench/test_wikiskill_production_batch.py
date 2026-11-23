"""The real local run/module stores inherit their planning batch release."""
from copy import deepcopy
import hashlib
import json
from uuid import UUID, uuid4

import httpx
import pytest

from agent_service.artifacts import frozen_manifest
from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles.video_joint_plan_contract import planning_method_task
from agent_service.skill_tool_evidence import SkillToolObserver
from video_workbench.animation_wikiskill_production import LibrarySkillProductionAdapter
from video_workbench.joint_plan_gateway import JointPlanAgentGateway
from video_workbench.joint_plan_store import JointPlanStore
from video_workbench.joint_plan_workflow import JointPlanWorkflow
from video_workbench.production_resource_stage import ManimPlanProducer, production_plan
from video_workbench.store import Store
from test_animation_wikiskill_production import adapter, release
from test_joint_plan_api import proposal, Gateway
from test_production_film_runs import env, begin, audio_phase, finish_module, OWNER, children
from test_production_runs import seed
from test_skill_production_gateway import Peer, JOB, ATTEMPT, TOKEN


def attach_library(env, *, use_wikiskill=True):
    original = release()
    state, calls = dict(active=original, recent={original['id']: original}), []
    service = adapter(env.path / 'library', state, calls)
    binding = service.pin(OWNER, 'creative-root', use_wikiskill=use_wikiskill)
    env.joint.skill_adapter = service
    env.adapter = service
    with env.store.transaction() as db:
        project = env.store._project(db, OWNER, env.project['id'])
        project['joint_plan']['library_binding'] = binding
        env.store._save(db, project)
    return service, state, calls, binding


@pytest.mark.parametrize('use_wikiskill', [True, False])
def test_run_and_modules_keep_adopted_plan_release_when_active_changes_and_on_restart(env, use_wikiskill):
    service, state, calls, binding = attach_library(env, use_wikiskill=use_wikiskill)
    expected_publications = {binding['release_sha256']} if use_wikiskill else set()
    # Publishing between planning and clicking 'produce' does not change the plan.
    state['active'] = release(2)
    project = env.store.project(OWNER, env.project['id'])
    original_request = dict(expected_revision=project['revision'], joint_plan_version_id='adopted-plan-1')
    run = begin(env)
    assert run['library_binding'] == binding
    audio_phase(env)
    first = children(env, 'film_module_operations')[0]
    pending = env.creation.operation('module', first['id'])
    assert pending['library_binding'] == binding
    core = pending['document']['core_task']
    delivery = service.freeze(OWNER, 'video-film-module:' + first['id'], core,
        core['constraints']['allowed_engines'], library_binding=binding)
    assert {row['publication_sha256'] for row in delivery['catalog']['skills']} == expected_publications
    # The normal stopped-output fixture does not refresh or choose packages.
    finish_module(env, first['id'])
    env.runs.tick()
    second = children(env, 'film_module_operations')[1]
    reopened = adapter(env.path / 'library', state, calls)
    prepared_second = env.creation.prepare_module(second['id'], reopened)
    assert prepared_second['library_binding'] == binding
    assert {row['publication_sha256'] for row in prepared_second['document']['skill_catalog']['skills']} == expected_publications
    # A refinement follows the original task's release even without a supplied
    # public binding. It cannot silently refresh to today's active version.
    base = env.creation.get_module(OWNER, first['id'])
    revision = deepcopy(pending['request'])
    revision.pop('library_binding', None)
    revision.update(expected_revision=env.store.project(OWNER, project['id'])['revision'],
        base_module_version_id=base['version_id'], instruction='Improve the object motion without changing its identity.')
    polish = env.creation.start_module(OWNER, project['id'], pending['scene_id'], revision, 'polish-original-batch')
    prepared_polish = env.creation.prepare_module(polish['id'], reopened)
    assert prepared_polish['library_binding'] == binding
    assert {row['publication_sha256'] for row in prepared_polish['document']['skill_catalog']['skills']} == expected_publications
    expected_calls = [('active', OWNER)] if use_wikiskill else []
    assert calls == expected_calls
    # An old exact request key returns the saved task, even after a new release.
    replay = env.runs.start(OWNER, project['id'], original_request, 'native-film-run-test')
    assert replay['id'] == run['id'] and calls == expected_calls


def test_planning_start_pins_before_execution_and_adoption_carries_the_same_release(tmp_path):
    original = release()
    state, calls = dict(active=original, recent={original['id']: original}), []
    service = adapter(tmp_path / 'library', state, calls)
    store = Store(tmp_path / 'business.db')
    joint = JointPlanStore(store, skill_adapter=service)
    project = seed(store, joint)
    request = dict(expected_revision=project['revision'], brief=dict(audience='', goal='', tone='precise'))
    public = joint.start(OWNER, project['id'], request, 'creative-root-request')
    state['active'] = release(2)
    assert joint.start(OWNER, project['id'], request, 'creative-root-request') == public
    prepared = joint.prepare(public['id'], service)
    document = prepared['document']
    assert document['method_delivery'] is None and len(document['skill_catalog']['skills']) == 6
    delivery = service.get_bundle(OWNER, 'video-joint-plan:' + public['id'], document['skill_catalog']['sha256'])
    observer = SkillToolObserver(delivery['catalog'], delivery['packages'], input_sha256=digest(document))
    evidence = observer.finish(str(uuid4()))
    output = proposal(document, digest(document))
    output.update(skill_decisions=[], skill_tool_evidence_sha256=evidence['sha256'])
    completed = joint.complete(public['id'], dict(result=output, evidence=evidence, skill_delivery=delivery))
    current = store.project(OWNER, project['id'])
    adopted = joint.adopt(OWNER, project['id'], dict(expected_revision=current['revision'],
        version_id=completed['version_id'], expected_head_version_id='adopted-plan-1'))
    assert adopted['joint_plan']['library_binding'] == public['library_binding']
    assert calls == [('active', OWNER)]
    next_request = dict(request, expected_revision=adopted['revision'])
    newer = joint.start(OWNER, project['id'], next_request, 'new-full-creative-root')
    assert newer['library_binding']['release_id'] == state['active']['id']


def test_starting_planner_accepts_cancel_without_resubmitting_the_job(tmp_path):
    store = Store(tmp_path / 'business.db')
    joint = JointPlanStore(store)
    project = seed(store, joint)
    operation = joint.start(OWNER, project['id'], dict(expected_revision=project['revision'],
        brief=dict(audience='', goal='', tone='precise')), 'starting-planner-request')
    gateway, cancelled = Gateway(), []
    gateway.poll = lambda job_id: dict(job_id=job_id, state='starting')
    gateway.cancel = lambda job_id: cancelled.append(job_id)
    workflow = JointPlanWorkflow(joint, gateway, enabled=True)
    workflow.tick()
    joint.cancel(OWNER, operation['id'])
    workflow.tick()
    assert cancelled == [joint.operation(operation['id'])['job_id']]
    assert gateway.submits == 1


def test_old_plan_requires_explicit_new_planning_before_audio_when_library_is_enabled(env):
    env.joint.skill_adapter = adapter(env.path / 'library', dict(active=release(), recent={}), [])
    with pytest.raises(ServiceError, match='new_plan_required'):
        begin(env)
    assert children(env, 'audio_operations') == [] and children(env, 'film_module_operations') == []


def test_resource_plan_replaces_untrusted_binding_with_the_owned_planning_binding(env):
    _, _, _, binding = attach_library(env)
    project = env.store.project(OWNER, env.project['id'])
    frozen = env.runs._freeze(project, film_aware=True)
    scene = frozen['scenes'][0]
    plan = production_plan(dict(nodes=[dict(id='resource', kind='animation', producer='manim',
        inputs=dict(resource_authoring=dict(kind='manim', frame_count=24), library_binding={'publisher_owner': 'other'}),
        dependencies=[], objective='Animate a changing relationship.', required_capabilities=['manim'], key_effects=[],
        local_timing={}, placement=dict(scene_id=scene['id'], start_frame=0))]), frozen,
        deadline_at=1000, library_binding=binding)
    node = next(row for row in plan['nodes'] if row['producer'] == 'manim')
    assert node['inputs']['library_binding'] == binding
    request = ManimPlanProducer.request(dict(task_id=str(uuid4()), project_id=project['id'],
        node_id=node['id'], inputs=node['inputs'], local_timing=node['local_timing'],
        objective=node['objective'], key_effects=[], deadline_at=1000))
    assert request['library_binding'] == binding


def test_formal_manim_coordinator_freezes_parent_release_before_any_recipe_admission(tmp_path):
    from test_manim_film_resource_transport import setup, prepared_request, OWNER as RESOURCE_OWNER
    original = release()
    local = adapter(tmp_path / 'workbench', dict(active=original, recent={}), [])
    binding = local.pin(RESOURCE_OWNER, 'creative-root')
    service, gateway, executor = setup(tmp_path / 'service')
    imports = []
    def archived(owner, pin):
        imports.append(deepcopy(pin))
        return deepcopy(original)
    remote = LibrarySkillProductionAdapter(lambda owner: pytest.fail('Never choose active for a child'), None, None,
        tmp_path / 'creation' / 'library.sqlite3', import_release=archived)
    service.coordinator.skill_adapter = remote
    request = prepared_request(library_binding=binding)
    service.start(RESOURCE_OWNER, request)
    service.advance(RESOURCE_OWNER, request['operation_id'])
    saved = service.coordinator._get(RESOURCE_OWNER, 'film-' + request['operation_id'])
    assert saved['state'] == 'reserved'
    assert {row['publication_sha256'] for row in saved['input']['skill_catalog']['skills']} == {original['sha256']}
    assert gateway.submissions == executor.calls == 0 and len(imports) == 1
    service.coordinator.skill_adapter = LibrarySkillProductionAdapter(None, None, None,
        tmp_path / 'creation' / 'library.sqlite3', import_release=lambda *args: pytest.fail('Use saved bytes'))
    service.advance(RESOURCE_OWNER, request['operation_id'])
    restarted = service.coordinator._get(RESOURCE_OWNER, 'film-' + request['operation_id'])
    assert restarted['input'] == saved['input'] and gateway.submissions == executor.calls == 0


def test_real_agent_client_uploads_planner_packages_and_verifies_host_read_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr('video_workbench.skill_production_gateway.time.time', lambda: 1000)
    original = release()
    service = adapter(tmp_path, dict(active=original, recent={}), [])
    document = dict(schema_version='video-joint-plan-input.v1', project=dict(title='排队',
        script='任务先进入队列。', style='', voice='voice'), scenes=[], brief=dict(audience='', goal='', tone='precise'),
        instruction='', preserve_script=False, method_delivery=None, allowed_engines=['html-svg'])
    binding = service.pin('recipient', 'planning-batch')
    delivery = service.freeze('recipient', 'planning', planning_method_task(document), ['html-svg'],
        library_binding=binding, discovery_only=True)
    document['skill_catalog'] = delivery['catalog']
    peer = Peer()
    peer.profile['profile'] = 'video.joint-plan.v2'
    with AgentClient('http://agent.invalid', TOKEN, transport=httpx.MockTransport(peer)) as client:
        gateway = JointPlanAgentGateway(client, skill_adapter=service)
        receipt = gateway.upload(document)
        gateway.submit('operation-1', document, 1200, receipt)
        assert peer.request['profile'] == 'video.joint-plan.v2'
        peer.job['state'] = 'starting'
        assert gateway.poll(JOB)['state'] == 'starting'
        assert gateway.lookup('operation-1')['state'] == 'starting'
        assert len(peer.request['inputs']) == len(peer.uploads) > 6
        assert peer.uploads['joint-plan-input.json'] == canonical_bytes(document)
        observer = SkillToolObserver(delivery['catalog'], delivery['packages'], input_sha256=digest(document))
        package = original['packages'][0]['sha256']
        params = dict(package_sha256=package)
        observer.observe(dict(type='tool_execution_start', toolName='skill_read', toolCallId='read', args=params))
        observer.observe(dict(type='tool_execution_end', toolName='skill_read', toolCallId='read',
            isError=False, result=observer.tools.tool_result('skill_read', params)))
        evidence = observer.finish(ATTEMPT)
        result = proposal(document, digest(document))
        result.update(skill_tool_evidence_sha256=evidence['sha256'], skill_decisions=[dict(package_sha256=package,
            action='adapt', reason='Track the same task as it enters the queue.', segment_ids=['segment-0'])])
        documents = {'joint-plan-result.json': result, 'animation-skill-tool-evidence.json': evidence}
        identities = {name: str(UUID(int=450 + i)) for i, name in enumerate(documents)}
        peer.contents = {identities[name]: canonical_bytes(value) for name, value in documents.items()}
        peer.manifest = frozen_manifest(JOB, ATTEMPT, [dict(artifact_id=identities[name], name=name,
            size=len(peer.contents[identities[name]]), sha256=hashlib.sha256(peer.contents[identities[name]]).hexdigest())
            for name in documents])
        peer.job.update(state='completed', pins=dict(profile='video.joint-plan.v2', profile_revision='1'),
            artifact_manifest_sha256=peer.manifest['sha256'], attempt=dict(attempt_id=ATTEMPT, fence=1), stop_proven=True,
            stop_proof=dict(stop_proven=True, attempt_id=ATTEMPT, fence=1, request_sha256='a' * 64,
                result_sha256='b' * 64, resource_ids=['stopped-synthetic-writer'], observed_at=1000))
        collected = gateway.collect(JOB, digest(document), document)
        assert collected['result']['skill_decisions'][0]['package_sha256'] == package
        assert collected['evidence'] == evidence and collected['skill_delivery'] == delivery
