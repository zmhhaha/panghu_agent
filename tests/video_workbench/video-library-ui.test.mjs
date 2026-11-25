import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildVideoProject, buildVideoExperiment, videoAssetUrl, validateReferenceFile} from '../../video_workbench/animation_lab_web/video-model.mjs';
import {VideoLibraryController, analyzeVideoSelection, mountVideoLibrary, referenceBody, renderVideoDetail, updateVideoDetail} from '../../video_workbench/animation_lab_web/video-library.mjs';

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
  assert.equal(body.max_rounds, 1); assert.equal(body.max_seconds, 3600);
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

test('analysis submissions default to an hour and keep explicit shorter budgets', async () => {
  for (const [options, expected] of [[{}, 3600], [{analysis_minutes: 2}, 120]]) {
    const h = harness((path, request) => request.method
      ? {id:'analysis-one', reference_id:'ref', state:'queued'} : catalog());
    await h.controller.refresh();
    assert.equal(await h.controller.analyze({reference_id:'ref', ...options}, null), true);
    const sent = h.requests.find(row => row.request.method);
    assert.equal(JSON.parse(sent.request.body).max_seconds, expected);
  }
  const h=harness(()=>catalog());await h.controller.refresh();
  await assert.rejects(h.controller.analyze({reference_id:'ref',analysis_minutes:61},null),/1–60/);
  assert.ok(h.requests.every(row=>!row.request.method));
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
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.handlers={};this.listeners={};this.attributes={};this.currentTime=0;this.paused=true;this.muted=false;this.ended=false;this.readyState=0;this.duration=NaN;this.playCalls=0;this.replacements=0;this._text='';this.value='';this.checked=false;this.disabled=false;}
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
  focus(){}
}
const all = node => [node,...node.children.flatMap(all)];
const doc = {createElement:tag=>new Element(tag),createDocumentFragment:()=>new Element('fragment')};

function videoFormDocument() {
  // Load the shipping form's controls and reset values, so changing the HTML
  // without the actual mounted submission flow cannot satisfy this regression.
  const html=readFileSync(new URL('../../video_workbench/animation_lab_web/video-library.html',import.meta.url),'utf8');
  const attributes=raw=>Object.fromEntries([...raw.matchAll(/([\w:-]+)(?:="([^"]*)")?/g)].map(([,key,value])=>[key,value??'']));
  const nodes=new Map([...html.matchAll(/<([a-z][\w-]*)\b([^>]*\bid="([^"]+)"[^>]*)>/g)].map(([,tag,raw,id])=>{
    const node=new Element(tag), attrs=attributes(raw);
    Object.assign(node,attrs,{id,attributes:attrs,defaultValue:attrs.value??'',defaultChecked:'checked'in attrs,
      checked:'checked'in attrs,hidden:'hidden'in attrs,disabled:'disabled'in attrs});
    node.value=node.defaultValue; return [id,node];
  }));
  for (const [,raw,body]of html.matchAll(/<select\b([^>]*)>([\s\S]*?)<\/select>/g)) {
    const node=nodes.get(attributes(raw).id), options=[...body.matchAll(/<option\b([^>]*)>/g)].map(([,value])=>attributes(value));
    node.defaultValue=(options.find(value=>'selected'in value)??options[0])?.value??'';
    node.value=node.defaultValue;
  }
  const form=nodes.get('video-form'), body=html.match(/<form id="video-form">([\s\S]*?)<\/form>/)[1];
  form.elements=[...body.matchAll(/\bid="([^"]+)"/g)].map(([,id])=>nodes.get(id))
    .filter(node=>['INPUT','SELECT','TEXTAREA','BUTTON'].includes(node.tagName));
  form.reset=()=>{for(const node of form.elements){node.value=node.defaultValue;node.checked=node.defaultChecked;}};
  return {getElementById:id=>nodes.get(id)??null,createElement:tag=>new Element(tag),
    createDocumentFragment:()=>new Element('fragment')};
}

function referenceAnalysisCatalog() {
  return {...catalog(), references:[
    {id:'ref-a',file_name:'山水漫游.mp4',duration_seconds:40},
    {id:'ref-b',file_name:'霓虹城市.mp4',duration_seconds:40}
  ], analyses:[
    {id:'a-success',reference_id:'ref-a',created_at:100,state:'completed',goal:'HISTORICAL-GOAL：早期空间效果',candidates:[
      {title:'第一个旧片段',learning_goal:'学习早期淡入',start_seconds:2,end_seconds:6},
      {title:'HiddenWaterfall 云瀑',learning_goal:'学习云瀑的前后遮挡',start_seconds:21,end_seconds:28}
    ]},
    {id:'b-success',reference_id:'ref-b',created_at:250,state:'completed',goal:'城市尺度递进',candidates:[
      {title:'城市镜头',learning_goal:'学习城市镜头推进',start_seconds:8,end_seconds:12}
    ]},
    {id:'a-failed',reference_id:'ref-a',created_at:200,state:'failed',goal:'旧的失败尝试',error:{code:'old_analysis_timeout'}},
    {id:'a-latest',reference_id:'ref-a',created_at:300,state:'failed',goal:'最新的空间分析',error:{code:'latest_analysis_failed'}}
  ]};
}
function deepFreeze(value) {
  if(value&&typeof value==='object') {
    for(const child of Object.values(value))deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}
function mountedAnalysisLibrary(t, initial) {
  const formDoc=videoFormDocument(),get=id=>formDoc.getElementById(id),requests=[],scheduled=new Map();
  let current=initial,timerId=0;
  const fetcher=async(url,request={})=>{
    requests.push({path:url.pathname,method:request.method??'GET',body:request.body?JSON.parse(request.body):null});
    return {ok:true,status:200,json:async()=>url.pathname.endsWith('/catalog')?current:project()};
  };
  const app=mountVideoLibrary(formDoc,{root,fetcher,storage:memoryStorage(),uuid:()=>uuid,
    schedule:callback=>{scheduled.set(++timerId,callback);return timerId;},unschedule:id=>scheduled.delete(id)});
  t.after(()=>app.destroy());
  return {app,get,requests,setCatalog:value=>{current=value;},async poll(){
    assert.ok(scheduled.size,'an active raw analysis must schedule polling');
    const [id,callback]=scheduled.entries().next().value;scheduled.delete(id);await callback();
  }};
}
const analysisCards=host=>all(host).filter(node=>node.tagName==='ARTICLE'&&node.getAttribute('data-analysis-id'));
const analysisAttempt=(host,id)=>all(host).find(node=>node.tagName==='BUTTON'&&node.getAttribute('data-analysis-id')===id);
const analysisStatus=host=>all(host).find(node=>node.className==='video-status');
async function clickButton(host,label) {
  const node=all(host).find(node=>node.tagName==='BUTTON'&&node.textContent===label);
  assert.ok(node,'missing button: '+label);await node.handlers.click();
}
function installFormDataShim(t) {
  const NativeFormData=globalThis.FormData;
  globalThis.FormData=class {
    constructor(form){this.entries=form.elements.filter(node=>node.name&&!node.disabled
      &&(node.type!=='checkbox'||node.checked)).map(node=>[node.name,node.value]);}
    [Symbol.iterator](){return this.entries[Symbol.iterator]();}
  };
  t.after(()=>{globalThis.FormData=NativeFormData;});
}

test('mounted reference cards group immutable attempts and default to the latest failure without hiding another video', async t => {
  const input=deepFreeze(referenceAnalysisCatalog()),snapshot=structuredClone(input);
  const {app,get,requests}=mountedAnalysisLibrary(t,input);await app.ready;
  const cards=analysisCards(get('video-analyses'));
  assert.deepEqual(cards.map(node=>[node.getAttribute('data-reference-id'),node.getAttribute('data-analysis-id')]),
    [['ref-a','a-latest'],['ref-b','b-success']]);
  assert.match(cards[0].textContent,/分析失败/);assert.match(cards[0].textContent,/latest_analysis_failed/);
  assert.match(cards[0].textContent,/3 次分析/);assert.doesNotMatch(cards[0].textContent,/待选学习片段/);
  assert.match(get('video-count').textContent,/2 个参考视频 · 4 次分析/);
  await clickButton(cards[0],'查看分析与候选');
  assert.equal(analysisAttempt(get('video-analysis-detail'),'a-latest').getAttribute('aria-pressed'),'true');
  assert.deepEqual(all(detailsNamed(get('video-analysis-detail'),'分析历史')).filter(node=>node.tagName==='BUTTON')
    .map(node=>node.getAttribute('data-analysis-id')),['a-latest','a-failed','a-success']);
  assert.deepEqual(input,snapshot,'rendering must not rewrite or reorder raw catalog history');
  assert.ok(requests.every(row=>row.method==='GET'),'browsing must never commission another analysis');
});

test('latest queued and running attempts stay visible while a successful attempt remains in history', async t => {
  for(const [state,label]of [['queued','等待分析'],['running','正在分析']]) {
    const input=referenceAnalysisCatalog();Object.assign(input.analyses.at(-1),{state,error:null});
    const {app,get,requests}=mountedAnalysisLibrary(t,deepFreeze(input));await app.ready;
    const card=analysisCards(get('video-analyses')).find(node=>node.getAttribute('data-reference-id')==='ref-a');
    assert.equal(card.getAttribute('data-analysis-id'),'a-latest');assert.match(card.textContent,new RegExp(label));
    assert.doesNotMatch(card.textContent,/待选学习片段/);
    await clickButton(card,'查看分析与候选');
    assert.equal(analysisStatus(get('video-analysis-detail')).querySelector('strong').textContent,label);
    assert.equal(analysisAttempt(get('video-analysis-detail'),'a-success').getAttribute('aria-pressed'),'false');
    assert.ok(requests.every(row=>row.method==='GET'));app.destroy();
  }
});

test('history browsing shows the selected failure and submits an old successful candidate with its original identity and index', async t => {
  installFormDataShim(t);
  const input=deepFreeze(referenceAnalysisCatalog()),snapshot=structuredClone(input);
  const {app,get,requests}=mountedAnalysisLibrary(t,input);await app.ready;
  await clickButton(analysisCards(get('video-analyses'))[0],'查看分析与候选');
  detailsNamed(get('video-analysis-detail'),'分析历史').open=true;
  await analysisAttempt(get('video-analysis-detail'),'a-failed').handlers.click();
  const failedStatus=analysisStatus(get('video-analysis-detail'));
  assert.match(failedStatus.textContent,/old_analysis_timeout/);assert.doesNotMatch(failedStatus.textContent,/latest_analysis_failed/);
  assert.equal(analysisAttempt(get('video-analysis-detail'),'a-failed').getAttribute('aria-pressed'),'true');
  assert.equal(detailsNamed(get('video-analysis-detail'),'分析历史').open,true);
  await analysisAttempt(get('video-analysis-detail'),'a-success').handlers.click();
  const choices=all(get('video-analysis-detail')).filter(node=>node.tagName==='BUTTON'&&node.textContent==='选择这个片段');
  assert.equal(choices.length,2);await choices[1].handlers.click();
  assert.equal(get('video-form-panel').hidden,false);
  assert.equal(get('video-analysis-id').value,'a-success');assert.equal(Number(get('video-segment-index').value),1);
  assert.equal(get('video-reference-id').value,'ref-a');assert.equal(get('video-name').value,'HiddenWaterfall 云瀑');
  assert.equal(Number(get('video-start').value),21);assert.equal(Number(get('video-end').value),28);
  assert.ok(requests.every(row=>row.method==='GET'),'choosing a candidate only fills the form');
  await get('video-form').handlers.submit({preventDefault(){}});
  const posts=requests.filter(row=>row.method==='POST');assert.equal(posts.length,1);
  assert.ok(posts[0].path.endsWith('/projects'));
  assert.equal(posts[0].body.analysis_id,'a-success');assert.equal(posts[0].body.segment_index,1);
  assert.deepEqual(posts[0].body.reference_ids,['ref-a']);
  assert.equal(posts[0].body.start_seconds,21);assert.equal(posts[0].body.end_seconds,28);
  assert.equal(posts[0].body.goal,'学习云瀑的前后遮挡');assert.deepEqual(input,snapshot);
});

test('search includes older goals and candidate titles while the form lists one latest card per selected reference', async t => {
  const {app,get,requests}=mountedAnalysisLibrary(t,deepFreeze(referenceAnalysisCatalog()));await app.ready;
  for(const query of ['historical-goal','hiddenwaterfall']) {
    get('video-search').value=query;await get('video-search').handlers.input();
    assert.deepEqual(analysisCards(get('video-analyses')).map(node=>node.getAttribute('data-analysis-id')),['a-latest']);
  }
  get('video-search').value='does-not-exist';await get('video-search').handlers.input();
  assert.equal(analysisCards(get('video-analyses')).length,0);
  get('video-search').value='';await get('video-search').handlers.input();
  await get('video-new').handlers.click();
  assert.deepEqual(analysisCards(get('video-form-analyses')).map(node=>node.getAttribute('data-analysis-id')),['a-latest','b-success']);
  get('video-saved-reference').value='ref-a';await get('video-saved-reference').handlers.change();
  assert.deepEqual(analysisCards(get('video-form-analyses')).map(node=>node.getAttribute('data-analysis-id')),['a-latest']);
  await clickButton(analysisCards(get('video-form-analyses'))[0],'查看分析与候选');
  await analysisAttempt(get('video-analysis-detail'),'a-success').handlers.click();
  await clickButton(get('video-analysis-detail'),'返回填写需求');
  assert.equal(get('video-form-panel').hidden,false);assert.equal(get('video-reference-id').value,'ref-a');
  assert.ok(requests.every(row=>row.method==='GET'));
});

test('polling refreshes grouped history and latest status while preserving an explicitly selected older attempt', async t => {
  const initial=referenceAnalysisCatalog();Object.assign(initial.analyses.at(-1),{state:'running',error:null});
  const {app,get,requests,setCatalog,poll}=mountedAnalysisLibrary(t,deepFreeze(initial));await app.ready;
  await clickButton(analysisCards(get('video-analyses'))[0],'查看分析与候选');
  detailsNamed(get('video-analysis-detail'),'分析历史').open=true;
  await analysisAttempt(get('video-analysis-detail'),'a-failed').handlers.click();
  const next=structuredClone(initial);
  Object.assign(next.analyses.at(-1),{state:'completed',candidates:[{title:'新完成片段',learning_goal:'新目标',start_seconds:5,end_seconds:9}]});
  next.analyses.push({id:'a-newest',reference_id:'ref-a',created_at:400,state:'queued',goal:'新排队的分析'});
  setCatalog(deepFreeze(next));await poll();
  const card=analysisCards(get('video-analyses'))[0];assert.equal(card.getAttribute('data-analysis-id'),'a-newest');
  assert.match(card.textContent,/等待分析/);assert.match(card.textContent,/4 次分析/);
  const host=get('video-analysis-detail'),history=detailsNamed(host,'分析历史');
  assert.equal(history.open,true);assert.match(history.querySelector('summary').textContent,/4 次/);
  assert.equal(analysisAttempt(host,'a-failed').getAttribute('aria-pressed'),'true');
  assert.equal(analysisAttempt(host,'a-newest').getAttribute('aria-pressed'),'false');
  assert.match(analysisStatus(host).textContent,/old_analysis_timeout/);
  assert.match(history.textContent,/待选学习片段/,'history-only changes must invalidate the detail cache');
  await clickButton(host,'返回视频学习库');await clickButton(analysisCards(get('video-analyses'))[0],'查看分析与候选');
  assert.equal(analysisAttempt(get('video-analysis-detail'),'a-newest').getAttribute('aria-pressed'),'true');
  assert.ok(requests.every(row=>row.method==='GET'));
});

test('owner changes clear chosen history and prevent retained controls from exposing the previous account', async t => {
  const {app,get,requests,setCatalog}=mountedAnalysisLibrary(t,deepFreeze(referenceAnalysisCatalog()));await app.ready;
  await clickButton(analysisCards(get('video-analyses'))[0],'查看分析与候选');
  const oldHistory=analysisAttempt(get('video-analysis-detail'),'a-success');await oldHistory.handlers.click();
  const oldChoice=all(get('video-analysis-detail')).filter(node=>node.tagName==='BUTTON'&&node.textContent==='选择这个片段')[1];
  await oldChoice.handlers.click();assert.equal(get('video-analysis-id').value,'a-success');
  setCatalog(deepFreeze({...catalog(),owner_key:'other-video-owner',references:[{id:'ref-a',file_name:'另一账号.mp4',duration_seconds:40}],
    analyses:[{id:'a-success',reference_id:'ref-a',created_at:500,state:'completed',goal:'另一账号的分析',candidates:[]}]}));
  await app.controller.refresh();
  assert.equal(get('video-analysis-id').value,'');assert.equal(get('video-segment-index').value,'');
  assert.equal(get('video-reference-id').value,'');assert.equal(get('video-analysis-detail').textContent,'');
  assert.equal(get('video-home').hidden,false);assert.equal(get('video-form-panel').hidden,true);
  await oldChoice.handlers.click();assert.equal(get('video-analysis-id').value,'');
  await oldHistory.handlers.click();
  assert.match(get('video-analysis-detail').textContent,/另一账号的分析/);
  assert.doesNotMatch(get('video-analysis-detail').textContent,/HISTORICAL-GOAL|old_analysis_timeout|HiddenWaterfall/);
  assert.match(detailsNamed(get('video-analysis-detail'),'分析历史').querySelector('summary').textContent,/1 次/);
  assert.ok(requests.every(row=>row.method==='GET'));
});

test('mounted new, continued and fresh forms reset and submit the one-hour one-round defaults', async t => {
  const formDoc=videoFormDocument(), get=id=>formDoc.getElementById(id), sent=[];
  const current=project({candidates:[{id:'work',work_kind:'reproduction',media_url:'/api/video-lab/projects/video-one/artifacts/media'}]});
  current.experiments[0].frozen={...fields,max_rounds:7,max_seconds:120};
  const fetcher=async(url,request={})=>{
    if(request.method)sent.push({path:url.pathname,body:JSON.parse(request.body)});
    return {ok:true,status:200,json:async()=>url.pathname.endsWith('/catalog')
      ? {...catalog([current]),references:[{id:'ref',file_name:'ref.mp4',duration_seconds:40}]} : current};
  };
  const NativeFormData=globalThis.FormData;
  globalThis.FormData=class {
    constructor(form){this.entries=form.elements.filter(node=>node.name&&!node.disabled
      &&(node.type!=='checkbox'||node.checked)).map(node=>[node.name,node.value]);}
    [Symbol.iterator](){return this.entries[Symbol.iterator]();}
  };
  t.after(()=>{globalThis.FormData=NativeFormData;});
  const app=mountVideoLibrary(formDoc,{root,fetcher,storage:memoryStorage(),uuid:()=>uuid,schedule:()=>1,unschedule:()=>{}});
  await app.ready;
  for(const mode of ['new','continue','fresh']) {
    get('video-rounds').value='7';get('video-minutes').value='2';get('video-analysis-minutes').value='2';
    if(mode==='new')await get('video-new').handlers.click();
    else await all(get('video-detail')).find(node=>node.tagName==='BUTTON'
      &&node.textContent===(mode==='continue'?'继续优化':'从零重做')).handlers.click();
    assert.equal(get('video-rounds').value,'1');assert.equal(get('video-minutes').value,'60');
    assert.equal(get('video-analysis-minutes').value,'60');
    get('video-name').value=fields.title;get('video-goal').value=fields.goal;get('video-reference-id').value='ref';
    get('video-feedback').value='改善标签的可读性';
    await get('video-form').handlers.submit({preventDefault(){}});
    const body=sent.at(-1).body;assert.equal(body.max_rounds,1);assert.equal(body.max_seconds,3600);
    if(mode!=='new')assert.equal(body.mode,mode);
    await app.controller.refresh();
  }
  const explicit=buildVideoProject({...fields,max_rounds:2,max_minutes:2},['ref'],uuid);
  assert.equal(explicit.max_rounds,2);assert.equal(explicit.max_seconds,120);
  const api=JSON.parse(readFileSync(new URL('../../video_workbench/animation_lab_web/video-api.openapi.json',import.meta.url),'utf8'));
  for(const name of ['ProjectStart','ExperimentStart']){
    assert.equal(api.components.schemas[name].properties.max_rounds.default,1);
    assert.equal(api.components.schemas[name].properties.max_seconds.default,3600);
  }
  assert.equal(api.components.schemas.AnalysisStart.properties.max_seconds.default,3600);
  app.destroy();
});

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

test('internal video studies remain history evidence with playable media and source links', () => {
  const p=project({candidates:[{id:'work',work_kind:'reproduction',media_url:'/api/video-lab/projects/video-one/artifacts/work'}],
    experiments:[{id:'exp',state:'running',frozen:{...fields},learning_progress:{phase:'probe_render',probes:[{id:'wave',goal:'wave shape',status:'authored'}],components:[]},
      internal_attempts:[{id:'probe',role:'internal_probe',probe_id:'wave',work_kind:'reproduction',media_url:'/api/video-lab/projects/video-one/artifacts/probe',
        source_url:'/api/video-lab/projects/video-one/artifacts/probe-source',review:{summary:'Actual wave observation'}}]}]});
  const detail=renderVideoDetail(doc,p,{root});
  assert.match(detail.textContent,/渲染技法小样/);assert.match(detail.textContent,/内部小样：wave/);
  assert.equal(detail.querySelectorAll('video').length,3);
  assert.equal(detail.querySelectorAll('button').filter(row=>row.textContent.startsWith('复现')).length,1);
  assert.ok(detail.querySelectorAll('a').some(row=>row.href?.endsWith('/probe-source')));
});
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


test('manual video validation requires explicit approval and has an independent one-round budget', () => {
  const input={mode:'transfer',base_candidate_id:'chosen',approve_candidate:true,use_wikiskill:true,feedback:'old',max_rounds:8};
  assert.deepEqual(buildVideoExperiment(input,uuid),{request_id:uuid,mode:'transfer',base_candidate_id:'chosen',
    approve_candidate:true,max_rounds:1,max_seconds:3600});
  assert.equal(buildVideoExperiment({...input,max_minutes:5},uuid).max_seconds,300);
  for(const approve_candidate of [undefined,false,'true'])assert.throws(()=>buildVideoExperiment({...input,approve_candidate},uuid),/认可/);
  assert.throws(()=>buildVideoExperiment({...input,base_candidate_id:undefined},uuid),/编号/);
  assert.equal('approve_candidate' in buildVideoExperiment({mode:'fresh'},uuid),false);
});

test('manual video validation accepts unscored rendered work and rejects unavailable or transfer work before POST', async () => {
  const work={id:'chosen',status:'rendered',work_kind:'reproduction',method:{technique:'local form'},
    media_url:'/api/video-lab/projects/video-one/artifacts/chosen-media'};
  const p=project({candidates:[work],best_candidate_id:work.id});
  assert.ok(all(renderVideoDetail(doc,p,{root})).some(row=>row.tagName==='BUTTON'&&row.textContent==='认可并验证换内容'));
  const h=harness((path,r)=>r.method?p:path.endsWith('catalog')?catalog([p]):p);
  await h.controller.refresh();await h.controller.open(p.id);
  await assert.rejects(h.controller.experiment({mode:'transfer',base_candidate_id:work.id}),/认可/);
  for(const change of [{status:'authored'},{method:null},{method:{}},{work_kind:'transfer'},{media_url:null}]) {
    Object.assign(work,{status:'rendered',method:{technique:'local form'},work_kind:'reproduction',
      media_url:'/api/video-lab/projects/video-one/artifacts/chosen-media'},change);
    assert.equal(await h.controller.experiment({mode:'transfer',base_candidate_id:work.id,approve_candidate:true}),false);
  }
  assert.ok(h.requests.every(row=>!row.request.method));
  Object.assign(work,{status:'rendered',method:{technique:'local form'},work_kind:'reproduction',
    media_url:'/api/video-lab/projects/video-one/artifacts/chosen-media'});
  assert.equal(await h.controller.experiment({mode:'transfer',base_candidate_id:work.id,approve_candidate:true}),true);
  const post=h.requests.find(row=>row.request.method);assert.equal(post.path,'api/video-lab/projects/video-one/experiments');
  assert.equal(JSON.parse(post.request.body).base_candidate_id,work.id);
  assert.ok(h.requests.every(row=>!row.path.endsWith('/adopt')));
});

test('video approval form waits for submission and keeps its chosen version across polling', async t => {
  const formDoc=videoFormDocument(),get=id=>formDoc.getElementById(id),sent=[];
  const first={id:'chosen',status:'rendered',work_kind:'reproduction',method:{technique:'chosen'},media_url:'/api/video-lab/projects/video-one/artifacts/chosen-media'};
  const later={...first,id:'later',status:'reviewed',method:{technique:'later'},media_url:'/api/video-lab/projects/video-one/artifacts/later-media'};
  const p=project({candidates:[first,later],best_candidate_id:first.id});
  const fetcher=async(url,request={})=>{
    if(request.method)sent.push({path:url.pathname,body:JSON.parse(request.body)});
    return{ok:true,status:200,json:async()=>url.pathname.endsWith('/catalog')?catalog([p]):p};
  };
  const NativeFormData=globalThis.FormData;
  globalThis.FormData=class{constructor(form){this.entries=form.elements.filter(node=>node.name&&!node.disabled
    &&(node.type!=='checkbox'||node.checked)).map(node=>[node.name,node.value]);}[Symbol.iterator](){return this.entries[Symbol.iterator]();}};
  t.after(()=>{globalThis.FormData=NativeFormData;});
  const app=mountVideoLibrary(formDoc,{root,fetcher,storage:memoryStorage(),uuid:()=>uuid,schedule:()=>1,unschedule:()=>{}});
  await app.ready;
  await all(get('video-cards')).find(row=>row.tagName==='BUTTON'&&row.textContent===p.title).handlers.click();
  await all(get('video-detail')).find(row=>row.tagName==='BUTTON'&&row.textContent==='认可并验证换内容').handlers.click();
  assert.equal(sent.length,0);assert.equal(get('video-minutes').value,'60');
  assert.equal(get('video-learning-options').hidden,true);assert.equal(get('video-wikiskill-field').hidden,true);
  assert.equal(get('video-submit').textContent,'认可此版本并开始验证');
  p.best_candidate_id=later.id;p.adopted_candidate_id=later.id;await app.controller.refresh();
  get('video-minutes').value='2';await get('video-form').handlers.submit({preventDefault(){}});
  assert.equal(sent.length,1);
  assert.deepEqual(sent[0].body,{request_id:uuid,mode:'transfer',base_candidate_id:first.id,approve_candidate:true,max_rounds:1,max_seconds:120});
  assert.ok(sent[0].path.endsWith('/experiments'));app.destroy();
});

test('lost approved video validation replays the original candidate and approval without adoption', async () => {
  const storage=memoryStorage(),work={id:'chosen',status:'rendered',work_kind:'reproduction',method:{technique:'form'},
    media_url:'/api/video-lab/projects/video-one/artifacts/chosen-media'};
  const p=project({candidates:[work],experiments:[{id:'old',state:'completed',request_id:'00000000-0000-4000-8000-000000000002'}]});
  const first=harness((path,r)=>{if(r.method)throw Error('lost');return path.endsWith('catalog')?catalog([p]):p;},storage);
  await first.controller.refresh();await first.controller.open(p.id);
  await first.controller.experiment({mode:'transfer',base_candidate_id:work.id,approve_candidate:true,max_minutes:2});
  const original=JSON.parse(first.requests.find(row=>row.request.method).request.body);p.best_candidate_id='different';
  const next=harness((path,r)=>r.method?p:path.endsWith('catalog')?catalog([p]):p,storage);
  await next.controller.refresh();assert.ok(next.controller.state.pending);
  assert.equal(await next.controller.replayPending(),true);
  assert.deepEqual(JSON.parse(next.requests.find(row=>row.request.method).request.body),original);
  assert.ok(next.requests.every(row=>!row.path.endsWith('/adopt')));
});
