// Exercise the real observer orchestration with an explicitly synthetic page.
// This tests which measured samples are persisted; it makes no browser claim.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtempSync,readFileSync,writeFileSync,mkdirSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {observeProject} from '../../video_workbench/motion_renderer/observe.mjs';

const hash=raw=>createHash('sha256').update(raw).digest('hex');

async function collect(compiler,historicalProcessEvidence){
 const root=mkdtempSync(join(tmpdir(),'lab-process-observer-'));
 const dist=join(root,'runtime');mkdirSync(dist);
 const runtime=Buffer.from('// synthetic runtime, not executed');
 writeFileSync(join(dist,'hyperframe.runtime.iife.js'),runtime);
 writeFileSync(join(dist,'hyperframe.manifest.json'),JSON.stringify({sha256:hash(runtime)}));
 writeFileSync(join(root,'timeline.json'),JSON.stringify({compiler,duration_seconds:4,
  actions:[{ordinal:0,start:1,duration:.5}]}));
 let current=0;
 const previousWindow=globalThis.window,previousDocument=globalThis.document;
 globalThis.window={__videoMotion:{ready:Promise.resolve(),inspect:()=>({token:{x:current}}),
  inspectRelations:()=>({path:{opacity:current}}),inspectLayout:()=>({fontReady:true})},
  __player:{renderSeek:time=>{current=time;},getTime:()=>current}};
 globalThis.document={fonts:{ready:Promise.resolve()}};
 const page={goto:async()=>{},evaluate:async(fn,arg)=>await fn(arg),addScriptTag:async()=>{},
  waitForFunction:async()=>{},screenshot:async({path})=>{
   const raw=Buffer.from('synthetic screenshot at '+current);writeFileSync(path,raw);return raw;
  }};
 const chromium={launch:async()=>({newContext:async()=>({newPage:async()=>page}),close:async()=>{}})};
 try{
  await observeProject(chromium,{root,dist,...(historicalProcessEvidence===undefined?{}:{historicalProcessEvidence})});
  const result=JSON.parse(readFileSync(join(root,'observations.json')));
  return result;
 }finally{
  if(previousWindow===undefined)delete globalThis.window;else globalThis.window=previousWindow;
  if(previousDocument===undefined)delete globalThis.document;else globalThis.document=previousDocument;
  rmSync(root,{recursive:true,force:true});
 }
}

test('historical process1 Lab explicitly retains both real captured zero-time states',async()=>{
 const observed=await collect('motion-process-compiler-1',true);
 assert.equal(observed.windows.length,1);
 for(const name of ['initial','repeated_initial']){
  assert.equal(observed[name].time,0);assert.equal(observed[name].actual_time,0);
  assert.deepEqual(observed[name].elements,{token:{x:0}});
  assert.equal(observed[name].frame_sha256,hash(Buffer.from('synthetic screenshot at 0')));
 }
 assert.deepEqual(observed.initial,observed.repeated_initial);
 assert.equal(observed.reverse_seek.repeat_state_equal,true);
 assert.equal(observed.reverse_seek.first_sha256,observed.initial.frame_sha256);
 assert.equal(observed.reverse_seek.repeated_first_sha256,observed.repeated_initial.frame_sha256);
 assert.equal(observed.timeline_sha256.length,64);
});

test('production observer default preserves process1 legacy output and process2 complete output',async()=>{
 const historical=await collect('motion-process-compiler-1');
 assert.equal('initial' in historical,false);assert.equal('repeated_initial' in historical,false);
 const current=await collect('motion-process-compiler-2');
 assert.equal(current.initial.time,0);assert.equal(current.repeated_initial.time,0);
});

test('historical evidence opt-in accepts only an explicit boolean for process1',async()=>{
 for(const [compiler,value] of [['motion-process-compiler-2',true],['motion-compiler-6',true],
   ['motion-process-compiler-1','true'],['motion-process-compiler-1',1]]){
  await assert.rejects(collect(compiler,value),/historical_process_evidence_scope_mismatch/);
 }
});
