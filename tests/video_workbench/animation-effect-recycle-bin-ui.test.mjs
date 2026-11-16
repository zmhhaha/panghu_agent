import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {EffectRecycleBinController, removalTarget, removalTimeLabel, renderRemovalActions, mountEffectRecycleBin}
  from '../../video_workbench/animation_lab_web/effect-recycle-bin.mjs';
import {mountMethodGallery} from '../../video_workbench/animation_lab_web/method-gallery.mjs';

const root = new URL('https://video-service.panghuer.top/dev/animations/');
const requestId = '00000000-0000-4000-8000-000000000001', snapshot = 'a'.repeat(64);
const policy = changes => ({allowed: true, reason_codes: [], snapshot_sha256: snapshot, member_count: 2,
  requires_stop_check: true, ...changes});
const detail = changes => ({id: 'experiment-1', title: '本次筛选实验', state: 'completed', candidates: [],
  deletion: policy({member_count: 1}), effect: {id: 'effect-1', title: '群体筛选', deletion: policy(),
    commissions: [{id: 'experiment-1', state: 'completed'}, {id: 'experiment-2', state: 'completed'}]}, ...changes});
const entry = changes => ({id: 'trash-1', scope: 'commission', target_id: 'experiment-1', title: '本次筛选实验',
  member_ids: ['experiment-1'], status: 'trashed', request_id: requestId, deleted_at: '2026-11-15T11:30:00Z',
  restore: {allowed: true, reason_codes: []}, ...changes});
const trash = items => ({schema: 'video.animation-trash.v1', items});
const receipt = (action = 'trash', changes = {}) => ({schema: 'video.animation-trash-operation.v1', action,
  entry: entry({...changes, status: action === 'restore' ? 'restored' : 'trashed'})});
function storage() { const values = new Map(); return {values, getItem: key => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)}; }
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; }
function harness(handler = () => trash([]), changes = {}) {
  const calls = [], store = storage(), events = [];
  const controller = new EffectRecycleBinController({storage: store, storageKey: 'test-recycle', uuid: () => requestId,
    request: async (path, options = {}) => { calls.push({path, options}); return handler(path, options); },
    afterTrash: value => events.push(['removed', value.target_id]), refreshLibrary: async () => events.push(['refresh']), ...changes});
  return {controller, calls, store, events};
}

test('eligibility fails closed and distinguishes the exact experiment from its entire effect', () => {
  assert.equal(removalTarget(detail(), 'commission').target_id, 'experiment-1');
  assert.equal(removalTarget(detail(), 'effect').target_id, 'effect-1');
  assert.equal(removalTarget(detail(), 'effect').member_count, 2);
  for (const changes of [{deletion: null}, {deletion: policy({allowed: false})},
    {deletion: policy({snapshot_sha256: 'invalid'})}, {deletion: policy({reason_codes: ['published']})},
    {state: 'running'}, {state: 'submission_unknown'}, {ui_preview: true}]) {
    assert.equal(removalTarget(detail(changes), 'commission').allowed, false);
  }
  const runningSibling = detail(); runningSibling.effect.commissions[1].state = 'rendering';
  assert.equal(removalTarget(runningSibling, 'effect').allowed, false);
  assert.equal(removalTarget(runningSibling, 'commission').allowed, true);
});

test('visiting the recycle bin is read only and never cancels or creates work', async () => {
  const h = harness(() => trash([entry()]));
  h.controller.setScope('trash'); await h.controller.refresh();
  assert.equal(h.controller.state.items.length, 1);
  assert.deepEqual(h.calls, [{path: 'api/method-lab/trash', options: {}}]);
  assert.equal(h.store.values.size, 0);
});

test('server epoch timestamps, ISO historical timestamps and protection reasons are readable', () => {
  assert.match(removalTimeLabel(1794742200.125), /2026/);
  assert.match(removalTimeLabel('2026-11-15T11:30:00Z'), /2026/);
  assert.equal(removalTimeLabel(null), '未记录');
  assert.equal(removalTimeLabel(Infinity), '未记录');
  assert.match(removalTarget(detail({deletion: policy({allowed: false, reason_codes: ['protected']})}), 'commission').reason, /正式制作引用/);
});

test('confirmation captures the exact snapshot and double click sends only one deletion request', async () => {
  const post = deferred();
  const h = harness((path, options) => options.method ? post.promise : trash([entry()]));
  assert.equal(h.controller.prepare(detail(), 'commission'), true);
  assert.equal(h.calls.length, 0);
  const first = h.controller.confirm();
  assert.equal(await h.controller.confirm(), false);
  assert.equal(h.calls.length, 1);
  const call = h.calls[0];
  assert.equal(call.path, 'api/method-lab/commissions/experiment-1/trash');
  assert.deepEqual(JSON.parse(call.options.body), {request_id: requestId, expected_snapshot_sha256: snapshot});
  assert.equal(JSON.parse(h.store.getItem('test-recycle')).request_id, requestId);
  post.resolve(receipt()); assert.equal(await first, true);
  assert.deepEqual(h.events, [['removed', 'experiment-1'], ['refresh']]);
  assert.equal(h.store.values.size, 0);
  assert.equal(h.calls.some(row => /cancel|commissions$/.test(row.path)), false);
});

test('removing an entire effect uses its own identity and snapshot, not the selected experiment', async () => {
  const h = harness((path, options) => options.method
    ? receipt('trash', {scope: 'effect', target_id: 'effect-1', member_ids: ['experiment-1', 'experiment-2']}) : trash([]));
  const run = detail(); run.effect.deletion.snapshot_sha256 = 'b'.repeat(64);
  h.controller.prepare(run, 'effect'); await h.controller.confirm();
  const call = h.calls.find(row => row.options.method);
  assert.equal(call.path, 'api/method-lab/effects/effect-1/trash');
  assert.equal(JSON.parse(call.options.body).expected_snapshot_sha256, 'b'.repeat(64));
});

test('active, protected and unverified targets never submit a deletion or cancellation', async () => {
  const h = harness();
  for (const run of [detail({state: 'running'}), detail({state: 'submission_unknown'}),
    detail({deletion: policy({allowed: false, reason_codes: ['referenced']})}), detail({deletion: null})]) {
    assert.equal(h.controller.prepare(run, 'commission'), false);
    assert.equal(await h.controller.confirm(), false);
  }
  assert.equal(h.calls.length, 0);
});

test('generation submission and unpersistable requests block recycle mutations', async () => {
  const busy = harness(() => trash([]), {externalBusy: () => true});
  assert.equal(busy.controller.prepare(detail(), 'commission'), false);
  assert.equal(await busy.controller.restore(entry()), false);
  assert.equal(busy.calls.length, 0);
  const unsupported = harness(() => { throw Error('unexpected network'); }, {storage: null});
  unsupported.controller.prepare(detail(), 'commission');
  assert.equal(await unsupported.controller.confirm(), false);
  assert.match(unsupported.controller.state.error, /保存操作编号/);
  assert.equal(unsupported.calls.length, 0);
});

test('an uncertain deletion is persisted, reconciled only by matching request and never automatically repeated', async () => {
  let found = false;
  const h = harness((path, options) => {
    if (options.method) throw Error('connection lost');
    return trash([entry({request_id: found ? requestId : 'unrelated-request'})]);
  });
  h.controller.prepare(detail(), 'commission'); assert.equal(await h.controller.confirm(), false);
  assert.equal(h.controller.state.scope, 'trash'); assert.equal(h.controller.locked, true);
  await h.controller.refresh(); assert.equal(h.controller.locked, true);
  found = true; await h.controller.refresh(); assert.equal(h.controller.locked, false);
  assert.equal(h.calls.filter(row => row.options.method).length, 1);
  assert.equal(h.store.values.size, 0);
  assert.deepEqual(h.events.slice(-2), [['removed', 'experiment-1'], ['refresh']]);
});

test('explicit retry reuses the exact saved request id, snapshot and body after reload', async () => {
  const store = storage();
  const h = harness((path, options) => { if (options.method) throw Error('not delivered'); return trash([]); }, {storage: store});
  h.controller.prepare(detail(), 'commission'); await h.controller.confirm();
  const original = h.calls[0];
  const reloaded = harness((path, options) => options.method ? receipt() : trash([entry()]),
    {storage: store, uuid: () => { throw Error('must not create another request'); }});
  assert.equal(reloaded.controller.locked, true);
  await reloaded.controller.refresh();
  // The read above finds the exact request in this fixture. Recreate the pending
  // state to exercise an explicitly retried request that was never delivered.
  store.setItem('test-recycle', JSON.stringify({...JSON.parse(original.options.body), action: 'trash',
    scope: 'commission', target_id: 'experiment-1'}));
  const retry = harness((path, options) => options.method ? receipt() : trash([]),
    {storage: store, uuid: () => { throw Error('must not create another request'); }});
  assert.equal(await retry.controller.retryPending(), true);
  const repeated = retry.calls.find(row => row.options.method);
  assert.deepEqual(repeated, original);
  assert.equal(retry.store.values.size, 0);
});

test('definite server refusal explains stop verification and clears pending without removing anything', async () => {
  const error = Object.assign(Error('HTTP 409'), {status: 409, code: 'animation_method_trash_stop_unproven'});
  const h = harness(() => { throw error; });
  h.controller.prepare(detail(), 'effect'); assert.equal(await h.controller.confirm(), false);
  assert.equal(h.controller.locked, false); assert.match(h.controller.state.error, /任务已经停止/);
  assert.equal(h.store.values.size, 0); assert.deepEqual(h.events, []);
});

test('restore is an explicit scoped operation and protected restore never posts', async () => {
  const h = harness((path, options) => options.method ? receipt('restore') : trash([]));
  assert.equal(await h.controller.restore(entry({restore: {allowed: false, reason_codes: ['referenced']}})), false);
  assert.equal(h.calls.length, 0);
  assert.equal(await h.controller.restore(entry()), true);
  assert.equal(h.calls[0].path, 'api/method-lab/trash/trash-1/restore');
  assert.deepEqual(JSON.parse(h.calls[0].options.body), {request_id: requestId});
  assert.equal(h.calls.some(row => row.path.includes('cancel')), false);
});

test('uncertain restore requires both disappearance from trash and a visible restored target', async () => {
  let visible = false;
  const h = harness((path, options) => { if (options.method) throw Error('lost response'); return trash([]); },
    {getCatalog: () => ({commissions: visible ? [{id: 'experiment-1'}] : [], effects: []})});
  await h.controller.restore(entry()); await h.controller.refresh(); assert.equal(h.controller.locked, true);
  visible = true; await h.controller.refresh(); assert.equal(h.controller.locked, false);
  assert.equal(h.calls.filter(row => row.options.method).length, 1);
});

class Node {
  constructor(tag, ownerDocument) { Object.assign(this, {tagName: tag.toUpperCase(), ownerDocument,
    children: [], attributes: {}, dataset: {}, handlers: {}, className: '', _text: '', paused: true,
    classList: {add: value => { this.className += ' ' + value; }}}); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(row => row.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); nodes.forEach(node => { node.parentElement = this; }); }
  replaceChildren(...nodes) { this._text = ''; this.children = []; this.append(...nodes); }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(key, handler) { this.handlers[key] = handler; }
  removeEventListener(key) { delete this.handlers[key]; }
  descendants(tag) { return this.children.flatMap(row => [row, ...row.descendants()])
    .filter(row => !tag || row.tagName === tag.toUpperCase()); }
  querySelectorAll(tag) { return this.descendants(tag); }
  contains(node) { return this === node || this.descendants().includes(node); }
  closest(tag) { return this.tagName === tag.toUpperCase() ? this : this.parentElement?.closest(tag) ?? null; }
  focus() { this.ownerDocument.activeElement = this; }
  pause() { this.paused = true; }
  load() {}
}
function documentFixture() {
  const html = readFileSync(new URL('../../video_workbench/animation_lab_web/method-gallery.html', import.meta.url), 'utf8');
  const nodes = new Map(), doc = {activeElement: null, createElement: tag => new Node(tag, doc),
    createDocumentFragment: () => new Node('fragment', doc), getElementById: id => nodes.get(id)};
  for (const [, tag, id] of html.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"/g)) nodes.set(id, new Node(tag, doc));
  return {doc, get: id => nodes.get(id)};
}

test('removal controls are separate from cancellation and protected items explain why', () => {
  const {doc} = documentFixture(); const selected = [];
  const content = renderRemovalActions(doc, detail(), (...args) => selected.push(args));
  const buttons = content.descendants('button');
  assert.deepEqual(buttons.map(row => row.textContent), ['移除本次实验', '移除整个效果']);
  buttons[0].handlers.click(); assert.equal(selected[0][1], 'commission');
  const protectedContent = renderRemovalActions(doc, detail({deletion: policy({allowed: false, reason_codes: ['published']})}), () => {});
  assert.equal(protectedContent.descendants('button')[0].disabled, true);
  assert.match(protectedContent.textContent, /已发布/);
});

test('scope navigation, explicit confirmation and restore expose different targets without a POST on opening', async () => {
  const {doc, get} = documentFixture(), calls = [];
  const controller = mountEffectRecycleBin({document: doc, root, storage: storage(), uuid: () => requestId,
    request: async (path, options = {}) => { calls.push({path, options}); return trash([entry()]); }});
  assert.equal(get('gallery-library').hidden, false);
  get('gallery-scope-trash').handlers.click(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(get('gallery-library').hidden, true); assert.equal(get('gallery-trash').hidden, false);
  assert.match(get('gallery-trash').textContent, /恢复到效果库/);
  assert.equal(calls.some(row => row.options.method), false);
  controller.setScope('library'); controller.prepare(detail(), 'effect');
  const confirmation = get('gallery-removal-dialog');
  assert.equal(confirmation.hidden, false); assert.match(confirmation.textContent, /移除整个效果/);
  assert.match(confirmation.textContent, /群体筛选/); assert.match(confirmation.textContent, /2 条实验/);
  assert.match(confirmation.textContent, /之前单独移除的实验保持原状/);
  assert.match(confirmation.textContent, /确认时会核实任务已经停止/);
  confirmation.descendants('button')[0].handlers.click(); assert.equal(confirmation.hidden, true);
  assert.equal(calls.some(row => row.options.method), false);
  controller.close();
});

test('mounted gallery sends CSRF, reselects remaining representative and never fetches deleted detail after removal', async () => {
  const {doc, get} = documentFixture(), calls = [], store = storage(); let removed = false;
  get('gallery-search').value = ''; get('gallery-state').value = get('gallery-engine').value = 'all';
  get('gallery-form-panel').hidden = get('gallery-detail').hidden = true;
  get('experiment-form').reset = () => {};
  const run = detail(), sibling = detail({id: 'experiment-2', title: '保留的实验'});
  const controller = mountMethodGallery({document: doc, root, storage: store, uuid: () => requestId,
    schedule: () => 1, unschedule() {}, fetch: async (url, options = {}) => {
      calls.push({url: url.pathname, options}); let result;
      if (url.pathname.endsWith('/catalog')) result = {availability: {enabled: true}, csrf_token: 'test-csrf',
        effects: [{id: 'effect-1', title: '群体筛选', state: 'completed', open_commission_id: removed ? 'experiment-2' : 'experiment-1'}],
        commissions: removed ? [sibling] : [run, sibling]};
      else if (url.pathname.endsWith('/trash') && options.method) { removed = true; result = receipt(); }
      else if (url.pathname.endsWith('/trash')) result = trash(removed ? [entry()] : []);
      else result = url.pathname.endsWith('/experiment-2') ? sibling : run;
      return {ok: true, status: 200, json: async () => structuredClone(result)};
    }});
  await new Promise(resolve => setImmediate(resolve)); await controller.select('experiment-1');
  assert.equal(controller.recycleBin.prepare(controller.state.detail, 'commission'), true);
  assert.equal(await controller.recycleBin.confirm(), true);
  assert.equal(controller.state.detail.id, 'experiment-2');
  const mutation = calls.find(row => row.options.method);
  assert.equal(mutation.options.headers['X-Animation-Lab-Token'], 'test-csrf');
  assert.equal(mutation.options.headers['X-CSRF-Token'], 'test-csrf');
  assert.equal(mutation.options.credentials, 'same-origin');
  const later = calls.slice(calls.indexOf(mutation) + 1);
  assert.equal(later.some(row => row.url.endsWith('/commissions/experiment-1')), false);
  assert.equal(calls.filter(row => row.options.method).length, 1);
  assert.equal(calls.some(row => row.url.endsWith('/cancel')), false);
  controller.close();
});
