// Dedicated opaque-realm guard. Capture observation intrinsics before import;
// Agent prototype changes cannot manufacture native-context rows.
export function installFilmRealmGuard(engine){
 const apply=Reflect.apply,define=Object.defineProperty,create=Object.create,number=Number,string=String;
 const test=RegExp.prototype.test,softwarePattern=/swiftshader/i,descriptor=Object.getOwnPropertyDescriptor;
 const nativeContext=HTMLCanvasElement.prototype.getContext,contexts=create(null);let count=0;
 const box=Element.prototype.getBoundingClientRect,style=window.getComputedStyle;
 const connected=descriptor(Node.prototype,'isConnected').get;
 const width=descriptor(HTMLCanvasElement.prototype,'width').get,height=descriptor(HTMLCanvasElement.prototype,'height').get;
 const rectWidth=descriptor(DOMRectReadOnly.prototype,'width').get,rectHeight=descriptor(DOMRectReadOnly.prototype,'height').get;
 const cssValue=CSSStyleDeclaration.prototype.getPropertyValue,playState=descriptor(Animation.prototype,'playState').get;
 const query=Document.prototype.querySelector,animations=Document.prototype.getAnimations;
 const gl=globalThis.WebGL2RenderingContext?.prototype;
 const parameter=gl?.getParameter,extension=gl?.getExtension,lost=gl?.isContextLost,error=gl?.getError;
 const raf=window.requestAnimationFrame,cancel=window.cancelAnimationFrame,pending=new Set();
 const setAdd=Set.prototype.add,setDelete=Set.prototype.delete,setSize=descriptor(Set.prototype,'size').get;
 const serviceWorkerAvailable=navigator.serviceWorker!==undefined;
 if(serviceWorkerAvailable)throw Error('film_realm_service_worker_exposed');
 const immutableProperty=(object,key,value)=>{const d=create(null);d.value=value;d.enumerable=true;d.configurable=false;d.writable=false;define(object,key,d);};
 immutableProperty(window,'requestAnimationFrame',callback=>{
  const id=apply(raf,window,[time=>{apply(setDelete,pending,[id]);callback(time);}]);apply(setAdd,pending,[id]);return id;
 });
 immutableProperty(window,'cancelAnimationFrame',id=>{apply(setDelete,pending,[id]);return apply(cancel,window,[id]);});
 immutableProperty(HTMLCanvasElement.prototype,'getContext',function(kind,options){
  if(!((engine==='canvas'&&kind==='2d')||(engine==='three'&&(kind==='2d'||kind==='webgl2'))))
   throw Error('film_realm_engine_not_admitted');
  const value=apply(nativeContext,this,[kind,kind==='webgl2'?{...options,preserveDrawingBuffer:true}:options]);
  if(value){
   let seen=false;for(let index=0;index<count;index++)if(contexts[index].value===value)seen=true;
   if(!seen){if(count>=16)throw Error('film_realm_context_budget');contexts[count++]={value,canvas:this,kind};}
  }
  return value;
 });
 for(const name of ['OffscreenCanvas','AudioContext','webkitAudioContext','OfflineAudioContext'])immutableProperty(window,name,undefined);
 if('transferControlToOffscreen' in HTMLCanvasElement.prototype)
  immutableProperty(HTMLCanvasElement.prototype,'transferControlToOffscreen',()=>{throw Error('film_realm_offscreen_not_admitted');});
 return ()=>{
  if(apply(query,document,['iframe,object,embed,video,audio'])||(engine==='html-svg'&&apply(query,document,['canvas'])))
   throw Error('film_realm_dom_not_admitted');
  const active=apply(animations,document,[]);
  for(let index=0;index<active.length;index++)if(apply(playState,active[index],[])==='running')throw Error('film_realm_autonomous_animation');
  const pendingCount=apply(setSize,pending,[]);if(pendingCount)throw Error('film_realm_pending_animation_frame');
  const rows=[];let requiredFound=false;
  for(let index=0;index<count;index++){
   const {value,canvas,kind}=contexts[index],bounds=apply(box,canvas,[]),css=apply(style,window,[canvas]);
   const attached=apply(connected,canvas,[]),w=apply(width,canvas,[]),h=apply(height,canvas,[]);
   const visible=attached&&apply(rectWidth,bounds,[])>0&&apply(rectHeight,bounds,[])>0
    &&apply(cssValue,css,['display'])!=='none'&&apply(cssValue,css,['visibility'])!=='hidden'
    &&number(apply(cssValue,css,['opacity']))>0;
   let native={};if(kind==='webgl2'){
    const debug=apply(extension,value,['WEBGL_debug_renderer_info']);
    const renderer=string(apply(parameter,value,[debug?0x9246:0x1f01]));
    native={renderer,software:apply(test,softwarePattern,[renderer]),context_lost:apply(lost,value,[]),gl_error:apply(error,value,[])};
    if(!native.software||native.context_lost||native.gl_error!==0)throw Error('film_realm_webgl_invalid');
   }
   immutableProperty(rows,string(index),{kind,width:w,height:h,connected:attached,visible,...native});
   if(kind===(engine==='canvas'?'2d':'webgl2')&&visible&&w>0&&h>0&&w<=4096&&h<=4096)requiredFound=true;
  }
  if(engine!=='html-svg'&&!requiredFound)throw Error('film_realm_native_context_missing');
  return {revision:'film-opaque-realm-guard-1',engine,service_worker_available:serviceWorkerAvailable,
   pending_animation_frames:pendingCount,contexts:rows};
 };
}
