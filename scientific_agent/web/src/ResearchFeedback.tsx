import {useState} from 'react';
import {api} from './api';
import type {Job,Project,Version} from './domain';
import {researchTime} from './time';

const completed=(job:Job)=>['completed','succeeded'].includes(job.status);

export function currentFinalized(project:Project):boolean {
  const current=project.versions.find(item=>item.id===project.current_version_id);
  return Boolean(current&&!current.stale&&(typeof current.input_revision!=='number'||typeof project.input_revision!=='number'||current.input_revision===project.input_revision)&&project.finalizations.some(item=>item.version_id===current.id));
}

// The result ID is the receipt for this task. Array position alone cannot
// identify an asynchronously delivered candidate or a user's current choice.
export function completedRevision(project:Project):Version|undefined {
  const candidates=project.jobs.filter(job=>completed(job)&&(job.action??job.stage)==='revise'&&job.result_version_id)
    .map(job=>({job,version:project.versions.find(version=>version.id===job.result_version_id)}))
    .filter((item):item is {job:Job;version:Version}=>Boolean(item.version&&item.version.id!==project.current_version_id&&item.version.parent_version_id===project.current_version_id&&!item.version.stale&&(typeof item.version.input_revision!=='number'||typeof project.input_revision!=='number'||item.version.input_revision===project.input_revision)));
  candidates.sort((a,b)=>String(b.job.updated_at??b.job.created_at??'').localeCompare(String(a.job.updated_at??a.job.created_at??''))||a.job.id.localeCompare(b.job.id));
  return candidates[0]?.version;
}

export function currentNotice(project:Project,notice:string):string {
  if(notice&&!/已提交|已请求|并提交/.test(notice))return notice;
  if(project.active_job_id)return notice;
  if(currentFinalized(project))return '当前工作版本已定稿，可在“定稿导出”下载。历史任务记录继续保留。';
  const candidate=completedRevision(project);
  if(candidate)return '章节修订已完成，修改候选已保存；请查看差异后决定是否采用。';
  const latest=project.jobs.at(-1);
  if(latest&&completed(latest))return ({plan:'研究方案已保存，请核对问题、范围和检索词后确认。',retrieve:'检索结果已保存，请核对后续证据整理状态。',outline:'证据与大纲已保存，核对并确认后即可写作。',draft:'报告初稿已保存，请进入审阅修订。',revise:'章节修订结果已保存，请在报告版本中核对。'} as Record<string,string>)[latest.action??latest.stage??'']??notice;
  return notice;
}

export function jobNextStep(project:Project,job:Job):string {
  const stage=job.action??job.stage;
  if(stage==='retrieve')return '检索完成并保存后，会自动提交一次证据与大纲整理；整理完成后等待你确认，再开始写作。';
  if(stage==='outline'){
    const retrieved=project.jobs.some(item=>completed(item)&&(item.action??item.stage)==='retrieve');
    return `${retrieved?'检索结果已保存。':''}本次只整理证据与大纲；完成后停下，等待你核对并确认写作。`;
  }
  if(stage==='revise')return '本次只修改选定章节。完成后会显示“修订已完成，查看差异”，原工作版本保留，候选不会自动采用。';
  if(stage==='draft')return '初稿保存后等待审阅，不会自动定稿或公开发布。';
  return '任务结果会保存在项目中。可以离开页面，返回后继续；等待人工确认时不占用执行容器。';
}

type Common={project:Project;busy:boolean;mutate:(task:()=>Promise<Project>,message?:string)=>Promise<Project|null>};
export function OfficialSourceForm({project,busy,mutate}:Common){
  const [url,setUrl]=useState('');
  async function add(){if(await mutate(()=>api.url(project,url.trim()),'官方资料正文已获取并保存，请核对版本、内容和访问范围'))setUrl('');}
  return <div className="official-source-form"><label className="field"><span>官方资料或仓库 URL</span><input type="url" value={url} onChange={event=>setUrl(event.target.value)} disabled={busy} placeholder="https://github.com/组织/仓库 或官方文档页面"/></label><div className="actions"><button disabled={busy||!/^https:\/\//i.test(url.trim())} onClick={add}>获取并保存正文</button></div><p className="muted small">读取公开页面或支持的仓库文件，记录来源、获取时间和可确认的版本。链接不等于已读取正文；无法获取时会明确提示，不会将链接标为全文。</p></div>;
}

export function WritingQuality({version,onRefine}:{version:Version;onRefine?:()=>void}){
  const quality=version.writing_quality;
  if(!quality)return null;
  const warnings=quality.warnings??[];const missing=quality.missing_requirements??[];
  return <section className={`writing-quality message ${warnings.length||missing.length?'warning':'info'}`} aria-label="报告要求与长度核对"><strong>报告要求与长度核对</strong><p>当前正文：{quality.actual_length} {quality.unit??'字'}{quality.target_length?`；目标约 ${quality.target_length} ${quality.unit??'字'}`:''}{typeof quality.length_delta==='number'?`；相对父版${quality.length_delta>=0?'增加':'减少'} ${Math.abs(quality.length_delta)} ${quality.unit??'字'}`:''}。</p>{typeof quality.parent_length==='number'&&<small>父版正文：{quality.parent_length} {quality.unit??'字'}。统计口径以服务端记录为准。</small>}{missing.length>0&&<><strong>尚未覆盖的明确要求</strong><ul>{missing.map((item,index)=><li key={index}>{item}</li>)}</ul></>}{warnings.length>0&&<ul>{warnings.map((item,index)=><li key={index}>{item}</li>)}</ul>}{onRefine&&(warnings.length>0||missing.length>0)&&<button onClick={onRefine}>带入本节精炼意见</button>}</section>;
}

export function ReportDownloads({projectId,version,isFinal}:{projectId:string;version:Version;isFinal:boolean}){
  const status=isFinal?'定稿':'草稿';
  return <div className="report-downloads"><p className="muted small">以下文件均对应当前所选版本{version.created_at?` · ${researchTime(version.created_at)}`:''}；阅读版供分享，原始稿与来源记录保留追溯信息。</p><div className="downloads"><a className="button-link" href={api.download(projectId,version.id,'reading-markdown')}>下载 {status} 阅读版 Markdown</a><a className="button-link" href={api.download(projectId,version.id,'docx')}>下载 {status} Word</a><a className="button-link" href={api.download(projectId,version.id,'pdf')}>下载 {status} PDF</a><a className="button-link" href={api.download(projectId,version.id,'markdown')}>下载 {status} Markdown</a><a className="button-link" href={api.download(projectId,version.id,'sources')}>下载来源记录</a></div></div>;
}

export function ImportReferenceMapping({project,version,busy,mutate,onSaved}:{version:Version;onSaved:()=>void}&Common){
  const [open,setOpen]=useState(false);const [base,setBase]=useState(project.revision);const [consent,setConsent]=useState(false);const [mapping,setMapping]=useState<Record<number,string>>({});
  const citations=version.import_citations;
  if(!citations)return null;
  const references=citations.references;
  const finalized=version.finalized===true||project.finalizations.some(item=>item.version_id===version.id);
  if(finalized)return <section className="card form-card import-reference-mapping"><h3>导入稿引用与来源匹配 · 定稿只读</h3><p>此版本已定稿，引用映射保持只读，原正文和定稿记录继续保留。尚有 {citations.unmatched_numbers.length} 项未匹配，不能视为自动验证成功。</p>{references.map(item=><div className="manual-entry" key={item.n}><strong>引用 {item.n} · {item.match_status==='manually_matched'?'已人工匹配':'未匹配'}</strong><p>{item.text}</p>{item.source_id&&<small>对应来源：{version.sources?.find(source=>source.paper_id===item.source_id)?.title??item.source_id}</small>}</div>)}<p className="message info">需要补齐引用时，{version.id!==project.current_version_id?'先在“报告版本”中选择当前工作版本，再':''}在“手工修改并保存新版本”中点击“编辑此版正文”，保留原文并保存为人工编辑候选。随后在新候选中匹配引用，重新审阅、采用并定稿；原定稿不会被覆盖。</p></section>;
  function toggle(){if(!open){setMapping(Object.fromEntries(references.map(item=>[item.n,item.source_id??''])));setConsent(false);setBase(project.revision);}setOpen(!open);}
  async function save(){const reference_mappings=references.filter(item=>mapping[item.n]).map(item=>({n:item.n,source_id:mapping[item.n]}));const result=await mutate(()=>api.action({...project,revision:base},'map_import_references',{version_id:version.id,reference_mappings}),'导入稿引用映射已保存，请重新审阅来源后再采用或定稿');if(result){setOpen(false);onSaved();}}
  return <section className="card form-card import-reference-mapping"><h3>导入稿引用与来源匹配</h3><p>已识别 {references.length} 项编号参考文献，尚有 {citations.unmatched_numbers.length} 项未匹配。人工匹配不代表模型证据链验证或论文全文核验，不会改写正文、证据和历史任务。</p>{references.length?<><button aria-expanded={open} onClick={toggle} disabled={busy}>{open?'收起引用匹配':'核对并匹配导入稿引用'}</button>{open&&<fieldset disabled={busy}>{references.map(item=><div className="manual-entry" key={item.n}><strong>引用 {item.n} · {item.match_status==='manually_matched'?'已人工匹配':'未匹配'}</strong><p>{item.text}</p><small>正文中识别到 {item.body_occurrences} 处引用。</small><label className="field"><span>引用 {item.n} 对应来源</span><select value={mapping[item.n]??''} onChange={event=>{setMapping({...mapping,[item.n]:event.target.value});setConsent(false);}}><option value="">尚未匹配（保留缺口）</option>{(version.sources??[]).map(source=><option key={source.paper_id} value={source.paper_id}>{source.title}</option>)}</select></label></div>)}<label className="check"><input type="checkbox" checked={consent} onChange={event=>setConsent(event.target.checked)}/><span>我已逐项核对编号与所选来源；未匹配项保持明确缺口，不视为自动校验成功。</span></label><p className="muted">修改匹配会清除此前的导入稿审阅确认，需要重新审阅；报告正文保持不变。</p>{base!==project.revision&&<p className="message warning">项目已更新，请收起后重新打开核对，再保存。</p>}<button className="primary" disabled={!consent||base!==project.revision} onClick={save}>保存引用匹配并重新审阅</button></fieldset>}</>:<p className="muted">没有识别到编号参考文献。请人工核对正文与资料，不会补造引用映射。</p>}</section>;
}
