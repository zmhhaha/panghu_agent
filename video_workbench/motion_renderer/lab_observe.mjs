// Developer capture entry. It reuses the real native HyperFrames observer;
// production run.mjs/verifyInput admission and old observation contracts stay unchanged.
import {readFileSync, writeFileSync, mkdirSync, readdirSync, copyFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {observeProject} from './observe.mjs';
import {collectRuntime} from './runtime.mjs';
import {readRegular} from './contract.mjs';

const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const canonical = value => JSON.stringify(value, (_key, child) => child && typeof child === 'object'
  && !Array.isArray(child) ? Object.fromEntries(Object.entries(child).sort(([a], [b]) => a.localeCompare(b))) : child);
const requiredFiles = ['alignment.json','audio.wav','beats.json','index.html','manifest.json','plan.json','timeline.json'];

export async function captureLab({root='/work',requestSha256,chromium,
  dist='/opt/renderer/node_modules/hyperframes/dist',assetRoot='/opt/assets'}={}) {
  root = resolve(root);
  if(!/^[0-9a-f]{64}$/.test(requestSha256 || '')) throw Error('capture_request_digest_required');
  const requestRaw = readRegular(join(root, 'capture-request.json'), 64*1024);
  if(hash(requestRaw)!==requestSha256) throw Error('capture_request_digest_mismatch');
  const request = JSON.parse(requestRaw);
  if(request.schema!=='video.animation-lab-capture.v1' || !/^[0-9a-f]{64}$/.test(request.capture_identity || '')
    || request.max_frames!==240 || request.max_evidence_bytes!==96*1024*1024)
    throw Error('capture_request_invalid');
  const here=dirname(fileURLToPath(import.meta.url));
  if(hash(readFileSync(fileURLToPath(import.meta.url)))!==request.capture_adapter_sha256
    || hash(readFileSync(join(here,'observe.mjs')))!==request.observer_sha256
    || hash(readFileSync(join(here,'runtime.mjs')))!==request.runtime_collector_sha256)
    throw Error('capture_adapter_mismatch');
  if(canonical(Object.keys(request.files).sort())!==canonical(requiredFiles)) throw Error('capture_files_mismatch');
  for(const [name,digest] of Object.entries(request.files)) {
    if(hash(readRegular(join(root,name),8*1024*1024))!==digest) throw Error('capture_input_digest_mismatch:'+name);
  }
  const actual=collectRuntime();
  const {image_digest,...expected}=request.runtime;
  if(canonical(actual)!==canonical(expected)) throw Error('capture_runtime_mismatch');
  if(!/^sha256:[0-9a-f]{64}$/.test(image_digest || '')
    || process.env.ANIMATION_LAB_IMAGE_DIGEST!==image_digest) throw Error('capture_image_binding_mismatch');
  const timeline=JSON.parse(readRegular(join(root,'timeline.json')));
  if(timeline.compiler!==request.compiler || timeline.duration_seconds!==request.duration_seconds
    || !Array.isArray(timeline.actions) || timeline.actions.length>64
    || !Number.isFinite(timeline.duration_seconds) || timeline.duration_seconds<=0 || timeline.duration_seconds>60)
    throw Error('capture_timeline_mismatch');
  if((timeline.compiler==='motion-process-compiler-1')!==request.historical_local_experiment)
    throw Error('capture_historical_scope_mismatch');
  if(hash(readRegular(join(root,'plan.json')))!==timeline.plan_sha256
    || hash(readRegular(join(root,'audio.wav'),8*1024*1024))!==timeline.audio_sha256)
    throw Error('capture_plan_audio_binding_mismatch');
  const engine=['canvas','three','svg-effects'].includes(timeline.engine);
  const manifest=JSON.parse(readRegular(join(root,'manifest.json')));
  if(engine) {
    if(hash(readFileSync(join(here,'engine_observe.mjs')))!==request.engine_observer_sha256)
      throw Error('capture_engine_observer_mismatch');
    if(canonical(request.engine_assets)!==canonical(manifest.engine_assets||{}))
      throw Error('capture_engine_asset_binding_mismatch');
  }
  mkdirSync(join(root,'assets'));
  for(const [name,key] of [['gsap.min.js','gsap_sha256'],['NotoSansCJK-Regular.ttc','font_sha256']]) {
    const source=join(assetRoot,name);
    if(hash(readRegular(source,32*1024*1024))!==actual[key]) throw Error('capture_asset_mismatch');
    copyFileSync(source,join(root,'assets',name));
  }
  if(engine)for(const [name,digest] of Object.entries(request.engine_assets)) {
    if(!/^three\.(module|core)\.min\.js$/.test(name)
      ||hash(readRegular(join(here,name),8*1024*1024))!==digest)throw Error('capture_engine_asset_mismatch');
    copyFileSync(join(here,name),join(root,'assets',name));
  }
  // This separate observer does not grant historical local compiler1 production
  // admission. The coordinator verifies trusted compiler bytes before packaging.
  if(engine) {
    const {observeEngineProject}=await import('./engine_observe.mjs');
    await observeEngineProject(chromium,{root,dist});
  } else await observeProject(chromium,{root,dist});
  const frames=[];
  let total=0;
  for(const name of readdirSync(join(root,'observations')).sort()) {
    if(!/^[a-z0-9][a-z0-9-]{0,100}\.jpg$/.test(name)) throw Error('capture_frame_name_invalid');
    const raw=readRegular(join(root,'observations',name),4*1024*1024);
    total+=raw.length;
    if(total>request.max_evidence_bytes || frames.length>=request.max_frames) throw Error('capture_frame_budget');
    frames.push({name,sha256:hash(raw),bytes:raw.length});
  }
  const observed=readRegular(join(root,'observations.json'),8*1024*1024);
  const receipt={schema:'video.animation-lab-capture-receipt.v1',capture_identity:request.capture_identity,
    request_sha256:hash(requestRaw),runtime:{...actual,image_digest},
    image_digest_source:'coordinator_pinned_not_self_measured',
    capture_adapter_sha256:request.capture_adapter_sha256,observer_sha256:request.observer_sha256,
    runtime_collector_sha256:request.runtime_collector_sha256,
    observations_sha256:hash(observed),frame_count:frames.length,frames,
    agent_calls:0,tts_calls:0,production_writes:0,output:'sampled_browser_observations_not_mp4'};
  if(engine)receipt.engine_assets=request.engine_assets;
  writeFileSync(join(root,'capture-receipt.json'),JSON.stringify(receipt,null,2));
  return receipt;
}

if(process.argv[1]===fileURLToPath(import.meta.url)) {
  const args=process.argv.slice(2);
  if(args.length!==4 || args[0]!=='--root' || args[2]!=='--request-sha256')
    throw Error('usage: lab_observe.mjs --root DIR --request-sha256 SHA');
  const require=createRequire('/opt/renderer/package.json');
  const {chromium}=require('playwright-core');
  await captureLab({root:args[1],requestSha256:args[3],chromium});
}
