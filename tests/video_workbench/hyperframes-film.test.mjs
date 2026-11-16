// Scheduling/core-adapter doubles only. These tests do not execute HyperFrames,
// arbitrary Agent source, native drawing, an admitted loader, or media capture.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHyperFramesFilmHost,connectHyperFramesFilmCore}
 from '../../video_workbench/motion_renderer/hyperframes_film.mjs';

const sha=char=>char.repeat(64);
const lifecycle={protocol:'hyperframes-composable-module-1',exports:{mount:'mount',render_at:'renderAt',dispose:'dispose'},
 scope:'mount-root',time:'absolute-global-frame',completion:'await-returned-promises',seek:'reconstructive'};
// Scheduling-shaped fixture, deliberately not a package-verification fixture.
// Exact file/hash custody belongs to test_hyperframes_film_project.py.
function manifest(){
 const modules=['one','two','three','bridge'].map((name,index)=>({schema:'video.hyperframes-composable-module.v1',
  module_id:name,namespace:name,role:index===3?'transition':'scene',scene_id:index===3?null:name,
  module_sha256:sha(String(index+1)),film_sha256:sha('a'),engine:['html-svg','canvas','three','html-svg'][index],
  lifecycle:structuredClone(lifecycle),states:{entry:{subject:{x:index}},exit:{subject:{x:index+1}}}}));
 return {schema:'video.hyperframes-film-project.v1',film_sha256:sha('a'),
  execution:{status:'not_executed',legacy_bridge:false,required_host_protocol:'hyperframes-composable-film-host-1'},
  clock:{fps:24,frame_count:48},modules,
  scenes:['one','two','three'].map((name,index)=>({scene_id:name,module_id:name,start_frame:index*16,end_frame:(index+1)*16,
   active_start_frame:index===2?30:index*16,active_end_frame:index===1?34:(index+1)*16})),
  boundaries:[0,1].map(index=>({schema:'video.hyperframes-film-boundary.v1',from_scene_id:['one','two'][index],
   to_scene_id:['two','three'][index],from_module_sha256:modules[index].module_sha256,
   to_module_sha256:modules[index+1].module_sha256,at_frame:(index+1)*16,
   objects:[{object_id:'subject',outgoing_state_sha256:sha('b'),incoming_state_sha256:sha('c'),mode:index?'transform':'retain'}],
   transition:index?{module_id:'bridge',module_sha256:modules[3].module_sha256,start_frame:30,end_frame:34}:null}))};
}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function fixture({mutate,overrides={}}={}){
 const value=manifest();if(mutate)mutate(value);
 const events=[],roots=new Set(),contexts=[],drawings=new Map();let serial=0;
 const host=createHyperFramesFilmHost({verifiedManifest:value,manifestSha256:sha('d'),
  async trustedLoadModule(module,options){
   assert.ok(Object.isFrozen(module));assert.equal(options.fresh_instance,true);assert.ok(Object.isFrozen(options));
   const instance=++serial;events.push(['load',module.module_id,instance]);
   return {async mount(root,context){await Promise.resolve();contexts.push(context);events.push(['mount',module.module_id,instance]);
     assert.ok(roots.has(root));assert.ok(Object.isFrozen(context));assert.ok(Object.isFrozen(context.states.entry));},
    async renderAt(context){await Promise.resolve();events.push(['render',module.module_id,instance]);
     drawings.set(module.module_id,{frame:context.global_frame,local:context.local_frame,phase:context.phase});},
    async dispose(){await Promise.resolve();events.push(['dispose',module.module_id,instance]);drawings.delete(module.module_id);}};
  },
  async createRoot(module,context){await Promise.resolve();const root={module:module.module_id,frame:context.global_frame};
   roots.add(root);events.push(['root',module.module_id]);return root;},
  async removeRoot(root,module){await Promise.resolve();roots.delete(root);events.push(['remove',module.module_id]);},
  ...overrides});
 return {host,value,events,roots,contexts,drawings};
}
function coreFixture(f,{deduplicate=true,brokenClock=false,noEvents=false,badEvent=false,brokenWaitUntil=false}={}){
 const listeners=new Set(),pending=[];let time=0,previous=null,forces=0;
 const target={addEventListener(name,fn){assert.equal(name,'hf-seek');listeners.add(fn);},
  removeEventListener(name,fn){assert.equal(name,'hf-seek');listeners.delete(fn);}};
 function emit(seconds){
  if(noEvents)return;
  let accepting=true;
  const detail={time:badEvent?seconds+.01:seconds,waitUntil(operation){
   assert.ok(accepting,'waitUntil must be registered synchronously');
   if(brokenWaitUntil)throw Error('wait_until_failed');pending.push(operation);
  }};
  try{for(const fn of listeners)fn({detail});}finally{accepting=false;}
 }
 const player={async renderSeek(seconds,options){assert.deepEqual(options,{exact:true});time=seconds;
   if(!deduplicate||seconds!==previous){previous=seconds;emit(seconds);}},
  getTime(){return brokenClock?time+.1:time;},getDuration(){return 2;}};
 const clock=connectHyperFramesFilmCore({host:f.host,eventTarget:target,player,
  async waitForSeekCompletion(){while(pending.length)await Promise.all(pending.splice(0));},
  forceReseek(seconds){forces++;emit(seconds);}});
 return {clock,player,target,listeners,forces:()=>forces};
}

test('each seek reconstructs fresh roots/instances at forward reverse random and repeated frames',async()=>{
 const f=fixture();let before=0;
 for(const frame of [0,47,8,32,30,31,31,16,0]){
  const observation=await f.host.renderFrame(frame);
  assert.equal(observation.completed_frame,frame);assert.equal(observation.package_execution_status,'not_executed');
  assert.equal(f.host.getState().status,'idle');
  assert.ok(f.events.filter(event=>event[0]==='load').length>before);before=f.events.filter(event=>event[0]==='load').length;
  for(const drawing of f.drawings.values())assert.equal(drawing.frame,frame);
  assert.equal(f.roots.size,observation.active_modules.length);
 }
 await f.host.dispose();assert.equal(f.roots.size,0);assert.equal(f.drawings.size,0);
 assert.equal(f.host.getState().status,'disposed');
 await assert.rejects(f.host.renderFrame(0),/host_stopped/);
});

test('overlap participants share global frame with unmodified local enter/exit time',async()=>{
 const f=fixture();
 for(const [frame,phases,locals] of [[30,['active','entering','active'],[14,-2,0]],
  [32,['exiting','active','active'],[16,0,2]]]){
  f.events.length=0;
  const observation=await f.host.renderFrame(frame);
  assert.deepEqual(observation.active_modules.map(row=>row.module_id),['two','three','bridge']);
  assert.deepEqual(observation.active_modules.map(row=>row.phase),phases);
  assert.deepEqual(observation.active_modules.map(row=>row.local_frame),locals);
  assert.deepEqual(f.events.filter(row=>row[0]==='mount').map(row=>row[1]),['two','three','bridge']);
  const firstRender=f.events.findIndex(row=>row[0]==='render'),lastMount=f.events.findLastIndex(row=>row[0]==='mount');
  assert.ok(firstRender>lastMount,'all mounts must complete before drawing');
 }
 await f.host.renderFrame(34);assert.deepEqual([...f.drawings.keys()],['three']);await f.host.dispose();
});

test('caller mutations do not change frozen schedule or passed structured states',async()=>{
 const f=fixture();f.value.scenes[0].start_frame=100;f.value.modules[0].states.entry.subject.x=999;
 await f.host.renderFrame(0);const context=f.contexts[0];assert.equal(context.states.entry.subject.x,0);
 assert.deepEqual(context.time,{numerator:0,denominator:1});
 assert.throws(()=>{context.states.entry.subject.x=100;},TypeError);
 await f.host.renderFrame(31);assert.deepEqual(f.contexts.at(-1).time,{numerator:31,denominator:24});
 await f.host.dispose();
});

test('async load/root/mount/render and dispose/remove are fully awaited',async()=>{
 const gate=deferred(),events=[];
 const f=fixture({overrides:{async trustedLoadModule(){await gate.promise;events.push('loaded');
   return {async mount(){await Promise.resolve();events.push('mounted');},
    async renderAt(){await Promise.resolve();events.push('rendered');},
    async dispose(){await Promise.resolve();events.push('disposed');}};},
  async createRoot(){await Promise.resolve();events.push('root');return {};},
  async removeRoot(){await Promise.resolve();events.push('removed');}}});
 let finished=false;const running=f.host.renderFrame(0).then(()=>{finished=true;});
 await Promise.resolve();assert.equal(finished,false);assert.equal(f.host.getState().completed_frame,null);
 await assert.rejects(f.host.renderFrame(1),/busy/);await assert.rejects(f.host.dispose(),/busy/);
 gate.resolve();await running;assert.deepEqual(events,['loaded','root','mounted','rendered']);
 await f.host.dispose();assert.deepEqual(events.slice(-2),['disposed','removed']);
});

for(const failing of ['load','root','mount','render'])test(`rejected ${failing} stops execution and awaits existing participant cleanup`,async()=>{
 const events=[],roots=new Set();let count=0;
 const f=fixture({overrides:{async trustedLoadModule(module){
   if(++count===2&&failing==='load')throw Error('failed_load');
   return {async mount(){if(module.module_id==='three'&&failing==='mount')throw Error('failed_mount');},
    async renderAt(){if(module.module_id==='three'&&failing==='render')throw Error('failed_render');},
    async dispose(){await Promise.resolve();events.push(['disposed',module.module_id]);}};},
  async createRoot(module){if(module.module_id==='three'&&failing==='root')throw Error('failed_root');
   const root={id:module.module_id};roots.add(root);return root;},
  async removeRoot(root){await Promise.resolve();roots.delete(root);events.push(['removed',root.id]);}}});
 await assert.rejects(f.host.renderFrame(32),new RegExp('failed_'+failing));
 assert.equal(f.host.getState().status,'failed');assert.equal(f.host.getState().completed_frame,null);
 assert.equal(roots.size,0);assert.ok(events.some(row=>row[0]==='disposed'&&row[1]==='two'));
 await assert.rejects(f.host.renderFrame(0),/host_stopped/);await f.host.dispose();
});

test('cleanup attempts every root in reverse order and never reports prior frame success',async()=>{
 const events=[];let fail=false;
 const f=fixture({overrides:{async trustedLoadModule(module){return {mount(){},renderAt(){},
   async dispose(){await Promise.resolve();events.push(module.module_id);if(fail)throw Error('dispose_failed');}};},
  createRoot(){return {};},removeRoot(){}}});
 await f.host.renderFrame(32);fail=true;
 await assert.rejects(f.host.renderFrame(0),/cleanup_failed/);
 assert.deepEqual(events,['bridge','three','two']);
 assert.equal(f.host.getState().completed_frame,null);assert.equal(f.host.getState().status,'failed');
 assert.equal(f.host.getState().live_roots,0);assert.equal(f.host.getState().pending_cleanup_modules,3);
 fail=false;await f.host.dispose();assert.equal(f.host.getState().pending_cleanup_modules,0);
});

test('legacy seek-only exports cannot satisfy module lifecycle',async()=>{
 const f=fixture({overrides:{trustedLoadModule(){return {seek(){}};}}});
 await assert.rejects(f.host.renderFrame(0),/lifecycle_exports_missing/);
 assert.equal(f.host.getState().status,'failed');assert.equal(f.roots.size,0);
});

test('mount roots must exist and simultaneous modules cannot share one root',async()=>{
 const missing=fixture({overrides:{createRoot(){return null;}}});
 await assert.rejects(missing.host.renderFrame(0),/mount_root_missing/);
 assert.equal(missing.host.getState().live_roots,0);
 const root={},removed=[];
 const reused=fixture({overrides:{trustedLoadModule(){return {mount(){},renderAt(){},dispose(){}};},
  createRoot(){return root;},removeRoot(value){removed.push(value);}}});
 await assert.rejects(reused.host.renderFrame(32),/mount_root_reused/);
 assert.deepEqual(removed,[root]);assert.equal(reused.host.getState().pending_cleanup_modules,0);
});

test('failed root removal is explicit and a later dispose retries only pending cleanup',async()=>{
 let fail=true,disposeCount=0;
 const f=fixture({overrides:{trustedLoadModule(){return {mount(){},renderAt(){},dispose(){disposeCount++;}};},
  removeRoot(){if(fail)throw Error('remove_failed');}}});
 await f.host.renderFrame(0);await assert.rejects(f.host.dispose(),/cleanup_failed/);
 assert.equal(f.host.getState().live_roots,1);assert.equal(f.host.getState().completed_frame,null);
 fail=false;await f.host.dispose();assert.equal(disposeCount,1);assert.equal(f.host.getState().live_roots,0);
});

for(const change of ['legacy','executed','protocol','range','frame','module','boundary','transition','extra'])
 test(`invalid ${change} schedule is rejected before source executes`,()=>{
  assert.throws(()=>fixture({mutate(value){
   if(change==='legacy')value.modules[0].schema='video.creative-scene.v5';
   if(change==='executed')value.execution.status='rendered';
   if(change==='protocol')value.modules[0].lifecycle.time='scene-local';
   if(change==='range')value.scenes[1].active_end_frame=33;
   if(change==='frame')value.scenes[1].start_frame=17;
   if(change==='module')value.modules[0].film_sha256=sha('f');
   if(change==='boundary')value.boundaries[1].to_module_sha256=sha('f');
   if(change==='transition')value.boundaries[1].transition.end_frame=49;
   if(change==='extra')value.modules.push({...value.modules[3],module_id:'unused'});
  }}),/hyperframes_film_/);
 });

test('frame requests are bounded and integer before loading any source',async()=>{
 const f=fixture();for(const frame of [-1,true,1.5,48,Infinity,NaN])await assert.rejects(f.host.renderFrame(frame),/frame_invalid/);
 assert.deepEqual(f.events,[]);await f.host.dispose();
});

test('core adapter uses exact global clock, synchronous waitUntil and deduplication fallback',async()=>{
 const f=fixture(),c=coreFixture(f);
 for(const frame of [0,47,32,16,30,30,0]){
  const observation=await c.clock.seekFrame(frame);
  assert.equal(observation.requested_frame,frame);assert.equal(observation.actual_seconds,frame/24);
  assert.equal(observation.completed_frame,frame);assert.equal(observation.package_execution_status,'not_executed');
 }
 assert.equal(c.forces(),1);assert.equal(c.listeners.size,1);
 await c.clock.close();assert.equal(c.listeners.size,0);assert.equal(f.roots.size,0);
 await assert.rejects(c.clock.seekFrame(0),/core_stopped/);
});

for(const option of ['brokenClock','noEvents','badEvent','brokenWaitUntil'])
 test(`core adapter failure ${option} clears all roots and prevents further seeks`,async()=>{
  const f=fixture(),c=coreFixture(f,{[option]:true});
  await assert.rejects(c.clock.seekFrame(32),/hyperframes_film_|wait_until_failed/);
  assert.equal(f.roots.size,0);assert.equal(f.host.getState().completed_frame,null);assert.equal(c.listeners.size,0);
  await assert.rejects(c.clock.seekFrame(0),/core_stopped/);await c.clock.close();
 });

test('core adapter propagates async render rejection and awaits disposal',async()=>{
 const disposed=[];
 const f=fixture({overrides:{trustedLoadModule(module){return {mount(){},async renderAt(){
   await Promise.resolve();throw Error('agent_render_failed');},async dispose(){await Promise.resolve();disposed.push(module.module_id);}};}}});
 const c=coreFixture(f);await assert.rejects(c.clock.seekFrame(32),/agent_render_failed/);
 assert.deepEqual(disposed,['bridge','three','two']);assert.equal(f.roots.size,0);assert.equal(c.listeners.size,0);
});

test('concurrent core seeks and close reject while the current render remains awaited',async()=>{
 const gate=deferred();
 const f=fixture({overrides:{trustedLoadModule(){return {mount(){},renderAt(){return gate.promise;},dispose(){}};}}});
 const c=coreFixture(f);let finished=false;
 const running=c.clock.seekFrame(0).then(()=>{finished=true;});
 await Promise.resolve();await Promise.resolve();
 await assert.rejects(c.clock.seekFrame(1),/busy/);await assert.rejects(c.clock.close(),/busy/);
 assert.equal(finished,false);gate.resolve();await running;await c.clock.close();
});

test('one binding per host and exact core duration are required',async()=>{
 const f=fixture(),c=coreFixture(f);
 assert.throws(()=>connectHyperFramesFilmCore({host:f.host,eventTarget:c.target,player:c.player,
  waitForSeekCompletion(){},forceReseek(){}}),/binding_invalid/);
 await c.clock.close();
 const g=fixture();assert.throws(()=>connectHyperFramesFilmCore({host:g.host,eventTarget:c.target,
  player:{...c.player,getDuration(){return 1;}},waitForSeekCompletion(){},forceReseek(){}}),/duration_mismatch/);
 await g.host.dispose();
});
