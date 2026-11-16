import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {CreationRunsController, creationAssetUrl, createCreationRunsPanel,
  renderCreationVersion, renderCreationReview} from '../../video_workbench/animation_lab_web/creation-runs.mjs';

const root = new URL('https://video-service.panghuer.top/dev/animations/');
const requestId = '00000000-0000-4000-8000-000000000001';
const definition = {id: 'motion', identity: 'frozen-definition', title: '运动关系'};
const limits = {initial_creations: 1, reviews: 1, creator_revisions: 1, renders: 2};
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };
const storage = () => {
  const values = new Map();
  return {values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value)};
};
const run = (changes = {}) => ({schema: 'video.animation-creation-run.v1', id: 'run-one', request_id: requestId,
  input_role: 'development', experiment: {...definition}, state: 'queued', stage: 'initial_creation',
  versions: [], review: null, error: null, aesthetic_status: 'unreviewed', ...changes});
const listing = (runs = [], changes = {}) => ({enabled: true, disabled_reason: null, limits, runs, ...changes});
function harness(handler, changes = {}) {
  const requests = [], timers = new Map(), store = changes.storage ?? storage(); let next = 0;
  const controller = new CreationRunsController({root, storage: store, uuid: () => requestId,
    request: async (path, options = {}) => { requests.push({path, options}); return handler(path, options); },
    schedule: fn => { const id = ++next; timers.set(id, fn); return id; }, unschedule: id => timers.delete(id), ...changes});
  return {controller, requests, timers, store,
    async advance() { const [id, fn] = timers.entries().next().value ?? []; assert.ok(fn, 'Expected a pending read'); timers.delete(id); fn(); await tick(); }};
}

test('opening a disabled creation panel only reads capability state and keeps the real reason', async () => {
  const {controller, requests, timers} = harness(() => listing([], {enabled: false, disabled_reason: 'Worker尚未准入'}));
  await controller.select(definition);
  assert.equal(controller.snapshot.canStart, false);
  assert.equal(controller.snapshot.disabledReason, 'Worker尚未准入');
  assert.equal(await controller.start(), false);
  assert.deepEqual(requests.map(row => row.options.method ?? 'GET'), ['GET']);
  assert.equal(timers.size, 0);
});

test('missing capability limits and failed reads cannot appear as an enabled successful run', async () => {
  for (const value of [listing([], {limits: {renders: 10}}), listing([], {runs: undefined})]) {
    const {controller, requests} = harness(() => value);
    await controller.select(definition);
    assert.equal(controller.snapshot.canStart, false); assert.match(controller.snapshot.error, /不完整/);
    assert.equal(requests.length, 1);
  }
  const {controller} = harness(() => { throw Error('HTTP503'); });
  await controller.select(definition);
  assert.match(controller.snapshot.error, /HTTP503/); assert.equal(controller.snapshot.runs.length, 0);
});

test('double-clicking starts once and persists the exact UUID before the only POST', async () => {
  const posted = deferred(); let captured;
  const h = harness((path, options) => {
    if (options.method === 'POST') {
      captured = JSON.parse(options.body);
      const persisted = JSON.parse([...h.store.values.values()][0]);
      assert.equal(persisted.request_id, captured.request_id); return posted.promise;
    }
    return listing();
  });
  await h.controller.select(definition);
  const started = h.controller.start();
  assert.equal(await h.controller.start(), false);
  assert.deepEqual(captured, {request_id: requestId, experiment_id: definition.id,
    definition_identity: definition.identity, input_role: 'development'});
  posted.resolve(run()); await started;
  assert.equal(h.requests.filter(row => row.options.method === 'POST').length, 1);
  assert.equal(h.controller.snapshot.canStart, false);
  assert.equal(h.controller.snapshot.record.run_id, 'run-one');
  assert.equal(h.timers.size, 1);
});

test('a lost response is reconciled by GET with the saved request after remount, never by another POST', async () => {
  const store = storage(); let posts = 0, visible = [];
  const handler = (_, options) => {
    if (options.method === 'POST') { posts++; visible = [run()]; throw Error('response lost after admission'); }
    return listing(visible);
  };
  const first = harness(handler, {storage: store}); await first.controller.select(definition);
  assert.equal(await first.controller.start(), false);
  assert.match(first.controller.snapshot.error, /不会自动重发或新建/);
  assert.equal(await first.controller.start(), false);
  first.controller.pause();
  const second = harness(handler, {storage: store, uuid: () => assert.fail('Recovery cannot create a UUID')});
  await second.controller.select(definition);
  assert.equal(second.controller.snapshot.record.request_id, requestId);
  assert.equal(second.controller.snapshot.record.run_id, 'run-one');
  assert.equal(second.controller.snapshot.runs[0].id, 'run-one');
  assert.equal(await second.controller.start(), false);
  assert.equal(posts, 1);
  assert.ok(second.requests.every(row => !row.options.method));
});

test('a saved request missing from reads stays blocked and is never silently replaced', async () => {
  const h = harness((_, options) => {
    if (options.method === 'POST') throw Error('lost');
    return listing();
  });
  await h.controller.select(definition); await h.controller.start();
  await h.controller.refresh(); await h.controller.resume();
  assert.equal(h.controller.snapshot.canStart, false); assert.match(h.controller.snapshot.notice, /尚未查到匹配任务/);
  assert.equal(h.requests.filter(row => row.options.method === 'POST').length, 1);
  assert.equal(h.controller.snapshot.record.request_id, requestId);
  assert.equal(h.timers.size, 0, 'No endless polling for an unconfirmed missing record');
});

test('unavailable, corrupt or unwritable persistence blocks creation before network submission', async () => {
  for (const store of [null, {getItem() { throw Error('storage disabled'); }},
    {getItem: () => 'corrupt-json', setItem() {}}, {getItem: () => null, setItem() { throw Error('quota exceeded'); }}]) {
    const h = harness(() => listing(), {storage: store});
    await h.controller.select(definition); await h.controller.start();
    assert.equal(h.requests.some(row => row.options.method === 'POST'), false);
    assert.ok(h.controller.snapshot.error);
  }
});

test('switching definition or input group aborts stale reads and cannot show an earlier response', async () => {
  const old = deferred(), newer = {...definition, id: 'geometry', identity: 'geometry-frozen'};
  const h = harness(path => path.includes('experiment_id=motion') ? old.promise : listing());
  const oldLoad = h.controller.select(definition);
  const oldSignal = h.requests[0].options.signal;
  await h.controller.select(newer, 'held_out');
  assert.equal(oldSignal.aborted, true);
  old.resolve(listing([run()])); await oldLoad;
  assert.equal(h.controller.snapshot.context.id, 'geometry');
  assert.equal(h.controller.snapshot.context.input_role, 'held_out');
  assert.deepEqual(h.controller.snapshot.runs, []);
  assert.equal(h.controller.snapshot.canStart, true);
});

test('a late POST response persists its original identity without replacing a new experiment', async () => {
  const posted = deferred();
  const h = harness((_, options) => options.method === 'POST' ? posted.promise : listing());
  await h.controller.select(definition); const pending = h.controller.start();
  await h.controller.select({...definition, id: 'other', identity: 'other-definition'});
  posted.resolve(run()); await pending;
  assert.equal(h.controller.snapshot.context.id, 'other');
  assert.deepEqual(h.controller.snapshot.runs, []); assert.equal(h.controller.snapshot.error, '');
  const saved = [...h.store.values.values()].map(JSON.parse);
  assert.equal(saved[0].experiment_id, 'motion'); assert.equal(saved[0].run_id, 'run-one');
});

test('a late lost POST response cannot erase a run already recovered by GET after resuming', async () => {
  const posted = deferred(); let submitted = false;
  const h = harness((_, options) => {
    if (options.method === 'POST') { submitted = true; return posted.promise; }
    return listing(submitted ? [run()] : []);
  });
  await h.controller.select(definition); const pending = h.controller.start();
  h.controller.pause(); await h.controller.resume();
  assert.equal(h.controller.snapshot.record.run_id, 'run-one');
  posted.reject(Error('response lost')); await pending;
  assert.equal(h.controller.snapshot.submitting, false);
  assert.equal(JSON.parse([...h.store.values.values()][0]).run_id, 'run-one');
  assert.equal(h.requests.filter(row => row.options.method === 'POST').length, 1);
});

test('background progress only GETs the original run and stops at terminal state', async () => {
  let progress = 0;
  const h = harness(path => path.includes('?') ? listing([run()])
    : run({state: ++progress === 1 ? 'running' : 'completed', stage: 'verification'}));
  await h.controller.select(definition);
  assert.equal(h.controller.snapshot.canStart, false, 'An active existing run blocks duplicate creation in a new tab');
  await h.advance(); await h.advance();
  assert.equal(h.controller.snapshot.runs[0].state, 'completed');
  assert.equal(h.timers.size, 0);
  assert.deepEqual(h.requests.map(row => row.path), ['api/creation-runs?experiment_id=motion',
    'api/creation-runs/run-one', 'api/creation-runs/run-one']);
  assert.ok(h.requests.every(row => !row.options.method));
});

test('failed polling preserves existing media and stops until an explicit refresh', async () => {
  const original = {id: 'original', status: 'rendered', technical_status: 'passed', media_url: null, source_url: null};
  const h = harness(path => {
    if (path.includes('?')) return listing([run({versions: [original]})]);
    throw Error('temporary unavailable');
  });
  await h.controller.select(definition); await h.advance();
  assert.match(h.controller.snapshot.error, /已停止轮询/);
  assert.deepEqual(h.controller.snapshot.runs[0].versions, [original]); assert.equal(h.timers.size, 0);
  await h.controller.refresh(); assert.equal(h.timers.size, 1);
  assert.equal(h.requests.some(row => row.options.method === 'POST'), false);
});

test('cancellation is single-shot, retains the request and cannot refund a new creation', async () => {
  const cancelled = deferred(); let made = false;
  const h = harness((path, options) => {
    if (path.endsWith('/cancel')) return cancelled.promise;
    if (options.method === 'POST') { made = true; return run(); }
    return listing(made ? [run()] : []);
  });
  await h.controller.select(definition); await h.controller.start();
  const pending = h.controller.cancel('run-one');
  assert.equal(await h.controller.cancel('run-one'), false);
  cancelled.resolve(run({state: 'cancelled'})); await pending;
  assert.equal(h.controller.snapshot.record.request_id, requestId);
  assert.equal(h.controller.snapshot.record.status, 'cancelled');
  assert.equal(h.controller.snapshot.canStart, false); assert.equal(h.timers.size, 0);
  const posts = h.requests.filter(row => row.path.endsWith('/cancel'));
  assert.equal(posts.length, 1); assert.equal(posts[0].options.body, '{}');
  assert.match(h.controller.snapshot.notice, /额度不会退回/);
});

test('refreshing during cancellation cannot lock the cancel button or apply a stale response', async () => {
  const cancelled = deferred();
  const h = harness(path => path.endsWith('/cancel') ? cancelled.promise : listing([run()]));
  await h.controller.select(definition); const pending = h.controller.cancel('run-one');
  await h.controller.refresh();
  assert.deepEqual(h.controller.snapshot.cancelling, ['run-one']);
  cancelled.resolve(run({state: 'cancelled'})); await pending;
  assert.deepEqual(h.controller.snapshot.cancelling, []);
  assert.equal(h.controller.snapshot.runs[0].state, 'queued', 'Late responses do not overwrite the refreshed view');
  assert.equal(h.requests.filter(row => row.path.endsWith('/cancel')).length, 1);
});

test('pausing a view cancels its polling and ignores a late detail response', async () => {
  const detail = deferred();
  const h = harness(path => path.includes('?') ? listing([run()]) : detail.promise);
  await h.controller.select(definition); const pending = h.controller.poll();
  const signal = h.requests.at(-1).options.signal;
  h.controller.pause(); assert.equal(signal.aborted, true); assert.equal(h.timers.size, 0);
  detail.resolve(run({state: 'completed'})); await pending;
  assert.equal(h.controller.snapshot.runs[0].state, 'queued');
});

test('a foreign or malformed detail response cannot be adopted as this run', async () => {
  for (const returned of [run({request_id: '00000000-0000-4000-8000-000000000002'}),
    run({experiment: {...definition, identity: 'other'}}), run({state: 'approved'}),
    run({versions: [{id: 'original'}]})]) {
    const h = harness(path => path.includes('?') ? listing([run()]) : returned);
    await h.controller.select(definition); await h.advance();
    assert.equal(h.controller.snapshot.runs[0].state, 'queued');
    assert.ok(h.controller.snapshot.error); assert.equal(h.timers.size, 0);
  }
});

test('asset links are tied to this base path, run and version without script or foreign navigation', () => {
  const path = '/dev/animations/api/creation-runs/run-one/versions/original/media';
  assert.equal(creationAssetUrl(path, root, 'run-one', 'original', 'media'), root.origin + path);
  for (const value of ['https://attacker.invalid' + path, 'javascript:alert(1)', '//attacker.invalid/a',
    path.replace('run-one', 'run-two'), path.replace('original', 'revised'), path + '?token=secret',
    path + '#x', '/oauth2/callback', path.replace('/dev/animations/', '/other/'), path + '\\x']) {
    assert.equal(creationAssetUrl(value, root, 'run-one', 'original', 'media'), null);
  }
});

class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.listeners = new Map(); this.value = ''; this.hidden = false;
    this.disabled = false; this.text = ''; this.classList = {toggle() {}}; this.pauses = 0;
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { throw Error('Agent output cannot be interpreted as markup'); }
  append(...elements) { for (const child of elements) { child.parent = this; this.children.push(child); } }
  replaceChildren(...elements) { this.text = ''; this.children = []; this.append(...elements); }
  addEventListener(type, fn) { this.listeners.set(type, fn); }
  setAttribute(key, value) { this[key] = value; }
  querySelectorAll(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.querySelectorAll(tag)]); }
  dispatch(type) { return this.listeners.get(type)?.({target: this}); }
  pause() { this.pauses++; }
  remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
}
const documentStub = () => {
  const elements = new Map();
  return {createElement: tag => new Element(tag), getElementById(id) {
    if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id);
  }};
};

test('versions only play rendered media and download source; source, model text and issues stay inert', () => {
  const document = documentStub(), attack = '<script>fetch("/api/private")</script>';
  const version = {id: 'original', status: 'rendered', technical_status: 'passed', sha256: attack,
    media_url: '/dev/animations/api/creation-runs/run-one/versions/original/media',
    source_url: '/dev/animations/api/creation-runs/run-one/versions/original/source'};
  const rendered = renderCreationVersion(document, version, run(), root);
  assert.equal(rendered.querySelectorAll('video').length, 1);
  assert.equal(rendered.querySelectorAll('iframe').length, 0); assert.equal(rendered.querySelectorAll('script').length, 0);
  assert.match(rendered.textContent, /技术检查通过 · 效果待评价/); assert.ok(rendered.textContent.includes(attack));
  const source = rendered.querySelectorAll('a').find(link => link.textContent.includes('源码'));
  assert.ok(source.download); assert.equal(source.target, undefined);
  const review = renderCreationReview(document, run({review: {summary: attack,
    issues: [{issue_id: 'one', start_seconds: 1.75, end_seconds: 2.25, problem: attack,
      change_target: '移动路径避开固定说明', frame_ids: ['frame-two']}], limitations: ['只观察抽样帧']}}));
  assert.ok(review.textContent.includes(attack)); assert.match(review.textContent, /1.750—2.250秒/);
  assert.match(review.textContent, /不代表问题已全部解决/); assert.match(review.textContent, /只观察抽样帧/);
  assert.equal(review.querySelectorAll('script').length, 0);
});

test('unchanged polling preserves the actual video element and media failures do not regenerate', async () => {
  const document = documentStub(), timers = new Map(); let counter = 0, posts = 0;
  const version = {id: 'original', status: 'rendered', technical_status: 'passed',
    media_url: '/dev/animations/api/creation-runs/run-one/versions/original/media', source_url: null};
  const value = run({state: 'running', stage: 'review', versions: [version]});
  const panel = createCreationRunsPanel({document, root, storage: storage(),
    schedule: fn => { timers.set(++counter, fn); return counter; }, unschedule: id => timers.delete(id),
    request: async (path, options = {}) => { if (options.method === 'POST') posts++; return path.includes('?') ? listing([value]) : value; }});
  await panel.select(definition);
  const list = document.getElementById('creation-runs-list'), video = list.querySelectorAll('video')[0];
  video.currentTime = 2; await panel.controller.poll();
  assert.equal(list.querySelectorAll('video')[0], video); assert.equal(video.currentTime, 2);
  video.dispatch('error'); assert.match(list.textContent, /媒体读取失败/); assert.equal(posts, 0);
  panel.pause(); assert.ok(video.pauses > 0); assert.equal(timers.size, 0);
});

test('the integrated request helper preserves lab CSRF and same-origin prefixed requests', async () => {
  const source = readFileSync(new URL('../../video_workbench/animation_lab_web/experiments.mjs', import.meta.url), 'utf8');
  const start = source.indexOf('async function request('), end = source.indexOf('\nfunction captureDraft(', start);
  let sent;
  const context = vm.createContext({URL, root, listing: {csrf_token: 'fixture-token'},
    fetch: async (url, options) => { sent = {url, options}; return {ok: true, json: async () => run()}; }});
  await vm.runInContext(`${source.slice(start, end)}\nrequest('api/creation-runs', {method: 'POST', body: '{}'})`, context);
  assert.equal(sent.url.href, root.href + 'api/creation-runs');
  assert.equal(sent.options.headers['X-Animation-Lab-Token'], 'fixture-token');
  assert.equal(sent.options.headers['X-Animation-Lab'], '1');
  assert.equal(sent.options.credentials, 'same-origin'); assert.equal(sent.options.cache, 'no-store');
});
