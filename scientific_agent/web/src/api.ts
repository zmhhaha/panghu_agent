import type { Project, ProjectSummary, Version } from './domain';
const ROOT='/api/v1/research';
let csrfToken:string|null=null;
let identityRead:Promise<{user_id:string;csrf_token:string}>|null=null;
const messages:Record<string,string>={
  stale_evidence_requires_update:'当前证据基于上一版筛选。请更新证据整理，或按当前来源保存人工证据版本。',source_review_required:'请先明确核对稿件来源。',import_review_required:'请先完成人工恢复稿审阅确认，再采用或定稿。',imported_report_requires_review:'请先审阅并确认导入稿的来源与限制。',invalid_workflow_evidence:'每项证据必须填写来源、发现、方法、限制、访问范围和原文位置；摘要来源的位置需注明摘要。',workflow_evidence_source_mismatch:'证据来源或访问范围不属于当前纳入集合，请核对来源并避免将摘要标为全文。',report_too_large:'稿件超过 2 MiB UTF-8 字节限制，请缩减后导入。',invalid_report:'稿件为空、过大或包含不可接受的文本，请核对后重新导入。',
  authentication_required:'登录已过期，请重新登录后继续。',unauthenticated:'登录已过期，请重新登录后继续。',
  revision_conflict:'项目已在另一页面更新。已重新读取，请核对后再提交。',stale_revision:'项目已在另一页面更新。已重新读取，请核对后再提交。',
  stale_confirmation:'资料或方案已改变，请重新确认。',plan_confirmation_required:'请先确认研究方案。',evidence_confirmation_required:'请先确认证据与大纲。',
  active_job_exists:'项目已有任务正在执行，请等待完成或取消。',project_busy:'项目已有任务正在执行，请等待完成或取消。',
  forbidden:'当前账号没有操作权限。',not_found:'项目或版本不可用，请刷新列表。',invalid_response:'服务未返回有效数据，请刷新状态。',
  network_error:'连接中断，操作可能已经提交。请先刷新状态核对，再决定是否重试。',request_timeout:'请求等待超时，操作可能已经提交。请先刷新状态核对。',
  file_too_large:'文件过大，请减少文件大小。',unsupported_file:'仅支持 PDF、Markdown 和 TXT 资料。'
  ,official_source_url_invalid:'请填写受支持的公开 HTTPS 官方页面或仓库地址。',official_source_url_forbidden:'该地址不能作为公开资料读取，请使用公开官方页面或上传资料文件。',official_source_unavailable:'暂时无法读取该官方资料，请核对地址或下载后上传文件。',official_source_timeout:'读取官方资料超时，请稍后重试或下载后上传。',official_source_too_large:'官方资料超过单次读取上限，请改用具体页面或较小文件。',official_source_unsupported:'暂不支持读取该内容类型，请使用公开文本页面或上传 PDF、Markdown、TXT。',official_source_empty:'未取得可使用的正文。链接尚未保存为全文，请换用具体文档或上传资料。',official_source_fetch_failed:'获取官方正文失败，未将链接标记为全文；请核对地址或上传文件。'
  ,official_source_invalid_url:'请填写公开 HTTPS 官方页面或受支持的仓库地址；带凭据或内部网络地址不能作为公开资料读取。',official_source_dns_unavailable:'暂时无法解析该资料域名，请核对地址，稍后重试或下载后上传。',official_source_redirect_not_allowed:'该资料地址发生了不受支持的跳转，请填写最终公开文档的直接地址。',official_source_byte_limit:'资料超过单次下载上限，请改用具体文档或上传较小的文件。',official_source_encoding_unsupported:'资料使用了暂不支持的传输编码，请下载为文本后上传。',official_source_not_found:'未找到该资料文件，请核对公开地址和文件路径。',official_source_rate_limited:'来源网站暂时限制读取频率，请稍后重试或下载后上传。',official_source_invalid_response:'来源未返回可读取的资料内容，请核对地址或上传文本文件。',official_source_github_url_unsupported:'请选择公开 GitHub 仓库或具体文本文件；暂不支持该类型的仓库页面。',official_source_file_type_unsupported:'该仓库文件类型暂不支持，请选择 Markdown、TXT、TOML 或支持的文本文件。',official_source_invalid_file:'仓库文件内容未通过读取校验，请核对原文件或下载后上传。',official_source_content_type_unsupported:'该页面的内容类型暂不支持，请使用正文页面或上传 PDF、Markdown、TXT。',official_source_utf8_required:'资料不是可读取的 UTF-8 文本，请转换后上传。',official_source_no_readable_text:'没有取得可用正文，未将链接标记为全文；请改用直接文档或上传文件。',official_source_text_limit:'提取正文超过上限，请选择更具体的页面或较小文件。'
  ,finalized_version_is_immutable:'该版本已定稿，引用映射保持只读。请从当前工作版本手工修改并保存新候选，再匹配引用和重新审阅。',invalid_reference_mappings:'引用编号或来源不属于所选版本，请重新打开引用匹配并核对。',imported_version_required:'此操作仅用于用户导入或人工编辑稿件，请核对所选报告版本。',import_review_confirmation_required:'引用或稿件已更新，请重新确认人工恢复稿审阅后再采用或定稿。'
  ,unready_materials_must_be_excluded:'存在解析失败的材料，请先从当前研究剔除后再确认。',limited_evidence_confirmation_required:'当前证据仍有缺口，请明确确认按有限证据成稿。',job_already_active:'项目已有执行任务，请等待完成或取消。',stale_version_requires_reconfirmation:'该报告依据已过期。请重新确认证据并生成新草稿，旧报告仍可阅读。',candidate_parent_conflict:'候选的父版本已不是当前工作版本，请核对版本后重新修改。',finalize_current_version_required:'请先将所选版本明确采用为当前工作版本，再定稿。',material_upload_limit:'文件超过上传限制，请减少文件大小。',material_type_unsupported:'仅支持 PDF、Markdown 和 TXT 资料。',unsupported_material_type:'仅支持 PDF、Markdown 和 TXT 资料。',invalid_plan_output:'方案字段不完整，请检查问题、检索词和执行范围。',invalid_outline_output:'大纲或证据引用不完整，请检查后保存。'
};
export class ApiError extends Error {
  constructor(public code:string,public status=0){super(messages[code.toLowerCase()]??(status===409?messages.revision_conflict:status===401?messages.authentication_required:'操作未完成，请刷新状态；若持续失败，请联系管理员。'));}
}
const keys=new Map<string,string>();
function normalizeJob(job:Record<string,unknown>){const state=String(job.status??job.state??'');const status=job.cancel_requested&&!['completed','succeeded','failed','timed_out','invalid_result','cancelled','canceled'].includes(state)?'cancel_requested':state;return {...job,status,error:job.error??job.error_code};}
function intentKey(route:string,payload:unknown){const signature=JSON.stringify([route,payload]);let key=keys.get(signature);if(!key){key=crypto.randomUUID();keys.set(signature,key);}return {key,signature};}
export async function request<T>(path:string,init:RequestInit={},prefix=ROOT):Promise<T>{
  const controller=new AbortController();const abort=()=>controller.abort();init.signal?.addEventListener('abort',abort,{once:true});
  if(init.signal?.aborted)controller.abort();
  const timeout=init.body instanceof FormData?120_000:init.method==='POST'&&/^\/projects\/[^/]+\/materials\/url$/.test(path)?60_000:30_000;
  const timer=setTimeout(()=>controller.abort(),timeout);
  try{
    const headers=new Headers(init.headers);
    if(init.body&&!(init.body instanceof FormData))headers.set('Content-Type','application/json');
    if(!['GET','HEAD','OPTIONS'].includes(init.method??'GET')){if(!csrfToken)await readIdentity();headers.set('X-CSRF-Token',csrfToken!);}
    const response=await fetch(`${prefix}${path}`,{...init,credentials:'same-origin',signal:controller.signal,headers});
    if(response.status===401){csrfToken=null;identityRead=null;}
    if(response.redirected||response.headers.get('content-type')?.includes('text/html'))throw new ApiError('authentication_required',401);
    if(!response.ok){let code=response.status===401?'authentication_required':response.status===403?'forbidden':response.status===404?'not_found':'service_error';try{const body=await response.json();code=body.detail?.code??body.error?.code??body.code??code;}catch{}throw new ApiError(code,response.status);}
    if(response.status===204)return undefined as T;
    try{const raw=await response.json();if(raw?.requirements&&Array.isArray(raw.jobs)&&Array.isArray(raw.versions))return {...raw,jobs:raw.jobs.map(normalizeJob),materials:(raw.materials??[]).map((material:Record<string,unknown>)=>({...material,id:material.id??material.material_id,error:material.error??material.parse_error})),search_batches:(raw.search_batches??[]).map((batch:Record<string,unknown>)=>({...batch,id:batch.id??batch.batch_id}))} as T;return raw as T;}catch{throw new ApiError('invalid_response');}
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
  url:(project:Project,url:string)=>mutation<Project>(`/projects/${id(project.id)}/materials/url`,{expected_revision:project.revision,url}),
  upload:async(project:Project,file:File)=>{const form=new FormData();form.append('file',file);form.append('expected_revision',String(project.revision));const intent=intentKey(`upload/${project.id}`,[project.revision,file.name,file.size,file.lastModified]);const result=await request<Project>(`/projects/${id(project.id)}/materials`,{method:'POST',body:form,headers:{'Idempotency-Key':intent.key}});keys.delete(intent.signature);return result;},
  removeMaterial:async(project:Project,materialId:string)=>{const path=`/projects/${id(project.id)}/materials/${id(materialId)}?expected_revision=${project.revision}`;const intent=intentKey(path,null);const result=await request<Project>(path,{method:'DELETE',headers:{'Idempotency-Key':intent.key}});keys.delete(intent.signature);return result;},
  version:(projectId:string,versionId:string,signal?:AbortSignal)=>request<Version>(`/projects/${id(projectId)}/versions/${id(versionId)}`,{signal}),
  compare:(projectId:string,base:string,candidate:string,signal?:AbortSignal)=>request<Record<string,unknown>>(`/projects/${id(projectId)}/compare?base_version_id=${id(base)}&candidate_version_id=${id(candidate)}`,{signal}),
  annotate:(project:Project,versionId:string,text:string,sectionId?:string)=>mutation<Project>(`/projects/${id(project.id)}/annotations`,{expected_revision:project.revision,version_id:versionId,text,...(sectionId?{section_id:sectionId}:{})}),
  download:(projectId:string,versionId:string,format:'markdown'|'sources'|'reading-markdown'|'docx'|'pdf')=>`${ROOT}/projects/${id(projectId)}/versions/${id(versionId)}/download?format=${format}`
  ,diagnostic:(projectId:string,jobId:string)=>`${ROOT}/projects/${id(projectId)}/jobs/${id(jobId)}/diagnostic`,
  evidence:(projectId:string,evidenceId:string)=>request<import('./domain').EvidenceSnapshot>(`/projects/${id(projectId)}/evidence/${id(evidenceId)}`),
  evidenceDownload:(projectId:string,evidenceId:string,format:'json'|'markdown')=>`${ROOT}/projects/${id(projectId)}/evidence/${id(evidenceId)}/download?format=${format}`
  ,legacyList:(query:string,offset:number,signal?:AbortSignal)=>request<LegacyReport[]>(`/scientific-reports?q=${id(query)}&offset=${offset}&limit=20`,{signal},''),
  legacyReport:(reportId:string,signal?:AbortSignal)=>request<LegacyReport>(`/scientific-reports/${id(reportId)}`,{signal},''),
  legacyCitations:(reportId:string,signal?:AbortSignal)=>request<{status?:string;availability?:string;citations?:Record<string,unknown>[];warnings?:unknown[]}>(`/scientific-research/${id(reportId)}/citations`,{signal},'')
};
