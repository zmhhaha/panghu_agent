import test from 'node:test';
import assert from 'node:assert/strict';
import {createRefinementRequest,createRefinementsPanel} from '../../video_workbench/web/refinements.mjs';

const source={run_id:'run',version_id:'original',sha256:'a'.repeat(64)};
const project={id:'project',revision:3,scenes:[{id:'one'},{id:'two'}]};
const sequence=()=>({id:'sequence',sources:[source],automatic_finished:true,active:false,
  limits:{automatic:1,user_requested:2},consumed:{automatic:1,user_requested:0},remaining:{automatic:0,user_requested:2}});
const automatic=()=>({id:'default',project_id:'project',sequence_id:'sequence',kind:'automatic',source,
  status:'completed',phase:'finished',review:{summary:'仅核对抽帧',issues:[],limitations:['未连续播放']},
  result:{source,revision_performed:false}});

class Element {
  constructor(tag='div'){this.tagName=tag;this.dataset={};this.children=[];this.listeners=new Map();this.content='';this.value='';this.checked=false;this.hidden=false;this.disabled=false;this.pauses=0;}
  set textContent(value){this.content=String(value);this.children=[];}
  get textContent(){return [this.content,...this.children.map(row=>row.textContent)].join('\n');}
  set innerHTML(value){throw Error('Executable markup forbidden');}
  append(...rows){this.children.push(...rows);}
  replaceChildren(...rows){this.content='';this.children=rows;}
  setAttribute(name,value){this[name]=value;}
  removeAttribute(name){delete this[name];}
  pause(){this.pauses++;}
  addEventListener(name,fn){this.listeners.set(name,[...(this.listeners.get(name)??[]),fn]);}
  async emit(name){for(const fn of this.listeners.get(name)??[])await fn({target:this,preventDefault(){}});}
}

function harness(){
  const root=new Element(),saved=new Map();
  const state={context:{project,owner:'developer',epoch:1,unsaved:false,projectBusy:false,
    run:{id:'run',current:true,sequence_id:'sequence',sequence_registration:{source}}},
    data:{enabled:true,sequences:[sequence()],operations:[automatic()]},calls:[],errors:[],successes:[],transport:null};
  const storage={getItem:key=>saved.get(key)??null,setItem:(key,value)=>saved.set(key,value),removeItem:key=>saved.delete(key)};
  let panel;
  panel=createRefinementsPanel({document:{createElement:tag=>new Element(tag),getElementById:id=>root},
    getContext:()=>state.context,request:async(path,options={})=>{state.calls.push({path,options});return state.transport?state.transport(path,options):structuredClone(state.data);},
    refresh:()=>panel.load(state.context.project.id,state.context.epoch),onError:error=>state.errors.push(error),onSuccess:text=>state.successes.push(text),
    storage,makeRequestId:()=> 'film-refinement:test'});
  return {state,panel,storage,saved,load:()=>panel.load(state.context.project.id,state.context.epoch)};
}
const posts=h=>h.state.calls.filter(row=>row.options.method==='POST');

test('whole-film requests preserve source identity and enforce default plus two explicit user rounds',()=>{
  const request=createRefinementRequest(project,source,'user_requested',{instruction:'  改善对象主次  '},sequence(),{requestId:'request'});
  assert.deepEqual(request.body,{source,kind:'user_requested',request_id:'request',instruction:'改善对象主次'});
  for(const data of [{...sequence(),automatic_finished:false},{...sequence(),remaining:{user_requested:0}}])
    assert.throws(()=>createRefinementRequest(project,source,'user_requested',{instruction:'改进'},data,{requestId:'request'}));
  assert.throws(()=>createRefinementRequest(project,source,'automatic',{},sequence(),{requestId:'request'}));
  assert.throws(()=>createRefinementRequest(project,source,'user_requested',{instruction:'改进'},sequence(),{requestId:'request',unsaved:true}));
});

test('requirements remain exact scoped problems with one finite render budget and no acceptance fields',()=>{
  const items=[{scope:{kind:'time_range',start_seconds:2,end_seconds:4.5},problem:'遮挡主体',expected:'主体清楚可见'}];
  const request=createRefinementRequest(project,source,'requirements',{items},{...sequence(),remaining:{user_requested:0}},{requestId:'requirements'});
  assert.deepEqual(request.body.items,items);
  assert.deepEqual(request.body.budget,{max_agent_tasks:3,max_renders:1});
  assert.doesNotMatch(JSON.stringify(request.body),/accepted|adopt|audio_reviewed/);
  for(const bad of [[],[{...items[0],expected:''}],[{...items[0],scope:{kind:'time_range',start_seconds:4,end_seconds:3}}]])
    assert.throws(()=>createRefinementRequest(project,source,'requirements',{items:bad},sequence(),{requestId:'requirements'}));
});

test('opening and playing original or revised drafts never spends work or asserts human review',async()=>{
  const h=harness(),revised={...source,version_id:'revised',sha256:'b'.repeat(64)};
  h.state.data.operations.unshift({...automatic(),id:'revision',kind:'user_requested',result:{source:revised,revision_performed:true}});
  h.state.data.sequences[0].sources.push(revised);
  await h.load();
  assert.equal(h.panel.nodes.player.src,'/api/renders/original/video');
  h.panel.nodes.chooser.value='revised';await h.panel.nodes.chooser.emit('change');
  assert.equal(h.panel.nodes.player.src,'/api/renders/revised/video');
  assert.match(h.panel.nodes.note.textContent,/未经人工认可/);
  assert.match(h.panel.nodes.history.textContent,/尚未再次完整审阅或试听/);
  await h.panel.nodes.player.emit('play');assert.equal(posts(h).length,0);
});

test('explicit viewing acknowledgement and one click issue only one user request',async()=>{
  const h=harness();await h.load();h.panel.nodes.instruction.value='降低视觉拥挤';await h.panel.nodes.instruction.emit('input');
  await h.panel.start('user_requested');assert.equal(posts(h).length,0);
  h.panel.nodes.acknowledged.checked=true;await h.panel.nodes.acknowledged.emit('change');
  h.state.transport=(path,options)=>{
    if(options.method==='POST'){
      const row={...automatic(),id:'requested',status:'running',phase:'review',kind:'user_requested',request_id:options.body.request_id,result:null};
      h.state.data.operations.unshift(row);h.state.data.sequences[0].active=true;return row;
    }return structuredClone(h.state.data);
  };
  await h.panel.start('user_requested');await h.panel.start('user_requested');
  assert.equal(posts(h).length,1);assert.equal(posts(h)[0].options.body.instruction,'降低视觉拥挤');
  assert.equal(h.panel.hasUnresolvedOperation(),true);assert.equal(h.saved.size,0);
});

test('lost acknowledgement is recovered by exact request ID through GET without another submission',async()=>{
  const h=harness();await h.load();h.panel.nodes.acknowledged.checked=true;
  h.panel.nodes.instruction.value='改善节奏';await h.panel.nodes.instruction.emit('input');
  h.state.transport=(path,options)=>{
    if(options.method==='POST'){
      h.state.data.operations.unshift({...automatic(),id:'lost',kind:'user_requested',request_id:options.body.request_id,status:'running',result:null});
      throw new TypeError('response lost');
    }return structuredClone(h.state.data);
  };
  await h.panel.start('user_requested');await h.panel.start('user_requested');
  assert.equal(posts(h).length,1);assert.equal(h.saved.size,0);assert.equal(h.panel.hasUnresolvedOperation(),true);
});

test('unknown submissions survive refresh and unavailable storage blocks paid work',async()=>{
  const h=harness();await h.load();h.panel.nodes.acknowledged.checked=true;h.panel.nodes.instruction.value='改善';await h.panel.nodes.instruction.emit('input');
  h.state.transport=(path,options)=>{if(options.method==='POST')throw new TypeError('lost');return structuredClone(h.state.data);};
  await h.panel.start('user_requested');await h.load();await h.panel.start('user_requested');
  assert.equal(posts(h).length,1);assert.equal(h.saved.size,1);assert.match(h.panel.nodes.note.textContent,/不会重复提交/);
  const reopened=harness();for(const [key,value]of h.saved)reopened.saved.set(key,value);
  reopened.state.data.operations.unshift({...automatic(),id:'recovered',request_id:'film-refinement:test',status:'running',result:null});
  await reopened.load();assert.equal(reopened.saved.size,0);assert.equal(posts(reopened).length,0);
  assert.equal(reopened.panel.hasUnresolvedOperation(),true);
  const broken=harness();await broken.load();broken.panel.nodes.acknowledged.checked=true;broken.panel.nodes.instruction.value='改善';await broken.panel.nodes.instruction.emit('input');
  broken.storage.setItem=()=>{throw Error('unwritable');};await broken.panel.start('user_requested');assert.equal(posts(broken).length,0);
});

test('a definitive pre-admission unavailability leaves no unknown paid request',async()=>{
  const h=harness();await h.load();h.panel.nodes.acknowledged.checked=true;
  h.panel.nodes.instruction.value='改善节奏';await h.panel.nodes.instruction.emit('input');
  h.state.transport=(path,options)=>{if(options.method==='POST')throw Object.assign(Error('not admitted'),{status:503,code:'film_review_unavailable'});return structuredClone(h.state.data);};
  await h.panel.start('user_requested');
  assert.equal(posts(h).length,1);assert.equal(h.saved.size,0);assert.equal(h.panel.hasUnresolvedOperation(),false);
});

test('disabled or stale projects retain previews while blocking refinement requests',async()=>{
  const h=harness();h.state.data.enabled=false;await h.load();
  assert.equal(h.panel.nodes.player.hidden,false);assert.equal(h.panel.nodes.broad.disabled,true);
  h.state.data.enabled=true;h.state.context.run.current=false;await h.load();
  h.panel.nodes.acknowledged.checked=true;await h.panel.nodes.acknowledged.emit('change');
  h.panel.nodes.instruction.value='改进';await h.panel.nodes.instruction.emit('input');await h.panel.start('user_requested');
  assert.equal(posts(h).length,0);
});
