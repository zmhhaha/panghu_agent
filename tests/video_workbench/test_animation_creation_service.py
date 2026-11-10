"""Bounded business orchestration with fake execution adapters, never model calls."""
from copy import deepcopy
import hashlib
import json
import sqlite3
import time
from threading import Event, Thread, current_thread
from uuid import UUID, uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_create as creation
from agent_service.profiles import video_animation_review as visual
from video_workbench import animation_creation_service as service_module
from video_workbench import animation_experiments as experiments
from video_workbench.animation_creation_service import AnimationCreationService, LIMITS
from test_animation_review_gateway import PNG, result_document


class Gateway:
    def __init__(self, profile):
        self.profile = profile
        self.calls, self.jobs = [], {}
        self.lose_submit, self.lookup_missing = None, False
        self.issues = True
        self.active = False
        self.block = None

    def preflight(self, deadline):
        self.calls.append(('preflight', deadline))
        if self.block:
            self.block[0].set()
            assert self.block[1].wait(5)

    def upload(self, document, files):
        self.calls.append(('upload', digest(document)))
        if self.profile == visual.REVIEW_BATCH_PROFILE:
            assert all(files[frame['file_name']] == PNG for frame in document['observation']['frames'])
        return []

    def submit(self, operation_id, document, deadline, refs):
        self.calls.append(('submit', operation_id))
        self.jobs[operation_id] = dict(job_id=str(uuid4()), state='queued' if self.active else 'completed',
                                       document=deepcopy(document))
        if self.lose_submit and operation_id.endswith(self.lose_submit):
            raise ServiceError('service_unavailable', 503)
        return deepcopy(self.jobs[operation_id])

    def lookup(self, operation_id):
        self.calls.append(('lookup', operation_id))
        if self.lookup_missing or operation_id not in self.jobs:
            raise ServiceError('job_not_found', 404)
        return deepcopy(self.jobs[operation_id])

    def _job(self, job_id):
        return next(job for job in self.jobs.values() if job['job_id'] == job_id)

    def poll(self, job_id):
        self.calls.append(('poll', job_id))
        return deepcopy(self._job(job_id))

    def cancel(self, job_id):
        self.calls.append(('cancel', job_id))
        self._job(job_id)['state'] = 'cancelled'
        return self.poll(job_id)

    def collect(self, job_id, checksum, document):
        self.calls.append(('collect', job_id))
        assert digest(self._job(job_id)['document']) == checksum == digest(document)
        result = result_document(self.profile, document)
        if self.profile == creation.PROFILE:
            base = document['base_source']
            if base:
                result['files']['scene.html'] = result['files']['scene.html'].replace('<svg>', '<svg aria-label="revised">')
                result.update(source_sha256=creation.source_sha256(result),
                    files_sha256={name: hashlib.sha256(raw.encode()).hexdigest() for name, raw in result['files'].items()},
                    base_source_sha256=base['source_sha256'], review_sha256=digest(document['review']))
        else:
            result['observed_frame_ids'] = [row['frame_id'] for row in document['observation']['frames']]
            if self.issues:
                result['issues'] = [dict(issue_id='fixed-label-overlap', severity='major', kind='composition',
                    start_seconds=1, end_seconds=2, frame_ids=result['observed_frame_ids'],
                    problem='A moving object obscures fixed explanatory text.',
                    change_target='Separate motion and fixed explanation regions.')]
        return result

    def model_frame_receipt(self, job_id):
        document = self._job(job_id)['document']
        return dict(schema='agent-model-image-evidence.v1', job_id=job_id,
            attempt_id=str(UUID(int=103)), request_sha256='c' * 64,
            evidence_scope='completed_upstream_transport', images=[dict(
                sha256=frame['sha256'], model_request_completed=True, model_request_sha256='d' * 64,
                media_type='image/png', byte_length=len(PNG), input_item_index=index, part_index=0)
                for index, frame in enumerate(document['observation']['frames'])])

    def count(self, action):
        return sum(name == action for name, _ in self.calls)


class Renderer:
    def __init__(self, root):
        self.root = root
        self.root.mkdir()
        self.calls, self.receipts, self.paths = [], {}, {}
        self.lose_response, self.never_record = False, False
        self.failed = False

    def render(self, operation_id, source, creation_input):
        self.calls.append(('render', operation_id))
        assert creation.source_sha256(source) == source['source_sha256']
        if self.never_record:
            raise ServiceError('render_response_unknown', 503)
        path = self.root / (operation_id.replace(':', '-') + '.mp4')
        path.write_bytes(b'fake-media-for-business-unit-test:' + operation_id.encode())
        self.paths[operation_id] = path
        receipt = dict(schema='video.animation-render-verification.v1', operation_id=operation_id,
            source_sha256=source['source_sha256'], technical_passed=not self.failed,
            media_versions=[] if self.failed else [dict(version_id='render', sha256=hashlib.sha256(path.read_bytes()).hexdigest())],
            checks=[dict(code='unit-test-only', status='failed' if self.failed else 'passed')])
        self.receipts[operation_id] = receipt
        if self.lose_response:
            raise ServiceError('render_response_unknown', 503)
        return deepcopy(receipt)

    def lookup(self, operation_id):
        self.calls.append(('lookup', operation_id))
        if operation_id not in self.receipts:
            raise ServiceError('render_not_found', 404)
        return deepcopy(self.receipts[operation_id])

    def media_path(self, operation_id):
        return self.paths[operation_id]

    def count(self, action):
        return sum(name == action for name, _ in self.calls)


def fake_prepare(media, directory, *, clip_id, goal, source_sha256, sampling_layers, frame_limit, review_profile):
    """A fake decoder for orchestration tests, explicitly not real media evidence."""
    assert media.read_bytes().startswith(b'fake-media-for-business-unit-test:')
    assert review_profile == visual.REVIEW_BATCH_PROFILE and frame_limit == 24
    inputs = directory / 'inputs'
    inputs.mkdir(parents=True)
    document = dict(schema=visual.REVIEW_INPUT_SCHEMA, clip_id=clip_id, goal=goal,
        source_sha256=source_sha256, media=dict(sha256=hashlib.sha256(media.read_bytes()).hexdigest(),
            duration_seconds=8, width=1280, height=720), prompt_revision='fake-decoder-r1',
        observation=dict(coverage='sampled_frames', audio_available=False,
            windows=[dict(start_seconds=0, end_seconds=8)], limitations=['UNIT TEST fake decoder.'],
            frames=[dict(frame_id=f'frame-{i}', file_name=f'frame-{i}.png', time_seconds=i,
                         sha256=hashlib.sha256(PNG).hexdigest()) for i in (1, 2)]))
    raw = canonical_bytes(document)
    (inputs / visual.REVIEW_INPUT_NAME).write_bytes(raw)
    for frame in document['observation']['frames']:
        (inputs / frame['file_name']).write_bytes(PNG)
    (directory / 'evidence.json').write_bytes(canonical_bytes(dict(
        schema='video.animation-observation-preparation.v1', profile=review_profile,
        input_name=visual.REVIEW_INPUT_NAME, input_sha256=digest(document),
        media_sha256=document['media']['sha256'], frames=2, total_image_bytes=2 * len(PNG),
        model_calls=0, observation_status='prepared_not_observed', input_directory='inputs')))


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, 'prepare_temporal_media', fake_prepare)
    creator = Gateway(creation.PROFILE)
    reviewer = Gateway(visual.REVIEW_BATCH_PROFILE)
    renderer = Renderer(tmp_path / 'renderer')
    root = tmp_path / 'new-service'
    svc = AnimationCreationService(root, creation_gateway=creator, review_gateway=reviewer, renderer=renderer)
    body = dict(request_id=str(uuid4()), experiment_id='identity-tracking',
        definition_identity=experiments.definition()['identity'], input_role='development')
    run = svc.start('alice', body)
    return dict(svc=svc, creator=creator, reviewer=reviewer, renderer=renderer, root=root, body=body, run=run)


def advance(work, count=1):
    for _ in range(count):
        work['run'] = work['svc'].advance('alice', work['run']['id'])
    return work['run']


def reopen(work):
    work['svc'] = AnimationCreationService(work['root'], creation_gateway=work['creator'],
        review_gateway=work['reviewer'], renderer=work['renderer'])


def complete(work):
    for _ in range(20):
        if work['run']['state'] in {'completed', 'failed', 'cancelled'}:
            break
        advance(work)
    assert work['run']['state'] == 'completed', work['run']
    return work['run']


def test_full_chain_is_one_initial_one_review_one_revision_and_two_renders(work):
    run = complete(work)
    assert run['limits'] == LIMITS
    assert [version['id'] for version in run['versions']] == ['original', 'revised']
    assert all(v['technical_status'] == 'passed' and v['media_url'].startswith('/api/creation-runs/')
               for v in run['versions'])
    assert work['creator'].count('submit') == 2
    assert work['reviewer'].count('submit') == 1
    assert work['renderer'].count('render') == 2
    assert run['review']['issues'][0]['issue_id'] == 'fixed-label-overlap'
    assert run['review']['observation']['model_understanding_verified'] is False
    assert run['aesthetic_status'] == 'unreviewed'
    initial, revised = [job['document'] for job in work['creator'].jobs.values()]
    assert initial['base_source'] is None and revised['base_source']['source_revision'] == 'original'
    assert revised['review']['issues'] == run['review']['issues']
    assert initial['goal'] == revised['goal'] and initial['guidance'] == revised['guidance']
    assert 'fixed explanations' in initial['guidance']
    assert run['request_id'] == work['body']['request_id'] and run['input_role'] == 'development'
    assert 'owner' not in run and 'frozen' not in run
    assert work['svc'].pending() == []
    calls = (len(work['creator'].calls), len(work['reviewer'].calls), len(work['renderer'].calls))
    reopen(work)
    for _ in range(3):
        work['svc'].get('alice', run['id'])
        work['svc'].list('alice')
        advance(work)
    assert calls == (len(work['creator'].calls), len(work['reviewer'].calls), len(work['renderer'].calls))


def test_issue_free_review_does_not_allocate_revision(work):
    work['reviewer'].issues = False
    assert [v['id'] for v in complete(work)['versions']] == ['original']
    assert work['creator'].count('submit') == work['reviewer'].count('submit') == 1
    assert work['renderer'].count('render') == 2


def test_reads_refresh_and_idempotent_start_do_not_submit_or_rebind_inputs(work, monkeypatch):
    run = work['run']
    monkeypatch.setattr(experiments, 'definition', lambda *args: (_ for _ in ()).throw(AssertionError('registry changed')))
    assert work['svc'].start('alice', deepcopy(work['body']))['id'] == run['id']
    assert work['svc'].get('alice', run['id'])['state'] == 'queued'
    assert work['svc'].list('alice', 'identity-tracking') == [run]
    assert work['svc'].pending() == [dict(owner='alice', id=run['id'])]
    assert work['creator'].calls == work['reviewer'].calls == work['renderer'].calls == []
    advance(work)
    assert work['creator'].count('submit') == 1


def test_same_key_changed_body_conflicts_and_owner_scope_is_enforced(work):
    body = dict(work['body'], input_role='held_out')
    with pytest.raises(ServiceError, match='animation_creation_idempotency_conflict'):
        work['svc'].start('alice', body)
    assert work['svc'].list('bob') == []
    for operation in (work['svc'].get, work['svc'].advance, work['svc'].cancel):
        with pytest.raises(ServiceError) as error:
            operation('bob', work['run']['id'])
        assert error.value.status == 404
    with pytest.raises(ServiceError) as error:
        work['svc'].artifact('bob', work['run']['id'], 'original', 'source')
    assert error.value.status == 404


@pytest.mark.parametrize('stage', ['initial_creation', ':review', ':revision'])
def test_unknown_agent_response_never_reposts_after_restart_or_missing_lookup(work, stage):
    if stage != 'initial_creation':
        advance(work, 3)  # initial create, collect and render
    if stage == ':revision':
        advance(work, 2)  # review submit and collect
    gateway = work['reviewer'] if stage == ':review' else work['creator']
    gateway.lose_submit = stage
    run = advance(work)
    assert run['state'] == 'submission_unknown'
    expected_posts = gateway.count('submit')
    gateway.lookup_missing = True
    for _ in range(3):
        reopen(work)
        assert advance(work)['state'] == 'submission_unknown'
    assert gateway.count('submit') == expected_posts
    assert gateway.count('lookup') == 3
    gateway.lookup_missing = False
    complete(work)
    assert work['creator'].count('submit') == 2 and work['reviewer'].count('submit') == 1


@pytest.mark.parametrize('verification', [False, True])
def test_unknown_render_only_looks_up_same_operation_after_restart(work, verification):
    advance(work, 3 if verification else 2)
    if verification:
        advance(work, 4)  # review and revision each submit+collect
    work['renderer'].lose_response = True
    run = advance(work)
    assert run['state'] == 'submission_unknown'
    count = work['renderer'].count('render')
    reopen(work)
    work['renderer'].lose_response = False
    complete(work)
    assert work['renderer'].count('render') == 2
    assert count == (2 if verification else 1)
    assert work['renderer'].count('lookup') >= 1


def test_missing_render_lookup_never_authorizes_render_retry(work):
    advance(work, 2)
    work['renderer'].never_record = True
    assert advance(work)['state'] == 'submission_unknown'
    for _ in range(3):
        reopen(work)
        assert advance(work)['state'] in {'submission_unknown', 'failed'}
    assert work['renderer'].count('render') == 1


def test_cancel_unknown_admission_reconciles_original_then_cancels_without_new_work(work):
    work['creator'].active = True
    work['creator'].lose_submit = 'initial_creation'
    assert advance(work)['state'] == 'submission_unknown'
    work['creator'].lookup_missing = True
    assert work['svc'].cancel('alice', work['run']['id'])['state'] == 'cancelling'
    reopen(work)
    work['creator'].lookup_missing = False
    assert advance(work)['state'] == 'cancelled'
    assert work['creator'].count('submit') == work['creator'].count('cancel') == 1
    assert work['renderer'].calls == work['reviewer'].calls == []
    assert advance(work)['state'] == 'cancelled'


@pytest.mark.parametrize('stage_steps', [0, 3, 4, 5, 6, 7])
def test_cancellation_is_durable_at_stage_boundaries_and_does_not_reopen_allowance(work, stage_steps):
    advance(work, stage_steps)
    before = (work['creator'].count('submit'), work['reviewer'].count('submit'), work['renderer'].count('render'))
    run = work['svc'].cancel('alice', work['run']['id'])
    assert run['state'] == 'cancelled'
    reopen(work)
    assert advance(work)['state'] == 'cancelled'
    assert before == (work['creator'].count('submit'), work['reviewer'].count('submit'), work['renderer'].count('render'))
    assert work['svc'].start('alice', work['body'])['state'] == 'cancelled'


def test_failed_render_is_terminal_and_preserves_source_without_adoption(work):
    advance(work, 2)
    work['renderer'].failed = True
    run = advance(work)
    assert run['state'] == 'failed' and run['aesthetic_status'] == 'unreviewed'
    assert work['svc'].artifact('alice', run['id'], 'original', 'source').is_file()
    with pytest.raises(ServiceError):
        work['svc'].artifact('alice', run['id'], 'original', 'media')
    reopen(work)
    advance(work, 3)
    assert work['renderer'].count('render') == 1
    assert work['reviewer'].calls == []


def test_original_and_revised_artifacts_keep_distinct_hashes_and_check_tampering(work):
    run = complete(work)
    paths = [work['svc'].artifact('alice', run['id'], version, 'media') for version in ('original', 'revised')]
    assert paths[0] != paths[1] and paths[0].read_bytes() != paths[1].read_bytes()
    paths[0].write_bytes(b'tampered')
    with pytest.raises(ServiceError, match='animation_creation_artifact_changed'):
        work['svc'].artifact('alice', run['id'], 'original', 'media')
    source = work['svc'].artifact('alice', run['id'], 'revised', 'source')
    source.write_bytes(b'{}')
    with pytest.raises(ServiceError, match='animation_creation_artifact_changed'):
        work['svc'].artifact('alice', run['id'], 'revised', 'source')


def test_cross_instance_advance_lock_and_cancel_before_admission(work):
    started, released = Event(), Event()
    work['creator'].block = (started, released)
    second = AnimationCreationService(work['root'], creation_gateway=work['creator'],
        review_gateway=work['reviewer'], renderer=work['renderer'])
    outcomes = []
    thread = Thread(target=lambda: outcomes.append(work['svc'].advance('alice', work['run']['id'])))
    thread.start()
    try:
        assert started.wait(3)
        assert second.advance('alice', work['run']['id'])['state'] == 'running'
        assert second.cancel('alice', work['run']['id'])['state'] == 'cancelling'
    finally:
        released.set()
        thread.join(5)
    assert not thread.is_alive() and outcomes[0]['state'] == 'cancelled'
    assert work['creator'].count('preflight') == 1 and work['creator'].count('submit') == 0


def test_inputs_are_frozen_and_old_experiment_database_is_not_used(work):
    before = sorted(path.name for path in work['root'].iterdir())
    assert before == ['creation-polish.sqlite3', 'creation-runs', 'creation-service.sqlite3']
    with sqlite3.connect(work['svc'].path) as db:
        row = json.loads(db.execute('SELECT document FROM creation_runs').fetchone()[0])
        row['frozen']['creation_input']['goal'] = 'tampered'
        db.execute('UPDATE creation_runs SET document=?', (json.dumps(row),))
    with pytest.raises(ServiceError, match='animation_creation_frozen_input_changed'):
        work['svc'].get('alice', work['run']['id'])
    assert work['creator'].calls == []


@pytest.mark.parametrize('polish', [False, True])
def test_crash_after_claim_before_post_never_retries_admission(work, polish):
    if polish:
        advance(work, 3)
    gateway = work['reviewer'] if polish else work['creator']

    def prepare(operation_id, *_args):
        def interrupted():
            if polish:
                assert work['svc'].revisions.operation(operation_id)['status'] == 'submitting'
            else:
                internal = work['svc']._read('alice', work['run']['id'])
                assert internal['operations']['initial_creation']['status'] == 'submitting'
            raise SystemExit('simulated process termination before HTTP POST')
        return interrupted

    gateway.prepare_submission = prepare
    with pytest.raises(SystemExit):
        advance(work)
    for _ in range(2):
        reopen(work)
        assert advance(work)['state'] == 'submission_unknown'
    assert gateway.count('submit') == 0 and gateway.count('lookup') == 2


def test_unknown_initial_render_cancellation_waits_for_original_identity_without_rerender(work):
    advance(work, 2)
    work['renderer'].never_record = True
    assert advance(work)['state'] == 'submission_unknown'
    assert work['svc'].cancel('alice', work['run']['id'])['state'] == 'cancelling'
    reopen(work)
    assert advance(work)['state'] == 'cancelling'
    assert work['renderer'].count('render') == 1
    assert work['reviewer'].count('submit') == 0


def test_cancellation_between_initial_render_claim_and_io_does_not_start_renderer(work, monkeypatch):
    advance(work, 2)
    save = work['svc']._save
    fired = []

    def cancel_after_claim(run):
        result = save(run)
        op = run['operations'].get('initial_render')
        if op and op['status'] == 'submitted' and not fired:
            fired.append(True)
            assert work['svc'].cancel('alice', run['id'])['state'] == 'cancelling'
        return result

    monkeypatch.setattr(work['svc'], '_save', cancel_after_claim)
    assert advance(work)['state'] == 'cancelled'
    assert work['renderer'].count('render') == 0


def test_cancellation_at_verification_render_boundary_finishes_claim_without_render(work, monkeypatch):
    advance(work, 7)
    submitted = work['svc'].revisions.mark_submitted

    def cancel_at_io_boundary(operation_id, job_id):
        result = submitted(operation_id, job_id)
        if operation_id.endswith(':verification'):
            assert work['svc'].cancel('alice', work['run']['id'])['state'] == 'cancelling'
        return result

    monkeypatch.setattr(work['svc'].revisions, 'mark_submitted', cancel_at_io_boundary)
    assert advance(work)['state'] == 'cancelled'
    assert work['renderer'].count('render') == 1
    internal = work['svc']._read('alice', work['run']['id'])
    assert work['svc'].revisions.operations(internal['lineage_id'])[-1]['status'] == 'cancelled'


def test_cancellation_during_final_render_is_retained_when_output_arrives(work, monkeypatch):
    advance(work, 7)
    render = work['renderer'].render

    def cancel_before_return(operation_id, source, document):
        if operation_id.endswith(':verification'):
            assert work['svc'].cancel('alice', work['run']['id'])['state'] == 'cancelling'
        return render(operation_id, source, document)

    monkeypatch.setattr(work['renderer'], 'render', cancel_before_return)
    assert advance(work)['state'] == 'cancelled'
    assert work['renderer'].count('render') == 2
    assert work['svc'].artifact('alice', work['run']['id'], 'original', 'media').is_file()


def test_cancellation_intent_does_not_overwrite_concurrently_finished_original_media(work, monkeypatch):
    advance(work, 2)
    snapshot_read, resume = Event(), Event()
    read = work['svc']._read
    snapshots, outcomes = [], []

    def pause_old_snapshot(owner, run_id):
        result = read(owner, run_id)
        if current_thread().name == 'creation-canceller-test' and not snapshots:
            snapshots.append(True)
            snapshot_read.set()
            assert resume.wait(5)
        return result

    monkeypatch.setattr(work['svc'], '_read', pause_old_snapshot)
    thread = Thread(name='creation-canceller-test',
        target=lambda: outcomes.append(work['svc'].cancel('alice', work['run']['id'])))
    thread.start()
    try:
        assert snapshot_read.wait(3)
        assert advance(work)['versions'][0]['technical_status'] == 'passed'
    finally:
        resume.set()
        thread.join(5)
    assert not thread.is_alive() and outcomes[0]['state'] == 'cancelled'
    assert outcomes[0]['versions'][0]['sha256']
    assert work['svc'].artifact('alice', work['run']['id'], 'original', 'media').is_file()
    assert work['renderer'].count('render') == 1 and work['reviewer'].count('submit') == 0


def test_supplied_frame_manifest_without_actual_model_receipt_cannot_drive_revision(work, monkeypatch):
    advance(work, 4)
    receipt = work['reviewer'].model_frame_receipt
    monkeypatch.setattr(work['reviewer'], 'model_frame_receipt', lambda job_id: dict(receipt(job_id), images=[]))
    run = advance(work)
    assert run['state'] == 'failed'
    assert run['error']['code'] == 'animation_model_frame_evidence_incomplete'
    assert work['creator'].count('submit') == 1
    assert work['renderer'].count('render') == 1


@pytest.mark.parametrize('change', [dict(input_role=[]), dict(request_id='not-uuid'), dict(extra=True),
                                   dict(definition_identity='0' * 64)])
def test_invalid_start_never_submits(work, change):
    with pytest.raises(ServiceError):
        work['svc'].start('alice', dict(work['body'], **change, request_id=str(uuid4()))
                          if 'request_id' not in change else dict(work['body'], **change))
    assert work['creator'].calls == []


@pytest.mark.parametrize('recover_unknown', [False, True])
def test_real_creation_http_and_background_pump_use_durable_service_without_duplicate_work(work, recover_unknown):
    from video_workbench.animation_creation_http import CreationAPIClient, CreationHTTPServer

    def opened(service, *, pump):
        server = CreationHTTPServer(service, 'test-only-internal-token-' + 'x' * 32, pump=pump)
        if server.pump:
            server.pump.interval = .01
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = CreationAPIClient('http://127.0.0.1:' + str(server.server_port),
                                   'test-only-internal-token-' + 'x' * 32)
        return server, thread, client

    server, thread, client = opened(work['svc'], pump=False)
    try:
        run = client.start('alice', work['body'])
        assert run['id'] == work['run']['id']
        for _ in range(2):
            assert client.get('alice', run['id'])['state'] == 'queued'
        assert work['creator'].count('submit') == 0
        if recover_unknown:
            work['creator'].lose_submit = 'initial_creation'
            assert advance(work)['state'] == 'submission_unknown'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    reopen(work)
    server, thread, client = opened(work['svc'], pump=True)
    try:
        server.pump.notify()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            run = client.get('alice', work['run']['id'])
            if run['state'] in {'completed', 'failed', 'cancelled'}:
                break
            Event().wait(.02)
        assert run['state'] == 'completed', run
        counts = (work['creator'].count('submit'), work['reviewer'].count('submit'), work['renderer'].count('render'))
        assert counts == (2, 1, 2)
        assert client.list('alice')['runs'][0]['id'] == run['id']
        for version_id in ('original', 'revised'):
            media, mime = client.artifact('alice', run['id'], version_id, 'media')
            source, source_mime = client.artifact('alice', run['id'], version_id, 'source')
            assert hashlib.sha256(media).hexdigest() == next(v['sha256'] for v in run['versions'] if v['id'] == version_id)
            assert mime == 'video/mp4' and source_mime == 'application/json'
            assert json.loads(source)['schema'] == creation.RESULT_SCHEMA
        for _ in range(2):
            assert client.get('alice', run['id'])['state'] == 'completed'
        assert counts == (work['creator'].count('submit'), work['reviewer'].count('submit'), work['renderer'].count('render'))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
