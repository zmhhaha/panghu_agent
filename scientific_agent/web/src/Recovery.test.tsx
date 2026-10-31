import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {beforeEach,describe,expect,it,vi} from 'vitest';
import {api} from './api';
import {emptyRequirements,type Project} from './domain';
import JobDetails from './JobDetails';
import {EvidenceHistory,ImportReview,ManualEvidence,ReportImport} from './Recovery';
import {BibliographicDetails,QualityNote,RetrievalDetails} from './SourceDetails';
import MaterialSettings from './MaterialSettings';

const project=():Project=>({id:'project-one',title:'研究',revision:5,input_revision:3,stage:'evidence',requirements:{...emptyRequirements,question:'DSH架构综述'},materials:[],plan:null,confirmations:{plan:null,evidence:null},search_batches:[],evidence:null,outline:null,selections:[],versions:[],current_version_id:null,finalizations:[],annotations:[],jobs:[],active_job_id:null,history:[]});
beforeEach(()=>vi.restoreAllMocks());
const mutation=async(task:()=>Promise<Project>)=>task();

describe('research failure and manual recovery',()=>{
  it('shows classified failure, real timestamps, both task IDs and a scoped diagnostic download',()=>{
    render(<JobDetails projectId="project-one" job={{id:'business-one',remote_job_id:'agent-one',stage:'outline',status:'failed',created_at:'2026-10-31T01:00:00Z',diagnostic:{category:'validation',code:'workflow_evidence_source_mismatch',reason:'证据引用不属于本次纳入来源。',step:'结构与来源校验',retryable:false,suggested_action:'请核对证据来源后重新整理。',started_at:'2026-10-31T01:01:00Z',last_progress_at:'2026-10-31T01:05:00Z'}}}/>);
    expect(screen.getByText('证据引用不属于本次纳入来源。')).toBeInTheDocument();
    expect(screen.getByText('workflow_evidence_source_mismatch')).toBeInTheDocument();
    expect(screen.getByText('business-one')).toBeInTheDocument();expect(screen.getByText('agent-one')).toBeInTheDocument();
    expect(screen.getByText('请先处理原因')).toBeInTheDocument();
    expect(screen.getByRole('link',{name:'下载脱敏诊断'})).toHaveAttribute('href','/api/v1/research/projects/project-one/jobs/business-one/diagnostic');
    expect(screen.getByText(/最近进展/)).not.toHaveTextContent('尚未记录');
  });
  it('distinguishes queue time from an unstarted execution and does not echo arbitrary raw errors',()=>{
    render(<JobDetails projectId="p" job={{id:'job',status:'queued',created_at:'2026-10-31T01:00:00Z',error:'https://private-host/secret?token=private'}}/>);
    expect(screen.getByText('实际开始：尚未开始')).toBeInTheDocument();
    expect(screen.getByText('最近进展：尚未记录')).toBeInTheDocument();
    expect(screen.queryByText(/private-host/)).not.toBeInTheDocument();
  });
  it('keeps a stale evidence version openable after regeneration fails, with version-specific exports',async()=>{
    const p=project();const saved={id:'saved-evidence',input_revision:2,stale:true,content:{evidence:[{evidence_id:'e1',paper_id:'source-one',finding:'上一版的真实证据',access_level:'abstract'}],sections:[{section_id:'s1',title:'原大纲',questions:['原问题'],evidence_ids:['e1']}],gaps:['仅摘要']}};
    p.evidence_history=[saved];p.jobs=[{id:'failed-outline',status:'failed',stage:'outline'}];vi.spyOn(api,'evidence').mockResolvedValue(saved);
    render(<EvidenceHistory project={p}/>);await userEvent.click(screen.getByRole('button',{name:/证据版本 1/}));
    await screen.findByText(/上一版的真实证据/);expect(screen.getByText(/不会直接作为新范围/)).toBeInTheDocument();
    expect(screen.getByRole('link',{name:'下载此版 Markdown'})).toHaveAttribute('href','/api/v1/research/projects/project-one/evidence/saved-evidence/download?format=markdown');
    expect(screen.queryByRole('button',{name:/采用|确认/})).not.toBeInTheDocument();
  });
  it('requires two explicit acknowledgements before importing a report, preserving provenance',async()=>{
    const p=project();const action=vi.spyOn(api,'action').mockImplementation(async()=>({...p,versions:[{id:'import-one',origin:'user_import',model_trace_available:false}]}));
    const imported=vi.fn();render(<ReportImport project={p} busy={false} mutate={mutation} onImported={imported}/>);
    await userEvent.click(screen.getByRole('button',{name:'导入 Markdown 或手工填写'}));
    fireEvent.change(screen.getByRole('textbox',{name:'导入的报告正文（Markdown）'}),{target:{value:'# 恢复稿\n\n依据摘要整理。'}});
    const submit=screen.getByRole('button',{name:'保存为用户导入报告候选'});expect(submit).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox',{name:/我已核对稿件中的来源/}));expect(submit).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox',{name:/我理解并保留当前证据缺口/}));await userEvent.click(submit);
    await waitFor(()=>expect(action).toHaveBeenCalledWith(p,'import_report',{report_markdown:'# 恢复稿\n\n依据摘要整理。',origin:'user_import',source_reviewed:true,allow_limited_evidence:true,unresolved_notes:''}));
    expect(imported).toHaveBeenCalledWith('import-one');expect(action).toHaveBeenCalledTimes(1);
  });
  it('records review against the selected imported version without claiming model success',async()=>{
    const p=project();const v={id:'import-one',origin:'user_import',model_trace_available:false};p.versions=[v];const action=vi.spyOn(api,'action').mockResolvedValue(p);
    render(<ImportReview project={p} version={v} busy={false} mutate={mutation}/>);const submit=screen.getByRole('button',{name:'确认人工恢复稿审阅'});expect(submit).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox',{name:/我已审阅这个版本/}));expect(submit).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox',{name:/我理解并接受已记录/}));await userEvent.click(submit);
    expect(action).toHaveBeenCalledWith(p,'confirm_import',{version_id:'import-one',source_reviewed:true,allow_limited_evidence:true,unresolved_notes:''});
  });
  it('saves a manually acknowledged limited outline through a distinct action',async()=>{
    const p=project();const action=vi.spyOn(api,'action').mockResolvedValue(p);render(<ManualEvidence project={p} busy={false} mutate={mutation}/>);
    await userEvent.click(screen.getByRole('button',{name:'开始人工整理'}));await userEvent.click(screen.getByRole('button',{name:'添加大纲章节'}));
    fireEvent.change(screen.getByRole('textbox',{name:'人工第 1 节标题'}),{target:{value:'研究范围与证据缺口'}});
    fireEvent.change(screen.getByRole('textbox',{name:'明确保留的证据缺口（每行一项）'}),{target:{value:'尚无可核对全文'}});
    const save=screen.getByRole('button',{name:'保存人工证据版本'});expect(save).toBeDisabled();await userEvent.click(screen.getByRole('checkbox',{name:/我已核对来源与实际访问范围/}));await userEvent.click(save);
    expect(action).toHaveBeenCalledWith(p,'manual_evidence',{outline:expect.objectContaining({evidence:[],gaps:['尚无可核对全文']}),allow_limited_evidence:true});
  });
  it('sends every manual evidence field as a nonempty string and excludes uploaded manuscripts',async()=>{
    const p=project();p.materials=[{id:'official-doc',name:'官方文档.md',status:'ready',material_role:'research_source'},{id:'draft',name:'恢复稿.md',status:'ready',material_role:'draft'}];const action=vi.spyOn(api,'action').mockResolvedValue(p);
    render(<ManualEvidence project={p} busy={false} mutate={mutation}/>);await userEvent.click(screen.getByRole('button',{name:'开始人工整理'}));await userEvent.click(screen.getByRole('button',{name:'添加证据'}));
    expect(screen.queryByRole('option',{name:'恢复稿.md'})).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox',{name:'证据 1 发现'}),{target:{value:'官方文档描述了执行入口'}});fireEvent.change(screen.getByRole('textbox',{name:'原文位置'}),{target:{value:'第3节'}});fireEvent.change(screen.getByRole('textbox',{name:'证据限制（必填，可换行）'}),{target:{value:'仅依据上传的文档版本'}});
    await userEvent.click(screen.getByRole('button',{name:'添加大纲章节'}));fireEvent.change(screen.getByRole('textbox',{name:'人工第 1 节标题'}),{target:{value:'执行入口'}});await userEvent.click(screen.getByRole('checkbox',{name:/我已核对来源与实际访问范围/}));await userEvent.click(screen.getByRole('button',{name:'保存人工证据版本'}));
    const payload=action.mock.calls[0]?.[2];const output=payload?.outline as {evidence:Record<string,unknown>[]};
    expect(Object.keys(output.evidence[0]).sort()).toEqual(['access_level','evidence_id','finding','limitations','locator','method','paper_id']);
    expect(Object.values(output.evidence[0]).every(value=>typeof value==='string'&&value.trim())).toBe(true);
    expect(output.evidence[0].limitations).toBe('仅依据上传的文档版本');
  });
  it('marks a recovered draft as a manuscript instead of an independent research source',async()=>{
    const p=project();const material={id:'recovery-file',name:'恢复稿.md',status:'ready',material_role:'reference_material'};const action=vi.spyOn(api,'action').mockResolvedValue(p);
    render(<MaterialSettings project={p} material={material} busy={false} mutate={mutation}/>);
    await userEvent.click(screen.getByRole('button',{name:'设置资料类型与内部来源'}));await userEvent.selectOptions(screen.getByRole('combobox',{name:'资料用途'}),'draft');
    expect(screen.getByText(/稿件不会自动计作新的研究证据/)).toBeInTheDocument();const save=screen.getByRole('button',{name:'保存资料类型与来源声明'});expect(save).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox',{name:/我已核对资料类型/}));await userEvent.click(save);
    expect(action).toHaveBeenCalledWith(p,'classify_material',{material_id:'recovery-file',classification:'draft'});
  });
  it('requires an explicit source mapping for a bundle instead of assuming every paper is full text',async()=>{
    const p=project();const action=vi.spyOn(api,'action').mockResolvedValue(p);
    render(<MaterialSettings project={p} material={{id:'pack',name:'资料合集.md',status:'ready'}} busy={false} mutate={mutation}/>);
    await userEvent.click(screen.getByRole('button',{name:'设置资料类型与内部来源'}));await userEvent.selectOptions(screen.getByRole('combobox',{name:'资料用途'}),'source_bundle');
    await userEvent.click(screen.getByRole('button',{name:'添加文件内来源'}));fireEvent.change(screen.getByRole('textbox',{name:'内部来源 1 标题'}),{target:{value:'论文摘要'}});
    fireEvent.change(screen.getByRole('spinbutton',{name:'起始行（从 1 开始）'}),{target:{value:'3'}});fireEvent.change(screen.getByRole('spinbutton',{name:'结束行（包含本行）'}),{target:{value:'8'}});
    await userEvent.click(screen.getByRole('checkbox',{name:/我已核对资料类型/}));await userEvent.click(screen.getByRole('button',{name:'保存资料类型与来源声明'}));
    expect(action).toHaveBeenCalledWith(p,'classify_material',{material_id:'pack',classification:'source_bundle',declared_sources:[{title:'论文摘要',access_level:'abstract',source_type:'paper',start_line:3,end_line:8}]});
  });
});

describe('source transparency',()=>{
  it('expands all recorded authors and distinguishes initial, version and journal dates',async()=>{
    render(<dl><BibliographicDetails paper={{paper_id:'p',title:'综述',authors:Array.from({length:26},(_,i)=>`作者${i+1}`),bibliography:{author_list_status:'complete',author_count:26,source_type:'preprint',version:'v3',journal_reference:'2026 journal',dates:{first_posted:'2025-04-01',version_updated:'2026-10-25',journal_published:'2026',online_published:null,print_published:null}}}}/></dl>);
    expect(screen.getByText('完整名单，共 26 位')).toBeInTheDocument();expect(screen.queryByText(/作者26/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button',{name:'展开已记录的 26 位作者'}));expect(screen.getByText(/作者26/)).toBeInTheDocument();
    expect(screen.getByText('2025-04-01')).toBeInTheDocument();expect(screen.getByText('2026-10-25')).toBeInTheDocument();expect(screen.getByText('2026')).toBeInTheDocument();
  });
  it('labels historical author completeness as unknown instead of claiming a full list',()=>{
    render(<dl><BibliographicDetails paper={{paper_id:'p',title:'旧来源',authors:['作者甲','作者乙']}}/></dl>);
    expect(screen.getByText('已记录 2 位，完整性未确认')).toBeInTheDocument();
  });
  it('explains exclusion and the topics still lacking evidence when the search budget is exhausted',()=>{
    const p=project();p.retrieval_summary={calls_used:3,call_budget:3,budget_exhausted:true,providers:[{provider:'crossref',query:'DSH Harness',status:'completed',result_count:9}],topics_without_evidence:['架构实证对照'],gaps:['需补充工程文档']};
    render(<><QualityNote assessment={{default_included:false,reasons:[{code:'out_of_range',message:'来源年份早于2022年'}],warnings:[],matched_terms:[],year_range:[2022,2026],assessed_years:[2000],method:'lexical_screening_v1'}}/><RetrievalDetails project={p}/></>);
    expect(screen.getByText('默认不纳入')).toBeInTheDocument();expect(screen.getByText('来源年份早于2022年')).toBeInTheDocument();expect(screen.getByText(/检索预算已用完/)).toBeInTheDocument();expect(screen.getByText('DSH Harness')).toBeInTheDocument();expect(screen.getByText('架构实证对照')).toBeInTheDocument();
  });
});
