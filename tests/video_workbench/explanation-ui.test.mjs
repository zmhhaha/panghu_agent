import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import {
  createExplanationAdoption, createExplanationPanel, createExplanationRequest,
  explanationDraft, explanationDurationComparison, explanationErrorLabel, explanationSceneReviews, explanationSelection,
} from '../../video_workbench/web/explanation.mjs';

const html = await readFile(new URL('../../video_workbench/web/index.html', import.meta.url), 'utf8');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.listeners = new Map(); this.value = '';
    this.checked = false; this.disabled = false; this.hidden = false; this.content = '';
    this.classes = new Set(); this.classList = { toggle: (name, enabled) => enabled ? this.classes.add(name) : this.classes.delete(name) };
  }
  set textContent(value) { this.content = String(value); this.children = []; }
  get textContent() { return this.content + this.children.map(child => child.textContent).join('\n'); }
  set innerHTML(value) { throw new Error(`Unsafe HTML assignment: ${value}`); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.content = ''; this.children = [...children]; }
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(callback);
  }
  async emit(name) { for (const callback of this.listeners.get(name) ?? []) await callback({ target: this, preventDefault() {} }); }
  reportValidity() { return true; }
}

function project(id = 'p1', revision = 1) {
  return { id, revision, script: '原始总稿', speech_settings: { voice: 'one-project-voice' }, scenes: [
    { id: 'scene-1', title: '原镜头', narration: '原旁白', visual_description: '原画面',
      speech_settings: { pronunciation: { SDK: '旧读音' } } },
  ] };
}

function version(id = 'v1', created = 1) {
  return { id, created_at: created, stale: false, adoptable: true, result: {
    schema_version: '1', input_sha256: 'a'.repeat(64), summary: '把关系讲清楚', warnings: ['检查数字与条件'], scenes: [
      { scene_id: 'scene-1', title: '候选镜头', narration: '<script>这是原样文本</script>',
        visual_description: '候选画面重点', duration_seconds: 8, pronunciation: { SDK: '艾斯迪开' },
        key_points: ['关键点甲'], visual_strategy: 'compare', visual_steps: ['先呈现两类对象', '再解释区别'],
        delivery_note: '先停顿，再解释', source_quotes: ['原始总稿'] },
    ],
  } };
}

function response(versions = [version()], operation = null, head = null) {
  return { operation, head_version_id: head, versions };
}

function harness() {
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element()]));
  const document = { createElement: tag => new Element(tag), getElementById: id => {
    assert.ok(nodes.has(id), `Missing page element ${id}`); return nodes.get(id);
  } };
  const state = { context: { project: project(), epoch: 1, configured: true, unsaved: false, projectBusy: false },
    calls: [], errors: [], successes: [], data: response(), refreshes: 0,
    transport: null, refreshAction: null };
  const panel = createExplanationPanel({ document, getContext: () => state.context,
    request: async (path, options = {}) => {
      state.calls.push({ path, options });
      return state.transport ? state.transport(path, options) : structuredClone(state.data);
    },
    refresh: async () => { state.refreshes += 1; if (state.refreshAction) await state.refreshAction(); },
    onError: error => state.errors.push(error), onSuccess: message => state.successes.push(message),
  });
  return { state, panel, $: id => nodes.get(id),
    load: () => panel.load(state.context.project.id, state.context.epoch) };
}

async function fill(h) {
  for (const [name, value] of [['audience', '开发者'], ['goal', '讲清两类记录'], ['instruction', '用一个贯穿例子']]) {
    h.$(`explanation-${name}`).value = value;
    await h.$(`explanation-${name}`).emit('input');
  }
}

test('request freezes only planning inputs, revision and selected brief with bounded fields', () => {
  const draft = { ...explanationDraft(), audience: ' 开发者 ', goal: ' 讲清概念 ', instruction: ' 少用术语 ' };
  const intent = createExplanationRequest(project('../p?'), draft);
  assert.equal(intent.path, '/api/projects/..%2Fp%3F/explanation');
  assert.deepEqual(intent.body, { expected_revision: 1,
    brief: { audience: '开发者', goal: '讲清概念', tone: 'conversational' }, instruction: '少用术语' });
  draft.goal = '后改的要求';
  assert.equal(intent.body.brief.goal, '讲清概念');
  assert.throws(() => { intent.body.brief.goal = '不能改'; }, TypeError);
  assert.deepEqual(createExplanationRequest(project(), explanationDraft()).body.brief,
    { audience: '', goal: '', tone: 'conversational' });
  for (const [field, maximum] of [['audience', 300], ['goal', 1200], ['instruction', 2000]]) {
    assert.throws(() => createExplanationRequest(project(), { ...draft, [field]: '字'.repeat(maximum + 1) }));
  }
  for (const tone of ['', 'arbitrary', null]) assert.throws(() => createExplanationRequest(project(), { ...draft, tone }));
});

test('operation errors use human descriptions and empty warnings still ask for fact review', async () => {
  const h = harness(), candidate = version(); candidate.result.warnings = [];
  for (const code of ['submission_uncertain', 'cancellation_uncertain', 'cancellation_retry_exhausted',
    'explanation_profile_unavailable', 'explanation_deadline_expired', 'explanation_plan_stale',
    'explanation_narration_requires_split', 'unknown_code', '__proto__', 'constructor']) {
    h.state.data = response([candidate], { id: 'op', status: 'running', error: code });
    await h.load();
    assert.ok(h.$('explanation-status-text').textContent.includes(explanationErrorLabel(code)));
    assert.ok(!h.$('explanation-status-text').textContent.includes(code));
  }
  assert.ok(h.$('explanation-review').textContent.includes('请核对事实、数字和术语。'));
  assert.ok(h.$('explanation-review').textContent.includes('来源摘录用于追溯原稿'));
});

test('first read restores the last submitted brief after reopening while newer local drafts always win', async () => {
  const saved = { brief: { audience: '之前的受众', goal: '之前的目标', tone: 'precise' }, instruction: '之前的调整要求' };
  const h = harness(); h.state.data = { ...response(), ...saved }; await h.load();
  for (const [field, value] of Object.entries(saved.brief)) assert.equal(h.$(`explanation-${field}`).value, value);
  assert.equal(h.$('explanation-instruction').value, saved.instruction);
  assert.equal(h.panel.hasDrafts(), false);

  await fill(h);
  h.state.data = { ...response(), brief: { audience: '服务端后改', goal: '不要覆盖', tone: 'storytelling' }, instruction: '后改指令' };
  await h.load();
  assert.equal(h.$('explanation-audience').value, '开发者');
  assert.equal(h.$('explanation-goal').value, '讲清两类记录');
  assert.equal(h.$('explanation-instruction').value, '用一个贯穿例子');
  h.state.context = { ...h.state.context, project: project('p2'), epoch: 2 }; await h.load();
  assert.equal(h.$('explanation-audience').value, '服务端后改');
  h.state.context = { ...h.state.context, project: project(), epoch: 3 }; await h.load();
  assert.equal(h.$('explanation-audience').value, '开发者');

  const pending = harness(); let resolveRead;
  pending.state.transport = () => new Promise(resolve => { resolveRead = resolve; });
  const firstRead = pending.load(); await fill(pending);
  resolveRead({ ...response(), ...saved }); await firstRead;
  assert.equal(pending.$('explanation-audience').value, '开发者');
  assert.equal(pending.$('explanation-goal').value, '讲清两类记录');
  assert.equal(pending.panel.hasDrafts(), true);
});

test('adoption binds the reviewed version and head, rejects stale candidates and protects unsaved edits', () => {
  const candidate = version(), data = response([candidate], null, 'previous-head'), p = project();
  const original = structuredClone(p);
  const intent = createExplanationAdoption(p, data, candidate);
  assert.deepEqual(intent.body, { expected_revision: 1, version_id: 'v1', expected_head_version_id: 'previous-head' });
  assert.deepEqual(p, original);
  assert.throws(() => createExplanationAdoption(p, data, candidate, { unsaved: true }), /未保存/);
  assert.throws(() => createExplanationAdoption(p, data, { ...candidate, stale: true }));
  assert.throws(() => createExplanationAdoption(p, data, { ...candidate, adoptable: false }));
  assert.throws(() => createExplanationAdoption(p, { ...data, head_version_id: candidate.id }, candidate));
});

test('review matches scene IDs, shows both versions and keeps hostile text inert', async () => {
  const h = harness(); await h.load();
  const review = h.$('explanation-review').textContent;
  for (const expected of ['当前已保存', '候选方案', '原旁白', '原画面', '候选画面重点', '关键点甲',
    '前后对照', '先呈现两类对象', '再解释区别', 'SDK = 艾斯迪开', '先停顿，再解释',
    '<script>这是原样文本</script>', '检查数字与条件', '来源摘录用于追溯原稿']) assert.ok(review.includes(expected), expected);
  const p = project(); p.scenes.unshift({ id: 'other', narration: '不能串到另一镜头' });
  assert.equal(explanationSceneReviews(p, version())[0].previous.id, 'scene-1');
  assert.equal(h.$('confirm-explanation-adoption').checked, false);
  assert.equal(h.$('adopt-explanation').disabled, true);
  assert.deepEqual(h.state.context.project.speech_settings, { voice: 'one-project-voice' });
});

test('adoption review shows 175 to 238 seconds and scene deltas by ID without using recording durations', async () => {
  const h = harness(), candidate = version();
  const old = [30, 40, 35, 36, 34], next = [40, 55, 45, 50, 48];
  h.state.context.project.scenes = old.map((duration, index) => ({ ...project().scenes[0],
    id: `scene-${index}`, duration_seconds: duration,
    head_audio_version_id: `audio-${index}`, audio_versions: [{ id: `audio-${index}`, duration_seconds: 999 }] }));
  candidate.result.scenes = [4, 2, 0, 3, 1].map(index => ({ ...version().result.scenes[0],
    scene_id: `scene-${index}`, duration_seconds: next[index] }));
  h.state.data = response([candidate]); await h.load();
  assert.deepEqual(explanationDurationComparison(h.state.context.project, candidate), {
    previousSeconds: 175, proposedSeconds: 238, deltaSeconds: 63, deltaPercent: 36,
  });
  for (const id of ['explanation-review', 'explanation-adoption-note']) {
    assert.match(h.$(id).textContent, /当前方案预计 175 秒 → 候选方案预计 238 秒，增加 63 秒（36%）/);
    assert.match(h.$(id).textContent, /均为文本估时，实际以配音录音为准/);
    assert.doesNotMatch(h.$(id).textContent, /999/);
  }
  assert.match(h.$('explanation-review').textContent, /原 34 秒 → 候选 48 秒（\+14 秒）/);
  h.$('confirm-explanation-adoption').checked = true;
  h.state.transport = async () => project('p1', 2); await h.panel.adopt();
  assert.equal(h.state.calls.filter(call => call.options.method === 'POST').length, 1);
});

test('duration comparison rejects missing, duplicated or unmeasured scene estimates and shows reductions', async () => {
  const p = project(), candidate = version(); p.scenes[0].duration_seconds = 20;
  candidate.result.scenes[0].duration_seconds = 15;
  assert.deepEqual(explanationDurationComparison(p, candidate), {
    previousSeconds: 20, proposedSeconds: 15, deltaSeconds: -5, deltaPercent: -25,
  });
  const h = harness(); h.state.context.project = p; h.state.data = response([candidate]); await h.load();
  assert.match(h.$('explanation-review').textContent, /减少 5 秒（25%）/);
  assert.match(h.$('explanation-review').textContent, /原 20 秒 → 候选 15 秒（-5 秒）/);
  const same = structuredClone(candidate); same.result.scenes[0].duration_seconds = 20;
  h.state.data = response([{ ...same, id: 'same' }]); await h.load();
  h.$('explanation-version').value = 'same'; await h.$('explanation-version').emit('change');
  assert.match(h.$('explanation-review').textContent, /时长不变/);
  for (const bad of [undefined, 0, -1, '20', Infinity, NaN]) {
    assert.equal(explanationDurationComparison({ ...p, scenes: [{ ...p.scenes[0], duration_seconds: bad }] }, candidate), null);
  }
  const unknown = structuredClone(candidate); unknown.result.scenes[0].scene_id = 'unknown';
  assert.equal(explanationDurationComparison(p, unknown), null);
  const duplicate = { ...candidate, result: { ...candidate.result, scenes: [candidate.result.scenes[0], candidate.result.scenes[0]] } };
  assert.equal(explanationDurationComparison({ ...p, scenes: [p.scenes[0], { ...p.scenes[0], id: 'second' }] }, duplicate), null);
  assert.equal(explanationDurationComparison({ scenes: [] }, { result: { scenes: [] } }), null);
});

test('new candidates and changed heads preserve the selected review until the user switches', async () => {
  const h = harness(); await h.load();
  assert.equal(h.$('explanation-version').value, 'v1');
  h.$('confirm-explanation-adoption').checked = true;
  h.state.data = response([version('v2', 2), version()], null, 'v2');
  await h.load();
  assert.equal(h.$('explanation-version').value, 'v1');
  assert.equal(h.$('explanation-new-candidate').hidden, false);
  assert.equal(h.$('confirm-explanation-adoption').checked, false);
  await h.$('explanation-new-candidate').emit('click');
  assert.equal(h.$('explanation-version').value, 'v2');
  assert.equal(h.$('adopt-explanation').disabled, true);
  assert.equal(explanationSelection(h.state.data, 'missing').id, 'v2');
});

test('switching away and back rejects a late read from the previous selection epoch', async () => {
  const h = harness(); let resolveOld;
  h.state.transport = () => new Promise(resolve => { resolveOld = resolve; });
  const old = h.load();
  h.state.context = { ...h.state.context, project: project('p2'), epoch: 2 };
  h.state.transport = async () => response([version('other')]); await h.load();
  h.state.context = { ...h.state.context, project: project(), epoch: 3 };
  h.state.transport = async () => response([version('new')]); await h.load();
  resolveOld(response([version('late')])); await old;
  assert.equal(h.$('explanation-version').value, 'new');
});

test('newer reads win and polling preserves form drafts and expanded scene review', async () => {
  const h = harness(); await h.load(); await fill(h);
  const sceneReview = h.$('explanation-review').children.at(-1);
  sceneReview.open = true;
  let resolveOld;
  h.state.transport = () => new Promise(resolve => { resolveOld = resolve; });
  const old = h.load();
  h.state.transport = async () => response([version(), version('new', 2)]); await h.load();
  resolveOld(response([version('late', 3)])); await old;
  assert.equal(h.$('explanation-audience').value, '开发者');
  assert.equal(h.$('explanation-goal').value, '讲清两类记录');
  assert.equal(h.$('explanation-review').children.at(-1), sceneReview);
  assert.equal(sceneReview.open, true);
  assert.equal(h.panel.hasDrafts(), true);
  h.state.context = { ...h.state.context, project: project('p2'), epoch: 2 }; await h.load();
  assert.equal(h.$('explanation-audience').value, '');
  h.state.context = { ...h.state.context, project: project(), epoch: 3 }; await h.load();
  assert.equal(h.$('explanation-audience').value, '开发者');
});

test('one click creates only one plan; in-flight form edits survive completion', async () => {
  const h = harness(); await h.load(); await fill(h); let complete;
  h.state.transport = (path, options) => options.method === 'POST'
    ? new Promise(resolve => { complete = resolve; }) : Promise.resolve(h.state.data);
  const one = h.panel.generate(); const duplicate = h.panel.generate();
  h.$('explanation-instruction').value = '用户还在修改要求'; await h.$('explanation-instruction').emit('input');
  complete({ id: 'op1', status: 'running', version_id: null }); await one; await duplicate;
  const posts = h.state.calls.filter(call => call.options.method === 'POST');
  assert.equal(posts.length, 1); assert.equal(posts[0].path, '/api/projects/p1/explanation');
  assert.equal(posts[0].options.body.instruction, '用一个贯穿例子');
  assert.equal(h.$('explanation-instruction').value, '用户还在修改要求');
  assert.equal(h.panel.hasDrafts(), true); assert.equal(h.panel.hasActiveOperation(), true);
  assert.equal(h.$('generate-explanation').disabled, true);
  assert.equal(h.state.refreshes, 1);
  assert.ok(!h.state.calls.some(call => /speech|audio|renders|generate|motion/.test(call.path)));
});

test('an uncertain generation queries the existing operation and never auto-resubmits', async () => {
  const h = harness(); await h.load(); await fill(h);
  h.state.transport = async (path, options) => {
    if (options.method === 'POST') throw new Error('response lost');
    return response([], { id: 'op-discovered', status: 'submitting' });
  };
  h.state.refreshAction = h.load;
  await h.panel.generate();
  assert.equal(h.state.calls.filter(call => call.options.method === 'POST').length, 1);
  assert.equal(h.panel.hasActiveOperation(), true);
  assert.match(h.$('explanation-status-text').textContent, /正在提交/);
  assert.equal(h.$('cancel-explanation').hidden, false);
  assert.equal(h.state.errors.length, 1);
  assert.match(h.$('explanation-read-error').textContent, /操作结果尚未确认/);
  assert.match(h.$('explanation-read-error').textContent, /不会自动重复提交/);
  assert.doesNotMatch(h.$('explanation-read-error').textContent, /response lost/);
});

test('409 feedback persists through successful reads, preserves drafts and clears only after a successful action', async () => {
  const h = harness(); await h.load(); await fill(h);
  h.state.transport = async (path, options) => {
    if (options.method === 'POST') throw Object.assign(new Error('已采用方案发生变化'), { status: 409 });
    return response();
  };
  h.state.refreshAction = h.load;
  await h.panel.generate();
  assert.equal(h.state.calls.filter(call => call.options.method === 'POST').length, 1);
  assert.equal(h.$('explanation-read-error').hidden, false);
  assert.match(h.$('explanation-read-error').textContent, /已有更新/);
  assert.match(h.$('explanation-read-error').textContent, /草稿和所选范围会保留/);
  await h.load();
  assert.match(h.$('explanation-read-error').textContent, /已有更新/);
  assert.equal(h.$('explanation-instruction').value, '用一个贯穿例子');
  assert.equal(h.panel.hasDrafts(), true);
  h.state.transport = async (path, options) => options.method === 'POST'
    ? { id: 'new-op', status: 'running' } : response();
  await h.panel.generate();
  assert.equal(h.$('explanation-read-error').hidden, true);
  assert.equal(h.state.calls.filter(call => call.options.method === 'POST').length, 2);
});

test('uncertain and quarantined operations remain refreshable, with a local ticking elapsed label and no repeat submit', async t => {
  let now = 1000; t.mock.method(Date, 'now', () => now * 1000);
  const h = harness(); await fill(h);
  for (const status of ['uncertain', 'quarantined']) {
    h.state.data = response([version()], { id: 'old-op', status, created_at: 935,
      error: status === 'quarantined' ? 'agent_quarantined' : 'service_protocol_invalid' });
    await h.load();
    assert.equal(h.panel.hasRefreshableOperation(), true);
    assert.equal(h.panel.hasActiveOperation(), false);
    assert.equal(h.$('cancel-explanation').hidden, true);
    assert.match(h.$('explanation-status-text').textContent, /已等待 1分5秒/);
    assert.match(h.$('explanation-status-text').textContent, status === 'quarantined' ? /生成服务已被隔离/ : /服务返回的数据不符合约定/);
    const calls = h.state.calls.length, review = h.$('explanation-review').children.at(-1);
    review.open = true; h.$('confirm-explanation-adoption').checked = true;
    now += 1; h.panel.refreshElapsed();
    assert.equal(h.state.calls.length, calls);
    assert.match(h.$('explanation-status-text').textContent, /已等待 1分6秒/);
    assert.equal(h.$('explanation-review').children.at(-1), review);
    assert.equal(review.open, true);
    assert.equal(h.$('confirm-explanation-adoption').checked, true);
    assert.equal(h.$('explanation-instruction').value, '用一个贯穿例子');
    now = 1000;
  }
  h.state.data = response([version()], { id: 'old-op', status: 'completed', created_at: 935 }); await h.load();
  assert.equal(h.panel.hasRefreshableOperation(), false);
  assert.doesNotMatch(h.$('explanation-status-text').textContent, /已等待/);
  assert.equal(h.state.calls.filter(call => call.options.method === 'POST').length, 0);
});

test('reload restores running work, cancellation uses the operation route and continues polling to terminal', async () => {
  const h = harness();
  h.state.data = response([], { id: '../operation', status: 'running' }); await h.load();
  assert.equal(h.panel.hasActiveOperation(), true);
  h.state.transport = async () => ({ id: '../operation', status: 'cancelling' });
  await h.panel.cancel();
  assert.equal(h.state.calls.at(-1).path, '/api/explanation-operations/..%2Foperation/cancel');
  assert.equal(h.panel.hasActiveOperation(), true); assert.equal(h.$('cancel-explanation').disabled, true);
  const calls = h.state.calls.length; await h.panel.cancel(); assert.equal(h.state.calls.length, calls);
  h.state.transport = async () => response([], { id: '../operation', status: 'cancelled' }); await h.load();
  assert.equal(h.panel.hasActiveOperation(), false);
  assert.equal(h.$('cancel-explanation').hidden, true);
});

test('adoption requires explicit confirmation and rejects dirty or newly changed project state', async () => {
  const h = harness(); await h.load(); await fill(h); await h.panel.adopt();
  assert.equal(h.state.calls.filter(call => call.options.method === 'POST').length, 0);
  h.$('confirm-explanation-adoption').checked = true;
  h.state.context.unsaved = true; h.panel.render(); await h.panel.adopt();
  assert.equal(h.state.calls.filter(call => call.options.method === 'POST').length, 0);
  assert.match(h.$('explanation-adoption-note').textContent, /草稿不会被覆盖/);
  assert.equal(h.$('explanation-goal').value, '讲清两类记录');
  h.state.context.unsaved = false; h.state.context.project = project('p1', 2); h.panel.render();
  assert.equal(h.$('confirm-explanation-adoption').checked, false);
  assert.equal(h.$('adopt-explanation').disabled, true);
  h.$('confirm-explanation-adoption').checked = true; await h.panel.adopt();
  assert.equal(h.state.calls.filter(call => call.options.method === 'POST').length, 0);
  await h.load(); h.$('confirm-explanation-adoption').checked = true;
  h.state.transport = async () => project('p1', 3); await h.panel.adopt();
  const post = h.state.calls.find(call => call.options.method === 'POST');
  assert.equal(post.path, '/api/projects/p1/explanation/adopt');
  assert.deepEqual(post.options.body, { expected_revision: 2, version_id: 'v1', expected_head_version_id: null });
  assert.equal(h.$('explanation-goal').value, '讲清两类记录');
});

test('read failures retain review and input; disabled generation still allows historical reads', async () => {
  const h = harness(); await h.load(); await fill(h);
  const previous = h.$('explanation-review').textContent;
  h.state.transport = async () => { throw new Error('offline'); }; await h.load();
  assert.equal(h.$('explanation-read-error').hidden, false);
  assert.equal(h.$('explanation-review').textContent, previous);
  assert.equal(h.$('explanation-audience').value, '开发者');
  h.state.context.configured = false; h.panel.render();
  const calls = h.state.calls.length; await h.load(); await h.panel.generate(); await h.panel.adopt();
  assert.equal(h.$('explanation-panel').hidden, false); assert.equal(h.state.calls.length, calls + 1);
  assert.equal(h.state.calls.at(-1).options.method, undefined);
  assert.equal(h.panel.hasActiveOperation(), false);
});

test('closed configuration restores in-flight work and permits cancellation while blocking new generation and adoption', async () => {
  const h = harness(); h.state.context.configured = false;
  h.state.data = response([], null); await h.load();
  assert.equal(h.$('explanation-panel').hidden, true);
  h.state.data = response([version()], { id: 'old-op', status: 'running' }); await h.load();
  assert.equal(h.$('explanation-panel').hidden, false);
  assert.equal(h.panel.hasActiveOperation(), true);
  assert.equal(h.$('cancel-explanation').hidden, false);
  assert.equal(h.$('cancel-explanation').disabled, false);
  assert.equal(h.$('generate-explanation').disabled, true);
  assert.equal(h.$('adopt-explanation').disabled, true);
  assert.match(h.$('explanation-form-note').textContent, /历史方案仍可审阅/);
  h.$('confirm-explanation-adoption').checked = true;
  const previous = h.state.calls.length; await h.panel.generate(); await h.panel.adopt();
  assert.equal(h.state.calls.length, previous);
  h.state.transport = async () => ({ id: 'old-op', status: 'cancelling' });
  await h.panel.cancel();
  assert.equal(h.state.calls.at(-1).path, '/api/explanation-operations/old-op/cancel');
  assert.equal(h.panel.hasActiveOperation(), true);
  assert.equal(h.$('cancel-explanation').disabled, true);
});

test('all new controls exist once and the app includes explanation reads in normal polling', async () => {
  const source = await readFile(new URL('../../video_workbench/web/explanation.mjs', import.meta.url), 'utf8');
  const app = await readFile(new URL('../../video_workbench/web/app.mjs', import.meta.url), 'utf8');
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length);
  for (const [, id] of source.matchAll(/\$\('([^']+)'\)/g)) assert.ok(ids.includes(id), id);
  assert.doesNotMatch(source, /innerHTML|insertAdjacentHTML|eval\(/);
  assert.match(app, /explanationPanel\.load\(ticket.projectId, ticket.epoch\)/);
  assert.match(app, /explanationPanel\.hasRefreshableOperation(?:\?\.)?\(state.projectId\)/);
  assert.match(app, /explanationPanel\.refreshElapsed(?:\?\.)?\(\)/);
  assert.match(app, /explanationPanel\.hasDrafts\(\)/);
  assert.match(html, /id="explanation-audience" maxlength="300"/);
  assert.match(html, /id="explanation-goal"[^>]*maxlength="1200"/);
  assert.match(html, /id="explanation-instruction"[^>]*maxlength="2000"/);
});
