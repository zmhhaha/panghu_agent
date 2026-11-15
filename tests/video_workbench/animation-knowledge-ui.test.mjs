import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {KnowledgeHTTPError, KnowledgeController, createKnowledgeClient, knowledgeFiles,
  mountKnowledgePage, renderKnowledgeMarkdown} from '../../video_workbench/animation_lab_web/knowledge.mjs';

const root = new URL('https://video-service.panghuer.top/dev/animations/');
const requestId = '00000000-0000-4000-8000-000000000001';
const otherRequestId = '00000000-0000-4000-8000-000000000002';
const sha = letter => letter.repeat(64);
const clone = value => JSON.parse(JSON.stringify(value));
const pkg = (id = 'draw-base', parent = null) => ({schema: 'video.animation-skill-package.v1', skill_id: id, version: 1,
  kind: parent ? 'expression' : 'foundation', name: id, description: '创作方法说明', runtime_requirements: {engines: ['canvas']},
  files: {'SKILL.md': '---\nname: ' + id + '\ndescription: 创作方法说明\n---\n# 创作方法\n完整动作的正文。\n## 适用条件\n- 多对象可追踪\n## 局限\n- 文字需要留出空间',
    'references/observations.md': '# 实验经验\n反例：不能删除未选对象。\n条件：通过空间聚合表现因果。\n局限：未验证超大群体。'},
  files_sha256: {'SKILL.md': sha('a'), 'references/observations.md': sha('b')}, sha256: sha('c')});
const release = (sequence = 1, changes = {}) => ({schema: 'video.animation-wikiskill-release.v1', id: 'release-' + sequence,
  owner: 'account-a', sequence, job_id: 'job-' + sequence, snapshot_id: 'snapshot-' + sequence, snapshot_sha256: sha('d'),
  input_sha256: sha('e'), created_at: 1800000000 + sequence, summary: '整库方法', packages: [pkg()],
  index: [{skill_id: 'draw-base', parent_skill_id: null, source_record_ids: ['effect-1']}],
  exclusions: [{record_id: 'effect-excluded', reason: '没有足够证据'}], limitations: ['只覆盖当前积累的方法'], sha256: sha(sequence % 2 ? 'f' : '1'), ...changes});
const summary = value => ({id: value.id, sequence: value.sequence, created_at: value.created_at, summary: value.summary,
  sha256: value.sha256, snapshot_id: value.snapshot_id, snapshot_sha256: value.snapshot_sha256,
  package_count: value.packages.length, file_count: value.packages.reduce((n, item) => n + Object.keys(item.files).length, 0)});
const job = (changes = {}) => ({schema: 'video.animation-wikiskill-job.v1', id: 'build-1', owner: 'account-a', request_id: requestId,
  state: 'running', snapshot_id: 'snapshot-build', snapshot_sha256: sha('d'), input_sha256: sha('e'), operation_id: 'operation-1',
  job_id: 'agent-job-1', release_id: null, error: null, created_at: 1800000020, updated_at: 1800000021, ...changes});
const catalogue = (releases = [release()], changes = {}) => ({schema: 'video.animation-wikiskill-catalogue.v1',
  owner_key: 'opaque-account-a', csrf_token: 'csrf-fixture', active_release_id: releases[0]?.id ?? null,
  active_release_sha256: releases[0]?.sha256 ?? null, releases: releases.map(summary), jobs: [], ...changes});
function storage() { const data = new Map(); return {data, getItem: key => data.get(key) ?? null,
  setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key)}; }
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; }
function harness(overrides = {}, options = {}) {
  const calls = [], timers = new Map(), store = options.storage ?? storage(); let nextTimer = 0, nextId = 0;
  const methods = {catalogue: async () => catalogue(), release: async id => release(Number(id.split('-').at(-1))),
    publish: async () => job(), job: async () => job(), request: async () => job(),
    activate: async raw => { const value = JSON.parse(raw); return {schema: 'video.animation-wikiskill-activation.v1',
      request_id: value.request_id, release_id: value.release_id, release_sha256: release(Number(value.release_id.split('-').at(-1))).sha256, activated_at: 1800000100}; },
    activationRequest: async () => { throw new KnowledgeHTTPError(404, 'not_found'); }, ...overrides};
  const client = Object.fromEntries(Object.entries(methods).map(([name, fn]) => [name, async (...args) => { calls.push({name, args}); return fn(...args); }]));
  const controller = new KnowledgeController({client, storage: store, uuid: () => ++nextId === 1 ? requestId : otherRequestId,
    schedule: (fn, ms) => { const id = ++nextTimer; timers.set(id, {fn, ms}); return id; }, unschedule: id => timers.delete(id), ...options});
  return {controller, calls, client, store, timers};
}
const response = (body, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => clone(body)});
const postCalls = h => h.calls.filter(call => ['publish', 'activate'].includes(call.name));

// The two pages deliberately keep one shared shell without adding a new build asset.
test('knowledge preserves the gallery shell and separates explicit publication from navigation and reads', () => {
  const read = name => readFileSync(new URL('../../video_workbench/animation_lab_web/' + name, import.meta.url), 'utf8');
  const html = read('knowledge.html'), gallery = read('method-gallery.html');
  const header = source => source.match(/<header class="topbar">([\s\S]*?)<\/header>/)?.[1]
    .replace(/ aria-current="page"/g, '').replace(/\s+/g, ' ').trim();
  assert.ok(header(html)); assert.equal(header(html), header(gallery));
  assert.match(html, /id="knowledge-publish" class="button primary" type="button" disabled>发布新版本/);
  assert.match(html, /id="knowledge-refresh" class="button secondary" type="button">手动刷新/);
  assert.match(html, /id="knowledge-activate"/);
  assert.doesNotMatch(html, /<form\b|<textarea\b|knowledge-lookup|knowledge-experiences|knowledge-publication-dialog/);
  assert.equal((html.match(/rel="stylesheet"/g) ?? []).length, 1);
  const styles = source => source.replace(/\/\*[\s\S]*?\*\//g, '');
  const rules = (css, selector) => [...styles(css).matchAll(new RegExp('(?:^|(?<=[{}]))\\s*'
    + selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^{}]*)\\}', 'g'))].map(match => match[1]);
  for (const selector of [':root', 'body', '.topbar', '.brand', '.brand-icon', '.button', '.workspace', '.workspace-nav', '.workspace-nav a',
    '.workspace-actionbar', '.library-sync', '.library-sync p', '.library-sync .button'])
    assert.deepEqual(rules(read('knowledge.css'), selector), rules(read('method-gallery.css'), selector), selector);
});

test('HTTP client uses the same-origin library routes, bound CSRF and exact frozen request bodies', async () => {
  const requests = [];
  const client = createKnowledgeClient({root, fetch: async (url, options) => { requests.push({url, options}); return response(url.endsWith('/library') ? catalogue() : {}); }});
  await client.catalogue(); await client.release('release-1'); await client.job('build-1'); await client.request(requestId); await client.activationRequest(requestId);
  const raw = JSON.stringify({request_id: requestId}); await client.publish(raw, 'opaque-account-a');
  const activeRaw = JSON.stringify({request_id: otherRequestId, release_id: 'release-1'}); await client.activate(activeRaw, 'opaque-account-a');
  assert.deepEqual(requests.map(row => row.url), ['', '/releases/release-1', '/jobs/build-1', '/requests/' + requestId,
    '/activation-requests/' + requestId, '/jobs', '/active'].map(path => root.href + 'api/method-lab/knowledge/library' + path));
  for (const {options} of requests) { assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store'); assert.equal(options.redirect, 'error'); }
  assert.equal(requests.slice(0, 5).every(row => row.options.method === 'GET' && !row.options.headers), true);
  assert.deepEqual(requests.at(-1).options.headers, {'Content-Type': 'application/json', 'X-Animation-Lab-Token': 'csrf-fixture'});
  assert.equal(requests.at(-2).options.body, raw); assert.equal(requests.at(-1).options.body, activeRaw);
  await assert.rejects(client.publish(raw, 'other-owner'), /当前账号/);
  for (const id of ['../x', 'x/y', 'x%2fy', '', 'x?other=1']) assert.throws(() => client.release(id), /ID/);
  assert.throws(() => client.publish(JSON.stringify({request_id: requestId, extra: true}), 'opaque-account-a'), /字段/);
});

test('a failed catalogue credential refresh cannot reuse a previous CSRF session', async () => {
  let reads = 0;
  const client = createKnowledgeClient({root, fetch: async () => response(++reads === 1 ? catalogue() : {schema: 'invalid'})});
  await client.catalogue(); await assert.rejects(client.catalogue(), /目录/);
  await assert.rejects(client.publish(JSON.stringify({request_id: requestId}), 'opaque-account-a'), /凭证/);
});

test('opening, refreshing and searching only read published products; an empty library stays truly empty', async () => {
  const h = harness({catalogue: async () => catalogue([])});
  await h.controller.refresh(); await h.controller.refresh(); h.controller.search('条件');
  assert.equal(h.controller.state.release, null); assert.deepEqual(h.calls.map(row => row.name), ['catalogue', 'catalogue']);
  assert.equal(h.controller.actionAvailable, true); assert.equal(postCalls(h).length, 0);
});

test('full-text search finds conditions, limitations and counterexamples in complete reference file bodies', async () => {
  const h = harness(); await h.controller.refresh(); const reads = h.calls.length;
  for (const term of ['反例', '空间聚合', '超大群体', '多对象可追踪']) {
    h.controller.search(term); assert.equal(h.controller.files.length, 1, term);
  }
  assert.equal(h.controller.files[0].path, 'SKILL.md'); assert.equal(h.calls.length, reads);
  h.controller.search('空间聚合 因果'); assert.equal(h.controller.files[0].path, 'references/observations.md');
  assert.equal(h.controller.selectFile('invented-file'), false);
});

test('hierarchical files come from exact packages and independent queries never merge release contents', async () => {
  const child = pkg('group-motion', 'draw-base'), value = release(2, {packages: [pkg(), child], index: [
    {skill_id: 'group-motion', parent_skill_id: 'draw-base', source_record_ids: ['effect-child']},
    {skill_id: 'draw-base', parent_skill_id: null, source_record_ids: ['effect-1']}]});
  assert.deepEqual(knowledgeFiles(value).map(row => [row.skillId, row.depth]), [
    ['draw-base', 0], ['draw-base', 0], ['group-motion', 1], ['group-motion', 1]]);
  const h = harness({catalogue: async () => catalogue([value, release()]), release: async id => id === value.id ? value : release()});
  await h.controller.refresh(); h.controller.search('group-motion'); assert.equal(h.controller.files.length, 2);
  await h.controller.selectRelease('release-1'); assert.equal(h.controller.files.length, 0); assert.equal(h.controller.state.selectedFileKey, null);
});

test('inconsistent content, file counts and cyclic hierarchy fail closed without a live Wiki fallback', async () => {
  for (const mutate of [value => ({...value, id: 'wrong'}), value => ({...value, sha256: sha('2')}),
    value => ({...value, packages: [{...value.packages[0], files: {'SKILL.md': 'only one'}}]}),
    value => ({...value, index: [{skill_id: 'draw-base', parent_skill_id: 'draw-base', source_record_ids: []}]})]) {
    const h = harness({release: async () => mutate(release())}); await h.controller.refresh();
    assert.equal(h.controller.state.release, null); assert.equal(h.controller.files.length, 0); assert.equal(h.controller.state.error, true);
    assert.equal(h.calls.some(row => ['page', 'draft', 'experience'].includes(row.name)), false);
  }
});

test('a slow release read cannot replace a newer user selection', async () => {
  const delayed = deferred(); let delay = false;
  const h = harness({catalogue: async () => catalogue([release(2), release()]), release: async id => delay && id === 'release-2' ? delayed.promise : release(Number(id.split('-').at(-1)))});
  await h.controller.refresh(); delay = true;
  const olderRead = h.controller.selectRelease('release-2'); await h.controller.selectRelease('release-1'); delayed.resolve(release(2)); await olderRead;
  assert.equal(h.controller.state.selectedReleaseId, 'release-1'); assert.equal(h.controller.state.release.id, 'release-1');
});

test('a single explicit publication persists one request before POST and prevents double clicks', async () => {
  const pending = deferred(); const h = harness({publish: async raw => { assert.equal(h.store.data.size, 1); assert.deepEqual(JSON.parse(raw), {request_id: requestId}); return pending.promise; }});
  await h.controller.refresh(); const submitted = h.controller.publish(); assert.equal(await h.controller.publish(), false);
  assert.equal(postCalls(h).length, 1); pending.resolve(job()); await submitted;
  assert.equal(h.controller.state.pending.job_id, 'build-1'); assert.equal(h.controller.working, true); assert.equal(h.timers.size, 1);
  assert.equal(await h.controller.retryPending(), false); assert.equal(postCalls(h).length, 1);
});

for (const failure of [new Error('network reset'), new KnowledgeHTTPError(503, 'unavailable'), {schema: 'unexpected'}]) {
  test('unknown publication persists the original identity across reload without an automatic POST: ' + (failure.message ?? 'mismatched response'), async () => {
    const h = harness({publish: async () => { if (failure instanceof Error) throw failure; return failure; }});
    await h.controller.refresh(); await h.controller.publish(); const frozen = h.controller.state.pending;
    assert.ok(frozen); assert.equal(frozen.job_id, undefined);
    const resumed = harness({request: async () => { throw new KnowledgeHTTPError(404, 'not_found'); }}, {storage: h.store});
    await resumed.controller.refresh(); assert.equal(resumed.controller.state.pending.raw, frozen.raw); assert.equal(postCalls(resumed).length, 0);
    await resumed.controller.checkPending(); assert.ok(resumed.controller.state.pending); assert.equal(postCalls(resumed).length, 0);
    assert.match(resumed.controller.state.message, /不会自动/);
    await resumed.controller.retryPending(); assert.equal(postCalls(resumed).length, 1); assert.equal(postCalls(resumed)[0].args[0], frozen.raw);
  });
}

test('known in-progress requests recover by GET and poll the same task, without animation or business generation', async () => {
  const h = harness({publish: async () => { throw new Error('lost ack'); }}); await h.controller.refresh(); await h.controller.publish();
  await h.controller.checkPending(); assert.equal(h.controller.state.pending.job_id, 'build-1'); await h.controller.pollJob();
  assert.deepEqual(h.calls.filter(row => !['catalogue', 'release'].includes(row.name)).map(row => row.name), ['publish', 'request', 'job']);
  assert.equal(h.calls.find(row => row.name === 'request').args[0], requestId);
  assert.equal(h.calls.find(row => row.name === 'job').args[0], 'build-1');
});

test('a failed build preserves the active release, clears the completed request and stops polling', async () => {
  const h = harness({job: async () => job({state: 'failed', error: {code: 'validation_failed'}})});
  await h.controller.refresh(); await h.controller.publish(); await h.controller.pollJob();
  assert.equal(h.controller.state.catalogue.active_release_id, 'release-1'); assert.equal(h.controller.state.release.id, 'release-1');
  assert.equal(h.controller.state.pending, null); assert.equal(h.store.data.size, 0); assert.equal(h.timers.size, 0);
  assert.equal(h.controller.actionAvailable, true); assert.equal(postCalls(h).length, 1);
});

test('successful background publication reads its new catalogue instead of inventing an active pointer', async () => {
  let published = false;
  const h = harness({catalogue: async () => published ? catalogue([release(2), release()], {jobs: [job({state: 'published', release_id: 'release-2'})]}) : catalogue(),
    job: async () => { published = true; return job({state: 'published', release_id: 'release-2'}); }});
  await h.controller.refresh(); await h.controller.publish(); await h.controller.pollJob();
  assert.equal(h.controller.state.catalogue.active_release_id, 'release-2'); assert.equal(h.controller.state.release.id, 'release-2');
  assert.equal(h.controller.state.pending, null); assert.equal(h.timers.size, 0); assert.equal(postCalls(h).length, 1);
});

test('a completed task found on reload clears the saved request without retrying it', async () => {
  const h = harness(); await h.controller.refresh(); await h.controller.publish();
  const resumed = harness({catalogue: async () => catalogue([release()], {jobs: [job({state: 'failed', error: {code: 'failed'}})]})}, {storage: h.store});
  await resumed.controller.refresh(); assert.equal(resumed.controller.state.pending, null); assert.equal(resumed.controller.actionAvailable, true);
  assert.equal(postCalls(resumed).length, 0); assert.equal(resumed.controller.state.catalogue.active_release_id, 'release-1');
});

test('task identity mismatches retain the original task and never start a replacement', async () => {
  const h = harness({job: async () => job({snapshot_sha256: sha('2')})}); await h.controller.refresh(); await h.controller.publish();
  await h.controller.pollJob(); assert.equal(h.controller.state.job.snapshot_sha256, sha('d'));
  assert.ok(h.controller.state.pending); assert.equal(postCalls(h).length, 1); assert.match(h.controller.state.message, /身份/);
});

test('activating a historical release is explicit and uses the original receipt, not an inferred active pointer', async () => {
  let active = 'release-2'; const activate = deferred();
  const h = harness({catalogue: async () => catalogue([release(2), release()], {active_release_id: active, active_release_sha256: release(Number(active.split('-').at(-1))).sha256}),
    activate: async raw => { const value = JSON.parse(raw); assert.equal(value.release_id, 'release-1'); throw new Error('lost ack'); },
    activationRequest: async id => { assert.equal(id, requestId); return activate.promise; }});
  await h.controller.refresh(); await h.controller.selectRelease('release-1'); assert.equal(postCalls(h).length, 0);
  await h.controller.activate(); assert.equal(h.controller.state.pending.kind, 'activate');
  // Another operation can restore the latest active pointer after the original request completed.
  const recovery = h.controller.checkPending(); activate.resolve({schema: 'video.animation-wikiskill-activation.v1', request_id: requestId,
    release_id: 'release-1', release_sha256: release().sha256, activated_at: 1800000100}); await recovery;
  assert.equal(h.controller.state.pending, null); assert.equal(h.controller.state.catalogue.active_release_id, 'release-2');
  assert.equal(postCalls(h).length, 1); assert.equal(h.calls.some(row => row.name === 'activationRequest'), true);
});

test('unmatched activation receipts keep the original request frozen', async () => {
  const h = harness({catalogue: async () => catalogue([release(2), release()]), activate: async () => ({schema: 'video.animation-wikiskill-activation.v1',
    request_id: requestId, release_id: 'release-1', release_sha256: sha('0'), activated_at: 1800000100})});
  await h.controller.refresh(); await h.controller.selectRelease('release-1'); await h.controller.activate();
  assert.ok(h.controller.state.pending); assert.equal(h.controller.state.catalogue.active_release_id, 'release-2');
  assert.equal(await h.controller.publish(), false); assert.equal(postCalls(h).length, 1);
});

test('unknown requests remain isolated to owner_key and a late previous-owner reply cannot enter the new session', async () => {
  const ack = deferred(); let owner = 'a';
  const h = harness({catalogue: async () => owner === 'a' ? catalogue() : catalogue([], {owner_key: 'opaque-account-b'}), publish: async () => ack.promise});
  await h.controller.refresh(); const old = h.controller.publish(); owner = 'b'; await h.controller.refresh();
  assert.equal(h.controller.state.ownerKey, 'opaque-account-b'); assert.equal(h.controller.state.pending, null);
  ack.resolve(job()); await old;
  assert.equal(h.controller.state.job, null); assert.equal(h.controller.state.release, null); assert.equal(h.store.data.size, 1);
  owner = 'a'; await h.controller.refresh(); assert.equal(h.controller.state.pending.request_id, requestId);
  assert.equal(postCalls(h).length, 1);
});

test('session storage failures block writes before POST while published reading continues', async () => {
  const broken = {getItem: () => null, setItem: () => { throw new Error('quota'); }, removeItem() {}};
  const h = harness({}, {storage: broken}); await h.controller.refresh(); await h.controller.publish();
  assert.equal(h.controller.state.release.id, 'release-1'); assert.equal(postCalls(h).length, 0); assert.match(h.controller.state.message, /没有提交/);
});

test('invalid stored records cannot be replaced by a new operation', async () => {
  const store = storage(); store.setItem('video.animation-wikiskill.pending.v1:opaque-account-a', '{not-json');
  const h = harness({}, {storage: store}); await h.controller.refresh();
  assert.equal(h.controller.state.storageBlocked, true); assert.equal(await h.controller.publish(), false);
  assert.equal(postCalls(h).length, 0); assert.equal(h.controller.state.release.id, 'release-1');
});

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.attributes = {}; this.value = ''; this.textContent = ''; this.classList = {toggle() {}}; }
  set innerHTML(_) { throw new Error('Model content must never be interpreted as HTML'); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, action) { this.listeners[name] = action; }
  setAttribute(name, value) { this.attributes[name] = value; }
  all() { return [this, ...this.children.flatMap(node => node.all())]; }
}
async function mounted(overrides = {}, options = {}) {
  const elements = new Map(); const document = {createElement: tag => new Element(tag), getElementById: id => {
    if (!elements.has(id)) elements.set(id, new Element('div')); return elements.get(id);
  }};
  const h = harness(overrides, options); mountKnowledgePage(document, h.controller);
  await new Promise(resolve => setImmediate(resolve)); return {...h, document};
}

test('the published reader displays full reference content and safe Markdown, with no human editing controls', async () => {
  const malicious = '<img src=x onerror=alert(1)>';
  const value = release(); value.packages[0].files['references/observations.md'] += '\n' + malicious;
  const h = await mounted({release: async () => value}); h.controller.search('未验证超大群体');
  const detail = h.document.getElementById('knowledge-detail');
  assert.ok(detail.all().some(node => node.textContent.includes('反例：不能删除未选对象')));
  assert.ok(detail.all().some(node => node.textContent.includes(malicious)));
  assert.equal(detail.all().some(node => ['form', 'input', 'textarea', 'script', 'img'].includes(node.tagName)), false);
  assert.equal(postCalls(h).length, 0); assert.equal(h.document.getElementById('knowledge-files').all().some(node => node.attributes['aria-current'] === 'page'), true);
});

test('empty and failed published states do not display working Wiki or fabricate knowledge', async () => {
  const h = await mounted({catalogue: async () => catalogue([])});
  assert.ok(h.document.getElementById('knowledge-detail').all().some(node => node.textContent === '尚无成功发布的 WikiSkill'));
  assert.equal(h.document.getElementById('knowledge-search').disabled, true);
  assert.equal(h.document.getElementById('knowledge-publish').disabled, false);
  assert.equal(h.calls.length, 1);
});

test('Markdown code and frontmatter remain inert complete text', () => {
  const document = {createElement: tag => new Element(tag)}, target = new Element('article');
  renderKnowledgeMarkdown(document, target, '---\nname: method\ndescription: test\n---\n# 标题\n## 局限\n- 限制一\n```html\n<script>alert(1)</script>\n```');
  assert.ok(target.all().some(node => node.tagName === 'pre' && node.textContent.includes('<script>')));
  assert.ok(target.all().some(node => node.className === 'frontmatter' && node.textContent.includes('description: test')));
  assert.equal(target.all().some(node => node.tagName === 'script'), false);
  assert.ok(target.all().some(node => node.tagName === 'h2' && node.textContent === '局限'));
});
