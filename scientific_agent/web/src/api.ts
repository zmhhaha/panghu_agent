import type { Project, ProjectSummary, Version } from './domain';
const ROOT='/api/v1/research';
let csrfToken:string|null=null;
let identityRead:Promise<{user_id:string;csrf_token:string}>|null=null;
const messages:Record<string,string>={
  authentication_required:'登录已过期，请重新登录后继续。',unauthenticated:'登录已过期，请重新登录后继续。',
  revision_conflict:'项目已在另一页面更新。已重新读取，请核对后再提交。',stale_revision:'项目已在另一页面更新。已重新读取，请核对后再提交。',
  stale_confirmation:'资料或方案已改变，请重新确认。',plan_confirmation_required:'请先确认研究方案。',evidence_confirmation_required:'请先确认证据与大纲。',
  active_job_exists:'项目已有任务正在执行，请等待完成或取消。',project_busy:'项目已有任务正在执行，请等待完成或取消。',
  forbidden:'当前账号没有操作权限。',not_found:'项目或版本不可用，请刷新列表。',invalid_response:'服务未返回有效数据，请刷新状态。',
  network_error:'连接中断，操作可能已经提交。请先刷新状态核对，再决定是否重试。',request_timeout:'请求等待超时，操作可能已经提交。请先刷新状态核对。',
  file_too_large:'文件过大，请减少文件大小。',unsupported_file:'仅支持 PDF、Markdown 和 TXT 资料。'
  ,unready_materials_must_be_excluded:'存在解析失败的材料，请先从当前研究剔除后再确认。',limited_evidence_confirmation_required:'当前证据仍有缺口，请明确确认按有限证据成稿。',job_already_active:'项目已有执行任务，请等待完成或取消。',stale_version_requires_reconfirmation:'该报告依据已过期。请重新确认证据并生成新草稿，旧报告仍可阅读。',candidate_parent_conflict:'候选的父版本已不是当前工作版本，请核对版本后重新修改。',finalize_current_version_required:'请先将所选版本明确采用为当前工作版本，再定稿。',material_upload_limit:'文件超过上传限制，请减少文件大小。',material_type_unsupported:'仅支持 PDF、Markdown 和 TXT 资料。',unsupported_material_type:'仅支持 PDF、Markdown 和 TXT 资料。',invalid_plan_output:'方案字段不完整，请检查问题、检索词和执行范围。',invalid_outline_output:'大纲或证据引用不完整，请检查后保存。'
};
export class ApiError extends Error {
  constructor(public code:string,public status=0){super(messages[code.toLowerCase()]??(status===409?messages.revision_conflict:status===401?messages.authentication_required:'操作未完成，请刷新状态；若持续失败，请联系管理员。'));}
}
const keys=new Map<string,string>();
function intentKey(route:string,payload:unknown){const signature=JSON.stringify([route,payload]);let key=keys.get(signature);if(!key){key=crypto.randomUUID();keys.set(signature,key);}return {key,signature};}
export async function request<T>(path:string,init:RequestInit={},prefix=ROOT):Promise<T>{
  const controller=new AbortController();const abort=()=>controller.abort();init.signal?.addEventListener('abort',abort,{once:true});
  if(init.signal?.aborted)controller.abort();
  const timer=setTimeout(()=>controller.abort(),init.body instanceof FormData?120_000:30_000);
  try{
    const headers=new Headers(init.headers);
    if(init.body&&!(init.body instanceof FormData))headers.set('Content-Type','application/json');
    if(!['GET','HEAD','OPTIONS'].includes(init.method??'GET')){if(!csrfToken)await readIdentity();headers.set('X-CSRF-Token',csrfToken!);}
    const response=await fetch(`${prefix}${path}`,{...init,credentials:'same-origin',signal:controller.signal,headers});
    if(response.status===401){csrfToken=null;identityRead=null;}
    if(response.redirected||response.headers.get('content-type')?.includes('text/html'))throw new ApiError('authentication_required',401);
    if(!response.ok){let code=response.status===401?'authentication_required':response.status===403?'forbidden':response.status===404?'not_found':'service_error';try{const body=await response.json();code=body.detail?.code??body.error?.code??body.code??code;}catch{}throw new ApiError(code,response.status);}
    if(response.status===204)return undefined as T;
    try{const raw=await response.json();if(raw?.requirements&&Array.isArray(raw.jobs)&&Array.isArray(raw.versions))return {...raw,jobs:raw.jobs.map((job:Record<string,unknown>)=>({...job,status:job.status??job.state,error:job.error??job.error_code})),materials:(raw.materials??[]).map((material:Record<string,unknown>)=>({...material,id:material.id??material.material_id,error:material.error??material.parse_error})),search_batches:(raw.search_batches??[]).map((batch:Record<string,unknown>)=>({...batch,id:batch.id??batch.batch_id}))} as T;return raw as T;}catch{throw new ApiError('invalid_response');}
  }catch(error){if(error instanceof ApiError)throw error;if(controller.signal.aborted)throw new ApiError('request_timeout');throw new ApiError('network_error');}
  finally{clearTimeout(timer);init.signal?.removeEventListener('abort',abort);}
}
function readIdentity(){
  if(identityRead)return identityRead;
  identityRead=request<{user_id:string;csrf_token:string}>('/me').then(profile=>{csrfToken=profile.csrf_token;return profile;}).finally(()=>{identityRead=null;});
  return identityRead;
}
async function mutation<T>(path:string,payload:unknown,method='POST'):Promise<T>{
  const intent=intentKey(path,payload);
  const result=await request<T>(path,{method,body:JSON.stringify(payload),headers:{'Idempotency-Key':intent.key}});
  keys.delete(intent.signature);return result;
}
const id=encodeURIComponent;
export interface LegacyReport{id:string;topic:string;summary?:string;created_at?:string;content?:string;}
export const api={
  me:readIdentity,
  list:()=>request<{projects:ProjectSummary[]}>('/projects'),
  project:(projectId:string,signal?:AbortSignal)=>request<Project>(`/projects/${id(projectId)}`,{signal}),
  create:(question:string)=>mutation<Project>('/projects',{requirements:{question}}),
  patch:(project:Project,patch:Record<string,unknown>)=>mutation<Project>(`/projects/${id(project.id)}`,{expected_revision:project.revision,...patch},'PATCH'),
  action:(project:Project,action:string,payload:Record<string,unknown>={})=>mutation<Project>(`/projects/${id(project.id)}/actions/${id(action)}`,{expected_revision:project.revision,...payload}),
  doi:(project:Project,doi:string)=>mutation<Project>(`/projects/${id(project.id)}/materials/doi`,{expected_revision:project.revision,doi}),
  upload:async(project:Project,file:File)=>{const form=new FormData();form.append('file',file);form.append('expected_revision',String(project.revision));const intent=intentKey(`upload/${project.id}`,[project.revision,file.name,file.size,file.lastModified]);const result=await request<Project>(`/projects/${id(project.id)}/materials`,{method:'POST',body:form,headers:{'Idempotency-Key':intent.key}});keys.delete(intent.signature);return result;},
  removeMaterial:async(project:Project,materialId:string)=>{const path=`/projects/${id(project.id)}/materials/${id(materialId)}?expected_revision=${project.revision}`;const intent=intentKey(path,null);const result=await request<Project>(path,{method:'DELETE',headers:{'Idempotency-Key':intent.key}});keys.delete(intent.signature);return result;},
  version:(projectId:string,versionId:string,signal?:AbortSignal)=>request<Version>(`/projects/${id(projectId)}/versions/${id(versionId)}`,{signal}),
  compare:(projectId:string,base:string,candidate:string,signal?:AbortSignal)=>request<Record<string,unknown>>(`/projects/${id(projectId)}/compare?base_version_id=${id(base)}&candidate_version_id=${id(candidate)}`,{signal}),
  annotate:(project:Project,versionId:string,text:string,sectionId?:string)=>mutation<Project>(`/projects/${id(project.id)}/annotations`,{expected_revision:project.revision,version_id:versionId,text,...(sectionId?{section_id:sectionId}:{})}),
  download:(projectId:string,versionId:string,format:'markdown'|'sources')=>`${ROOT}/projects/${id(projectId)}/versions/${id(versionId)}/download?format=${format}`
  ,legacyList:(query:string,offset:number,signal?:AbortSignal)=>request<LegacyReport[]>(`/scientific-reports?q=${id(query)}&offset=${offset}&limit=20`,{signal},''),
  legacyReport:(reportId:string,signal?:AbortSignal)=>request<LegacyReport>(`/scientific-reports/${id(reportId)}`,{signal},''),
  legacyCitations:(reportId:string,signal?:AbortSignal)=>request<{status?:string;availability?:string;citations?:Record<string,unknown>[];warnings?:unknown[]}>(`/scientific-research/${id(reportId)}/citations`,{signal},'')
};
