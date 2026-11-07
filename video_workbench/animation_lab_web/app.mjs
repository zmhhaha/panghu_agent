import {isCurrentAnnotation, issueLocation, isCurrentEvaluation, evaluationFindingLocation,
  currentEvaluationDecisions, localResourceUrl, warmPreviewAssets, iterationPullRequestUrl,
  iterationResourceUrl, isIterationOverview, iterationStateLabel, canReviewIteration,
  iterationAttemptReceipt, canImportIterationReceipt, verifiedIterationCandidate, canAcceptIteration,
  filterAnimationCases, animationFacetOptions, animationCoverage, animationEngineLabel,
  animationRequirementRows, animationRequirementsUrl} from './model.mjs';

const $ = id => document.getElementById(id);
const applicationRoot = new URL('./', import.meta.url);
const TAGS = {layout: '布局', typography: '文字', connector: '连线', motion: '动作', pacing: '节奏', continuity: '衔接', clarity: '表达', other: '其他'};
const SEVERITIES = {note: '建议', minor: '轻微', major: '明显', blocker: '严重'};
const PREFERENCES = {baseline: 'A 更好', candidate: 'B 更好', tie: '差不多', neither: '都需改进'};
const MODEL_STATES = {not_run: '尚未调用', unavailable: '模型暂不可用', failed: '调用失败，已停止', invalid: '评审证据无效'};
const MODEL_PREFERENCES = {A: '倾向 A', B: '倾向 B', tie: '两版相当', inconclusive: '证据不足，暂无法判断'};
const DECISIONS = {needs_fix: '确认需修改', dismiss: '此项不成立', defer: '暂缓判断'};
let catalog = {modules: [], cases: []};
let currentCase = null;
let players = [];
let annotations = [];
let time = 0;
let duration = 0;
let playing = false;
let animationFrame = null;
let playStart = 0;
let playTime = 0;
let lastDispatch = 0;
let requestSerial = 0;
let caseEpoch = 0;
let previewWarmController = null;
let loadSerial = 0;
let drawing = false;
let region = null;
let regionVariant = null;
let viewedRegion = null;
let pointerOrigin = null;
let saving = false;
let dirty = false;
let mutationSerial = 0;
let evaluationState = null;
let evaluationBusy = false;
let evaluationOperation = 0;
let evaluationLoadSerial = 0;
const drafts = new Map();
const decisionDrafts = new Map();
const knownReviews = new Set();
const loadTimers = new Set();
let iterationState = null;
let iterationLoadSerial = 0;
let iterationLoading = false;
let iterationBusy = false;
const iterationReceiptDrafts = new Map();
const iterationReviewDrafts = new Map();

function node(tag, text, className) {
  const value = document.createElement(tag);
  if (text !== undefined) value.textContent = String(text);
  if (className) value.className = className;
  return value;
}

function formatTime(value) {
  const milliseconds = Math.round(Math.max(0, Number(value) || 0) * 1000);
  return `${String(Math.floor(milliseconds / 60000)).padStart(2, '0')}:${String(Math.floor(milliseconds / 1000) % 60).padStart(2, '0')}.${String(milliseconds % 1000).padStart(3, '0')}`;
}

function shortIdentity(value) {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? '');
  return text.length > 20 ? `${text.slice(0, 12)}…${text.slice(-6)}` : text;
}

function globalMessage(text, error = false) {
  $('global-message').textContent = text;
  $('global-message').classList.toggle('error', error);
  $('global-message').hidden = !text;
}

function formMessage(text, error = false) {
  $('annotation-message').textContent = text;
  $('annotation-message').classList.toggle('error', error);
  $('annotation-message').hidden = !text;
}

async function request(path, options = {}) {
  const headers = {'Accept': 'application/json', ...(options.headers ?? {})};
  if (options.body) {
    headers['Content-Type'] = 'application/json';
    headers['X-Animation-Lab'] = '1';
    if (catalog.csrf_token) headers['X-Animation-Lab-Token'] = catalog.csrf_token;
  }
  const response = await fetch(new URL(path.replace(/^\//, ''), applicationRoot),
    {...options, headers, credentials: 'same-origin', cache: 'no-store'});
  let value;
  try { value = await response.json(); } catch { throw new Error(`服务返回了无法读取的内容（HTTP ${response.status}）。`); }
  if (!response.ok) {
    const message = value.message ?? value.error?.message ?? value.error ?? value.detail ?? `HTTP ${response.status}`;
    throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
  }
  return value;
}

function moduleFor(id) { return catalog.modules.find(row => row.id === id); }
function caseIdentity(row = currentCase) { return row?.case_identity ?? row?.identity ?? ''; }
function playablePlayers() { return players.filter(row => row.variant.available !== false && !row.error); }
function allReady() { return players.length > 0 && players.every(row => row.ready && !row.error && row.variant.available !== false); }
function isRetracted(row) { return Boolean(row.retracted_at || row.retracted || row.status === 'retracted'); }

function iterationMessage(text, error = false) {
  $('iteration-message').textContent = text;
  $('iteration-message').classList.toggle('error', error);
  $('iteration-message').hidden = !text;
}

function syncIterationModules() {
  const previous = $('iteration-module').value;
  $('iteration-module').replaceChildren(...catalog.modules.map(module => {
    const option = node('option', module.label ?? module.id); option.value = module.id; return option;
  }));
  $('iteration-module').value = catalog.modules.some(module => module.id === previous)
    ? previous : catalog.modules.find(module => module.id === currentCase?.module)?.id ?? catalog.modules[0]?.id ?? '';
  invalidateIterations();
}

function invalidateIterations() {
  ++iterationLoadSerial;
  iterationState = null;
  iterationLoading = false;
  $('iteration-refresh-note').textContent = '待刷新';
  renderIterations();
  if ($('iteration-panel').open) void loadIterations();
}

function iterationFeedbackCard(feedback, change) {
  const article = node('article', undefined, 'iteration-feedback-item');
  const heading = node('header');
  const kind = {issue: '问题标注', preference: '版本偏好', review: '人工评价', needs_fix: '已确认评测问题'}[feedback?.kind]
    ?? (feedback?.source_type === 'evaluation_finding' ? '已确认评测问题' : '反馈记录');
  heading.append(node('strong', kind));
  if (change) heading.append(node('span', change.op === 'inactivate' ? '停止作为指导' : '新增或更新',
    `iteration-change-label ${change.op === 'inactivate' ? 'inactive' : ''}`));
  if (feedback?.severity && SEVERITIES[feedback.severity]) heading.append(node('span', SEVERITIES[feedback.severity], `severity-label ${feedback.severity}`));
  article.append(heading);
  const label = feedback?.title ?? catalog.cases.find(row => row.id === feedback?.case_id)?.title ?? feedback?.case_id ?? '冻结样例';
  const times = Number.isFinite(feedback?.start_seconds)
    ? ` · ${formatTime(feedback.start_seconds)}${Number.isFinite(feedback.end_seconds) && feedback.end_seconds > feedback.start_seconds ? `—${formatTime(feedback.end_seconds)}` : ''}` : '';
  article.append(node('p', `${label}${feedback?.variant_id ? ` · ${feedback.variant_id}` : ''}${times}`, 'muted small'));
  const text = feedback?.comment || feedback?.description
    || (feedback?.kind === 'preference' ? PREFERENCES[feedback.preference]
      : feedback?.kind === 'review' ? ({acceptable: '效果可接受', needs_work: '还需改进'})[feedback.verdict] : '')
    || '保留原始反馈及其版本依据。';
  article.append(node('p', text, 'iteration-feedback-comment'));
  if (feedback?.description && feedback.description !== text) article.append(node('p', feedback.description, 'iteration-feedback-comment'));
  if (change?.reason) article.append(node('p', ({new: '新记录', updated: '意见已更新', retracted: '原意见已撤回',
    superseded: '已被新的偏好或评价替代', decision_dismiss: '已改判为此项不成立',
    decision_defer: '已改为暂缓判断', no_longer_effective: '原意见不再有效'})[change.reason] ?? change.reason, 'muted small'));
  if (feedback?.historical_source === true) article.append(node('p', '来自历史样例版本；保留原版本依据，不代表当前画面仍存在此问题。', 'iteration-stale'));
  const metadata = [feedback?.region ? '含画面区域' : '', ...(feedback?.tags ?? []).map(tag => TAGS[tag] ?? tag)].filter(Boolean);
  if (metadata.length) article.append(node('p', metadata.join(' · '), 'muted small'));
  const report = iterationResourceUrl(feedback?.links?.report_url, location.origin, applicationRoot.pathname);
  if (report) { const link = node('a', '查看冻结评测报告 ↗', 'small'); link.href = report; link.target = '_blank'; link.rel = 'noopener noreferrer'; article.append(link); }
  if (feedback?.record_id) article.append(node('code', feedback.record_id, 'iteration-record-id'));
  return article;
}

function iterationTaskCard(task) {
  const article = node('article', undefined, 'iteration-task');
  const validId = typeof task?.task_id === 'string' && /^[0-9a-f]{64}$/.test(task.task_id);
  const heading = node('header');
  heading.append(node('strong', validId ? shortIdentity(task.task_id) : '任务记录待核对'),
    node('span', iterationStateLabel(task?.state), 'iteration-state'));
  article.append(heading);
  if (!validId) { article.append(node('p', '任务身份不完整，请刷新后核对。', 'muted small')); return article; }
  const source = task.package?.source_revision ?? '';
  const metadata = [task.created_at ? new Date(task.created_at).toLocaleString('zh-CN') : '',
    /^[0-9a-f]{40}$/.test(source) ? `源码 ${shortIdentity(source)}` : '源码版本未配置'].filter(Boolean);
  if (metadata.length) article.append(node('p', metadata.join(' · '), 'muted small'));
  if (task.current_feedback_matches === false) article.append(node('p', '当前意见已变化；本任务保留原依据，不能用来确认新一轮反馈已经解决。', 'iteration-stale'));
  const actions = node('div', undefined, 'iteration-task-actions');
  const download = node('a', '下载轻量任务包', 'button secondary');
  download.href = new URL(`api/iterations/${task.task_id}/export`, applicationRoot).href;
  download.download = `animation-iteration-${task.task_id.slice(0, 12)}.json`; actions.append(download);
  const receipt = iterationAttemptReceipt(task);
  const prUrl = iterationPullRequestUrl(receipt.pr_url);
  if (prUrl) { const link = node('a', '查看 GitHub PR ↗', 'button secondary'); link.href = prUrl; link.target = '_blank'; link.rel = 'noopener noreferrer'; actions.append(link); }
  const candidateUrl = iterationResourceUrl(receipt.candidate_url, location.origin, applicationRoot.pathname);
  if (candidateUrl) { const link = node('a', '查看候选样例 ↗', 'button secondary'); link.href = candidateUrl; link.target = '_blank'; link.rel = 'noopener noreferrer'; actions.append(link); }
  article.append(actions);
  const execution = [receipt.run_id ? `执行 ${receipt.run_id}` : '', receipt.branch ? `分支 ${receipt.branch}` : '', receipt.commit ? `提交 ${shortIdentity(receipt.commit)}` : ''].filter(Boolean);
  if (execution.length) article.append(node('p', execution.join(' · '), 'iteration-execution muted small'));
  if (task.events?.length) article.append(node('p', '执行与候选状态来自已登记回执；外部结果未在此独立核验，可打开 PR 和证据核对。', 'muted small'));
  const verified = verifiedIterationCandidate(task);
  if (verified) {
    article.append(node('p', '冻结候选资料已核对；与提交的关联由开发者登记，尚无独立构建证明。', 'muted small'));
    article.append(node('p', `候选样例 ${verified.case_id} · 覆盖 ${verified.covered_feedback_keys.length} 条意见 · 未覆盖 ${verified.uncovered_feedback_keys.length} 条意见`, 'muted small'));
    if (verified.uncovered_feedback_keys.length) article.append(node('p', '这份候选只覆盖部分意见，其余样例仍需处理，当前不能将本任务判为通过。', 'iteration-stale'));
  }
  if (task.state === 'rejected') article.append(node('p', '验收说明保留在任务历史；如需下一轮采用，请在对应候选样例添加问题标注，再生成新任务。原任务重试不会自动吸收这段新评价。', 'iteration-stale'));
  if (task.state === 'candidate_ready') {
    const review = node('div', undefined, 'iteration-review');
    const label = node('label', '候选样例验收意见');
    const comment = node('textarea'); comment.rows = 2; comment.maxLength = 2000;
    comment.placeholder = '说明哪些效果已改善，或还有哪些问题；此处仅验收开发样例。';
    comment.value = iterationReviewDrafts.get(task.task_id) ?? '';
    comment.addEventListener('input', () => iterationReviewDrafts.set(task.task_id, comment.value));
    label.append(comment); review.append(label);
    const controls = node('div', undefined, 'iteration-task-actions');
    for (const [state, title] of [['accepted', '开发样例通过'], ['rejected', '仍需改进']]) {
      const button = node('button', title, 'button secondary'); button.type = 'button';
      button.disabled = iterationBusy || (state === 'accepted' ? !canAcceptIteration(task) : !canReviewIteration(task));
      button.addEventListener('click', () => reviewIteration(task, state, comment.value)); controls.append(button);
    }
    if (!canReviewIteration(task)) review.append(node('p', '候选尚未取得匹配的发布核验，或反馈已变化；当前记录不能提交样例验收。', 'muted small'));
    review.append(controls, node('p', '通过仅记录这份开发样例的判断，合入和业务采用另行决定。', 'muted small'));
    article.append(review);
  }
  const history = node('details', undefined, 'iteration-event-history');
  history.append(node('summary', `处理历史（${task.events?.length ?? 0}）`));
  for (const event of task.events ?? []) {
    const row = node('div', undefined, 'iteration-event');
    row.append(node('p', `${iterationStateLabel(event.state)}${event.created_at ? ` · ${new Date(event.created_at).toLocaleString('zh-CN')}` : ''}`, 'small'));
    if (event.receipt?.comment) row.append(node('p', event.receipt.comment, 'muted small'));
    const links = node('div', undefined, 'iteration-task-actions');
    const pr = iterationPullRequestUrl(event.receipt?.pr_url);
    if (pr) { const link = node('a', '本条回执的 PR ↗', 'small'); link.href = pr; link.target = '_blank'; link.rel = 'noopener noreferrer'; links.append(link); }
    const candidate = iterationResourceUrl(event.receipt?.candidate_url, location.origin, applicationRoot.pathname);
    if (candidate) { const link = node('a', '本条回执的候选 ↗', 'small'); link.href = candidate; link.target = '_blank'; link.rel = 'noopener noreferrer'; links.append(link); }
    if (links.children.length) row.append(links);
    history.append(row);
  }
  article.append(history);
  if (canImportIterationReceipt(task)) {
    const imported = node('details', undefined, 'iteration-receipt-import');
    imported.append(node('summary', '导入外部执行回执'), node('p', '粘贴独立开发执行器返回的完整事件 JSON。外部回执未在这里独立核验；登记不会启动 Agent，也不需要填写凭据。', 'muted small'));
    if (['rejected', 'failed', 'canceled'].includes(task.state)) imported.append(node('p', '本任务已停止；这里只接受显式新执行尝试的回执，并保留先前历史。登记不会自动重试。', 'muted small'));
    const textarea = node('textarea'); textarea.rows = 4; textarea.maxLength = 32768;
    textarea.setAttribute('aria-label', '外部执行回执 JSON'); textarea.spellcheck = false;
    textarea.value = iterationReceiptDrafts.get(task.task_id) ?? '';
    textarea.addEventListener('input', () => iterationReceiptDrafts.set(task.task_id, textarea.value));
    const button = node('button', '核对并登记回执', 'button secondary'); button.type = 'button'; button.disabled = iterationBusy;
    button.addEventListener('click', () => importIterationReceipt(task, textarea.value));
    imported.append(textarea, button); article.append(imported);
  }
  return article;
}

function renderIterations() {
  const module = $('iteration-module').value;
  const current = isIterationOverview(iterationState, module);
  $('iteration-refresh').disabled = iterationBusy || iterationLoading || !module;
  $('iteration-prepare').disabled = iterationBusy || iterationLoading || !current || !iterationState.preview_delta.length;
  $('iteration-prepare').textContent = iterationBusy ? '正在保存…' : '生成增量任务包';
  $('iteration-summary').replaceChildren();
  $('iteration-feedback').replaceChildren(); $('iteration-changes').replaceChildren(); $('iteration-tasks').replaceChildren();
  for (const id of ['iteration-feedback-count', 'iteration-change-count', 'iteration-task-count']) $(id).textContent = current ? '0' : '—';
  if (!current) {
    $('iteration-source').textContent = '';
    $('iteration-tasks').append(node('p', iterationLoading ? '正在读取本模块的反馈和任务…' : '展开面板或刷新后，查看本模块的有效反馈与开发记录。', 'muted small'));
    return;
  }
  const value = iterationState;
  const active = value.current_effective_feedback;
  const changes = value.preview_delta;
  const inactivated = changes.filter(change => change.op === 'inactivate').length;
  for (const [label, count] of [['有效意见', active.length], ['增量变化', changes.length], ['撤回或改判', inactivated], ['开发任务', value.tasks.length]]) {
    const card = node('article'); card.append(node('span', label), node('strong', count)); $('iteration-summary').append(card);
  }
  const source = /^[0-9a-f]{40}$/.test(value.source_revision ?? '') ? value.source_revision : '未配置，不能作为已绑定源码的开发任务';
  $('iteration-source').textContent = `模块：${moduleFor(module)?.label ?? module} · 源码：${source} · 当前意见 ${shortIdentity(value.current_cursor)} · 已同步 ${value.module_cursor ? shortIdentity(value.module_cursor) : '尚无'}`;
  $('iteration-feedback-count').textContent = String(active.length);
  $('iteration-change-count').textContent = String(changes.length);
  $('iteration-task-count').textContent = String(value.tasks.length);
  for (const feedback of active) $('iteration-feedback').append(iterationFeedbackCard(feedback));
  for (const change of changes) $('iteration-changes').append(iterationFeedbackCard(change.feedback, change));
  for (const task of value.tasks) $('iteration-tasks').append(iterationTaskCard(task));
  if (!active.length) $('iteration-feedback').append(node('p', '本模块当前没有有效意见。技术验收的已撤回记录仍保留在完整历史中。', 'muted small'));
  if (!changes.length) $('iteration-changes').append(node('p', '相对已同步意见没有新增变化。任务执行状态仍需单独核对。', 'muted small'));
  if (!value.tasks.length) $('iteration-tasks').append(node('p', '尚未生成开发任务。可以先标注问题，再为本模块生成增量任务包。', 'muted small'));
}

async function loadIterations() {
  const module = $('iteration-module').value;
  if (!module) return;
  const serial = ++iterationLoadSerial;
  iterationLoading = true; iterationState = null; renderIterations();
  $('iteration-refresh-note').textContent = '正在读取';
  try {
    const result = await request(`/api/iterations?module=${encodeURIComponent(module)}`);
    if (serial !== iterationLoadSerial || module !== $('iteration-module').value) return;
    const summary = result.summary ?? result;
    if (!isIterationOverview(summary, module)) throw new Error('反馈汇总与当前模块或版本不匹配，请刷新核对。');
    iterationState = summary;
    $('iteration-refresh-note').textContent = summary.preview_delta.length ? `${summary.preview_delta.length} 项变化` : '已更新';
  } catch (error) {
    if (serial === iterationLoadSerial && module === $('iteration-module').value) {
      $('iteration-refresh-note').textContent = '读取失败';
      iterationMessage(`读取反馈迭代失败：${error.message}`, true);
    }
  } finally {
    if (serial === iterationLoadSerial && module === $('iteration-module').value) { iterationLoading = false; renderIterations(); }
  }
}

async function prepareIteration() {
  const module = $('iteration-module').value;
  if (iterationBusy || !isIterationOverview(iterationState, module) || !iterationState.preview_delta.length) return;
  const summary = iterationState;
  iterationBusy = true; renderIterations(); iterationMessage('正在保存本轮反馈依据和增量任务…');
  try {
    const result = await request('/api/iterations', {method: 'POST', body: JSON.stringify({module,
      base_cursor: summary.module_cursor, expected_cursor: summary.current_cursor})});
    const task = result.task ?? result;
    if (!task || !/^[0-9a-f]{64}$/.test(task.task_id ?? '') || task.package?.module !== module)
      throw new Error('任务回执与本轮模块不匹配，请刷新后核对。');
    if (module === $('iteration-module').value) iterationMessage('增量任务已保存，可从任务记录下载。生成和下载不会标记为已执行或已修复。');
    await loadIterations();
  } catch (error) {
    if (module === $('iteration-module').value) iterationMessage(`任务生成未完成：${error.message}。未自动重试，请刷新任务记录核对。`, true);
  } finally { iterationBusy = false; renderIterations(); }
}

async function recordIterationEvent(task, event) {
  if (iterationBusy) return;
  const module = $('iteration-module').value;
  iterationBusy = true; renderIterations(); iterationMessage('正在核对并保存任务事件…');
  try {
    const result = await request(`/api/iterations/${encodeURIComponent(task.task_id)}/events`, {method: 'POST', body: JSON.stringify(event)});
    const updated = result.task ?? result;
    if (updated?.task_id !== task.task_id || updated.state !== event.state) throw new Error('事件回执与本次任务不匹配，请刷新核对。');
    iterationReceiptDrafts.delete(task.task_id); iterationReviewDrafts.delete(task.task_id);
    if (module === $('iteration-module').value) iterationMessage(`已保存：${iterationStateLabel(updated.state)}。`);
    await loadIterations();
  } catch (error) {
    if (module === $('iteration-module').value) iterationMessage(`事件未确认保存：${error.message}。原输入保留，未自动重试；请刷新后核对。`, true);
  } finally { iterationBusy = false; renderIterations(); }
}

function importIterationReceipt(task, text) {
  if (iterationBusy) return;
  try {
    if (!text.trim() || text.length > 32768) throw new Error('请粘贴不超过 32KB 的完整事件 JSON。');
    const event = JSON.parse(text);
    if (!event || typeof event !== 'object' || Array.isArray(event) || event.expected_state !== task.state
        || typeof event.state !== 'string' || typeof event.idempotency_key !== 'string'
        || !event.idempotency_key || !event.receipt || event.receipt.task_identity !== task.task_id)
      throw new Error('请使用与当前任务及状态匹配的完整执行事件；不要省略身份和幂等键。');
    void recordIterationEvent(task, event);
  } catch (error) { iterationMessage(`回执尚未提交：${error.message}`, true); }
}

function reviewIteration(task, state, text) {
  if (iterationBusy || !canReviewIteration(task) || (state === 'accepted' && !canAcceptIteration(task))) return;
  const comment = text.trim();
  if (!comment) { iterationMessage('请填写候选样例的验收意见，再选择通过或继续改进。', true); return; }
  const receipt = {task_identity: task.task_id, module: task.package.module,
    source_revision: task.package.source_revision, comment};
  void recordIterationEvent(task, {state, expected_state: task.state,
    idempotency_key: crypto.randomUUID(), receipt});
}

$('iteration-panel').addEventListener('toggle', () => {
  if ($('iteration-panel').open && !iterationState && !iterationLoading) { iterationMessage(''); void loadIterations(); }
});
$('iteration-module').addEventListener('change', () => { iterationMessage(''); invalidateIterations(); });
$('iteration-refresh').addEventListener('click', () => { iterationMessage(''); void loadIterations(); });
$('iteration-prepare').addEventListener('click', prepareIteration);

function setDirty(value = true) {
  dirty = value;
  $('draft-status').textContent = dirty ? '有未保存的标注草稿' : '可标注时间区间与画面区域';
}

function captureDraft() {
  if (!currentCase) return;
  drafts.set(currentCase.id, {
    identity: caseIdentity(), variant: $('annotation-variant').value,
    severity: $('annotation-severity').value, start: $('annotation-start').value,
    end: $('annotation-end').value, comment: $('annotation-comment').value,
    tags: Array.from(document.querySelectorAll('input[name="tag"]:checked'), input => input.value),
    region: region ? {...region} : null, regionVariant, dirty,
  });
}

function restoreDraft() {
  const draft = drafts.get(currentCase.id);
  $('annotation-form').reset();
  $('annotation-variant').value = players.at(-1)?.variant.id ?? '';
  $('annotation-start').value = '0';
  $('annotation-end').value = '0';
  region = null;
  regionVariant = null;
  if (draft?.identity === caseIdentity()) {
    if (players.some(row => row.variant.id === draft.variant)) $('annotation-variant').value = draft.variant;
    $('annotation-severity').value = draft.severity;
    $('annotation-start').value = draft.start;
    $('annotation-end').value = draft.end;
    $('annotation-comment').value = draft.comment;
    for (const input of document.querySelectorAll('input[name="tag"]')) input.checked = draft.tags.includes(input.value);
    region = draft.region;
    regionVariant = draft.regionVariant;
    setDirty(draft.dirty);
  } else setDirty(false);
  renderRegion();
  formMessage('');
}

function caseFilters() {
  return {module: $('module-filter').value, domain: $('domain-filter').value,
    engine: $('engine-filter').value, query: $('case-search').value, review: $('review-filter').value};
}

function filteredCases() { return filterAnimationCases(catalog.cases, caseFilters(), knownReviews); }

function renderRequirementsMatrix() {
  const coverage = animationCoverage(catalog.cases, catalog.modules);
  $('requirements-count').textContent = `${coverage.total} 个跨领域需求样例`;
  $('requirements-summary').replaceChildren(...[
    ['能力模块', coverage.modules.length], ['领域', coverage.domains.length],
    ['渲染引擎', coverage.engines.length], ['需求样例', coverage.total],
  ].map(([label, count]) => { const card = node('article'); card.append(node('span', label), node('strong', count)); return card; }));
  const table = node('table'), head = node('thead'), header = node('tr');
  for (const label of ['能力模块', '样例数', '覆盖领域', '渲染引擎']) header.append(node('th', label));
  head.append(header); table.append(head);
  const body = node('tbody');
  for (const module of coverage.modules) {
    const row = node('tr');
    for (const value of [module.label, module.cases, module.domains.map(item => item.label).join('、') || '待分类',
      module.engines.map(item => item.label).join('、') || '待分类']) row.append(node('td', value));
    body.append(row);
  }
  table.append(body); $('requirements-coverage').replaceChildren(table);
  updateRequirementsDownload();
}

function updateRequirementsDownload() {
  const filters = caseFilters();
  const url = animationRequirementsUrl(applicationRoot, filters);
  const count = filterAnimationCases(catalog.cases, {module: filters.module, domain: filters.domain, engine: filters.engine})
    .filter(row => row.is_matrix_case === true).length;
  const link = $('requirements-export');
  if (url && count) { link.href = url; link.removeAttribute('aria-disabled'); }
  else { link.removeAttribute('href'); link.setAttribute('aria-disabled', 'true'); }
  link.textContent = `下载模块／领域／引擎需求集（${count}）`;
  $('requirements-export-note').textContent = '检索词和评价状态只影响左侧列表。';
}

function renderCaseRequirement(row) {
  const rows = animationRequirementRows(row);
  $('case-requirement').hidden = !rows.length;
  $('case-requirement-meta').textContent = [row.domain_label, animationEngineLabel(row.engine),
    {basic: '基础', intermediate: '进阶', boundary: '边界'}[row.difficulty]].filter(Boolean).join(' · ');
  $('case-requirement-body').replaceChildren(...rows.map(item => {
    const section = node('section'); section.append(node('h4', item.label));
    if (item.values.length === 1) section.append(node('p', item.values[0]));
    else { const list = node('ul'); for (const value of item.values) list.append(node('li', value)); section.append(list); }
    return section;
  }));
}

function renderCaseList() {
  const selectedModule = $('module-filter').value;
  const matches = filteredCases();
  $('case-count').textContent = String(matches.length);
  $('filter-summary').textContent = `当前组合：${matches.length} 个样例，${animationFacetOptions(matches, 'domain').length} 个领域。`;
  const list = $('case-list');
  list.replaceChildren();
  for (const row of matches) {
    const button = node('button', undefined, 'case-button');
    button.type = 'button';
    button.setAttribute('aria-current', String(currentCase?.id === row.id));
    const category = moduleFor(row.module)?.label ?? row.module;
    button.append(node('span', `${category}${row.domain_label ? ' · '+row.domain_label : ''}${knownReviews.has(`${row.id}:${caseIdentity(row)}`) ? ' · 已核看' : ''}`, 'case-category'), node('strong', row.title), node('span', row.description ?? '', 'case-description'));
    button.addEventListener('click', () => selectCase(row));
    list.append(button);
  }
  if (!matches.length) list.append(node('p', '当前筛选组合没有样例，请调整模块、领域、引擎、检索词或评价状态。', 'muted small'));
  const selected = moduleFor(selectedModule);
  const available = !selected || ['ready', 'available', 'implemented', 'active'].includes(selected.status ?? 'ready');
  $('module-note').hidden = available;
  $('module-note').textContent = available ? '' : selected.reason ?? selected.description ?? '这项能力尚未接入，本页不提供模拟效果。';
  for (const button of document.querySelectorAll('[data-evaluation-case]')) button.setAttribute('aria-current', String(button.dataset.evaluationCase === currentCase?.id));
  updateRequirementsDownload();
}

function evaluationMessage(text, error = false) {
  $('evaluation-message').textContent = text;
  $('evaluation-message').classList.toggle('error', error);
  $('evaluation-message').hidden = !text;
}

function renderEvaluation() {
  const expected = currentCase?.evaluation;
  $('automatic-review').hidden = !expected;
  $('evaluation-findings').replaceChildren();
  $('revision-task').hidden = true;
  $('evaluation-empty').hidden = true;
  $('evaluation-context').hidden = true;
  if (!expected) return;
  if (!isCurrentEvaluation(evaluationState, currentCase)) {
    $('evaluation-technical').textContent = '正在读取';
    $('evaluation-model').textContent = '正在读取';
    $('evaluation-developer').textContent = '尚未确认';
    return;
  }
  const evaluation = evaluationState;
  const findings = [...(evaluation.findings ?? [])].sort((a, b) =>
    ({blocker: 0, major: 1, minor: 2, note: 3}[a.severity] ?? 4) - ({blocker: 0, major: 1, minor: 2, note: 3}[b.severity] ?? 4));
  const decisions = currentEvaluationDecisions(evaluation, currentCase);
  const counts = {needs_fix: 0, dismiss: 0, defer: 0};
  for (const decision of decisions) counts[decision.decision]++;
  $('evaluation-technical').textContent = {passed: '采样检查通过', failed: '采样检查未通过', not_run: '尚未检查'}[evaluation.technical?.status] ?? '检查状态未知';
  $('evaluation-model').textContent = evaluation.model?.status === 'reviewed'
    ? MODEL_PREFERENCES[evaluation.model.preference] ?? '已返回意见'
    : MODEL_STATES[evaluation.model?.status] ?? '状态未知';
  $('evaluation-developer').textContent = decisions.length
    ? `${counts.needs_fix} 项待修改 · ${counts.dismiss} 项不成立 · ${counts.defer} 项暂缓`
    : findings.length ? `${findings.length} 项待核看` : '暂无问题需处理';
  const task = localResourceUrl(evaluation.task_url, location.origin);
  if (counts.needs_fix > 0 && task) { $('revision-task').href = task; $('revision-task').hidden = false; }
  if (!findings.length) {
    $('evaluation-empty').hidden = false;
    $('evaluation-empty').textContent = evaluation.technical?.status === 'failed'
      ? '采样检查未通过，当前没有可定位的问题记录，请结合原始报告核对。'
      : evaluation.model?.status === 'reviewed'
        ? '采样画面中未发现有证据支持的问题。可以结合原始意见与播放画面，继续判断效果差异。'
        : '当前没有可定位的问题记录。视觉模型尚未完成有效审阅，不能据此判断整体效果。';
  }
  $('evaluation-reason').textContent = evaluation.model?.comparison_reason ?? '';
  $('evaluation-limitations').replaceChildren(...(evaluation.model?.limitations ?? []).map(value => node('li', value)));
  $('evaluation-context').hidden = false;
  for (const finding of findings) {
    const article = node('article', undefined, 'evaluation-finding');
    const header = node('header');
    header.append(node('span', finding.source === 'technical' ? '技术检查' : '视觉模型', `finding-source ${finding.source}`),
      node('strong', TAGS[finding.category] ?? '效果问题'),
      node('span', SEVERITIES[finding.severity] ?? finding.severity ?? '待判断', `severity-label ${finding.severity}`));
    if (finding.claim === 'sampled_motion') header.append(node('span', '采样动作窗', 'muted small'));
    article.append(header, node('p', finding.description ?? '', 'finding-description'));
    const evidence = node('div', undefined, 'finding-evidence');
    for (const frame of finding.frames ?? []) {
      const target = evaluationFindingLocation(evaluation, currentCase, finding.id, frame.id);
      const player = players.find(value => value.variant.id === frame.variant_id);
      const letter = player?.key === 'baseline' ? 'A' : player?.key === 'candidate' ? 'B' : '未知版本';
      const label = `${letter} · ${formatTime(frame.time_seconds)}`;
      const jump = node('button', undefined, 'finding-jump');
      jump.type = 'button'; jump.title = frame.id;
      jump.setAttribute('aria-label', `定位 ${label} · ${frame.id}`);
      jump.dataset.locationValid = String(Boolean(target));
      jump.disabled = !target || !allReady();
      const image = localResourceUrl(frame.image_url, location.origin);
      if (image) { const thumbnail = node('img'); thumbnail.src = image; thumbnail.alt = frame.id; thumbnail.loading = 'lazy'; jump.append(thumbnail); }
      jump.append(node('span', label));
      if (!target) jump.title = '证据的运行身份、版本或时间不匹配，不能定位当前画面。';
      jump.addEventListener('click', () => {
        const exact = evaluationFindingLocation(evaluationState, currentCase, finding.id, frame.id);
        if (!exact || !allReady()) return;
        pause(); setDrawing(false); seek(exact.time);
        viewedRegion = {variant: exact.variant_id, region: exact.region, source: 'automatic'};
        renderRegion();
        if (player) $(`${player.key}-stage`).scrollIntoView({behavior: 'smooth', block: 'center'});
      });
      evidence.append(jump);
    }
    if (evidence.children.length) article.append(evidence);
    else article.append(node('p', '此项没有可定位的截图，保留检查说明供核对。', 'muted small'));
    if (finding.suggestions?.length) {
      const suggestions = node('div', undefined, 'finding-suggestions');
      suggestions.append(node('p', '修改建议（待核看）', 'muted small'));
      const list = node('ul');
      for (const suggestion of finding.suggestions) {
        const layout = {flow: '流程布局', focus: '关系强调布局'}[suggestion.parameters?.layout_template];
        list.append(node('li', `${layout ? `尝试${layout}：` : ''}${suggestion.reason ?? '结合证据核对候选修改。'}`));
      }
      suggestions.append(list); article.append(suggestions);
    }
    const previous = decisions.find(row => row.finding_id === finding.id);
    const state = node('p', previous ? `开发者处理：${DECISIONS[previous.decision]}` : '开发者尚未处理', 'finding-decision-state');
    article.append(state);
    if (previous?.comment) article.append(node('p', previous.comment, 'finding-decision-comment'));
    const commentLabel = node('label', '处理备注（可选）', 'finding-comment-label');
    const comment = node('textarea'); comment.rows = 2; comment.maxLength = 1000; comment.disabled = evaluationBusy;
    const key = `${evaluation.report_identity}:${finding.id}`;
    comment.value = decisionDrafts.get(key) ?? '';
    comment.addEventListener('input', () => decisionDrafts.set(key, comment.value));
    commentLabel.append(comment); article.append(commentLabel);
    const actions = node('div', undefined, 'finding-actions');
    for (const [decision, title] of Object.entries(DECISIONS)) {
      const button = node('button', title, 'button secondary'); button.type = 'button';
      button.disabled = evaluationBusy;
      button.classList.toggle('selected', previous?.decision === decision);
      button.setAttribute('aria-pressed', String(previous?.decision === decision));
      button.addEventListener('click', () => saveFindingDecision(finding.id, decision, comment.value));
      actions.append(button);
    }
    article.append(actions); $('evaluation-findings').append(article);
  }
}

async function loadEvaluation(row, epoch) {
  if (!row.evaluation) return;
  const serial = ++evaluationLoadSerial, operation = evaluationOperation;
  const isLatest = () => epoch === caseEpoch && serial === evaluationLoadSerial && operation === evaluationOperation;
  evaluationMessage('');
  try {
    const result = await request(`/api/evaluations/${encodeURIComponent(row.evaluation.id)}`);
    if (!isLatest()) return;
    const evaluation = result.evaluation ?? result;
    if (!isCurrentEvaluation(evaluation, currentCase)) throw new Error('评测报告已变化，请刷新样例后再核看。');
    evaluationState = evaluation; renderEvaluation();
  } catch (error) {
    if (isLatest()) {
      evaluationState = null; renderEvaluation();
      $('evaluation-technical').textContent = '记录未能载入';
      $('evaluation-model').textContent = '记录未能载入';
      evaluationMessage(`读取自动评测失败：${error.message}`, true);
    }
  }
}

async function saveFindingDecision(findingId, decision, comment) {
  if (evaluationBusy || !isCurrentEvaluation(evaluationState, currentCase)) return;
  const evaluation = evaluationState, epoch = caseEpoch, operation = ++evaluationOperation;
  const reportIdentity = evaluation.report_identity;
  const key = `${reportIdentity}:${findingId}`;
  decisionDrafts.set(key, comment);
  evaluationBusy = true; renderEvaluation(); evaluationMessage('正在保存处理意见…');
  try {
    const result = await request(`/api/evaluations/${encodeURIComponent(evaluation.id)}/decisions`, {
      method: 'POST', body: JSON.stringify({report_identity: reportIdentity, finding_id: findingId, decision, comment: comment.trim()}),
    });
    invalidateIterations();
    if (epoch !== caseEpoch || operation !== evaluationOperation) return;
    const saved = result.decision;
    if (!saved || saved.finding_id !== findingId || saved.decision !== decision || typeof saved.id !== 'string'
        || (saved.report_identity !== undefined && saved.report_identity !== reportIdentity)) throw new Error('保存回执与本次评测不匹配，请刷新核对。');
    evaluationState = {...evaluationState, decisions: [...(evaluationState.decisions ?? []).filter(row => row.finding_id !== findingId), saved]};
    decisionDrafts.delete(key);
    evaluationMessage('处理意见已保存。已确认需修改的问题可以导出为独立任务。');
  } catch (error) {
    if (epoch === caseEpoch && operation === evaluationOperation) evaluationMessage(`处理意见保存未完成：${error.message}。未自动重试，请刷新核对。`, true);
  } finally {
    if (epoch === caseEpoch && operation === evaluationOperation) { evaluationBusy = false; renderEvaluation(); }
  }
}

function clearLoadTimers() { for (const timer of loadTimers) clearTimeout(timer); loadTimers.clear(); }

function configurePlayer(key, variant) {
  // A new browsing context prevents delayed messages from the previous case
  // being accepted for the current one while its resources are warming.
  const oldFrame = $(`${key}-frame`), frame = oldFrame.cloneNode(false);
  frame.removeAttribute('src'); oldFrame.replaceWith(frame);
  const player = {key, variant, ready: false, error: null, previewStarted: false, frame};
  player.frame.title = `${key === 'baseline' ? 'A' : 'B'} · ${variant.label ?? variant.id} 动画预览`;
  $(`${key}-title`).textContent = variant.label ?? variant.id;
  $(`${key}-duration`).textContent = `${Number(variant.duration_seconds).toFixed(3)} 秒`;
  $(`${key}-identity`).textContent = shortIdentity(variant.identity);
  $(`${key}-identity`).title = typeof variant.identity === 'string' ? variant.identity : JSON.stringify(variant.identity ?? '');
  $(`${key}-loading`).hidden = false;
  $(`${key}-loading`).classList.remove('failed');
  $(`${key}-loading`).textContent = '正在加载动画资源，首次打开可能需要一些时间…';
  $(`${key}-state`).textContent = '等待预览';
  $(`${key}-state`).classList.remove('ready');
  const url = new URL(variant.preview_url ?? '', location.origin);
  if (variant.available === false || !variant.preview_url || url.origin !== location.origin || !['http:', 'https:'].includes(url.protocol)) {
    failPlayer(player, variant.reason ?? '这个版本尚无可用预览。');
    player.frame.removeAttribute('src');
    return player;
  }
  player.previewUrl = url.href;
  return player;
}

async function preparePlayers(selected, epoch, controller) {
  const available = selected.filter(player => !player.error);
  try {
    await warmPreviewAssets(available.map(player => player.variant), {
      origin: location.origin, signal: controller.signal,
      onProgress: ({completed, total}) => {
        if (caseEpoch !== epoch || controller.signal.aborted) return;
        for (const player of available) $(`${player.key}-loading`).textContent =
          `正在加载动画资源（${completed}/${total}），首次打开可能需要一些时间…`;
      }
    });
    if (caseEpoch !== epoch || controller.signal.aborted) return;
    for (const player of available) {
      player.previewStarted = true;
      $(`${player.key}-loading`).textContent = '正在准备预览…';
      player.frame.src = player.previewUrl;
      const timer = setTimeout(() => {
        loadTimers.delete(timer);
        if (caseEpoch === epoch && !player.ready && !player.error)
          failPlayer(player, '预览尚未就绪。请刷新样例重试。');
      }, 20000);
      loadTimers.add(timer);
    }
  } catch (error) {
    if (caseEpoch !== epoch || controller.signal.aborted) return;
    for (const player of available) failPlayer(player, error.message ?? '动画资源加载失败。');
  }
}

function failPlayer(player, message) {
  player.error = String(message);
  player.ready = false;
  $(`${player.key}-loading`).hidden = false;
  $(`${player.key}-loading`).classList.add('failed');
  $(`${player.key}-loading`).textContent = player.error;
  $(`${player.key}-state`).textContent = '预览不可用';
  $(`${player.key}-state`).classList.remove('ready');
  pause();
  updateTransport();
}

function renderChecks() {
  const checks = $('case-checks'); checks.replaceChildren();
  if (Array.isArray(currentCase.checks) && currentCase.checks.length) {
    checks.append(node('h4', '这个样例关注什么'));
    const ul = node('ul');
    for (const check of currentCase.checks) ul.append(node('li', typeof check === 'string' ? check : check.label ?? check.description ?? JSON.stringify(check)));
    checks.append(ul);
  } else if (currentCase.checks) checks.append(node('pre', JSON.stringify(currentCase.checks, null, 2)));
  const summaries = $('variant-summaries'); summaries.replaceChildren();
  for (const player of players) {
    if (!player.variant.summary) continue;
    summaries.append(node('h4', `${player.key === 'baseline' ? 'A' : 'B'} · ${player.variant.label}`));
    summaries.append(node('pre', typeof player.variant.summary === 'string' ? player.variant.summary : JSON.stringify(player.variant.summary, null, 2)));
  }
  $('case-identity').textContent = caseIdentity();
}

function renderCheckpoints() {
  const target = $('checkpoints'); target.replaceChildren();
  const values = [];
  for (const player of players) {
    for (const point of player.variant.checkpoints ?? player.variant.observation_times ?? []) {
      const timeValue = typeof point === 'number' ? point : Number(point.time ?? point.seconds ?? point.start);
      if (!Number.isFinite(timeValue)) continue;
      const label = typeof point === 'number' ? `${timeValue.toFixed(2)} 秒` : point.label ?? point.title ?? `${timeValue.toFixed(2)} 秒`;
      values.push({time: Math.max(0, Math.min(duration, timeValue)), label});
    }
  }
  const unique = new Set();
  for (const point of [{time: 0, label: '首帧'}, ...values, {time: Math.max(0, duration - 1 / (currentCase.fps ?? 24)), label: '末帧'}]) {
    const key = `${point.time.toFixed(3)}:${point.label}`;
    if (unique.has(key)) continue;
    unique.add(key);
    const button = node('button', point.label, 'checkpoint');
    button.type = 'button'; button.title = formatTime(point.time);
    button.addEventListener('click', () => { pause(); seek(point.time); });
    target.append(button);
    if (unique.size >= 32) break;
  }
}

async function selectCase(row) {
  if (currentCase?.id === row.id && caseIdentity(currentCase) === caseIdentity(row)) { $('empty-state').hidden = true; $('case-workspace').hidden = false; if (row.evaluation && !evaluationBusy) await loadEvaluation(row, caseEpoch); return; }
  captureDraft(); pause(); clearLoadTimers();
  previewWarmController?.abort();
  previewWarmController = new AbortController();
  currentCase = row;
  const epoch = ++caseEpoch;
  evaluationState = null; evaluationBusy = false; evaluationOperation++;
  setDrawing(false);
  annotations = []; region = null; regionVariant = null; viewedRegion = null; mutationSerial = 0;
  $('empty-state').hidden = true;
  $('case-workspace').hidden = false;
  $('case-title').textContent = row.title;
  $('case-description').textContent = row.description ?? '';
  $('module-badge').textContent = moduleFor(row.module)?.label ?? row.module;
  renderCaseRequirement(row);
  $('candidate-card').hidden = row.variants.length < 2;
  document.querySelector('.comparison-grid').classList.toggle('single', row.variants.length < 2);
  players = [];
  if (row.variants[0]) players.push(configurePlayer('baseline', row.variants[0]));
  if (row.variants.length > 1) players.push(configurePlayer('candidate', row.variants.at(-1)));
  else $('candidate-frame').removeAttribute('src');
  duration = Math.max(0, ...players.map(player => Number(player.variant.duration_seconds) || 0));
  time = 0;
  $('seek').max = String(duration);
  for (const id of ['annotation-start', 'annotation-end']) $(id).max = String(duration);
  $('annotation-variant').replaceChildren(...players.map(player => {
    const option = node('option', `${player.key === 'baseline' ? 'A' : 'B'} · ${player.variant.label}`);
    option.value = player.variant.id; return option;
  }));
  $('annotation-variant').value = players.at(-1)?.variant.id ?? '';
  for (const button of document.querySelectorAll('[data-preference]')) { button.disabled = players.length < 2; button.classList.remove('selected'); }
  $('preference-note').textContent = players.length < 2 ? '当前样例只有一个版本，可记录问题与核看结果。' : '';
  const different = players.length === 2 && Math.abs(players[0].variant.duration_seconds - players[1].variant.duration_seconds) > .001;
  $('timing-note').textContent = different ? '两版包含不同的转场时长；公共时间轴按较长版本显示，较短版本到末尾后保持末帧。' : '两侧使用同一个预览时钟。可逐帧定位，再记录具体问题。';
  restoreDraft(); renderCaseList(); renderChecks(); renderCheckpoints(); renderHistory(); updateTransport(); renderEvaluation();
  void preparePlayers(players, epoch, previewWarmController);
  void loadEvaluation(row, epoch);
  const url = new URL(location.href); url.hash = encodeURIComponent(row.id); history.replaceState(null, '', url);
  try {
    const serial = mutationSerial;
    const value = await request(`/api/annotations?case_id=${encodeURIComponent(row.id)}`);
    if (epoch !== caseEpoch) return;
    const received = Array.isArray(value.annotations) ? value.annotations : [];
    annotations = serial === mutationSerial ? received : [...new Map([...received, ...annotations].map(value => [value.id, value])).values()];
    renderHistory();
  } catch (error) { if (epoch === caseEpoch) formMessage(`读取标注失败：${error.message}。当前草稿仍可保留。`, true); }
}

function updateTransport() {
  const ready = allReady();
  for (const id of ['play', 'reset', 'previous-frame', 'next-frame', 'seek']) $(id).disabled = !ready;
  $('play').textContent = playing ? '暂停' : '播放';
  $('seek').value = String(time);
  $('timecode').textContent = `${formatTime(time)} / ${formatTime(duration)}`;
  $('draw-region').disabled = !ready;
  $('capture-time').disabled = !ready;
  for (const button of document.querySelectorAll('.finding-jump')) button.disabled = !ready || button.dataset.locationValid !== 'true';
}

function seek(value) {
  if (!currentCase) return;
  time = Math.max(0, Math.min(duration, Number(value) || 0));
  const requestId = ++requestSerial;
  for (const player of playablePlayers()) {
    if (!player.ready) continue;
    const maximum = Math.max(0, Number(player.variant.duration_seconds) - 1 / (currentCase.fps ?? 24));
    player.frame.contentWindow?.postMessage({type: 'animation-lab:seek', time: Math.min(time, maximum), requestId}, location.origin);
  }
  updateTransport();
}

function pause() {
  playing = false;
  if (animationFrame !== null) cancelAnimationFrame(animationFrame);
  animationFrame = null;
  updateTransport();
}

function tick(now) {
  if (!playing) return;
  const target = Math.min(duration, playTime + (now - playStart) / 1000 * Number($('speed').value));
  if (now - lastDispatch >= 1000 / (currentCase.fps ?? 24) || target >= duration) { seek(target); lastDispatch = now; }
  if (target >= duration) { pause(); return; }
  animationFrame = requestAnimationFrame(tick);
}

function play() {
  if (!allReady()) return;
  setDrawing(false);
  if (time >= duration - .001) seek(0);
  playing = true; playStart = performance.now(); playTime = time; lastDispatch = 0;
  updateTransport();
  animationFrame = requestAnimationFrame(tick);
}

window.addEventListener('message', event => {
  if (event.origin !== location.origin || !event.data || typeof event.data !== 'object') return;
  const player = players.find(row => row.frame.contentWindow === event.source);
  if (!player || !player.previewStarted) return;
  const data = event.data;
  if (data.type === 'animation-lab:ready') {
    if (player.error || player.variant.available === false) return;
    player.ready = true;
    $(`${player.key}-loading`).hidden = true;
    $(`${player.key}-state`).textContent = '预览就绪';
    $(`${player.key}-state`).classList.add('ready');
    seek(time);
  } else if (data.type === 'animation-lab:state' && player.ready) {
    if (Number.isFinite(data.time)) $(`${player.key}-state`).textContent = `${formatTime(data.time)} · 就绪`;
  } else if (data.type === 'animation-lab:error') failPlayer(player, data.message ?? '动画预览失败。');
});

function renderRegion() {
  for (const player of players) {
    const box = $(`${player.key}-overlay`).querySelector('.region-box');
    const displayed = viewedRegion?.source === 'automatic'
      ? viewedRegion.variant === player.variant.id ? viewedRegion.region : null
      : viewedRegion?.variant === player.variant.id ? viewedRegion.region : regionVariant === player.variant.id ? region : null;
    const show = Boolean(displayed);
    box.hidden = !show;
    if (show) { box.style.left = `${displayed.x * 100}%`; box.style.top = `${displayed.y * 100}%`; box.style.width = `${displayed.width * 100}%`; box.style.height = `${displayed.height * 100}%`; }
  }
  $('clear-region').disabled = !region && !viewedRegion;
  $('region-note').textContent = viewedRegion?.source === 'automatic' ? '正在查看自动评测证据；不会写入人工标注草稿' : viewedRegion ? '正在查看历史标注框选；不会写入当前草稿' : region ? `已框选 ${Math.round(region.width * 1280)} × ${Math.round(region.height * 720)}（1280 × 720 基准）` : '可选；不框选则评价整个画面';
}

function setDrawing(value) {
  drawing = Boolean(value);
  if (drawing) { pause(); viewedRegion = null; renderRegion(); }
  const target = $('annotation-variant').value;
  for (const player of players) $(`${player.key}-overlay`).classList.toggle('drawing', drawing && player.variant.id === target);
  $('draw-region').setAttribute('aria-pressed', String(drawing));
  $('draw-region').textContent = drawing ? '在所选版本上拖动框选' : '框选画面区域';
}

for (const key of ['baseline', 'candidate']) {
  const overlay = $(`${key}-overlay`);
  function point(event) { const rect = overlay.getBoundingClientRect(); return {x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))}; }
  overlay.addEventListener('pointerdown', event => {
    const player = players.find(row => row.key === key);
    if (!drawing || !player || player.variant.id !== $('annotation-variant').value || event.button !== 0) return;
    overlay.setPointerCapture(event.pointerId);
    pointerOrigin = {point: point(event), pointerId: event.pointerId, key};
    regionVariant = player.variant.id;
    region = {...pointerOrigin.point, width: 0, height: 0};
    renderRegion();
  });
  overlay.addEventListener('pointermove', event => {
    if (!pointerOrigin || pointerOrigin.key !== key || pointerOrigin.pointerId !== event.pointerId) return;
    const current = point(event), origin = pointerOrigin.point;
    region = {x: Math.min(current.x, origin.x), y: Math.min(current.y, origin.y), width: Math.abs(current.x - origin.x), height: Math.abs(current.y - origin.y)};
    renderRegion();
  });
  function finish(event) {
    if (!pointerOrigin || pointerOrigin.key !== key || pointerOrigin.pointerId !== event.pointerId) return;
    if (!region || region.width < .005 || region.height < .005) { region = null; regionVariant = null; }
    pointerOrigin = null;
    if (overlay.hasPointerCapture(event.pointerId)) overlay.releasePointerCapture(event.pointerId);
    setDrawing(false); renderRegion(); setDirty();
  }
  overlay.addEventListener('pointerup', finish);
  overlay.addEventListener('pointercancel', finish);
}

function currentIdentityAnnotations() { return annotations.filter(row => !isRetracted(row) && isCurrentAnnotation(row, caseIdentity())); }

function renderHistory() {
  const rows = [...annotations].sort((a, b) => String(b.created_at ?? '').localeCompare(String(a.created_at ?? '')));
  $('annotation-count').textContent = String(rows.length);
  const currentRows = rows.filter(row => !isRetracted(row) && isCurrentAnnotation(row, caseIdentity()));
  const latestReview = currentRows.find(row => row.kind === 'review');
  const reviewed = Boolean(latestReview);
  if (reviewed) knownReviews.add(`${currentCase.id}:${caseIdentity()}`);
  else knownReviews.delete(`${currentCase.id}:${caseIdentity()}`);
  $('review-badge').textContent = latestReview?.verdict === 'acceptable' ? '效果可接受' : latestReview?.verdict === 'needs_work' ? '还需改进' : reviewed ? '已核看' : currentRows.length ? '已有标注' : '待标注';
  $('review-badge').classList.toggle('reviewed', latestReview?.verdict === 'acceptable');
  for (const button of document.querySelectorAll('[data-verdict]')) { button.disabled = saving || latestReview?.verdict === button.dataset.verdict; button.classList.toggle('selected', latestReview?.verdict === button.dataset.verdict); button.setAttribute('aria-pressed', String(latestReview?.verdict === button.dataset.verdict)); }
  const preference = currentRows.find(row => row.kind === 'preference');
  for (const button of document.querySelectorAll('[data-preference]')) { button.classList.toggle('selected', button.dataset.preference === preference?.preference); button.setAttribute('aria-pressed', String(button.dataset.preference === preference?.preference)); }
  const target = $('annotation-history'); target.replaceChildren();
  if (!rows.length) target.append(node('p', '还没有标注。可以先播放，在具体位置记录观察。', 'muted small'));
  for (const row of rows) {
    const article = node('article', undefined, 'annotation-item');
    const header = node('header');
    const variant = players.find(player => player.variant.id === row.variant_id);
    const letter = row.variant_id === 'comparison' ? '对比' : variant?.key === 'baseline' ? 'A' : variant?.key === 'candidate' ? 'B' : '历史版本';
    header.append(node('strong', row.kind === 'preference' ? '版本偏好' : row.kind === 'review' ? '核看记录' : `${letter} · 问题标注`));
    if (row.kind === 'issue') {
      header.append(node('span', SEVERITIES[row.severity] ?? row.severity, `severity-label ${row.severity}`));
      const jump = node('button', row.end_seconds > row.start_seconds ? `${formatTime(row.start_seconds)}—${formatTime(row.end_seconds)}` : formatTime(row.start_seconds), 'jump-button');
      jump.type = 'button';
      const location = issueLocation(row, caseIdentity(), players.map(player => player.variant));
      jump.disabled = !location;
      if (!location) jump.title = '这条记录属于历史版本或缺少匹配的版本身份，仅保留记录与导出。';
      else jump.addEventListener('click', () => {
        pause(); seek(location.time);
        viewedRegion = location.region ? {region: location.region, variant: location.variant_id} : null;
        renderRegion(); setDrawing(false);
        if (variant) $(`${variant.key}-stage`).scrollIntoView({behavior: 'smooth', block: 'center'});
      });
      header.append(jump);
    }
    const created = node('time', row.created_at ? new Date(row.created_at).toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'}) : '');
    if (row.created_at) created.dateTime = row.created_at;
    header.append(created); article.append(header);
    if (row.kind === 'preference') article.append(node('p', PREFERENCES[row.preference] ?? row.preference ?? row.comment));
    else if (row.kind === 'review') article.append(node('p', row.verdict === 'acceptable' ? '效果可接受' : row.verdict === 'needs_work' ? '还需改进' : row.comment || '已完成本版本的人工核看。'));
    else article.append(node('p', row.comment || ''));
    const tags = node('div', undefined, 'tag-list');
    for (const tag of row.tags ?? []) tags.append(node('span', TAGS[tag] ?? tag, 'tag-label'));
    if (row.region) tags.append(node('span', '含画面框选', 'tag-label'));
    if (!isCurrentAnnotation(row, caseIdentity())) tags.append(node('span', row.case_identity ? '来自历史样例版本 · 仅保留记录' : '缺少版本身份 · 仅保留记录', 'tag-label'));
    if (isRetracted(row)) tags.append(node('span', '已撤回 · 仅保留历史', 'tag-label'));
    if (tags.children.length) article.append(tags);
    if (!isRetracted(row) && row.id) {
      const retract = node('button', '撤回这条标注', 'text-button retract-button');
      retract.type = 'button'; retract.disabled = saving;
      retract.addEventListener('click', () => retractAnnotation(row));
      article.append(retract);
    }
    target.append(article);
  }
  renderCaseList();
}

function setSaving(value) {
  saving = value;
  $('save-annotation').disabled = value;
  $('save-annotation').textContent = value ? '正在保存…' : '保存标注';
  const latestReview = [...currentIdentityAnnotations()].sort((a, b) => String(b.created_at ?? '').localeCompare(String(a.created_at ?? ''))).find(row => row.kind === 'review');
  for (const button of document.querySelectorAll('[data-verdict]')) button.disabled = value || latestReview?.verdict === button.dataset.verdict;
  for (const button of document.querySelectorAll('.retract-button')) button.disabled = value;
  for (const button of document.querySelectorAll('[data-preference]')) button.disabled = value || players.length < 2;
}

async function saveAnnotation(payload) {
  if (!currentCase || saving) return false;
  const row = currentCase, epoch = caseEpoch;
  setSaving(true);
  try {
    const result = await request('/api/annotations', {method: 'POST', body: JSON.stringify({case_id: row.id, case_identity: caseIdentity(row), ...payload})});
    invalidateIterations();
    if (epoch !== caseEpoch) return false;
    if (!result.annotation || result.annotation.case_id !== row.id) throw new Error('保存结果缺少本样例的标注回执，请刷新后核对。');
    mutationSerial++;
    annotations = [...annotations.filter(value => value.id !== result.annotation.id), result.annotation];
    renderHistory();
    return true;
  } catch (error) {
    if (epoch === caseEpoch) formMessage(`保存未完成：${error.message}。未自动重试，草稿保留；请先核对历史记录。`, true);
    return false;
  } finally { setSaving(false); }
}

async function retractAnnotation(row) {
  if (saving || !currentCase || isRetracted(row)) return;
  const epoch = caseEpoch;
  setSaving(true);
  try {
    const result = await request(`/api/annotations/${encodeURIComponent(row.id)}/retract`, {method: 'POST', body: JSON.stringify({reason: '开发者撤回'})});
    invalidateIterations();
    if (epoch !== caseEpoch) return;
    if (!result.annotation || result.annotation.id !== row.id) throw new Error('撤回回执不完整，请刷新后核对。');
    mutationSerial++;
    annotations = annotations.map(value => value.id === row.id ? result.annotation : value);
    renderHistory();
    formMessage('这条标注已撤回，原始记录仍保留在历史和导出中。');
  } catch (error) {
    if (epoch === caseEpoch) formMessage(`撤回未完成：${error.message}。请刷新历史核对，未自动重试。`, true);
  } finally { setSaving(false); }
}

$('annotation-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!currentCase || saving) return;
  pause(); setDrawing(false);
  const variantId = $('annotation-variant').value;
  const start = Number($('annotation-start').value), end = Number($('annotation-end').value);
  const maximum = Number(players.find(row => row.variant.id === variantId)?.variant.duration_seconds) || duration;
  const comment = $('annotation-comment').value.trim();
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end < start || end > maximum + .001) { formMessage(`请填写有效区间：开始不晚于结束，范围为 0—${maximum.toFixed(3)} 秒。`, true); return; }
  if (!comment) { formMessage('请写下具体观察或改进建议。', true); return; }
  if (region && regionVariant !== variantId) { formMessage('画面框选属于另一个版本，请清除框选后重选。', true); return; }
  captureDraft();
  const success = await saveAnnotation({variant_id: variantId, kind: 'issue', start_seconds: start, end_seconds: end, region: region ? {...region} : null, tags: Array.from(document.querySelectorAll('input[name="tag"]:checked'), input => input.value), severity: $('annotation-severity').value, comment, preference: null});
  if (success) {
    $('annotation-comment').value = '';
    for (const input of document.querySelectorAll('input[name="tag"]')) input.checked = false;
    region = null; regionVariant = null; renderRegion();
    setDirty(false); captureDraft(); formMessage('标注已保存，并绑定当前样例和版本。');
  }
});

for (const button of document.querySelectorAll('[data-preference]')) button.addEventListener('click', async () => {
  if (players.length < 2) return;
  pause();
  const success = await saveAnnotation({variant_id: 'comparison', kind: 'preference', start_seconds: 0, end_seconds: duration, region: null, tags: [], severity: 'note', comment: '', preference: button.dataset.preference});
  if (success) $('preference-note').textContent = `已记录：${PREFERENCES[button.dataset.preference]}。可以继续补充具体问题。`;
});

for (const button of document.querySelectorAll('[data-verdict]')) button.addEventListener('click', async () => {
  const success = await saveAnnotation({variant_id: 'comparison', kind: 'review', start_seconds: 0, end_seconds: duration, region: null, tags: [], severity: 'note', comment: '', preference: null, verdict: button.dataset.verdict});
  if (success) formMessage('已记录人工效果评价；这项评价独立于生产视频的旁白时序核准。');
});
$('play').addEventListener('click', () => playing ? pause() : play());
$('reset').addEventListener('click', () => { pause(); seek(0); });
$('previous-frame').addEventListener('click', () => { pause(); seek(time - 1 / (currentCase?.fps ?? 24)); });
$('next-frame').addEventListener('click', () => { pause(); seek(time + 1 / (currentCase?.fps ?? 24)); });
$('seek').addEventListener('input', event => { pause(); seek(event.target.value); });
$('speed').addEventListener('change', () => { if (playing) { pause(); play(); } });
$('capture-time').addEventListener('click', () => {
  pause();
  const selected = players.find(row => row.variant.id === $('annotation-variant').value);
  const value = Math.min(time, Number(selected?.variant.duration_seconds) || duration).toFixed(3);
  $('annotation-start').value = value; $('annotation-end').value = value; setDirty();
});
$('draw-region').addEventListener('click', () => setDrawing(!drawing));
$('clear-region').addEventListener('click', () => { region = null; regionVariant = null; viewedRegion = null; setDrawing(false); renderRegion(); setDirty(); });
$('annotation-variant').addEventListener('change', () => {
  setDrawing(false); region = null; regionVariant = null; viewedRegion = null; renderRegion(); setDirty();
  const maximum = players.find(row => row.variant.id === $('annotation-variant').value)?.variant.duration_seconds ?? duration;
  $('annotation-start').max = String(maximum); $('annotation-end').max = String(maximum);
});
$('annotation-form').addEventListener('input', () => setDirty());
function applyCaseFilters() {
  captureDraft(); pause(); renderCaseList();
  const matches = filteredCases();
  if (!matches.length) {
    // Hiding a still-selected case must preserve its in-flight resource warmup.
    // Selecting this same frozen case again reuses its players and controller.
    $('case-workspace').hidden = true; $('empty-state').hidden = false;
    $('empty-state').querySelector('h2').textContent = '当前筛选组合没有样例';
    $('empty-state').querySelector('p').textContent = '请调整模块、领域、引擎、检索词或评价状态。';
  } else {
    $('empty-state').querySelector('h2').textContent = '选择一个效果样例';
    $('empty-state').querySelector('p').textContent = '比较不同领域的需求样例，逐项改善表现力。';
    const retained = matches.find(row => row.id === currentCase?.id);
    if (retained && !$('case-workspace').hidden) return;
    void selectCase(retained ?? matches[0]);
  }
}
for (const id of ['module-filter', 'domain-filter', 'engine-filter', 'review-filter']) $(id).addEventListener('change', applyCaseFilters);
$('case-search').addEventListener('input', applyCaseFilters);
document.addEventListener('visibilitychange', () => { if (document.hidden) pause(); });
window.addEventListener('pagehide', () => { pause(); captureDraft(); clearLoadTimers(); });

async function loadCatalog() {
  if (saving) return;
  const serial = ++loadSerial;
  captureDraft(); pause();
  $('reload').disabled = true;
  try {
    const result = await request('/api/catalog');
    if (serial !== loadSerial) return;
    if (!Array.isArray(result.modules) || !Array.isArray(result.cases)) throw new Error('样例目录格式不完整。');
    const oldId = currentCase?.id;
    catalog = result;
    const evaluations = Array.isArray(catalog.evaluations) ? catalog.evaluations : [];
    $('evaluations-panel').hidden = !evaluations.length;
    $('evaluations-list').replaceChildren();
    for (const item of evaluations) {
      const row = node('div', undefined, 'evaluation-list-item');
      const evaluatedCase = catalog.cases.find(value => value.id === item.case_id);
      if (evaluatedCase) {
        const select = node('button', item.title, 'case-button'); select.type = 'button';
        select.dataset.evaluationCase = evaluatedCase.id;
        select.addEventListener('click', () => {
          if (!['all', evaluatedCase.module].includes($('module-filter').value)) $('module-filter').value = evaluatedCase.module;
          for (const key of ['domain', 'engine']) if (!['all', evaluatedCase[key]].includes($(`${key}-filter`).value)) $(`${key}-filter`).value = 'all';
          $('case-search').value = ''; $('review-filter').value = 'all';
          selectCase(evaluatedCase);
        });
        row.append(select);
      } else row.append(node('p', item.title, 'small'));
      const technical = {passed: '技术采样通过', failed: '技术采样未通过'}[item.technical_status] ?? '技术检查待核对';
      row.append(node('p', `${technical} · 模型 ${item.model_status}`, 'muted small'));
      const report = localResourceUrl(item.report_url, location.origin);
      if (report) { const link = node('a', '查看原始报告', 'small'); link.href = report; link.target = '_blank'; link.rel = 'noopener'; row.append(link); }
      $('evaluations-list').append(row);
    }
    for (const row of catalog.cases) {
      const key = `${row.id}:${caseIdentity(row)}`;
      if (row.reviewed_identity && row.reviewed_identity === caseIdentity(row)) knownReviews.add(key);
      else knownReviews.delete(key);
    }
    const selectedModule = $('module-filter').value;
    $('module-filter').replaceChildren(node('option', '全部模块'));
    $('module-filter').firstElementChild.value = 'all';
    for (const module of catalog.modules) {
      const available = ['ready', 'available', 'implemented', 'active'].includes(module.status ?? 'ready');
      const option = node('option', `${module.label}${available ? '' : ' · 未接入'}`); option.value = module.id;
      $('module-filter').append(option);
    }
    $('module-filter').value = catalog.modules.some(row => row.id === selectedModule) ? selectedModule : 'all';
    for (const key of ['domain', 'engine']) {
      const selector = $(`${key}-filter`), previous = selector.value;
      const rows = animationFacetOptions(catalog.cases, key);
      const all = node('option', key === 'domain' ? '全部领域' : '全部引擎'); all.value = 'all';
      selector.replaceChildren(all, ...rows.map(row => { const option = node('option', `${row.label}（${row.count}）`); option.value = row.id; return option; }));
      selector.value = rows.some(row => row.id === previous) ? previous : 'all';
    }
    syncIterationModules();
    let hashId = ''; try { hashId = decodeURIComponent(location.hash.slice(1)); } catch { /* An invalid hash is not a case selection. */ }
    const matches = filteredCases();
    const explicitHash = !oldId ? catalog.cases.find(row => row.id === hashId) : null;
    const currentEvaluation = catalog.cases.find(row => row.id === oldId && row.evaluation);
    const selected = explicitHash ?? currentEvaluation ?? matches.find(row => row.id === oldId) ?? matches[0];
    currentCase = null;
    renderRequirementsMatrix(); renderCaseList(); globalMessage('');
    if (selected) await selectCase(selected);
    else { $('case-workspace').hidden = true; $('empty-state').hidden = false;
      $('empty-state').querySelector('h2').textContent = '当前筛选组合没有样例';
      $('empty-state').querySelector('p').textContent = '请调整筛选条件后查看。'; }
  } catch (error) { if (serial === loadSerial) globalMessage(`无法读取动画实验样例：${error.message}。请检查开发者服务后刷新。`, true); }
  finally { if (serial === loadSerial) $('reload').disabled = false; }
}
$('reload').addEventListener('click', loadCatalog);
loadCatalog();
