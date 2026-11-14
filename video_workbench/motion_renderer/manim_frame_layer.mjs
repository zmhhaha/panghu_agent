/* A resource consumer, not a drawing template or an animation clock. */
const schema='video.manim-frame-resource.v1',sha=/^[0-9a-f]{64}$/;
function require(value,code='manim_frame_resource_invalid'){if(!value)throw Error(code);}
export function validateManimManifest(value){
 require(value?.schema===schema&&/^[a-z][a-z0-9_-]{0,63}$/.test(value.resource_id)
  &&!/^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(value.resource_id)
  &&value.fps===24&&Number.isInteger(value.frame_count)&&value.frame_count>=1&&value.frame_count<=240
  &&Number.isInteger(value.global_start_frame)&&value.global_start_frame>=0
  &&Number.isInteger(value.width)&&value.width>=320&&value.width<=1280
  &&Number.isInteger(value.height)&&value.height>=180&&value.height<=720
  &&typeof value.transparent==='boolean'&&value.runtime?.library==='manim-community'
  &&value.runtime.version==='0.22.1'&&value.runtime.renderer==='cairo'&&value.audio===null
  &&sha.test(value.request_sha256)&&sha.test(value.source_sha256)
  &&value.decoded_bytes===value.width*value.height*value.frame_count*4&&value.decoded_bytes<=134217728
  &&value.limits?.max_resource_bytes===25165824&&value.limits.max_decoded_bytes===134217728
  &&Array.isArray(value.frames)&&value.frames.length===value.frame_count);
 let total=0;
 for(let i=0;i<value.frames.length;i++){
  const row=value.frames[i];
  require(row.index===i&&row.global_frame===value.global_start_frame+i
   &&row.path===`assets/manim/${value.resource_id}/frame_${String(i).padStart(6,'0')}.png`
   &&sha.test(row.sha256)&&Number.isInteger(row.bytes)&&row.bytes>0&&row.bytes<=4194304);
  total+=row.bytes;
 }
 require(total<=25165824);
 return structuredClone(value);
}
export function manimFrameIndex(manifest,globalFrame){
 require(Number.isInteger(globalFrame)&&globalFrame>=0,'manim_frame_clock_invalid');
 const index=globalFrame-manifest.global_start_frame;
 // Half-open interval: a module decides any final hold explicitly; the adapter
 // never clamps another scene to a stale last frame or invents a timing stretch.
 return index<0||index>=manifest.frame_count?null:index;
}
export function createManimFrameLayer(root,value,{assetURL=path=>'/project/'+path,createImage=()=>new Image()}={}){
 const manifest=validateManimManifest(value);
 require(root&&typeof root.append==='function'&&typeof assetURL==='function');
 const image=createImage();
 // One live image only, decoded on demand. Never preload a full sequence. The
 // independent decoded-byte budget also bounds aggregate browser cache pressure.
 image.alt='';image.width=manifest.width;image.height=manifest.height;
 image.style.cssText='display:none;width:100%;height:100%;object-fit:contain';
 root.append(image);
 let disposed=false,index=null,busy=false;
 return {
  async renderAt(context){
   require(!disposed,'manim_frame_layer_disposed');
   require(!busy,'manim_frame_concurrent_render');
   busy=true;
   try{
   const requested=manimFrameIndex(manifest,context?.global_frame);
   image.style.display='none';
   if(requested===null){index=null;return;}
   if(index!==requested){
    const path=manifest.frames[requested].path,url=assetURL(path);
    require(url==='/project/'+path,
     'manim_frame_asset_url_invalid');
    index=null;
    image.src=url;
    // The HyperFrames host awaits renderAt; do not return an undecoded image.
    await image.decode();
    require(!disposed,'manim_frame_layer_disposed');
    index=requested;
   }
   image.style.display='block';
   }finally{busy=false;}
  },
  dispose(){disposed=true;index=null;image.removeAttribute('src');image.remove();},
 };
}
