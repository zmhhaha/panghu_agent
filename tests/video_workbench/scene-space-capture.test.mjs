import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {captureSceneSpace} from '../../video_workbench/scene_space_host.mjs';

const hash=raw=>createHash('sha256').update(raw).digest('hex');
const policy={schema:'video.scene-space-sample-capture.v1',format:'png',quality:null,
 max_samples:3,max_sample_bytes:4194304,max_total_sample_bytes:12582912};

// Transport/control fixtures test capture choices and budgets, not real pixels.
async function fixture({lossless=true,start=12,end=16,maxBytes=10000,mutate=null,screenshot=null}={}){
 const root=mkdtempSync(join(tmpdir(),'scene-capture-')),calls=[];let current=start;
 const runtime=Buffer.from('inert runtime input'),sha=hash(runtime);
 const request={schema:'video.scene-space-render-request.v1',engine:'canvas',static:false,
  scene_runtime_sha256:'runtime',source_sha256:'source',project:{},constraints:{width:1280,height:720},
  surface_runtime_sha256:sha,surface_budget_sha256:sha,geometry_runtime_sha256:sha,geometry_budget_sha256:sha,
  artwork_api_revision:3,max_frame_bytes:maxBytes,project_sha256:'project',...(lossless?{sample_capture:{...policy}}:{})};
 if(mutate)mutate(request);
 const binding={schema:'video.scene-space-render-binding.v1',backend:'canvas',runtime_sha256:'runtime',
  surface_runtime_sha256:sha,surface_budget_sha256:sha,geometry_runtime_sha256:sha,geometry_budget_sha256:sha,
  artwork_api_revision:3,source_sha256:'source',frame_range:{start_frame:start,end_frame:end},
  object_caches:[{}],total_cached_pixels:144,frozen:{plan:{viewport:{width:1280,height:720}}}};
 const page={setDefaultTimeout(){},on(){},async goto(){},async evaluate(callback){
  if(String(callback).includes('__trustedSceneSpaceObservation'))return{objects_painted:1,cache_pixels:144};
  if(String(callback).includes('__trustedHyperFramesProject'))return{core_seek_count:3};
 },async screenshot(options){calls.push({...options,frame:current});
  return screenshot?screenshot(options,current,calls):Buffer.from(options.type+':'+current);}};
 const context={async route(){},async newPage(){return page;},async newCDPSession(){return{};}};
 const chromium={async launch(){return{async newContext(){return context;},async close(){}};}};
 const engineRuntime={browserLaunchArgs:()=>[],async installEngineGuard(){},async verifyPageEngine(){return{engine:'canvas'};}};
 const projectRuntime={buildHyperFramesProject:()=>({project:{}}),hyperFramesRuntimeArtifact:()=>({runtimePath:'core',core_sha256:'core'}),
  async installHyperFramesProject(){},async seekHyperFramesProject(_,seconds){current=start+Math.round(seconds*24);}};
 try{
  const result=await captureSceneSpace({chromium,request,binding,root,engineRuntime,projectRuntime,
   resources:new Map(['scene_space_surface.mjs','scene_space_surface_budget.mjs','scene_space_geometry.mjs',
    'scene_space_geometry_budget.mjs'].map(name=>['/runtime/'+name,runtime]))});
  return{result,calls,retained:result.sample_frames.map(row=>readFileSync(join(root,row.file_name))),
   videoFrames:Array.from({length:end-start},(_,i)=>readFileSync(join(root,'frames',String(i).padStart(6,'0')+'.jpg')))};
 }finally{rmSync(root,{recursive:true,force:true});}
}

test('new sample policy keeps PNG evidence separate from original JPEG88 video frames',async()=>{
 const {result,calls,retained,videoFrames}=await fixture();
 assert.deepEqual(result.sample_capture,policy);
 assert.deepEqual(result.sample_frames.map(row=>row.frame),[12,14,15]);
 assert.equal(result.sample_capture_count,12);
 assert.equal(calls.filter(c=>c.type==='png').length,12);
 const video=calls.filter(c=>c.type==='jpeg');
 assert.deepEqual(video.map(c=>c.frame),[12,13,14,15]);
 assert.ok(video.every(c=>c.quality===88));
 assert.ok(calls.filter(c=>c.type==='png').every(c=>!Object.hasOwn(c,'quality')));
 assert.ok(videoFrames.every(bytes=>bytes.toString().startsWith('jpeg:')));
 assert.ok(retained.every(bytes=>bytes.toString().startsWith('png:')));
 assert.equal(result.sample_bytes,retained.reduce((sum,raw)=>sum+raw.length,0));
 assert.equal(result.frame_bytes,result.sample_bytes+videoFrames.reduce((sum,raw)=>sum+raw.length,0));
 for(const [i,row]of result.sample_frames.entries()){
  assert.equal(row.file_name,'sample-'+String(row.frame).padStart(6,'0')+'.png');
  assert.deepEqual(row.capture,{schema:'video.scene-space-frame-capture.v1',absolute_frame:row.frame,
   relative_frame:row.frame-12,format:'png',quality:null,sha256:hash(retained[i]),bytes:retained[i].length});
  assert.equal(row.time_seconds,(row.frame-12)/24);
 }
});

test('one-frame clip de-duplicates selected captures and retains its absolute frame',async()=>{
 const {result}=await fixture({start:71,end:72});
 assert.equal(result.sample_frames.length,1);assert.equal(result.sample_frames[0].capture.absolute_frame,71);
 assert.equal(result.sample_capture_count,4);assert.equal(result.sample_frames[0].capture.relative_frame,0);
});

test('no-policy snapshots keep exact JPEG sample shapes and screenshot quality',async()=>{
 const {result,calls}=await fixture({lossless:false});
 assert.ok(calls.every(row=>row.type==='jpeg'&&row.quality===88));
 assert.ok(!Object.hasOwn(result,'sample_capture')&&!Object.hasOwn(result,'sample_bytes'));
 assert.ok(result.sample_frames.every(row=>Object.keys(row).sort().join(',')==='bytes,file_name,frame,sha256,time_seconds'));
 assert.ok(result.sample_frames.every(row=>row.file_name.endsWith('.jpg')));
});

test('invalid policy cannot silently degrade to lossy captures',async()=>{
 for(const change of [r=>r.sample_capture.quality=88,r=>r.sample_capture.max_samples=4,
  r=>r.sample_capture.format='jpeg',r=>r.sample_capture.extra=true,r=>r.sample_capture=null]){
  await assert.rejects(fixture({mutate:change}),/scene_space_sample_capture_invalid/);
 }
});

test('PNG retention consumes the existing frame disk budget',async()=>{
 await assert.rejects(fixture({maxBytes:8}),/scene_space_frame_disk_budget_exceeded/);
});

test('oversized PNG and changed repeat bytes remain hard failures',async()=>{
 await assert.rejects(fixture({screenshot:options=>options.type==='png'?Buffer.alloc(4194305):Buffer.from('jpg')}),
  /scene_space_sample_capture_budget_exceeded/);
 let pngCalls=0;
 await assert.rejects(fixture({screenshot:(options,frame)=>Buffer.from(options.type+':'+frame+
  (options.type==='png'&&++pngCalls>3?':changed':''))}),/scene_space_reverse_or_repeat_mismatch/);
});
