import type { Job } from './domain';

export function QualityDiagnosticNotice({job}:{job:Job}) {
 if(job.status!=='failed'||job.error_code!=='quality_gate_failed')return null;
 const diagnostic=job.quality_diagnostics;
 return <section className="notice warning" aria-label="质量检查诊断"><div>
  <strong>{diagnostic?.title??'质量检查尚未通过'}</strong>
  <p>本次尚未形成新的可预览或下载版本，原资料和已确认方案仍保留。</p>
  {diagnostic?<>
   <p className="plain-text">{diagnostic.message}</p>
   <p className="plain-text">{diagnostic.suggestion}</p>
   <p><small>检查项：<code>{diagnostic.error_code}</code></small></p>
   <a className="secondary compact" href={diagnostic.diagnostic_download_url} download={`ppt-quality-diagnostic-${job.id}.json`}>下载诊断报告（JSON）</a>
  </>:<p>此任务尚无可显示的具体诊断。请联系管理员并提供上方任务号，以核对服务端检查结果后定向修复。</p>}
 </div></section>;
}
