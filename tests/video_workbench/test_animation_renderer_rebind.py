"""Explicit renderer replay is new rendering, never new Agent authorship."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.animation_renderer_rebind import freeze_binding, render_input
from test_animation_method_service import work, start, finish, runtime_capabilities


def failed_render(work, monkeypatch):
    service, creator, reviewer, renderer = work
    admitted = runtime_capabilities(['html-svg', 'canvas'])
    monkeypatch.setattr(renderer, 'capabilities', lambda: deepcopy(admitted), raising=False)
    renderer.failed = True
    failed = finish(service, start(service)['id'])
    assert failed['state'] == 'failed' and not failed['candidates']
    original = service.commissions.get('developer', failed['id'])
    admitted['runtime_identity'].update(image_digest='sha256:' + 'd' * 64,
        engine_runtime_sha256='e' * 64, admission_id='UNIT-TEST-rebound')
    return original, admitted


def retry_body(original, **changes):
    return dict(request_id=str(uuid4()), retry_of_commission_id=original['id'],
        budget=dict(max_rounds=1, max_seconds=300), renderer_rebind='current-admitted', **changes)


def test_rebind_renders_exact_historical_source_once_under_frozen_new_runtime(work, monkeypatch):
    service, creator, reviewer, renderer = work
    original, admitted = failed_render(work, monkeypatch)
    body = retry_body(original)
    expected_runtime = deepcopy(admitted['runtime_identity'])
    queued = service.start('developer', body)
    restored = service.commissions.get('developer', queued['id'])
    binding = restored['frozen']['render_context_binding']
    assert binding['runtime_identity'] == expected_runtime
    assert binding['original_runtime_identity'] != expected_runtime
    assert restored['frozen']['constraints'] == original['frozen']['constraints']
    authors = [op for op in original['operations'] if op['stage'] == 'creator']
    assert [op['input'] for op in restored['operations']] == [op['input'] for op in authors]
    assert [op['result'] for op in restored['operations']] == [op['result'] for op in authors]
    assert [op['job_id'] for op in restored['operations']] == [op['job_id'] for op in authors]
    assert all(op['reused_from']['commission_id'] == original['id'] for op in restored['operations'])
    # A subsequent admission cannot reinterpret an HTTP replay's binding.
    admitted['runtime_identity']['image_digest'] = 'sha256:' + 'f' * 64
    assert service.start('developer', body)['id'] == queued['id']
    assert service.commissions.get('developer', queued['id'])['frozen'] == restored['frozen']
    admitted['runtime_identity'] = expected_runtime
    renderer.failed = False
    original_render = renderer.render
    rendered = []
    def record_render(operation_id, source, document, **options):
        rendered.append((deepcopy(source), deepcopy(document)))
        assert document['constraints']['runtime_identity'] == expected_runtime
        return original_render(operation_id, source, document, **options)
    monkeypatch.setattr(renderer, 'render', record_render)
    creator_calls = len(creator.submissions)
    completed = finish(service, queued['id'])
    assert completed['state'] == 'completed'
    assert len(creator.submissions) == creator_calls
    assert completed['usage'] == dict(creator=0, render=1, review=1)
    old_author = next(op for op in authors if op['input']['mode'] == 'create')
    assert len(rendered) == 1 and rendered[0][0] == old_author['result']['source']
    assert rendered[0][1] != old_author['input']
    current = service.commissions.get('developer', queued['id'])
    render = next(op for op in current['operations'] if op['stage'] == 'render')
    receipt = render['input']['renderer_rebind_receipt']
    assert receipt['author_input_sha256'] == old_author['input_sha256']
    assert receipt['render_input_sha256'] == digest(rendered[0][1])
    assert receipt['binding_sha256'] == digest(binding)
    review = next(op for op in current['operations'] if op['stage'] == 'review')
    assert review['input']['constraints']['runtime_identity'] == expected_runtime
    assert review['input']['constraints']['renderer_rebind_provenance']['scope'] == binding['scope']
    inspection = service.prompt_inspection('developer', queued['id'])
    assert inspection['submission']['document'] == body
    assert inspection['renderer_rebind'] == binding
    assert inspection['render_replays'][0]['receipt'] == receipt
    assert service.commissions.get('developer', original['id']) == original


def test_plain_three_field_retry_keeps_original_runtime(work, monkeypatch):
    service, _, _, _ = work
    original, _ = failed_render(work, monkeypatch)
    body = retry_body(original)
    del body['renderer_rebind']
    retry = service.start('developer', body)
    stored = service.commissions.get('developer', retry['id'])
    assert stored['frozen']['constraints'] == original['frozen']['constraints']
    assert 'render_context_binding' not in stored['frozen']


@pytest.mark.parametrize('change', [
    {'renderer_rebind': False}, {'renderer_rebind': 'latest'},
    {'goal': 'Another direction'}, {'use_wikiskill': False},
    {'budget': {'max_rounds': 2, 'max_seconds': 300}},
    {'render_context_binding': {'runtime_identity': 'forged'}},
])
def test_rebind_rejects_invalid_policy_creative_changes_or_injected_binding(work, monkeypatch, change):
    service, _, _, _ = work
    original, _ = failed_render(work, monkeypatch)
    body = retry_body(original)
    body.update(change)
    before = len(service.commissions.list_all('developer'))
    with pytest.raises(ServiceError):
        service.start('developer', body)
    assert len(service.commissions.list_all('developer')) == before


@pytest.mark.parametrize('field,value', [('collaboration', {'enabled': True}),
    ('resource_authoring', {'kind': 'manim'}), ('resource_planning', 'after-plan.v1'), ('role', 'transfer')])
def test_rebind_rejects_unsupported_authoring_scope(work, monkeypatch, field, value):
    original, admitted = failed_render(work, monkeypatch)
    original['frozen'][field] = value
    with pytest.raises(ServiceError, match='renderer_rebind_not_available'):
        freeze_binding(original, admitted)


def test_rebind_requires_failed_first_render_and_no_candidate_or_active_origin(work, monkeypatch):
    original, admitted = failed_render(work, monkeypatch)
    for change in ('running', 'passed', 'missing_create', 'candidate'):
        changed = deepcopy(original)
        if change == 'running':
            changed['state'] = 'running'
        elif change == 'passed':
            next(op for op in changed['operations'] if op['stage'] == 'render')['result']['technical']['status'] = 'passed'
        elif change == 'missing_create':
            changed['operations'] = [op for op in changed['operations'] if op['input'].get('mode') != 'create']
        else:
            changed['candidates'] = [{'id': 'existing'}]
        with pytest.raises(ServiceError, match='renderer_rebind_not_available'):
            freeze_binding(changed, admitted)


def test_rebind_rejects_changed_assets_libraries_and_unadmitted_engine(work, monkeypatch):
    original, admitted = failed_render(work, monkeypatch)
    author = next(op for op in original['operations'] if op['input'].get('mode') == 'create')
    for kind in ('assets', 'libraries', 'engine'):
        changed = deepcopy(original)
        item = next(op for op in changed['operations'] if op['id'] == author['id'])
        if kind == 'assets':
            item['input']['assets']['gsap.min.js'] = 'a' * 64
        elif kind == 'libraries':
            item['input']['constraints']['authoring_libraries'] = {'animejs': {'version': 'old'}}
        else:
            item['result']['source']['engine'] = 'three'
        item['input_sha256'], item['result_sha256'] = digest(item['input']), digest(item['result'])
        with pytest.raises(ServiceError, match='renderer_rebind_(assets_changed|libraries_changed|engine_not_admitted)'):
            freeze_binding(changed, admitted)


def test_derived_render_rejects_source_edits_even_when_author_row_hash_is_updated(work, monkeypatch):
    original, admitted = failed_render(work, monkeypatch)
    binding = freeze_binding(original, admitted)
    author = deepcopy(next(op for op in original['operations'] if op['input'].get('mode') == 'create'))
    author['result']['source']['files']['scene.html'] += '<!-- changed -->'
    author['result_sha256'] = digest(author['result'])
    with pytest.raises(ServiceError, match='renderer_rebind_source_changed'):
        render_input({'frozen': {'render_context_binding': binding}}, author)


def test_rebind_cannot_reuse_completed_media_across_runtime_bindings(work, monkeypatch):
    service, _, _, _ = work
    original, _ = failed_render(work, monkeypatch)
    queued = service.start('developer', retry_body(original))
    run = service.commissions.get('developer', queued['id'])
    assert service._reuse_retry_render('developer', run, {'status': 'reserved'}) is False


def test_rebind_openapi_accepts_only_explicit_stopped_retry_and_one_render():
    from test_animation_capability_selection_openapi import validator
    body = dict(request_id=str(uuid4()), retry_of_commission_id='failed-render',
        budget=dict(max_rounds=1, max_seconds=300), renderer_rebind='current-admitted')
    validator('CommissionRequest').validate(body)
    for change in ({'goal': 'new art'}, {'renderer_rebind': 'latest'},
        {'budget': {'max_rounds': 2, 'max_seconds': 300}}, {'render_context_binding': {}}):
        assert list(validator('CommissionRequest').iter_errors(dict(body, **change)))
    del body['retry_of_commission_id']
    body['goal'] = 'new art'
    assert list(validator('CommissionRequest').iter_errors(body))
