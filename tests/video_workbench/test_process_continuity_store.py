"""Real SQLite candidate/adoption boundaries; no audio, Agent or renderer calls."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError, canonical_bytes
from video_workbench.continuity_store import ContinuityStore, PROCESS_COMPILER, LEGACY_COMPILER
from video_workbench.app import create_app
from video_workbench.motion_store import MotionStore
from video_workbench.store import Store
from test_continuity_store import OWNER, add_motion, scene_fields, unrelated_rows


def process_plan(beats, start, end):
    ids = [row['id'] for row in beats['segments']]
    return dict(schema='video.process-plan.v1', template='flow', title='请求继续传递', takeaway='保留请求与节点状态。',
        elements=[dict(id=slot, kind='card', slot=slot, label=label, detail='保持处理记录')
                  for slot, label in [('source', '入口'), ('queue', '队列'), ('worker', '执行节点')]] + [
            dict(id='token', kind='token', slot='token_' + start, label='请求', detail='')],
        relations=[dict(id='edge', source=start, target=end, label='传递')], actions=[
            dict(kind='draw', target='edge', beat_id=ids[0], placement='start', pace='normal'),
            dict(kind='reveal', target='token', beat_id=ids[0], placement='middle', pace='quick'),
            dict(kind='transfer', target='token', relation_id='edge', beat_id=ids[1], placement='start', pace='slow'),
            dict(kind='set_state', target='worker', state='running', beat_id=ids[2], placement='start', pace='normal')])


def add_process(base, motion, project_id, scene_id, key, start='source', end='queue'):
    started = motion.start(OWNER, scene_id,
        dict(expected_revision=base.project(OWNER, project_id)['revision'], diagram_kind='process'), key)
    operation = motion.operation(started['id'], OWNER)
    result = dict(schema='video.process-result.v1', input_sha256=operation['input_sha256'],
                  plan=process_plan(operation['document']['beats'], start, end))
    version_id = motion.complete(started['id'], result)['version_id']
    project = motion.project(OWNER, project_id)
    scene = next(row for row in project['scenes'] if row['id'] == scene_id)
    motion.adopt(OWNER, scene_id, version_id, scene['motion']['head_version_id'], project['revision'])
    return version_id


@pytest.fixture
def state(tmp_path):
    base = Store(tmp_path / 'state.sqlite')
    base.configure_speech_provider('kokoro-rk3588', 'zm_yunjian', ['zm_yunjian', 'zm_yunxi'])
    project = base.create_project(OWNER, dict(title='连续过程', script='请求持续处理。', style='清晰', production_mode='motion'))
    for title in ['进入队列', '送交执行', '失败返回']:
        project = base.save_scene(OWNER, project['id'], scene_fields(title))
    motion = MotionStore(base)
    for index, (scene, (start, end)) in enumerate(zip(project['scenes'], [('source', 'queue'), ('queue', 'worker'), ('worker', 'queue')])):
        add_process(base, motion, project['id'], scene['id'], f'process-{index}', start, end)
    return base, motion, ContinuityStore(base, motion), project['id']


def revision(state):
    return state[0].project(OWNER, state[3])['revision']


def propose(state, links=None):
    return state[2].propose(OWNER, state[3], revision(state), links)


def test_process_candidates_without_audio_freeze_compiler_and_effective_boundary_summary(state):
    base, _, continuity, project_id = state
    before, independent = base.project(OWNER, project_id), unrelated_rows(base)
    candidate = propose(state)
    assert candidate['compiler'] == PROCESS_COMPILER and candidate['plan']['links']
    assert all(scene.get('head_audio_version_id') is None for scene in before['scenes'])
    assert base.project(OWNER, project_id) == before and unrelated_rows(base) == independent
    assert continuity.project_projection(OWNER, project_id)['head_version_id'] is None
    first, second, third = candidate['scene_summaries']
    worker = lambda summary: next(row for row in summary['elements'] if row['id'] == 'worker')
    token = lambda summary: next(row for row in summary['elements'] if row['kind'] == 'token')
    assert worker(first)['initial_state'] == 'idle' and worker(first)['final_state'] == 'running'
    assert worker(second)['initial_state'] == 'running' and worker(second)['standalone_initial_state'] == 'idle'
    assert worker(third)['initial_state'] == 'running'
    assert token(first)['final_slot'] == token(second)['initial_slot'] == 'token_queue'
    selected = continuity.render_selection(OWNER, project_id, candidate['id'])
    assert selected['compiler'] == PROCESS_COMPILER and selected['version_id'] == candidate['id']
    assert 'audio' not in json.dumps(candidate['input_binding'])
    restarted = ContinuityStore(Store(base.path))
    assert restarted.get(OWNER, candidate['id']) == candidate


def test_process_copy_only_for_token_and_new_candidate_preserves_original(state):
    base, _, continuity, project_id = state
    original = propose(state)
    links = deepcopy(original['plan']['links'])
    card = next(row for row in links if row['from_element_id'] != 'token')
    card['mode'] = 'copy'
    with pytest.raises(ServiceError, match='process_continuity_card_copy_not_supported'):
        propose(state, links)
    assert len(continuity.list(OWNER, project_id)) == 1
    links = deepcopy(original['plan']['links'])
    next(row for row in links if row['from_element_id'] == 'token')['mode'] = 'copy'
    copied = propose(state, links)
    assert copied['id'] != original['id'] and copied['compiler'] == PROCESS_COMPILER
    assert continuity.get(OWNER, original['id'])['plan'] == original['plan']
    assert continuity.project_projection(OWNER, project_id)['head_version_id'] is None


def test_explicit_empty_process_links_remain_empty_and_restore_standalone_initial_state(state):
    _, _, continuity, project_id = state
    original = propose(state)
    candidate = propose(state, [])
    assert original['plan']['links'] and candidate['plan']['links'] == []
    assert candidate['compiler'] == PROCESS_COMPILER
    for summary in candidate['scene_summaries']:
        worker = next(row for row in summary['elements'] if row['id'] == 'worker')
        assert worker['initial_state'] == worker['standalone_initial_state'] == 'idle'
    continuity.adopt(OWNER, project_id, candidate['id'], revision(state), None)
    assert continuity.render_selection(OWNER, project_id)['plan']['links'] == []
    assert continuity.get(OWNER, original['id'])['plan'] == original['plan']


def test_process_adoption_keeps_owner_revision_and_head_cas(state):
    _, _, continuity, project_id = state
    first, second = propose(state), propose(state)
    before = revision(state)
    for action in [lambda: continuity.get('bob', first['id']),
                   lambda: continuity.render_selection('bob', project_id, first['id']),
                   lambda: continuity.adopt('bob', project_id, first['id'], before, None)]:
        with pytest.raises(ServiceError, match='not_found'):
            action()
    continuity.adopt(OWNER, project_id, first['id'], before, None)
    with pytest.raises(ServiceError, match='revision_conflict'):
        continuity.adopt(OWNER, project_id, second['id'], before, first['id'])
    with pytest.raises(ServiceError, match='continuity_head_conflict'):
        continuity.adopt(OWNER, project_id, second['id'], revision(state), None)
    assert continuity.render_selection(OWNER, project_id)['id'] == first['id']


def test_process_audio_settings_do_not_stale_visual_plan_but_new_motion_head_does(state):
    base, motion, continuity, project_id = state
    candidate = propose(state)
    project = base.save_project_speech_settings(OWNER, project_id,
        dict(expected_revision=revision(state), voice='zm_yunxi'))
    assert not continuity.get(OWNER, candidate['id'])['stale']
    first = project['scenes'][0]
    add_process(base, motion, project_id, first['id'], 'replacement')
    assert continuity.get(OWNER, candidate['id'])['stale']
    with pytest.raises(ServiceError, match='continuity_plan_stale'):
        continuity.render_selection(OWNER, project_id, candidate['id'])


def test_mixed_process_and_diagram_rejected_without_changing_history(state):
    base, motion, continuity, project_id = state
    candidate = propose(state)
    scene = base.project(OWNER, project_id)['scenes'][0]
    add_motion(base, motion, project_id, scene['id'], 'ordinary-replacement')
    projection = continuity.project_projection(OWNER, project_id)
    assert any(row['reason'] == 'process_continuity_mixed_not_supported' for row in projection['issues'])
    for action in [lambda: propose(state), lambda: continuity.render_selection(OWNER, project_id, candidate['id'])]:
        with pytest.raises(ServiceError, match='process_continuity_mixed_not_supported'):
            action()
    assert len(continuity.list(OWNER, project_id)) == 1


@pytest.mark.parametrize('compiler', [None, 'motion-process-compiler-1', 'unknown'])
def test_unsupported_source_compiler_cannot_be_silently_upgraded(state, compiler):
    base, motion, continuity, project_id = state
    candidate = propose(state)
    with base.transaction() as db:
        project = base._project(db, OWNER, project_id)
        version_id = motion._head(db, OWNER, project_id, project['scenes'][0]['id'])['version_id']
        version = motion._version(db, OWNER, version_id)
        version['compiler_version'] = compiler
        db.execute('UPDATE motion_versions SET document=? WHERE id=?', (canonical_bytes(version).decode(), version_id))
    assert continuity.get(OWNER, candidate['id'])['stale']
    with pytest.raises(ServiceError, match='process_continuity_source_unsupported'):
        propose(state)


@pytest.mark.parametrize('compiler', ['absent', None, LEGACY_COMPILER, 'unknown'])
def test_process_candidate_cannot_claim_or_fall_back_to_old_diagram_compiler(state, compiler):
    base, _, continuity, project_id = state
    candidate = propose(state)
    with base.transaction() as db:
        row = json.loads(db.execute('SELECT document FROM continuity_versions WHERE id=?', (candidate['id'],)).fetchone()[0])
        if compiler == 'absent':
            row.pop('compiler')
        else:
            row['compiler'] = compiler
        db.execute('UPDATE continuity_versions SET document=? WHERE id=?', (canonical_bytes(row).decode(), candidate['id']))
    assert continuity.get(OWNER, candidate['id'])['stale']
    assert not continuity.get(OWNER, candidate['id'])['adoptable']
    with pytest.raises(ServiceError, match='continuity_compiler_'):
        continuity.render_selection(OWNER, project_id, candidate['id'])


def test_legacy_candidate_missing_compiler_keeps_original_diagram_version(tmp_path):
    base = Store(tmp_path / 'legacy.sqlite')
    project = base.create_project(OWNER, dict(title='历史连续', script='请求处理。', style='', production_mode='motion'))
    for title in ['排队', '执行']:
        project = base.save_scene(OWNER, project['id'], scene_fields(title))
    motion = MotionStore(base)
    for index, scene in enumerate(project['scenes']):
        add_motion(base, motion, project['id'], scene['id'], f'legacy-{index}')
    continuity = ContinuityStore(base, motion)
    candidate = continuity.propose(OWNER, project['id'], base.project(OWNER, project['id'])['revision'])
    with base.transaction() as db:
        row = json.loads(db.execute('SELECT document FROM continuity_versions WHERE id=?', (candidate['id'],)).fetchone()[0])
        row.pop('compiler')
        original = canonical_bytes(row).decode()
        db.execute('UPDATE continuity_versions SET document=? WHERE id=?', (original, candidate['id']))
    selected = continuity.render_selection(OWNER, project['id'], candidate['id'])
    assert selected['compiler'] == LEGACY_COMPILER
    assert not continuity.get(OWNER, candidate['id'])['stale']
    with base.transaction() as db:
        assert db.execute('SELECT document FROM continuity_versions WHERE id=?', (candidate['id'],)).fetchone()[0] == original


def test_process_api_proposals_keep_recordings_optional_and_explain_invalid_choices(state):
    base, motion, continuity, project_id = state
    app = create_app(db_path=base.path, background=False, motion_enabled=True, trusted_hosts={'testserver'})
    with TestClient(app) as client:
        client.headers['X-Forwarded-User'] = OWNER
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        path = f'/api/projects/{project_id}/continuity/proposals'
        response = client.post(path, json=dict(expected_revision=revision(state)))
        assert response.status_code == 201, response.text
        candidate = response.json()
        assert candidate['compiler'] == PROCESS_COMPILER and not candidate['stale']
        links = deepcopy(candidate['plan']['links'])
        next(row for row in links if row['from_element_id'] != 'token')['mode'] = 'copy'
        response = client.post(path, json=dict(expected_revision=revision(state), links=links))
        assert response.status_code == 422
        detail = response.json()['detail']
        assert detail['code'] == 'process_continuity_card_copy_not_supported'
        assert '处理节点' in detail['message'] and 'process_' not in detail['message']
        assert len(continuity.list(OWNER, project_id)) == 1
        scene = base.project(OWNER, project_id)['scenes'][0]
        add_motion(base, motion, project_id, scene['id'], 'mixed-api')
        response = client.post(path, json=dict(expected_revision=revision(state)))
        assert response.status_code == 409
        detail = response.json()['detail']
        assert detail['code'] == 'process_continuity_mixed_not_supported'
        assert '混合' in detail['message'] and '普通整片草稿' in detail['message']
