"""Imported-reference matching stays owner/version scoped and human reviewed."""
from copy import deepcopy
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.scientific_workspace import create_router
from scientific_agent.import_citations import citation_inventory
from scientific_agent.workspace import WorkspaceError
from scientific_agent.workspace_materials import parse_material
from test_recovery import import_payload
from test_workspace import setup, create, action


def report():
    return '# 文献综述\n\n已核对与待核对的来源 ' + ' '.join('[' + str(i) + ']' for i in range(1, 19)) \
        + '。再次引用 [1]。\n\n## 参考文献\n\n' \
        + '\n'.join('[' + str(i) + '] 参考条目 ' + str(i) + '。' for i in range(1, 19)) + '\n'


def imported(setup):
    store, _, _ = setup
    project = create(store)
    for i in range(2):
        project = store.add_material('alice', project['id'], project['revision'], 'source-' + str(i),
            filename='source-' + str(i) + '.md', raw=('# Explicit supplied source ' + str(i) + '\nSource body.').encode())
    return action(store, project, 'import_report', dict(import_payload(), report_markdown=report()))


def mapping(project, rows):
    return dict(version_id=project['versions'][-1]['id'], reference_mappings=rows)


def confirm(store, project, version_id):
    return action(store, project, 'confirm_import', dict(version_id=version_id,
        source_reviewed=True, allow_limited_evidence=True, unresolved_notes='Unmatched references remain explicit.'))


def test_inventory_retains_all_eighteen_numbers_and_body_occurrences():
    value = citation_inventory(report())
    assert [row['n'] for row in value['references']] == list(range(1, 19))
    assert value['unmatched_numbers'] == list(range(1, 19))
    assert value['references'][0]['body_occurrences'] == 2
    assert all(row['body_occurrences'] == 1 for row in value['references'][1:])
    assert all(row['match_status'] == 'unmatched' and row['source_id'] is None for row in value['references'])
    assert value['validation'] == 'user_matched_not_machine_claim_validated'


def test_partial_matching_keeps_unmatched_reference_thirteen_visible_without_faking_trace(setup):
    store, _, agent = setup
    project = imported(setup)
    version_id = project['versions'][0]['id']
    before = store.version('alice', project['id'], version_id)
    source_ids = [row['paper_id'] for row in before['sources']]
    project = action(store, project, 'map_import_references', mapping(project,
        [{'n': 1, 'source_id': source_ids[0]}, {'n': 18, 'source_id': source_ids[1]}]))
    version = store.version('alice', project['id'], version_id)
    inventory = version['import_citations']
    rows = {row['n']: row for row in inventory['references']}
    assert len(rows) == 18 and rows[1]['match_status'] == rows[18]['match_status'] == 'manually_matched'
    assert rows[13]['text'] == '参考条目 13。'
    assert rows[13]['source_id'] is None and rows[13]['match_status'] == 'unmatched'
    assert 13 in inventory['unmatched_numbers']
    for key in ('report_markdown', 'report_sha256', 'sources', 'origin', 'model_trace_available'):
        assert version[key] == before[key]
    assert version['origin'] == 'user_import' and version['model_trace_available'] is False
    assert version['references'] == version['evidence'] == version['claims'] == []
    assert project['confirmations'] == {'plan': None, 'evidence': None}
    assert agent.posts == 0 and project['jobs'] == []


def test_inline_code_numbers_are_not_inventory_citations():
    value = citation_inventory('# Findings\nLiteral `[13]` and ``[14]``; actual [1].\n\n## References\n[1] Actual.\n')
    assert [row['n'] for row in value['references']] == [1]
    assert value['references'][0]['body_occurrences'] == 1


def test_reference_display_preserves_the_actual_title_with_inline_code():
    value = citation_inventory('# Findings\nLiteral `[13]`; actual [1].\n\n## References\n'
        '[1] The `README.md` and `pyproject.toml` engineering documents.\n')
    assert [row['n'] for row in value['references']] == [1]
    assert value['references'][0]['text'] == 'The `README.md` and `pyproject.toml` engineering documents.'


def test_fenced_and_indented_examples_are_not_reference_records():
    text = ('# Findings\nActual [1].\n\n```text\n[13] Fake.\n```\n\n'
            '    [14] Another example.\n\n## References\n[1] Actual.\n')
    value = citation_inventory(text)
    assert [row['n'] for row in value['references']] == [1]


def test_fence_with_info_suffix_inside_code_is_not_a_closing_fence():
    text = ('# Findings\n```text\n[99] Code.\n```python\n[13] Still code.\n```\n\n'
            'Actual [1].\n\n## References\n[1] Actual.\n')
    value = citation_inventory(text)
    assert [row['n'] for row in value['references']] == [1]


def test_unknown_body_reference_is_kept_as_an_explicit_missing_entry():
    value = citation_inventory('# Findings\nUnknown citation [13].\n\n## References\n[1] Only known entry.\n')
    rows = {row['n']: row for row in value['references']}
    assert rows[13]['text'] == '' and rows[13]['body_occurrences'] == 1
    assert rows[13]['match_status'] == 'unmatched' and 13 in value['unmatched_numbers']


@pytest.mark.parametrize('rows', [[{'n': 0, 'source_id': 'placeholder'}],
    [{'n': 19, 'source_id': 'placeholder'}], [{'n': '1', 'source_id': 'placeholder'}],
    [{'n': True, 'source_id': 'placeholder'}], [{'n': 1, 'source_id': 'unknown-source'}],
    [{'n': 1, 'source_id': 'placeholder', 'machine_verified': True}],
    [{'n': 1, 'source_id': 'placeholder'}, {'n': 1, 'source_id': 'placeholder'}], None])
def test_invalid_mapping_is_rejected_atomically(setup, rows):
    store, _, _ = setup
    project = imported(setup)
    version_id = project['versions'][0]['id']
    source_id = store.version('alice', project['id'], version_id)['sources'][0]['paper_id']
    rows = deepcopy(rows)
    if rows:
        for row in rows:
            if row.get('source_id') == 'placeholder':
                row['source_id'] = source_id
    before = store.raw('alice', project['id'])
    with pytest.raises(WorkspaceError, match='invalid_reference_mappings'):
        action(store, project, 'map_import_references', mapping(project, rows))
    assert store.raw('alice', project['id']) == before


def test_source_from_another_project_is_not_accepted_into_frozen_version_mapping(setup):
    store, _, _ = setup
    project = imported(setup)
    another = create(store, owner='bob')
    another = store.add_material('bob', another['id'], another['revision'], 'other-source',
        filename='other.md', raw=b'# Different owner source\nNot in this report version.')
    foreign_id = another['materials'][0]['material_id']
    before = store.raw('alice', project['id'])
    with pytest.raises(WorkspaceError, match='invalid_reference_mappings'):
        action(store, project, 'map_import_references', mapping(project, [{'n': 1, 'source_id': foreign_id}]))
    assert store.raw('alice', project['id']) == before


def test_mapping_revokes_prior_review_and_requires_fresh_review_before_finalization(setup):
    store, _, _ = setup
    project = imported(setup)
    version_id = project['versions'][0]['id']
    project = confirm(store, project, version_id)
    project = action(store, project, 'adopt', {'version_id': version_id})
    old_confirmation = store.version('alice', project['id'], version_id)['confirmation_id']
    source_id = store.version('alice', project['id'], version_id)['sources'][0]['paper_id']
    project = action(store, project, 'map_import_references', mapping(project, [{'n': 1, 'source_id': source_id}]))
    version = store.version('alice', project['id'], version_id)
    assert version['import_review'] is None and version['confirmation_id'] is None
    with pytest.raises(WorkspaceError, match='import_review_confirmation_required'):
        action(store, project, 'finalize', {'version_id': version_id})
    project = confirm(store, project, version_id)
    assert store.version('alice', project['id'], version_id)['confirmation_id'] != old_confirmation
    project = action(store, project, 'finalize', {'version_id': version_id})
    assert project['stage'] == 'final' and project['finalizations'][0]['version_id'] == version_id


def test_finalized_reference_mapping_is_immutable(setup):
    store, _, _ = setup
    project = imported(setup)
    version_id = project['versions'][0]['id']
    source_id = store.version('alice', project['id'], version_id)['sources'][0]['paper_id']
    project = confirm(store, project, version_id)
    project = action(store, project, 'adopt', {'version_id': version_id})
    project = action(store, project, 'finalize', {'version_id': version_id})
    before = store.raw('alice', project['id'])
    with pytest.raises(WorkspaceError, match='finalized_version_is_immutable'):
        action(store, project, 'map_import_references', mapping(project, [{'n': 1, 'source_id': source_id}]))
    assert store.raw('alice', project['id']) == before


def test_mapping_replay_does_not_revoke_a_later_independent_review(setup):
    store, _, _ = setup
    project = imported(setup)
    version_id = project['versions'][0]['id']
    source_id = store.version('alice', project['id'], version_id)['sources'][0]['paper_id']
    payload = dict(expected_revision=project['revision'], **mapping(project, [{'n': 1, 'source_id': source_id}]))
    project = store.action('alice', project['id'], 'map_import_references', payload, 'map-key')
    project = confirm(store, project, version_id)
    reviewed = store.version('alice', project['id'], version_id)
    replay = store.action('alice', project['id'], 'map_import_references', payload, 'map-key')
    assert replay['revision'] == project['revision']
    assert store.version('alice', project['id'], version_id)['import_review'] == reviewed['import_review']
    assert store.version('alice', project['id'], version_id)['confirmation_id'] == reviewed['confirmation_id']


def test_stale_version_cannot_receive_new_reference_decisions(setup):
    store, _, _ = setup
    project = imported(setup)
    version_id = project['versions'][0]['id']
    source_id = store.version('alice', project['id'], version_id)['sources'][0]['paper_id']
    project = store.patch('alice', project['id'], dict(expected_revision=project['revision'],
        requirements={'question': 'A new research scope'}), 'new-scope')
    before = store.raw('alice', project['id'])
    with pytest.raises(WorkspaceError, match='stale_version_requires_reconfirmation'):
        action(store, project, 'map_import_references', mapping(project, [{'n': 1, 'source_id': source_id}]))
    assert store.raw('alice', project['id']) == before


def test_native_generated_version_cannot_be_relabelled_as_import_reference_matching(setup):
    store, _, _ = setup
    project = imported(setup)
    version_id = project['versions'][0]['id']
    # Isolate the boundary guard; this synthetic flag does not claim an actual
    # model run or turn the fixture import into an accepted generated report.
    store.internal('alice', project['id'], lambda value:
        value['versions'][0].update(model_trace_available=True))
    project = store.get('alice', project['id'])
    before = store.raw('alice', project['id'])
    with pytest.raises(WorkspaceError, match='imported_version_required'):
        action(store, project, 'map_import_references', {'version_id': version_id,
            'reference_mappings': [{'n': 1, 'source_id': 'doi:10.1234/fixture'}]})
    assert store.raw('alice', project['id']) == before


def test_old_import_inventory_is_projected_without_rewriting_persisted_history(setup):
    store, _, _ = setup
    project = imported(setup)
    version_id = project['versions'][0]['id']
    store.internal('alice', project['id'], lambda value: value['versions'][0].pop('import_citations'))
    before = store.db._query('SELECT document FROM scientific_workspace_projects')
    version = store.version('alice', project['id'], version_id)
    assert len(version['import_citations']['references']) == 18
    assert 13 in version['import_citations']['unmatched_numbers']
    assert store.db._query('SELECT document FROM scientific_workspace_projects') == before


def test_api_mapping_and_source_record_download_remain_owner_scoped(setup):
    store, service, _ = setup
    project = imported(setup)
    version_id = project['versions'][0]['id']
    source_id = store.version('alice', project['id'], version_id)['sources'][0]['paper_id']
    app = FastAPI()
    app.include_router(create_router(service))
    url = '/api/v1/research/projects/' + project['id']
    with TestClient(app, headers={'X-Forwarded-User': 'alice'}) as client:
        response = client.post(url + '/actions/map_import_references',
            json=dict(expected_revision=project['revision'], version_id=version_id,
                reference_mappings=[{'n': 1, 'source_id': source_id}]),
            headers={'Idempotency-Key': 'map-api'})
        assert response.status_code == 200, response.text
        source_record = client.get(url + '/versions/' + version_id + '/download?format=sources')
        assert source_record.status_code == 200
        assert source_record.json()['import_citations']['references'][0]['source_id'] == source_id
        assert source_record.json()['references'] == source_record.json()['claims'] == source_record.json()['evidence'] == []
        assert client.get(url + '/versions/' + version_id,
            headers={'X-Forwarded-User': 'bob'}).status_code == 404
        wrong = client.post(url + '/actions/map_import_references',
            json=dict(expected_revision=response.json()['revision'], version_id=version_id, reference_mappings=[]),
            headers={'Idempotency-Key': 'foreign-map', 'X-Forwarded-User': 'bob'})
        assert wrong.status_code == 404
