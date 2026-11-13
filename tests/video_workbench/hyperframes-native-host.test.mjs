// Opt-in real-core/browser protocol fixtures, not Agent samples or aesthetic
// approval. No dependency downloads or implicit output into the system disk.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {existsSync,readFileSync,mkdirSync,mkdtempSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {captureMethodFrames} from '../../video_workbench/animation_method_host.mjs';
import * as engineRuntime from '../../video_workbench/motion_renderer/engine_runtime.mjs';
import {HYPERFRAMES_CORE_SHA256} from '../../video_workbench/motion_renderer/hyperframes_project.mjs';

const require=createRequire(import.meta.url);
let chromium;
try{({chromium}=require(process.env.ANIMATION_TEST_PLAYWRIGHT_MODULE||'playwright-core'));}catch{}
const executablePath=process.env.HYPERFRAMES_BROWSER_PATH||chromium?.executablePath();
const hyperFramesDist=process.env.HYPERFRAMES_TEST_DIST;
const evidence=process.env.ANIMATION_HYPERFRAMES_EVIDENCE_DIR;
const assetsRoot=process.env.ANIMATION_TEST_ASSETS;
const sha=value=>createHash('sha256').update(value).digest('hex');
const available=!!chromium&&!!executablePath&&existsSync(executablePath)&&!!evidence&&!!hyperFramesDist
 &&existsSync(join(hyperFramesDist,'hyperframe.runtime.iife.js'));
const svg=`<!doctype html><style>body{margin:0;background:#08121e}</style>
 <svg width="320" height="180"><circle cx="30" cy="90" r="20" fill="#ffb347"/></svg>
 <script>window.__hfSceneReady=Promise.resolve();window.addEventListener('hf-seek',event=>{
 event.detail.waitUntil(Promise.resolve().then(()=>document.querySelector('circle').setAttribute('cx',30+Math.round(event.detail.time*96))));
 });</script>`;
const canvas=`<!doctype html><style>body{margin:0;background:#08121e}</style>
 <canvas width="320" height="180"></canvas><script>
 const ctx=document.querySelector('canvas').getContext('2d');
 window.__hfSceneReady=Promise.resolve();window.addEventListener('hf-seek',event=>{
 event.detail.waitUntil(Promise.resolve().then(()=>{ctx.fillStyle='#08121e';ctx.fillRect(0,0,320,180);
 ctx.fillStyle='#ffb347';ctx.fillRect(10+Math.round(event.detail.time*96),60,36,36);}));});</script>`;
const three=`<!doctype html><style>body{margin:0;background:#08121e}</style>
 <script type="module">import * as THREE from '/assets/three.module.min.js';
 const renderer=new THREE.WebGLRenderer({antialias:false,preserveDrawingBuffer:true});
 renderer.setSize(320,180);renderer.setClearColor('#08121e');document.body.append(renderer.domElement);
 const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(40,320/180,.1,100);camera.position.z=5;
 const mesh=new THREE.Mesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshBasicMaterial({color:'#ffb347'}));scene.add(mesh);
 window.__hfSceneReady=Promise.resolve();window.addEventListener('hf-seek',event=>{
 event.detail.waitUntil(Promise.resolve().then(()=>{mesh.rotation.set(event.detail.time*.4,event.detail.time*.6,0);renderer.render(scene,camera);}));});</script>`;

async function capture(engine,html){
 mkdirSync(evidence,{recursive:true});const root=mkdtempSync(join(evidence,'hyperframes-'+engine+'-'));
 const project={schema:'video.hyperframes-project.v1',project_id:'real-core-'+engine,
  width:320,height:180,fps:24,duration_seconds:1,entry:'scene.html',scenes:[
   {id:'scene',start_seconds:0,duration_seconds:1,entry:'scene.html',source_sha256:sha(html),engine}]};
 const request={schema:'video.animation-method-render-request.v3',engine,project,
  project_sha256:sha(JSON.stringify(project)),legacy_bridge:false,
  constraints:{width:320,height:180,fps:24,duration_seconds:1},max_evidence_bytes:16*1024*1024};
 const resources=new Map([['/scene.html',Buffer.from(html)]]);
 if(engine==='three')resources.set('/assets/three.module.min.js',readFileSync(join(assetsRoot,'three.module.min.js')));
 const result=await captureMethodFrames({chromium,request,resources,root,engineRuntime,executablePath,hyperFramesDist});
 writeFileSync(join(root,'capture.json'),JSON.stringify(result,null,2));return result;
}

for(const [engine,html] of [['html-svg',svg],['canvas',canvas],['three',three]]){
 test(`real HyperFrames core drives native ${engine} with async, reverse, repeat, idle and reload`,
  {skip:!available||(engine==='three'&&(!assetsRoot||!existsSync(join(assetsRoot,'three.module.min.js'))))},async()=>{
   const result=await capture(engine,html);
   assert.equal(result.frame_count,24);assert.equal(result.frames.length,25);
   assert.notEqual(result.frames[0].sha256,result.frames[23].sha256);
   assert.equal(result.frames[0].sha256,result.frames[24].sha256);
   assert.equal(result.seek_checks.non_monotonic_seek_verified,true);
   assert.equal(result.seek_checks.repeated_seek_verified,true);
   assert.equal(result.seek_checks.fresh_load_verified,true);
   assert.equal(result.project_runtime.core_sha256,HYPERFRAMES_CORE_SHA256);
   assert.equal(result.project_runtime.version,'0.8.149');
   assert.equal(result.project_runtime.runtime,'hyperframes-core-project-1');
   assert.equal(result.project_runtime.legacy_bridge,false);
   assert.ok(result.project_runtime.core_seek_count>=2);
   assert.ok(result.project_runtime.dispatch_count>=2);
  });
}

test('real core rejects a native hf-seek asynchronous draw failure',{skip:!available},async()=>{
 const html=svg.replace("Promise.resolve().then(()=>document.querySelector('circle').setAttribute('cx',30+Math.round(event.detail.time*96)))",
  "Promise.reject(Error('intentional_async_fixture_failure'))");
 await assert.rejects(capture('html-svg',html),/intentional_async_fixture_failure|scene_failed_or_blocked/);
});
