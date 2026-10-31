import {useState} from 'react';
import {api} from './api';
import {isActiveJob, taskStageNames, type Job} from './domain';

const timestamp=(value?:string|null)=>value?new Date(value).toLocaleString('zh-CN'):'尚未记录';
const categoryNames:Record<string,string>={timeout:'执行超时',source_failed:'来源获取失败',generation_failed:'模型生成失败',validation_failed:'结构或来源校验失败',save_failed:'成果保存失败',submission_uncertain:'提交状态待核对',execution_failed:'任务执行失败',source_fetch:'来源获取失败',generation:'模型生成失败',validation:'结构或来源校验失败',storage:'成果保存失败',input:'输入装配失败',execution:'任务执行失败'};
const fallback=(job:Job)=>{
  const raw=typeof job.error==='string'?job.error:job.error?.code;
  const code=raw&&/^[a-z][a-z0-9_]{0,99}$/.test(raw)?raw:undefined;
  if(job.status==='timed_out'||code?.includes('timeout'))return {code,reason:'本次执行超过等待时限。',suggested_action:'保留现有成果，检查任务进展后再明确重试。'};
  if(job.status==='invalid_result')return {code,reason:'生成结果未通过结构或来源校验，未替换已保存成果。',suggested_action:'下载诊断并提交任务 ID；也可使用人工恢复入口。'};
  return {code,reason:'本次任务没有完成。具体原因尚未记录，请用任务 ID 关联后台记录。',suggested_action:'查看脱敏诊断和历史成果，确认原因后再重试。'};
};

export function JobTiming({job}:{job:Job}){
  const diagnostic=job.diagnostic;
  const queued=['queued','pending','admitting','admission_uncertain','starting'].includes(job.status);
  return <div className="job-timing"><span>{queued?'提交时间':'创建时间'}：{timestamp(job.created_at)}</span><span>实际开始：{diagnostic?.started_at?timestamp(diagnostic.started_at):queued?'尚未开始':'尚未记录'}</span><span>最近进展：{timestamp(diagnostic?.last_progress_at)}</span>{diagnostic?.step&&<span>当前子步骤：{diagnostic.step}</span>}</div>;
}

export default function JobDetails({projectId,job}:{projectId:string;job:Job}){
  const [copied,setCopied]=useState('');
  const diagnostic={...fallback(job),...job.diagnostic};
  const failed=['failed','timed_out','invalid_result'].includes(job.status);
  async function copy(){try{await navigator.clipboard.writeText(`project=${projectId}\ntask=${job.id}${job.remote_job_id?`\nagent_task=${job.remote_job_id}`:''}${diagnostic.code?`\ncode=${diagnostic.code}`:''}`);setCopied('任务标识已复制');}catch{setCopied('复制未完成，可选中下方任务 ID 手动复制。');}}
  return <section className="job-diagnostic" aria-label={`${taskStageNames[job.stage??job.action??'']??'任务'}诊断`}>
    {failed&&<div className="message warning"><strong>{diagnostic.category?categoryNames[diagnostic.category]??diagnostic.reason:diagnostic.reason}</strong>{diagnostic.category&&<p>{diagnostic.reason}</p>}<dl className="diagnostic-fields"><dt>失败步骤</dt><dd>{diagnostic.step??taskStageNames[job.stage??job.action??'']??'待关联后台记录'}</dd><dt>是否可重试</dt><dd>{typeof diagnostic.retryable==='boolean'?diagnostic.retryable?'可以明确重试':'请先处理原因':'待核对原因'}</dd>{diagnostic.code&&<><dt>错误码</dt><dd><code>{diagnostic.code}</code></dd></>}</dl><p>{diagnostic.suggested_action}</p><p>历史成果保留在项目中；旧证据可从“文献与证据 → 已保存的证据版本”打开。</p></div>}
    <JobTiming job={job}/>
    <dl className="diagnostic-fields"><dt>任务 ID</dt><dd><code>{job.id}</code></dd>{job.remote_job_id&&<><dt>Agent 任务 ID</dt><dd><code>{job.remote_job_id}</code></dd></>}{diagnostic.correlation_id&&diagnostic.correlation_id!==job.id&&<><dt>关联 ID</dt><dd><code>{diagnostic.correlation_id}</code></dd></>}</dl>
    <div className="actions"><button onClick={copy}>复制任务标识</button><a className="button-link" href={api.diagnostic(projectId,job.id)} download>下载脱敏诊断</a></div>{copied&&<small role="status">{copied}</small>}{isActiveJob(job)&&<small>排队等待与实际执行分别记录；没有进展时间时不会用页面刷新时间代替。</small>}
  </section>;
}
