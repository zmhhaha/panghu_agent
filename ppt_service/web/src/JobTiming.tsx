import { activeStatuses, type Job } from './domain';

function timestamp(value?: string | null) {
 if (typeof value!=='string'||!value.trim()) return null;
 const date=new Date(value);
 if (!Number.isFinite(date.getTime())) return null;
 return {iso:date.toISOString(),label:date.toLocaleString('zh-CN',{hour12:false})};
}

export function JobTiming({job}:{job:Job}) {
 const created=timestamp(job.created_at),deadline=timestamp(job.deadline);
 const seconds=job.phase_timeout_seconds;
 const budget=typeof seconds==='number'&&Number.isSafeInteger(seconds)&&seconds>0&&seconds<=3600?seconds:null;
 if (!created&&!deadline&&!budget) return null;
 const queued=job.status==='queued';
 return <section role="group" aria-label="任务时间" className="hint">
  {created&&<p>提交时间：<time dateTime={created.iso}>{created.label}</time></p>}
  {queued?<p>正在排队，尚未开始本阶段计时。</p>:deadline&&<p>{activeStatuses.has(job.status)?'本阶段执行截止时间':'本阶段原执行截止时间'}：<time dateTime={deadline.iso}>{deadline.label}</time></p>}
  {budget&&<p>当前阶段配置时限：{budget%60===0?`${budget/60} 分钟`:`${budget} 秒`}，不是预计完成时间。</p>}
  {job.kind==='prepare'&&<p>制作准备完成后会自动进入演示稿制作，新阶段使用新的任务号。</p>}
 </section>;
}
