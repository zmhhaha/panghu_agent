import {createMethodComparison} from './method-comparison.mjs';

const API = 'api/method-lab/';
const ACTIVE = new Set(['queued', 'running', 'cancelling', 'submission_unknown', 'planning', 'creating', 'reviewing', 'rendering']);
const STATES = {queued: '等待制作', running: '正在探索', planning: '正在整理目标', creating: '正在创作',
  reviewing: '正在评审', rendering: '正在生成样片', cancelling: '正在取消', submission_unknown: '提交待核对',
  completed: '本次实验结束 · 待评价', stopped: '本次实验结束 · 待评价', cancelled: '已取消', failed: '本次执行失败',
  candidate: '待评价', review: '待评价', accepted: '已认可', published: '方法已发布'};
const STOP_REASONS = {goal_met: '已达到本次目标', objective_met: '已达到本次目标', budget_exhausted: '本次预算已用完',
  no_progress: '多种尝试后暂无明显改进', stalled: '多种尝试后暂无明显改进', cancelled: '开发者已取消',
  failed: '本次执行未完成', deadline: '已到本次时间上限', deadline_exceeded: '已到本次时间上限'};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const text = value => typeof value === 'string' ? value : '';
const rows = value => Array.isArray(value) ? value : [];
const identity = value => text(value?.version_id) || text(value?.id);
const ENGINE_LABELS = {'html-svg': 'HTML/SVG', canvas: 'Canvas 2D', three: 'Three.js', unknown: '待确认'};
const ENGINE_ALIASES = {'html-svg': 'html svg html/svg html-svg 矢量', canvas: 'canvas canvas2d canvas 2d 2d 画布',
  three: 'three three.js threejs three js webgl 3d 三维', unknown: '待确认 待验证 尚无样片'};
function actualEngine(value) {
  return value?.engine_status === 'rendered' && Object.hasOwn(ENGINE_LABELS, value?.engine) && value.engine !== 'unknown'
    ? value.engine : 'unknown';
}
function engineLabel(value, noSample = false) {
  const engine = actualEngine(value);
  return engine !== 'unknown' ? ENGINE_LABELS[engine] : value?.engine_status === 'unverified' ? '待验证'
    : noSample ? '尚无样片' : ENGINE_LABELS.unknown;
}
function engineAvailability(value) {
  if (!Array.isArray(value?.allowed_engines)) return '当前可生成技术待确认。';
  const allowed = value.allowed_engines.filter(engine => engine !== 'unknown' && Object.hasOwn(ENGINE_LABELS, engine));
  const closed = ['canvas', 'three'].filter(engine => !allowed.includes(engine)).map(engine => ENGINE_LABELS[engine]);
  return (allowed.length ? '当前可生成：' + allowed.map(engine => ENGINE_LABELS[engine]).join('、') : '当前暂无可生成的渲染技术')
    + (closed.length ? '；' + closed.join('、') + ' 暂未开放。' : '。');
}

export function methodStateLabel(value) { return STATES[value] ?? '状态待核对'; }
export function methodStopLabel(value) { return STOP_REASONS[typeof value === 'object' ? value?.code : value] ?? ''; }
export function methodAssetUrl(value, root) {
  if (typeof value !== 'string' || !value || /[\u0000-\u0020\\]/.test(value)) return null;
  try {
    const base = new URL(root), prefix = new URL(API, base).pathname;
    let url = new URL(value, base);
    if (url.origin === base.origin && !url.username && !url.password && url.pathname.startsWith('/api/method-lab/')) {
      url = new URL(url.pathname.slice(1) + url.search + url.hash, base);
    }
    if (!['http:', 'https:'].includes(url.protocol) || url.origin !== base.origin || url.username || url.password
      || !url.pathname.startsWith(prefix) || url.pathname === prefix || url.hash || url.search
      || /%(?:2f|5c|00|0a|0d)/i.test(url.pathname)) return null;
    return url.href;
  } catch { return null; }
}
export function commissionCandidates(detail) {
  const candidates = rows(detail?.candidates);
  return candidates.length ? candidates : detail?.best_candidate ? [detail.best_candidate] : [];
}
export function selectedCandidate(detail) {
  const candidates = commissionCandidates(detail);
  return candidates.find(row => row.id === detail?.best_candidate_id) ?? detail?.best_candidate ?? candidates.at(-1) ?? null;
}
export function continuationFeedback(detail, candidateId, draft = '') {
  const comments = rows(detail?.feedback).filter(row => !row.withdrawn && row.kind !== 'preference'
    && row.candidate_id === candidateId && text(row.comment).trim());
  const saved = comments.map(row => (Number.isFinite(row.start_seconds) ? '[' + row.start_seconds + '–' + row.end_seconds + ' 秒] ' : '') + row.comment.trim());
  const current = text(draft).trim();
  if (current && !comments.some(row => row.comment.trim() === current)) saved.push(current);
  return saved.join('\n\n');
}
export function continuationDuration(detail, candidateId) {
  const candidate = commissionCandidates(detail).find(row => row.id === candidateId) ?? selectedCandidate(detail);
  return [candidate?.media?.duration_seconds, candidate?.duration_seconds, detail?.duration_seconds,
    detail?.constraints?.duration_seconds, detail?.frozen?.duration_seconds]
    .find(value => typeof value === 'number' && Number.isFinite(value) && value >= 2 && value <= 60) ?? 24;
}
export function publicationSelection(detail) {
  const methodIds = Array.isArray(detail?.publication_method_version_ids) ? detail.publication_method_version_ids
    : rows(detail?.method_version_ids).length ? detail.method_version_ids
      : rows(selectedCandidate(detail)?.method_version_ids).length ? selectedCandidate(detail).method_version_ids
        : rows(detail?.methods).map(identity).filter(Boolean);
  const explicit = rows(detail?.demonstration_ids), best = selectedCandidate(detail);
  const validations = rows(detail?.validations).filter(row => methodIds.includes(row.method_version_id)
    && row.outcome !== 'failed' && ['reference', 'transfer'].includes(row.role));
  const demoIds = explicit.length ? explicit : Array.isArray(detail?.validations)
    ? validations.map(row => row.demonstration_version_id) : [
    ...commissionCandidates(detail).filter(row => row.id === best?.id).map(row => row.demonstration_id),
    ...rows(detail?.transfers).map(row => row.demonstration_id ?? row.demonstration_version_id),
  ].filter(value => typeof value === 'string' && value);
  return {method_version_ids: [...new Set(methodIds.filter(value => typeof value === 'string' && value))],
    demonstration_ids: [...new Set(demoIds.filter(value => typeof value === 'string' && value))]};
}
export function methodsPublished(detail) {
  if (detail?.state === 'published' || detail?.publication === 'published') return true;
  const ids = publicationSelection(detail).method_version_ids;
  return ids.length > 0 && ids.every(id => rows(detail?.methods).some(row => identity(row) === id && row.status === 'published'));
}
function displayState(detail) {
  return !ACTIVE.has(detail?.state) && methodsPublished(detail) ? 'published' : detail?.state;
}
export function canPublish(detail) {
  const selected = publicationSelection(detail);
  return !ACTIVE.has(detail?.state) && !methodsPublished(detail) && (detail?.publishable === true || detail?.publication_ready === true)
    && rows(detail?.publication_blockers).length === 0
    && selected.method_version_ids.length > 0 && selected.demonstration_ids.length > 1;
}
export function needsAudioVerification(detail) { return detail?.requires_audio_verification !== false; }
export function publicationApprovalBinding(detail) {
  return {commission_id: detail?.id, ...publicationSelection(detail),
    requires_audio_verification: needsAudioVerification(detail)};
}
function number(value, minimum, maximum, label, integer = false) {
  const result = value === '' || value == null ? NaN : Number(value);
  if (!Number.isFinite(result) || result < minimum || result > maximum || (integer && !Number.isInteger(result))) throw Error(label);
  return result;
}
export function buildCommissionRequest(fields, requestId) {
  if (!UUID.test(requestId ?? '')) throw Error('无法生成有效请求编号');
  const title = text(fields.title).trim(), goal = text(fields.goal).trim(), feedback = text(fields.feedback).trim();
  if (!title || title.length > 200 || !goal || goal.length > 8000 || feedback.length > 8000) throw Error('请填写效果名称和表达目标，并缩短过长的内容');
  let reference = null;
  const referenceUrl = text(fields.reference_url).trim();
  if (referenceUrl) {
    let url; try { url = new URL(referenceUrl); } catch { throw Error('参考链接格式不正确'); }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw Error('请使用有效的 HTTP 或 HTTPS 参考链接');
    const hasStart = fields.reference_start !== '' && fields.reference_start != null;
    const hasEnd = fields.reference_end !== '' && fields.reference_end != null;
    if (hasStart !== hasEnd) throw Error('指定片段时，请同时填写开始和结束时间');
    const start = hasStart ? number(fields.reference_start, 0, 86400, '片段开始时间不正确') : null;
    const end = hasEnd ? number(fields.reference_end, 0, 86400, '片段结束时间不正确') : null;
    if (hasStart && end <= start) throw Error('片段结束时间需要大于开始时间');
    reference = {url: url.href, start_seconds: start, end_seconds: end};
  } else if ((fields.reference_start !== '' && fields.reference_start != null) || (fields.reference_end !== '' && fields.reference_end != null)) {
    throw Error('填写片段时间前，请先提供参考链接');
  }
  const body = {request_id: requestId, title, goal, reference,
    duration_seconds: number(fields.duration_seconds ?? 24, 2, 60, '样片时长应为 2 至 60 秒'),
    budget: {max_rounds: number(fields.max_rounds ?? 3, 1, 8, '本次探索轮数应为 1 至 8', true),
      max_seconds: number(fields.max_minutes ?? 15, 1, 60, '本次时间上限应为 1 至 60 分钟', true) * 60},
    base_commission_id: text(fields.base_commission_id) || null, feedback};
  const retryOf = text(fields.retry_of_commission_id).trim();
  if (retryOf) {
    body.retry_of_commission_id = retryOf;
    delete body.base_commission_id;
  } else {
    if (body.base_commission_id && !feedback) throw Error('继续优化时，请说明本次修改意见');
    if (fields.base_candidate_id) body.base_candidate_id = text(fields.base_candidate_id);
  }
  const criteria = text(fields.criteria_text).split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  if (criteria.length > 16 || criteria.some(value => value.length > 1200)) throw Error('验收标准最多 16 项，每项不超过 1200 字');
  if (criteria.length) body.criteria = criteria.map((description, index) => {
    const original = rows(fields.criteria_metadata)[index];
    return original ? {...original, description} : {id: 'criterion-' + (index + 1), description, requires_motion: true};
  });
  return body;
}
export function buildTransferRequest(fields, requestId) {
  if (fields.retry_of_commission_id) throw Error('重新运行不能同时进行迁移验证');
  const body = buildCommissionRequest({...fields, base_commission_id: null}, requestId);
  const dimension = text(fields.transfer_dimension), beforeText = text(fields.transfer_before).trim(), afterText = text(fields.transfer_after).trim();
  if (!['relations', 'data_structure', 'staging', 'explanation_problem', 'object_count', 'timing', 'task'].includes(dimension)
    || !beforeText || !afterText || beforeText.length > 2000 || afterText.length > 2000 || beforeText === afterText) {
    throw Error('请说明迁移任务中真正改变的条件，以及变化前后有什么不同');
  }
  const before = dimension === 'object_count' ? number(beforeText, 0, 100000, '对象数量需要是有效整数', true) : beforeText;
  const after = dimension === 'object_count' ? number(afterText, 0, 100000, '对象数量需要是有效整数', true) : afterText;
  if (before === after) throw Error('迁移任务需要有实质变化');
  delete body.base_commission_id;
  delete body.criteria;
  body.structural_changes = {[dimension]: {before, after}};
  return body;
}

export function layoutPreviewDetail() {
  return {id: 'layout-preview', ui_preview: true, title: '样片详情 · 界面示意',
    goal: '此页展示功能布局，不是已生成的动画或已验证的方法。', state: 'review',
    best_candidate_id: 'layout-best', baseline_candidate: {id: 'layout-original', title: '优化前样片的位置'},
    candidates: [{id: 'layout-original', title: '优化前样片的位置', technical_status: 'unverified'},
      {id: 'layout-best', title: '当前最佳样片的位置', technical_status: 'unverified'}],
    criteria: [{id: 'layout-standard', description: '这里展示每个目标的具体评价标准，由 Agent 提议，也可由你调整。'}],
    methods: [], transfers: [], feedback: [], reviews: [],
    remaining_issues: ['这里展示最佳结果仍未解决的问题，以及为何结束本次实验。'],
    publication_ready: false, publication_blockers: ['界面示意没有真实样片、迁移证据或可发布方法。'],
    summary: '实际制作完成后，可在这里比较完整样片、保存意见并继续优化。'};
}

export class MethodGalleryController {
  constructor({root, request, storage, uuid = () => crypto.randomUUID(), onChange = () => {},
    schedule = (fn, ms) => setTimeout(fn, ms), unschedule = timer => clearTimeout(timer)}) {
    Object.assign(this, {root: new URL(root), request, storage, uuid, onChange, schedule, unschedule});
    this.state = {catalog: null, detail: null, loading: false, busy: false, error: '', notice: '', pending: null};
    this.epoch = 0; this.timer = null; this.closed = false;
  }
  get snapshot() { return {...this.state}; }
  emit() { if (!this.closed) this.onChange(this.snapshot); }
  key() { return 'animation-method-commission:' + this.root.href; }
  readPending() {
    if (!this.storage) throw Error('浏览器无法保存请求，暂不能启动实验');
    const raw = this.storage.getItem(this.key());
    if (!raw) return null;
    let pending; try { pending = JSON.parse(raw); } catch { throw Error('保存的请求无法读取，请先核对原任务'); }
    if (!UUID.test(pending?.request_id ?? '')) throw Error('保存的请求编号不完整，请先核对原任务');
    return pending;
  }
  savePending(body) {
    if (!this.storage?.setItem) throw Error('浏览器无法保存请求，暂不能启动实验');
    this.storage.setItem(this.key(), JSON.stringify(body));
  }
  clearPending() { this.storage.removeItem(this.key()); }
  clearTimer() { if (this.timer !== null) this.unschedule(this.timer); this.timer = null; }
  queueRead() {
    this.clearTimer();
    if (this.closed || this.state.error || this.state.pending) return;
    const active = ACTIVE.has(this.state.detail?.state) || rows(this.state.catalog?.commissions).some(row => ACTIVE.has(row.state));
    if (active) this.timer = this.schedule(() => { this.timer = null; void this.refresh(); }, 3000);
  }
  async refresh() {
    if (this.closed || this.state.loading) return;
    this.clearTimer(); this.state.loading = true; this.emit();
    try {
      const catalog = await this.request(API + 'catalog');
      if (!catalog || !Array.isArray(catalog.effects) || !Array.isArray(catalog.commissions)
        || typeof catalog.availability?.enabled !== 'boolean') throw Error('效果库状态不完整，请稍后刷新');
      if (this.closed) return;
      this.state.catalog = catalog; this.state.error = '';
      let pending = this.readPending(), restored = false;
      if (pending) {
        const found = catalog.commissions.find(row => row.request_id === pending.request_id);
        if (found) {
          this.clearPending(); pending = null; restored = true;
          this.state.notice = '已找回原实验，后续只读取进度。';
          await this.select(found.id);
        } else this.state.notice = '本次请求结果尚待核对。请刷新查找原任务；不会自动重新提交。';
      }
      this.state.pending = pending;
      if (!pending && !restored && this.state.detail) await this.select(this.state.detail.id);
    } catch (error) { if (!this.closed) this.state.error = error.message; }
    finally { if (!this.closed) { this.state.loading = false; this.emit(); this.queueRead(); } }
  }
  async select(id) {
    const epoch = ++this.epoch;
    try {
      const detail = await this.request(API + 'commissions/' + encodeURIComponent(id));
      if (!detail || detail.id !== id || !Array.isArray(detail.candidates)) throw Error('实验详情与所选记录不符');
      if (this.closed || epoch !== this.epoch) return;
      this.state.detail = detail; this.state.error = ''; this.emit(); this.queueRead();
    } catch (error) { if (!this.closed && epoch === this.epoch) { this.state.error = error.message; this.emit(); } }
  }
  deselect() { this.epoch++; this.state.detail = null; this.emit(); }
  async start(fields) {
    if (this.closed || this.state.busy || this.state.loading || !this.state.catalog?.availability.enabled) return false;
    let body;
    try {
      if (this.readPending()) throw Error('上次提交尚未核对，先刷新查找原任务');
      body = fields.transfer_commission_id ? buildTransferRequest(fields, this.uuid()) : buildCommissionRequest(fields, this.uuid());
      this.savePending(body); this.state.pending = body;
    } catch (error) { this.state.error = error.message; this.emit(); return false; }
    ++this.epoch;
    this.state.busy = true; this.state.error = ''; this.state.notice = '正在提交本次实验…'; this.emit();
    try {
      const path = fields.transfer_commission_id ? API + 'commissions/' + encodeURIComponent(fields.transfer_commission_id) + '/transfer' : API + 'commissions';
      const detail = await this.request(path, {method: 'POST', body: JSON.stringify(body)});
      if (this.closed) return false;
      if (!detail?.id || detail.request_id !== body.request_id || !Array.isArray(detail.candidates)) throw Error('提交回执与本次请求不符');
      this.clearPending(); this.state.pending = null; this.state.detail = detail;
      const catalog = this.state.catalog;
      catalog.commissions = [detail, ...catalog.commissions.filter(row => row.id !== detail.id)];
      this.state.notice = '实验已提交。可以离开页面，稍后回来查看最佳结果。';
      // Group membership belongs to the server. A failed read must never turn a
      // confirmed submission into an uncertain one or cause a second POST.
      try {
        const refreshed = await this.request(API + 'catalog');
        if (!refreshed || !Array.isArray(refreshed.effects) || !Array.isArray(refreshed.commissions)
          || typeof refreshed.availability?.enabled !== 'boolean') throw Error('效果库状态不完整');
        if (!this.closed) this.state.catalog = refreshed;
      } catch {
        this.state.notice += ' 效果库列表暂未更新，请刷新查看；本次实验已确认提交。';
      }
      return true;
    } catch (error) {
      this.state.error = '尚未确认提交结果：' + error.message + '。请刷新核对，不会自动重发。'; return false;
    } finally { this.state.busy = false; this.emit(); this.queueRead(); }
  }
  async mutate(action, body) {
    const detail = this.state.detail;
    if (!detail || detail.ui_preview || this.state.busy) return false;
    this.state.busy = true; this.state.error = ''; this.emit();
    try {
      const value = await this.request(API + 'commissions/' + encodeURIComponent(detail.id) + '/' + action,
        {method: 'POST', body: JSON.stringify(body)});
      if (this.state.detail?.id === detail.id && value?.id === detail.id && Array.isArray(value.candidates)) this.state.detail = value;
      else if (this.state.detail?.id === detail.id) await this.select(detail.id);
      if (action === 'feedback') this.state.notice = '意见已保存。选择“继续优化这版”时才会开始下一次实验。';
      else if (action === 'withdraw_feedback') this.state.notice = '意见已撤回，原记录保留供核对。不会开始新的制作。';
      else if (action === 'publish') this.state.notice = '验收与发布请求已提交，状态以服务器结果为准。';
      else this.state.notice = '取消请求已提交，已开始的工作会保留记录。';
      return true;
    } catch (error) { this.state.error = '请求结果待核对：' + error.message + '。请刷新查看，没有自动重试。'; return false; }
    finally { this.state.busy = false; this.emit(); this.queueRead(); }
  }
  cancel() { return ACTIVE.has(this.state.detail?.state) ? this.mutate('cancel', {}) : Promise.resolve(false); }
  saveFeedback({candidate_id, comment, start_seconds, end_seconds}) {
    if (!commissionCandidates(this.state.detail).some(row => row.id === candidate_id) || !text(comment).trim()) {
      this.state.error = '请选择实际样片，并填写修改意见'; this.emit(); return Promise.resolve(false);
    }
    const body = {request_id: this.uuid(), candidate_id, comment: text(comment).trim()};
    if (body.comment.length > 8000) { this.state.error = '请将意见缩短至 8000 字以内'; this.emit(); return Promise.resolve(false); }
    if (start_seconds !== '' && start_seconds != null || end_seconds !== '' && end_seconds != null) {
      try {
        body.start_seconds = number(start_seconds, 0, 3600, '意见开始时间不正确');
        body.end_seconds = number(end_seconds, 0, 3600, '意见结束时间不正确');
        if (body.end_seconds < body.start_seconds) throw Error('意见结束时间不能早于开始时间');
      } catch (error) { this.state.error = error.message; this.emit(); return Promise.resolve(false); }
    }
    return this.mutate('feedback', body);
  }
  savePreference({left_candidate_id, right_candidate_id, preference}) {
    const ids = new Set(commissionCandidates(this.state.detail).map(row => row.id));
    if (!ids.has(left_candidate_id) || !ids.has(right_candidate_id) || left_candidate_id === right_candidate_id
      || !['left', 'right', 'equal', 'neither'].includes(preference)) {
      this.state.error = '请选择两个不同版本再记录比较偏好'; this.emit(); return Promise.resolve(false);
    }
    return this.mutate('feedback', {request_id: this.uuid(), kind: 'preference', left_candidate_id, right_candidate_id, preference});
  }
  withdrawFeedback(feedback_id) {
    const row = rows(this.state.detail?.feedback).find(value => value.id === feedback_id);
    if (!row || row.withdrawn) return Promise.resolve(false);
    return this.mutate('withdraw_feedback', {request_id: this.uuid(), feedback_id});
  }
  publish(acknowledged, expected = null) {
    if (!acknowledged || !canPublish(this.state.detail)) return Promise.resolve(false);
    if (expected && JSON.stringify(expected) !== JSON.stringify(publicationApprovalBinding(this.state.detail))) {
      this.state.error = '样片或关联方法已经更新，请核对当前版本后重新认可'; this.emit(); return Promise.resolve(false);
    }
    return this.mutate('publish', {request_id: this.uuid(), ...publicationSelection(this.state.detail),
      human_verification: {motion_watched: true, audio_listened: needsAudioVerification(this.state.detail)}});
  }
  close() { this.closed = true; this.epoch++; this.clearTimer(); }
}

function element(doc, tag, className, value) {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = String(value);
  return node;
}
function button(doc, label, handler, className = 'button secondary') {
  const node = element(doc, 'button', className, label); node.type = 'button'; node.addEventListener('click', handler); return node;
}
function renderEngineBadge(doc, value, noSample = false) {
  const badge = element(doc, 'span', 'engine-badge engine-' + actualEngine(value), engineLabel(value, noSample));
  badge.dataset.renderEngine = actualEngine(value);
  badge.setAttribute('title', '渲染技术：' + engineLabel(value, noSample));
  return badge;
}
function list(doc, values, className = '') {
  const node = element(doc, 'ul', className);
  for (const row of rows(values)) node.append(element(doc, 'li', '', typeof row === 'string' ? row : text(row.description) || text(row.message) || text(row.issue)));
  return node;
}
export function renderMethodMedia(doc, candidate, root, emptyText = '尚未生成可播放样片') {
  const stage = element(doc, 'div', 'media-stage'), url = methodAssetUrl(candidate?.media_url ?? candidate?.media?.url, root);
  if (url) {
    const video = element(doc, 'video'); video.controls = true; video.preload = 'metadata'; video.playsInline = true;
    video.src = url; video.setAttribute('aria-label', text(candidate?.title) || '完整效果样片');
    video.addEventListener('error', () => {
      stage.replaceChildren(element(doc, 'p', 'muted', '样片暂时无法播放，请刷新核对媒体状态。'));
    });
    stage.append(video);
  } else {
    stage.classList.add('media-empty'); stage.append(element(doc, 'span', '', '◈'), element(doc, 'p', '', emptyText));
  }
  return stage;
}
export function renderMethodCard(doc, effect, root, onOpen) {
  const card = element(doc, 'article', 'effect-card');
  card.dataset.effectId = text(effect.effect_id) || text(effect.id);
  const candidate = effect.best_candidate ?? {media_url: effect.media_url, title: effect.title};
  card.append(renderMethodMedia(doc, candidate, root, ACTIVE.has(effect.state) ? '正在制作，完成后展示实际样片' : '打开实验，查看样片与尚存问题'));
  const body = element(doc, 'div', 'card-body'), heading = element(doc, 'h3');
  heading.append(button(doc, text(effect.title) || '未命名效果', () => onOpen(effect.open_commission_id ?? effect.commission_id ?? effect.id), 'effect-title'));
  const engine = element(doc, 'div', 'card-engine');
  engine.append(element(doc, 'span', 'small muted', '渲染技术'), renderEngineBadge(doc, effect,
    !text(candidate?.media_url) && !text(candidate?.media?.url)));
  body.append(heading, engine, element(doc, 'p', 'card-goal', text(effect.goal)));
  const meta = element(doc, 'div', 'card-meta');
  const state = displayState(effect);
  meta.append(element(doc, 'span', 'badge ' + (Object.hasOwn(STATES, state) ? state : ''), methodStateLabel(state)));
  if (rows(effect.methods).length) meta.append(element(doc, 'span', '', effect.methods.length + ' 项关联方法'));
  if (rows(effect.commissions).length > 1) meta.append(element(doc, 'span', '', effect.commissions.length + ' 次实验'));
  if (effect.relationship_status === 'needs_review') meta.append(element(doc, 'span', '', '关系待核对'));
  body.append(meta); card.append(body); return card;
}
function panel(doc, title) {
  const section = element(doc, 'section', 'panel'); section.append(element(doc, 'h3', '', title)); return section;
}
function details(doc, title, value) {
  const section = element(doc, 'details', 'technical-details'); section.append(element(doc, 'summary', '', title));
  if (value) section.append(element(doc, 'pre', '', typeof value === 'string' ? value : JSON.stringify(value, null, 2)));
  return section;
}
export function renderMethodDetail(doc, detail, root, {onBack, onContinue, onCancel, onFeedback, onPublish,
  onTransfer = () => {}, onRetry = () => {}, onSelectCommission = () => {}, feedbackDraft = null, onDraftChange = () => {},
  onPreference = () => {}, onWithdraw = () => {}, comparisonSelection = null,
  onComparisonReady = () => {}, onComparisonChange = () => {}, disclosureState = null}) {
  const disclosure = (key, title, value) => {
    const section = details(doc, title, value); section.dataset.methodDisclosure = key;
    if (Object.hasOwn(disclosureState ?? {}, key)) section.open = disclosureState[key] === true;
    return section;
  };
  const fragment = doc.createDocumentFragment(), head = element(doc, 'header', 'detail-heading'), heading = element(doc, 'div');
  heading.append(button(doc, '← 返回效果库', onBack), element(doc, 'h1', '', text(detail.title) || '效果实验'),
    element(doc, 'p', 'muted', text(detail.goal)), element(doc, 'span', 'badge', methodStateLabel(displayState(detail))));
  heading.children[1].id = 'detail-title';
  head.append(heading); fragment.append(head);
  const history = rows(detail.effect?.commissions);
  if (history.length) {
    const section = disclosure('effect-history', '版本与实验记录（' + history.length + '）');
    section.classList.add('effect-history');
    if (detail.effect.title && detail.effect.title !== detail.title) section.append(element(doc, 'p', 'small muted', '所属效果：' + detail.effect.title));
    if (detail.effect.relationship_status === 'needs_review') section.append(element(doc, 'p', 'small muted', '关系待核对'));
    const items = element(doc, 'ol', 'effect-history-list');
    const labels = {original: '原作', continuation: '继续优化', transfer: '迁移验证', retry: '重新运行', historical_attempt: '早期执行'};
    for (const run of history) {
      const item = element(doc, 'li', 'effect-history-item'), label = labels[run.kind] ?? '实验记录';
      const open = button(doc, text(run.title) || label, () => {
        if (run.id !== detail.id) onSelectCommission(run.id);
      }, 'effect-history-title');
      if (run.id === detail.id) open.setAttribute('aria-current', 'true');
      const meta = element(doc, 'div', 'effect-history-meta');
      meta.append(element(doc, 'span', '', label), renderEngineBadge(doc, run), element(doc, 'span', 'badge', methodStateLabel(run.state)));
      if (run.id === detail.id) meta.append(element(doc, 'span', '', '正在查看'));
      if (run.id === detail.effect.open_commission_id) meta.append(element(doc, 'span', '', '代表样片'));
      item.append(open, meta); items.append(item);
    }
    section.append(items); fragment.append(section);
  }
  const candidates = commissionCandidates(detail), best = selectedCandidate(detail);
  const baseline = detail.baseline_candidate ?? detail.baseline ?? null, original = baseline?.candidate ?? baseline ?? candidates[0];
  const latestAttempt = candidates.findLast(candidate => candidate.id !== best?.id);
  const preferences = element(doc, 'div', 'comparison-preferences detail-actions');
  let comparison;
  const updatePreferenceButtons = selection => {
    for (const child of preferences.children) child.disabled = detail.ui_preview === true || !selection.left || !selection.right;
    if (comparison) {
      const selected = [selection.left, selection.right].filter(Boolean);
      for (const [index, card] of [...comparison.element.querySelectorAll('article')].entries()) {
        const header = card.querySelectorAll('header')[0], candidate = candidates.find(row => row.id === selected[index]);
        if (!header) continue;
        let badge = [...header.children].find(row => row.dataset.renderEngine !== undefined);
        if (!badge) { badge = renderEngineBadge(doc, candidate); header.append(badge); }
        else {
          badge.textContent = engineLabel(candidate); badge.className = 'engine-badge engine-' + actualEngine(candidate);
          badge.dataset.renderEngine = actualEngine(candidate); badge.setAttribute('title', '渲染技术：' + engineLabel(candidate));
        }
      }
    }
    onComparisonChange(selection);
  };
  comparison = createMethodComparison(doc, {candidates, bestId: best?.id, baselineId: original?.id, root,
    assetUrl: methodAssetUrl,
    selection: comparisonSelection ?? {left: original?.id ?? best?.id,
      right: original?.id !== best?.id ? best?.id : latestAttempt?.id ?? ''},
    onSelectionChange: updatePreferenceButtons, onTimeChange: (_time, selection) => onComparisonChange(selection)});
  for (const [value, label] of [['left', '左侧更好'], ['right', '右侧更好'], ['equal', '差不多'], ['neither', '都需改进']]) {
    preferences.append(button(doc, label, () => {
      const selection = comparison.getSelection();
      if (!detail.ui_preview) void onPreference({left_candidate_id: selection.left, right_candidate_id: selection.right, preference: value});
    }));
  }
  updatePreferenceButtons(comparison.getSelection()); onComparisonReady(comparison);
  comparison.element.append(preferences, element(doc, 'p', 'small muted', '快捷比较只记录当前两版的偏好，不会自动更换最佳样片或发布方法。'));
  fragment.append(comparison.element);
  const retryable = ['failed', 'cancelled'].includes(detail.state) && detail.can_retry !== false;
  if (detail.stop_reason || rows(detail.remaining_issues).length || detail.error || retryable) {
    const result = panel(doc, ACTIVE.has(detail.state) ? '当前进度与问题' : '本次结果');
    const reason = methodStopLabel(detail.stop_reason);
    if (reason) result.append(element(doc, 'p', '', reason));
    if (detail.stop_explanation || detail.summary) result.append(element(doc, 'p', 'muted', detail.stop_explanation || detail.summary));
    if (detail.error) result.append(element(doc, 'p', 'muted', '本次执行未完成，记录已保留。可在执行详情中核对原因。'));
    if (rows(detail.remaining_issues).length) result.append(list(doc, detail.remaining_issues));
    if (retryable) {
      const retry = button(doc, '重新运行', () => onRetry(detail), 'button primary');
      retry.disabled = detail.ui_preview === true;
      result.append(retry, element(doc, 'p', 'small muted', '先核对原目标和本次预算，再提交重新运行。已有样片和失败记录会保留。'));
    }
    fragment.append(result);
  }
  const criteria = panel(doc, '本次想让观众看懂什么');
  if (rows(detail.criteria).length) criteria.append(list(doc, detail.criteria, 'criteria-list'));
  else criteria.append(element(doc, 'p', 'muted', '验收标准尚在整理。可以通过后续修改意见调整目标。'));
  const adjust = button(doc, '调整标准并继续', () => onContinue(detail, best?.id, continuationFeedback(detail, best?.id)));
  adjust.disabled = !best || ACTIVE.has(detail.state); criteria.append(adjust); fragment.append(criteria);
  if (detail.comparison) fragment.append(disclosure('comparison', '优化对比与判断依据', detail.comparison));
  const transfers = panel(doc, '换一种任务，方法还能成立吗？');
  const startTransfer = button(doc, '验证不同结构', () => onTransfer(detail, best?.id), 'button secondary');
  startTransfer.disabled = !best || ACTIVE.has(detail.state); transfers.append(startTransfer);
  if (!rows(detail.transfers).length) transfers.append(element(doc, 'p', 'muted', '尚未形成迁移验证结果。先打磨代表片段，再在里程碑验证不同结构的任务。'));
  const transferGrid = element(doc, 'div', 'transfer-grid');
  const transferGroups = new Map();
  for (const transfer of rows(detail.transfers)) {
    const demonstration = text(transfer.demonstration_id), mediaUrl = text(transfer.media_url) || text(transfer.media?.url);
    const key = demonstration ? 'demonstration:' + demonstration : mediaUrl ? 'media:' + mediaUrl : transfer;
    if (!transferGroups.has(key)) transferGroups.set(key, []);
    transferGroups.get(key).push(transfer);
  }
  for (const validations of transferGroups.values()) {
    const transfer = validations[0];
    const item = element(doc, 'article', 'transfer-item');
    item.append(element(doc, 'h4', '', text(transfer.title) || '迁移样片'),
      renderEngineBadge(doc, transfer),
      element(doc, 'p', 'muted', text(transfer.goal) || text(transfer.requirement)),
      renderMethodMedia(doc, transfer, root, '尚无可播放的迁移样片'));
    if (transfer.structural_changes) item.append(element(doc, 'p', 'small muted',
      typeof transfer.structural_changes === 'string' ? transfer.structural_changes : JSON.stringify(transfer.structural_changes)));
    for (const validation of validations) {
      const status = validation.status ?? validation.outcome, result = element(doc, 'p', 'small');
      result.dataset.validationId = text(validation.id);
      result.dataset.methodVersionId = text(validation.method_version_id);
      if (validation.method_version_id) {
        const method = rows(detail.methods).find(row => identity(row) === validation.method_version_id);
        result.append(element(doc, 'span', '', (text(method?.title) || validation.method_version_id) + '：'));
      }
      result.append(element(doc, 'span', 'badge', validation.accepted === true || validation.acceptance ? '开发者已认可'
        : status === 'passed' ? '检查通过 · 待认可' : status === 'incomplete' ? '部分证据待人工核对'
          : status === 'failed' ? '尚未通过目标标准' : '迁移结果待核对'));
      item.append(result);
    }
    transferGrid.append(item);
  }
  transfers.append(transferGrid);
  const feedback = panel(doc, '指出问题，继续打磨');
  const form = element(doc, 'form', 'feedback-form'), selectLabel = element(doc, 'label', '', '这条意见针对哪版样片');
  const select = element(doc, 'select'); select.setAttribute('aria-label', '意见对应样片');
  for (const candidate of candidates) {
    const option = element(doc, 'option', '', text(candidate.title) || (candidate.id === detail.best_candidate_id ? '当前最佳样片' : '样片 ' + (candidates.indexOf(candidate) + 1)));
    option.value = candidate.id; select.append(option);
  }
  if (best) select.value = best.id;
  if (feedbackDraft && candidates.some(row => row.id === feedbackDraft.candidate_id)) select.value = feedbackDraft.candidate_id;
  selectLabel.append(select);
  const commentLabel = element(doc, 'label', '', '哪里不清楚，或希望怎样改善'), comment = element(doc, 'textarea');
  comment.rows = 3; comment.maxLength = 8000; comment.placeholder = '例如：对象被筛掉之后数字突然变化，没看清两者的关系。';
  comment.value = text(feedbackDraft?.comment); commentLabel.append(comment);
  const rangeDetails = disclosure('feedback-range', '定位到具体时间（可选）'), rangeFields = element(doc, 'div', 'form-row');
  const startLabel = element(doc, 'label', '', '开始（秒）'), endLabel = element(doc, 'label', '', '结束（秒）');
  const start = element(doc, 'input'), end = element(doc, 'input');
  for (const input of [start, end]) { input.type = 'number'; input.min = '0'; input.step = '0.001'; }
  start.setAttribute('aria-label', '意见开始秒数'); end.setAttribute('aria-label', '意见结束秒数');
  start.value = feedbackDraft?.start_seconds ?? ''; end.value = feedbackDraft?.end_seconds ?? '';
  if (!Object.hasOwn(disclosureState ?? {}, 'feedback-range')) rangeDetails.open = start.value !== '' || end.value !== '';
  startLabel.append(start); endLabel.append(end); rangeFields.append(startLabel, endLabel);
  const draft = () => ({candidate_id: select.value, comment: comment.value, start_seconds: start.value, end_seconds: end.value});
  const rememberDraft = () => onDraftChange(draft());
  const rangeNotice = element(doc, 'p', 'small muted'); rangeNotice.setAttribute('role', 'status');
  const capture = button(doc, '用当前画面时间', () => {
    const time = comparison.getTime(select.value);
    if (time == null) { rangeNotice.textContent = '先在上方选中这版样片，等待媒体加载后再定位。'; return; }
    start.value = end.value = time.toFixed(3); rememberDraft(); rangeNotice.textContent = '已记录当前画面时间，可调整结束时间标注一段。';
  });
  capture.disabled = detail.ui_preview === true || !candidates.length;
  const clearRange = button(doc, '清除时间', () => { start.value = end.value = ''; rememberDraft(); rangeNotice.textContent = ''; });
  rangeDetails.append(rangeFields, capture, clearRange, rangeNotice);
  start.addEventListener('input', rememberDraft); end.addEventListener('input', rememberDraft);
  select.addEventListener('change', () => {
    start.value = end.value = ''; rangeNotice.textContent = '已切换样片，请按这版画面重新定位时间。'; rememberDraft();
  });
  comment.addEventListener('input', rememberDraft);
  const actions = element(doc, 'div', 'detail-actions'), save = element(doc, 'button', 'button secondary', '保存意见'); save.type = 'submit'; save.disabled = detail.ui_preview === true || !candidates.length;
  const continueButton = button(doc, '继续优化这版', () => onContinue(detail, select.value, continuationFeedback(detail, select.value, comment.value)), 'button primary');
  continueButton.disabled = !candidates.length || ACTIVE.has(detail.state);
  actions.append(save, continueButton); form.append(selectLabel, commentLabel, rangeDetails, actions);
  form.addEventListener('submit', event => { event.preventDefault(); if (!detail.ui_preview) void onFeedback(draft()); });
  feedback.append(form, element(doc, 'p', 'small muted', '保存意见不会开始制作。继续优化时，会把这版尚未撤回的文字意见带入表单供你核对，再按新预算开始实验。累计打磨次数不限。'));
  if (rows(detail.feedback).length) {
    const history = disclosure('feedback-history', '已记录意见（' + detail.feedback.filter(row => !row.withdrawn).length + ' 条有效）');
    const items = element(doc, 'ul', 'feedback-history');
    const candidateName = id => { const index = candidates.findIndex(row => row.id === id); return index < 0 ? '原样片' : '版本 ' + (index + 1) + ' · ' + (text(candidates[index].title) || '样片'); };
    for (const row of detail.feedback) {
      const item = element(doc, 'li', row.withdrawn ? 'withdrawn' : '');
      if (row.kind === 'preference') {
        const labels = {left: '左侧更好', right: '右侧更好', equal: '差不多', neither: '都需改进'};
        item.append(element(doc, 'strong', '', '比较偏好：' + (labels[row.preference] ?? '待核对')),
          element(doc, 'small', '', '左侧：' + candidateName(row.left_candidate_id) + '；右侧：' + candidateName(row.right_candidate_id)));
      } else item.append(element(doc, 'small', '', candidateName(row.candidate_id)));
      if (row.comment) item.append(element(doc, 'p', '', row.comment));
      const controls = element(doc, 'div', 'detail-actions');
      if (row.candidate_id && Number.isFinite(row.start_seconds)) {
        controls.append(button(doc, row.start_seconds + '–' + row.end_seconds + ' 秒 · 跳到画面', () => {
          comparison.seek(row.candidate_id, row.start_seconds); comparison.element.scrollIntoView?.({behavior: 'smooth', block: 'start'});
        }));
      }
      if (row.withdrawn) controls.append(element(doc, 'span', 'small muted', '已撤回 · 保留原记录'));
      else if (row.id) { const withdraw = button(doc, '撤回意见', () => { if (!detail.ui_preview) void onWithdraw(row.id); }); withdraw.disabled = detail.ui_preview === true; controls.append(withdraw); }
      item.append(controls); items.append(item);
    }
    history.append(items); feedback.append(history);
  }
  fragment.append(feedback);
  fragment.append(transfers);
  const publish = panel(doc, '认可效果，发布创作方法'); publish.classList.add('publication');
  const selection = publicationSelection(detail), ready = canPublish(detail);
  publish.append(element(doc, 'p', 'muted', '发布会保存这些已认可的方法版本，确认范围绑定本次样片与迁移结果。发布后，正式制作可读取这些方法；采用情况保存在制作记录中。'));
  if (selection.method_version_ids.length) publish.append(list(doc, rows(detail.methods)
    .filter(row => selection.method_version_ids.includes(identity(row))).map(row => text(row.title) || text(row.problem) || '关联方法')));
  if (rows(detail.publication_blockers).length) publish.append(list(doc, detail.publication_blockers));
  else if (!ready) publish.append(element(doc, 'p', 'muted', methodsPublished(detail) ? '本版本的方法已发布。继续优化会产生新的候选版本。' : '尚未具备完整样片、迁移结果和方法版本，暂不能发布。'));
  const acknowledgment = element(doc, 'label', 'checkbox-label'), checkbox = element(doc, 'input');
  checkbox.type = 'checkbox'; checkbox.disabled = !ready;
  acknowledgment.append(checkbox, element(doc, 'span', '', needsAudioVerification(detail)
    ? '我已完整观看样片及迁移样片，并试听其中音效，确认这些效果符合目标。'
    : '我已完整观看样片及迁移样片，确认这些效果符合目标。本次样片无音效，无需试听。'));
  const publishButton = button(doc, '认可并发布方法', () => onPublish(checkbox.checked, publicationApprovalBinding(detail)), 'button primary');
  publishButton.disabled = true; checkbox.addEventListener('change', () => { publishButton.disabled = !ready || !checkbox.checked; });
  publish.append(acknowledgment, publishButton); fragment.append(publish);
  const methods = disclosure('methods', '关联创作方法（' + rows(detail.methods).length + '）');
  for (const method of rows(detail.methods)) {
    const article = element(doc, 'article', 'method-item');
    article.append(element(doc, 'h4', '', text(method.title) || text(method.problem) || '创作方法'),
      element(doc, 'p', 'muted', text(method.problem)), element(doc, 'pre', '', text(method.guidance)));
    if (method.applicability) article.append(element(doc, 'p', 'small muted', '适用条件：' + (typeof method.applicability === 'string' ? method.applicability : JSON.stringify(method.applicability))));
    if (rows(method.limitations).length) article.append(list(doc, method.limitations));
    methods.append(article);
  }
  fragment.append(methods);
  fragment.append(disclosure('execution', '候选、技术检查与执行记录', {candidates, budget: detail.budget, operations: detail.operations ?? detail.execution,
    events: detail.events, stop_reason: detail.stop_reason, error: detail.error}));
  if (ACTIVE.has(detail.state)) fragment.append(button(doc, '取消本次实验', onCancel, 'button secondary danger'));
  return fragment;
}

export function mountMethodGallery({document: doc = document, root = new URL('./', location.href), fetch: fetcher = fetch,
  storage = sessionStorage, uuid, schedule, unschedule} = {}) {
  const $ = id => doc.getElementById(id), home = $('gallery-home'), detailHost = $('gallery-detail'), formPanel = $('gallery-form-panel');
  let continuation = null, controller, renderedDetailId = null, renderedDetailSnapshot = null, renderedCardsSnapshot = null, comparison = null, replacing = false;
  const feedbackDrafts = new Map(), comparisonStates = new Map(), disclosureStates = new Map();
  const releaseComparison = () => {
    replacing = true;
    try {
      if (renderedDetailId) disclosureStates.set(renderedDetailId, Object.fromEntries(
        [...detailHost.querySelectorAll('details')].filter(section => section.dataset.methodDisclosure)
          .map(section => [section.dataset.methodDisclosure, section.open === true])));
      if (comparison && renderedDetailId) comparisonStates.set(renderedDetailId, {...comparison.getSelection(), playing: false});
      comparison?.destroy(); comparison = null;
      for (const video of detailHost.querySelectorAll('video')) video.pause();
    } finally { replacing = false; }
  };
  const request = async (path, options = {}) => {
    const headers = {Accept: 'application/json', ...options.headers};
    if (options.method === 'POST') Object.assign(headers, {'Content-Type': 'application/json', 'X-Animation-Lab': '1',
      'X-Animation-Lab-Token': controller.state.catalog?.csrf_token ?? '', 'X-CSRF-Token': controller.state.catalog?.csrf_token ?? ''});
    const response = await fetcher(new URL(path, root), {...options, headers, credentials: 'same-origin', redirect: 'error', cache: 'no-store'});
    if (path === API + 'catalog' && !options.method && [404, 503].includes(response.status)) {
      return {effects: [], commissions: [], availability: {enabled: false,
        reason: '前端预览：生成服务尚未接通。可以先检查页面与功能，不会启动实验。'}};
    }
    if (!response.ok) throw Error('请求失败（HTTP ' + response.status + '），请刷新核对');
    return response.json();
  };
  const show = mode => {
    if (mode !== 'detail' && comparison) {
      releaseComparison(); renderedDetailId = renderedDetailSnapshot = null;
    }
    if (mode !== 'home') for (const video of home.querySelectorAll('video')) video.pause();
    home.hidden = mode !== 'home'; detailHost.hidden = mode !== 'detail'; formPanel.hidden = mode !== 'form';
  };
  const openForm = (detail = null, candidateId = null, feedback = '', mode = 'continue') => {
    const isTransfer = Boolean(detail) && mode === 'transfer', isRetry = Boolean(detail) && mode === 'retry';
    continuation = detail ? {commission_id: detail.id, candidate_id: candidateId, mode, criteria: detail.criteria,
      ui_preview: detail.ui_preview === true} : null;
    $('experiment-form').reset();
    if (detail) $('experiment-duration').value = String(isRetry && Number.isFinite(detail.duration_seconds)
      && detail.duration_seconds >= 2 && detail.duration_seconds <= 60 ? detail.duration_seconds : continuationDuration(detail, candidateId));
    if (isRetry) {
      $('experiment-reference').value = text(detail.reference?.url);
      $('experiment-reference-start').value = detail.reference?.start_seconds ?? '';
      $('experiment-reference-end').value = detail.reference?.end_seconds ?? '';
      $('experiment-rounds').value = '1';
      $('experiment-rounds').closest('details').open = true;
      const seconds = Number(detail.retry_budget?.max_seconds ?? detail.budget?.seconds);
      $('experiment-seconds').value = String(Number.isFinite(seconds) && seconds > 0 ? Math.min(60, Math.max(1, Math.ceil(seconds / 60))) : 15);
    }
    $('experiment-title').value = isTransfer ? (detail.title + ' · 迁移验证') : detail?.title ?? '';
    $('experiment-goal').value = isTransfer ? '' : detail?.goal ?? '';
    $('experiment-feedback').value = isRetry ? text(detail.feedback_text) : feedback;
    $('experiment-feedback').required = Boolean(detail) && !isTransfer && !isRetry;
    $('experiment-feedback-label').hidden = !detail || isTransfer;
    $('experiment-criteria').value = rows(detail?.criteria).map(row => typeof row === 'string' ? row : row.description).filter(Boolean).join('\n');
    $('experiment-form-title').textContent = isRetry ? '重新运行本次实验' : isTransfer ? '验证方法在不同任务中的表现' : detail ? '继续优化这版' : '新建实验';
    $('experiment-base-note').hidden = !detail;
    $('experiment-base-note').textContent = isRetry ? '按当前表单重新执行，使用新预算，保留已有样片和失败记录。'
      : isTransfer ? '将使用选定样片背后的方法，为新目标创作。请观看迁移结果后再决定发布。'
      : detail ? '将基于选定样片与本次意见开始新实验，已有结果和发布版本保持。' : '';
    $('experiment-transfer-fields').hidden = !isTransfer;
    $('experiment-transfer-before').required = isTransfer; $('experiment-transfer-after').required = isTransfer;
    $('experiment-submit').textContent = isRetry ? '确认重新运行' : isTransfer ? '开始迁移验证' : detail ? '开始本次优化' : '开始本次实验';
    $('experiment-submit').disabled = detail?.ui_preview === true || controller.state.catalog?.availability.enabled !== true;
    $('experiment-unavailable').hidden = !($('experiment-submit').disabled);
    show('form'); $('experiment-title').focus();
  };
  const renderCards = () => {
    const catalog = controller.state.catalog;
    if (!catalog) return;
    const query = $('gallery-search').value.trim().toLocaleLowerCase(), filter = $('gallery-state').value,
      engine = $('gallery-engine').value;
    const all = catalog.effects;
    const filtered = all.filter(row => (!query || (text(row.title) + ' ' + text(row.goal) + ' ' + ENGINE_ALIASES[actualEngine(row)]).toLocaleLowerCase().includes(query))
      && (engine === 'all' || actualEngine(row) === engine)
      && (filter === 'all' || filter === 'active' && ACTIVE.has(row.state) || filter === 'published' && displayState(row) === 'published'
        || filter === 'review' && !ACTIVE.has(row.state) && displayState(row) !== 'published'));
    const snapshot = JSON.stringify({filtered, query, filter, engine});
    if (renderedCardsSnapshot !== snapshot) {
      for (const video of $('gallery-cards').querySelectorAll('video')) video.pause();
      $('gallery-cards').replaceChildren(...filtered.map(row => renderMethodCard(doc, row, root, id => { show('detail'); void controller.select(id); })));
      renderedCardsSnapshot = snapshot;
    }
    const constrained = Boolean(query) || filter !== 'all' || engine !== 'all';
    $('gallery-empty').hidden = all.length > 0 || constrained;
    $('gallery-no-results').hidden = filtered.length > 0 || !constrained;
    const unavailableEngine = ['canvas', 'three'].includes(engine) && !all.some(row => actualEngine(row) === engine);
    $('gallery-no-results-message').textContent = unavailableEngine
      ? '效果库中暂无 ' + ENGINE_LABELS[engine] + ' 实际样片。' + (!rows(catalog.availability.allowed_engines).includes(engine) ? '该技术暂未开放生成。' : '')
      : '没有匹配的效果，请调整搜索、状态或渲染技术。';
    $('gallery-count').textContent = (constrained ? filtered.length + ' / ' : '') + all.length + ' 个效果';
  };
  controller = new MethodGalleryController({root, request, storage, uuid, schedule, unschedule, onChange: state => {
    const message = $('gallery-message'); message.hidden = !state.error && !state.notice;
    message.textContent = state.error || state.notice; message.className = 'message' + (state.error ? ' error' : '');
    const enabled = state.catalog?.availability.enabled === true;
    $('gallery-new').disabled = state.busy || Boolean(state.pending);
    $('gallery-empty-new').disabled = $('gallery-new').disabled;
    $('experiment-submit').disabled = !enabled || continuation?.ui_preview === true || state.busy || Boolean(state.pending);
    $('experiment-unavailable').hidden = enabled && continuation?.ui_preview !== true;
    $('gallery-preview-note').hidden = !state.catalog || enabled;
    $('gallery-refresh').disabled = state.loading || state.busy;
    $('experiment-form-close').disabled = state.busy;
    $('gallery-availability').textContent = state.catalog ? enabled ? '按目标自主探索，完成后查看最佳样片、改进依据与尚存问题。'
      : state.catalog.availability.reason || '制作服务尚未就绪，可查看已有样片。' : '正在读取效果库…';
    $('gallery-engine-availability').textContent = state.catalog ? engineAvailability(state.catalog.availability) : '';
    renderCards();
    if (!formPanel.hidden) return;
    if (state.detail) {
      const isTyping = detailHost.contains(doc.activeElement) && ['TEXTAREA', 'INPUT', 'SELECT'].includes(doc.activeElement?.tagName);
      const isPlaying = [...detailHost.querySelectorAll('video')].some(video => !video.paused && !video.ended);
      const snapshot = JSON.stringify(state.detail), switched = renderedDetailId !== state.detail.id;
      if (!state.busy && (switched || !isTyping && !isPlaying && renderedDetailSnapshot !== snapshot)) {
        const detail = state.detail;
        releaseComparison();
        detailHost.replaceChildren(renderMethodDetail(doc, detail, root, {
          onBack: () => { controller.deselect(); show('home'); }, onContinue: openForm,
          onRetry: current => openForm(current, null, '', 'retry'),
          onSelectCommission: id => { show('detail'); void controller.select(id); },
          onTransfer: (current, candidateId) => openForm(current, candidateId, '', 'transfer'),
          onCancel: () => void controller.cancel(), onFeedback: async value => {
            const before = JSON.stringify(feedbackDrafts.get(detail.id));
            const saved = await controller.saveFeedback(value);
            if (saved && JSON.stringify(feedbackDrafts.get(detail.id)) === before) {
              feedbackDrafts.delete(detail.id);
              if (renderedDetailId === detail.id) { renderedDetailSnapshot = null; controller.emit(); }
            }
            return saved;
          },
          onPreference: value => controller.savePreference(value), onWithdraw: id => controller.withdrawFeedback(id),
          onPublish: (acknowledged, expected) => controller.publish(acknowledged, expected),
          feedbackDraft: feedbackDrafts.get(detail.id),
          onDraftChange: value => feedbackDrafts.set(detail.id, value),
          disclosureState: disclosureStates.get(detail.id),
          comparisonSelection: comparisonStates.get(detail.id), onComparisonReady: value => { comparison = value; },
          onComparisonChange: value => comparisonStates.set(detail.id, value),
        }));
        renderedDetailId = detail.id; renderedDetailSnapshot = snapshot;
        const mountedComparison = comparison;
        for (const video of detailHost.querySelectorAll('video')) {
          video.addEventListener('pause', () => { if (!replacing && comparison === mountedComparison) controller.emit(); });
          video.addEventListener('ended', () => { if (!replacing && comparison === mountedComparison) controller.emit(); });
        }
      }
      if (!formPanel.hidden && !state.busy) return;
      show('detail');
    }
  }});
  $('gallery-new').addEventListener('click', () => openForm());
  $('gallery-empty-new').addEventListener('click', () => openForm());
  $('gallery-preview-detail').addEventListener('click', () => {
    controller.deselect();
    releaseComparison();
    const preview = layoutPreviewDetail();
    detailHost.replaceChildren(renderMethodDetail(doc, preview, root, {
      onBack: () => show('home'), onContinue: openForm,
      onRetry: current => openForm(current, null, '', 'retry'),
      onTransfer: (current, candidateId) => openForm(current, candidateId, '', 'transfer'),
      onFeedback: () => Promise.resolve(false), onPublish: () => Promise.resolve(false), onCancel: () => {},
      disclosureState: disclosureStates.get(preview.id),
      comparisonSelection: comparisonStates.get(preview.id), onComparisonReady: value => { comparison = value; },
      onComparisonChange: value => comparisonStates.set(preview.id, value),
    }));
    renderedDetailId = preview.id; renderedDetailSnapshot = JSON.stringify(preview);
    for (const form of detailHost.querySelectorAll('form')) {
      for (const submit of form.querySelectorAll('[type="submit"]')) submit.disabled = true;
    }
    show('detail');
  });
  $('gallery-refresh').addEventListener('click', () => void controller.refresh());
  $('experiment-form-close').addEventListener('click', () => {
    show(controller.state.detail ? 'detail' : 'home');
    if (controller.state.detail) controller.emit();
  });
  $('gallery-search').addEventListener('input', renderCards);
  $('gallery-state').addEventListener('change', renderCards);
  $('gallery-engine').addEventListener('change', renderCards);
  $('gallery-reset').addEventListener('click', () => {
    $('gallery-search').value = ''; $('gallery-state').value = 'all'; $('gallery-engine').value = 'all'; renderCards();
  });
  detailHost.addEventListener('focusout', () => queueMicrotask(() => controller.emit()));
  $('experiment-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (continuation?.ui_preview || controller.state.catalog?.availability.enabled !== true) return;
    const fields = Object.fromEntries(new FormData($('experiment-form')));
    if (continuation) Object.assign(fields, {criteria_metadata: continuation.criteria,
      ...(continuation.mode === 'retry' ? {retry_of_commission_id: continuation.commission_id}
        : {base_candidate_id: continuation.candidate_id,
          ...(continuation.mode === 'transfer' ? {transfer_commission_id: continuation.commission_id} : {base_commission_id: continuation.commission_id})})});
    if (await controller.start(fields)) { show('detail'); controller.emit(); }
  });
  const closeController = controller.close.bind(controller);
  controller.close = () => { closeController(); releaseComparison(); };
  void controller.refresh(); return controller;
}

if (typeof document !== 'undefined' && document.getElementById('method-gallery-app')) {
  let storage = null; try { storage = window.sessionStorage; } catch { /* Viewing remains available. */ }
  const controller = mountMethodGallery({document, root: new URL('./', location.href), storage});
  window.addEventListener('pagehide', () => controller.close(), {once: true});
}
