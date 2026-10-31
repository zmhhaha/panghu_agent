export const stages = ['goal','plan','evidence','draft','review','final'] as const;
export type Stage = typeof stages[number];
export const stageNames: Record<Stage,string> = { goal:'研究目标', plan:'研究方案', evidence:'文献与证据', draft:'报告初稿', review:'审阅修订', final:'定稿导出' };
export interface Requirements {
  question:string; purpose:string; audience:string; language:string; depth:'overview'|'review'|'comparison';
  time_range:string; include:string; exclude:string; notes:string;
}
export const emptyRequirements: Requirements = { question:'',purpose:'',audience:'',language:'zh-CN',depth:'review',time_range:'',include:'',exclude:'',notes:'' };
export interface PlanOutput { questions:string[]; research_questions:string[]; search_queries:string[]; inclusion_criteria:string[]; exclusion_criteria:string[]; scope:string; limitations:string[]; }
export interface Plan { id:string; revision:number; output:PlanOutput; source_policy?:{providers:string[];max_results:number;max_search_calls:number};execution_timeout_seconds?:number; [key:string]:unknown; }
export interface Paper { paper_id:string; title:string; authors?:string[]; year?:number; doi?:string; pmid?:string; url?:string; sources?:string[]; availability?:string|Record<string,{available:boolean;obtained_at?:string}>; access_level?:string; abstract?:string; verification?:string|{status:string;checked_at?:string;source?:string;error?:string|null};citation_evidence?:Evidence[];citation_claims?:{claim_id:string;assessment:string;explanation:string}[]; }
export function paperAccess(paper:Paper):string {
  if(paper.access_level)return accessNames[paper.access_level]??'访问范围待核对';
  if(typeof paper.availability==='object')for(const level of ['full_text','abstract','metadata'])if(paper.availability[level]?.available)return accessNames[level];
  return accessNames[typeof paper.availability==='string'?paper.availability:'']??'访问范围待核对';
}
export function paperVerification(paper:Paper):string {
  const status=typeof paper.verification==='object'?paper.verification.status:paper.verification;
  return ({verified:'已核验',unverified:'未核验',failed:'核验失败',not_checked:'尚未核验',not_verified:'尚未核验',pending:'待核验',unavailable:'暂不可核验'} as Record<string,string>)[status??'']??'核验状态待确认';
}
export interface Evidence { evidence_id:string; paper_id:string; finding:string; method?:string; limitations?:string|string[]; access_level?:string; locator?:string; }
export interface OutlineSection { section_id:string; title:string; questions:string[]; evidence_ids:string[]; }
export interface OutlineOutput { evidence:Evidence[]; sections:OutlineSection[]; gaps:string[]; }
export interface Material { id:string; name?:string; filename?:string; doi?:string; status?:string; parse_status?:string; access_level?:string; error?:string; included?:boolean;excluded?:boolean; }
export interface SearchBatch { id:string; output?:{papers?:Paper[];gaps?:string[]}; papers?:Paper[]; created_at?:string; }
export interface EvidenceSnapshot { id:string; output?:OutlineOutput; content?:OutlineOutput; papers?:Paper[]; evidence?:Evidence[]; selections?:Selection[]; gaps?:string[]; stale?:boolean;input_revision?:number;origin?:string;created_at?:string; [key:string]:unknown; }
export interface Selection { source_id:string; included:boolean; reason:string; }
export interface QualityAssessment {default_included:boolean;reasons:{code:string;message:string}[];warnings:{code:string;message:string}[];matched_terms:string[];year_range:(number|null)[]|null;assessed_years:number[];method:string;}
export interface Bibliography {author_list_status:'complete'|'incomplete'|'not_confirmed';author_count:number|null;source_type:'preprint'|'journal'|'other';dates:{first_posted:string|null;version_updated:string|null;journal_published:string|null;online_published:string|null;print_published:string|null};version:string|null;journal_reference:string|null;}
export interface Paper {bibliography?:Bibliography;source_quality?:QualityAssessment;quality_assessment?:QualityAssessment;}
export interface Material {file_status?:string;material_role?:string;source_count?:number;paper_count?:number;document_count?:number;source_count_basis?:string;file_read_scope?:string;access_note?:string;declared_sources?:{title?:string;source_id?:string;access_level?:string;source_type?:string;locator?:string;start_line?:number;end_line?:number;doi?:string}[];}
export interface RetrievalSummary {calls_used:number;call_budget:number;budget_exhausted:boolean;providers:{provider:string;query:string;status:string;result_count:number}[];topics_without_evidence:string[];gaps:string[];}
export interface SearchBatch {retrieval_summary?:RetrievalSummary;search_summary?:RetrievalSummary;}
export interface Project {retrieval_summary?:RetrievalSummary;source_quality?:Record<string,QualityAssessment>;selection_assessments?:Record<string,QualityAssessment>;}
export interface Project {materials_summary?:{file_count:number;read_file_count:number;unique_file_count:number;draft_count:number;undeclared_file_count:number;declared_source_count:number;paper_count:number;document_count:number;abstract_count:number;full_text_count:number;count_basis:string};}
export interface Section { section_id?:string; id?:string; title:string; start?:number; end?:number; editable?:boolean;kind?:string; }
export interface Version { id:string; title?:string; created_at?:string; status?:string; parent_version_id?:string; report_markdown?:string; section_index?:Section[]|{schema_version?:string;sections:Section[]}; revision_sections?:Section[]; sections?:Section[]; citations?:unknown; sources?:Paper[]; [key:string]:unknown; }
export interface JobDiagnostic {category?:string;code?:string;reason?:string;step?:string;retryable?:boolean;suggested_action?:string;correlation_id?:string;started_at?:string|null;last_progress_at?:string|null;}
export interface Job { id:string; stage?:string; action?:string; status:string; progress?:string; message?:string; error?:string|{code?:string}; created_at?:string;updated_at?:string;remote_job_id?:string|null;diagnostic?:JobDiagnostic; [key:string]:unknown; }
export interface Annotation { id:string; version_id:string; section_id?:string; text:string; resolved?:boolean; }
export interface Finalization { id:string; version_id:string; created_at?:string; }
export interface ProjectSummary { id:string; title:string; revision:number; stage:Stage; updated_at?:string; active_job_id?:string|null; }
export interface Project extends ProjectSummary {
  input_revision?:number;
  requirements:Requirements; materials:Material[]; plan:Plan|null; confirmations:{plan:Record<string,unknown>|null;evidence:Record<string,unknown>|null};
  search_batches:SearchBatch[]; evidence:EvidenceSnapshot|null;evidence_history?:EvidenceSnapshot[]; outline:{id?:string;output:OutlineOutput;stale?:boolean;input_revision?:number}|null;
  selections?:Selection[]; versions:Version[]; current_version_id:string|null; finalizations:Finalization[]; annotations:Annotation[]; jobs:Job[];
  history:Record<string,unknown>[];
}
export const isActiveJob = (job:Job) => ['queued','running','pending','accepted','submitted','admitting','admission_uncertain','starting','quarantined','cancelling','cancel_requested','validating','receiving'].includes(job.status);
export function papersFor(project:Project):Paper[] {
  const map=new Map<string,Paper>();
  for(const batch of project.search_batches??[]) for(const paper of batch.output?.papers??batch.papers??[]) map.set(paper.paper_id,paper);
  if(!project.evidence?.stale&&(project.evidence?.input_revision===undefined||project.evidence.input_revision===project.input_revision))for(const paper of project.evidence?.papers??[]) map.set(paper.paper_id,paper);
  return Array.from(map.values());
}
export function sectionList(version:Version|null):Section[] {
  if(!version)return [];
  const index=version.section_index;
  if(Array.isArray(index))return index;
  if(index && typeof index==='object')return index.sections??[];
  return version.sections??[];
}
export const editableSections=(version:Version|null)=>version?.revision_sections??sectionList(version).filter(section=>section.editable!==false&&section.kind!=='bibliography'&&!/^(?:参考文献|references|bibliography)$/i.test(section.title.trim()));
export const accessNames:Record<string,string>={metadata:'仅元数据',abstract:'已获取摘要',full_text:'已提供全文',fulltext:'已提供全文',provided_full_text:'已提供全文'};
export const jobNames:Record<string,string>={queued:'排队中',pending:'等待执行',admitting:'正在提交',admission_uncertain:'正在核对提交状态',starting:'正在准备执行',quarantined:'正在清理执行环境',submitted:'已提交',accepted:'已接收',running:'执行中',validating:'校验与保存',receiving:'接收成果',succeeded:'已完成',completed:'已完成',failed:'执行失败',timed_out:'执行超时',invalid_result:'成果未通过校验',cancelled:'已取消',canceled:'已取消',cancelling:'正在取消',cancel_requested:'正在取消'};
export const taskStageNames:Record<string,string>={plan:'生成研究方案',retrieve:'检索文献',outline:'整理证据与大纲',draft:'撰写报告草稿',revise:'章节修订'};
