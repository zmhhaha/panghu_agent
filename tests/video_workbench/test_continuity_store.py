"""Independent project continuity candidates, exact input binding and explicit heads."""
from copy import deepcopy
import json

import pytest

from agent_service.contracts import ServiceError
from video_workbench import continuity_store as module
from video_workbench.continuity_store import ContinuityStore
from video_workbench.motion import example_plan
from video_workbench.motion_store import MotionStore
from video_workbench.store import Store


OWNER = 'alice'


def scene_fields(title):
    return dict(title=title, narration='先接收请求。再放入队列。最后执行任务。',
                visual_description='展示请求、队列、工作节点的关系。', duration_seconds=15, format='svg')


def add_motion(base, motion, project_id, scene_id, key, *, adopt=True):
    project = base.project(OWNER, project_id)
    started = motion.start(OWNER, scene_id, dict(expected_revision=project['revision']), key)
    operation = motion.operation(started['id'], OWNER)
    result = dict(schema='video.motion-result.v1', input_sha256=operation['input_sha256'],
                  plan=example_plan(operation['document']['beats']))
    version_id = motion.complete(started['id'], result)['version_id']
    if adopt:
        current = motion.project(OWNER, project_id)
        scene = next(scene for scene in current['scenes'] if scene['id'] == scene_id)
        motion.adopt(OWNER, scene_id, version_id, scene['motion']['head_version_id'], current['revision'])
    return version_id


@pytest.fixture
def state(tmp_path):
    base = Store(tmp_path / 'state.sqlite')
    base.configure_speech_provider('kokoro-rk3588', 'zm_yunjian', ['zm_yunjian', 'zm_yunxi'])
    project = base.create_project(OWNER, dict(title='连续讲解', script='先排队，再执行。',
                                             style='清晰深色图解', production_mode='motion'))
    for title in ('请求排队', '任务执行', '结果返回'):
        project = base.save_scene(OWNER, project['id'], scene_fields(title))
    motion = MotionStore(base)
    for index, scene in enumerate(project['scenes']):
        add_motion(base, motion, project['id'], scene['id'], f'motion-{index}')
    return base, motion, ContinuityStore(base, motion), project['id']


def revision(state):
    return state[0].project(OWNER, state[3])['revision']


def propose(state, links=None):
    return state[2].propose(OWNER, state[3], revision(state), links)


def adopt(state, candidate, head=None):
    return state[2].adopt(OWNER, state[3], candidate['id'], revision(state), head)


def raw_versions(base):
    with base.transaction() as db:
        return [tuple(row) for row in db.execute('SELECT rowid,* FROM continuity_versions ORDER BY rowid')]


def unrelated_rows(base):
    with base.transaction() as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if row[0] not in {'continuity_versions', 'continuity_heads', 'projects'}]
        return {name: [tuple(row) for row in db.execute(f'SELECT rowid,* FROM "{name}" ORDER BY rowid')]
                for name in tables}


def test_proposal_is_reviewable_without_head_media_or_project_mutation(state):
    base, motion, continuity, project_id = state
    before = base.project(OWNER, project_id)
    independent = unrelated_rows(base)
    candidate = propose(state)
    assert candidate['plan']['schema'] == 'video.continuity-plan.v1'
    assert candidate['plan']['links']
    assert candidate['adoptable'] and not candidate['stale'] and not candidate['adopted']
    assert base.project(OWNER, project_id) == before
    assert unrelated_rows(base) == independent
    projection = continuity.project_projection(OWNER, project_id)
    assert projection['project_revision'] == before['revision']
    assert projection['head_version_id'] is None and not projection['head_stale']
    assert projection['issues'] == [] and projection['versions'] == [candidate]
    assert [row['scene_id'] for row in projection['scene_summaries']] == [scene['id'] for scene in before['scenes']]
    assert all(row['elements'] for row in projection['scene_summaries'])
    assert continuity.render_selection(OWNER, project_id) is None
    selected = continuity.render_selection(OWNER, project_id, candidate['id'])
    assert selected['version_id'] == candidate['id'] and selected['plan'] == candidate['plan']
    assert continuity.project_projection(OWNER, project_id)['head_version_id'] is None


def test_explicit_adoption_only_changes_continuity_head_and_project_metadata(state):
    base, motion, continuity, project_id = state
    candidate = propose(state)
    before = base.project(OWNER, project_id)
    independent = unrelated_rows(base)
    versions = raw_versions(base)
    result = adopt(state, candidate)
    after = base.project(OWNER, project_id)
    assert result['head_version_id'] == candidate['id'] and result['project_revision'] == before['revision'] + 1
    assert after['scenes'] == before['scenes']
    assert {key: value for key, value in after.items() if key not in {'revision', 'updated_at'}} == {
        key: value for key, value in before.items() if key not in {'revision', 'updated_at'}}
    assert unrelated_rows(base) == independent
    assert raw_versions(base) == versions
    assert continuity.get(OWNER, candidate['id'])['adopted']
    assert continuity.render_selection(OWNER, project_id)['version_id'] == candidate['id']
    repeated = adopt(state, candidate, candidate['id'])
    assert repeated['project_revision'] == result['project_revision']
    assert raw_versions(base) == versions


def test_edited_links_create_new_immutable_candidate_and_explicit_empty_is_preserved(state):
    base, motion, continuity, project_id = state
    first = propose(state)
    original = raw_versions(base)
    links = deepcopy(first['plan']['links'])
    links[0]['mode'] = 'copy'
    second = propose(state, links)
    empty = propose(state, [])
    assert second['id'] != first['id'] and second['plan']['links'][0]['mode'] == 'copy'
    assert empty['plan']['links'] == []
    links.clear()
    assert continuity.get(OWNER, second['id'])['plan']['links']
    assert raw_versions(base)[0] == original[0]
    assert continuity.get(OWNER, first['id'])['plan'] == first['plan']
    assert continuity.project_projection(OWNER, project_id)['head_version_id'] is None
    adopt(state, second)
    assert not continuity.get(OWNER, first['id'])['stale']
    assert not continuity.get(OWNER, empty['id'])['stale']


def test_owner_and_project_isolation_for_reads_adoption_and_render_selection(state):
    base, motion, continuity, project_id = state
    candidate = propose(state)
    for action in [lambda: continuity.propose('bob', project_id, revision(state)),
                   lambda: continuity.get('bob', candidate['id']),
                   lambda: continuity.list('bob', project_id),
                   lambda: continuity.project_projection('bob', project_id),
                   lambda: continuity.adopt('bob', project_id, candidate['id'], revision(state), None),
                   lambda: continuity.render_selection('bob', project_id, candidate['id'])]:
        with pytest.raises(ServiceError, match='not_found'):
            action()
    other = base.create_project(OWNER, dict(title='其他项目', script='另一个文稿', style=''))
    for action in [lambda: continuity.adopt(OWNER, other['id'], candidate['id'], other['revision'], None),
                   lambda: continuity.render_selection(OWNER, other['id'], candidate['id'])]:
        with pytest.raises(ServiceError, match='not_found'):
            action()


def test_revision_and_head_compare_and_swap_fail_without_partial_writes(state):
    base, motion, continuity, project_id = state
    first, second = propose(state), propose(state)
    old_revision = revision(state)
    adopt(state, first)
    versions = raw_versions(base)
    with pytest.raises(ServiceError, match='revision_conflict'):
        continuity.adopt(OWNER, project_id, second['id'], old_revision, first['id'])
    with pytest.raises(ServiceError, match='continuity_head_conflict'):
        continuity.adopt(OWNER, project_id, second['id'], revision(state), None)
    assert continuity.project_projection(OWNER, project_id)['head_version_id'] == first['id']
    assert raw_versions(base) == versions
    adopt(state, second, first['id'])
    assert continuity.project_projection(OWNER, project_id)['head_version_id'] == second['id']


@pytest.mark.parametrize('change', ['narration', 'visual_description', 'scene_title', 'style', 'order', 'motion_head', 'mode'])
def test_exact_semantic_order_and_motion_head_binding_invalidates_candidate(state, change):
    base, motion, continuity, project_id = state
    candidate = propose(state)
    adopt(state, candidate)
    original_version_rows = raw_versions(base)
    project = base.project(OWNER, project_id)
    first = project['scenes'][0]
    if change == 'motion_head':
        add_motion(base, motion, project_id, first['id'], 'replacement-motion')
    elif change == 'mode':
        motion.set_mode(OWNER, first['id'], 'static', project['revision'])
    else:
        with base.transaction() as db:
            document = base._project(db, OWNER, project_id)
            if change == 'order':
                document['scenes'].reverse()
            elif change == 'style':
                document['style'] += '更新'
            else:
                key = 'title' if change == 'scene_title' else change
                document['scenes'][0][key] += '更新'
            base._save(db, document)
    assert continuity.get(OWNER, candidate['id'])['stale']
    projection = continuity.project_projection(OWNER, project_id)
    assert projection['head_stale'] and not projection['versions'][0]['adoptable']
    with pytest.raises(ServiceError, match='continuity_plan_stale'):
        continuity.adopt(OWNER, project_id, candidate['id'], revision(state), candidate['id'])
    with pytest.raises(ServiceError, match='continuity_plan_stale'):
        continuity.render_selection(OWNER, project_id)
    with pytest.raises(ServiceError, match='continuity_plan_stale'):
        continuity.render_selection(OWNER, project_id, candidate['id'])
    assert raw_versions(base) == original_version_rows


def test_audio_voice_speech_and_unadopted_motion_candidate_do_not_invalidate_plan(state):
    base, motion, continuity, project_id = state
    candidate = propose(state)
    original_binding = candidate['input_binding_sha256']
    project = base.project(OWNER, project_id)
    first = project['scenes'][0]
    project = base.save_project_speech_settings(OWNER, project_id,
                                               dict(expected_revision=project['revision'], voice='zm_yunxi'))
    base.save_speech_settings(OWNER, first['id'], dict(expected_revision=project['revision'], speed=1.2,
                                                     pronunciation={'请求': '请 求'}))
    add_motion(base, motion, project_id, first['id'], 'unadopted-motion', adopt=False)
    current = continuity.get(OWNER, candidate['id'])
    assert not current['stale'] and current['input_binding_sha256'] == original_binding
    assert 'audio' not in json.dumps(current['input_binding']) and 'alignment' not in json.dumps(current['input_binding'])
    adopt(state, candidate)


def test_missing_adopted_motion_is_reported_and_no_candidate_is_created(state):
    base, motion, continuity, project_id = state
    project = base.project(OWNER, project_id)
    base.save_scene(OWNER, project_id, scene_fields('尚无图解'))
    projection = continuity.project_projection(OWNER, project_id)
    assert projection['issues'][-1]['reason'] == 'continuity_missing_motion'
    assert projection['scene_summaries'][-1]['elements'] == []
    with pytest.raises(ServiceError, match='continuity_inputs_unavailable'):
        propose(state)
    assert continuity.list(OWNER, project_id) == []
    empty = base.create_project(OWNER, dict(title='空项目', script='待制作', style='', production_mode='motion'))
    assert continuity.project_projection(OWNER, empty['id'])['issues'][0]['reason'] == 'continuity_requires_scenes'
    single = base.save_scene(OWNER, empty['id'], scene_fields('只有一镜'))
    add_motion(base, motion, single['id'], single['scenes'][0]['id'], 'single-motion')
    assert continuity.project_projection(OWNER, single['id'])['issues'][0]['reason'] == 'continuity_requires_scenes'
    with pytest.raises(ServiceError, match='continuity_inputs_unavailable'):
        continuity.propose(OWNER, single['id'], base.project(OWNER, single['id'])['revision'])


def test_invalid_link_rolls_back_and_does_not_change_previous_candidates(state):
    base, motion, continuity, project_id = state
    valid = propose(state)
    before = raw_versions(base)
    links = deepcopy(valid['plan']['links'])
    links[0]['to_element_id'] = 'not-an-element'
    with pytest.raises(ServiceError):
        propose(state, links)
    assert raw_versions(base) == before
    assert continuity.project_projection(OWNER, project_id)['head_version_id'] is None


def test_restart_preserves_candidates_heads_and_summaries(state):
    base, motion, continuity, project_id = state
    candidate = propose(state)
    adopt(state, candidate)
    expected = continuity.project_projection(OWNER, project_id)
    restarted = ContinuityStore(Store(base.path))
    assert restarted.project_projection(OWNER, project_id) == expected
    assert restarted.render_selection(OWNER, project_id)['version_id'] == candidate['id']


def test_capacity_admission_is_atomic(state, monkeypatch):
    base, motion, continuity, project_id = state
    propose(state)
    before = raw_versions(base)
    monkeypatch.setattr(module, 'MAX_PROJECT_CANDIDATES', 1)
    with pytest.raises(ServiceError, match='continuity_candidate_limit'):
        propose(state)
    assert raw_versions(base) == before
    monkeypatch.setattr(module, 'MAX_PROJECT_CANDIDATES', 64)
    monkeypatch.setattr(module, 'MAX_VERSION_BYTES', 1)
    with pytest.raises(ServiceError, match='continuity_storage_limit'):
        propose(state)
    assert raw_versions(base) == before
