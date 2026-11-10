"""Durable bounded orchestration tests; fake executors never make model calls."""
from copy import deepcopy
import hashlib
import json
from uuid import UUID

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_create as creation
from agent_service.profiles import video_animation_review as visual
from video_workbench.animation_polish import AnimationPolishCoordinator
from video_workbench.animation_revision_store import AnimationRevisionStore
from video_workbench.animation_observation import load_bundle
from test_animation_review_gateway import PNG, visual_document, creation_document, result_document


def write_review_bundle(directory, document):
    inputs = directory / 'inputs'
    inputs.mkdir(parents=True)
    raw = canonical_bytes(document)
    (inputs / visual.REVIEW_INPUT_NAME).write_bytes(raw)
    for frame in document['observation']['frames']:
        (inputs / frame['file_name']).write_bytes(PNG)
    receipt = dict(schema='video.animation-observation-preparation.v1',
        profile=visual.REVIEW_PROFILE, input_name=visual.REVIEW_INPUT_NAME,
        input_sha256=hashlib.sha256(raw).hexdigest(), media_sha256=document['media']['sha256'],
        frames=2, total_image_bytes=2 * len(PNG), model_calls=0,
        observation_status='prepared_not_observed', input_directory='inputs')
    (directory / 'evidence.json').write_bytes(canonical_bytes(receipt))


class FakeGateway:
    def __init__(self, profile, issues=False):
        self.profile, self.issues = profile, issues
        self.calls, self.job, self.document, self.result = [], None, None, None
        self.lose_response = False
        self.lookup_missing = False
        self.receipt_change = None

    def preflight(self, deadline):
        self.calls.append(('preflight', deadline))

    def upload(self, document, files):
        self.calls.append(('upload', digest(document)))
        return [dict(input_id=str(UUID(int=77)), sha256=digest(document), path='test.json', role='test')]

    def submit(self, operation_id, document, deadline, refs):
        self.calls.append(('submit', operation_id))
        self.document = deepcopy(document)
        self.job = dict(job_id=str(UUID(int=700 if self.profile == visual.REVIEW_PROFILE else 701)),
                        state='queued', publication_authorized=False)
        if self.lose_response:
            raise ServiceError('service_unavailable', 503)
        return deepcopy(self.job)

    def lookup(self, operation_id):
        self.calls.append(('lookup', operation_id))
        if self.lookup_missing or self.job is None:
            raise ServiceError('job_not_found', 404)
        return deepcopy(self.job)

    def poll(self, job_id):
        self.calls.append(('poll', job_id))
        assert self.job['job_id'] == job_id
        return deepcopy(self.job)

    def complete(self):
        self.result = result_document(self.profile, self.document)
        if self.profile == visual.REVIEW_PROFILE and self.issues:
            self.result['issues'] = [dict(issue_id='visual-overlap', severity='major', kind='motion',
                start_seconds=1, end_seconds=2, frame_ids=['frame-1', 'frame-2'],
                problem='The two visible objects overlap.', change_target='Separate their trajectories.')]
        if self.profile == creation.PROFILE:
            self.result['base_source_sha256'] = self.document['base_source']['source_sha256']
            self.result['review_sha256'] = digest(self.document['review'])
        self.job['state'] = 'completed'

    def collect(self, job_id, input_sha256, document):
        self.calls.append(('collect', job_id))
        assert job_id == self.job['job_id'] and input_sha256 == digest(self.document) == digest(document)
        validator = visual.validate_review_result if self.profile == visual.REVIEW_PROFILE else creation.validate_creation_result
        return validator(self.result, input_sha256, document)

    def model_frame_receipt(self, job_id):
        self.calls.append(('model_frame_receipt', job_id))
        receipt = dict(schema='agent-model-image-evidence.v1', job_id=job_id,
            attempt_id=str(UUID(int=703)), request_sha256='c' * 64,
            evidence_scope='completed_upstream_transport',
            images=[dict(sha256=hashlib.sha256(PNG).hexdigest(), model_request_completed=True,
                model_request_sha256='d' * 64, media_type='image/png', byte_length=len(PNG),
                input_item_index=1, part_index=0)])
        if self.receipt_change:
            receipt.update(deepcopy(self.receipt_change))
        return receipt

    def count(self, action):
        return sum(name == action for name, _ in self.calls)


class FakeIsolatedRenderer:
    def __init__(self):
        self.calls, self.receipts = [], {}
        self.lose_response, self.never_record, self.passed = False, False, True
        self.changed_source = False

    def render(self, operation_id, source, creation_input):
        self.calls.append(('render', operation_id))
        receipt = dict(schema='video.animation-render-verification.v1', operation_id=operation_id,
            source_sha256=creation.source_sha256(source), technical_passed=self.passed,
            media_versions=[dict(version_id='render-r1', sha256='e' * 64)] if self.passed else [],
            checks=[dict(code='isolated-seek-replay', status='passed' if self.passed else 'failed')])
        if self.changed_source:
            receipt['source_sha256'] = '0' * 64
        if not self.never_record:
            self.receipts[operation_id] = receipt
        if self.lose_response or self.never_record:
            raise ServiceError('isolated_renderer_unavailable', 503)
        return deepcopy(receipt)

    def lookup(self, operation_id):
        self.calls.append(('lookup', operation_id))
        if operation_id not in self.receipts:
            raise ServiceError('render_not_found', 404)
        return deepcopy(self.receipts[operation_id])

    def count(self, action):
        return sum(name == action for name, _ in self.calls)


@pytest.fixture
def work(tmp_path):
    store = AnimationRevisionStore(tmp_path / 'automatic-polish.sqlite3')
    document = creation_document()
    initial = result_document(creation.PROFILE, document)
    base = dict(source_revision='base-r1', source_sha256=initial['source_sha256'],
                engine=initial['engine'], files=initial['files'])
    review_document = visual_document()
    review_document['source_sha256'] = base['source_sha256']
    bundle = tmp_path / 'review-evidence'
    write_review_bundle(bundle, review_document)
    lineage = store.create_lineage('alice', 'project-1', base['source_revision'], base['source_sha256'], 'draft-1')
    reviewer, creator = FakeGateway(visual.REVIEW_PROFILE, issues=True), FakeGateway(creation.PROFILE)
    renderer = FakeIsolatedRenderer()
    return dict(store=store, lineage=lineage, reviewer=reviewer, creator=creator, renderer=renderer,
        kwargs=dict(review_directory=bundle, creation_input=document, base_source=base, deadline_at=2000000000))


def coordinator(work):
    return AnimationPolishCoordinator(work['store'], work['reviewer'], work['creator'], work['renderer'])


def step(work):
    return coordinator(work).step(work['lineage']['id'], **work['kwargs'])


def finish_review(work):
    assert step(work)['state'] == 'submitted'
    work['reviewer'].complete()
    assert step(work)['state'] == 'stage_completed'


def finish_revision(work):
    finish_review(work)
    assert step(work)['stage'] == 'revision'
    work['creator'].complete()
    assert step(work)['state'] == 'stage_completed'


def test_submission_preparation_failure_does_not_consume_admission_claim(work):
    def unavailable(*args):
        raise ServiceError('animation_preflight_unavailable', 503)
    work['reviewer'].prepare_submission = unavailable
    with pytest.raises(ServiceError, match='animation_preflight_unavailable'):
        step(work)
    operation = work['store'].operation(work['lineage']['id'] + ':review')
    assert operation['status'] == 'reserved'
    assert work['reviewer'].count('submit') == 0


def test_prepared_admission_runs_only_after_the_persisted_claim(work):
    def prepare(*args):
        def admit():
            reopened = AnimationRevisionStore(work['store'].path)
            operation = reopened.operation(work['lineage']['id'] + ':review')
            assert operation['status'] == 'submitting'
            return work['reviewer'].submit(*args)
        return admit
    work['reviewer'].prepare_submission = prepare
    assert step(work)['state'] == 'submitted'
    assert work['reviewer'].count('submit') == 1


def test_one_independent_review_one_creator_revision_one_isolated_verification(work):
    finish_revision(work)
    result = step(work)
    assert result['state'] == 'completed'
    assert result['aesthetic_status'] == 'unreviewed'
    assert result['second_agent_review_performed'] is False
    assert result['media_versions'] == [dict(version_id='render-r1', sha256='e' * 64)]
    assert work['reviewer'].count('submit') == work['creator'].count('submit') == 1
    assert work['renderer'].count('render') == 1
    review, revision, verification = work['store'].operations(work['lineage']['id'])
    assert review['issues'] and review['result']['observation']['whole_video_watched'] is False
    assert review['result']['observation']['model_understanding_verified'] is False
    assert review['result']['observation']['model_evidence_scope'] == 'completed_upstream_transport'
    assert revision['result']['review_sha256'] == digest(review['result']['review'])
    assert revision['result']['base_source_sha256'] == work['kwargs']['base_source']['source_sha256']
    assert verification['revision_result_sha256'] == revision['result_sha256']
    # Reopening the ledger and pressing continue does not allocate fresh rounds.
    work['store'] = AnimationRevisionStore(work['store'].path)
    assert step(work)['state'] == 'completed'
    assert work['reviewer'].count('submit') == work['creator'].count('submit') == work['renderer'].count('render') == 1


def test_issue_free_review_verifies_original_source_without_a_creator_attempt(work):
    work['reviewer'].issues = False
    finish_review(work)
    result = step(work)
    assert result['state'] == 'completed'
    assert work['creator'].calls == []
    operations = work['store'].operations(work['lineage']['id'])
    assert [row['stage'] for row in operations] == ['review', 'verification']
    assert operations[-1]['result']['source_sha256'] == work['kwargs']['base_source']['source_sha256']


@pytest.mark.parametrize('stage', ['review', 'revision'])
def test_lost_admission_response_only_looks_up_the_original_operation_after_restart(work, stage):
    gateway = work['reviewer'] if stage == 'review' else work['creator']
    if stage == 'revision':
        finish_review(work)
    gateway.lose_response = True
    assert step(work)['state'] == 'submission_unknown'
    work['store'] = AnimationRevisionStore(work['store'].path)
    assert step(work)['state'] == 'reconciled'
    assert gateway.count('submit') == gateway.count('upload') == 1
    assert gateway.count('lookup') == 1


def test_empty_lookup_after_ambiguous_submission_never_permits_another_post(work):
    work['reviewer'].lose_response = True
    work['reviewer'].lookup_missing = True
    assert step(work)['state'] == 'submission_unknown'
    assert step(work)['state'] == step(work)['state'] == 'submission_unknown'
    assert work['reviewer'].count('submit') == 1
    assert work['reviewer'].count('lookup') == 2
    assert work['creator'].calls == []


def test_another_caller_cannot_submit_an_already_claimed_slot(work, monkeypatch):
    original = work['store'].mark_submitting
    def claimed_elsewhere(operation_id):
        value = original(operation_id)
        value['submission_claimed'] = False
        return value
    monkeypatch.setattr(work['store'], 'mark_submitting', claimed_elsewhere)
    assert step(work)['state'] == 'reconcile_required'
    assert work['reviewer'].count('submit') == 0
    assert work['store'].operations(work['lineage']['id'])[0]['status'] == 'submitting'
    assert step(work)['state'] == 'submission_unknown'
    assert work['reviewer'].count('lookup') == 1


@pytest.mark.parametrize('change', ['base', 'creation_context', 'review_manifest'])
def test_original_source_and_confirmed_context_are_frozen_before_review_submission(work, change):
    assert step(work)['state'] == 'submitted'
    if change == 'base':
        base = work['kwargs']['base_source']
        base['files']['scene.html'] += '<!-- changed -->'
        base['source_sha256'] = creation.source_sha256(base)
    elif change == 'creation_context':
        work['kwargs']['creation_input']['goal'] += ' and change the confirmed narration'
    else:
        directory = work['kwargs']['review_directory']
        document = visual_document()
        document['source_sha256'] = work['kwargs']['base_source']['source_sha256']
        document['goal'] += ' changed'
        raw = canonical_bytes(document)
        (directory / 'inputs' / visual.REVIEW_INPUT_NAME).write_bytes(raw)
        receipt = dict(schema='video.animation-observation-preparation.v1', profile=visual.REVIEW_PROFILE,
            input_name=visual.REVIEW_INPUT_NAME, input_sha256=hashlib.sha256(raw).hexdigest())
        (directory / 'evidence.json').write_bytes(canonical_bytes(receipt))
    with pytest.raises(ServiceError):
        step(work)
    assert work['reviewer'].count('submit') == 1
    assert work['creator'].calls == []


@pytest.mark.parametrize('profile', [visual.REVIEW_PROFILE, visual.REVIEW_SEQUENCE_PROFILE])
@pytest.mark.parametrize('status', ['reserved', 'submitting', 'submission_unknown', 'submitted'])
def test_same_review_document_cannot_switch_execution_role_before_any_io(work, profile, status):
    bundle = work['kwargs']['review_directory']
    receipt_path = bundle / 'evidence.json'
    receipt = json.loads(receipt_path.read_bytes())
    receipt['profile'] = profile
    receipt_path.write_bytes(canonical_bytes(receipt))
    work['reviewer'].profile = profile
    _, document, _ = load_bundle(bundle)
    base, context = work['kwargs']['base_source'], work['kwargs']['creation_input']
    versions = [dict(version_id=base['source_revision'], sha256=document['media']['sha256'],
        source_sha256=base['source_sha256'], creation_input_sha256=digest(context))]
    operation_id = work['lineage']['id'] + ':review'
    store = work['store']
    versions = store.review_media_versions(operation_id, versions, profile)
    store.reserve(work['lineage']['id'], 'review', operation_id, operation_id, digest(document), versions)
    if status != 'reserved':
        store.mark_submitting(operation_id)
    if status == 'submission_unknown':
        store.mark_submission_unknown(operation_id, 'fixture lost response')
    elif status == 'submitted':
        store.mark_submitted(operation_id, 'original-job')
    before = store.operations(work['lineage']['id']), store.events(work['lineage']['id'])
    changed = visual.REVIEW_SEQUENCE_PROFILE if profile == visual.REVIEW_PROFILE else visual.REVIEW_PROFILE
    receipt['profile'] = changed
    receipt_path.write_bytes(canonical_bytes(receipt))
    work['reviewer'].profile = changed
    assert load_bundle(bundle)[1] == document  # Only execution identity changed.
    with pytest.raises(ServiceError, match='animation_revision_idempotency_conflict'):
        step(work)
    assert work['reviewer'].calls == work['creator'].calls == work['renderer'].calls == []
    assert (store.operations(work['lineage']['id']), store.events(work['lineage']['id'])) == before


def test_legacy_unlabelled_reservation_remains_v1_and_can_continue_without_migration(work):
    _, document, _ = load_bundle(work['kwargs']['review_directory'])
    base, context = work['kwargs']['base_source'], work['kwargs']['creation_input']
    versions = [dict(version_id=base['source_revision'], sha256=document['media']['sha256'],
        source_sha256=base['source_sha256'], creation_input_sha256=digest(context))]
    operation_id = work['lineage']['id'] + ':review'
    store = work['store']
    store.reserve(work['lineage']['id'], 'review', operation_id, operation_id, digest(document), versions)
    assert step(work)['state'] == 'submitted'
    assert store.operation(operation_id)['media_versions'] == versions
    assert all('review_profile' not in row for row in store.operation(operation_id)['media_versions'])


@pytest.mark.parametrize('images', [[], [dict(sha256='0' * 64, model_request_completed=True)],
                                    [dict(sha256=hashlib.sha256(PNG).hexdigest(), model_request_completed=False)]])
def test_review_cannot_finish_from_supplied_frame_names_or_self_reported_reading(work, images):
    assert step(work)['state'] == 'submitted'
    work['reviewer'].complete()
    work['reviewer'].receipt_change = dict(images=images)
    result = step(work)
    assert result['state'] == 'failed'
    assert work['creator'].calls == []
    assert work['renderer'].calls == []


def test_definitive_failed_agent_job_consumes_its_allowance_and_stops_the_pipeline(work):
    assert step(work)['state'] == 'submitted'
    work['reviewer'].job['state'] = 'failed'
    assert step(work)['state'] == step(work)['state'] == 'failed'
    assert work['reviewer'].count('submit') == 1
    assert work['creator'].calls == []


def test_lost_render_response_uses_durable_lookup_without_a_second_render(work):
    finish_revision(work)
    work['renderer'].lose_response = True
    assert step(work)['state'] == 'verification_unknown'
    work['store'] = AnimationRevisionStore(work['store'].path)
    assert step(work)['state'] == 'completed'
    assert work['renderer'].count('render') == work['renderer'].count('lookup') == 1
    assert work['reviewer'].count('submit') == 1


def test_render_absence_after_an_ambiguous_call_does_not_reset_the_budget(work):
    finish_revision(work)
    work['renderer'].never_record = True
    assert step(work)['state'] == 'verification_unknown'
    assert step(work)['state'] == 'verification_unknown'
    assert work['renderer'].count('render') == work['renderer'].count('lookup') == 1
    assert work['reviewer'].count('submit') == work['creator'].count('submit') == 1


def test_failed_technical_check_does_not_start_a_second_review_or_revision(work):
    finish_revision(work)
    work['renderer'].passed = False
    assert step(work)['state'] == step(work)['state'] == 'failed'
    assert work['renderer'].count('render') == 1
    assert work['reviewer'].count('submit') == work['creator'].count('submit') == 1


def test_renderer_receipt_cannot_substitute_a_different_scene_source(work):
    finish_revision(work)
    work['renderer'].changed_source = True
    assert step(work)['state'] == step(work)['state'] == 'failed'
    operation = work['store'].operations(work['lineage']['id'])[-1]
    assert operation['result']['error'] == 'animation_polish_renderer_binding_mismatch'
    assert work['renderer'].count('render') == 1
    assert work['reviewer'].count('submit') == work['creator'].count('submit') == 1
