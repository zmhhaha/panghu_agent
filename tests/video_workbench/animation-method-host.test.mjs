// Real local Chromium technical fixtures, never Agent output or quality approval.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {existsSync,mkdirSync,mkdtempSync,readFileSync,writeFileSync} from 'node:fs';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {captureMethodFrames} from '../../video_workbench/animation_method_host.mjs';
import * as runtime from '../../video_workbench/motion_renderer/engine_runtime.mjs';

const require=createRequire(import.meta.url);
let chromium;
try{({chromium}=require(process.env.ANIMATION_TEST_PLAYWRIGHT_MODULE||'playwright-core'));}catch{}
const executablePath=process.env.HYPERFRAMES_BROWSER_PATH||chromium?.executablePath();
const available=!!chromium&&!!executablePath&&existsSync(executablePath);
const repository=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const evidenceRoot=join(repository,'video_workbench/rendered/feedback-20261106/animation-lab/agent-creation-phase1/creation-product-r1/spec-consolidation-r1/canvas-three-r1');
const threePath=[
 join(repository,'video_workbench/animation_lab_webgl/node_modules/three/build/three.module.min.js'),
 join(repository,'video_workbench/rendered/feedback-20261106/animation-lab/assets/three.module.min.js'),
].find(path=>existsSync(path)&&createHash('sha256').update(readFileSync(path)).digest('hex')===runtime.THREE_ASSET_SHA256);

function canvasScene(extra='',seek='draw(t);'){
 return `<!doctype html><style>body{margin:0;background:#08121e}canvas{display:block}</style>
 <canvas width="320" height="180"></canvas><script>
 const canvas=document.querySelector('canvas'),ctx=canvas.getContext('2d');
 function draw(t){ctx.fillStyle='#08121e';ctx.fillRect(0,0,320,180);
 ctx.fillStyle='#ffb347';ctx.fillRect(10+Math.round(t*96),60,36,36);}
 window.__animationExperiment={ready:Promise.resolve(),seek(t){${seek}}};${extra}</script>`;
}

function threeScene(){
 return `<!doctype html><style>body{margin:0;background:#08121e}canvas{display:block}</style>
 <script type="module">import * as THREE from '/assets/three.module.min.js';
 const renderer=new THREE.WebGLRenderer({antialias:false,preserveDrawingBuffer:true});
 renderer.setSize(320,180);renderer.setClearColor('#08121e');document.body.append(renderer.domElement);
 const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(40,320/180,.1,100);camera.position.z=5;
 const mesh=new THREE.Mesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshBasicMaterial({color:'#ffb347'}));scene.add(mesh);
 window.__animationExperiment={ready:Promise.resolve(),seek(t){mesh.rotation.set(t*.4,t*.6,0);renderer.render(scene,camera);}};
 </script>`;
}

async function capture(engine,html,{legacy=false,budget=16*1024*1024,fullAssets=false}={}){
 mkdirSync(evidenceRoot,{recursive:true});
 const root=mkdtempSync(join(evidenceRoot,'host-'));
 const request={schema:'video.animation-method-render-request.'+(legacy?'v1':'v2'),
  ...(legacy?{}:{engine}),constraints:{width:320,height:180,fps:24,duration_seconds:1},
  max_evidence_bytes:budget};
 const resources=new Map([['/scene.html',Buffer.from(html)]]);
 if(fullAssets){
  request.assets_sha256={};
  for(const name of ['gsap.min.js','NotoSansCJK-Regular.ttc','three.module.min.js','three.LICENSE.txt']){
   const bytes=readFileSync(join(repository,'video_workbench/rendered/feedback-20261106/animation-lab/assets',name));
   resources.set('/assets/'+name,bytes);
   request.assets_sha256[name]=createHash('sha256').update(bytes).digest('hex');
  }
 }
 if(engine==='three'&&threePath)resources.set('/assets/three.module.min.js',readFileSync(threePath));
 const result=await captureMethodFrames({chromium,request,resources,root,engineRuntime:runtime,executablePath});
 writeFileSync(join(root,'capture.json'),JSON.stringify(result,null,2));
 return {result,root};
}

for(const engine of ['canvas','three']){
 test(`real ${engine} context supports absolute, reverse, repeat, idle and fresh-load checks`,
  {skip:!available||(engine==='three'&&!threePath)},async()=>{
   const {result,root}=await capture(engine,engine==='canvas'?canvasScene():threeScene());
   assert.equal(result.frame_count,24);assert.equal(result.frames.length,25);
   const required=engine==='canvas'?'2d':'webgl2';
   assert.equal(result.engine_observation.engine,engine);
   const context=result.engine_observation.contexts.find(row=>row.kind===required);
   assert.equal(context.visible,true);assert.equal(context.width,320);assert.equal(context.height,180);
   if(engine==='three'){
    assert.equal(context.software,true);assert.equal(context.context_lost,false);assert.equal(context.gl_error,0);
   }
   assert.notEqual(result.frames[0].sha256,result.frames[23].sha256);
   assert.equal(result.frames[0].sha256,result.frames[24].sha256);
   const checks=result.seek_checks;
   assert.equal(checks.probes.length,8);
   for(const key of ['absolute_seek','non_monotonic_seek_verified','repeated_seek_verified','idle_frame_verified','fresh_load_verified'])
    assert.equal(checks[key],true);
   assert.deepEqual(new Set(checks.probes.map(row=>row.kind)),new Set(['absolute','reverse','repeat','idle','reload']));
   for(const probe of checks.probes){
    const original=result.frames.find(row=>row.name===probe.reference_name);
    assert.equal(probe.timestamp_seconds,original.timestamp_seconds);assert.equal(probe.sha256,original.sha256);
    const bytes=readFileSync(join(root,'frames',probe.name));
    assert.equal(bytes.length,probe.bytes);assert.equal(createHash('sha256').update(bytes).digest('hex'),probe.sha256);
   }
  });
}

test('legacy HTML/SVG keeps the original receipt scope and rejects Canvas',
 {skip:!available},async()=>{
  const html='<svg width="320" height="180"><rect width="320" height="180" fill="#08121e"/></svg>'+
   '<script>window.__animationExperiment={seek(t){document.querySelector("rect").setAttribute("x",Math.round(t*20));}};</script>';
  const {result}=await capture('html-svg',html,{legacy:true});
  assert.equal(result.frames.length,25);assert.equal(result.seek_checks,undefined);
  assert.equal(result.engine_observation,undefined);
  await assert.rejects(capture('canvas',canvasScene(),{legacy:true}),/method_engine_not_admitted/);
 });

test('engine-aware HTML/SVG accepts the full four-asset freeze and checks fresh-load seeking',
 {skip:!available},async()=>{
  const html='<style>body{margin:0}</style><svg width="320" height="180"><rect width="320" height="180" fill="#08121e"/>'+
   '<circle cx="30" cy="80" r="20" fill="#ffb347"/></svg>'+
   '<script>window.__animationExperiment={seek(t){document.querySelector("circle").setAttribute("cx",30+Math.round(t*96));}};</script>';
  const {result}=await capture('html-svg',html,{fullAssets:true});
  assert.equal(result.frames.length,25);assert.equal(result.seek_checks.probes.length,8);
  assert.equal(result.seek_checks.fresh_load_verified,true);
  assert.equal(result.engine_observation.engine,'html-svg');
  assert.deepEqual(result.engine_observation.contexts,[]);
  assert.notEqual(result.frames[0].sha256,result.frames[23].sha256);
  await assert.rejects(capture('html-svg','<canvas width="320" height="180"></canvas>'+
   '<script>window.__animationExperiment={seek(){}};</script>',{fullAssets:true}),/engine_not_admitted/);
 });

for(const [label,scene,pattern] of [
 ['SVG substitute','<svg></svg><script>window.__animationExperiment={seek(){}};</script>',/native_context_missing/],
 ['wrong native context',canvasScene("canvas.getContext('webgl2');"),/scene_failed_or_blocked/],
 ['iframe',canvasScene("document.body.append(document.createElement('iframe'));"),/engine_not_admitted/],
 ['forged DOM observers',canvasScene("document.body.append(document.createElement('iframe'));document.querySelector=()=>null;document.getAnimations=()=>[];"),/engine_not_admitted/],
 ['audio',canvasScene("document.body.append(document.createElement('audio'));"),/engine_not_admitted/],
 ['video',canvasScene("document.body.append(document.createElement('video'));"),/engine_not_admitted/],
 ['Web Audio',canvasScene('new AudioContext();'),/scene_failed_or_blocked/],
 ['OffscreenCanvas transfer',canvasScene("document.createElement('canvas').transferControlToOffscreen();"),/scene_failed_or_blocked/],
 ['external network even when caught',canvasScene("fetch('https://example.invalid/unavailable').catch(()=>{});"),/scene_failed_or_blocked/],
 ['worker even when caught',canvasScene("try{new Worker(URL.createObjectURL(new Blob(['postMessage(1)'],{type:'text/javascript'})));}catch{}"),/scene_failed_or_blocked/],
 ['websocket even when caught',canvasScene("try{new WebSocket('wss://example.invalid/unavailable');}catch{}"),/scene_failed_or_blocked/],
 ['history-dependent motion',canvasScene('','canvas.dataset.x=String((Number(canvas.dataset.x)||0)+1);draw(Number(canvas.dataset.x)/24);'),/seek_mismatch/],
 ['unfrozen initialization randomness',canvasScene("const randomColor='#'+Math.floor(Math.random()*16777216).toString(16).padStart(6,'0');").replace("ctx.fillStyle='#ffb347'","ctx.fillStyle=randomColor"),/seek_mismatch/],
 ['autonomous CSS animation',canvasScene("const style=document.createElement('style');style.textContent='canvas{animation:move 1s infinite}@keyframes move{to{transform:translateX(30px)}}';document.head.append(style);"),/autonomous_animation/],
]){
 test(`Canvas host rejects ${label}`,{skip:!available},async()=>{
  await assert.rejects(capture('canvas',scene),pattern);
 });
}

test('evidence budget is enforced on actual frame bytes',{skip:!available},async()=>{
 await assert.rejects(capture('canvas',canvasScene(),{budget:10}),/evidence_budget_exceeded/);
});

test('a Three declaration and raw WebGL cannot replace loading the fixed Three module',
 {skip:!available},async()=>{
  const scene='<canvas width="320" height="180"></canvas><script>'+
   'const gl=document.querySelector("canvas").getContext("webgl2");'+
   'window.__animationExperiment={seek(t){gl.clearColor(t,0,0,1);gl.clear(gl.COLOR_BUFFER_BIT);}};</script>';
  await assert.rejects(capture('three',scene),/three_module_not_loaded/);
 });

test('Three context loss is observed from the native context and rejected',
 {skip:!available||!threePath},async()=>{
  const scene=threeScene().replace('renderer.render(scene,camera);',
   'renderer.render(scene,camera);renderer.getContext().getExtension("WEBGL_lose_context").loseContext();');
  await assert.rejects(capture('three',scene),/webgl_software_required/);
 });
