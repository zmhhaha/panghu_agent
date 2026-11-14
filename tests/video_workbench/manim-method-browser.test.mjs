// Exact PNG custody plus an optional actual Chromium/HyperFrames v5 fixture.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {deflateSync} from 'node:zlib';
import {readFileSync,mkdtempSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {validateGeneratedResources,captureMethodFrames} from '../../video_workbench/animation_method_host.mjs';
import * as engineRuntime from '../../video_workbench/motion_renderer/engine_runtime.mjs';

const sha=raw=>createHash('sha256').update(raw).digest('hex');
const canonical=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)
 ?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
function png(red){
 const crc=bytes=>{let value=0xffffffff;for(const byte of bytes){value^=byte;for(let i=0;i<8;i++)value=(value>>>1)^((value&1)?0xedb88320:0);}return (value^0xffffffff)>>>0;};
 const chunk=(name,data)=>{const nameBytes=Buffer.from(name),result=Buffer.alloc(data.length+12);
  result.writeUInt32BE(data.length);nameBytes.copy(result,4);data.copy(result,8);
  result.writeUInt32BE(crc(Buffer.concat([nameBytes,data])),data.length+8);return result;};
 const header=Buffer.alloc(13);header.writeUInt32BE(320);header.writeUInt32BE(180,4);header[8]=8;header[9]=6;
 const pixels=Buffer.alloc((320*4+1)*180);
 for(let y=0;y<180;y++)for(let x=0;x<320;x++){
  const i=y*(320*4+1)+1+x*4;pixels[i]=red;pixels[i+1]=80;pixels[i+2]=180;pixels[i+3]=255;
 }
 return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',header),chunk('IDAT',deflateSync(pixels)),chunk('IEND',Buffer.alloc(0))]);
}
function fixture(){
 const resources=new Map(),assets={},helpers={};
 for(const name of ['manim_frame_layer.mjs','manim_method_frames.mjs']){
  const raw=readFileSync(new URL('../../video_workbench/motion_renderer/'+name,import.meta.url));
  resources.set('/assets/'+name,raw);helpers[name]=sha(raw);assets[name]=sha(raw);
 }
 const manifest={schema:'video.manim-frame-resource.v1',resource_id:'technical_fixture',request_sha256:'a'.repeat(64),
  source_sha256:'b'.repeat(64),runtime:{library:'manim-community',version:'0.22.1',renderer:'cairo',
   image_digest:'sha256:'+'c'.repeat(64),seed:17,latex_available:false},width:320,height:180,fps:24,frame_count:3,
  decoded_bytes:320*180*3*4,limits:{max_resource_bytes:25165824,max_decoded_bytes:134217728},
  global_start_frame:0,transparent:true,frames:[],audio:null,evidence_scope:'technical_fixture_not_manim_generation'};
 for(let index=0;index<3;index++){
  const raw=png(40+index*70),path='assets/manim/technical_fixture/frame_'+String(index).padStart(6,'0')+'.png';
  resources.set('/'+path,raw);assets[path.slice(7)]=sha(raw);
  manifest.frames.push({index,global_frame:index,path,bytes:raw.length,sha256:sha(raw)});
 }
 const path='assets/manim/technical_fixture/manifest.json',raw=Buffer.from(canonical(manifest));
 resources.set('/'+path,raw);assets[path.slice(7)]=sha(raw);
 const descriptor={schema:'video.manim-method-resource.v1',scope:{owner:'fixture',effect_id:'fixture',
  experiment_id:'fixture',operation_id:'fixture'},envelope_sha256:'d'.repeat(64),image_digest:manifest.runtime.image_digest,
  manifest_path:path,manifest_sha256:sha(raw),manifest,helpers,assets_sha256:assets};
 descriptor.sha256=sha(canonical(descriptor));
 const request={schema:'video.animation-method-render-request.v5',engine:'html-svg',
  constraints:{width:320,height:180,fps:24,duration_seconds:1},generated_resources:[descriptor],
  assets_sha256:assets,max_evidence_bytes:192*1024*1024,legacy_bridge:true};
 return {request,resources,descriptor};
}

test('v5 only admits exact nested PNG inventory and pinned consumer helpers',()=>{
 const {request,resources}=fixture();
 assert.equal(validateGeneratedResources(request,resources).size,6);
 for(const change of ['png','manifest','helper','extra','clock']){
  const item=fixture(),d=item.descriptor;
  if(change==='png')item.resources.set('/'+d.manifest.frames[0].path,png(255));
  if(change==='manifest')item.resources.set('/'+d.manifest_path,Buffer.from('{}'));
  if(change==='helper')d.helpers['manim_method_frames.mjs']='f'.repeat(64);
  if(change==='extra')d.assets_sha256['unrelated.js']='f'.repeat(64);
  if(change==='clock')d.manifest.global_start_frame=24;
  assert.throws(()=>validateGeneratedResources(item.request,item.resources),/method_generated_resource/);
 }
});

const enabled=process.env.PANGHU_ANIME_PLAYWRIGHT&&process.env.PANGHU_ANIME_BROWSER
 &&process.env.PANGHU_ANIME_HYPERFRAMES_DIST&&process.env.PANGHU_TEST_TEMP;
for(const placementStartFrame of [0,12])
test('actual v5 predecodes PNGs and preserves absolute, reverse, repeat, idle and reload frames at '+placementStartFrame,
 {skip:!enabled},async()=>{
 const {chromium}=await import(pathToFileURL(process.env.PANGHU_ANIME_PLAYWRIGHT).href);
 const root=mkdtempSync(join(process.env.PANGHU_TEST_TEMP,'manim-method-host-'));
 const profile=mkdtempSync(join(process.env.PANGHU_TEST_TEMP,'manim-method-browser-'));
 const {request,resources,descriptor}=fixture();
 const html=`<!doctype html><style>body{margin:0;background:#101629}#layer{position:absolute;inset:0}</style>
 <div id="layer"></div><script type="module">
 import {prepareManimMethodFrames} from '/assets/manim_method_frames.mjs';
 const layer=prepareManimMethodFrames(document.querySelector('#layer'),${JSON.stringify(descriptor.manifest)},
  {placementStartFrame:${placementStartFrame}});
 window.__animationExperiment={ready:layer.ready,seek(seconds){layer.seekFrame(Math.round(seconds*24));}};
 </script>`;
 const project={schema:'video.hyperframes-project.v1',project_id:'manim-method-technical',width:320,height:180,
  fps:24,duration_seconds:1,entry:'scene.html',scenes:[{id:'scene',start_seconds:0,duration_seconds:1,
   entry:'scene.html',source_sha256:sha(html),engine:'html-svg'}]};
 Object.assign(request,{project,project_sha256:sha(canonical(project))});resources.set('/scene.html',Buffer.from(html));
 const launcher={async launch(options){const context=await chromium.launchPersistentContext(profile,
  {...options,viewport:{width:320,height:180},deviceScaleFactor:1,serviceWorkers:'block',acceptDownloads:false});
  return {async newContext(){return context;},async close(){await context.close();}};}};
 try{
  const result=await captureMethodFrames({chromium:launcher,request,resources,root,engineRuntime,
   executablePath:process.env.PANGHU_ANIME_BROWSER,hyperFramesDist:process.env.PANGHU_ANIME_HYPERFRAMES_DIST});
  assert.equal(result.frame_count,24);assert.equal(result.seek_checks.fresh_load_verified,true);
  assert.equal(result.seek_checks.idle_frame_verified,true);assert.equal(result.reverse_seek_verified,true);
  assert.deepEqual(result.generated_resource_observation.resources_sha256,[descriptor.sha256]);
  assert.deepEqual(result.generated_resource_observation.loaded_png_paths,descriptor.manifest.frames.map(row=>'/'+row.path));
  assert.notEqual(result.frames[placementStartFrame].sha256,result.frames[placementStartFrame+1].sha256);
  assert.notEqual(result.frames[placementStartFrame+2].sha256,result.frames[placementStartFrame+3].sha256);
  if(placementStartFrame){
   assert.equal(result.frames[0].sha256,result.frames[placementStartFrame-1].sha256);
   assert.equal(result.frames[0].sha256,result.frames[placementStartFrame+3].sha256);
  }
 }finally{rmSync(root,{recursive:true,force:true});rmSync(profile,{recursive:true,force:true});}
});
