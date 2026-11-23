// Offline acceptance helper; imports the same release-owned capture function.
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {dirname,join} from 'node:path';
import {spawnSync} from 'node:child_process';
const config=JSON.parse(readFileSync(process.argv[2]));
const {chromium}=createRequire(config.playwright_package)('playwright-core');
const {captureSceneSpace}=await import(pathToFileURL(config.host).href);
const engineRuntime=await import(pathToFileURL(config.engine_runtime).href);
const projectRuntime=await import(pathToFileURL(config.project_runtime).href);
const run=(command,args)=>{const p=spawnSync(command,args,{timeout:120000,maxBuffer:1024*1024,encoding:'utf8'});
 if(p.error||p.status!==0)throw Error('native_probe_media_failed:'+String(p.error||p.stderr));return p.stdout;};
const results=[];
for(const input of config.cases){const b=JSON.parse(readFileSync(input.binding)),root=input.output;mkdirSync(root,{recursive:true});
 const resources=new Map(Object.entries(b.source.files).map(([n,s])=>['/'+n,Buffer.from(s)]));
 resources.set('/runtime/scene-space-runtime.mjs',readFileSync(config.scene_runtime));
 resources.set('/runtime/scene_space_surface.mjs',readFileSync(config.surface_runtime||join(dirname(config.scene_runtime),'scene_space_surface.mjs')));
 resources.set('/runtime/scene_space_surface_budget.mjs',readFileSync(config.surface_budget||join(dirname(config.scene_runtime),'scene_space_surface_budget.mjs')));
 resources.set('/runtime/scene_space_geometry.mjs',readFileSync(config.geometry_runtime||join(dirname(config.scene_runtime),'scene_space_geometry.mjs')));
 resources.set('/runtime/scene_space_geometry_budget.mjs',readFileSync(config.geometry_budget||join(dirname(config.scene_runtime),'scene_space_geometry_budget.mjs')));
 for(const [name,path]of Object.entries(input.assets))resources.set('/assets/'+name,readFileSync(path));
 let evidence;
 try{evidence=await captureSceneSpace({chromium,request:b.request,binding:b.scene_space,resources,engineRuntime,projectRuntime,
  root,executablePath:config.browser,hyperFramesDist:config.hyperframes_dist});}
 catch(error){if(!input.expect_error||!String(error).includes(input.expect_error))throw error;
  results.push({name:input.name,expected_rejection:input.expect_error,error:String(error),
   captured_frames:0,host_sha256:b.request.host_sha256,runtime_sha256:b.request.scene_runtime_sha256,
   surface_runtime_sha256:b.request.surface_runtime_sha256,surface_budget_sha256:b.request.surface_budget_sha256,
   geometry_runtime_sha256:b.request.geometry_runtime_sha256,geometry_budget_sha256:b.request.geometry_budget_sha256});continue;}
 if(input.expect_error)throw Error('native_probe_expected_rejection_missing:'+input.name);
 if(!b.request.static){run(config.ffmpeg,['-v','error','-y','-framerate','24','-i',root+'/frames/%06d.jpg',
   '-c:v','libx264','-pix_fmt','yuv420p','-crf','18',root+'/output.mp4']);
  run(config.ffmpeg,['-v','error','-i',root+'/output.mp4','-f','null','-']);
  evidence.media_probe=JSON.parse(run(config.ffprobe,['-v','error','-count_frames','-show_streams','-show_format','-of','json',root+'/output.mp4']));}
 writeFileSync(root+'/capture.json',JSON.stringify(evidence,null,2));
 results.push({name:input.name,host_sha256:b.request.host_sha256,runtime_sha256:b.request.scene_runtime_sha256,
  surface_runtime_sha256:b.request.surface_runtime_sha256,
  surface_budget_sha256:b.request.surface_budget_sha256,
  geometry_runtime_sha256:b.request.geometry_runtime_sha256,
  geometry_budget_sha256:b.request.geometry_budget_sha256,
  scene_space_sha256:b.scene_space.sha256,source_sha256:b.source_sha256,...evidence});
}
writeFileSync(config.output,JSON.stringify({schema:'video.scene-space-native-browser-probe.v1',
 evidence_scope:'actual_browser_frames_and_media_not_container_admission_or_visual_acceptance',
 whole_video_watched:false,motion_observed:false,audio_observed:false,results},null,2));
console.log(JSON.stringify({cases:results.map(r=>r.name),output:config.output}));
