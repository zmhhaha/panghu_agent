import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {basename,dirname,join,resolve} from 'node:path';
import test from 'node:test';
import {captureSceneSpace,collectSceneDrawingExecution} from '../../video_workbench/scene_space_host.mjs';

const source='export function renderCanvas(){ return 1; }';
const sha=createHash('sha256').update(source).digest('hex');
const library={assets:{'ink-landscape.mjs':sha}};
const fn=count=>({functionName:'renderCanvas',ranges:[{startOffset:7,endOffset:source.length,count}]});

test('capture drains and verifies a document before navigation retires its script IDs',async()=>{
 const root=mkdtempSync(join(tmpdir(),'scene-coverage-'));
 const events=[];let current=0,pending=[],routeHandler;
 const session={async send(method,args){
  if(method==='Profiler.takePreciseCoverage'){
   events.push('collect:'+current);
   const result=pending;pending=[];return{result};
  }
  if(method==='Debugger.getScriptSource'){
   assert.equal(args.scriptId,String(current),'Debugger script ID belongs to a retired document');
   events.push('source:'+current);return{scriptSource:source};
  }
  return{};
 }};
 const page={setDefaultTimeout(){},on(){},
  async goto(url){
   current++;events.push('navigate:'+current);
   const origin=new URL(url).origin;
   pending.push({url:origin+'/assets/ink-landscape.mjs',scriptId:String(current),functions:[fn(2)]});
   await routeHandler({request:()=>({url:()=>origin+'/assets/ink-landscape.mjs',
    resourceType:()=> 'script',method:()=> 'GET'}),continue:async()=>{},abort:async()=>assert.fail('unexpected blocked script')});
  },
  async evaluate(callback){
   if(String(callback).includes('__trustedSceneSpaceObservation'))return{objects_painted:1,cache_pixels:144};
   if(String(callback).includes('__trustedHyperFramesProject'))return{core_seek_count:3};
  },
  async screenshot(){return Buffer.from('inert pixel transport fixture');}
 };
 const context={async route(_,handler){routeHandler=handler;},async newPage(){return page;},
  async newCDPSession(){return session;}};
 const chromium={async launch(){return{async newContext(){return context;},async close(){}};}};
 const request={schema:'video.scene-space-render-request.v1',engine:'canvas',static:true,
  scene_runtime_sha256:'runtime',source_sha256:'source',project:{},constraints:{width:1280,height:720},
  drawing_libraries:{'ink-landscape':library},max_frame_bytes:1024,project_sha256:'project'};
 const binding={schema:'video.scene-space-render-binding.v1',backend:'canvas',runtime_sha256:'runtime',
  source_sha256:'source',frame_range:{start_frame:0,end_frame:1},object_caches:[{}],total_cached_pixels:144,
  frozen:{plan:{viewport:{width:1280,height:720}}}};
 const engineRuntime={browserLaunchArgs:()=>[],async installEngineGuard(){},async verifyPageEngine(){return{engine:'canvas'};}};
 const projectRuntime={buildHyperFramesProject:()=>({project:{}}),hyperFramesRuntimeArtifact:()=>({runtimePath:'core',core_sha256:'core'}),
  async installHyperFramesProject(){},async seekHyperFramesProject(){}};
 try{
  const result=await captureSceneSpace({chromium,request,binding,root,engineRuntime,projectRuntime,
   resources:new Map([['/assets/ink-landscape.mjs',Buffer.from(source)]])});
  assert.deepEqual(events,['navigate:1','collect:1','source:1','navigate:2','collect:2','source:2']);
  assert.deepEqual(result.drawing_library_observation.api_execution.functions,[{
   function_name:'renderCanvas',start_offset:7,end_offset:source.length,call_count:4}]);
 }finally{
  assert.equal(dirname(resolve(root)),resolve(tmpdir()));
  assert.ok(basename(root).startsWith('scene-coverage-'));
  rmSync(root,{recursive:true,force:true});
 }
});

test('coverage still rejects changed module bytes even when its URL matches',async()=>{
 const session={async send(method){return method==='Profiler.takePreciseCoverage'
  ?{result:[{url:'http://local/assets/ink-landscape.mjs',scriptId:'1',functions:[fn(1)]}]}
  :{scriptSource:'changed source'};}};
 await assert.rejects(collectSceneDrawingExecution(session,'http://local',library),/scene_space_drawing_coverage_changed/);
});

test('unavailable source is not silently promoted into execution evidence',async()=>{
 const session={async send(method){
  if(method==='Profiler.takePreciseCoverage')return{result:[{url:'http://local/assets/ink-landscape.mjs',scriptId:'old',functions:[fn(1)]}]};
  throw Error('No script for id: old');
 }};
 await assert.rejects(collectSceneDrawingExecution(session,'http://local',library),/No script for id/);
});

test('uncalled functions do not gain a synthetic positive call count',async()=>{
 const session={async send(method){return method==='Profiler.takePreciseCoverage'
  ?{result:[{url:'http://local/assets/ink-landscape.mjs',scriptId:'1',functions:[fn(0)]}]}
  :{scriptSource:source};}};
 assert.deepEqual(await collectSceneDrawingExecution(session,'http://local',library),[]);
});
