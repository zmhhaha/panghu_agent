import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, ChevronRight, CircleAlert, FileText, FolderClosed, Layers, LoaderCircle, LogOut, Plus, Presentation, RefreshCw, ShieldCheck, Upload, X } from 'lucide-react';
import { api, ApiError, safeError } from './api';
import { activeStatuses, canGenerate, defaultSettings, hasConfirmedCommunication, label, usageCost, validateFiles, type Communication, type Job, type Profile, type Project, type Settings, type Version } from './domain';
import { useJob } from './useJob';
import { JobTiming } from './JobTiming';
import { planningLabels, restoredDirection } from './planning';
import { useParsePolling } from './useParsePolling';
import { hasObservedParser, hasPendingParses, unreadyParseAssets } from './parse';
import { SourceFiles } from './SourceFiles';
import { AcceptancePanel, type AcceptanceSnapshot, type SavedReviewFeedback } from './acceptance';
import './styles.css';
function ErrorBox({error,onRetry}:{error:unknown;onRetry?:()=>void}) { if(!error)return null;return <div className="notice error" role="alert"><CircleAlert size={18}/><div>{safeError(error)} {error instanceof ApiError&&error.status===401?<a href="/oauth2/start">重新登录</a>:onRetry&&<button className="link" onClick={onRetry}>重试读取</button>}</div></div>; }
function Empty({title,children}:{title:string;children?:React.ReactNode}) {return <div className="empty"><FolderClosed size={30}/><h3>{title}</h3><p>{children}</p></div>;}
function PlainPlanValue({value}:{value:unknown}) {return <pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',fontFamily:'inherit',fontSize:'11px',margin:0}}>{typeof value==='string'?value:JSON.stringify(value,null,2)??'未指定'}</pre>;}
function PlanDetails({title,data}:{title:string;data?:Record<string,unknown>}) {if(!data||!Object.keys(data).length)return null;return <details><summary>{title}</summary><dl>{Object.entries(data).map(([key,value])=><div key={key}><dt>{planningLabels[key]??key}</dt><dd><PlainPlanValue value={value}/></dd></div>)}</dl></details>;}
function CommunicationReview({communication}:{communication?:Communication}) {
 const fields=Object.entries(communication?.fields??{});
 const valueLabels:Record<string,Record<string,string>>={primary_language:{zh:'简体中文','zh-CN':'简体中文'},canvas:{ppt169:'16:9 横屏'},template_choice:{free_design:'自由设计',templates:'服务内置模板'}};
 return <section aria-label="沟通内容">
  {communication?.summary&&<p className="plain-text">{communication.summary}</p>}
  {fields.length>0&&<dl>{fields.map(([key,field])=>{
   const entry=field!==null&&typeof field==='object'&&!Array.isArray(field)?field as Record<string,unknown>:{};
   const raw=Object.hasOwn(entry,'value')?entry.value:field;
   const value=typeof raw==='string'?(valueLabels[key]?.[raw]??(raw.trim()?raw:'未指定')):raw??'未指定';
   return <div key={key}><dt>{planningLabels[key]??key}</dt><dd><PlainPlanValue value={value}/>{[entry.explanation,entry.reason].filter((item):item is string=>typeof item==='string'&&!!item.trim()).map((item,i)=><p className="plain-text" key={i}>{item}</p>)}</dd></div>;
  })}</dl>}
  {!communication?.summary&&!fields.length&&!Object.keys(communication?.details??{}).length&&<p>等待服务提供沟通内容。</p>}
  <PlanDetails title="完整沟通说明" data={communication?.details}/>
 </section>;
}
function ProductionSettings({data}:{data:Record<string,unknown>}) {const keys=['page_count','design_spec_depth','refine_spec','native_charts','visual_review','proactive_speaker_notes','proactive_custom_animations','proactive_narration_audio'];const selected=Object.entries(data).filter(([key])=>keys.includes(key));const visible=selected.length?selected:Object.entries(data).slice(0,6);return <><dl>{visible.map(([key,value])=><div key={key}><dt>{planningLabels[key]??key}</dt><dd><PlainPlanValue value={value}/></dd></div>)}</dl><PlanDetails title="核对完整制作参数" data={data}/></>;}
function Consent({checked,onChange,disabled=false}:{checked:boolean;onChange:(v:boolean)=>void;disabled?:boolean}) {
 if(checked)return <p className="consent" role="status"><ShieldCheck size={18} aria-hidden="true"/><span>本项目已同意使用外部模型，上传、规划、生成和修改无需重复确认。</span></p>;
 return <label className="consent"><input type="checkbox" checked={false} disabled={disabled} onChange={e=>{if(e.target.checked)onChange(true);}}/><span>我同意将本项目必要的正文及图片发送给服务指定的外部模型，用于规划、生成与修改。本项目只需确认一次。请仅上传有权使用的资料。</span></label>;
}
function UsageView({profile,job}:{profile:Profile;job?:Job|null}) {const usage=job?.usage??profile.usage;return <div className="usage"><div><span>已观测输入 / 输出</span><strong>{usage?.input_tokens??'未知'} / {usage?.output_tokens??'未知'} tokens</strong></div><div><span>供应商费用</span><strong>{usageCost(usage)}</strong></div><div><span>用户额度</span><strong>{profile.quota?.enabled ? `剩余 ${profile.quota.remaining??'待核对'} · ${profile.quota.period??'当前周期'}`:'未启用'}</strong></div></div>;}
export default function App() {
 const [profile,setProfile]=useState<Profile|null>(null);const [projects,setProjects]=useState<Project[]>([]);const [selected,setSelected]=useState<string|null>(()=>localStorage.getItem('ppt:selected-project'));const [error,setError]=useState<unknown>(null);const [loading,setLoading]=useState(true);const [creating,setCreating]=useState(false);const [title,setTitle]=useState('');const [busy,setBusy]=useState(false);
 const load=useCallback(async()=>{setLoading(true);setError(null);try{const user=await api.me();setProfile(user);setProjects(await api.projects());}catch(e){setError(e);}finally{setLoading(false);}},[]);
 useEffect(()=>{void load();},[load]);
 const select=(id:string|null)=>{setSelected(id);if(id)localStorage.setItem('ppt:selected-project',id);else localStorage.removeItem('ppt:selected-project');};
 async function create(e:React.FormEvent){e.preventDefault();if(busy||!title.trim())return;setBusy(true);try{const p=await api.create(title.trim());setProjects(old=>[p,...old]);setCreating(false);setTitle('');select(p.id);}catch(e){setError(e);}finally{setBusy(false);}}
 return <div className="app"><header className="topbar"><a className="brand" href="/" onClick={e=>{e.preventDefault();select(null);}}><span className="brand-mark"><Presentation size={22}/></span><span>PPT 工作台<small>从资料到可编辑演示稿</small></span></a><div className="account"><span className="test-badge">开发测试中</span>{profile&&<><span className="user-name">{profile.name}</span><a className="icon-link" href="/oauth2/sign_out" aria-label="退出登录"><LogOut size={18}/></a></>}</div></header>
 <div className="release-banner"><ShieldCheck size={16}/><span>PowerPoint 人工验收待完成，正式发布与邀请尚未开放。</span></div>{profile?.issuer==='urn:ppt:development'&&<div className="development-notice" role="status">本地开发测试账户 · 正式 SSO 未接通；制稿执行器尚未接通，不会调用模型</div>}
 <div className="shell"><aside className="sidebar"><div className="sidebar-heading"><span>私有项目</span><button className="icon-button" aria-label="创建项目" onClick={()=>{setCreating(true);select(null);}}><Plus size={18}/></button></div><button className={`nav-item ${!selected?'selected':''}`} onClick={()=>select(null)}><FolderClosed size={18}/>全部项目 <span>{projects.length}</span></button><nav aria-label="项目列表">{projects.map(p=><button key={p.id} className={`project-link ${selected===p.id?'selected':''}`} onClick={()=>select(p.id)}><FileText size={16}/><span>{p.title}</span><ChevronRight size={14}/></button>)}</nav><div className="sidebar-foot"><ShieldCheck size={17}/><span>项目仅本人可见<br/>资料与版本保留到主动删除</span></div></aside>
 <main><ErrorBox error={error} onRetry={()=>void load()}/>{loading?<div className="loading" role="status"><LoaderCircle className="spin"/>正在读取账号与项目…</div>:!profile?<div className="welcome"><span className="eyebrow">PPT SERVICE</span><h1>让资料成为清晰的表达</h1><p>登录后创建私有项目、确认方案，再生成可编辑演示稿。</p><a className="button primary" href="/oauth2/start">登录工作台 <ArrowRight size={17}/></a></div>:selected?<Workspace key={selected} id={selected} profile={profile} onBack={()=>select(null)} onChanged={()=>void api.projects().then(setProjects).catch(setError)}/>:<>
 <div className="page-heading"><div><span className="eyebrow">YOUR WORKSPACE</span><h1>我的项目</h1><p>整理资料，确认表达方向，逐步完成你的演示稿。</p></div><button className="primary" onClick={()=>setCreating(true)}><Plus size={18}/>新建项目</button></div>
 {creating&&<form className="panel create-form" onSubmit={create}><label>项目名称<input autoFocus required maxLength={120} placeholder="例如：季度产品进展汇报" value={title} onChange={e=>setTitle(e.target.value)}/></label><div className="actions"><button className="secondary" type="button" onClick={()=>setCreating(false)}>取消</button><button className="primary" disabled={busy||!title.trim()}>{busy?'正在创建…':'创建私有项目'}</button></div></form>}
 {projects.length?<div className="project-grid">{projects.map(p=><button className="project-card" key={p.id} onClick={()=>select(p.id)}><span className="card-icon"><Presentation size={24}/></span><span className="pill">{label(p.stage)}</span><h2>{p.title}</h2><p>{p.settings?.purpose||'添加资料，开始规划演示稿'}</p><div><span>{p.settings?.pages??8} 页目标</span><span>进入项目 <ArrowRight size={15}/></span></div></button>)}</div>:<Empty title="从第一个项目开始">上传真实资料，确认内容与设计后再启动生成。项目创建后不会自动调用模型。</Empty>}<UsageView profile={profile}/>
 <div className="process-note"><span>01 资料与要求</span><ArrowRight size={15}/><span>02 沟通与方案确认</span><ArrowRight size={15}/><span>03 生成与修改</span></div></> }</main></div></div>;
}
type UploadState = {phase:'preparing'|'uploading'|'syncing'|'complete'|'stopped';current:number;total:number;fileName:string;received:number};
const uploadPending = (state:UploadState|null) => !!state && ['preparing','uploading','syncing'].includes(state.phase);
function UploadStatus({state,onCancel}:{state:UploadState;onCancel:()=>void}) {
 const pending=uploadPending(state);
 const heading=state.phase==='preparing'?'正在准备上传':state.phase==='uploading'?`正在上传第 ${state.current} / ${state.total} 个文件`:state.phase==='syncing'?'正在更新资料列表':state.phase==='complete'?'资料上传完成':'上传等待已结束';
 return <div className={`upload-status ${state.phase==='stopped'?'upload-stopped':''}`}><div className="upload-feedback" role="status" aria-live="polite" aria-atomic="true">{pending?<LoaderCircle className="spin" size={19}/>:state.phase==='complete'?<Check size={19}/>:<CircleAlert size={19}/>}<div><strong>{heading}</strong>{state.fileName&&pending&&<span className="upload-file">{state.fileName}</span>}<small>{state.received} / {state.total} 个文件已确认收到{state.phase==='stopped'?'；请刷新状态核对后再继续。':state.phase==='syncing'?'；正在同步项目状态。':state.phase==='uploading'?'；请等待服务器确认。':''}</small></div></div>{pending&&<button className="secondary compact" type="button" onClick={onCancel}><X size={14}/>取消等待</button>}</div>;
}
function PlanningJobNotice({job,project,onOpen}:{job:Job;project:Project;onOpen:()=>void}) {
 if(job.kind!=='plan_options')return null;
 if(activeStatuses.has(job.status))return <p>正在规划三套设计方向，尚未制作演示稿。单次执行上限为 20 分钟；完成后仍需选择并确认一个方向，再生成演示稿。</p>;
 if(!['failed','interrupted','cancelled'].includes(job.status))return null;
 const timedOut=!!job.error_code&&new ApiError(job.error_code).code==='TIMEOUT';
 const recovery=hasConfirmedCommunication(project)?'初步沟通仍已确认，可核对后重新提交三套方向规划。':project.plan&&project.plan.input_snapshot_hash!==project.input_snapshot_hash?'资料或设置已变化，请依据最新资料重新规划。':'请核对当前方案；若初步沟通确认已失效，先重新确认沟通内容与设计方式。';
 return <div className="notice warning"><div><strong>{timedOut?'三套设计方向规划超时':'三套设计方向规划未完成'}</strong><p>本次方案未成功交付，也没有生成演示稿。已有资料与规划记录仍保留。{recovery}重试会创建新任务，请核对要求后点击提交。</p><button className="secondary compact" onClick={onOpen}>查看规划与重试</button></div></div>;
}
function Workspace({id,profile,onBack,onChanged}:{id:string;profile:Profile;onBack:()=>void;onChanged:()=>void}) {
 const [project,setProject]=useState<Project|null>(null);const [versions,setVersions]=useState<Version[]>([]);const [loading,setLoading]=useState(true);const [error,setError]=useState<unknown>(null);const [tab,setTab]=useState('materials');const [busy,setBusy]=useState(false);const busyRef=useRef(false);const [localJob,setLocalJob]=useState<string|null>(null);
 const [submittedJob,setSubmittedJob]=useState<{id:string;projectRevision:number}|null>(null);
 const refreshSequence=useRef(0);
 const [uploadState,setUploadState]=useState<UploadState|null>(null);const uploadController=useRef<AbortController|null>(null);
 useEffect(()=>()=>uploadController.current?.abort(),[]);
 const refresh=useCallback(async()=>{const sequence=++refreshSequence.current;try{const p=await api.project(id);const versions=await api.versions(id);if(sequence!==refreshSequence.current)return;setProject(p);setVersions(versions);setUploadState(state=>uploadPending(state)?state:null);setError(null);}catch(e){if(sequence===refreshSequence.current)setError(e);}finally{if(sequence===refreshSequence.current)setLoading(false);}},[id]);
 useEffect(()=>{void refresh();},[refresh]);
 const parsePanel=tab==='materials'||tab==='planning';
 const parsePollingError=useParsePolling(project,parsePanel&&!busy,setProject);
 // The controller can replace prepare with generate without a browser action.
 // Keep that newest job after the project clears its active task at completion.
 useEffect(()=>{if(project?.active_job_id)setLocalJob(project.active_job_id);},[project?.active_job_id]);
 const settled=useCallback(()=>{void refresh();},[refresh]);
 // A just-accepted submission outranks a project read from before its admission.
 // Once the revision advances, the server owns prepare -> generate handoffs.
 const submittedPending=!!submittedJob&&!!project&&project.revision<=submittedJob.projectRevision;
 const latestJob=project?.latest_job??null;
 const trackedJobId=submittedPending?submittedJob.id:project?.active_job_id??(latestJob?(activeStatuses.has(latestJob.status)?latestJob.id:null):localJob);
 const {job:liveJob,setJob,connection,error:jobError,refresh:refreshJob}=useJob(trackedJobId,settled);
 // Terminal history is already authoritative: display it without subscribing,
 // polling or firing onSettled again whenever the project DTO is refreshed.
 const job=trackedJobId?(liveJob?.id===trackedJobId?liveJob:latestJob?.id===trackedJobId?latestJob:null):latestJob;
 const active=!!trackedJobId&&(!job||activeStatuses.has(job.status));
 const perform=async(action:()=>Promise<unknown>)=>{if(busyRef.current)return;busyRef.current=true;setBusy(true);setError(null);try{await action();await refresh();onChanged();}catch(e){setError(e);if(e instanceof ApiError&&['STALE_REVISION','STALE_PLAN','SNAPSHOT_MISMATCH'].includes(e.code)){await refresh();setError(e);}}finally{busyRef.current=false;setBusy(false);}};
 const uploadFiles=async(files:File[])=>{
  if(!project?.external_model_ack||!files.length||busyRef.current||active)return;
  const controller=new AbortController();uploadController.current=controller;
  busyRef.current=true;setBusy(true);setError(null);
  let received=0;let currentIndex=0;let fileName=files[0].name;let syncing=false;
  const progress=(phase:UploadState['phase'])=>setUploadState({phase,current:currentIndex,total:files.length,fileName,received});
  progress('preparing');
  try {
   let current=project;
   for(let index=0;index<files.length;index++){
    const file=files[index];currentIndex=index+1;fileName=file.name;syncing=false;progress('uploading');
    await api.upload(current,file,controller.signal);received++;syncing=true;progress('syncing');
    current=await api.project(id,controller.signal);setProject(current);
   }
   setVersions(await api.versions(id,controller.signal));onChanged();progress('complete');
  } catch(nextError){
   progress('stopped');
   if(received===files.length)setError(new ApiError('UPLOAD_REFRESH_FAILED',nextError instanceof ApiError?nextError.status:0));
   else if(syncing&&received>0)setError(new ApiError('UPLOAD_PARTIAL_REFRESH_FAILED',nextError instanceof ApiError?nextError.status:0));
   else if(nextError instanceof ApiError&&nextError.code==='REQUEST_CANCELLED')setError(new ApiError('UPLOAD_CANCELLED'));
   else if(nextError instanceof ApiError&&nextError.code==='REQUEST_TIMEOUT')setError(new ApiError('UPLOAD_TIMEOUT'));
   else setError(nextError);
  } finally {
   if(uploadController.current===controller)uploadController.current=null;
   busyRef.current=false;setBusy(false);
  }
 };
 const start=async(action:()=>Promise<{job_id:string}>)=>perform(async()=>{const projectRevision=project?.revision??0;const result=await action();refreshSequence.current++;setSubmittedJob({id:result.job_id,projectRevision});setLocalJob(result.job_id);});
 if(loading)return <div className="loading" role="status"><LoaderCircle className="spin"/>正在读取项目…</div>;
 if(!project)return <><button className="link" onClick={onBack}><ArrowLeft size={15}/>返回项目</button><ErrorBox error={error} onRetry={()=>void refresh()}/></>;
 const consent=project.external_model_ack===true;
 const setConsent=(value:boolean)=>{if(value&&!consent)void perform(async()=>{setProject(await api.acknowledge(project));});};
 const disabled=busy||active;
 return <><button className="back link" onClick={onBack}><ArrowLeft size={15}/>全部项目</button><div className="page-heading"><div><span className="eyebrow">PRIVATE PROJECT</span><h1>{project.title}</h1><p><span className="pill">{label(project.stage)}</span><span className="metadata">项目修订 {project.revision}</span></p></div><button className="secondary compact" disabled={busy} onClick={()=>void Promise.all([refresh(),refreshJob()])}><RefreshCw size={16}/>刷新状态</button></div><ErrorBox error={error}/>{parsePanel&&<ErrorBox error={parsePollingError}/>}
 {job&&<section className="job-panel" aria-label="当前任务"><div><span className={`status-dot ${active?'pulse':''}`}/><strong role="status">{label(job.status)}</strong><span>{job.stage?label(job.stage):'等待服务状态'}</span><small>{trackedJobId?connection:''}</small></div><div><code>任务 {job.id}</code>{activeStatuses.has(job.status)&&<button className="secondary compact" disabled={busy||job.status==='cancelling'} onClick={()=>void perform(async()=>{setJob(await api.cancel(job.id));})}>{job.status==='cancelling'?'正在停止…':'取消任务'}</button>}</div>{job.error_code&&<ErrorBox error={new ApiError(job.error_code)}/>}<ErrorBox error={trackedJobId?jobError:null}/><PlanningJobNotice job={job} project={project} onOpen={()=>setTab('planning')}/><JobTiming job={job}/><p>{active?'关闭页面后任务继续执行，返回时会恢复服务端记录。':'最近一次任务的服务端记录；刷新页面后仍可查看结果。'}</p></section>}
 {uploadState&&<UploadStatus state={uploadState} onCancel={()=>uploadController.current?.abort()}/>}
 <div className="tabs" role="tablist" aria-label="项目工作区">{[['materials','资料与要求'],['planning','沟通与方案'],['versions','预览与修改']].map(([key,text],n)=><button key={key} role="tab" aria-selected={tab===key} aria-controls={`panel-${key}`} id={`tab-${key}`} onClick={()=>setTab(key)}><span>0{n+1}</span>{text}{key==='versions'&&<small>{versions.length}</small>}</button>)}</div>
 <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
 {tab==='materials'&&<Materials project={project} disabled={disabled} perform={perform} onUpdate={p=>{setProject(p);}} consent={consent} setConsent={setConsent} upload={uploadFiles} uploadState={uploadState}/>}
 {tab==='planning'&&<Planning project={project} disabled={disabled} consent={consent} setConsent={setConsent} perform={perform} start={start}/>}
 {tab==='versions'&&<Versions project={project} versions={versions} disabled={disabled} consent={consent} setConsent={setConsent} start={start} perform={perform}/>}
 </div><UsageView profile={profile} job={job}/><details className="details"><summary>项目标识与数据管理</summary><p>项目 ID：<code>{project.id}</code></p><p>当前输入快照：<code>{project.input_snapshot_hash||'尚未建立'}</code></p><p>删除将移除资料与历史版本，并请求停止活动任务。</p><button className="danger" disabled={busy} onClick={()=>{if(window.confirm('确认删除此项目及全部资料和版本？此操作无法撤销。'))void perform(async()=>{await api.remove(project);onBack();});}}>删除项目</button></details></>;
}
type Performer=(action:()=>Promise<unknown>)=>Promise<void>;
type Starter=(action:()=>Promise<{job_id:string}>)=>Promise<void>;
function Materials({project,disabled,perform,onUpdate,consent,setConsent,upload,uploadState}:{project:Project;disabled:boolean;perform:Performer;onUpdate:(p:Project)=>void;consent:boolean;setConsent:(v:boolean)=>void;upload:(files:File[])=>Promise<void>;uploadState:UploadState|null}) {
 const [settings,setSettings]=useState<Settings>(project.settings??defaultSettings);const [fileError,setFileError]=useState('');const [dirty,setDirty]=useState(false);useEffect(()=>{setSettings(project.settings??defaultSettings);setDirty(false);},[project.settings?.pages,project.settings?.language,project.settings?.audience,project.settings?.purpose,project.settings?.style,project.settings?.requirements]);
 const change=(key:keyof Settings,value:string|number)=>{setSettings(old=>({...old,[key]:value}));setDirty(true);};
 return <div className="workspace-columns"><section className="panel"><div className="section-title"><div><h2>源资料</h2><p>以资料为依据，不补造事实、数字或引用。</p></div><Upload size={20}/></div><Consent checked={consent} onChange={setConsent} disabled={disabled}/><label className={`upload-zone ${disabled?'disabled':''}`}><Upload size={27}/><strong>{uploadPending(uploadState)?'正在处理本次上传':'选择并上传文件'}</strong><span>Markdown · 文本 PDF · DOCX · PNG · JPEG</span><small>25 MiB / 文件 · 最多 10 个文件 · 合计 100 MiB</small><input aria-label="上传源资料" type="file" multiple accept=".md,.pdf,.docx,.png,.jpg,.jpeg" disabled={disabled||!consent} onChange={e=>{const files=Array.from(e.target.files??[]);e.target.value='';const message=validateFiles(files,project.assets??[]);setFileError(message);if(!message&&files.length)void upload(files);}}/></label>{!consent&&<p className="hint">勾选上方数据传输同意后可上传资料。</p>}{fileError&&<p role="alert" className="field-error">{fileError}</p>}
 <div className="storage">{project.assets?.length??0} / 10 个文件 <span>{((project.assets??[]).reduce((n,a)=>n+a.bytes,0)/1024**2).toFixed(1)} / 100 MiB</span></div>
 <p className="hint">带文本层 PDF 最多 150 页；单文件文本最多 50 万字符。超出请拆分后上传。</p>
 {hasObservedParser(project.assets??[])&&<p className="hint">材料解析采用隔离本地沙箱；下方状态来自服务端，不会调用模型。</p>}{hasPendingParses(project.assets??[])&&<p className="hint">在“资料与要求”和“沟通与方案”每 5 秒更新解析状态；也可点击“刷新状态”。离开这两个页面或隐藏页面时暂停刷新。</p>}
  {project.assets?.length?<><div className="asset-list-heading"><h3>已上传文件</h3><small>点击“删除文件”后确认</small></div><SourceFiles project={project} disabled={disabled} perform={perform}/>{disabled&&<p className="hint">项目有活动任务或正在提交，完成后可删除文件。</p>}</>:<Empty title="尚未添加资料">上传后会显示真实解析状态和可用摘要。</Empty>}
 </section><section className="panel"><div className="section-title"><div><h2>演示要求</h2><p>修改资料或设置后，需要重新确认方案。</p></div></div><form onSubmit={e=>{e.preventDefault();void perform(async()=>onUpdate(await api.update(project,settings)));}}><fieldset disabled={disabled}><div className="form-grid"><label>目标页数<input type="number" min={3} max={20} required value={settings.pages} onChange={e=>change('pages',Number(e.target.value))}/><small>3–20 页，默认建议 8 页</small></label><label>语言<select value={settings.language} onChange={e=>change('language',e.target.value)}><option>简体中文</option><option>繁體中文</option><option>English</option></select></label></div><label>目标受众<input maxLength={300} placeholder="例如：产品团队、业务负责人" value={settings.audience} onChange={e=>change('audience',e.target.value)}/></label><label>演示用途<input maxLength={300} placeholder="例如：方案评审、工作汇报" value={settings.purpose} onChange={e=>change('purpose',e.target.value)}/></label><label>视觉与表达风格<input maxLength={300} value={settings.style} onChange={e=>change('style',e.target.value)}/></label><label>补充要求<textarea rows={4} maxLength={10000} placeholder="希望强调的内容、不能遗漏的信息…" value={settings.requirements} onChange={e=>change('requirements',e.target.value)}/></label><button className="primary" disabled={disabled||!dirty}>保存要求</button>{dirty&&<span className="hint">设置尚未保存</span>}</fieldset></form></section></div>;
}
function Planning({project,disabled,consent,setConsent,perform,start}:{project:Project;disabled:boolean;consent:boolean;setConsent:(v:boolean)=>void;perform:Performer;start:Starter}) {
 const plan=project.plan;const [feedback,setFeedback]=useState('');const [designMode,setDesignMode]=useState('free');const [template,setTemplate]=useState('');const [direction,setDirection]=useState('');
 const parseBlockers=unreadyParseAssets(project.assets??[]);const parseBlocked=parseBlockers.length>0;
 const communicationConfirmed=hasConfirmedCommunication(project);
 useEffect(()=>{setDirection(restoredDirection(project));setTemplate('');},[plan?.revision,plan?.confirmed_direction_id,project.confirmed_plan_revision,project.input_snapshot_hash]);const stale=!!plan&&plan.input_snapshot_hash!==project.input_snapshot_hash;const validDirections=plan?.directions?.length===3&&plan.directions.every(d=>d.title&&d.pages.length>0);
 const needsConfirmation=plan?.stage==='communication'&&!stale&&!communicationConfirmed&&!feedback.trim();
 return <div className="planning"><section className="panel"><div className="section-title"><div><span className="eyebrow">PLAN BEFORE PRODUCTION</span><h2>{!plan?'先明确内容，再确定设计':plan.stage==='communication'?'第一次确认 · 沟通与设计方式':'第二次确认 · 选择完整方案'}</h2><p>每次确认都绑定具体方案修订与资料快照，服务端核验后才会生效。</p></div></div><Consent checked={consent} onChange={setConsent} disabled={disabled}/>{stale&&<div className="notice warning" role="status">资料或设置已变化，当前方案确认已失效，请重新规划。</div>}
 {!!project.assets?.length&&<section className="planning-materials" aria-label={parseBlocked?'待处理资料解析状态':'项目文件管理'}><div className="asset-list-heading"><h3>已上传文件</h3><small>{project.assets.length} 个文件 · 可直接删除</small></div>{parseBlocked&&<div className="notice warning"><CircleAlert size={18}/><div><strong>资料尚未全部解析完成</strong><p>请先等待解析完成，或在下方删除不再使用的文件，再开始规划。可点击“刷新状态”核对；添加文件请返回“资料与要求”。</p></div></div>}<SourceFiles project={project} disabled={disabled} perform={perform} showSummary={false}/>{disabled&&<p className="hint">项目有活动任务或正在提交，完成后可删除文件。</p>}</section>}
  {plan?.stage==='communication'&&plan.confirmed_at&&!communicationConfirmed&&!stale&&<div className="notice warning" role="status"><div><strong>初步沟通需要重新确认</strong><p>已有沟通记录仍保留，但当前项目的确认状态已失效。请重新确认下方沟通内容与设计方式，再生成三套设计方向；确认本身不会启动模型任务。</p></div></div>}
  {communicationConfirmed&&<div className="notice"><p>初步沟通已确认。点击“生成三套设计方向”后仅生成方案；随后还需主动选择一个方向、确认所选完整方案，再点击“生成演示稿”。</p></div>}
  {plan?.stage==='directions'&&<div className="notice"><p>三套设计方向是规划结果。请先选择一个方向，点击“确认所选完整方案”，再点击“生成演示稿”；规划完成不会自动制作演示稿。</p></div>}
  {plan?.stage==='directions'&&plan.recommendation&&<div className="notice"><div><strong>系统推荐仅供参考，不代表用户确认</strong><p>{plan.directions?.find(d=>d.id===plan.recommendation?.direction_id)?.title??plan.recommendation.direction_id} · {plan.recommendation.reason}</p><p>请阅读三套完整方案后主动点选；推荐不会自动确认或生成。</p></div></div>}
  {!plan?<Empty title="还没有规划方案">添加资料并保存要求后，提交规划。模型任务仅在你明确点击后创建。</Empty>:<><div className="snapshot"><span>方案修订 <code>{plan.revision}</code></span><span>输入快照 <code>{plan.input_snapshot_hash}</code></span></div>{!!plan.questions?.length&&<div className="notice warning"><div><strong>需要补充的信息</strong><ul>{plan.questions.map((q,i)=><li key={i}>{q}</li>)}</ul><p>可在下方反馈回答，或返回资料补充文件。不会自动联网补齐。</p></div></div>}
 {plan.stage==='communication'?<div className="communication"><CommunicationReview communication={plan.communication}/><fieldset disabled={disabled||stale}><legend>设计方式</legend><label className="radio-card"><input type="radio" name="design" checked={designMode==='free'} onChange={()=>setDesignMode('free')}/><span><strong>自由设计</strong><small>依据资料与要求提出三套完整方向</small></span></label><label className="radio-card"><input type="radio" name="design" checked={designMode==='template'} onChange={()=>setDesignMode('template')} disabled={!plan.communication?.template_options?.length}/><span><strong>服务内置模板</strong><small>{plan.communication?.template_options?.length?'从服务提供的已验收模板选择':'当前方案没有可用模板'}</small></span></label>{designMode==='template'&&<label>选择模板<select value={template} onChange={e=>setTemplate(e.target.value)}><option value="">请选择</option>{plan.communication?.template_options.map(t=><option key={t}>{t}</option>)}</select></label>}<button className="primary" disabled={disabled||stale||(designMode==='template'&&!template)} onClick={()=>void perform(()=>api.confirm(project,{design_mode:designMode==='free'?'free_design':'managed_template',template_id:designMode==='template'?template:null}))}><Check size={17}/>确认沟通内容与设计方式</button></fieldset></div>:<>{!validDirections?<div className="notice warning">服务尚未返回三套完整方向，暂不能完成第二次确认。</div>:<div className="direction-grid">{plan.directions!.map((d,i)=><article key={d.id} className={`direction-card ${direction===d.id?'chosen':''}`}><div className="direction-heading"><span>方向 0{i+1}</span><input aria-label={`选择${d.title}`} type="radio" name="direction" checked={direction===d.id} disabled={disabled||stale} onChange={()=>setDirection(d.id)}/></div><h3>{d.title}</h3><code>{d.id}</code><p className="plain-text">{d.description}</p><PlanDetails title="完整设计说明" data={d.details}/>{!!d.page_expression?.length&&<><h4>逐页视觉表达</h4>{d.page_expression.map((text,index)=><p className="plain-text" key={index}>{text}</p>)}</>}<h4>逐页计划</h4><ol className="page-plan">{d.pages.map(page=><li key={page.number}><strong>{page.number.toString().padStart(2,'0')} · {page.title}</strong>{page.purpose&&<p>{page.purpose}</p>}{page.points?.map((point,n)=><p key={n}>{point}</p>)}{page.sources?.length&&<small>来源：{page.sources.join('、')}</small>}<PlanDetails title={`第 ${page.number} 页完整说明`} data={page.details}/></li>)}</ol><h4>制作设置</h4><ProductionSettings data={d.production_settings}/></article>)}</div>}<div className="actions"><button className="primary" disabled={disabled||stale||!validDirections||!direction} onClick={()=>void perform(()=>api.confirm(project,{direction_id:direction}))}><Check size={17}/>确认所选完整方案</button>{canGenerate(project)&&<span className="confirmed"><Check size={16}/>服务端已确认当前修订</span>}</div></> }</>}
  <div className="planning-feedback"><label>{plan?'补充信息或规划反馈':'规划补充要求'}<textarea rows={3} value={feedback} maxLength={10000} onChange={e=>setFeedback(e.target.value)} placeholder="回答缺失信息，或说明需要调整的大纲与风格…" disabled={disabled}/></label><div className="actions"><button className={canGenerate(project)?'secondary':'primary'} disabled={disabled||!consent||!(project.assets?.length)||parseBlocked||needsConfirmation} onClick={()=>{if(!parseBlocked&&!needsConfirmation)void start(()=>api.plan(project,feedback,consent));}}>{stale?'依据最新资料重新规划':needsConfirmation?'请先确认沟通内容':communicationConfirmed&&!feedback.trim()?'生成三套设计方向':plan?'提交反馈并重新规划':'开始规划'}<ArrowRight size={16}/></button>{canGenerate(project)&&<button className="primary" disabled={disabled||!consent} onClick={()=>void start(()=>api.generate(project,consent))}><Presentation size={17}/>生成演示稿</button>}</div><p className="hint">{disabled?'项目有活动任务或正在提交，请等待当前操作完成。':!consent?'需要先同意外部模型数据传输。':!project.assets?.length?'请先上传源资料。':parseBlocked?'请先等待资料解析完成，或处理失败材料，再开始规划。':stale?'将依据当前资料重新进行初步沟通，无需确认过期方案。':needsConfirmation?'请先确认沟通内容与设计方式，或填写反馈重新规划。':canGenerate(project)?'已确认的输入快照可用于生成。':'生成前须完成两次确认。'}</p></div></section></div>;
}
function Versions({project,versions,disabled,consent,setConsent,start,perform}:{project:Project;versions:Version[];disabled:boolean;consent:boolean;setConsent:(v:boolean)=>void;start:Starter;perform:Performer}) {
 const [base,setBase]=useState('');const [pages,setPages]=useState<number[]>([]);const [feedback,setFeedback]=useState('');const [perPage,setPerPage]=useState<Record<string,string>>({});const [scope,setScope]=useState('whole');
 const [reviewSnapshot,setReviewSnapshot]=useState<AcceptanceSnapshot|null>(null);
 const [reviewNotice,setReviewNotice]=useState('');const revisionForm=useRef<HTMLElement|null>(null);
 const lastReview=useRef<AcceptanceSnapshot|null>(null);
 const version=versions.find(v=>v.id===base);const review=version?.status==='review';
 const canRevise=!!version&&!review&&version.can_revise!==false;
 const currentReview=reviewSnapshot?.project_id===project.id&&reviewSnapshot.version_id===base?reviewSnapshot:null;
 const canReviewRevise=!!review&&currentReview?.can_review===true;
 const observeReview=useCallback((value:AcceptanceSnapshot|null)=>{
  if(value&&(value.project_id!==project.id||value.version_id!==base))return;
  const previous=lastReview.current;
  if(value){
   if(previous&&JSON.stringify(previous.binding)!==JSON.stringify(value.binding)){setPages([]);setPerPage({});setFeedback('');setScope('whole');setReviewNotice('审核稿来源已变化，未提交的修改意见已清空，请核对当前来源后重新填写。');}
   else if(previous&&previous.revision!==value.revision){setReviewNotice('检查记录已更新，请核对修改意见后提交。');}
   lastReview.current=value;
  }
  setReviewSnapshot(value);
 },[base,project.id]);
 const useSavedFeedback=(value:SavedReviewFeedback)=>{if(!currentReview||value.reviewRevision!==currentReview.revision||JSON.stringify(value.binding)!==JSON.stringify(currentReview.binding))return;setScope('pages');setPages(value.pages);setPerPage({});setFeedback(value.feedback);setReviewNotice(`已带入第 ${value.pages.join('、')} 页的已保存问题，请核对修改意见后提交。`);revisionForm.current?.scrollIntoView?.({behavior:'smooth',block:'start'});};
 const changeBase=(value:string)=>{setBase(value);setPages([]);setPerPage({});setFeedback('');setScope('whole');setReviewSnapshot(null);setReviewNotice('');lastReview.current=null;};
 const revisionFeedback=[feedback,...(scope==='pages'?pages.map(n=>perPage[String(n)]?.trim()?`第 ${n} 页：${perPage[String(n)]}`:''):[])].filter(Boolean).join('\n\n');
 const gateLabels:Record<string,string>={independent_export_build_contract:'完整导出核验',independent_visual_review:'逐页视觉审核',powerpoint_open_edit_save_reopen:'PowerPoint 打开、编辑、保存与重开验收',postflight_warnings_review:'质量提示复核'};
 return <div className="panel">
  <div className="section-title"><div><h2>版本与逐页修改</h2><p>选择具体版本查看、检查或提交修改。审核草稿的修改结果仍需审核。</p></div><Layers size={21}/></div>
  {!versions.length?<Empty title="还没有可预览版本">完成方案确认并生成后，通过独立程序检查的草稿会出现在这里。</Empty>:<>
   <div className="version-list">{versions.map((v,i)=><div key={v.id} className={'version-row '+(base===v.id?'chosen':'')}>
    <label><input name="base" type="radio" checked={base===v.id} onChange={()=>changeBase(v.id)}/><span>
     <strong>{v.status==='review'?'待审核草稿':'版本'} {versions.length-i}</strong><code>{v.id}</code>
     <small>{new Date(v.created_at).toLocaleString('zh-CN')}{v.parent_version_id&&' · 基于 '+v.parent_version_id}</small>
    </span></label>
    <button className="secondary compact" disabled={disabled} onClick={()=>void perform(()=>api.download(v.id))}>{v.status==='review'?'下载审核草稿':'下载 PPTX'}</button>
   </div>)}</div>
   {!version?<p className="notice">选择一个版本查看预览；新版本到达不会替换你的选择。</p>:<>
    <div className="selected-base">{review?'当前预览':'当前基线'} <code>{version.id}</code></div>
    {review&&<div className="notice warning" role="status"><div>
     <strong>待审核草稿 · 尚未正式发布</strong>
     <p>已完成独立程序检查，可以预览和下载审核。仍需完成以下审核：</p>
     <ul>{(version.required_pending_gates?.length?version.required_pending_gates:['independent_export_build_contract','independent_visual_review','powerpoint_open_edit_save_reopen']).map(gate=><li key={gate}>{gateLabels[gate]??'其他审核待完成'}</li>)}</ul>
     <p>可保存检查问题，并基于本草稿创建新的审核稿；这不会发布演示稿，也不会替代正式版本验收。</p>
    </div></div>}
    <p className="hint">预览为源 SVG 的静态 PNG 栅格图，PowerPoint 的字体、换行与布局可能有所不同。</p>
    {review?<AcceptancePanel projectId={project.id} versionId={version.id} onSnapshot={observeReview} onUseFeedback={useSavedFeedback}/>:<div className="slide-grid">{version.slides.map(slide=><article className="slide" key={slide.number}>
     <Preview key={version.id+':'+slide.number} versionId={version.id} page={slide.number}/>
     <div><span className="page-number">{slide.number.toString().padStart(2,'0')}</span><strong>{slide.title}</strong></div>
     {(slide.summary??slide.purpose)&&<p>{slide.summary??slide.purpose}</p>}
    </article>)}</div>}
    {review&&!canReviewRevise&&<p className="hint">读取到当前有效的检查记录与来源后，才可提交审核稿修改；来源失效时请先处理资料或重新生成。</p>}
    {(canRevise||canReviewRevise)&&<section ref={revisionForm} className="revision-form" aria-label={review?'审核稿修改':'正式版本修改'}><h3>{review?'基于所选草稿创建新审核稿':'提交修改意见'}</h3>{review&&<><p className="hint">可手动填写，或在上方将已保存问题带入。提交会调用外部模型；原草稿与检查记录保留，新结果仍需独立检查与人工审核。</p>{reviewNotice&&<p className="notice" role="status">{reviewNotice}</p>}</>}<fieldset disabled={disabled}>
     <legend className="sr-only">修改范围</legend>
     <div className="inline-radios"><label><input type="radio" name="scope" checked={scope==='whole'} onChange={()=>setScope('whole')}/>整份演示稿</label><label><input type="radio" name="scope" checked={scope==='pages'} onChange={()=>setScope('pages')}/>指定页面</label></div>
     {scope==='pages'&&<div className="page-feedback">{version.slides.map(slide=><div key={slide.number}>
      <label className="check-page"><input type="checkbox" checked={pages.includes(slide.number)} onChange={e=>setPages(old=>e.target.checked?[...old,slide.number].sort((a,b)=>a-b):old.filter(n=>n!==slide.number))}/>第 {slide.number} 页 · {slide.title}</label>
      {pages.includes(slide.number)&&<label>第 {slide.number} 页意见<textarea rows={2} maxLength={5000} value={perPage[String(slide.number)]??''} onChange={e=>setPerPage(old=>({...old,[slide.number]:e.target.value}))}/></label>}
     </div>)}</div>}
     <label>{scope==='whole'?'整份修改意见':'共同修改意见（可选）'}<textarea rows={4} maxLength={review?12000:10000} value={feedback} onChange={e=>setFeedback(e.target.value)} placeholder="说明需要调整的内容、数据或版式…"/></label>
     <Consent checked={consent} onChange={setConsent} disabled={disabled}/>
     {review?<button className="primary" disabled={disabled||!consent||!currentReview?.can_review||!revisionFeedback.trim()||revisionFeedback.length>12000||(scope==='pages'&&!pages.length)} onClick={()=>{if(currentReview?.can_review)void start(()=>api.reviewRevise(project,version.id,scope==='pages'?pages:[],revisionFeedback,currentReview.binding,currentReview.revision));}}>创建新审核稿修改任务 <ArrowRight size={16}/></button>:<button className="primary" disabled={disabled||!consent||(scope==='whole'?!feedback.trim():!pages.length||(!feedback.trim()&&pages.some(n=>!perPage[String(n)]?.trim())))} onClick={()=>void start(()=>api.revise(project,version.id,scope==='pages'?pages:[],feedback,scope==='pages'?Object.fromEntries(pages.map(n=>[String(n),perPage[String(n)]??''])):{},consent))}>基于所选版本创建修改任务 <ArrowRight size={16}/></button>}
     {review&&revisionFeedback.length>12000&&<p className="field-error" role="alert">修改意见合计超过 12000 字符，请精简后提交。</p>}
     <p className="hint">{review?'审核稿来源':'提交基线'}：{version.id}。不会自动切换到最新版本。</p>
    </fieldset></section>}
   </>}
  </>}
 </div>;
}
function Preview({versionId,page}:{versionId:string;page:number}) {const [failed,setFailed]=useState(false);const [loaded,setLoaded]=useState(false);return <div className="preview">{!loaded&&!failed&&<span role="status">正在读取第 {page} 页预览…</span>}{failed?<div role="status">第 {page} 页预览暂不可用 <button className="link" onClick={()=>{setFailed(false);setLoaded(false);}}>重试</button></div>:<img alt={`第 ${page} 页静态预览`} src={api.preview(versionId,page)} loading="lazy" onLoad={()=>setLoaded(true)} onError={()=>setFailed(true)}/>}</div>;}
