// Trusted isolated renderer host. Generated code only runs in the browser page;
// observations are measured in a separate JavaScript world, never self-reported.
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {readFileSync, writeFileSync, mkdirSync, lstatSync} from 'node:fs';
import {join, extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {collectRuntime} from './runtime.mjs';

const hash=raw=>createHash('sha256').update(raw).digest('hex');
const read=(path,limit)=>{const info=lstatSync(path);if(!info.isFile()||info.isSymbolicLink()||info.size>limit)throw Error('host_file_invalid');return readFileSync(path);};
const require=createRequire('/opt/renderer/package.json');
const {chromium}=require('playwright-core');
const root='/work',source='/input';
let browser,server;
const receipt={schema:'video.animation-experiment-render-receipt.v1',status:'starting'};
const persist=()=>writeFileSync(join(root,'render-receipt.json'),JSON.stringify(receipt,null,2));
const run=(command,args)=>{
 const result=spawnSync(command,args,{timeout:45000,encoding:'utf8',maxBuffer:2*1024*1024});
 if(result.error||result.status!==0)throw Error('host_'+command+'_failed');
 return result.stdout;
};
try{
 const raw=read(join(source,'request.json'),64*1024),request=JSON.parse(raw);
 if(hash(raw)!==process.env.ANIMATION_EXPERIMENT_REQUEST_SHA256
   ||request.schema!=='video.animation-experiment-render-request.v1'
   ||request.image_digest!==process.env.ANIMATION_EXPERIMENT_IMAGE_DIGEST
   ||request.host_sha256!==hash(read(fileURLToPath(import.meta.url),1024*1024)))throw Error('host_request_binding');
 const c=request.constraints,count=c.duration_seconds*c.fps;
 if(c.fps!==24||!Number.isInteger(count)||count<24||count>240||c.width>1280||c.height>720)throw Error('host_budget_invalid');
 Object.assign(receipt,{request_identity:request.identity,request_sha256:hash(raw),host_sha256:request.host_sha256,
  image_digest:request.image_digest,image_digest_source:'coordinator_pinned_not_self_measured',
  runtime:collectRuntime(),renderer:'native_seek_screenshots_ffmpeg_not_hyperframes_export'});
 persist();
 const allowed=new Map();
 for(const [name,digest]of Object.entries(request.files_sha256)){
  if(!['scene.html','scene.js','scene.css'].includes(name))throw Error('host_scene_file_invalid');
  const content=read(join(source,'scene',name),512*1024);if(hash(content)!==digest)throw Error('host_scene_digest');
  allowed.set('/'+name,content);
 }
 for(const [name,digest]of Object.entries(request.assets_sha256)){
  if(!['gsap.min.js','three.module.min.js','NotoSansCJK-Regular.ttc'].includes(name))throw Error('host_asset_invalid');
  const content=read(join(source,'scene','assets',name),32*1024*1024);if(hash(content)!==digest)throw Error('host_asset_digest');
  allowed.set('/assets/'+name,content);
 }
 const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.ttc':'font/collection'};
 server=createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost');
  const content=allowed.get(url.pathname);
  res.setHeader('Content-Security-Policy',"default-src 'none';script-src 'self' 'unsafe-inline';style-src 'self' 'unsafe-inline';img-src 'self' data:;font-src 'self';connect-src 'none';media-src 'none';worker-src 'none';object-src 'none';frame-src 'none';base-uri 'none';form-action 'none'");
  res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Cache-Control','no-store');
  if(req.method!=='GET'||!content||url.search){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',mime[extname(url.pathname)]||'application/octet-stream');res.end(content);
 });
 await new Promise((done,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',done);});
 const origin='http://127.0.0.1:'+server.address().port;
 browser=await chromium.launch({headless:true,executablePath:process.env.HYPERFRAMES_BROWSER_PATH||'/opt/browser/headless_shell',
  args:['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage','--disable-gpu','--disable-background-networking']});
 const context=await browser.newContext({viewport:{width:c.width,height:c.height},deviceScaleFactor:1,
  serviceWorkers:'block',acceptDownloads:false});
 const blocked=[],errors=[];
 await context.route('**/*',async route=>{const url=new URL(route.request().url());
  if(url.origin===origin&&allowed.has(url.pathname)&&!url.search&&route.request().method()==='GET')await route.continue();
  else{if(blocked.length<32)blocked.push(url.protocol+'//'+url.hostname);await route.abort();}});
 if(context.routeWebSocket)await context.routeWebSocket('**/*',socket=>socket.close());
 const page=await context.newPage();page.setDefaultTimeout(10000);
 page.on('pageerror',error=>{if(errors.length<32)errors.push(String(error).slice(0,1000));});
 page.on('popup',popup=>popup.close());
 await page.goto(origin+'/scene.html',{waitUntil:'networkidle',timeout:20000});
 await page.evaluate(async()=>{await document.fonts.ready;if(typeof window.__animationExperiment?.seek!=='function')throw Error('scene_seek_missing');});
 const cdp=await context.newCDPSession(page),tree=await cdp.send('Page.getFrameTree');
 const isolated=await cdp.send('Page.createIsolatedWorld',{frameId:tree.frameTree.frame.id,worldName:'trusted-animation-observer'});
 const observe=async()=>{
  const {result,exceptionDetails}=await cdp.send('Runtime.evaluate',{contextId:isolated.executionContextId,
   returnByValue:true,expression:`(()=>Array.from(document.querySelectorAll('[data-object-id]')).map(node=>{
    const rect=node.getBoundingClientRect();let visible=rect.width>0&&rect.height>0;
    for(let p=node;p;p=p.parentElement){const s=getComputedStyle(p);if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0)visible=false;}
    return {id:node.getAttribute('data-object-id'),text:(node.textContent||'').trim(),visible,
     bounds:{x:rect.x,y:rect.y,width:rect.width,height:rect.height}};
   }).sort((a,b)=>a.id.localeCompare(b.id)))()`});
  if(exceptionDetails||!Array.isArray(result.value))throw Error('host_observation_failed');
  return result.value;
 };
 mkdirSync(join(root,'frames'));
 const frames=[],samples=[];let total=0;
 const capture=async(time,name)=>{
  await page.evaluate(async t=>{await window.__animationExperiment.seek(t);},time);
  const objects=await observe();
  const bytes=await page.screenshot({type:'jpeg',quality:80,timeout:10000});
  total+=bytes.length;if(total>request.max_evidence_bytes||bytes.length>4*1024*1024)throw Error('host_evidence_budget');
  writeFileSync(join(root,'frames',name),bytes);frames.push({name,sha256:hash(bytes),bytes:bytes.length});
  return {time,objects,frame_sha256:hash(bytes)};
 };
 for(let index=0;index<count;index++)samples.push({frame_index:index,...await capture(index/24,'frame-'+String(index).padStart(6,'0')+'.jpg')});
 // The page was at its final frame. Revisiting the first frame validates a
 // nonmonotonic seek against measured pixels and geometry, not a page claim.
 const reverse=await capture(0,'reverse-initial.jpg');
 const observations={schema:'video.animation-experiment-observations.v1',
  observer:'isolated_world_dom_and_browser_screenshots',request_identity:request.identity,
  input_sha256:request.input_sha256,samples,reverse_initial_sha256:reverse.frame_sha256,
  reverse_initial_objects:reverse.objects,blocked_requests:blocked,page_errors:errors};
 const observed=Buffer.from(JSON.stringify(observations,null,2));
 writeFileSync(join(root,'observations.json'),observed);
 await browser.close();browser=null;
 if(errors.length||blocked.length)throw Error('host_scene_errors_or_external_requests');
 run('ffmpeg',['-v','error','-nostdin','-y','-framerate','24','-i',join(root,'frames','frame-%06d.jpg'),
  '-frames:v',String(count),'-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',join(root,'output.mp4')]);
 const media=read(join(root,'output.mp4'),request.max_media_bytes);
 const probe=JSON.parse(run('ffprobe',['-v','error','-show_streams','-show_format','-of','json',join(root,'output.mp4')]));
 const video=probe.streams.filter(stream=>stream.codec_type==='video');
 if(video.length!==1||probe.streams.length!==1||video[0].width!==c.width||video[0].height!==c.height
  ||Math.abs(Number(probe.format.duration)-c.duration_seconds)>1/24+.001)throw Error('host_encoded_probe_mismatch');
 run('ffmpeg',['-v','error','-nostdin','-i',join(root,'output.mp4'),'-f','null','-']);
 Object.assign(receipt,{status:'rendered',output_sha256:hash(media),output_bytes:media.length,
  observations_sha256:hash(observed),frames,frame_count:count,decode_verified:true,probe,
  output:'silent_native_frames_and_mp4',visual_quality:'not_evaluated',production_admitted:false});
}catch(error){receipt.status='failed';receipt.error=String(error).slice(0,2000);process.exitCode=1;}
finally{if(browser)await browser.close().catch(()=>{});if(server)await new Promise(done=>server.close(done));persist();}
