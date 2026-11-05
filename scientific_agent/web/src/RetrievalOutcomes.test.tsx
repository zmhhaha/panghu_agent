import {render,screen,within} from '@testing-library/react';
import {describe,expect,it} from 'vitest';
import {emptyRequirements,type Project,type QueryOutcome} from './domain';
import {RetrievalDetails} from './SourceDetails';

function project(outcomes:QueryOutcome[],missing:string[]=[]):Project {
  const initial={provider:'arxiv',query:'DSH OR "DeepSeek-Harness"',status:'success',result_count:0};
  const simple={provider:'arxiv',query:'DeepSeek Harness',status:'success',result_count:3};
  return {id:'p',title:'架构调研',revision:3,stage:'evidence',requirements:emptyRequirements,materials:[],plan:null,confirmations:{plan:null,evidence:null},search_batches:[],evidence:null,outline:null,versions:[],current_version_id:null,finalizations:[],annotations:[],jobs:[],history:[],retrieval_summary:{calls_used:3,call_budget:3,budget_exhausted:true,cumulative_calls:6,batch_count:2,current_batch_id:'second',providers:[initial,simple],topics_without_evidence:missing,gaps:[],query_outcomes:outcomes,batches:[{batch_id:'first',calls_used:3,call_budget:3,providers:[initial],gaps:['首次复杂查询返回 0 项']},{batch_id:'second',calls_used:3,call_budget:3,providers:[simple,{provider:'arxiv',query:'AutoGen',status:'success',result_count:4}],gaps:[]}]}};
}

describe('retrieval query history',()=>{
  it('shows original zero-result queries beside the recorded simpler follow-up results without claiming topic completeness',()=>{
    render(<RetrievalDetails project={project([
      {planned_query:'DSH OR "DeepSeek-Harness"',status:'relaxed_result',matched_searches:[{query:'DeepSeek Harness',provider:'arxiv',result_count:3,batch_id:'second',match:'named_entity'}],unmatched_entities:[]},
      {planned_query:'AutoGen AND (architecture OR AgentChat)',status:'relaxed_result',matched_searches:[{query:'AutoGen',provider:'arxiv',result_count:4,batch_id:'second',match:'named_entity'}],unmatched_entities:[]}
    ])}/>);
    const linked=screen.getByRole('region',{name:'原计划查询与实际补检'});
    const first=within(linked).getByRole('article',{name:'计划查询 1'});
    expect(first).toHaveTextContent('原计划查询：DSH OR "DeepSeek-Harness"');
    expect(first).toHaveTextContent('实际查询：DeepSeek Harness · 简化检索（词法关联） · arxiv · 检索批次 2 · 返回 3 项');
    expect(within(linked).getByRole('article',{name:'计划查询 2'})).toHaveTextContent('AutoGen · 简化检索（词法关联） · arxiv · 检索批次 2 · 返回 4 项');
    expect(linked).toHaveTextContent('不代表原查询的全部限定条件已满足，也不证明科学主题完整');
    expect(screen.getByText('首次复杂查询返回 0 项')).toBeInTheDocument();
    expect(screen.getByText('success · 0 项')).toBeInTheDocument();
    expect(screen.getByText('当前批预算').closest('p')).toHaveTextContent('已用 3 / 3 次检索');
    expect(screen.getByText('累计调用').closest('p')).toHaveTextContent('6 次；已记录 2 批');
    expect(screen.queryByText('当前计划仍待补检的查询')).not.toBeInTheDocument();
  });

  it('keeps the unmet entity and plan-query gap when only part of a multi-project AND query has results',()=>{
    const query='AutoGen AND LangGraph';
    render(<RetrievalDetails project={project([{planned_query:query,status:'partial_relaxed_result',matched_searches:[{query:'AutoGen',provider:'arxiv',result_count:4,batch_id:'second',match:'named_entity'}],unmatched_entities:['LangGraph']}],[query])}/>);
    const linked=screen.getByRole('region',{name:'原计划查询与实际补检'});
    expect(linked).toHaveTextContent('部分实体已有简化检索结果，仍有查询缺口');
    expect(linked).toHaveTextContent('尚无对应成功查询记录的实体：LangGraph');
    expect(linked).toHaveTextContent('已有实体的结果不替代这些缺口');
    expect(screen.getByText('当前计划仍待补检的查询')).toBeInTheDocument();
    expect(within(linked).queryByText('简化检索已有返回记录（词法关联）')).not.toBeInTheDocument();
  });
});
