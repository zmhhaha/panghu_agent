import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createProductionRunRequest, createProductionRunsPanel, productionRunUnresolved,
  currentProductionRun, productionRunDraftReady, productionRunActualSeconds } from '../../video_workbench/web/production-runs.mjs';

const html = await readFile(new URL('../../video_workbench/web/index.html', import.meta.url), 'utf8');
class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.listeners = new Map(); this.content = ''; this.value = ''; this.hidden = false; this.disabled = false; this.paused = true; this.pauses = 0; }
  set textContent(value) { this.content = String(value); this.children = []; }
  get textContent() { return [this.content, ...this.children.map(node => node.textContent)].join('\n'); }
  set innerHTML(value) { throw new Error(`Executable markup forbidden: ${value}`); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this.content = ''; }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  pause() { this.paused = true; this.pauses++; }
  addEventListener(name, callback) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(callback); }
  async emit(name) { for (const callback of this.listeners.get(name) ?? []) await callback({ target:this, preventDefault() {} }); }
}
const project = (id = 'p1') => ({ id, revision:4, script:'保留的原始总文稿。', speech_settings:{voice:'zm_yunjian'},
  joint_plan:{version_id:'approved-plan'}, scenes:[{id:`${id}-scene`,title:'示例段落',narration:'旁白',head_audio_version_id:null,head_version_id:null}] });
const run = (fields = {}) => ({id:'run-1',project_id:'p1',joint_plan_version_id:'approved-plan',request_key:'production-run:test-key',
  status:'queued',phase:'audio',voice:'zm_yunjian',items:[{scene_id:'p1-scene',title:'示例段落',status:'queued'}],
  review_status:'not_reviewed',selection:'draft_only',created_at:1,...fields});
function harness() {
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Element()]));
  const $ = id => { assert.ok(nodes.has(id),id); return nodes.get(id); };
  const state = {context:{project:project(),epoch:1,enabled:true,unsaved:false,projectBusy:false,settings:{speech_voices:[{id:'zm_yunjian',name:'云健'}]}},
    calls:[],errors:[],successes:[],data:{runs:[]},refreshes:0,transport:null,refreshAction:null,changes:0,plays:0};
  const panel = createProductionRunsPanel({document:{getElementById:$,createElement:tag=>new Element(tag)},getContext:()=>state.context,
    request:async(path,options={})=>{state.calls.push({path,options});return state.transport?state.transport(path,options):structuredClone(state.data);},
    refresh:async()=>{state.refreshes++;await state.refreshAction?.();},onError:error=>state.errors.push(error),onSuccess:value=>state.successes.push(value),
    onChange:()=>state.changes++,onMediaPlay:()=>state.plays++,makeRequestKey:()=> 'production-run:test-key'});
  return {state,panel,$,load:()=>panel.load(state.context.project.id,state.context.epoch)};
}
const posts = h => h.state.calls.filter(row=>row.options.method==='POST');

test('whole-video request requires adopted plan and saved inputs, sends no per-scene approvals',()=>{
  const p=project(), before=structuredClone(p);
  assert.deepEqual(createProductionRunRequest(p),{path:'/api/projects/p1/production-runs',body:{expected_revision:4,joint_plan_version_id:'approved-plan'}});
  assert.deepEqual(p,before);
  assert.throws(()=>createProductionRunRequest({...p,joint_plan:null}),/采用/);
  assert.throws(()=>createProductionRunRequest(p,{unsaved:true}),/保存/);
  assert.throws(()=>createProductionRunRequest(p,{busy:true}),/任务/);
});

test('initial reads never produce media and missing adoption has a clear prerequisite',async()=>{
  const h=harness();h.state.context.project.joint_plan=null;await h.load();
  assert.equal(h.$('start-production-run').disabled,true);assert.match(h.$('production-run-note').textContent,/先.*采用/);
  assert.equal(posts(h).length,0);
  h.state.context.project.joint_plan={version_id:'approved-plan'};h.panel.render();
  assert.equal(h.$('start-production-run').disabled,false);
});

test('peer tasks and unsaved edits block programmatic start as well as the main button',async()=>{
  const h=harness();await h.load();h.state.context.projectBusy=true;h.panel.render();
  assert.equal(h.$('start-production-run').disabled,true);await h.panel.start();
  assert.equal(posts(h).length,0);assert.match(h.state.errors.at(-1).message,/任务/);
  h.state.context.projectBusy=false;h.state.context.unsaved=true;h.panel.render();
  assert.equal(h.$('start-production-run').disabled,true);await h.panel.start();
  assert.equal(posts(h).length,0);assert.match(h.state.errors.at(-1).message,/保存/);
});

test('one click submits one bounded request with dedicated idempotency key then GET refreshes',async()=>{
  const h=harness();await h.load();h.state.refreshAction=h.load;
  h.state.transport=(path,options)=>{if(options.method==='POST'){h.state.data={runs:[run()]};return run();}return h.state.data;};
  await h.panel.start();await h.panel.start();
  assert.equal(posts(h).length,1);assert.deepEqual(posts(h)[0],{path:'/api/projects/p1/production-runs',options:{method:'POST',body:{expected_revision:4,joint_plan_version_id:'approved-plan'},idempotencyKey:'production-run:test-key'}});
  assert.equal(h.panel.hasUnresolvedOperation(),true);assert.match(h.$('production-run-voice').textContent,/云健/);
  assert.doesNotMatch(h.state.calls.map(row=>row.path).join('\n'),/adopt|audio\/generate|motion-renders|timing/);
});

test('lost start response recovers exact request key using GET without repeating POST, including a fresh page',async()=>{
  const h=harness();await h.load();h.state.refreshAction=h.load;
  h.state.transport=(path,options)=>{if(options.method==='POST'){h.state.data={runs:[run()]};throw new TypeError('lost response');}return h.state.data;};
  await h.panel.start();await h.$('reload-production-runs').emit('click');await h.panel.start();
  assert.equal(posts(h).length,1);assert.equal(h.panel.hasUnresolvedOperation(),true);
  assert.match(h.$('production-run-status').textContent,/等待制作/);
  const reloaded=harness();reloaded.state.data=h.state.data;await reloaded.load();
  assert.equal(reloaded.$('start-production-run').disabled,true);assert.equal(posts(reloaded).length,0);
});

test('an unknown request absent from GET remains blocked and does not borrow an older result',async()=>{
  const h=harness();h.state.data={runs:[run({id:'old',status:'completed',request_key:'old-key',draft_render_id:'old-render'})]};await h.load();
  h.state.refreshAction=h.load;h.state.transport=(path,options)=>{if(options.method==='POST')throw new TypeError('lost');return h.state.data;};
  await h.panel.start();await h.panel.start();await h.$('reload-production-runs').emit('click');
  assert.equal(posts(h).length,1);assert.match(h.$('production-run-note').textContent,/不会自动重复提交/);
  assert.equal(h.$('start-production-run').disabled,true);
});

test('a late list read cannot erase the newly acknowledged run',async()=>{
  const h=harness();await h.load();let resolve;
  h.state.transport=(path,options)=>options.method==='POST'?run():new Promise(done=>{resolve=done;});
  const old=h.load();await h.panel.start();resolve({runs:[]});await old;
  assert.equal(h.panel.hasUnresolvedOperation(),true);assert.match(h.$('production-run-status').textContent,/等待制作/);
});

test('switching project during a submit never renders the original result in the new project',async()=>{
  const h=harness();await h.load();let resolve;
  h.state.transport=(path,options)=>options.method==='POST'?new Promise(done=>{resolve=done;}):{runs:[]};
  const starting=h.panel.start();h.state.context={...h.state.context,project:project('p2'),epoch:2};await h.load();
  resolve(run());await starting;
  assert.equal(h.$('production-run-result').hidden,true);assert.equal(h.panel.hasUnresolvedOperation('p2'),false);
  assert.equal(h.panel.hasUnresolvedOperation('p1'),true);
});

test('completed result is one draft player with the real media route; playback never approves or posts',async()=>{
  const h=harness();h.state.data={runs:[run({status:'completed',phase:'completed',draft_render_id:'render/a?b',
    items:[{scene_id:'p1-scene',title:'<script>inert</script>',audio_seconds:8.125,audio_version_id:'a',motion_version_id:'m'}]})]};
  await h.load();assert.equal(h.$('production-run-player').hidden,false);
  assert.equal(h.$('production-run-video').src,'/api/renders/render%2Fa%3Fb/video');
  assert.equal(h.$('download-production-run').href,h.$('production-run-video').src);
  assert.match(h.$('production-run-review-note').textContent,/不代表.*试听、采用或核准/);
  assert.match(h.$('production-run-items').textContent,/实测录音 8.13 秒/);assert.match(h.$('production-run-items').textContent,/<script>inert<\/script>/);
  const pauses=h.$('production-run-video').pauses;h.$('production-run-video').paused=false;h.panel.render();
  assert.equal(h.$('production-run-video').pauses,pauses,'polling must not reset active playback');
  await h.$('production-run-video').emit('play');assert.equal(h.state.plays,1);assert.equal(posts(h).length,0);
  assert.equal(h.state.context.project.scenes[0].head_audio_version_id,null);
});

test('cancel is explicit, sent once, and refresh cannot restart cancelled or failed work',async()=>{
  const h=harness();h.state.data={runs:[run({status:'running',phase:'motion'})]};await h.load();h.state.refreshAction=h.load;
  h.state.transport=(path,options)=>{if(options.method==='POST'){h.state.data={runs:[run({status:'cancelled'})]};return h.state.data.runs[0];}return h.state.data;};
  await h.panel.cancel();await h.panel.cancel();await h.$('reload-production-runs').emit('click');
  assert.equal(posts(h).length,1);assert.equal(posts(h)[0].path,'/api/production-runs/run-1/cancel');
  assert.equal(h.panel.hasUnresolvedOperation(),false);
});

test('unknown upstream stop remains fenced even with a failed status and read errors disable new requests',async()=>{
  const h=harness();h.state.data={runs:[run({status:'failed',error:'speech_submission_uncertain'})]};await h.load();
  assert.equal(productionRunUnresolved(h.state.data.runs[0]),true);assert.equal(h.$('start-production-run').disabled,true);
  assert.match(h.$('production-run-problem').textContent,/不要重复生成/);
  h.state.data={runs:[]};await h.load();h.state.transport=()=>{throw new TypeError('offline');};await h.load();
  assert.equal(h.$('start-production-run').disabled,true);await h.panel.start();assert.equal(posts(h).length,0);
});

test('new reads use project revision and the feature gate allows historical review only',async()=>{
  const h=harness();await h.load();h.state.context.project.revision++;h.panel.render();
  assert.equal(h.$('start-production-run').disabled,true);
  h.state.data={runs:[run({status:'completed',phase:'completed',draft_render_id:'render'})]};h.state.context.enabled=false;await h.load();
  assert.equal(h.$('production-runs-panel').hidden,false);assert.equal(h.$('start-production-run').disabled,true);
  assert.equal(h.$('production-run-player').hidden,false);assert.equal(posts(h).length,0);
});

test('step progress uses its own denominator and terminal draining or blocked runs still fence new work',async()=>{
  const h=harness();h.state.data={runs:[run({status:'cancelled',phase:'audio',progress:{done:2,total:7},
    blocked_reason:'production_run_cancellation_pending',draining:true})]};await h.load();
  assert.match(h.$('production-run-status').textContent,/2\/7 步/);
  assert.doesNotMatch(h.$('production-run-status').textContent,/2\/1 段/);
  assert.equal(h.$('start-production-run').disabled,true);assert.equal(h.panel.hasUnresolvedOperation(),true);
  assert.match(h.$('production-run-problem').textContent,/等待上游停止/);
  assert.equal(productionRunUnresolved(run({status:'cancelled',draining:true})),true);
  h.state.data={runs:[run({status:'cancelled',progress:{done:2,total:7},blocked_reason:null,draining:false})]};await h.load();
  assert.equal(h.panel.hasUnresolvedOperation(),false);assert.equal(h.$('start-production-run').disabled,false);
});

const readyRun = (fields = {}) => run({status:'completed',phase:'completed',current:true,draft_render_id:'draft-render',duration_seconds:9,
  items:[{scene_id:'p1-scene',title:'示例段落',audio_seconds:8.25,duration_seconds:9,
    audio_version_id:'exact-audio',motion_version_id:'exact-motion',candidate_current:true}],...fields});

test('current projection requires server currentness, exact plan and ordered scenes, and never falls back past a failed latest run',()=>{
  const p=project(), completed=readyRun(), before=structuredClone([p,completed]);
  assert.equal(currentProductionRun(p,[completed]).id,completed.id);
  for(const invalid of [readyRun({current:false}),readyRun({current:undefined}),readyRun({project_id:'other'}),
    readyRun({joint_plan_version_id:'old-plan'}),readyRun({items:[]}),readyRun({items:[{scene_id:'other'}]}),
    readyRun({draining:true}),readyRun({error:'uncertain'})]) assert.equal(currentProductionRun(p,[invalid]),null);
  for(const status of ['failed','cancelled','stale']) assert.equal(currentProductionRun(p,[completed,
    readyRun({id:'newer',created_at:2,status})]),null);
  const two={...p,scenes:[...p.scenes,{id:'second'}]};
  assert.equal(currentProductionRun(two,[readyRun({items:[{scene_id:'second'},{scene_id:'p1-scene'}]})]),null);
  assert.deepEqual([p,completed],before,'Reading presentation state never adopts or rewrites project/run state');
});

test('draft readiness requires exact candidate validation and encoded actual duration never comes from the audio schedule',()=>{
  const r=readyRun();
  assert.equal(productionRunDraftReady(r),true);
  assert.equal(productionRunActualSeconds(r,[]),null);
  assert.equal(productionRunActualSeconds(r,[{id:'draft-render',status:'completed',selection:'draft_only',content_state:'current',actual_duration_seconds:9.5}]),9.5);
  for(const render of [{id:'other',status:'completed',selection:'draft_only',content_state:'current',actual_duration_seconds:9.5},
    {id:'draft-render',status:'completed',selection:'draft_only',content_state:'changed',actual_duration_seconds:9.5},
    {id:'draft-render',status:'running',selection:'draft_only',content_state:'current',actual_duration_seconds:9.5}])
    assert.equal(productionRunActualSeconds(r,[render]),null);
  assert.equal(productionRunDraftReady(readyRun({items:[{...r.items[0],candidate_current:false}]})),false);
  assert.equal(productionRunDraftReady(readyRun({current:false})),false);
});

test('completed status is not duplicated, schedule and encoded duration stay distinct, and history selection does not project old candidates',async()=>{
  const h=harness();
  h.state.data={runs:[readyRun({id:'latest',created_at:2}),readyRun({id:'old',current:false,draft_render_id:'old-render',created_at:1})]};
  await h.load();
  assert.equal(h.$('production-run-status').textContent.match(/草稿待审阅/g)?.length,1);
  assert.match(h.$('production-run-review-note').textContent,/按录音与留白排程 9 秒.*视频实测时长待读取/);
  h.state.context.renders=[{id:'draft-render',status:'completed',actual_duration_seconds:9.5}]; h.panel.render();
  assert.match(h.$('production-run-review-note').textContent,/视频实际 9.5 秒/);
  h.$('production-run-select').value='old';await h.$('production-run-select').emit('change');
  assert.equal(h.panel.currentRun(h.state.context.project).id,'latest');
  assert.equal(h.$('production-run-video').src,'/api/renders/old-render/video');
  assert.equal(h.$('production-run-player-heading').textContent,'历史整片预览');
  assert.match(h.$('production-run-review-note').textContent,/项目内容已变化/);
  assert.deepEqual([...h.panel.managedRenderIds('p1')].sort(),['draft-render','old-render']);
  assert.equal(posts(h).length,0);
});

test('revision changes, failed reads and late project reads cannot keep a current-candidate projection',async()=>{
  const h=harness();h.state.data={runs:[readyRun()]};await h.load();
  assert.ok(h.panel.currentRun(h.state.context.project));
  h.state.context.project.revision++;h.panel.render();
  assert.equal(h.panel.currentRun(h.state.context.project),null);
  assert.match(h.$('production-run-status').textContent,/内容匹配状态待读取/);
  h.state.transport=()=>{throw new TypeError('offline');};await h.load();
  assert.equal(h.panel.currentRun(h.state.context.project),null);
  assert.match(h.$('production-run-review-note').textContent,/匹配状态尚未确认/);
  let resolve;h.state.transport=()=>new Promise(done=>{resolve=done;});const old=h.load();
  h.state.context={...h.state.context,project:project('p2'),epoch:2};h.panel.render();
  resolve({runs:[readyRun()]});await old;
  assert.equal(h.panel.currentRun(h.state.context.project),null);
  assert.equal(h.$('production-run-player').hidden,true);
  assert.equal(h.$('production-run-video').src,undefined);
  assert.equal(h.$('download-production-run').href,undefined);
  assert.equal(posts(h).length,0);
});

test('terminal partial runs describe unfinished work without claiming operations are still running',async()=>{
  const h=harness();h.state.data={runs:[run({status:'failed',phase:'motion',error:'child_failed',items:[{
    scene_id:'p1-scene',audio_operation_id:'audio-op',motion_operation_id:'motion-op'}]})]};await h.load();
  assert.match(h.$('production-run-items').textContent,/配音尚未完成.*动画尚未完成/);
  assert.doesNotMatch(h.$('production-run-items').textContent,/正在制作/);
  assert.equal(h.panel.currentRun(h.state.context.project),null);
});
