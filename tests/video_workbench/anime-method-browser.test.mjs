// Real host/core compatibility; this does not admit an image or approve a sample.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {captureMethodFrames} from '../../video_workbench/animation_method_host.mjs';
import * as engineRuntime from '../../video_workbench/motion_renderer/engine_runtime.mjs';
const enabled=process.env.PANGHU_ANIME_PLAYWRIGHT&&process.env.PANGHU_ANIME_BROWSER
  &&process.env.PANGHU_ANIME_HYPERFRAMES_DIST&&process.env.PANGHU_TEST_TEMP;
const sha=raw=>createHash('sha256').update(raw).digest('hex');
const canonical=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)
  ?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);

test('actual method v4 host imports official Anime under real HyperFrames core and verifies every frame', {skip:!enabled},async()=>{
  const {chromium}=await import(pathToFileURL(process.env.PANGHU_ANIME_PLAYWRIGHT).href);
  const root=mkdtempSync(join(process.env.PANGHU_TEST_TEMP,'anime-method-host-'));
  const profile=mkdtempSync(join(process.env.PANGHU_TEST_TEMP,'anime-method-profile-'));
  const vendor=new URL('../../video_workbench/motion_renderer/vendor/',import.meta.url);
  const manifest=JSON.parse(readFileSync(new URL('animejs-assets.json',vendor)));
  const assets=Object.fromEntries(Object.entries(manifest.assets).map(([name,row])=>[name,row.sha256]));
  const descriptor={schema:'video.animation-authoring-library.v1',library_id:'animejs',version:'4.5.0',
    license:'MIT',role:'animation-authoring',engines:['html-svg','canvas','three'],
    clock:'hyperframes-absolute-seek',assets};
  const html=`<!doctype html><style>body{margin:0;background:#101629}svg{display:block}</style>
  <svg width="640" height="360"><circle id="dot" cx="40" cy="180" r="30" fill="#50d4d0"/></svg>
  <script type="module">
  import {createFrameTimeline} from './assets/anime_timeline.mjs';
  const state={x:40}, timeline=createFrameTimeline({fps:24,durationFrames:24});
  timeline.add(state,{x:[40,560],duration:1000});
  const dot=document.querySelector('#dot');
  window.__animationExperiment={seek(seconds){const frame=Math.round(seconds*24);
   timeline.renderAt({global_frame:frame,time:{numerator:frame,denominator:24}});dot.setAttribute('cx',state.x);}};
  </script>`;
  const project={schema:'video.hyperframes-project.v1',project_id:'anime-real-host',width:640,height:360,fps:24,
    duration_seconds:1,entry:'scene.html',scenes:[{id:'scene',start_seconds:0,duration_seconds:1,
      entry:'scene.html',source_sha256:sha(html),engine:'html-svg'}]};
  const request={schema:'video.animation-method-render-request.v4',engine:'html-svg',
    constraints:{width:640,height:360,fps:24,duration_seconds:1},project,project_sha256:sha(canonical(project)),
    legacy_bridge:true,assets_sha256:assets,authoring_libraries:{animejs:descriptor},max_evidence_bytes:192*1024*1024};
  const resources=new Map(Object.keys(assets).map(name=>['/assets/'+name,readFileSync(new URL(name,vendor))]));
  resources.set('/scene.html',Buffer.from(html));
  // Keep Chromium's user profile on the caller-specified scratch volume.
  const launcher={async launch(options){
    const context=await chromium.launchPersistentContext(profile,{...options,viewport:{width:640,height:360},
      deviceScaleFactor:1,serviceWorkers:'block',acceptDownloads:false});
    return {async newContext(){return context;},async close(){await context.close();}};
  }};
  try{
    const result=await captureMethodFrames({chromium:launcher,request,resources,root,engineRuntime,
      executablePath:process.env.PANGHU_ANIME_BROWSER,hyperFramesDist:process.env.PANGHU_ANIME_HYPERFRAMES_DIST});
    assert.equal(result.frame_count,24);assert.equal(result.reverse_seek_verified,true);
    assert.equal(result.seek_checks.idle_frame_verified,true);assert.equal(result.seek_checks.fresh_load_verified,true);
    assert.equal(result.project_runtime.core_sha256,'675e02ee21f31540e3a6abaec061f86cea92a0178ae67a390700acb4f801dbbc');
    assert.deepEqual(result.authoring_library_observation.authoring_libraries,{animejs:descriptor});
    assert.deepEqual(result.authoring_library_observation.used_libraries,['animejs']);
    assert.ok(result.authoring_library_observation.loaded_scripts.includes('/assets/anime.esm.min.js'));
    assert.ok(result.authoring_library_observation.loaded_scripts.includes('/assets/anime_timeline.mjs'));
    assert.notEqual(result.frames[0].sha256,result.frames[12].sha256);
  } finally {rmSync(root,{recursive:true,force:true});rmSync(profile,{recursive:true,force:true});}
});
