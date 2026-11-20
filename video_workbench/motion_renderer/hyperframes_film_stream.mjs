// One backpressured PNG stream, one encoder, no per-shot intermediate movies.
import {spawn} from 'node:child_process';
import {existsSync,lstatSync} from 'node:fs';
import {isAbsolute,dirname,resolve} from 'node:path';
import {createHash} from 'node:crypto';

const check=(value,code)=>{if(!value)throw Error(code);};
export async function createFilmPngEncoder({ffmpegPath,audioInput,outputPath,width,height,frameCount,
 maxMediaBytes=128*1024*1024}={}){
 check([ffmpegPath,audioInput,outputPath].every(p=>typeof p==='string'&&isAbsolute(p)), 'film_stream_path_invalid');
 check([ffmpegPath,audioInput].every(p=>lstatSync(p).isFile()&&!lstatSync(p).isSymbolicLink()),'film_stream_input_invalid');
 check(dirname(resolve(audioInput))===dirname(resolve(outputPath))&&!existsSync(outputPath),'film_stream_output_invalid');
 check(Number.isInteger(width)&&width>0&&width<=1920&&width%2===0
  &&Number.isInteger(height)&&height>0&&height<=1080&&height%2===0
  &&Number.isInteger(frameCount)&&frameCount>0&&frameCount<=7200,'film_stream_dimensions_invalid');
 check(Number.isInteger(maxMediaBytes)&&maxMediaBytes>0&&maxMediaBytes<=128*1024*1024,'film_stream_media_budget');
 const child=spawn(ffmpegPath,['-hide_banner','-loglevel','error','-nostdin','-n','-threads','1','-filter_threads','1',
  '-f','image2pipe','-framerate','24','-vcodec','png','-i','pipe:0','-i',audioInput,
  '-map','0:v:0','-map','1:a:0','-c:v','libx264','-threads:v','1','-crf','20','-pix_fmt','yuv420p',
  '-c:a','aac','-b:a','192k','-ar','48000','-ac','2','-map_metadata','-1','-movie_timescale','48000',
  '-movflags','+faststart','-fs',String(maxMediaBytes+1),outputPath],{stdio:['pipe','ignore','pipe'],windowsHide:true});
 let diagnostic=Buffer.alloc(0),failure=null,closed=false,frames=0,total=0,maximum=0;
 const chain=createHash('sha256');
 child.stderr.on('data',chunk=>{diagnostic=Buffer.concat([diagnostic,chunk]).subarray(-4096);});
 child.stdin.on('error',error=>{failure=error;});
 const completed=new Promise((yes,no)=>{
  child.once('error',error=>{failure=error;closed=true;no(error);});
  child.once('close',(code,signal)=>{closed=true;
   if(code===0&&!failure)yes();else no(Error('film_stream_encoder_failed:'+String(code??signal)+':'+diagnostic.toString('utf8')));});
 });
 // A pipe failure can precede the next awaited write/finish. Keep that failure
 // observed, while retaining the original rejection for the owner to inspect.
 completed.catch(()=>{});
 async function abort(){if(!closed)child.kill('SIGKILL');try{await completed;}catch{}}
 return {
  get processId(){return child.pid;},
  async write(bytes){
   check(!closed&&!failure&&frames<frameCount,'film_stream_encoder_not_writable');
   check(Buffer.isBuffer(bytes)&&bytes.length>=24&&bytes.length<=12*1024*1024
    &&bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))
    &&bytes.readUInt32BE(16)===width&&bytes.readUInt32BE(20)===height,'film_stream_frame_invalid');
   // Await each pipe write before accepting another PNG: only one complete
   // compressed frame can be pending, independent of total film duration.
   await new Promise((yes,no)=>child.stdin.write(bytes,error=>error?no(error):yes()));
   chain.update(frames+':'+bytes.length+':'+createHash('sha256').update(bytes).digest('hex')+'\n');
   frames++;total+=bytes.length;maximum=Math.max(maximum,bytes.length);
   check(total<=16*1024*1024*1024,'film_stream_frame_budget');
   if(existsSync(outputPath))check(lstatSync(outputPath).size<=maxMediaBytes,'film_stream_media_budget');
  },
  async finish(){
   check(frames===frameCount,'film_stream_frame_count');child.stdin.end();await completed;
   check(existsSync(outputPath)&&lstatSync(outputPath).isFile()&&!lstatSync(outputPath).isSymbolicLink()
    &&lstatSync(outputPath).size>0&&lstatSync(outputPath).size<=maxMediaBytes,'film_stream_media_invalid');
   return {schema:'video.hyperframes-film-png-stream.v1',mode:'streaming-png-v2',encoder_invocations:1,
    frame_files:0,peak_pending_frames:1,max_frame_bytes:maximum,frame_bytes:total,frame_count:frames,
    frame_inventory_sha256:chain.digest('hex')};
  },abort
 };
}
