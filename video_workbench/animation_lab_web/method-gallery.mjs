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
  if (body.base_commission_id && !feedback) throw Error('继续优化时，请说明本次修改意见');
  if (fields.base_candidate_id) body.base_candidate_id = text(fields.base_candidate_id);
  const criteria = text(fields.criteria_text).split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  if (criteria.length > 16 || criteria.some(value => value.length > 1200)) throw Error('验收标准最多 16 项，每项不超过 1200 字');
  if (criteria.length) body.criteria = criteria.map((description, index) => {
    const original = rows(fields.criteria_metadata)[index];
    return original ? {...original, description} : {id: 'criterion-' + (index + 1), description, requires_motion: true};
  });
  return body;
}
export function buildTransferRequest(fields, requestId) {
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
  body.structural_changes = {[dimension]: {before, after}};
  return body;
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
    if (this.state.busy || this.state.loading || !this.state.catalog?.availability.enabled) return false;
    let body;
    try {
      if (this.readPending()) throw Error('上次提交尚未核对，先刷新查找原任务');
      body = fields.transfer_commission_id ? buildTransferRequest(fields, this.uuid()) : buildCommissionRequest(fields, this.uuid());
      this.savePending(body); this.state.pending = body;
    } catch (error) { this.state.error = error.message; this.emit(); return false; }
    this.state.busy = true; this.state.error = ''; this.state.notice = '正在提交本次实验…'; this.emit();
    try {
      const path = fields.transfer_commission_id ? API + 'commissions/' + encodeURIComponent(fields.transfer_commission_id) + '/transfer' : API + 'commissions';
      const detail = await this.request(path, {method: 'POST', body: JSON.stringify(body)});
      if (!detail?.id || detail.request_id !== body.request_id || !Array.isArray(detail.candidates)) throw Error('提交回执与本次请求不符');
      this.clearPending(); this.state.pending = null; this.state.detail = detail;
      const catalog = this.state.catalog;
      catalog.commissions = [detail, ...catalog.commissions.filter(row => row.id !== detail.id)];
      this.state.notice = '实验已提交。可以离开页面，稍后回来查看最佳结果。'; return true;
    } catch (error) {
      this.state.error = '尚未确认提交结果：' + error.message + '。请刷新核对，不会自动重发。'; return false;
    } finally { this.state.busy = false; this.emit(); this.queueRead(); }
  }
  async mutate(action, body) {
    const detail = this.state.detail;
    if (!detail || this.state.busy) return false;
    this.state.busy = true; this.state.error = ''; this.emit();
    try {
      const value = await this.request(API + 'commissions/' + encodeURIComponent(detail.id) + '/' + action,
        {method: 'POST', body: JSON.stringify(body)});
      if (this.state.detail?.id === detail.id && value?.id === detail.id && Array.isArray(value.candidates)) this.state.detail = value;
      else if (this.state.detail?.id === detail.id) await this.select(detail.id);
      if (action === 'feedback') this.state.notice = '意见已保存。选择“继续优化这版”时才会开始下一次实验。';
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
  const candidate = effect.best_candidate ?? {media_url: effect.media_url, title: effect.title};
  card.append(renderMethodMedia(doc, candidate, root, ACTIVE.has(effect.state) ? '正在制作，完成后展示实际样片' : '打开实验，查看样片与尚存问题'));
  const body = element(doc, 'div', 'card-body'), heading = element(doc, 'h3');
  heading.append(button(doc, text(effect.title) || '未命名效果', () => onOpen(effect.commission_id ?? effect.id), 'effect-title'));
  body.append(heading, element(doc, 'p', 'card-goal', text(effect.goal)));
  const meta = element(doc, 'div', 'card-meta');
  const state = displayState(effect);
  meta.append(element(doc, 'span', 'badge ' + (Object.hasOwn(STATES, state) ? state : ''), methodStateLabel(state)));
  if (rows(effect.methods).length) meta.append(element(doc, 'span', '', effect.methods.length + ' 项关联方法'));
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
  onTransfer = () => {}, feedbackDraft = null, onDraftChange = () => {}}) {
  const fragment = doc.createDocumentFragment(), head = element(doc, 'header', 'detail-heading'), heading = element(doc, 'div');
  heading.append(button(doc, '← 返回效果库', onBack), element(doc, 'h1', '', text(detail.title) || '效果实验'),
    element(doc, 'p', 'muted', text(detail.goal)), element(doc, 'span', 'badge', methodStateLabel(displayState(detail))));
  heading.children[1].id = 'detail-title';
  head.append(heading); fragment.append(head);
  const candidates = commissionCandidates(detail), best = selectedCandidate(detail);
  const baseline = detail.baseline_candidate ?? detail.baseline ?? null, original = baseline?.candidate ?? baseline ?? candidates[0];
  const latestAttempt = candidates.findLast(candidate => candidate.id !== best?.id);
  const chosen = original && best && original.id !== best.id ? [original, best]
    : best && latestAttempt ? [best, latestAttempt] : best ? [best] : [];
  const comparison = element(doc, 'div', 'comparison-grid' + (chosen.length < 2 ? ' single' : ''));
  for (const candidate of chosen) {
    const card = element(doc, 'article', 'comparison-card'), header = element(doc, 'header');
    const label = candidate.id === detail.best_candidate_id ? '当前最佳样片' + (baseline && candidate.id === original?.id ? ' · 本次优化基线' : '')
      : baseline && candidate.id === original?.id ? '本次优化基线'
        : candidate.id === original?.id ? '初始样片' : '本次候选 · 暂未超过最佳样片';
    header.append(element(doc, 'strong', '', label));
    const source = methodAssetUrl(candidate.source_url, root);
    if (source) { const link = element(doc, 'a', 'source-link', '下载源码'); link.href = source; link.download = ''; header.append(link); }
    card.append(header, renderMethodMedia(doc, candidate, root),
      element(doc, 'footer', '', candidate.technical_status === 'passed' ? '技术检查通过 · 表达效果请结合目标评价' : '技术状态：' + (text(candidate.technical_status) || '待核对')));
    comparison.append(card);
  }
  if (!chosen.length) comparison.append(renderMethodMedia(doc, null, root, ACTIVE.has(detail.state) ? '正在准备实际样片，稍后可在这里查看' : '本次尚无可播放结果，请查看执行说明'));
  fragment.append(comparison);
  if (detail.stop_reason || rows(detail.remaining_issues).length || detail.error) {
    const result = panel(doc, ACTIVE.has(detail.state) ? '当前进度与问题' : '本次结果');
    const reason = methodStopLabel(detail.stop_reason);
    if (reason) result.append(element(doc, 'p', '', reason));
    if (detail.stop_explanation || detail.summary) result.append(element(doc, 'p', 'muted', detail.stop_explanation || detail.summary));
    if (detail.error) result.append(element(doc, 'p', 'muted', '本次执行未完成，记录已保留。可在执行详情中核对原因。'));
    if (rows(detail.remaining_issues).length) result.append(list(doc, detail.remaining_issues));
    fragment.append(result);
  }
  const criteria = panel(doc, '本次想让观众看懂什么');
  if (rows(detail.criteria).length) criteria.append(list(doc, detail.criteria, 'criteria-list'));
  else criteria.append(element(doc, 'p', 'muted', '验收标准尚在整理。可以通过后续修改意见调整目标。'));
  const adjust = button(doc, '调整标准并继续', () => onContinue(detail, best?.id, ''));
  adjust.disabled = !best || ACTIVE.has(detail.state); criteria.append(adjust); fragment.append(criteria);
  if (detail.comparison) fragment.append(details(doc, '优化对比与判断依据', detail.comparison));
  const transfers = panel(doc, '换一种任务，方法还能成立吗？');
  const startTransfer = button(doc, '验证不同结构', () => onTransfer(detail, best?.id), 'button secondary');
  startTransfer.disabled = !best || ACTIVE.has(detail.state); transfers.append(startTransfer);
  if (!rows(detail.transfers).length) transfers.append(element(doc, 'p', 'muted', '尚未形成迁移验证结果。先打磨代表片段，再在里程碑验证不同结构的任务。'));
  const transferGrid = element(doc, 'div', 'transfer-grid');
  for (const transfer of rows(detail.transfers)) {
    const item = element(doc, 'article', 'transfer-item');
    item.append(element(doc, 'h4', '', text(transfer.title) || '迁移样片'),
      element(doc, 'p', 'muted', text(transfer.goal) || text(transfer.requirement)),
      renderMethodMedia(doc, transfer, root, '尚无可播放的迁移样片'));
    if (transfer.structural_changes) item.append(element(doc, 'p', 'small muted',
      typeof transfer.structural_changes === 'string' ? transfer.structural_changes : JSON.stringify(transfer.structural_changes)));
    const status = transfer.status ?? transfer.outcome;
    item.append(element(doc, 'span', 'badge', transfer.accepted === true || transfer.acceptance ? '开发者已认可'
      : status === 'passed' ? '检查通过 · 待认可' : status === 'incomplete' ? '部分证据待人工核对'
        : status === 'failed' ? '尚未通过目标标准' : '迁移结果待核对'));
    transferGrid.append(item);
  }
  transfers.append(transferGrid); fragment.append(transfers);
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
  const rememberDraft = () => onDraftChange({candidate_id: select.value, comment: comment.value});
  select.addEventListener('change', rememberDraft); comment.addEventListener('input', rememberDraft);
  const actions = element(doc, 'div', 'detail-actions'), save = element(doc, 'button', 'button secondary', '保存意见'); save.type = 'submit'; save.disabled = !candidates.length;
  const continueButton = button(doc, '继续优化这版', () => onContinue(detail, select.value, comment.value), 'button primary');
  continueButton.disabled = !candidates.length || ACTIVE.has(detail.state);
  actions.append(save, continueButton); form.append(selectLabel, commentLabel, actions);
  form.addEventListener('submit', event => { event.preventDefault(); void onFeedback({candidate_id: select.value, comment: comment.value}); });
  feedback.append(form, element(doc, 'p', 'small muted', '保存意见不会开始制作。继续优化会建立新的有界实验，不限制这个效果的累计打磨次数。'));
  if (rows(detail.feedback).length) feedback.append(list(doc, detail.feedback.map(row => row.comment ?? row), 'feedback-history'));
  fragment.append(feedback);
  const publish = panel(doc, '认可效果，发布创作方法'); publish.classList.add('publication');
  const selection = publicationSelection(detail), ready = canPublish(detail);
  publish.append(element(doc, 'p', 'muted', '发布会保存这些已认可的方法版本，确认范围绑定本次样片与迁移结果。正式制作接入仍待完成。'));
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
  const methods = details(doc, '关联创作方法（' + rows(detail.methods).length + '）');
  for (const method of rows(detail.methods)) {
    const article = element(doc, 'article', 'method-item');
    article.append(element(doc, 'h4', '', text(method.title) || text(method.problem) || '创作方法'),
      element(doc, 'p', 'muted', text(method.problem)), element(doc, 'pre', '', text(method.guidance)));
    if (method.applicability) article.append(element(doc, 'p', 'small muted', '适用条件：' + (typeof method.applicability === 'string' ? method.applicability : JSON.stringify(method.applicability))));
    if (rows(method.limitations).length) article.append(list(doc, method.limitations));
    methods.append(article);
  }
  fragment.append(methods);
  fragment.append(details(doc, '候选、技术检查与执行记录', {candidates, budget: detail.budget, operations: detail.operations ?? detail.execution,
    events: detail.events, stop_reason: detail.stop_reason, error: detail.error}));
  if (ACTIVE.has(detail.state)) fragment.append(button(doc, '取消本次实验', onCancel, 'button secondary danger'));
  return fragment;
}

export function mountMethodGallery({document: doc = document, root = new URL('./', location.href), fetch: fetcher = fetch,
  storage = sessionStorage, uuid, schedule, unschedule} = {}) {
  const $ = id => doc.getElementById(id), home = $('gallery-home'), detailHost = $('gallery-detail'), formPanel = $('gallery-form-panel');
  let continuation = null, controller, renderedDetailId = null, renderedDetailSnapshot = null;
  const feedbackDrafts = new Map();
  const request = async (path, options = {}) => {
    const headers = {Accept: 'application/json', ...options.headers};
    if (options.method === 'POST') Object.assign(headers, {'Content-Type': 'application/json', 'X-Animation-Lab': '1',
      'X-Animation-Lab-Token': controller.state.catalog?.csrf_token ?? '', 'X-CSRF-Token': controller.state.catalog?.csrf_token ?? ''});
    const response = await fetcher(new URL(path, root), {...options, headers, credentials: 'same-origin', redirect: 'error', cache: 'no-store'});
    if (!response.ok) throw Error('请求失败（HTTP ' + response.status + '），请刷新核对');
    return response.json();
  };
  const show = mode => { home.hidden = mode !== 'home'; detailHost.hidden = mode !== 'detail'; formPanel.hidden = mode !== 'form'; };
  const openForm = (detail = null, candidateId = null, feedback = '', mode = 'continue') => {
    const isTransfer = Boolean(detail) && mode === 'transfer';
    continuation = detail ? {commission_id: detail.id, candidate_id: candidateId, mode, criteria: detail.criteria} : null;
    $('experiment-form').reset();
    $('experiment-title').value = isTransfer ? (detail.title + ' · 迁移验证') : detail?.title ?? '';
    $('experiment-goal').value = isTransfer ? '' : detail?.goal ?? '';
    $('experiment-feedback').value = feedback; $('experiment-feedback').required = Boolean(detail) && !isTransfer;
    $('experiment-criteria').value = rows(detail?.criteria).map(row => typeof row === 'string' ? row : row.description).filter(Boolean).join('\n');
    $('experiment-form-title').textContent = isTransfer ? '验证方法在不同任务中的表现' : detail ? '继续优化这版' : '新建实验';
    $('experiment-base-note').hidden = !detail;
    $('experiment-base-note').textContent = isTransfer ? '将使用选定样片背后的方法，为新目标创作。请观看迁移结果后再决定发布。'
      : detail ? '将基于选定样片与本次意见开始新实验，已有结果和发布版本保持。' : '';
    $('experiment-transfer-fields').hidden = !isTransfer;
    $('experiment-transfer-before').required = isTransfer; $('experiment-transfer-after').required = isTransfer;
    $('experiment-submit').textContent = isTransfer ? '开始迁移验证' : detail ? '开始本次优化' : '开始本次实验';
    show('form'); $('experiment-title').focus();
  };
  const renderCards = () => {
    const catalog = controller.state.catalog;
    if (!catalog) return;
    const query = $('gallery-search').value.trim().toLocaleLowerCase(), filter = $('gallery-state').value;
    const all = [...catalog.effects], seen = new Set(all.map(row => row.commission_id ?? row.id));
    for (const commission of catalog.commissions) if (!seen.has(commission.id)) { all.push(commission); seen.add(commission.id); }
    const filtered = all.filter(row => (!query || (text(row.title) + ' ' + text(row.goal)).toLocaleLowerCase().includes(query))
      && (filter === 'all' || filter === 'active' && ACTIVE.has(row.state) || filter === 'published' && displayState(row) === 'published'
        || filter === 'review' && !ACTIVE.has(row.state) && displayState(row) !== 'published'));
    $('gallery-cards').replaceChildren(...filtered.map(row => renderMethodCard(doc, row, root, id => { show('detail'); void controller.select(id); })));
    $('gallery-empty').hidden = all.length > 0; $('gallery-count').textContent = all.length + ' 个效果与实验';
    if (all.length && !filtered.length) $('gallery-cards').append(element(doc, 'p', 'muted', '没有匹配的效果，请调整查找条件。'));
  };
  controller = new MethodGalleryController({root, request, storage, uuid, schedule, unschedule, onChange: state => {
    const message = $('gallery-message'); message.hidden = !state.error && !state.notice;
    message.textContent = state.error || state.notice; message.className = 'message' + (state.error ? ' error' : '');
    const enabled = state.catalog?.availability.enabled === true;
    $('gallery-new').disabled = !enabled || state.busy || Boolean(state.pending);
    $('gallery-empty-new').disabled = $('gallery-new').disabled;
    $('experiment-submit').disabled = !enabled || state.busy || Boolean(state.pending);
    $('gallery-refresh').disabled = state.loading || state.busy;
    $('gallery-availability').textContent = state.catalog ? enabled ? '按目标自主探索，完成后查看最佳样片、改进依据与尚存问题。'
      : state.catalog.availability.reason || '制作服务尚未就绪，可查看已有样片。' : '正在读取效果库…';
    renderCards();
    if (state.detail) {
      const isTyping = detailHost.contains(doc.activeElement) && ['TEXTAREA', 'INPUT', 'SELECT'].includes(doc.activeElement?.tagName);
      const isPlaying = [...detailHost.querySelectorAll('video')].some(video => !video.paused && !video.ended);
      const snapshot = JSON.stringify(state.detail), switched = renderedDetailId !== state.detail.id;
      if (!state.busy && (switched || !isTyping && !isPlaying && renderedDetailSnapshot !== snapshot)) {
        const detail = state.detail;
        detailHost.replaceChildren(renderMethodDetail(doc, detail, root, {
          onBack: () => { controller.deselect(); show('home'); }, onContinue: openForm,
          onTransfer: (current, candidateId) => openForm(current, candidateId, '', 'transfer'),
          onCancel: () => void controller.cancel(), onFeedback: value => controller.saveFeedback(value),
          onPublish: (acknowledged, expected) => controller.publish(acknowledged, expected),
          feedbackDraft: feedbackDrafts.get(detail.id),
          onDraftChange: value => feedbackDrafts.set(detail.id, value),
        }));
        renderedDetailId = detail.id; renderedDetailSnapshot = snapshot;
        for (const video of detailHost.querySelectorAll('video')) {
          video.addEventListener('pause', () => controller.emit());
          video.addEventListener('ended', () => controller.emit());
        }
      }
      if (!formPanel.hidden && !state.busy) return;
      show('detail');
    }
  }});
  $('gallery-new').addEventListener('click', () => openForm());
  $('gallery-empty-new').addEventListener('click', () => openForm());
  $('gallery-refresh').addEventListener('click', () => void controller.refresh());
  $('experiment-form-close').addEventListener('click', () => show(controller.state.detail ? 'detail' : 'home'));
  $('gallery-search').addEventListener('input', renderCards);
  $('gallery-state').addEventListener('change', renderCards);
  detailHost.addEventListener('focusout', () => queueMicrotask(() => controller.emit()));
  $('experiment-form').addEventListener('submit', async event => {
    event.preventDefault();
    const fields = Object.fromEntries(new FormData($('experiment-form')));
    if (continuation) Object.assign(fields, {base_candidate_id: continuation.candidate_id, criteria_metadata: continuation.criteria,
      ...(continuation.mode === 'transfer' ? {transfer_commission_id: continuation.commission_id} : {base_commission_id: continuation.commission_id})});
    if (await controller.start(fields)) show('detail');
  });
  void controller.refresh(); return controller;
}

if (typeof document !== 'undefined' && document.getElementById('method-gallery-app')) {
  let storage = null; try { storage = window.sessionStorage; } catch { /* Viewing remains available. */ }
  const controller = mountMethodGallery({document, root: new URL('./', location.href), storage});
  window.addEventListener('pagehide', () => controller.close(), {once: true});
}
