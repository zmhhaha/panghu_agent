import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {beforeEach,describe,expect,it,vi} from 'vitest';
import App from './App';
import {api} from './api';
import {emptyRequirements,type Project,type Version} from './domain';
import {completedRevision,currentFinalized,ImportReferenceMapping,OfficialSourceForm,ReportDownloads,WritingQuality} from './ResearchFeedback';
import {RetrievalDetails} from './SourceDetails';
import {researchTime} from './time';

function project(overrides:Partial<Project>={}):Project{return {id:'feedback-project',title:'架构调研',revision:9,input_revision:4,stage:'review',requirements:{...emptyRequirements,question:'比较文件问答架构'},materials:[],plan:null,confirmations:{plan:null,evidence:null},search_batches:[],evidence:null,outline:null,selections:[],versions:[],current_version_id:null,finalizations:[],annotations:[],jobs:[],active_job_id:null,history:[],...overrides};}
const response=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status,headers:{'content-type':'application/json'}});
function backend(p:Project,versions:Record<string,Version>={}){
  localStorage.setItem('scientific-project',p.id);const calls:string[]=[];
  vi.stubGlobal('fetch',vi.fn(async(input:RequestInfo|URL)=>{const path=String(input);calls.push(path);
    if(path.endsWith('/me'))return response({user_id:'reader',csrf_token:'csrf'});
    if(path.endsWith('/projects'))return response({projects:[p]});
    if(path.endsWith(`/projects/${p.id}`))return response(p);
    if(path.includes('/compare?'))return response({diff:'本节增加固定文件案例'});
    const id=path.split('/versions/')[1];if(id&&versions[id])return response(versions[id]);
    return response({detail:{code:'not_found'}},404);
  }));return calls;
}
beforeEach(()=>{vi.restoreAllMocks();localStorage.clear();});

describe('research feedback workflow',()=>{
  it('identifies a completed revision through its task receipt instead of the version array order',()=>{
    const p=project({current_version_id:'base',versions:[{id:'candidate',parent_version_id:'base',input_revision:4},{id:'base'},{id:'unrelated',parent_version_id:'base'}],jobs:[{id:'revision-task',stage:'revise',status:'completed',result_version_id:'candidate'}]});
    expect(completedRevision(p)?.id).toBe('candidate');
    expect(completedRevision({...p,jobs:[{id:'unknown',stage:'revise',status:'completed'}]})).toBeUndefined();
    expect(completedRevision({...p,input_revision:5})).toBeUndefined();
    expect(completedRevision({...p,current_version_id:'candidate'})).toBeUndefined();
  });

  it('does not label a finalized report from an older input scope as the current successful outcome',()=>{
    const p=project({current_version_id:'old-final',input_revision:5,versions:[{id:'old-final',input_revision:4}],finalizations:[{id:'f',version_id:'old-final'}]});
    expect(currentFinalized(p)).toBe(false);
    expect(currentFinalized({...p,input_revision:4})).toBe(true);
  });

  it('keeps the viewed working version until the user opens the completed candidate and its exact parent diff',async()=>{
    const base={id:'base',report_markdown:'# 当前原稿',references:[],sources:[],revision_sections:[{section_id:'s1',title:'共同案例'}]};
    const candidate={id:'candidate',parent_version_id:'base',report_markdown:'# 新修订候选',references:[],sources:[],revision_sections:[{section_id:'s1',title:'共同案例'}]};
    const p=project({current_version_id:'base',versions:[candidate,base,{id:'last-but-unrelated'}],jobs:[{id:'task',stage:'revise',status:'completed',result_version_id:'candidate'}]});
    const calls=backend(p,{base,candidate});render(<App/>);
    await screen.findByRole('heading',{name:'当前原稿'});expect(screen.getByRole('combobox',{name:'报告版本'})).toHaveValue('base');
    await userEvent.click(screen.getByRole('button',{name:'修订已完成，查看差异'}));
    await screen.findByRole('heading',{name:'新修订候选'});expect(await screen.findByLabelText('报告版本差异')).toHaveTextContent('本节增加固定文件案例');
    expect(calls.some(path=>path.includes('base_version_id=base&candidate_version_id=candidate'))).toBe(true);
    expect(calls.some(path=>path.includes('/actions/adopt'))).toBe(false);
  });

  it('preserves failure history while presenting the current finalized report as the main outcome',async()=>{
    const version={id:'imported',origin:'user_import',model_trace_available:false,report_markdown:'# 已完成恢复稿',references:[],sources:[]};
    const p=project({stage:'final',current_version_id:'imported',versions:[version],finalizations:[{id:'final',version_id:'imported'}],jobs:[{id:'old-failed',stage:'outline',status:'failed',diagnostic:{reason:'历史输出结构无效'}}]});
    backend(p,{imported:version});render(<App/>);await screen.findByRole('heading',{name:'已完成恢复稿'});
    expect(screen.getByText('当前工作版本已定稿')).toBeInTheDocument();
    expect(screen.queryByText('整理证据与大纲：执行失败')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button',{name:'查看历史任务与诊断'}));
    expect(await screen.findByText('历史输出结构无效')).toBeInTheDocument();
    expect(screen.getByText('old-failed')).toBeInTheDocument();
  });

  it('explains the queued retrieval and the automatic outline that follows without claiming writing has started',async()=>{
    const p=project({stage:'evidence',active_job_id:'queued-search',jobs:[{id:'queued-search',stage:'retrieve',status:'queued',created_at:'2026-11-03T16:32:00Z'}]});backend(p);render(<App/>);
    expect(await screen.findByText('实际开始：尚未开始')).toBeInTheDocument();
    expect(screen.getByText(/检索完成并保存后，会自动提交一次证据与大纲整理/)).toBeInTheDocument();
    expect(screen.getByText(/等待执行安排/)).toBeInTheDocument();
    expect(screen.queryByText('正在撰写报告')).not.toBeInTheDocument();
  });

  it('shows each batch budget separately from cumulative calls and does not turn old empty results into current gaps',()=>{
    const providers=[{provider:'arxiv',query:'AutoGen',status:'completed',result_count:10}];
    const p=project({retrieval_summary:{calls_used:3,call_budget:3,budget_exhausted:true,cumulative_calls:6,batch_count:2,current_batch_id:'batch2',providers,topics_without_evidence:[],gaps:[],batches:[{batch_id:'batch1',calls_used:3,call_budget:3,providers:[{...providers[0],query:'complex AutoGen query',result_count:0}],gaps:['历史首批没有 AutoGen 记录']},{batch_id:'batch2',calls_used:3,call_budget:3,providers,gaps:[]}]}});
    render(<RetrievalDetails project={p}/>);
    expect(screen.getByText(/6 次/)).toHaveTextContent('已记录 2 批');
    expect(screen.getByRole('heading',{name:'检索批次 1 · 历史记录'})).toBeInTheDocument();
    expect(screen.getByRole('heading',{name:'检索批次 2 · 当前批'})).toBeInTheDocument();
    expect(screen.queryByText('当前计划尚无成功结果的查询')).not.toBeInTheDocument();
    expect(screen.getByText('历史首批没有 AutoGen 记录')).toBeInTheDocument();
  });

  it('submits an explicit public source URL through the project mutation without calling it full text beforehand',async()=>{
    const p=project();const get=vi.spyOn(api,'url').mockResolvedValue(p);
    render(<OfficialSourceForm project={p} busy={false} mutate={async task=>task()}/>);
    const submit=screen.getByRole('button',{name:'获取并保存正文'});expect(submit).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox',{name:'官方资料或仓库 URL'}),{target:{value:'https://github.com/example/project'}});
    await userEvent.click(submit);await waitFor(()=>expect(get).toHaveBeenCalledWith(p,'https://github.com/example/project'));
    expect(screen.getByText(/链接不等于已读取正文/)).toBeInTheDocument();
    expect(screen.queryByText('已获取全文')).not.toBeInTheDocument();
  });

  it('shows the exact fetched repository files and a safe gap when an optional file was unavailable',async()=>{
    const p=project({stage:'goal',materials:[{id:'official',name:'仓库文档',status:'ready',material_role:'research_source',retrieval:{input_url:'https://github.com/example/repo',final_url:'https://github.com/example/repo/tree/abcdef',fetched_at:'2026-11-03T16:05:00Z',access_scope:'fetched_document_body',github:{repository:'example/repo',commit:'abcdef',requested_ref:'main',files:[{path:'README.md'}]},gaps:[{path:'pyproject.toml',code:'official_source_not_found'}]}}]});
    backend(p);render(<App/>);await screen.findByText('来源正文已获取');
    expect(screen.getByText(/已读文件：README.md/)).toHaveTextContent('不代表已读取整个仓库');
    expect(screen.getByText(/资料缺口：pyproject.toml/)).toHaveTextContent('未找到该资料文件');
    expect(screen.getByText(/实际读取范围/)).toHaveTextContent('仅本次实际获取的工程文档正文');
    expect(screen.queryByText(/\[object Object\]/)).not.toBeInTheDocument();
  });

  it('shows the full-report length delta and sends refinement back to the selected section',async()=>{
    const refine=vi.fn();render(<WritingQuality version={{id:'v',writing_quality:{actual_length:4404,parent_length:3106,length_delta:1298,target_length:3000,unit:'汉字',warnings:['正文超过约定长度，请审阅并精炼。'],missing_requirements:['pyproject.toml']}}} onRefine={refine}/>);
    expect(screen.getByText(/4404 汉字/)).toHaveTextContent('增加 1298 汉字');
    expect(screen.getByText('pyproject.toml')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button',{name:'带入本节精炼意见'}));expect(refine).toHaveBeenCalledOnce();
  });

  it('pins every reading and trace download to the explicitly selected report version',()=>{
    render(<ReportDownloads projectId="project-one" version={{id:'selected-v',created_at:'2026-11-03T16:00:00Z'}} isFinal/>);
    for(const [name,format] of [['下载 定稿 阅读版 Markdown','reading-markdown'],['下载 定稿 Word','docx'],['下载 定稿 PDF','pdf'],['下载 定稿 Markdown','markdown'],['下载来源记录','sources']])expect(screen.getByRole('link',{name})).toHaveAttribute('href',`/api/v1/research/projects/project-one/versions/selected-v/download?format=${format}`);
    expect(screen.getByText(/以下文件均对应当前所选版本/)).toHaveTextContent('北京时间');
  });

  it('uses the same Beijing day for UTC instants around midnight',()=>{
    const shown=researchTime('2026-11-03T16:05:00Z');expect(shown).toContain('2026/11/04');expect(shown).toContain('00:05');expect(shown).toContain('北京时间');
    expect(researchTime('not a timestamp')).toBe('尚未记录');
  });

  it('saves explicitly acknowledged imported reference mappings without generating evidence or changing the report',async()=>{
    const p=project();const version:Version={id:'import-one',origin:'user_import',report_markdown:'# 导入原稿\n\n判断 [13]。',sources:[{paper_id:'p13',title:'原始论文摘要'}],import_citations:{references:[{n:13,text:'第十三项参考文献',body_occurrences:1,source_id:null,match_status:'unmatched'}],unmatched_numbers:[13],validation:'user_matched_not_machine_claim_validated'}};
    const saved=vi.fn();const action=vi.spyOn(api,'action').mockResolvedValue(p);
    render(<ImportReferenceMapping project={p} version={version} busy={false} mutate={async task=>task()} onSaved={saved}/>);
    await userEvent.click(screen.getByRole('button',{name:'核对并匹配导入稿引用'}));
    const submit=screen.getByRole('button',{name:'保存引用匹配并重新审阅'});expect(submit).toBeDisabled();
    await userEvent.selectOptions(screen.getByRole('combobox',{name:'引用 13 对应来源'}),'p13');
    await userEvent.click(screen.getByRole('checkbox',{name:/我已逐项核对编号与所选来源/}));await userEvent.click(submit);
    expect(action).toHaveBeenCalledWith(p,'map_import_references',{version_id:'import-one',reference_mappings:[{n:13,source_id:'p13'}]});
    expect(saved).toHaveBeenCalledOnce();expect(action).toHaveBeenCalledOnce();
    expect(screen.getByText(/不会改写正文、证据和历史任务/)).toBeInTheDocument();
  });

  it('explains an unmatched imported citation instead of silently failing or inventing a source mapping',async()=>{
    const version:Version={id:'import-one',origin:'user_import',model_trace_available:false,report_markdown:'# 导入报告\n\n待核对的判断 [13]。',sources:[],references:[],import_citations:{references:[{n:13,text:'尚待匹配的参考文献',body_occurrences:1,source_id:null,match_status:'unmatched'}],unmatched_numbers:[13],validation:'user_matched_not_machine_claim_validated'}};
    const p=project({current_version_id:'import-one',versions:[version]});const calls=backend(p,{'import-one':version});render(<App/>);
    await userEvent.click(await screen.findByRole('button',{name:'查看引用 13'}));
    expect(screen.getByText(/引用 13 尚未匹配到已保存的来源/)).toBeInTheDocument();
    expect(calls.some(path=>path.includes('/actions/'))).toBe(false);
  });

  it('keeps finalized imported references read-only and points to a new manual candidate',()=>{
    const version:Version={id:'import-final',origin:'user_import',sources:[{paper_id:'p13',title:'原始论文摘要'}],import_citations:{references:[{n:13,text:'第十三项参考文献',body_occurrences:1,source_id:null,match_status:'unmatched'}],unmatched_numbers:[13],validation:'user_matched_not_machine_claim_validated'}};
    const p=project({current_version_id:version.id,finalizations:[{id:'final',version_id:version.id}]});
    render(<ImportReferenceMapping project={p} version={version} busy={false} mutate={async task=>task()} onSaved={vi.fn()}/>);
    expect(screen.getByRole('heading',{name:'导入稿引用与来源匹配 · 定稿只读'})).toBeInTheDocument();
    expect(screen.getByText('第十三项参考文献')).toBeInTheDocument();
    expect(screen.getByText(/此版本已定稿/)).toHaveTextContent('尚有 1 项未匹配');
    expect(screen.getByText(/需要补齐引用时/)).toHaveTextContent('在“手工修改并保存新版本”中点击“编辑此版正文”');
    expect(screen.getByText(/需要补齐引用时/)).toHaveTextContent('随后在新候选中匹配引用，重新审阅、采用并定稿');
    expect(screen.queryByRole('button',{name:'核对并匹配导入稿引用'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button',{name:'保存引用匹配并重新审阅'})).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });
});
