"""Skill/Wiki orchestration using synthetic stopped jobs and inert media only."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.animation_skills_contract import build_skill_package
from agent_service.animation_knowledge_contract import MAX_EXPERIENCE_BYTES
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_skill_method as protocol
from video_workbench.animation_method_commissions import AnimationMethodCommissionStore
from video_workbench.animation_method_service import AnimationMethodService, _review_experience
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


@pytest.mark.parametrize('audio_criterion', [False, True])
def test_validation_listening_binds_exact_demo_and_method_not_other_media(work, monkeypatch, audio_criterion):
    service, _, reviewer, renderer = work
    # A frame-only reviewer cannot pass an audio criterion.
    reviewer.incomplete = audio_criterion
    original_render = renderer.render

    def render(*args, **kwargs):
        result = original_render(*args, **kwargs)
        # Synthetic reference has sound; synthetic transfer has no audio track.
        if len(renderer.calls) == 1:
            result['media']['audio_sha256'] = 'e' * 64
            renderer.results[args[0]] = deepcopy(result)
        return result

    monkeypatch.setattr(renderer, 'render', render)
    criteria = [dict(id='causality', description='Intervention precedes its consequence.',
                     requires_audio=audio_criterion)]
    reference = finish(service, start(service, criteria=criteria)['id'])
    reference_validation = next(row for row in reference['validations'] if row['role'] == 'reference')
    service.accept_validation('developer', reference_validation['id'], dict(request_id=str(uuid4()),
        method_content_sha256=reference_validation['method_content_sha256'],
        comment='UNIT TEST explicit acceptance of synthetic reference.', human_verification=dict(
            criteria_ids=['causality'], motion_watched=True, audio_listened=True,
            validation_target_sha256=reference_validation['acceptance_target_sha256'],
            media_sha256=reference_validation['media_sha256'])))
    transfer = finish(service, service.transfer('developer', reference['id'], dict(
        request_id=str(uuid4()), goal='Explain resource allocation among competing branches',
        structural_changes={'relations': {'before': 'a causal chain', 'after': 'competing branches'}},
        content_changes=dict(before=reference['goal'], after='Explain resource allocation among competing branches',
                             reason='Different entities and explanatory question.'),
        budget=dict(max_rounds=1, max_seconds=300)))['id'])
    detail = service.get('developer', reference['id'])
    reference_validation = next(row for row in detail['validations'] if row['role'] == 'reference')
    transfer_validation = next(row for row in detail['validations'] if row['role'] == 'transfer')
    assert reference_validation['audio_sha256'] == 'e' * 64
    assert reference_validation['requires_audio_verification'] is True
    assert transfer_validation['audio_sha256'] is None
    assert transfer_validation['requires_audio_verification'] is audio_criterion
    assert transfer_validation['media_sha256'] == transfer['candidates'][0]['media']['sha256']
    assert reference_validation['media_sha256'] != transfer_validation['media_sha256']
    assert detail['transfers'][0]['audio_sha256'] is None
    assert detail['transfers'][0]['requires_audio_verification'] is audio_criterion
    assert 'reference_removal' not in transfer_validation
    before = len(renderer.calls)
    service.get('developer', reference['id'])
    assert len(renderer.calls) == before


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
    historical = service.knowledge_page_revision('developer', derived['page_id'], 1)
    assert historical['page'] == derived and historical['current_revision'] == 1
    assert historical['evidence_issues'] == [experience['id']]
    assert next(page for page in service.knowledge_catalogue('developer')['pages']
                if page['page_id'] == derived['page_id'])['evidence_status'] == 'withdrawn_feedback'
    assert len(creator.submissions) == 2 and len(reviewer.submissions) == len(renderer.calls) == 1


def test_explicit_page_history_preserves_body_and_owner_without_execution_or_mutation(work):
    service, creator, reviewer, renderer = work
    evidence = service.knowledge.record_experience('developer', 'failure', 'fixture-history', {'error': 'Observed failure'})
    first = service.knowledge.revise_page('developer', 'draw:async',
        dict(title='Original interpretation', content='Original full body'), expected_revision=0,
        evidence_ids=[evidence['id']], change_note='Initial hypothesis.')
    second = service.knowledge.revise_page('developer', 'draw:async',
        dict(title='Corrected interpretation', content='Corrected full body'), expected_revision=1,
        evidence_ids=[evidence['id']], change_note='Correct after inspecting evidence.', change_kind='correct')
    for _ in range(2):
        historical = service.knowledge_page_revision('developer', 'draw:async', 1)
        assert historical == dict(page=first, current_revision=2, evidence_issues=[])
    assert service.knowledge_page('developer', 'draw:async')['page'] == second
    with pytest.raises(ServiceError, match='not_found'):
        service.knowledge_page_revision('another', 'draw:async', 1)
    assert len(service.knowledge.page_history('developer', 'draw:async')) == 2
    assert len(service.knowledge.list_experiences('developer')) == 1
    assert creator.submissions == reviewer.submissions == renderer.calls == []


def test_new_sampled_review_retains_diagnostics_with_idempotent_immutable_evidence(work, monkeypatch):
    service, creator, reviewer, renderer = work
    original_collect = reviewer.collect
    def detailed_review(*args):
        result = original_collect(*args)
        result['review']['remaining_issues'] = ['A label overlaps a moving object in the supplied image.']
        result['review']['next_guidance'] = 'Reserve protected label space and test the same sampled time.'
        return result
    monkeypatch.setattr(reviewer, 'collect', detailed_review)
    row = finish(service, start(service)['id'])
    run = service.commissions.get('developer', row['id'])
    operation = next(op for op in run['operations'] if op['stage'] == 'review')
    record = next(value for value in service.knowledge.list_experiences('developer')
                  if value['kind'] == 'sampled_review' and value['source_id'] == operation['id'])
    for key in ('criteria_results', 'remaining_issues', 'next_guidance'):
        assert record['document'][key] == operation['result']['review'][key]
    assert record['document']['remaining_issues'] and record['document']['next_guidance']
    assert record['document']['scope'] == 'sampled_images_only'
    assert record['document']['observation']['motion_observed'] is False
    assert record['document']['observation']['audio_observed'] is False
    frozen = service.knowledge.snapshot('developer', page_ids=[], experience_ids=[record['id']])
    unchanged = canonical_bytes(record)
    before = (len(service.knowledge.list_experiences('developer')), len(creator.submissions),
              len(reviewer.submissions), len(renderer.calls))
    for _ in range(2):
        service._sync_knowledge('developer', run)
    assert canonical_bytes(service.knowledge.get_experience('developer', record['id'])) == unchanged
    assert service.knowledge.get_snapshot('developer', frozen['id']) == frozen
    assert before == (len(service.knowledge.list_experiences('developer')), len(creator.submissions),
                      len(reviewer.submissions), len(renderer.calls))


def test_existing_sampled_review_and_frozen_snapshot_are_not_backfilled(work, monkeypatch):
    service, creator, reviewer, renderer = work
    original_archive = service._experience
    fields = {'criteria_results', 'remaining_issues', 'next_guidance'}
    def legacy_archive(owner, kind, source_id, document):
        if kind == 'sampled_review':
            document = {key: value for key, value in document.items() if key not in fields}
        return original_archive(owner, kind, source_id, document)
    monkeypatch.setattr(service, '_experience', legacy_archive)
    row = finish(service, start(service)['id'])
    monkeypatch.setattr(service, '_experience', original_archive)
    record = next(value for value in service.knowledge.list_experiences('developer') if value['kind'] == 'sampled_review')
    assert not fields & set(record['document'])
    frozen = service.knowledge.snapshot('developer', page_ids=[], experience_ids=[record['id']])
    unchanged = canonical_bytes(record)
    before = (len(service.knowledge.list_experiences('developer')), len(creator.submissions),
              len(reviewer.submissions), len(renderer.calls))
    run = service.commissions.get('developer', row['id'])
    for _ in range(2):
        service._sync_knowledge('developer', run)
    assert canonical_bytes(service.knowledge.get_experience('developer', record['id'])) == unchanged
    assert service.knowledge.get_snapshot('developer', frozen['id']) == frozen
    assert before == (len(service.knowledge.list_experiences('developer')), len(creator.submissions),
                      len(reviewer.submissions), len(renderer.calls))


@pytest.mark.parametrize('issue, count', [('x' * 8000, 33), ('\U0001f642' * 8000, 9)],
                         ids=['ascii-over-budget', 'utf8-over-budget'])
def test_valid_large_sampled_review_finishes_with_explicit_bounded_projection(work, monkeypatch, issue, count):
    service, creator, reviewer, renderer = work
    original_collect = reviewer.collect
    def large_review(job_id, checksum, document):
        result = original_collect(job_id, checksum, document)
        result['review']['remaining_issues'] = [issue] * count
        # The counterexample is admissible model output, including UTF-8 bytes.
        protocol.validate_result(result, checksum, document)
        return result
    monkeypatch.setattr(reviewer, 'collect', large_review)
    row = finish(service, start(service)['id'])
    assert row['state'] == 'completed'
    assert (len(creator.submissions), len(reviewer.submissions), len(renderer.calls)) == (2, 1, 1)
    run = service.commissions.get('developer', row['id'])
    operation = next(op for op in run['operations'] if op['stage'] == 'review')
    assert operation['status'] == 'completed'
    assert operation['result']['review']['remaining_issues'] == [issue] * count
    record = next(value for value in service.knowledge.list_experiences('developer')
                  if value['kind'] == 'sampled_review' and value['source_id'] == operation['id'])
    document = record['document']
    assert len(canonical_bytes(document)) <= MAX_EXPERIENCE_BYTES
    assert not {'criteria_results', 'remaining_issues', 'next_guidance'} & set(document)
    projection = document['diagnostics_projection']
    assert projection['completeness'] == 'structured_summary_only_not_full_diagnostics'
    assert projection['reason'] == 'complete_diagnostics_exceed_experience_byte_budget'
    assert projection['full_result_reference'] == dict(commission_id=row['id'], operation_id=operation['id'],
        result_sha256=operation['result_sha256'], json_pointer='/review',
        availability='operation_store_only_not_in_agent_inputs')
    for key in ('criteria_results', 'remaining_issues', 'next_guidance'):
        value = operation['result']['review'][key]
        expected = dict(sha256=digest(value), json_bytes=len(canonical_bytes(value)),
            **({'item_count': len(value)} if type(value) is list else {'character_count': len(value)}))
        assert projection['fields'][key] == expected
    assert projection['fields_not_inlined'] == ['criteria_results', 'remaining_issues', 'next_guidance']
    assert projection['experience_fields_not_inlined'] == []
    assert projection['criteria_results'] == [
        {key: value[key] for key in ('criterion_id', 'status')}
        for value in operation['result']['review']['criteria_results']]
    frozen = service.knowledge.snapshot('developer', page_ids=[], experience_ids=[record['id']])
    assert frozen['experiences'][0]['document']['diagnostics_projection'] == projection
    unchanged = canonical_bytes(record)
    before = (len(service.knowledge.list_experiences('developer')), len(creator.submissions),
              len(reviewer.submissions), len(renderer.calls))
    for _ in range(2):
        service._sync_knowledge('developer', run)
    assert canonical_bytes(service.knowledge.get_experience('developer', record['id'])) == unchanged
    assert service.knowledge.get_snapshot('developer', frozen['id']) == frozen
    assert before == (len(service.knowledge.list_experiences('developer')), len(creator.submissions),
                      len(reviewer.submissions), len(renderer.calls))


def test_review_projection_can_fit_when_original_experience_has_no_spare_metadata_space():
    document = dict(commission_id='synthetic', operation_id='synthetic-review', result_sha256='a' * 64,
        goal='A finite goal.', summary='A finite summary.', observation={'limitations': []})
    padding = MAX_EXPERIENCE_BYTES - len(canonical_bytes(document))
    document['observation']['limitations'] = ['x' * (padding - 2)]
    assert len(canonical_bytes(document)) == MAX_EXPERIENCE_BYTES
    original = deepcopy(document)
    opinion = dict(criteria_results=[dict(criterion_id='meaning', status='failed', evidence='Not observed.')],
        remaining_issues=['A label is unclear.'], next_guidance='Preserve label contrast.')
    projected = _review_experience(document, opinion)
    assert len(canonical_bytes(projected)) <= MAX_EXPERIENCE_BYTES
    assert document == original
    projection = projected['diagnostics_projection']
    assert projection['experience_fields_not_inlined'] == [dict(field='observation',
        sha256=digest(original['observation']), json_bytes=len(canonical_bytes(original['observation'])))]
    assert projected['goal'] == original['goal'] and projected['summary'] == original['summary']
    assert 'observation' not in projected


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
