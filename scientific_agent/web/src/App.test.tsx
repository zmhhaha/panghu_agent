import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { emptyRequirements, type Project } from './domain';

const projectId='09aa4841-5d5c-4653-bb15-c3403d100bcd';
const planOutput={questions:[],research_questions:['可重复构建的方法有哪些？'],search_queries:['reproducible builds'],inclusion_criteria:['实证研究'],exclusion_criteria:['无方法描述'],scope:'最多三次检索，每次十项，达到范围停止。',limitations:['尚未检索全文']};
function project(overrides:Partial<Project>={}):Project{return {id:projectId,title:'可重复构建研究',revision:4,stage:'goal',requirements:{...emptyRequirements,question:'可重复构建的方法与局限'},materials:[],plan:null,confirmations:{plan:null,evidence:null},search_batches:[],evidence:null,outline:null,selections:[],versions:[],current_version_id:null,finalizations:[],annotations:[],jobs:[],active_job_id:null,history:[],...overrides};}
const response=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status,headers:{'content-type':'application/json'}});
type Call={path:string;init:RequestInit;body:Record<string,unknown>};
function server(initial:Project|null,handler?:(call:Call)=>Response|Promise<Response>|undefined){
  let current=initial;const calls:Call[]=[];
  if(initial)localStorage.setItem('scientific-project',initial.id);
  const fetchMock=vi.fn(async(input:RequestInfo|URL,init:RequestInit={})=>{
    const path=String(input).replace('/api/v1/research','');const body=typeof init.body==='string'?JSON.parse(init.body):{};const call={path,init,body};calls.push(call);
    const custom=handler?.(call);if(custom!==undefined)return custom;
    if(path==='/me')return response({user_id:'researcher',csrf_token:'test-csrf'});
    if(path==='/projects'&&(init.method??'GET')==='GET')return response({projects:current?[current]:[]});
    if(path===`/projects/${projectId}`&&(init.method??'GET')==='GET')return response(current);
    if(path===`/projects/${projectId}`&&init.method==='PATCH'){current={...current!,...body,revision:current!.revision+1} as Project;return response(current);}
    if(path==='/projects'&&init.method==='POST'){current=project({requirements:{...emptyRequirements,...body.requirements as object}});return response(current,201);}
    if(path.includes('/actions/')){current={...current!,revision:current!.revision+1};return response(current);}
    return response({detail:{code:'not_found'}},404);
  });vi.stubGlobal('fetch',fetchMock);return {calls,fetchMock,set:(value:Project)=>{current=value;}};
}
beforeEach(()=>{localStorage.clear();});

describe('project workflow',()=>{
  it('creates and persists a project from one question without starting a model job',async()=>{
    const backend=server(null);render(<App/>);await screen.findByRole('button',{name:/开始一项研究/});await userEvent.click(screen.getByRole('button',{name:/开始一项研究/}));
    await userEvent.type(screen.getByRole('textbox',{name:'研究问题'}),'编译缓存有哪些一致性保证？');await userEvent.click(screen.getByRole('button',{name:/建立项目/}));
    await screen.findByDisplayValue('编译缓存有哪些一致性保证？');const write=backend.calls.find(call=>call.path==='/projects'&&call.init.method==='POST');
    expect(write?.body).toEqual({requirements:{question:'编译缓存有哪些一致性保证？'}});expect(backend.calls.some(call=>call.path.includes('/actions/'))).toBe(false);
    expect(new Headers(write!.init.headers).get('X-CSRF-Token')).toBe('test-csrf');expect(new Headers(write!.init.headers).get('Idempotency-Key')).toBeTruthy();
  });
  it('autosaves changed requirements to the captured revision without starting a model task',async()=>{
    const backend=server(project());render(<App/>);const question=await screen.findByRole('textbox',{name:'研究问题'});
    await userEvent.clear(question);await userEvent.type(question,'自动保存后的研究问题');
    await waitFor(()=>expect(backend.calls.some(call=>call.init.method==='PATCH')).toBe(true),{timeout:2500});
    const write=backend.calls.find(call=>call.init.method==='PATCH')!;expect(write.body.expected_revision).toBe(4);expect((write.body.requirements as {question:string}).question).toBe('自动保存后的研究问题');
    expect(backend.calls.some(call=>call.path.includes('/actions/'))).toBe(false);await screen.findByText('要求已自动保存');
  });
  it('requires explicit plan confirmation and sends the frozen project revision',async()=>{
    const backend=server(project({stage:'plan',plan:{id:'plan-1',revision:4,output:planOutput}}));render(<App/>);
    const submit=await screen.findByRole('button',{name:/确认方案并检索/});expect(submit).toBeDisabled();expect(backend.calls.some(call=>call.path.includes('/actions/'))).toBe(false);
    await userEvent.click(screen.getByRole('checkbox',{name:/我已核对方案/}));await userEvent.click(submit);
    await waitFor(()=>expect(backend.calls.some(call=>call.path.endsWith('/actions/confirm_plan'))).toBe(true));
    expect(backend.calls.find(call=>call.path.endsWith('/actions/confirm_plan'))?.body).toEqual({expected_revision:4});
  });
  it('requires a separate limited-evidence acknowledgement before drafting',async()=>{
    const outline={evidence:[],sections:[{section_id:'s1',title:'研究现状',questions:['已有发现'],evidence_ids:[]}],gaps:['缺少实证对照']};
    const backend=server(project({stage:'evidence',plan:{id:'plan-1',revision:3,output:planOutput},confirmations:{plan:{id:'c1'},evidence:null},evidence:{id:'e1',content:outline,papers:[]},outline:{id:'o1',output:outline}}));render(<App/>);
    const submit=await screen.findByRole('button',{name:/确认证据与大纲并写作/});expect(submit).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox',{name:/我已核对资料/}));expect(submit).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox',{name:/当前证据仍有限/}));await userEvent.click(submit);
    await waitFor(()=>expect(backend.calls.find(call=>call.path.endsWith('/actions/confirm_evidence'))?.body).toEqual({expected_revision:4,allow_limited_evidence:true}));
  });
  it('saves screening without sending an obsolete outline or starting a model call',async()=>{
    const outline={evidence:[{evidence_id:'e1',paper_id:'p1',finding:'有工程研究支持',access_level:'abstract'}],sections:[{section_id:'s1',title:'研究现状',questions:['已有发现'],evidence_ids:['e1']}],gaps:[]};
    const backend=server(project({stage:'evidence',confirmations:{plan:{id:'c1'},evidence:null},search_batches:[{id:'batch',papers:[{paper_id:'p1',title:'Build reproducibility',year:2024,access_level:'abstract'}]}],evidence:{id:'e1',content:outline},outline:{id:'o1',output:outline},selections:[{source_id:'p1',included:true,reason:'Relevant'}]}));render(<App/>);
    await screen.findByRole('checkbox',{name:'纳入本次证据'});await userEvent.click(screen.getByRole('checkbox',{name:'纳入本次证据'}));
    const reason=screen.getByRole('textbox',{name:'Build reproducibility筛选理由'});await userEvent.clear(reason);await userEvent.type(reason,'不符合预先约定的研究对象');
    expect(screen.getByRole('textbox',{name:'第1节标题'})).toBeDisabled();await userEvent.click(screen.getByRole('button',{name:'保存筛选与大纲'}));
    await waitFor(()=>expect(backend.calls.some(call=>call.init.method==='PATCH')).toBe(true));
    expect(backend.calls.find(call=>call.init.method==='PATCH')?.body).toEqual({expected_revision:4,selections:[{source_id:'p1',included:false,reason:'不符合预先约定的研究对象'}]});
    expect(backend.calls.some(call=>call.path.includes('/actions/'))).toBe(false);
  });
  it('preserves unsaved input after a conflicting revision and reloads authoritative state',async()=>{
    const latest=project({revision:7,requirements:{...emptyRequirements,question:'另一标签页更新的问题'}});
    const backend=server(project(),call=>{if(call.init.method==='PATCH'){backend.set(latest);return response({detail:{code:'project_revision_conflict'}},409);}});render(<App/>);
    const question=await screen.findByRole('textbox',{name:'研究问题'});await userEvent.clear(question);await userEvent.type(question,'保留这份未保存的研究问题');await userEvent.click(screen.getByRole('button',{name:'保存要求'}));
    await screen.findByRole('alert');expect(question).toHaveValue('保留这份未保存的研究问题');
    await waitFor(()=>expect(backend.calls.filter(call=>call.path===`/projects/${projectId}`&&!call.init.method).length).toBeGreaterThanOrEqual(2));
    expect(backend.calls.filter(call=>call.init.method==='PATCH')).toHaveLength(1);
  });
  it('restores a queued server task and cancels by its persisted task identity',async()=>{
    const raw=project({stage:'plan',active_job_id:'job-one',jobs:[{id:'job-one',status:'queued',state:'queued',stage:'plan'}]});
    const backend=server(raw);render(<App/>);await screen.findByRole('button',{name:'取消本次执行'});await userEvent.click(screen.getByRole('button',{name:'取消本次执行'}));
    await waitFor(()=>expect(backend.calls.find(call=>call.path.endsWith('/actions/cancel'))?.body).toEqual({expected_revision:4,job_id:'job-one'}));
  });
  it('keeps the selected version when an older version request arrives late',async()=>{
    let resolveFirst!:(value:Response)=>void;const delayed=new Promise<Response>(resolve=>{resolveFirst=resolve;});
    server(project({stage:'review',current_version_id:'v1',versions:[{id:'v1'},{id:'v2',parent_version_id:'v1'}]}),call=>{
      if(call.path.endsWith('/versions/v1'))return delayed;
      if(call.path.endsWith('/versions/v2'))return response({id:'v2',parent_version_id:'v1',report_markdown:'# 新版本报告',section_index:{sections:[{section_id:'s1',title:'新版本报告'}]},sources:[],references:[]});
    });render(<App/>);const selector=await screen.findByRole('combobox',{name:'报告版本'});fireEvent.change(selector,{target:{value:'v2'}});
    await screen.findByRole('heading',{name:'新版本报告'});await act(async()=>{resolveFirst(response({id:'v1',report_markdown:'# 过期响应报告',sources:[],references:[]}));});
    expect(screen.queryByRole('heading',{name:'过期响应报告'})).not.toBeInTheDocument();expect(selector).toHaveValue('v2');
  });
  it('maps citations using recorded reference numbers rather than source array order',async()=>{
    server(project({stage:'review',current_version_id:'v1',versions:[{id:'v1'}]}),call=>call.path.endsWith('/versions/v1')?response({id:'v1',report_markdown:'# 研究报告\n\n关键发现 [1]。',sources:[{paper_id:'other',title:'错误的来源'},{paper_id:'correct',title:'正确的来源',abstract:'已记录的实际摘要',access_level:'abstract'}],references:[{n:1,paper_id:'correct'}],section_index:{sections:[]}}):undefined);
    render(<App/>);await userEvent.click(await screen.findByRole('button',{name:'查看引用 1'}));expect(await screen.findByRole('heading',{name:'正确的来源'})).toBeInTheDocument();expect(screen.getByText('已记录的实际摘要')).toBeInTheDocument();expect(screen.queryByRole('heading',{name:'错误的来源'})).not.toBeInTheDocument();
  });
  it('does not offer finalization of an unadopted candidate',async()=>{
    const p=project({stage:'final',current_version_id:'v1',versions:[{id:'v1'},{id:'v2',parent_version_id:'v1'}]});
    server(p,call=>call.path.includes('/versions/')?response({id:call.path.endsWith('v2')?'v2':'v1',report_markdown:'# 报告',sources:[],references:[],parent_version_id:call.path.endsWith('v2')?'v1':undefined}):undefined);
    render(<App/>);const selector=await screen.findByRole('combobox',{name:'报告版本'});fireEvent.change(selector,{target:{value:'v2'}});await screen.findByText(/当前选择的是候选版本/);
    await userEvent.click(screen.getByRole('checkbox',{name:/我已审阅这个明确版本/}));expect(screen.getByRole('button',{name:/确认为定稿/})).toBeDisabled();
  });
  it('opens actual legacy history and renders its saved content and citation evidence',async()=>{
    const backend=server(null,call=>{
      if(call.path.startsWith('/scientific-reports?'))return response([{id:'old-report',topic:'历史科研课题',summary:'原来的成果'}]);
      if(call.path==='/scientific-reports/old-report')return response({id:'old-report',topic:'历史科研课题',content:'# 原始历史报告\n\n保留原文内容。'});
      if(call.path==='/scientific-research/old-report/citations')return response({availability:'available',citations:[{n:1,status:'resolved',paper:{paper_id:'old-source',title:'已保存的历史来源',availability:{metadata:{available:true},abstract:{available:true},full_text:{available:false}},verification:{status:'verified'}},evidence:[{evidence_id:'old-e',paper_id:'old-source',finding:'当时记录的实际证据'}],claims:[]}]});
    });render(<App/>);await userEvent.click(await screen.findByRole('button',{name:/查看旧版任务与报告/}));
    await userEvent.click(await screen.findByRole('button',{name:/历史科研课题/}));await screen.findByRole('heading',{name:'原始历史报告'});
    expect(await screen.findByRole('heading',{name:'已保存的历史来源'})).toBeInTheDocument();expect(screen.getByText('当时记录的实际证据')).toBeInTheDocument();
    expect(backend.calls.some(call=>call.init.method&&call.init.method!=='GET')).toBe(false);
  });
  it('preserves inline code while making actual citation text interactive',async()=>{
    server(project({stage:'review',current_version_id:'v1',versions:[{id:'v1'}]}),call=>call.path.endsWith('/versions/v1')?response({id:'v1',report_markdown:'# 报告\n\n示例代码 `[1]`；实际引文 [1]。\n\n[外部链接](https://example.org)',sources:[{paper_id:'p1',title:'实际来源'}],references:[{n:1,paper_id:'p1'}],section_index:{sections:[]}}):undefined);
    render(<App/>);await screen.findByRole('button',{name:'查看引用 1'});expect(screen.getAllByRole('button',{name:'查看引用 1'})).toHaveLength(1);expect(document.querySelector('code')).toHaveTextContent('[1]');
    expect(screen.getByRole('link',{name:'外部链接'})).toHaveAttribute('href','https://example.org');
  });
});
