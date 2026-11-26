"""New compilation budget is opt-in and never rewrites a frozen publication."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from agent_service import animation_wikiskill_contract as C
from agent_service.compilation_input_tools import (
    COMPILATION_CAPACITY_ENVIRONMENT_NAME, COMPILATION_CAPACITY_FILES,
    COMPILATION_CAPACITY_TOOLS_SHA256, verified_compilation_input_extension,
)
from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import implementation
from video_workbench.animation_wikiskill_coordinator import WikiSkillCoordinator
from video_workbench.animation_wikiskill_gateway import AnimationWikiSkillGateway
from video_workbench.animation_wikiskill_store import (
    AnimationWikiSkillStore, PUBLICATION_SECONDS, PUBLICATION_TIMING,
    collection_reserve_seconds, stored_compiler_revision,
)
from test_animation_review_gateway import Peer
from test_animation_wikiskill import Gateway, records, result_for


class RoutedGateway(Gateway):
    def __init__(self):
        super().__init__()
        self.revisions = []

    def for_revision(self, revision):
        self.revisions.append(revision)
        return SimpleNamespace(profile_revision=revision, preflight=self.preflight,
            upload=self.upload, prepare_submission=self.prepare_submission,
            lookup=self.lookup, poll=self.poll, stopped=self.stopped, collect=self.collect)


def create(store, revision, *, request=None):
    request = request or dict(request_id=str(uuid4()))
    frozen = C.make_snapshot('publisher', records(), captured_at=1000., compilation_revision=revision)
    document = C.build_input(frozen, compilation_revision=revision)
    return store.create('publisher', request, frozen, document, compilation_revision=revision)


def finish(coordinator, gateway, job):
    for _ in range(5):
        current = coordinator.advance('publisher', job['id'])
        if current['state'] == 'running':
            gateway.jobs[job['id']].update(state='completed', stop_proven=True)
        if current['state'] in {'published', 'failed'}:
            return current
    pytest.fail('bounded synthetic publication did not finish')


@pytest.mark.parametrize('revision,outer,reserve,schema', [
    ('1', 1200, 250, 'video.animation-wikiskill-publication-timing.v1'),
    ('2', 1200, 250, 'video.animation-wikiskill-publication-timing.v1'),
    ('3', 4200, 450, 'video.animation-wikiskill-publication-timing.v2'),
])
def test_explicit_creation_freezes_each_revision_budget(tmp_path, revision, outer, reserve, schema):
    store = AnimationWikiSkillStore(tmp_path/'timing.sqlite3', clock=lambda: 1000.)
    job = create(store, revision)
    assert PUBLICATION_SECONDS == 1200
    assert PUBLICATION_TIMING == dict(schema='video.animation-wikiskill-publication-timing.v1',
        publication_seconds=1200, native_max_seconds=900, collection_reserve_seconds=250)
    assert job['deadline_at'] == 1000. + outer
    assert stored_compiler_revision(job) == revision
    assert collection_reserve_seconds(job) == reserve
    assert job['publication_timing'] == dict(schema=schema, publication_seconds=outer,
        native_max_seconds=3600 if revision == '3' else 900, collection_reserve_seconds=reserve)
    request = dict(request_id=job['request_id'])
    original = store.operation('publisher', job['id'])
    restarted = WikiSkillCoordinator(AnimationWikiSkillStore(store.path, clock=lambda: 1100.),
        RoutedGateway(), lambda _: pytest.fail('replay must not recapture a library'), clock=lambda: 1100.)
    assert restarted.start('publisher', request) == job
    assert restarted.store.operation('publisher', job['id']) == original


def test_new_manual_publication_uses_revision_three_without_changing_original_file_bytes(tmp_path):
    source = C.make_snapshot('publisher', records(), captured_at=1000., compilation_revision='2')
    newer = C.make_snapshot('publisher', records(), captured_at=1000., snapshot_id=source['id'], compilation_revision='3')
    assert newer == source
    old_document = C.build_input(source, compilation_revision='2')
    new_document = C.build_input(newer, compilation_revision='3')
    assert new_document == old_document
    assert C.input_files(newer, new_document, compilation_revision='3') == C.input_files(
        source, old_document, compilation_revision='2')
    assert C.validate_result(result_for(new_document), digest(new_document), new_document,
        compilation_revision='3') == C.validate_result(result_for(old_document), digest(old_document), old_document,
        compilation_revision='2')
    assert C.compilation_limits('3') == C.compilation_limits('2') == (63*1024**2, 64*1024**2)
    store = AnimationWikiSkillStore(tmp_path/'new.sqlite3', clock=lambda: 1000.)
    gateway = RoutedGateway()
    coordinator = WikiSkillCoordinator(store, gateway, lambda _: records(), clock=lambda: 1000.)
    job = coordinator.start('publisher', dict(request_id=str(uuid4())))
    assert job['compiler_revision'] == '3' and job['deadline_at'] == 5200.
    assert coordinator.advance('publisher', job['id'])['state'] == 'prepared'
    assert coordinator.advance('publisher', job['id'])['state'] == 'running'
    assert gateway.revisions == ['3', '3']
    assert gateway.submission_timing == [dict(deadline_at=5200., collection_reserve_seconds=450)]


@pytest.mark.parametrize('revision,reserve,outer,native', [
    ('1', 250, 1200, 900), ('2', 250, 1200, 900), ('3', 450, 4200, 3600),
])
def test_real_gateway_http_carries_only_selected_native_window(monkeypatch, revision, reserve, outer, native):
    monkeypatch.setattr('video_workbench.animation_wikiskill_gateway.time.time', lambda: 1000.)
    peer = Peer(AnimationWikiSkillGateway)
    peer.profiles[0].update(profile_revision=revision, max_timeout_seconds=3600)
    frozen = C.make_snapshot('publisher', records(), captured_at=1000., compilation_revision=revision)
    document = C.build_input(frozen, compilation_revision=revision)
    files = C.input_files(frozen, document, compilation_revision=revision)
    with AgentClient('http://agent.invalid', 'offline-budget-token-'+'a'*32,
            transport=httpx.MockTransport(peer)) as client:
        gateway = AnimationWikiSkillGateway(client, profile_revision=revision)
        refs = gateway.upload(document, files)
        gateway.prepare_submission('same-frozen-operation', document, 1000.+outer, refs,
            collection_reserve_seconds=reserve)()
    assert peer.request['profile_revision'] == revision
    assert peer.request['timeout_seconds'] == native
    assert peer.request['execution_deadline_at'] == 1000.+outer-reserve
    assert peer.request['task'] == dict(input_sha256=digest(document))
    assert peer.uploads == files
    assert sum(call.method == 'POST' and call.url.path == '/v1/jobs' for call in peer.calls) == 1


@pytest.mark.parametrize('reserve', [0, 250, 449, 451])
def test_revision_three_does_not_accept_historical_or_unrecognized_reserve(monkeypatch, reserve):
    monkeypatch.setattr('video_workbench.animation_wikiskill_gateway.time.time', lambda: 1000.)
    peer = Peer(AnimationWikiSkillGateway)
    peer.profiles[0].update(profile_revision='3', max_timeout_seconds=3600)
    frozen = C.make_snapshot('publisher', records(), captured_at=1000., compilation_revision='3')
    document = C.build_input(frozen, compilation_revision='3')
    with AgentClient('http://agent.invalid', 'offline-budget-token-'+'a'*32,
            transport=httpx.MockTransport(peer)) as client:
        gateway = AnimationWikiSkillGateway(client, profile_revision='3')
        refs = gateway.upload(document, C.input_files(frozen, document, compilation_revision='3'))
        with pytest.raises(ServiceError, match='timing_invalid'):
            gateway.prepare_submission('invalid-window', document, 5200., refs, collection_reserve_seconds=reserve)
    assert peer.request is None


@pytest.mark.parametrize('revision', ['1', '2'])
def test_historical_gateway_cannot_borrow_new_reserve(monkeypatch, revision):
    monkeypatch.setattr('video_workbench.animation_wikiskill_gateway.time.time', lambda: 1000.)
    peer = Peer(AnimationWikiSkillGateway)
    peer.profiles[0].update(profile_revision=revision, max_timeout_seconds=3600)
    frozen = C.make_snapshot('publisher', records(), captured_at=1000., compilation_revision=revision)
    document = C.build_input(frozen, compilation_revision=revision)
    with AgentClient('http://agent.invalid', 'offline-budget-token-'+'a'*32,
            transport=httpx.MockTransport(peer)) as client:
        gateway = AnimationWikiSkillGateway(client, profile_revision=revision)
        refs = gateway.upload(document, C.input_files(frozen, document, compilation_revision=revision))
        with pytest.raises(ServiceError, match='timing_invalid'):
            gateway.prepare_submission('old-window', document, 5200., refs, collection_reserve_seconds=450)
    assert peer.request is None


@pytest.mark.parametrize('observed_at,timeout', [(4700.,50),(4750.,None)])
def test_extended_gateway_never_resets_a_partially_consumed_submission_deadline(monkeypatch,observed_at,timeout):
    monkeypatch.setattr('video_workbench.animation_wikiskill_gateway.time.time',lambda:observed_at)
    peer=Peer(AnimationWikiSkillGateway)
    peer.profiles[0].update(profile_revision='3',max_timeout_seconds=3600)
    frozen=C.make_snapshot('publisher',records(),captured_at=1000.,compilation_revision='3')
    document=C.build_input(frozen,compilation_revision='3')
    with AgentClient('http://agent.invalid','offline-budget-token-'+'a'*32,
            transport=httpx.MockTransport(peer)) as client:
        gateway=AnimationWikiSkillGateway(client,profile_revision='3')
        refs=gateway.upload(document,C.input_files(frozen,document,compilation_revision='3'))
        if timeout is None:
            with pytest.raises(ServiceError,match='deadline_expired'):
                gateway.prepare_submission('spent-window',document,5200.,refs,collection_reserve_seconds=450)
            assert peer.request is None
        else:
            gateway.prepare_submission('spent-window',document,5200.,refs,collection_reserve_seconds=450)()
            assert peer.request['timeout_seconds']==timeout and peer.request['execution_deadline_at']==4750.


@pytest.mark.parametrize('revision',['1','2'])
def test_historical_jobs_cannot_adopt_even_a_well_formed_extended_timing_marker(tmp_path,revision):
    store=AnimationWikiSkillStore(tmp_path/'old-marker.sqlite3',clock=lambda:1000.)
    old=create(store,revision)
    changed=deepcopy(old)
    changed['publication_timing']=dict(schema='video.animation-wikiskill-publication-timing.v2',
        publication_seconds=4200,native_max_seconds=3600,collection_reserve_seconds=450)
    changed['deadline_at']=5200.
    with pytest.raises(ServiceError,match='timing_invalid'):
        collection_reserve_seconds(changed)


@pytest.mark.parametrize('mutation', ['legacy-marker', 'missing-marker', 'deadline', 'native-max', 'reserve'])
def test_new_budget_markers_cannot_be_reinterpreted_as_another_contract(tmp_path, mutation):
    store = AnimationWikiSkillStore(tmp_path/'mutations.sqlite3', clock=lambda: 1000.)
    job = create(store, '3')
    changed = deepcopy(job)
    if mutation == 'legacy-marker':
        changed['publication_timing'] = deepcopy(PUBLICATION_TIMING)
    elif mutation == 'missing-marker':
        changed.pop('publication_timing')
    elif mutation == 'deadline':
        changed['deadline_at'] += 1
    elif mutation == 'native-max':
        changed['publication_timing']['native_max_seconds'] = 900
    else:
        changed['publication_timing']['collection_reserve_seconds'] = 250
    with pytest.raises(ServiceError, match='timing_invalid'):
        collection_reserve_seconds(changed)


def test_legacy_unmarked_replay_preserves_900_second_deadline_and_revision_one(tmp_path):
    store = AnimationWikiSkillStore(tmp_path/'legacy.sqlite3', clock=lambda: 1000.)
    job = create(store, '1')
    with store._transaction() as db:
        value = json.loads(db.execute('SELECT document FROM wikiskill_jobs WHERE id=?',(job['id'],)).fetchone()[0])
        value.pop('publication_timing')
        value['deadline_at'] = 1900.
        raw = canonical_bytes(value).decode()
        db.execute('UPDATE wikiskill_jobs SET document=? WHERE id=?',(raw,job['id']))
    gateway = RoutedGateway()
    resumed = WikiSkillCoordinator(AnimationWikiSkillStore(store.path, clock=lambda: 1100.),gateway,
        lambda _: pytest.fail('historical replay must not capture live data'),clock=lambda: 1100.)
    recovered = resumed.start('publisher',dict(request_id=job['request_id']))
    assert recovered['deadline_at'] == 1900. and 'compiler_revision' not in recovered
    assert collection_reserve_seconds(recovered) is None
    with resumed.store._transaction() as db:
        assert db.execute('SELECT document FROM wikiskill_jobs WHERE id=?',(job['id'],)).fetchone()[0] == raw
    resumed.advance('publisher',job['id'])
    assert resumed.advance('publisher',job['id'])['state'] == 'running'
    assert gateway.revisions == ['1','1'] and gateway.submission_timing == [dict(deadline_at=1900.)]


def test_frozen_revision_two_ambiguous_submission_recovers_without_new_paid_admission(tmp_path):
    store = AnimationWikiSkillStore(tmp_path/'lost-response.sqlite3',clock=lambda:1000.)
    job = create(store,'2')
    gateway = RoutedGateway();gateway.lose_response = True
    coordinator = WikiSkillCoordinator(store,gateway,lambda _:pytest.fail('frozen job must not recapture'),clock=lambda:1000.)
    assert coordinator.advance('publisher',job['id'])['state'] == 'prepared'
    assert coordinator.advance('publisher',job['id'])['state'] == 'submission_unknown'
    restarted = WikiSkillCoordinator(AnimationWikiSkillStore(store.path,clock=lambda:1100.),gateway,
        lambda _:pytest.fail('frozen job must not recapture'),clock=lambda:1100.)
    assert restarted.start('publisher',dict(request_id=job['request_id']))['deadline_at'] == 2200.
    assert restarted.advance('publisher',job['id'])['state'] == 'running'
    assert gateway.submissions == [job['id']] and gateway.lookups == [job['id']]
    assert gateway.revisions == ['2','2','2']
    assert gateway.submission_timing == [dict(deadline_at=2200.,collection_reserve_seconds=250)]


@pytest.mark.parametrize('collected_at,expected', [(5100.,'published'),(5200.,'failed')])
def test_extended_outer_collection_window_is_finite_and_keeps_old_release_on_failure(tmp_path,collected_at,expected):
    store = AnimationWikiSkillStore(tmp_path/'collect.sqlite3',clock=lambda:1000.)
    gateway = RoutedGateway()
    coordinator = WikiSkillCoordinator(store,gateway,lambda _:records(),clock=lambda:1000.)
    prior = create(store,'2')
    assert finish(coordinator,gateway,prior)['state'] == 'published'
    previous_release = store.active('publisher')
    current = coordinator.start('publisher',dict(request_id=str(uuid4())))
    coordinator.advance('publisher',current['id'])
    coordinator.advance('publisher',current['id'])
    gateway.jobs[current['id']].update(state='completed',stop_proven=True)
    now=[4800.]
    store.clock=coordinator.clock=lambda:now[0]
    original_collect=gateway.collect
    def collect(*args):
        result=original_collect(*args);now[0]=collected_at;return result
    gateway.collect=collect
    done=coordinator.advance('publisher',current['id'])
    assert done['state'] == expected and done['deadline_at'] == 5200.
    if expected == 'published':
        assert store.active('publisher')['job_id'] == current['id']
    else:
        assert done['error']['code'] == 'animation_wikiskill_publication_deadline_expired'
        assert store.active('publisher') == previous_release
    assert gateway.submissions.count(current['id']) == 1


def test_revision_three_reuses_verified_capacity_tools_and_authored_prompt_exactly():
    previous=implementation('video.animation-wikiskill-compile.v4','2')
    current=implementation('video.animation-wikiskill-compile.v4','3')
    assert previous.max_timeout_seconds == 900 and current.max_timeout_seconds == 3600
    assert [C.compilation_native_max_seconds(revision) for revision in ('1','2','3')] == [900,900,3600]
    assert previous.task_schema == current.task_schema and previous.output_schema == current.output_schema
    assert previous.limits == current.limits and current.limits.max_input_total_bytes == 64*1024**2
    assert previous.required_tools == current.required_tools == ('read_compilation_input','write','check_compilation_output')
    assert previous.build_prompt(dict(input_sha256='a'*64)) == current.build_prompt(dict(input_sha256='a'*64))
    extension=verified_compilation_input_extension(COMPILATION_CAPACITY_ENVIRONMENT_NAME)
    assert extension.name == 'checked_compilation_capacity_extension.mjs'
    assert digest(COMPILATION_CAPACITY_FILES) == COMPILATION_CAPACITY_TOOLS_SHA256
    for name,checksum in COMPILATION_CAPACITY_FILES.items():
        assert hashlib.sha256(extension.with_name(name).read_bytes()).hexdigest() == checksum
