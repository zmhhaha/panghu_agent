// Trusted method-demonstration host. Scene scripts never own encoding or sound.
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {readFileSync,writeFileSync,mkdirSync,lstatSync,unlinkSync} from 'node:fs';
import {join,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';

const hash=raw=>createHash('sha256').update(raw).digest('hex');
const finite=value=>typeof value==='number'&&Number.isFinite(value);

export function validateSoundEvents(events,duration){
 if(!finite(duration)||duration<1||duration>60||!Array.isArray(events)||events.length>128)
  throw Error('method_sound_budget_invalid');
 for(const event of events){
  if(!event||Array.isArray(event)||Object.keys(event).sort().join(',')!=='duration,frequency,gain,time,waveform')
   throw Error('method_sound_event_invalid');
  const {time,duration:seconds,frequency,gain,waveform}=event;
  if(!finite(time)||time<0||!finite(seconds)||seconds<.01||seconds>5||time+seconds>duration
   ||!finite(frequency)||frequency<40||frequency>16000||!finite(gain)||gain<0||gain>.8
   ||!['sine','triangle','noise'].includes(waveform))throw Error('method_sound_event_invalid');
 }
 return events;
}

export function synthesizeSoundEvents(events,duration,sampleRate=48000){
 validateSoundEvents(events,duration);
 if(sampleRate!==48000)throw Error('method_sound_sample_rate_invalid');
 const samples=Math.round(duration*sampleRate),signal=new Float64Array(samples);
 for(let index=0;index<events.length;index++){
  const event=events[index],start=Math.round(event.time*sampleRate),count=Math.round(event.duration*sampleRate);
  let seed=(index+1)*2654435761>>>0;
  for(let i=0;i<count&&start+i<samples;i++){
   const phase=i*event.frequency/sampleRate;
   let value;
   if(event.waveform==='noise'){seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;value=(seed>>>0)/2147483648-1;}
   else if(event.waveform==='triangle')value=1-4*Math.abs(Math.round(phase)-phase);
   else value=Math.sin(2*Math.PI*phase);
   // A short deterministic envelope avoids click artifacts without changing
   // the event's absolute start or silently adding/shortening the video.
   const envelope=Math.min(1,i/Math.max(1,sampleRate*.005),(count-1-i)/Math.max(1,sampleRate*.015));
   signal[start+i]+=value*event.gain*Math.max(0,envelope);
  }
 }
 const pcm=Buffer.alloc(samples*2);
 for(let i=0;i<samples;i++)pcm.writeInt16LE(Math.round(Math.max(-1,Math.min(1,signal[i]))*32767),i*2);
 const wav=Buffer.alloc(44+pcm.length);
 wav.write('RIFF',0);wav.writeUInt32LE(36+pcm.length,4);wav.write('WAVEfmt ',8);
 wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);
 wav.writeUInt32LE(sampleRate,24);wav.writeUInt32LE(sampleRate*2,28);
 wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);
 wav.writeUInt32LE(pcm.length,40);pcm.copy(wav,44);
 return wav;
}

async function main(){
 const root='/work',input='/input';
 let browser,server;
 const receipt={schema:'video.animation-method-render-receipt.v1',status:'starting'};
 const read=(path,max)=>{const st=lstatSync(path);if(!st.isFile()||st.isSymbolicLink()||st.size>max)throw Error('method_file_invalid');return readFileSync(path);};
 const run=(command,args)=>{
  const result=spawnSync(command,args,{timeout:180000,maxBuffer:2*1024*1024,encoding:'utf8'});
  if(result.error||result.status!==0)throw Error('method_media_command_failed');
  return result.stdout;
 };
 try{
  const raw=read(join(input,'request.json'),256*1024),request=JSON.parse(raw),c=request.constraints;
  // The trusted adapter authenticates the exact UTF-8 request bytes through
  // this environment value. Do not reserialize parsed floats here: Python and
  // JavaScript legitimately use different spellings for 1.0 and small values.
  if(request.schema!=='video.animation-method-render-request.v1'
   ||!/^([0-9a-f]{64})$/.test(request.identity)
   ||!/^([0-9a-f]{64})$/.test(request.sound_events_sha256)
   ||hash(raw)!==process.env.ANIMATION_METHOD_REQUEST_SHA256
   ||request.image_digest!==process.env.ANIMATION_METHOD_IMAGE_DIGEST
   ||hash(read(fileURLToPath(import.meta.url),1024*1024))!==request.host_sha256)
   throw Error('method_request_binding_invalid');
  if(c.fps!==24||!Number.isInteger(c.width)||c.width<320||c.width>1280
   ||!Number.isInteger(c.height)||c.height<180||c.height>720
   ||!finite(c.duration_seconds)||c.duration_seconds<1||c.duration_seconds>60
   ||!Number.isInteger(c.duration_seconds*24))throw Error('method_render_budget_invalid');
  validateSoundEvents(request.sound_events,c.duration_seconds);
  const count=c.duration_seconds*24;
  if(count>request.max_frames||request.max_frames!==1440)throw Error('method_frame_budget_invalid');
  const {collectRuntime}=await import('./runtime.mjs');
  Object.assign(receipt,{request_identity:request.identity,request_sha256:hash(raw),
   image_digest:request.image_digest,host_sha256:request.host_sha256,runtime:collectRuntime()});
  const allowed=new Map();
  for(const [name,expected] of Object.entries(request.files_sha256)){
   if(!['scene.html','scene.js','scene.css'].includes(name))throw Error('method_scene_file_invalid');
   const bytes=read(join(input,'scene',name),512*1024);
   if(hash(bytes)!==expected)throw Error('method_scene_changed');
   allowed.set('/'+name,bytes);
  }
  for(const [name,expected] of Object.entries(request.assets_sha256)){
   if(!['gsap.min.js','NotoSansCJK-Regular.ttc'].includes(name))throw Error('method_asset_not_admitted');
   const bytes=read(join(input,'scene/assets',name),32*1024*1024);
   if(hash(bytes)!==expected)throw Error('method_asset_changed');
   allowed.set('/assets/'+name,bytes);
  }
  const mime={'.html':'text/html;charset=utf-8','.js':'text/javascript;charset=utf-8',
   '.css':'text/css;charset=utf-8','.ttc':'font/collection'};
  server=createServer((req,res)=>{
   const url=new URL(req.url,'http://localhost'),bytes=allowed.get(url.pathname);
   res.setHeader('Content-Security-Policy',"default-src 'none';script-src 'self' 'unsafe-inline';style-src 'self' 'unsafe-inline';img-src 'self' data:;font-src 'self';connect-src 'none';media-src 'none';worker-src 'none';frame-src 'none';object-src 'none';base-uri 'none';form-action 'none'");
   res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');
   if(req.method!=='GET'||url.search||!bytes){res.writeHead(404);res.end();return;}
   res.setHeader('Content-Type',mime[extname(url.pathname)]||'application/octet-stream');res.end(bytes);
  });
  await new Promise((done,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',done);});
  const origin='http://127.0.0.1:'+server.address().port;
  const {chromium}=createRequire('/opt/renderer/package.json')('playwright-core');
  browser=await chromium.launch({headless:true,executablePath:'/opt/browser/headless_shell',
   args:['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage','--disable-gpu','--disable-background-networking']});
  const context=await browser.newContext({viewport:{width:c.width,height:c.height},
   deviceScaleFactor:1,serviceWorkers:'block',acceptDownloads:false});
  const blocked=[],errors=[];
  await context.route('**/*',async route=>{
   const url=new URL(route.request().url());
   if(url.origin===origin&&allowed.has(url.pathname)&&!url.search&&route.request().method()==='GET')
    await route.continue();
   else{blocked.push(url.protocol+'//'+url.hostname);await route.abort();}
  });
  if(context.routeWebSocket)await context.routeWebSocket('**/*',socket=>socket.close());
  const page=await context.newPage();page.setDefaultTimeout(10000);
  page.on('pageerror',error=>{if(errors.length<32)errors.push(String(error).slice(0,1000));});
  page.on('popup',popup=>popup.close());
  await page.goto(origin+'/scene.html',{waitUntil:'networkidle',timeout:20000});
  await page.evaluate(async()=>{await document.fonts.ready;if(typeof window.__animationExperiment?.seek!=='function')throw Error('method_seek_missing');});
  const cdp=await context.newCDPSession(page),tree=await cdp.send('Page.getFrameTree');
  const world=await cdp.send('Page.createIsolatedWorld',{frameId:tree.frameTree.frame.id,worldName:'trusted-method-runtime'});
  const checkRuntime=async()=>{
   const result=await cdp.send('Runtime.evaluate',{contextId:world.executionContextId,returnByValue:true,
    expression:"document.querySelectorAll('canvas,audio,video,iframe,object,embed').length"});
   if(result.exceptionDetails||result.result.value!==0)throw Error('method_engine_not_admitted');
  };
  mkdirSync(join(root,'frames'));
  const frames=[];let evidenceBytes=0;
  const capture=async(time,name)=>{
   await page.evaluate(async seconds=>{await window.__animationExperiment.seek(seconds);},time);
   await checkRuntime();
   const bytes=await page.screenshot({type:'jpeg',quality:80,timeout:10000});
   evidenceBytes+=bytes.length;
   if(evidenceBytes>request.max_evidence_bytes||bytes.length>4*1024*1024)throw Error('method_evidence_budget_exceeded');
   writeFileSync(join(root,'frames',name),bytes);
   const frame={name,timestamp_seconds:time,sha256:hash(bytes),bytes:bytes.length};
   frames.push(frame);return frame;
  };
  for(let i=0;i<count;i++)await capture(i/24,'frame-'+String(i).padStart(6,'0')+'.jpg');
  const reverse=await capture(0,'reverse-initial.jpg');
  if(reverse.sha256!==frames[0].sha256)throw Error('method_reverse_seek_mismatch');
  await browser.close();browser=null;
  if(errors.length||blocked.length)throw Error('method_scene_failed_or_blocked');
  const video=request.sound_events.length?'video.mp4':'output.mp4';
  run('ffmpeg',['-v','error','-nostdin','-y','-framerate','24','-i',join(root,'frames/frame-%06d.jpg'),
   '-frames:v',String(count),'-c:v','libx264','-preset','veryfast','-crf','23',
   '-pix_fmt','yuv420p','-movflags','+faststart',join(root,video)]);
  let audioSha=null;
  if(request.sound_events.length){
   const wav=synthesizeSoundEvents(request.sound_events,c.duration_seconds);
   writeFileSync(join(root,'sound-effects.wav'),wav);audioSha=hash(wav);
   run('ffmpeg',['-v','error','-nostdin','-y','-i',join(root,video),'-i',join(root,'sound-effects.wav'),
    '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','128k',
    '-t',String(c.duration_seconds),'-movflags','+faststart',join(root,'output.mp4')]);
   unlinkSync(join(root,video));
  }
  const bytes=read(join(root,'output.mp4'),request.max_media_bytes);
  const probe=JSON.parse(run('ffprobe',['-v','error','-show_streams','-show_format','-of','json',join(root,'output.mp4')]));
  const videos=probe.streams.filter(s=>s.codec_type==='video'),audios=probe.streams.filter(s=>s.codec_type==='audio');
  if(videos.length!==1||audios.length!==(audioSha?1:0)||probe.streams.length!==(audioSha?2:1)
   ||videos[0].width!==c.width||videos[0].height!==c.height
   ||Math.abs(Number(probe.format.duration)-c.duration_seconds)>1/24+.001)throw Error('method_media_probe_invalid');
  run('ffmpeg',['-v','error','-nostdin','-i',join(root,'output.mp4'),'-f','null','-']);
  Object.assign(receipt,{status:'rendered',frames,frame_count:count,
   output_sha256:hash(bytes),output_bytes:bytes.length,duration_seconds:c.duration_seconds,
   audio_sha256:audioSha,sound_events_sha256:request.sound_events_sha256,
   reverse_seek_verified:true,decode_verified:true,probe,
   whole_video_watched:false,audio_observed:false,expressive_quality:'not_evaluated'});
 }catch(error){receipt.status='failed';receipt.error=String(error).slice(0,2000);process.exitCode=1;}
 finally{
  if(browser)await browser.close().catch(()=>{});
  if(server)await new Promise(done=>server.close(done));
  writeFileSync(join(root,'render-receipt.json'),JSON.stringify(receipt,null,2));
 }
}

if(process.argv[1]===fileURLToPath(import.meta.url))await main();
