import type { Job, Profile, Project, Version } from './domain';
import { adaptDirections } from './planning';
const root = '/api/v1';
let csrfToken: string | null = null;
export const errors: Record<string,string> = {
  EXECUTION_NOT_CONNECTED:'执行器尚未接通，当前仅可测试项目、资料和设置；不会调用模型。',
  CONSENT_REQUIRED:'请先确认外部模型数据传输，再重试此操作。', UPLOAD_LIMIT:'文件超出上传限额，请检查单文件 25 MiB、项目 10 文件 / 100 MiB 限制。',
  UNAUTHENTICATED:'登录已过期，请重新登录。', FORBIDDEN:'当前账号无法执行此操作。', NOT_FOUND:'资源不可用或已删除，请返回项目列表。', STALE_REVISION:'资料或设置已变更，请刷新项目并重新确认方案。', STALE_PLAN:'确认已失效，请重新规划并确认。', SNAPSHOT_MISMATCH:'材料快照已变化，请重新规划。', ACTIVE_JOB_EXISTS:'项目已有活动任务，请等待完成或取消。', QUEUE_FULL:'任务队列已满，请稍后重试。', PARSE_FAILED:'资料解析失败，请检查文件是否含可读文本，或更换文件。', MODEL_CONNECTION_FAILED:'模型连接失败，请稍后重新提交。', TIMEOUT:'任务超时，已有版本仍可使用。请减少资料或页数后重试。', QUALITY_GATE_FAILED:'产物未通过质量检查，请调整要求后重试。', RENDER_FAILED:'预览渲染失败，请重试或联系管理员。', EXPORT_FAILED:'PPTX 导出失败，请重试或联系管理员。', STORAGE_LIMIT:'存储空间不足，请清理不再需要的项目后重试。', QUOTA_EXCEEDED:'当前可用额度不足，请联系服务管理员。', NETWORK_ERROR:'无法连接服务，请检查网络并重试。', INVALID_RESPONSE:'服务响应暂不可用，请刷新或联系管理员。', CURSOR_EXPIRED:'进度记录已过期，正在重新读取任务状态。'
};
const errorAliases: Record<string,string> = { EVENT_CURSOR_EXPIRED:'CURSOR_EXPIRED', REVISION_CONFLICT:'STALE_REVISION', PROJECT_REVISION_MISMATCH:'STALE_REVISION', PLAN_STALE:'STALE_PLAN', INPUT_SNAPSHOT_MISMATCH:'SNAPSHOT_MISMATCH', JOB_ALREADY_ACTIVE:'ACTIVE_JOB_EXISTS', PROJECT_BUSY:'ACTIVE_JOB_EXISTS', EXTERNAL_MODEL_ACK_REQUIRED:'CONSENT_REQUIRED', PARSE_ERROR:'PARSE_FAILED', MATERIAL_PARSE_FAILED:'PARSE_FAILED', FILE_TOO_LARGE:'UPLOAD_LIMIT', TOO_MANY_FILES:'UPLOAD_LIMIT', PROJECT_STORAGE_LIMIT:'STORAGE_LIMIT', MODEL_UNAVAILABLE:'MODEL_CONNECTION_FAILED', EXECUTION_TIMEOUT:'TIMEOUT', QUALITY_FAILED:'QUALITY_GATE_FAILED' };
export class ApiError extends Error { public code:string; constructor(code: string, public status = 0) { const normalized=code.toUpperCase();const canonical=errorAliases[normalized]??normalized; super(errors[canonical] ?? '操作未完成，请刷新后重试；若持续发生，请联系管理员。');this.code=canonical; } }
export const safeError = (error: unknown) => error instanceof ApiError ? `${error.message}（${/^[A-Z_]{1,60}$/.test(error.code) ? error.code : 'SERVICE_ERROR'}）` : errors.NETWORK_ERROR;
// Keys live only in memory: retries of the same intent reuse them; changed payload creates a new intent.
export class IntentKeys {
  private keys = new Map<string,string>();
  key(route: string, payload: unknown) { const identity = JSON.stringify([route,payload]); let key = this.keys.get(identity); if (!key) { key = crypto.randomUUID(); this.keys.set(identity,key); } return key; }
  complete(route:string,payload:unknown) { this.keys.delete(JSON.stringify([route,payload])); }
}
export const intents = new IntentKeys();
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  const headers = new Headers(init.headers);
  headers.delete('X-CSRF-Token');
  const isMutation = !['GET','HEAD','OPTIONS'].includes((init.method??'GET').toUpperCase());
  if (isMutation && csrfToken) headers.set('X-CSRF-Token',csrfToken);
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type','application/json');
  try { response = await fetch(`${root}${path}`, { credentials:'same-origin', ...init, headers }); } catch { throw new ApiError('NETWORK_ERROR'); }
  if (response.status === 401) csrfToken = null;
  if (!response.ok) { let code = response.status === 401 ? 'UNAUTHENTICATED' : response.status === 403 ? 'FORBIDDEN' : response.status === 404 ? 'NOT_FOUND' : 'SERVICE_ERROR'; try { const body = await response.json(); code = body.error?.code ?? body.detail?.code ?? body.code ?? code; } catch { /* Never expose server HTML or raw details. */ } throw new ApiError(code,response.status); }
  if (response.status === 204) return undefined as T;
  try { return await response.json() as T; } catch { throw new ApiError('INVALID_RESPONSE'); }
}
export async function mutate<T>(path:string,payload:unknown = {},method='POST'):Promise<T> { const key=intents.key(path,payload); const result=await request<T>(path,{method,body:JSON.stringify(payload),headers:{'Idempotency-Key':key}}); intents.complete(path,payload); return result; }
const id = encodeURIComponent;
type RecordValue = Record<string, any>;
export function adaptProject(raw: RecordValue): Project {
 const plans = raw.plans ?? []; const latest = plans.length ? plans[plans.length-1] : raw.plan;
 const options = latest?.design?.directions ?? latest?.design?.options ?? latest?.directions;
 const plan = latest ? { ...latest, revision:String(latest.revision), stage:latest.phase==='plan_initial'?'communication':latest.phase==='plan_options'?'directions':latest.stage,
 communication: { summary:latest.design?.summary??latest.outline?.summary??(typeof latest.outline==='string'?latest.outline:''), template_options:latest.design?.validated_templates??[] },
 directions:adaptDirections(options),
 recommendation:latest.design?.recommendation ? {direction_id:String(latest.design.recommendation.direction_id??''),reason:typeof latest.design.recommendation.reason==='string'?latest.design.recommendation.reason:'',selection_is:String(latest.design.recommendation.selection_is??'')} : undefined,
 questions:latest.questions??latest.design?.questions??latest.outline?.questions??[],
 confirmed_direction_id:latest.confirmed_at ? latest.confirmation_selection?.direction_id??latest.selection?.direction_id??latest.design?.selection?.direction_id : undefined,
 } : undefined;
 return { ...raw, settings:raw.settings??{pages:raw.page_count??8,language:raw.language==='zh'?'简体中文':raw.language??'简体中文',audience:raw.audience??'',purpose:raw.purpose??'',style:raw.style??'',requirements:raw.requirements??''}, assets:raw.assets??[], plan,
 input_snapshot_hash:raw.input_snapshot_hash??'', confirmed_plan_revision:raw.confirmed_plan_revision==null?null:String(raw.confirmed_plan_revision) } as Project;
}
export function adaptJob(raw:RecordValue):Job {return {...raw,id:raw.id??raw.job_id,last_event_seq:raw.event_cursor??raw.last_event_seq??0} as Job;}
const flatSettings=(s:Project['settings'])=>({page_count:s.pages,language:s.language==='简体中文'?'zh':s.language,audience:s.audience,purpose:s.purpose,style:s.style,requirements:s.requirements});
export const api = {
  me: async () => {const raw=await request<RecordValue>('/me');csrfToken=typeof raw.csrf_token==='string'&&raw.csrf_token?raw.csrf_token:null;const {csrf_token: _csrf, ...profile}=raw;return {...profile,subject:raw.sub??raw.subject,name:raw.name??raw.display_name??'我的账号'} as Profile;}, projects: async () => (await request<RecordValue[]>('/projects')).map(adaptProject), project: async (projectId:string, signal?:AbortSignal) => adaptProject(await request<RecordValue>(`/projects/${id(projectId)}`,{signal})),
  create: async (title:string) => adaptProject(await mutate<RecordValue>('/projects',{title,purpose:'',audience:'',page_count:8,language:'zh',style:'',requirements:'',external_model_ack:false})),
  update: async (p:Project, settings:Project['settings']) => adaptProject(await mutate<RecordValue>(`/projects/${id(p.id)}`,{title:p.title,...flatSettings(settings),external_model_ack:p.external_model_ack??false,expected_revision:p.revision},'PATCH')),
  acknowledge: async (p:Project) => adaptProject(await mutate<RecordValue>(`/projects/${id(p.id)}`,{title:p.title,...flatSettings(p.settings),external_model_ack:true,expected_revision:p.revision},'PATCH')),
  remove: (p:Project) => mutate<void>(`/projects/${id(p.id)}`,{expected_revision:p.revision},'DELETE'),
  upload: async (p:Project,file:File) => { const body=new FormData(); body.set('file',file); return request<unknown>(`/projects/${id(p.id)}/assets`,{method:'POST',body,headers:{'Idempotency-Key':intents.key(`/projects/${id(p.id)}/assets`,[p.revision,file.name,file.size,file.lastModified])}}); },
  plan: (p:Project, feedback:string, _consent:boolean) => mutate<{job_id:string}>(`/projects/${id(p.id)}/plans`,{expected_revision:p.revision,feedback,phase:p.plan?.stage==='communication'&&p.plan.confirmed_at&&!feedback.trim()?'plan_options':'plan_initial'}),
  confirm: async (p:Project, selection:unknown) => adaptProject(await mutate<RecordValue>(`/projects/${id(p.id)}/plans/${id(p.plan!.revision)}/confirm`,{expected_revision:p.revision,input_snapshot_hash:p.plan!.input_snapshot_hash,selection})),
  generate: (p:Project, _consent:boolean) => mutate<{job_id:string}>(`/projects/${id(p.id)}/generations`,{expected_revision:p.revision,plan_revision:Number(p.plan!.revision)}),
  revise: (p:Project,base:string,pages:number[],feedback:string,pageFeedback:Record<string,string>,_consent:boolean) => mutate<{job_id:string}>(`/projects/${id(p.id)}/revisions`,{expected_revision:p.revision,base_version_id:base,pages,feedback:[feedback,...Object.entries(pageFeedback).map(([n,text])=>`第 ${n} 页：${text}`)].filter(Boolean).join('\n\n')}),
  job: async (jobId:string) => adaptJob(await request<RecordValue>(`/jobs/${id(jobId)}`)), cancel: async (jobId:string) => adaptJob(await mutate<RecordValue>(`/jobs/${id(jobId)}/cancel`)),
  versions: (projectId:string) => request<Version[]>(`/projects/${id(projectId)}/versions`),
  preview: (versionId:string,page:number) => `${root}/versions/${id(versionId)}/slides/${page}/preview`,
  download: async (versionId:string) => { const response=await fetch(`${root}/versions/${id(versionId)}/download`,{credentials:'same-origin'}); if(response.status===401)csrfToken=null; if (!response.ok) throw new ApiError(response.status===401?'UNAUTHENTICATED':'EXPORT_FAILED',response.status); const blob=await response.blob(); if (!blob.size) throw new ApiError('EXPORT_FAILED'); const url=URL.createObjectURL(blob); const link=document.createElement('a'); link.href=url; link.download=`presentation-${versionId}.pptx`; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); }
};
export const cursorStore = { read(jobId:string) { const n=Number(localStorage.getItem(`ppt:cursor:${jobId}`)); return Number.isSafeInteger(n)&&n>=0?n:0; }, write(jobId:string,seq:number) { if (Number.isSafeInteger(seq)&&seq>=0) localStorage.setItem(`ppt:cursor:${jobId}`,String(seq)); } };
export interface BusinessEvent { seq:number; type:string; payload?: { status?:string; stage?:string }; }
// Fetch streaming supports a real Last-Event-ID header on every reconnect; EventSource cannot set it.
export async function consumeEvents(jobId:string,cursor:number,signal:AbortSignal,onEvent:(event:BusinessEvent)=>void) {
 const response=await fetch(`${root}/jobs/${id(jobId)}/events`,{credentials:'same-origin',headers:{Accept:'text/event-stream',...(cursor?{'Last-Event-ID':String(cursor)}:{})},signal});
 if (response.status===401) csrfToken=null;
 if (response.status===410 || response.status===409) throw new ApiError('CURSOR_EXPIRED',response.status);
 if (!response.ok) throw new ApiError(response.status===401?'UNAUTHENTICATED':'NETWORK_ERROR',response.status);
 if (!response.body) throw new ApiError('INVALID_RESPONSE');
 const reader=response.body.getReader(); const decoder=new TextDecoder(); let buffer='';
 try { while (!signal.aborted) { const {value,done}=await reader.read(); if(done) break; buffer+=decoder.decode(value,{stream:true}).replace(/\r\n/g,'\n'); let boundary; while((boundary=buffer.indexOf('\n\n'))>=0) { const block=buffer.slice(0,boundary); buffer=buffer.slice(boundary+2); const lines=block.split('\n'); const data=lines.filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n'); const eventId=lines.find(l=>l.startsWith('id:'))?.slice(3).trim(); if(!data)continue; try { const event=JSON.parse(data); const seq=Number(eventId??event.seq); if(Number.isSafeInteger(seq)&&seq>cursor) { cursor=seq; onEvent({seq,type:String(event.type??''),payload:{status:event.payload?.status,stage:event.payload?.stage}}); } } catch { /* Ignore malformed events, then reconcile using authoritative snapshot. */ } } } } finally { reader.releaseLock(); }
}
