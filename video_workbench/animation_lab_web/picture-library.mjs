import {PICTURE_API, MAX_REFERENCE_BYTES, buildPictureProject, buildPictureExperiment, validateReferenceFile,
  pictureAssetUrl, pictureCandidates, latestPictureExperiment, pictureProjectState, pictureState,
  pictureIsActive, pictureDefaultCandidate, pictureCandidateRole, pictureProjectMatches,
  pictureStopReason, pictureRequestPath, pictureArtifactPath, pictureId, rows, text} from './picture-model.mjs';

const PENDING_KEY = 'video.picture-learning.pending.v1';
const ownerKey = value => typeof value === 'string' && value.length > 0 && value.length <= 300;
const pendingKey = owner => PENDING_KEY + ':' + encodeURIComponent(owner);
const browserStorage = () => { try { return globalThis.sessionStorage ?? null; } catch { return null; } };
const ERROR_MESSAGES = {
  picture_learning_unavailable: '图片学习执行器尚未配置。', picture_reference_invalid: '参考图片无法读取，请选择有效的 PNG 或 JPEG。',
  picture_project_active: '项目仍在运行，请先停止或等待本次实验结束。', picture_budget_exhausted: '本次实验预算已用完。',
  picture_reference_too_large: '参考图片超过大小上限，请缩小后再上传。', picture_request_conflict: '同一请求编号的内容不一致，请核对原请求。',
};
const MODE_NAMES = {continue: '继续优化', fresh: '从零重做', retry: '重试失败的实验'};
function errorMessage(error) {
  return ERROR_MESSAGES[error?.code] ?? (error?.status === 403 ? '登录或页面授权已失效，请重新登录后刷新。'
    : error?.status === 413 ? '提交内容过大，请缩小参考图片后重试。' : text(error?.message) || '请求未完成，请刷新核对。');
}
function pendingOperation(value) {
  return value && value.schema === 'video.picture-learning-pending.v1'
    && ownerKey(value.owner_key)
    && typeof value.path === 'string' && /^api\/picture-lab\/projects(?:\/[A-Za-z0-9_.:%-]+\/(?:experiments|adopt|stop))?$/.test(value.path)
    && typeof value.body === 'object' && value.body !== null && !Array.isArray(value.body) ? value : null;
}
export async function referenceBody(file, requestId) {
  const description = validateReferenceFile(file), bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes.byteLength !== file.size || bytes.byteLength > MAX_REFERENCE_BYTES) throw Error('参考文件在读取时发生变化，请重新选择');
  let binary = '';
  for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return {request_id: requestId, ...description, content_base64: btoa(binary)};
}
export class PictureLibraryController {
  constructor({request, root, storage = null, uuid = () => globalThis.crypto.randomUUID(),
    schedule = (fn, ms) => setTimeout(fn, ms), unschedule = id => clearTimeout(id), onChange = () => {}}) {
    this.request = request; this.root = root; this.storage = storage; this.uuid = uuid;
    this.schedule = schedule; this.unschedule = unschedule; this.onChange = onChange; this.timer = null; this.closed = false;
    this.readEpoch = 0; this.detailEpoch = 0; this.writeReady = false;
    this.state = {catalog: null, detail: null, busy: false, message: '', pending: null, pendingInvalid: false, owner_key: null, lastRead: null};
  }
  changed() { this.onChange(this.state); }
  enabled() { return this.writeReady && this.state.catalog?.availability?.enabled === true; }
  locked() { return this.state.busy || Boolean(this.state.pending) || this.state.pendingInvalid; }
  setMessage(value) { this.state.message = value; this.changed(); }
  useOwner(owner) {
    if (this.state.owner_key === owner) return;
    this.state.owner_key = owner; this.state.detail = null; this.detailEpoch++;
    this.state.pending = null; this.state.pendingInvalid = false;
    try {
      const raw = this.storage?.getItem(pendingKey(owner));
      if (raw !== null && raw !== undefined) {
        const saved = pendingOperation(JSON.parse(raw));
        if (!saved || saved.owner_key !== owner) throw Error('invalid');
        this.state.pending = saved;
      }
    } catch { this.state.pendingInvalid = true; this.state.message = '本浏览器保存的原请求无法读取。请先核对原实验，当前不会创建新任务。'; }
  }
  setPending(operation, owner = this.state.owner_key) {
    if (!ownerKey(owner)) throw Error('请先刷新，取得当前账号的授权与身份');
    if (operation) {
      const value = {schema: 'video.picture-learning-pending.v1', owner_key: owner, path: operation.path, body: structuredClone(operation.body)};
      if (this.storage) {
        try { this.storage.setItem(pendingKey(owner), JSON.stringify(value)); }
        catch { throw Error('无法保存本次请求，请释放浏览器存储空间后再提交'); }
      }
      if (owner === this.state.owner_key) this.state.pending = value;
    } else {
      this.storage?.removeItem(pendingKey(owner)); if (owner === this.state.owner_key) this.state.pending = null;
    }
  }
  poll() {
    if (this.timer !== null) this.unschedule(this.timer);
    this.timer = null;
    if (!this.closed && (pictureIsActive(this.state.detail) || rows(this.state.catalog?.projects).some(pictureIsActive) || this.state.pending)) {
      this.timer = this.schedule(() => { this.timer = null; return this.refresh(); }, 5000);
    }
  }
  async refresh() {
    const epoch = ++this.readEpoch;
    try {
      const catalog = await this.request(PICTURE_API + 'catalog');
      if (epoch !== this.readEpoch || this.closed) return this.state.catalog;
      if (!ownerKey(catalog?.owner_key) || !text(catalog?.csrf_token)) throw Error('目录缺少当前账号的授权信息，请刷新核对');
      this.useOwner(catalog.owner_key); this.state.catalog = catalog; this.writeReady = true;
      const detailId = this.state.detail?.id, detailEpoch = this.detailEpoch;
      if (detailId) {
        const detail = await this.request(pictureRequestPath(detailId));
        if (epoch !== this.readEpoch || this.closed) return this.state.catalog;
        if (detailEpoch === this.detailEpoch && this.state.detail?.id === detailId) this.state.detail = detail;
      }
      const recovered = this.reconcilePending();
      if (recovered && !Array.isArray(recovered.experiments)) {
        const detailEpoch = this.detailEpoch, owner = this.state.owner_key;
        const detail = await this.request(pictureRequestPath(recovered.id));
        if (epoch === this.readEpoch && detailEpoch === this.detailEpoch && owner === this.state.owner_key) this.state.detail = detail;
      }
      this.state.lastRead = new Date();
      if (!this.state.pending && !this.state.pendingInvalid) this.state.message = '';
    } catch (error) {
      if (epoch !== this.readEpoch || this.closed) return this.state.catalog;
      this.writeReady = false; if (this.state.catalog) this.state.catalog = {...this.state.catalog, csrf_token: ''};
      this.state.message = errorMessage(error);
    }
    this.changed(); this.poll(); return this.state.catalog;
  }
  reconcilePending() {
    const pending = this.state.pending, requestId = pending?.body?.request_id;
    const projects = [...rows(this.state.catalog?.projects), ...(this.state.detail ? [this.state.detail] : [])];
    if (pending && !requestId) {
      const match = /^api\/picture-lab\/projects\/([^/]+)\/(stop|adopt)$/.exec(pending.path);
      const project = match && projects.find(row => row.id === decodeURIComponent(match[1]));
      if (project && (match[2] === 'stop' && !pictureIsActive(project)
        || match[2] === 'adopt' && project.adopted_candidate_id === pending.body.candidate_id)) this.setPending(null);
      return;
    }
    if (!requestId) return;
    const found = projects.find(project => project.request_id === requestId || project.latest_experiment_request_id === requestId
      || rows(project.experiments).some(experiment => experiment.request_id === requestId));
    if (found) { this.setPending(null); if (!this.state.detail) this.state.detail = found; return found; }
  }
  async open(projectId) {
    const epoch = ++this.detailEpoch, owner = this.state.owner_key;
    try {
      const detail = await this.request(pictureRequestPath(projectId));
      if (epoch !== this.detailEpoch || owner !== this.state.owner_key || this.closed) return null;
      this.state.detail = detail; this.state.message = '';
    } catch (error) { if (epoch === this.detailEpoch) this.state.message = errorMessage(error); }
    this.changed(); this.poll(); return this.state.detail;
  }
  async send(path, body) {
    if (!this.writeReady || this.locked()) return false;
    this.setPending({path, body}); return this.replayPending();
  }
  async replayPending() {
    if (!this.writeReady || this.state.busy || !this.state.pending || this.state.pending.owner_key !== this.state.owner_key) return false;
    const operation = structuredClone(this.state.pending);
    this.state.busy = true; this.state.message = ''; this.changed();
    try {
      const result = await this.request(operation.path, {method: 'POST', body: JSON.stringify(operation.body)});
      const projectId = result?.project?.id ?? result?.project_id ?? result?.id;
      pictureId(projectId);
      this.setPending(null, operation.owner_key);
      if (operation.owner_key === this.state.owner_key) {
        this.detailEpoch++;
        this.state.detail = result?.project ?? (Array.isArray(result?.experiments) ? result : await this.request(pictureRequestPath(projectId)));
      }
      return true;
    } catch (error) {
      // Only definitive client rejection clears the immutable paid-operation key.
      if (Number.isInteger(error?.status) && error.status >= 400 && error.status < 500 && error.status !== 408) this.setPending(null, operation.owner_key);
      this.state.message = errorMessage(error) + (this.state.pending ? ' 原请求已保留，请核对或重送，勿重新提交。' : '');
      return false;
    } finally {
      this.state.busy = false; this.changed(); this.poll();
    }
  }
  async start(fields, file) {
    if (!this.enabled() || this.locked()) return false;
    // Validate the inexpensive form before uploading any pixels.
    buildPictureProject(fields, ['reference-validation'], this.uuid());
    const owner = this.state.owner_key;
    this.state.busy = true; this.state.message = '正在保存参考图片…'; this.changed();
    let reference;
    try { reference = await this.request(PICTURE_API + 'references', {method: 'POST', body: JSON.stringify(await referenceBody(file, this.uuid()))}); }
    catch (error) { this.state.message = errorMessage(error); return false; }
    finally { this.state.busy = false; this.changed(); }
    if (owner !== this.state.owner_key || !this.enabled()) { this.setMessage('当前登录身份或授权已变化，请刷新后重新提交'); return false; }
    const referenceId = reference?.reference?.id ?? reference?.reference_id ?? reference?.id;
    return this.send(PICTURE_API + 'projects', buildPictureProject(fields, [referenceId], this.uuid()));
  }
  async experiment(fields) {
    const detail = this.state.detail;
    if (!detail?.id || !this.enabled() || this.locked() || pictureIsActive(detail)) return false;
    return this.send(pictureRequestPath(detail.id, 'experiments'), buildPictureExperiment(fields, this.uuid()));
  }
  async stop() {
    if (!this.state.detail?.id || this.locked() || !pictureIsActive(this.state.detail)) return false;
    return this.send(pictureRequestPath(this.state.detail.id, 'stop'), {});
  }
  async adopt(candidateId) {
    if (!this.state.detail?.id || this.locked() || pictureIsActive(this.state.detail)) return false;
    pictureId(candidateId);
    return this.send(pictureRequestPath(this.state.detail.id, 'adopt'), {candidate_id: candidateId});
  }
  destroy() { this.closed = true; if (this.timer !== null) this.unschedule(this.timer); this.timer = null; }
}

function el(doc, tag, className = '', content = null) {
  const result = doc.createElement(tag); if (className) result.className = className;
  if (content !== null) result.textContent = String(content); return result;
}
function button(doc, label, handler, {className = 'button secondary', disabled = false} = {}) {
  const result = el(doc, 'button', className, label); result.type = 'button'; result.disabled = disabled;
  result.addEventListener('click', handler); return result;
}
function externalArtifactLink(doc, label, value, root, download = false) {
  const url = pictureAssetUrl(value, root); if (!url) return null;
  const link = el(doc, 'a', 'button secondary', label); link.href = url;
  if (download) link.download = ''; else { link.target = '_blank'; link.rel = 'noopener'; }
  return link;
}
function candidateArtifact(candidate, kind, projectId) {
  const direct = candidate?.[kind + '_url'];
  if (direct) return direct;
  const row = candidate?.artifacts?.[kind];
  if (typeof row === 'string') return pictureArtifactPath(projectId, row);
  if (row?.url) return row.url;
  if (row?.id) return pictureArtifactPath(projectId, row.id);
  const id = candidate?.[kind + '_artifact_id'];
  return id ? pictureArtifactPath(projectId, id) : null;
}
export function pictureImageUrl(candidate, project, root) {
  return pictureAssetUrl(candidateArtifact(candidate, 'picture', project?.id) ?? candidateArtifact(candidate, 'image', project?.id)
    ?? candidate?.media_url, root);
}
function referenceUrl(reference, root) {
  return pictureAssetUrl(reference?.media_url ?? reference?.image_url ?? reference?.url
    ?? (reference?.id ? PICTURE_API + 'references/' + encodeURIComponent(pictureId(reference.id)) + '/media' : null), root);
}
function pictureFigure(doc, title, url, note, root) {
  const figure = el(doc, 'figure', 'picture-figure'), caption = el(doc, 'figcaption', '', title), well = el(doc, 'div', 'picture-image-well');
  if (url) {
    const image = el(doc, 'img'); image.src = url; image.alt = title; image.decoding = 'async'; well.append(image);
    const open = externalArtifactLink(doc, '查看原图', url, root); if (open) caption.append(open);
  } else well.append(el(doc, 'p', '', '图片尚未生成。完成后会在这里展示实际渲染结果。'));
  figure.append(caption, well); if (note) figure.append(el(doc, 'footer', '', note)); return figure;
}
function jsonSection(doc, title, value, open = false) {
  const section = el(doc, 'details', 'technical-details'); section.open = open;
  section.append(el(doc, 'summary', '', title), el(doc, 'pre', 'picture-json', JSON.stringify(value, null, 2))); return section;
}
function reviewPanel(doc, review) {
  const panel = el(doc, 'section', 'panel'); panel.append(el(doc, 'h3', '', '独立比较与剩余问题'));
  if (!review) { panel.append(el(doc, 'p', 'muted', '尚无评审结果。图片存在不代表效果已达标。')); return panel; }
  const summary = text(review.summary) || text(review.verdict) || text(review.conclusion);
  if (summary) panel.append(el(doc, 'p', '', summary));
  if (Number.isFinite(review.score)) panel.append(el(doc, 'p', 'small muted', '模型对照评分：' + review.score + ' / 100；不代替开发者验收。'));
  for (const [key, label] of [['strengths', '实际观察到的进展'], ['issues', '仍需改善'], ['limitations', '尚未验证']]) {
    const items = rows(review[key]); if (!items.length) continue;
    panel.append(el(doc, 'h4', '', label)); const list = el(doc, 'ul');
    for (const item of items) list.append(el(doc, 'li', '', typeof item === 'string' ? item : text(item.description) || text(item.issue) || JSON.stringify(item)));
    panel.append(list);
  }
  panel.append(jsonSection(doc, '查看完整评审记录', review)); return panel;
}
export function renderPictureCard(doc, project, {root, onOpen}) {
  const article = el(doc, 'article', 'effect-card picture-card'), candidate = pictureDefaultCandidate(project), url = pictureImageUrl(candidate, project, root) ?? pictureAssetUrl(project.preview_url, root);
  if (url) { const image = el(doc, 'img', 'picture-card-image'); image.src = url; image.alt = text(project.title); image.loading = 'lazy'; article.append(image); }
  else article.append(el(doc, 'div', 'picture-card-placeholder', '▧'));
  const body = el(doc, 'div', 'card-body'); body.append(button(doc, project.title || '未命名学习项目', () => onOpen(project.id), {className: 'effect-title'}));
  body.append(el(doc, 'p', 'card-goal', project.goal), el(doc, 'span', 'badge', pictureState(pictureProjectState(project))));
  if (project.adopted_candidate_id) body.append(el(doc, 'span', 'badge', '已选默认作品'));
  body.append(el(doc, 'div', 'card-meta', (project.experiments_count ?? rows(project.experiments).length) + ' 次实验'
    + (pictureCandidates(project).length ? ' · ' + pictureCandidates(project).length + ' 份作品' : ''))); article.append(body); return article;
}
export function renderPictureDetail(doc, project, {root, selection = null, onSelect = () => {}, onBack = () => {}, onStop = () => {}, onExperiment = () => {}, onAdopt = () => {}, onInspect = null, locked = false}) {
  const fragment = doc.createDocumentFragment(), header = el(doc, 'div', 'detail-heading');
  const intro = el(doc, 'div'); intro.append(el(doc, 'p', 'eyebrow', '图片学习项目'), el(doc, 'h1', '', project.title), el(doc, 'p', 'muted', project.goal));
  header.append(intro); fragment.append(button(doc, '返回图片学习库', onBack), header);
  const current = latestPictureExperiment(project), active = pictureIsActive(project), candidate = pictureDefaultCandidate(project, selection);
  const toolbar = el(doc, 'div', 'picture-details-toolbar');
  if (active) toolbar.append(button(doc, ['stopping', 'cancelling'].includes(pictureProjectState(project)) ? '正在核实停止' : '停止本次实验', onStop,
    {className: 'button danger', disabled: locked || ['stopping', 'cancelling'].includes(pictureProjectState(project))}));
  else {
    if (candidate) toolbar.append(button(doc, '继续优化', () => onExperiment('continue'), {className: 'button primary', disabled: locked}));
    toolbar.append(button(doc, '从零重做', () => onExperiment('fresh'), {disabled: locked}));
    if (pictureProjectState(project) === 'failed') toolbar.append(button(doc, '技术重试', () => onExperiment('retry'), {disabled: locked}));
  }
  const status = el(doc, 'div', 'picture-status'); status.append(el(doc, 'strong', '', pictureState(pictureProjectState(project))));
  const reason = pictureStopReason(current?.stop_reason ?? project.stop_reason); if (reason) status.append(el(doc, 'p', 'small muted', reason));
  status.append(el(doc, 'p', 'small muted', active ? '本次需求、参考和知识版本已经冻结。运行中只查看或停止，新的意见用于下一次实验。'
    : '内部最佳与默认作品分别保存。采用一幅图片不会发布知识，也不代表运动已经验证。'));
  if (typeof current?.frozen?.use_wikiskill === 'boolean') status.append(el(doc, 'p', 'small muted',
    current.frozen.use_wikiskill === false ? '本次未加载已发布 WikiSkill。'
      : current.frozen.library_binding?.release_id ? '本次固定 WikiSkill 版本：' + current.frozen.library_binding.release_id
        : '本次选择加载 WikiSkill，但尚无可用发布版，未注入技法正文。'));
  const failure = text(current?.error_code) || text(current?.error?.code) || text(current?.error);
  if (failure) status.append(el(doc, 'p', 'small muted', '执行信息：' + failure));
  fragment.append(toolbar, status);
  const candidates = pictureCandidates(project), versions = el(doc, 'div', 'picture-versions');
  for (let i = 0; i < candidates.length; i++) {
    const row = candidates[i], labels = [pictureCandidateRole(row) + ' ' + (i + 1)];
    if (row.id === project.best_candidate_id) labels.push('内部最佳');
    if (row.id === project.adopted_candidate_id) labels.push('默认作品');
    const choice = button(doc, labels.join(' · '), () => onSelect(row.id)); choice.setAttribute('aria-pressed', String(candidate?.id === row.id)); versions.append(choice);
  }
  if (candidates.length) fragment.append(versions);
  const references = rows(project.references), reference = references[0] ?? (rows(project.reference_ids)[0] ? {id: project.reference_ids[0]} : null);
  const work = el(doc, 'div', 'picture-workspace');
  work.append(pictureFigure(doc, '参考图片', referenceUrl(reference, root), reference?.file_name ?? '', root));
  const pictureUrl = pictureImageUrl(candidate, project, root);
  work.append(pictureFigure(doc, candidate ? pictureCandidateRole(candidate) : '实际绘制结果', pictureUrl,
    candidate?.picture_sha256 || candidate?.sha256 ? '图片 SHA-256：' + (candidate.picture_sha256 ?? candidate.sha256) : '由源码真实渲染；不使用参考截图充当成品。', root)); fragment.append(work);
  if (candidate) {
    const actions = el(doc, 'div', 'picture-result-links');
    if (pictureUrl) { const link = externalArtifactLink(doc, '下载图片', pictureUrl, root, true); if (link) actions.append(link); }
    for (const [kind, label] of [['source', '下载绘制源码'], ['receipt', '下载渲染回执']]) {
      const link = externalArtifactLink(doc, label, candidateArtifact(candidate, kind, project.id), root, true); if (link) actions.append(link);
    }
    if (!active && (candidate.role ?? candidate.work_kind) !== 'transfer') actions.append(button(doc, candidate.id === project.adopted_candidate_id ? '已作为默认作品' : '采用为默认作品',
      () => onAdopt(candidate.id), {disabled: locked || candidate.id === project.adopted_candidate_id}));
    fragment.append(actions);
  }
  const evidence = el(doc, 'div', 'picture-evidence-grid'); evidence.append(reviewPanel(doc, candidate?.review ?? candidate?.assessment));
  const scope = el(doc, 'section', 'panel'); scope.append(el(doc, 'h3', '', '本次验证范围'));
  const transfer = candidates.filter(row => (row.role ?? row.work_kind) === 'transfer');
  scope.append(el(doc, 'p', 'muted', '图片学习验证造型、笔触、材质、光影等静态效果；连续运动与声音尚未验证。'),
    el(doc, 'p', '', transfer.length ? '已有换内容验证作品，请切换作品查看实际结果与评审。' : '换内容验证尚无产物；复现进展不能替代迁移通过。'));
  if ((candidate?.role ?? candidate?.work_kind) === 'transfer') {
    const experiment = rows(project.experiments).find(row => rows(row.candidates).some(item => item.id === candidate.id));
    if (experiment?.transfer_task) scope.append(jsonSection(doc, '本次换内容验证的任务', experiment.transfer_task, true));
  }
  if (candidate?.method) scope.append(jsonSection(doc, '查看本次形成的方法', candidate.method));
  if (candidate?.observation_receipts) scope.append(jsonSection(doc, '参考与结果的实际输入记录', candidate.observation_receipts));
  evidence.append(scope); fragment.append(evidence);
  const history = el(doc, 'details', 'technical-details'); history.append(el(doc, 'summary', '', '实验历史与冻结需求（' + rows(project.experiments).length + '）'));
  const historyList = el(doc, 'ol', 'picture-history');
  for (const experiment of rows(project.experiments)) {
    const item = el(doc, 'li'), content = el(doc, 'div');
    content.append(el(doc, 'strong', '', (MODE_NAMES[experiment.mode] ?? '首次学习') + ' · ' + pictureState(experiment.state)));
    const feedback = experiment.frozen?.feedback ?? experiment.feedback;
    if (feedback) content.append(el(doc, 'p', 'small muted', feedback));
    const submitted = experiment.frozen ?? experiment.input ?? experiment.request ?? experiment;
    content.append(jsonSection(doc, '查看本次提交与冻结输入', submitted));
    if (onInspect) {
      const inspection = el(doc, 'details', 'technical-details'); inspection.append(el(doc, 'summary', '', '查看实际 Agent 输入'));
      inspection.append(el(doc, 'p', 'small muted', '展开时只读取已保存的本次角色输入；不代表完整系统提示词，也不会启动创作。'));
      let loaded = false;
      inspection.addEventListener('toggle', async () => {
        if (!inspection.open || loaded) return;
        loaded = true; const output = el(doc, 'pre', 'picture-json', '正在读取…'); inspection.append(output);
        try { output.textContent = JSON.stringify(await onInspect(experiment.id), null, 2); }
        catch (error) { output.textContent = errorMessage(error); loaded = false; }
      }); content.append(inspection);
    }
    item.append(content, el(doc, 'small', '', typeof experiment.created_at === 'number'
      ? new Date(experiment.created_at * 1000).toLocaleString() : experiment.created_at ?? experiment.id)); historyList.append(item);
  }
  history.append(historyList); fragment.append(history); return fragment;
}

export function mountPictureLibrary(doc, {root = new URL('./', globalThis.location?.href), fetcher = globalThis.fetch,
  storage = browserStorage(), uuid, schedule, unschedule, URLObject = globalThis.URL} = {}) {
  const $ = id => doc.getElementById(id), app = $('picture-library-app'); if (!app) return null;
  let formMode = null, shown = 'home', selectedId = null, previewUrl = null, detailSignature = '', controller;
  const request = async (path, options = {}) => {
    const headers = {Accept: 'application/json'};
    if (options.method === 'POST') Object.assign(headers, {'Content-Type': 'application/json', 'X-Animation-Lab': '1',
      'X-Animation-Lab-Token': controller.state.catalog?.csrf_token ?? '', 'X-CSRF-Token': controller.state.catalog?.csrf_token ?? ''});
    const response = await fetcher(new URL(path, root), {...options, headers, credentials: 'same-origin', redirect: 'error', cache: 'no-store'});
    if (!response.ok) {
      const error = Error('请求未完成（HTTP ' + response.status + '）'); error.status = response.status;
      try { error.code = text((await response.json())?.error); } catch {} throw error;
    }
    return response.json();
  };
  const clearPreview = () => { if (previewUrl) URLObject.revokeObjectURL(previewUrl); previewUrl = null; };
  const show = mode => { shown = mode; $('picture-home').hidden = mode !== 'home'; $('picture-detail').hidden = mode !== 'detail'; $('picture-form-panel').hidden = mode !== 'form'; };
  const renderCards = () => {
    const projects = rows(controller.state.catalog?.projects), query = $('picture-search').value;
    const filtered = projects.filter(project => pictureProjectMatches(project, query));
    $('picture-cards').replaceChildren(...filtered.map(project => renderPictureCard(doc, project, {root, onOpen: async id => {
      await controller.open(id); if (controller.state.detail?.id === id) { show('detail'); detailSignature = ''; render(controller.state); }
    }})));
    $('picture-count').textContent = projects.length + ' 个学习项目';
    $('picture-empty').hidden = projects.length > 0;
    if (projects.length && !filtered.length) $('picture-cards').append(el(doc, 'p', 'muted', '没有符合条件的学习项目。'));
  };
  const render = state => {
    $('picture-message').hidden = !state.message; $('picture-message').textContent = state.message;
    $('picture-pending').hidden = !state.pending; $('picture-pending-retry').disabled = state.busy;
    $('picture-new').disabled = !controller.enabled() || controller.locked(); $('picture-empty-new').disabled = $('picture-new').disabled;
    $('picture-sync').textContent = state.busy ? '正在提交…' : state.lastRead ? '最近读取：' + state.lastRead.toLocaleTimeString() : '正在读取图片学习库…';
    $('picture-availability').textContent = state.catalog?.availability?.enabled === false ? text(state.catalog.availability.reason) || '图片学习执行器尚未启用。' : '';
    $('picture-submit').disabled = !controller.enabled() || controller.locked();
    for (const element of $('picture-form').elements) element.disabled = state.busy || Boolean(state.pending)
      || formMode === 'retry' && !['picture-rounds', 'picture-minutes', 'picture-submit'].includes(element.id);
    $('picture-submit').disabled = !controller.enabled() || controller.locked();
    renderCards();
    if (state.detail && shown === 'detail') {
      const signature = JSON.stringify({detail: state.detail, locked: controller.locked(), selectedId});
      if (signature !== detailSignature) {
        detailSignature = signature;
        $('picture-detail').replaceChildren(renderPictureDetail(doc, state.detail, {root, selection: selectedId, locked: controller.locked(),
          onSelect: id => { selectedId = id; detailSignature = ''; render(state); }, onBack: () => show('home'),
          onStop: () => void controller.stop(), onExperiment: mode => openForm(mode), onAdopt: id => void controller.adopt(id),
          onInspect: experimentId => request(pictureRequestPath(state.detail.id, 'experiments/' + encodeURIComponent(pictureId(experimentId)) + '/input'))}));
      }
    }
  };
  const openForm = (mode = null) => {
    if (controller.locked()) return;
    formMode = mode; clearPreview(); $('picture-form').reset(); $('picture-reference-preview').replaceChildren(); $('picture-reference-preview').hidden = true;
    $('picture-new-fields').hidden = Boolean(mode); $('picture-feedback-label').hidden = !mode || mode === 'retry';
    $('picture-form-title').textContent = mode ? MODE_NAMES[mode] : '新建图片学习';
    $('picture-submit').textContent = mode ? '开始' + MODE_NAMES[mode] : '开始图片学习';
    $('picture-form-note').hidden = !mode;
    $('picture-form-note').textContent = mode === 'fresh' ? '保留同一参考和学习目标，从零创作。旧源码、布局和专用方法不会作为起点；可在下方填写精简的问题。'
      : mode === 'retry' ? '保留原来的目标、参考、标准和确切知识版本，重试失败环节。这里仅调整本次预算。'
        : '保留同一参考和学习目标，在已有作品上继续改善。下方意见会冻结进下一次实验，不修改历史需求。';
    for (const id of ['picture-name', 'picture-goal', 'picture-reference', 'picture-width', 'picture-height']) $(id).required = !mode;
    $('picture-feedback').required = mode === 'continue';
    $('picture-use-wikiskill').checked = mode === 'retry' ? latestPictureExperiment(controller.state.detail)?.frozen?.use_wikiskill !== false : true;
    $('picture-skill-help').hidden = mode === 'retry';
    show('form'); render(controller.state); (mode === 'retry' ? $('picture-rounds') : mode ? $('picture-feedback') : $('picture-name')).focus();
  };
  controller = new PictureLibraryController({request, root, storage, uuid, schedule, unschedule, onChange: render});
  $('picture-refresh').addEventListener('click', () => void controller.refresh());
  $('picture-new').addEventListener('click', () => openForm()); $('picture-empty-new').addEventListener('click', () => openForm());
  $('picture-pending-retry').addEventListener('click', () => void controller.replayPending());
  $('picture-form-close').addEventListener('click', () => { clearPreview(); show(formMode ? 'detail' : 'home'); detailSignature = ''; render(controller.state); });
  $('picture-search').addEventListener('input', renderCards);
  $('picture-reference').addEventListener('change', () => {
    clearPreview(); const host = $('picture-reference-preview'); host.replaceChildren(); host.hidden = true;
    const file = $('picture-reference').files?.[0]; if (!file) return;
    try {
      validateReferenceFile(file); previewUrl = URLObject.createObjectURL(file);
      const image = el(doc, 'img'); image.src = previewUrl; image.alt = '待上传的参考图片';
      host.append(image, el(doc, 'p', 'small muted', file.name)); host.hidden = false; controller.setMessage('');
    } catch (error) { $('picture-reference').value = ''; controller.setMessage(errorMessage(error)); }
  });
  $('picture-form').addEventListener('submit', async event => {
    event.preventDefault(); if (controller.locked()) return;
    const fields = Object.fromEntries(new FormData($('picture-form'))); fields.use_wikiskill = $('picture-use-wikiskill').checked;
    if (formMode === 'continue') fields.base_candidate_id = pictureDefaultCandidate(controller.state.detail, selectedId)?.id;
    try {
      const success = formMode ? await controller.experiment({...fields, mode: formMode}) : await controller.start(fields, $('picture-reference').files?.[0]);
      if (success && controller.state.detail) { clearPreview(); show('detail'); selectedId = null; detailSignature = ''; render(controller.state); void controller.refresh(); }
    } catch (error) { controller.setMessage(errorMessage(error)); }
  });
  const ready = controller.refresh();
  return {controller, ready, destroy: () => { clearPreview(); controller.destroy(); }};
}
if (typeof document !== 'undefined' && document.getElementById('picture-library-app')) mountPictureLibrary(document);
