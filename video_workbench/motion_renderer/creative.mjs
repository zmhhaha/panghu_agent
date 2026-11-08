// Runs only inside the credential-free, deny-all production renderer Job.
// Node owns paths, evidence and FFmpeg; the page receives no Node capability.
import {createServer} from 'node:http';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright-core';
import {readRegular,sha,encodedDuration} from './contract.mjs';

export async function captureCreative({root='/work',mode='render',quality='draft'}={}){
 const plan=JSON.parse(readRegular(root+'/plan.json'));
 const timeline=JSON.parse(readRegular(root+'/timeline.json'));
 if(plan.schema!=='video.creative-scene.v1'||plan.engine!=='html-svg'
  ||timeline.compiler!=='creative-scene-adapter-1')throw Error('creative_contract_mismatch');
 const files=plan.result?.files||{},allowed=['scene.html','scene.js','scene.css'];
 if(!files['scene.html']||Object.keys(files).some(name=>!allowed.includes(name)))throw Error('creative_files');
 const count=Math.round(encodedDuration(timeline)*24);
 if(count<1||count>1440)throw Error('creative_frame_limit');
 const resources=new Map(Object.entries(files).map(([name,value])=>['/'+name,Buffer.from(value)]));
 for(const name of ['gsap.min.js','NotoSansCJK-Regular.ttc']){
  const raw=readRegular('/opt/assets/'+name,32*1024*1024);
  const expected=plan.input_document.assets?.[name];
  if(expected&&sha(raw)!==expected)throw Error('creative_asset_digest_mismatch');
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
 const errors=[],blocked=[];
 await new Promise((done,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',done);});
 const origin='http://127.0.0.1:'+server.address().port;
 try{
  browser=await chromium.launch({headless:true,executablePath:process.env.HYPERFRAMES_BROWSER_PATH,
   args:['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage','--disable-gpu']});
  const context=await browser.newContext({viewport:{width:1280,height:720},deviceScaleFactor:1,
   serviceWorkers:'block',acceptDownloads:false});
  await context.addInitScript(()=>{
   Object.defineProperty(HTMLCanvasElement.prototype,'getContext',{
    value(){throw Error('creative_engine_not_admitted');},configurable:false,writable:false});
   Object.defineProperty(window,'OffscreenCanvas',{value:undefined,configurable:false,writable:false});
  });
  await context.route('**/*',route=>{
   const u=new URL(route.request().url());
   if(u.origin===origin&&resources.has(u.pathname)&&!u.search&&route.request().method()==='GET')return route.continue();
   if(blocked.length<32)blocked.push('external_or_undeclared_resource');return route.abort();
  });
  const page=await context.newPage();
  page.on('pageerror',()=>{if(errors.length<32)errors.push('page_error');});
  page.on('crash',()=>{if(errors.length<32)errors.push('page_crashed');});
  page.on('popup',p=>{errors.push('popup_not_allowed');p.close();});
  page.on('dialog',dialog=>dialog.dismiss());
  await page.goto(origin+'/scene.html',{waitUntil:'networkidle',timeout:20000});
  await page.evaluate(async()=>{
   await document.fonts.ready;
   if(typeof window.__animationExperiment?.seek!=='function')throw Error('creative_seek_missing');
   if(document.querySelector('canvas,iframe,object,embed,video,audio'))throw Error('creative_engine_not_admitted');
  });
  const capture=async(time,path)=>{
   const local=Math.max(0,Math.min(timeline.audio_seconds,time-timeline.leading_seconds));
   await page.evaluate(async t=>{
    await window.__animationExperiment.seek(t);
    if(document.querySelector('canvas,iframe,object,embed,video,audio'))throw Error('creative_engine_not_admitted');
   },local);
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
   if(bytes.length>2*1024*1024)throw Error('creative_frame_too_large');
   writeFileSync(root+'/'+path,bytes);
   if(errors.length||blocked.length)throw Error('creative_page_or_resource_error');
   return {time,local_seconds:local,frame_sha256:sha(bytes),bytes:bytes.length,path};
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
   const probes=[];
   for(let i=0;i<times.length;i++)probes.push((await capture(times[i],'creative-frames/probe-'+i+'.jpg')).frame_sha256);
   return {schema:'video.creative-capture.v1',renderer:'isolated-chromium-absolute-seek-ffmpeg-1',
    frame_count:count,first_sha256:samples[0].frame_sha256,
    last_sha256:samples.at(-1).frame_sha256,reverse_initial_sha256:reverse.frame_sha256,
    probe_sha256:probes,blocked_requests:blocked,page_errors:errors};
  }
  mkdirSync(root+'/observations',{recursive:true});
  for(let i=0;i<times.length;i++)samples.push(await capture(times[i],'observations/creative-'+i+'.jpg'));
  if(samples[0].frame_sha256!==samples.at(-1).frame_sha256)throw Error('creative_reverse_seek_mismatch');
  const result={schema:'video.creative-observations.v1',renderer:'isolated-chromium-absolute-seek-ffmpeg-1',
   input_sha256:plan.input_sha256,source_sha256:plan.result_sha256,frame_count:count,
   samples,blocked_requests:blocked,page_errors:errors,visual_quality:'not_evaluated'};
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
