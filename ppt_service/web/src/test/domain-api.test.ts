import { describe,it,expect,vi,afterEach } from 'vitest';
import { adaptJob,adaptProject,adaptQuota,adaptUsage,api,ApiError,consumeEvents,IntentKeys,request,safeError } from '../api';
import { canGenerate,defaultSettings,quotaRemaining,usageCost,usageTokens,validateFiles,type Project } from '../domain';
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
 it.each([
  ['native_chart_not_verified','native_chart_data','planning'],
  ['source_chart_binding_required','native_chart_data','planning'],
  ['source_chart_binding_invalid','native_chart_data','planning'],
  ['final_pptx_text_layout_failed','final_pptx_text_layout','generation'],
  ['final_pptx_text_extraction_failed','final_pptx_text_extraction','validation'],
 ])('retains the safe final-PPTX diagnosis %s without raw report data or publication authority',(error_code,stage,retry_stage)=>{
  const id='43133abb-2e94-4560-860a-2c1504f8ed42';
  const diagnostic={schema_version:1,error_code,stage,title:'PPTX 文字检查',message:'检查未通过，请核对固定说明。',suggestion:'保留原资料，先处理对应检查问题。',retry_stage,publication_authorized:false,diagnostic_download_url:`/api/v1/jobs/${id}/diagnostics`,raw_report:'PRIVATE-CANDIDATE-TEXT',path:'/private/secret.log'};
  const raw={id,project_id:'p',kind:'generate',status:'failed',error_code:'quality_gate_failed',quality_diagnostics:diagnostic};
  const job=adaptJob(raw);
  expect(job.quality_diagnostics).toMatchObject({error_code,stage,retry_stage,publication_authorized:false});
  expect(job.quality_diagnostics?.diagnostic_download_url).toBe(`/api/v1/jobs/${id}/diagnostics`);
  expect(JSON.stringify(job.quality_diagnostics)).not.toMatch(/PRIVATE|private|secret|raw_report/);
  expect(adaptProject({id:'p',latest_job:raw}).latest_job?.quality_diagnostics).toEqual(job.quality_diagnostics);
  expect(adaptJob({...raw,quality_diagnostics:{...diagnostic,publication_authorized:true}}).quality_diagnostics).toBeUndefined();
  expect(adaptJob({...raw,quality_diagnostics:{...diagnostic,diagnostic_download_url:'https://private.invalid/report'}}).quality_diagnostics).toBeUndefined();
 });
 it('adapts a bounded quality diagnosis and drops private report fields',()=>{
  const id='43133abb-2e94-4560-860a-2c1504f8ed42';
  const diagnostic={schema_version:1,error_code:'external_svg_resource',stage:'source_binding',title:'页面引用检查未通过',message:'页面包含不允许的资源引用。',suggestion:'请管理员核对引用类型与校验规则。',retry_stage:'validation',publication_authorized:false,diagnostic_download_url:`/api/v1/jobs/${id}/diagnostics`,private_logs:'/private/check.log',raw_report:'SECRET',resource_url:'https://private.invalid'};
  const job=adaptJob({id,project_id:'p',kind:'generate',status:'failed',error_code:'quality_gate_failed',quality_diagnostics:diagnostic});
  expect(job.quality_diagnostics).toEqual({schema_version:1,error_code:'external_svg_resource',stage:'source_binding',title:'页面引用检查未通过',message:'页面包含不允许的资源引用。',suggestion:'请管理员核对引用类型与校验规则。',retry_stage:'validation',publication_authorized:false,diagnostic_download_url:`/api/v1/jobs/${id}/diagnostics`});
  expect(JSON.stringify(job.quality_diagnostics)).not.toMatch(/private|SECRET|raw_report|resource_url/);
  expect(adaptProject({id:'p',latest_job:{...job}}).latest_job?.quality_diagnostics).toEqual(job.quality_diagnostics);
  expect(safeError(new ApiError('quality_gate_failed'))).not.toContain('调整要求');
 });
 it('rejects malformed, unrelated or unsafe quality diagnoses instead of rendering raw fields',()=>{
  const id='43133abb-2e94-4560-860a-2c1504f8ed42';
  const diagnostic={schema_version:1,error_code:'external_svg_resource',stage:'source_binding',title:'检查未通过',message:'资源检查失败',suggestion:'核对检查记录',retry_stage:'validation',publication_authorized:false,diagnostic_download_url:`/api/v1/jobs/${id}/diagnostics`};
  const raw={id,project_id:'p',kind:'generate',status:'failed',error_code:'quality_gate_failed',quality_diagnostics:diagnostic};
  for(const value of [null,[],false,'PRIVATE',{...diagnostic,schema_version:true},{...diagnostic,publication_authorized:true},{...diagnostic,error_code:'private_raw_failure'},
   {...diagnostic,stage:'<img>'},{...diagnostic,retry_stage:'publish'},{...diagnostic,title:''},{...diagnostic,title:'x'.repeat(161)},
   {...diagnostic,message:'x'.repeat(2001)},{...diagnostic,suggestion:'private\u0000text'},
   ...['https://evil.invalid/report','//evil.invalid/report','javascript:alert(1)',`/api/v1/jobs/${id}/diagnostics?token=SECRET`,'/api/v1/jobs/00000000-0000-0000-0000-000000000000/diagnostics'].map(diagnostic_download_url=>({...diagnostic,diagnostic_download_url}))]) {
   expect(adaptJob({...raw,quality_diagnostics:value}).quality_diagnostics).toBeUndefined();
  }
  expect(adaptJob({...raw,status:'running'}).quality_diagnostics).toBeUndefined();
  expect(adaptJob({...raw,error_code:'execution_interrupted'}).quality_diagnostics).toBeUndefined();
  expect(adaptJob({...raw,id:'../../private'}).quality_diagnostics).toBeUndefined();
 });
 it('adapts only bounded plain text needs-input questions for the reported terminal error',()=>{
  const raw={id:'needs',project_id:'p',kind:'generate',status:'failed',error_code:'execution_needs_input',event_cursor:5,
   needs_input:{questions:['请核对讲解重点。',{question:'not-public'},false,...Array(30).fill('x'.repeat(2000))],private_result:'private'}};
  const job=adaptJob(raw);expect(job.needs_input?.questions[0]).toBe('请核对讲解重点。');
  expect(job.needs_input?.questions.every(value=>typeof value==='string'&&value.length<=1000)).toBe(true);
  expect(job.needs_input!.questions.length).toBeLessThanOrEqual(20);
  expect(job.needs_input!.questions.reduce((total,value)=>total+value.length,0)).toBeLessThanOrEqual(10000);
  expect(JSON.stringify(job.needs_input)).not.toMatch(/private|not-public/);
  expect(adaptJob({...raw,error_code:'execution_interrupted'}).needs_input).toBeUndefined();
  expect(adaptJob({...raw,status:'running'}).needs_input).toBeUndefined();
 });
 it('explains needs-input without claiming a successful version or automatically retrying',()=>{
  const message=safeError(new ApiError('execution_needs_input'));
  expect(message).toContain('任务已停止');expect(message).toContain('执行器报告的问题');
  expect(message).toContain('已确认方案仍保留');expect(message).toContain('EXECUTION_NEEDS_INPUT');
  expect(message).not.toMatch(/连接失败|额度|上下文|超时/);
 });
 it('explains a failed model response without guessing its underlying cause',()=>{
  const error=new ApiError('model_response_failed');const message=safeError(error);
  expect(error.code).toBe('MODEL_RESPONSE_FAILED');expect(message).toContain('模型服务未能完成本次请求');
  expect(message).toContain('任务已停止');expect(message).toContain('已确认方案仍保留');
  expect(message).not.toMatch(/EXECUTION_INTERRUPTED|连接失败|额度|上下文|超时/);
 });
 it('gives an explicit explanation for an otherwise unclassified interrupted task',()=>{
  const message=safeError(new ApiError('execution_interrupted'));
  expect(message).toContain('本次任务未能正常完成');expect(message).toContain('请刷新状态核对');
  expect(message).toContain('已有版本仍保留');expect(message).toContain('EXECUTION_INTERRUPTED');
  expect(message).not.toContain('模型服务未能完成');
 });
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
 it('unknown supplier cost remains unknown instead of zero',()=>{expect(usageCost({input_tokens:12,reported_cost:null,cost_status:'unknown'})).toContain('未知');expect(usageCost({reported_cost:'0',cost_status:'reported',currency:'USD'})).toBe('0 USD');});
 it('bounds uploads by per-file size, file count and project total',()=>{const file=new File(['text'],'text.md');expect(validateFiles([file],[])).toBe('');expect(validateFiles([new File(['x'],'x.svg')],[])).toContain('请选择');expect(validateFiles(Array(11).fill(file),[])).toContain('10');const huge={name:'big.pdf',size:26*1024**2} as File;expect(validateFiles([huge],[])).toContain('25');});
});
describe('observed usage and separate quota counts',()=>{
 const observed={scope:'task',input_tokens:120,output_tokens:40,tokens_status:'known',reported_cost:'0.0001234567890123456789',currency:'USD',cost_status:'reported',cost_breakdown:[{currency:'USD',reported_cost:'0.0001234567890123456789'}],record_count:2,unknown_record_count:0};
 it('preserves exact supplier decimals and task usage while dropping private ledger fields',()=>{
  const job=adaptJob({id:'usage-job',status:'failed',kind:'generate',usage:{...observed,private_ledger:'/work/private',provider_key:'SECRET'}});
  expect(job.usage).toEqual(observed);expect(usageTokens(job.usage)).toBe('120 / 40 tokens');
  expect(usageCost(job.usage)).toBe('0.0001234567890123456789 USD');
  expect(JSON.stringify(job.usage)).not.toMatch(/SECRET|private|ledger/);
 });
 it('keeps unknown and partially observed tokens or charges distinct from complete totals',()=>{
  const usage=adaptUsage({...observed,input_tokens:120,output_tokens:null,tokens_status:'partial',reported_cost:'1.25',cost_status:'partial',unknown_record_count:1},'task');
  expect(usageTokens(usage)).toBe('120 / 未知 tokens');
  expect(usage?.tokens_status).toBe('partial');expect(usageCost(usage)).toBe('1.25 USD（已报告部分，另有费用未知）');
  const unknown=adaptUsage({...observed,input_tokens:null,output_tokens:null,tokens_status:'unknown',reported_cost:null,currency:null,cost_status:'unknown',cost_breakdown:[],unknown_record_count:2},'task');
  expect(usageTokens(unknown)).toBe('未知 / 未知 tokens');expect(usageCost(unknown)).toContain('费用未知');
  expect(usageCost(unknown)).not.toContain('0 USD');
 });
 it('lists currencies separately instead of adding or converting them',()=>{
  const usage=adaptUsage({...observed,reported_cost:null,currency:null,cost_status:'mixed_currency',cost_breakdown:[{currency:'EUR',reported_cost:'2.50'},{currency:'USD',reported_cost:'1.25'}],unknown_record_count:1},'task');
  expect(usageCost(usage)).toBe('2.50 EUR；1.25 USD（不同币种分别列示，另有费用未知）');
  expect(usage?.reported_cost).toBeNull();expect(usage?.currency).toBeNull();
 });
 it('does not infer unknown costs from incomplete tokens when every mixed-currency charge is reported',()=>{
  const usage=adaptUsage({...observed,output_tokens:null,tokens_status:'partial',reported_cost:null,currency:null,cost_status:'mixed_currency',cost_breakdown:[{currency:'EUR',reported_cost:'2.50'},{currency:'USD',reported_cost:'1.25'}],unknown_record_count:0},'task');
  expect(usageTokens(usage)).toBe('120 / 未知 tokens');expect(usage?.tokens_status).toBe('partial');
  expect(usageCost(usage)).toBe('2.50 EUR；1.25 USD（不同币种分别列示）');
  expect(usageCost(usage)).not.toContain('另有费用未知');
 });
 it('does not substitute an account total or malformed data for a task observation',()=>{
  expect(adaptUsage({...observed,scope:'account'},'task')).toBeUndefined();
  expect(adaptJob({id:'missing',usage:{...observed,scope:'account'}}).usage).toBeUndefined();
  for(const value of [null,[],false,'raw'])expect(adaptUsage(value,'task')).toBeUndefined();
  const malformed=adaptUsage({...observed,input_tokens:-1,output_tokens:Number.MAX_SAFE_INTEGER+1,reported_cost:'NaN',currency:'USD',cost_breakdown:[{currency:'<img>',reported_cost:'1'},{currency:'USD',reported_cost:'-1'}]},'task');
  expect(usageTokens(malformed)).toBe('未知 / 未知 tokens');expect(usageCost(malformed)).toContain('未知');expect(malformed?.cost_breakdown).toEqual([]);
 });
 it('accepts only enabled, distinct server-reported making and revision quota balances',()=>{
  const quota=adaptQuota({enabled:true,period:'2026-11',generation_limit:5,revision_limit:8,generation_count:2,revision_count:3,generation_remaining:3,revision_remaining:5,remaining:99,private_key:'SECRET'});
  expect(quotaRemaining(quota)).toBe('制作剩余 3 次 / 修改剩余 5 次');expect(JSON.stringify(quota)).not.toMatch(/SECRET|private_key|"remaining"/);
  expect(quotaRemaining(adaptQuota({enabled:false,generation_remaining:99,revision_remaining:99}))).toBe('未启用');
  expect(quotaRemaining(adaptQuota({enabled:true,generation_remaining:-1,revision_remaining:2.5}))).toBe('制作剩余 待核对 / 修改剩余 待核对');
 });
});

describe('durable business SSE',()=>{
 it('uses actual Last-Event-ID and ignores duplicate or malformed events and raw tool payload',async()=>{
 const encoder=new TextEncoder();const stream=new ReadableStream({start(c){c.enqueue(encoder.encode('id: 7\nevent: stage.changed\ndata: {"type":"stage.changed","payload":{"stage":"running","raw":"secret"},"seq":7}\n\nid: 7\ndata: {"type":"duplicate","seq":7}\n\nid: 9\ndata: {"type":"job.succeeded","payload":{"status":"succeeded"}}\n\n'));c.close();}});const fetcher=vi.fn().mockResolvedValue(new Response(stream));vi.stubGlobal('fetch',fetcher);const event=vi.fn();await consumeEvents('j',6,new AbortController().signal,event);expect(fetcher.mock.calls[0][1].headers['Last-Event-ID']).toBe('6');expect(event).toHaveBeenCalledTimes(2);expect(event.mock.calls[0][0]).toEqual({seq:7,type:'stage.changed',payload:{stage:'running',status:undefined}});expect(event.mock.calls[1][0].seq).toBe(9);
 });
 it('cursor expiry explicitly requests snapshot refresh',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('',{status:410})));await expect(consumeEvents('j',12,new AbortController().signal,vi.fn())).rejects.toEqual(new ApiError('CURSOR_EXPIRED',410));});
});
