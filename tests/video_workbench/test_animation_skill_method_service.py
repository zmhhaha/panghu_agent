"""Skill/Wiki orchestration using synthetic stopped jobs and inert media only."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.animation_skills_contract import build_skill_package
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_skill_method as protocol
from video_workbench.animation_method_commissions import AnimationMethodCommissionStore
from video_workbench.animation_method_service import AnimationMethodService
from test_animation_method_service import Gateway, Renderer, finish, start


class SkillGateway(Gateway):
    protocol = protocol

    def upload(self, document, files):
        super().upload(document, files)
        refs = [dict(path=protocol.INPUT_NAME, sha256=digest(document), input_id=str(uuid4()),
                     role='animation_method_input')]
        refs.extend(dict(path=frame['file_name'], sha256=frame['sha256'], input_id=str(uuid4()),
                         role='animation_frame') for frame in document['observation']['frames'])
        return refs

    def collect(self, job_id, checksum, document):
        result = super().collect(job_id, checksum, document)
        result.update(schema=protocol.RESULT_SCHEMA, knowledge_proposals=[])
        transfer = document['constraints'].get('method_application', {}).get('role') == 'transfer'
        if transfer or document['mode'] == 'review':
            result['methods'] = []
        else:
            parent = next((row for row in document['methods'] if row.get('version_id')), None)
            identity = parent['method_id'] if parent else 'fixture-' + digest(document['goal'])[:12]
            version = parent['version'] + 1 if parent else 1
            package = build_skill_package(skill_id=identity, version=version, kind='expression',
                files={'SKILL.md': '---\nname: meaningful-causality\ndescription: Stage an intervention before its result.\n---\n'
                    'Adapt the entities and the relation structure; preserve the intervention identity.'},
                runtime_requirements={'engines': ['html-svg']})
            result['methods'][0].update(schema='video.animation-method.v2', skill_package=package,
                                       research_record_ids=[row['id'] for row in document['research_records']])
            if parent:
                result['methods'][0].update(method_id=identity, parent_version_id=parent['version_id'])
        snapshot = document['knowledge_snapshot']
        if document['mode'] == 'plan' and snapshot is not None and not snapshot['pages']:
            result['knowledge_proposals'] = [dict(page_id='causal-identity', expected_revision=0,
                body=dict(title='Provisional causality', content='A requested goal suggests preserving intervention identity; '
                          'this is not evidence that the new source has rendered successfully.'),
                evidence_ids=[snapshot['experiences'][0]['id']], change_note='Synthetic brief-derived hypothesis.')]
        return result


@pytest.fixture
def work(tmp_path):
    creator, reviewer, renderer = SkillGateway(), SkillGateway(True), Renderer(tmp_path / 'renders')
    service = AnimationMethodService(tmp_path / 'lab', author_gateway=creator, review_gateway=reviewer, renderer=renderer)
    return service, creator, reviewer, renderer


def test_new_authorship_materializes_standard_skill_and_wiki_without_publishing(work):
    service, creator, reviewer, renderer = work
    first = start(service)
    initial = deepcopy(service.commissions.get('developer', first['id'])['frozen'])
    row = finish(service, first['id'])
    assert len(renderer.calls) == 1 and len(creator.submissions) == 2 and len(reviewer.submissions) == 1
    method = service.methods.get_method('developer', row['publication_method_version_ids'][0])
    assert method['schema'] == 'video.animation-method.v2' and method['status'] == 'candidate'
    assert method['skill_package']['files']['SKILL.md'].startswith('---\n')
    assert 'skill_package' not in row['methods'][0] and row['methods'][0]['skill_package_sha256']
    assert not row['publication_ready'] and not service.published_catalogue('developer')['methods']
    page = service.knowledge.get_page('developer', 'causal-identity')
    assert page['status'] == 'provisional'
    assert service.knowledge.get_snapshot('developer', initial['knowledge_snapshot_id'])['pages'] == []
    run = service.commissions.get('developer', row['id'])
    create = next(op for op in run['operations'] if op['input'].get('mode') == 'create')
    assert create['input']['knowledge_snapshot']['pages'][0]['sha256'] == page['sha256']
    experiences = service.knowledge.list_experiences('developer')
    assert {'brief', 'authored_proposal', 'sampled_review', 'render', 'knowledge_proposal'} <= {
        record['kind'] for record in experiences}
    before = len(experiences), len(creator.submissions), len(reviewer.submissions), len(renderer.calls)
    for _ in range(2):
        service.get('developer', row['id'])
        service.knowledge_catalogue('developer')
    assert before == (len(service.knowledge.list_experiences('developer')), len(creator.submissions),
                      len(reviewer.submissions), len(renderer.calls))


def test_reference_free_transfer_and_exact_human_acceptances_are_required_for_release(work):
    service, _, _, _ = work
    first = finish(service, start(service)['id'])
    transfer_body = dict(request_id=str(uuid4()), goal='Explain allocation among several competing branches',
        structural_changes={'relations': {'before': 'a causal chain', 'after': 'competing branches'}},
        content_changes=dict(before=first['goal'], after='Explain allocation among several competing branches',
                             reason='Different explanatory problem and entities.'),
        budget=dict(max_rounds=1, max_seconds=300))
    transferred = finish(service, service.transfer('developer', first['id'], transfer_body)['id'])
    run = service.commissions.get('developer', transferred['id'])
    authored = next(op for op in run['operations'] if op['input'].get('mode') == 'create')
    assert authored['input']['knowledge_snapshot'] is None
    assert authored['input']['research_records'] == [] and authored['input']['reference'] is None
    assert authored['input']['base_source'] is None and authored['result']['methods'] == []
    first = service.get('developer', first['id'])
    method_id = first['publication_method_version_ids'][0]
    publish = dict(request_id=str(uuid4()), method_version_ids=[method_id], demonstration_ids=[
        first['candidates'][0]['demonstration_id'], transferred['candidates'][0]['demonstration_id']])
    with pytest.raises(ServiceError, match='publication_blocked'):
        service.publish('developer', first['id'], publish)
    assert all(row['acceptance'] is None for row in service.methods.list_validations('developer', method_id))
    for validation in service.methods.list_validations('developer', method_id):
        demo = service.methods.get_demonstration('developer', validation['demonstration_version_id'])
        human = dict(criteria_ids=[row['id'] for row in first['criteria']], motion_watched=True,
            audio_listened=False, validation_target_sha256=validation['acceptance_target_sha256'],
            media_sha256=demo['media']['sha256'])
        if validation['role'] == 'transfer':
            assert validation['reference_removal']['input_receipt']['operation_id'] == authored['id']
            assert validation['reference_removal']['input_document'] == authored['input']
            assert validation['reference_removal']['resource_manifest'] == authored['input_resource_manifest']
            human.update(content_and_structure_changed=True, reference_free_reuse=True)
        service.accept_validation('developer', validation['id'], dict(request_id=str(uuid4()),
            method_content_sha256=validation['method_content_sha256'], comment='UNIT TEST explicit developer decision',
            human_verification=human))
    assert service.publication_readiness('developer', method_id)['ready']
    published = service.publish('developer', first['id'], publish)
    assert published['methods'][0]['publication']['schema'] == 'video.animation-method-publication.v2'
    catalog = service.published_catalogue('developer')
    assert 'skill_package' not in catalog['methods'][0]
    assert catalog['methods'][0]['skill_package_sha256'] == published['methods'][0]['skill_package_sha256']
    assert service.get_method_version('developer', method_id)['skill_package']


def test_failed_render_and_feedback_are_kept_as_real_experiences_without_new_paid_work(work):
    service, _, _, renderer = work
    successful = finish(service, start(service)['id'])
    feedback = service.feedback('developer', successful['id'], dict(request_id=str(uuid4()),
        candidate_id=successful['best_candidate_id'], comment='UNIT TEST: relation appears too early.'))
    assert any(row['source_id'] == feedback['id'] for row in service.knowledge.list_experiences('developer'))
    renderer.failed = True
    failed = finish(service, start(service, goal='A distinct synthetic failure case')['id'])
    assert failed['state'] == 'failed'
    assert any(row['kind'] == 'render' and row['document']['technical']['status'] == 'failed'
               for row in service.knowledge.list_experiences('developer'))


def test_reader_and_combined_ledger_budget_are_checked_before_paid_reservation(tmp_path):
    store = AnimationMethodCommissionStore(tmp_path / 'commissions.sqlite3')
    run = store.create('fixture-owner', dict(goal='Synthetic budget boundary',
        budget=dict(creator=1, review=0, render=0, seconds=300)), 'fixture-budget')
    oversized = {'schema': protocol.INPUT_SCHEMA, 'padding': 'x' * (2 * 1024 * 1024)}
    with pytest.raises(ServiceError, match='operation_budget_exceeded'):
        store.reserve('fixture-owner', run['id'], 'creator', oversized, 'oversize')
    assert store.get('fixture-owner', run['id'])['usage']['creator'] == 0
    document = {'schema': protocol.INPUT_SCHEMA, 'padding': 'x' * (protocol.INPUT_MAX_BYTES - 1024)}
    operation = store.reserve('fixture-owner', run['id'], 'creator', document, 'within-bound')
    store.claim('fixture-owner', operation['id'])
    result = {'payload': 'y' * (protocol.OUTPUT_MAX_BYTES - 1024)}
    assert store.complete('fixture-owner', operation['id'], result)['result'] == result


def test_large_frozen_wiki_is_rejected_before_creating_or_submitting_commission(work):
    service, creator, reviewer, renderer = work
    for index in range(10):
        service.knowledge.record_experience('developer', 'large_fixture', str(index), {'text': 'x' * 230000})
    with pytest.raises(ServiceError, match='input_too_large'):
        start(service)
    assert creator.submissions == reviewer.submissions == renderer.calls == []
    assert service.commissions.list_all('developer') == []


def test_feedback_and_withdrawal_archives_recover_without_reusing_withdrawn_wiki_evidence(work, monkeypatch):
    service, creator, reviewer, renderer = work
    row = finish(service, start(service)['id'])
    original_record = service.knowledge.record_experience
    def interrupted_feedback(owner, kind, source_id, document):
        if kind == 'developer_feedback':
            raise OSError('Synthetic crash after feedback metadata commit')
        return original_record(owner, kind, source_id, document)
    body = dict(request_id=str(uuid4()), candidate_id=row['best_candidate_id'],
                comment='UNIT TEST feedback subsequently withdrawn by the same developer.')
    monkeypatch.setattr(service.knowledge, 'record_experience', interrupted_feedback)
    with pytest.raises(OSError):
        service.feedback('developer', row['id'], body)
    monkeypatch.setattr(service.knowledge, 'record_experience', original_record)
    feedback = service.feedback('developer', row['id'], body)
    experience = next(record for record in service.knowledge.list_experiences('developer')
                      if record['kind'] == 'developer_feedback' and record['source_id'] == feedback['id'])
    derived = service.knowledge.revise_page('developer', 'feedback-derived',
        dict(title='A feedback-dependent hypothesis', content='Depends on the exact opinion, not objective rendering proof.'),
        expected_revision=0, evidence_ids=[experience['id']], change_note='Synthetic Wiki dependency test.')
    frozen = service._knowledge_snapshot('developer')
    assert derived['page_id'] in {page['page_id'] for page in frozen['pages']}
    def interrupted_withdrawal(owner, kind, source_id, document):
        if kind == 'feedback_withdrawal':
            raise OSError('Synthetic crash after withdrawal metadata commit')
        return original_record(owner, kind, source_id, document)
    withdrawal = dict(request_id=str(uuid4()), feedback_id=feedback['id'])
    monkeypatch.setattr(service.knowledge, 'record_experience', interrupted_withdrawal)
    with pytest.raises(OSError):
        service.withdraw_feedback('developer', row['id'], withdrawal)
    monkeypatch.setattr(service.knowledge, 'record_experience', original_record)
    assert service.withdraw_feedback('developer', row['id'], withdrawal)['withdrawn'] is True
    current = service._knowledge_snapshot('developer')
    assert experience['id'] not in {record['id'] for record in current['experiences']}
    assert derived['page_id'] not in {page['page_id'] for page in current['pages']}
    assert any(record['kind'] == 'feedback_withdrawal' for record in current['experiences'])
    assert service.knowledge.get_snapshot('developer', frozen['id']) == frozen
    assert service.knowledge.get_page('developer', derived['page_id']) == derived
    assert service.knowledge_page('developer', derived['page_id'])['evidence_issues'] == [experience['id']]
    assert next(page for page in service.knowledge_catalogue('developer')['pages']
                if page['page_id'] == derived['page_id'])['evidence_status'] == 'withdrawn_feedback'
    assert len(creator.submissions) == 2 and len(reviewer.submissions) == len(renderer.calls) == 1


def test_new_gateway_rejects_historical_unfinished_commission_without_reinterpreting_or_resubmitting(tmp_path):
    old_creator, old_review = Gateway(), Gateway(True)
    renderer = Renderer(tmp_path / 'renders')
    legacy = AnimationMethodService(tmp_path / 'lab', author_gateway=old_creator,
                                   review_gateway=old_review, renderer=renderer)
    row = start(legacy)
    creator, reviewer = SkillGateway(), SkillGateway(True)
    service = AnimationMethodService(legacy.root, author_gateway=creator, review_gateway=reviewer, renderer=renderer)
    assert service.get('developer', row['id'])['execution'] == []
    with pytest.raises(ServiceError, match='historical_gateway_required'):
        service.advance('developer', row['id'])
    assert creator.submissions == reviewer.submissions == renderer.calls == []
    assert service.commissions.get('developer', row['id'])['usage']['creator'] == 0


def test_changed_uploaded_inventory_is_rejected_before_claim_or_paid_submission(work, monkeypatch):
    service, creator, reviewer, renderer = work
    row = start(service)
    service.advance('developer', row['id'])
    upload = creator.upload
    def changed(document, files):
        refs = upload(document, files)
        refs[0]['sha256'] = 'f' * 64
        return refs
    monkeypatch.setattr(creator, 'upload', changed)
    with pytest.raises(ServiceError, match='input_manifest_invalid'):
        service.advance('developer', row['id'])
    operation = service.commissions.get('developer', row['id'])['operations'][0]
    assert operation['status'] == 'reserved' and operation['job_id'] is None
    assert 'input_resource_manifest' not in operation
    assert creator.submissions == reviewer.submissions == renderer.calls == []


def test_validated_upload_inventory_survives_restart_between_upload_and_claim(work, monkeypatch):
    service, creator, reviewer, renderer = work
    row = start(service)
    service.advance('developer', row['id'])
    prepare = creator.prepare_submission
    def interrupted(*args, **kwargs):
        raise OSError('Synthetic crash after inventory commit and before admission claim')
    monkeypatch.setattr(creator, 'prepare_submission', interrupted)
    with pytest.raises(OSError):
        service.advance('developer', row['id'])
    original = service.commissions.get('developer', row['id'])['operations'][0]
    assert original['status'] == 'reserved' and not creator.submissions
    assert original['input_resource_manifest'] == [dict(path=protocol.INPUT_NAME,
                                                       sha256=original['input_sha256'])]
    monkeypatch.setattr(creator, 'prepare_submission', prepare)
    reopened = AnimationMethodService(service.root, author_gateway=creator,
        review_gateway=reviewer, renderer=renderer)
    reopened.advance('developer', row['id'])
    recovered = reopened.commissions.operation('developer', original['id'])
    assert recovered['input_resource_manifest'] == original['input_resource_manifest']
    assert recovered['input_resource_manifest_sha256'] == original['input_resource_manifest_sha256']
    assert recovered['status'] == 'submitted' and creator.submissions == [original['id']]
    assert len([event for event in reopened.commissions.events('developer', row['id'])
                if event['event'] == 'input_manifest_frozen']) == 1
