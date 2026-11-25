// Offline clock/control-flow fixtures; these do not claim real rendering.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,existsSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve,dirname,basename} from 'node:path';
import {createMethodRenderBudget,METHOD_STAGE_TIMEOUTS,captureMethodFrames}
 from '../../video_workbench/video_learning_host.mjs';

test('stage windows allow bounded software rendering and shrink across one total deadline',()=>{
 let clock=50;
 const budget=createMethodRenderBudget({deadlineAtMs:2000000,wallNow:()=>1000000,now:()=>clock});
 assert.equal(budget.timeout(METHOD_STAGE_TIMEOUTS.project_ready),180000);
 assert.equal(budget.timeout(METHOD_STAGE_TIMEOUTS.screenshot),120000);
 assert.equal(budget.timeout(METHOD_STAGE_TIMEOUTS.seek),120000);
 clock+=999975;
 assert.equal(budget.timeout(METHOD_STAGE_TIMEOUTS.project_ready),25);
 assert.equal(budget.timeout(METHOD_STAGE_TIMEOUTS.screenshot),25,'a new frame must not reset the job allowance');
 clock+=25;
 assert.throws(()=>budget.timeout(120000),/method_render_deadline_exceeded/);
 assert.equal(budget.snapshot().elapsed_ms,1000000);
 assert.throws(()=>createMethodRenderBudget({deadlineAtMs:Infinity}),/deadline_invalid/);
 assert.throws(()=>budget.timeout(0),/stage_timeout_invalid/);
});

test('a stuck stage stops once and preserves the active substage and elapsed time',async()=>{
 const budget=createMethodRenderBudget();let calls=0;
 await assert.rejects(budget.run('project_ready',15,()=>{
  calls++;budget.substage('scene_prepare');return new Promise(()=>{});
 }),/method_project_ready_timeout/);
 const diagnostic=budget.snapshot();
 assert.equal(calls,1);
 assert.equal(diagnostic.stage,'project_ready');assert.equal(diagnostic.substage,'scene_prepare');
 assert.ok(diagnostic.stage_elapsed_ms>=10);
 assert.equal(diagnostic.history.at(-1).status,'failed');
});

function fixture(root,budget,{screenshot}={}){
 const listeners=new Map(),screenshots=[];let launchCalls=0;
 const page={setDefaultTimeout(){},on(name,callback){listeners.set(name,callback);},
  async goto(){},async evaluate(){},async screenshot(options){
   screenshots.push(options);return await screenshot?.(listeners);
  }};
 const session={on(){},async send(method){
  if(method==='Page.getFrameTree')return{frameTree:{frame:{id:'fixture-frame'}}};
  if(method==='Page.createIsolatedWorld')return{executionContextId:1};
  if(method==='Runtime.evaluate')return{result:{value:0}};
  return{};
 }};
 const context={async route(){},async newPage(){return page;},async newCDPSession(){return session;}};
 const chromium={async launch(){launchCalls++;return{async newContext(){return context;},async close(){}};}};
 const request={schema:'video.animation-method-render-request.v1',
  constraints:{width:320,height:180,fps:24,duration_seconds:1},max_evidence_bytes:4096};
 return{capture:()=>captureMethodFrames({chromium,request,resources:new Map(),root,budget}),
  screenshots,launchCalls:()=>launchCalls};
}

function clean(root){
 assert.equal(dirname(resolve(root)),resolve(tmpdir()));
 assert.ok(basename(root).startsWith('learning-host-deadline-'));
 rmSync(root,{recursive:true,force:true});
}

test('first screenshot uses remaining deadline once and preserves page errors and crash on timeout',async()=>{
 const root=mkdtempSync(join(tmpdir(),'learning-host-deadline-'));
 const budget=createMethodRenderBudget({deadlineAtMs:Date.now()+150});
 const f=fixture(root,budget,{screenshot:async listeners=>{
  listeners.get('pageerror')(Error('source initialization failed'));
  listeners.get('crash')();return await new Promise(()=>{});
 }});
 try{
  await assert.rejects(f.capture(),error=>{
   assert.match(String(error),/method_render_deadline_exceeded|method_screenshot_timeout/);
   const diagnostic=error.methodRenderDiagnostics;
   assert.equal(diagnostic.stage,'screenshot');assert.equal(diagnostic.frame.index,0);
   assert.equal(diagnostic.crashed,true);
   assert.ok(diagnostic.page_errors.some(value=>value.includes('source initialization failed')));
   assert.ok(diagnostic.page_errors.includes('method_page_crashed'));
   return true;
  });
  assert.equal(f.screenshots.length,1,'the host must not retry the screenshot');
  assert.ok(f.screenshots[0].timeout>0&&f.screenshots[0].timeout<=150);
  assert.equal(existsSync(join(root,'frames','frame-000000.jpg')),false);
 }finally{clean(root);}
});

test('expired overall budget prevents browser launch',async()=>{
 const root=mkdtempSync(join(tmpdir(),'learning-host-deadline-'));
 const budget=createMethodRenderBudget({deadlineAtMs:Date.now()-1});
 const f=fixture(root,budget);
 try{
  await assert.rejects(f.capture(),/method_render_deadline_exceeded/);
  assert.equal(f.launchCalls(),0);assert.equal(f.screenshots.length,0);
 }finally{clean(root);}
});
