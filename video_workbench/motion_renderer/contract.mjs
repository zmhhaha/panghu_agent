import { readFileSync, lstatSync } from 'node:fs';
import { createHash } from 'node:crypto';
export const sha=raw=>createHash('sha256').update(raw).digest('hex');
// Verified against the pinned v0.8.149 CLI: delivery is its high quality preset.
export function renderQuality(value='draft'){
 if(value==='draft')return {quality:'draft',cli_quality:'draft'};
 if(value==='final')return {quality:'final',cli_quality:'delivery'};
 throw Error('quality_outside_range');
}
export const inputFiles=['index.html','plan.json','beats.json','alignment.json','timeline.json'];
export const runtimeKeys=['schema','hyperframes','hyperframes_source','node','browser','ffmpeg','gsap',
 'gsap_sha256','font_sha256','dpkg_sha256','npm_lock_sha256'];
export const engineRuntimeKeys=[...runtimeKeys,'three','three_sha256','three_license_sha256','engine_runtime_sha256','creative_host_sha256'];
export const hyperFramesRuntimeKeys=[...engineRuntimeKeys,'hyperframes_core_sha256','hyperframes_project_host_sha256'];
const keysForRuntime=value=>value?.schema==='video.motion-runtime.v3'?hyperFramesRuntimeKeys:
 value?.schema==='video.motion-runtime.v2'?engineRuntimeKeys:runtimeKeys;
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exactKeys=(value,keys)=>object(value)&&JSON.stringify(Object.keys(value).sort())===JSON.stringify([...keys].sort());
const projectPair=(compiler,template)=>(compiler==='motion-compiler-1'&&template==='technical-diagrams-1')
 ||(compiler==='motion-compiler-2'&&template==='technical-diagrams-2')
 ||(compiler==='motion-compiler-3'&&template==='technical-diagrams-3')
 ||(compiler==='motion-compiler-4'&&template==='technical-diagrams-4')
 ||(compiler==='motion-compiler-5'&&template==='technical-diagrams-5')
 ||(compiler==='motion-compiler-6'&&template==='technical-diagrams-6')
 ||(compiler==='motion-process-compiler-2'&&template==='controlled-process-2')
 ||(compiler==='motion-process-continuity-compiler-1'&&template==='continuous-process-1')
 ||(compiler==='motion-continuity-compiler-1'&&template==='continuous-diagrams-1')
 ||(compiler==='motion-continuity-compiler-2'&&template==='continuous-diagrams-2')
 ||(compiler==='motion-demo-compiler-1'&&template==='record-demonstration-1')
 ||(compiler==='creative-scene-adapter-1'&&template==='generated-html-svg-1')
 ||(compiler==='creative-method-scene-adapter-1'&&template==='generated-method-html-svg-1')
 ||(compiler==='creative-method-engine-scene-adapter-1'&&template==='generated-method-engine-scene-1')
 ||(compiler==='creative-film-scene-adapter-1'&&template==='generated-film-engine-scene-1')
 ||(compiler==='creative-skill-film-scene-adapter-1'&&template==='generated-skill-film-engine-scene-1');
function validateRuntimeShape(value,keys){
 if(!exactKeys(value,keys)||Object.values(value).some(v=>typeof v!=='string'||v.length<1||v.length>2000||/[\x00-\x1f\x7f]/.test(v)))
  throw Error('runtime_fingerprint_invalid');
 if(!['video.motion-runtime.v1','video.motion-runtime.v2','video.motion-runtime.v3'].includes(value.schema)||value.hyperframes!=='0.8.149'
  ||value.hyperframes_source!=='2905162d925fc87fb8242e24ed0bbf025d307ea1'||value.gsap!=='3.14.2')
  throw Error('runtime_version_mismatch');
 for(const key of ['gsap_sha256','font_sha256','dpkg_sha256','npm_lock_sha256'])
  if(!/^[0-9a-f]{64}$/.test(value[key]))throw Error('runtime_fingerprint_invalid');
 if(['video.motion-runtime.v2','video.motion-runtime.v3'].includes(value.schema)&&(value.three!=='0.170.0'
  ||value.three_sha256!=='08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7'
  ||value.three_license_sha256!=='4c40a1ef62450b857c3b2aaf294936304cd552d965fbcd9d32d4c5bcf4ba4454'
  ||!/^[0-9a-f]{64}$/.test(value.engine_runtime_sha256)
  ||!/^[0-9a-f]{64}$/.test(value.creative_host_sha256)))throw Error('runtime_version_mismatch');
 if(value.schema==='video.motion-runtime.v3'&&(value.hyperframes_core_sha256!=='675e02ee21f31540e3a6abaec061f86cea92a0178ae67a390700acb4f801dbbc'
  ||!/^[0-9a-f]{64}$/.test(value.hyperframes_project_host_sha256)))throw Error('runtime_version_mismatch');
}
export function verifyRuntime(frozen,installed,imageDigest,{allowUnboundExperiment=false}={}){
 // The image deliberately cannot supply its own registry identity. The trusted
 // coordinator pins image@digest and passes this same digest separately.
 if(!/^sha256:[0-9a-f]{64}$/.test(imageDigest||''))throw Error('external_image_digest_required');
 const installedKeys=keysForRuntime(installed);
 validateRuntimeShape(installed,installedKeys);
 if(frozen===null){
  if(allowUnboundExperiment!==true)throw Error('runtime_fingerprint_required');
  return {status:'unbound_experiment',image_digest:imageDigest,image_digest_source:'coordinator'};
 }
 const frozenKeys=keysForRuntime(frozen);
 validateRuntimeShape(frozen,[...frozenKeys,'image_digest']);
 if(frozen.image_digest!==imageDigest)throw Error('runtime_image_digest_mismatch');
 for(const key of frozenKeys)if(frozen[key]!==installed[key])throw Error('runtime_installed_mismatch:'+key);
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
 const plan=JSON.parse(readRegular(root+'/plan.json'));
 const skillCreative=manifest.compiler==='creative-skill-film-scene-adapter-1';
 const filmCreative=manifest.compiler==='creative-film-scene-adapter-1'||skillCreative;
 const engineCreative=manifest.compiler==='creative-method-engine-scene-adapter-1'||filmCreative;
 const methodCreative=manifest.compiler==='creative-method-scene-adapter-1'||engineCreative;
 if(manifest.compiler==='creative-scene-adapter-1'||methodCreative){
  const schema=skillCreative?'video.creative-scene.v5':filmCreative?'video.creative-scene.v4':engineCreative?'video.creative-scene.v3':methodCreative?'video.creative-scene.v2':'video.creative-scene.v1';
  const audio=methodCreative?plan.input_document?.scene?.audio_sha256:plan.input_document?.constraints?.production?.audio_sha256;
  if(plan?.schema!==schema||!(engineCreative?['html-svg','canvas','three']:['html-svg']).includes(plan.engine)
   ||plan.adapter_revision!==manifest.compiler||audio!==manifest.audio_sha256)
   throw Error('creative_plan_binding_mismatch');
  const source=methodCreative?plan.result?.source:plan.result;
  if(skillCreative){
   const proof=timeline.creative;
   if(plan.input_document?.schema!=='video.animation-production-input.v4'
    ||plan.result?.schema!=='video.animation-production-result.v4'
    ||plan.skill_catalog_sha256!==plan.input_document?.skill_catalog?.sha256
    ||plan.skill_delivery_sha256!==plan.skill_delivery?.sha256
    ||plan.skill_tool_evidence_sha256!==plan.skill_tool_evidence?.sha256
    ||plan.result.skill_tool_evidence_sha256!==plan.skill_tool_evidence_sha256
    ||!['skill_catalog_sha256','skill_delivery_sha256','skill_tool_evidence_sha256'].every(key=>
     /^[0-9a-f]{64}$/.test(plan[key]||'')&&proof?.[key]===plan[key])
    ||proof?.skill_evidence_scope!==plan.evidence_scope)throw Error('creative_skill_binding_mismatch');
  }
  if(filmCreative){
   const context=plan.input_document?.film_context,film=context?.film;
   const handoff=plan.result?.handoff,proof=timeline.creative;
   const index=context?.current_scene_index,row=film?.scenes?.[index];
   if(!Number.isInteger(index)||!row||context.film_sha256!==proof?.film_sha256
    ||handoff?.film_sha256!==context.film_sha256||handoff?.scene_id!==row.scene_id
    ||row.scene_id!==plan.input_document.scene.scene_id||row.audio_sha256!==timeline.audio_sha256
    ||Math.abs(row.audio_seconds-timeline.audio_seconds)>1e-9
    ||Math.abs(row.start_seconds+timeline.duration_seconds-row.end_seconds)>1e-6
    ||Math.abs(row.leading_silence_seconds-timeline.leading_seconds)>1e-9
    ||Math.abs(row.trailing_silence_seconds-(timeline.duration_seconds-timeline.leading_seconds-timeline.audio_seconds))>1e-6
    ||proof.predecessor_motion_version_id!==(context.predecessor?.motion_version_id??null)
    ||(index===0?context.predecessor!==null:context.predecessor?.scene_id!==film.scenes[index-1]?.scene_id)
    ||!['film_context_sha256','film_sha256','handoff_sha256'].every(key=>/^[0-9a-f]{64}$/.test(proof[key]||'')))
    throw Error('creative_film_binding_mismatch');
  }
  if(engineCreative&&(source?.engine!==plan.engine||manifest.engine!==plan.engine
   ||timeline.creative?.engine!==plan.engine
   ||!(skillCreative?['video.motion-runtime.v2','video.motion-runtime.v3']:['video.motion-runtime.v2']).includes(manifest.runtime_fingerprint?.schema)
   ||!plan.input_document?.constraints?.allowed_engines?.includes(plan.engine)))throw Error('creative_plan_binding_mismatch');
  if(engineCreative){
   const runtime=manifest.runtime_fingerprint,identity=plan.input_document.constraints.runtime_identity;
   if((plan.engine!=='html-svg'&&!identity)||(identity&&(identity.image_digest!==runtime.image_digest
    ||identity.host_sha256!==runtime.creative_host_sha256||identity.engine_runtime_sha256!==runtime.engine_runtime_sha256)))
    throw Error('creative_plan_binding_mismatch');
   const assets=plan.engine==='three'?{'three.module.min.js':runtime.three_sha256}:{};
   if(JSON.stringify(manifest.engine_assets)!==JSON.stringify(assets)
    ||(plan.engine==='three'&&plan.input_document.assets?.['three.module.min.js']!==runtime.three_sha256)
    ||JSON.stringify(manifest.required_assets)!==JSON.stringify(['assets/gsap.min.js','assets/NotoSansCJK-Regular.ttc',
     ...Object.keys(assets).map(name=>'assets/'+name)]))throw Error('creative_asset_binding_mismatch');
  }
  const files=source?.files,hashes=source?.files_sha256;
  if(!object(files)||!object(hashes)||!files['scene.html']||Object.keys(files).length>3
   ||JSON.stringify(Object.keys(files).sort())!==JSON.stringify(Object.keys(hashes).sort())
   ||Object.keys(files).some(name=>!['scene.html','scene.js','scene.css'].includes(name)
     ||typeof files[name]!=='string'||sha(Buffer.from(files[name]))!==hashes[name])
   ||sha(Buffer.from(files['scene.html']))!==manifest.files['index.html'])throw Error('creative_source_mismatch');
 }
 if((plan?.schema==='video.creative-scene.v1')!==(manifest.compiler==='creative-scene-adapter-1')
  ||(plan?.schema==='video.creative-scene.v2')!==(methodCreative&&!engineCreative)
  ||(plan?.schema==='video.creative-scene.v3')!==(engineCreative&&!filmCreative)
  ||(plan?.schema==='video.creative-scene.v4')!==(filmCreative&&!skillCreative)
  ||(plan?.schema==='video.creative-scene.v5')!==skillCreative)throw Error('project_schema_compiler_mismatch');
 if((manifest.compiler==='motion-process-compiler-2')!==(plan?.schema==='video.process-plan.v1')
  ||(manifest.compiler==='motion-process-continuity-compiler-1')!==(plan?.schema==='video.process-continuity-scene.v1'))
  throw Error('project_schema_compiler_mismatch');
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
