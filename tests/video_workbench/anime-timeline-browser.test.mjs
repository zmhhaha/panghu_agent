// Opt-in real-browser compatibility probe; no installed renderer admission.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFileSync,mkdtempSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
const enabled=process.env.PANGHU_ANIME_PLAYWRIGHT&&process.env.PANGHU_ANIME_BROWSER&&process.env.PANGHU_TEST_TEMP;
const sha=raw=>createHash('sha256').update(raw).digest('hex');
const vendor=new URL('../../video_workbench/motion_renderer/vendor/',import.meta.url);
const resources=new Map(['anime.esm.min.js','anime_timeline.mjs','three.module.min.js'].map(name=>[
  '/assets/'+name,readFileSync(new URL(name,vendor))]));
resources.set('/guard.mjs',readFileSync(new URL('../../video_workbench/motion_renderer/hyperframes_film_realm_guard.mjs',import.meta.url)));
const source=`import {installFilmRealmGuard} from '/guard.mjs';
const kind=location.hash.slice(1), check=installFilmRealmGuard(kind);
const {createFrameTimeline,svg}=await import('/assets/anime_timeline.mjs');
document.body.style.cssText='margin:0;background:#101629';
let canvas,draw,release=()=>{};
const state={x:0};
const timeline=createFrameTimeline({fps:24,durationFrames:48});
timeline.add(state,{x:[0,100],duration:2000});
if(kind==='html-svg'){
 document.body.innerHTML='<svg width="640" height="360" viewBox="0 0 640 360"><path id="path" d="M 50 220 Q 300 30 570 210" fill="none" stroke="#50d4d0" stroke-width="12"/><circle id="dot" cx="50" cy="270" r="18" fill="#ffbb66"/></svg>';
 const path=document.querySelector('#path'),dot=document.querySelector('#dot');
 timeline.add(svg.createDrawable(path),{draw:['0 0','0 1'],duration:2000},0);
 draw=()=>dot.setAttribute('cx',50+state.x*5);
}else{
 canvas=document.createElement('canvas');canvas.width=640;canvas.height=360;document.body.append(canvas);
 if(kind==='canvas'){
  const ctx=canvas.getContext('2d');draw=()=>{ctx.fillStyle='#101629';ctx.fillRect(0,0,640,360);ctx.fillStyle='#50d4d0';ctx.fillRect(30+state.x*4,120,80,80);};
 }else{
  const THREE=await import('/assets/three.module.min.js');
  const renderer=new THREE.WebGLRenderer({canvas,antialias:false,preserveDrawingBuffer:true});renderer.setSize(640,360);
  const scene=new THREE.Scene();scene.background=new THREE.Color('#101629');
  const camera=new THREE.PerspectiveCamera(45,640/360,.1,100);camera.position.z=6;
  const geometry=new THREE.BoxGeometry(1.6,1.6,1.6),material=new THREE.MeshNormalMaterial();
  const mesh=new THREE.Mesh(geometry,material);scene.add(mesh);
  draw=()=>{mesh.rotation.y=state.x/30;mesh.position.x=(state.x-50)/80;renderer.render(scene,camera);};
  release=()=>{geometry.dispose();material.dispose();renderer.dispose();};
 }
}
window.probe={seek(frame){timeline.renderAt({global_frame:frame,time:{numerator:frame,denominator:24}});draw();return check();},
 dispose(){timeline.dispose();release();document.body.replaceChildren();return true;}};
window.probe.seek(0);`;
resources.set('/probe.mjs',Buffer.from(source));
resources.set('/index.html',Buffer.from('<!doctype html><meta charset="utf-8"><script type="module" src="/probe.mjs"></script>'));

test('real Chrome: pinned Anime.js + actual film guard, SVG path, Canvas and Three.js', {skip:!enabled}, async()=>{
  const {chromium}=await import(pathToFileURL(process.env.PANGHU_ANIME_PLAYWRIGHT).href);
  const profile=mkdtempSync(join(process.env.PANGHU_TEST_TEMP,'anime-browser-'));
  const server=createServer((req,res)=>{
    const raw=resources.get(req.url);
    if(!raw){res.writeHead(404);res.end();return;}
    res.setHeader('Content-Type',req.url.endsWith('.html')?'text/html':'text/javascript');
    res.setHeader('X-Content-Type-Options','nosniff');res.end(raw);
  });
  await new Promise(done=>server.listen(0,'127.0.0.1',done));
  const origin='http://127.0.0.1:'+server.address().port;
  let context;
  try{
    context=await chromium.launchPersistentContext(profile,{executablePath:process.env.PANGHU_ANIME_BROWSER,
      headless:true,viewport:{width:640,height:360},args:['--disable-background-networking',
        '--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
    await context.addInitScript(()=>Object.defineProperty(Navigator.prototype,'serviceWorker',{value:undefined}));
    for(const kind of ['html-svg','canvas','three']){
      const page=await context.newPage(),errors=[],blocked=[];
      page.on('pageerror',error=>errors.push(error.message));
      await page.route('**/*',route=>{
        if(route.request().url().startsWith(origin+'/'))return route.continue();
        blocked.push(route.request().url());return route.abort();
      });
      await page.goto(origin+'/index.html#'+kind);
      await page.waitForFunction(()=>!!window.probe,null,{polling:20});
      const hashes=new Map();
      for(const frame of [0,12,36,6,47,0,36,36]){
        const evidence=await page.evaluate(frame=>window.probe.seek(frame),frame);
        assert.equal(evidence.engine,kind);assert.equal(evidence.pending_animation_frames,0);
        const image=await page.screenshot();
        if(hashes.has(frame))assert.equal(sha(image),hashes.get(frame),kind+' reverse/repeat');
        hashes.set(frame,sha(image));
        await page.waitForTimeout(60);
        assert.equal(sha(await page.screenshot()),sha(image),kind+' idle');
      }
      assert.notEqual(hashes.get(0),hashes.get(36),kind+' genuinely changes pixels');
      await page.reload();await page.waitForFunction(()=>!!window.probe,null,{polling:20});
      await page.evaluate(()=>window.probe.seek(36));
      assert.equal(sha(await page.screenshot()),hashes.get(36),kind+' fresh reconstruction');
      assert.equal(await page.evaluate(()=>window.probe.dispose()),true);
      assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);await page.close();
    }
  } finally {
    if(context)await context.close();await new Promise(done=>server.close(done));
    rmSync(profile,{recursive:true,force:true});
  }
});
