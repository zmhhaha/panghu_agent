import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { continuityErrorLabel, continuityIssues, createContinuityAdoption, createContinuityDraftRequest,
  createContinuityPanel, createContinuityProposal } from '../../video_workbench/web/continuity.mjs';

const html = await readFile(new URL('../../video_workbench/web/index.html', import.meta.url), 'utf8');
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.listeners = new Map();
    this.value = ''; this.content = ''; this.disabled = false; this.hidden = false; this.checked = false;
  }
  set textContent(value) { this.content = String(value); this.children = []; }
  get textContent() { return [this.content, ...this.children.map(row => row.textContent)].join('\n'); }
  set innerHTML(value) { throw new Error(`Unsafe HTML: ${value}`); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.content = ''; this.children = [...children]; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(callback);
  }
  async emit(name) { for (const callback of this.listeners.get(name) ?? []) await callback({ target: this }); }
}
const link = (mode = 'retain') => ({ from_scene_id: 's1', from_element_id: 'log', to_scene_id: 's2', to_element_id: 'history', mode });
function project(id = 'p1', revision = 1) {
  return { id, revision, production_mode: 'motion', speech_settings: { voice: 'one-project-voice' }, scenes: [1, 2, 3].map(index => ({
    id: `s${index}`, title: `第${index}段`, narration: `原旁白${index}`, head_audio_version_id: `a${index}`,
    audio_versions: [{ id: `a${index}`, stale: false }], audio_stale: false,
    motion: { mode: 'motion', head_version_id: `m${index}`, versions: [{ id: `m${index}`, stale: false }], head_stale: false },
  })) };
}

test('mixed process and diagram candidates block continuity while preserving ordinary drafts', async () => {
  const h = harness();
  h.state.context.project.scenes[0].motion.versions[0].diagram_kind = 'process';
  h.state.context.project.scenes[0].motion.versions[0].compiler_version = 'motion-process-compiler-2';
  await h.load();
  assert.match(continuityIssues(h.state.context.project)[0], /混合.*普通整片草稿/);
  assert.equal(h.$('propose-continuity').disabled, true);
  assert.equal(h.$('preview-continuity').disabled, true);
  assert.match(h.$('continuity-issues').textContent, /混合.*普通整片草稿/);
  await h.panel.propose();
  assert.equal(h.state.calls.filter(row => row.options.method === 'POST').length, 0);
  assert.match(continuityErrorLabel('process_continuity_not_supported'), /普通整片草稿/);
  assert.equal(h.state.context.project.scenes[0].motion.head_version_id, 'm1');
});
function version(id = 'v1', links = [link()]) {
  return { id, stale: false, adoptable: true, plan: { schema: 'video.continuity-plan.v1', links },
    scene_summaries: [
      { scene_id: 's1', title: '第1段', elements: [{ id: 'log', kind: 'card', label: '<script>原日志</script>', detail: '完整保留' }] },
      { scene_id: 's2', title: '第2段', elements: [{ id: 'history', kind: 'card', label: '历史记录', detail: '完整保留' }] },
      { scene_id: 's3', title: '第3段', elements: [] },
    ] };
}
function response(versions = [version()], head = null, revision = 1) {
  return { project_revision: revision, versions, head_version_id: head, head_stale: false };
}
function harness() {
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(row => [row[1], new Element()]));
  const document = { createElement: tag => new Element(tag), getElementById: id => {
    assert.ok(nodes.has(id), `Missing page element ${id}`); return nodes.get(id);
  } };
  const state = { context: { project: project(), epoch: 1, enabled: true, renderConfigured: true,
    deliveryReady: true, unsaved: false, projectBusy: false }, data: response(), calls: [], errors: [],
    inputErrors: [], successes: [], previews: [], deliveries: [], transport: null, refreshes: 0 };
  let panel;
  async function fallback(path, options) {
    if (path.endsWith('/proposals')) {
      const next = version(`v${state.data.versions.length + 1}`, options.body.links ?? [link()]);
      state.data.versions.push(next); return structuredClone(next);
    }
    if (path.endsWith('/adopt')) {
      state.data.head_version_id = options.body.version_id;
      state.context.project.revision += 1; state.data.project_revision += 1;
      return structuredClone(state.context.project);
    }
    return structuredClone(state.data);
  }
  panel = createContinuityPanel({ document, getContext: () => state.context,
    request: async (path, options = {}) => {
      state.calls.push({ path, options: structuredClone(options) });
      return state.transport ? state.transport(path, options, fallback) : fallback(path, options);
    },
    refresh: async () => { state.refreshes += 1; await panel.load(state.context.project.id, state.context.epoch); },
    onError: error => state.errors.push(error), onInputError: message => state.inputErrors.push(message),
    onSuccess: message => state.successes.push(message), onDraft: item => state.previews.push(item.id),
    onDelivery: item => state.deliveries.push(item.id),
  });
  return { state, panel, $: id => nodes.get(id), load: () => panel.load(state.context.project.id, state.context.epoch) };
}
const descendants = node => [node, ...node.children.flatMap(descendants)];
const linkSelects = h => descendants(h.$('continuity-review')).filter(row => row.tagName === 'select');
const writes = h => h.state.calls.filter(row => row.options.method === 'POST');

function asProcess(h) {
  for (const scene of h.state.context.project.scenes) {
    Object.assign(scene.motion.versions[0], { diagram_kind: 'process', compiler_version: 'motion-process-compiler-2' });
  }
  Object.assign(h.state.data.versions[0], { compiler: 'motion-process-continuity-compiler-1' });
  return h;
}

test('all supported process candidates can plan and preview without changing adopted media', async () => {
  const h = asProcess(harness()), before = structuredClone(h.state.context.project);
  await h.load();
  assert.deepEqual(continuityIssues(h.state.context.project, { audio: true }), []);
  assert.equal(h.$('propose-continuity').disabled, false);
  assert.equal(h.$('preview-continuity').disabled, false);
  assert.equal(writes(h).length, 0);
  await h.$('preview-continuity').emit('click');
  assert.deepEqual(h.state.previews, ['v1']);
  assert.equal(createContinuityDraftRequest(h.state.context.project, h.state.data.versions[0]).body.continuity_version_id, 'v1');
  assert.equal(writes(h).length, 0);
  assert.deepEqual(h.state.context.project, before);
});

test('old or unspecified process source compiler remains visibly blocked', async () => {
  for (const compiler of [undefined, 'motion-process-compiler-1', 'unknown']) {
    const h = asProcess(harness());
    h.state.context.project.scenes[1].motion.versions[0].compiler_version = compiler;
    await h.load();
    assert.match(h.$('continuity-issues').textContent, /第2段.*版本尚不支持/);
    assert.equal(h.$('propose-continuity').disabled, true);
    await h.panel.propose(); assert.equal(writes(h).length, 0);
  }
});

test('process cards expose state changes and cannot be copied as request objects', async () => {
  const h = asProcess(harness());
  Object.assign(h.state.data.versions[0].scene_summaries[0].elements[0],
    { initial_state: 'idle', final_state: 'running', initial_visible: true, final_visible: true });
  Object.assign(h.state.data.versions[0].scene_summaries[1].elements[0],
    { initial_state: 'running', standalone_initial_state: 'idle', final_state: 'completed', initial_visible: true, final_visible: true });
  await h.load();
  assert.match(h.$('continuity-review').textContent, /前镜终态：处理中.*下一镜独立起态：待命.*衔接起态：处理中.*衔接终态：完成/);
  assert.match(h.$('continuity-review').textContent, /处理节点可以延续/);
  const select = linkSelects(h)[0];
  assert.equal(select.children.find(row => row.value === 'copy').disabled, true);
  select.value = 'copy'; await select.emit('change');
  assert.equal(h.panel.hasDrafts(), false);
  select.value = 'none'; await select.emit('change');
  assert.equal(h.panel.hasDrafts(), true);
  assert.match(h.$('continuity-review').textContent, /摘要来自已保存候选.*保存为新候选后/);
  assert.equal(h.$('preview-continuity').disabled, true);
});

test('process token copy shows real docking labels and keeps the original despite a different destination', async () => {
  const h = asProcess(harness()), row = h.state.data.versions[0];
  row.plan.links = [{ from_scene_id: 's1', from_element_id: 'token1', to_scene_id: 's2', to_element_id: 'token2', mode: 'copy' }];
  row.scene_summaries[0].elements = [
    { id: 'queue', kind: 'card', slot: 'queue', label: '队列', detail: '' },
    { id: 'token1', kind: 'token', label: '<svg>请求', detail: '', initial_slot: 'token_source', final_slot: 'token_queue', final_visible: true },
  ];
  row.scene_summaries[1].elements = [
    { id: 'source', kind: 'card', slot: 'source', label: '入口', detail: '' },
    { id: 'worker', kind: 'card', slot: 'worker', label: '执行节点', detail: '' },
    { id: 'token2', kind: 'token', label: '<svg>请求', detail: '', initial_slot: 'token_source', final_slot: 'token_worker', final_visible: true },
  ];
  await h.load();
  assert.match(h.$('continuity-review').textContent, /前镜结束位置：队列，请求可见.*下一镜起点：入口.*结束位置：执行节点/);
  assert.match(h.$('continuity-review').textContent, /复制将保留上一镜原件/);
  assert.match(h.$('continuity-review').textContent, /<svg>请求/);
  assert.equal(linkSelects(h)[0].value, 'copy');
  assert.equal(linkSelects(h)[0].children.find(row => row.value === 'copy').disabled, false);
  assert.equal(linkSelects(h)[0].children.find(row => row.value === 'retain').disabled, true);
  assert.match(h.$('continuity-review').textContent, /延续不可用.*结束位置与下一镜起点不同/);
  linkSelects(h)[0].value = 'retain'; await linkSelects(h)[0].emit('change');
  assert.equal(h.panel.hasDrafts(), false);
  assert.equal(writes(h).length, 0);
  row.scene_summaries[0].elements[1].final_visible = false;
  h.state.context.project.revision += 1; h.state.data.project_revision += 1;
  await h.load();
  assert.equal(linkSelects(h)[0].children.find(row => row.value === 'copy').disabled, true);
  assert.match(h.$('continuity-review').textContent, /上一镜结束时请求不可见/);
});

test('detail updates are shown for review and cannot be presented as unchanged copies', async () => {
  const h = harness();
  h.state.data.versions[0].scene_summaries[1].elements[0].detail = '下一镜新增说明';
  await h.load();
  const select = linkSelects(h)[0];
  assert.equal(select.children.find(row => row.value === 'copy').disabled, true);
  assert.match(h.$('continuity-review').textContent, /完整保留.*下一镜新增说明/);
  select.value = 'copy'; await select.emit('change');
  assert.equal(h.panel.hasDrafts(), false);
});

test('proposal and draft requests bind explicit revisions and versions without speech generation', () => {
  const p = project('../p?'), links = [link('copy')];
  const intent = createContinuityProposal(p, links); links[0].mode = 'retain';
  assert.equal(intent.path, '/api/projects/..%2Fp%3F/continuity/proposals');
  assert.deepEqual(intent.body, { expected_revision: 1, links: [link('copy')] });
  assert.deepEqual(createContinuityProposal(p).body, { expected_revision: 1 });
  assert.deepEqual(createContinuityProposal(p, []).body.links, []);
  assert.throws(() => createContinuityProposal(p, [link('arbitrary')]));
  assert.deepEqual(createContinuityDraftRequest(p, version(), { endHoldSeconds: 4 }).body,
    { expected_revision: 1, purpose: 'draft', subtitles: false, continuity_version_id: 'v1', end_hold_seconds: 4 });
  assert.deepEqual(createContinuityAdoption(p, response([], 'previous'), version()).body,
    { expected_revision: 1, version_id: 'v1', expected_head_version_id: 'previous' });
  assert.throws(() => createContinuityAdoption(p, response([], 'v1'), version()));
});

test('boundary review uses inert object labels and explains boundaries without links', async () => {
  const h = harness(); await h.load();
  const text = h.$('continuity-review').textContent;
  for (const label of ['第1段', '第2段', '第3段', '<script>原日志</script>', '历史记录',
    '延续同一对象', '复制并保留原件', '不关联', '两段分别展开']) assert.ok(text.includes(label), label);
  assert.equal(linkSelects(h).length, 1);
  assert.equal(h.$('adopt-continuity').disabled, true);
  assert.equal(h.$('preview-continuity').disabled, false);
  assert.equal(h.$('deliver-continuity').disabled, true);
  assert.equal(writes(h).length, 0);
});

test('planning creates only a candidate and preserves narration and unified recordings', async () => {
  const h = harness(); h.state.data = response([]); const before = structuredClone(h.state.context.project);
  await h.load(); await h.panel.propose();
  assert.equal(writes(h).length, 1);
  assert.ok(writes(h)[0].path.endsWith('/continuity/proposals'));
  assert.equal(h.$('continuity-version').value, 'v1');
  assert.equal(h.state.data.head_version_id, null);
  assert.deepEqual(h.state.context.project, before);
  assert.equal(h.$('confirm-continuity-adoption').checked, false);
});

test('edited choices save a complete new candidate and never implicitly adopt it', async () => {
  const h = harness(); await h.load();
  linkSelects(h)[0].value = 'copy'; await linkSelects(h)[0].emit('change');
  assert.equal(h.panel.hasDrafts(), true);
  assert.equal(h.panel.hasProjectDrafts('p1'), true);
  assert.equal(h.$('preview-continuity').disabled, true);
  assert.equal(h.$('save-continuity-changes').disabled, false);
  await h.$('save-continuity-changes').emit('click');
  assert.deepEqual(writes(h)[0].options.body.links, [link('copy')]);
  assert.equal(h.$('continuity-version').value, 'v2');
  assert.equal(h.panel.hasDrafts(), false);
  assert.equal(h.state.data.head_version_id, null);
  assert.equal(h.$('confirm-continuity-adoption').checked, false);
  h.$('continuity-version').value = 'v1'; await h.$('continuity-version').emit('change');
  assert.equal(linkSelects(h)[0].value, 'retain');
  assert.equal(h.panel.hasDrafts(), false);
});

test('no association saves an explicitly empty plan rather than regenerating suggestions', async () => {
  const h = harness(); await h.load();
  linkSelects(h)[0].value = 'none'; await linkSelects(h)[0].emit('change');
  await h.$('save-continuity-changes').emit('click');
  assert.deepEqual(writes(h)[0].options.body.links, []);
});

test('reverting choices removes dirty state and refresh preserves unfinished edits', async () => {
  const h = harness(); await h.load();
  linkSelects(h)[0].value = 'copy'; await linkSelects(h)[0].emit('change'); await h.load();
  assert.equal(linkSelects(h)[0].value, 'copy');
  assert.equal(h.panel.hasDrafts(), true);
  linkSelects(h)[0].value = 'retain'; await linkSelects(h)[0].emit('change');
  assert.equal(h.panel.hasDrafts(), false);
  linkSelects(h)[0].value = 'copy'; await linkSelects(h)[0].emit('change');
  await h.$('reset-continuity-changes').emit('click');
  assert.equal(linkSelects(h)[0].value, 'retain');
  assert.equal(h.panel.hasDrafts(), false);
});

test('adoption requires a new explicit confirmation and exact reviewed version and head', async () => {
  const h = harness(); await h.load(); await h.panel.adopt(); assert.equal(writes(h).length, 0);
  h.$('confirm-continuity-adoption').checked = true; await h.$('confirm-continuity-adoption').emit('change');
  await h.panel.adopt();
  assert.deepEqual(writes(h)[0].options.body, { expected_revision: 1, version_id: 'v1', expected_head_version_id: null });
  assert.equal(h.$('confirm-continuity-adoption').checked, false);
  assert.equal(h.$('deliver-continuity').disabled, false);
  const adopted = structuredClone(h.state.context.project);
  adopted.revision = 1;
  assert.deepEqual(adopted, project());
});

test('changed project, adopted head or selected candidate invalidates confirmation', async () => {
  const h = harness(); await h.load(); h.$('confirm-continuity-adoption').checked = true;
  h.state.data = response([version(), version('other')], 'other'); await h.load();
  assert.equal(h.$('confirm-continuity-adoption').checked, false);
  h.$('confirm-continuity-adoption').checked = true;
  h.$('continuity-version').value = 'other'; await h.$('continuity-version').emit('change');
  assert.equal(h.$('confirm-continuity-adoption').checked, false);
  h.state.context.project.revision = 2; h.panel.render();
  assert.equal(h.$('preview-continuity').disabled, true);
});

test('missing audio explains the affected scene and blocks draft but permits planning', async () => {
  const h = harness(); h.state.context.project.scenes[1].head_audio_version_id = null; await h.load();
  assert.ok(h.$('continuity-issues').textContent.includes('第2段'));
  assert.ok(h.$('continuity-issues').textContent.includes('试听并采用'));
  assert.equal(h.$('propose-continuity').disabled, false);
  assert.equal(h.$('preview-continuity').disabled, true);
  await h.panel.preview(); assert.deepEqual(h.state.previews, []);
  assert.throws(() => createContinuityDraftRequest(h.state.context.project, version()));
  h.state.context.project.scenes[0].motion.head_stale = true;
  assert.ok(continuityIssues(h.state.context.project)[0].includes('第1段'));
});

test('candidate draft and adopted delivery are separate explicit actions', async () => {
  const h = harness(); await h.load(); await h.panel.preview(); await h.panel.deliver();
  assert.deepEqual(h.state.previews, ['v1']); assert.deepEqual(h.state.deliveries, []);
  h.state.data.head_version_id = 'v1'; await h.load(); await h.panel.deliver();
  assert.deepEqual(h.state.deliveries, ['v1']); assert.equal(writes(h).length, 0);
  h.state.context.deliveryReady = false; h.panel.render(); await h.panel.deliver();
  assert.equal(h.$('deliver-continuity').disabled, true);
  assert.deepEqual(h.state.deliveries, ['v1']);
});

test('server-side stale-input issues identify the scene and prevent every write and preview', async () => {
  const h = harness();
  h.state.data.issues = [{ scene_id: 's2', scene_title: '第2段', kind: 'motion', reason: 'continuity_motion_stale' }];
  await h.load();
  assert.ok(h.$('continuity-issues').textContent.includes('第2段：已采用图解'));
  assert.equal(h.$('propose-continuity').disabled, true);
  assert.equal(h.$('preview-continuity').disabled, true);
  h.$('confirm-continuity-adoption').checked = true;
  await h.panel.propose(); await h.panel.adopt(); await h.panel.preview();
  assert.equal(writes(h).length, 0); assert.deepEqual(h.state.previews, []);
});

test('historical review preserves the candidate boundary order and labels after current scenes change', async () => {
  const h = harness(); h.state.data.versions[0].stale = true;
  h.state.context.project.scenes.reverse(); h.state.context.project.scenes[2].title = '后来改名';
  await h.load();
  const text = h.$('continuity-review').textContent;
  assert.ok(text.includes('镜头 1「第1段」 → 镜头 2「第2段」'));
  assert.ok(text.includes('<script>原日志</script>'));
  assert.equal(linkSelects(h)[0].disabled, true);
});

test('a failed later read retains the reviewed choice while disabling edits until state is confirmed again', async () => {
  const h = harness(); await h.load();
  linkSelects(h)[0].value = 'copy'; await linkSelects(h)[0].emit('change');
  h.state.transport = async () => { throw new Error('network'); }; await h.load();
  assert.equal(linkSelects(h)[0].value, 'copy');
  assert.equal(linkSelects(h)[0].disabled, true);
  assert.equal(h.$('save-continuity-changes').disabled, true);
  h.state.transport = null; await h.load();
  assert.equal(linkSelects(h)[0].value, 'copy');
  assert.equal(linkSelects(h)[0].disabled, false);
  assert.equal(h.$('save-continuity-changes').disabled, false);
});

test('stale, unsaved, busy, disabled and failed reads cannot submit or render', async () => {
  for (const blocked of ['stale', 'unsaved', 'projectBusy', 'disabled', 'read']) {
    const h = harness();
    if (blocked === 'stale') h.state.data.versions[0].stale = true;
    if (blocked === 'unsaved' || blocked === 'projectBusy') h.state.context[blocked] = true;
    if (blocked === 'disabled') h.state.context.enabled = false;
    if (blocked === 'read') h.state.transport = async () => { throw new Error('network'); };
    await h.load(); h.$('confirm-continuity-adoption').checked = true;
    await h.panel.adopt(); await h.panel.preview(); await h.panel.deliver();
    assert.equal(writes(h).length, 0, blocked); assert.deepEqual(h.state.previews, [], blocked);
    if (blocked !== 'stale') { await h.panel.propose(); assert.equal(writes(h).length, 0, blocked); }
    if (blocked === 'disabled') assert.equal(h.$('continuity-panel').hidden, true);
  }
});

test('newer reads win, selection survives refresh, and late previous-epoch reads are discarded', async () => {
  const h = harness(); await h.load(); let resolveOld;
  h.state.transport = () => new Promise(resolve => { resolveOld = resolve; });
  const old = h.load();
  h.state.transport = async () => response([version(), version('new')]); await h.load();
  resolveOld(response([version('late')])); await old;
  assert.equal(h.$('continuity-version').value, 'v1');
  h.state.transport = () => new Promise(resolve => { resolveOld = resolve; });
  const oldEpoch = h.load();
  h.state.context = { ...h.state.context, project: project('other'), epoch: 2 };
  h.state.transport = async () => response([version('other')]); await h.load();
  h.state.context = { ...h.state.context, project: project(), epoch: 3 };
  h.state.transport = async () => response([version('latest')]); await h.load();
  resolveOld(response([version('from-old-epoch')])); await oldEpoch;
  assert.equal(h.$('continuity-version').value, 'latest');
});

test('duplicate clicks cannot submit twice and uncertain writes only trigger a read', async () => {
  const h = harness(); await h.load(); let complete;
  h.state.transport = (path, options, fallback) => options.method === 'POST'
    ? new Promise(resolve => { complete = resolve; }) : fallback(path, options);
  const first = h.panel.propose(); await h.panel.propose();
  assert.equal(writes(h).length, 1); assert.equal(h.panel.hasUnresolvedOperation('p1'), true);
  complete(version('v2')); await first; assert.equal(h.panel.hasUnresolvedOperation('p1'), false);
  h.state.transport = async (path, options, fallback) => {
    if (options.method === 'POST') throw new Error('connection lost');
    return fallback(path, options);
  };
  await h.panel.propose(); assert.equal(writes(h).length, 2);
  assert.ok(h.state.inputErrors[0].includes('不会自动重试'));
  assert.equal(h.state.calls.at(-1).options.method, undefined);
});

test('unknown error codes remain readable and page keeps explicit separate continuous entry points', () => {
  for (const code of ['continuity_head_conflict', '__proto__', 'constructor', 'unrecognized']) {
    assert.equal(typeof continuityErrorLabel(code), 'string');
    assert.ok(!continuityErrorLabel(code).includes(code));
  }
  assert.ok(html.includes('id="preview-continuity"'));
  assert.ok(html.includes('id="deliver-continuity"'));
  assert.ok(html.includes('id="preview-batch-project"'));
});
