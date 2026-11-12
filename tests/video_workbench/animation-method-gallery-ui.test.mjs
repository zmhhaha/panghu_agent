import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {
  MethodGalleryController, mountMethodGallery, buildCommissionRequest, buildTransferRequest, methodAssetUrl, methodStateLabel, layoutPreviewDetail, continuationFeedback, continuationDuration,
  publicationSelection, publicationApprovalBinding, methodsPublished, canPublish, renderMethodMedia, renderMethodCard, renderMethodDetail,
} from '../../video_workbench/animation_lab_web/method-gallery.mjs';

const root = new URL('https://video-service.panghuer.top/dev/animations/');
const firstId = '00000000-0000-4000-8000-000000000001';
const secondId = '00000000-0000-4000-8000-000000000002';
const fields = {title: '群体筛选', goal: '理解筛选如何改变统计值', duration_seconds: 24, max_rounds: 3, max_minutes: 15};
const candidate = (id = 'clip-1') => ({id, title: '完整筛选过程', technical_status: 'passed',
  media_url: 'api/method-lab/commissions/commission-1/candidates/' + id + '/media',
  demonstration_id: 'demonstration-' + id});
const run = (changes = {}) => ({id: 'commission-1', request_id: firstId, title: fields.title, goal: fields.goal,
  state: 'queued', candidates: [], criteria: [], methods: [], transfers: [], ...changes});
const catalog = (commissions = [], changes = {}) => ({effects: [], commissions, csrf_token: 'csrf-test',
  availability: {enabled: true}, ...changes});
function storage() {
  const data = new Map();
  return {data, getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value),
    removeItem: key => data.delete(key)};
}
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; }
function harness(handler, overrides = {}) {
  const requests = [], timers = new Map(), store = overrides.storage ?? storage(); let next = 0;
  const controller = new MethodGalleryController({root, storage: store, uuid: () => firstId,
    request: async (path, options = {}) => { requests.push({path, options}); return handler(path, options); },
    schedule: fn => { const id = ++next; timers.set(id, fn); return id; }, unschedule: id => timers.delete(id),
    ...overrides});
  return {controller, requests, timers, store};
}

test('opening the gallery only reads and a disabled service retains its actual availability', async () => {
  const h = harness(() => catalog([], {availability: {enabled: false, reason: '尚未接通制作执行器'}}));
  await h.controller.refresh();
  assert.equal(h.controller.snapshot.catalog.availability.reason, '尚未接通制作执行器');
  assert.equal(await h.controller.start(fields), false);
  assert.deepEqual(h.requests.map(row => row.options.method ?? 'GET'), ['GET']);
});

test('layout preview carries no generated media, accepted evidence or publishable methods', () => {
  const detail = layoutPreviewDetail();
  assert.equal(detail.ui_preview, true);
  assert.equal(canPublish(detail), false);
  assert.equal(detail.candidates.some(row => row.media_url || row.demonstration_id), false);
  assert.deepEqual(publicationSelection(detail), {method_version_ids: [], demonstration_ids: []});
  assert.match(detail.goal, /不是已生成/);
});

test('commission input preserves reference ranges, independent budget and exact continuation candidate', () => {
  const value = buildCommissionRequest({...fields, reference_url: 'https://www.bilibili.com/video/example/',
    reference_start: '12.5', reference_end: '38', base_commission_id: 'older', base_candidate_id: 'original-clip',
    feedback: '筛选后数值变化太突然', criteria_text: '对象移动时仍可追踪\n筛选与统计变化的因果可见'}, firstId);
  assert.equal(value.duration_seconds, 24);
  assert.deepEqual(value.budget, {max_rounds: 3, max_seconds: 900});
  assert.deepEqual(value.reference, {url: 'https://www.bilibili.com/video/example/', start_seconds: 12.5, end_seconds: 38});
  assert.equal(value.base_commission_id, 'older'); assert.equal(value.base_candidate_id, 'original-clip');
  assert.deepEqual(value.criteria.map(row => row.description), ['对象移动时仍可追踪', '筛选与统计变化的因果可见']);
  assert.throws(() => buildCommissionRequest({...fields, base_commission_id: 'older'}, firstId), /修改意见/);
  assert.throws(() => buildCommissionRequest({...fields, max_rounds: 0}, firstId), /轮数/);
  assert.throws(() => buildCommissionRequest({...fields, reference_url: 'javascript:alert(1)'}, firstId), /HTTP/);
  assert.throws(() => buildCommissionRequest({...fields, reference_url: 'https://example.com', reference_start: 2}, firstId), /同时/);
});

test('retry input keeps its origin without requiring feedback or changing it into continuation or transfer', () => {
  const value = buildCommissionRequest({...fields, retry_of_commission_id: 'failed-run',
    base_commission_id: 'unrelated-base', base_candidate_id: 'unrelated-clip', feedback: ''}, firstId);
  assert.equal(value.retry_of_commission_id, 'failed-run');
  assert.equal(Object.hasOwn(value, 'base_commission_id'), false);
  assert.equal(Object.hasOwn(value, 'base_candidate_id'), false);
  assert.equal(Object.hasOwn(value, 'transfer_commission_id'), false);
  assert.equal(value.feedback, '');
  assert.throws(() => buildTransferRequest({...fields, retry_of_commission_id: 'failed-run'}, firstId), /不能同时/);
});

test('only same-origin controlled method-lab media can be embedded', () => {
  assert.equal(methodAssetUrl(candidate().media_url, root), new URL(candidate().media_url, root).href);
  assert.equal(methodAssetUrl('/api/method-lab/commissions/c/candidates/v/media', root),
    'https://video-service.panghuer.top/dev/animations/api/method-lab/commissions/c/candidates/v/media');
  for (const value of ['https://evil.example/api/method-lab/media', 'javascript:alert(1)', 'data:video/mp4;base64,AA',
    '/oauth2/sign_out', 'api/method-lab/../private', 'api/method-lab/', 'api/method-lab/a%2fb/media',
    '/api/method-lab/media?token=unexpected', '/api/method-lab/media#fragment',
    'https://user:secret@video-service.panghuer.top/dev/animations/api/method-lab/media']) {
    assert.equal(methodAssetUrl(value, root), null, value);
  }
});

test('service limits and criterion evidence requirements survive continuation edits', () => {
  const value = buildCommissionRequest({...fields, duration_seconds: 60, max_rounds: 8, max_minutes: 60,
    criteria_text: '修改后的动态标准\n音效与动作对应',
    criteria_metadata: [{id: 'track', description: '旧标准', requires_motion: true, requires_audio: false,
      observation: '观察对象移动全过程'}, {id: 'sound', description: '旧音效标准', requires_audio: true}]}, firstId);
  assert.equal(value.duration_seconds, 60); assert.deepEqual(value.budget, {max_rounds: 8, max_seconds: 3600});
  assert.deepEqual(value.criteria[0], {id: 'track', description: '修改后的动态标准', requires_motion: true,
    requires_audio: false, observation: '观察对象移动全过程'});
  assert.equal(value.criteria[1].requires_audio, true);
  assert.throws(() => buildCommissionRequest({...fields, duration_seconds: 61}, firstId), /时长/);
  assert.throws(() => buildCommissionRequest({...fields, max_rounds: 9}, firstId), /轮数/);
  assert.throws(() => buildCommissionRequest({...fields, max_minutes: 1.5}, firstId), /时间/);
  assert.throws(() => buildCommissionRequest({...fields, criteria_text: Array(17).fill('观察过程').join('\n')}, firstId), /16/);
  assert.equal(buildCommissionRequest({...fields, criteria_text: '因果过程可追踪'}, firstId).criteria[0].requires_motion, true);
});

test('a transfer creates a materially different task from the selected sample and uses the transfer route', async () => {
  const input = {...fields, transfer_commission_id: 'original-commission', base_candidate_id: 'exact-best',
    transfer_dimension: 'object_count', transfer_before: '8', transfer_after: '40',
    goal: '对象数量增加后仍能追踪筛选和汇总的关联'};
  const body = buildTransferRequest(input, firstId);
  assert.equal(body.base_candidate_id, 'exact-best'); assert.equal(body.base_commission_id, undefined);
  assert.equal(buildTransferRequest({...input, criteria_text: '不能用当前委托的新标准替换方法原标准'}, firstId).criteria, undefined);
  assert.deepEqual(body.structural_changes, {object_count: {before: 8, after: 40}});
  assert.throws(() => buildTransferRequest({...input, transfer_after: '8'}, firstId), /变化/);
  assert.throws(() => buildTransferRequest({...input, transfer_before: ''}, firstId), /真正改变/);
  const h = harness((path, options) => options.method ? run({id: 'transfer-new'}) : catalog());
  await h.controller.refresh(); assert.equal(await h.controller.start(input), true);
  const post = h.requests.find(row => row.options.method);
  assert.equal(post.path, 'api/method-lab/commissions/original-commission/transfer');
  assert.deepEqual(JSON.parse(post.options.body), body);
  h.controller.close();
});

test('double click saves the original request before the only submission', async () => {
  const post = deferred(); let body;
  const h = harness((path, options) => {
    if (options.method === 'POST') {
      body = JSON.parse(options.body);
      assert.equal(JSON.parse([...h.store.data.values()][0]).request_id, body.request_id);
      return post.promise;
    }
    return catalog();
  });
  await h.controller.refresh(); const started = h.controller.start(fields);
  assert.equal(await h.controller.start(fields), false);
  post.resolve(run()); assert.equal(await started, true);
  assert.equal(h.requests.filter(row => row.options.method === 'POST').length, 1);
  assert.equal(h.controller.snapshot.pending, null); assert.equal(h.store.data.size, 0);
  h.controller.close();
});

test('a lost creation response is recovered by matching the saved request using GET, never another POST', async () => {
  const store = storage(); let admitted = false, posts = 0;
  const handler = (path, options) => {
    if (options.method === 'POST') { posts++; admitted = true; throw Error('response lost'); }
    return path.endsWith('/catalog') ? catalog(admitted ? [run()] : []) : run();
  };
  const first = harness(handler, {storage: store}); await first.controller.refresh();
  assert.equal(await first.controller.start(fields), false); assert.ok(first.controller.snapshot.pending);
  assert.equal(await first.controller.start(fields), false); first.controller.close();
  const reopened = harness(handler, {storage: store, uuid: () => assert.fail('GET recovery must not allocate a request')});
  await reopened.controller.refresh();
  assert.equal(reopened.controller.snapshot.detail.id, 'commission-1');
  assert.equal(reopened.controller.snapshot.pending, null); assert.equal(posts, 1);
  assert.ok(reopened.requests.every(row => !row.options.method)); reopened.controller.close();
});

test('retry double submission and a lost response recover the same request without spending a second budget', async () => {
  const store = storage(), response = deferred(), input = {...fields, retry_of_commission_id: 'failed-run', max_rounds: 1};
  const retried = run({id: 'retry-run', retry_of_commission_id: 'failed-run'}); let admitted = false;
  const handler = (path, options) => {
    if (options.method === 'POST') {
      admitted = true;
      assert.equal(path, 'api/method-lab/commissions');
      assert.equal(JSON.parse(options.body).retry_of_commission_id, 'failed-run');
      assert.equal(JSON.parse([...store.data.values()][0]).request_id, firstId);
      return response.promise;
    }
    return path.endsWith('/catalog') ? catalog(admitted ? [retried] : []) : retried;
  };
  const first = harness(handler, {storage: store}); await first.controller.refresh();
  const started = first.controller.start(input);
  assert.equal(await first.controller.start(input), false);
  response.reject(Error('response lost')); assert.equal(await started, false);
  assert.equal(first.requests.filter(row => row.options.method === 'POST').length, 1);
  assert.equal(first.controller.snapshot.pending.request_id, firstId); first.controller.close();
  const reopened = harness(handler, {storage: store, uuid: () => assert.fail('recovery cannot create another retry')});
  await reopened.controller.refresh();
  assert.equal(reopened.controller.snapshot.detail.id, 'retry-run');
  assert.equal(reopened.controller.snapshot.pending, null);
  assert.ok(reopened.requests.every(row => !row.options.method)); reopened.controller.close();
});

test('missing acknowledgement blocks a replacement request and does not poll forever', async () => {
  const h = harness((path, options) => { if (options.method) throw Error('unknown'); return catalog(); });
  await h.controller.refresh(); await h.controller.start(fields); await h.controller.refresh();
  assert.ok(h.controller.snapshot.pending); assert.match(h.controller.snapshot.notice, /尚待核对/);
  assert.equal(await h.controller.start(fields), false); assert.equal(h.timers.size, 0);
  assert.equal(h.requests.filter(row => row.options.method === 'POST').length, 1);
});

test('a completed commission can be explicitly continued with a new budget without any lifetime round gate', async () => {
  let uuidIndex = 0, posts = 0;
  const h = harness((path, options) => {
    if (options.method) { const body = JSON.parse(options.body); posts++; return run({id: 'new-' + posts, request_id: body.request_id, state: 'completed'}); }
    return catalog();
  }, {uuid: () => [firstId, secondId][uuidIndex++]});
  await h.controller.refresh(); assert.equal(await h.controller.start(fields), true);
  assert.equal(await h.controller.start({...fields, base_commission_id: 'new-1', base_candidate_id: 'clip',
    feedback: '继续改善对象消失与统计的关联'}), true);
  assert.equal(posts, 2);
  const secondBody = JSON.parse(h.requests.filter(row => row.options.method)[1].options.body);
  assert.equal(secondBody.request_id, secondId); assert.equal(secondBody.base_commission_id, 'new-1');
});

test('late detail reads cannot replace the currently selected effect', async () => {
  const slow = deferred();
  const h = harness(path => path.endsWith('/old') ? slow.promise : run({id: 'new'}));
  const pending = h.controller.select('old'); await h.controller.select('new');
  slow.resolve(run({id: 'old'})); await pending;
  assert.equal(h.controller.snapshot.detail.id, 'new'); h.controller.close();
});

test('unwritable persistence prevents creation before any network mutation', async () => {
  const h = harness(() => catalog(), {storage: {getItem: () => null, setItem() { throw Error('quota'); }}});
  await h.controller.refresh(); assert.equal(await h.controller.start(fields), false);
  assert.ok(h.controller.snapshot.error);
  assert.equal(h.requests.some(row => row.options.method === 'POST'), false);
});

test('technical success never supplies publication permission or missing transfer evidence', () => {
  const detail = run({state: 'completed', candidates: [candidate()], best_candidate_id: 'clip-1',
    methods: [{id: 'method-1'}], transfers: [{demonstration_id: 'transfer-demo'}]});
  assert.equal(canPublish(detail), false);
  assert.equal(canPublish({...detail, publishable: true, transfers: []}), false);
  assert.equal(canPublish({...detail, publishable: true, publication_blockers: ['迁移未通过']}), false);
  assert.equal(canPublish({...detail, publishable: true}), true);
  assert.deepEqual(publicationSelection(detail), {method_version_ids: ['method-1'],
    demonstration_ids: ['demonstration-clip-1', 'transfer-demo']});
  assert.match(methodStateLabel('completed'), /待评价/);
});

function actualServiceDetail(changes = {}) {
  const best = {...candidate('best'), method_version_ids: ['best-method-v2'],
    media_url: '/api/method-lab/commissions/commission-1/candidates/best/media'};
  return run({schema: 'video.animation-method-lab-detail.v1', state: 'completed',
    candidates: [{...candidate('old'), method_version_ids: ['old-method-v1']}, best], best_candidate_id: 'best',
    methods: [{id: 'old-method-v1', title: '旧候选方法', status: 'candidate'},
      {id: 'best-method-v2', title: '当前最佳方法', status: 'candidate'}],
    publication_method_version_ids: ['best-method-v2'], requires_audio_verification: false,
    validations: [
      {id: 'old-reference', method_version_id: 'old-method-v1', role: 'reference', outcome: 'passed', demonstration_version_id: 'old-demo'},
      {id: 'reference-v2', method_version_id: 'best-method-v2', role: 'reference', outcome: 'incomplete', demonstration_version_id: 'demonstration-best'},
      {id: 'transfer-v2', method_version_id: 'best-method-v2', role: 'transfer', outcome: 'incomplete',
        demonstration_version_id: 'different-structure-demo', baseline_demonstration_version_id: 'demonstration-best'},
      {id: 'failed-transfer', method_version_id: 'best-method-v2', role: 'transfer', outcome: 'failed', demonstration_version_id: 'failed-demo'}],
    transfers: [{id: 'transfer-v2', method_version_id: 'best-method-v2', demonstration_version_id: 'different-structure-demo',
      role: 'transfer', outcome: 'incomplete', title: '分支汇合', goal: '追踪交错完成的请求',
      media_url: '/api/method-lab/commissions/transfer-commission/candidates/transfer/media',
      structural_changes: {relations: {before: '单路径', after: '分支与汇合'}}}],
    publication_ready: true, publishable: true, publication_blockers: [],
    summary: '保持了更清楚的对象身份', execution: [{id: 'operation', stage: 'creator', status: 'completed'}], ...changes});
}

test('actual service publication selects only the best method and its nonfailed reference and transfer evidence', () => {
  const detail = actualServiceDetail();
  assert.deepEqual(publicationSelection(detail), {method_version_ids: ['best-method-v2'],
    demonstration_ids: ['demonstration-best', 'different-structure-demo']});
  assert.equal(canPublish(detail), true);
  assert.deepEqual(publicationSelection({...detail, publication_method_version_ids: []}),
    {method_version_ids: [], demonstration_ids: []});
  const published = {...detail, methods: detail.methods.map(row => row.id === 'best-method-v2' ? {...row, status: 'published'} : row)};
  assert.equal(methodsPublished(published), true); assert.equal(canPublish(published), false);
  assert.equal(canPublish({...detail, state: 'running'}), false);
});

test('actual feedback and publication records trigger GET detail refresh without resubmitting mutations', async () => {
  let detail = actualServiceDetail();
  const h = harness((path, options) => {
    if (options.method && path.endsWith('/feedback')) return {id: 'feedback-record', commission_id: detail.id,
      candidate_id: 'best', comment: '改进节奏', source: 'explicit_developer_feedback'};
    if (options.method && path.endsWith('/publish')) {
      detail = {...detail, methods: detail.methods.map(row => ({...row, status: 'published'}))};
      return {methods: detail.methods, publication: 'published'};
    }
    return path.endsWith('/catalog') ? catalog([detail]) : detail;
  });
  await h.controller.refresh(); await h.controller.select(detail.id);
  assert.equal(await h.controller.saveFeedback({candidate_id: 'best', comment: '改进节奏'}), true);
  assert.equal(h.requests.at(-1).path, 'api/method-lab/commissions/commission-1');
  assert.equal(h.requests.at(-1).options.method, undefined);
  assert.equal(await h.controller.publish(false), false);
  assert.equal(await h.controller.publish(true, publicationApprovalBinding(detail)), true);
  const posts = h.requests.filter(row => row.options.method);
  assert.deepEqual(posts.map(row => row.path.split('/').at(-1)), ['feedback', 'publish']);
  assert.deepEqual(JSON.parse(posts[1].options.body).human_verification, {motion_watched: true, audio_listened: false});
  assert.equal(h.requests.at(-1).options.method, undefined);
  assert.equal(methodsPublished(h.controller.snapshot.detail), true);
  assert.equal(await h.controller.publish(true), false);
  h.controller.close();
});

test('publication sends human watching/listening evidence only after explicit acknowledgement', async () => {
  const detail = run({state: 'completed', candidates: [candidate()], best_candidate_id: 'clip-1',
    methods: [{id: 'method-1'}], transfers: [{demonstration_id: 'transfer-demo'}], publishable: true});
  const h = harness((path, options) => path.endsWith('/catalog') ? catalog() : detail);
  await h.controller.refresh(); await h.controller.select(detail.id);
  assert.equal(await h.controller.publish(false), false);
  assert.equal(h.requests.some(row => row.options.method), false);
  assert.equal(await h.controller.publish(true), true);
  assert.deepEqual(JSON.parse(h.requests.at(-1).options.body).human_verification,
    {motion_watched: true, audio_listened: true});
  h.controller.state.detail = {...detail, requires_audio_verification: false};
  await h.controller.publish(true);
  assert.deepEqual(JSON.parse(h.requests.at(-1).options.body).human_verification,
    {motion_watched: true, audio_listened: false});
});

test('an acknowledgement of an older displayed method/sample cannot publish a background-updated version', async () => {
  const detail = run({state: 'completed', candidates: [candidate()], best_candidate_id: 'clip-1',
    methods: [{id: 'method-1'}], transfers: [{demonstration_id: 'transfer-demo'}], publishable: true});
  const h = harness(path => path.endsWith('/catalog') ? catalog() : detail);
  await h.controller.refresh(); await h.controller.select(detail.id);
  const displayed = publicationApprovalBinding(detail);
  h.controller.state.detail = {...detail, methods: [{id: 'method-2'}]};
  assert.equal(await h.controller.publish(true, displayed), false);
  assert.match(h.controller.snapshot.error, /已经更新/);
  assert.equal(h.requests.some(row => row.options.method), false);
  assert.equal(canPublish({...detail, state: 'published'}), false);
});

test('saving feedback binds an actual candidate and never starts another commission', async () => {
  const detail = run({state: 'completed', candidates: [candidate()]});
  const h = harness(path => path.endsWith('/catalog') ? catalog() : detail);
  await h.controller.refresh(); await h.controller.select(detail.id);
  assert.equal(await h.controller.saveFeedback({candidate_id: 'unrelated', comment: '改进'}), false);
  assert.equal(await h.controller.saveFeedback({candidate_id: 'clip-1', comment: '统计值变化与筛选过程没有关联',
    start_seconds: 2, end_seconds: 5}), true);
  const posts = h.requests.filter(row => row.options.method === 'POST');
  assert.equal(posts.length, 1); assert.ok(posts[0].path.endsWith('/commission-1/feedback'));
  assert.equal(JSON.parse(posts[0].options.body).candidate_id, 'clip-1');
  assert.equal(JSON.parse(posts[0].options.body).start_seconds, 2);
});

class FakeElement {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.handlers = {}; this.attributes = {}; this.dataset = {}; this.className = ''; this._text = ''; this.readyState = 0; this.duration = NaN; this.currentTime = 0; this.paused = true; this.classList = {add: value => { this.className += ' ' + value; }}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(row => row.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); for (const node of nodes) node.parentElement = this; }
  replaceChildren(...nodes) { this._text = ''; this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  removeEventListener(name, fn) { if (this.handlers[name] === fn) delete this.handlers[name]; }
  removeAttribute(name) { delete this.attributes[name]; }
  pause() { this.paused = true; }
  load() {}
  querySelectorAll(tag) { return this.descendants(tag); }
  contains(node) { return this === node || this.descendants().includes(node); }
  closest(tag) { return this.tagName === tag.toUpperCase() ? this : this.parentElement?.closest(tag) ?? null; }
  focus() { if (this.ownerDocument) this.ownerDocument.activeElement = this; }
  descendants(tag) { return this.children.flatMap(row => [row, ...row.descendants(tag)]).filter(row => !tag || row.tagName === tag.toUpperCase()); }
}
const doc = {createElement: tag => new FakeElement(tag), createDocumentFragment: () => new FakeElement('fragment')};
const callbacks = {onBack() {}, onContinue() {}, onCancel() {}, onFeedback() {}, onPublish() {}};

function mountedGallery(initial, {catalogValue = null, detailRecords = []} = {}) {
  const html = readFileSync(new URL('../../video_workbench/animation_lab_web/method-gallery.html', import.meta.url), 'utf8');
  const nodes = new Map([...html.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"/g)]
    .map(([, tag, id]) => [id, new FakeElement(tag)]));
  const document = {activeElement: null, getElementById: id => nodes.get(id),
    createElement(tag) { const element = new FakeElement(tag); element.ownerDocument = this; return element; },
    createDocumentFragment: () => new FakeElement('fragment')};
  const get = id => nodes.get(id);
  const budgetDetails = new FakeElement('details'); budgetDetails.open = false;
  budgetDetails.append(get('experiment-rounds'));
  get('gallery-search').value = ''; get('gallery-state').value = 'all'; get('gallery-engine').value = 'all';
  get('gallery-form-panel').hidden = get('gallery-detail').hidden = true;
  get('experiment-form').reset = () => {
    for (const node of nodes.values()) node.value = '';
    get('experiment-duration').value = '24'; get('experiment-rounds').value = '3'; get('experiment-seconds').value = '15';
  };
  let current = initial;
  const records = new Map(detailRecords.map(row => [row.id, row])), requests = [];
  const controller = mountMethodGallery({document, root, storage: storage(), uuid: () => firstId,
    schedule: () => 1, unschedule() {}, fetch: async (url, options = {}) => {
      requests.push({url, options});
      return {ok: true, status: 200, json: async () => structuredClone(url.pathname.endsWith('/catalog')
        ? catalogValue ?? catalog([current]) : records.get(url.pathname.split('/commissions/')[1]?.split('/')[0]) ?? current)};
    }});
  return {controller, document, get, requests, ready: () => new Promise(resolve => setImmediate(resolve)),
    update: value => { current = value; }};
}

test('the homepage renders only server-grouped effects and opens each representative commission', async () => {
  const original = run({id: 'root', state: 'completed'}), continuation = run({id: 'continuation', state: 'completed'});
  const failedTransfer = run({id: 'failed-transfer', state: 'failed'});
  const effect = {...original, id: 'root', effect_id: 'root', open_commission_id: continuation.id,
    commissions: [original, continuation, failedTransfer]};
  const h = mountedGallery(original, {catalogValue: catalog([original, continuation, failedTransfer], {effects: [effect]}),
    detailRecords: [continuation]});
  await h.ready();
  const cards = h.get('gallery-cards').descendants('article');
  assert.equal(cards.length, 1); assert.equal(cards[0].dataset.effectId, 'root');
  assert.equal(h.get('gallery-count').textContent, '1 个效果');
  cards[0].descendants('button')[0].handlers.click();
  await h.ready();
  assert.ok(h.requests.some(row => row.url.pathname.endsWith('/commissions/continuation')));
  assert.equal(h.requests.some(row => row.options.method), false);
  h.controller.close();
});

test('commissions never become fallback effect cards and similarly named independent effects stay separate', async () => {
  const original = run(), h = mountedGallery(original, {catalogValue: catalog([original])});
  await h.ready();
  assert.equal(h.get('gallery-cards').descendants('article').length, 0);
  assert.equal(h.get('gallery-count').textContent, '0 个效果'); h.controller.close();
  const independent = mountedGallery(original, {catalogValue: catalog([original], {effects: [
    {...original, id: 'root-one', open_commission_id: 'run-one'},
    {...original, id: 'root-two', open_commission_id: 'run-two'}]})});
  await independent.ready();
  assert.equal(independent.get('gallery-cards').descendants('article').length, 2);
  independent.controller.close();
});

test('rendering technology, state and alias search combine and can be reset without starting work', async () => {
  const effects = [
    {id: 'svg-effect', title: '队列', goal: '追踪请求', state: 'completed', engine: 'html-svg', engine_status: 'rendered'},
    {id: 'canvas-effect', title: '波动', goal: '理解传播', state: 'creating', engine: 'canvas', engine_status: 'rendered'},
    {id: 'three-effect', title: '装配', goal: '理解结构', state: 'completed', engine: 'three', engine_status: 'rendered'},
    {id: 'unknown-effect', title: '探索中', goal: '尚未验证', state: 'failed', engine: null, engine_status: 'unverified'},
  ];
  const h = mountedGallery(run(), {catalogValue: catalog([], {effects, availability: {enabled: true, allowed_engines: ['html-svg']}})});
  await h.ready();
  const visibleIds = () => h.get('gallery-cards').descendants('article').map(row => row.dataset.effectId);
  const select = (id, value) => { h.get(id).value = value; h.get(id).handlers.change(); };
  const search = value => { h.get('gallery-search').value = value; h.get('gallery-search').handlers.input(); };
  assert.match(h.get('gallery-engine-availability').textContent, /当前可生成：HTML\/SVG；Canvas 2D、Three\.js 暂未开放/);
  select('gallery-state', 'active'); select('gallery-engine', 'canvas'); search('2d');
  assert.deepEqual(visibleIds(), ['canvas-effect']); assert.equal(h.get('gallery-count').textContent, '1 / 4 个效果');
  search('threejs'); assert.deepEqual(visibleIds(), []); assert.equal(h.get('gallery-no-results').hidden, false);
  h.get('gallery-reset').handlers.click();
  assert.equal(h.get('gallery-state').value, 'all'); assert.equal(h.get('gallery-engine').value, 'all');
  assert.equal(h.get('gallery-search').value, ''); assert.equal(h.get('gallery-count').textContent, '4 个效果');
  assert.equal(h.get('gallery-no-results').hidden, true);
  search('html/svg'); assert.deepEqual(visibleIds(), ['svg-effect']);
  search('threejs'); assert.deepEqual(visibleIds(), ['three-effect']);
  search('待验证'); select('gallery-engine', 'unknown'); assert.deepEqual(visibleIds(), ['unknown-effect']);
  assert.equal(h.requests.some(row => row.options.method), false); h.controller.close();
});

test('technology filters use only the displayed sample, ignoring family engines, method requirements and source declarations', async () => {
  const effects = [
    {id: 'representative-svg', title: '对象过程', state: 'completed', engine: 'html-svg', engine_status: 'rendered',
      engines: ['three'], commissions: [{id: 'other', engine: 'three', engine_status: 'rendered'}],
      methods: [{runtime_requirements: {engine: 'canvas'}}]},
    {id: 'declaration-only', title: '待验证对象', state: 'failed', engine: 'three', engine_status: 'unverified',
      best_candidate: {source: {engine: 'three'}}},
    {id: 'conflict', title: '待核对对象', state: 'completed', engine: 'canvas', engine_status: 'conflict'},
  ];
  const h = mountedGallery(run(), {catalogValue: catalog([], {effects, availability: {enabled: true, allowed_engines: ['html-svg']}})});
  await h.ready();
  h.get('gallery-engine').value = 'three'; h.get('gallery-engine').handlers.change();
  assert.equal(h.get('gallery-cards').descendants('article').length, 0);
  assert.match(h.get('gallery-no-results-message').textContent, /暂无 Three\.js 实际样片/);
  assert.match(h.get('gallery-no-results-message').textContent, /暂未开放生成/);
  h.get('gallery-engine').value = 'canvas'; h.get('gallery-engine').handlers.change();
  assert.equal(h.get('gallery-cards').descendants('article').length, 0);
  assert.match(h.get('gallery-no-results-message').textContent, /暂无 Canvas 2D 实际样片/);
  h.get('gallery-reset').handlers.click();
  h.get('gallery-search').value = 'threejs'; h.get('gallery-search').handlers.input();
  assert.equal(h.get('gallery-cards').descendants('article').length, 0);
  h.get('gallery-reset').handlers.click();
  h.get('gallery-engine').value = 'unknown'; h.get('gallery-engine').handlers.change();
  assert.deepEqual(h.get('gallery-cards').descendants('article').map(row => row.dataset.effectId), ['declaration-only', 'conflict']);
  assert.equal(h.requests.some(row => row.options.method), false); h.controller.close();
});

test('cards distinguish confirmed technology, unverified source and an absent sample', () => {
  const known = renderMethodCard(doc, {id: 'known', engine: 'html-svg', engine_status: 'rendered'}, root, () => {});
  assert.match(known.textContent, /渲染技术HTML\/SVG/);
  const declared = renderMethodCard(doc, {id: 'declared', engine: null, engine_status: 'unverified',
    methods: [{runtime_requirements: {engine: 'three'}}]}, root, () => {});
  assert.match(declared.textContent, /渲染技术待验证/); assert.doesNotMatch(declared.textContent, /Three\.js/);
  const missing = renderMethodCard(doc, {id: 'missing', engine: null, engine_status: 'unknown'}, root, () => {});
  assert.match(missing.textContent, /渲染技术尚无样片/);
});

test('candidate, history and transfer badges show each actual sample technology and follow comparison selection', () => {
  let comparison;
  const candidates = [
    {...candidate('svg'), engine: 'html-svg', engine_status: 'rendered'},
    {...candidate('canvas'), engine: 'canvas', engine_status: 'rendered'},
    {...candidate('three'), engine: 'three', engine_status: 'rendered'},
  ];
  const rendered = renderMethodDetail(doc, run({state: 'completed', candidates, best_candidate_id: 'canvas',
    effect: {id: 'effect', commissions: [
      {id: 'original', kind: 'original', state: 'completed', engine: 'three', engine_status: 'rendered'},
      {id: 'unverified', kind: 'transfer', state: 'failed', engine: null, engine_status: 'unverified'},
    ]}, transfers: [{id: 'validation', demonstration_id: 'demo', media_url: candidate().media_url,
      engine: 'html-svg', engine_status: 'rendered'}]}), root, {...callbacks, onComparisonReady: value => { comparison = value; }});
  const comparisonBadges = () => comparison.element.descendants('span').filter(row => row.dataset.renderEngine !== undefined);
  assert.deepEqual(comparisonBadges().map(row => row.textContent), ['HTML/SVG', 'Canvas 2D']);
  const right = comparison.element.descendants('select').find(row => row.attributes['aria-label'] === '右侧样片');
  right.value = 'three'; right.handlers.change();
  assert.deepEqual(comparisonBadges().map(row => row.textContent), ['HTML/SVG', 'Three.js']);
  const left = comparison.element.descendants('select').find(row => row.attributes['aria-label'] === '左侧样片');
  left.value = 'three'; left.handlers.change();
  assert.deepEqual(comparisonBadges().map(row => row.textContent), ['Three.js']);
  left.value = 'canvas'; left.handlers.change(); right.value = 'svg'; right.handlers.change();
  assert.deepEqual(comparisonBadges().map(row => row.textContent), ['Canvas 2D', 'HTML/SVG']);
  const history = rendered.descendants('details').find(row => row.dataset.methodDisclosure === 'effect-history');
  assert.deepEqual(history.descendants('span').filter(row => row.dataset.renderEngine !== undefined).map(row => row.textContent), ['Three.js', '待验证']);
  const transfer = rendered.descendants('article').find(row => row.className === 'transfer-item');
  assert.deepEqual(transfer.descendants('span').filter(row => row.dataset.renderEngine !== undefined).map(row => row.textContent), ['HTML/SVG']);
  comparison.destroy();
});

test('effect history links exact original, continuation and failed transfer records without relabelling a global best', () => {
  const selected = [], effect = {id: 'root', title: '筛选效果', open_commission_id: 'continued', commissions: [
    {id: 'root', title: '最初目标', kind: 'original', state: 'completed'},
    {id: 'continued', title: '优化节奏', kind: 'continuation', state: 'completed'},
    {id: 'transfer-failed', title: '不同结构', kind: 'transfer', state: 'failed'}]};
  const rendered = renderMethodDetail(doc, run({id: 'continued', title: '优化节奏', effect}), root,
    {...callbacks, onSelectCommission: id => selected.push(id)});
  const history = rendered.descendants('details').find(node => node.dataset.methodDisclosure === 'effect-history');
  assert.match(history.textContent, /版本与实验记录（3）/);
  assert.match(history.textContent, /原作/); assert.match(history.textContent, /继续优化/);
  assert.match(history.textContent, /迁移验证/); assert.match(history.textContent, /本次执行失败/);
  assert.doesNotMatch(history.textContent, /全局最佳/);
  const buttons = history.descendants('button');
  assert.equal(buttons[1].attributes['aria-current'], 'true');
  buttons[1].handlers.click(); buttons[0].handlers.click(); buttons[2].handlers.click();
  assert.deepEqual(selected, ['root', 'transfer-failed']);
});

test('switching history preserves commission-scoped feedback, continuation and publication', async () => {
  const original = run({id: 'root', state: 'completed', candidates: [candidate('original')], best_candidate_id: 'original'});
  const continued = run({id: 'continued', title: '优化版本', state: 'completed', candidates: [candidate('improved')],
    best_candidate_id: 'improved', publishable: true, methods: [{id: 'method-improved'}], transfers: [{demonstration_id: 'transfer-demo'}]});
  const effect = {id: 'root', open_commission_id: 'continued', commissions: [
    {id: 'root', title: '原版', kind: 'original', state: 'completed'},
    {id: 'continued', title: '优化版本', kind: 'continuation', state: 'completed'}]};
  original.effect = continued.effect = effect;
  const h = mountedGallery(original, {detailRecords: [original, continued]});
  await h.ready(); await h.controller.select('root');
  h.get('gallery-detail').descendants('button').find(row => row.textContent === '优化版本').handlers.click();
  await h.ready();
  assert.equal(h.controller.snapshot.detail.id, 'continued');
  const host = h.get('gallery-detail');
  host.descendants('textarea')[0].value = '保留对象轨迹';
  await host.descendants('form')[0].handlers.submit({preventDefault() {}});
  await h.ready();
  assert.ok(h.requests.some(row => row.url.pathname.endsWith('/commissions/continued/feedback')));
  const approval = host.descendants('input').find(row => row.type === 'checkbox');
  approval.checked = true; approval.handlers.change();
  host.descendants('button').find(row => row.textContent === '认可并发布方法').handlers.click();
  await h.ready();
  const publication = h.requests.find(row => row.url.pathname.endsWith('/commissions/continued/publish'));
  assert.ok(publication); assert.deepEqual(JSON.parse(publication.options.body).method_version_ids, ['method-improved']);
  host.descendants('button').find(row => row.textContent === '继续优化这版').handlers.click();
  assert.equal(h.get('experiment-title').value, '优化版本');
  assert.equal(h.requests.filter(row => row.options.method).length, 2);
  h.controller.close();
});

test('a catalog read failure after acknowledged creation preserves success without another submission', async () => {
  let reads = 0;
  const h = harness((path, options) => {
    if (options.method) return run();
    if (++reads > 1) throw Error('catalog unavailable');
    return catalog();
  });
  await h.controller.refresh();
  assert.equal(await h.controller.start(fields), true);
  assert.equal(h.controller.snapshot.pending, null); assert.equal(h.store.data.size, 0);
  assert.equal(h.controller.snapshot.detail.id, 'commission-1');
  assert.equal(h.controller.snapshot.error, ''); assert.match(h.controller.snapshot.notice, /已确认提交/);
  assert.equal(h.requests.filter(row => row.options.method).length, 1); h.controller.close();
});

test('polling preserves each commission disclosure state and time drafts without replacing focused input', async () => {
  let detail = run({state: 'running', candidates: [candidate('a')], best_candidate_id: 'a',
    feedback: [{id: 'first', candidate_id: 'a', comment: '原意见'}], methods: [{id: 'm1'}]});
  const h = mountedGallery(detail);
  await h.controller.refresh(); await h.controller.select(detail.id);
  const host = h.get('gallery-detail');
  const section = key => host.descendants('details').find(node => node.dataset.methodDisclosure === key);
  const input = label => host.descendants('input').find(node => node.attributes['aria-label'] === label);
  for (const key of ['feedback-range', 'execution', 'feedback-history', 'methods']) section(key).open = true;
  const start = input('意见开始秒数'); start.value = '2'; start.handlers.input(); start.focus();
  input('意见结束秒数').value = '3.5'; input('意见结束秒数').handlers.input();
  const comment = host.descendants('textarea')[0]; comment.value = '继续让变化更清楚'; comment.handlers.input();
  detail = {...detail, feedback: [...detail.feedback, {id: 'second', candidate_id: 'a', comment: '新增意见'}],
    methods: [...detail.methods, {id: 'm2'}], execution: [{id: 'operation', status: 'running'}]};
  h.update(detail); await h.controller.refresh();
  assert.equal(input('意见开始秒数'), start);
  h.document.activeElement = null; h.controller.emit();
  assert.notEqual(input('意见开始秒数'), start);
  for (const key of ['feedback-range', 'execution', 'feedback-history', 'methods']) assert.equal(section(key).open, true);
  assert.match(section('feedback-history').textContent, /2 条有效/);
  assert.match(section('methods').textContent, /关联创作方法（2）/);
  assert.equal(input('意见开始秒数').value, '2'); assert.equal(input('意见结束秒数').value, '3.5');
  assert.equal(host.descendants('textarea')[0].value, '继续让变化更清楚');
  section('feedback-range').open = false;
  detail = {...detail, execution: [{id: 'operation', status: 'completed'}]};
  h.update(detail); await h.controller.refresh();
  assert.equal(section('feedback-range').open, false);
  const other = run({id: 'commission-2', state: 'running', candidates: [candidate('other')], best_candidate_id: 'other'});
  h.update(other); await h.controller.select(other.id);
  assert.notEqual(section('execution').open, true); assert.equal(section('feedback-range').open, false);
  assert.equal(input('意见开始秒数').value, ''); assert.equal(host.descendants('textarea')[0].value, '');
  h.update(detail); await h.controller.select(detail.id);
  assert.equal(section('execution').open, true); assert.equal(section('feedback-range').open, false);
  assert.equal(input('意见开始秒数').value, '2'); h.controller.close();
});

test('continue form inherits the selected actual media duration while a fresh experiment keeps defaults', async () => {
  const detail = run({state: 'completed', candidates: [
    {...candidate('original'), media: {duration_seconds: 16}}, {...candidate('best'), media: {duration_seconds: 31}}],
    best_candidate_id: 'best', criteria: [{id: 'clear', description: '能看清变化'}]});
  const h = mountedGallery(detail);
  await h.controller.refresh(); await h.controller.select(detail.id);
  const host = h.get('gallery-detail');
  const selected = host.descendants('select').find(node => node.attributes['aria-label'] === '意见对应样片');
  selected.value = 'original'; selected.handlers.change();
  host.descendants('button').find(node => node.textContent === '继续优化这版').handlers.click();
  assert.equal(h.get('experiment-duration').value, '16');
  assert.equal(h.get('experiment-rounds').value, '3'); assert.equal(h.get('experiment-seconds').value, '15');
  assert.equal(h.get('experiment-criteria').value, '能看清变化');
  h.get('gallery-new').handlers.click(); assert.equal(h.get('experiment-duration').value, '24');
  assert.equal(continuationDuration({duration_seconds: 18}, null), 18);
  assert.equal(continuationDuration({...detail, candidates: [{...candidate('best'), media: {duration_seconds: NaN}}]}, 'best'), 24);
  h.controller.close();
});

test('failed and cancelled experiments offer retry with or without usable samples, but running or completed ones do not', () => {
  for (const state of ['failed', 'cancelled']) {
    for (const candidates of [[], [candidate()]]) {
      const detail = run({state, candidates, can_retry: true}); let opened;
      const rendered = renderMethodDetail(doc, detail, root, {...callbacks, onRetry: value => { opened = value; }});
      const retry = rendered.descendants('button').find(node => node.textContent === '重新运行');
      assert.ok(retry); assert.equal(retry.disabled, false); retry.handlers.click(); assert.equal(opened, detail);
      if (candidates.length) assert.ok(rendered.descendants('button').some(node => node.textContent === '继续优化这版' && !node.disabled));
    }
  }
  for (const detail of [run({state: 'running', can_retry: true}), run({state: 'completed', can_retry: true}),
    run({state: 'failed', can_retry: false})]) {
    const rendered = renderMethodDetail(doc, detail, root, callbacks);
    assert.equal(rendered.descendants('button').some(node => node.textContent === '重新运行'), false);
  }
});

test('opening retry only prefills the original goal, reference, standards and a new budget before explicit submission', async t => {
  const criterion = {id: 'tracking', description: '对象运动过程可追踪', requires_motion: true, observation: '看完整段运动'};
  const detail = run({state: 'failed', duration_seconds: 14, can_retry: true, base_commission_id: 'original-run',
    feedback_text: '保留上一版的轨迹，改善交叉时的可辨识度',
    reference: {url: 'https://www.bilibili.com/video/BVexample/', start_seconds: 12.5, end_seconds: 38},
    retry_budget: {max_rounds: 4, max_seconds: 901}, budget: {seconds: 88}, criteria: [criterion],
    candidates: [{...candidate('first'), media: {duration_seconds: 11}}], best_candidate_id: 'first'});
  const h = mountedGallery(detail); await h.ready(); await h.controller.select(detail.id);
  h.get('gallery-detail').descendants('button').find(node => node.textContent === '重新运行').handlers.click();
  assert.equal(h.get('gallery-form-panel').hidden, false);
  assert.equal(h.get('experiment-form-title').textContent, '重新运行本次实验');
  assert.equal(h.get('experiment-title').value, fields.title); assert.equal(h.get('experiment-goal').value, fields.goal);
  assert.equal(h.get('experiment-reference').value, detail.reference.url);
  assert.equal(h.get('experiment-reference-start').value, 12.5); assert.equal(h.get('experiment-reference-end').value, 38);
  assert.equal(h.get('experiment-criteria').value, criterion.description);
  assert.equal(h.get('experiment-duration').value, '14'); assert.equal(h.get('experiment-rounds').value, '1');
  assert.equal(h.get('experiment-rounds').closest('details').open, true);
  assert.equal(h.get('experiment-seconds').value, '16'); assert.equal(h.get('experiment-feedback').required, false);
  assert.equal(h.get('experiment-feedback').value, detail.feedback_text);
  assert.match(h.get('experiment-base-note').textContent, /使用新预算，保留已有样片和失败记录/);
  assert.equal(h.requests.some(row => row.options.method), false);
  const inputIds = {title: 'title', goal: 'goal', feedback: 'feedback', reference_url: 'reference',
    reference_start: 'reference-start', reference_end: 'reference-end', duration_seconds: 'duration',
    max_rounds: 'rounds', max_minutes: 'seconds', criteria_text: 'criteria'};
  t.mock.method(globalThis, 'FormData', function(form) {
    assert.equal(form, h.get('experiment-form'));
    return Object.entries(inputIds).map(([name, id]) => [name, h.get('experiment-' + id).value]);
  });
  await h.get('experiment-form').handlers.submit({preventDefault() {}});
  const posts = h.requests.filter(row => row.options.method);
  assert.equal(posts.length, 1); assert.equal(posts[0].url.pathname, '/dev/animations/api/method-lab/commissions');
  const body = JSON.parse(posts[0].options.body);
  assert.equal(body.retry_of_commission_id, detail.id); assert.deepEqual(body.criteria, [criterion]);
  assert.equal(body.feedback, detail.feedback_text);
  assert.deepEqual(body.reference, detail.reference); assert.deepEqual(body.budget, {max_rounds: 1, max_seconds: 960});
  assert.equal(Object.hasOwn(body, 'base_commission_id'), false); assert.equal(Object.hasOwn(body, 'base_candidate_id'), false);
  h.controller.close();
});

test('retry defaults clamp unusually long budgets and preview retries cannot execute', async () => {
  const detail = run({state: 'cancelled', can_retry: true, ui_preview: true, retry_budget: {max_seconds: 8000}});
  const h = mountedGallery(detail); await h.ready(); await h.controller.select(detail.id);
  const retry = h.get('gallery-detail').descendants('button').find(node => node.textContent === '重新运行');
  assert.equal(retry.disabled, true);
  retry.handlers.click();
  assert.equal(h.get('experiment-seconds').value, '60'); assert.equal(h.get('experiment-submit').disabled, true);
  await h.get('experiment-form').handlers.submit({preventDefault() {}});
  assert.equal(h.requests.some(row => row.options.method), false); h.controller.close();
});

test('effect history names retries separately from continuation', () => {
  const rendered = renderMethodDetail(doc, run({state: 'failed', effect: {commissions: [
    {id: 'retry', title: '重试原目标', kind: 'retry', state: 'completed'}]}}), root, callbacks);
  const history = rendered.descendants('details').find(node => node.dataset.methodDisclosure === 'effect-history');
  assert.match(history.textContent, /重新运行/); assert.doesNotMatch(history.textContent, /继续优化/);
});

test('real media exposes controls without forcing autoplay or muting necessary sound', () => {
  const stage = renderMethodMedia(doc, candidate(), root), video = stage.descendants('video')[0];
  assert.equal(video.controls, true); assert.equal(video.preload, 'metadata');
  assert.notEqual(video.autoplay, true); assert.notEqual(video.muted, true);
  assert.equal(renderMethodMedia(doc, {media_url: 'https://evil.example/video.mp4'}, root).descendants('video').length, 0);
});

test('cards and details keep malicious content as text and media absent when untrusted', () => {
  const hostile = '<img src=x onerror=alert(1)>';
  const card = renderMethodCard(doc, {id: 'one', title: hostile, goal: hostile, state: 'completed',
    media_url: 'javascript:alert(1)'}, root, () => {});
  assert.ok(card.textContent.includes(hostile)); assert.equal(card.descendants('img').length, 0);
  const detail = renderMethodDetail(doc, run({state: 'completed', title: hostile, criteria: [{description: hostile}],
    candidates: [candidate()], best_candidate_id: 'clip-1', remaining_issues: ['还看不清因果'],
    methods: [{id: 'm', title: '方法', guidance: hostile}], transfers: []}), root, callbacks);
  assert.ok(detail.textContent.includes(hostile)); assert.ok(detail.textContent.includes('还看不清因果'));
  assert.equal(detail.descendants('img').length, 0);
  assert.ok(detail.descendants('details').some(row => row.textContent.includes('关联创作方法')));
  assert.ok(detail.textContent.includes('尚未形成迁移验证结果'));
  assert.ok(detail.descendants('button').find(row => row.textContent === '认可并发布方法').disabled);
});

test('an unsaved comment and selected candidate survive detail reconstruction', () => {
  const detail = renderMethodDetail(doc, run({state: 'completed', candidates: [candidate('first'), candidate('best')],
    best_candidate_id: 'best'}), root, {...callbacks,
    feedbackDraft: {candidate_id: 'first', comment: '希望继续改善原作的对象分组'}});
  assert.equal(detail.descendants('select').find(row => row.attributes['aria-label'] === '意见对应样片').value, 'first');
  assert.equal(detail.descendants('textarea')[0].value, '希望继续改善原作的对象分组');
});

test('a regressing attempt remains viewable when the frozen baseline is still best', () => {
  const baseline = candidate('baseline'), attempt = candidate('attempt');
  const rendered = renderMethodDetail(doc, run({state: 'completed', baseline_candidate: baseline,
    candidates: [baseline, attempt], best_candidate_id: 'baseline'}), root, callbacks);
  assert.equal(rendered.descendants('video').length, 2);
  const comparison = rendered.descendants('select').filter(row => ['左侧样片', '右侧样片'].includes(row.attributes['aria-label']));
  assert.deepEqual(comparison.map(row => row.value), ['baseline', 'attempt']);
  assert.match(comparison[0].textContent, /当前最佳.*优化基线/);
});

test('actual transfer metadata is playable and published methods are labelled without hiding pending motion evidence', () => {
  const detail = actualServiceDetail();
  const rendered = renderMethodDetail(doc, detail, root, callbacks);
  assert.equal(rendered.descendants('video').length, 3);
  assert.ok(rendered.descendants('video').some(video => video.src.endsWith('/transfer-commission/candidates/transfer/media')));
  assert.match(rendered.textContent, /部分证据待人工核对/);
  assert.match(rendered.textContent, /本次样片无音效，无需试听/);
  const published = {...detail, methods: detail.methods.map(row => row.id === 'best-method-v2' ? {...row, status: 'published'} : row)};
  const card = renderMethodCard(doc, {...published, best_candidate: published.candidates[1]}, root, () => {});
  assert.match(card.textContent, /方法已发布/);
  const after = renderMethodDetail(doc, published, root, callbacks);
  assert.match(after.textContent, /本版本的方法已发布/);
  assert.ok(after.descendants('button').find(row => row.textContent === '认可并发布方法').disabled);
});

test('three method validations of one demonstration share a single preview while retaining each result', () => {
  const methods = [{id: 'method-a', title: '对象身份'}, {id: 'method-b', title: '因果动作'}, {id: 'method-c', title: '空间编排'}];
  const outcomes = ['passed', 'incomplete', 'failed'];
  const transfers = methods.map((method, index) => ({id: 'validation-' + index, method_version_id: method.id,
    demonstration_id: 'one-demo', title: '同一迁移片段', outcome: outcomes[index],
    media_url: 'api/method-lab/commissions/transfer/candidates/one/media'}));
  const rendered = renderMethodDetail(doc, run({state: 'completed', methods, transfers}), root, callbacks);
  const previews = rendered.descendants('article').filter(row => row.className === 'transfer-item');
  assert.equal(previews.length, 1); assert.equal(previews[0].descendants('video').length, 1);
  assert.deepEqual(previews[0].descendants('p').filter(row => row.dataset.validationId)
    .map(row => [row.dataset.validationId, row.dataset.methodVersionId]),
  [['validation-0', 'method-a'], ['validation-1', 'method-b'], ['validation-2', 'method-c']]);
  assert.match(previews[0].textContent, /对象身份：检查通过 · 待认可/);
  assert.match(previews[0].textContent, /因果动作：部分证据待人工核对/);
  assert.match(previews[0].textContent, /空间编排：尚未通过目标标准/);
  assert.match(rendered.textContent, /发布后，正式制作可读取这些方法；采用情况保存在制作记录中/);
  assert.doesNotMatch(rendered.textContent, /正式制作接入.*待完成/);
});

test('transfer preview fallback groups exact media URLs without merging different demonstrations or matching titles', () => {
  const shared = 'api/method-lab/commissions/transfer/candidates/one/media';
  const rendered = renderMethodDetail(doc, run({state: 'completed', transfers: [
    {title: '相同名称', media_url: shared, outcome: 'passed'},
    {title: '相同名称', media_url: shared, outcome: 'incomplete'},
    {title: '相同名称', media_url: shared, demonstration_id: 'different-demo', outcome: 'passed'},
    {title: '相同名称', media_url: 'api/method-lab/commissions/transfer/candidates/two/media', outcome: 'failed'},
  ]}), root, callbacks);
  assert.equal(rendered.descendants('article').filter(row => row.className === 'transfer-item').length, 3);
});

test('the new entry presents an effect gallery and goals rather than legacy module/engineering workflows', () => {
  const html = readFileSync(new URL('../../video_workbench/animation_lab_web/method-gallery.html', import.meta.url), 'utf8');
  assert.match(html, /id="method-gallery-app"/); assert.match(html, /希望观众看懂什么/);
  assert.match(html, /本次最多探索轮数/); assert.match(html, /可包含帮助表达的音效/);
  assert.match(html, /发布后，正式制作可读取这些方法；采用情况保存在制作记录中/);
  assert.doesNotMatch(html, /正式制作接入.*待完成/);
  assert.doesNotMatch(html, /组件与参数回归|跨领域需求矩阵|生成增量任务包|held_out|调优输入/);
});

test('late old detail cannot replace a newly created commission', async () => {
  const old = deferred();
  const h = harness((path, options) => options.method ? run({id: 'new'}) : path.endsWith('/old') ? old.promise : catalog());
  await h.controller.refresh(); const reading = h.controller.select('old');
  assert.equal(await h.controller.start(fields), true);
  old.resolve(run({id: 'old'})); await reading;
  assert.equal(h.controller.snapshot.detail.id, 'new'); h.controller.close();
});

test('pair preferences and withdrawal only post the explicit scoped action', async () => {
  const detail = run({state: 'completed', candidates: [candidate('a'), candidate('b')], feedback: [{id: 'f', candidate_id: 'a', comment: '节奏'}]});
  const h = harness((path, options) => path.endsWith('/catalog') ? catalog() : options.method ? {id: 'receipt'} : detail);
  await h.controller.refresh(); await h.controller.select(detail.id);
  assert.equal(await h.controller.savePreference({left_candidate_id: 'a', right_candidate_id: 'a', preference: 'left'}), false);
  assert.equal(await h.controller.savePreference({left_candidate_id: 'a', right_candidate_id: 'b', preference: 'right'}), true);
  assert.equal(await h.controller.withdrawFeedback('foreign'), false);
  assert.equal(await h.controller.withdrawFeedback('f'), true);
  const posts = h.requests.filter(row => row.options.method);
  assert.deepEqual(posts.map(row => row.path.split('/').at(-1)), ['feedback', 'withdraw_feedback']);
  assert.deepEqual(JSON.parse(posts[0].options.body), {request_id: firstId, kind: 'preference', left_candidate_id: 'a', right_candidate_id: 'b', preference: 'right'});
  assert.deepEqual(JSON.parse(posts[1].options.body), {request_id: firstId, feedback_id: 'f'});
  h.controller.state.detail = layoutPreviewDetail();
  assert.equal(await h.controller.savePreference({left_candidate_id: 'layout-original', right_candidate_id: 'layout-best', preference: 'equal'}), false);
  assert.equal(h.requests.filter(row => row.options.method).length, 2); h.controller.close();
});

test('continue preloads only active text feedback for the exact selected candidate, preserving ranges', () => {
  const detail = {feedback: [
    {candidate_id: 'a', comment: '看不清关联', start_seconds: 2, end_seconds: 4},
    {candidate_id: 'a', comment: '已不需要', withdrawn: true},
    {candidate_id: 'b', comment: '别的版本'},
    {kind: 'preference', left_candidate_id: 'a', right_candidate_id: 'b', comment: '偏好说明'},
  ]};
  assert.equal(continuationFeedback(detail, 'a', '下一步优化节奏'), '[2–4 秒] 看不清关联\n\n下一步优化节奏');
  assert.equal(continuationFeedback(detail, 'a', '看不清关联'), '[2–4 秒] 看不清关联');
});

test('review controls bind preferences, frame capture, history jump and withdrawal to visible candidate IDs', () => {
  let player, preference, feedback, withdrawn, continued;
  const detail = run({state: 'completed', candidates: [candidate('a'), candidate('b')], best_candidate_id: 'b',
    feedback: [{id: 'f', candidate_id: 'a', comment: '这里需要停顿', start_seconds: 2, end_seconds: 3},
      {id: 'gone', candidate_id: 'a', comment: '已撤销', withdrawn: true}]});
  const rendered = renderMethodDetail(doc, detail, root, {...callbacks, comparisonSelection: {left: 'b', right: 'a', time: 1, speed: .5},
    feedbackDraft: {candidate_id: 'a', comment: '让过程更清楚', start_seconds: '1', end_seconds: '3'},
    onComparisonReady: value => { player = value; }, onPreference: value => { preference = value; },
    onFeedback: value => { feedback = value; }, onWithdraw: value => { withdrawn = value; },
    onContinue: (...args) => { continued = args; }});
  const byText = label => rendered.descendants('button').find(row => row.textContent === label);
  byText('右侧更好').handlers.click();
  assert.deepEqual(preference, {left_candidate_id: 'b', right_candidate_id: 'a', preference: 'right'});
  rendered.descendants('form')[0].handlers.submit({preventDefault() {}});
  assert.deepEqual(feedback, {candidate_id: 'a', comment: '让过程更清楚', start_seconds: '1', end_seconds: '3'});
  byText('撤回意见').handlers.click(); assert.equal(withdrawn, 'f');
  assert.equal(rendered.descendants('button').filter(row => row.textContent === '撤回意见').length, 1);
  byText('继续优化这版').handlers.click(); assert.equal(continued[1], 'a'); assert.match(continued[2], /这里需要停顿/); assert.doesNotMatch(continued[2], /已撤销/);
  for (const video of rendered.descendants('video')) { video.duration = 8; video.readyState = 2; video.handlers.loadedmetadata(); }
  byText('2–3 秒 · 跳到画面').handlers.click();
  assert.equal(player.getTime('a'), 2);
  byText('用当前画面时间').handlers.click();
  const input = name => rendered.descendants('input').find(row => row.attributes['aria-label'] === name);
  assert.equal(input('意见开始秒数').value, '2.000'); assert.equal(input('意见结束秒数').value, '2.000');
  player.destroy();
});

test('switching the feedback version clears a time range from the previous version', () => {
  let draft, player;
  const rendered = renderMethodDetail(doc, run({state: 'completed', candidates: [candidate('a'), candidate('b')], best_candidate_id: 'b'}), root,
    {...callbacks, feedbackDraft: {candidate_id: 'a', comment: '节奏', start_seconds: 1, end_seconds: 4},
      onDraftChange: value => { draft = value; }, onComparisonReady: value => { player = value; }});
  const select = rendered.descendants('select').find(row => row.attributes['aria-label'] === '意见对应样片');
  select.value = 'b'; select.handlers.change();
  assert.equal(draft.candidate_id, 'b'); assert.equal(draft.start_seconds, ''); assert.equal(draft.end_seconds, '');
  player.destroy();
});
