import {createHyperFramesFilmHost,connectHyperFramesFilmCore} from './hyperframes_film.mjs';
import {createFilmRealmReplyReceiver} from './hyperframes_film_channel.mjs';
// Only this trusted parent realm owns core and the lifecycle controller. Agent
// modules live in opaque sandboxed documents and cannot read this global.
const [config,manifest]=await Promise.all(['/host/config.json','/project/manifest.json'].map(async path=>{
 const response=await fetch(path);if(!response.ok)throw Error('film_root_input_missing');return await response.json();
}));
const container=document.getElementById('film-root');
document.documentElement.style.cssText='margin:0;overflow:hidden;background:#fff';
document.body.style.cssText=`margin:0;width:${config.width}px;height:${config.height}px;overflow:hidden`;
container.style.cssText=`position:relative;width:${config.width}px;height:${config.height}px;overflow:hidden`;
document.body.setAttribute('data-composition-id','film-'+config.film_sha256.slice(0,24));
document.body.setAttribute('data-duration',String(manifest.clock.frame_count/24));
document.body.setAttribute('data-width',String(config.width));document.body.setAttribute('data-height',String(config.height));
window.__HF_EXPORT_RENDER_SEEK_CONFIG={mode:'preview-phase',diagnostics:false,step:1/120,
 offsetFraction:.5,fps:24,fpsSource:'render-options',owner:'runtime'};
const instances=new Map();let serial=0;
function timeout(operation,label,milliseconds=15000){
 let timer;return Promise.race([operation,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(label)),milliseconds);})])
  .finally(()=>clearTimeout(timer));
}
const host=createHyperFramesFilmHost({verifiedManifest:manifest,manifestSha256:config.project_manifest_sha256,
 trustedLoadModule(module){
  const instance={module,id:++serial,iframe:null,port:null,pending:new Map(),counter:0,observation:null,closed:false};
  instances.set(module.module_id,instance);
  const command=async(name,context)=>{
   if(instance.fatal)throw instance.fatal;
   await instance.ready;
   if(instance.fatal)throw instance.fatal;
   const id=++instance.counter;
   const operation=new Promise((resolve,reject)=>{instance.pending.set(id,{resolve,reject});
    instance.port.postMessage({id,command:name,context});});
   try{return await timeout(operation,'film_realm_operation_timeout');}
   finally{instance.pending.delete(id);}
  };
  return {async mount(root,context){
    if(root!==instance.iframe)throw Error('film_realm_root_mismatch');await command('mount',context);},
   async renderAt(context){const result=await command('render',context);instance.observation=result.observation;
    instance.effectiveOrigin=result.realm_origin;
    instance.context={global_frame:context.global_frame,local_frame:context.local_frame,phase:context.phase};},
   async dispose(){
    if(instance.closed)return;
    try{if(instance.port&&!instance.fatal)await command('dispose');}
    finally{instance.closed=true;instance.port?.close();for(const pending of instance.pending.values())pending.reject(Error('film_realm_closed'));
     instance.pending.clear();}
   }};
 },
 createRoot(module){
  const instance=instances.get(module.module_id),iframe=document.createElement('iframe');
  instance.iframe=iframe;iframe.setAttribute('sandbox','allow-scripts');
  iframe.style.cssText=`position:absolute;inset:0;width:${config.width}px;height:${config.height}px;border:0;background:transparent`;
  const token=crypto.randomUUID();
  instance.ready=timeout(new Promise((resolve,reject)=>{
   const onReady=event=>{
    if(event.source!==iframe.contentWindow||event.origin!=='null'||event.data?.type!=='film-realm-ready'||event.data.token!==token)return;
    window.removeEventListener('message',onReady);
    const channel=new MessageChannel();instance.port=channel.port1;
    const receive=createFilmRealmReplyReceiver({identity:module,pending:instance.pending,onConnected:resolve,
     onFatal(error){instance.fatal=error;reject(error);channel.port1.close();}});
    channel.port1.onmessage=event=>receive(event.data);
    channel.port1.start();iframe.contentWindow.postMessage({type:'film-realm-connect',token},'*',[channel.port2]);
   };
   instance.removeReady=()=>window.removeEventListener('message',onReady);window.addEventListener('message',onReady);
  }),'film_realm_ready_timeout').finally(()=>instance.removeReady());
  instance.ready.catch(()=>{});
  iframe.src='/realms/'+module.namespace+'/index.html#'+token;container.append(iframe);return iframe;
 },
 removeRoot(root,module){
  const instance=instances.get(module.module_id);instance?.removeReady?.();instance?.port?.close();
  root.remove();instances.delete(module.module_id);
 }});
await document.fonts.ready;
await new Promise((resolve,reject)=>{const script=document.createElement('script');script.src='/runtime/hyperframes.js';
 script.onload=resolve;script.onerror=()=>reject(Error('film_core_load_failed'));document.head.append(script);});
await timeout(new Promise(resolve=>{
 const check=()=>{if(window.__playerReady===true&&window.__renderReady===true)resolve();else setTimeout(check,10);};check();
}),'film_core_ready_timeout');
const clock=connectHyperFramesFilmCore({host,eventTarget:window,player:window.__player,
 waitForSeekCompletion:window.__hfWaitForSeekCompletion,forceReseek:window.__hfReseekGpu});
Object.defineProperty(window,'__trustedFilmBrowser',{configurable:false,writable:false,value:Object.freeze({
 inspect(){return {remaining_iframes:container.querySelectorAll('iframe').length,...host.getState()};},
 async seekFrame(frame){
  const result=await clock.seekFrame(frame);
  return {...result,realms:[...instances.values()].map(instance=>({module_id:instance.module.module_id,
   module_sha256:instance.module.module_sha256,instance_id:instance.id,sandbox:instance.iframe.getAttribute('sandbox'),
   realm_origin:instance.effectiveOrigin,context:instance.context,observation:instance.observation})),execution_scope:'technical_realm_lifecycle_not_formal_film_delivery'};
 },async close(){await clock.close();return {remaining_iframes:container.querySelectorAll('iframe').length,...host.getState()};}
})});
