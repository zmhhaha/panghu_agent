// Actual PNG pipe/FFmpeg checks. These do not claim browser, core or art proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync,writeFileSync,readFileSync,existsSync,rmSync,readdirSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {deflateSync} from 'node:zlib';
import {createHash} from 'node:crypto';
import {createFilmPngEncoder} from '../../video_workbench/motion_renderer/hyperframes_film_stream.mjs';

function binary(name){const r=spawnSync(process.platform==='win32'?'where.exe':'which',[name],{encoding:'utf8'});
 return r.status===0?r.stdout.trim().split(/\r?\n/)[0]:null;}
const ffmpeg=binary('ffmpeg'),ffprobe=binary('ffprobe');
const crc=buffer=>{let value=0xffffffff;for(const n of buffer){value^=n;for(let i=0;i<8;i++)value=(value>>>1)^((value&1)?0xedb88320:0);}return(value^0xffffffff)>>>0;};
function png(color){const chunk=(name,data)=>{const n=Buffer.from(name),length=Buffer.alloc(4),sum=Buffer.alloc(4);
 length.writeUInt32BE(data.length);sum.writeUInt32BE(crc(Buffer.concat([n,data])));return Buffer.concat([length,n,data,sum]);};
 const header=Buffer.alloc(13);header.writeUInt32BE(64,0);header.writeUInt32BE(36,4);header[8]=8;header[9]=2;
 const pixels=Buffer.concat(Array.from({length:36},()=>Buffer.concat([Buffer.from([0]),Buffer.from(Array.from({length:64},()=>color).flat())])));
 return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',header),chunk('IDAT',deflateSync(pixels)),chunk('IEND',Buffer.alloc(0))]);}
function fixture(){const root=mkdtempSync(join(process.env.PANGHU_TEST_TEMP||tmpdir(),'film-stream-'));
 const audio=join(root,'delivery.wav'),output=join(root,'film.mp4'),head=Buffer.alloc(44),data=Buffer.alloc(48000*4);
 head.write('RIFF',0);head.writeUInt32LE(data.length+36,4);head.write('WAVEfmt ',8);head.writeUInt32LE(16,16);
 head.writeUInt16LE(1,20);head.writeUInt16LE(2,22);head.writeUInt32LE(48000,24);head.writeUInt32LE(48000*4,28);
 head.writeUInt16LE(4,32);head.writeUInt16LE(16,34);head.write('data',36);head.writeUInt32LE(data.length,40);
 writeFileSync(audio,Buffer.concat([head,data]));
 return {root,audio,output,options:{ffmpegPath:ffmpeg,audioInput:audio,outputPath:output,width:64,height:36,frameCount:24}};}

test('one backpressured stream produces one complete dynamic video with no frame files',{skip:!ffmpeg||!ffprobe},async()=>{
 const f=fixture();try{
  const encoder=await createFilmPngEncoder(f.options),chain=createHash('sha256');
  for(let i=0;i<24;i++){
   const raw=png(i<12?[190,40,30]:[20,60,190]);
   chain.update(i+':'+raw.length+':'+createHash('sha256').update(raw).digest('hex')+'\n');await encoder.write(raw);
  }
  const report=await encoder.finish();assert.equal(report.encoder_invocations,1);assert.equal(report.frame_files,0);
  assert.equal(report.peak_pending_frames,1);assert.equal(report.frame_count,24);assert.equal(report.frame_inventory_sha256,chain.digest('hex'));
  assert.deepEqual(readdirSync(f.root).sort(),['delivery.wav','film.mp4']);
  const probe=spawnSync(ffprobe,['-v','error','-count_frames','-show_streams','-of','json',f.output],{encoding:'utf8'});
  assert.equal(probe.status,0,probe.stderr);const streams=JSON.parse(probe.stdout).streams;
  const video=streams.find(s=>s.codec_type==='video'),audio=streams.find(s=>s.codec_type==='audio');
  assert.equal(Number(video.nb_read_frames),24);assert.equal(Number(video.duration),1);assert.equal(Number(audio.duration),1);
  const decoded=spawnSync(ffmpeg,['-v','error','-i',f.output,'-map','0:v:0','-f','rawvideo','-pix_fmt','rgb24','-']);
  assert.equal(decoded.status,0);assert.equal(decoded.stdout.length,64*36*3*24);
  assert.ok(decoded.stdout[0]>150);assert.ok(decoded.stdout[23*64*36*3+2]>150);
 }finally{rmSync(f.root,{recursive:true,force:true});}
});

test('abort waits for the owned encoder to stop and no next frame can be accepted',{skip:!ffmpeg},async()=>{
 const f=fixture();try{
  const encoder=await createFilmPngEncoder(f.options);await encoder.write(png([40,50,60]));
  const pid=encoder.processId;await encoder.abort();
  assert.throws(()=>process.kill(pid,0),error=>error.code==='ESRCH');
  await assert.rejects(encoder.write(png([40,50,60])),/not_writable/);
 }finally{rmSync(f.root,{recursive:true,force:true});}
});

test('a prior output is never overwritten by the stream encoder',{skip:!ffmpeg},async()=>{
 const f=fixture();try{writeFileSync(f.output,'caller-owned');
  await assert.rejects(createFilmPngEncoder(f.options),/output_invalid/);assert.equal(readFileSync(f.output,'utf8'),'caller-owned');
 }finally{rmSync(f.root,{recursive:true,force:true});}
});

test('invalid PNG geometry cannot be queued to the encoder',{skip:!ffmpeg},async()=>{
 const f=fixture();try{const encoder=await createFilmPngEncoder(f.options),raw=png([1,2,3]);
  raw.writeUInt32BE(66,16);await assert.rejects(encoder.write(raw),/frame_invalid/);await encoder.abort();
 }finally{rmSync(f.root,{recursive:true,force:true});}
});

test('unbounded frame counts and viewports are rejected before an output is created',{skip:!ffmpeg},async()=>{
 const f=fixture();try{
  for(const change of [{frameCount:7201},{width:1922},{height:1082}]){
   await assert.rejects(createFilmPngEncoder({...f.options,...change}),/dimensions_invalid/);
   assert.equal(existsSync(f.output),false);
  }
 }finally{rmSync(f.root,{recursive:true,force:true});}
});
