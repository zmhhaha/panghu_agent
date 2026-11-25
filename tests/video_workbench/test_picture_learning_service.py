"""Picture orchestration checks real PNG bytes, frozen inputs and paid boundaries."""
import base64
from copy import deepcopy
import hashlib
import io
import json
from uuid import uuid4

from PIL import Image
import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_picture_learning as protocol
from agent_service.profiles.video_animation_create import source_sha256
from video_workbench.picture_learning_service import PictureLearningService


def png(color=(40, 60, 80)):
    stream = io.BytesIO()
    Image.new('RGB', (320, 180), color).save(stream, format='PNG')
    return stream.getvalue()


def source():
    value = dict(engine='canvas', strategy=dict(description='Own geometry', tradeoffs='Static test'),
        files={'scene.html': '<canvas id="scene"></canvas><script src="/scene.js"></script>',
               'scene.js': 'window.seek=()=>{};'})
    value['source_sha256'] = source_sha256(value)
    return value


class Gateway:
    visual_input = True

    def __init__(self, *, scores=None):
        self.jobs, self.submissions, self.uploads, self.lookups, self.cancelled = {}, [], [], [], []
        self.scores = scores or [75]
        self.review_count = 0
        self.lose_submit = False
        self.active = False
        self.receipt_missing = False
        self.before_admit = None

    def upload(self, document, files):
        protocol.validate_input(document)
        assert files[protocol.INPUT_NAME] == canonical_bytes(document)
        assert all(hashlib.sha256(files[frame['file_name']]).hexdigest() == frame['sha256']
                   for frame in document['observation']['frames'])
        self.uploads.append((deepcopy(document), deepcopy(files)))
        return []

    def prepare_submission(self, operation_id, document, deadline_at, refs):
        if self.before_admit:
            self.before_admit(document)

        def submit():
            assert operation_id not in self.jobs
            self.submissions.append(operation_id)
            job = dict(job_id=uuid4().hex, state='running' if self.active else 'completed',
                       stop_proven=not self.active, document=deepcopy(document))
            self.jobs[operation_id] = job
            if self.lose_submit:
                raise ServiceError('service_response_lost', 503)
            return deepcopy(job)
        return submit

    def lookup(self, operation_id):
        self.lookups.append(operation_id)
        if operation_id not in self.jobs:
            raise ServiceError('job_not_found', 404)
        return deepcopy(self.jobs[operation_id])

    def _job(self, job_id):
        return next(job for job in self.jobs.values() if job['job_id'] == job_id)

    def poll(self, job_id):
        return deepcopy(self._job(job_id))

    def collect(self, job_id, input_sha256, document):
        result = dict(schema=protocol.RESULT_SCHEMA, input_sha256=input_sha256, mode=document['mode'],
                      source=None, review=None, transfer_task=None, method=None)
        if document['mode'] == 'create':
            result.update(source=source(), method=dict(technique='Layer original shapes with restrained contrast',
                evidence_scope='static_only', limitations=['Motion not tested']))
        else:
            score = self.scores[min(self.review_count, len(self.scores) - 1)]
            self.review_count += 1
            result['review'] = dict(score=score, summary='Actual reference and output compared.',
                issues=[] if score >= 80 else ['Contour needs improvement.'], stop_recommended=score >= 80)
            if document['work_kind'] == 'reproduction' or document['mode'] == 'transfer_plan':
                result['transfer_task'] = dict(goal='Change the subject silhouette and foreground relation.',
                    criteria=['New silhouette preserves the learned contrast technique.'])
        protocol.validate_result(result, input_sha256, document)
        return result

    def model_frame_receipt(self, job_id):
        frames = self._job(job_id)['document']['observation']['frames']
        return dict(images=[] if self.receipt_missing else [
            dict(sha256=frame['sha256'], model_request_completed=True) for frame in frames])

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        self._job(job_id).update(state='cancelled', stop_proven=True)


class Renderer:
    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.calls, self.recovered, self.results = [], [], {}
        self.fail = False
        self.lose_result = False

    def capabilities(self):
        return dict(allowed_engines=['canvas', 'html-svg'], assets={},
                    runtime_identity=dict(admission_id='test-only'))

    def render(self, operation_id, source_document, document, *, deadline_at=None):
        assert operation_id not in self.calls
        self.calls.append(operation_id)
        protocol.validate_source(source_document)
        if self.fail:
            result = dict(status='failed', technical_passed=False, stop_proven=True)
        else:
            directory = self.root / operation_id
            directory.mkdir()
            raw = png((len(self.calls), 20, 30))
            (directory / 'picture.png').write_bytes(raw)
            (directory / 'source.json').write_bytes(canonical_bytes(source_document))
            (directory / 'receipt.json').write_text('{"scope":"static_only"}', encoding='utf-8')
            result = dict(status='rendered', technical_passed=True, stop_proven=True,
                picture_path=str(directory / 'picture.png'), source_path=str(directory / 'source.json'),
                receipt_path=str(directory / 'receipt.json'), sha256=hashlib.sha256(raw).hexdigest(),
                width=320, height=180)
        self.results[operation_id] = result
        if self.lose_result:
            raise ServiceError('renderer_response_lost', 503)
        return deepcopy(result)

    def recover(self, operation_id):
        self.recovered.append(operation_id)
        return deepcopy(self.results.get(operation_id))

    lookup = recover


@pytest.fixture
def system(tmp_path):
    author, reviewer, renderer = Gateway(), Gateway(scores=[60, 85, 82]), Renderer(tmp_path / 'renders')
    service = PictureLearningService(tmp_path / 'picture', author_gateway=author,
        review_gateway=reviewer, renderer=renderer)
    return service, author, reviewer, renderer


def reference(service, owner='developer', request_id=None):
    return service.upload_reference(owner, dict(request_id=request_id or uuid4().hex,
        file_name='reference.png', mime_type='image/png', content_base64=base64.b64encode(png()).decode()))


def start(service, *, owner='developer', **overrides):
    ref = reference(service, owner)
    body = dict(request_id=uuid4().hex, title='Contour study', goal='Learn shape and material without copying layout.',
        reference_ids=[ref['id']], width=320, height=180, use_wikiskill=False, max_rounds=2, max_seconds=900)
    body.update(overrides)
    return service.start(owner, body), body


def finish(service, project, *, limit=60):
    for _ in range(limit):
        project = service.get('developer', project['id'])
        if project['state'] in {'completed', 'failed', 'cancelled'}:
            return project
        service.advance('developer', project['latest_experiment_id'])
    raise AssertionError(project)


def request_transfer(service, project, candidate_id=None, **overrides):
    candidate_id = candidate_id or next(item['id'] for item in project['candidates']
        if item['work_kind'] == 'reproduction' and (item.get('picture_sha256') or item.get('media_sha256')))
    body = dict(request_id=uuid4().hex, mode='transfer', base_candidate_id=candidate_id,
        approve_candidate=True, max_rounds=1, max_seconds=3600)
    body.update(overrides)
    return service.next_experiment('developer', project['id'], body)


def test_reproduction_then_independent_transfer_is_one_project_with_sources(system):
    service, author, reviewer, renderer = system
    project, _ = start(service)
    completed = finish(service, project)
    assert completed['state'] == 'completed'
    assert [item['work_kind'] for item in completed['candidates']] == ['reproduction', 'reproduction']
    assert completed['experiments'][0]['transfer_status'] == 'awaiting_approval'
    assert len(renderer.calls) == 2
    # The user's choice can differ from the internal higher-scored best.
    approved = completed['candidates'][0]
    assert approved['id'] != completed['best_candidate_id']
    completed = finish(service, request_transfer(service, completed, approved['id']))
    candidates = completed['candidates']
    assert [item['work_kind'] for item in candidates] == ['reproduction', 'reproduction', 'transfer']
    assert len(service.catalogue('developer')['projects']) == 1
    assert len(renderer.calls) == 3
    assert candidates[1]['id'] == completed['best_candidate_id']
    assert completed['adopted_candidate_id'] is None
    assert completed['verification_scope'] == 'static_only' and completed['published'] is False
    for candidate in candidates:
        picture = candidate['artifacts']['picture']
        path, mime = service.artifact('developer', project['id'], picture['id'])
        assert mime == 'image/png' and hashlib.sha256(path.read_bytes()).hexdigest() == picture['sha256']
        path, mime = service.artifact('developer', project['id'], candidate['artifacts']['source']['id'])
        assert json.loads(path.read_bytes())['source_sha256'] == candidate['source_sha256']
    assert author.uploads[0][0]['observation']['frames'][0]['purpose'] == 'reference'
    assert any(frame['purpose'] == 'baseline' for frame in author.uploads[1][0]['observation']['frames'])
    transferred = author.uploads[-1][0]
    assert transferred['work_kind'] == 'transfer' and transferred['base_source'] is None
    assert transferred['observation']['frames'] == []
    assert transferred['learned_method'] and transferred['knowledge_snapshot'] is None
    assert completed['experiments'][-1]['transfer_status'] == 'reviewed'
    assert completed['experiments'][-1]['frozen']['transfer_approval']['candidate_id'] == approved['id']


@pytest.mark.parametrize('use_wikiskill', [False, True])
def test_picture_transfer_keeps_runtime_api_without_original_goal_or_knowledge(
        system, tmp_path, monkeypatch, use_wikiskill):
    from video_workbench.ink_landscape_library import descriptor, runtime_guidance
    from test_ink_landscape_knowledge import _adapter, _published

    service, author, reviewer, renderer = system
    library = descriptor()
    capabilities = dict(renderer.capabilities(), assets=library['assets'],
        drawing_libraries={'ink-landscape': library})
    monkeypatch.setattr(renderer, 'capabilities', lambda: deepcopy(capabilities))
    knowledge_reads = []
    service.library_adapter = _adapter(tmp_path, _published(), knowledge_reads)
    original_goal = 'Learn the visible contour; this goal contains no library API documentation.'
    project, _ = start(service, goal=original_goal, use_wikiskill=use_wikiskill)
    completed = finish(service, project)
    completed = finish(service, request_transfer(service, completed))
    assert completed['state'] == 'completed'
    documents = [document for document, _ in author.uploads + reviewer.uploads]
    transfer = next(document for document, _ in author.uploads if document['work_kind'] == 'transfer')
    assert transfer['goal'] != original_goal and original_goal not in transfer['goal']
    assert transfer['base_source'] is None and transfer['observation']['frames'] == []
    for document in documents:
        assert document['constraints']['drawing_library_api'] == runtime_guidance()
        assert document['constraints']['drawing_libraries'] == {'ink-landscape': library}
        expected_knowledge = use_wikiskill and document['work_kind'] != 'transfer'
        assert document['knowledge_binding']['use_wikiskill'] is expected_knowledge
        assert (document['knowledge_snapshot'] is not None) is expected_knowledge
    assert bool(knowledge_reads) is use_wikiskill


def test_owner_isolation_idempotency_and_read_only_catalog(system):
    service, author, _, _ = system
    project, body = start(service)
    assert service.start('developer', body)['id'] == project['id']
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        service.start('developer', dict(body, goal='Different request'))
    assert service.catalogue('someone-else')['projects'] == []
    with pytest.raises(ServiceError) as error:
        service.get('someone-else', project['id'])
    assert error.value.status == 404
    ref = reference(service)
    with pytest.raises(ServiceError) as error:
        service.reference_artifact('someone-else', ref['id'])
    assert error.value.status == 404
    service.get('developer', project['id'])
    service.catalogue('developer')
    assert author.submissions == []


def test_unknown_admission_reconciles_original_operation_after_restart(system):
    service, author, reviewer, renderer = system
    author.lose_submit = True
    project, _ = start(service)
    eid = project['latest_experiment_id']
    service.advance('developer', eid)
    assert len(author.submissions) == 1
    assert service.get('developer', project['id'])['state'] == 'submission_unknown'
    recovered = PictureLearningService(service.root, author_gateway=author,
        review_gateway=reviewer, renderer=renderer)
    recovered.advance('developer', eid)
    recovered.advance('developer', eid)
    assert len(author.submissions) == 1 and len(author.lookups) == 1
    assert recovered.get('developer', project['id'])['experiments'][0]['stage'] == 'review'
    assert len(renderer.calls) == 1


def test_unknown_render_recovery_never_renders_twice(system):
    service, _, _, renderer = system
    renderer.lose_result = True
    project, _ = start(service)
    eid = project['latest_experiment_id']
    for _ in range(4):
        service.advance('developer', eid)
    detail = service.get('developer', project['id'])
    assert len(renderer.calls) == 1 and renderer.recovered
    assert detail['experiments'][0]['stage'] == 'review'


def test_stop_intent_is_checked_at_paid_admission_and_persists(system):
    service, author, _, _ = system
    project, _ = start(service)
    author.before_admit = lambda document: service.stop('developer', project['id'])
    eid = project['latest_experiment_id']
    service.advance('developer', eid)
    assert author.submissions == []
    stopped = service.advance('developer', eid)
    assert stopped['state'] == 'cancelled' and stopped['experiments'][0]['stop_proven']


def test_running_stop_waits_for_actual_stopped_job(system):
    service, author, _, _ = system
    author.active = True
    project, _ = start(service)
    eid = project['latest_experiment_id']
    service.advance('developer', eid)
    assert service.stop('developer', project['id'])['state'] == 'stopping'
    service.advance('developer', eid)
    assert service.get('developer', project['id'])['state'] == 'stopping'
    assert author.cancelled
    done = service.advance('developer', eid)
    assert done['state'] == 'cancelled' and done['experiments'][0]['stop_proven']


def test_missing_actual_author_image_receipt_fails_without_render(system):
    service, author, _, renderer = system
    author.receipt_missing = True
    project, _ = start(service)
    completed = finish(service, project)
    assert completed['state'] == 'failed'
    assert completed['experiments'][0]['stop_reason'] == 'invalid_result'
    assert renderer.calls == []


def test_fresh_excludes_baseline_and_feedback_is_next_experiment_only(system):
    service, author, _, _ = system
    project, _ = start(service)
    completed = finish(service, project)
    old_frozen = service.inspect('developer', project['id'], project['latest_experiment_id'])['frozen']
    new = service.next_experiment('developer', project['id'], dict(request_id=uuid4().hex,
        mode='fresh', feedback='Rebuild silhouette rather than add texture.', use_wikiskill=False))
    service.advance('developer', new['latest_experiment_id'])
    document = author.uploads[-1][0]
    assert document['base_source'] is None and document['learned_method'] is None
    assert document['feedback'] == 'Rebuild silhouette rather than add texture.'
    assert all(frame['purpose'] == 'reference' for frame in document['observation']['frames'])
    assert service.inspect('developer', project['id'], project['latest_experiment_id'])['frozen'] == old_frozen
    with pytest.raises(ServiceError, match='experiment_active'):
        service.next_experiment('developer', project['id'], dict(request_id=uuid4().hex, mode='fresh'))
    assert len(completed['experiments']) == 1


def test_adopted_default_does_not_follow_new_candidates(system):
    service, _, _, _ = system
    project, _ = start(service)
    completed = finish(service, project)
    chosen = completed['candidates'][0]['id']
    adopted = service.adopt('developer', project['id'], dict(candidate_id=chosen))
    assert adopted['adopted_candidate_id'] == chosen
    new = service.next_experiment('developer', project['id'], dict(request_id=uuid4().hex,
        mode='continue', feedback='Improve near contour.', use_wikiskill=False))
    assert new['experiments'][-1]['frozen']['base_candidate_id'] == chosen
    assert new['adopted_candidate_id'] == chosen


def test_adopt_waits_for_latest_experiment_to_finish(system):
    service, author, _, _ = system
    project, _ = start(service)
    for _ in range(12):
        current = service.advance('developer', project['latest_experiment_id'])
        run = current['experiments'][-1]
        if run['round'] == 2 and run['stage'] == 'author':
            break
    else:
        raise AssertionError(current)
    chosen = current['candidates'][0]['id']
    assert current['candidates'][0]['picture_sha256']
    author.active = True
    active = service.advance('developer', project['latest_experiment_id'])
    operation = active['experiments'][-1]['operations'][-1]
    assert operation['stage'] == 'author' and operation['status'] == 'submitted'
    assert author._job(operation['job_id'])['state'] == 'running'
    with pytest.raises(ServiceError, match='picture_learning_experiment_active') as error:
        service.adopt('developer', project['id'], dict(candidate_id=chosen))
    assert error.value.status == 409
    assert service.get('developer', project['id']) == active
    author._job(operation['job_id']).update(state='completed', stop_proven=True)
    author.active = False
    completed = finish(service, project)
    assert completed['state'] == 'completed' and completed['adopted_candidate_id'] is None
    count = len(author.submissions)
    adopted = service.adopt('developer', project['id'], dict(candidate_id=chosen))
    assert adopted['adopted_candidate_id'] == chosen
    assert len(author.submissions) == count


def test_technical_retry_restores_render_without_reauthoring(system):
    service, author, _, renderer = system
    renderer.fail = True
    project, _ = start(service)
    failed = finish(service, project)
    assert failed['state'] == 'failed' and failed['experiments'][0]['stage'] == 'render'
    authored_count = len(author.submissions)
    renderer.fail = False
    retried = service.next_experiment('developer', project['id'], dict(request_id=uuid4().hex, mode='retry'))
    assert retried['experiments'][-1]['stage'] == 'render'
    service.advance('developer', retried['latest_experiment_id'])
    assert len(author.submissions) == authored_count
    assert retried['experiments'][-1]['frozen_sha256'] == failed['experiments'][0]['frozen_sha256']


def test_original_reference_and_source_are_immutable_and_inspection_is_exact(system):
    service, _, _, _ = system
    ref = reference(service)
    path, mime = service.reference_artifact('developer', ref['id'])
    assert mime == 'image/png' and path.read_bytes() == png()
    project, _ = start(service)
    service.advance('developer', project['latest_experiment_id'])
    inspection = service.inspect('developer', project['id'], project['latest_experiment_id'])
    assert inspection['model_understanding_verified'] is False
    op = inspection['operations'][0]
    assert digest(op['input']) == op['input_sha256']
    path.write_bytes(b'tampered')
    with pytest.raises(ServiceError, match='artifact_changed'):
        service.reference_artifact('developer', ref['id'])


def test_reference_bytes_validated_before_any_paid_call(system):
    service, author, _, _ = system
    with pytest.raises(ServiceError, match='reference_format'):
        service.upload_reference('developer', dict(request_id=uuid4().hex, file_name='fake.png',
            mime_type='image/png', content_base64=base64.b64encode(b'not a PNG').decode()))
    with pytest.raises(ServiceError, match='reference_encoding'):
        service.upload_reference('developer', dict(request_id=uuid4().hex, file_name='fake.png',
            mime_type='image/png', content_base64='not base64!'))
    assert author.submissions == []


def test_runtime_and_binding_are_frozen_for_entire_experiment(system):
    service, author, _, renderer = system
    project, _ = start(service)
    renderer.capabilities = lambda: dict(allowed_engines=['three'], assets={'different.js': 'changed'},
                                        runtime_identity=dict(admission_id='later-version'))
    service.advance('developer', project['latest_experiment_id'])
    document = author.uploads[-1][0]
    assert document['constraints']['allowed_engines'] == ['canvas', 'html-svg']
    assert document['constraints']['runtime_identity']['admission_id'] == 'test-only'
    assert document['knowledge_binding']['use_wikiskill'] is False
    assert document['knowledge_snapshot'] is None


def test_manual_release_capture_is_exact_static_records_and_never_executes(system):
    from agent_service.animation_wikiskill_contract import make_snapshot, validate_snapshot
    service, author, reviewer, renderer = system
    assert service.capture_wikiskill_records('developer') == []
    project, _ = start(service)
    finish(service, project)
    before = len(author.submissions), len(reviewer.submissions), len(renderer.calls)
    records = service.capture_wikiskill_records('developer')
    snapshot = make_snapshot('developer', records)
    assert validate_snapshot(snapshot) == snapshot
    works = [row['content'] for row in records if row['kind'] == 'picture_work']
    assert len(works) == 2 and all(row['verification_scope'] == 'static_only' for row in works)
    assert all(row['motion_verified'] is False and row['audio_verified'] is False for row in works)
    assert service.capture_wikiskill_records('other') == []
    assert before == (len(author.submissions), len(reviewer.submissions), len(renderer.calls))


def test_no_progress_preserves_best_and_stops_reproduction_early(system):
    service, author, reviewer, _ = system
    reviewer.scores = [70, 65, 60, 60]
    project, _ = start(service, max_rounds=6)
    completed = finish(service, project)
    reproduction = [item for item in completed['candidates'] if item['work_kind'] == 'reproduction']
    assert len(reproduction) == 3
    assert completed['best_candidate_id'] == reproduction[0]['id']
    assert completed['experiments'][0]['reproduction_stop_reason'] == 'no_progress'
    # A worse candidate does not replace the internally preferred earlier one.
    baseline_reads = [frame for doc, _ in reviewer.uploads[1:3]
                      for frame in doc['observation']['frames'] if frame['purpose'] == 'baseline']
    assert baseline_reads


def test_unknown_missing_submission_cannot_be_retried_or_marked_stopped(system):
    service, author, _, renderer = system
    author.lose_submit = True
    project, _ = start(service)
    eid = project['latest_experiment_id']
    service.advance('developer', eid)
    author.jobs.clear()
    service.stop('developer', project['id'])
    for _ in range(3):
        service.advance('developer', eid)
    detail = service.get('developer', project['id'])
    assert detail['state'] == 'stopping' and detail['experiments'][0]['stop_proven'] is False
    assert len(author.submissions) == 1 and renderer.calls == []
    with pytest.raises(ServiceError, match='experiment_active'):
        service.next_experiment('developer', project['id'], dict(request_id=uuid4().hex, mode='retry'))


def test_budget_prefers_reproduction_and_marks_transfer_unverified(system):
    service, _, reviewer, _ = system
    reviewer.scores = [85]
    project, _ = start(service, max_rounds=1)
    eid = project['latest_experiment_id']
    # Reach submitted independent comparison, then use the remaining budget.
    for _ in range(4):
        service.advance('developer', eid)
    detail = service.get('developer', project['id'])
    assert detail['experiments'][0]['stage'] == 'review'
    assert reviewer.submissions
    service.clock = lambda: detail['experiments'][0]['deadline_at'] - 20
    completed = service.advance('developer', eid)
    assert completed['state'] == 'completed'
    assert len(completed['candidates']) == 1
    assert completed['experiments'][0]['transfer_status'] == 'awaiting_approval'
    assert completed['experiments'][0]['stop_reason'] == 'reproduction_complete'


def test_stop_preserves_already_completed_author_source_without_render(system):
    service, author, _, renderer = system
    project, _ = start(service)
    eid = project['latest_experiment_id']
    service.advance('developer', eid)
    service.stop('developer', project['id'])
    stopped = service.advance('developer', eid)
    assert stopped['state'] == 'cancelled'
    assert len(author.submissions) == 1 and renderer.calls == []
    candidate = stopped['candidates'][0]
    path, mime = service.artifact('developer', project['id'], candidate['artifacts']['source']['id'])
    assert mime == 'application/json' and json.loads(path.read_bytes())['source_sha256']


def test_explicit_visual_comparison_can_keep_baseline_despite_higher_new_score(system):
    service, _, reviewer, _ = system
    reviewer.scores = [75, 95, 85]
    collect = reviewer.collect

    def compare(job_id, input_sha256, document):
        value = collect(job_id, input_sha256, document)
        if any(frame['purpose'] == 'baseline' for frame in document['observation']['frames']):
            value['review']['preferred'] = 'baseline'
        return value

    reviewer.collect = compare
    project, _ = start(service)
    completed = finish(service, project)
    reproduction = [candidate for candidate in completed['candidates'] if candidate['work_kind'] == 'reproduction']
    assert len(reproduction) == 2
    assert reproduction[1]['review']['score'] > reproduction[0]['review']['score']
    assert completed['best_candidate_id'] == reproduction[0]['id']
    assert completed['experiments'][0]['best_candidate_id'] == reproduction[0]['id']


def test_readiness_outage_does_not_upload_and_preparation_reuses_uploaded_inputs(system):
    service, author, _, _ = system
    project, _ = start(service)
    eid = project['latest_experiment_id']

    def unavailable(deadline):
        raise ServiceError('animation_profile_unavailable', 503)

    author.preflight = unavailable
    service.advance('developer', eid)
    assert author.uploads == [] and author.submissions == []
    author.preflight = lambda deadline: None
    prepare = author.prepare_submission
    author.prepare_submission = lambda *args: unavailable(args[2])
    service.advance('developer', eid)
    service.advance('developer', eid)
    assert len(author.uploads) == 1 and author.submissions == []
    author.prepare_submission = prepare
    service.advance('developer', eid)
    assert len(author.uploads) == 1 and len(author.submissions) == 1


def test_source_only_failed_candidate_cannot_be_continued_or_adopted(system):
    service, author, _, renderer = system
    renderer.fail = True
    project, _ = start(service)
    failed = finish(service, project)
    candidate_id = failed['candidates'][0]['id']
    with pytest.raises(ServiceError, match='baseline_not_rendered') as error:
        service.next_experiment('developer', project['id'], dict(request_id=uuid4().hex,
            mode='continue', base_candidate_id=candidate_id, use_wikiskill=False))
    assert error.value.status == 409
    with pytest.raises(ServiceError, match='candidate_not_rendered') as error:
        service.adopt('developer', project['id'], dict(candidate_id=candidate_id))
    assert error.value.status == 409
    assert service.get('developer', project['id'])['state'] == 'failed'
    assert len(author.submissions) == 1
