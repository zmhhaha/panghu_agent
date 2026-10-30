import { describe,it,expect,vi,afterEach } from 'vitest';
import { adaptProject,api,ApiError,consumeEvents,IntentKeys,request,safeError } from '../api';
import { canGenerate,defaultSettings,usageCost,validateFiles,type Project } from '../domain';
const project:Project={id:'p',title:'项目',revision:2,stage:'awaiting_confirmation',settings:defaultSettings,assets:[],input_snapshot_hash:'hash',confirmed_plan_revision:'2',plan:{revision:'2',stage:'directions',input_snapshot_hash:'hash',confirmed_direction_id:'B'}};
afterEach(()=>vi.unstubAllGlobals());
describe('immutable confirmation',()=>{
 it('requires matching revision, snapshot and chosen direction',()=>{expect(canGenerate(project)).toBe(true);expect(canGenerate({...project,input_snapshot_hash:'new-upload'})).toBe(false);expect(canGenerate({...project,confirmed_plan_revision:'1'})).toBe(false);expect(canGenerate({...project,plan:{...project.plan!,confirmed_direction_id:undefined}})).toBe(false);});
 it('adapts flat backend project and actual direction options without inventing cards',()=>{const p=adaptProject({id:'p',page_count:9,language:'zh',plans:[{revision:3,phase:'plan_options',input_snapshot_hash:'abc',outline:{},design:{}}]});expect(p.settings.pages).toBe(9);expect(p.plan?.stage).toBe('directions');expect(p.plan?.directions).toBeUndefined();});
 it('sends exact snapshot, revision and explicit stage selection',async()=>{const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({id:'p',plans:[]})));vi.stubGlobal('fetch',fetcher);await api.confirm(project,{direction_id:'B'});const body=JSON.parse(fetcher.mock.calls[0][1].body);expect(body).toEqual({expected_revision:2,input_snapshot_hash:'hash',selection:{direction_id:'B'}});expect(fetcher.mock.calls[0][0]).toBe('/api/v1/projects/p/plans/2/confirm');});
 it('sends first stage free design selection with its own immutable snapshot',async()=>{const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({id:'p',plans:[]})));vi.stubGlobal('fetch',fetcher);const initial={...project,plan:{revision:'1',stage:'communication' as const,input_snapshot_hash:'initial-snapshot'}};await api.confirm(initial,{design_mode:'free_design',template_id:null});expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({expected_revision:2,input_snapshot_hash:'initial-snapshot',selection:{design_mode:'free_design',template_id:null}});});
 it('keeps explicit edit base even if latest differs',async()=>{const fetcher=vi.fn().mockResolvedValue(new Response('{"job_id":"j"}'));vi.stubGlobal('fetch',fetcher);await api.revise({...project,latest_version_id:'new-version'},'older-version',[2],'修改',{2:'缩短标题'},true);expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({expected_revision:2,base_version_id:'older-version',pages:[2],feedback:'修改\n\n第 2 页：缩短标题'});});
 it('maps stage one confirmation to options job only after actual server confirmation',async()=>{const fetcher=vi.fn().mockResolvedValue(new Response('{"job_id":"j"}'));vi.stubGlobal('fetch',fetcher);await api.plan({...project,plan:{revision:'2',stage:'communication',input_snapshot_hash:'hash',confirmed_at:'2026-10-21'}},'',true);expect(JSON.parse(fetcher.mock.calls[0][1].body).phase).toBe('plan_options');});
 it.each([
  {name:'cleared confirmation pointer',confirmed_plan_revision:null,input_snapshot_hash:'hash'},
  {name:'different confirmed revision',confirmed_plan_revision:'1',input_snapshot_hash:'hash'},
 ])('does not spend a planning request on historical confirmation with $name',async state=>{
  const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
  const initial:Project={...project,...state,plan:{revision:'2',stage:'communication',input_snapshot_hash:'hash',confirmed_at:'2026-10-21'}};
  await expect(api.plan(initial,' \n ',true)).rejects.toMatchObject({code:'INITIAL_CONFIRMATION_REQUIRED'});
  expect(fetcher).not.toHaveBeenCalled();
 });
 it('starts a new initial plan from changed inputs without requiring feedback or confirming the obsolete plan',async()=>{
  const fetcher=vi.fn().mockResolvedValue(new Response('{"job_id":"new-initial"}'));vi.stubGlobal('fetch',fetcher);
  const initial:Project={...project,input_snapshot_hash:'new-upload',confirmed_plan_revision:null,plan:{revision:'2',stage:'communication',input_snapshot_hash:'hash',confirmed_at:'2026-10-21'}};
  await api.plan(initial,'',true);
  expect(fetcher).toHaveBeenCalledOnce();expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({expected_revision:2,feedback:'',phase:'plan_initial'});
 });
 it('allows explicit feedback to create a new initial plan after the old confirmation was cleared',async()=>{
  const fetcher=vi.fn().mockResolvedValue(new Response('{"job_id":"new-initial"}'));vi.stubGlobal('fetch',fetcher);
  const initial:Project={...project,confirmed_plan_revision:null,plan:{revision:'2',stage:'communication',input_snapshot_hash:'hash',confirmed_at:'2026-10-21'}};
  await api.plan(initial,'缩小为入门介绍，保留资料引用。',true);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({expected_revision:2,feedback:'缩小为入门介绍，保留资料引用。',phase:'plan_initial'});
 });
 it('adapts the latest terminal job from the project detail without losing its authoritative cursor',()=>{
  const latest={id:'latest',job_id:'latest',project_id:'p',kind:'plan_options',status:'failed',stage:'plan_options',event_cursor:17,attempt:1,error_code:'execution_timeout',base_version_id:null};
  const result=adaptProject({id:'p',plans:[],latest_job:latest});
  expect(result.latest_job).toMatchObject({id:'latest',project_id:'p',kind:'plan_options',status:'failed',stage:'plan_options',last_event_seq:17,error_code:'execution_timeout'});
  expect(adaptProject({id:'p',plans:[],latest_job:null}).latest_job).toBeNull();
 });
});
describe('retry and safety',()=>{
 it.each(['execution_task_too_large','execution_request_invalid'])('explains pre-dispatch rejection %s without claiming an interrupted model job',code=>{
  const message=safeError(new ApiError(code));
  expect(message).toContain('尚未启动制作');expect(message).toContain('已确认方案仍保留');
  expect(message).toContain(code.toUpperCase());expect(message).not.toContain('EXECUTION_INTERRUPTED');
 });
 it('acknowledges with required title and preserves all configured project fields',async()=>{const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({id:'p',plans:[]})));vi.stubGlobal('fetch',fetcher);const configured={...project,settings:{...defaultSettings,pages:12,audience:'实际受众',purpose:'实际用途'}};await api.acknowledge(configured);expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({title:'项目',page_count:12,language:'zh',audience:'实际受众',purpose:'实际用途',style:defaultSettings.style,requirements:'',external_model_ack:true,expected_revision:2});});
 it('keeps acknowledged consent while saving settings with required title',async()=>{const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({id:'p',plans:[]})));vi.stubGlobal('fetch',fetcher);await api.update({...project,external_model_ack:true},{...defaultSettings,pages:9});expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({title:'项目',page_count:9,external_model_ack:true,expected_revision:2});});
 it('keeps stable keys for same action and changes on changed payload or successful new intent',()=>{const keys=new IntentKeys();const a=keys.key('plan',{feedback:'a'});expect(keys.key('plan',{feedback:'a'})).toBe(a);expect(keys.key('plan',{feedback:'b'})).not.toBe(a);keys.complete('plan',{feedback:'a'});expect(keys.key('plan',{feedback:'a'})).not.toBe(a);});
 it('does not expose server detail, stack, paths, or HTML errors',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:{code:'INTERNAL',message:'secret-token /work/private'}}),{status:500})));try{await request('/projects');throw new Error('expected failure');}catch(e){expect(safeError(e)).not.toContain('secret-token');expect(safeError(e)).not.toContain('/work');}});
 it('surfaces authentication and ownership errors with no project details',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('',{status:403})));await expect(request('/projects/private')).rejects.toMatchObject({code:'FORBIDDEN'});});
 it('unknown supplier cost remains unknown instead of zero',()=>{expect(usageCost({input_tokens:12,reported_cost:null,cost_status:'unknown'})).toContain('未知');expect(usageCost({reported_cost:0,cost_status:'reported',currency:'USD'})).toBe('0 USD');});
 it('bounds uploads by per-file size, file count and project total',()=>{const file=new File(['text'],'text.md');expect(validateFiles([file],[])).toBe('');expect(validateFiles([new File(['x'],'x.svg')],[])).toContain('请选择');expect(validateFiles(Array(11).fill(file),[])).toContain('10');const huge={name:'big.pdf',size:26*1024**2} as File;expect(validateFiles([huge],[])).toContain('25');});
});
describe('durable business SSE',()=>{
 it('uses actual Last-Event-ID and ignores duplicate or malformed events and raw tool payload',async()=>{
 const encoder=new TextEncoder();const stream=new ReadableStream({start(c){c.enqueue(encoder.encode('id: 7\nevent: stage.changed\ndata: {"type":"stage.changed","payload":{"stage":"running","raw":"secret"},"seq":7}\n\nid: 7\ndata: {"type":"duplicate","seq":7}\n\nid: 9\ndata: {"type":"job.succeeded","payload":{"status":"succeeded"}}\n\n'));c.close();}});const fetcher=vi.fn().mockResolvedValue(new Response(stream));vi.stubGlobal('fetch',fetcher);const event=vi.fn();await consumeEvents('j',6,new AbortController().signal,event);expect(fetcher.mock.calls[0][1].headers['Last-Event-ID']).toBe('6');expect(event).toHaveBeenCalledTimes(2);expect(event.mock.calls[0][0]).toEqual({seq:7,type:'stage.changed',payload:{stage:'running',status:undefined}});expect(event.mock.calls[1][0].seq).toBe(9);
 });
 it('cursor expiry explicitly requests snapshot refresh',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('',{status:410})));await expect(consumeEvents('j',12,new AbortController().signal,vi.fn())).rejects.toEqual(new ApiError('CURSOR_EXPIRED',410));});
});
