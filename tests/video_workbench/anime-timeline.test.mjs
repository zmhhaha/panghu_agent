import test from 'node:test';
import assert from 'node:assert/strict';
import {setTimeout as wait} from 'node:timers/promises';
import {createFrameTimeline, stagger, ANIMEJS_VERSION} from '../../video_workbench/motion_renderer/vendor/anime_timeline.mjs';
import {engine} from '../../video_workbench/motion_renderer/vendor/anime.esm.min.js';
import {readFileSync} from 'node:fs';
import {validateAuthoringLibraries} from '../../video_workbench/animation_method_host.mjs';

const clock = frame => ({global_frame:frame,time:{numerator:frame,denominator:24}});
const close = (actual, expected) => assert.ok(Math.abs(actual-expected)<1e-5, `${actual} != ${expected}`);

test('real pinned Anime.js seeks repeated, reverse and arbitrary frames without idle motion', async () => {
  assert.equal(ANIMEJS_VERSION,'4.5.0');
  const state={x:0}, timeline=createFrameTimeline({fps:24,startFrame:24,durationFrames:48});
  timeline.add(state,{x:[0,100],duration:1000},0).add(state,{x:[100,200],duration:1000},1000);
  for (const [frame,expected] of [[24,0],[36,50],[60,150],[30,25],[72,200],[24,0],[36,50],[36,50]]) {
    timeline.renderAt(clock(frame)); close(state.x,expected);
    await wait(10); close(state.x,expected);
    assert.equal(engine.reqId,0); assert.equal(engine.useDefaultMainLoop,false);
  }
  timeline.dispose(); close(state.x,0); timeline.dispose();
  assert.throws(()=>timeline.renderAt(clock(24)),/disposed/);
});

test('staggered object state works for Canvas and Three.js draw-after-seek', () => {
  const points=Array.from({length:3},()=>({x:0}));
  const timeline=createFrameTimeline({fps:24,durationFrames:48});
  timeline.add(points,{x:[0,10],duration:1000,delay:stagger(250)},0);
  timeline.renderAt(clock(18));
  points.forEach((point,i)=>close(point.x,7.5-2.5*i));
  timeline.renderAt(clock(0)); points.forEach(point=>close(point.x,0));
  timeline.dispose();
});

test('clock mismatch, selectors, callbacks, autoplay and late mutations fail closed', () => {
  const timeline=createFrameTimeline({fps:24,durationFrames:24});
  for(const values of [{onUpdate:()=>{}},{autoplay:false},{loop:true},{x:{to:1,onComplete:()=>{}}}])
    assert.throws(()=>timeline.add({x:0},values),/autonomous/);
  assert.throws(()=>timeline.add('.outside',{x:10}),/scoped_targets/);
  assert.throws(()=>timeline.add(['.outside'],{x:10}),/scoped_targets/);
  assert.throws(()=>timeline.add([[{x:0}],['.outside']],{x:10}),/scoped_targets/);
  timeline.add({x:0},{x:[0,10],duration:1000});
  for(const context of [{global_frame:1,time:{numerator:1,denominator:25}},
    {global_frame:0.5,time:{numerator:1,denominator:48}},{}])
    assert.throws(()=>timeline.renderAt(context),/clock_invalid/);
  timeline.renderAt(clock(12));
  assert.throws(()=>timeline.add({x:0},{x:1}),/already_rendered/);
  timeline.dispose();
});

test('timeline cannot silently exceed its admitted frame window', () => {
  const timeline=createFrameTimeline({fps:24,durationFrames:24});
  assert.throws(()=>timeline.add({x:0},{x:10,duration:1001}),/exceeds_frame_window/);
  timeline.dispose();
});

test('method v4 validates official bytes, license and adapter instead of broadening old requests', () => {
  const root=new URL('../../video_workbench/motion_renderer/vendor/',import.meta.url);
  const provenance=JSON.parse(readFileSync(new URL('animejs-assets.json',root)));
  const assets=Object.fromEntries(Object.entries(provenance.assets).map(([name,row])=>[name,row.sha256]));
  const library={schema:'video.animation-authoring-library.v1',library_id:'animejs',version:'4.5.0',
    license:'MIT',role:'animation-authoring',engines:['html-svg','canvas','three'],
    clock:'hyperframes-absolute-seek',assets};
  const request={schema:'video.animation-method-render-request.v4',authoring_libraries:{animejs:library},assets_sha256:assets};
  const resources=new Map(Object.keys(assets).map(name=>['/assets/'+name,readFileSync(new URL(name,root))]));
  assert.deepEqual(validateAuthoringLibraries(request,resources),request.authoring_libraries);
  assert.throws(()=>validateAuthoringLibraries({...request,schema:'video.animation-method-render-request.v3'},resources),/library_invalid/);
  const changed=new Map(resources);changed.set('/assets/anime_timeline.mjs',Buffer.from('changed'));
  assert.throws(()=>validateAuthoringLibraries(request,changed),/asset_changed/);
  const partial=new Map(resources);partial.delete('/assets/animejs.LICENSE.txt');
  assert.throws(()=>validateAuthoringLibraries(request,partial),/asset_changed/);
});
