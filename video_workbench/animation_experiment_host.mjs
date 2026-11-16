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
// BEGIN TRUSTED LABEL OBSERVER
function observeAnimationObjects(expectedLabels){
 const revision='svg-label-geometry-v2',svg='http://www.w3.org/2000/svg';
 const labels=new Map(expectedLabels.map(row=>[row.id,row.label]));
 const boundsOf=rect=>({x:rect.x,y:rect.y,width:rect.width,height:rect.height});
 const positive=rect=>[rect.x,rect.y,rect.width,rect.height].every(Number.isFinite)&&rect.width>0&&rect.height>0;
 const inside=(small,large)=>small.x>=large.x-.5&&small.y>=large.y-.5
  &&small.x+small.width<=large.x+large.width+.5&&small.y+small.height<=large.y+large.height+.5;
 const shown=node=>{
  for(let p=node;p;p=p.parentElement){
   const s=getComputedStyle(p);
   if(s.display==='none'||['hidden','collapse'].includes(s.visibility)||Number(s.opacity)<=0
    ||s.contentVisibility==='hidden')return false;
  }
  return true;
 };
 const paint=value=>{
  value=String(value).trim().toLowerCase();
  if(!value||['none','transparent'].includes(value)||value.includes('url('))return false;
  const alpha=value.match(/(?:rgba|hsla)\([^)]*,\s*([\d.]+)\s*\)$/)
   ||value.match(/\/\s*([\d.]+)(%)?\s*\)$/);
  return !alpha||Number(alpha[1])>0;
 };
 const paintedText=node=>{
  if(!shown(node))return false;
  for(let p=node;p;p=p.parentElement){
   const s=getComputedStyle(p);
   // Bounded pilot labels use plain paint. Complex clipping, masks or filters
   // require a different verifier instead of claiming visible glyphs from a bbox.
   if((s.clipPath&&s.clipPath!=='none')||(s.maskImage&&s.maskImage!=='none')
    ||(s.filter&&s.filter!=='none'))return false;
  }
  const s=getComputedStyle(node);
  if(!(Number.parseFloat(s.fontSize)>0))return false;
  if(node.namespaceURI===svg)return (paint(s.fill)&&Number(s.fillOpacity)>0)
    ||(paint(s.stroke)&&Number(s.strokeOpacity)>0&&Number.parseFloat(s.strokeWidth)>0);
  return paint(s.color);
 };
 const renderedLabels=scope=>{
  const groups=new Map(),walker=document.createTreeWalker(scope,NodeFilter.SHOW_TEXT);
  for(let text=walker.nextNode();text;text=walker.nextNode()){
   const p=text.parentElement,raw=text.nodeValue||'',trimmed=raw.trim();
   if(!p||!trimmed||p.closest('script,style,title,desc,metadata,defs,symbol,clipPath,mask,marker,noscript,template'))continue;
   const owner=p.namespaceURI===svg?p.closest('text'):p;
   if(!owner||!scope.contains(owner)||!paintedText(p))continue;
   const start=raw.indexOf(trimmed),range=document.createRange();
   range.setStart(text,start);range.setEnd(text,start+trimmed.length);
   const rect=range.getBoundingClientRect();
   if(!positive(rect))continue;
   let clipped=false;
   for(let a=p;a;a=a.parentElement){
    const s=getComputedStyle(a);
    if((['hidden','clip','scroll','auto'].includes(s.overflowX)
       ||['hidden','clip','scroll','auto'].includes(s.overflowY))&&!inside(rect,a.getBoundingClientRect()))clipped=true;
   }
   if(clipped)continue;
   const row=groups.get(owner)||{text:'',rects:[]};row.text+=raw;row.rects.push(rect);groups.set(owner,row);
  }
  return [...groups.values()].map(row=>{
   const left=Math.min(...row.rects.map(r=>r.x)),top=Math.min(...row.rects.map(r=>r.y));
   return {text:row.text.trim(),bounds:{x:left,y:top,
    width:Math.max(...row.rects.map(r=>r.right))-left,height:Math.max(...row.rects.map(r=>r.bottom))-top}};
  });
 };
 return [...document.querySelectorAll('[data-object-id]')].map(node=>{
  const id=node.getAttribute('data-object-id'),rect=node.getBoundingClientRect(),bounds=boundsOf(rect);
  let scope=node,attachment='marked_object_descendants';
  if(node.namespaceURI===svg&&node.localName!=='g'){
   scope=node.parentElement?.closest('g')||node;attachment='nearest_svg_group';
  }
  const markers=[...(scope.matches('[data-object-id]')?[scope]:[]),...scope.querySelectorAll('[data-object-id]')];
  const candidates=renderedLabels(scope).map(label=>({...label,within_object:inside(label.bounds,bounds)}));
  const attached=markers.length===1&&markers[0]===node;
  const label=candidates.length===1?candidates[0]:null,expected=labels.get(id);
  const status=!attached?'attachment_ambiguous':candidates.length===0?'missing':candidates.length!==1?'ambiguous'
   :label.text!==expected?'wrong_text':!label.within_object?'outside_object':'measured';
  return {id,text:status==='measured'?label.text:'',visible:positive(rect)&&shown(node),bounds,
   label_observation:{verifier_revision:revision,status,expected_text:expected??null,attachment,
    object_marker_count:markers.length,candidates}};
 }).sort((a,b)=>a.id.localeCompare(b.id));
}
// END TRUSTED LABEL OBSERVER
// BEGIN TRUSTED SEMANTIC OBSERVER
function observeAnimationSemantics(){
 const svg='http://www.w3.org/2000/svg';
 const boundsOf=r=>({x:r.x,y:r.y,width:r.width,height:r.height});
 const positive=r=>[r.x,r.y,r.width,r.height].every(Number.isFinite)&&r.width>0&&r.height>0;
 const inside=(a,b)=>a.x>=b.x-.5&&a.y>=b.y-.5&&a.x+a.width<=b.x+b.width+.5&&a.y+a.height<=b.y+b.height+.5;
 const shown=node=>{
  for(let p=node;p;p=p.parentElement){const s=getComputedStyle(p);
   if(s.display==='none'||['hidden','collapse'].includes(s.visibility)||Number(s.opacity)<=0
    ||s.contentVisibility==='hidden'||(s.clipPath&&s.clipPath!=='none')
    ||(s.maskImage&&s.maskImage!=='none')||(s.filter&&s.filter!=='none'))return false;
  }return true;
 };
 const paint=value=>{
  value=String(value||'').trim().toLowerCase();
  if(!value||['none','transparent'].includes(value)||value.includes('url('))return false;
  const alpha=value.match(/(?:rgba|hsla)\([^)]*,\s*([\d.]+)\s*\)$/)||value.match(/\/\s*([\d.]+)(%)?\s*\)$/);
  return !alpha||Number(alpha[1])>0;
 };
 const painted=node=>{const s=getComputedStyle(node);
  const stroke=paint(s.stroke)&&Number(s.strokeOpacity)>0&&Number.parseFloat(s.strokeWidth)>0;
  return node.namespaceURI===svg?((node.localName!=='line'&&paint(s.fill)&&Number(s.fillOpacity)>0)
   ||stroke):paint(s.color);
 };
 const textOf=node=>{
  const parts=[],walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT);
  for(let text=walker.nextNode();text;text=walker.nextNode()){
   const p=text.parentElement,raw=text.nodeValue||'',trimmed=raw.trim();
   if(!p||!trimmed||p.closest('script,style,title,desc,metadata,defs,symbol,clipPath,mask,marker,noscript,template')
    ||!shown(p)||!painted(p)||!(Number.parseFloat(getComputedStyle(p).fontSize)>0))continue;
   const start=raw.indexOf(trimmed),range=document.createRange();range.setStart(text,start);range.setEnd(text,start+trimmed.length);
   const r=range.getBoundingClientRect();if(!positive(r))continue;
   let clipped=false;
   for(let a=p;a;a=a.parentElement){const s=getComputedStyle(a);
    if((['hidden','clip','scroll','auto'].includes(s.overflowX)||['hidden','clip','scroll','auto'].includes(s.overflowY))
     &&!inside(r,a.getBoundingClientRect()))clipped=true;
   }
   if(!clipped)parts.push(trimmed);
  }return parts.join(' ').trim();
 };
 const geometryOf=node=>{
  if(node.namespaceURI!==svg)return null;
  const matrix=node.getScreenCTM();if(!matrix)return null;
  const point=(x,y)=>{const p=new DOMPoint(x,y).matrixTransform(matrix);return [p.x,p.y];};
  if(['circle','ellipse'].includes(node.localName))return {kind:'point',point:point(node.cx.baseVal.value,node.cy.baseVal.value)};
  if(node.localName==='line')return {kind:'line',points:[point(node.x1.baseVal.value,node.y1.baseVal.value),point(node.x2.baseVal.value,node.y2.baseVal.value)]};
  if(node.localName==='polygon')return {kind:'polygon',points:[...node.points].map(p=>point(p.x,p.y))};
  return null;
 };
 return [...document.querySelectorAll('[data-semantic-id]')].slice(0,129).map(node=>{
  const rect=node.getBoundingClientRect(),text=textOf(node),geometry=geometryOf(node),displayed=shown(node);
  let entity=node;
  while(entity&&!String(entity.getAttribute('data-semantic-id')||'').startsWith('entity-'))entity=entity.parentElement;
  const rendered=displayed&&(!geometry||painted(node));
  return {id:node.getAttribute('data-semantic-id'),tag:node.localName,bounds:boundsOf(rect),
   rendered,visible:rendered&&positive(rect),text,text_status:text?'measured':'missing',
   entity_attachment:entity?.getAttribute('data-semantic-id')||null,geometry};
 }).sort((a,b)=>a.id.localeCompare(b.id));
}
// END TRUSTED SEMANTIC OBSERVER
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
   returnByValue:true,expression:`(${observeAnimationObjects.toString()})(${JSON.stringify((c.objects||[]).map(({id,label})=>({id,label})))})`});
  if(exceptionDetails||!Array.isArray(result.value))throw Error('host_observation_failed');
  return result.value;
 };
 const observeSemantics=async()=>{
  const {result,exceptionDetails}=await cdp.send('Runtime.evaluate',{contextId:isolated.executionContextId,
   returnByValue:true,expression:`(${observeAnimationSemantics.toString()})()`});
  if(exceptionDetails||!Array.isArray(result.value))throw Error('host_semantic_observation_failed');
  return result.value;
 };
 mkdirSync(join(root,'frames'));
 const frames=[],samples=[];let total=0;
 const capture=async(time,name)=>{
  await page.evaluate(async t=>{await window.__animationExperiment.seek(t);},time);
  const objects=await observe(),semantics=c.semantic?await observeSemantics():undefined;
  const bytes=await page.screenshot({type:'jpeg',quality:80,timeout:10000});
  total+=bytes.length;if(total>request.max_evidence_bytes||bytes.length>4*1024*1024)throw Error('host_evidence_budget');
  writeFileSync(join(root,'frames',name),bytes);frames.push({name,sha256:hash(bytes),bytes:bytes.length});
  return {time,objects,...(c.semantic?{semantics}:{}),frame_sha256:hash(bytes)};
 };
 for(let index=0;index<count;index++)samples.push({frame_index:index,...await capture(index/24,'frame-'+String(index).padStart(6,'0')+'.jpg')});
 // The page was at its final frame. Revisiting the first frame validates a
 // nonmonotonic seek against measured pixels and geometry, not a page claim.
 const reverse=await capture(0,'reverse-initial.jpg');
 const observations={schema:'video.animation-experiment-observations.v1',
  observer:'isolated_world_dom_and_browser_screenshots',request_identity:request.identity,
  input_sha256:request.input_sha256,samples,reverse_initial_sha256:reverse.frame_sha256,
  reverse_initial_objects:reverse.objects,blocked_requests:blocked,page_errors:errors};
 if(c.semantic)Object.assign(observations,{semantic_observer_revision:'visible-semantic-geometry-v1',reverse_initial_semantics:reverse.semantics});
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
