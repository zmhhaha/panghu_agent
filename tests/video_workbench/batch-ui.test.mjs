import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createBatchPanel, createBatchRequest, createBatchAdoption, batchSceneResults, batchErrorLabel } from '../../video_workbench/web/batch.mjs';

const html = await readFile(new URL('../../video_workbench/web/index.html', import.meta.url), 'utf8');
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.listeners = new Map(); this.dataset = {};
    this.value = ''; this.content = ''; this.disabled = false; this.hidden = false;
    this.checked = false; this.open = false; this.paused = true;
  }
  set textContent(value) { this.content = String(value); this.children = []; }
  get textContent() { return [this.content, ...this.children.map(row => row.textContent)].join('\n'); }
  set innerHTML(value) { throw new Error(`Executable HTML: ${value}`); }
  append(...elements) { this.children.push(...elements); }
  replaceChildren(...elements) { this.content = ''; this.children = [...elements]; }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(callback);
  }
  async emit(name) { for (const callback of this.listeners.get(name) ?? []) await callback({ target: this }); }
  pause() { this.paused = true; }
  async play() { this.paused = false; }
}
function project(id = 'p1', revision = 1) {
  return { id, revision, production_mode: 'motion', speech_settings: { voice: 'one-project-voice' }, scenes: [
    { id: 's1', title: '队列', narration: '先入队，再执行。', production_mode: 'motion',
      head_audio_version_id: null, audio_stale: false,
      audio_versions: [{ id: 'a1', number: 1, stale: false }, { id: 'a2', number: 2, stale: false }],
      motion: { mode: 'motion', head_version_id: null, versions: [{ id: 'm1', stale: false }, { id: 'm2', stale: false }] } },
  ] };
}
function batch(id = 'b1', status = 'completed') {
  return { id, project_id: 'p1', status, items: [
    { id: 'i-motion', scene_id: 's1', scene_title: '队列', step: 'motion', status: 'completed', version_id: 'm1' },
    { id: 'i-audio', scene_id: 's1', scene_title: '队列', step: 'audio', status: 'completed', version_id: 'a1' },
    { id: 'i-draft', scene_id: 's1', scene_title: '队列', step: 'draft', status: 'completed', render_id: 'r1', motion_version_id: 'm1', audio_version_id: 'a1' },
  ] };
}
function version(id, projectId = 'p1') {
  return { id, project_id: projectId, scene_id: 's1', stale: false,
    plan: { title: '<script>队列</script>', takeaway: '先排队再处理', elements: [{ label: '<img onerror=alert(1)>请求', detail: '进入队列' }] },
    beats: { segments: [{ source_text: '先入队。' }, { source_text: '再执行。' }] } };
}
function harness() {
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element()]));
  const document = { createElement: tag => new Element(tag), getElementById: id => { assert.ok(nodes.has(id), id); return nodes.get(id); } };
  const state = { context: { project: project(), epoch: 1, enabled: true, unsaved: false, projectBusy: false },
    batches: [batch()], calls: [], errors: [], inputErrors: [], successes: [], opens: [], plays: [], projectDrafts: 0,
    refreshes: 0, transport: null, versions: new Map() };
  let panel;
  const fallback = async (path, options) => {
    if (path.endsWith('/batches/preview')) return { plan_sha256: 'a'.repeat(64),
      estimated_tasks: { motion: 1, audio: 1, draft: 1 }, items: batch().items.map(item => ({ ...item, status: 'queued' })) };
    if (path.endsWith('/batch-adopt')) return structuredClone(state.context.project);
    if (path.endsWith('/batches')) {
      if (options.method === 'POST') { const next = batch('b2', 'queued'); state.batches.unshift(next); return structuredClone(next); }
      return { batches: structuredClone(state.batches) };
    }
    if (path.endsWith('/cancel')) { state.batches[0].status = 'cancelling'; return structuredClone(state.batches[0]); }
    if (path.endsWith('/retry')) { const next = batch('retry-1', 'queued'); next.parent_batch_id = state.batches[0].id; state.batches.unshift(next); return structuredClone(next); }
    if (path.startsWith('/api/batches/')) return structuredClone(state.batches.find(row => row.id === decodeURIComponent(path.split('/').at(-1))));
    if (path.startsWith('/api/motion-versions/')) {
      const id = decodeURIComponent(path.split('/').at(-1)); return structuredClone(state.versions.get(id) ?? version(id, state.context.project.id));
    }
    throw new Error(`Unexpected request ${path}`);
  };
  panel = createBatchPanel({ document, getContext: () => state.context,
    request: async (path, options = {}) => {
      state.calls.push({ path, options: structuredClone(options) });
      return state.transport ? state.transport(path, options, fallback) : fallback(path, options);
    },
    refresh: async () => { state.refreshes += 1; await panel.load(state.context.project.id, state.context.epoch); },
    onError: error => state.errors.push(error), onInputError: message => state.inputErrors.push(message),
    onSuccess: message => state.successes.push(message),
    onOpenScene: (...args) => state.opens.push(args), onPlayRender: item => state.plays.push(item),
    onProjectDraft: () => { state.projectDrafts += 1; },
  });
  return { state, panel, $: id => nodes.get(id), load: () => panel.load(state.context.project.id, state.context.epoch) };
}
const writes = h => h.state.calls.filter(row => row.options.method === 'POST');
const descendants = element => [element, ...element.children.flatMap(descendants)];
const inputIn = element => descendants(element).find(element => element.tagName === 'input');
const reviewCheck = h => descendants(h.$('batch-review')).find(element => element.tagName === 'input');

test('batch intent validates the selected project scenes, bounded steps and clean revision', () => {
  const p = project('../p?');
  const intent = createBatchRequest(p, { sceneIds: ['s1'], steps: ['draft', 'motion'] });
  assert.deepEqual(intent, { path: '/api/projects/..%2Fp%3F/batches/preview', body: {
    expected_revision: 1, scene_ids: ['s1'], steps: ['motion', 'draft'], reuse_current: true } });
  for (const change of [{ sceneIds: [] }, { sceneIds: ['s1', 's1'] }, { sceneIds: ['foreign'] },
    { steps: [] }, { steps: ['unknown'] }, { unsaved: true }, { busy: true }]) {
    assert.throws(() => createBatchRequest(p, { sceneIds: ['s1'], steps: ['motion'], ...change }));
  }
  p.scenes = Array.from({ length: 13 }, (_, index) => ({ id: `s${index}` }));
  assert.throws(() => createBatchRequest(p, { sceneIds: p.scenes.map(scene => scene.id), steps: ['motion'] }), /12/);
});

test('first read only restores batch progress and inert review; nothing is automatically submitted or adopted', async () => {
  const h = harness(); h.panel.render(); await h.load();
  assert.equal(writes(h).length, 0);
  assert.equal(h.$('batch-panel').hidden, false);
  assert.match(h.$('batch-review').textContent, /<script>队列<\/script>/);
  assert.match(h.$('batch-review').textContent, /<img onerror=alert\(1\)>请求/);
  assert.equal(reviewCheck(h).checked, false);
  assert.equal(h.$('adopt-batch').disabled, true);
  assert.equal(h.$('start-batch').disabled, true);
  assert.equal(h.state.context.project.scenes[0].motion.head_version_id, null);
});

test('range preview shows task caps and requires an explicit second confirmation to start exactly once', async () => {
  const h = harness(); h.state.batches = []; await h.load(); await h.panel.preview();
  assert.equal(writes(h).length, 1); assert.ok(writes(h)[0].path.endsWith('/preview'));
  assert.match(h.$('batch-plan-summary').textContent, /动态图解 1 次，配音 1 次，镜头草稿 1 次/);
  assert.match(h.$('batch-plan-items').textContent, /待制作（尚未启动）/);
  assert.doesNotMatch(h.$('batch-plan-items').textContent, /排队中|制作中/);
  await h.panel.start(); assert.equal(writes(h).length, 1);
  h.$('confirm-batch-start').checked = true; await h.$('confirm-batch-start').emit('change');
  await Promise.all([h.panel.start(), h.panel.start()]);
  const starts = writes(h).filter(row => row.path === '/api/projects/p1/batches');
  assert.equal(starts.length, 1); assert.equal(starts[0].options.body.plan_sha256, 'a'.repeat(64));
  assert.equal(h.panel.hasActiveOperation(), true);
  assert.equal(h.state.context.project.scenes[0].head_audio_version_id, null);
});

test('empty scene or step selection is local validation; choosing valid input enables a clean range preview', async () => {
  const h = harness(); h.state.batches = []; await h.load();
  let sceneCheck = inputIn(h.$('batch-scenes').children[0]);
  sceneCheck.checked = false; await sceneCheck.emit('change');
  assert.equal(h.$('preview-batch').disabled, true);
  assert.match(h.$('batch-form-note').textContent, /请至少选择一个镜头/);
  await h.panel.preview();
  assert.equal(writes(h).length, 0);
  assert.equal(h.state.errors.length, 0, 'A missing selection must not reach the network-error handler');
  assert.match(h.state.inputErrors.at(-1), /选择.*镜头/);
  sceneCheck = inputIn(h.$('batch-scenes').children[0]);
  sceneCheck.checked = true; await sceneCheck.emit('change');
  assert.equal(h.$('preview-batch').disabled, false);
  assert.doesNotMatch(h.$('batch-form-note').textContent, /请选择|请至少选择|连接中断/);
  for (const step of ['motion', 'audio', 'draft']) {
    h.$(`batch-step-${step}`).checked = false; await h.$(`batch-step-${step}`).emit('change');
  }
  assert.equal(h.$('preview-batch').disabled, true);
  await h.panel.preview();
  assert.match(h.state.inputErrors.at(-1), /至少选择一个制作步骤/);
  assert.equal(writes(h).length, 0); assert.equal(h.state.errors.length, 0);
  h.$('batch-step-draft').checked = true; await h.$('batch-step-draft').emit('change');
  await h.panel.preview();
  assert.deepEqual(writes(h)[0].options.body.scene_ids, ['s1']);
  assert.deepEqual(writes(h)[0].options.body.steps, ['draft']);
  assert.match(h.$('batch-form-note').textContent, /核对范围只计算任务上限/);
  assert.equal(h.$('start-batch').disabled, true);
});

test('transport failures still use the network-error handler and never create a startable plan', async () => {
  const h = harness(); h.state.batches = []; await h.load();
  h.state.transport = async () => { throw new Error('offline'); };
  await h.panel.preview();
  assert.equal(h.state.errors.length, 1); assert.equal(h.state.inputErrors.length, 0);
  assert.equal(h.$('batch-plan').hidden, true); assert.equal(h.$('start-batch').disabled, true);
  assert.equal(writes(h).length, 1);
  h.state.transport = null; await h.panel.preview();
  assert.match(h.state.successes.at(-1), /尚未启动生成/);
  assert.equal(writes(h).filter(row => row.path === '/api/projects/p1/batches').length, 0);
});

test('known render failures explain recovery without exposing raw diagnostics or repeating successful work', async () => {
  const h = harness(); h.state.batches[0].status = 'partial_failed';
  Object.assign(h.state.batches[0].items[2], { status: 'failed', render_id: null, error: 'motion_execution_oom' });
  await h.load();
  assert.match(h.$('batch-progress-rows').textContent, /内存不足/);
  assert.match(h.$('batch-progress-rows').textContent, /仅补做草稿/);
  assert.doesNotMatch(h.$('batch-progress-rows').textContent, /motion_execution_oom/);
  assert.equal(writes(h).length, 0);
  assert.equal(h.state.batches[0].items[0].version_id, 'm1');
  assert.equal(h.state.batches[0].items[1].version_id, 'a1');
  assert.match(batchErrorLabel('motion_execution_timed_out'), /超过时间上限/);
  assert.match(batchErrorLabel('motion_render_stage_failed'), /画面渲染阶段/);
  assert.match(batchErrorLabel('motion_observation_stage_failed'), /画面检查阶段/);
  assert.match(batchErrorLabel('motion_audio_mux_failed'), /音画合成阶段/);
  assert.match(batchErrorLabel('motion_relation_layout_unavailable'), /减少节点.*配音/);
  assert.match(batchErrorLabel('untrusted backend traceback'), /本步骤未完成/);
});

test('changed steps, dirty content, unavailable providers and revision updates invalidate start', async () => {
  const h = harness(); h.state.batches = []; await h.load(); await h.panel.preview();
  h.$('confirm-batch-start').checked = true; h.state.context.unsaved = true;
  await h.panel.start(); assert.equal(writes(h).length, 1);
  h.state.context.unsaved = false; h.state.context.renderConfigured = false;
  await h.panel.start(); assert.equal(writes(h).length, 1);
  h.state.context.renderConfigured = true; h.state.context.project.revision = 2; h.panel.render();
  assert.equal(h.$('start-batch').disabled, true);
  assert.match(h.$('batch-plan-summary').textContent, /项目已变化/);
  await h.panel.start(); assert.equal(writes(h).length, 1);
  h.$('batch-step-audio').checked = false; await h.$('batch-step-audio').emit('change');
  assert.equal(h.$('batch-plan').hidden, true);
});

test('failed uncertain submission reads existing batch and never starts a second one', async () => {
  const h = harness(); h.state.batches = []; await h.load(); await h.panel.preview();
  h.state.transport = async (path, options, fallback) => {
    if (path === '/api/projects/p1/batches' && options.method === 'POST') {
      h.state.batches.push(batch('accepted-original', 'blocked')); throw new Error('response lost');
    }
    return fallback(path, options);
  };
  h.$('confirm-batch-start').checked = true; await h.panel.start(); await h.panel.start();
  assert.equal(writes(h).filter(row => row.path === '/api/projects/p1/batches').length, 1);
  assert.equal(h.panel.hasUnresolvedOperation(), true);
  assert.equal(h.panel.hasActiveOperation(), true);
  assert.equal(h.state.errors.length, 1);
});

test('project epochs and out-of-order reads cannot restore a previous batch selection', async () => {
  const h = harness(); await h.load();
  let finishOld;
  h.state.transport = () => new Promise(resolve => { finishOld = resolve; });
  const old = h.load();
  h.state.context = { ...h.state.context, project: project('p2'), epoch: 2 };
  h.state.batches = [{ ...batch('p2-batch'), project_id: 'p2' }]; h.state.transport = null;
  await h.load();
  finishOld({ batches: [batch('late-old')] }); await old;
  assert.equal(h.$('batch-select').value, 'p2-batch');
  h.state.transport = () => new Promise(resolve => { finishOld = resolve; }); const older = h.load();
  h.state.transport = null; await h.load(); finishOld({ batches: [batch('out-of-order')] }); await older;
  assert.equal(h.$('batch-select').value, 'p2-batch');
});

test('polling preserves exact review checks, expanded cards and chosen historical batch', async () => {
  const h = harness(); await h.load();
  const check = reviewCheck(h); check.checked = true; await check.emit('change');
  const details = h.$('batch-review').children[0]; details.open = false; await details.emit('toggle');
  h.state.batches.unshift(batch('newer-batch')); await h.load();
  assert.equal(h.$('batch-select').value, 'b1'); assert.equal(reviewCheck(h).checked, true);
  assert.equal(h.$('batch-review').children[0].open, false);
  assert.equal(h.panel.hasProjectDrafts('p1'), true);
  assert.equal(writes(h).length, 0);
});

test('batch adoption binds reviewed exact IDs and both current heads with an independent confirmation', async () => {
  const h = harness(); await h.load(); await h.panel.adopt(); assert.equal(writes(h).length, 0);
  const check = reviewCheck(h); check.checked = true; await check.emit('change');
  await h.panel.adopt(); assert.equal(writes(h).length, 0);
  h.$('confirm-batch-adoption').checked = true; await h.$('confirm-batch-adoption').emit('change');
  await h.panel.adopt();
  assert.deepEqual(writes(h).at(-1), { path: '/api/projects/p1/batch-adopt', options: { method: 'POST', body: {
    expected_revision: 1, selections: [{ scene_id: 's1', motion_version_id: 'm1', audio_version_id: 'a1',
      expected_motion_head_version_id: null, expected_audio_head_version_id: null }] } } });
  assert.equal(h.panel.hasDrafts(), false);
  assert.ok(!writes(h).some(row => /timing\/verify|motion-renders|audio\/generate/.test(row.path)));
});

test('stale plans and changed adoption heads block the saved review rather than choosing latest', async () => {
  const h = harness(); await h.load();
  let check = reviewCheck(h); check.checked = true; await check.emit('change');
  h.state.context.project.scenes[0].motion.head_version_id = 'm2'; h.panel.render();
  assert.equal(reviewCheck(h).checked, true); assert.equal(h.$('confirm-batch-adoption').disabled, true);
  h.$('confirm-batch-adoption').checked = true; await h.panel.adopt(); assert.equal(writes(h).length, 0);
  h.state.context.project.scenes[0].motion.head_version_id = null;
  h.state.versions.set('m1', { ...version('m1'), stale: true }); await h.load();
  assert.equal(h.$('confirm-batch-adoption').disabled, true);
});

test('failed-step retry is explicit, counts each type and retains successful results', async () => {
  const h = harness(); h.state.batches[0].status = 'partial_failed';
  h.state.batches[0].items[1] = { ...h.state.batches[0].items[1], status: 'failed', version_id: null, error: 'speech_busy' };
  h.state.batches[0].items[2] = { ...h.state.batches[0].items[2], status: 'failed', render_id: null, error: 'batch_dependency_failed' };
  await h.load();
  assert.match(h.$('batch-review').textContent, /队列/);
  const audioCell = h.$('batch-progress-rows').children[0].children[2];
  const retry = inputIn(audioCell); retry.checked = true; await retry.emit('change');
  assert.match(h.$('batch-retry-note').textContent, /动态图解 0 次，配音 1 次，镜头草稿 0 次/);
  await h.panel.retry();
  assert.deepEqual(writes(h).at(-1), { path: '/api/batches/b1/retry', options: { method: 'POST', body: {
    expected_revision: 1, item_ids: ['i-audio'] } } });
  assert.equal(h.state.batches.find(row => row.id === 'b1').items[0].version_id, 'm1');
});

test('blocked uncertain steps cannot be retried and cancellation remains available during own activity', async () => {
  const h = harness(); h.state.batches[0].status = 'blocked'; h.state.context.projectBusy = true;
  h.state.batches[0].items[1].status = 'blocked'; h.state.batches[0].items[1].error = 'batch_child_uncertain';
  await h.load(); await h.panel.retry(); assert.equal(writes(h).length, 0);
  assert.equal(h.$('cancel-batch').disabled, false);
  await h.panel.cancel();
  assert.equal(writes(h).at(-1).path, '/api/batches/b1/cancel');
  assert.equal(h.$('cancel-batch').disabled, true);
  assert.equal(h.panel.hasActiveOperation(), true);
});

test('candidate media and scene navigation use the exact batch pair including reused drafts', async () => {
  const h = harness(); h.state.batches[0].items[2].status = 'reused'; await h.load();
  const buttons = descendants(h.$('batch-review')).filter(element => element.tagName === 'button');
  await buttons.find(element => /试听配音/.test(element.textContent)).emit('click');
  assert.equal(h.$('batch-audio-preview').src, '/api/audio-versions/a1/audio');
  await buttons.find(element => element.textContent === '播放对应草稿').emit('click');
  assert.deepEqual(h.state.plays, [{ id: 'r1', purpose: 'draft' }]);
  await buttons.find(element => element.textContent === '打开此候选审阅').emit('click');
  assert.deepEqual(h.state.opens[0], ['s1', { motionVersionId: 'm1', audioVersionId: 'a1', section: 'review' }]);
  const reordered = batch(); reordered.items.reverse(); reordered.items.find(row => row.step === 'motion').version_id = 'm2';
  assert.equal(batchSceneResults(reordered)[0].motion_version_id, 'm1');
});

test('whole-project draft is a separate explicit action and never implies verified timing', async () => {
  const h = harness(); await h.load(); await h.$('preview-batch-project').emit('click');
  assert.equal(h.state.projectDrafts, 0);
  h.state.context.project.scenes[0].head_audio_version_id = 'a1';
  h.state.context.project.scenes[0].motion.head_version_id = 'm1'; h.panel.render();
  await h.$('preview-batch-project').emit('click');
  assert.equal(h.state.projectDrafts, 1); assert.equal(writes(h).length, 0);
  assert.ok(!h.state.context.project.scenes[0].motion.timing_verified);
});

test('failed reads preserve candidates and checks; owner-mismatched candidates cannot be adopted', async () => {
  const h = harness(); await h.load();
  const check = reviewCheck(h); check.checked = true; await check.emit('change');
  h.state.transport = async () => { throw new Error('offline'); }; await h.load();
  assert.equal(reviewCheck(h).checked, true); assert.equal(h.$('batch-read-error').hidden, false);
  h.state.transport = null; h.state.versions.set('m1', version('m1', 'foreign')); await h.load();
  assert.equal(h.$('confirm-batch-adoption').disabled, true);
  assert.equal(writes(h).length, 0);
});

test('adoption helper excludes unreviewed fields and errors stay inert without exposing raw backend codes', () => {
  const p = project('../p?');
  assert.deepEqual(createBatchAdoption(p, [{ scene_id: 's1', audio_version_id: 'a1', ignored: 'latest' }]), {
    path: '/api/projects/..%2Fp%3F/batch-adopt', body: { expected_revision: 1,
      selections: [{ scene_id: 's1', audio_version_id: 'a1', expected_audio_head_version_id: null }] } });
  assert.throws(() => createBatchAdoption(p, []));
  assert.throws(() => createBatchAdoption(p, [{ scene_id: 'foreign', audio_version_id: 'a1' }]));
  for (const code of ['batch_inputs_changed', 'submission_uncertain', '__proto__', 'constructor']) {
    assert.ok(!batchErrorLabel(code).includes(code));
  }
});

test('all controls are unique and the app integrates reads, polling, exact navigation and draft-only export', async () => {
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length);
  const source = await readFile(new URL('../../video_workbench/web/batch.mjs', import.meta.url), 'utf8');
  for (const [, id] of source.matchAll(/\$\('([^']+)'\)/g)) assert.ok(ids.includes(id), id);
  assert.doesNotMatch(source, /innerHTML|insertAdjacentHTML|eval\(/);
  const app = await readFile(new URL('../../video_workbench/web/app.mjs', import.meta.url), 'utf8');
  assert.match(app, /batchPanel\.load\(ticket.projectId, ticket.epoch\)/);
  assert.match(app, /!batchPanel\.hasActiveOperation\(state.projectId\)/);
  assert.match(app, /batchPanel\.hasUnresolvedOperation\(state.projectId\)/);
  assert.match(app, /motionPanel\.select\(state.projectId, sceneId, motionVersionId\)/);
  assert.match(app, /createProjectMotionDraftRequest\(state.project,/);
});
