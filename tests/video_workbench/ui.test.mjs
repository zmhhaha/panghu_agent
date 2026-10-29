import test from 'node:test';
import assert from 'node:assert/strict';
import {
  acceptProjectRead, activeOperations, chooseVersion, createIntentKeys, createSceneDraft,
  createWorkspaceState, intentFingerprint, issueProjectRead, selectProject, selectScene,
  selectedVersion, syncSceneDraft, updateProjectSummary, versionPath,
} from '../../video_workbench/web/model.mjs';

function project(id = 'a', revision = 1, versions = []) {
  return { id, revision, scenes: [{ id: 'scene-1', title: '第一镜头', narration: '原旁白',
    format: 'svg', duration_seconds: 8, versions, head_version_id: null }] };
}
function open(state, data) {
  selectProject(state, data.id);
  assert.equal(acceptProjectRead(state, issueProjectRead(state), data), true);
}

test('project switch rejects the previous project response, including switching back', () => {
  const state = createWorkspaceState();
  selectProject(state, 'a');
  const oldA = issueProjectRead(state);
  selectProject(state, 'b');
  assert.equal(acceptProjectRead(state, oldA, project('a')), false);
  selectProject(state, 'a');
  assert.equal(acceptProjectRead(state, oldA, project('a')), false);
  assert.equal(state.project, null);
});

test('out of order reads and revision regressions cannot replace newer project state', () => {
  const state = createWorkspaceState();
  selectProject(state, 'a');
  const older = issueProjectRead(state);
  const newer = issueProjectRead(state);
  assert.equal(acceptProjectRead(state, newer, project('a', 3)), true);
  assert.equal(acceptProjectRead(state, older, project('a', 4)), false);
  assert.equal(acceptProjectRead(state, issueProjectRead(state), project('a', 2)), false);
  assert.equal(state.project.revision, 3);
});

test('a candidate arriving or another adopted head does not move the selected preview', () => {
  const state = createWorkspaceState();
  open(state, project('a', 1, [{ id: 'v1', number: 1 }, { id: 'v2', number: 2 }]));
  chooseVersion(state, 'scene-1', 'v1');
  const updated = project('a', 2, [{ id: 'v1', number: 1 }, { id: 'v2', number: 2 }, { id: 'v3', number: 3 }]);
  updated.scenes[0].head_version_id = 'v3';
  acceptProjectRead(state, issueProjectRead(state), updated);
  assert.equal(selectedVersion(state).id, 'v1');
  assert.equal(state.project.scenes[0].head_version_id, 'v3');
});

test('the first completed candidate becomes viewable without being adopted', () => {
  const state = createWorkspaceState();
  open(state, project());
  assert.equal(selectedVersion(state), null);
  acceptProjectRead(state, issueProjectRead(state), project('a', 2, [{ id: 'v1', number: 1 }]));
  assert.equal(selectedVersion(state).id, 'v1');
  assert.equal(state.project.scenes[0].head_version_id, null);
});

test('polling preserves the selected scene and chooses adopted head on first read', () => {
  const state = createWorkspaceState();
  const initial = project('a', 1, [{ id: 'v1', number: 1 }, { id: 'v2', number: 2 }]);
  initial.scenes[0].head_version_id = 'v1';
  initial.scenes.push({ id: 'scene-2', versions: [] });
  open(state, initial);
  assert.equal(selectedVersion(state).id, 'v1');
  selectScene(state, 'scene-2');
  acceptProjectRead(state, issueProjectRead(state), { ...initial, revision: 2 });
  assert.equal(state.sceneId, 'scene-2');
});

test('a detail refresh updates its sidebar count, title and timestamp without another list read', () => {
  const original = [
    { id: 'a', title: '旧标题', scene_count: 0, updated_at: 1 },
    { id: 'b', title: '其他项目', scene_count: 3, updated_at: 2 },
  ];
  const detail = { ...project('a', 2), title: '更新后的标题', updated_at: 9 };
  detail.scenes.push({ id: 'scene-2', versions: [] });
  const updated = updateProjectSummary(original, detail);
  assert.deepEqual(updated[0], { id: 'a', title: '更新后的标题', scene_count: 2, updated_at: 9 });
  assert.equal(updated[1], original[1]);
  assert.equal(original[0].scene_count, 0);
  assert.equal(updateProjectSummary(updated, { ...detail, updated_at: undefined })[0].updated_at, 9);
});

test('dirty drafts keep user input and their original concurrency revision on poll', () => {
  const scene = project().scenes[0];
  const draft = createSceneDraft(scene, 1);
  draft.title = '我还在写';
  draft.dirty = true;
  const result = syncSceneDraft(draft, { ...scene, title: '其他客户端的新标题' }, 9);
  assert.equal(result, draft);
  assert.equal(result.title, '我还在写');
  assert.equal(result.expected_revision, 1);
  const clean = syncSceneDraft(createSceneDraft(scene, 1), { ...scene, title: '新标题' }, 9);
  assert.equal(clean.title, '新标题');
  assert.equal(clean.expected_revision, 9);
});

test('uncertain retries reuse intent keys and confirmed success retires them', () => {
  let sequence = 0;
  const keys = createIntentKeys(() => `key-${++sequence}`);
  const first = keys.get('POST', '/generate', { instruction: '放大文字', expected_revision: 2 });
  assert.equal(keys.get('POST', '/generate', { expected_revision: 2, instruction: '放大文字' }), first);
  assert.notEqual(keys.get('POST', '/generate', { instruction: '缩小文字', expected_revision: 2 }), first);
  keys.complete('POST', '/generate', { expected_revision: 2, instruction: '放大文字' });
  assert.notEqual(keys.get('POST', '/generate', { instruction: '放大文字', expected_revision: 2 }), first);
  assert.notEqual(intentFingerprint('POST', '/a', {}), intentFingerprint('POST', '/b', {}));
});

test('polling tracks submitting and cancelling operations, deduplicates and excludes terminal states', () => {
  const operation = { id: 'operation-1', status: 'submitting' };
  const data = { storyboard_operation: operation, scenes: [
    { operation }, { operation: { id: 'operation-2', status: 'cancelling' } },
    { operation: { id: 'operation-3', status: 'completed' } },
    { operation: { id: 'operation-4', status: 'unexpected' } },
  ] };
  assert.deepEqual(activeOperations(data).map(item => item.id), ['operation-1', 'operation-2']);
});

test('version URLs only address immutable preview/source paths and encode untrusted IDs', () => {
  assert.equal(versionPath('../other?x=1', 'source'), '/api/versions/..%2Fother%3Fx%3D1/source');
  assert.equal(versionPath('v1'), '/api/versions/v1/preview');
  assert.throws(() => versionPath('v1', '../admin'));
});
