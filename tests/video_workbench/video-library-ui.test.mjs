import test from 'node:test';
import assert from 'node:assert/strict';
import {buildVideoProject, buildVideoExperiment, videoAssetUrl, validateReferenceFile} from '../../video_workbench/animation_lab_web/video-model.mjs';
import {VideoLibraryController, analyzeVideoSelection, referenceBody, renderVideoDetail} from '../../video_workbench/animation_lab_web/video-library.mjs';

const root = new URL('https://video-service.panghuer.top/dev/animations/');
const uuid = '00000000-0000-4000-8000-000000000001';
const fields = {title: '群体涟漪', goal: '学习触发传播与收束', start_seconds: 21, end_seconds: 28, learn_audio: true};
const project = extra => ({id: 'video-one', title: fields.title, goal: fields.goal, references: [{id: 'ref', duration_seconds: 40}],
  experiments: [{id: 'exp', state: 'completed', frozen: {...fields}}], candidates: [], ...extra});
const catalog = projects => ({availability: {enabled: true}, csrf_token: 'csrf', owner_key: 'video-owner', projects: projects ?? []});
function harness(handler, storage) {
  const requests = [];
  const controller = new VideoLibraryController({root, uuid: () => uuid, storage,
    request: async (path, request = {}) => {requests.push({path, request}); return handler(path, request);},
    schedule: () => 1, unschedule: () => {}});
  return {controller, requests};
}
function memoryStorage() {const data = new Map(); return {getItem: k => data.get(k) ?? null, setItem: (k,v) => data.set(k,v), removeItem: k => data.delete(k)};}

test('video brief freezes an explicit clip and sound-learning intent, within the admitted rendering size', () => {
  const body = buildVideoProject({...fields, use_wikiskill: false}, ['ref'], uuid);
  assert.equal(body.start_seconds, 21); assert.equal(body.end_seconds, 28); assert.equal(body.learn_audio, true);
  assert.equal(body.use_wikiskill, false); assert.equal(body.width, 1280);
  for (const value of [{start_seconds: ''}, {end_seconds: Infinity}, {end_seconds: 21.5}, {end_seconds: 82}, {width: 1920}, {height: 1080}, {learn_audio: 'true'}]) {
    assert.throws(() => buildVideoProject({...fields, ...value}, ['ref'], uuid));
  }
  assert.throws(() => buildVideoProject(fields, ['a', 'b'], uuid));
  assert.deepEqual(buildVideoExperiment({mode: 'retry', feedback: 'new', max_minutes: 60}, uuid), {request_id: uuid, mode: 'retry'});
});

test('reference upload preserves the original bytes and rejects images and oversized media before submission', async () => {
  const bytes = new Uint8Array([0,0,0,24,102,116,121,112]);
  const file = {name:'ref.mp4', type:'video/mp4', size:bytes.length, arrayBuffer:async()=>bytes.buffer};
  const body = await referenceBody(file, uuid);
  assert.deepEqual(Buffer.from(body.content_base64,'base64'), Buffer.from(bytes));
  assert.throws(() => validateReferenceFile({...file, type:'image/png'}));
  assert.throws(() => validateReferenceFile({...file, size:24*1024*1024+1}));
});

test('video artifacts remain owner-authenticated API URLs', () => {
  assert.equal(videoAssetUrl('/api/video-lab/projects/a/artifacts/media', root), root.href+'api/video-lab/projects/a/artifacts/media');
  for (const value of ['https://evil.example/a.mp4', '/api/picture-lab/projects/a', 'api/video-lab/a%2fb', 'api/video-lab/projects?a=1', 'data:video/mp4;base64,AA']) assert.equal(videoAssetUrl(value, root), null);
});

test('a lost paid submission survives reload and replays the same clip, body and key', async () => {
  const storage = memoryStorage(), original = buildVideoProject(fields, ['ref'], uuid);
  const a = harness((_p,r) => {if(r.method)throw Error('lost'); return catalog();}, storage);
  await a.controller.refresh(); await a.controller.send('api/video-lab/projects', original);
  original.start_seconds = 0;
  const b = harness((_p,r) => r.method ? project() : catalog(), storage);
  await b.controller.refresh(); assert.equal(b.requests.filter(x=>x.request.method).length, 0);
  assert.equal(await b.controller.replayPending(), true);
  const replay = JSON.parse(b.requests.find(x=>x.request.method).request.body);
  assert.equal(replay.start_seconds,21); assert.equal(replay.request_id,uuid);
});

test('source-only and active candidates cannot be adopted or used to continue', async () => {
  const p = project({candidates:[{id:'source-only', work_kind:'reproduction', source_url:'/api/video-lab/projects/video-one/artifacts/source'}]});
  const h = harness(path => path.endsWith('catalog') ? catalog([p]) : p);
  await h.controller.refresh(); await h.controller.open(p.id);
  assert.equal(await h.controller.adopt('source-only'), false);
  assert.equal(await h.controller.experiment({mode:'continue', feedback:'改善节奏'}), false);
  p.experiments[0].state = 'running';
  assert.equal(await h.controller.experiment({mode:'fresh'}), false);
  assert.ok(h.requests.every(x=>!x.request.method));
});

test('candidate analysis uploads once without creating a project; explicit range creation reuses that reference', async () => {
  const h = harness((path, r) => path.endsWith('catalog') ? catalog()
    : path.endsWith('references') ? {id:'ref'} : path.endsWith('analyses') ? {id:'analysis-one',reference_id:'ref',state:'queued'} : project());
  await h.controller.refresh();
  const bytes=new Uint8Array([1,2,3]);
  await h.controller.analyze({goal:fields.goal}, {name:'ref.mp4',type:'video/mp4',size:3,arrayBuffer:async()=>bytes.buffer});
  assert.equal(h.requests.filter(x=>x.request.method&&x.path.endsWith('projects')).length,0);
  assert.ok(h.requests.some(x=>x.path==='api/video-lab/references/ref/analyses'));
  await h.controller.start({...fields,reference_id:'ref'},null);
  const posts=h.requests.filter(x=>x.request.method);
  assert.equal(posts.filter(x=>x.path.endsWith('references')).length,1);
  assert.equal(posts.filter(x=>x.path.endsWith('projects')).length,1);
});

test('an unknown analysis submission reconciles its original key from catalog without a second paid request', async () => {
  const storage=memoryStorage(), h=harness((p,r)=>{if(r.method)throw Error('lost');return catalog();},storage);
  await h.controller.refresh();await h.controller.analyze({reference_id:'ref',goal:fields.goal},null);
  const reload=harness(()=>({...catalog(),analyses:[{id:'analysis-one',reference_id:'ref',request_id:uuid,state:'running'}]}),storage);
  await reload.controller.refresh(); assert.equal(reload.controller.state.pending,null);
  assert.ok(reload.requests.every(x=>!x.request.method));
});

test('switching reference after analysis A cannot commission A when the new B analysis is rejected', async () => {
  let uploads = 0, selectedReference = '';
  const h = harness((path, request) => {
    if (path.endsWith('catalog')) return catalog();
    if (path.endsWith('references')) {
      if (++uploads === 1) throw Object.assign(Error('B upload rejected'), {status: 422});
      return {id: 'reference-B'};
    }
    if (path.endsWith('analyses')) return {id: 'analysis-A', reference_id: 'reference-A', state: 'completed'};
    return project();
  });
  await h.controller.refresh();
  const applyReference = id => {selectedReference = id;};
  assert.equal(await analyzeVideoSelection(h.controller, {reference_id: 'reference-A'}, null, applyReference), true);
  assert.equal(selectedReference, 'reference-A');
  // The file-change handler clears both the selected reference and its old analysis.
  selectedReference = ''; h.controller.clearAnalysis();
  const bytes = new Uint8Array([1, 2, 3]);
  const file = {name: 'B.mp4', type: 'video/mp4', size: bytes.length, arrayBuffer: async () => bytes.buffer};
  assert.equal(await analyzeVideoSelection(h.controller, {}, file, applyReference), false);
  assert.equal(selectedReference, ''); assert.equal(h.controller.state.analysis, null);
  assert.equal(h.controller.state.pending, null);
  assert.equal(await h.controller.start({...fields, reference_id: selectedReference}, file), true);
  const creation = h.requests.find(row => row.path.endsWith('projects') && row.request.method === 'POST');
  assert.deepEqual(JSON.parse(creation.request.body).reference_ids, ['reference-B']);
});

test('a lost analysis stop response is cleared by read-only proof of its terminal state', async () => {
  const storage=memoryStorage(), h=harness((_p,r)=>{if(r.method)throw Error('lost');return catalog();},storage);
  await h.controller.refresh(); await h.controller.stopAnalysis({id:'analysis-one',reference_id:'ref'});
  assert.ok(h.controller.state.pending);
  const next=harness(()=>({...catalog(),analyses:[{id:'analysis-one',reference_id:'ref',state:'cancelled'}]}),storage);
  await next.controller.refresh();assert.equal(next.controller.state.pending,null);
  assert.ok(next.requests.every(x=>!x.request.method));
});

class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.handlers={};this.attributes={};this.currentTime=0;this.paused=true;this._text='';}
  set textContent(value){this._text=String(value);this.children=[];} get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}
  append(...items){this.children.push(...items);} setAttribute(k,v){this.attributes[k]=v;}
  addEventListener(k,v){this.handlers[k]=v;} querySelector(tag){return all(this).find(x=>x!==this&&x.tagName===tag.toUpperCase())??null;}
  pause(){this.paused=true;} play(){this.paused=false;return Promise.resolve();}
}
const all = node => [node,...node.children.flatMap(all)];
const doc = {createElement:tag=>new Element(tag),createDocumentFragment:()=>new Element('fragment')};

test('comparison uses actual video players and aligns reference original time with generated clip time', async () => {
  const p=project({candidates:[{id:'work',work_kind:'reproduction',media_url:'/api/video-lab/projects/video-one/artifacts/media'}]});
  const view=renderVideoDetail(doc,p,{root});
  const players=all(view).filter(x=>x.tagName==='VIDEO'); assert.equal(players.length,2);
  players[0].handlers.loadedmetadata(); assert.equal(players[0].currentTime,21);
  const timeline=all(view).find(x=>x.tagName==='INPUT'&&x.type==='range');timeline.value='2.5';timeline.handlers.input();
  assert.equal(players[0].currentTime,23.5);assert.equal(players[1].currentTime,2.5);
  const play=all(view).find(x=>x.tagName==='BUTTON'&&x.textContent==='同时播放画面对照');await play.handlers.click();
  assert.equal(players[0].muted,true);assert.equal(players[1].muted,true);
  players[1].currentTime=7;players[1].handlers.timeupdate();assert.equal(players[0].paused,true);
  assert.match(view.textContent,/不能由静帧补签/);
});
