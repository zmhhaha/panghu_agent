import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildCommissionRequest, MethodGalleryController, mountMethodGallery, renderCollaborationProgress}
  from '../../video_workbench/animation_lab_web/method-gallery.mjs';

const root = new URL('https://video-service.panghuer.top/dev/animations/');
const requestId = '00000000-0000-4000-8000-000000000001';
const fields = {title: '触发与响应', goal: '将主对象、群体响应与背景变化组织成完整表达片段',
  duration_seconds: 18, max_rounds: 3, max_minutes: 15};
const storage = () => { const data = new Map(); return {data, getItem: key => data.get(key) ?? null,
  setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key)}; };

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.handlers = {}; this.attributes = {};
    this.dataset = {}; this.className = ''; this._text = ''; this.paused = true;
    this.classList = {add: value => { this.className += ' ' + value; }};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(row => row.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); for (const node of nodes) node.parentElement = this; }
  replaceChildren(...nodes) { this._text = ''; this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  removeEventListener(name, fn) { if (this.handlers[name] === fn) delete this.handlers[name]; }
  descendants(tag) { return this.children.flatMap(row => [row, ...row.descendants()])
    .filter(row => !tag || row.tagName === tag.toUpperCase()); }
  querySelectorAll(tag) { return this.descendants(tag); }
  contains(node) { return this === node || this.descendants().includes(node); }
  closest(tag) { return this.tagName === tag.toUpperCase() ? this : this.parentElement?.closest(tag) ?? null; }
  focus() { if (this.ownerDocument) this.ownerDocument.activeElement = this; }
  pause() { this.paused = true; }
}
const doc = {createElement: tag => new Element(tag), createDocumentFragment: () => new Element('fragment')};

test('ordinary creation remains unchanged; collaboration is explicit or inherits its original configuration', () => {
  assert.equal(Object.hasOwn(buildCommissionRequest(fields, requestId), 'collaboration'), false);
  const value = buildCommissionRequest({...fields, collaboration_enabled: 'on'}, requestId);
  assert.deepEqual(value.collaboration, {max_components: 3});
  assert.deepEqual(value.budget, {max_rounds: 1, max_seconds: 900});
  for (const extra of [{base_commission_id: 'base', feedback: '改善节奏'}, {retry_of_commission_id: 'failed'}]) {
    const inherited = buildCommissionRequest({...fields, ...extra, collaboration_inherited: true}, requestId);
    assert.equal(inherited.budget.max_rounds, 1);
    assert.equal(Object.hasOwn(inherited, 'collaboration'), false);
    assert.equal(Object.hasOwn(inherited, 'collaboration_inherited'), false);
  }
  assert.throws(() => buildCommissionRequest({...fields, transfer_commission_id: 'source',
    collaboration_enabled: true}, requestId), /不支持迁移/);
  assert.throws(() => buildCommissionRequest({...fields, collaboration_enabled: true,
    use_manim_resource: 'on', manim_resource_seconds: 2}, requestId), /不能同时选择 Manim/);
});

test('unadmitted collaboration stops before persistence or a paid submission, including a missing capability flag', async () => {
  for (const capability of [undefined, false]) {
    const requests = [], store = storage();
    const controller = new MethodGalleryController({root, storage: store, uuid: () => requestId,
      request: async (path, options = {}) => { requests.push({path, options});
        return {effects: [], commissions: [], availability: {enabled: true, collaboration: capability}};
      }, schedule: () => 1, unschedule() {}});
    await controller.refresh();
    assert.equal(await controller.start({...fields, collaboration_enabled: true}), false);
    assert.match(controller.snapshot.error, /尚未启用/);
    assert.equal(store.data.size, 0);
    assert.equal(requests.some(row => row.options.method), false);
    controller.close();
  }
});

test('admitted collaboration posts exactly once with the explicit configuration', async () => {
  const requests = [], store = storage();
  const detail = {id: 'collaboration-run', request_id: requestId, state: 'queued', candidates: []};
  const controller = new MethodGalleryController({root, storage: store, uuid: () => requestId,
    request: async (path, options = {}) => { requests.push({path, options});
      return options.method ? detail : {effects: [], commissions: [], availability: {enabled: true, collaboration: true}};
    }, schedule: () => 1, unschedule() {}});
  await controller.refresh();
  assert.equal(await controller.start({...fields, collaboration_enabled: true, max_minutes: 30}), true);
  const writes = requests.filter(row => row.options.method);
  assert.equal(writes.length, 1);
  const body = JSON.parse(writes[0].options.body);
  assert.deepEqual(body.collaboration, {max_components: 3});
  assert.deepEqual(body.budget, {max_rounds: 1, max_seconds: 1800});
  controller.close();
});

test('progress uses actual component and budget values without inventing missing counters or controls', () => {
  assert.equal(renderCollaborationProgress(doc, {}), null);
  assert.equal(renderCollaborationProgress(doc, {collaboration: {enabled: false}}), null);
  const progress = renderCollaborationProgress(doc, {collaboration: {enabled: true, stage: 'component',
    completed: 1, total: 2, author_calls_used: 3, author_calls_max: 5,
    author_calls_scope: 'reserved_creator_operations_not_provider_model_requests',
    components: [{id: 'main', title: '<script>主对象</script>', status: 'completed'},
      {id: 'field', title: '背景响应', status: 'running'}]}, usage: {render: 0, review: 0}, budget: {render: 1, review: 1}});
  assert.match(progress.textContent, /部件创作/);
  assert.match(progress.textContent, /1 \/ 2/);
  assert.match(progress.textContent, /已占用创作任务额度：3 \/ 5/);
  assert.match(progress.textContent, /不代表实际模型请求次数/);
  assert.match(progress.textContent, /样片渲染：0 \/ 1/);
  assert.equal(progress.descendants('li')[0].dataset.componentId, 'main');
  assert.equal(progress.descendants('script').length, 0);
  assert.equal(progress.descendants('button').length, 0);
  assert.equal(progress.descendants('video').length, 0);
  const partial = renderCollaborationProgress(doc, {collaboration: {enabled: true, stage: 'future-stage'}});
  assert.match(partial.textContent, /等待核对/);
  assert.doesNotMatch(partial.textContent, /0 \/|已占用创作任务额度：|已完成部件：/);
});

function mounted(capability) {
  const html = readFileSync(new URL('../../video_workbench/animation_lab_web/method-gallery.html', import.meta.url), 'utf8');
  const nodes = new Map([...html.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"/g)]
    .map(([, tag, id]) => [id, new Element(tag)]));
  const document = {activeElement: null, getElementById: id => nodes.get(id),
    createElement(tag) { const value = new Element(tag); value.ownerDocument = this; return value; },
    createDocumentFragment: () => new Element('fragment')};
  const get = id => nodes.get(id);
  const budgetSection = new Element('details'); budgetSection.append(get('experiment-rounds'));
  get('gallery-search').value = ''; get('gallery-state').value = get('gallery-engine').value = 'all';
  get('gallery-form-panel').hidden = get('gallery-detail').hidden = true;
  get('experiment-form').reset = () => {
    for (const node of nodes.values()) node.value = '';
    get('experiment-duration').value = '24'; get('experiment-rounds').value = '3'; get('experiment-seconds').value = '15';
  };
  const controller = mountMethodGallery({document, root, storage: storage(), uuid: () => requestId,
    schedule: () => 1, unschedule() {}, fetch: async () => ({ok: true, status: 200, json: async () => ({
      effects: [], commissions: [], csrf_token: 'test', availability: {enabled: true, collaboration: capability}})})});
  return {controller, get, ready: () => new Promise(resolve => setImmediate(resolve))};
}

test('new-form opt-in explains its budget, restores ordinary values and resets on another new experiment', async () => {
  const h = mounted(true); await h.ready();
  h.get('gallery-new').handlers.click();
  const checkbox = h.get('experiment-collaboration');
  assert.equal(checkbox.disabled, false); assert.equal(checkbox.checked, false);
  checkbox.checked = true; checkbox.handlers.change();
  assert.equal(h.get('experiment-rounds').value, '1');
  assert.equal(h.get('experiment-rounds').readOnly, true);
  assert.equal(h.get('experiment-seconds').value, '30');
  checkbox.checked = false; checkbox.handlers.change();
  assert.equal(h.get('experiment-rounds').value, '3');
  assert.equal(h.get('experiment-rounds').readOnly, false);
  assert.equal(h.get('experiment-seconds').value, '15');
  checkbox.checked = true; checkbox.handlers.change();
  h.get('experiment-form-close').handlers.click(); h.get('gallery-new').handlers.click();
  assert.equal(checkbox.checked, false); assert.equal(h.get('experiment-rounds').value, '3');
  assert.equal(h.get('experiment-seconds').value, '15');
  assert.equal(h.get('experiment-collaboration-unavailable').hidden, true);
  h.controller.close();
});

test('new-form collaboration stays disabled until explicitly admitted, while ordinary submit remains available', async () => {
  const h = mounted(undefined); await h.ready(); h.get('gallery-new').handlers.click();
  assert.equal(h.get('experiment-collaboration').disabled, true);
  assert.equal(h.get('experiment-collaboration-unavailable').hidden, false);
  assert.equal(h.get('experiment-submit').disabled, false);
  h.controller.close();
});

test('continuing or retrying a collaborative work keeps one round and the inherited budget without a second opt-in', async () => {
  for (const [state, action] of [['completed', '继续优化这版'], ['failed', '重新运行']]) {
    const h = mounted(true); await h.ready();
    h.controller.state.detail = {id: 'original', request_id: requestId, title: fields.title, goal: fields.goal, state,
      candidates: [{id: 'candidate', title: '完整样片'}], feedback: [], methods: [],
      collaboration: {enabled: true, stage: state, max_components: 4}, budget: {seconds: 1800}};
    h.controller.emit();
    const button = h.get('gallery-detail').descendants('button').find(row => row.textContent === action);
    assert.ok(button, action); button.handlers.click();
    assert.equal(h.get('experiment-collaboration-options').hidden, true);
    assert.equal(h.get('experiment-collaboration').checked, false);
    assert.equal(h.get('experiment-rounds').value, '1');
    assert.equal(h.get('experiment-rounds').readOnly, true);
    assert.equal(h.get('experiment-seconds').value, '30');
    assert.match(h.get('experiment-base-note').textContent, /延续原协作分工与部件上限/);
    h.controller.close();
  }
});
