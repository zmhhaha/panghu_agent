// Offline contract and lifecycle checks. The browser/core doubles below do
// not constitute actual HyperFrames rendering or visual quality evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import vm from 'node:vm';
import {buildHyperFramesProject,validateHyperFramesProject,prepareHyperFramesPage,
 bindHyperFramesCore,withHyperFramesTimeout,HYPERFRAMES_CORE_SHA256}
 from '../../video_workbench/motion_renderer/hyperframes_project.mjs';
import {verifyRuntime} from '../../video_workbench/motion_renderer/contract.mjs';

const source='<svg xmlns="http://www.w3.org/2000/svg"></svg>';
const sha=value=>createHash('sha256').update(value).digest('hex');
function project(){return {schema:'video.hyperframes-project.v1',project_id:'project-1',
 width:320,height:180,fps:24,duration_seconds:2,entry:'index.html',scenes:[
  {id:'scene-1',start_seconds:0,duration_seconds:1,entry:'scenes/one/scene.html',source_sha256:sha(source),engine:'canvas'},
  {id:'scene-2',start_seconds:1,duration_seconds:1,entry:'scenes/two/scene.html',source_sha256:sha(source),engine:'three'}]};}

test('one whole project binds multiple source files and original UTF-8 entries',()=>{
 const p=project(),sources={'index.html':'<main></main>',
  'scenes/one/scene.html':source,'scenes/two/scene.html':source,
  'scenes/one/scene.js':'export function draw(t){}'};
 const built=buildHyperFramesProject(p,sources,{'assets/font.ttc':Buffer.from('fixture')});
 assert.equal(built.entry,'index.html');assert.equal(built.resources.size,5);
 assert.equal(built.files_sha256['scenes/one/scene.html'],p.scenes[0].source_sha256);
 p.scenes[0].id='changed';sources['index.html']='changed';
 assert.equal(built.project.scenes[0].id,'scene-1');assert.equal(built.resources.get('index.html').toString(),'<main></main>');
});

test('manifest rejects changed source, traversal, foreign resources and non-frame scene boundaries',()=>{
 const p=project(),files={'index.html':'<main/>','scenes/one/scene.html':source,'scenes/two/scene.html':source};
 assert.throws(()=>buildHyperFramesProject(p,{...files,'scenes/one/scene.html':'changed'}),/scene_digest/);
 for(const name of ['../secret.js','/absolute.js','scenes/../secret.js','https://remote/a.js','a.js?query=1','a//b.js'])
  assert.throws(()=>buildHyperFramesProject(p,{...files,[name]:'bad'}),/resource_invalid/);
 assert.throws(()=>buildHyperFramesProject(p,files,{'foreign.js':'bad'}),/resource_invalid/);
 assert.throws(()=>validateHyperFramesProject({...p,scenes:[{...p.scenes[0],start_seconds:.01}]}),/scene_invalid/);
 assert.throws(()=>validateHyperFramesProject({...p,scenes:[p.scenes[0],p.scenes[0]]}),/scene_invalid/);
 assert.throws(()=>validateHyperFramesProject({...p,duration_seconds:601}),/project_invalid/);
});

test('project resources enforce source type and actual byte budgets',()=>{
 const p=project(),files={'index.html':'<main/>','scenes/one/scene.html':source,'scenes/two/scene.html':source};
 assert.throws(()=>buildHyperFramesProject(p,{...files,'file.json':'{}'}),/resource_invalid/);
 assert.throws(()=>buildHyperFramesProject(p,{...files,'file.js':Buffer.from([0xff])}),/source_not_utf8/);
 assert.throws(()=>buildHyperFramesProject(p,{...files,'big.js':'x'.repeat(512*1024+1)}),/resource_budget/);
 assert.equal(HYPERFRAMES_CORE_SHA256.length,64);
});

async function browserFixture({legacy=false,deduplicate=true,brokenClock=false}={}){
 const listeners=new Map(),attrs=new Map(),pending=[];
 let clock=0,previous=null,drawCount=0,fail=false,actual=0;
 const win={addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(fn);},
  __animationExperiment:{ready:Promise.resolve(),async seek(t){await Promise.resolve();actual=t;drawCount++;}}};
 const dispatch=t=>{let accepting=true;const detail={time:t,waitUntil(p){
  if(!accepting)throw Error('late_wait_until');pending.push(Promise.resolve(p));}};
  try{for(const listener of listeners.get('hf-seek')||[])listener({detail});}finally{accepting=false;}};
 win.__player={renderSeek(t,options){assert.equal(options.exact,true);clock=t;
  if(!deduplicate||previous!==t){previous=t;dispatch(t);}},getTime(){return brokenClock?clock+1:clock;},getDuration(){return 2;}};
 win.__hfWaitForSeekCompletion=async()=>{while(pending.length)await Promise.all(pending.splice(0));};
 win.__hfReseekGpu=dispatch;
 const context=vm.createContext({window:win,document:{fonts:{ready:Promise.resolve()},body:{setAttribute(k,v){attrs.set(k,v);}}},
  Promise,Number,Math,Error});
 const p=project();if(legacy)p.scenes=[{...p.scenes[0],duration_seconds:2,leading_seconds:.25,audio_seconds:1.5}];
 if(!legacy)win.addEventListener('hf-seek',event=>event.detail.waitUntil(Promise.resolve().then(()=>{
  if(fail)throw Error('drawing_failed');actual=event.detail.time;drawCount++;
 })));
 await vm.runInContext('('+prepareHyperFramesPage.toString()+')',context)({project:p,legacyBridge:legacy});
 vm.runInContext('('+bindHyperFramesCore.toString()+')()',context);
 return {win,attrs,p,actual:()=>actual,drawCount:()=>drawCount,setFailure:()=>{fail=true;}};
}

test('core exact clock waits for native asynchronous draw at forward/reverse/random/repeated times',async()=>{
 const f=await browserFixture();
 for(const t of [0,1.75,.5,1.25,0,.5,.5]){
  const prior=f.drawCount(),proof=await f.win.__trustedHyperFramesSeek(t);
  assert.equal(f.actual(),t);assert.equal(proof.actual_seconds,t);
  assert.ok(f.drawCount()>prior);assert.ok(proof.dispatch_count>=proof.core_seek_count);
 }
 assert.equal(f.attrs.get('data-composition-id'),'project-1');
 assert.equal(f.attrs.get('data-duration'),'2');
});

test('core propagates async rejected work and detects a wrong clock',async()=>{
 const f=await browserFixture();f.setFailure();
 await assert.rejects(f.win.__trustedHyperFramesSeek(.5),/drawing_failed/);
 const broken=await browserFixture({brokenClock:true});
 await assert.rejects(broken.win.__trustedHyperFramesSeek(.5),/core_seek_mismatch/);
});

test('explicit legacy bridge applies local speech clock through core event completion',async()=>{
 const f=await browserFixture({legacy:true});
 await f.win.__trustedHyperFramesSeek(.1);assert.equal(f.actual(),0);
 await f.win.__trustedHyperFramesSeek(1);assert.equal(f.actual(),.75);
 await f.win.__trustedHyperFramesSeek(1.9);assert.equal(f.actual(),1.5);
 assert.equal(f.win.__trustedHyperFramesProject.state.legacy_bridge,true);
});

test('multi-scene legacy bridge is rejected rather than silently using one source clock',async()=>{
 const context=vm.createContext({window:{__animationExperiment:{seek(){}}},
  document:{fonts:{ready:Promise.resolve()}},Error});
 await assert.rejects(vm.runInContext('('+prepareHyperFramesPage.toString()+')',context)(
  {project:project(),legacyBridge:true}),/requires_single_scene/);
});

test('bounded async readiness rejects instead of leaving a renderer hung',async()=>{
 await assert.rejects(withHyperFramesTimeout(new Promise(()=>{}),10,'ready_timeout'),/ready_timeout/);
 assert.equal(await withHyperFramesTimeout(Promise.resolve('ready'),20,'ready_timeout'),'ready');
});

test('new runtime identity independently binds real core and project host instead of v2 admission',()=>{
 const image='sha256:'+'e'.repeat(64),installed={schema:'video.motion-runtime.v3',hyperframes:'0.8.149',
  hyperframes_source:'2905162d925fc87fb8242e24ed0bbf025d307ea1',node:'22',browser:'Chromium',ffmpeg:'ffmpeg',
  gsap:'3.14.2',gsap_sha256:'a'.repeat(64),font_sha256:'b'.repeat(64),dpkg_sha256:'c'.repeat(64),
  npm_lock_sha256:'d'.repeat(64),three:'0.170.0',
  three_sha256:'08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7',
  three_license_sha256:'4c40a1ef62450b857c3b2aaf294936304cd552d965fbcd9d32d4c5bcf4ba4454',
  engine_runtime_sha256:'a'.repeat(64),creative_host_sha256:'b'.repeat(64),
  hyperframes_core_sha256:HYPERFRAMES_CORE_SHA256,hyperframes_project_host_sha256:'c'.repeat(64)};
 const frozen={...installed,image_digest:image};
 assert.equal(verifyRuntime(frozen,installed,image).status,'matched');
 assert.throws(()=>verifyRuntime(frozen,{...installed,hyperframes_core_sha256:'f'.repeat(64)},image),/runtime_version/);
 assert.throws(()=>verifyRuntime(frozen,{...installed,hyperframes_project_host_sha256:'f'.repeat(64)},image),/installed_mismatch/);
 const old={...frozen,schema:'video.motion-runtime.v2'};
 delete old.hyperframes_core_sha256;delete old.hyperframes_project_host_sha256;
 assert.throws(()=>verifyRuntime(old,installed,image),/installed_mismatch/);
});
