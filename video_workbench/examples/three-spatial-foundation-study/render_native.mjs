// Run from the checkout with --out <fresh directory>. No model or production API.
import {readFileSync,writeFileSync,mkdirSync,existsSync} from 'node:fs';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {dirname,resolve,join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import assert from 'node:assert/strict';
const EXAMPLE=dirname(fileURLToPath(import.meta.url)),REPO=resolve(EXAMPLE,'../../..');
const arg=name=>process.argv.indexOf(name)>=0?process.argv[process.argv.indexOf(name)+1]:null;
const out=resolve(arg('--out')||'three-spatial-native-output');assert(!existsSync(out),'output must be fresh');mkdirSync(out,{recursive:true});
const sha=raw=>createHash('sha256').update(raw).digest('hex'),save=(name,value)=>writeFileSync(join(out,name),JSON.stringify(value,null,2)+'\n');
const baselineRoot=join(EXAMPLE,'../spatial-foundation-study'),baselineRaw=readFileSync(join(baselineRoot,'baseline-source.json'));
const baseline=JSON.parse(baselineRaw),source=baseline.files['scene.js'];
assert.equal(baseline.source_sha256,'1b6693b620f576dacf2b328367999a00458b8440f3f86ff825e996b153734be0');
const runtime=join(REPO,'video_workbench/motion_renderer'),resources=new Map([
 ['/study.html',readFileSync(join(EXAMPLE,'study.html'))],['/study.mjs',readFileSync(join(EXAMPLE,'study.mjs'))],
 ['/plan.json',readFileSync(join(baselineRoot,'plan.json'))],
 ['/baseline-scene.js',Buffer.from(source+'\nexport {MASS_CONFIG,authoredHeight};\n')],
 ['/three.module.min.js',readFileSync(join(runtime,'vendor/three.module.min.js'))],
 ...['spatial_three_adapter.mjs','scene_space_surface.mjs','scene_space_surface_budget.mjs']
  .map(name=>['/'+name,readFileSync(join(runtime,name))]),
 ...['ink-landscape.mjs','ink-landscape.LICENSE.txt'].map(name=>['/assets/'+name,readFileSync(join(REPO,'video_workbench/vendor/ink-landscape',name))])]);
save('input-resources.json',Object.fromEntries([...resources].map(([path,raw])=>[path,{bytes:raw.length,sha256:sha(raw)}])));
const served=[],server=createServer((request,response)=>{const path=new URL(request.url,'http://127.0.0.1').pathname,raw=resources.get(path);
 served.push({path,found:!!raw});if(!raw){response.writeHead(404);response.end();return;}
 response.writeHead(200,{'content-type':path.endsWith('.html')?'text/html':path.endsWith('.json')?'application/json':path.endsWith('.txt')?'text/plain':'text/javascript',
  'cache-control':'no-store','x-content-type-options':'nosniff'});response.end(raw);});
await new Promise(ok=>server.listen(0,'127.0.0.1',ok));
const playwright=arg('--playwright-package')||'C:/Users/4770_2070super/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core/package.json';
const chrome=arg('--chrome')||'C:/Program Files/Google/Chrome/Application/chrome.exe';
const {chromium}=createRequire(playwright)('playwright-core');
const engine=await import(pathToFileURL(join(runtime,'engine_runtime.mjs')).href);
const started=performance.now(),browser=await chromium.launch({headless:true,executablePath:chrome,args:engine.browserLaunchArgs('three')});
const context=await browser.newContext({viewport:{width:1280,height:720},deviceScaleFactor:1});await engine.installEngineGuard(context,'three');
const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(String(error)));
const url='http://127.0.0.1:'+server.address().port+'/study.html';
const load=async()=>{await page.goto(url,{waitUntil:'networkidle'});await page.waitForFunction(()=>!!window.__threeStudy,{timeout:60000});assert.deepEqual(errors,[]);};
const png=async frame=>{await page.evaluate(frame=>window.__threeStudy.renderFrame(frame),frame);
 const image=await page.evaluate(()=>window.__threeStudy.png());return Buffer.from(image.split(',')[1],'base64');};
try{
 await load();const native=await engine.verifyPageEngine(page,'three');save('native-engine.json',native);
 const diagnostics=await page.evaluate(()=>window.__threeStudy.diagnostics());save('diagnostics.json',diagnostics);
 assert.equal(diagnostics.actual_backend.webgl2,true);assert.match(diagnostics.actual_backend.renderer,/swiftshader/i);
 assert.equal(diagnostics.actual_backend.context_lost,false);assert.equal(diagnostics.actual_backend.gl_error,0);
 for(const [name,result]of Object.entries(diagnostics))if(name!=='actual_backend')assert.equal(result.passed,true,name);
 const sourceMeshes=await page.evaluate(()=>window.__threeStudy.sourceMeshes);save('source-meshes.json',sourceMeshes);
 const frames=[0,18,80,120,216,239],hashes={};
 for(const frame of frames){const raw=await png(frame);hashes[frame]=sha(raw);writeFileSync(join(out,'sample-'+frame+'.png'),raw);
  assert.equal(sha(await png(frame)),hashes[frame],'repeat '+frame);}
 for(const frame of [...frames].reverse())assert.equal(sha(await png(frame)),hashes[frame],'reverse '+frame);
 const beforeReload=await page.evaluate(()=>window.__threeStudy.observation());save('observation-before-reload.json',beforeReload);
 await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>!!window.__threeStudy,{timeout:60000});
 for(const frame of frames)assert.equal(sha(await png(frame)),hashes[frame],'reload '+frame);
 assert.notEqual(hashes[0],hashes[239],'the planned camera push must actually affect pixels');
 const frameDir=join(out,'frames');mkdirSync(frameDir);for(let frame=0;frame<240;frame++){
  await page.evaluate(frame=>window.__threeStudy.renderFrame(frame),frame);
  const image=await page.evaluate(()=>window.__threeStudy.jpg());writeFileSync(join(frameDir,String(frame).padStart(6,'0')+'.jpg'),Buffer.from(image.split(',')[1],'base64'));
 }
 save('observation-final.json',await page.evaluate(()=>window.__threeStudy.observation()));
 assert.deepEqual(errors,[]);save('native-engine-final.json',await engine.verifyPageEngine(page,'three'));
 const mediaRun=(name,args)=>{const result=spawnSync(name,args,{encoding:'utf8',timeout:120000,maxBuffer:4*1024*1024});
  assert.equal(result.status,0,result.error?.message||result.stderr);return result.stdout;};
 const mp4=join(out,'preview.mp4');mediaRun('ffmpeg',['-v','error','-y','-framerate','24','-i',join(frameDir,'%06d.jpg'),'-c:v','libx264','-crf','18','-pix_fmt','yuv420p',mp4]);
 const probe=JSON.parse(mediaRun('ffprobe',['-v','error','-count_frames','-show_streams','-show_format','-of','json',mp4]));save('ffprobe.json',probe);
 const video=probe.streams.find(item=>item.codec_type==='video');assert.equal(video.nb_read_frames,'240');assert.equal(video.width,1280);assert.equal(video.height,720);assert.equal(video.r_frame_rate,'24/1');
 assert(!probe.streams.some(item=>item.codec_type==='audio'));mediaRun('ffmpeg',['-v','error','-xerror','-i',mp4,'-f','null','-']);
 const evidence={schema:'video.three-spatial-local-evidence.v1',scope:'actual_local_cpu_browser_not_production_admission_or_artistic_acceptance',
  model_requests:0,production_posts:0,seed:631907,baseline_source_sha256:baseline.source_sha256,
  original_baseline_file_sha256:sha(baselineRaw),original_scene_js_sha256:sha(source),plan_file_sha256:sha(resources.get('/plan.json')),
  controlled_differences:['Three indexed vertex-normal surfaces replace the Canvas mountain ink shader',
   'Authored per-mass orthographic projections are mapped into one Three world with explicit world matrices',
   'Original non-mountain painters become static Three textures; 0.28-height clipping replaces the original foot alpha fade',
   'Seeded appearance is per-vertex linear color; it is not equivalent to original brush strokes or PBR'],
  supported:['world normal/light guidance','fixed object-coordinate material','distance and height color atmosphere',
   'native opaque depth coverage','explicit native transparency','absolute-frame camera reconstruction'],
  not_claimed:['artistic reproduction','physical shadow transport','pixel texture','skinning','morphing','new GPU admission','production rollout','motion acceptance'],
  checks:{native_swiftshader:true,mechanism_diagnostics:true,repeat:true,reverse:true,reload:true,planned_camera_changes_pixels:true,full_decode:true},
  sampled_frame_png_sha256:hashes,preview:{sha256:sha(readFileSync(mp4)),width:1280,height:720,fps:24,frames:240,seconds:10,audio:false},
  resource_inventory:Object.fromEntries([...resources].map(([path,raw])=>[path,{bytes:raw.length,sha256:sha(raw)}])),
  elapsed_seconds:(performance.now()-started)/1000,browser_executable:chrome,browser_version:browser.version(),
  whole_video_watched:false,user_artistic_acceptance:false};save('evidence.json',evidence);
 console.log(JSON.stringify({output:out,seconds:evidence.elapsed_seconds,checks:evidence.checks,preview:evidence.preview}));
}catch(error){save('failure.json',{error:String(error),stack:error.stack,elapsed_seconds:(performance.now()-started)/1000});throw error;
}finally{save('browser-errors.json',errors);save('served-paths.json',served);await context.close();await browser.close();await new Promise(ok=>server.close(ok));}
