// Resource custody tests only; this does not execute browser/core or Agent code.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,rmSync,symlinkSync,readFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createHash} from 'node:crypto';
import {readFilmBrowserBundle} from '../../video_workbench/motion_renderer/hyperframes_film_browser.mjs';
import {HYPERFRAMES_CORE_SHA256} from '../../video_workbench/motion_renderer/hyperframes_project.mjs';
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
function fixture(){
 const root=mkdtempSync(join(process.env.PANGHU_TEST_TEMP||tmpdir(),'hffilm-browser-'));
 const project=JSON.stringify({schema:'video.hyperframes-film-project.v1',film_sha256:'a'.repeat(64),execution:{status:'not_executed'}});
 const host=readFileSync(new URL('../../video_workbench/motion_renderer/hyperframes_film_browser.mjs',import.meta.url));
 const config={schema:'video.hyperframes-film-root-config.v1',project_manifest_sha256:sha(project),film_sha256:'a'.repeat(64),
  width:640,height:360,root_entry:'host/index.html',realm_protocol:'opaque-sandbox-message-channel-1',
  hyperframes_core_sha256:HYPERFRAMES_CORE_SHA256,host_files_sha256:{'hyperframes_film_browser.mjs':sha(host)}};
 const files={'host/config.json':Buffer.from(JSON.stringify(config)),'host/index.html':Buffer.from('<main/>'),
  'host/hyperframes_film_browser.mjs':host,'project/manifest.json':Buffer.from(project)};
 const bundle={schema:'video.hyperframes-film-browser-bundle.v1',execution_status:'not_executed',
  package_verification:'independent_project_recompilation',root_config:config,project_manifest_sha256:sha(project),realms:{},
  resources:Object.fromEntries(Object.entries(files).map(([name,raw])=>[name,{sha256:sha(raw),bytes:raw.length,role:'fixture'}]))};
 const save=()=>{const raw=Buffer.from(JSON.stringify(bundle));for(const [name,bytes] of Object.entries({...files,'browser-manifest.json':raw})){
  const target=join(root,name);mkdirSync(join(target,'..'),{recursive:true});writeFileSync(target,bytes);
 }return sha(raw);};
 return {root,bundle,files,save,dispose(){rmSync(root,{recursive:true,force:true});}};
}
test('external root digest binds every byte and exact inventory',()=>{
 const f=fixture();try{
  const digest=f.save();assert.equal(readFilmBrowserBundle(f.root,digest).config.width,640);
  assert.throws(()=>readFilmBrowserBundle(f.root,'f'.repeat(64)),/bundle_digest/);
  writeFileSync(join(f.root,'host/hyperframes_film_browser.mjs'),'changed');
  assert.throws(()=>readFilmBrowserBundle(f.root,digest),/resource_changed/);
 }finally{f.dispose();}
});
test('unlisted files and symlink resources are rejected',()=>{
 const f=fixture();try{
  const digest=f.save();writeFileSync(join(f.root,'extra'),'extra');
  assert.throws(()=>readFilmBrowserBundle(f.root,digest),/exact_inventory/);
  rmSync(join(f.root,'extra'));
  if(process.platform!=='win32'){
   symlinkSync(join(f.root,'host/index.html'),join(f.root,'linked'));
   assert.throws(()=>readFilmBrowserBundle(f.root,digest),/symlink/);
  }
 }finally{f.dispose();}
});
for(const field of ['root_entry','width','project_manifest_sha256','hyperframes_core_sha256','realm_protocol'])
 test(`a rehashed bundle does not bypass ${field} root binding`,()=>{
  const f=fixture();try{
   f.bundle.root_config[field]=field==='width'?1:'invalid';const digest=f.save();
   assert.throws(()=>readFilmBrowserBundle(f.root,digest),/root_binding/);
  }finally{f.dispose();}
 });
test('resource paths cannot traverse or load a remote URL',()=>{
 for(const path of ['../escape','/absolute','https://example.com/a.js','host//a.js']){
  const f=fixture();try{
   f.bundle.resources[path]={sha256:'a'.repeat(64),bytes:1};const digest=f.save();
   assert.throws(()=>readFilmBrowserBundle(f.root,digest),/path_invalid/);
  }finally{f.dispose();}
 }
});
