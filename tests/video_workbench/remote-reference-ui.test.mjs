import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {remoteReferenceBody, validRemoteReferenceBody, referenceDescription} from '../../video_workbench/animation_lab_web/remote-reference.mjs';
import {PictureLibraryController, renderPictureDetail} from '../../video_workbench/animation_lab_web/picture-library.mjs';
import {VideoLibraryController, renderVideoDetail, renderVideoAnalysisDetail} from '../../video_workbench/animation_lab_web/video-library.mjs';

const root = new URL('https://video-service.panghuer.top/dev/animations/');
const source = 'https://www.bilibili.com/video/BV1BxWu6SESe/';
const pictureFields = {title: '山石皴擦', goal: '学习笔触与留白', reference_source: 'video-url', source_url: source,
  timestamp_seconds: 24.5, crop: {x: .1, y: .2, width: .6, height: .7}, use_wikiskill: false};
const videoFields = {title: '水面与鱼群', goal: '学习亮纹和姿态变化', reference_source: 'video-url', source_url: source,
  start_seconds: 35, end_seconds: 43, use_wikiskill: false};
const project = extra => ({id: 'project-one', title: '实际学习项目', goal: '学习画面', references: [], candidates: [],
  experiments: [{id: 'exp', state: 'completed', frozen: {start_seconds: 35, end_seconds: 43}}], ...extra});
const catalogue = extra => ({availability: {enabled: true}, owner_key: 'owner-one', csrf_token: 'csrf', projects: [], references: [], ...extra});
function storage() {
  const data = new Map(); return {getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key)};
}
function harness(Controller, handler, saved = storage()) {
  let sequence = 0; const requests = [];
  const controller = new Controller({root, storage: saved, uuid: () => '00000000-0000-4000-8000-' + String(++sequence).padStart(12, '0'), schedule: () => 1, unschedule: () => {},
    request: async (path, options = {}) => {requests.push({path, ...options}); return handler(path, options);}});
  return {controller, requests};
}

test('remote image reference captures one exact source time and normalized crop without video bytes', () => {
  const body = remoteReferenceBody(pictureFields, 'request-one', {picture: true});
  assert.deepEqual(body, {request_id: 'request-one', source_type: 'video-url', source_url: source,
    timestamp_seconds: 24.5, crop: pictureFields.crop});
  assert.equal(validRemoteReferenceBody({...body, crop: {height: .7, width: .6, y: .2, x: .1}}, {picture: true}), true);
  assert.equal(validRemoteReferenceBody({...body, content_base64: 'AA=='}, {picture: true}), false);
  for (const values of [{source_url: 'http://example.com/a'}, {source_url: 'https://user:secret@example.com/a'},
    {timestamp_seconds: ''}, {timestamp_seconds: -1}, {crop: {x: .8, y: 0, width: .4, height: 1}}, {crop: {x: 0, y: 0, width: 1, height: 0}}]) {
    assert.throws(() => remoteReferenceBody({...pictureFields, ...values}, 'request-one', {picture: true}));
  }
  assert.equal(Object.hasOwn(remoteReferenceBody(videoFields, 'video-request'), 'timestamp_seconds'), false);
});

test('picture learning registers a link then commissions exactly one project without reading a file', async () => {
  const h = harness(PictureLibraryController, (path, options) => !options.method ? catalogue()
    : path.endsWith('references') ? {id: 'remote-one'} : project());
  await h.controller.refresh();
  assert.equal(await h.controller.start(pictureFields, {arrayBuffer() {throw Error('file must not be read');}}), true);
  const posts = h.requests.filter(row => row.method);
  assert.deepEqual(posts.map(row => row.path), ['api/picture-lab/references', 'api/picture-lab/projects']);
  const reference = JSON.parse(posts[0].body), creation = JSON.parse(posts[1].body);
  assert.equal(reference.source_url, source); assert.equal(reference.timestamp_seconds, 24.5);
  assert.equal(reference.content_base64, undefined); assert.deepEqual(creation.reference_ids, ['remote-one']);
  assert.equal(creation.use_wikiskill, false); assert.equal(h.controller.state.pending, null);
});

test('a saved picture reference reuses its identity and performs no source registration or file access', async () => {
  const h = harness(PictureLibraryController, (_path, options) => options.method ? project() : catalogue());
  await h.controller.refresh();
  assert.equal(await h.controller.start({...pictureFields, reference_source: 'saved', reference_id: 'existing-ref'}, null), true);
  assert.deepEqual(h.requests.filter(row => row.method).map(row => row.path), ['api/picture-lab/projects']);
  assert.deepEqual(JSON.parse(h.requests.at(-1).body).reference_ids, ['existing-ref']);
});

test('a lost remote reference response survives refresh and replays the exact original source, crop and commission', async () => {
  const saved = storage(), mutable = structuredClone(pictureFields);
  const first = harness(PictureLibraryController, (_path, options) => {if (options.method) throw Error('connection lost'); return catalogue();}, saved);
  await first.controller.refresh(); assert.equal(await first.controller.start(mutable, null), false);
  const original = structuredClone(first.controller.state.pending);
  mutable.source_url = 'https://example.com/different.mp4'; mutable.crop.width = .1; mutable.goal = 'changed';
  const second = harness(PictureLibraryController, (path, options) => !options.method ? catalogue()
    : path.endsWith('references') ? {id: 'remote-one'} : project(), saved);
  await second.controller.refresh();
  assert.equal(second.requests.filter(row => row.method).length, 0);
  assert.deepEqual(second.controller.state.pending, original);
  assert.equal(await second.controller.replayPending(), true);
  const posts = second.requests.filter(row => row.method);
  assert.deepEqual(JSON.parse(posts[0].body), original.body);
  assert.equal(JSON.parse(posts[1].body).goal, pictureFields.goal);
  assert.equal(JSON.parse(posts[1].body).request_id, original.continuation.body.request_id);
});

test('remote video analysis registers one source without commissioning a video; subsequent creation reuses it', async () => {
  const h = harness(VideoLibraryController, (path, options) => !options.method ? catalogue()
    : path.endsWith('references') ? {id: 'remote-one'} : path.endsWith('analyses') ? {id: 'analysis-one', reference_id: 'remote-one', state: 'queued'} : project());
  await h.controller.refresh();
  assert.equal(await h.controller.analyze({...videoFields, analysis_minutes: 10}, null), true);
  assert.equal(h.requests.filter(row => row.method && row.path.endsWith('projects')).length, 0);
  assert.equal(JSON.parse(h.requests.find(row => row.method && row.path.endsWith('references')).body).content_base64, undefined);
  assert.equal(await h.controller.start({...videoFields, reference_source: 'saved', reference_id: 'remote-one'}, null), true);
  assert.equal(h.requests.filter(row => row.method && row.path.endsWith('references')).length, 1);
  assert.equal(h.requests.filter(row => row.method && row.path.endsWith('projects')).length, 1);
});

test('source permission failures do not masquerade as an expired Video Service session or commission paid work', async () => {
  for (const [Controller, fields] of [[PictureLibraryController, pictureFields], [VideoLibraryController, videoFields]]) {
    const h = harness(Controller, (_path, options) => {
      if (!options.method) return catalogue();
      throw Object.assign(Error('source denied'), {status: 403, code: 'remote_reference_access_required'});
    });
    await h.controller.refresh(); assert.equal(await h.controller.start(fields, null), false);
    assert.match(h.controller.state.message, /源站要求访问权限/);
    assert.equal(h.requests.some(row => row.method && row.path.endsWith('projects')), false);
    assert.equal(h.controller.state.pending, null);
  }
});

class Element {
  constructor(tag) {this.tagName = tag.toUpperCase(); this.children = []; this.handlers = {}; this.attributes = {}; this._text = '';}
  set textContent(value) {this._text = String(value); this.children = [];}
  get textContent() {return this._text + this.children.map(child => child.textContent).join('');}
  append(...children) {this.children.push(...children);}
  setAttribute(key, value) {this.attributes[key] = value;}
  addEventListener(key, fn) {this.handlers[key] = fn;}
  querySelectorAll(tag) {return all(this).filter(child => child !== this && child.tagName === tag.toUpperCase());}
  querySelector(tag) {return this.querySelectorAll(tag)[0] ?? null;}
}
const all = node => [node, ...node.children.flatMap(all)];
const doc = {createElement: tag => new Element(tag), createDocumentFragment: () => new Element('fragment')};
const remote = {id: 'remote-one', source_type: 'video-url', source_url: source, width: 1920, height: 1080,
  duration_seconds: 60, media_url: null, url: null, timestamp_seconds: 24.5, observation_status: 'registered_not_observed'};

test('registered references display the source and unobserved state without making a media URL or video player', () => {
  const picture = renderPictureDetail(doc, project({references: [remote]}), {root});
  assert.match(picture.textContent, /尚未读取参考画面/);
  assert.equal(all(picture).filter(row => row.tagName === 'IMG').length, 0);
  assert.ok(all(picture).some(row => row.tagName === 'A' && row.href === source));
  const video = renderVideoDetail(doc, project({references: [remote], candidates: [{id: 'work', work_kind: 'reproduction',
    media_url: '/api/video-lab/projects/project-one/artifacts/work'}]}), {root});
  const players = all(video).filter(row => row.tagName === 'VIDEO');
  assert.equal(players.length, 1); assert.ok(players[0].src.endsWith('/artifacts/work'));
  assert.equal(all(video).some(row => row.src === source || /references\/remote-one\/media/.test(row.src ?? '')), false);
  assert.equal(all(video).some(row => row.tagName === 'BUTTON' && row.textContent === '同时播放画面对照'), false);
  const analysis = renderVideoAnalysisDetail(doc, {id: 'analysis', state: 'completed', goal: '学习', candidate_segments: []}, {root, reference: remote});
  assert.equal(all(analysis).filter(row => row.tagName === 'VIDEO').length, 0);
});

test('only an actual frozen frame URL is loaded and observed dimensions remain distinct from provider metadata', () => {
  const observed = {...remote, observation_status: 'sampled_frames_only', actual_pts: 24.52, observed_width: 1280, observed_height: 720,
    frame_url: '/api/picture-lab/projects/project-one/artifacts/reference-' + 'a'.repeat(64)};
  const view = renderPictureDetail(doc, project({references: [observed]}), {root});
  const images = all(view).filter(row => row.tagName === 'IMG'); assert.equal(images.length, 1);
  assert.ok(images[0].src.endsWith('/artifacts/reference-' + 'a'.repeat(64)));
  assert.match(view.textContent, /实际观察画面：1280×720/); assert.match(view.textContent, /实际帧时间：24.52 秒/);
  assert.match(referenceDescription(remote), /源站可用画面：1920×1080/);
  const bad = renderPictureDetail(doc, project({references: [{...observed, frame_url: 'https://evil.example/frame.jpg'}]}), {root});
  assert.equal(all(bad).some(row => row.src === 'https://evil.example/frame.jpg'), false);
});

test('reference analysis displays its own read-only observed frame instead of the unobserved source catalogue row', () => {
  const reference_observation = {...remote, observation_status: 'sampled_frames_only', actual_pts: 8,
    observed_width: 640, observed_height: 360, frame_url: '/api/video-lab/references/remote-one/analyses/analysis-one/frames/' + 'a'.repeat(64)};
  const view = renderVideoAnalysisDetail(doc, {id: 'analysis-one', state: 'completed', goal: '学习', candidate_segments: [], reference_observation}, {root, reference: remote});
  assert.match(view.textContent, /实际观察画面：640×360/);
  assert.equal(view.textContent.includes('尚未读取参考画面'), false);
  const images = all(view).filter(row => row.tagName === 'IMG'); assert.equal(images.length, 1);
  assert.ok(images[0].src.endsWith('/analyses/analysis-one/frames/' + 'a'.repeat(64)));
});

test('both API contracts expose distinct upload and remote source inputs, with picture-only frame selection', () => {
  for (const kind of ['picture', 'video']) {
    const api = JSON.parse(readFileSync(new URL('../../video_workbench/animation_lab_web/' + kind + '-api.openapi.json', import.meta.url), 'utf8'));
    assert.equal(api.paths['/references'].post.requestBody.content['application/json'].schema.$ref, '#/components/schemas/ReferenceInput');
    assert.equal(api.components.schemas.ReferenceInput.oneOf.length, 2);
    const remoteSchema = api.components.schemas.RemoteVideoReference;
    assert.equal(remoteSchema.properties.source_type.const, 'video-url');
    assert.equal(remoteSchema.required.includes('timestamp_seconds'), kind === 'picture');
    assert.equal(Object.hasOwn(remoteSchema.properties, 'crop'), kind === 'picture');
    assert.equal(api.components.schemas.RemoteVideoReferenceRecord.required.includes('sha256'), false);
    assert.equal(api.components.schemas.RemoteVideoReferenceRecord.properties.source_identity_scope.const, 'provider_metadata');
  }
});
