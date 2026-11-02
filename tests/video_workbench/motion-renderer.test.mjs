import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { sha, verifyInput, verifyRuntime, validateProbe, encodedDuration, muxAudioFilter } from '../../video_workbench/motion_renderer/contract.mjs';
import { runtimeArtifact, withObservationPage, observationSeekConfig, seekPageExact, screenshotObservation } from '../../video_workbench/motion_renderer/observe.mjs';
function fixture(){
 const path=mkdtempSync(join(tmpdir(),'motion-contract-'));
 const plan=Buffer.from('{}'),audio=Buffer.from('RIFF');
 const timeline=Buffer.from(JSON.stringify({schema:'video.motion-timeline.v1',audio_sha256:sha(audio),plan_sha256:sha(plan),duration_seconds:15}));
 const files={'index.html':Buffer.from('compiler page'),'beats.json':Buffer.from('{}'),'alignment.json':Buffer.from('{}'),'plan.json':plan,'timeline.json':timeline};
 const manifest=Buffer.from(JSON.stringify({schema:'video.motion-project.v1',compiler:'motion-compiler-1',template:'technical-diagrams-1',hyperframes:'0.8.149',gsap:'3.14.2',width:1280,height:720,fps:24,
  files:Object.fromEntries(Object.entries(files).map(([k,v])=>[k,sha(v)])),plan_sha256:sha(plan),timeline_sha256:sha(timeline),audio_sha256:sha(audio)}));
 for(const [name,data] of Object.entries({...files,'manifest.json':manifest,'audio.wav':audio}))writeFileSync(join(path,name),data);
 return {path,expected:sha(manifest),close:()=>rmSync(path,{recursive:true,force:true})};
}
test('verified manifest requires externally frozen digest',()=>{const f=fixture();try{assert.throws(()=>verifyInput(f.path),/expected_manifest/);assert.throws(()=>verifyInput(f.path,'a'.repeat(64)),/binding/);assert.equal(verifyInput(f.path,f.expected).timeline.duration_seconds,15);}finally{f.close();}});
test('tampered source cannot execute',()=>{const f=fixture();try{writeFileSync(join(f.path,'index.html'),'tampered');assert.throws(()=>verifyInput(f.path,f.expected),/input_digest/);}finally{f.close();}});
test('recording is bound independently',()=>{const f=fixture();try{writeFileSync(join(f.path,'audio.wav'),'different');assert.throws(()=>verifyInput(f.path,f.expected),/audio_binding/);}finally{f.close();}});
const goodProbe=()=>({streams:[{codec_type:'video',codec_name:'h264',width:1280,height:720,avg_frame_rate:'24/1',duration:'15',nb_frames:'360'},{codec_type:'audio',codec_name:'aac',duration:'15'}]});
test('requires actual video and complete audio',()=>{assert.doesNotThrow(()=>validateProbe(goodProbe(),{duration_seconds:15}));for(const change of [p=>p.streams.pop(),p=>p.streams[0].width=1920,p=>p.streams[0].nb_frames='1',p=>p.streams[0].avg_frame_rate='30/1',p=>p.streams[1].duration='14',p=>delete p.streams[1].duration]){const p=goodProbe();change(p);assert.throws(()=>validateProbe(p,{duration_seconds:15}));}});
const installedRuntime=()=>({schema:'video.motion-runtime.v1',hyperframes:'0.8.149',
 hyperframes_source:'2905162d925fc87fb8242e24ed0bbf025d307ea1',node:'22.20.0',browser:'HeadlessChrome/149',
 ffmpeg:'ffmpeg version 5.1.8',gsap:'3.14.2',gsap_sha256:'a'.repeat(64),font_sha256:'b'.repeat(64),
 dpkg_sha256:'c'.repeat(64),npm_lock_sha256:'d'.repeat(64)});
const imageDigest='sha256:'+'e'.repeat(64);
test('checks every frozen installed key and coordinator image digest',()=>{
 const installed=installedRuntime(),frozen={...installed,image_digest:imageDigest};
 assert.equal(verifyRuntime(frozen,installed,imageDigest).status,'matched');
 for(const key of Object.keys(installed)){
  const changed={...installed,[key]:key.endsWith('sha256')?'f'.repeat(64):installed[key]+'-different'};
  assert.throws(()=>verifyRuntime(frozen,changed,imageDigest),/runtime_/);
 }
 assert.throws(()=>verifyRuntime(frozen,installed,'sha256:'+'f'.repeat(64)),/image_digest_mismatch/);
 assert.throws(()=>verifyRuntime(frozen,installed),/external_image_digest_required/);
 // An image may not assert that its own data file proves its registry digest.
 assert.throws(()=>verifyRuntime(frozen,{...installed,image_digest:imageDigest},imageDigest),/fingerprint_invalid/);
});
test('null is explicitly an unbound experiment, never silently matched',()=>{
 assert.throws(()=>verifyRuntime(null,installedRuntime(),imageDigest),/fingerprint_required/);
 assert.throws(()=>verifyRuntime(undefined,installedRuntime(),imageDigest,{allowUnboundExperiment:true}),/fingerprint_invalid/);
 assert.throws(()=>verifyRuntime(null,installedRuntime(),undefined,{allowUnboundExperiment:true}),/external_image_digest/);
 const result=verifyRuntime(null,installedRuntime(),imageDigest,{allowUnboundExperiment:true});
 assert.deepEqual(result,{status:'unbound_experiment',image_digest:imageDigest,image_digest_source:'coordinator'});
});
test('a matching partial fingerprint cannot omit font or lock identity',()=>{
 const installed=installedRuntime(),frozen={...installed,image_digest:imageDigest};
 for(const key of ['font_sha256','npm_lock_sha256','image_digest']){
  const partial={...frozen};delete partial[key];
  assert.throws(()=>verifyRuntime(partial,installed,imageDigest),/fingerprint_invalid/);
 }
});
test('pads audio to the final complete frame without trimming the recording',()=>{
 const timeline={duration_seconds:1.001,leading_seconds:.0005,audio_seconds:1,trailing_seconds:.0005};
 const end=25/24;
 assert.equal(encodedDuration(timeline),end);
 assert.equal(muxAudioFilter(timeline),`[1:a]adelay=0.5:all=1,apad=whole_dur=${end}[audio]`);
 const probe=goodProbe();Object.assign(probe.streams[0],{duration:String(end),nb_frames:'25'});probe.streams[1].duration=String(end);
 assert.doesNotThrow(()=>validateProbe(probe,timeline));
 probe.streams[1].duration='1.001';
 assert.throws(()=>validateProbe(probe,timeline),/output_contract/);
});
test('rejects inconsistent audio timing instead of cutting to hide mismatch',()=>{
 assert.throws(()=>muxAudioFilter({duration_seconds:1,leading_seconds:0,audio_seconds:2,trailing_seconds:0}),/audio_timing_invalid/);
});
test('compiler frame padding is accepted without ceiling decimal frame counts twice',()=>{
 for(const duration_seconds of [361/24,15.04166667]){
  const timeline={duration_seconds,leading_seconds:0,audio_seconds:15.013,trailing_seconds:0};
  assert.equal(encodedDuration(timeline),361/24);
  assert.match(muxAudioFilter(timeline),/whole_dur=15\.041666666666666/);
  const probe=goodProbe();Object.assign(probe.streams[0],{duration:String(361/24),nb_frames:'361'});probe.streams[1].duration=String(361/24);
  assert.doesNotThrow(()=>validateProbe(probe,timeline));
 }
 for(const duration_seconds of [15,362/24])assert.throws(()=>muxAudioFilter({duration_seconds,leading_seconds:0,audio_seconds:15.013,trailing_seconds:0}),/audio_timing_invalid/);
});
test('observation frames use bounded JPEG bytes without reducing DOM observations',async()=>{
 const jpeg=Buffer.from([0xff,0xd8,0xff,0xd9]);let options;
 const page={screenshot:async value=>{options=value;return jpeg;}};
 assert.equal(await screenshotObservation(page,'/work/observations/a0-middle.jpg'),jpeg);
 assert.deepEqual(options,{path:'/work/observations/a0-middle.jpg',type:'jpeg',quality:75});
});
test('observation adapter uses Playwright API shapes and closes browser',async()=>{
 const calls=[];
 const page={
  goto:async(...args)=>calls.push(['goto',...args]),
  evaluate:async(fn,arg)=>{assert.equal(typeof fn,'function');calls.push([arg?'seek-config':'fonts',arg]);},
  addScriptTag:async value=>calls.push(['script',value]),
  waitForFunction:async(fn,arg,options)=>{assert.equal(typeof fn,'function');assert.equal(arg,null);assert.deepEqual(options,{timeout:30000,polling:100});calls.push(['ready']);}
 };
 const chromium={launch:async options=>{calls.push(['launch',options]);return{
  newContext:async options=>{calls.push(['context',options]);return{newPage:async()=>page};},
  close:async()=>calls.push(['closed'])
 };}};
 const result=await withObservationPage(chromium,{url:'http://127.0.0.1:1000/index.html',executablePath:'/fixed/shell',runtimePath:'/fixed/runtime.js'},async actual=>{
  assert.equal(actual,page);assert.equal(calls.at(-1)[0],'ready');return 'observed';
 });
 assert.equal(result,'observed');
 assert.deepEqual(calls.find(x=>x[0]==='context')[1],{viewport:{width:1280,height:720},deviceScaleFactor:1});
 assert.deepEqual(calls.find(x=>x[0]==='goto').slice(1),['http://127.0.0.1:1000/index.html',{waitUntil:'networkidle',timeout:30000}]);
 assert.deepEqual(calls.find(x=>x[0]==='script')[1],{path:'/fixed/runtime.js'});
 assert.deepEqual(calls.find(x=>x[0]==='seek-config')[1],observationSeekConfig);
 assert.ok(calls.findIndex(x=>x[0]==='seek-config')<calls.findIndex(x=>x[0]==='script'));
 assert.equal(calls[0][1].executablePath,'/fixed/shell');assert.equal(calls.at(-1)[0],'closed');
});
test('exact observation uses native renderSeek and completion without an hf bridge or RAF',async()=>{
 const priorWindow=globalThis.window;
 let current=-1,completed=false;
 const calls=[];
 try{
  globalThis.window={__hf:{seek:undefined},__player:{renderSeek:(time,options)=>{calls.push({time,options});current=time;},getTime:()=>current},
   __hfWaitForSeekCompletion:async()=>{completed=true;},requestAnimationFrame:()=>{throw Error('RAF must not be awaited');}};
  const page={evaluate:async(fn,arg)=>await fn(arg)};
  assert.equal(await seekPageExact(page,4.519231),4.519231);
  assert.deepEqual(calls,[{time:4.519231,options:{exact:true}}]);assert.equal(completed,true);
 }finally{if(priorWindow===undefined)delete globalThis.window;else globalThis.window=priorWindow;}
});
test('browser is closed if navigation or observation fails',async()=>{
 for(const failure of ['navigation','collection']){
  let closed=false;
  const page={goto:async()=>{if(failure==='navigation')throw Error('navigation');},evaluate:async()=>{},addScriptTag:async()=>{},waitForFunction:async()=>{}};
  const chromium={launch:async()=>({newContext:async()=>({newPage:async()=>page}),close:async()=>{closed=true;}})};
  await assert.rejects(withObservationPage(chromium,{url:'http://localhost',runtimePath:'/r'},async()=>{throw Error('collection');}),new RegExp(failure));
  assert.equal(closed,true);
 }
});
test('runtime IIFE is resolved from the published manifest and checksum',()=>{
 const path=mkdtempSync(join(tmpdir(),'motion-runtime-'));
 try{
  const raw=Buffer.from('window.__hf={};\n');
  writeFileSync(join(path,'hyperframe.runtime.iife.js'),raw);
  writeFileSync(join(path,'hyperframe.manifest.json'),JSON.stringify({sha256:sha(raw),artifacts:{iife:'hyperframe.runtime.iife.js'}}));
  assert.equal(runtimeArtifact(path),join(path,'hyperframe.runtime.iife.js'));
  writeFileSync(join(path,'hyperframe.runtime.iife.js'),'tampered');
  assert.throws(()=>runtimeArtifact(path),/runtime_digest_mismatch/);
  writeFileSync(join(path,'hyperframe.manifest.json'),JSON.stringify({sha256:sha(raw),artifacts:{iife:'../escape.js'}}));
  assert.throws(()=>runtimeArtifact(path),/runtime_name/);
 }finally{rmSync(path,{recursive:true,force:true});}
});
