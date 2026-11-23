// New release-owned route. Historical picture/video/method hosts stay exact.
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {readFileSync,writeFileSync,lstatSync,mkdirSync,unlinkSync} from 'node:fs';
import {extname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {performance} from 'node:perf_hooks';
const hash=raw=>createHash('sha256').update(raw).digest('hex');
const requireValue=(ok,code='scene_space_host_invalid')=>{if(!ok)throw Error(code);};
const read=(path,max)=>{const s=lstatSync(path);requireValue(s.isFile()&&!s.isSymbolicLink()&&s.size<=max,'scene_space_file_invalid');return readFileSync(path);};

// Drain coverage while each document's Debugger script IDs are still live.
export async function collectSceneDrawingExecution(session,origin,library,previous=[]){
 const captured=await session.send('Profiler.takePreciseCoverage');
 const byRange=new Map(previous.map(fn=>[fn.start_offset+':'+fn.end_offset,{...fn}]));
 for(const script of captured.result){if(script.url!==origin+'/assets/ink-landscape.mjs')continue;
  const text=await session.send('Debugger.getScriptSource',{scriptId:script.scriptId});
  requireValue(hash(Buffer.from(text.scriptSource,'utf8'))===library.assets['ink-landscape.mjs'],'scene_space_drawing_coverage_changed');
  for(const fn of script.functions){const r=fn.ranges[0];if(!fn.functionName||!r||r.count<=0)continue;
   requireValue(Number.isSafeInteger(r.count)&&fn.functionName.length<=200,'scene_space_drawing_coverage_invalid');
   const key=r.startOffset+':'+r.endOffset,old=byRange.get(key);
   byRange.set(key,{function_name:fn.functionName,start_offset:r.startOffset,end_offset:r.endOffset,
    call_count:r.count+(old?.call_count||0)});}}
 const rows=[...byRange.values()].sort((a,b)=>a.start_offset-b.start_offset||a.end_offset-b.end_offset);
 requireValue(rows.length<=1024&&rows.every(fn=>Number.isSafeInteger(fn.call_count)),'scene_space_drawing_coverage_budget');
 return rows;
}

export async function captureSceneSpace({chromium,request,binding,resources,engineRuntime,projectRuntime,root,
 executablePath='/opt/browser/headless_shell',hyperFramesDist='/opt/renderer/node_modules/hyperframes/dist'}){
 requireValue(request.schema==='video.scene-space-render-request.v1'&&request.engine==='canvas');
 requireValue(binding.schema==='video.scene-space-render-binding.v1'&&binding.backend==='canvas'
  &&binding.runtime_sha256===request.scene_runtime_sha256&&binding.source_sha256===request.source_sha256
  &&binding.artwork_api_revision===2&&request.artwork_api_revision===2
  &&binding.surface_runtime_sha256===request.surface_runtime_sha256
  &&binding.surface_budget_sha256===request.surface_budget_sha256);
 const surfaceRuntime=resources.get('/runtime/scene_space_surface.mjs');
 const surfaceBudget=resources.get('/runtime/scene_space_surface_budget.mjs');
 requireValue(surfaceRuntime&&hash(surfaceRuntime)===request.surface_runtime_sha256,'scene_space_surface_runtime_changed');
 requireValue(surfaceBudget&&hash(surfaceBudget)===request.surface_budget_sha256,'scene_space_surface_budget_changed');
 const sources=Object.fromEntries([...resources].filter(([name])=>['/scene.html','/scene.js','/scene.css'].includes(name)).map(([n,b])=>[n.slice(1),b]));
 const assets=Object.fromEntries([...resources].filter(([n])=>n.startsWith('/assets/')).map(([n,b])=>[n.slice(1),b]));
 const project=projectRuntime.buildHyperFramesProject(request.project,sources,assets).project;
 const core=projectRuntime.hyperFramesRuntimeArtifact(hyperFramesDist),c=request.constraints;
 const mime={'.html':'text/html;charset=utf-8','.js':'text/javascript;charset=utf-8','.mjs':'text/javascript;charset=utf-8',
  '.css':'text/css;charset=utf-8','.ttc':'font/collection','.png':'image/png'};
 const server=createServer((req,res)=>{const u=new URL(req.url,'http://localhost'),bytes=resources.get(u.pathname);
  res.setHeader('Content-Security-Policy',"default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; media-src 'none'; worker-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'");
  res.setHeader('X-Content-Type-Options','nosniff');
  if(req.method!=='GET'||u.search||!bytes){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',mime[extname(u.pathname)]||'application/octet-stream');res.end(bytes);});
 await new Promise((done,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',done);});
 let browser;
 try{const origin='http://127.0.0.1:'+server.address().port,errors=[],blocked=[],scripts=new Set();
  browser=await chromium.launch({headless:true,executablePath,args:engineRuntime.browserLaunchArgs('canvas')});
  const context=await browser.newContext({viewport:{width:c.width,height:c.height},deviceScaleFactor:1,
   serviceWorkers:'block',acceptDownloads:false});
  await engineRuntime.installEngineGuard(context,'canvas');
  await context.route('**/*',async route=>{const u=new URL(route.request().url());
   if(u.origin===origin&&resources.has(u.pathname)&&!u.search&&route.request().method()==='GET'){
    if(route.request().resourceType()==='script')scripts.add(u.pathname);await route.continue();
   }else{if(blocked.length<16)blocked.push(u.protocol);await route.abort();}});
  if(context.routeWebSocket)await context.routeWebSocket('**/*',socket=>{blocked.push('websocket');socket.close();});
  const page=await context.newPage();page.setDefaultTimeout(20000);
  const security=await context.newCDPSession(page);
  if(request.drawing_libraries){await security.send('Debugger.enable');await security.send('Profiler.enable');
   await security.send('Profiler.startPreciseCoverage',{callCount:true,detailed:true});}
  page.on('pageerror',()=>errors.push('scene_error'));page.on('crash',()=>errors.push('page_crashed'));
  page.on('popup',popup=>{errors.push('popup_forbidden');popup.close();});page.on('dialog',d=>d.dismiss());
  const ready=async()=>{await page.goto(origin+'/scene.html',{waitUntil:'networkidle',timeout:30000});
   await page.evaluate(async binding=>{await window.__sceneSpaceArtwork?.ready;
    const faces=[...document.fonts];if(faces.length>64)throw Error('scene_space_font_budget_exceeded');
    await Promise.all(faces.map(face=>face.load()));await document.fonts.ready;
    const nodes=document.querySelectorAll('canvas[data-scene-space-output]');
    if(nodes.length!==1||typeof window.__sceneSpaceArtwork?.paintObject!=='function')throw Error('scene_space_artwork_hook_missing');
    const {createSceneSpaceRenderer}=await import('/runtime/scene-space-runtime.mjs');
    const compositor=createSceneSpaceRenderer({canvas:nodes[0],binding,paintObject:window.__sceneSpaceArtwork.paintObject,
     paintCoverage:window.__sceneSpaceArtwork.paintCoverage});
    const rect=nodes[0].getBoundingClientRect();
    if(Math.abs(rect.x)>1e-7||Math.abs(rect.y)>1e-7||Math.abs(rect.width-binding.frozen.plan.viewport.width)>1e-7
     ||Math.abs(rect.height-binding.frozen.plan.viewport.height)>1e-7)throw Error('scene_space_output_viewport_mismatch');
    Object.defineProperty(window,'__animationExperiment',{value:Object.freeze({seek:seconds=>compositor.seek(seconds)}),writable:false,configurable:false});
    Object.defineProperty(window,'__trustedSceneSpaceObservation',{value:()=>compositor.observation,writable:false,configurable:false});
   },binding);
   await projectRuntime.installHyperFramesProject(page,{project,runtimePath:core.runtimePath,legacyBridge:true});};
  const start=binding.frame_range.start_frame,end=binding.frame_range.end_frame,count=end-start;
  const seek=async frame=>{await projectRuntime.seekHyperFramesProject(page,(frame-start)/24);};
  const capture=async frame=>{await seek(frame);await page.evaluate(async()=>{
    await document.fonts.ready;if(document.getAnimations().some(a=>a.playState==='running'))throw Error('scene_space_autonomous_animation');});
   const bytes=await page.screenshot(request.static?{type:'png',animations:'disabled'}:{type:'jpeg',quality:88,animations:'disabled'});
   requireValue(!errors.length&&!blocked.length,'scene_space_scene_failed_or_blocked');return bytes;};
  await ready();
  const selected=[...new Set([start,Math.min(end-1,start+Math.floor(count/2)),end-1])].sort((a,b)=>a-b);
  const reference=new Map(),samples=[],sampleFrames=[];let frameBytes=0;
  if(!request.static)mkdirSync(join(root,'frames'),{recursive:false});
  for(let frame=start;frame<(request.static?start+1:end);frame++){
   const bytes=await capture(frame);frameBytes+=bytes.length;
   requireValue(frameBytes<=request.max_frame_bytes,'scene_space_frame_disk_budget_exceeded');
   if(selected.includes(frame)){reference.set(frame,bytes);
    if(!request.static){const name='sample-'+String(frame).padStart(6,'0')+'.jpg';writeFileSync(join(root,name),bytes);
     sampleFrames.push({frame,file_name:name,time_seconds:(frame-start)/24,sha256:hash(bytes),bytes:bytes.length});}}
   if(request.static)writeFileSync(join(root,'output.png'),bytes);
   else writeFileSync(join(root,'frames',String(frame-start).padStart(6,'0')+'.jpg'),bytes);
  }
  if(request.static){selected.splice(0,selected.length,start);reference.set(start,read(join(root,'output.png'),8*1024*1024));}
  for(const frame of [...selected].reverse()){const reverse=await capture(frame),repeat=await capture(frame);
   requireValue(reverse.equals(reference.get(frame))&&repeat.equals(reverse),'scene_space_reverse_or_repeat_mismatch');
   samples.push({frame,sha256:hash(reverse)});}
  const firstObservation=await page.evaluate(()=>window.__trustedSceneSpaceObservation());
  let drawingFunctions=[];
  if(request.drawing_libraries)drawingFunctions=await collectSceneDrawingExecution(security,origin,
   request.drawing_libraries['ink-landscape'],drawingFunctions);
  await ready();
  for(const frame of selected){requireValue((await capture(frame)).equals(reference.get(frame)),'scene_space_reload_mismatch');}
  if(request.drawing_libraries)drawingFunctions=await collectSceneDrawingExecution(security,origin,
   request.drawing_libraries['ink-landscape'],drawingFunctions);
  const engineObservation=await engineRuntime.verifyPageEngine(page,'canvas');
  const runtimeProof=await page.evaluate(()=>({...window.__trustedHyperFramesProject.state}));
  requireValue(firstObservation.objects_painted===binding.object_caches.length&&firstObservation.cache_pixels===binding.total_cached_pixels,
   'scene_space_object_cache_not_consumed');
  let drawingObservation;
  if(request.drawing_libraries){const library=request.drawing_libraries['ink-landscape'];
   requireValue(scripts.has('/assets/ink-landscape.mjs'),'scene_space_drawing_library_not_loaded');
   drawingObservation={schema:'video.learning-drawing-library-observation.v1',scope:'loaded_scripts_not_api_calls_or_visual_application',
    drawing_libraries:request.drawing_libraries,loaded_scripts:[...scripts].sort(),loaded_libraries:['ink-landscape'],
    api_execution:{schema:'video.learning-drawing-api-execution.v1',scope:'v8_executed_functions_not_visual_application',
     module_sha256:library.assets['ink-landscape.mjs'],functions:drawingFunctions}};}
  return{checks:{repeat_passed:true,reverse_passed:true,reload_passed:true,samples},sample_frames:sampleFrames,
   ...(drawingObservation?{drawing_library_observation:drawingObservation}:{}),
   scene_space_observation:{...firstObservation,frame_range:binding.frame_range,runtime_sha256:request.scene_runtime_sha256,
    surface_runtime_sha256:request.surface_runtime_sha256,surface_budget_sha256:request.surface_budget_sha256,
    artwork_api_revision:request.artwork_api_revision},
   engine_observation:engineObservation,loaded_scripts:[...scripts].sort(),
   project_runtime:{...runtimeProof,core_sha256:core.core_sha256,project_sha256:request.project_sha256},
   captured_frames:request.static?1:count,frame_bytes:frameBytes};
 }finally{if(browser)await browser.close().catch(()=>{});await new Promise(done=>server.close(done));}
}

async function main(){const began=performance.now(),receipt={schema:'video.scene-space-render-receipt.v1',status:'starting'};
 try{const raw=read('/input/request.json',256*1024),request=JSON.parse(raw);
  requireValue(hash(raw)===process.env.ANIMATION_METHOD_REQUEST_SHA256&&request.image_digest===process.env.ANIMATION_METHOD_IMAGE_DIGEST,
   'scene_space_request_binding_invalid');
  requireValue(hash(read(fileURLToPath(import.meta.url),1024*1024))===request.host_sha256,'scene_space_host_changed');
  Object.assign(receipt,{request_identity:request.identity,request_sha256:hash(raw),
   ...Object.fromEntries(['host_sha256','scene_runtime_sha256','surface_runtime_sha256','surface_budget_sha256','artwork_api_revision','sidecar_sha256','image_digest'].map(k=>[k,request[k]]))});
  const sidecar=read('/input/scene-space.json',16*1024*1024),runtime=read('/input/scene-space-runtime.mjs',256*1024);
  const surfaceRuntime=read('/input/scene_space_surface.mjs',256*1024);
  const surfaceBudget=read('/input/scene_space_surface_budget.mjs',64*1024);
  requireValue(hash(sidecar)===request.sidecar_sha256&&hash(runtime)===request.scene_runtime_sha256,'scene_space_sidecar_changed');
  requireValue(hash(surfaceRuntime)===request.surface_runtime_sha256&&request.artwork_api_revision===2,'scene_space_surface_runtime_changed');
  requireValue(hash(surfaceBudget)===request.surface_budget_sha256,'scene_space_surface_budget_changed');
  const resources=new Map([['/runtime/scene-space-runtime.mjs',runtime],['/runtime/scene_space_surface.mjs',surfaceRuntime],
   ['/runtime/scene_space_surface_budget.mjs',surfaceBudget]]);
  for(const [name,sha]of Object.entries(request.files_sha256)){
   requireValue(['scene.html','scene.js','scene.css'].includes(name),'scene_space_source_path_invalid');
   const bytes=read('/input/scene/'+name,512*1024);requireValue(hash(bytes)===sha,'scene_space_source_changed');resources.set('/'+name,bytes);}
  for(const [name,sha]of Object.entries(request.assets_sha256)){
   requireValue(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$/.test(name),'scene_space_asset_path_invalid');
   const bytes=read('/input/scene/assets/'+name,32*1024*1024);requireValue(hash(bytes)===sha,'scene_space_asset_changed');resources.set('/assets/'+name,bytes);}
  if(request.drawing_libraries){const library=request.drawing_libraries['ink-landscape'];
   requireValue(library?.schema==='video.learning-drawing-library.v1'&&library.library_id==='ink-landscape'
    &&library.source_revision==='b6c96a5e94124bfad8113aaa136ede2298151b5f'
    &&library.assets?.['ink-landscape.mjs']==='c6bb3f07c09776beddddf04a7e7d9faae38ec8b6f3499abd8e6722d2115d46f6'
    &&Object.entries(library.assets).every(([name,sha])=>request.assets_sha256[name]===sha),'scene_space_drawing_descriptor_changed');}
  for(const [name,key]of [['engine_runtime.mjs','engine_runtime_sha256'],['hyperframes_project.mjs','project_runtime_sha256']])
   requireValue(hash(read('/opt/renderer/'+name,1024*1024))===request[key],'scene_space_dependency_changed');
  const engineRuntime=await import('./engine_runtime.mjs'),projectRuntime=await import('./hyperframes_project.mjs');
  const {chromium}=createRequire('/opt/renderer/package.json')('playwright-core');
  const evidence=await captureSceneSpace({chromium,request,binding:JSON.parse(sidecar),resources,engineRuntime,projectRuntime,root:'/work'});
  const output=request.static?'output.png':'output.mp4';
  if(!request.static){const args=['-hide_banner','-loglevel','error','-y','-framerate','24','-i','/work/frames/%06d.jpg',
    '-c:v','libx264','-pix_fmt','yuv420p','-crf','18','-movflags','+faststart','/work/output.mp4'];
   const encoded=spawnSync('ffmpeg',args,{timeout:180000,maxBuffer:1024*1024});requireValue(!encoded.error&&encoded.status===0,'scene_space_encode_failed');
   const frames=JSON.parse(sidecar).frame_range;
   for(let i=0;i<frames.end_frame-frames.start_frame;i++)unlinkSync('/work/frames/'+String(i).padStart(6,'0')+'.jpg');
   const decoded=spawnSync('ffmpeg',['-v','error','-i','/work/output.mp4','-f','null','-'],{timeout:180000,maxBuffer:1024*1024});
   requireValue(!decoded.error&&decoded.status===0,'scene_space_decode_failed');
   const probed=spawnSync('ffprobe',['-v','error','-count_frames','-show_streams','-show_format','-of','json','/work/output.mp4'],
    {timeout:30000,maxBuffer:1024*1024,encoding:'utf8'});
   requireValue(!probed.error&&probed.status===0,'scene_space_probe_failed');
   evidence.media_probe=JSON.parse(probed.stdout);}
  const bytes=read('/work/'+output,request.max_media_bytes);
  Object.assign(receipt,{status:'rendered',request_identity:request.identity,request_sha256:hash(raw),
   ...Object.fromEntries(['host_sha256','scene_runtime_sha256','surface_runtime_sha256','surface_budget_sha256','artwork_api_revision','sidecar_sha256','image_digest'].map(k=>[k,request[k]])),
   output_sha256:hash(bytes),output_bytes:bytes.length,evidence_scope:request.static?'static_only':'sampled_frames_only',
   motion_observed:false,audio_observed:false,...evidence});
 }catch(error){receipt.status='failed';receipt.error=String(error).slice(0,1000);process.exitCode=1;}
 receipt.measured_render_seconds=(performance.now()-began)/1000;
 writeFileSync('/work/render-receipt.json',JSON.stringify(receipt,null,2));
}
if(process.argv[1]&&fileURLToPath(import.meta.url)===process.argv[1])await main();
