"""User recovery retains old results and never manufactures agent success."""
from copy import deepcopy
from uuid import uuid4

import pytest

from scientific_agent.workspace import WorkspaceError, WorkspaceStore
from scientific_agent.workspace_execution import WorkspaceService
from test_workspace import SQLite, Agent, action, create, finish, outlined, outline, planned


@pytest.fixture
def setup():
    database = SQLite()
    store = WorkspaceStore(database)
    store.initialize()
    agent = Agent(store)
    service = WorkspaceService(store, agent)
    yield store, service, agent
    database.connection.close()


def import_payload(**changes):
    return dict(report_markdown='# DSH 架构综述\n\n用户提供的恢复稿，引用待人工核对。\n',
        origin='user_import', source_reviewed=True, allow_limited_evidence=True,
        unresolved_notes='材料只包含摘要，尚未逐条校验引用。', **changes)


def test_filter_change_then_failed_outline_retains_exact_prior_snapshot(setup):
    store, service, agent = setup
    project = outlined(setup)
    before = deepcopy(project['evidence'])
    previous = store.evidence('alice', project['id'], before['id'])
    project = store.patch('alice', project['id'], dict(expected_revision=project['revision'],
        selections=[dict(source_id='doi:10.1234/fixture', included=False, reason='New scope')]), 'filter')
    project = action(store, project, 'outline')
    service.reconcile('alice', project['id'])
    job = store.raw('alice', project['id'])['jobs'][-1]
    agent.jobs[job['remote_job_id']].update(state='failed', error_code='model_error')
    service.reconcile('alice', project['id'])
    project = store.get('alice', project['id'])
    assert project['evidence']['id'] == before['id']
    assert project['evidence']['content'] == before['content']
    assert project['evidence']['stale'] and project['outline']['stale']
    assert project['jobs'][-1]['state'] == 'failed' and not project['versions']
    exported = store.evidence('alice', project['id'], before['id'])
    assert exported['source_lineage'] == previous['source_lineage']
    assert exported['stale'] and exported['read_only']
    assert 'snapshot' not in exported
    with pytest.raises(WorkspaceError, match='stale_evidence_requires_update'):
        action(store, project, 'confirm_evidence', dict(allow_limited_evidence=True))


def test_old_release_hidden_evidence_is_exposed_without_database_rewrite(setup):
    store, _, _ = setup
    project = outlined(setup)
    evidence_id = project['evidence']['id']
    def old_invalidation(document):
        document['input_revision'] += 1
        document['evidence'], document['outline'] = None, None
    store.internal('alice', project['id'], old_invalidation)
    rows_before = store.db._query('SELECT document FROM scientific_workspace_projects')
    visible = store.get('alice', project['id'])
    assert visible['evidence']['id'] == evidence_id and visible['evidence']['stale']
    assert visible['outline']['content'] == project['outline']['content']
    assert store.db._query('SELECT document FROM scientific_workspace_projects') == rows_before


def test_manual_evidence_is_validated_against_current_sources_and_retains_history(setup):
    store, _, _ = setup
    project = outlined(setup)
    original_id = project['evidence']['id']
    edited = outline()
    edited['sections'][0]['title'] = 'Manually reviewed findings'
    project = action(store, project, 'manual_evidence', dict(outline=edited))
    assert project['evidence']['origin'] == 'manual_edit' and not project['evidence']['stale']
    assert project['evidence']['id'] != original_id
    assert len(project['jobs']) == 3 and project['confirmations']['evidence'] is None
    assert store.evidence('alice', project['id'], original_id)['stale']
    project = store.patch('alice', project['id'], dict(expected_revision=project['revision'],
        selections=[dict(source_id='doi:10.1234/fixture', included=False, reason='Not selected')]), 'exclude')
    with pytest.raises(ValueError, match='workflow_evidence_source_mismatch'):
        action(store, project, 'manual_evidence', dict(outline=edited))


def test_empty_manual_evidence_requires_explicit_gap_acceptance_before_writing(setup):
    store, service, agent = setup
    project = action(store, planned(setup), 'confirm_plan')
    project = action(store, project, 'cancel', dict(job_id=project['active_job_id']))
    empty = dict(evidence=[], sections=[dict(section_id='limitations', title='Limits',
        questions=['Which evidence is missing?'], evidence_ids=[])], gaps=['No usable sources'])
    with pytest.raises(WorkspaceError, match='limited_evidence_confirmation_required'):
        action(store, project, 'manual_evidence', dict(outline=empty))
    project = action(store, project, 'manual_evidence', dict(outline=empty, allow_limited_evidence=True))
    with pytest.raises(WorkspaceError, match='limited_evidence_confirmation_required'):
        action(store, project, 'confirm_evidence')
    project = action(store, project, 'confirm_evidence', dict(allow_limited_evidence=True))
    assert project['jobs'][-1]['stage'] == 'draft'
    assert len(agent.jobs) == 1  # Only the earlier, completed planning execution.


def test_manual_evidence_enters_original_draft_contract_without_fake_agent_result(setup):
    store, service, agent = setup
    project = outlined(setup)
    project = action(store, project, 'manual_evidence', dict(outline=outline()))
    assert project['evidence']['task_id'] is None
    assert len(project['jobs']) == 3
    project = action(store, project, 'confirm_evidence')
    project = finish(store, service, agent, project, 'draft')
    assert project['stage'] == 'review' and project['current_version_id']
    assert len(project['jobs']) == 4 and project['jobs'][-1]['state'] == 'completed'
    version = store.version('alice', project['id'], project['current_version_id'])
    assert version['model_trace_available'] is True and version['origin'] == 'website_generated'


def test_import_review_adopt_finalize_without_faking_preceding_stage_success(setup):
    store, _, agent = setup
    project = create(store)
    original = import_payload()['report_markdown']
    project = action(store, project, 'import_report', import_payload())
    imported = project['versions'][0]
    assert project['current_version_id'] is None and imported['status'] == 'candidate'
    assert not project['jobs'] and agent.posts == 0
    assert project['confirmations'] == dict(plan=None, evidence=None)
    assert imported['origin'] == 'user_import' and imported['model_trace_available'] is False
    version = store.version('alice', project['id'], imported['id'])
    assert version['report_markdown'] == original
    assert version['revision_sections'] == []
    assert version['references'] == version['claims'] == version['evidence'] == []
    assert 'trace_artifacts' not in version and 'source_snapshot' not in version
    with pytest.raises(WorkspaceError, match='import_review_confirmation_required'):
        action(store, project, 'adopt', dict(version_id=imported['id']))
    project = action(store, project, 'confirm_import', dict(version_id=imported['id'],
        source_reviewed=True, allow_limited_evidence=True, unresolved_notes='Full texts not checked.'))
    project = action(store, project, 'adopt', dict(version_id=imported['id']))
    project = action(store, project, 'finalize', dict(version_id=imported['id']))
    assert project['current_version_id'] == imported['id'] and project['stage'] == 'final'
    assert project['finalizations'][0]['publication_authorized'] is False
    assert project['confirmations'] == dict(plan=None, evidence=None)
    assert not project['jobs'] and agent.posts == 0


def test_import_cannot_bypass_source_review_or_machine_revision_limits(setup):
    store, _, _ = setup
    project = create(store)
    for field, code in [('source_reviewed', 'source_review_required'),
                        ('allow_limited_evidence', 'limited_evidence_confirmation_required')]:
        payload = import_payload()
        payload[field] = False
        with pytest.raises(WorkspaceError, match=code):
            action(store, project, 'import_report', payload)
    project = action(store, project, 'import_report', import_payload())
    version_id = project['versions'][0]['id']
    with pytest.raises(WorkspaceError, match='imported_report_requires_manual_revision'):
        action(store, project, 'revise', dict(version_id=version_id,
            section_ids=['anything'], feedback='Rewrite'))
    project = store.patch('alice', project['id'], dict(expected_revision=project['revision'],
        requirements=dict(question='Changed scope')), 'scope-change')
    with pytest.raises(WorkspaceError, match='stale_version_requires_reconfirmation'):
        action(store, project, 'confirm_import', dict(version_id=version_id,
            source_reviewed=True, allow_limited_evidence=True))


def test_import_size_limit_is_utf8_bytes_not_unicode_character_count(setup):
    store, _, _ = setup
    project = create(store)
    payload = import_payload()
    payload['report_markdown'] = '# 大稿件\n' + '研' * 700_000
    assert len(payload['report_markdown']) < 2 * 1024 * 1024
    assert len(payload['report_markdown'].encode('utf-8')) > 2 * 1024 * 1024
    with pytest.raises(WorkspaceError, match='report_too_large') as exc:
        action(store, project, 'import_report', payload)
    assert exc.value.status == 413
    assert store.get('alice', project['id'])['versions'] == []


def test_import_owner_cas_and_receipt_replay_are_enforced(setup):
    store, _, _ = setup
    project = create(store)
    payload = dict(expected_revision=project['revision'], **import_payload())
    with pytest.raises(WorkspaceError, match='project_not_found'):
        store.action('bob', project['id'], 'import_report', payload, 'import')
    saved = store.action('alice', project['id'], 'import_report', payload, 'import')
    replay = store.action('alice', project['id'], 'import_report', payload, 'import')
    assert replay == saved and len(replay['versions']) == 1
    with pytest.raises(WorkspaceError, match='project_revision_conflict'):
        store.action('alice', project['id'], 'import_report', payload, 'different-import')
    with pytest.raises(WorkspaceError, match='project_not_found'):
        store.version('bob', project['id'], saved['versions'][0]['id'])


def test_manual_report_edit_preserves_parent_and_requires_new_review(setup):
    store, _, _ = setup
    project = action(store, create(store), 'import_report', import_payload())
    original_id = project['versions'][0]['id']
    project = action(store, project, 'confirm_import', dict(version_id=original_id,
        source_reviewed=True, allow_limited_evidence=True))
    project = action(store, project, 'adopt', dict(version_id=original_id))
    old = store.version('alice', project['id'], original_id)['report_markdown']
    edit = import_payload()
    edit.update(origin='manual_edit', parent_version_id=original_id, report_markdown=old + '\n人工修订。\n')
    project = action(store, project, 'import_report', edit)
    candidate = project['versions'][-1]
    assert project['current_version_id'] == original_id
    assert candidate['origin'] == 'manual_edit' and candidate['import_review'] is None
    assert store.version('alice', project['id'], original_id)['report_markdown'] == old
    diff = store.compare('alice', project['id'], original_id, candidate['id'])
    assert diff['changed'] and '+人工修订。' in diff['diff']
    with pytest.raises(WorkspaceError, match='import_review_confirmation_required'):
        action(store, project, 'adopt', dict(version_id=candidate['id']))


def test_classifying_uploaded_manuscript_separates_file_reading_from_sources(setup):
    store, _, _ = setup
    project = create(store)
    project = store.add_material('alice', project['id'], project['revision'], 'upload-manuscript',
        filename='recovery.md', raw=b'# Manuscript\nUser written recovery draft.\n')
    material_id = project['materials'][0]['material_id']
    project = action(store, project, 'classify_material',
        dict(material_id=material_id, classification='draft'))
    material = project['materials'][0]
    assert material['file_status'] == 'read' and material['material_role'] == 'draft'
    assert material['source_count'] == material['paper_count'] == 0
    assert material_id not in {row['source_id'] for row in project['selections']}
    raw = store.raw('alice', project['id'])
    assert raw['materials'][0]['text'].startswith('# Manuscript')
    project = action(store, project, 'plan')
    assert store.raw('alice', project['id'])['jobs'][-1]['frozen_context']['materials'] == []


def test_bundle_declarations_are_explicit_bounded_and_not_promoted_to_full_text(setup):
    store, _, _ = setup
    project = create(store)
    project = store.add_material('alice', project['id'], project['revision'], 'upload-pack',
        filename='evidence-pack.md', raw=b'Paper abstract\nOnly an abstract is available.\nOfficial engineering documentation.\n')
    material_id = project['materials'][0]['material_id']
    declarations = [dict(title='Paper', access_level='abstract', source_type='paper',
        start_line=1, end_line=2), dict(title='DSH documentation', access_level='full_text',
        source_type='engineering_document', start_line=3, end_line=3)]
    project = action(store, project, 'classify_material', dict(material_id=material_id,
        classification='source_bundle', declared_sources=declarations))
    material = project['materials'][0]
    assert material['file_status'] == 'read'
    assert (material['paper_count'], material['document_count'], material['source_count']) == (1, 1, 2)
    assert material['declared_sources'][0]['access_level'] == 'abstract'
    assert all(row['declaration_basis'] == 'user_declared' for row in material['declared_sources'])
