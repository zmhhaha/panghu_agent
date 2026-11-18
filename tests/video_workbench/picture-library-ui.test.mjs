import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildPictureProject, buildPictureExperiment, pictureAssetUrl, pictureDefaultCandidate,
  pictureCandidates, pictureIsActive, validateReferenceFile, pictureCandidateRole} from '../../video_workbench/animation_lab_web/picture-model.mjs';
import {PictureLibraryController, referenceBody, renderPictureDetail, renderPictureCard} from '../../video_workbench/animation_lab_web/picture-library.mjs';

const root = new URL('https://video-service.panghuer.top/dev/animations/');
const uuid = '00000000-0000-4000-8000-000000000001';
const fields = {title: '山石的皴擦', goal: '学习笔触怎样构成体积', width: 1280, height: 720, max_rounds: 3, max_minutes: 15};
const project = (extra = {}) => ({id: 'picture-project-1', title: fields.title, goal: fields.goal, state: 'completed',
  references: [{id: 'ref-1', file_name: 'reference.png', url: '/api/picture-lab/references/ref-1/media'}],
  experiments: [{id: 'exp-1', state: 'completed', request_id: uuid, mode: 'new', frozen: {goal: fields.goal, use_wikiskill: false}, candidates: []}],
  candidates: [], ...extra});
const candidate = (id, extra = {}) => ({id, work_kind: 'reproduction',
  picture_url: '/api/picture-lab/projects/picture-project-1/artifacts/' + id + '-png',
  source_url: '/api/picture-lab/projects/picture-project-1/artifacts/' + id + '-source',
  receipt_url: '/api/picture-lab/projects/picture-project-1/artifacts/' + id + '-receipt', ...extra});
const catalog = (projects = [], extra = {}) => ({availability: {enabled: true}, csrf_token: 'csrf', owner_key: 'owner-test', projects, ...extra});
function memoryStorage() {
  const values = new Map(); return {getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)};
}
function harness(handler = () => catalog(), options = {}) {
  const requests = [], timers = new Map(); let sequence = 0;
  const controller = new PictureLibraryController({root, uuid: () => uuid, storage: memoryStorage(),
    request: async (path, request = {}) => { requests.push({path, request}); return handler(path, request); },
    schedule: fn => { timers.set(++sequence, fn); return sequence; }, unschedule: id => timers.delete(id), ...options});
  return {controller, requests, timers};
}
class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.handlers = {}; this._text = ''; this.open = false; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this._text = ''; this.children = items; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, handler) { this.handlers[name] = handler; }
}
const doc = {createElement: tag => new Element(tag), createDocumentFragment: () => new Element('fragment')};
const all = node => [node, ...node.children.flatMap(all)];
const buttons = node => all(node).filter(child => child.tagName === 'BUTTON');

test('a new image project transmits actual reference identities, a separate output size, static criteria and explicit knowledge choice', () => {
  const body = buildPictureProject({...fields, width: 960, height: 540, use_wikiskill: false,
    criteria_text: '笔触参与造型\n远景与近景浓淡分明'}, ['reference-one'], uuid);
  assert.equal(body.width, 960); assert.equal(body.height, 540);
  assert.equal(body.use_wikiskill, false); assert.deepEqual(body.reference_ids, ['reference-one']);
  assert.deepEqual(body.criteria, ['笔触参与造型', '远景与近景浓淡分明']);
  assert.equal(body.max_rounds, 3); assert.equal(body.max_seconds, 900);
  for (const field of ['duration_seconds', 'fps', 'reference_url', 'source']) assert.equal(Object.hasOwn(body, field), false);
  assert.throws(() => buildPictureProject(fields, [], uuid), /参考/);
  assert.throws(() => buildPictureProject({...fields, max_minutes: 61}, ['ref'], uuid), /时间/);
  assert.throws(() => buildPictureProject({...fields, width: 1921}, ['ref'], uuid), /宽度/);
  assert.throws(() => buildPictureProject({...fields, use_wikiskill: 'false'}, ['ref'], uuid), /明确/);
});

test('continue binds the selected reproduction; fresh drops old source selections and retry preserves original inputs', () => {
  const common = {...fields, feedback: '石面皴擦需要更有转折', base_candidate_id: 'candidate-one', use_wikiskill: false};
  assert.equal(buildPictureExperiment({...common, mode: 'continue'}, uuid).base_candidate_id, 'candidate-one');
  const fresh = buildPictureExperiment({...common, mode: 'fresh', source: {files: []}}, uuid);
  assert.equal(fresh.use_wikiskill, false); assert.equal(Object.hasOwn(fresh, 'base_candidate_id'), false);
  assert.equal(Object.hasOwn(fresh, 'source'), false);
  assert.deepEqual(buildPictureExperiment({...common, mode: 'retry', max_rounds: 8, max_minutes: 60}, uuid), {request_id: uuid, mode: 'retry'});
  assert.throws(() => buildPictureExperiment({mode: 'continue'}, uuid), /改善/);
});

test('the upload contains the exact selected PNG bytes, without replacing them with textual descriptions or a URL', async () => {
  const bytes = Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const file = {name: 'reference.png', type: 'image/png', size: bytes.length, arrayBuffer: async () => bytes.buffer};
  const body = await referenceBody(file, uuid);
  assert.deepEqual([...Buffer.from(body.content_base64, 'base64')], [...bytes]);
  assert.equal(body.mime_type, 'image/png'); assert.equal(body.file_name, 'reference.png');
  assert.throws(() => validateReferenceFile({...file, type: 'image/svg+xml'}), /PNG/);
  assert.throws(() => validateReferenceFile({...file, size: 8 * 1024 * 1024 + 1}), /8 MB/);
  await assert.rejects(() => referenceBody({...file, size: 9}, uuid), /变化/);
});

test('all image and source URLs stay in the authenticated picture API, including executor-root URLs', () => {
  assert.equal(pictureAssetUrl('/api/picture-lab/references/ref/media', root), root.href + 'api/picture-lab/references/ref/media');
  for (const value of ['https://evil.example/image.png', 'javascript:alert(1)', 'data:image/png;base64,AA',
    '/api/method-lab/catalog', 'api/picture-lab/%2e%2e/secret', 'api/picture-lab/a%2fb',
    'api/picture-lab/catalog?run=true', 'api/picture-lab/catalog#part']) assert.equal(pictureAssetUrl(value, root), null, value);
});

test('opening and polling an active picture project only read state; disabled availability never starts a task', async () => {
  const running = project({state: 'running', experiments: [{id: 'exp', state: 'running'}]});
  const h = harness(path => path.endsWith('catalog') ? catalog([running]) : running);
  await h.controller.refresh(); await h.controller.open(running.id);
  const timer = [...h.timers.entries()][0]; h.timers.delete(timer[0]); await timer[1]();
  assert.ok(h.requests.length >= 4); assert.ok(h.requests.every(row => !row.request.method));
  h.controller.destroy(); assert.equal(h.timers.size, 0);
  const disabled = harness(() => catalog([], {availability: {enabled: false, reason: '尚未准入'}}));
  await disabled.controller.refresh(); assert.equal(await disabled.controller.start(fields, null), false);
  assert.equal(disabled.requests.length, 1);
});

test('new project submission uploads once and starts one immutable paid request; a successful detail response needs no further execution', async () => {
  const bytes = new Uint8Array([1, 2, 3]);
  const h = harness((path, request) => path.endsWith('references') ? {id: 'ref-1'}
    : request.method === 'POST' ? project({state: 'queued', experiments: [{id: 'exp-1', state: 'queued'}]}) : catalog());
  await h.controller.refresh();
  assert.equal(await h.controller.start(fields, {name: 'ref.png', type: 'image/png', size: 3, arrayBuffer: async () => bytes.buffer}), true);
  assert.deepEqual(h.requests.map(row => row.path), ['api/picture-lab/catalog', 'api/picture-lab/references', 'api/picture-lab/projects']);
  assert.equal(JSON.parse(h.requests.at(-1).request.body).request_id, uuid); assert.equal(h.controller.state.pending, null);
  assert.equal(h.controller.state.detail.id, 'picture-project-1');
});

test('unknown submission persists across reload and explicit replay sends exactly the same key and frozen content', async () => {
  const storage = memoryStorage(), original = buildPictureProject(fields, ['ref'], uuid);
  const first = harness((_path, request) => { if (request.method) throw Error('网络中断'); return catalog(); }, {storage});
  await first.controller.refresh(); assert.equal(await first.controller.send('api/picture-lab/projects', original), false);
  original.goal = 'mutated outside';
  const second = harness((path, request) => request.method ? project() : catalog(), {storage, uuid: () => '00000000-0000-4000-8000-000000000099'});
  await second.controller.refresh(); assert.ok(second.controller.state.pending); assert.equal(await second.controller.replayPending(), true);
  const replay = JSON.parse(second.requests[1].request.body);
  assert.equal(replay.request_id, uuid); assert.equal(replay.goal, fields.goal); assert.equal(second.controller.state.pending, null);
});

test('catalog request identity can reconcile a lost new-project response without a second POST', async () => {
  const h = harness(() => catalog([project({request_id: uuid})]));
  await h.controller.refresh();
  h.controller.setPending({path: 'api/picture-lab/projects', body: buildPictureProject(fields, ['ref'], uuid)});
  await h.controller.refresh(); assert.equal(h.controller.state.pending, null);
  assert.equal(h.controller.state.detail.id, 'picture-project-1'); assert.equal(h.requests.length, 2);
  assert.equal(h.requests[0].request.method, undefined);
});

test('running experiments reject next feedback and adoption; stopping is an explicit request without new generation', async () => {
  const running = project({state: 'running', experiments: [{id: 'exp', state: 'running'}]});
  const h = harness((path, request) => request.method ? project({state: 'stopping', experiments: [{id: 'exp', state: 'stopping'}]}) : path.endsWith('catalog') ? catalog() : running);
  await h.controller.refresh(); await h.controller.open(running.id);
  assert.equal(await h.controller.experiment({mode: 'continue', feedback: '修改'}), false);
  assert.equal(await h.controller.adopt('candidate-1'), false);
  assert.equal(await h.controller.stop(), true); assert.equal(h.requests.length, 3);
  assert.equal(h.requests[2].path, 'api/picture-lab/projects/picture-project-1/stop');
  assert.deepEqual(JSON.parse(h.requests[2].request.body), {});
});

test('reproduction and transfer stay inside one project; adopted output wins over a newer internal best', () => {
  const one = candidate('one'), two = candidate('two'), transfer = candidate('transfer', {work_kind: 'transfer'});
  const p = project({candidates: [one, two, transfer], best_candidate_id: 'two', adopted_candidate_id: 'one'});
  assert.equal(pictureCandidates(p).length, 3); assert.equal(pictureDefaultCandidate(p).id, 'one');
  assert.equal(pictureDefaultCandidate(p, 'transfer').id, 'transfer'); assert.equal(pictureCandidateRole(transfer), '换内容验证作品');
  assert.equal(pictureIsActive(p), false);
});

test('detail compares real reference and result, provides source and receipt, and never exposes edit controls while running', () => {
  const p = project({candidates: [candidate('one')], best_candidate_id: 'one'});
  const detail = renderPictureDetail(doc, p, {root});
  const images = all(detail).filter(row => row.tagName === 'IMG'); assert.equal(images.length, 2);
  assert.match(images[0].src, /references\/ref-1\/media$/); assert.match(images[1].src, /artifacts\/one-png$/);
  assert.ok(all(detail).some(row => row.tagName === 'A' && row.textContent === '下载绘制源码' && row.href.endsWith('one-source')));
  assert.ok(all(detail).some(row => row.tagName === 'A' && row.textContent === '下载渲染回执' && row.href.endsWith('one-receipt')));
  assert.match(detail.textContent, /连续运动与声音尚未验证/); assert.match(detail.textContent, /换内容验证尚无产物/);
  const active = renderPictureDetail(doc, {...p, experiments: [{id: 'exp', state: 'running'}]}, {root});
  const labels = buttons(active).map(row => row.textContent);
  assert.ok(labels.includes('停止本次实验')); assert.equal(labels.includes('继续优化'), false);
  assert.equal(labels.includes('从零重做'), false); assert.equal(labels.includes('采用为默认作品'), false);
});

test('source-only failed candidates keep their download and retry but cannot continue or become the adopted image', async () => {
  const sourceOnly = candidate('source-only', {picture_url: null, receipt_url: null});
  const p = project({state: 'failed', candidates: [sourceOnly],
    experiments: [{id: 'exp', state: 'failed'}]});
  const detail = renderPictureDetail(doc, p, {root});
  const labels = buttons(detail).map(row => row.textContent);
  assert.ok(labels.includes('技术重试')); assert.ok(labels.includes('从零重做'));
  assert.equal(labels.includes('继续优化'), false); assert.equal(labels.includes('采用为默认作品'), false);
  assert.match(detail.textContent, /图片未完成/); assert.match(detail.textContent, /绘制源码已保存/);
  assert.ok(all(detail).some(row => row.tagName === 'A' && row.textContent === '下载绘制源码'));
  assert.equal(all(detail).filter(row => row.tagName === 'IMG').length, 1);
  const h = harness(path => path.endsWith('catalog') ? catalog() : p);
  await h.controller.refresh(); await h.controller.open(p.id);
  assert.equal(await h.controller.experiment({mode: 'continue', feedback: '继续', base_candidate_id: sourceOnly.id}), false);
  assert.equal(await h.controller.adopt(sourceOnly.id), false);
  assert.equal(h.requests.some(row => row.request.method), false);
});

test('transfer validation images cannot be continued or adopted as reproduction baselines', async () => {
  const transfer = candidate('transfer', {work_kind: 'transfer'}), p = project({candidates: [transfer]});
  const detail = renderPictureDetail(doc, p, {root});
  assert.equal(buttons(detail).some(row => ['继续优化', '采用为默认作品'].includes(row.textContent)), false);
  const h = harness(path => path.endsWith('catalog') ? catalog() : p);
  await h.controller.refresh(); await h.controller.open(p.id);
  assert.equal(await h.controller.experiment({mode: 'continue', feedback: '继续', base_candidate_id: transfer.id}), false);
  assert.equal(await h.controller.adopt(transfer.id), false);
  assert.equal(h.requests.some(row => row.request.method), false);
});

test('untrusted titles, reviews and goals stay text; malicious artifact URLs do not become images or anchors', () => {
  const p = project({title: '<img src=x onerror=alert(1)>', goal: '<script>evil()</script>',
    candidates: [candidate('one', {picture_url: 'https://evil.example/image.png', source_url: 'javascript:evil()', review: {summary: '<svg onload=evil()>'}})]});
  const detail = renderPictureDetail(doc, p, {root});
  assert.equal(all(detail).filter(row => row.tagName === 'IMG').length, 1); assert.match(detail.textContent, /<svg onload/);
  assert.equal(all(detail).some(row => /evil\.example|javascript:/.test(row.href ?? '')), false);
  const card = renderPictureCard(doc, p, {root, onOpen: () => {}}); assert.match(card.textContent, /<img src=x/);
});

test('exact role input inspection is lazy and read-only', async () => {
  const inspected = [], detail = renderPictureDetail(doc, project(), {root, onInspect: async id => { inspected.push(id); return {input: {goal: 'actual goal'}}; }});
  assert.equal(inspected.length, 0);
  const section = all(detail).find(row => row.tagName === 'DETAILS' && row.children[0]?.textContent === '查看实际 Agent 输入');
  section.open = true; await section.handlers.toggle(); assert.deepEqual(inspected, ['exp-1']);
  assert.match(section.textContent, /actual goal/); await section.handlers.toggle(); assert.equal(inspected.length, 1);
});

test('navigation and the documented JSON contract expose an independent image library without pretending video learning is implemented', () => {
  const base = new URL('../../video_workbench/animation_lab_web/', import.meta.url);
  for (const name of ['method-gallery.html', 'knowledge.html', 'picture-library.html']) {
    const html = readFileSync(new URL(name, base), 'utf8'); assert.match(html, /href="\.\/picture-library\.html"/);
    assert.match(html, /workspace-nav/); assert.equal(/video-library\.html/.test(html), false);
  }
  const contract = JSON.parse(readFileSync(new URL('picture-api.openapi.json', base), 'utf8'));
  assert.equal(contract.servers[0].url, root.href + 'api/picture-lab');
  for (const path of ['/catalog', '/references', '/projects', '/projects/{project_id}/experiments', '/projects/{project_id}/stop', '/projects/{project_id}/adopt']) assert.ok(contract.paths[path]);
  assert.equal(contract.components.schemas.ProjectStart.properties.use_wikiskill.default, true);
  assert.deepEqual(contract.components.schemas.ExperimentStart.allOf[0].then.not.anyOf.map(row => row.required[0]),
    ['feedback', 'base_candidate_id', 'use_wikiskill', 'max_rounds', 'max_seconds']);
});

test('a failed catalogue read clears write readiness and stale CSRF cannot submit another operation', async () => {
  let reads = 0;
  const h = harness((_path, request) => {
    assert.equal(request.method, undefined);
    if (++reads === 1) return catalog();
    const failure = Error('expired'); failure.status = 403; throw failure;
  });
  await h.controller.refresh(); assert.equal(h.controller.enabled(), true);
  await h.controller.refresh(); assert.equal(h.controller.enabled(), false);
  assert.equal(h.controller.state.catalog.csrf_token, '');
  assert.equal(await h.controller.send('api/picture-lab/projects', buildPictureProject(fields, ['ref'], uuid)), false);
  assert.equal(h.requests.length, 2);
});

test('pending operations remain isolated by authenticated owner across account changes', async () => {
  const store = memoryStorage(); let owner = 'first-owner';
  const h = harness((_path, request) => { if (request.method) throw Error('lost response'); return catalog([], {owner_key: owner}); }, {storage: store});
  await h.controller.refresh();
  await h.controller.send('api/picture-lab/projects', buildPictureProject(fields, ['ref'], uuid));
  assert.ok(h.controller.state.pending);
  owner = 'second-owner'; await h.controller.refresh();
  assert.equal(h.controller.state.pending, null); assert.equal(h.controller.state.detail, null);
  assert.equal(await h.controller.replayPending(), false);
  owner = 'first-owner'; await h.controller.refresh();
  assert.equal(h.controller.state.pending.owner_key, 'first-owner');
  assert.equal(h.requests.filter(row => row.request.method).length, 1);
});

test('a malformed success acknowledgement retains the original identity for reconciliation', async () => {
  const h = harness((_path, request) => request.method ? {} : catalog());
  await h.controller.refresh();
  assert.equal(await h.controller.send('api/picture-lab/projects', buildPictureProject(fields, ['ref'], uuid)), false);
  assert.equal(h.controller.state.pending.body.request_id, uuid);
  assert.equal(await h.controller.start(fields, null), false);
  assert.equal(h.requests.filter(row => row.request.method).length, 1);
});

test('unwritable pending storage rejects a paid submission before POST', async () => {
  const h = harness(() => catalog(), {storage: {getItem: () => null, setItem: () => { throw Error('quota'); }, removeItem: () => {}}});
  await h.controller.refresh();
  await assert.rejects(() => h.controller.send('api/picture-lab/projects', buildPictureProject(fields, ['ref'], uuid)), /存储空间/);
  assert.equal(h.requests.filter(row => row.request.method).length, 0);
});

test('late detail reads never replace the project most recently opened', async () => {
  let resolveOld;
  const waiting = new Promise(resolve => { resolveOld = resolve; });
  const h = harness(path => path.endsWith('/old') ? waiting : path.endsWith('catalog') ? catalog() : project({id: 'new'}));
  await h.controller.refresh(); const older = h.controller.open('old'); await h.controller.open('new');
  resolveOld(project({id: 'old'})); await older;
  assert.equal(h.controller.state.detail.id, 'new');
});

test('a lost stop response is reconciled from terminal state without repeated stop POSTs', async () => {
  let stopped = false;
  const h = harness((_path, request) => {
    if (request.method) { stopped = true; throw Error('response lost'); }
    return catalog([project({state: stopped ? 'cancelled' : 'running', experiments: []})]);
  });
  await h.controller.refresh();
  await h.controller.send('api/picture-lab/projects/picture-project-1/stop', {});
  assert.ok(h.controller.state.pending); await h.controller.refresh();
  assert.equal(h.controller.state.pending, null);
  assert.equal(h.requests.filter(row => row.request.method).length, 1);
});
