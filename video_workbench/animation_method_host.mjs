// Trusted method-demonstration host. Scene scripts never own encoding or sound.
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {readFileSync,writeFileSync,mkdirSync,lstatSync,unlinkSync,existsSync} from 'node:fs';
import {join,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {isDeepStrictEqual} from 'node:util';

const hash=raw=>createHash('sha256').update(raw).digest('hex');
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const ANIME_ASSETS={
 'anime.esm.min.js':'a19015a1a92d52025a2fb6703b6d67eadd1cc2aeaf880770e96e04cf6aa07be1',
 'animejs.LICENSE.txt':'3f3e835a9952cfc2a6ca836fb95b3257c981f24fa3ef51249055acb09931897b',
 'anime_timeline.mjs':'b8d594eca930e0758be51e49724ca6274c1c1d3e95d16f43e78d8196fd89cf67'};
export function validateAuthoringLibraries(request,resources){
 const expected={animejs:{schema:'video.animation-authoring-library.v1',library_id:'animejs',
  version:'4.5.0',license:'MIT',role:'animation-authoring',engines:['html-svg','canvas','three'],
  clock:'hyperframes-absolute-seek',assets:{...ANIME_ASSETS}}};
 if(!['video.animation-method-render-request.v4','video.animation-method-render-request.v5'].includes(request.schema)
  ||!isDeepStrictEqual(request.authoring_libraries,expected))throw Error('method_authoring_library_invalid');
 for(const [name,sha] of Object.entries(ANIME_ASSETS))
  if(request.assets_sha256[name]!==sha||!resources.has('/assets/'+name)
   ||hash(resources.get('/assets/'+name))!==sha)throw Error('method_authoring_library_asset_changed');
 return expected;
}

const MANIM_HELPERS={
 'manim_frame_layer.mjs':'de34dea66d0ce8449080b35668f38b4e1ed6ae55e10690d9a3f1b118bb07fa51',
 'manim_method_frames.mjs':'6c1d5cd90cbfbe862a646b014ad988a6637c5b74a9af1cb7bd4d5513a8606bfc'};
const canonicalResource=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)
 ?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
export function validateGeneratedResources(request,resources){
 if(request.schema!=='video.animation-method-render-request.v5'||!Array.isArray(request.generated_resources)
  ||request.generated_resources.length!==1)throw Error('method_generated_resource_invalid');
 const names=new Set();let decoded=0,total=0;
 for(const descriptor of request.generated_resources){
  const scope=descriptor?.scope;
  const fields='assets_sha256,envelope_sha256,helpers,image_digest,manifest,manifest_path,manifest_sha256,schema,scope,sha256';
  if(descriptor?.schema!=='video.manim-method-resource.v1'||Object.keys(descriptor).sort().join(',')!==fields
   ||!/^[0-9a-f]{64}$/.test(descriptor.sha256)||!isDeepStrictEqual(descriptor.helpers,MANIM_HELPERS)
   ||hash(canonicalResource(Object.fromEntries(Object.entries(descriptor).filter(([key])=>key!=='sha256'))))!==descriptor.sha256
   ||!scope||Object.keys(scope).sort().join(',')!=='effect_id,experiment_id,operation_id,owner'
   ||typeof scope.owner!=='string'||scope.owner.length<1||scope.owner.length>200||/[\x00-\x1f\x7f\uD800-\uDFFF]/u.test(scope.owner)
   ||['effect_id','experiment_id','operation_id'].some(key=>typeof scope[key]!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$/.test(scope[key]))
   ||!/^sha256:[0-9a-f]{64}$/.test(descriptor.image_digest))
   throw Error('method_generated_resource_invalid');
  const m=descriptor.manifest,root='assets/manim/'+m?.resource_id+'/';
  if(m?.schema!=='video.manim-frame-resource.v1'||!Number.isInteger(m.frame_count)||m.frame_count<1||m.frame_count>60
   ||!/^[a-z][a-z0-9_-]{0,63}$/.test(m.resource_id)||/^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(m.resource_id)
   ||m.fps!==24||!Number.isInteger(m.global_start_frame)||m.global_start_frame<0
   ||m.global_start_frame+m.frame_count>request.constraints.duration_seconds*24
   ||!Number.isInteger(m.width)||m.width<320||m.width>1280||!Number.isInteger(m.height)||m.height<180||m.height>720
   ||!Array.isArray(m.frames)||m.frames.length!==m.frame_count||descriptor.manifest_path!==root+'manifest.json'
   ||typeof m.transparent!=='boolean'||m.audio!==null||m.runtime?.library!=='manim-community'
   ||m.runtime.version!=='0.22.1'||m.runtime.renderer!=='cairo'||m.runtime.latex_available!==false
   ||!Number.isInteger(m.runtime.seed)||m.runtime.seed<0||m.runtime.seed>=2**32
   ||!/^([0-9a-f]{64})$/.test(m.source_sha256)||!/^([0-9a-f]{64})$/.test(m.request_sha256)
   ||m.decoded_bytes!==m.width*m.height*m.frame_count*4
   ||m.limits?.max_resource_bytes!==25165824||m.limits.max_decoded_bytes!==134217728
   ||m.runtime?.image_digest!==descriptor.image_digest)throw Error('method_generated_resource_manifest_invalid');
  decoded+=m.width*m.height*m.frame_count*4;
  if(decoded>128*1024*1024)throw Error('method_generated_resource_budget_exceeded');
  const manifest=resources.get('/'+descriptor.manifest_path);
  if(!manifest||hash(manifest)!==descriptor.manifest_sha256||!isDeepStrictEqual(JSON.parse(manifest),m))
   throw Error('method_generated_resource_manifest_changed');
  const expected=new Map(Object.entries(MANIM_HELPERS));
  expected.set(descriptor.manifest_path.slice(7),descriptor.manifest_sha256);
  for(let index=0;index<m.frame_count;index++){
   const row=m.frames[index],path=root+'frame_'+String(index).padStart(6,'0')+'.png';
   const png=resources.get('/'+path);
   if(row.index!==index||row.global_frame!==m.global_start_frame+index||row.path!==path
    ||!png||png.length<33||png.length!==row.bytes||hash(png)!==row.sha256||png.length>4*1024*1024
    ||png.subarray(0,8).toString('hex')!=='89504e470d0a1a0a'
    ||png.readUInt32BE(16)!==m.width||png.readUInt32BE(20)!==m.height||png[25]!==6)
    throw Error('method_generated_resource_frame_changed');
   expected.set(path.slice(7),row.sha256);
  }
  if(!isDeepStrictEqual(Object.fromEntries(expected),descriptor.assets_sha256))
   throw Error('method_generated_resource_inventory_invalid');
  for(const [name,checksum] of expected){
   const raw=resources.get('/assets/'+name);
   if(names.has(name)||!raw||hash(raw)!==checksum||request.assets_sha256[name]!==checksum)
    throw Error('method_generated_resource_asset_changed');
   names.add(name);total+=raw.length;
  }
 }
 if(total>25*1024*1024)throw Error('method_generated_resource_budget_exceeded');
 return names;
}

export function validateSoundEvents(events,duration){
 if(!finite(duration)||duration<1||duration>60||!Array.isArray(events)||events.length>128)
  throw Error('method_sound_budget_invalid');
 for(const event of events){
  if(!event||Array.isArray(event)||Object.keys(event).sort().join(',')!=='duration,frequency,gain,time,waveform')
   throw Error('method_sound_event_invalid');
  const {time,duration:seconds,frequency,gain,waveform}=event;
  if(!finite(time)||time<0||!finite(seconds)||seconds<.01||seconds>5||time+seconds>duration
   ||!finite(frequency)||frequency<40||frequency>16000||!finite(gain)||gain<0||gain>.8
   ||!['sine','triangle','noise'].includes(waveform))throw Error('method_sound_event_invalid');
 }
 return events;
}

export function synthesizeSoundEvents(events,duration,sampleRate=48000){
 validateSoundEvents(events,duration);
 if(sampleRate!==48000)throw Error('method_sound_sample_rate_invalid');
 const samples=Math.round(duration*sampleRate),signal=new Float64Array(samples);
 for(let index=0;index<events.length;index++){
  const event=events[index],start=Math.round(event.time*sampleRate),count=Math.round(event.duration*sampleRate);
  let seed=(index+1)*2654435761>>>0;
  for(let i=0;i<count&&start+i<samples;i++){
   const phase=i*event.frequency/sampleRate;
   let value;
   if(event.waveform==='noise'){seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;value=(seed>>>0)/2147483648-1;}
   else if(event.waveform==='triangle')value=1-4*Math.abs(Math.round(phase)-phase);
   else value=Math.sin(2*Math.PI*phase);
   // A short deterministic envelope avoids click artifacts without changing
   // the event's absolute start or silently adding/shortening the video.
   const envelope=Math.min(1,i/Math.max(1,sampleRate*.005),(count-1-i)/Math.max(1,sampleRate*.015));
   signal[start+i]+=value*event.gain*Math.max(0,envelope);
  }
 }
 const pcm=Buffer.alloc(samples*2);
 for(let i=0;i<samples;i++)pcm.writeInt16LE(Math.round(Math.max(-1,Math.min(1,signal[i]))*32767),i*2);
 const wav=Buffer.alloc(44+pcm.length);
 wav.write('RIFF',0);wav.writeUInt32LE(36+pcm.length,4);wav.write('WAVEfmt ',8);
 wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);
 wav.writeUInt32LE(sampleRate,24);wav.writeUInt32LE(sampleRate*2,28);
 wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);
 wav.writeUInt32LE(pcm.length,40);pcm.copy(wav,44);
 return wav;
}

// Only trusted callers supply the browser and measured engine-runtime module.
// Scene code receives neither, and never chooses file paths or observations.
export async function captureMethodFrames({chromium,request,resources,root,engineRuntime=null,
 executablePath='/opt/browser/headless_shell',hyperFramesDist='/opt/renderer/node_modules/hyperframes/dist'}){
 const resourceAware=request.schema==='video.animation-method-render-request.v5';
 const authoringAware=request.schema==='video.animation-method-render-request.v4'||(resourceAware&&!!request.authoring_libraries);
 const projectAware=resourceAware||authoringAware||request.schema==='video.animation-method-render-request.v3';
 const authoringLibraries=authoringAware?validateAuthoringLibraries(request,resources):null;
 if(resourceAware)validateGeneratedResources(request,resources);
 const c=request.constraints,count=c.duration_seconds*24,extended=projectAware||request.schema==='video.animation-method-render-request.v2';
 const engine=extended?request.engine:'html-svg';
 let projectRuntime,coreRuntime,project;
 if(projectAware){
  const adjacent=new URL('./hyperframes_project.mjs',import.meta.url);
  projectRuntime=await import(existsSync(fileURLToPath(adjacent))?adjacent:new URL('./motion_renderer/hyperframes_project.mjs',import.meta.url));
  const sourceFiles=Object.fromEntries([...resources].filter(([name])=>!name.startsWith('/assets/')).map(([name,bytes])=>[name.slice(1),bytes]));
  const assets=Object.fromEntries([...resources].filter(([name])=>name.startsWith('/assets/')).map(([name,bytes])=>[name.slice(1),bytes]));
  project=projectRuntime.buildHyperFramesProject(request.project,sourceFiles,assets).project;
  if(project.duration_seconds!==c.duration_seconds||project.fps!==c.fps||project.width!==c.width||project.height!==c.height
   ||project.scenes.length!==1||project.scenes[0].engine!==engine||typeof request.legacy_bridge!=='boolean')
   throw Error('method_hyperframes_project_mismatch');
  coreRuntime=projectRuntime.hyperFramesRuntimeArtifact(hyperFramesDist);
 }
 if(!Number.isInteger(count)||count<24||count>1440)throw Error('method_frame_budget_invalid');
 if(extended&&(!['html-svg','canvas','three'].includes(engine)||!engineRuntime))throw Error('method_engine_not_admitted');
 const mime={'.html':'text/html;charset=utf-8','.js':'text/javascript;charset=utf-8','.mjs':'text/javascript;charset=utf-8',
  '.css':'text/css;charset=utf-8','.ttc':'font/collection','.png':'image/png','.json':'application/json'};
 const server=createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost'),bytes=resources.get(url.pathname);
  res.setHeader('Content-Security-Policy',"default-src 'none';script-src 'self' 'unsafe-inline';style-src 'self' 'unsafe-inline';img-src 'self' data:;font-src 'self';connect-src 'none';media-src 'none';worker-src 'none';frame-src 'none';object-src 'none';base-uri 'none';form-action 'none'");
  res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');
  if(req.method!=='GET'||url.search||!bytes){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',mime[extname(url.pathname)]||'application/octet-stream');res.end(bytes);
 });
 let browser;
 await new Promise((done,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',done);});
 try{
  const origin='http://127.0.0.1:'+server.address().port;
  browser=await chromium.launch({headless:true,executablePath,
   args:extended?engineRuntime.browserLaunchArgs(engine):
    ['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage','--disable-gpu','--disable-background-networking']});
  const context=await browser.newContext({viewport:{width:c.width,height:c.height},
   deviceScaleFactor:1,serviceWorkers:'block',acceptDownloads:false});
  if(extended)await engineRuntime.installEngineGuard(context,engine);
  const blocked=[],errors=[],scripts=new Set(),loadedPNG=new Set();
  await context.route('**/*',async route=>{
   const url=new URL(route.request().url());
   if(url.origin===origin&&resources.has(url.pathname)&&!url.search&&route.request().method()==='GET'){
    if(route.request().resourceType()==='script')scripts.add(url.pathname);
    if(url.pathname.endsWith('.png'))loadedPNG.add(url.pathname);
    await route.continue();
   }
   else{if(blocked.length<32)blocked.push(url.protocol+'//'+url.hostname);await route.abort();}
  });
  if(context.routeWebSocket)await context.routeWebSocket('**/*',socket=>{
   if(blocked.length<32)blocked.push('websocket');socket.close();
  });
  const page=await context.newPage();page.setDefaultTimeout(10000);
  const security=await context.newCDPSession(page);
  await security.send('Log.enable');
  security.on('Log.entryAdded',({entry})=>{
   if(entry.source==='security'&&entry.level==='error'&&errors.length<32)
    errors.push('method_security_policy_violation');
  });
  page.on('pageerror',error=>{if(errors.length<32)errors.push(String(error).slice(0,1000));});
  page.on('crash',()=>{if(errors.length<32)errors.push('method_page_crashed');});
  page.on('popup',popup=>{if(errors.length<32)errors.push('method_popup_forbidden');popup.close();});
  page.on('dialog',dialog=>dialog.dismiss());
  const ready=async()=>{
   if(projectAware){
    await projectRuntime.installHyperFramesProject(page,{project,runtimePath:coreRuntime.runtimePath,
     legacyBridge:request.legacy_bridge});return;
   }
   await page.evaluate(async()=>{
    await document.fonts.ready;await window.__animationExperiment?.ready;
    if(typeof window.__animationExperiment?.seek!=='function')throw Error('method_seek_missing');
   });
  };
  await page.goto(origin+'/'+(projectAware?project.entry:'scene.html'),{waitUntil:'networkidle',timeout:20000});
  await ready();
  if(resourceAware&&Object.keys(MANIM_HELPERS).some(name=>!scripts.has('/assets/'+name)))
   throw Error('method_generated_resource_helper_not_loaded');
  if(engine==='three'&&!scripts.has('/assets/three.module.min.js'))throw Error('method_three_module_not_loaded');
  if(authoringAware&&scripts.has('/assets/anime.esm.min.js')!==scripts.has('/assets/anime_timeline.mjs'))
   throw Error('method_authoring_library_adapter_not_loaded');
  let checkRuntime,engineObservation;
  if(extended){
   checkRuntime=async()=>{engineObservation=await engineRuntime.verifyPageEngine(page,engine);};
  }else{
   const cdp=await context.newCDPSession(page),tree=await cdp.send('Page.getFrameTree');
   const world=await cdp.send('Page.createIsolatedWorld',{frameId:tree.frameTree.frame.id,worldName:'trusted-method-runtime'});
   checkRuntime=async()=>{
    const result=await cdp.send('Runtime.evaluate',{contextId:world.executionContextId,returnByValue:true,
     expression:"document.querySelectorAll('canvas,audio,video,iframe,object,embed').length"});
    if(result.exceptionDetails||result.result.value!==0)throw Error('method_engine_not_admitted');
   };
  }
  mkdirSync(join(root,'frames'));
  const frames=[];let evidenceBytes=0;
  const capture=async(time,name,{seek=true}={})=>{
   if(seek){
    if(projectAware)await projectRuntime.seekHyperFramesProject(page,time);
    else await page.evaluate(async seconds=>{await window.__animationExperiment.seek(seconds);},time);
   }
   await checkRuntime();
   if(projectAware)await page.evaluate(()=>{
    if(document.getAnimations().some(animation=>animation.playState==='running'))
     throw Error('method_autonomous_animation');
    // Rebuild SVG layout before painting just as the formal creative host
    // does. This changes no Agent nodes/attributes and keeps exact forward,
    // reverse, repeat and reload frame comparisons mandatory.
    const root=document.documentElement;
    const prior=root.style.getPropertyValue('display'),priority=root.style.getPropertyPriority('display');
    root.style.setProperty('display','none','important');root.getBoundingClientRect();
    if(prior)root.style.setProperty('display',prior,priority);else root.style.removeProperty('display');
    root.getBoundingClientRect();
   });
   const bytes=await page.screenshot({type:'jpeg',quality:80,timeout:10000});
   evidenceBytes+=bytes.length;
   if(evidenceBytes>request.max_evidence_bytes||bytes.length>4*1024*1024)throw Error('method_evidence_budget_exceeded');
   writeFileSync(join(root,'frames',name),bytes);
   if(errors.length||blocked.length)throw Error('method_scene_failed_or_blocked');
   return {name,timestamp_seconds:time,sha256:hash(bytes),bytes:bytes.length};
  };
  for(let i=0;i<count;i++)frames.push(await capture(i/24,'frame-'+String(i).padStart(6,'0')+'.jpg'));
  const reverse=await capture(0,'reverse-initial.jpg');
  frames.push(reverse);
  if(reverse.sha256!==frames[0].sha256)throw Error('method_reverse_seek_mismatch');
  let seekChecks;
  if(extended){
   const middle=Math.floor((count-1)/2),last=count-1,probes=[];
   const probe=async(index,name,kind,options)=>{
    const actual=await capture(index/24,name,options),reference=frames[index];
    if(actual.sha256!==reference.sha256||actual.bytes!==reference.bytes)throw Error('method_absolute_seek_mismatch');
    probes.push({...actual,reference_name:reference.name,kind});
   };
   await probe(middle,'seek-middle.jpg','absolute');
   await probe(last,'seek-final.jpg','absolute');
   await probe(middle,'seek-middle-reverse.jpg','reverse');
   await probe(middle,'seek-middle-repeat.jpg','repeat');
   await new Promise(done=>setTimeout(done,50));
   await probe(middle,'seek-middle-idle.jpg','idle',{seek:false});
   await probe(0,'seek-initial-repeat.jpg','repeat');
   await page.reload({waitUntil:'networkidle',timeout:20000});await ready();
   await probe(middle,'seek-middle-reload.jpg','reload');
   await probe(0,'seek-initial-reload.jpg','reload');
   seekChecks={schema:'video.animation-engine-seek-checks.v1',absolute_seek:true,
    non_monotonic_seek_verified:true,repeated_seek_verified:true,idle_frame_verified:true,
    fresh_load_verified:true,probes};
  }
  return {frames,frame_count:count,reverse_seek_verified:true,
   ...(resourceAware?{generated_resource_observation:{schema:'video.manim-method-resource-observation.v1',
    scope:'loaded_resources_not_visual_acceptance',helpers_loaded:true,
    resources_sha256:request.generated_resources.map(item=>item.sha256),loaded_png_paths:[...loadedPNG].sort()}}:{}),
   ...(authoringAware?{authoring_library_observation:{schema:'video.animation-authoring-library-observation.v1',
    scope:'loaded_scripts_not_api_calls_or_visual_application',
    authoring_libraries:authoringLibraries,loaded_scripts:[...scripts].sort(),
    used_libraries:scripts.has('/assets/anime_timeline.mjs')?['animejs']:[]}}:{}),
   ...(extended?{engine_observation:engineObservation,seek_checks:seekChecks}:{}),
   ...(projectAware?{project_runtime:{...(await page.evaluate(()=>({...window.__trustedHyperFramesProject.state}))),
    schema:'video.hyperframes-project-runtime-proof.v1',
    project_id:project.project_id,project_sha256:request.project_sha256,
    version:coreRuntime.version,core_sha256:coreRuntime.core_sha256,
    seek_protocol:'hyperframes-core-renderSeek-exact',legacy_bridge:request.legacy_bridge} }:{} )};
 }finally{
  if(browser)await browser.close().catch(()=>{});
  await new Promise(done=>server.close(done));
 }
}

async function main(){
 const root='/work',input='/input';
 const receipt={schema:'video.animation-method-render-receipt.v1',status:'starting'};
 const read=(path,max)=>{const st=lstatSync(path);if(!st.isFile()||st.isSymbolicLink()||st.size>max)throw Error('method_file_invalid');return readFileSync(path);};
 const run=(command,args)=>{
  const result=spawnSync(command,args,{timeout:180000,maxBuffer:2*1024*1024,encoding:'utf8'});
  if(result.error||result.status!==0)throw Error('method_media_command_failed');
  return result.stdout;
 };
 try{
  const raw=read(join(input,'request.json'),256*1024),request=JSON.parse(raw),c=request.constraints;
  // The trusted adapter authenticates the exact UTF-8 request bytes through
  // this environment value. Do not reserialize parsed floats here: Python and
  // JavaScript legitimately use different spellings for 1.0 and small values.
  const resourceAware=request.schema==='video.animation-method-render-request.v5';
  const authoringAware=request.schema==='video.animation-method-render-request.v4'||(resourceAware&&!!request.authoring_libraries);
  const projectAware=resourceAware||authoringAware||request.schema==='video.animation-method-render-request.v3';
  const extended=projectAware||request.schema==='video.animation-method-render-request.v2';
  if((!extended&&request.schema!=='video.animation-method-render-request.v1')
   ||(extended&&!['html-svg','canvas','three'].includes(request.engine))
   ||(!extended&&(request.engine!==undefined||request.engine_runtime_sha256!==undefined))
   ||!/^([0-9a-f]{64})$/.test(request.identity)
   ||!/^([0-9a-f]{64})$/.test(request.sound_events_sha256)
   ||hash(raw)!==process.env.ANIMATION_METHOD_REQUEST_SHA256
   ||request.image_digest!==process.env.ANIMATION_METHOD_IMAGE_DIGEST
   ||hash(read(fileURLToPath(import.meta.url),1024*1024))!==request.host_sha256)
   throw Error('method_request_binding_invalid');
  let engineRuntime=null,spec=null;
  if(extended){
   if(!/^[0-9a-f]{64}$/.test(request.engine_runtime_sha256)
    ||hash(read(fileURLToPath(new URL('./engine_runtime.mjs',import.meta.url)),1024*1024))!==request.engine_runtime_sha256)
    throw Error('method_engine_runtime_changed');
   engineRuntime=await import('./engine_runtime.mjs');spec=engineRuntime.engineSpec(request.engine);
   Object.assign(receipt,{schema:'video.animation-method-render-receipt.v2',engine:request.engine,
    engine_runtime_sha256:request.engine_runtime_sha256});
   if(request.engine==='three'&&request.assets_sha256['three.module.min.js']!==spec.three_sha256)
    throw Error('method_three_asset_not_pinned');
   for(const [name,expected] of Object.entries({'three.module.min.js':engineRuntime.THREE_ASSET_SHA256,
    'three.LICENSE.txt':engineRuntime.THREE_LICENSE_SHA256})){
    if(request.assets_sha256[name]!==undefined&&request.assets_sha256[name]!==expected)
     throw Error('method_three_asset_not_pinned');
   }
  }
  if(projectAware){
   if(!/^[0-9a-f]{64}$/.test(request.project_runtime_sha256||'')
    ||hash(read(fileURLToPath(new URL('./hyperframes_project.mjs',import.meta.url)),1024*1024))!==request.project_runtime_sha256)
    throw Error('method_hyperframes_runtime_changed');
   Object.assign(receipt,{schema:'video.animation-method-render-receipt.'+(resourceAware?'v5':authoringAware?'v4':'v3'),
    project_runtime_sha256:request.project_runtime_sha256});
  }
  if(c.fps!==24||!Number.isInteger(c.width)||c.width<320||c.width>1280
   ||!Number.isInteger(c.height)||c.height<180||c.height>720
   ||!finite(c.duration_seconds)||c.duration_seconds<1||c.duration_seconds>60
   ||!Number.isInteger(c.duration_seconds*24))throw Error('method_render_budget_invalid');
  validateSoundEvents(request.sound_events,c.duration_seconds);
  const count=c.duration_seconds*24;
  if(count>request.max_frames||request.max_frames!==1440)throw Error('method_frame_budget_invalid');
  const {collectRuntime}=await import('./runtime.mjs');
  Object.assign(receipt,{request_identity:request.identity,request_sha256:hash(raw),
   image_digest:request.image_digest,host_sha256:request.host_sha256,runtime:collectRuntime()});
  const allowed=new Map();
  for(const [name,expected] of Object.entries(request.files_sha256)){
   if(!['scene.html','scene.js','scene.css'].includes(name))throw Error('method_scene_file_invalid');
   const bytes=read(join(input,'scene',name),512*1024);
   if(hash(bytes)!==expected)throw Error('method_scene_changed');
   allowed.set('/'+name,bytes);
  }
  const generatedNames=resourceAware?new Set(request.generated_resources.flatMap(item=>Object.keys(item.assets_sha256))):new Set();
  for(const [name,expected] of Object.entries(request.assets_sha256)){
   if(typeof name!=='string'||!name||name.split('/').some(part=>!part||part==='.'||part==='..')||name.includes('\\'))
    throw Error('method_asset_path_invalid');
   if(!['gsap.min.js','NotoSansCJK-Regular.ttc',
    ...(extended?['three.module.min.js','three.LICENSE.txt']:[]),
    ...(authoringAware?Object.keys(ANIME_ASSETS):[])].includes(name)&&!generatedNames.has(name))throw Error('method_asset_not_admitted');
   const bytes=read(join(input,'scene/assets',name),32*1024*1024);
   if(hash(bytes)!==expected)throw Error('method_asset_changed');
   allowed.set('/assets/'+name,bytes);
  }
  if(resourceAware)validateGeneratedResources(request,allowed);
  const {chromium}=createRequire('/opt/renderer/package.json')('playwright-core');
  const captured=await captureMethodFrames({chromium,request,resources:allowed,root,engineRuntime});
  const video=request.sound_events.length?'video.mp4':'output.mp4';
  run('ffmpeg',['-v','error','-nostdin','-y','-framerate','24','-i',join(root,'frames/frame-%06d.jpg'),
   '-frames:v',String(count),'-c:v','libx264','-preset','veryfast','-crf','23',
   '-pix_fmt','yuv420p','-movflags','+faststart',join(root,video)]);
  let audioSha=null;
  if(request.sound_events.length){
   const wav=synthesizeSoundEvents(request.sound_events,c.duration_seconds);
   writeFileSync(join(root,'sound-effects.wav'),wav);audioSha=hash(wav);
   run('ffmpeg',['-v','error','-nostdin','-y','-i',join(root,video),'-i',join(root,'sound-effects.wav'),
    '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','128k',
    '-t',String(c.duration_seconds),'-movflags','+faststart',join(root,'output.mp4')]);
   unlinkSync(join(root,video));
  }
  const bytes=read(join(root,'output.mp4'),request.max_media_bytes);
  const probe=JSON.parse(run('ffprobe',['-v','error','-show_streams','-show_format','-of','json',join(root,'output.mp4')]));
  const videos=probe.streams.filter(s=>s.codec_type==='video'),audios=probe.streams.filter(s=>s.codec_type==='audio');
  if(videos.length!==1||audios.length!==(audioSha?1:0)||probe.streams.length!==(audioSha?2:1)
   ||videos[0].width!==c.width||videos[0].height!==c.height
   ||Math.abs(Number(probe.format.duration)-c.duration_seconds)>1/24+.001)throw Error('method_media_probe_invalid');
  run('ffmpeg',['-v','error','-nostdin','-i',join(root,'output.mp4'),'-f','null','-']);
  Object.assign(receipt,{status:'rendered',...captured,
   output_sha256:hash(bytes),output_bytes:bytes.length,duration_seconds:c.duration_seconds,
   audio_sha256:audioSha,sound_events_sha256:request.sound_events_sha256,
   reverse_seek_verified:true,decode_verified:true,probe,
   whole_video_watched:false,audio_observed:false,expressive_quality:'not_evaluated'});
 }catch(error){receipt.status='failed';receipt.error=String(error).slice(0,2000);process.exitCode=1;}
 finally{
  writeFileSync(join(root,'render-receipt.json'),JSON.stringify(receipt,null,2));
 }
}

if(process.argv[1]===fileURLToPath(import.meta.url))await main();
