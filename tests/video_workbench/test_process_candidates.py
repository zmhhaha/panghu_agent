"""Process candidates are opt-in, durable and independent; no remote calls."""
from copy import deepcopy
import hashlib
import io
import json
import wave

import httpx
import pytest

from agent_service.artifacts import frozen_manifest
from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.motion import example_plan
from video_workbench.motion_gateway import MotionAgentGateway, ProcessAgentGateway
from video_workbench.motion_store import (MotionStore, PROCESS_CANDIDATE_COMPILER,
    operation_diagram_kind, plan_diagram_kind)
from video_workbench.motion_timing import MotionTiming
from video_workbench.motion_workflow import MotionWorkflow
from video_workbench.store import Store
from test_motion_store import state, fields, OWNER, FakeGateway
from test_motion_gateway import Peer, JOB, ATTEMPT, INPUT, ARTIFACT, TOKEN, document as diagram_document


def process_plan(beats):
    ids = [beat['id'] for beat in beats['segments']]
    first, middle, last = ids[0], ids[len(ids) // 2], ids[-1]
    def action(kind, target, beat, placement, **fields):
        return dict(kind=kind, target=target, beat_id=beat, placement=placement, pace='quick', **fields)
    return dict(schema='video.process-plan.v1', template='flow', title='请求经过队列', takeaway='一个请求逐步完成',
        elements=[dict(id=name, kind='card', slot=name, label=label, detail='')
            for name, label in [('source', '入口'), ('queue', '队列'), ('worker', '执行器')]] + [
            dict(id='request', kind='token', slot='token_source', label='请求', detail='')],
        relations=[dict(id='enter', source='source', target='queue', label='排队'),
                   dict(id='dispatch', source='queue', target='worker', label='领取')],
        actions=[action('reveal', 'request', first, 'start'), action('draw', 'enter', first, 'start'),
            action('transfer', 'request', first, 'middle', relation_id='enter'),
            action('set_state', 'queue', first, 'end', state='waiting'),
            action('draw', 'dispatch', middle, 'start'),
            action('transfer', 'request', middle, 'middle', relation_id='dispatch'),
            action('set_state', 'worker', middle, 'end', state='running'),
            action('set_state', 'worker', last, 'middle', state='completed')])


def document():
    return dict(diagram_document(), schema='video.process-input.v1')


def start_process(state, *, key='process-one', **extra):
    base, motion, pid, sid = state
    request = dict(expected_revision=base.project(OWNER, pid)['revision'], diagram_kind='process', **extra)
    return motion.operation(motion.start(OWNER, sid, request, key)['id'])


def complete_process(motion, op):
    result = dict(schema='video.process-result.v1', input_sha256=op['input_sha256'],
                  plan=process_plan(op['document']['beats']))
    return motion.complete(op['id'], result)['version_id'], result


def test_process_candidate_is_pinned_never_auto_adopted_and_coexists_with_old_diagram(state):
    base, motion, pid, sid = state
    old = motion.operation(motion.start(OWNER, sid, dict(expected_revision=2), 'old-diagram')['id'])
    old_id = motion.complete(old['id'], dict(schema='video.motion-result.v1', input_sha256=old['input_sha256'],
        plan=example_plan(old['document']['beats'])))['version_id']
    historical = motion.version(OWNER, old_id)
    assert 'compiler_version' not in historical and 'diagram_kind' not in historical
    op = start_process(state)
    assert op['document']['schema'] == 'video.process-input.v1' and op['kind'] == 'process-plan'
    vid, result = complete_process(motion, op)
    version = motion.version(OWNER, vid)
    assert version['compiler_version'] == PROCESS_CANDIDATE_COMPILER
    assert version['diagram_kind'] == 'process' and version['plan'] == result['plan']
    assert motion.version(OWNER, old_id) == historical
    view = motion.project(OWNER, pid)
    meta = view['scenes'][0]['motion']
    assert meta['head_version_id'] is None
    assert [row['diagram_kind'] for row in meta['versions']] == ['diagram', 'process']
    assert motion.complete(op['id'], result)['version_id'] == vid
    adopted = motion.adopt(OWNER, sid, vid, None, view['revision'])
    assert adopted['scenes'][0]['motion']['mode'] == 'motion'
    assert adopted['scenes'][0]['motion']['head_version_id'] == vid
    assert base.project(OWNER, pid)['scenes'][0]['head_version_id'] is None


def test_new_diagram_default_preserves_exact_legacy_request_hash_and_replay(state):
    base, motion, pid, sid = state
    request = dict(expected_revision=2, instruction='', base_version_id=None)
    first = motion.start(OWNER, sid, request, 'legacy-request')
    assert motion.start(OWNER, sid, dict(request, diagram_kind='diagram'), 'legacy-request') == first
    with base.transaction() as db:
        stored_hash = db.execute('SELECT request_hash FROM motion_operations WHERE id=?', (first['id'],)).fetchone()[0]
    assert stored_hash == digest(dict(scene_id=sid, request=request, timeout=600))
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        motion.start(OWNER, sid, dict(request, diagram_kind='process'), 'legacy-request')
    # Other historical defaults keep their original meaning in the fingerprint.
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        motion.start(OWNER, sid, dict(expected_revision=2), 'legacy-request')


@pytest.mark.parametrize('kind', ['diagram', 'process'])
def test_cross_kind_revision_base_is_rejected_and_same_kind_preserves_parent(state, kind):
    base, motion, pid, sid = state
    op = start_process(state)
    process_id, _ = complete_process(motion, op)
    revision = base.project(OWNER, pid)['revision']
    if kind == 'diagram':
        base_id, other = process_id, 'diagram'
    else:
        old = motion.operation(motion.start(OWNER, sid, dict(expected_revision=revision), 'old')['id'])
        base_id = motion.complete(old['id'], dict(schema='video.motion-result.v1', input_sha256=old['input_sha256'],
            plan=example_plan(old['document']['beats'])))['version_id']
        other = 'process'
    revision = base.project(OWNER, pid)['revision']
    with pytest.raises(ServiceError, match='motion_base_kind_mismatch') as failed:
        motion.start(OWNER, sid, dict(expected_revision=revision, diagram_kind=other,
            base_version_id=base_id), 'wrong-kind')
    assert failed.value.status == 409
    revised = start_process(state, key='revise', base_version_id=process_id, instruction='突出等待')
    assert revised['parent_version_id'] == process_id
    assert revised['document']['base_plan'] == motion.version(OWNER, process_id)['plan']


@pytest.mark.parametrize('bad_kind', [None, True, {}, 'unknown', 'motion'])
def test_unknown_kind_is_not_a_diagram_fallback(state, bad_kind):
    base, motion, pid, sid = state
    with pytest.raises(ServiceError):
        motion.start(OWNER, sid, dict(expected_revision=2, diagram_kind=bad_kind), 'invalid')
    assert not motion.active_operations()


def test_process_binding_ownership_staleness_and_uniform_project_voice(state):
    base, motion, pid, sid = state
    op = start_process(state)
    vid, _ = complete_process(motion, op)
    original = motion.version(OWNER, vid)
    base.configure_speech_provider('kokoro-rk3588', 'zm_yunjian', ['zm_yunjian', 'zm_yunxi'])
    project = base.project(OWNER, pid)
    project = base.save_project_speech_settings(OWNER, pid,
        dict(expected_revision=project['revision'], voice='zm_yunxi'))
    assert motion.version(OWNER, vid) == original
    assert 'voice' not in op['document'] and 'audio_sha256' not in op['document']
    with pytest.raises(ServiceError, match='not_found'):
        motion.version('other-owner', vid)
    base.save_scene(OWNER, pid, dict(fields(), narration=fields()['narration'] + '改动。'), sid, project['revision'])
    with pytest.raises(ServiceError, match='motion_plan_stale'):
        motion.adopt(OWNER, sid, vid, None, base.project(OWNER, pid)['revision'])


@pytest.mark.parametrize('bad', ['diagram_result', 'bad_digest', 'code', 'schema'])
def test_process_completion_validates_original_input_contract_without_adoption(state, bad):
    _, motion, pid, _ = state
    op = start_process(state)
    result = dict(schema='video.process-result.v1', input_sha256=op['input_sha256'],
                  plan=process_plan(op['document']['beats']))
    if bad == 'diagram_result':
        result = dict(schema='video.motion-result.v1', input_sha256=op['input_sha256'],
                      plan=example_plan(op['document']['beats']))
    elif bad == 'bad_digest':
        result['input_sha256'] = '0' * 64
    elif bad == 'code':
        result['plan']['javascript'] = 'alert(1)'
    else:
        result['plan']['schema'] = 'unknown'
    with pytest.raises(ServiceError):
        motion.complete(op['id'], result)
    assert motion.project(OWNER, pid)['scenes'][0]['motion']['versions'] == []


class ProcessPeer(Peer):
    def __init__(self):
        super().__init__()
        self.profiles[0]['profile'] = 'video.process-plan.v1'

    def __call__(self, request):
        if request.url.path == '/v1/inputs' and request.method == 'POST':
            self.calls.append(request)
            assert request.url.params['name'] == 'process-input.json'
            return httpx.Response(201, json=dict(input_id=INPUT, name='process-input.json',
                size=len(request.content), sha256=hashlib.sha256(request.content).hexdigest()))
        return super().__call__(request)

    def complete(self, operation_id, input_document=None):
        doc = input_document or document()
        result = dict(schema='video.process-result.v1', input_sha256=digest(doc), plan=process_plan(doc['beats']))
        self.content = canonical_bytes(result)
        self.manifest = frozen_manifest(JOB, ATTEMPT, [dict(artifact_id=ARTIFACT,
            name='process-result.json', size=len(self.content), sha256=hashlib.sha256(self.content).hexdigest())])
        super().complete(operation_id, diagram_document())
        self.job.update(business_ref='video-process:' + operation_id,
                        pins=dict(profile='video.process-plan.v1', profile_revision='1'))
        self.content = canonical_bytes(result)
        self.manifest = frozen_manifest(JOB, ATTEMPT, [dict(artifact_id=ARTIFACT,
            name='process-result.json', size=len(self.content), sha256=hashlib.sha256(self.content).hexdigest())])
        self.job['artifact_manifest_sha256'] = self.manifest['sha256']
        return result


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setattr('video_workbench.motion_gateway.time.time', lambda: 1000.0)
    peer = ProcessPeer()
    with AgentClient('http://agent.invalid', TOKEN, transport=httpx.MockTransport(peer)) as client:
        yield ProcessAgentGateway(client), peer


def test_process_wire_uses_separate_profile_names_business_key_and_stopped_artifact(wire):
    gateway, peer = wire
    doc = document()
    ref = gateway.upload(doc)
    gateway.submit('operation-1', doc, 1250, ref)
    assert ref['path'] == 'process-input.json' and ref['role'] == 'process_input'
    assert peer.request['profile'] == 'video.process-plan.v1'
    assert peer.request['business_ref'] == 'video-process:operation-1'
    assert next(call for call in peer.calls if call.url.path == '/v1/jobs').headers['idempotency-key'] == 'process-operation-1'
    result = peer.complete('operation-1')
    assert gateway.collect(JOB, digest(doc), doc['beats']) == result


@pytest.mark.parametrize('bad', ['diagram_input', 'diagram_only', 'diagram_pins', 'diagram_name', 'diagram_schema', 'stop'])
def test_process_gateway_refuses_cross_contract_fallback_or_unproven_artifact(wire, bad):
    gateway, peer = wire
    doc = document()
    if bad == 'diagram_input':
        with pytest.raises(ServiceError):
            gateway.upload(diagram_document())
    elif bad == 'diagram_only':
        peer.profiles[0]['profile'] = 'video.motion-plan.v1'
        with pytest.raises(ServiceError):
            gateway.submit('operation-1', doc, 1200, gateway.upload(doc))
        assert peer.count('POST', '/v1/jobs') == 0
    else:
        result = peer.complete('operation-1')
        if bad == 'diagram_pins':
            peer.job['pins']['profile'] = 'video.motion-plan.v1'
        elif bad == 'diagram_name':
            peer.manifest['files'][0]['name'] = 'motion-result.json'
            peer.rehash_manifest()
        elif bad == 'diagram_schema':
            result['schema'] = 'video.motion-result.v1'
            peer.content = canonical_bytes(result)
            peer.manifest['files'][0].update(size=len(peer.content), sha256=digest(result))
            peer.manifest['total_bytes'] = len(peer.content)
            peer.rehash_manifest()
        else:
            peer.job['stop_proven'] = False
        with pytest.raises(ServiceError):
            gateway.collect(JOB, digest(doc), doc['beats'])


def test_real_process_wire_restores_after_uncertain_submission_through_original_profile(wire, state):
    gateway, peer = wire
    base, motion, pid, sid = state
    op = start_process(state)
    peer.lose_response = True
    legacy = FakeGateway()
    MotionWorkflow(motion, legacy, enabled=True, process_gateway=gateway, process_enabled=True).tick()
    assert motion.operation(op['id'])['error'] == 'submission_uncertain'
    restored = MotionStore(Store(base.path))
    # Both gates are now off; reconciliation must still look up the admitted process key.
    workflow = MotionWorkflow(restored, legacy, enabled=False, process_gateway=gateway, process_enabled=False)
    workflow.tick()
    assert restored.operation(op['id'])['job_id'] == JOB
    peer.complete(op['id'], restored.operation(op['id'])['document'])
    workflow.tick()
    assert restored.operation(op['id'])['status'] == 'completed'
    assert peer.count('POST', '/v1/jobs') == peer.count('POST', '/v1/inputs') == 1
    assert legacy.posts == legacy.uploads == legacy.lookups == 0
    assert restored.project(OWNER, pid)['scenes'][0]['motion']['head_version_id'] is None


def test_process_gate_cancels_only_unsubmitted_and_missing_adapter_never_uses_diagram(state):
    _, motion, _, _ = state
    op = start_process(state)
    legacy = FakeGateway()
    workflow = MotionWorkflow(motion, legacy, enabled=True, process_enabled=False)
    with pytest.raises(ServiceError, match='process_disabled'):
        workflow.require_gateway('process')
    assert workflow.require_gateway() is legacy
    workflow.tick()
    assert motion.operation(op['id'])['status'] == 'cancelled' and legacy.posts == 0
    uncertain = start_process(state, key='already-submitted')
    motion.claim_submission(uncertain['id'])
    workflow.tick()
    saved = motion.operation(uncertain['id'])
    assert saved['status'] == 'submitting' and saved['error'] == 'agent_not_configured'
    assert legacy.lookups == legacy.uploads == legacy.posts == 0


def wav(seconds=18):
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\x01\x00' * int(24000 * seconds))
    return output.getvalue()


def process_with_audio(state):
    base, motion, pid, sid = state
    vid, _ = complete_process(motion, start_process(state))
    project = base.project(OWNER, pid)
    project = base.upload_audio(OWNER, sid, dict(expected_revision=project['revision']), 'local-audio', wav())
    aid = project['scenes'][0]['audio_versions'][0]['id']
    return vid, aid


def test_process_timing_uses_actual_local_wav_pinned_compiler_and_explicit_human_review(state):
    _, motion, _, _ = state
    vid, aid = process_with_audio(state)
    timing = MotionTiming(motion)
    current = timing.get(OWNER, vid, aid)
    assert current['timeline']['compiler'] == PROCESS_CANDIDATE_COMPILER
    assert current['audio']['duration_seconds'] == 18
    assert current['requires_review'] is True and current['alignment']['review_status'] == 'pending'
    checked = timing.save(OWNER, vid, dict(expected_revision=current['project_revision'],
        audio_version_id=aid, expected_alignment_sha256=current['alignment_sha256']), verify=True)
    assert checked['requires_review'] is False and checked['alignment']['reviewed_by'] == OWNER
    assert checked['alignment']['audio_sha256'] == current['alignment']['audio_sha256']
    assert checked['timeline']['actions'] == current['timeline']['actions']


@pytest.mark.parametrize('compiler', [None, 'motion-process-compiler-1', 'motion-compiler-5', 'unknown'])
def test_process_timing_never_reinterprets_missing_or_old_compiler(state, compiler):
    base, motion, _, _ = state
    vid, aid = process_with_audio(state)
    with base.transaction() as db:
        version = motion._version(db, OWNER, vid)
        if compiler is None:
            version.pop('compiler_version')
        else:
            version['compiler_version'] = compiler
        db.execute('UPDATE motion_versions SET document=? WHERE id=?', (json.dumps(version), vid))
    with pytest.raises(ServiceError, match='process_candidate_compiler_invalid'):
        MotionTiming(motion).get(OWNER, vid, aid)


def test_unknown_persisted_schema_is_rejected_instead_of_diagram_fallback():
    for plan in [None, {}, {'schema': True}, {'schema': 'unknown'}]:
        with pytest.raises(ServiceError):
            plan_diagram_kind(plan)
    with pytest.raises(ServiceError):
        operation_diagram_kind({'document': {'schema': 'unknown'}})
