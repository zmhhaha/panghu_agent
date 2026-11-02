import { readFileSync, lstatSync } from 'node:fs';
import { createHash } from 'node:crypto';
export const sha=raw=>createHash('sha256').update(raw).digest('hex');
export const inputFiles=['index.html','plan.json','beats.json','alignment.json','timeline.json'];
export const runtimeKeys=['schema','hyperframes','hyperframes_source','node','browser','ffmpeg','gsap',
 'gsap_sha256','font_sha256','dpkg_sha256','npm_lock_sha256'];
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exactKeys=(value,keys)=>object(value)&&JSON.stringify(Object.keys(value).sort())===JSON.stringify([...keys].sort());
const projectPair=(compiler,template)=>(compiler==='motion-compiler-1'&&template==='technical-diagrams-1')
 ||(compiler==='motion-demo-compiler-1'&&template==='record-demonstration-1');
function validateRuntimeShape(value,keys){
 if(!exactKeys(value,keys)||Object.values(value).some(v=>typeof v!=='string'||v.length<1||v.length>2000||/[\x00-\x1f\x7f]/.test(v)))
  throw Error('runtime_fingerprint_invalid');
 if(value.schema!=='video.motion-runtime.v1'||value.hyperframes!=='0.8.149'
  ||value.hyperframes_source!=='2905162d925fc87fb8242e24ed0bbf025d307ea1'||value.gsap!=='3.14.2')
  throw Error('runtime_version_mismatch');
 for(const key of ['gsap_sha256','font_sha256','dpkg_sha256','npm_lock_sha256'])
  if(!/^[0-9a-f]{64}$/.test(value[key]))throw Error('runtime_fingerprint_invalid');
}
export function verifyRuntime(frozen,installed,imageDigest,{allowUnboundExperiment=false}={}){
 // The image deliberately cannot supply its own registry identity. The trusted
 // coordinator pins image@digest and passes this same digest separately.
 if(!/^sha256:[0-9a-f]{64}$/.test(imageDigest||''))throw Error('external_image_digest_required');
 validateRuntimeShape(installed,runtimeKeys);
 if(frozen===null){
  if(allowUnboundExperiment!==true)throw Error('runtime_fingerprint_required');
  return {status:'unbound_experiment',image_digest:imageDigest,image_digest_source:'coordinator'};
 }
 validateRuntimeShape(frozen,[...runtimeKeys,'image_digest']);
 if(frozen.image_digest!==imageDigest)throw Error('runtime_image_digest_mismatch');
 for(const key of runtimeKeys)if(frozen[key]!==installed[key])throw Error('runtime_installed_mismatch:'+key);
 return {status:'matched',image_digest:imageDigest,image_digest_source:'coordinator'};
}
export function encodedDuration(timeline){
 if(!Number.isFinite(timeline.duration_seconds)||timeline.duration_seconds<=0||timeline.duration_seconds>60)throw Error('duration_outside_range');
 const frames=timeline.duration_seconds*24,nearest=Math.round(frames);
 // Compiler output is already frame-ceiled. Decimal serialization such as
 // 15.04166667 must not ceil the same 361 frames a second time to 362.
 return (nearest>=1&&Math.abs(frames-nearest)<=24e-6?nearest:Math.ceil(frames))/24;
}
export function muxAudioFilter(timeline){
 const leading=timeline.leading_seconds,trailing=timeline.trailing_seconds,audio=timeline.audio_seconds;
 if(![leading,trailing,audio].every(Number.isFinite)||leading<0||trailing<0||audio<=0)throw Error('audio_timing_invalid');
 const total=leading+audio+trailing,expected=Math.ceil(total*24)/24;
 // The compiler permits only the fractional final frame as padding. Check
 // against its raw-audio ceil, not equality with the unpadded recording.
 if(Math.abs(encodedDuration(timeline)-expected)>1e-6)throw Error('audio_timing_invalid');
 // whole_dur pads to the end of the final complete video frame; pad_dur with
 // only trailing seconds would leave fractional-frame tails without audio.
 // No trim, -shortest or stretch is applied to the original recording.
 return `[1:a]adelay=${leading*1000}:all=1,apad=whole_dur=${encodedDuration(timeline)}[audio]`;
}
export function readRegular(path,limit=1024*1024){
 const stat=lstatSync(path);
 if(!stat.isFile()||stat.isSymbolicLink()||stat.size>limit)throw Error('input_not_regular_or_large');
 return readFileSync(path);
}
// The coordinator freezes this expected digest from trusted compiler output.
// Accepting a digest from an arbitrary page author would not establish trust.
export function verifyInput(root,expected){
 if(!/^[0-9a-f]{64}$/.test(expected||''))throw Error('expected_manifest_required');
 const bytes=readRegular(root+'/manifest.json');
 if(sha(bytes)!==expected)throw Error('manifest_binding_mismatch');
 const manifest=JSON.parse(bytes);
 if(manifest.schema!=='video.motion-project.v1'||!projectPair(manifest.compiler,manifest.template)
  ||manifest.hyperframes!=='0.8.149'
  ||manifest.gsap!=='3.14.2'||manifest.width!==1280||manifest.height!==720||manifest.fps!==24)
  throw Error('project_contract_mismatch');
 if(JSON.stringify(Object.keys(manifest.files).sort())!==JSON.stringify([...inputFiles].sort()))throw Error('project_files_mismatch');
 for(const name of inputFiles){if(sha(readRegular(root+'/'+name))!==manifest.files[name])throw Error('input_digest_mismatch:'+name);}
 const timeline=JSON.parse(readRegular(root+'/timeline.json'));
 if(timeline.schema!=='video.motion-timeline.v1'||timeline.compiler!==manifest.compiler
  ||timeline.audio_sha256!==manifest.audio_sha256
  ||timeline.plan_sha256!==manifest.plan_sha256||manifest.files['plan.json']!==manifest.plan_sha256
  ||manifest.files['timeline.json']!==manifest.timeline_sha256
  ||!Number.isFinite(timeline.duration_seconds)||timeline.duration_seconds<=0||timeline.duration_seconds>60)
  throw Error('timeline_contract_mismatch');
 if(sha(readRegular(root+'/audio.wav',8*1024*1024))!==timeline.audio_sha256)throw Error('audio_binding_mismatch');
 return {manifest,timeline};
}
export function validateProbe(probe,timeline){
 const seconds=encodedDuration(timeline);
 const video=probe.streams?.filter(s=>s.codec_type==='video')||[];
 const audio=probe.streams?.filter(s=>s.codec_type==='audio')||[];
 if(video.length!==1||audio.length!==1)throw Error('track_count_mismatch');
 const v=video[0],a=audio[0];
 if(![Number(v.duration),Number(a.duration),Number(v.nb_frames)].every(Number.isFinite))throw Error('output_metrics_missing');
 const ratio=x=>{const[n,d]=x.split('/').map(Number);return n/d;};
 if(v.width!==1280||v.height!==720||v.codec_name!=='h264'||a.codec_name!=='aac'
  ||ratio(v.avg_frame_rate)!==24||Math.abs(Number(v.duration)-seconds)>.001
  ||Number(v.nb_frames)!==Math.round(seconds*24)
  ||Number(a.duration)+.03<seconds
  ||Math.abs(Number(a.duration)-seconds)>.12)
  throw Error('output_contract_mismatch');
}
