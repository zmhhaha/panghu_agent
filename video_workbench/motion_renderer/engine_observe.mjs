// Explicit developer engines only; production input admission is unchanged.
import {createServer} from 'node:http';
import {join, resolve, extname} from 'node:path';
import {mkdirSync, writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {readRegular} from './contract.mjs';
import {runtimeArtifact, observationSeekConfig, seekPageExact, screenshotObservation} from './observe.mjs';

const sha = raw => createHash('sha256').update(raw).digest('hex');
export async function observeEngineProject(chromium,{root,dist}) {
  root=resolve(root);
  const timeline=JSON.parse(readRegular(join(root,'timeline.json')));
  const manifest=JSON.parse(readRegular(join(root,'manifest.json')));
  if(!['canvas','three','svg-effects','scenario-effects'].includes(timeline.engine))throw Error('engine_not_supported');
  const assets=Object.keys(manifest.engine_assets||{});
  const allowed=new Set(['index.html','assets/gsap.min.js','assets/NotoSansCJK-Regular.ttc',...assets.map(name=>'assets/'+name)]);
  const server=createServer((req,res)=>{try{
    const p=resolve(root,'.'+decodeURIComponent(new URL(req.url,'http://localhost').pathname));
    if(!p.startsWith(root+'/')||!allowed.has(p.slice(root.length+1)))throw Error('path');
    res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.ttc':'font/collection'})[extname(p)]||'application/octet-stream');
    res.end(readRegular(p,32*1024*1024));
  }catch{res.statusCode=404;res.end();}});
  let browser;
  await new Promise((done,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',done);});
  try {
    const webgl=timeline.engine==='three'||(timeline.engine==='scenario-effects'&&timeline.rendering==='three');
    const canvas=timeline.engine==='canvas'||(timeline.engine==='scenario-effects'&&timeline.rendering==='canvas');
    const gpu=webgl?['--use-angle=swiftshader','--enable-unsafe-swiftshader']:['--disable-gpu'];
    browser=await chromium.launch({headless:true,executablePath:process.env.HYPERFRAMES_BROWSER_PATH,
      args:['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage',...gpu]});
    const context=await browser.newContext({viewport:{width:1280,height:720},deviceScaleFactor:1});
    const page=await context.newPage(), errors=[];
    page.on('pageerror',error=>errors.push(String(error.message).slice(0,300)));
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html`,{waitUntil:'networkidle',timeout:30000});
    await page.evaluate(async()=>{await document.fonts.ready;await window.__videoMotion.ready;});
    await page.evaluate(config=>{window.__HF_EXPORT_RENDER_SEEK_CONFIG=config;},observationSeekConfig);
    await page.addScriptTag({path:runtimeArtifact(dist)});
    await page.waitForFunction(()=>window.__playerReady===true&&window.__renderReady===true
      &&typeof window.__player?.renderSeek==='function'&&window.__player.getDuration()>0,null,{timeout:30000,polling:100});
    mkdirSync(join(root,'observations'));
    const capture=async(time,label)=>{
      const actual_time=await seekPageExact(page,time);
      const engine_state=await page.evaluate(()=>window.__videoMotion.snapshot());
      const raw=await screenshotObservation(page,join(root,'observations',label+'.jpg'));
      return {time,actual_time,engine_state,frame_sha256:sha(raw)};
    };
    const windows=[];
    for(const action of timeline.actions) {
      windows.push({ordinal:action.ordinal,
        before:await capture(Math.max(0,action.start-1/24),`a${action.ordinal}-before`),
        middle:await capture(action.start+action.duration/2,`a${action.ordinal}-middle`),
        after:await capture(Math.min(timeline.duration_seconds-1/24,action.start+action.duration+1/24),`a${action.ordinal}-after`)});
    }
    const initial=await capture(0,'seek-first');
    const last=await capture(timeline.duration_seconds-1/24,'seek-last');
    const repeated=await capture(0,'seek-repeat');
    if(errors.length)throw Error('engine_page_error:'+errors[0]);
    writeFileSync(join(root,'observations.json'),JSON.stringify({schema:'video.animation-lab-engine-observations.v1',
      seek_protocol:'hyperframes-core-renderSeek-exact',render_fps:24,
      timeline_sha256:sha(readRegular(join(root,'timeline.json'))),engine:timeline.engine,windows,
      initial,last,repeated_initial:repeated,
      reverse_seek:{first_sha256:initial.frame_sha256,last_sha256:last.frame_sha256,
        repeated_first_sha256:repeated.frame_sha256,
        repeat_state_equal:JSON.stringify(initial.engine_state)===JSON.stringify(repeated.engine_state)},
      rendering:webgl?'webgl_software_requested':canvas?'canvas_2d':timeline.rendering==='html'?'html_dom':'svg_dom',page_errors:errors},null,2));
  } finally {
    if(browser)await browser.close();
    await new Promise(done=>server.close(done));
  }
}
