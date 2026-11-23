"""Fresh/retry boundaries through real ledgers and HTTP, using inert fixtures."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.animation_creation_http import CreationHTTPServer, CreationTransportError
from video_workbench.animation_laboratory_library import LaboratoryLibraryAdapter
from video_workbench.animation_method_http import MethodLabClient
import test_animation_collaboration_service as collaboration_fixture
from test_animation_method_http import TOKEN, serving
from test_animation_method_service import finish, start, work
from test_animation_wikiskill_production import release


OWNER = 'developer'


def library(tmp_path, state, reads):
    def active(owner):
        reads.append(('active', owner))
        return deepcopy(state['active'])

    return LaboratoryLibraryAdapter(active,
        lambda owner, identity: deepcopy(state['recent'][identity]), lambda owner: [],
        tmp_path / 'published-library.sqlite3')


@pytest.fixture
def collaborative_work(tmp_path):
    # Delegate to the existing fixture without registering a second "work".
    return collaboration_fixture.work.__wrapped__(tmp_path)


def test_collaborative_fresh_disabled_replans_all_roles_without_old_art_or_wiki(
        collaborative_work, tmp_path, monkeypatch):
    service, ordinary, reviewer, author, renderer = collaborative_work
    publication = release(count=1)
    state, reads = dict(active=publication, recent={publication['id']: publication}), []
    service.library_adapter = library(tmp_path, state, reads)
    original = finish(service, collaboration_fixture.begin(service)['id'], steps=80)
    assert original['state'] == 'completed'
    old = service.commissions.get(OWNER, original['id'])
    old_roles = [part['responsibility'] for part in collaboration_fixture.PLAN['components']]
    old_creator = next(op for op in old['operations'] if op['input'].get('phase') == 'plan')
    assert old_creator['input']['constraints']['animation_knowledge']['pages']
    before = service.catalogue(OWNER)['effects'][0]
    assert before['best_candidate']['id'] == original['best_candidate_id']

    replacement = deepcopy(collaboration_fixture.PLAN)
    replacement.update(composition='A newly planned orbital composition.',
        material='Distinct matte particles and explicit orbital paths.')
    for index, part in enumerate(replacement['components']):
        part['responsibility'] = f'New independent orbital responsibility {index}.'
    monkeypatch.setattr(collaboration_fixture, 'PLAN', replacement)
    original_collect = author.collect

    def collect_fresh(job_id, checksum, document):
        result = original_collect(job_id, checksum, document)
        if document['phase'] == 'integrate':
            # The original fixture deliberately emits one fixed Skill identity;
            # this independent fresh work must author a distinct first version.
            method = result['methods'][0]
            method['title'] = 'New orbital coordinates'
            package = method['skill_package']
            method['skill_package'] = collaboration_fixture.build_skill_package(
                skill_id='fresh-orbital-coordinates', version=1, kind=package['kind'],
                runtime_requirements=package['runtime_requirements'], files=package['files'])
        return collaboration_fixture.contract.validate_result(result, checksum, document)

    monkeypatch.setattr(author, 'collect', collect_fresh)
    # A disabled policy must neither consult the active release nor its bytes.
    service.library_adapter.active = lambda *_: pytest.fail('Disabled consulted active release')
    reads_before = list(reads)
    queued = collaboration_fixture.begin(service, creation_mode='fresh', effect_id=before['id'],
        use_wikiskill=False, goal='Show orbital synchronization without old mountain imagery.')
    rebuilt = finish(service, queued['id'], steps=80)
    assert rebuilt['state'] == 'completed'
    run = service.commissions.get(OWNER, queued['id'])
    operations = [op for op in run['operations'] if op['stage'] == 'creator']
    assert [op['input']['phase'] for op in operations] == [
        'plan', 'component', 'component', 'component', 'integrate']
    old_ids = [original['id'], original['best_candidate_id'], *old['candidates'][0]['method_versions']]
    for operation in operations:
        document = operation['input']
        constraints = document['constraints']
        assert document['base_source'] is None and document['methods'] == []
        assert constraints['authoring_policy']['creation_mode'] == 'fresh'
        assert constraints['library_binding']['use_wikiskill'] is False
        assert constraints['animation_knowledge']['pages'] == constraints['animation_knowledge']['experiences'] == []
        serialized = str(document)
        assert not any(identity in serialized for identity in old_ids)
        assert not any(role in serialized for role in old_roles)
    for operation in [op for op in operations if op['input']['phase'] == 'component']:
        part = next(part for part in replacement['components'] if part['id'] == operation['input']['component_id'])
        assert part['responsibility'] in str(operation['input']['brief'])
    review = next(op for op in run['operations'] if op['stage'] == 'review')
    assert review['input']['knowledge_snapshot']['pages'] == []
    assert reads == reads_before
    after = service.catalogue(OWNER)['effects'][0]
    assert after['open_commission_id'] == before['open_commission_id']
    assert after['best_candidate']['id'] == before['best_candidate']['id']
    assert ordinary.submissions == [] and len(renderer.calls) == len(reviewer.submissions) == 2


@pytest.mark.parametrize('creation_mode', ['new', 'fresh'])
@pytest.mark.parametrize('use_wikiskill', [False, True])
def test_http_creation_replay_strict_retry_and_exact_prompt_snapshots(
        work, tmp_path, creation_mode, use_wikiskill):
    service, author, reviewer, renderer = work
    publication = release(count=1)
    state, reads = dict(active=publication, recent={publication['id']: publication}), []
    service.library_adapter = library(tmp_path, state, reads)
    effect_id = None
    if creation_mode == 'fresh':
        initial = finish(service, start(service, use_wikiskill=False)['id'])
        effect_id = initial['effect_id']
    body = dict(request_id=str(uuid4()), creation_mode=creation_mode,
        goal='Make a distinct, traceable causal expression.', duration_seconds=18,
        budget=dict(max_rounds=1, max_seconds=300), use_wikiskill=use_wikiskill)
    if effect_id is not None:
        body['effect_id'] = effect_id
    with serving(CreationHTTPServer(SimpleNamespace(), TOKEN, pump=False, method_service=service)) as backend:
        client = MethodLabClient('http://127.0.0.1:' + str(backend.server_port), TOKEN)
        queued = client.start(OWNER, body)
        frozen = deepcopy(service.commissions.get(OWNER, queued['id'])['frozen'])
        assert client.start(OWNER, body)['id'] == queued['id']
        assert client.prompt_inspection(OWNER, queued['id'])['submission']['document'] == body
        # Fail only the reserved review: completed creation and render remain
        # reusable, while the new request itself may contain only three fields.
        failed_operation = None
        for _ in range(50):
            run = service.commissions.get(OWNER, queued['id'])
            failed_operation = next((op for op in run['operations']
                if op['stage'] == 'review' and op['status'] == 'reserved'), None)
            if failed_operation:
                service.commissions.claim(OWNER, failed_operation['id'])
                service.commissions.fail(OWNER, failed_operation['id'], 'fixture_review_failed')
                break
            service.advance(OWNER, queued['id'])
        assert failed_operation is not None
        assert finish(service, queued['id'])['state'] == 'failed'
        old_run = service.commissions.get(OWNER, queued['id'])
        calls = len(author.submissions), len(renderer.calls), len(reviewer.submissions)
        retry_body = dict(request_id=str(uuid4()), retry_of_commission_id=queued['id'],
            budget=dict(max_rounds=1, max_seconds=300))
        retried = client.start(OWNER, retry_body)
        assert client.start(OWNER, retry_body)['id'] == retried['id']
        snapshot = client.prompt_inspection(OWNER, retried['id'])
        assert snapshot['submission']['document'] == retry_body
        assert snapshot['effective_request']['document']['creation_mode'] == creation_mode
        assert snapshot['effective_request']['document']['use_wikiskill'] is use_wikiskill
        with pytest.raises(CreationTransportError) as denied:
            client.start(OWNER, dict(retry_body, request_id=str(uuid4()), use_wikiskill=use_wikiskill))
        assert denied.value.status == 409
        assert 'creative_contract_changed' in str(denied.value)
        completed = finish(service, retried['id'])
        assert completed['state'] == 'completed'
        assert (len(author.submissions), len(renderer.calls), len(reviewer.submissions)) == (
            calls[0], calls[1], calls[2] + 1)
        repaired = service.commissions.get(OWNER, retried['id'])
        for key in ('creation_mode', 'use_wikiskill', 'library_binding', 'constraints'):
            assert repaired['frozen'].get(key) == frozen.get(key)
        for operation in repaired['operations']:
            if operation['stage'] not in {'creator', 'review'}:
                continue
            inspected = client.operation_input(OWNER, retried['id'], operation['id'])
            assert inspected['input'] == operation['input']
            assert inspected['input_sha256'] == digest(operation['input'])
            constraints = operation['input']['constraints']
            assert constraints['library_binding']['use_wikiskill'] is use_wikiskill
            knowledge = constraints.get('animation_knowledge', operation['input'].get('knowledge_snapshot'))
            assert bool(knowledge['pages']) is use_wikiskill
        assert service.commissions.get(OWNER, queued['id']) == old_run
        assert client.start(OWNER, body)['id'] == queued['id']
        assert client.start(OWNER, retry_body)['id'] == retried['id']
        assert len(reads) == (1 if use_wikiskill else 0)


@pytest.mark.parametrize('failed_stage', ['creator', 'render', 'review'])
def test_retry_retained_baseline_get_catalogue_prompt_and_trash_projections(work, monkeypatch, failed_stage):
    service, author, reviewer, renderer = work
    reviewer.incomplete = True
    initial = start(service, creation_mode='fresh', use_wikiskill=False,
        effect_id=finish(service, start(service, use_wikiskill=False)['id'])['effect_id'],
        budget=dict(max_rounds=2, max_seconds=300))
    # The bootstrap review was deliberately incomplete as well, but its one
    # round is terminal and supplies only effect membership for this fresh run.
    failed_operation = None
    for _ in range(80):
        run = service.commissions.get(OWNER, initial['id'])
        if run['reviews']:
            failed_operation = next((op for op in run['operations'] if op['stage'] == failed_stage
                and op['status'] == 'reserved'
                and (failed_stage != 'creator' or op['input'].get('mode') == 'create')), None)
            if failed_operation:
                service.commissions.claim(OWNER, failed_operation['id'])
                service.commissions.fail(OWNER, failed_operation['id'], 'fixture_later_stage_failed')
                if failed_stage != 'render':
                    gateway = author if failed_stage == 'creator' else reviewer
                    gateway.jobs[failed_operation['id']] = dict(job_id=str(uuid4()), state='failed',
                        stop_proven=True, document=deepcopy(failed_operation['input']))
                break
        service.advance(OWNER, initial['id'])
    assert failed_operation is not None and finish(service, initial['id'])['state'] == 'failed'
    original = service.commissions.get(OWNER, initial['id'])
    retained_id = original['best_candidate_id']
    reviewer.incomplete = False
    with serving(CreationHTTPServer(SimpleNamespace(), TOKEN, pump=False, method_service=service)) as backend:
        client = MethodLabClient('http://127.0.0.1:' + str(backend.server_port), TOKEN)
        body = dict(request_id=str(uuid4()), retry_of_commission_id=initial['id'],
            budget=dict(max_rounds=1, max_seconds=300))
        queued = client.start(OWNER, body)
        detail = client.get(OWNER, queued['id'])
        assert detail['baseline_candidate']['id'] == retained_id
        assert detail['baseline_candidate']['commission_id'] == initial['id']
        assert detail['best_candidate_id'] == retained_id
        assert client.prompt_inspection(OWNER, queued['id'])['submission']['document'] == body
        completed = finish(service, queued['id'])
        assert completed['state'] == 'completed'
        detail = client.get(OWNER, queued['id'])
        own = [row for row in detail['candidates'] if row['commission_id'] == queued['id']]
        assert own and detail['baseline_candidate']['id'] == retained_id
        assert any(row['id'] == retained_id for row in detail['candidates'])
        stored = service.commissions.get(OWNER, queued['id'])
        assert stored['retry_base_candidate']['id'] == retained_id
        assert 'retry_base_candidate' not in stored['frozen']
        for operation in stored['operations']:
            if operation['stage'] in {'creator', 'review'}:
                assert client.operation_input(OWNER, queued['id'], operation['id'])['input'] == operation['input']
        # Recycling is a visibility change, retaining both candidate ownership
        # and the exact captured request when the whole effect is restored.
        monkeypatch.setattr(renderer, 'stopping_proof', lambda identity: dict(
            operation_id=identity, stop_proven=identity in renderer.results
                or failed_stage == 'render' and identity == failed_operation['id']), raising=False)
        reused = next(op for op in stored['operations'] if op.get('reused_from'))
        forged = deepcopy(reused)
        forged['reused_from']['commission_id'] = str(uuid4())
        with pytest.raises(ServiceError, match='stop_unproven'):
            service.trash.stopped(OWNER, [dict(stored, operations=[forged])])
        if failed_stage == 'review':
            rendered = next(op for op in stored['operations'] if op['stage'] == 'render')
            assert rendered.get('artifact_operation_id')
            forged = deepcopy(rendered)
            forged['artifact_operation_id'] = str(uuid4())
            with pytest.raises(ServiceError, match='stop_unproven'):
                service.trash.stopped(OWNER, [dict(stored, operations=[forged])])
        effect = next(row for row in client.catalogue(OWNER)['effects'] if row['id'] == detail['effect_id'])
        deleted = client.trash_effect(OWNER, effect['id'], dict(request_id=str(uuid4()),
            expected_snapshot_sha256=effect['deletion']['snapshot_sha256']))
        assert client.catalogue(OWNER)['effects'] == []
        assert [row['id'] for row in client.trash_catalogue(OWNER)['items']] == [deleted['entry']['id']]
        client.restore_trash(OWNER, deleted['entry']['id'], dict(request_id=str(uuid4())))
        restored = client.get(OWNER, queued['id'])
        assert restored['baseline_candidate']['id'] == retained_id
        assert [(row['id'], row['commission_id']) for row in restored['candidates']] == [
            (row['id'], row['commission_id']) for row in detail['candidates']]
        assert client.prompt_inspection(OWNER, queued['id'])['submission']['document'] == body
        assert service.commissions.get(OWNER, initial['id']) == original
