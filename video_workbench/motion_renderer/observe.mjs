import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { resolve, extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readRegular } from './contract.mjs';
const sha=raw=>createHash('sha256').update(raw).digest('hex');
export const observationSeekConfig=Object.freeze({mode:'preview-phase',diagnostics:false,step:1/120,
 offsetFraction:.5,fps:24,fpsSource:'render-options',owner:'runtime'});
export async function screenshotObservation(page,path){
 return await page.screenshot({path,type:'jpeg',quality:75});
}

// An additional bounded, non-monotonic pass is required only by the new
// process-continuity compiler. Old observation contracts and sampling stay
// byte-for-byte structurally compatible with their existing consumers.
export function processContinuitySeekTimes(timeline){
 const last=timeline.duration_seconds-1/24;
 const middles=timeline.actions.map(row=>row.start+row.duration/2);
 const count=Math.min(24,middles.length),selected=[];
 for(let i=0;i<count;i++)selected.push(middles[Math.floor(i*middles.length/count)]);
 return [last,0,...selected.reverse(),Math.min(.375,last),Math.min(.6,last),last,0];
}

export async function seekPageExact(page,time){
 return await page.evaluate(async value=>{
  const player=window.__player;
  if(!player||typeof player.renderSeek!=='function'||typeof player.getTime!=='function')throw Error('native_player_missing');
  player.renderSeek(value,{exact:true});
  if(typeof window.__hfWaitForSeekCompletion==='function')await window.__hfWaitForSeekCompletion();
  return player.getTime();
 },time);
}

export function runtimeArtifact(dist){
 const manifest=JSON.parse(readRegular(join(dist,'hyperframe.manifest.json')));
 const name=manifest.artifacts?.iife || 'hyperframe.runtime.iife.js';
 if(!/^[A-Za-z0-9_.-]+\.js$/.test(name))throw Error('runtime_name');
 const path=join(dist,name);
 if(sha(readRegular(path,16*1024*1024))!==manifest.sha256)throw Error('runtime_digest_mismatch');
 return path;
}

// Keep the adapter injectable so tests can check Playwright's actual argument
// shapes without installing a second browser/dependency on the development PC.
export async function withObservationPage(chromium,{url,executablePath,runtimePath},collect){
 let browser;
 try{
  browser=await chromium.launch({headless:true,executablePath,
   args:['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage','--disable-gpu']});
  const context=await browser.newContext({viewport:{width:1280,height:720},deviceScaleFactor:1});
  const page=await context.newPage();
  await page.goto(url,{waitUntil:'networkidle',timeout:30000});
  await page.evaluate(async()=>{await document.fonts.ready;await window.__videoMotion.ready;});
  // The standalone core does not include producer's engine-protocol bridge.
  // Configure the same render FPS before bootstrap, then use its native player.
  await page.evaluate(config=>{window.__HF_EXPORT_RENDER_SEEK_CONFIG=config;},observationSeekConfig);
  await page.addScriptTag({path:runtimePath});
  // Timer polling also works if a runtime replaces or parks requestAnimationFrame.
  await page.waitForFunction(()=>window.__playerReady===true&&window.__renderReady===true
   &&typeof window.__player?.renderSeek==='function'&&typeof window.__player?.getTime==='function'
   &&typeof window.__player?.getDuration==='function'&&window.__player.getDuration()>0,
   null,{timeout:30000,polling:100});
  return await collect(page);
 }finally{if(browser)await browser.close();}
}

export async function observeProject(chromium,{root='/work',dist='/opt/renderer/node_modules/hyperframes/dist'}={}){
 const timeline=JSON.parse(readRegular(join(root,'timeline.json')));
 // This is the same IIFE published by build-runtime.ts for real rendering;
 // resolve its manifest filename and verify its checksum before any execution.
 const runtimePath=runtimeArtifact(dist);
 const mime={'.html':'text/html','.js':'text/javascript','.ttc':'font/collection'};
 const server=createServer((req,res)=>{try{
  const p=resolve(root,'.'+decodeURIComponent(new URL(req.url,'http://localhost').pathname));
  if(!p.startsWith(root+'/'))throw Error('path');
  const relative=p.slice(root.length+1);
  if(!['index.html','assets/gsap.min.js','assets/NotoSansCJK-Regular.ttc'].includes(relative))throw Error('path');
  res.setHeader('Content-Type',mime[extname(p)]||'application/octet-stream');res.end(readRegular(p,32*1024*1024));
 }catch{res.statusCode=404;res.end();}});
 await new Promise((done,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',done);});
 try{
  return await withObservationPage(chromium,{url:`http://127.0.0.1:${server.address().port}/index.html`,
   executablePath:process.env.HYPERFRAMES_BROWSER_PATH,runtimePath},async page=>{
   mkdirSync(root+'/observations',{recursive:true});
   const capture=async(time,label)=>{
    const actual_time=await seekPageExact(page,time);
    const state=await page.evaluate(processContinuity=>({elements:window.__videoMotion.inspect(),relations:window.__videoMotion.inspectRelations(),layout:window.__videoMotion.inspectLayout(),
     ...(processContinuity?{continuity:window.__videoMotion.inspectContinuity()}:{} )}),timeline.compiler==='motion-process-continuity-compiler-1');
    // Screenshot waits for browser capture; no custom rAF-based wait can hang
    // this observer when the paused runtime owns the page's animation clock.
    const raw=await screenshotObservation(page,root+'/observations/'+label+'.jpg');
    return {time,actual_time,...state,frame_sha256:sha(raw)};
   };
   const windows=[];
   for(const action of timeline.actions){
    const before=Math.max(0,action.start-1/24),middle=action.start+action.duration/2,after=Math.min(timeline.duration_seconds-1/24,action.start+action.duration+1/24);
    windows.push({ordinal:action.ordinal,before:await capture(before,`a${action.ordinal}-before`),middle:await capture(middle,`a${action.ordinal}-middle`),after:await capture(after,`a${action.ordinal}-after`)});
   }
   const first=await capture(0,'seek-first'),last=await capture(timeline.duration_seconds-1/24,'seek-last'),repeat=await capture(0,'seek-repeat');
   let continuityBoundary;
   if(['motion-continuity-compiler-1','motion-continuity-compiler-2','motion-process-continuity-compiler-1'].includes(timeline.compiler)){
    const plan=JSON.parse(readRegular(join(root,'plan.json')));
    continuityBoundary={initial:first,final:last,repeated_initial:repeat,
     ...(plan.segment.index?{transition_middle:await capture(.375,'continuity-transition-middle'),
       transition_end:await capture(.6,'continuity-transition-end')}:{})};
   }
   let seekSequence;
   if(timeline.compiler==='motion-process-continuity-compiler-1'){
    const samples=[];
    for(const [index,time] of processContinuitySeekTimes(timeline).entries())samples.push(await capture(time,`continuity-sequence-${index}`));
    seekSequence={schema:'video.process-continuity-seek-sequence.v1',samples};
   }
   const state=x=>JSON.stringify([x.elements,x.relations,x.layout]);
   writeFileSync(root+'/observations.json',JSON.stringify({schema:'video.motion-observations.v1',
    seek_protocol:'hyperframes-core-renderSeek-exact',render_fps:24,
    timeline_sha256:sha(readRegular(root+'/timeline.json')),windows,
    ...(continuityBoundary?{continuity_boundary:continuityBoundary}:{}),
    ...(seekSequence?{seek_sequence:seekSequence}:{}),
    ...(['motion-compiler-4','motion-compiler-5','motion-compiler-6','motion-process-compiler-2'].includes(timeline.compiler)?{initial:first,repeated_initial:repeat}:{}),
    reverse_seek:{first_sha256:first.frame_sha256,last_sha256:last.frame_sha256,repeated_first_sha256:repeat.frame_sha256,repeat_state_equal:state(first)===state(repeat)}},null,2));
  });
 }finally{await new Promise(done=>server.close(done));}
}

if(process.argv[1]===fileURLToPath(import.meta.url)){
 // Direct, exactly pinned dependency; do not rely on a transitive Puppeteer
 // package's private on-disk layout, which changed in the installed release.
 const {chromium}=await import('playwright-core');
 await observeProject(chromium);
}
