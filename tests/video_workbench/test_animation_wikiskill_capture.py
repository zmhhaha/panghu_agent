"""Snapshots come from actual durable stores, not UI projections or old Wiki."""
from copy import deepcopy
import json
import sqlite3
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.animation_wikiskill_contract import make_snapshot, build_input, input_files, validate_input_files
from agent_service.animation_skills_contract import build_skill_package
from test_animation_method_service import work, start, finish
from test_animation_method_trash import OWNER, no_work, delete, evidence, snapshot_database, stop_reader
from test_animation_method_research import research_body, method_body


def source_state(service):
    return evidence(service), snapshot_database(service.metadata_path)


def test_capture_contains_exact_original_inputs_results_source_and_scoped_opinions(work):
    service, creator, reviewer, renderer = work
    run = finish(service, start(service)['id'])
    opinion = service.feedback(OWNER, run['id'], dict(request_id=str(uuid4()),
        candidate_id=run['best_candidate_id'], comment='Synthetic opinion: keep identity visible.'))
    withdrawal = service.withdraw_feedback(OWNER, run['id'], dict(request_id=str(uuid4()), feedback_id=opinion['id']))
    before = source_state(service)
    calls = deepcopy((creator.submissions, reviewer.submissions, renderer.calls))
    captured = service.capture_wikiskill_records(OWNER)
    by_id = {row['record_id']: row for row in captured}
    assert captured == service.capture_wikiskill_records(OWNER)
    assert source_state(service) == before
    assert calls == (creator.submissions, reviewer.submissions, renderer.calls)
    original = service.commissions.get(OWNER, run['id'])
    with sqlite3.connect(service.commissions.path) as db:
        persisted = json.loads(db.execute('SELECT document FROM animation_method_commissions WHERE id=?',
            (run['id'],)).fetchone()[0])
    assert by_id['commission:' + run['id']]['content'] == persisted
    for operation in original['operations']:
        identity = 'operation:' + operation['id']
        assert by_id[identity + ':input']['content'] == operation['input']
        if operation.get('result') is not None:
            assert by_id[identity + ':result']['content'] == operation['result']
    with sqlite3.connect(service.metadata_path) as db:
        persisted_opinion = json.loads(db.execute('SELECT document FROM feedback WHERE owner=? AND request_id=?',
            (OWNER, opinion['request_id'])).fetchone()[0])
    assert by_id['feedback:' + opinion['id']]['content'] == persisted_opinion
    assert any(row['kind'] == 'feedback_withdrawal' and row['content']['feedback_id'] == opinion['id'] for row in captured)
    assert {'commission', 'operation', 'operation_input', 'operation_result', 'method', 'demonstration', 'effect'} <= {row['kind'] for row in captured}
    assert all(row['sha256'] == digest(row['content']) for row in captured)
    frozen = make_snapshot(OWNER, captured)
    assert validate_input_files(build_input(frozen), input_files(frozen)) == frozen
    # No media bytes are captured or opened as a substitute for a hash/identity.
    assert all(type(row['content']) is dict for row in captured)
    assert not any(b'UNIT TEST inert media' in canonical_bytes(row) for row in captured)


def test_research_is_captured_by_exact_link_and_not_from_unrelated_or_previous_wiki(work):
    service, creator, reviewer, renderer = work
    research = service.methods.create_research(OWNER, research_body(), 'linked-research')
    unrelated = service.methods.create_research(OWNER, research_body(), 'unrelated-research')
    package = build_skill_package(skill_id='bounded-staging', version=1, kind='expression',
        files={'SKILL.md': '---\nname: bounded-staging\ndescription: A bounded synthetic staging lesson.\n---\n'
            'Keep intervention identity clear; no claim of full motion review.'},
        runtime_requirements={'engines': ['html-svg']})
    method = service.methods.create_method(OWNER, method_body(runtime_requirements={'engine': 'html-svg'},
        skill_package=package, research_record_ids=[research['id']]), 'linked-method')
    run = start(service, method_versions=[method['id']])
    captured = service.capture_wikiskill_records(OWNER)
    by_id = {row['record_id']: row for row in captured}
    assert by_id['research:' + research['id']]['content'] == research
    assert 'research:' + unrelated['id'] not in by_id
    assert by_id['method:' + method['id']]['content'] == method
    assert not any(row['kind'] in {'wiki_page', 'knowledge_snapshot', 'experience'} for row in captured)
    assert not creator.submissions and not reviewer.submissions and not renderer.calls


def test_capture_excludes_trash_records_and_other_owners_but_keeps_original_persistence(work):
    service, creator, reviewer, renderer = work
    retained = no_work(service, title='Retained visible experiment')
    hidden = no_work(service, title='Deleted private experiment')
    delete(service, hidden['id'])
    other = service.start('another-owner', dict(request_id=str(uuid4()), goal='Private to another owner.',
        budget=dict(max_rounds=1, max_seconds=300), duration_seconds=18))
    before = source_state(service)
    captured = service.capture_wikiskill_records(OWNER)
    run_ids = {row['content']['id'] for row in captured if row['kind'] == 'commission'}
    assert run_ids == {retained['id']} and hidden['id'] not in run_ids and other['id'] not in run_ids
    assert source_state(service) == before
    assert not creator.submissions and not reviewer.submissions and not renderer.calls
    with pytest.raises(ServiceError, match='library_empty'):
        service.capture_wikiskill_records('no-existing-library')


def test_hidden_representative_cannot_leak_through_effect_descriptor(work, monkeypatch):
    service, creator, reviewer, renderer = work
    stop_reader(renderer, monkeypatch)
    original = finish(service, start(service, title='Retained sample')['id'])
    removed = finish(service, start(service, base_commission_id=original['id'], title='Removed latest sample')['id'])
    delete(service, removed['id'])
    captured = service.capture_wikiskill_records(OWNER)
    effects = [row['content'] for row in captured if row['kind'] == 'effect']
    assert len(effects) == 1
    effect = effects[0]
    assert {member['id'] for member in effect['commissions']} == {original['id']}
    assert effect.get('open_commission_id') in (None, original['id'])
    assert removed['best_candidate_id'] not in canonical_bytes(effect).decode()
    assert removed['id'] not in canonical_bytes(effect).decode()


def test_legacy_hidden_empty_retry_is_not_new_compilation_material(work, monkeypatch):
    service, _, _, renderer = work
    stop_reader(renderer, monkeypatch)
    hidden = no_work(service)
    current = finish(service, start(service, retry_of_commission_id=hidden['id'])['id'])
    service.record_failed_attempt_deletion(OWNER, hidden['id'], reason='Synthetic old removal')
    before = source_state(service)
    captured = service.capture_wikiskill_records(OWNER)
    assert {row['content']['id'] for row in captured if row['kind'] == 'commission'} == {current['id']}
    assert source_state(service) == before
