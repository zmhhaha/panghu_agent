"""Inspection exports real immutable business inputs without advancing jobs."""
from copy import deepcopy
import json
import sqlite3
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.animation_method_service import AnimationMethodService
from video_workbench.animation_prompt_inspection import inspection_view, operation_input_view
from test_animation_method_service import work, start, finish
from test_animation_method_trash import snapshot_database
from test_animation_collaboration_service import work as collaboration_work, begin


def reserve_plan(service, run_id, *, transform=None):
    run = service.commissions.get('developer', run_id)
    document = service._document('developer', run, 'plan')
    if transform:
        transform(document)
    return service.commissions.reserve('developer', run_id, 'creator', document, 'plan')


def test_original_submission_is_distinct_from_effective_defaults_and_stays_out_of_detail(work):
    service, creator, reviewer, renderer = work
    body = dict(request_id=str(uuid4()), title=None, goal='A submitted goal',
                budget=dict(max_rounds=1, max_seconds=300))
    row = service.start('developer', body)
    view = service.prompt_inspection('developer', row['id'])
    assert view['submission']['origin'] == 'captured_request'
    assert view['submission']['is_original'] is True
    assert view['submission']['action'] == 'create'
    assert view['submission']['document'] == body
    assert view['submission']['sha256'] == digest(body)
    actual = view['effective_request']['document']
    assert 'title' not in actual and actual['duration_seconds'] == 18
    assert actual['budget'] == dict(creator=2, review=1, render=1, seconds=300)
    assert view['operations'] == []
    assert not ({'submission', 'effective_request', 'operations', 'submission_snapshot'} & set(row))
    assert creator.submissions == reviewer.submissions == renderer.calls == []


def test_snapshot_first_write_is_immutable_and_equivalent_replay_does_not_replace_it(work):
    service, _, _, _ = work
    body = dict(request_id=str(uuid4()), title=None, goal='First submission',
                budget=dict(max_rounds=1, max_seconds=300))
    row = service.start('developer', body)
    frozen = deepcopy(service.commissions.get('developer', row['id'])['frozen'])
    replay = {key: value for key, value in body.items() if key != 'title'}
    assert service.start('developer', replay)['id'] == row['id']
    stored = service.commissions.get('developer', row['id'])
    assert stored['frozen'] == frozen
    assert stored['submission_snapshot']['document'] == body
    assert stored['submission_snapshot_sha256'] == digest(stored['submission_snapshot'])


def test_submission_receipts_are_host_owned_and_cannot_be_injected_by_api_body(work):
    service, creator, reviewer, renderer = work
    for field in ('submission_snapshot', 'submission_snapshot_sha256', '_submission_snapshot'):
        with pytest.raises(ServiceError):
            start(service, **{field: {'document': {'goal': 'invented earlier request'}}})
    assert service.commissions.list_all('developer') == []
    assert creator.submissions == reviewer.submissions == renderer.calls == []


def test_historical_request_is_labelled_reconstruction_and_replay_does_not_backfill(work):
    service, _, _, _ = work
    request_id = str(uuid4())
    body = dict(goal='Historic task', budget=dict(creator=2, review=1, render=1, seconds=300),
                duration_seconds=18)
    old = service.commissions.create('developer', body, request_id)
    before = snapshot_database(service.commissions.path)
    view = service.prompt_inspection('developer', old['id'])
    assert view['submission']['origin'] == 'reconstructed_frozen_fields'
    assert view['submission']['is_original'] is False
    assert view['submission']['limitations']
    assert 'submission_snapshot' not in service.commissions.get('developer', old['id'])
    assert snapshot_database(service.commissions.path) == before
    replay = service.start('developer', dict(request_id=request_id, goal='Historic task',
        budget=dict(max_rounds=1, max_seconds=300)))
    assert replay['id'] == old['id']
    assert 'submission_snapshot' not in service.commissions.get('developer', old['id'])


def test_transfer_captures_its_original_body_not_the_host_derived_start_request(work):
    service, _, _, _ = work
    first = finish(service, start(service)['id'])
    body = dict(request_id=str(uuid4()), goal='A fork and a join',
        structural_changes={'relations': {'before': 'one route', 'after': 'a fork and join'}},
        budget=dict(max_rounds=1, max_seconds=300))
    row = service.transfer('developer', first['id'], body)
    view = service.prompt_inspection('developer', row['id'])
    assert view['submission']['action'] == 'transfer'
    assert view['submission']['document'] == body
    assert 'method_versions' not in view['submission']['document']
    assert view['effective_request']['document']['method_versions']
    assert view['effective_request']['document']['role'] == 'transfer'


def test_input_is_exact_frozen_document_not_rebuilt_from_current_settings_and_get_is_readonly(work, monkeypatch):
    service, creator, reviewer, renderer = work
    row = start(service)
    operation = reserve_plan(service, row['id'], transform=lambda doc:
        doc['constraints'].update(large_context='original frozen data' * 30000))
    before = snapshot_database(service.commissions.path), snapshot_database(service.methods.path)
    monkeypatch.setattr(service, 'advance', lambda *args: pytest.fail('GET must not advance'))
    monkeypatch.setattr(service, '_document', lambda *args, **kwargs: pytest.fail('GET must not rebuild'))
    monkeypatch.setattr(service, '_capabilities', lambda *args: pytest.fail('GET must not inspect current admission'))
    view = service.prompt_inspection('developer', row['id'])
    exported = service.operation_input('developer', row['id'], operation['id'])
    assert exported['input'] == operation['input']
    assert exported['input_sha256'] == operation['input_sha256'] == digest(exported['input'])
    assert view['operations'][0]['input_bytes'] == len(canonical_bytes(operation['input']))
    assert len(canonical_bytes(view)) < 20000
    assert exported['round'] == 0 and exported['round_basis'] == 'plan'
    assert 'job_id' not in exported and 'owner' not in exported and 'claim_token' not in exported
    exported['input']['goal'] = 'A local edited copy'
    assert service.operation_input('developer', row['id'], operation['id'])['input'] == operation['input']
    assert before == (snapshot_database(service.commissions.path), snapshot_database(service.methods.path))
    assert creator.submissions == reviewer.submissions == renderer.calls == []


def test_rounds_and_review_candidates_are_linked_by_actual_frozen_operations(work):
    service, _, reviewer, _ = work
    reviewer.incomplete = True
    row = finish(service, start(service, budget=dict(max_rounds=2, max_seconds=300))['id'])
    view = service.prompt_inspection('developer', row['id'])
    assert [op['round'] for op in view['operations'] if op['mode'] == 'create'] == [1, 2]
    assert [op['round'] for op in view['operations'] if op['mode'] == 'review'] == [1, 2]
    assert all(op['round_basis'] == 'candidate_author' and op['candidate_ids']
               for op in view['operations'] if op['mode'] == 'review')
    assert all(op['stage'] in {'creator', 'review'} for op in view['operations'])
    assert all(op['input_url'].endswith('/operations/' + op['operation_id'] + '/input')
               for op in view['operations'])


def test_owner_commission_and_deleted_visibility_are_enforced(work):
    service, _, _, _ = work
    first, second = start(service), start(service)
    operation = reserve_plan(service, first['id'])
    with pytest.raises(ServiceError) as other_owner:
        service.operation_input('another-developer', first['id'], operation['id'])
    assert other_owner.value.status == 404
    with pytest.raises(ServiceError) as wrong_commission:
        service.operation_input('developer', second['id'], operation['id'])
    assert wrong_commission.value.status == 404
    service.commissions.release_unsubmitted('developer', operation['id'], {'reason': 'fixture stopped'})
    service.commissions.stop('developer', first['id'], 'failed', 'No sample was made')
    service.record_failed_attempt_deletion('developer', first['id'], reason='Read visibility test')
    with pytest.raises(ServiceError) as hidden_index:
        service.prompt_inspection('developer', first['id'])
    assert hidden_index.value.status == 404
    with pytest.raises(ServiceError) as hidden_input:
        service.operation_input('developer', first['id'], operation['id'])
    assert hidden_input.value.status == 404


@pytest.mark.parametrize('private', [dict(claim_token='do-not-export'),
    dict(authorization='Bearer do-not-export'), dict(workspace_path='C:/private/worker'),
    dict(path='/srv/private/resource')])
def test_private_envelopes_and_local_paths_are_refused_without_redacting_or_rehashing(work, private):
    service, _, _, _ = work
    row = start(service)
    operation = reserve_plan(service, row['id'], transform=lambda doc: doc['constraints'].update(private))
    with pytest.raises(ServiceError, match='private_data'):
        service.operation_input('developer', row['id'], operation['id'])
    index = service.prompt_inspection('developer', row['id'])['operations'][0]
    assert index['available'] is False and index['unavailable_reason'] == 'animation_prompt_inspection_private_data'
    assert service.commissions.operation('developer', operation['id'])['input'] == operation['input']


def test_corrupt_input_and_submission_digests_fail_closed(work):
    service, _, _, _ = work
    row = start(service)
    operation = reserve_plan(service, row['id'])
    with sqlite3.connect(service.commissions.path) as db:
        raw = json.loads(db.execute('SELECT document FROM animation_method_commission_operations WHERE id=?',
                                    (operation['id'],)).fetchone()[0])
        raw['input']['goal'] = 'Changed behind the immutable record'
        db.execute('UPDATE animation_method_commission_operations SET document=? WHERE id=?',
                   (json.dumps(raw), operation['id']))
    with pytest.raises(ServiceError, match='frozen_input_changed|trash_record_changed'):
        service.operation_input('developer', row['id'], operation['id'])
    # The normal visibility layer also checks ledger integrity and can refuse
    # the whole corrupt task. The projection itself isolates an unreadable row.
    index = inspection_view(service.commissions.get('developer', row['id']))
    assert index['submission']['document']['goal'] == row['goal']
    assert index['operations'][0]['available'] is False
    assert index['operations'][0]['unavailable_reason'] == 'animation_method_operation_frozen_input_changed'
    with sqlite3.connect(service.commissions.path) as db:
        raw = json.loads(db.execute('SELECT document FROM animation_method_commissions WHERE id=?',
                                    (row['id'],)).fetchone()[0])
        raw['submission_snapshot']['document']['goal'] = 'Changed submission'
        db.execute('UPDATE animation_method_commissions SET document=? WHERE id=?', (json.dumps(raw), row['id']))
    with pytest.raises(ServiceError, match='submission_changed'):
        service.prompt_inspection('developer', row['id'])


def test_collaboration_indexes_real_phase_inputs_and_its_single_round(collaboration_work):
    service, _, _, _, _ = collaboration_work
    row = finish(service, begin(service)['id'], steps=100)
    view = service.prompt_inspection('developer', row['id'])
    components = [op for op in view['operations'] if op['phase'] == 'component']
    assert len(components) == 3
    assert {op['component_id'] for op in components} == {'structure', 'foreground', 'atmosphere'}
    assert all(op['scope'] == 'collaboration' and op['round'] == 1 for op in components)
    for operation in components:
        exported = service.operation_input('developer', row['id'], operation['operation_id'])
        assert exported['input']['component_id'] == operation['component_id']
        assert exported['input_sha256'] == digest(exported['input'])
    assert next(op for op in view['operations'] if op['mode'] == 'review')['round'] == 1
