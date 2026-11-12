// Shared trusted host support. These guards measure native contexts; author
// declarations and screenshots alone cannot establish which engine executed.
export const ENGINE_RUNTIME_REVISION='animation-engine-runtime-1';
export const THREE_VERSION='0.170.0';
export const THREE_ASSET_SHA256='08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7';
export const THREE_LICENSE_SHA256='4c40a1ef62450b857c3b2aaf294936304cd552d965fbcd9d32d4c5bcf4ba4454';
export function engineSpec(engine){
 if(!['html-svg','canvas','three'].includes(engine))throw Error('creative_engine_not_admitted');
 return {engine,revision:ENGINE_RUNTIME_REVISION,
  contexts:engine==='html-svg'?[]:engine==='canvas'?['2d']:['2d','webgl2'],
  asset_names:engine==='three'?['three.module.min.js']:[],
  ...(engine==='three'?{three:THREE_VERSION,three_sha256:THREE_ASSET_SHA256,
   three_license_sha256:THREE_LICENSE_SHA256}:{})};
}
export function browserLaunchArgs(engine){
 engineSpec(engine);
 return ['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage',
  '--disable-background-networking',...(engine==='three'
   ?['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']
   :['--disable-gpu'])];
}
export async function installEngineGuard(context,engine){
 const spec=engineSpec(engine);
 await context.addInitScript(({engine,contexts,revision})=>{
  const native=HTMLCanvasElement.prototype.getContext,seen=new Map();
  const boxOf=Element.prototype.getBoundingClientRect,styleOf=window.getComputedStyle;
  const widthOf=Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype,'width').get;
  const heightOf=Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype,'height').get;
  const connected=Object.getOwnPropertyDescriptor(Node.prototype,'isConnected').get;
  const webgl=typeof WebGL2RenderingContext==='function'?WebGL2RenderingContext.prototype:null;
  const parameter=webgl?.getParameter,extension=webgl?.getExtension;
  const isLost=webgl?.isContextLost,getError=webgl?.getError;
  Object.defineProperty(HTMLCanvasElement.prototype,'getContext',{
   value:function(kind,options){
    if(!contexts.includes(kind))throw Error('creative_engine_not_admitted');
    const value=native.call(this,kind,kind==='webgl2'?{...options,preserveDrawingBuffer:true}:options);
    if(value&&!seen.has(value)){
     if(seen.size>=16)throw Error('creative_context_budget');
     seen.set(value,{canvas:this,kind});
    }
    return value;
   },configurable:false,writable:false});
  Object.defineProperty(window,'OffscreenCanvas',{value:undefined,configurable:false,writable:false});
  if('transferControlToOffscreen' in HTMLCanvasElement.prototype)
   Object.defineProperty(HTMLCanvasElement.prototype,'transferControlToOffscreen',{
    value:function(){throw Error('creative_engine_not_admitted');},configurable:false,writable:false});
  for(const name of ['AudioContext','webkitAudioContext','OfflineAudioContext','webkitOfflineAudioContext']){
   Object.defineProperty(window,name,{value:function(){throw Error('creative_media_not_admitted');},
    configurable:false,writable:false});
  }
  Object.defineProperty(window,'__trustedAnimationEngineEvidence',{
   value:()=>({engine,revision,contexts:[...seen].map(([value,{canvas,kind}])=>{
    let renderer=null,contextLost=null,glError=null;
    if(kind==='webgl2'){
     const debug=extension.call(value,'WEBGL_debug_renderer_info');
     renderer=String(parameter.call(value,debug?debug.UNMASKED_RENDERER_WEBGL:0x1f01));
     contextLost=isLost.call(value);glError=getError.call(value);
    }
    const box=boxOf.call(canvas),style=styleOf.call(window,canvas),attached=connected.call(canvas);
    return {kind,width:widthOf.call(canvas),height:heightOf.call(canvas),connected:attached,
     visible:attached&&box.width>0&&box.height>0&&style.display!=='none'
      &&style.visibility!=='hidden'&&Number(style.opacity)>0,
     ...(renderer!==null?{renderer,software:/swiftshader/i.test(renderer),context_lost:contextLost,gl_error:glError}:{})};
   })}),configurable:false,writable:false});
 },spec);
}
const trustedWorlds=new WeakMap();
const watchedPages=new WeakSet();
async function checkPageDOM(page,engine){
 if(!watchedPages.has(page)){
  watchedPages.add(page);
  const invalidate=()=>{const prior=trustedWorlds.get(page);trustedWorlds.delete(page);
   if(prior)prior.cdp.detach().catch(()=>{});};
  page.on('framenavigated',frame=>{if(frame===page.mainFrame())invalidate();});
  page.on('close',invalidate);
 }
 let world=trustedWorlds.get(page);
 if(!world){
  const cdp=await page.context().newCDPSession(page),tree=await cdp.send('Page.getFrameTree');
  const created=await cdp.send('Page.createIsolatedWorld',{frameId:tree.frameTree.frame.id,
   worldName:'trusted-animation-engine-observer'});
  world={cdp,id:created.executionContextId};trustedWorlds.set(page,world);
 }
 const {result,exceptionDetails}=await world.cdp.send('Runtime.evaluate',{contextId:world.id,
  returnByValue:true,expression:`(()=>({forbidden:!!document.querySelector(${JSON.stringify(engine==='html-svg'
   ?'canvas,iframe,object,embed,video,audio':'iframe,object,embed,video,audio')}),
   running:document.getAnimations().some(animation=>animation.playState==='running')}))()`});
 if(exceptionDetails||!result?.value)throw Error('creative_engine_dom_observation_failed');
 if(result.value.forbidden)throw Error('creative_engine_not_admitted');
 if(result.value.running)throw Error('creative_autonomous_animation');
}
export async function verifyPageEngine(page,engine,{requireContext=true}={}){
 engineSpec(engine);
 await checkPageDOM(page,engine);
 const evidence=await page.evaluate(()=>{
  if(typeof window.__trustedAnimationEngineEvidence!=='function')throw Error('creative_engine_guard_missing');
  return window.__trustedAnimationEngineEvidence();
 });
 if(evidence.engine!==engine||evidence.revision!==ENGINE_RUNTIME_REVISION)
  throw Error('creative_engine_guard_mismatch');
 const contexts=evidence.contexts;
 if(!Array.isArray(contexts)||contexts.length>16)throw Error('creative_context_budget');
 if(engine==='html-svg'&&contexts.length)throw Error('creative_engine_not_admitted');
 if(engine==='canvas'&&contexts.some(row=>row.kind!=='2d'))throw Error('creative_engine_not_admitted');
 if(engine==='three'&&contexts.some(row=>row.kind==='webgl2'&&(!row.software||row.context_lost||row.gl_error!==0)))
  throw Error('creative_webgl_software_required');
 const required=engine==='canvas'?'2d':'webgl2';
 if(requireContext&&engine!=='html-svg'&&!contexts.some(row=>row.kind===required&&row.visible
  &&row.width>0&&row.height>0&&row.width<=4096&&row.height<=4096))
  throw Error('creative_native_context_missing');
 return evidence;
}
