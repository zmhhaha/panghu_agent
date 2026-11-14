// Real pre-browser rejection boundaries. Synthetic bundle metadata exercises
// byte custody/limits only; no browser, core, frame capture or media is run.
// Complete frame/repeat/storage evidence belongs to the separately budgeted
// real-core technical export, not a replaceable production probe dependency.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,existsSync,rmSync,symlinkSync} from 'node:fs';
import {join,dirname} from 'node:path';
import {tmpdir} from 'node:os';
import {createHash} from 'node:crypto';
import {captureFilmFrames} from '../../video_workbench/motion_renderer/hyperframes_film_capture.mjs';
import {HYPERFRAMES_CORE_SHA256} from '../../video_workbench/motion_renderer/hyperframes_project.mjs';

const sha=value=>createHash('sha256').update(value).digest('hex');
function fixture(frameCount=24){
 const root=mkdtempSync(join(process.env.PANGHU_TEST_TEMP||tmpdir(),'hffilm-capture-'));
 const bundleRoot=join(root,'bundle'),framesRoot=join(root,'frames');mkdirSync(bundleRoot);
 const project=Buffer.from(JSON.stringify({schema:'video.hyperframes-film-project.v1',film_sha256:'a'.repeat(64),
  execution:{status:'not_executed'},clock:{frame_count:frameCount}}));
 const adapter=readFileSync(new URL('../../video_workbench/motion_renderer/hyperframes_film_browser.mjs',import.meta.url));
 const config={schema:'video.hyperframes-film-root-config.v1',project_manifest_sha256:sha(project),film_sha256:'a'.repeat(64),
  width:640,height:360,root_entry:'host/index.html',realm_protocol:'opaque-sandbox-message-channel-1',
  hyperframes_core_sha256:HYPERFRAMES_CORE_SHA256,host_files_sha256:{'hyperframes_film_browser.mjs':sha(adapter)}};
 const resources={'host/index.html':Buffer.from('<main/>'),'host/config.json':Buffer.from(JSON.stringify(config)),
  'host/hyperframes_film_browser.mjs':adapter,'project/manifest.json':project};
 const manifest=Buffer.from(JSON.stringify({schema:'video.hyperframes-film-browser-bundle.v1',execution_status:'not_executed',
  package_verification:'independent_project_recompilation',root_config:config,project_manifest_sha256:sha(project),realms:{},
  resources:Object.fromEntries(Object.entries(resources).map(([name,raw])=>[name,{sha256:sha(raw),bytes:raw.length,role:'test-custody-fixture'}]))}));
 for(const [name,raw] of Object.entries({...resources,'browser-manifest.json':manifest})){
  const path=join(bundleRoot,name);mkdirSync(dirname(path),{recursive:true});writeFileSync(path,raw);
 }
 let launches=0;
 const options={bundleRoot,framesRoot,expectedBundleSha256:sha(manifest),
  chromium:{launch(){launches++;throw Error('unexpected_browser_start');}},
  hyperFramesDist:join(root,'core-must-not-be-read'),executablePath:'browser-must-not-run'};
 return {root,bundleRoot,framesRoot,options,launches:()=>launches,dispose(){rmSync(root,{recursive:true,force:true});}};
}

for(const count of [0,-1,1.5,1441,99999,'24',null,true])test(`invalid frame budget ${String(count)} fails before output or browser`,async()=>{
 const f=fixture(count);try{
  await assert.rejects(captureFilmFrames(f.options),/film_capture_duration_budget/);
  assert.equal(existsSync(f.framesRoot),false);assert.equal(f.launches(),0);
 }finally{f.dispose();}
});

for(const digest of [undefined,'wrong','f'.repeat(64)])test(`missing or incorrect external digest ${String(digest).slice(0,8)} fails before capture`,async()=>{
 const f=fixture();try{
  await assert.rejects(captureFilmFrames({...f.options,expectedBundleSha256:digest}),/film_browser_(external_digest_required|bundle_digest_mismatch)/);
  assert.equal(existsSync(f.framesRoot),false);assert.equal(f.launches(),0);
 }finally{f.dispose();}
});

test('source changed after freezing cannot create an output directory',async()=>{
 const f=fixture();try{
  writeFileSync(join(f.bundleRoot,'host/hyperframes_film_browser.mjs'),'changed source');
  await assert.rejects(captureFilmFrames(f.options),/film_browser_resource_changed/);
  assert.equal(existsSync(f.framesRoot),false);assert.equal(f.launches(),0);
 }finally{f.dispose();}
});

test('existing output directory and all prior frame bytes remain untouched',async()=>{
 const f=fixture();try{
  mkdirSync(f.framesRoot);const prior=Buffer.from('previous complete frame');
  const path=join(f.framesRoot,'frame-000000.png');writeFileSync(path,prior);
  await assert.rejects(captureFilmFrames(f.options),error=>error.code==='EEXIST');
  assert.ok(readFileSync(path).equals(prior));assert.equal(f.launches(),0);
 }finally{f.dispose();}
});

test('existing regular output file is never overwritten',async()=>{
 const f=fixture();try{
  const prior=Buffer.from('caller-owned file');writeFileSync(f.framesRoot,prior);
  await assert.rejects(captureFilmFrames(f.options),error=>error.code==='EEXIST');
  assert.ok(readFileSync(f.framesRoot).equals(prior));assert.equal(f.launches(),0);
 }finally{f.dispose();}
});

test('bundle input directory cannot be reused as frame output',async()=>{
 const f=fixture();try{
  const manifest=readFileSync(join(f.bundleRoot,'browser-manifest.json'));
  await assert.rejects(captureFilmFrames({...f.options,framesRoot:f.bundleRoot}),error=>error.code==='EEXIST');
  assert.ok(readFileSync(join(f.bundleRoot,'browser-manifest.json')).equals(manifest));assert.equal(f.launches(),0);
 }finally{f.dispose();}
});

test('existing output symlink cannot redirect or overwrite frames',{skip:process.platform==='win32'},async()=>{
 const f=fixture();try{
  const target=join(f.root,'caller-owned');mkdirSync(target);const path=join(target,'keep');writeFileSync(path,'keep');
  symlinkSync(target,f.framesRoot,'dir');await assert.rejects(captureFilmFrames(f.options),error=>error.code==='EEXIST');
  assert.equal(readFileSync(path,'utf8'),'keep');assert.equal(f.launches(),0);
 }finally{f.dispose();}
});
