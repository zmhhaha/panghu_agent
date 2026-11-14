// Offline whole-project capture. Production submission/admission is separate.
import {mkdirSync,writeFileSync,readFileSync,lstatSync} from 'node:fs';
import {resolve,join,dirname} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {readFilmBrowserBundle,probeFilmBrowser} from './hyperframes_film_browser.mjs';

const sha=raw=>createHash('sha256').update(raw).digest('hex');
const check=(value,code)=>{if(!value)throw Error(code);};
export async function captureFilmFrames({bundleRoot,expectedBundleSha256,framesRoot,
 chromium,executablePath,hyperFramesDist}={}){
 const verified=readFilmBrowserBundle(bundleRoot,expectedBundleSha256);
 const count=verified.project.clock?.frame_count;
 // Initial offline boundary: longer films need a separately verified chunk protocol.
 check(Number.isInteger(count)&&count>0&&count<=1440,'film_capture_duration_budget');
 framesRoot=resolve(framesRoot);
 for(let ancestor=dirname(framesRoot);;ancestor=dirname(ancestor)){
  const stat=lstatSync(ancestor);
  check(stat.isDirectory()&&!stat.isSymbolicLink(),'film_capture_output_ancestor_invalid');
  if(dirname(ancestor)===ancestor)break;
 }
 mkdirSync(framesRoot);check(!lstatSync(framesRoot).isSymbolicLink(),'film_capture_output_invalid');
 const frames=Array.from({length:count},(_,index)=>index),repeats=[count-1,0,Math.floor(count/2)];
 let ordinal=0,total=0;const samples=[];
 const probe=await probeFilmBrowser({bundleRoot,expectedBundleSha256,chromium,executablePath,hyperFramesDist,
  frames:[...frames,...repeats],onFrame(frame,bytes){
   const checksum=sha(bytes);
   if(ordinal<count){
    check(frame===ordinal,'film_capture_frame_order');total+=bytes.length;
    check(bytes.length<=12*1024*1024&&total<=512*1024*1024,'film_capture_storage_budget');
    const path='frame-'+String(ordinal).padStart(6,'0')+'.png';
    writeFileSync(join(framesRoot,path),bytes,{flag:'wx'});
    samples.push({frame,path,bytes:bytes.length,sha256:checksum});
   }else check(frame===repeats[ordinal-count]&&checksum===samples[frame].sha256,'film_capture_repeat_mismatch');
   ordinal++;
  }});
 check(ordinal===count+repeats.length&&samples.length===count,'film_capture_incomplete');
 return {schema:'video.hyperframes-film-frame-capture.v1',bundle_sha256:expectedBundleSha256,
  project_manifest_sha256:verified.bundle.project_manifest_sha256,film_sha256:verified.project.film_sha256,
  fps:24,width:verified.config.width,height:verified.config.height,frame_count:count,
  frame_bytes:total,frames:samples,probe,
  evidence_scope:'actual_whole_project_frames_not_agent_quality_or_production_admission'};
}

if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
 const config=JSON.parse(readFileSync(process.argv[2],'utf8'));
 const {chromium}=await import(config.playwright_module||'playwright-core');
 const receipt=await captureFilmFrames({...config,chromium});
 process.stdout.write(JSON.stringify(receipt));
}
