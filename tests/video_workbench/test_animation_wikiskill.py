"""Whole-library release tests use synthetic records, never real media approval."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service import animation_wikiskill_contract as C
from agent_service.animation_skills_contract import build_skill_package
from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles import video_animation_wikiskill as P
from video_workbench.animation_wikiskill_store import AnimationWikiSkillStore
from video_workbench.animation_wikiskill_coordinator import WikiSkillCoordinator


def records():
    return [dict(record_id='goal', kind='brief', content={'goal': 'Show a cause and result.'}),
        dict(record_id='observation', kind='review', content={
            'coverage': 'sampled_frames', 'limitation': 'Motion was not observed.'})]


def snapshot():
    return C.make_snapshot('publisher', records(), captured_at=1000.)


def result_for(document):
    package = build_skill_package(skill_id='causal-discovery', version=1, kind='foundation',
        files={'SKILL.md': '---\nname: causal-discovery\ndescription: Discover bounded causal staging guidance.\n---\n'
            'The supplied goal suggests establishing the cause first. Sampled stills do not validate motion.'},
        runtime_requirements={'engines': ['html-svg', 'canvas']})
    return dict(schema=C.RESULT_SCHEMA, input_sha256=digest(document),
        snapshot_sha256=document['snapshot']['sha256'], evidence_scope=C.EVIDENCE_SCOPE,
        summary='Synthetic fixture compilation; not a rendered or accepted effect.', packages=[package],
        index=[dict(skill_id=package['skill_id'], parent_skill_id=None,
                    source_record_ids=[row['record_id'] for row in document['records']])],
        exclusions=[], limitations=[C.COMPILATION_LIMITATION])


class Gateway:
    def __init__(self):
        self.jobs, self.submissions, self.uploads, self.lookups = {}, [], [], []
        self.submission_timing = []
        self.lose_response = self.invalid_result = self.lookup_missing = False

    def preflight(self, deadline):
        return {}

    def upload(self, document, files):
        C.validate_input_files(document, files)
        self.uploads.append(deepcopy(document))
        return [dict(input_id=str(uuid4()), path=name, sha256=hashlib.sha256(raw).hexdigest(), role='fixture')
                for name, raw in files.items()]

    def prepare_submission(self, operation_id, document, deadline, refs, **timing):
        self.submission_timing.append(dict(deadline_at=deadline, **timing))
        def admit():
            self.submissions.append(operation_id)
            self.jobs[operation_id] = dict(job_id=str(uuid4()), state='running', stop_proven=False,
                document=deepcopy(document))
            if self.lose_response:
                raise OSError('synthetic lost admission response')
            return deepcopy(self.jobs[operation_id])
        return admit

    def lookup(self, operation_id):
        self.lookups.append(operation_id)
        if self.lookup_missing:
            raise ServiceError('job_not_found', 404)
        return deepcopy(self.jobs.get(operation_id))

    def poll(self, job_id):
        return deepcopy(next(row for row in self.jobs.values() if row['job_id'] == job_id))

    def stopped(self, job):
        # Only a fake in these coordinator unit tests. Real HTTP proof is
        # verified separately by the production Gateway integration tests.
        return job.get('stop_proven') is True

    def collect(self, job_id, checksum, document):
        assert self.poll(job_id)['document'] == document and checksum == digest(document)
        value = result_for(document)
        if self.invalid_result:
            value['snapshot_sha256'] = '0' * 64
        return value


@pytest.fixture
def work(tmp_path):
    store = AnimationWikiSkillStore(tmp_path / 'wikiskill.sqlite3', clock=lambda: 1000.)
    gateway = Gateway()
    captures = []
    def capture(owner):
        captures.append(owner)
        return records()
    return store, gateway, WikiSkillCoordinator(store, gateway, capture, clock=lambda: 1000.), captures


def start(work, owner='publisher'):
    return work[2].start(owner, {'request_id': str(uuid4())})


def finish(work, job):
    store, gateway, coordinator, _ = work
    for _ in range(5):
        current = coordinator.advance(job['owner'], job['id'])
        if current['state'] == 'running':
            gateway.jobs[current['operation_id']].update(state='completed', stop_proven=True)
        if current['state'] in {'published', 'failed'}:
            return current
    raise AssertionError('synthetic job failed to terminate')


def test_snapshot_binds_every_byte_and_record_file_without_silent_truncation():
    frozen = snapshot()
    document = C.build_input(frozen)
    files = C.input_files(frozen, document)
    assert C.validate_input_files(document, files) == frozen
    frozen['records'][0]['content']['goal'] = 'changed'
    with pytest.raises(ServiceError, match='record_changed'):
        C.validate_snapshot(frozen)
    files[document['records'][0]['file_name']] += b' '
    with pytest.raises(ServiceError, match='binding_mismatch'):
        C.validate_input_files(document, files)
    with pytest.raises(ServiceError, match='snapshot_record_limit'):
        C.make_snapshot('publisher', records() * C.MAX_RECORDS)


@pytest.mark.parametrize('mutation,code', [
    ('wrong_snapshot', 'binding_mismatch'), ('unseen_record', 'evidence_not_supplied'),
    ('missing_coverage', 'coverage_incomplete'), ('false_scope', 'binding_mismatch'),
    ('missing_limitation', 'observation_scope_required'), ('cycle', 'root_required'),
    ('external_url', 'external_dependency'), ('broken_local_link', 'reference_missing'),
])
def test_compiler_rejects_unbound_or_incomplete_or_external_knowledge(mutation, code):
    document = C.build_input(snapshot())
    result = result_for(document)
    if mutation == 'wrong_snapshot':
        result['snapshot_sha256'] = 'a' * 64
    elif mutation == 'unseen_record':
        result['index'][0]['source_record_ids'].append('not-supplied')
    elif mutation == 'missing_coverage':
        result['index'][0]['source_record_ids'].pop()
    elif mutation == 'false_scope':
        result['evidence_scope'] = 'fully_watched_and_approved'
    elif mutation == 'missing_limitation':
        result['limitations'] = []
    elif mutation == 'cycle':
        result['index'][0]['parent_skill_id'] = result['index'][0]['skill_id']
        code = 'animation_wikiskill_invalid'
    else:
        package = result['packages'][0]
        fields = {key: deepcopy(package[key]) for key in
            ('skill_id', 'version', 'kind', 'files', 'runtime_requirements')}
        fields['files']['SKILL.md'] += ('\nFetch https://example.invalid/source' if mutation == 'external_url'
            else '\n[missing](references/absent.md)')
        result['packages'][0] = fields
        with pytest.raises(ServiceError, match=code):
            C.normalize_agent_result(result)
        return
    with pytest.raises(ServiceError, match=code):
        C.validate_result(result, digest(document), document)


def test_draft_hashes_are_computed_without_changing_agent_authored_text():
    document = C.build_input(snapshot())
    result = result_for(document)
    expected = deepcopy(result['packages'][0])
    result['packages'][0] = {key: expected[key] for key in
        ('skill_id', 'version', 'kind', 'files', 'runtime_requirements')}
    normalized = C.normalize_agent_result(result)
    assert normalized['packages'][0] == expected
    assert C.validate_result(normalized, digest(document), document) == normalized


def test_hierarchy_has_one_root_and_rejects_disconnected_child_cycle():
    document = C.build_input(snapshot())
    result = result_for(document)
    for number in (1, 2):
        package = build_skill_package(skill_id=f'child-{number}', version=1, kind='expression',
            files={'SKILL.md': f'---\nname: child-{number}\ndescription: A separate synthetic bounded example.\n---\n'
                'Only a test of package navigation, with no media-quality assertion.'},
            runtime_requirements={'engines': ['canvas']})
        result['packages'].append(package)
        result['index'].append(dict(skill_id=package['skill_id'], parent_skill_id='causal-discovery',
            source_record_ids=['goal']))
    assert C.validate_result(result, digest(document), document) == result
    result['index'][1]['parent_skill_id'] = 'child-2'
    result['index'][2]['parent_skill_id'] = 'child-1'
    with pytest.raises(ServiceError, match='hierarchy_cycle'):
        C.validate_result(result, digest(document), document)


def test_unused_material_is_accounted_for_and_invalid_json_is_explicitly_rejected():
    document = C.build_input(snapshot())
    result = result_for(document)
    result['index'][0]['source_record_ids'] = ['goal']
    result['exclusions'] = [{'record_id': 'observation', 'reason': 'No reliable movement observation exists.'}]
    assert C.validate_result(result, digest(document), document) == result
    malformed = records()
    malformed[0]['content']['nonfinite'] = float('nan')
    with pytest.raises(ServiceError, match='json_invalid'):
        C.make_snapshot('publisher', malformed)


def test_post_start_is_unpaid_reads_are_passive_and_replay_does_not_recapture(work):
    store, gateway, coordinator, captures = work
    request = {'request_id': str(uuid4()), 'note': 'compile this exact current library'}
    job = coordinator.start('publisher', request)
    assert job['state'] == 'queued' and not gateway.submissions and not gateway.uploads
    assert coordinator.start('publisher', request) == job and captures == ['publisher']
    assert coordinator.get('publisher', job['id']) == coordinator.recover('publisher', request['request_id'])
    assert coordinator.catalogue('publisher')['jobs'] == [job] and not gateway.submissions
    with pytest.raises(ServiceError, match='request_conflict'):
        coordinator.start('publisher', {**request, 'note': 'different'})
    with pytest.raises(ServiceError, match='in_progress'):
        start(work)


def test_new_publication_reserves_collection_time_and_replay_preserves_budget(work):
    from video_workbench.animation_wikiskill_store import PUBLICATION_TIMING
    store, gateway, coordinator, captures = work
    job = start(work)
    assert job['deadline_at'] == job['created_at'] + 1200
    assert job['publication_timing'] == PUBLICATION_TIMING
    restarted = WikiSkillCoordinator(AnimationWikiSkillStore(store.path, clock=lambda: 1100.),
        gateway, lambda _: pytest.fail('must not recapture'), clock=lambda: 1100.)
    assert restarted.start('publisher', {'request_id': job['request_id']}) == job
    assert captures == ['publisher']
    assert restarted.advance('publisher', job['id'])['state'] == 'prepared'
    assert restarted.advance('publisher', job['id'])['state'] == 'running'
    assert gateway.submission_timing == [dict(deadline_at=2200., collection_reserve_seconds=250)]
    token = store.acquire('publisher', job['id'])
    with pytest.raises(ServiceError):
        store.change('publisher', job['id'], token, deadline_at=2300.)
    with pytest.raises(ServiceError):
        store.change('publisher', job['id'], token, publication_timing={})
    store.release_lease('publisher', job['id'], token)


def test_legacy_publication_keeps_original_900_second_clock_and_gateway_call(work):
    store, gateway, _, captures = work
    job = start(work)
    # Seed an old persisted record in this isolated test DB. Deployment code
    # never rewrites a historical job or adds the new marker to one.
    with store._transaction() as db:
        original = json.loads(db.execute('SELECT document FROM wikiskill_jobs WHERE id=?', (job['id'],)).fetchone()[0])
        original.pop('publication_timing')
        original['deadline_at'] = original['created_at'] + 900
        raw = canonical_bytes(original).decode()
        db.execute('UPDATE wikiskill_jobs SET document=? WHERE id=?', (raw, job['id']))
    reopened = AnimationWikiSkillStore(store.path, clock=lambda: 1100.)
    restarted = WikiSkillCoordinator(reopened, gateway, lambda _: pytest.fail('must not recapture'), clock=lambda: 1100.)
    recovered = restarted.start('publisher', {'request_id': job['request_id']})
    assert recovered['deadline_at'] == 1900. and 'publication_timing' not in recovered
    with reopened._transaction() as db:
        assert db.execute('SELECT document FROM wikiskill_jobs WHERE id=?', (job['id'],)).fetchone()[0] == raw
    restarted.advance('publisher', job['id'])
    assert restarted.advance('publisher', job['id'])['state'] == 'running'
    assert gateway.submission_timing == [dict(deadline_at=1900.)]
    assert reopened.snapshot('publisher', job['snapshot_id'])['sha256'] == job['snapshot_sha256']
    assert captures == ['publisher']


def test_new_publication_does_not_consume_collection_reserve_before_admission(work):
    store, gateway, coordinator, _ = work
    job = start(work)
    store.clock = coordinator.clock = lambda: 1950.
    failed = coordinator.advance('publisher', job['id'])
    assert failed['state'] == 'failed' and not gateway.uploads and not gateway.submissions
    assert failed['deadline_at'] == 2200.


@pytest.mark.parametrize('collected_at,expected', [(2100., 'published'), (2200., 'failed')])
def test_collection_has_separate_outer_window_and_cannot_publish_after_it(work, collected_at, expected):
    store, gateway, coordinator, _ = work
    finish(work, start(work))
    previous = store.active('publisher')
    job = start(work)
    coordinator.advance('publisher', job['id'])
    coordinator.advance('publisher', job['id'])
    gateway.jobs[job['operation_id']].update(state='completed', stop_proven=True)
    now = [2000.]  # Collection begins after the former 900-second window.
    store.clock = coordinator.clock = lambda: now[0]
    collect = gateway.collect
    def delayed_collection(*args):
        result = collect(*args)
        now[0] = collected_at
        return result
    gateway.collect = delayed_collection
    done = coordinator.advance('publisher', job['id'])
    assert done['state'] == expected and done['deadline_at'] == 2200.
    if expected == 'published':
        assert store.active('publisher')['job_id'] == job['id']
    else:
        assert done['error']['code'] == 'animation_wikiskill_publication_deadline_expired'
        assert store.active('publisher') == previous
    assert store.snapshot('publisher', job['snapshot_id'])['sha256'] == job['snapshot_sha256']
    assert gateway.submissions.count(job['operation_id']) == 1


def test_complete_release_switches_atomically_and_preserves_exact_source_snapshot(work):
    store, gateway, coordinator, _ = work
    job = start(work)
    source = store.snapshot('publisher', job['snapshot_id'])
    done = finish(work, job)
    release = store.active('publisher')
    assert done['state'] == 'published' and len(gateway.submissions) == 1
    assert release['id'] == done['release_id'] and release['snapshot_sha256'] == source['sha256']
    assert store.snapshot('publisher', job['snapshot_id']) == source
    assert release['evidence_scope'] == C.EVIDENCE_SCOPE
    assert store.active('another-publisher') is None
    for action in (lambda: store.get('another-publisher', job['id']),
                   lambda: store.get_release('another-publisher', release['id']),
                   lambda: store.snapshot('another-publisher', source['id'])):
        with pytest.raises(ServiceError):
            action()


def test_failed_compilation_keeps_previous_active_and_snapshot(work):
    store, gateway, coordinator, _ = work
    finish(work, start(work))
    previous = store.active('publisher')
    gateway.invalid_result = True
    job = start(work)
    assert finish(work, job)['state'] == 'failed'
    assert store.active('publisher') == previous
    assert store.snapshot('publisher', job['snapshot_id'])['sha256'] == job['snapshot_sha256']


def test_release_reader_rejects_rehashed_malformed_hierarchy(work):
    store = work[0]
    finish(work, start(work))
    release = store.active('publisher')
    release['index'] = []
    release['sha256'] = digest({key: value for key, value in release.items() if key != 'sha256'})
    with pytest.raises(ServiceError):
        C.validate_release(release)


def test_running_lookup_failure_cannot_free_an_unstopped_paid_job(work, monkeypatch):
    store, gateway, coordinator, _ = work
    job = start(work)
    coordinator.advance('publisher', job['id'])
    coordinator.advance('publisher', job['id'])
    def missing(_):
        raise ServiceError('job_not_found', 404)
    monkeypatch.setattr(gateway, 'poll', missing)
    assert coordinator.advance('publisher', job['id'])['state'] == 'running'
    with pytest.raises(ServiceError, match='in_progress'):
        start(work)
    assert len(gateway.submissions) == 1 and store.active('publisher') is None


def test_two_publishers_have_separate_active_pointers_and_request_receipts(work):
    store = work[0]
    first = start(work)
    second = start(work, owner='second-publisher')
    finish(work, first)
    finish(work, second)
    assert store.active('publisher')['job_id'] == first['id']
    assert store.active('second-publisher')['job_id'] == second['id']
    assert len(store.list_recent('publisher')) == len(store.list_recent('second-publisher')) == 1


def test_uncertain_submit_restarts_by_original_key_and_never_creates_another_job(work):
    store, gateway, coordinator, _ = work
    gateway.lose_response = True
    job = start(work)
    assert coordinator.advance('publisher', job['id'])['state'] == 'prepared'
    assert coordinator.advance('publisher', job['id'])['state'] == 'submission_unknown'
    restarted = WikiSkillCoordinator(store, gateway, lambda _: pytest.fail('must not recapture'), clock=lambda: 1000.)
    assert restarted.recover('publisher', job['request_id'])['state'] == 'submission_unknown'
    gateway.lookup_missing = True
    for _ in range(2):
        assert restarted.advance('publisher', job['id'])['state'] == 'submission_unknown'
    assert gateway.submissions == [job['operation_id']]
    assert gateway.lookups == [job['operation_id']] * 2
    gateway.lookup_missing = False
    assert restarted.advance('publisher', job['id'])['state'] == 'running'
    gateway.jobs[job['operation_id']].update(state='completed', stop_proven=True)
    assert restarted.advance('publisher', job['id'])['state'] == 'published'
    assert len(gateway.submissions) == 1


def test_crash_after_admission_claim_does_not_resubmit_even_when_lookup_is_empty(work):
    store, gateway, coordinator, _ = work
    job = start(work)
    coordinator.advance('publisher', job['id'])
    token = store.acquire('publisher', job['id'])
    store.change('publisher', job['id'], token, state='submitting', submission_attempted=True)
    store.release_lease('publisher', job['id'], token)
    assert coordinator.advance('publisher', job['id'])['state'] == 'submission_unknown'
    assert not gateway.submissions


@pytest.mark.parametrize('state', ['completed', 'failed', 'cancelled'])
def test_remote_terminal_without_proven_stop_stays_unresolved(work, state):
    store, gateway, coordinator, _ = work
    job = start(work)
    coordinator.advance('publisher', job['id'])
    coordinator.advance('publisher', job['id'])
    gateway.jobs[job['operation_id']].update(state=state, stop_proven=False)
    row = coordinator.advance('publisher', job['id'])
    assert row['state'] == 'running' and row['error']['code'].endswith('stop_unproven')
    assert store.active('publisher') is None


def test_recent_five_history_rollback_and_archived_frozen_artifacts(work):
    store, gateway, coordinator, _ = work
    releases = []
    for _ in range(6):
        done = finish(work, start(work))
        releases.append(store.get_release('publisher', done['release_id']))
    assert [row['id'] for row in store.list_recent('publisher')] == [row['id'] for row in reversed(releases[1:])]
    with pytest.raises(ServiceError, match='not_found'):
        store.get_release('publisher', releases[0]['id'])
    assert store.get_release('publisher', releases[0]['id'], include_archived=True) == releases[0]
    body = dict(request_id=str(uuid4()), release_id=releases[1]['id'])
    receipt = store.activate('publisher', body)
    assert store.active('publisher') == releases[1]
    assert store.activate('publisher', body) == store.recover_activation('publisher', body['request_id']) == receipt
    with pytest.raises(ServiceError, match='request_conflict'):
        store.activate('publisher', {**body, 'release_id': releases[-1]['id']})
    with pytest.raises(ServiceError, match='not_found'):
        store.activate('another-publisher', {'request_id': str(uuid4()), 'release_id': releases[-1]['id']})


def test_live_lease_fences_a_second_pump_and_original_request_is_stable(work):
    store, gateway, coordinator, _ = work
    job = start(work)
    token = store.acquire('publisher', job['id'])
    assert coordinator.advance('publisher', job['id']) == job and not gateway.uploads
    store.release_lease('publisher', job['id'], token)
    assert coordinator.advance('publisher', job['id'])['state'] == 'prepared'
    with pytest.raises(ServiceError, match='lease_lost'):
        store.change('publisher', job['id'], token, state='failed')


def stage_profile(root):
    frozen = snapshot()
    document = C.build_input(frozen)
    files = C.input_files(frozen, document)
    for name, raw in files.items():
        path = root / 'inputs' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    envelope = SimpleNamespace(profile=P.PROFILE, profile_revision='1', attempt_id=str(uuid4()),
        task={'input_sha256': digest(document)}, inputs=[InlineFile.from_bytes(name, raw) for name, raw in files.items()],
        checkpoint_inputs=[], checkpoint_ref=None)
    proof = StopEvidence(execution_id=envelope.attempt_id, all_writers_stopped=True, resource_ids=('fixture-agent',))
    raw = result_for(document)
    raw['packages'] = [{key: value for key, value in row.items() if key in
        {'skill_id', 'version', 'kind', 'files', 'runtime_requirements'}} for row in raw['packages']]
    (root / 'output.json').write_bytes(canonical_bytes(raw))
    return document, envelope, proof


def test_generic_profile_reads_manifest_and_record_files_and_collects_authored_package(tmp_path):
    document, envelope, proof = stage_profile(tmp_path)
    profile = P.VideoAnimationWikiSkillProfile()
    assert profile.required_tools == ('read', 'write')
    assert set(P.verified_bundle()) == P.BUNDLE_FILES
    prompt = profile.build_prompt(envelope.task)
    assert 'read/write only' in prompt and 'No shell' in prompt
    output, = profile.collect_output(tmp_path, envelope, proof)
    result = json.loads(output.content())
    assert result == result_for(document) and output.name == C.RESULT_NAME


def test_v2_reuses_original_byte_and_output_validation_with_bounded_read_prompt(tmp_path):
    from agent_service.profiles import video_animation_wikiskill_v2 as current
    document, envelope, proof = stage_profile(tmp_path)
    envelope.profile = current.PROFILE
    handler = current.VideoAnimationWikiSkillV2Profile()
    assert handler.required_tools == ('read_compilation_input', 'write')
    assert handler.task_schema == P.VideoAnimationWikiSkillProfile.task_schema
    prompt = handler.build_prompt(envelope.task)
    assert 'mode=manifest' in prompt and 'mode=batch' in prompt and 'next_offset_bytes' in prompt
    assert 'No shell' in prompt and digest(document) in prompt
    artifact, = handler.collect_output(tmp_path, envelope, proof)
    assert json.loads(artifact.content()) == result_for(document)
    path = tmp_path / 'inputs' / C.INPUT_NAME
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ServiceError, match='binding_mismatch'):
        handler.collect_output(tmp_path, envelope, proof)


def test_scope_literal_must_be_separate_and_host_never_repairs_authored_output(tmp_path):
    document, envelope, proof = stage_profile(tmp_path)
    profile = P.VideoAnimationWikiSkillProfile()
    prompt = profile.build_prompt(envelope.task)
    assert '`limitations` MUST include this exact string as a standalone array element:' in prompt
    assert 'translate, paraphrase or append another sentence within that element' in prompt
    assert 'it never inserts or rewrites this statement for you' in prompt

    path = tmp_path / 'output.json'
    authored = json.loads(path.read_bytes())
    additional = 'No source video or sound was supplied for new observation.'
    # The real r1 candidate merged these two sentences, which is meaningful
    # prose but does not preserve the required literal as its own member.
    authored['limitations'] = [C.COMPILATION_LIMITATION + ' ' + additional]
    original = canonical_bytes(authored)
    path.write_bytes(original)
    with pytest.raises(ServiceError, match='animation_wikiskill_observation_scope_required'):
        profile.collect_output(tmp_path, envelope, proof)
    assert path.read_bytes() == original

    authored['limitations'] = [C.COMPILATION_LIMITATION, additional]
    path.write_bytes(canonical_bytes(authored))
    artifact, = profile.collect_output(tmp_path, envelope, proof)
    collected = json.loads(artifact.content())
    assert collected['limitations'] == authored['limitations']
    assert collected['input_sha256'] == digest(document)


@pytest.mark.parametrize('mutation', ['running', 'other_profile', 'changed_record', 'extra_file', 'missing_ref', 'envelope_hash'])
def test_profile_rejects_unstopped_unbound_or_changed_input(tmp_path, mutation):
    document, envelope, proof = stage_profile(tmp_path)
    if mutation == 'running':
        proof = replace(proof, all_writers_stopped=False)
    elif mutation == 'other_profile':
        envelope.profile = 'video.animation-method-author.v2'
    elif mutation == 'changed_record':
        (tmp_path / 'inputs' / document['records'][0]['file_name']).write_bytes(b'{}')
    elif mutation == 'extra_file':
        (tmp_path / 'inputs' / 'extra.json').write_bytes(b'{}')
    elif mutation == 'missing_ref':
        envelope.inputs.pop()
    else:
        envelope.inputs[0] = InlineFile.from_bytes(envelope.inputs[0].name, b'{}')
    with pytest.raises(ServiceError):
        P.VideoAnimationWikiSkillProfile().collect_output(tmp_path, envelope, proof)
