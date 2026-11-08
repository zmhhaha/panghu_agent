// Experimental isolated renderer. Admission/authentication and resource/egress
// boundaries are provided by the coordinator/Job, never by the page itself.
import { cpSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { performance } from 'node:perf_hooks';
import { sha, readRegular, verifyInput, verifyRuntime, validateProbe, muxAudioFilter, inputFiles, renderQuality } from './contract.mjs';
import { collectRuntime } from './runtime.mjs';
const root='/work', source='/input', mode=process.argv[2]||'render';
const started=performance.now(),children=new Set();
let stopped=null;
const receipt={schema:'video.motion-render-receipt.v1',status:'starting',mode};
const persist=()=>{receipt.wall_seconds=(performance.now()-started)/1000;writeFileSync(root+'/receipt.json',JSON.stringify(receipt,null,2));};
const kill=child=>{try{process.kill(-child.pid,'SIGTERM');}catch{}setTimeout(()=>{try{process.kill(-child.pid,'SIGKILL');}catch{}},1000).unref();};
const stop=reason=>{stopped=reason;for(const child of children)kill(child);};
for(const signal of ['SIGTERM','SIGINT'])process.on(signal,()=>stop('cancelled'));
const budget=Number(process.env.MOTION_TIMEOUT_SECONDS||300);
if(!Number.isFinite(budget)||budget<1||budget>600)throw Error('budget_outside_range');
const timer=setTimeout(()=>stop('timed_out'),budget*1000);
async function execute(command,args,{capture=false}={}){
 if(stopped)throw Error(stopped);
 const child=spawn(command,args,{cwd:root,stdio:capture?['ignore','pipe','pipe']:['ignore','inherit','inherit'],detached:true});
 children.add(child);let output='',error='';
 if(capture){child.stdout.on('data',d=>{output+=d;if(output.length>2*1024*1024)stop('output_limit');});child.stderr.on('data',d=>{error=(error+d).slice(-16384);});}
 const result=await new Promise(resolve=>{child.once('error',e=>resolve({code:-1,error:String(e)}));child.once('exit',(code,signal)=>resolve({code,signal}));});
 // The parent exiting must not leave its Chrome/FFmpeg grandchildren alive.
 try{process.kill(-child.pid,'SIGKILL');}catch{}
 children.delete(child);
 if(stopped)throw Error(stopped);
 if(result.code!==0)throw Error(JSON.stringify({...result,stderr:error}));
 return output;
}
try{
 mkdirSync(root,{recursive:true});mkdirSync('/tmp/renderer',{recursive:true,mode:0o700});persist();
 Object.assign(receipt,renderQuality(process.env.MOTION_QUALITY||'draft'));
 if(!['render','observe'].includes(mode))throw Error('mode_not_allowed');
 const {manifest,timeline}=verifyInput(source,process.env.MOTION_MANIFEST_SHA256);
 const creative=manifest.compiler==='creative-scene-adapter-1';
 receipt.timeline_sha256=manifest.timeline_sha256;receipt.input_manifest_sha256=process.env.MOTION_MANIFEST_SHA256;
 // Probe the files and binaries that will actually run rather than trusting
 // the build-time runtime.json as an assertion about a later image layer.
 receipt.runtime=collectRuntime();
 receipt.runtime_binding=verifyRuntime(manifest.runtime_fingerprint,receipt.runtime,process.env.MOTION_IMAGE_DIGEST,
  {allowUnboundExperiment:process.env.MOTION_ALLOW_UNBOUND_EXPERIMENT==='1'});
 for(const name of [...inputFiles,'manifest.json','audio.wav'])cpSync(source+'/'+name,root+'/'+name);
 mkdirSync(root+'/assets',{recursive:true});
 cpSync('/opt/assets/gsap.min.js',root+'/assets/gsap.min.js');
 cpSync('/opt/assets/NotoSansCJK-Regular.ttc',root+'/assets/NotoSansCJK-Regular.ttc');
 if(mode==='observe'){
  receipt.stage='observing_scene';persist();
  await execute(process.execPath,creative?['/opt/renderer/creative.mjs','observe']:['/opt/renderer/observe.mjs']);
  receipt.status='observed_pending_validation';
 }else{
  const cli='/opt/renderer/node_modules/hyperframes/bin/hyperframes.mjs';
  receipt.status='rendering';receipt.stage='rendering_scene';persist();
  if(creative){
   await execute(process.execPath,['/opt/renderer/creative.mjs','render']);
   receipt.creative_capture=JSON.parse(readRegular(root+'/creative-render.json'));
   await execute('ffmpeg',['-v','error','-nostdin','-y','-framerate','24','-i',root+'/creative-frames/frame-%06d.jpg',
    '-frames:v',String(receipt.creative_capture.frame_count),'-c:v','libx264','-preset','veryfast',
    '-crf',receipt.quality==='final'?'18':'25','-pix_fmt','yuv420p','-movflags','+faststart',root+'/silent.mp4']);
  }else{
   await execute(process.execPath,[cli,'render',root,'--output',root+'/silent.mp4','--fps','24',
    '--workers','1','--quality',receipt.cli_quality,'--no-browser-gpu','--low-memory-mode','--strict','--no-best-effort']);
  }
  receipt.status='muxing';receipt.stage='preparing_audio';persist();
  await execute('ffmpeg',['-v','error','-nostdin','-y','-i',root+'/silent.mp4','-i',root+'/audio.wav',
   '-filter_complex',muxAudioFilter(timeline),
   '-map','0:v:0','-map','[audio]','-c:v','copy','-c:a','aac','-b:a','128k',root+'/output.mp4']);
  receipt.stage='validating_scene';persist();
  const probe=JSON.parse(await execute('ffprobe',['-v','error','-show_streams','-show_format','-of','json',root+'/output.mp4'],{capture:true}));
  validateProbe(probe,timeline);receipt.probe=probe;
  await execute('ffmpeg',['-v','error','-nostdin','-i',root+'/output.mp4','-f','null','-']);
  receipt.decode_verified=true;receipt.output_sha256=sha(readRegular(root+'/output.mp4',64*1024*1024));
  receipt.status='encoded_pending_validation';
 }
}catch(error){receipt.status=stopped||'failed';receipt.error=String(error);process.exitCode=1;}
finally{
 clearTimeout(timer);for(const child of children)kill(child);
 if(stopped){receipt.status=stopped;process.exitCode=1;}
 try{receipt.cgroup={memory_peak_bytes:readFileSync('/sys/fs/cgroup/memory.peak','utf8').trim(),cpu_stat:readFileSync('/sys/fs/cgroup/cpu.stat','utf8').trim()};}catch{}
 try{persist();}catch(error){console.error('receipt_unavailable',String(error));process.exitCode=1;}
}
