// Runs only inside the credential-free, deny-all production renderer Job.
// Node owns paths, evidence and FFmpeg; the page receives no Node capability.
import {createServer} from 'node:http';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {readRegular,sha,encodedDuration} from './contract.mjs';
import {engineSpec,browserLaunchArgs,installEngineGuard,verifyPageEngine} from './engine_runtime.mjs';

export async function captureCreative({root='/work',mode='render',quality='draft',
 assetsRoot='/opt/assets',chromium:providedChromium,executablePath=process.env.HYPERFRAMES_BROWSER_PATH}={}){
 const chromium=providedChromium||(await import('playwright-core')).chromium;
 const plan=JSON.parse(readRegular(root+'/plan.json'));
 const timeline=JSON.parse(readRegular(root+'/timeline.json'));
 const skillCreative=plan.schema==='video.creative-scene.v5';
 const filmCreative=plan.schema==='video.creative-scene.v4'||skillCreative;
 const engineCreative=plan.schema==='video.creative-scene.v3'||filmCreative;
 const methodCreative=plan.schema==='video.creative-scene.v2'||engineCreative;
 const compiler=skillCreative?'creative-skill-film-scene-adapter-1':filmCreative?'creative-film-scene-adapter-1':engineCreative?'creative-method-engine-scene-adapter-1':methodCreative?'creative-method-scene-adapter-1':'creative-scene-adapter-1';
 const engine=plan.engine;
 if(!['video.creative-scene.v1','video.creative-scene.v2','video.creative-scene.v3','video.creative-scene.v4','video.creative-scene.v5'].includes(plan.schema)
  ||!(engineCreative?['html-svg','canvas','three']:['html-svg']).includes(engine)
  ||plan.adapter_revision!==compiler||timeline.compiler!==compiler)throw Error('creative_contract_mismatch');
 const specification=engineSpec(engine);
 const renderer=engineCreative?'isolated-chromium-engine-seek-ffmpeg-1':'isolated-chromium-absolute-seek-ffmpeg-1';
 const source=methodCreative?plan.result?.source:plan.result;
 const files=source?.files||{},allowed=['scene.html','scene.js','scene.css'];
 if(!files['scene.html']||Object.keys(files).some(name=>!allowed.includes(name)))throw Error('creative_files');
 const count=Math.round(encodedDuration(timeline)*24);
 if(count<1||count>1440)throw Error('creative_frame_limit');
 const resources=new Map(Object.entries(files).map(([name,value])=>['/'+name,Buffer.from(value)]));
 for(const name of ['gsap.min.js','NotoSansCJK-Regular.ttc',...specification.asset_names]){
  const raw=readRegular(assetsRoot+'/'+name,32*1024*1024);
  const expected=plan.input_document.assets?.[name];
  if(expected&&sha(raw)!==expected)throw Error('creative_asset_digest_mismatch');
  if(name==='three.module.min.js'&&(sha(raw)!==specification.three_sha256||expected!==specification.three_sha256))
   throw Error('creative_asset_digest_mismatch');
  resources.set('/assets/'+name,raw);
 }
 const mime=path=>path.endsWith('.html')?'text/html; charset=utf-8':path.endsWith('.js')?'text/javascript; charset=utf-8':path.endsWith('.css')?'text/css; charset=utf-8':'font/collection';
 const server=createServer((req,res)=>{
  const path=req.url;
  if(req.method!=='GET'||!resources.has(path)){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',mime(path));
  res.setHeader('Content-Security-Policy',"default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'none'; frame-src 'none'; worker-src 'none'; media-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'");
  res.end(resources.get(path));
 });
 let browser;
 const errors=[],blocked=[],loadedEngineAssets=new Set();
 await new Promise((done,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',done);});
 const origin='http://127.0.0.1:'+server.address().port;
 try{
  browser=await chromium.launch({headless:true,executablePath,
   args:browserLaunchArgs(engine)});
  const context=await browser.newContext({viewport:{width:1280,height:720},deviceScaleFactor:1,
   serviceWorkers:'block',acceptDownloads:false});
  await installEngineGuard(context,engine);
  await context.route('**/*',route=>{
   const u=new URL(route.request().url());
   if(u.origin===origin&&resources.has(u.pathname)&&!u.search&&route.request().method()==='GET'){
    if(u.pathname==='/assets/three.module.min.js'&&route.request().resourceType()==='script')
     loadedEngineAssets.add('three.module.min.js');
    return route.continue();
   }
   if(blocked.length<32)blocked.push('external_or_undeclared_resource');return route.abort();
  });
  const page=await context.newPage();
  page.on('pageerror',()=>{if(errors.length<32)errors.push('page_error');});
  page.on('console',message=>{if(message.type()==='error'&&/Content Security Policy|violates.*directive|Refused to/i.test(message.text())
   &&errors.length<32)errors.push('page_security_policy_error');});
  page.on('crash',()=>{if(errors.length<32)errors.push('page_crashed');});
  page.on('popup',p=>{errors.push('popup_not_allowed');p.close();});
  page.on('dialog',dialog=>dialog.dismiss());
  await page.goto(origin+'/scene.html',{waitUntil:'networkidle',timeout:20000});
  let readyTimer;
  try{await Promise.race([page.evaluate(async()=>{
    await document.fonts.ready;
    await window.__animationExperiment?.ready;
    if(typeof window.__animationExperiment?.seek!=='function')throw Error('creative_seek_missing');
   }),new Promise((_,reject)=>{readyTimer=setTimeout(()=>reject(Error('creative_ready_timeout')),20000);})]);
  }finally{clearTimeout(readyTimer);}
  await verifyPageEngine(page,engine,{requireContext:false});
  if(engine==='three'&&!loadedEngineAssets.has('three.module.min.js'))throw Error('creative_three_module_not_loaded');
  const capture=async(time,path)=>{
   const local=Math.max(0,Math.min(timeline.audio_seconds,time-timeline.leading_seconds));
   await page.evaluate(async t=>{
    await window.__animationExperiment.seek(t);
   },local);
   const engineObservation=await verifyPageEngine(page,engine);
   // Rebuild layout objects before painting to avoid observed SVG rounded-edge
   // raster drift between otherwise identical forward/reverse states.
   // This preserves source nodes/attributes and the strict frame-hash checks.
   await page.evaluate(()=>{
    if(document.getAnimations().some(animation=>animation.playState==='running'))
     throw Error('creative_autonomous_animation');
    const root=document.documentElement;
    const prior=root.style.getPropertyValue('display');
    const priority=root.style.getPropertyPriority('display');
    root.style.setProperty('display','none','important');
    root.getBoundingClientRect();
    if(prior)root.style.setProperty('display',prior,priority);else root.style.removeProperty('display');
    root.getBoundingClientRect();
   });
   const bytes=await page.screenshot({type:'jpeg',quality:quality==='final'?90:75,
    animations:'allow',timeout:15000});
   if(engineCreative&&local===0){
    await page.waitForTimeout(80);
    const idle=await page.screenshot({type:'jpeg',quality:quality==='final'?90:75,
     animations:'allow',timeout:15000});
    if(sha(idle)!==sha(bytes))throw Error('creative_autonomous_animation');
   }
   if(bytes.length>2*1024*1024)throw Error('creative_frame_too_large');
   writeFileSync(root+'/'+path,bytes);
   if(errors.length||blocked.length)throw Error('creative_page_or_resource_error');
   return {time,local_seconds:local,frame_sha256:sha(bytes),bytes:bytes.length,path,
    ...(engineCreative?{engine_observation:engineObservation}:{})};
  };
  const samples=[];
  const times=[0,...Array.from({length:17},(_,i)=>(count-1)/24*(i+1)/18),(count-1)/24,0];
  if(mode==='render'){
   mkdirSync(root+'/creative-frames',{recursive:true});let total=0;
   for(let i=0;i<count;i++){
    const row=await capture(i/24,'creative-frames/frame-'+String(i).padStart(6,'0')+'.jpg');
    total+=row.bytes;if(total>256*1024*1024)throw Error('creative_frames_budget');
    if(i===0||i===count-1)samples.push(row);
   }
   const reverse=await capture(0,'creative-frames/reverse-initial.jpg');
   if(reverse.frame_sha256!==samples[0].frame_sha256)throw Error('creative_reverse_seek_mismatch');
   const probes=[],engineProbes=[];
   for(let i=0;i<times.length;i++){
    const probe=await capture(times[i],'creative-frames/probe-'+i+'.jpg');probes.push(probe.frame_sha256);
    if(engineCreative)engineProbes.push(probe.engine_observation);
   }
   return {schema:engineCreative?'video.creative-capture.v2':'video.creative-capture.v1',renderer,
    frame_count:count,first_sha256:samples[0].frame_sha256,
    last_sha256:samples.at(-1).frame_sha256,reverse_initial_sha256:reverse.frame_sha256,
    probe_sha256:probes,blocked_requests:blocked,page_errors:errors,
    ...(engineCreative?{engine,probe_engine_observations:engineProbes,idle_clock_verified:true,
     loaded_engine_assets:[...loadedEngineAssets]}:{})};
  }
  mkdirSync(root+'/observations',{recursive:true});
  for(let i=0;i<times.length;i++)samples.push(await capture(times[i],'observations/creative-'+i+'.jpg'));
  if(samples[0].frame_sha256!==samples.at(-1).frame_sha256)throw Error('creative_reverse_seek_mismatch');
  const result={schema:engineCreative?'video.creative-observations.v2':'video.creative-observations.v1',renderer,
   input_sha256:plan.input_sha256,source_sha256:plan.result_sha256,frame_count:count,
   samples,blocked_requests:blocked,page_errors:errors,visual_quality:'not_evaluated',
   ...(engineCreative?{engine,idle_clock_verified:true,loaded_engine_assets:[...loadedEngineAssets]}:{})};
  writeFileSync(root+'/observations.json',JSON.stringify(result));return result;
 }finally{
  if(browser)await browser.close();
  await new Promise(done=>server.close(done));
 }
}

if(process.argv[1]===fileURLToPath(import.meta.url)){
 const mode=process.argv[2];
 if(!['render','observe'].includes(mode))throw Error('creative_mode_invalid');
 const result=await captureCreative({mode,quality:process.env.MOTION_QUALITY||'draft'});
 if(mode==='render')writeFileSync('/work/creative-render.json',JSON.stringify(result));
}
