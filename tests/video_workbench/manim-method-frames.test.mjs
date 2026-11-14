// Resource/decode stand-ins; actual browser/Cairo/HyperFrames proof is separate.
import test from 'node:test';
import assert from 'node:assert/strict';
import {prepareManimMethodFrames} from '../../video_workbench/motion_renderer/manim_method_frames.mjs';
function manifest(){return {schema:'video.manim-frame-resource.v1',resource_id:'geometry',fps:24,
 frame_count:3,global_start_frame:0,width:320,height:180,transparent:true,
 decoded_bytes:320*180*3*4,limits:{max_resource_bytes:25165824,max_decoded_bytes:134217728},
 request_sha256:'a'.repeat(64),source_sha256:'b'.repeat(64),
 runtime:{library:'manim-community',version:'0.22.1',renderer:'cairo'},audio:null,
 frames:[0,1,2].map(index=>({index,global_frame:index,
  path:`assets/manim/geometry/frame_${String(index).padStart(6,'0')}.png`,bytes:512,sha256:'c'.repeat(64)}))};}
function fixture(decode=async()=>{}){
 const images=[];
 const frames=prepareManimMethodFrames({append(){}},manifest(),{createImage(){
  const image={style:{},async decode(){await decode();},removeAttribute(){this.src=null;},remove(){this.removed=true;}};
  images.push(image);return image;
 }});
 return {frames,images};
}
test('initialization predecodes then exact synchronous seeks require no new image loading',async()=>{
 let count=0;const f=fixture(async()=>{count++;});
 assert.throws(()=>f.frames.seekFrame(0),/not_ready/);
 await f.frames.ready;
 assert.equal(count,3);
 for(const frame of [0,2,1,1,0,3,0]){
  assert.equal(f.frames.seekFrame(frame),undefined);
  assert.deepEqual(f.images.map(image=>image.style.display==='block'),[0,1,2].map(index=>index===frame));
 }
 assert.equal(count,3);
 f.frames.dispose();f.frames.dispose();assert.ok(f.images.every(image=>image.removed&&image.src===null));
 assert.throws(()=>f.frames.seekFrame(0),/disposed/);
});
test('only two frames decode at once and disposal promptly rejects initialization',async()=>{
 let started=0;const pending=new Promise(()=>{});
 const f=fixture(async()=>{started++;await pending;});
 assert.equal(started,2);assert.equal(f.images.length,2);
 f.frames.dispose();await assert.rejects(f.frames.ready,/disposed/);
 assert.ok(f.images.every(image=>image.removed));
});
test('failed decode removes every created image and never exposes seek',async()=>{
 const f=fixture(async()=>{throw Error('decode_failed');});
 await assert.rejects(f.frames.ready,/decode_failed/);
 assert.ok(f.images.every(image=>image.removed&&image.src===null));
 assert.throws(()=>f.frames.seekFrame(0),/disposed/);
});
test('outside or fractional frame does not silently stretch or round the resource',async()=>{
 const f=fixture();await f.frames.ready;
 f.frames.seekFrame(3);assert.ok(f.images.every(image=>image.style.display==='none'));
 assert.throws(()=>f.frames.seekFrame(.5),/clock_invalid/);
 f.frames.dispose();
});
