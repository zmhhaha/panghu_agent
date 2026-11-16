import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import vm from 'node:vm';
import * as model from '../../video_workbench/animation_lab_web/model.mjs';

const appUrl = new URL('../../video_workbench/animation_lab_web/app.mjs', import.meta.url);
const source = (await readFile(appUrl, 'utf8'))
  .replace(/^import \{[\s\S]*?\} from '\.\/model\.mjs';\s*/, '')
  .replace("new URL('./', import.meta.url)", "new URL('https://video-service.panghuer.top/dev/animations/')");
const id = 'a'.repeat(64), cursor = 'b'.repeat(64), revision = 'c'.repeat(40);
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
const response = value => ({ok: true, status: 200, json: async () => value});
const summary = (module, overrides = {}) => ({module, source_revision: revision,
  current_cursor: cursor, module_cursor: null, current_effective_feedback: [], preview_delta: [], tasks: [], ...overrides});

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.listeners = new Map();
    this.dataset = {}; this.value = ''; this.hidden = false; this.disabled = false; this.open = false;
    this.classList = {toggle() {}, add() {}, remove() {}}; this.style = {}; this.text = '';
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ''; this.children = children; }
  get firstElementChild() { return this.children[0]; }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
  dispatch(type) { return this.listeners.get(type)?.({target: this, preventDefault() {}}); }
  querySelector() { return new Element(); }
}

async function harness(fetcher) {
  const elements = new Map();
  const get = name => { if (!elements.has(name)) elements.set(name, new Element()); return elements.get(name); };
  const document = {getElementById: get, createElement: tag => new Element(tag),
    querySelectorAll: () => [], addEventListener() {}, hidden: false};
  const requests = [];
  vm.runInNewContext(source, {...model, document,
    window: {addEventListener() {}}, history: {replaceState() {}},
    location: {origin: 'https://video-service.panghuer.top', href: 'https://video-service.panghuer.top/dev/animations/', hash: ''},
    URL, crypto: webcrypto, AbortController, DOMException, performance, setTimeout, clearTimeout,
    cancelAnimationFrame() {}, requestAnimationFrame() {},
    fetch: async (url, options) => {
      const path = new URL(url).pathname + new URL(url).search;
      requests.push({path, options});
      if (path.endsWith('/api/catalog')) return response({csrf_token: 'synthetic-csrf',
        modules: [{id: 'canvas', label: 'Canvas'}, {id: 'three', label: 'Three.js'}], cases: []});
      return fetcher(path, options);
    }});
  await tick();
  return {get, requests};
}

test('a late Canvas response cannot replace a newer Three.js module summary', async () => {
  const canvas = deferred(), three = deferred();
  const {get} = await harness(path => path.endsWith('module=canvas') ? canvas.promise : three.promise);
  get('iteration-panel').open = true; get('iteration-panel').dispatch('toggle');
  get('iteration-module').value = 'three'; get('iteration-module').dispatch('change');
  three.resolve(response(summary('three', {preview_delta: [{op: 'upsert', reason: 'new', feedback: {comment: 'three-only'}}]})));
  await tick();
  assert.match(get('iteration-source').textContent, /Three\.js/);
  assert.equal(get('iteration-prepare').disabled, false);
  canvas.resolve(response(summary('canvas')));
  await tick();
  assert.match(get('iteration-source').textContent, /Three\.js/);
  assert.equal(get('iteration-prepare').disabled, false);
  assert.match(get('iteration-changes').textContent, /three-only/);
});

test('task preparation uses exact cursors and CSRF, and does not create duplicate requests during a module switch', async () => {
  const created = deferred();
  let posts = 0;
  const {get, requests} = await harness((path, options) => {
    if (options.method === 'POST') { posts++; return created.promise; }
    return response(summary(path.endsWith('module=three') ? 'three' : 'canvas',
      path.endsWith('module=canvas') ? {preview_delta: [{op: 'upsert', feedback: {comment: 'change'}}]} : {}));
  });
  get('iteration-panel').open = true; get('iteration-panel').dispatch('toggle'); await tick();
  const saving = get('iteration-prepare').dispatch('click');
  get('iteration-prepare').dispatch('click');
  assert.equal(posts, 1);
  const post = requests.find(row => row.options.method === 'POST');
  assert.deepEqual(JSON.parse(post.options.body), {module: 'canvas', base_cursor: null, expected_cursor: cursor});
  assert.equal(post.options.headers['X-Animation-Lab-Token'], 'synthetic-csrf');
  assert.equal(post.options.credentials, 'same-origin');
  get('iteration-module').value = 'three'; get('iteration-module').dispatch('change'); await tick();
  created.resolve(response({task_id: id, state: 'prepared', package: {module: 'canvas'}}));
  await saving; await tick();
  assert.match(get('iteration-source').textContent, /Three\.js/);
  assert.equal(get('iteration-prepare').disabled, true);
  assert.equal(get('iteration-message').hidden, true);
});

test('developer acceptance submits a bound event and leaves production adoption outside the flow', async () => {
  const proof = {task_identity: id, module: 'canvas', source_revision: revision, commit: 'f'.repeat(40),
    run_slug: 'canvas-r1', report_identity: 'e'.repeat(64), run_identity: 'd'.repeat(64),
    artifact_sha256s: ['9'.repeat(64)], semantic_input_sha256: '8'.repeat(64), publication_identity: '7'.repeat(64),
    case_id: 'canvas-queue', covered_feedback_keys: ['annotation:one'], uncovered_feedback_keys: [],
    verification_scope: 'frozen_report_and_developer_publication_binding',
    candidate_url: '/dev/animations/#evaluation-canvas-r1-' + 'd'.repeat(16)};
  let current = {task_id: id, state: 'candidate_ready', current_feedback_matches: true,
    acceptance_scope: 'developer_sample_only', production_adopted: false,
    package: {identity: id, module: 'canvas', source_revision: revision, feedback: [{key: 'annotation:one', case_id: 'canvas-queue'}]},
    events: [{state: 'candidate_ready', receipt: {commit: proof.commit, run_slug: proof.run_slug,
      report_identity: proof.report_identity, candidate_url: proof.candidate_url}, verified_candidate: proof}]};
  let submitted;
  const {get} = await harness((path, options) => {
    if (options.method === 'POST') {
      submitted = JSON.parse(options.body);
      current = {...current, state: submitted.state, events: [submitted]};
      return response(current);
    }
    return response(summary('canvas', {tasks: [current]}));
  });
  get('iteration-panel').open = true; get('iteration-panel').dispatch('toggle'); await tick();
  const all = element => [element, ...element.children.flatMap(all)];
  const nodes = all(get('iteration-tasks'));
  const comment = nodes.find(element => element.tagName === 'TEXTAREA' && element.placeholder);
  comment.value = '镜头承接更清楚，开发样例可以接受。'; comment.dispatch('input');
  const accept = nodes.find(element => element.tagName === 'BUTTON' && element.textContent === '开发样例通过');
  accept.dispatch('click'); await tick();
  assert.equal(submitted.state, 'accepted');
  assert.equal(submitted.expected_state, 'candidate_ready');
  assert.match(submitted.idempotency_key, /^[0-9a-f-]{36}$/);
  assert.deepEqual(submitted.receipt, {task_identity: id, module: 'canvas', source_revision: revision,
    comment: '镜头承接更清楚，开发样例可以接受。'});
  assert.equal('production_adopted' in submitted, false);
  assert.match(get('iteration-tasks').textContent, /开发样例验收通过/);
  assert.equal(all(get('iteration-tasks')).some(element => element.tagName === 'BUTTON' && element.textContent === '开发样例通过'), false);
});
