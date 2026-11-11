import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {
  MethodGalleryController, buildCommissionRequest, buildTransferRequest, methodAssetUrl, methodStateLabel, layoutPreviewDetail,
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
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.handlers = {}; this.attributes = {}; this.className = ''; this._text = ''; this.classList = {add: value => { this.className += ' ' + value; }}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(row => row.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text = ''; this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  descendants(tag) { return this.children.flatMap(row => [row, ...row.descendants(tag)]).filter(row => !tag || row.tagName === tag.toUpperCase()); }
}
const doc = {createElement: tag => new FakeElement(tag), createDocumentFragment: () => new FakeElement('fragment')};
const callbacks = {onBack() {}, onContinue() {}, onCancel() {}, onFeedback() {}, onPublish() {}};

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
  assert.equal(detail.descendants('select')[0].value, 'first');
  assert.equal(detail.descendants('textarea')[0].value, '希望继续改善原作的对象分组');
});

test('a regressing attempt remains viewable when the frozen baseline is still best', () => {
  const baseline = candidate('baseline'), attempt = candidate('attempt');
  const rendered = renderMethodDetail(doc, run({state: 'completed', baseline_candidate: baseline,
    candidates: [baseline, attempt], best_candidate_id: 'baseline'}), root, callbacks);
  assert.equal(rendered.descendants('video').length, 2);
  assert.match(rendered.textContent, /当前最佳样片 · 本次优化基线/);
  assert.match(rendered.textContent, /本次候选 · 暂未超过最佳样片/);
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

test('the new entry presents an effect gallery and goals rather than legacy module/engineering workflows', () => {
  const html = readFileSync(new URL('../../video_workbench/animation_lab_web/method-gallery.html', import.meta.url), 'utf8');
  assert.match(html, /id="method-gallery-app"/); assert.match(html, /希望观众看懂什么/);
  assert.match(html, /本次最多探索轮数/); assert.match(html, /可包含帮助表达的音效/);
  assert.doesNotMatch(html, /组件与参数回归|跨领域需求矩阵|生成增量任务包|held_out|调优输入/);
});
