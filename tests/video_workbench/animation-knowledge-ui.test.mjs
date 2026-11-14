import test from 'node:test';
import assert from 'node:assert/strict';
import {KnowledgeHTTPError, KnowledgeController, createKnowledgeClient, draftFromPage, buildKnowledgeRevision,
  mountKnowledgePage} from '../../video_workbench/animation_lab_web/knowledge.mjs';

const root = new URL('https://video-service.panghuer.top/dev/animations/');
const clone = value => JSON.parse(JSON.stringify(value));
const page = (revision = 1, changes = {}) => ({schema: 'video.animation-knowledge-page.v1', page_id: 'draw:async', revision,
  body: {title: '异步绘制', content: '以实测事件确定完成时点。', applicability: ['异步初始化'], limitations: ['尚需迁移验证'],
    counterexamples: [{description: '固定延迟不足以确认完成', evidence_ids: ['exp:failure']}]},
  evidence_ids: ['exp:failure'], relations: [{relation: 'extends', page_id: 'draw:base', revision: 1, content_sha256: 'a'.repeat(64)}],
  status: 'provisional', content_sha256: 'b'.repeat(64), sha256: 'c'.repeat(64),
  change_kind: revision === 1 ? 'create' : 'correct', change_note: '已有原因', ...changes});
const catalogue = () => ({pages: [{page_id: 'draw:async', revision: 1, title: '异步绘制', status: 'provisional'}],
  experiences: [{id: 'exp:failure', kind: 'failure', source_id: 'actual-run'}], csrf_token: 'csrf-fixture'});
const response = (body, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => clone(body)});
function harness(overrides = {}) {
  const calls = []; let operation = 0;
  const client = Object.fromEntries(Object.entries({catalogue: async () => catalogue(),
    page: async () => ({page: page(), history: [{revision: 1}]}),
    revision: async (_, revision) => ({page: page(revision), current_revision: 3}),
    experience: async id => ({id, kind: 'failure', document: {error: 'Actual recorded failure'}}),
    revise: async (_, raw) => { const body = JSON.parse(raw); return page(body.expected_revision + 1, body); },
    ...overrides}).map(([name, fn]) => [name, async (...args) => { calls.push({name, args}); return fn(...args); }]));
  const controller = new KnowledgeController({client, uuid: () => 'request:' + ++operation});
  return {controller, client, calls};
}
async function editing(h) {
  await h.controller.refresh(); await h.controller.select('page', 'draw:async'); assert.equal(h.controller.edit(), true);
  h.controller.update({...h.controller.state.editor.fields, content: '修正后的理解', change_note: '真实失败说明需要改正'});
}
const acknowledgment = raw => { const body = JSON.parse(raw); return page(body.expected_revision + 1, body); };

test('knowledge client scopes all reads and explicit CSRF writes to the prefixed knowledge API', async () => {
  const requests = [];
  const client = createKnowledgeClient({root, fetch: async (url, options) => {
    requests.push({url, options}); return response(url.endsWith('/knowledge') ? catalogue() : {});
  }});
  await client.catalogue(); await client.page('draw:async'); await client.revision('draw:async', 2); await client.experience('exp:failure');
  const raw = '{"request_id":"exact"}'; await client.revise('draw:async', raw);
  assert.deepEqual(requests.map(item => item.url), ['', '/pages/draw%3Aasync', '/pages/draw%3Aasync/revisions/2',
    '/experiences/exp%3Afailure', '/pages/draw%3Aasync'].map(path => root.href + 'api/method-lab/knowledge' + path));
  for (const {options} of requests) {
    assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store'); assert.equal(options.redirect, 'error');
  }
  assert.equal(requests.slice(0, 4).every(item => item.options.method === 'GET' && !item.options.headers), true);
  assert.deepEqual(requests.at(-1).options.headers, {'Content-Type': 'application/json', 'X-Animation-Lab-Token': 'csrf-fixture'});
  assert.equal(requests.at(-1).options.body, raw);
  for (const id of ['../x', 'x/y', 'x%2fy', '', 'x?user=other']) assert.throws(() => client.page(id), /ID/);
  for (const revision of [0, -1, '1', true, 1.1, 1000001]) assert.throws(() => client.revision('draw:async', revision), /版本号/);
  assert.equal(requests.length, 5);
});

test('writes require a catalogue credential and non-JSON success is an uncertain response', async () => {
  const client = createKnowledgeClient({root, fetch: async () => ({ok: true, status: 200, json: async () => { throw new Error('HTML login'); }})});
  await assert.rejects(client.revise('draw:async', '{}'), /凭证/);
  await assert.rejects(client.page('draw:async'), error => error instanceof KnowledgeHTTPError && error.status === 0);
});

test('reading records is passive and historical bodies and real experiences cannot enter the editor', async () => {
  const h = harness(); await h.controller.refresh();
  await h.controller.select('page', 'draw:async', 1); assert.equal(h.controller.edit(), false);
  await h.controller.select('experience', 'exp:failure'); assert.equal(h.controller.edit(), false);
  assert.deepEqual(h.calls.map(item => item.name), ['catalogue', 'revision', 'experience']);
  assert.equal(h.controller.state.pending, null);
});

test('explicit revisions bind the read revision, preserve advanced evidence and never carry owner or publication instructions', () => {
  const source = page(4), fields = {...draftFromPage(source), expected_revision: 999, status: 'active', change_note: '纠正原因'};
  const result = buildKnowledgeRevision(source, fields, 'request:1');
  assert.equal(result.expected_revision, 4); assert.equal(result.request_id, 'request:1');
  assert.deepEqual(result.body.counterexamples, source.body.counterexamples); assert.deepEqual(result.relations, source.relations);
  assert.deepEqual(result.evidence_ids, source.evidence_ids); assert.equal(result.change_kind, 'correct');
  assert.equal(Object.hasOwn(result, 'owner'), false); assert.equal(Object.hasOwn(result, 'publish'), false);
  assert.equal(buildKnowledgeRevision(source, {...fields, status: 'deprecated'}, 'request:2').change_kind, 'deprecate');
  assert.throws(() => buildKnowledgeRevision(source, {...fields, change_note: ' '}, 'request:3'), /原因/);
});

test('successful save freezes the exact revision and reads current head after an idempotent acknowledgment', async () => {
  let headReads = 0;
  const h = harness({page: async () => ({page: page(++headReads === 1 ? 2 : 5), history: []})});
  await editing(h); await h.controller.save();
  const write = h.calls.find(item => item.name === 'revise');
  assert.equal(JSON.parse(write.args[1]).expected_revision, 2);
  assert.equal(h.controller.state.detail.page.revision, 5);
  assert.equal(h.controller.state.pending, null); assert.equal(h.controller.state.editor, null);
  assert.equal(h.controller.state.draft, null);
  assert.deepEqual(h.calls.map(item => item.name), ['catalogue', 'page', 'revise', 'page', 'catalogue']);
});

test('CAS conflict keeps the draft, never retries and requires an explicit latest read before another edit', async () => {
  let reads = 0;
  const h = harness({page: async () => ({page: page(++reads), history: []}),
    revise: async () => { throw new KnowledgeHTTPError(409, 'revision_conflict'); }});
  await editing(h); await h.controller.save();
  assert.equal(h.controller.state.draft.fields.content, '修正后的理解');
  assert.equal(h.controller.state.pending, null); assert.equal(h.controller.edit(), false);
  assert.equal(await h.controller.save(), false); assert.equal(h.calls.filter(item => item.name === 'revise').length, 1);
  await h.controller.select('page', 'draw:async'); assert.equal(h.controller.edit(), true);
  assert.equal(h.controller.state.editor.page.revision, 2);
  assert.equal(h.controller.state.editor.fields.content, '修正后的理解');
  await h.controller.save();
  const writes = h.calls.filter(item => item.name === 'revise').map(item => JSON.parse(item.args[1]));
  assert.deepEqual(writes.map(item => item.expected_revision), [1, 2]);
  assert.notEqual(writes[0].request_id, writes[1].request_id);
});

for (const failure of [new Error('Network reset'), new KnowledgeHTTPError(503, 'unavailable'), null]) {
  test('uncertain submission freezes original ID and body until an explicit identical retry: ' + (failure?.message ?? 'malformed success'), async () => {
    let attempts = 0;
    const h = harness({revise: async (_, raw) => {
      if (++attempts === 1) { if (failure) throw failure; return {revision: 2}; }
      return acknowledgment(raw);
    }});
    await editing(h); await h.controller.save();
    const pending = h.controller.state.pending; assert.ok(pending); assert.equal(Object.isFrozen(pending), true);
    h.controller.update({...h.controller.state.editor.fields, content: '不得替换冻结请求'});
    assert.equal(await h.controller.save(), false); assert.equal(await h.controller.select('page', 'another'), false);
    await h.controller.checkPending(); assert.equal(h.controller.state.pending, pending);
    assert.equal(attempts, 1); await h.controller.submitPending(); assert.equal(attempts, 2);
    const writes = h.calls.filter(item => item.name === 'revise');
    assert.equal(writes[0].args[1], writes[1].args[1]); assert.equal(writes[1].args[1], pending.raw);
    assert.equal(h.controller.state.pending, null);
  });
}

test('a structurally plausible but mismatched acknowledgment does not unlock new writes', async () => {
  const h = harness({revise: async (_, raw) => ({...acknowledgment(raw), change_note: 'unrelated operation'})});
  await editing(h); await h.controller.save();
  assert.ok(h.controller.state.pending); assert.equal(await h.controller.save(), false);
  assert.equal(h.calls.filter(item => item.name === 'revise').length, 1);
});

test('a successful HTTP response without content identity remains an unconfirmed submission', async () => {
  const h = harness({revise: async (_, raw) => { const result = acknowledgment(raw); delete result.content_sha256; return result; }});
  await editing(h); await h.controller.save();
  assert.ok(h.controller.state.pending); assert.equal(await h.controller.save(), false);
  assert.equal(h.calls.filter(item => item.name === 'revise').length, 1);
});

test('known validation rejection retains editable fields without creating automatic work', async () => {
  const h = harness({revise: async () => { throw new KnowledgeHTTPError(422, 'invalid_evidence'); }});
  await editing(h); await h.controller.save();
  assert.equal(h.controller.state.pending, null); assert.equal(h.controller.state.editor.fields.content, '修正后的理解');
  assert.match(h.controller.state.message, /拒绝/); assert.equal(h.calls.filter(item => item.name === 'revise').length, 1);
});

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.value = ''; this.classList = {toggle() {}}; }
  set innerHTML(_) { throw new Error('Untrusted content must not be parsed as HTML'); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, action) { this.listeners[name] = action; }
  all() { return [this, ...this.children.flatMap(node => node.all())]; }
}

test('mounted page renders model content as text and exposes historical reads without write controls', async () => {
  const elements = new Map();
  const document = {createElement: tag => new Element(tag), getElementById: id => {
    if (!elements.has(id)) elements.set(id, new Element('div')); return elements.get(id);
  }};
  const malicious = '<img src=x onerror=alert(1)>';
  const h = harness({page: async () => ({page: page(1, {body: {...page().body, content: malicious}}), history: [{revision: 1}]})});
  mountKnowledgePage(document, h.controller);
  await new Promise(resolve => setImmediate(resolve)); await h.controller.select('page', 'draw:async');
  const detail = document.getElementById('knowledge-detail');
  assert.ok(detail.all().some(node => node.textContent === malicious));
  assert.ok(detail.all().some(node => node.textContent === '修订当前页面'));
  await h.controller.select('page', 'draw:async', 1);
  assert.ok(detail.all().some(node => node.textContent === '历史版本 · 只读'));
  assert.equal(detail.all().some(node => node.textContent === '修订当前页面'), false);
  assert.equal(h.calls.some(item => item.name === 'revise'), false);
});
