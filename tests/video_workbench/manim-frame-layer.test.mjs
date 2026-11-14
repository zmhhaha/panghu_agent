// Image/decode stand-ins verify scheduling only, not actual browser rasterization.
import test from 'node:test';
import assert from 'node:assert/strict';
import {validateManimManifest,manimFrameIndex,createManimFrameLayer}
 from '../../video_workbench/motion_renderer/manim_frame_layer.mjs';

function manifest(){return {schema:'video.manim-frame-resource.v1',resource_id:'geometry',fps:24,
 frame_count:3,global_start_frame:48,width:320,height:180,transparent:true,
 decoded_bytes:320*180*3*4,limits:{max_resource_bytes:25165824,max_decoded_bytes:134217728},
 request_sha256:'a'.repeat(64),source_sha256:'b'.repeat(64),
 runtime:{library:'manim-community',version:'0.22.1',renderer:'cairo'},audio:null,
 frames:[0,1,2].map(index=>({index,global_frame:48+index,
  path:`assets/manim/geometry/frame_${String(index).padStart(6,'0')}.png`,bytes:512,sha256:'c'.repeat(64)}))};}
function fixture({decode=async()=>{},placementStartFrame,value=manifest()}={}){
 const events=[];let removed=false;
 const image={style:{},removeAttribute(name){events.push(['removeAttribute',name]);},
  remove(){removed=true;},async decode(){events.push(['decode',this.src]);await decode();}};
 const root={append(child){assert.equal(child,image);events.push(['append']);}};
 const layer=createManimFrameLayer(root,value,{createImage:()=>image,placementStartFrame});
 return {layer,image,events,removed:()=>removed};
}
test('resource frame has exact half-open interval and no implicit end hold',()=>{
 const value=validateManimManifest(manifest());
 assert.deepEqual([47,48,49,50,51,72].map(frame=>manimFrameIndex(value,frame)),[null,0,1,2,null,null]);
 for(const frame of [-1,.5,NaN,Infinity,true,undefined])assert.throws(()=>manimFrameIndex(value,frame),/clock_invalid/);
});
test('forward backward repeated random seek reconstructs the exact same PNG',async()=>{
 const f=fixture();
 for(const frame of [48,50,49,49,48,50,51,47,48]){
  await f.layer.renderAt({global_frame:frame});
  if(frame<48||frame>=51)assert.equal(f.image.style.display,'none');
  else{
   assert.equal(f.image.style.display,'block');
   assert.equal(f.image.src,`/project/assets/manim/geometry/frame_${String(frame-48).padStart(6,'0')}.png`);
  }
 }
 // Repeating a decoded frame needs no loading, but repeat/backward calls have
 // identical visible state. Seeking outside then back does not reuse stale state.
 assert.equal(f.events.filter(row=>row[0]==='decode').length,6);
 f.layer.dispose();assert.equal(f.removed(),true);
 await assert.rejects(f.layer.renderAt({global_frame:48}),/disposed/);
});
test('render completion awaits image decode and concurrent render fails closed',async()=>{
 let release;const waiting=new Promise(resolve=>{release=resolve;});
 const f=fixture({decode:()=>waiting});let done=false;
 const pending=f.layer.renderAt({global_frame:49}).then(()=>{done=true;});
 await Promise.resolve();assert.equal(done,false);assert.equal(f.image.style.display,'none');
 await assert.rejects(f.layer.renderAt({global_frame:48}),/concurrent_render/);
 release();await pending;assert.equal(done,true);assert.equal(f.image.style.display,'block');
});
test('decode failure does not show old pixels and previous frame can be reloaded',async()=>{
 let fail=false;const f=fixture({decode:async()=>{if(fail)throw Error('decode_failed');}});
 await f.layer.renderAt({global_frame:48});
 fail=true;await assert.rejects(f.layer.renderAt({global_frame:49}),/decode_failed/);
 assert.equal(f.image.style.display,'none');
 fail=false;await f.layer.renderAt({global_frame:48});
 assert.ok(f.image.src.endsWith('frame_000000.png'));
 assert.equal(f.events.filter(row=>row[0]==='decode').length,3);
});
test('dispose while loading never reveals decoded image after cleanup',async()=>{
 let release;const waiting=new Promise(resolve=>{release=resolve;});
 const f=fixture({decode:()=>waiting});
 const pending=f.layer.renderAt({global_frame:48});f.layer.dispose();release();
 await assert.rejects(pending,/disposed/);assert.equal(f.image.style.display,'none');assert.ok(f.removed());
});
test('manifest rejects missing extra out-of-order foreign and traversing frames',()=>{
 const changes=[value=>value.frames.pop(),value=>value.frames.push(value.frames[0]),
  value=>value.frames.reverse(),value=>value.frames[0].global_frame++,
  value=>value.frames[0].path='assets/manim/geometry/../../escape.png',
  value=>value.frames[0].sha256='forged',value=>value.fps=30,
  value=>value.runtime.renderer='opengl',value=>value.audio={path:'sound.mp3'}];
 for(const change of changes){const value=manifest();change(value);assert.throws(()=>validateManimManifest(value),/invalid/);}
});
test('asset URL cannot escape the frozen project resource server',async()=>{
 const f=fixture();
 for(const url of ['https://cdn.example/image.png','/project/assets/manim/geometry/../../escape.png',
  'data:image/png;base64,AAAA','/project/assets/manim/geometry/frame_000000.png?cache=1']){
  const layer=createManimFrameLayer({append(){}},manifest(),{assetURL:()=>url,createImage:()=>f.image});
  await assert.rejects(layer.renderAt({global_frame:48}),/asset_url_invalid/);
 }
});
test('compression never grants unlimited decoded image memory',()=>{
 const value=manifest();value.width=1280;value.height=720;value.frame_count=240;
 value.decoded_bytes=value.width*value.height*value.frame_count*4;
 value.frames=Array.from({length:240},(_,index)=>({index,global_frame:48+index,
  path:`assets/manim/geometry/frame_${String(index).padStart(6,'0')}.png`,bytes:512,sha256:'c'.repeat(64)}));
 assert.ok(value.frames.reduce((sum,row)=>sum+row.bytes,0)<25165824);
 assert.throws(()=>validateManimManifest(value),/invalid/);
});

test('explicit placement preserves the original manifest and PNG inventory at different film positions',async()=>{
 const value=manifest(),before=structuredClone(value);
 const first=fixture({value,placementStartFrame:0}),later=fixture({value,placementStartFrame:144});
 for(const offset of [0,2,1,1,0,2,3,0]){
  await first.layer.renderAt({global_frame:offset});
  await later.layer.renderAt({global_frame:144+offset});
  assert.equal(first.image.style.display,later.image.style.display);
  assert.equal(first.image.src,later.image.src);
 }
 assert.equal(value.global_start_frame,48);assert.deepEqual(value,before);
 // Exact source inventory is addressed by its original row index, with no
 // rewritten global_frame or per-placement URL masquerading as a new asset.
 assert.deepEqual([143,144,145,146,147].map(frame=>manimFrameIndex(value,frame,144)),[null,0,1,2,null]);
 for(const f of [first,later])f.layer.dispose();
});

test('a locally authored zero-based sequence is hidden on both sides of its placement',async()=>{
 const value=manifest();value.global_start_frame=0;
 for(const row of value.frames)row.global_frame=row.index;
 const f=fixture({value,placementStartFrame:72});
 for(const frame of [0,71,72,74,75,73,73,71,72]){
  await f.layer.renderAt({global_frame:frame});
  assert.equal(f.image.style.display,frame>=72&&frame<75?'block':'none');
  if(frame>=72&&frame<75)assert.ok(f.image.src.endsWith(`frame_${String(frame-72).padStart(6,'0')}.png`));
 }
 assert.equal(value.global_start_frame,0);f.layer.dispose();
});

test('placement validation happens before creating or appending a resource image',()=>{
 let created=0,appended=0;
 for(const placementStartFrame of [-1,.5,null,true,NaN,Infinity,Number.MAX_SAFE_INTEGER]){
  assert.throws(()=>createManimFrameLayer({append(){appended++;}},manifest(),
   {placementStartFrame,createImage(){created++;return {};}}),/placement_invalid/);
 }
 assert.equal(created,0);assert.equal(appended,0);
});
