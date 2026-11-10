import {experimentIdentity, experimentLocalUrl} from './experiment-model.mjs';

const ACTIVE = new Set(['queued', 'running', 'cancelling', 'submission_unknown']);
const STATES = {queued: '等待执行', running: '正在执行', cancelling: '正在取消',
  completed: '本轮流程已结束 · 效果待评价', failed: '执行失败', cancelled: '已取消 · 已用额度不退回',
  submission_unknown: '提交结果待核对 · 不会重复提交'};
const STAGES = {initial_creation: 'Agent生成初作', initial_render: '渲染初作',
  review: '独立审阅初作', revision: 'Agent修改一次', verification: '修订后渲染与技术复验'};
const ROLES = new Set(['development', 'held_out']);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function creationStateLabel(state) { return STATES[state] ?? '状态待核对'; }
export function creationStageLabel(stage) { return STAGES[stage] ?? '阶段待核对'; }

export function creationAssetUrl(value, root, runId, versionId, kind) {
  if (!['original', 'revised'].includes(versionId) || !['media', 'source'].includes(kind)) return null;
  const local = experimentLocalUrl(value, root);
  if (!local) return null;
  const url = new URL(local), base = new URL(root);
  const expected = `${base.pathname}api/creation-runs/${encodeURIComponent(runId)}/versions/${versionId}/${kind}`;
  return url.pathname === expected && !url.search && !url.hash ? local : null;
}

function validateRun(run) {
  if (!run || typeof run.id !== 'string' || !run.id || !UUID.test(run.request_id ?? '')
    || !ROLES.has(run.input_role) || !Object.hasOwn(STATES, run.state)
    || !run.experiment || typeof run.experiment.id !== 'string' || typeof run.experiment.identity !== 'string'
    || !Array.isArray(run.versions) || run.versions.some(version => !version
      || !['original', 'revised'].includes(version.id) || typeof version.status !== 'string'
      || typeof version.technical_status !== 'string'
      || (version.media_url != null && typeof version.media_url !== 'string')
      || (version.source_url != null && typeof version.source_url !== 'string'))
    || new Set(run.versions.map(version => version.id)).size !== run.versions.length
    || (run.review !== null && run.review !== undefined
      && (!Array.isArray(run.review.issues) || !Array.isArray(run.review.limitations)))
    || (run.aesthetic_status !== undefined && run.aesthetic_status !== 'unreviewed')) {
    throw Error('制作记录字段不完整，无法确认本轮状态');
  }
  return run;
}

// All progress reads are GETs. A persisted request is never replaced or retried by a POST.
export class CreationRunsController {
  constructor({request, root, storage, uuid = () => crypto.randomUUID(), onChange = () => {},
    schedule = (fn, ms) => setTimeout(fn, ms), unschedule = timer => clearTimeout(timer), pollMs = 2500}) {
    Object.assign(this, {request, root: new URL(root), storage, uuid, onChange, schedule, unschedule, pollMs});
    this.epoch = 0; this.active = false; this.timer = null; this.readAbort = null;
    this.pendingSubmissions = new Set();
    this.pendingCancellations = new Set();
    this.state = {context: null, loading: false, enabled: false, disabledReason: null,
      limits: null, runs: [], record: null, error: '', notice: '', submitting: false, cancelling: []};
  }

  get snapshot() { return {...this.state, canStart: this.canStart()}; }
  canStart() {
    return Boolean(this.active && this.state.context && this.state.enabled && !this.state.loading
      && !this.state.submitting && !this.state.record && !this.state.error
      && !this.state.runs.some(run => ACTIVE.has(run.state)));
  }
  emit() { this.onChange(this.snapshot); }
  key(context = this.state.context) {
    return `video-animation-creation-v1:${this.root.href}:${JSON.stringify(context &&
      [context.id, context.identity, context.input_role])}`;
  }
  saveRecord(record, context = this.state.context) {
    if (!this.storage) throw Error('浏览器无法保存本次请求编号，已禁止启动，避免刷新后重复制作');
    this.storage.setItem(this.key(context), JSON.stringify(record));
  }
  readRecord(context) {
    if (!this.storage) throw Error('浏览器无法保存本次请求编号，已禁止启动，避免刷新后重复制作');
    const raw = this.storage.getItem(this.key(context));
    if (!raw) return null;
    let record;
    try { record = JSON.parse(raw); } catch { throw Error('已保存的制作请求无法读取，请先核对原任务；没有创建新请求'); }
    if (!UUID.test(record?.request_id ?? '') || record.experiment_id !== context.id
      || record.definition_identity !== context.identity || record.input_role !== context.input_role) {
      throw Error('已保存的制作请求与当前定义不符，请先核对；没有创建新请求');
    }
    return record;
  }
  stopRead() {
    if (this.timer !== null) this.unschedule(this.timer);
    this.timer = null; this.readAbort?.abort(); this.readAbort = null;
  }
  pause() { this.active = false; this.epoch++; this.stopRead(); }
  async select(experiment, role = 'development') {
    this.pause();
    const identity = experimentIdentity(experiment);
    const context = experiment && identity && ROLES.has(role)
      ? {id: experiment.id, identity, title: experiment.title ?? experiment.id, input_role: role} : null;
    this.state = {context, loading: false, enabled: false, disabledReason: null,
      limits: null, runs: [], record: null, error: '', notice: '', submitting: false, cancelling: []};
    this.active = Boolean(context);
    if (context) {
      try { this.state.record = this.readRecord(context); }
      catch (error) { this.state.error = error.message; }
    }
    this.emit();
    if (context) await this.refresh();
  }
  async resume() {
    if (!this.state.context) return;
    this.active = true;
    await this.refresh();
  }
  accepts(run, context = this.state.context) {
    return run.experiment.id === context?.id && run.experiment.identity === context.identity
      && run.input_role === context.input_role;
  }
  rememberRun(run, context = this.state.context) {
    const record = this.state.record;
    if (record?.request_id === run.request_id) {
      const updated = {...record, run_id: run.id, status: run.state};
      this.saveRecord(updated, context); this.state.record = updated;
    }
  }
  queueRead() {
    if (!this.active || !this.state.enabled || this.state.error || this.state.loading || this.readAbort
      || !this.state.runs.some(run => ACTIVE.has(run.state))) return;
    if (this.timer !== null) this.unschedule(this.timer);
    this.timer = this.schedule(() => { this.timer = null; void this.poll(); }, this.pollMs);
  }
  async refresh() {
    if (!this.active || !this.state.context) return;
    this.stopRead(); const epoch = ++this.epoch, context = this.state.context;
    const abort = new AbortController(); this.readAbort = abort;
    this.state.loading = true; this.emit();
    try {
      const value = await this.request(`api/creation-runs?experiment_id=${encodeURIComponent(context.id)}`,
        {signal: abort.signal});
      if (!this.active || epoch !== this.epoch) return;
      if (typeof value.enabled !== 'boolean' || !Array.isArray(value.runs)
        || !value.limits || value.limits.initial_creations !== 1 || value.limits.reviews !== 1
        || value.limits.creator_revisions !== 1 || value.limits.renders !== 2) {
        throw Error('制作能力或单轮额度配置不完整，暂不能启动');
      }
      const runs = value.runs.map(validateRun).filter(run => this.accepts(run, context));
      this.state.enabled = value.enabled;
      this.state.disabledReason = value.disabled_reason || (value.enabled ? null : '服务尚未启用完整制作链路');
      this.state.limits = value.limits; this.state.runs = runs;
      this.state.cancelling = runs.filter(run => this.pendingCancellations.has(run.id)).map(run => run.id);
      this.state.record = this.readRecord(context); this.state.error = '';
      this.state.submitting = this.pendingSubmissions.has(this.state.record?.request_id);
      const found = runs.find(run => run.request_id === this.state.record?.request_id);
      if (found) { this.rememberRun(found, context); this.state.notice = '已匹配本次原制作记录，后续只读取进度。'; }
      else if (this.state.record) this.state.notice = '本次请求已保存，但尚未查到匹配任务。请刷新核对；不会自动重新提交。';
      else this.state.notice = '';
    } catch (error) {
      if (!this.active || epoch !== this.epoch || error.name === 'AbortError') return;
      this.state.error = `无法读取本轮制作：${error.message}。没有提交或重试制作任务。`;
    } finally {
      if (epoch === this.epoch && this.active) {
        this.readAbort = null; this.state.loading = false; this.emit(); this.queueRead();
      }
    }
  }
  async poll() {
    if (!this.active || !this.state.context) return;
    const run = this.state.runs.find(row => ACTIVE.has(row.state));
    if (!run) return;
    const epoch = this.epoch, abort = new AbortController(); this.readAbort = abort;
    try {
      const value = validateRun(await this.request(`api/creation-runs/${encodeURIComponent(run.id)}`, {signal: abort.signal}));
      if (!this.active || epoch !== this.epoch) return;
      if (value.id !== run.id || value.request_id !== run.request_id || !this.accepts(value)) throw Error('任务身份与当前实验不符');
      this.state.runs = this.state.runs.map(row => row.id === value.id ? value : row);
      this.rememberRun(value); this.state.error = '';
    } catch (error) {
      if (!this.active || epoch !== this.epoch || error.name === 'AbortError') return;
      this.state.error = `读取制作进度失败：${error.message}。已停止轮询，请刷新核对；不会重新制作。`;
    } finally {
      if (this.active && epoch === this.epoch) { this.readAbort = null; this.emit(); this.queueRead(); }
    }
  }
  async start() {
    if (!this.canStart()) return false;
    const context = this.state.context, epoch = this.epoch;
    let record;
    try {
      // Re-read just before reservation so a second mount cannot silently replace it.
      if (this.readRecord(context)) { await this.refresh(); return false; }
      record = {request_id: this.uuid(), experiment_id: context.id, definition_identity: context.identity,
        input_role: context.input_role, run_id: null, status: 'submitting'};
      if (!UUID.test(record.request_id)) throw Error('无法生成有效的制作请求编号');
      this.saveRecord(record, context);
    } catch (error) { this.state.error = error.message; this.emit(); return false; }
    this.pendingSubmissions.add(record.request_id);
    this.state.record = record; this.state.submitting = true; this.state.notice = '已保存请求编号，正在提交本轮制作…'; this.emit();
    try {
      const value = validateRun(await this.request('api/creation-runs', {method: 'POST',
        body: JSON.stringify({request_id: record.request_id, experiment_id: record.experiment_id,
          definition_identity: record.definition_identity, input_role: record.input_role})}));
      if (value.request_id !== record.request_id || !this.accepts(value, context)) throw Error('提交回执的任务身份不符');
      this.saveRecord({...record, run_id: value.id, status: value.state}, context);
      if (!this.active || epoch !== this.epoch) return true;
      this.state.record = {...record, run_id: value.id, status: value.state};
      this.state.runs = [value, ...this.state.runs.filter(run => run.id !== value.id)];
      this.state.notice = '本轮已提交，后续仅查询进度。技术检查与模型意见不代表效果已获认可。';
      this.state.error = ''; return true;
    } catch (error) {
      let saved = {...record, status: 'submission_unknown'};
      try {
        const recovered = this.readRecord(context);
        if (recovered?.request_id === record.request_id && recovered.run_id) saved = recovered;
        this.saveRecord(saved, context);
      } catch { /* Original reservation remains. */ }
      if (this.active && epoch === this.epoch) {
        this.state.record = saved;
        this.state.error = `启动未获确认：${error.message}。请刷新核对本次请求，不会自动重发或新建。`;
      }
      return false;
    } finally {
      this.pendingSubmissions.delete(record.request_id);
      if (this.active && this.state.record?.request_id === record.request_id) {
        this.state.submitting = false;
        if (epoch !== this.epoch && !this.state.error) this.state.notice = '启动回执已保存，请刷新核对本次原任务；不会重复提交。';
        this.emit(); this.queueRead();
      }
    }
  }
  async cancel(id) {
    const run = this.state.runs.find(row => row.id === id);
    if (!this.active || !run || !['queued', 'running', 'submission_unknown'].includes(run.state)
      || this.pendingCancellations.has(id)) return false;
    this.stopRead(); const epoch = ++this.epoch;
    this.pendingCancellations.add(id); this.state.cancelling.push(id); this.emit();
    try {
      const value = validateRun(await this.request(`api/creation-runs/${encodeURIComponent(id)}/cancel`,
        {method: 'POST', body: '{}'}));
      if (!this.active || epoch !== this.epoch) return true;
      if (value.id !== run.id || value.request_id !== run.request_id || !this.accepts(value)) throw Error('取消回执身份不符');
      this.state.runs = this.state.runs.map(row => row.id === id ? value : row); this.rememberRun(value);
      this.state.notice = '取消请求已记录；已开始使用的本轮额度不会退回。'; this.state.error = ''; return true;
    } catch (error) {
      if (this.active && epoch === this.epoch) this.state.error = `取消未获确认：${error.message}。请刷新核对，没有自动重发取消。`;
      return false;
    } finally {
      this.pendingCancellations.delete(id);
      if (this.active && this.state.runs.some(value => value.id === id)) {
        this.state.cancelling = this.state.cancelling.filter(value => value !== id); this.emit();
        if (epoch === this.epoch) this.stopRead();
        this.queueRead();
      }
    }
  }
}

function text(document, tag, value, className) {
  const element = document.createElement(tag);
  if (value !== undefined) element.textContent = String(value);
  if (className) element.className = className;
  return element;
}

export function renderCreationVersion(document, version, run, root) {
  const make = (tag, value, className) => text(document, tag, value, className);
  const article = make('article', undefined, 'creation-version');
  const title = version.id === 'original' ? '初作 · 保留原版' : '一次修订后';
  article.append(make('h4', title));
  const statuses = {pending: '等待执行', running: '正在生成', generated: '已生成源码', rendered: '已渲染',
    completed: '产物步骤已结束', failed: '执行失败', cancelled: '已取消', not_run: '未执行', skipped: '未生成该版本'};
  article.append(make('p', `版本状态：${statuses[version.status] ?? version.status}`, 'muted small'));
  const technical = {passed: '技术检查通过 · 效果待评价', failed: '技术检查失败 · 原结果保留',
    pending: '技术检查待执行', running: '技术检查进行中', not_run: '未执行技术检查'};
  article.append(make('p', technical[version.technical_status] ?? '技术检查状态待核对', 'muted small'));
  const media = creationAssetUrl(version.media_url, root, run.id, version.id, 'media');
  if (media) {
    const video = make('video'); video.controls = true; video.preload = 'metadata'; video.muted = true; video.playsInline = true;
    video.setAttribute('aria-label', `${title}实际渲染视频`); video.src = media;
    const status = make('p', '媒体正在加载…', 'muted small');
    video.addEventListener('loadedmetadata', () => { status.textContent = '实际视频可播放；可使用播放器定位查看。'; });
    video.addEventListener('error', () => { status.textContent = '媒体读取失败，请刷新核对；不会自动重新制作。'; status.className = 'creation-error small'; });
    const link = make('a', '下载视频', 'text-button'); link.href = media; link.download = `${run.id}-${version.id}.mp4`;
    article.append(video, status, link);
  } else article.append(make('p', version.media_url ? '媒体地址未通过当前制作记录绑定校验。' : '尚无可播放的渲染媒体。', 'muted small'));
  const source = creationAssetUrl(version.source_url, root, run.id, version.id, 'source');
  if (source) {
    const link = make('a', '下载Agent源码（仅文件）', 'text-button');
    link.href = source; link.download = `${run.id}-${version.id}-source.json`; article.append(link);
  } else if (version.source_url) article.append(make('p', '源码地址未通过当前制作记录绑定校验。', 'creation-error small'));
  if (version.sha256) article.append(make('code', typeof version.sha256 === 'string' ? version.sha256 : JSON.stringify(version.sha256)));
  return article;
}

export function renderCreationReview(document, run) {
  const make = (tag, value, className) => text(document, tag, value, className);
  const section = make('section', undefined, 'creation-review'); section.append(make('h4', '独立审阅意见 · 针对初作'));
  if (!run.review) { section.append(make('p', '尚无独立审阅结果。', 'muted small')); return section; }
  section.append(make('p', run.review.summary ?? '审阅未提供摘要。'));
  if (!run.review.issues.length) section.append(make('p', '本次未提出具体问题；不表示画面已经获得认可。', 'muted small'));
  for (const issue of run.review.issues) {
    const article = make('article', undefined, 'creation-issue');
    if (typeof issue === 'string') article.append(make('p', issue));
    else {
      const item = issue && typeof issue === 'object' ? issue : {problem: String(issue)};
      const window = Number.isFinite(item.start_seconds) && Number.isFinite(item.end_seconds)
        ? `${item.start_seconds.toFixed(3)}—${item.end_seconds.toFixed(3)}秒` : '时间范围未提供';
      article.append(make('strong', `${item.issue_id ?? '画面问题'} · ${window}`),
        make('p', item.problem ?? JSON.stringify(item)));
      if (item.change_target) article.append(make('p', `修改目标：${item.change_target}`));
      if (Array.isArray(item.frame_ids)) article.append(make('p', `依据帧：${item.frame_ids.join('、')}`, 'muted small'));
    }
    section.append(article);
  }
  if (run.review.limitations.length) {
    const details = make('details'); details.append(make('summary', '观察范围与限制'));
    const list = make('ul');
    for (const limitation of run.review.limitations) list.append(make('li', limitation));
    details.append(list); section.append(details);
  }
  section.append(make('p', '修订后只做渲染与技术复验；原审阅意见不代表问题已全部解决，效果需要观看确认。', 'muted small'));
  return section;
}

export function createCreationRunsPanel({document, request, root, storage, ...options}) {
  const get = id => document.getElementById(id), make = (tag, value, className) => text(document, tag, value, className);
  let experiment = null, cards = new Map();
  const controller = new CreationRunsController({request, root, storage, ...options, onChange(state) {
    get('creation-runs-start').disabled = !state.canStart;
    get('creation-runs-start').textContent = state.submitting ? '正在提交…' : '制作并打磨一轮';
    get('creation-runs-refresh').disabled = !state.context || state.loading || state.submitting;
    get('creation-runs-role').disabled = state.submitting;
    const availability = state.loading ? '正在读取制作能力与已有记录…'
      : !state.context ? '请先选择有冻结定义的能力实验。'
      : !state.enabled ? `暂不可制作：${state.disabledReason ?? '能力状态尚未核对'}`
      : state.record ? `本次请求已保存：${state.record.request_id}；刷新只核对进度，不会再提交。`
      : state.runs.some(run => ACTIVE.has(run.state)) ? '已有制作正在执行；请查看原记录，避免重复启动。'
      : '本轮：初作1次、独立审阅1次、有具体问题时最多修改1次、最多渲染2次。';
    get('creation-runs-availability').textContent = availability;
    get('creation-runs-message').textContent = state.error || state.notice;
    get('creation-runs-message').hidden = !(state.error || state.notice);
    get('creation-runs-message').classList.toggle('error', Boolean(state.error));
    const list = get('creation-runs-list');
    if (!state.runs.length) {
      for (const card of cards.values()) for (const video of card.element.querySelectorAll('video')) video.pause();
      cards = new Map(); list.replaceChildren(make('p', state.loading ? '正在读取本定义与输入组的制作记录…'
        : '该定义与输入组尚无制作记录。打开或刷新页面不会调用Agent。', 'muted small'));
      return;
    }
    const currentIds = new Set(state.runs.map(run => run.id));
    for (const [id, card] of cards) if (!currentIds.has(id)) {
      for (const video of card.element.querySelectorAll('video')) video.pause();
      card.element.remove(); cards.delete(id);
    }
    for (const run of state.runs) {
      let card = cards.get(run.id);
      if (!card) {
        const element = make('article', undefined, 'creation-run'), heading = make('header');
        const status = make('strong'), cancel = make('button', '取消本轮', 'button secondary'); cancel.type = 'button';
        cancel.addEventListener('click', () => { void controller.cancel(run.id); });
        heading.append(status, cancel);
        const stage = make('p', undefined, 'muted small'), body = make('div');
        element.append(heading, stage, body); card = {element, status, stage, cancel, body, signature: null};
        if (!cards.size) list.replaceChildren(); cards.set(run.id, card); list.append(element);
      }
      card.status.textContent = creationStateLabel(run.state);
      card.stage.textContent = `${creationStageLabel(run.stage)} · ${run.id}`;
      card.cancel.hidden = !['queued', 'running', 'submission_unknown', 'cancelling'].includes(run.state);
      card.cancel.disabled = run.state === 'cancelling' || state.cancelling.includes(run.id);
      card.cancel.textContent = run.state === 'cancelling' || state.cancelling.includes(run.id) ? '正在取消…' : '取消本轮';
      const signature = JSON.stringify([run.versions, run.review, run.error]);
      if (signature === card.signature) continue;
      card.signature = signature;
      for (const video of card.body.querySelectorAll('video')) video.pause();
      card.body.replaceChildren();
      if (run.error) card.body.append(make('p', `执行错误：${run.error.code ?? 'unknown'}${run.error.message ? ` · ${run.error.message}` : ''}`, 'creation-error small'));
      const versions = make('div', undefined, 'creation-versions');
      for (const id of ['original', 'revised']) {
        const version = run.versions.find(value => value.id === id);
        if (version) versions.append(renderCreationVersion(document, version, run, root));
      }
      if (versions.children.length) card.body.append(versions);
      else card.body.append(make('p', '尚无生成作品；以执行阶段为准。', 'muted small'));
      card.body.append(renderCreationReview(document, run));
    }
  }});
  get('creation-runs-start').addEventListener('click', () => { void controller.start(); });
  get('creation-runs-refresh').addEventListener('click', () => { void controller.refresh(); });
  get('creation-runs-role').addEventListener('change', () => { void controller.select(experiment, get('creation-runs-role').value); });
  const pauseMedia = () => { for (const video of get('creation-runs-list').querySelectorAll('video')) video.pause(); };
  return {
    select(value) {
      const changed = value?.id !== experiment?.id || experimentIdentity(value) !== experimentIdentity(experiment);
      experiment = value;
      if (changed) get('creation-runs-role').value = 'development';
      pauseMedia(); return controller.select(value, get('creation-runs-role').value || 'development');
    },
    pause() { pauseMedia(); controller.pause(); },
    resume() { return controller.resume(); },
    controller,
  };
}
