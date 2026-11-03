export interface Settings { pages: number; language: string; audience: string; purpose: string; style: string; requirements: string }
export interface Asset { id: string; display_name: string; bytes: number; parse_status: string; text_summary?: string | null; parse_error?: string | null; summary?: string; error_code?: string }
export interface Page { number: number; title: string; summary?: string; purpose?: string; points?: string[]; sources?: string[]; details?: Record<string, unknown> }
export interface Direction { id: string; title: string; description: string; pages: Page[]; production_settings: Record<string, unknown>; page_expression?: string[]; details?: Record<string, unknown> }
export interface Communication { summary: string; template_options: string[]; fields?: Record<string, unknown>; details?: Record<string, unknown> }
export interface Plan { revision: string; input_snapshot_hash: string; stage: 'communication' | 'directions'; communication?: Communication; directions?: Direction[]; recommendation?: {direction_id:string; reason:string; selection_is:string}; questions?: string[]; confirmed_at?: string | null; confirmed_stage?: string; confirmed_direction_id?: string }
export interface Project { external_model_ack?: boolean; id: string; title: string; stage: string; revision: number; input_snapshot_hash: string; settings: Settings; assets: Asset[]; plan?: Plan; active_job_id?: string | null; latest_job?: Job | null; confirmed_plan_revision?: string | null; latest_version_id?: string | null; storage_bytes?: number }
export interface QualityDiagnostic { schema_version: 1; error_code: string; stage: string; title: string; message: string; suggestion: string; retry_stage: string; publication_authorized: false; diagnostic_download_url: string }
export interface Job { id: string; project_id: string; kind: string; status: string; stage?: string; last_event_seq: number; error_code?: string; needs_input?: { questions: string[] }; quality_diagnostics?: QualityDiagnostic; usage?: Usage; created_at?: string | null; deadline?: string | null; phase_timeout_seconds?: number | null }
export interface Version { id: string; created_at: string; parent_version_id?: string; slides: Page[]; status?: 'published' | 'review'; can_revise?: boolean; validation_status?: string; publication_authorization?: boolean; required_pending_gates?: string[] }
export interface Usage { scope?: 'task' | 'account'; input_tokens?: number | null; output_tokens?: number | null; tokens_status?: 'known' | 'partial' | 'unknown'; reported_cost?: string | null; currency?: string | null; cost_status?: 'reported' | 'partial' | 'unknown' | 'mixed_currency'; cost_breakdown?: { currency: string; reported_cost: string }[]; record_count?: number; unknown_record_count?: number }
export interface Quota { enabled: boolean; period?: string | null; generation_limit?: number | null; revision_limit?: number | null; generation_count?: number | null; revision_count?: number | null; generation_remaining?: number | null; revision_remaining?: number | null }
export interface Profile { issuer?: string; subject: string; name: string; usage?: Usage; quota?: Quota }
export const defaultSettings: Settings = { pages: 8, language: '简体中文', audience: '', purpose: '', style: '克制、清晰、专业', requirements: '' };
export const activeStatuses = new Set(['queued', 'starting', 'running', 'collecting', 'validating', 'cancelling']);
export const statusLabel: Record<string, string> = { draft:'草稿', planning:'规划中', awaiting_confirmation:'等待确认', awaiting_review:'等待审核', review:'待审核草稿', generating:'生成中', revising:'修改中', ready:'可预览', queued:'排队中', starting:'准备执行', running:'执行中', collecting:'收集产物', validating:'检查与渲染', succeeded:'已完成', failed:'失败', interrupted:'执行中断', cancelling:'正在停止', cancelled:'已取消', plan_initial:'初步沟通', plan_options:'三套方向规划', prepare:'制作准备', generate:'制作演示稿', generation:'制作演示稿', revise:'按反馈修改', revision:'按反馈修改', parsing:'解析中', parsed:'解析完成', pending:'等待解析', communication:'初步沟通', directions:'设计方向' };
Object.assign(statusLabel, { execution_queued:'等待执行资源', execution_starting:'正在准备执行环境', execution_running:'正在处理本阶段内容' });
export function label(value: string) { return statusLabel[value] ?? '状态待核对'; }
export function hasConfirmedCommunication(project: Project) { const plan=project.plan;return !!project.input_snapshot_hash && plan?.stage==='communication' && !!plan.confirmed_at && project.confirmed_plan_revision===plan.revision && plan.input_snapshot_hash===project.input_snapshot_hash; }
export function canGenerate(project: Project) { return !!project.input_snapshot_hash && !!project.plan && project.confirmed_plan_revision === project.plan.revision && project.plan.input_snapshot_hash === project.input_snapshot_hash && !!project.plan.confirmed_direction_id; }
export const validUsageCount = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
export const validUsageAmount = (value: unknown): value is string => typeof value === 'string' && value.length <= 80 && /^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value);
export const validUsageCurrency = (value: unknown): value is string => typeof value === 'string' && /^[A-Z]{3}$/.test(value);
export function usageTokens(usage?: Usage) { const input=usage?.input_tokens,output=usage?.output_tokens;return `${validUsageCount(input) ? input : '未知'} / ${validUsageCount(output) ? output : '未知'} tokens`; }
export function usageCost(usage?: Usage) {
 const reported_cost=usage?.reported_cost,currency=usage?.currency;
 const amount=validUsageAmount(reported_cost)&&validUsageCurrency(currency)?`${reported_cost} ${currency}`:null;
 if(usage?.cost_status==='reported'&&amount)return amount;
 if(usage?.cost_status==='partial'&&amount)return `${amount}（已报告部分，另有费用未知）`;
 if(usage?.cost_status==='mixed_currency') {
  const amounts=usage.cost_breakdown?.filter(row=>validUsageAmount(row.reported_cost)&&validUsageCurrency(row.currency)).map(row=>`${row.reported_cost} ${row.currency}`)??[];
  if(amounts.length)return `${amounts.join('；')}（不同币种分别列示${usage.unknown_record_count?'，另有费用未知':''}）`;
 }
 return '费用未知（待供应商记录）';
}
export function quotaRemaining(quota?: Quota) {
 if(!quota?.enabled)return '未启用';
 const remaining=(value:unknown)=>validUsageCount(value)?`${value} 次`:'待核对';
 return `制作剩余 ${remaining(quota.generation_remaining)} / 修改剩余 ${remaining(quota.revision_remaining)}`;
}
export function validateFiles(files: File[], assets: Asset[]) { if (files.length + assets.length > 10) return '每项目最多 10 个文件。'; if (files.some(file => file.size > 25 * 1024 ** 2)) return '单个文件不能超过 25 MiB。'; if (files.reduce((n,f)=>n+f.size, assets.reduce((n,a)=>n+a.bytes,0)) > 100 * 1024 ** 2) return '项目资料总量不能超过 100 MiB。'; if (files.some(file => !/\.(md|pdf|docx|png|jpe?g)$/i.test(file.name))) return '请选择 Markdown、带文本层 PDF、DOCX、PNG 或 JPEG。'; return ''; }
