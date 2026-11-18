import test from 'node:test';
import assert from 'node:assert/strict';
import {buildVideoProject, buildVideoExperiment, videoAssetUrl, validateReferenceFile} from '../../video_workbench/animation_lab_web/video-model.mjs';
import {VideoLibraryController, analyzeVideoSelection, referenceBody, renderVideoDetail, updateVideoDetail} from '../../video_workbench/animation_lab_web/video-library.mjs';

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
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.handlers={};this.listeners={};this.attributes={};this.currentTime=0;this.paused=true;this.muted=false;this.ended=false;this.readyState=0;this.duration=NaN;this.playCalls=0;this.replacements=0;this._text='';}
  set textContent(value){this._text=String(value);this.children=[];} get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}
  append(...items){this.children.push(...items);} setAttribute(k,v){this.attributes[k]=v;} getAttribute(k){return this.attributes[k]??null;}
  replaceChildren(...items){this.children=[];this.append(...items);this.replacements++;}
  addEventListener(k,fn,options={}){
    (this.listeners[k]??=[]).push({fn,once:options.once});
    this.handlers[k]=(...args)=>Promise.all([...this.listeners[k]].map(row=>{
      if(row.once)this.listeners[k]=this.listeners[k].filter(x=>x!==row);
      return row.fn(...args);
    }));
  }
  querySelectorAll(tag){return all(this).filter(x=>x!==this&&x.tagName===tag.toUpperCase());}
  querySelector(tag){return this.querySelectorAll(tag)[0]??null;}
  pause(){this.paused=true;} play(){this.paused=false;this.playCalls++;return Promise.resolve();}
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

const displayedProject = extra => project({candidates:[{id:'work',work_kind:'reproduction',media_url:'/api/video-lab/projects/video-one/artifacts/media'}],...extra});
const detailsNamed = (host, name) => host.querySelectorAll('details').find(x=>x.querySelector('summary')?.textContent.startsWith(name));
const options = extra => ({root,ownerKey:'owner-A',selection:'work',locked:false,...extra});

test('timestamp polling keeps the exact live DOM, expanded input and player without another input read', async () => {
  const host=new Element('section'), p=displayedProject(); let reads=0;
  const settings=options({onInspect:async id=>{reads++;return {experiment_id:id,role:'author'};}});
  updateVideoDetail(doc,host,p,settings);
  const history=detailsNamed(host,'实验历史'), inspect=detailsNamed(host,'查看实际 Agent 输入'), player=host.querySelectorAll('video')[1];
  assert.equal(reads,0); history.open=true; inspect.open=true; await inspect.handlers.toggle();
  player.currentTime=3.5; await player.play();
  const poll=structuredClone(p);poll.updated_at=500;poll.experiments[0].updated_at=500;
  assert.equal(updateVideoDetail(doc,host,poll,settings),false);
  assert.equal(host.replacements,1);assert.equal(host.querySelectorAll('video')[1],player);
  assert.equal(detailsNamed(host,'查看实际 Agent 输入'),inspect);assert.equal(inspect.open,true);assert.equal(history.open,true);
  assert.equal(player.currentTime,3.5);assert.equal(player.paused,false);assert.equal(reads,1);
  assert.match(inspect.textContent,/author/);
});

test('a real stage/history update preserves keyed disclosure, playback intent and comparison position', async () => {
  const host=new Element('section'),p=displayedProject(),settings=options({onInspect:async()=>({role:'review'})});
  updateVideoDetail(doc,host,p,settings);
  detailsNamed(host,'实验历史').open=true;detailsNamed(host,'查看实际 Agent 输入').open=true;
  const oldPlayers=host.querySelectorAll('video');
  oldPlayers[0].currentTime=24.25;oldPlayers[0].muted=true;await oldPlayers[0].play();
  oldPlayers[1].currentTime=3.25;oldPlayers[1].muted=false;await oldPlayers[1].play();
  const timeline=host.querySelectorAll('input').find(x=>x.type==='range');timeline.value='3.25';
  const next=structuredClone(p);next.experiments[0].state='running';next.experiments.push({id:'exp-two',state:'queued',frozen:{...fields}});
  assert.equal(updateVideoDetail(doc,host,next,settings),true);
  assert.equal(detailsNamed(host,'实验历史').open,true);assert.match(detailsNamed(host,'实验历史').querySelector('summary').textContent,/（2）/);
  assert.equal(detailsNamed(host,'查看实际 Agent 输入').open,true);
  assert.equal(host.querySelectorAll('input').find(x=>x.type==='range').value,'3.25');
  const players=host.querySelectorAll('video');assert.ok(oldPlayers.every(x=>x.paused));
  for(const player of players) await player.handlers.loadedmetadata();
  assert.equal(players[0].currentTime,24.25);assert.equal(players[1].currentTime,3.25);
  assert.equal(players[0].muted,true);assert.equal(players[1].muted,false);assert.ok(players.every(x=>!x.paused));
});

test('project, owner, work, media and clip changes cannot inherit another viewing position', async () => {
  for(const change of ['project','owner','work','media','clip']) {
    const host=new Element('section'),p=displayedProject(),settings=options();
    updateVideoDetail(doc,host,p,settings);
    const old=host.querySelectorAll('video');old[0].currentTime=25;old[1].currentTime=4;old[1].muted=true;await old[1].play();
    const next=structuredClone(p),nextSettings={...settings};
    if(change==='project')next.id='another-project';
    if(change==='owner')nextSettings.ownerKey='owner-B';
    if(change==='work'){next.candidates[0].id='another-work';nextSettings.selection='another-work';}
    if(change==='media')next.candidates[0].media_url='/api/video-lab/projects/video-one/artifacts/another-media';
    if(change==='clip')next.experiments[0].frozen.start_seconds=20;
    updateVideoDetail(doc,host,next,nextSettings);
    const players=host.querySelectorAll('video');
    for(const player of players)if(player.handlers.loadedmetadata)await player.handlers.loadedmetadata();
    assert.equal(players[0].currentTime,change==='clip'?20:21,change);
    assert.equal(players[1].currentTime,0,change);assert.equal(players[1].muted,false,change);assert.equal(players[1].paused,true,change);
  }
});

test('paused playback stays paused and a superseded metadata callback cannot restart a detached player', async () => {
  const host=new Element('section'),p=displayedProject(),settings=options();
  updateVideoDetail(doc,host,p,settings);const first=host.querySelectorAll('video')[1];first.currentTime=2;first.muted=true;
  const next=structuredClone(p);next.experiments[0].state='running';updateVideoDetail(doc,host,next,settings);
  const paused=host.querySelectorAll('video')[1];await paused.handlers.loadedmetadata();
  assert.equal(paused.currentTime,2);assert.equal(paused.paused,true);assert.equal(paused.muted,true);
  await paused.play();next.experiments[0].state='reviewing';updateVideoDetail(doc,host,next,settings);
  const detached=host.querySelectorAll('video')[1];
  updateVideoDetail(doc,host,displayedProject({id:'new-project'}),settings);
  await detached.handlers.loadedmetadata();assert.equal(detached.paused,true);assert.equal(detached.playCalls,0);
});
