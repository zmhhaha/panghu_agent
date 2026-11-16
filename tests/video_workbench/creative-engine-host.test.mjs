// Real-browser protocol fixtures. These are deliberately not Agent samples or
// evidence that a human approved the resulting animation's visual quality.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {existsSync,mkdirSync,mkdtempSync,readFileSync,writeFileSync} from 'node:fs';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {captureCreative} from '../../video_workbench/motion_renderer/creative.mjs';
import {THREE_ASSET_SHA256} from '../../video_workbench/motion_renderer/engine_runtime.mjs';

const require=createRequire(import.meta.url);
let chromium;
try{({chromium}=require(process.env.ANIMATION_TEST_PLAYWRIGHT_MODULE||'playwright-core'));}catch{}
const executablePath=process.env.HYPERFRAMES_BROWSER_PATH||chromium?.executablePath();
const repository=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const evidenceRoot=join(repository,'video_workbench/rendered/feedback-20261106/animation-lab/agent-creation-phase1/creation-product-r1/spec-consolidation-r1/canvas-three-r1');
const assetsRoot=process.env.ANIMATION_TEST_ASSETS||join(repository,'video_workbench/rendered/feedback-20261106/animation-lab/assets');
const available=!!chromium&&!!executablePath&&existsSync(executablePath)
 &&['gsap.min.js','NotoSansCJK-Regular.ttc','three.module.min.js'].every(name=>existsSync(join(assetsRoot,name)));
const sha=raw=>createHash('sha256').update(raw).digest('hex');
const canvasHTML=(extra='',seek='draw(t);')=>`<!doctype html><style>body{margin:0;background:#08121e}canvas{display:block}</style>
 <canvas width="320" height="180"></canvas><script>
 const canvas=document.querySelector('canvas'),ctx=canvas.getContext('2d');
 function draw(t){ctx.fillStyle='#08121e';ctx.fillRect(0,0,320,180);ctx.fillStyle='#ffb347';
 ctx.fillRect(10+Math.round(t*96),60,36,36);}
 window.__animationExperiment={ready:Promise.resolve(),seek(t){${seek}}};${extra}</script>`;
const threeHTML=`<!doctype html><style>body{margin:0;background:#08121e}canvas{display:block}</style>
 <script type="module">import * as THREE from '/assets/three.module.min.js';
 const renderer=new THREE.WebGLRenderer({antialias:false,preserveDrawingBuffer:true});
 renderer.setSize(320,180);renderer.setClearColor('#08121e');document.body.append(renderer.domElement);
 const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(40,320/180,.1,100);camera.position.z=5;
 const mesh=new THREE.Mesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshBasicMaterial({color:'#ffb347'}));scene.add(mesh);
 window.__animationExperiment={ready:Promise.resolve(),seek(t){mesh.rotation.set(t*.4,t*.6,0);renderer.render(scene,camera);}};
 </script>`;

function input(engine,html,{legacy=false,assetHash}={}){
 mkdirSync(evidenceRoot,{recursive:true});
 const root=mkdtempSync(join(evidenceRoot,'formal-host-'));
 const assets=Object.fromEntries(['gsap.min.js','NotoSansCJK-Regular.ttc',...(engine==='three'?['three.module.min.js']:[])]
  .map(name=>[name,sha(readFileSync(join(assetsRoot,name)))]));
 if(assetHash)assets['three.module.min.js']=assetHash;
 const plan={schema:legacy?'video.creative-scene.v2':'video.creative-scene.v3',engine,
  adapter_revision:legacy?'creative-method-scene-adapter-1':'creative-method-engine-scene-adapter-1',
  input_document:{assets},result:{source:{engine,files:{'scene.html':html}}},input_sha256:'a'.repeat(64),result_sha256:'b'.repeat(64)};
 const timeline={compiler:plan.adapter_revision,duration_seconds:1,audio_seconds:1,leading_seconds:0};
 writeFileSync(join(root,'plan.json'),JSON.stringify(plan));
 writeFileSync(join(root,'timeline.json'),JSON.stringify(timeline));
 writeFileSync(join(root,'fixture-host-identity.json'),JSON.stringify({scope:'manual_protocol_fixture_not_agent_sample',
  engine,engine_runtime_sha256:sha(readFileSync(join(repository,'video_workbench/motion_renderer/engine_runtime.mjs'))),
  creative_host_sha256:sha(readFileSync(join(repository,'video_workbench/motion_renderer/creative.mjs'))),
  model_requests:0,deployed:false},null,2));
 return root;
}
const capture=(root,mode='observe')=>captureCreative({root,mode,assetsRoot,chromium,executablePath});

for(const engine of ['canvas','three']){
 test(`formal ${engine} actually renders and independently reproduces all probe frames`,{skip:!available},async()=>{
  const root=input(engine,engine==='canvas'?canvasHTML():threeHTML);
  const rendered=await capture(root,'render'),observed=await capture(root,'observe');
  writeFileSync(join(root,'formal-render-capture.json'),JSON.stringify(rendered,null,2));
  assert.equal(rendered.frame_count,24);
  assert.equal(rendered.reverse_initial_sha256,rendered.first_sha256);
  assert.deepEqual(rendered.probe_sha256,observed.samples.map(row=>row.frame_sha256));
  assert.deepEqual(rendered.probe_engine_observations,observed.samples.map(row=>row.engine_observation));
  assert.ok(new Set(rendered.probe_sha256).size>4);
  assert.equal(observed.samples[0].local_seconds,0);
  assert.equal(observed.samples.at(-2).local_seconds,23/24);
  assert.equal(observed.idle_clock_verified,true);
  assert.equal(observed.engine,engine);
  assert.equal(observed.visual_quality,'not_evaluated');
  const native=observed.samples[0].engine_observation.contexts;
  assert.ok(native.some(row=>row.kind===(engine==='canvas'?'2d':'webgl2')&&row.visible));
  if(engine==='three'){
   assert.equal(sha(readFileSync(join(assetsRoot,'three.module.min.js'))),THREE_ASSET_SHA256);
   assert.ok(native.some(row=>row.kind==='webgl2'&&row.software&&!row.context_lost&&row.gl_error===0));
   assert.deepEqual(observed.loaded_engine_assets,['three.module.min.js']);
  }
 });
}

test('legacy formal adapter retains HTML-only semantics',{skip:!available},async()=>{
 await assert.rejects(capture(input('canvas',canvasHTML(),{legacy:true})),/creative_contract_mismatch/);
 const hiddenCanvas='<!doctype html><canvas></canvas><script>window.__animationExperiment={seek(){}};</script>';
 await assert.rejects(capture(input('html-svg',hiddenCanvas,{legacy:true})),/creative_engine_not_admitted/);
});
test('formal Canvas cannot request WebGL or browser audio',{skip:!available},async()=>{
 for(const extra of ["document.createElement('canvas').getContext('webgl2');",'new AudioContext();',
  "document.createElement('canvas').transferControlToOffscreen();"])
  await assert.rejects(capture(input('canvas',canvasHTML(extra))),/creative_page_or_resource_error/);
});
test('formal Three rejects changed assets and unproven raw WebGL',{skip:!available},async()=>{
 await assert.rejects(capture(input('three',threeHTML,{assetHash:'f'.repeat(64)})),/creative_asset_digest_mismatch/);
 const raw="<!doctype html><canvas></canvas><script>document.querySelector('canvas').getContext('webgl2');window.__animationExperiment={seek(){}};</script>";
 await assert.rejects(capture(input('three',raw)),/creative_three_module_not_loaded/);
});
test('formal absolute clock rejects accumulated seek state',{skip:!available},async()=>{
 const html=canvasHTML('window.accumulated=0;','window.accumulated+=t;draw(window.accumulated);');
 await assert.rejects(capture(input('canvas',html)),/creative_reverse_seek_mismatch/);
});
test('formal CSP catches swallowed external fetches',{skip:!available},async()=>{
 const html=canvasHTML("fetch('https://example.invalid/data').catch(()=>{});");
 await assert.rejects(capture(input('canvas',html)),/creative_page_or_resource_error/);
});
