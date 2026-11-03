import { useEffect, useState } from 'react';
import { api, ApiError, safeError } from './api';
import { label, type Job } from './domain';
import { JobTiming } from './JobTiming';
import { QualityDiagnosticNotice } from './QualityDiagnosticNotice';
import './recent-jobs.css';

function SubmittedAt({value}:{value?:string|null}) {
 const date=typeof value==='string'&&value.trim()?new Date(value):null;
 return date&&Number.isFinite(date.getTime())
  ?<time dateTime={date.toISOString()}>{date.toLocaleString('zh-CN',{hour12:false})}</time>
  :<span>提交时间待核对</span>;
}

export function RecentJobs({projectId,currentJobId,refreshKey}:{projectId:string;currentJobId?:string|null;refreshKey:string}) {
 const [expanded,setExpanded]=useState(false);
 const [jobs,setJobs]=useState<Job[]>([]);
 const [selectedId,setSelectedId]=useState<string|null>(null);
 const [loading,setLoading]=useState(false);
 const [error,setError]=useState<unknown>(null);
 const [read,setRead]=useState(0);
 useEffect(()=>{
  if(!expanded)return;
  const controller=new AbortController();let disposed=false;
  setLoading(true);setError(null);
  void api.jobs(projectId,controller.signal).then(records=>{
   if(disposed)return;
   const visible=records.filter(job=>job.project_id===projectId).slice(0,20);
   setJobs(visible);setSelectedId(previous=>visible.some(job=>job.id===previous)?previous:null);
  }).catch(nextError=>{
   if(disposed)return;
   setJobs([]);setSelectedId(null);setError(nextError);
  }).finally(()=>{if(!disposed)setLoading(false);});
  return()=>{disposed=true;controller.abort();};
 },[expanded,projectId,refreshKey,read]);
 const visibleJobs=jobs.filter(job=>job.project_id===projectId);
 const selected=visibleJobs.find(job=>job.id===selectedId);
 return <details className="recent-jobs" onToggle={event=>setExpanded(event.currentTarget.open)}>
  <summary>最近任务</summary>
  {expanded&&<div className="recent-jobs-body">
   <div className="recent-jobs-heading"><p>查看最近 20 次任务的服务端记录。选择记录只查看详情，不会影响当前任务。</p><button type="button" className="secondary compact" disabled={loading} onClick={()=>setRead(value=>value+1)}>刷新任务记录</button></div>
   {loading&&<p role="status">正在读取任务记录…</p>}
   {!!error&&<div className="notice error" role="alert">{safeError(error)}{error instanceof ApiError&&error.status===401&&<a href="/oauth2/start">重新登录</a>}</div>}
   {!loading&&!error&&!visibleJobs.length&&<p>此项目还没有任务记录。</p>}
   {!!visibleJobs.length&&<div className="recent-jobs-layout">
    <ul className="recent-jobs-list" aria-label="最近任务记录">{visibleJobs.map(job=><li key={job.id}><button type="button" aria-pressed={selectedId===job.id} aria-label={`查看${label(job.kind)}任务 ${job.id}`} onClick={()=>setSelectedId(job.id)}><span><strong>{label(job.kind)}</strong><span className="pill">{label(job.status)}</span></span><small><SubmittedAt value={job.created_at}/>{job.id===currentJobId&&<span> · 当前任务</span>}</small></button></li>)}</ul>
    {selected?<section className="recent-job-detail" aria-label="所选任务详情">
     <h3>{label(selected.kind)} · {label(selected.status)}</h3>
     <p>任务 <code>{selected.id}</code></p>
     <p>这是读取时的任务记录；当前任务的进度在上方更新。</p>
     {selected.stage&&<p>执行阶段：{label(selected.stage)}</p>}
     {selected.error_code&&<div className="notice error" role="alert">{safeError(new ApiError(selected.error_code))}</div>}
     {!!selected.needs_input?.questions.length&&<section className="notice warning" aria-label="历史任务待处理问题"><div><strong>本次制作需要处理的问题</strong><ul>{selected.needs_input.questions.map((question,index)=><li className="plain-text" key={index}>{question}</li>)}</ul></div></section>}
     <QualityDiagnosticNotice job={selected}/><JobTiming job={selected}/>
    </section>:<p className="recent-job-placeholder">选择一条任务，查看提交时间、执行结果或失败诊断。</p>}
   </div>}
  </div>}
 </details>;
}
