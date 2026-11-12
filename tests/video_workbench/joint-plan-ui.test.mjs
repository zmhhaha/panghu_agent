import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createJointPlanAdoption, createJointPlanPanel, createJointPlanRequest, jointPlanDraft,
  jointPlanErrorLabel, jointPlanEstimate, jointPlanSelection } from '../../video_workbench/web/joint-plan.mjs';

const html = await readFile(new URL('../../video_workbench/web/index.html', import.meta.url), 'utf8');
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.listeners = new Map(); this.value = '';
    this.checked = false; this.disabled = false; this.hidden = false; this.content = '';
    this.classes = new Set(); this.classList = { toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name) };
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
  async emit(name) { for (const fn of this.listeners.get(name) ?? []) await fn({ target: this, preventDefault() {} }); }
  reportValidity() { return true; }
}
function project(id = 'p1', revision = 1) {
  return { id, revision, script: '原始总文稿', speech_settings: { voice: 'one-project-voice' }, scenes: [] };
}
function version(id = 'v1', created_at = 1) {
  return { id, created_at, stale: false, adoptable: true, result: {
    schema_version: 'video-joint-plan-result.v1', input_sha256: 'a'.repeat(64), summary: '从全片目标安排表达',
    understanding_goal: '看懂队列如何分配工作', visual_style: '统一的对象和颜色', continuity: '同一个任务贯穿段落',
    method_decision: null, warnings: ['核对数值和边界条件'], scenes: [
      { segment_id: 'task-enters', source_scene_ids: [], title: '任务进入', narration: '<script>不要执行</script>',
        visual_description: '一个任务逐步进入队列', duration_seconds: 8, expression: '跟随一个对象的旅程',
        pronunciation: { SDK: '艾斯迪开' }, key_points: ['等待不是丢失'], visual_steps: ['先看入口', '任务移动到队尾'],
        delivery_note: '先提出问题，再解释原因', source_quotes: ['原始总文稿'] },
      { segment_id: 'worker-takes', source_scene_ids: [], title: '执行器接走', narration: '再由空闲执行器取走任务。',
        visual_description: '执行器从队头接走同一个任务', duration_seconds: 10, expression: '运动揭示对象关系',
        pronunciation: {}, key_points: ['按顺序处理'], visual_steps: ['执行器接近队头', '接走任务'],
        source_quotes: ['原始总文稿'] },
    ],
  } };
}
const response = (versions = [version()], operation = null, head_version_id = null) => ({ versions, operation, head_version_id });
function harness() {
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element()]));
  const document = { createElement: tag => new Element(tag), getElementById: id => {
    assert.ok(nodes.has(id), `Missing page element ${id}`); return nodes.get(id);
  } };
  const state = { context: { project: project(), epoch: 1, configured: true, unsaved: false, projectBusy: false },
    calls: [], errors: [], successes: [], data: response(), refreshes: 0, transport: null, refreshAction: null };
  const panel = createJointPlanPanel({ document, getContext: () => state.context,
    request: async (path, options = {}) => {
      state.calls.push({ path, options });
      return state.transport ? state.transport(path, options) : structuredClone(state.data);
    },
    refresh: async () => { state.refreshes += 1; if (state.refreshAction) await state.refreshAction(); },
    onError: error => state.errors.push(error), onSuccess: value => state.successes.push(value),
  });
  return { state, panel, $: id => nodes.get(id), load: () => panel.load(state.context.project.id, state.context.epoch) };
}
async function fill(h) {
  for (const [field, value] of [['audience', '初学者'], ['goal', '理解任务的全过程'], ['instruction', '使用贯穿全片的例子']]) {
    h.$(`joint-plan-${field}`).value = value; await h.$(`joint-plan-${field}`).emit('input');
  }
}
const posts = h => h.state.calls.filter(call => call.options.method === 'POST');

test('zero-scene project can plan saved script with immutable bounded inputs and original-script option', () => {
  const draft = { ...jointPlanDraft(), goal: ' 讲清楚 ', preserve_script: true };
  const intent = createJointPlanRequest(project('../p?'), draft);
  assert.equal(intent.path, '/api/projects/..%2Fp%3F/joint-plan');
  assert.deepEqual(intent.body, { expected_revision: 1, brief: { audience: '', goal: '讲清楚', tone: 'conversational' },
    instruction: '', preserve_script: true });
  draft.goal = '后来改变'; assert.equal(intent.body.brief.goal, '讲清楚');
  assert.throws(() => { intent.body.brief.goal = '改写'; }, TypeError);
  assert.throws(() => createJointPlanRequest({ ...project(), script: ' ' }, draft), /总文稿/);
  for (const [field, max] of [['audience', 300], ['goal', 1200], ['instruction', 2000]]) {
    assert.throws(() => createJointPlanRequest(project(), { ...draft, [field]: '字'.repeat(max + 1) }));
  }
  assert.throws(() => createJointPlanRequest(project(), { ...draft, tone: 'unknown' }));
  assert.throws(() => createJointPlanRequest(project(), { ...draft, preserve_script: 'yes' }));
});

test('review supports autonomous structure, global expression and inert text, without synthetic recommendations', async () => {
  const h = harness(); await h.load();
  assert.equal(h.$('generate-joint-plan').disabled, false, 'no scene split required');
  const content = h.$('joint-plan-review').textContent;
  for (const value of ['看懂队列如何分配工作', '统一的对象和颜色', '同一个任务贯穿段落', '2 个表达段落',
    '预计 18 秒', '段落 1', '段落 2', '跟随一个对象的旅程', '运动揭示对象关系',
    '<script>不要执行</script>', 'SDK = 艾斯迪开', '项目统一音色', '来源摘录']) assert.ok(content.includes(value), value);
  assert.doesNotMatch(content, /本方案的表达方法建议|示范视频|方法推荐/);
  assert.deepEqual(h.state.context.project.speech_settings, { voice: 'one-project-voice' });
  const provided = version('v2', 2); provided.result.method_decision = { delivery_sha256: 'b'.repeat(64), decisions: [
    { version_id: 'method-v1', content_sha256: 'c'.repeat(64), action: 'adapt', reason: '沿用同一对象追踪', segment_ids: ['task-enters'] },
  ] };
  h.state.data = response([provided]); await h.load();
  assert.match(h.$('joint-plan-review').textContent, /本方案的表达方法建议/);
  assert.match(h.$('joint-plan-review').textContent, /沿用同一对象追踪/);
  assert.match(h.$('joint-plan-review').textContent, /调整后使用/);
  assert.doesNotMatch(h.$('joint-plan-review').textContent, /delivery_sha256|method-v1/);
  assert.equal(jointPlanEstimate(provided), 18);
  provided.result.scenes[0].duration_seconds = '8'; assert.equal(jointPlanEstimate(provided), null);
});

test('whole-plan adoption requires explicit confirmation and exact revision/head; no media production follows', async () => {
  const h = harness(); h.state.data = response([version()], null, 'old-head'); await h.load();
  await h.panel.adopt(); assert.equal(posts(h).length, 0);
  assert.match(h.$('joint-plan-adoption-note').textContent, /创建全部表达段落/);
  h.$('confirm-joint-plan-adoption').checked = true; await h.$('confirm-joint-plan-adoption').emit('change');
  assert.equal(h.$('adopt-joint-plan').disabled, false);
  h.state.transport = () => project('p1', 2); await h.panel.adopt();
  assert.deepEqual(posts(h).map(call => [call.path, call.options.body]), [[
    '/api/projects/p1/joint-plan/adopt', { expected_revision: 1, version_id: 'v1', expected_head_version_id: 'old-head' },
  ]]);
  assert.equal(h.$('confirm-joint-plan-adoption').checked, false);
  assert.doesNotMatch(h.state.calls.map(call => call.path).join('\n'), /audio|render|speech/);
  const p = project(), data = response();
  assert.throws(() => createJointPlanAdoption(p, data, version(), { unsaved: true }), /未保存/);
  assert.throws(() => createJointPlanAdoption(p, data, { ...version(), stale: true }));
  assert.throws(() => createJointPlanAdoption(p, data, { ...version(), adoptable: false }));
  assert.throws(() => createJointPlanAdoption(p, { ...data, head_version_id: 'v1' }, version()));
});

test('project changes and changed adoption head invalidate confirmation while local drafts remain', async () => {
  const h = harness(); h.state.context.project.scenes = [{ id: 'old-scene', title: '旧镜头' }]; await h.load(); await fill(h);
  assert.match(h.$('joint-plan-adoption-note').textContent, /替换现有镜头结构/);
  h.$('confirm-joint-plan-adoption').checked = true;
  h.state.context.unsaved = true; h.panel.render(); await h.panel.adopt();
  assert.equal(posts(h).length, 0); assert.equal(h.$('adopt-joint-plan').disabled, true);
  assert.match(h.$('joint-plan-adoption-note').textContent, /未保存/);
  h.state.context.unsaved = false; h.state.context.project.revision = 2; h.panel.render();
  assert.equal(h.$('confirm-joint-plan-adoption').checked, false);
  assert.equal(h.$('adopt-joint-plan').disabled, true, 'wait for read at current revision');
  await h.load(); h.$('confirm-joint-plan-adoption').checked = true;
  h.state.data = response([version()], null, 'new-head'); await h.load();
  assert.equal(h.$('confirm-joint-plan-adoption').checked, false);
  assert.equal(h.$('joint-plan-goal').value, '理解任务的全过程');
});

test('polling restores settings once and preserves edits, checkbox and edits during first read', async () => {
  const saved = { brief: { audience: '老观众', goal: '旧目标', tone: 'precise' }, instruction: '旧要求', preserve_script: true };
  const h = harness(); h.state.data = { ...response(), ...saved }; await h.load();
  assert.equal(h.$('joint-plan-goal').value, '旧目标'); assert.equal(h.$('joint-plan-preserve-script').checked, true);
  assert.equal(h.panel.hasDrafts(), false);
  await fill(h); h.$('joint-plan-preserve-script').checked = false; await h.$('joint-plan-preserve-script').emit('change');
  await h.load(); assert.equal(h.$('joint-plan-preserve-script').checked, false);
  assert.equal(h.$('joint-plan-goal').value, '理解任务的全过程');
  h.state.context = { ...h.state.context, project: project('p2'), epoch: 2 }; await h.load();
  assert.equal(h.$('joint-plan-preserve-script').checked, true);
  h.state.context = { ...h.state.context, project: project(), epoch: 3 }; await h.load();
  assert.equal(h.$('joint-plan-preserve-script').checked, false);
  const pending = harness(); let resolve;
  pending.state.transport = () => new Promise(done => { resolve = done; });
  const loading = pending.load(); await fill(pending);
  pending.$('joint-plan-preserve-script').checked = false; await pending.$('joint-plan-preserve-script').emit('change');
  resolve({ ...response(), ...saved }); await loading;
  assert.equal(pending.$('joint-plan-preserve-script').checked, false);
  assert.equal(pending.$('joint-plan-goal').value, '理解任务的全过程');
});

test('generation uses zero scenes, keeps in-flight draft changes and never starts speech/render', async () => {
  const h = harness(); await h.load(); await fill(h);
  let resolve; h.state.transport = () => new Promise(done => { resolve = done; });
  const generating = h.panel.generate();
  h.$('joint-plan-instruction').value = '新要求'; await h.$('joint-plan-instruction').emit('input');
  resolve({ id: 'op', kind: 'joint-plan', status: 'queued' }); await generating;
  assert.equal(posts(h).length, 1);
  assert.deepEqual(posts(h)[0].options.body, { expected_revision: 1, brief: { audience: '初学者', goal: '理解任务的全过程', tone: 'conversational' },
    instruction: '使用贯穿全片的例子', preserve_script: false });
  assert.equal(h.panel.hasDrafts(), true); assert.equal(h.$('joint-plan-instruction').value, '新要求');
  assert.equal(h.panel.hasUnresolvedOperation(), true);
  assert.equal(h.panel.hasUnresolvedOperation('other-project'), false);
  assert.doesNotMatch(h.state.calls.map(call => call.path).join('\n'), /audio|render|speech/);
});

test('unknown submission discovers existing operation by refresh without automatically repeating POST', async () => {
  const h = harness(); await h.load();
  h.state.transport = async (path, options) => {
    if (options.method === 'POST') throw new TypeError('network response lost');
    return response([], { id: 'original-op', status: 'uncertain', error: 'submission_uncertain' });
  };
  h.state.refreshAction = h.load; await h.panel.generate();
  assert.equal(posts(h).length, 1); assert.equal(h.state.refreshes, 1);
  assert.equal(h.panel.hasUnresolvedOperation(), true);
  assert.equal(h.panel.hasRefreshableOperation(), true);
  assert.equal(h.$('generate-joint-plan').disabled, true);
  await h.panel.generate(); assert.equal(posts(h).length, 1);
  assert.match(h.$('joint-plan-status-text').textContent, /不会自动重复生成/);
});

test('cancel, progress clocks and stale reads keep project identity and never overwrite another project', async () => {
  const h = harness(); h.state.data = response([], { id: 'op/a', status: 'running', created_at: Date.now() / 1000 - 61 });
  await h.load(); assert.equal(h.$('cancel-joint-plan').hidden, false);
  assert.equal(h.panel.hasActiveOperation(), true); assert.equal(h.panel.hasRefreshableOperation(), true);
  assert.match(h.$('joint-plan-status-text').textContent, /已等待/); h.panel.refreshElapsed();
  h.state.transport = async () => ({ id: 'op/a', status: 'cancel_requested' }); await h.panel.cancel();
  assert.deepEqual(posts(h).map(call => [call.path, call.options.body]), [['/api/joint-plan-operations/op%2Fa/cancel', {}]]);
  assert.equal(h.$('cancel-joint-plan').disabled, true);
  let oldResolve;
  h.state.transport = () => new Promise(done => { oldResolve = done; }); const oldRead = h.load();
  h.state.context = { ...h.state.context, project: project('p2'), epoch: 2 };
  h.state.transport = async () => response([version('p2-result')]); await h.load();
  oldResolve(response([version('wrong-project')])); await oldRead;
  assert.equal(h.$('joint-plan-version').value, 'p2-result');
});

test('new candidates preserve the reviewed version and all errors use human descriptions', async () => {
  const h = harness(); await h.load(); h.$('confirm-joint-plan-adoption').checked = true;
  h.state.data = response([version('v2', 2), version()]); await h.load();
  assert.equal(h.$('joint-plan-version').value, 'v1'); assert.equal(h.$('joint-plan-new-candidate').hidden, false);
  await h.$('joint-plan-new-candidate').emit('click');
  assert.equal(h.$('joint-plan-version').value, 'v2'); assert.equal(h.$('confirm-joint-plan-adoption').checked, false);
  assert.equal(jointPlanSelection(h.state.data, 'missing').id, 'v2');
  for (const code of ['joint_plan_profile_unavailable', 'joint_plan_plan_stale', '__proto__', 'constructor']) {
    h.state.data = response([], { id: 'op', status: 'failed', error: code }); await h.load();
    assert.ok(h.$('joint-plan-status-text').textContent.includes(jointPlanErrorLabel(code)));
    assert.ok(!h.$('joint-plan-status-text').textContent.includes(code));
  }
});
