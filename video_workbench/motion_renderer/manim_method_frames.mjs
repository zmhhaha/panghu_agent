/* Resource-only compatibility with an admitted synchronous method seek host. */
import {validateManimManifest,manimFrameIndex} from './manim_frame_layer.mjs';
function require(value,code){if(!value)throw Error(code);}
export function prepareManimMethodFrames(root,value,{createImage=()=>new Image(),placementStartFrame}={}){
 const manifest=validateManimManifest(value),images=[];
 const first=placementStartFrame===undefined?manifest.global_start_frame:placementStartFrame;
 require(Number.isSafeInteger(first)&&first>=0&&Number.isSafeInteger(first+manifest.frame_count),
  'manim_frame_placement_invalid');
 require(manifest.frame_count<=60&&root&&typeof root.append==='function','manim_method_resource_invalid');
 let disposed=false,prepared=false,cursor=0,visible=null,rejectDisposed;
 const stopped=new Promise((_,reject)=>{rejectDisposed=reject;});
 const remove=image=>{image.removeAttribute('src');image.remove();};
 function dispose(){
  if(disposed)return;
  disposed=true;prepared=false;visible=null;
  for(const image of images)if(image)remove(image);
  rejectDisposed(Error('manim_method_resource_disposed'));
 }
 async function decodeNext(){
  while(cursor<manifest.frame_count){
   require(!disposed,'manim_method_resource_disposed');
   const index=cursor++,row=manifest.frames[index],image=createImage();
   images[index]=image;image.alt='';image.width=manifest.width;image.height=manifest.height;
   image.style.cssText='position:absolute;inset:0;display:none;width:100%;height:100%;object-fit:contain';
   image.style.display='none';root.append(image);image.src='/'+row.path;
   await image.decode();
   require(!disposed,'manim_method_resource_disposed');
  }
 }
 // At most two concurrent decodes; at most the manifest's 128 MiB RGBA total
 // remains resident. The host must enforce aggregate resource budgets too.
 const preparation=Promise.all([decodeNext(),decodeNext()]).then(()=>{prepared=true;});
 const ready=Promise.race([preparation,stopped]).catch(error=>{dispose();throw error;});
 // Disposal before a caller awaits ready must still release resources without
 // becoming an unhandled rejection. The original ready promise stays rejected.
 ready.catch(()=>{});
 return {ready,
  seekFrame(globalFrame){
   require(!disposed,'manim_method_resource_disposed');
   require(prepared,'manim_method_resource_not_ready');
   const index=manimFrameIndex(manifest,globalFrame,first);
   if(visible!==null)images[visible].style.display='none';
   visible=index;
   if(index!==null)images[index].style.display='block';
  },
  dispose,
 };
}
