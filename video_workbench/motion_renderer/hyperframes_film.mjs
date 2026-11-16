// Pure composable-film lifecycle scheduling. The caller separately verifies
// package bytes, loads admitted resources/core, and provides a confined root.
// No creative source rewriting, DOM styling, capture or runtime admission.
const SCHEMA='video.hyperframes-film-project.v1';
const PROTOCOL='hyperframes-composable-film-host-1';
const hosts=new WeakMap();
const own=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const check=(value,code)=>{if(!value)throw Error(code);};
const sha=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const integer=(value,min,max)=>Number.isInteger(value)&&value>=min&&value<=max;
const copy=value=>JSON.parse(JSON.stringify(value));
function freeze(value){
 if(value&&typeof value==='object'){for(const child of Object.values(value))freeze(child);Object.freeze(value);}
 return value;
}
function ratio(frame){
 let a=frame,b=24;while(b){const remainder=a%b;a=b;b=remainder;}
 return {numerator:frame/a,denominator:24/a};
}

function schedule(manifest){
 check(own(manifest)&&manifest.schema===SCHEMA&&manifest.execution?.status==='not_executed'
  &&manifest.execution.legacy_bridge===false&&manifest.execution.required_host_protocol===PROTOCOL
  &&manifest.clock?.fps===24&&integer(manifest.clock.frame_count,1,14400)
  &&Array.isArray(manifest.scenes)&&manifest.scenes.length>=1&&manifest.scenes.length<=24
  &&Array.isArray(manifest.modules)&&manifest.modules.length<=47
  &&Array.isArray(manifest.boundaries)&&manifest.boundaries.length===manifest.scenes.length-1
  &&sha(manifest.film_sha256),'hyperframes_film_verified_manifest_required');
 const modules=new Map();
 for(const module of manifest.modules){
  check(own(module)&&module.schema==='video.hyperframes-composable-module.v1'
   &&typeof module.module_id==='string'&&!modules.has(module.module_id)
   &&module.film_sha256===manifest.film_sha256&&sha(module.module_sha256)
   &&['scene','transition'].includes(module.role)&&['html-svg','canvas','three'].includes(module.engine)
   &&module.lifecycle?.protocol==='hyperframes-composable-module-1'
   &&module.lifecycle.exports?.mount==='mount'&&module.lifecycle.exports?.render_at==='renderAt'
   &&module.lifecycle.exports?.dispose==='dispose'&&module.lifecycle.scope==='mount-root'
   &&module.lifecycle.time==='absolute-global-frame'&&module.lifecycle.completion==='await-returned-promises'
   &&module.lifecycle.seek==='reconstructive','hyperframes_film_composable_module_required');
  modules.set(module.module_id,module);
 }
 let previous=0;
 const sceneIds=new Set(),used=new Set(),ranges=manifest.scenes.map(row=>({start:row.start_frame,end:row.end_frame}));
 for(let index=0;index<manifest.scenes.length;index++){
  const row=manifest.scenes[index],module=modules.get(row.module_id);
  check(module?.role==='scene'&&module.scene_id===row.scene_id&&!sceneIds.has(row.scene_id)
   &&!used.has(row.module_id)&&row.start_frame===previous
   &&integer(row.end_frame,row.start_frame+1,Math.min(row.start_frame+1440,manifest.clock.frame_count)),
   'hyperframes_film_scene_schedule_invalid');
  sceneIds.add(row.scene_id);used.add(row.module_id);previous=row.end_frame;
 }
 check(previous===manifest.clock.frame_count,'hyperframes_film_scene_schedule_invalid');
 let lastEnd=0;
 for(let index=0;index<manifest.boundaries.length;index++){
  const boundary=manifest.boundaries[index],left=manifest.scenes[index],right=manifest.scenes[index+1];
  check(boundary.schema==='video.hyperframes-film-boundary.v1'&&boundary.at_frame===left.end_frame
   &&boundary.from_scene_id===left.scene_id&&boundary.to_scene_id===right.scene_id
   &&boundary.from_module_sha256===modules.get(left.module_id).module_sha256
   &&boundary.to_module_sha256===modules.get(right.module_id).module_sha256,
   'hyperframes_film_boundary_schedule_invalid');
  if(boundary.transition!==null){
   const transition=boundary.transition,module=modules.get(transition?.module_id);
   check(module?.role==='transition'&&!used.has(module.module_id)
    &&module.module_sha256===transition.module_sha256
    &&integer(transition.start_frame,Math.max(left.start_frame,lastEnd),boundary.at_frame)
    &&integer(transition.end_frame,boundary.at_frame+1,right.end_frame)
    &&transition.end_frame-transition.start_frame<=48,'hyperframes_film_transition_schedule_invalid');
   ranges[index].end=transition.end_frame;ranges[index+1].start=transition.start_frame;
   lastEnd=transition.end_frame;used.add(module.module_id);
  }
 }
 check(used.size===modules.size,'hyperframes_film_unused_module');
 manifest.scenes.forEach((row,index)=>check(row.active_start_frame===ranges[index].start
  &&row.active_end_frame===ranges[index].end,'hyperframes_film_active_range_invalid'));
 return modules;
}

// verifiedManifest and its external digest must come from the independent
// package verifier. This function validates scheduling shape, not byte custody.
export function createHyperFramesFilmHost({verifiedManifest,manifestSha256,
 trustedLoadModule,createRoot,removeRoot}={}){
 check(sha(manifestSha256)&&typeof trustedLoadModule==='function'&&typeof createRoot==='function'
  &&typeof removeRoot==='function','hyperframes_film_host_input_invalid');
 const manifest=freeze(copy(verifiedManifest)),modules=schedule(manifest);
 let status='idle',busy=false,completedFrame=null,entries=[],completedSeeks=0;
 const snapshot=()=>({protocol:PROTOCOL,status,completed_frame:completedFrame,completed_seeks:completedSeeks,
  manifest_sha256:manifestSha256,film_sha256:manifest.film_sha256,
  live_roots:entries.filter(entry=>!entry.removed).length,pending_cleanup_modules:entries.length,
  package_execution_status:manifest.execution.status,
  evidence_scope:'lifecycle_calls_not_capture_visual_proof_or_runtime_admission'});
 async function cleanup(){
  const errors=[];
  for(const entry of [...entries].reverse()){
   if(!entry.disposed){
    try{await entry.exports.dispose();entry.disposed=true;}catch(error){errors.push(error);}
   }
   if(!entry.removed){
    try{await removeRoot(entry.root,entry.module);entry.removed=true;}catch(error){errors.push(error);}
   }
  }
  entries=entries.filter(entry=>!entry.disposed||!entry.removed);
  return errors;
 }
 function active(frame){
  const result=[],time=ratio(frame);
  for(const row of manifest.scenes){
   if(row.active_start_frame<=frame&&frame<row.active_end_frame){
    const module=modules.get(row.module_id);
    result.push({module,context:freeze({global_frame:frame,time,film_sha256:manifest.film_sha256,
     module_id:module.module_id,namespace:module.namespace,role:module.role,engine:module.engine,
     scene_id:row.scene_id,start_frame:row.start_frame,end_frame:row.end_frame,
     local_frame:frame-row.start_frame,phase:frame<row.start_frame?'entering':frame>=row.end_frame?'exiting':'active',
     states:copy(module.states)})});
   }
  }
  manifest.boundaries.forEach((boundary,index)=>{
   const transition=boundary.transition;
   if(transition&&transition.start_frame<=frame&&frame<transition.end_frame){
    const module=modules.get(transition.module_id);
    result.push({module,context:freeze({global_frame:frame,time,film_sha256:manifest.film_sha256,
     module_id:module.module_id,namespace:module.namespace,role:module.role,engine:module.engine,scene_id:null,
     boundary_index:index,from_scene_id:boundary.from_scene_id,to_scene_id:boundary.to_scene_id,
     at_frame:boundary.at_frame,start_frame:transition.start_frame,end_frame:transition.end_frame,
     local_frame:frame-transition.start_frame,phase:'active',objects:copy(boundary.objects),states:copy(module.states)})});
   }
  });
  return result;
 }
 async function renderFrame(frame){
  check(integer(frame,0,manifest.clock.frame_count-1),'hyperframes_film_frame_invalid');
  check(!busy,'hyperframes_film_busy');check(status==='idle','hyperframes_film_host_stopped');
  busy=true;status='executing';completedFrame=null;
  let previousCleanupFailed=false;
  try{
   const oldErrors=await cleanup();
   if(oldErrors.length){previousCleanupFailed=true;throw new AggregateError(oldErrors,'hyperframes_film_cleanup_failed');}
   const planned=active(frame);
   // Roots follow film scene order, then the associated transition order.
   // The factory owns root creation; this host never changes author styles.
   for(const {module,context} of planned){
    const exports=await trustedLoadModule(module,freeze({fresh_instance:true}));
    check(own(exports)&&['mount','renderAt','dispose'].every(name=>typeof exports[name]==='function'),
     'hyperframes_film_lifecycle_exports_missing');
    const entry={exports,module,root:null,disposed:false,removed:true};
    entries.push(entry);entry.root=await createRoot(module,context);
    check(entry.root!==null&&entry.root!==undefined,'hyperframes_film_mount_root_missing');
    check(!entries.some(other=>other!==entry&&!other.removed&&other.root===entry.root),
     'hyperframes_film_mount_root_reused');
    entry.removed=false;
    await exports.mount(entry.root,context);
   }
   // Mount all participants before rendering so both sides and their authored
   // transition share one completed global-frame operation.
   for(let index=0;index<planned.length;index++)await entries[index].exports.renderAt(planned[index].context);
   completedFrame=frame;completedSeeks++;status='idle';
   return {...snapshot(),active_modules:planned.map(({module,context})=>({module_id:module.module_id,
    module_sha256:module.module_sha256,engine:module.engine,phase:context.phase,local_frame:context.local_frame}))};
  }catch(error){
   status='failed';completedFrame=null;
   const errors=previousCleanupFailed?[]:await cleanup();
   if(errors.length)throw new AggregateError([error,...errors],'hyperframes_film_execution_and_cleanup_failed');
   throw error;
  }finally{busy=false;}
 }
 async function dispose(){
  check(!busy,'hyperframes_film_busy');busy=true;completedFrame=null;
  try{
   const errors=await cleanup();status=errors.length?'failed':'disposed';
   if(errors.length)throw new AggregateError(errors,'hyperframes_film_cleanup_failed');
   return snapshot();
  }finally{busy=false;}
 }
 const host=Object.freeze({renderFrame,dispose,getState:snapshot});
 hosts.set(host,{manifest,bound:false});return host;
}

// Supply captured genuine core methods only after runtime-byte and page
// validation. Doubles can exercise this adapter but cannot establish admission.
export function connectHyperFramesFilmCore({host,eventTarget,player,waitForSeekCompletion,forceReseek}={}){
 const data=hosts.get(host);
 check(data&&!data.bound&&typeof eventTarget?.addEventListener==='function'
  &&typeof eventTarget?.removeEventListener==='function'&&typeof player?.renderSeek==='function'
  &&typeof player?.getTime==='function'&&typeof player?.getDuration==='function'
  &&typeof waitForSeekCompletion==='function'&&typeof forceReseek==='function',
  'hyperframes_film_core_binding_invalid');
 const duration=data.manifest.clock.frame_count/24;
 check(Number.isFinite(player.getDuration())&&Math.abs(player.getDuration()-duration)<=1e-7,
  'hyperframes_film_core_duration_mismatch');
 const renderSeek=player.renderSeek.bind(player),getTime=player.getTime.bind(player);
 let request=null,closed=false,failed=false,coreSeeks=0,dispatches=0;
 function onSeek(event){
  if(!request||closed)return;
  const current=request,detail=event?.detail;
  const eventTime=detail?.time,waitUntil=detail?.waitUntil;
  const operation=current.tail.then(async()=>{
   check(Number.isFinite(eventTime)&&Math.abs(eventTime-current.frame/24)<=1e-7
    &&typeof waitUntil==='function','hyperframes_film_core_event_mismatch');
   dispatches++;return await host.renderFrame(current.frame);
  });
  // Register before returning from hf-seek; the real core closes this window.
  current.operations.push(operation);current.tail=operation;
  operation.catch(()=>{});
  try{
   check(typeof waitUntil==='function','hyperframes_film_core_event_mismatch');
   waitUntil.call(detail,operation);
  }catch(error){current.eventError=error;}
 }
 eventTarget.addEventListener('hf-seek',onSeek);data.bound=true;
 async function seekFrame(frame){
  check(integer(frame,0,data.manifest.clock.frame_count-1),'hyperframes_film_frame_invalid');
  check(!closed&&!failed,'hyperframes_film_core_stopped');check(!request,'hyperframes_film_busy');
  const current={frame,tail:Promise.resolve(),operations:[],eventError:null};request=current;
  try{
   coreSeeks++;await renderSeek(frame/24,{exact:true});await waitForSeekCompletion();
   if(current.operations.length===0){await forceReseek(frame/24);await waitForSeekCompletion();}
   const outcomes=await Promise.allSettled(current.operations);
   const rejected=outcomes.find(outcome=>outcome.status==='rejected');
   if(current.eventError)throw current.eventError;
   if(rejected)throw rejected.reason;
   const actual=getTime();
   check(current.operations.length>0&&Number.isFinite(actual)&&Math.abs(actual-frame/24)<=1e-7
    &&host.getState().completed_frame===frame,'hyperframes_film_core_seek_mismatch');
   return {...host.getState(),requested_frame:frame,actual_seconds:actual,core_seek_count:coreSeeks,
    dispatch_count:dispatches,core_binding:'supplied_trusted_core_methods'};
  }catch(error){
   failed=true;eventTarget.removeEventListener('hf-seek',onSeek);
   await Promise.allSettled(current.operations);
   try{await host.dispose();}catch(cleanupError){
    throw new AggregateError([error,cleanupError],'hyperframes_film_core_and_cleanup_failed');
   }
   throw error;
  }finally{request=null;}
 }
 async function close(){
  check(!request,'hyperframes_film_busy');closed=true;
  eventTarget.removeEventListener('hf-seek',onSeek);return await host.dispose();
 }
 return Object.freeze({seekFrame,close});
}
