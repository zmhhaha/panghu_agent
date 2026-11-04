import {useState} from 'react';
import type {Paper,Project,QualityAssessment} from './domain';

export function QualityNote({assessment}:{assessment?:QualityAssessment}){
  if(!assessment)return null;
  return <div className="source-quality"><span className="tag">{assessment.default_included?'建议纳入，仍需人工核对':'默认不纳入'}</span>{[...(assessment.reasons??[]),...(assessment.warnings??[])].map((item,index)=><small key={`${item.code}-${index}`}>{item.message}</small>)}{assessment.year_range&&<small>要求年份：{assessment.year_range.map(year=>year??'不限').join('—')}；记录年份：{assessment.assessed_years?.join('、')||'未提供'}</small>}</div>;
}

export function BibliographicDetails({paper}:{paper:Paper}){
  const [expanded,setExpanded]=useState(false);const info=paper.bibliography;
  const authors=paper.authors??[];const displayed=expanded?authors:authors.slice(0,8);
  const count=info?.author_count;const status=info?.author_list_status;
  return <><dt>作者</dt><dd>{displayed.length?displayed.join('、'):'未提供'}{authors.length>8&&!expanded?' 等':''}<small className="metadata-note">{status==='complete'?`完整名单，共 ${count??authors.length} 位`:status==='incomplete'?`名单不完整，已记录 ${authors.length} 位${count?` / 共 ${count} 位`:''}`:`已记录 ${authors.length} 位，完整性未确认`}</small>{authors.length>8&&<button className="quiet" aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>{expanded?'收起作者名单':`展开已记录的 ${authors.length} 位作者`}</button>}</dd>{info?.source_type&&<><dt>来源类型</dt><dd>{{preprint:'预印本',journal:'期刊记录',other:'其他'}[info.source_type]}</dd></>}{info?.version&&<><dt>所引版本</dt><dd>{info.version}</dd></>}{info?.dates?.first_posted&&<><dt>预印本首版</dt><dd>{info.dates.first_posted}</dd></>}{info?.dates?.version_updated&&<><dt>版本更新</dt><dd>{info.dates.version_updated}</dd></>}{info?.dates?.journal_published&&<><dt>期刊发表</dt><dd>{info.dates.journal_published}</dd></>}{info?.dates?.online_published&&<><dt>在线发表</dt><dd>{info.dates.online_published}</dd></>}{info?.dates?.print_published&&<><dt>印刷发表</dt><dd>{info.dates.print_published}</dd></>}{info?.journal_reference&&<><dt>期刊记录</dt><dd>{info.journal_reference}</dd></>}</>;
}

export function RetrievalDetails({project}:{project:Project}){
  const summary=project.retrieval_summary;
  const recorded=project.search_batches.flatMap(batch=>{const value=batch.retrieval_summary??batch.search_summary;return value?[{...value,batch_id:batch.id}]:[];});
  const entries=summary?.batches?.length?summary.batches:recorded.length?recorded:summary?[{...summary,batch_id:summary.current_batch_id??'current'}]:[];
  if(!entries.length)return null;
  const current=entries.find(item=>item.batch_id===summary?.current_batch_id)??entries.at(-1)!;
  const cumulative=summary?.cumulative_calls??(recorded.length===project.search_batches.length&&recorded.length?recorded.reduce((total,item)=>total+item.calls_used,0):undefined);
  const missing=summary?.topics_without_evidence??current.topics_without_evidence??[];
  return <details className="card retrieval-details"><summary>实际检索与预算记录</summary><div className="retrieval-overview"><p><strong>当前批预算</strong>：已用 {current.calls_used} / {current.call_budget} 次检索{current.budget_exhausted?' · 本批额度已用完':''}</p><p><strong>累计调用</strong>：{cumulative===undefined?'尚未提供':`${cumulative} 次`}；已记录 {summary?.batch_count??(project.search_batches.length||entries.length)} 批。</p><p className="muted small">预算按每批独立计算。累计调用、返回记录、去重来源与实际引用是不同数量，不代表主题已完整覆盖。</p></div>{missing.length>0&&<div className="message warning"><strong>当前计划尚无成功结果的查询</strong><ul>{missing.map((topic,index)=><li key={index}>{topic}</li>)}</ul><p>这是检索记录中的待补范围，不等同于完整的主题覆盖评估。</p></div>}{entries.map((item,index)=><section key={item.batch_id}><h4>检索批次 {index+1}{item.batch_id===current.batch_id?' · 当前批':' · 历史记录'}</h4><p>本批已用 {item.calls_used} / {item.call_budget} 次检索{item.budget_exhausted?' · 检索预算已用完':''}</p><div className="table-scroll"><table><thead><tr><th>提供方</th><th>实际检索词</th><th>结果</th></tr></thead><tbody>{(item.providers??[]).map((provider,i)=><tr key={i}><td>{provider.provider}</td><td>{provider.query}</td><td>{provider.status} · {provider.result_count} 项</td></tr>)}</tbody></table></div>{(item.gaps??[]).length>0&&<details><summary>本批当时记录的缺口</summary>{item.gaps?.map((gap,i)=><p className="muted" key={i}>{gap}</p>)}<small>历史空结果保持可查，不能直接代表补检后的当前状态。</small></details>}</section>)}</details>;
}
