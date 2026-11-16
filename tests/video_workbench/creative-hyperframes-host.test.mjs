// Opt-in real-core protocol fixtures, never Agent samples or visual approval.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {existsSync,mkdirSync,mkdtempSync,readFileSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {captureCreative} from '../../video_workbench/motion_renderer/creative.mjs';
import {HYPERFRAMES_CORE_SHA256} from '../../video_workbench/motion_renderer/hyperframes_project.mjs';

const require=createRequire(import.meta.url);
let chromium;
try{({chromium}=require(process.env.ANIMATION_TEST_PLAYWRIGHT_MODULE||'playwright-core'));}catch{}
const executablePath=process.env.HYPERFRAMES_BROWSER_PATH||chromium?.executablePath();
const evidence=process.env.ANIMATION_HYPERFRAMES_EVIDENCE_DIR;
const hyperFramesDist=process.env.HYPERFRAMES_TEST_DIST;
const assetsRoot=process.env.ANIMATION_TEST_ASSETS;
const sha=value=>createHash('sha256').update(value).digest('hex');
const available=!!chromium&&!!executablePath&&existsSync(executablePath)&&!!evidence&&!!assetsRoot&&!!hyperFramesDist
 &&existsSync(join(hyperFramesDist,'hyperframe.runtime.iife.js'));
const compiler='creative-skill-hyperframes-scene-adapter-1';
const svg=`<!doctype html><style>body{margin:0;background:#08121e}</style>
 <svg width="320" height="180"><circle cx="30" cy="90" r="20" fill="#ffb347"/></svg><script>
 window.__animationExperiment={ready:Promise.resolve(),seek(t){return Promise.resolve().then(()=>{
 document.querySelector('circle').setAttribute('cx',30+Math.round(t*96));});}};</script>`;
const canvas=`<!doctype html><style>body{margin:0;background:#08121e}</style><canvas width="320" height="180"></canvas><script>
 const ctx=document.querySelector('canvas').getContext('2d');
 window.__animationExperiment={ready:Promise.resolve(),seek(t){return Promise.resolve().then(()=>{
 ctx.fillStyle='#08121e';ctx.fillRect(0,0,320,180);ctx.fillStyle='#ffb347';ctx.fillRect(10+Math.round(t*96),60,36,36);});}};</script>`;
const three=`<!doctype html><style>body{margin:0;background:#08121e}</style>
 <script type="module">import * as THREE from '/assets/three.module.min.js';
 const renderer=new THREE.WebGLRenderer({antialias:false,preserveDrawingBuffer:true});
 renderer.setSize(320,180);renderer.setClearColor('#08121e');document.body.append(renderer.domElement);
 const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(40,320/180,.1,100);camera.position.z=5;
 const mesh=new THREE.Mesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshBasicMaterial({color:'#ffb347'}));scene.add(mesh);
 window.__animationExperiment={ready:Promise.resolve(),seek(t){return Promise.resolve().then(()=>{
 mesh.rotation.set(t*.4,t*.6,0);renderer.render(scene,camera);});}};</script>`;

function input(engine,html){
 mkdirSync(evidence,{recursive:true});const root=mkdtempSync(join(evidence,'formal-hyperframes-'+engine+'-'));
 const source={engine,files:{'scene.html':html},files_sha256:{'scene.html':sha(html)}};
 const plan={schema:'video.creative-scene.v5',engine,adapter_revision:'creative-skill-film-scene-adapter-1',
  input_document:{assets:Object.fromEntries(['gsap.min.js','NotoSansCJK-Regular.ttc',...(engine==='three'?['three.module.min.js']:[])]
   .map(name=>[name,sha(readFileSync(join(assetsRoot,name)))]))},result:{source},input_sha256:'a'.repeat(64),result_sha256:'b'.repeat(64)};
 // The same source holds still during leading/trailing time. Only HF owns
 // the outer clock; the bridge maps that clock to the original source.
 const project={schema:'video.hyperframes-project.v1',project_id:'formal-fixture-'+engine,
  entry:'scene.html',width:1280,height:720,fps:24,duration_seconds:1,scenes:[
   {id:'scene',entry:'scene.html',engine,source_sha256:sha(html),start_seconds:0,
    duration_seconds:1,leading_seconds:.25,audio_seconds:.5}]};
 const timeline={compiler,duration_seconds:1,audio_seconds:.5,leading_seconds:.25,trailing_seconds:.25,
  creative:{legacy_bridge:true,source_adapter_revision:plan.adapter_revision,
   project_runtime:'hyperframes-core-project-1',renderer:'isolated-hyperframes-core-project-ffmpeg-1',
   seek_protocol:'hyperframes-core-renderSeek-exact',hyperframes_project:project,
   hyperframes_project_sha256:sha(JSON.stringify(project))}};
 writeFileSync(join(root,'plan.json'),JSON.stringify(plan));writeFileSync(join(root,'timeline.json'),JSON.stringify(timeline));
 return root;
}
const capture=(root,mode)=>captureCreative({root,mode,assetsRoot,chromium,executablePath,hyperFramesDist});
for(const [engine,html] of [['html-svg',svg],['canvas',canvas],['three',three]]){
 test(`formal real HF ${engine} preserves source, audio holds and independent captures`,{skip:!available},async()=>{
  const root=input(engine,html),rendered=await capture(root,'render'),observed=await capture(root,'observe');
  writeFileSync(join(root,'capture.json'),JSON.stringify(rendered,null,2));
  assert.equal(rendered.schema,'video.creative-capture.v3');
  assert.equal(observed.schema,'video.creative-observations.v3');
  assert.equal(rendered.frame_count,24);
  assert.deepEqual(rendered.probe_sha256,observed.samples.map(row=>row.frame_sha256));
  assert.equal(rendered.first_sha256,rendered.reverse_initial_sha256);
  assert.notEqual(rendered.first_sha256,rendered.last_sha256);
  assert.equal(observed.samples[1].local_seconds,0);
  assert.equal(observed.samples.at(-2).local_seconds,.5);
  for(const result of [rendered,observed]){
   assert.equal(result.project_runtime.core_sha256,HYPERFRAMES_CORE_SHA256);
   assert.equal(result.project_runtime.legacy_bridge,true);
   assert.equal(result.project_runtime.evidence_scope,'single_scene_project_not_whole_film');
   assert.equal(result.project_runtime.actual_seconds,0);
   assert.ok(result.project_runtime.core_seek_count>=20);
   assert.ok(result.project_runtime.dispatch_count>=result.project_runtime.core_seek_count);
  }
  if(process.env.HYPERFRAMES_TEST_ENCODE==='1'){
   const path=join(root,'technical-fixture.mp4');
   const encoded=spawnSync('ffmpeg',['-v','error','-nostdin','-y','-framerate','24','-i',join(root,'creative-frames/frame-%06d.jpg'),
    '-f','lavfi','-i','anullsrc=r=48000:cl=mono','-t','1','-frames:v','24','-c:v','libx264','-preset','veryfast',
    '-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',path],{timeout:30000,encoding:'utf8'});
   assert.equal(encoded.status,0,encoded.stderr);
   const decoded=spawnSync('ffmpeg',['-v','error','-nostdin','-i',path,'-f','null','-'],{timeout:30000,encoding:'utf8'});
   assert.equal(decoded.status,0,decoded.stderr);
   const probe=spawnSync('ffprobe',['-v','error','-show_streams','-of','json',path],{timeout:10000,encoding:'utf8'});
   assert.equal(probe.status,0,probe.stderr);
   const streams=JSON.parse(probe.stdout).streams,video=streams.find(row=>row.codec_type==='video');
   assert.equal(Number(video.nb_frames),24);assert.equal(Number(video.duration),1);
   assert.equal(video.width,1280);assert.equal(video.height,720);
   assert.equal(streams.filter(row=>row.codec_type==='audio').length,1);
   writeFileSync(join(root,'technical-encode-proof.json'),JSON.stringify({scope:'protocol_fixture_not_agent_sample',
    media_sha256:sha(readFileSync(path)),decode_verified:true,streams},null,2));
  }
 });
}

test('formal HF rejects a mismatched project clock',{skip:!available},async()=>{
 const root=input('canvas',canvas),path=join(root,'timeline.json'),timeline=JSON.parse(readFileSync(path));
 timeline.creative.hyperframes_project.scenes[0].audio_seconds=.4;writeFileSync(path,JSON.stringify(timeline));
 await assert.rejects(capture(root,'observe'),/creative_hyperframes_project_mismatch/);
});
