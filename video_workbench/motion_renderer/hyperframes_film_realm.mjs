import {installFilmRealmGuard} from './hyperframes_film_realm_guard.mjs';
import {filmRealmMessage} from './hyperframes_film_channel.mjs';
const parse=JSON.parse.bind(JSON),clone=structuredClone,freeze=Object.freeze,keys=Object.keys,apply=Reflect.apply,
 define=Object.defineProperty;
const integer=Number.isInteger,string=String,slice=String.prototype.slice,then=Promise.prototype.then;
const config=parse(document.getElementById('film-module-config').textContent);
const root=document.getElementById('film-module-root');
document.documentElement.style.cssText='margin:0;width:100%;height:100%;background:transparent;overflow:hidden';
document.body.style.cssText='margin:0;width:100%;height:100%;background:transparent;overflow:hidden';
root.style.cssText=`position:relative;width:${config.width}px;height:${config.height}px;overflow:hidden`;
const observe=installFilmRealmGuard(config.engine);
const add=EventTarget.prototype.addEventListener,remove=EventTarget.prototype.removeEventListener;
const post=MessagePort.prototype.postMessage,start=MessagePort.prototype.start;
const token=location.hash.slice(1),parentWindow=window.parent;
const effectiveOrigin=window.origin;
const sendReady=window.parent.postMessage.bind(window.parent);
let established=false;
function immutable(value){
 const result=clone(value);
 const deep=item=>{if(item&&typeof item==='object'){const names=keys(item);for(let index=0;index<names.length;index++)deep(item[names[index]]);freeze(item);}};
 deep(result);return result;
}
// Only the verified release compiler can install this policy. It is set before
// any authored module (including direct or fragment helper imports) can run.
if(config.spatial_appearance){
 if(config.engine!=='three')throw Error('film_spatial_library_not_admitted');
 Object.defineProperty(globalThis,'__trustedSceneAppearancePolicy',{
  value:immutable(config.spatial_appearance.policy),writable:false,configurable:false});
}
let spatialObserver=null,surfaceObserver=null;
function init(event){
 if(established||event.source!==parentWindow||event.data?.type!=='film-realm-connect'
  ||event.data.token!==token||event.ports.length!==1)return;
 established=true;apply(remove,window,['message',init]);
 const port=event.ports[0],reply=(value)=>apply(post,port,[filmRealmMessage(config,value)]);
 let exports=null,mounted=false,disposed=false,chain=Promise.resolve();
 apply(add,port,['message',event=>{
  const request=event.data;
  chain=apply(then,chain,[async()=>{
   try{
    if(!integer(request?.id)||(request.command!=='mount'&&request.command!=='render'&&request.command!=='dispose'))throw Error('film_realm_command_invalid');
    let observation=null;
    if(request.command==='dispose'){
     if(exports&&!disposed){await exports.dispose();disposed=true;}
    }else{
     const context=immutable(request.context);
     if(disposed||context.module_id!==config.module_id||context.film_sha256!==config.film_sha256
      ||!integer(context.global_frame))throw Error('film_realm_context_invalid');
     if(request.command==='mount'){
      if(mounted)throw Error('film_realm_already_mounted');
      if(config.spatial_appearance){
       spatialObserver=await import('/project/assets/spatial_three_adapter.mjs');
       surfaceObserver=await import('/project/assets/scene_space_surface.mjs');
      }
      exports=await import('/project/'+config.entry);
      if(typeof exports.mount!=='function'||typeof exports.renderAt!=='function'||typeof exports.dispose!=='function')throw Error('film_realm_lifecycle_missing');
      await exports.mount(root,context);mounted=true;await document.fonts.ready;
     }else{
      if(!mounted)throw Error('film_realm_not_mounted');
      await exports.renderAt(context);await document.fonts.ready;observation=observe();
      if(config.spatial_appearance){
       define(observation,'spatial_appearance',{value:{library:config.spatial_appearance.library,
        material:surfaceObserver.appearanceObservation(),adapter:spatialObserver.threeAppearanceObservation(),
        scope:'actual_helper_operations_not_pixels_or_artistic_acceptance'},enumerable:true});
      }
     }
    }
    reply({id:request.id,ok:true,module_id:config.module_id,module_sha256:config.module_sha256,
     realm_origin:effectiveOrigin,observation});
   }catch(error){reply({id:request?.id,ok:false,error:apply(slice,string(error?.message||'film_realm_failed'),[0,240])});}
  }]);
 }]);
 apply(start,port,[]);reply({type:'connected',module_id:config.module_id,module_sha256:config.module_sha256});
}
apply(add,window,['message',init]);
sendReady({type:'film-realm-ready',token},'*');
