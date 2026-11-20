// Offline whole-project capture. Production submission/admission is separate.
import {mkdirSync,writeFileSync,readFileSync,lstatSync} from 'node:fs';
import {resolve,join,dirname} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {readFilmBrowserBundle,probeFilmBrowser} from './hyperframes_film_browser.mjs';
import {createFilmPngEncoder} from './hyperframes_film_stream.mjs';

const sha=raw=>createHash('sha256').update(raw).digest('hex');
const check=(value,code)=>{if(!value)throw Error(code);};
export async function captureFilmFrames({bundleRoot,expectedBundleSha256,framesRoot,
 chromium,executablePath,hyperFramesDist,streamEncoding}={}){
 const verified=readFilmBrowserBundle(bundleRoot,expectedBundleSha256);
 const count=verified.project.clock?.frame_count;
 const streaming=streamEncoding!==undefined;
 check(Number.isInteger(count)&&count>0&&count<=(streaming?7200:1440),'film_capture_duration_budget');
 if(streaming)check(verified.config.capture_lifecycle==='persistent-active-modules-v1','film_capture_stream_lifecycle_required');
 framesRoot=resolve(framesRoot);
 for(let ancestor=dirname(framesRoot);;ancestor=dirname(ancestor)){
  const stat=lstatSync(ancestor);
  check(stat.isDirectory()&&!stat.isSymbolicLink(),'film_capture_output_ancestor_invalid');
  if(dirname(ancestor)===ancestor)break;
 }
 mkdirSync(framesRoot);check(!lstatSync(framesRoot).isSymbolicLink(),'film_capture_output_invalid');
 const frames=Array.from({length:count},(_,index)=>index),repeats=[count-1,0,Math.floor(count/2)];
 let ordinal=0,total=0;const samples=[];
 let encoder,storage,probe;
 try{
 if(streaming)encoder=await createFilmPngEncoder({...streamEncoding,width:verified.config.width,
  height:verified.config.height,frameCount:count});
 probe=await probeFilmBrowser({bundleRoot,expectedBundleSha256,chromium,executablePath,hyperFramesDist,
  frames:[...frames,...repeats],...(streaming?{reconstructFromOrdinal:count}:{}),async onFrame(frame,bytes){
   const checksum=sha(bytes);
   if(ordinal<count){
    check(frame===ordinal,'film_capture_frame_order');total+=bytes.length;
    check(bytes.length<=12*1024*1024&&total<=(streaming?16*1024*1024*1024:512*1024*1024),'film_capture_storage_budget');
    const path='frame-'+String(ordinal).padStart(6,'0')+'.png';
    if(streaming)await encoder.write(bytes);else writeFileSync(join(framesRoot,path),bytes,{flag:'wx'});
    samples.push({frame,path,bytes:bytes.length,sha256:checksum});
   }else check(frame===repeats[ordinal-count]&&checksum===samples[frame].sha256,'film_capture_repeat_mismatch');
   ordinal++;
  }});
 if(streaming)storage=await encoder.finish();
 }catch(error){await encoder?.abort();throw error;}
 check(ordinal===count+repeats.length&&samples.length===count,'film_capture_incomplete');
 return {schema:'video.hyperframes-film-frame-capture.'+(streaming?'v2':'v1'),bundle_sha256:expectedBundleSha256,
  project_manifest_sha256:verified.bundle.project_manifest_sha256,film_sha256:verified.project.film_sha256,
  fps:24,width:verified.config.width,height:verified.config.height,frame_count:count,
  frame_bytes:total,frames:samples,probe,...(streaming?{storage}:{}),
  evidence_scope:'actual_whole_project_frames_not_agent_quality_or_production_admission'};
}

if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
 const config=JSON.parse(readFileSync(process.argv[2],'utf8'));
 const {chromium}=await import(config.playwright_module||'playwright-core');
 const receipt=await captureFilmFrames({...config,chromium});
 process.stdout.write(JSON.stringify(receipt));
}
