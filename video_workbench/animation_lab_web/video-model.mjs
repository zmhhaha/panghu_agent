import {learningOptions} from './learning-controls.mjs';
export const VIDEO_API = 'api/video-lab/';
export const MAX_REFERENCE_BYTES = 24 * 1024 * 1024;
export const VIDEO_ACTIVE_STATES = new Set(['queued', 'running', 'creating', 'reviewing', 'rendering', 'planning', 'transfer_planning', 'transferring', 'stopping', 'cancelling', 'submission_unknown', 'render_unknown']);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/;
const STATES = {queued: '等待执行', running: '正在学习', creating: '正在绘制', rendering: '正在渲染完整片段', reviewing: '正在对照评审', planning: '正在分析目标', transfer_planning: '正在拟定换内容任务', transferring: '正在换内容验证', submission_unknown: '提交结果待核对', render_unknown: '渲染结果待核对', stopping: '正在停止', cancelling: '正在停止', completed: '本次实验已结束', stopped: '本次实验已停止', cancelled: '已停止', failed: '本次执行失败', idle: '尚未开始', reviewed: '已评审'};
const REASONS = {goal_met: '已达到本次目标', objective_met: '已达到本次目标', learning_complete: '本次复现与换内容验证已结束',
  reproduction_complete: '本次复现已结束', awaiting_user_approval: '本次复现已结束，换内容验证等待你认可作品后单独发起', transfer_complete: '本次换内容验证已结束', developer_stopped: '开发者已停止', agent_failed: '创作任务未完成', render_failed: '视频渲染未完成',
  invalid_result: '创作结果不符合执行合同', invalid_input_or_result: '输入或结果未通过检查', budget_exhausted: '本次预算已用完',
  max_rounds: '已到本次探索轮数', max_seconds: '已到本次时间上限', deadline: '已到本次时间上限', deadline_exceeded: '已到本次时间上限',
  no_progress: '多种尝试后暂无明显进展', cancelled: '开发者已停止', stopped: '开发者已停止', failed: '执行未完成'};
export const text = value => typeof value === 'string' ? value : '';
export const rows = value => Array.isArray(value) ? value : [];
export function videoState(value) { return STATES[value] ?? '状态待核对'; }
export function videoStopReason(value) { return REASONS[typeof value === 'object' ? value?.code : value] ?? ''; }
export function videoId(value) { if (!IDENTIFIER.test(value ?? '')) throw Error('项目或作品编号不正确'); return value; }
function number(value, low, high, message) {
  const n = value === '' || value == null ? NaN : Number(value);
  if (!Number.isInteger(n) || n < low || n > high) throw Error(message);
  return n;
}
function requestId(value) { if (!UUID.test(value ?? '')) throw Error('无法生成有效请求编号'); return value; }
export function videoBudget(fields) {
  return {max_rounds: number(fields.max_rounds ?? 1, 1, 8, '探索轮数应为 1 至 8'),
    max_seconds: number(fields.max_minutes ?? 60, 1, 60, '时间上限应为 1 至 60 分钟') * 60};
}
function skillSelection(fields) {
  if (fields.use_wikiskill !== undefined && typeof fields.use_wikiskill !== 'boolean') throw Error('请明确是否加载 WikiSkill');
  return fields.use_wikiskill !== false;
}
export function buildVideoProject(fields, referenceIds, id) {
  const title = text(fields.title).trim(), goal = text(fields.goal).trim();
  if (!title || title.length > 200 || !goal || goal.length > 8000) throw Error('请填写项目名称和学习目标，并缩短过长的内容');
  if (!Array.isArray(referenceIds) || referenceIds.length < 1 || referenceIds.length > 1
    || new Set(referenceIds).size !== referenceIds.length || referenceIds.some(value => !IDENTIFIER.test(value ?? ''))) throw Error('请先上传有效的参考视频');
  const criteria = text(fields.criteria_text).split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  if (criteria.length > 32 || criteria.some(value => value.length > 2000)) throw Error('验收标准最多 32 项，每项不超过 2000 字');
  return {request_id: requestId(id), title, goal, reference_ids: [...referenceIds], criteria,
    width: number(fields.width ?? 1280, 320, 1280, '输出宽度应为 320 至 1280 像素'),
    height: number(fields.height ?? 720, 180, 720, '输出高度应为 180 至 720 像素'),
    use_wikiskill: skillSelection(fields), ...videoClip(fields), ...videoBudget(fields), ...learningOptions(fields),
    ...(fields.analysis_id && fields.segment_index !== undefined && fields.segment_index !== '' ? {
      analysis_id: videoId(fields.analysis_id), segment_index: number(fields.segment_index, 0, 100, '候选编号不正确')} : {})};
}
export function buildVideoExperiment(fields, id) {
  const mode = fields.mode ?? 'continue';
  if (!['continue', 'fresh', 'retry', 'transfer'].includes(mode)) throw Error('请选择有效的实验方式');
  // A technical retry retains the immutable original brief and knowledge pin.
  if (mode === 'retry') return {request_id: requestId(id), mode};
  if (mode === 'transfer') {
    if (fields.approve_candidate !== true) throw Error('请明确认可所选作品的效果，再开始换内容验证');
    return {request_id: requestId(id), mode, base_candidate_id: videoId(fields.base_candidate_id),
      approve_candidate: true, max_rounds: 1,
      max_seconds: number(fields.max_minutes ?? 60, 1, 60, '验证时间上限应为 1 至 60 分钟') * 60};
  }
  const body = {request_id: requestId(id), mode, ...videoBudget(fields)};
  const feedback = text(fields.feedback).trim();
  if (feedback.length > 8000) throw Error('本次意见不能超过 8000 字');
  if (mode === 'continue' && !feedback) throw Error('请说明下一次实验希望改善哪里');
  return {...body, feedback, use_wikiskill: skillSelection(fields), ...learningOptions(fields),
    ...(mode === 'continue' && fields.base_candidate_id ? {base_candidate_id: videoId(fields.base_candidate_id)} : {})};
}
export function videoClip(fields) {
  const start = Number(fields.start_seconds), end = Number(fields.end_seconds);
  if (fields.start_seconds === '' || fields.end_seconds === '' || !Number.isFinite(start) || !Number.isFinite(end)
    || start < 0 || end <= start || end - start < 1 || end - start > 60) throw Error('请选择 1 至 60 秒的明确学习片段');
  if (Math.abs((end - start) * 24 - Math.round((end - start) * 24)) > 1e-6) throw Error('片段时长须对齐 24 fps；可以填写整数秒');
  if (fields.learn_audio !== undefined && typeof fields.learn_audio !== 'boolean') throw Error('请明确是否学习音效');
  return {start_seconds: start, end_seconds: end, learn_audio: fields.learn_audio !== false};
}
export function validateReferenceFile(file) {
  if (!file || !['video/mp4', 'video/webm'].includes(file.type) || !Number.isInteger(file.size)
    || file.size < 1 || file.size > MAX_REFERENCE_BYTES) throw Error('请选择不超过 24 MB 的 MP4 或 WebM 视频');
  const name = text(file.name).replace(/.*[\\/]/, '');
  if (!name || name.length > 200 || /[\u0000-\u001f\u007f]/.test(name)) throw Error('参考文件名不正确');
  return {file_name: name, mime_type: file.type};
}
export function videoAssetUrl(value, root) {
  if (typeof value !== 'string' || !value || /[\u0000-\u0020\\]/.test(value)) return null;
  try {
    const base = new URL(root), prefix = new URL(VIDEO_API, base).pathname;
    let url = new URL(value, base);
    if (url.origin === base.origin && !url.username && !url.password && url.pathname.startsWith('/api/video-lab/')) {
      url = new URL(url.pathname.slice(1) + url.search + url.hash, base);
    }
    if (!['http:', 'https:'].includes(url.protocol) || url.origin !== base.origin || url.username || url.password
      || !url.pathname.startsWith(prefix) || url.pathname === prefix || url.hash || url.search
      || /%(?:2f|5c|00|0a|0d)/i.test(url.pathname)) return null;
    return url.href;
  } catch { return null; }
}
export function videoCandidates(project) {
  return rows(project?.candidates).length ? project.candidates : rows(project?.experiments).flatMap(row => rows(row.candidates));
}
export function latestVideoExperiment(project) {
  const list = rows(project?.experiments);
  return project?.active_experiment ?? project?.latest_experiment ?? list.at(-1) ?? null;
}
export function videoProjectState(project) { return latestVideoExperiment(project)?.state ?? project?.state ?? 'idle'; }
export function videoIsActive(project) { return VIDEO_ACTIVE_STATES.has(videoProjectState(project)); }
export function videoCandidateRole(candidate) { return (candidate?.role ?? candidate?.work_kind) === 'transfer' ? '换内容验证作品' : '复现作品'; }
export function videoDefaultCandidate(project, selection = null) {
  const candidates = videoCandidates(project);
  return candidates.find(row => row.id === selection)
    ?? candidates.find(row => row.id === project?.adopted_candidate_id)
    ?? candidates.find(row => row.id === project?.best_candidate_id)
    ?? candidates.filter(row => (row.role ?? row.work_kind) !== 'transfer').at(-1) ?? candidates.at(-1) ?? null;
}
export function videoProjectMatches(project, query) {
  return (text(project?.title) + '\n' + text(project?.goal)).toLocaleLowerCase().includes(text(query).trim().toLocaleLowerCase());
}
export function videoRequestPath(projectId, action = '') {
  return VIDEO_API + 'projects/' + encodeURIComponent(videoId(projectId)) + (action ? '/' + action : '');
}
export function videoArtifactPath(projectId, artifactId) {
  return videoRequestPath(projectId, 'artifacts/' + encodeURIComponent(videoId(artifactId)));
}
