// Shared trusted HyperFrames project host. Drawing decisions remain in Agent
// source; this module owns only project identity, time and completed frames.
import {readFileSync,lstatSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {join,dirname} from 'node:path';

export const HYPERFRAMES_PROJECT_SCHEMA='video.hyperframes-project.v1';
export const HYPERFRAMES_PROJECT_RUNTIME='hyperframes-core-project-1';
export const HYPERFRAMES_VERSION='0.8.149';
export const HYPERFRAMES_CORE_SHA256='675e02ee21f31540e3a6abaec061f86cea92a0178ae67a390700acb4f801dbbc';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const id=value=>typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/.test(value);
const ownObject=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const onFrame=(value,fps)=>Math.abs(value*fps-Math.round(value*fps))<1e-7;
const path=value=>typeof value==='string'&&value.length<=240&&/^[A-Za-z0-9][A-Za-z0-9_.\/-]*$/.test(value)
 &&value.split('/').every(part=>part&&part!=='.'&&part!=='..');

export function validateHyperFramesProject(project){
 if(!ownObject(project)||project.schema!==HYPERFRAMES_PROJECT_SCHEMA||!id(project.project_id)
  ||project.fps!==24||!Number.isInteger(project.width)||project.width<320||project.width>1920
  ||!Number.isInteger(project.height)||project.height<180||project.height>1080
  ||!finite(project.duration_seconds)||project.duration_seconds<=0||project.duration_seconds>600
  ||!onFrame(project.duration_seconds,project.fps)||!path(project.entry)||!project.entry.endsWith('.html')
  ||!Array.isArray(project.scenes)||project.scenes.length<1||project.scenes.length>24)
  throw Error('hyperframes_project_invalid');
 const ids=new Set();
 for(const scene of project.scenes){
  if(!ownObject(scene)||!id(scene.id)||ids.has(scene.id)||!path(scene.entry)||!scene.entry.endsWith('.html')
   ||!['html-svg','canvas','three'].includes(scene.engine)||!/^[0-9a-f]{64}$/.test(scene.source_sha256)
   ||!finite(scene.start_seconds)||scene.start_seconds<0||!onFrame(scene.start_seconds,project.fps)
   ||!finite(scene.duration_seconds)||scene.duration_seconds<=0||!onFrame(scene.duration_seconds,project.fps)
   ||scene.start_seconds+scene.duration_seconds>project.duration_seconds+1e-8)
   throw Error('hyperframes_project_scene_invalid');
  if(scene.leading_seconds!==undefined&&(!finite(scene.leading_seconds)||scene.leading_seconds<0
    ||scene.leading_seconds>scene.duration_seconds))throw Error('hyperframes_project_scene_invalid');
  if(scene.audio_seconds!==undefined&&(!finite(scene.audio_seconds)||scene.audio_seconds<=0
    ||(scene.leading_seconds??0)+scene.audio_seconds>scene.duration_seconds+1e-8))
   throw Error('hyperframes_project_scene_invalid');
  ids.add(scene.id);
 }
 return project;
}

function entries(value){
 if(value instanceof Map)return [...value];
 if(ownObject(value)&&[Object.prototype,null].includes(Object.getPrototypeOf(value)))return Object.entries(value);
 throw Error('hyperframes_project_resources_invalid');
}

export function buildHyperFramesProject(project,sources,assets={}){
 validateHyperFramesProject(project);
 const resources=new Map();let total=0;
 for(const [kind,items] of [['source',entries(sources)],['asset',entries(assets)]]){
  for(const [name,value] of items){
   if(!path(name)||resources.has(name)||!(typeof value==='string'||Buffer.isBuffer(value)||value instanceof Uint8Array)
    ||(kind==='source'&&!/\.(html|js|mjs|css)$/.test(name))
    ||(kind==='asset'&&!name.startsWith('assets/')))
    throw Error('hyperframes_project_resource_invalid');
   const bytes=Buffer.from(value);total+=bytes.length;
   if(bytes.length>(kind==='source'?512*1024:32*1024*1024)||total>48*1024*1024)
    throw Error('hyperframes_project_resource_budget');
   if(kind==='source'&&!Buffer.from(bytes.toString('utf8')).equals(bytes))
    throw Error('hyperframes_project_source_not_utf8');
   resources.set(name,bytes);
  }
 }
 if(!resources.has(project.entry))throw Error('hyperframes_project_entry_missing');
 for(const scene of project.scenes){
  const bytes=resources.get(scene.entry);
  if(!bytes||hash(bytes)!==scene.source_sha256)throw Error('hyperframes_project_scene_digest_mismatch');
 }
 const document=JSON.parse(JSON.stringify(project));
 return {project:document,resources,entry:document.entry,
  files_sha256:Object.fromEntries([...resources].map(([name,bytes])=>[name,hash(bytes)]))};
}

export function hyperFramesRuntimeArtifact(dist='/opt/renderer/node_modules/hyperframes/dist'){
 const read=(file,max)=>{const stat=lstatSync(file);
  if(!stat.isFile()||stat.isSymbolicLink()||stat.size>max)throw Error('hyperframes_runtime_file_invalid');
  return readFileSync(file);};
 const packageJson=JSON.parse(read(join(dirname(dist),'package.json'),256*1024));
 const manifest=JSON.parse(read(join(dist,'hyperframe.manifest.json'),256*1024));
 const name=manifest.artifacts?.iife;
 if(packageJson.version!==HYPERFRAMES_VERSION||!/^[-A-Za-z0-9_.]+\.js$/.test(name||'')
  ||manifest.sha256!==HYPERFRAMES_CORE_SHA256)throw Error('hyperframes_runtime_identity_mismatch');
 const runtimePath=join(dist,name),bytes=read(runtimePath,16*1024*1024);
 if(hash(bytes)!==HYPERFRAMES_CORE_SHA256)throw Error('hyperframes_runtime_digest_mismatch');
 return {runtimePath,version:HYPERFRAMES_VERSION,core_sha256:HYPERFRAMES_CORE_SHA256};
}

export async function withHyperFramesTimeout(operation,timeoutMs,label){
 if(!Number.isInteger(timeoutMs)||timeoutMs<1||timeoutMs>60000)throw Error('hyperframes_timeout_invalid');
 let timer;
 try{return await Promise.race([operation,new Promise((_,reject)=>{
  timer=setTimeout(()=>reject(Error(label)),timeoutMs);
 })]);}finally{clearTimeout(timer);}
}

// This function is serialized by Playwright. It never renders source directly
// from Node: a compatibility bridge subscribes to the genuine core event.
export async function prepareHyperFramesPage({project,legacyBridge}){
 await document.fonts.ready;
 await window.__hfSceneReady;
 if(window.__trustedHyperFramesProject)throw Error('hyperframes_project_already_installed');
 if(legacyBridge){
  if(project.scenes.length!==1)throw Error('hyperframes_legacy_bridge_requires_single_scene');
  await window.__animationExperiment?.ready;
  if(typeof window.__animationExperiment?.seek!=='function')throw Error('hyperframes_legacy_seek_missing');
 }
 const root=document.body;
 if(!root)throw Error('hyperframes_project_root_missing');
 root.setAttribute('data-composition-id',project.project_id);
 root.setAttribute('data-duration',String(project.duration_seconds));
 root.setAttribute('data-width',String(project.width));root.setAttribute('data-height',String(project.height));
 const state={schema:'video.hyperframes-project-observation.v1',runtime:'hyperframes-core-project-1',
  project_id:project.project_id,legacy_bridge:legacyBridge,dispatch_count:0,core_seek_count:0,
  last_dispatched_seconds:null,actual_seconds:null};
 window.addEventListener('hf-seek',event=>{
  const detail=event.detail;
  if(!detail||!Number.isFinite(detail.time)||typeof detail.waitUntil!=='function')
   throw Error('hyperframes_seek_event_invalid');
  state.dispatch_count++;state.last_dispatched_seconds=detail.time;
  if(legacyBridge){
   // Keep leading/trailing holds in the project clock; source time is local.
   const scene=project.scenes[0];
   const local=Math.max(0,Math.min(scene.audio_seconds??scene.duration_seconds,
    detail.time-scene.start_seconds-(scene.leading_seconds??0)));
   detail.waitUntil(Promise.resolve().then(()=>window.__animationExperiment.seek(local)));
  }
 });
 for(const scene of project.scenes)Object.freeze(scene);
 Object.freeze(project.scenes);Object.freeze(project);
 const activate=()=>{
  const player=window.__player,complete=window.__hfWaitForSeekCompletion,force=window.__hfReseekGpu;
  if(typeof player?.renderSeek!=='function'||typeof player?.getTime!=='function'
   ||typeof player?.getDuration!=='function'||typeof complete!=='function'||typeof force!=='function')
   throw Error('hyperframes_core_missing');
  const duration=player.getDuration();
  if(!Number.isFinite(duration)||Math.abs(duration-project.duration_seconds)>1e-7)
   throw Error('hyperframes_core_duration_mismatch');
  const seek=player.renderSeek.bind(player),time=player.getTime.bind(player);
  Object.defineProperty(window,'__trustedHyperFramesSeek',{configurable:false,writable:false,
   value:async seconds=>{
    if(!Number.isFinite(seconds)||seconds<0||seconds>project.duration_seconds)
     throw Error('hyperframes_project_seek_invalid');
    const before=state.dispatch_count;
    state.core_seek_count++;
    await seek(seconds,{exact:true});await complete();
    // Core adapter deduplication must not turn repeated captures into no-ops.
    if(state.dispatch_count===before){force(seconds);await complete();}
    const actual=time();
    if(!Number.isFinite(actual)||Math.abs(actual-seconds)>1e-7
     ||state.dispatch_count<=before||Math.abs(state.last_dispatched_seconds-seconds)>1e-7)
     throw Error('hyperframes_core_seek_mismatch');
    state.actual_seconds=actual;
    return {...state,requested_seconds:seconds};
   }});
  return {...state,duration_seconds:duration};
 };
 Object.defineProperty(window,'__trustedHyperFramesProject',{
  value:Object.freeze({get state(){return {...state};},project,activate}),writable:false,configurable:false});
 window.__HF_EXPORT_RENDER_SEEK_CONFIG={mode:'preview-phase',diagnostics:false,step:1/120,
  offsetFraction:.5,fps:project.fps,fpsSource:'render-options',owner:'runtime'};
 return {project_id:project.project_id};
}

// Captures real core methods after loading the checksum-verified IIFE. No
// caller can satisfy this contract just by reporting a library version.
export function bindHyperFramesCore(){
 const trusted=window.__trustedHyperFramesProject;
 if(!trusted||typeof trusted.activate!=='function')throw Error('hyperframes_core_missing');
 return trusted.activate();
}

export async function installHyperFramesProject(page,{project,runtimePath,legacyBridge=false,timeoutMs=20000}={}){
 validateHyperFramesProject(project);
 if(typeof legacyBridge!=='boolean'||typeof runtimePath!=='string')throw Error('hyperframes_install_invalid');
 const stat=lstatSync(runtimePath);
 if(!stat.isFile()||stat.isSymbolicLink()||stat.size>16*1024*1024
  ||hash(readFileSync(runtimePath))!==HYPERFRAMES_CORE_SHA256)throw Error('hyperframes_runtime_digest_mismatch');
 const run=async()=>{
  await page.evaluate(prepareHyperFramesPage,{project,legacyBridge});
  await page.addScriptTag({path:runtimePath});
  await page.waitForFunction(()=>window.__playerReady===true&&window.__renderReady===true
   &&typeof window.__player?.renderSeek==='function',null,{timeout:timeoutMs,polling:50});
  const observation=await page.evaluate(bindHyperFramesCore);
  return {...observation,version:HYPERFRAMES_VERSION,core_sha256:HYPERFRAMES_CORE_SHA256};
 };
 return await withHyperFramesTimeout(run(),timeoutMs,'hyperframes_project_ready_timeout');
}

export async function seekHyperFramesProject(page,seconds,{timeoutMs=15000}={}){
 if(!finite(seconds)||seconds<0)throw Error('hyperframes_seek_time_invalid');
 return await withHyperFramesTimeout(page.evaluate(async time=>{
  const trusted=window.__trustedHyperFramesProject;
  if(!trusted||time>trusted.project.duration_seconds||typeof window.__trustedHyperFramesSeek!=='function')
   throw Error('hyperframes_project_seek_invalid');
  return await window.__trustedHyperFramesSeek(time);
 },seconds),timeoutMs,'hyperframes_project_seek_timeout');
}
