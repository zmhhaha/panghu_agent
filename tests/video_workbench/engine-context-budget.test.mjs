import test from 'node:test';
import assert from 'node:assert/strict';
import {createContext,runInContext} from 'node:vm';
import {engineSpec,installEngineGuard,verifyPageEngine}
 from '../../video_workbench/motion_renderer/engine_runtime.mjs';

// Execute the actual browser init script against a native-context identity
// fixture. No animation source, browser assets or production storage is used.
async function guardedRealm(engine){
 const realm=createContext({});
 runInContext(`
  class Node {get isConnected(){return false;}}
  class Element extends Node {getBoundingClientRect(){return {width:1,height:1};}}
  class WebGL2RenderingContext {
   getParameter(){return 'Google SwiftShader';}
   getExtension(){return null;}
   isContextLost(){return false;}
   getError(){return 0;}
  }
  const sizes=new WeakMap(),nativeContexts=new WeakMap();
  class HTMLCanvasElement extends Element {
   constructor(){super();sizes.set(this,{width:1,height:1});nativeContexts.set(this,new Map());}
   get width(){return sizes.get(this).width;}
   get height(){return sizes.get(this).height;}
   getContext(kind){
    const contexts=nativeContexts.get(this);
    if(!contexts.has(kind))contexts.set(kind,kind==='webgl2'?new WebGL2RenderingContext():{});
    return contexts.get(kind);
   }
  }
  const window=globalThis;
  window.getComputedStyle=()=>({display:'block',visibility:'visible',opacity:'1'});
 `,realm);
 await installEngineGuard({addInitScript:async(callback,spec)=>{
  runInContext(`(${callback.toString()})(${JSON.stringify(spec)})`,realm);
 }},engine);
 return realm;
}

function evidencePage(observation){
 return {
  on(){},mainFrame(){return this;},
  context(){return {newCDPSession:async()=>({
   async send(method){
    if(method==='Page.getFrameTree')return {frameTree:{frame:{id:'fixture'}}};
    if(method==='Page.createIsolatedWorld')return {executionContextId:1};
    if(method==='Runtime.evaluate')return {result:{value:{forbidden:false,running:false}}};
    throw Error('unexpected CDP method');
   }
  })};},
  async evaluate(){return observation;}
 };
}

for(const engine of ['canvas','three']){
 test(`${engine}: 500 contexts pass, repeated lookups do not consume slots, 501 fails`,async()=>{
  assert.equal(engineSpec(engine).max_contexts,500);
  const realm=await guardedRealm(engine);
  // Three's WebGL context and its 2D resources share the same global count.
  realm.firstKind=engine==='three'?'webgl2':'2d';
  runInContext(`
   const retained=new HTMLCanvasElement();
   const first=retained.getContext(firstKind);
   for(let i=0;i<1000;i++){
    if(retained.getContext(firstKind)!==first)throw Error('context identity changed');
   }
   for(let i=1;i<500;i++)new HTMLCanvasElement().getContext('2d');
  `,realm);
  const observation=runInContext('window.__trustedAnimationEngineEvidence()',realm);
  assert.equal(observation.contexts.length,500);
  assert.equal(observation.contexts[0].kind,realm.firstKind);
  assert.ok(observation.contexts.every(row=>row.connected===false));
  assert.ok(observation.contexts.slice(1).every(row=>row.kind==='2d'));
  await verifyPageEngine(evidencePage(observation),engine,{requireContext:false});
  assert.throws(()=>runInContext("new HTMLCanvasElement().getContext('2d')",realm),
   /creative_context_budget/);
  assert.equal(runInContext('window.__trustedAnimationEngineEvidence().contexts.length',realm),500);
  // Independent receipt validation uses the same bound as context creation.
  await assert.rejects(verifyPageEngine(evidencePage({...observation,
   contexts:[...observation.contexts,observation.contexts[0]]}),engine,{requireContext:false}),
  /creative_context_budget/);
 });
}
