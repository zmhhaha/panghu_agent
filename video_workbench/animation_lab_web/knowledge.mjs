const ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA = /^[0-9a-f]{64}$/;
const STATES = ['queued', 'uploading', 'prepared', 'submitting', 'running', 'submission_unknown', 'failed', 'published'];
const TERMINAL = new Set(['failed', 'published']);
const PENDING_SCHEMA = 'video.animation-wikiskill-pending.v1';
const text = value => typeof value === 'string' && value.trim().length > 0;
const identifier = value => text(value) && ID.test(value) && !['.', '..'].includes(value);
const hash = value => typeof value === 'string' && SHA.test(value);
const integer = value => Number.isSafeInteger(value) && value > 0;
const timestamp = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 8640000000000;
const requireValue = (condition, message) => { if (!condition) throw new Error(message); };
const copy = value => JSON.parse(JSON.stringify(value));
const storageOrNull = () => { try { return globalThis.sessionStorage ?? null; } catch { return null; } };
const pendingKey = owner => 'video.animation-wikiskill.pending.v1:' + encodeURIComponent(owner);
const fileKey = (skillId, path) => JSON.stringify([skillId, path]);

export class KnowledgeHTTPError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function validJob(value) {
  requireValue(value?.schema === 'video.animation-wikiskill-job.v1' && identifier(value.id) && text(value.owner)
    && typeof value.request_id === 'string' && UUID.test(value.request_id) && STATES.includes(value.state)
    && identifier(value.snapshot_id) && hash(value.snapshot_sha256) && hash(value.input_sha256)
    && identifier(value.operation_id) && (value.job_id === null || text(value.job_id))
    && (value.release_id === null || identifier(value.release_id))
    && (value.error === null || text(value.error?.code)) && timestamp(value.created_at) && timestamp(value.updated_at)
    && (value.state !== 'published' || identifier(value.release_id)), '发布任务响应不完整');
  return value;
}

function validSummary(value) {
  requireValue(value && identifier(value.id) && integer(value.sequence) && timestamp(value.created_at)
    && typeof value.summary === 'string' && hash(value.sha256) && identifier(value.snapshot_id) && hash(value.snapshot_sha256)
    && Number.isSafeInteger(value.package_count) && value.package_count > 0
    && Number.isSafeInteger(value.file_count) && value.file_count >= value.package_count, '整库版本目录响应不完整');
  return value;
}

function validCatalogue(value) {
  requireValue(value?.schema === 'video.animation-wikiskill-catalogue.v1' && text(value.owner_key) && value.owner_key.length <= 300
    && text(value.csrf_token) && Array.isArray(value.releases) && value.releases.length <= 5 && Array.isArray(value.jobs)
    && ((value.active_release_id === null && value.active_release_sha256 === null)
      || (identifier(value.active_release_id) && hash(value.active_release_sha256))), '知识库目录响应不完整');
  const ids = new Set(); let previous = Infinity;
  for (const release of value.releases) {
    validSummary(release);
    requireValue(!ids.has(release.id) && release.sequence < previous, '整库版本目录身份或顺序不一致');
    ids.add(release.id); previous = release.sequence;
  }
  if (value.active_release_id !== null) requireValue(value.releases.some(row => row.id === value.active_release_id
    && row.sha256 === value.active_release_sha256), '当前线上版本与目录不一致');
  for (const job of value.jobs) validJob(job);
  requireValue(new Set(value.jobs.map(job => job.id)).size === value.jobs.length, '发布任务目录身份重复');
  return value;
}

function safePath(path) {
  return text(path) && !path.includes('\\') && !path.includes('?') && !path.includes('#') && !path.includes('%')
    && !path.startsWith('/') && path.split('/').every(part => part && part !== '.' && part !== '..')
    && (path === 'SKILL.md' || /^(references|assets|scripts)\//.test(path));
}

function validRelease(value, summary) {
  requireValue(value?.schema === 'video.animation-wikiskill-release.v1' && identifier(value.id) && text(value.owner)
    && integer(value.sequence) && identifier(value.job_id) && identifier(value.snapshot_id) && hash(value.snapshot_sha256)
    && hash(value.input_sha256) && timestamp(value.created_at) && typeof value.summary === 'string' && hash(value.sha256)
    && Array.isArray(value.packages) && value.packages.length > 0 && Array.isArray(value.index)
    && Array.isArray(value.exclusions) && value.exclusions.every(row => text(row?.record_id) && text(row?.reason))
    && Array.isArray(value.limitations) && value.limitations.every(row => typeof row === 'string'), 'WikiSkill 正式版本响应不完整');
  const ids = new Set(); let files = 0;
  for (const pkg of value.packages) {
    requireValue(pkg?.schema === 'video.animation-skill-package.v1' && identifier(pkg.skill_id) && !ids.has(pkg.skill_id)
      && integer(pkg.version) && ['foundation', 'expression'].includes(pkg.kind) && text(pkg.name) && text(pkg.description)
      && pkg.files && typeof pkg.files === 'object' && !Array.isArray(pkg.files) && text(pkg.files['SKILL.md'])
      && pkg.files_sha256 && typeof pkg.files_sha256 === 'object' && hash(pkg.sha256)
      && Object.keys(pkg.files).length === Object.keys(pkg.files_sha256).length
      && Object.entries(pkg.files).every(([path, content]) => safePath(path) && text(content) && hash(pkg.files_sha256[path])),
    'WikiSkill 文件包响应不完整');
    ids.add(pkg.skill_id); files += Object.keys(pkg.files).length;
  }
  requireValue(value.index.length === ids.size && new Set(value.index.map(row => row.skill_id)).size === ids.size
    && value.index.every(row => ids.has(row.skill_id) && (row.parent_skill_id === null || ids.has(row.parent_skill_id))
      && Array.isArray(row.source_record_ids) && row.source_record_ids.every(text)), 'WikiSkill 层级目录不完整');
  const parents = new Map(value.index.map(row => [row.skill_id, row.parent_skill_id]));
  for (const id of ids) {
    const seen = new Set(); let next = id;
    while (next !== null) { requireValue(!seen.has(next), 'WikiSkill 层级目录形成循环'); seen.add(next); next = parents.get(next); }
  }
  if (summary) requireValue(value.id === summary.id && value.sequence === summary.sequence && value.created_at === summary.created_at
    && value.sha256 === summary.sha256 && value.snapshot_id === summary.snapshot_id && value.snapshot_sha256 === summary.snapshot_sha256
    && value.packages.length === summary.package_count && files === summary.file_count, '读取产物与所选整库版本不一致');
  return value;
}

function validActivation(value, pending) {
  requireValue(value?.schema === 'video.animation-wikiskill-activation.v1' && value.request_id === pending.request_id
    && value.release_id === pending.release_id && value.release_sha256 === pending.release_sha256 && timestamp(value.activated_at),
  '线上版本切换回执与原请求不一致');
  return value;
}

function validBody(raw, kind) {
  requireValue(typeof raw === 'string' && raw.length <= 2000, '操作请求必须先冻结');
  let value; try { value = JSON.parse(raw); } catch { throw new Error('原操作请求无法解析'); }
  requireValue(value && typeof value.request_id === 'string' && UUID.test(value.request_id)
    && Object.keys(value).sort().join(',') === (kind === 'publish' ? 'request_id' : 'release_id,request_id')
    && (kind === 'publish' || identifier(value.release_id)), '原操作请求字段不完整');
  return value;
}

export function createKnowledgeClient({fetch: request = globalThis.fetch, root = new URL('./', import.meta.url)} = {}) {
  const base = new URL(root);
  requireValue(['http:', 'https:'].includes(base.protocol) && !base.search && !base.hash && !base.username && !base.password,
    '知识接口地址不正确');
  const endpoint = new URL('api/method-lab/knowledge/library', base).href;
  let credential = null, catalogueRead = 0;
  async function send(suffix, raw, owner) {
    const options = {method: raw === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error'};
    if (raw !== undefined) {
      requireValue(credential && credential.owner_key === owner, '请先刷新，取得当前账号的操作凭证');
      options.headers = {'Content-Type': 'application/json', 'X-Animation-Lab-Token': credential.csrf_token}; options.body = raw;
    }
    const response = await request(endpoint + suffix, options);
    let value;
    try { value = await response.json(); }
    catch { throw new KnowledgeHTTPError(response.ok ? 0 : response.status, '服务响应无法解析，请核对原操作'); }
    if (!response.ok) throw new KnowledgeHTTPError(response.status, String(value?.error ?? '知识库请求失败'));
    return value;
  }
  const idRead = (prefix, id, uuid = false) => {
    requireValue(uuid ? typeof id === 'string' && UUID.test(id) : identifier(id), '记录 ID 不正确');
    return send(prefix + encodeURIComponent(id));
  };
  return {
    async catalogue() {
      const generation = ++catalogueRead; credential = null;
      const value = validCatalogue(await send(''));
      if (generation === catalogueRead) credential = {owner_key: value.owner_key, csrf_token: value.csrf_token};
      return value;
    },
    release: id => idRead('/releases/', id), job: id => idRead('/jobs/', id),
    request: id => idRead('/requests/', id, true), activationRequest: id => idRead('/activation-requests/', id, true),
    publish: (raw, owner) => { validBody(raw, 'publish'); return send('/jobs', raw, owner); },
    activate: (raw, owner) => { validBody(raw, 'activate'); return send('/active', raw, owner); },
  };
}

export function knowledgeFiles(release, query = '') {
  if (!release) return [];
  const terms = String(query).trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const rows = [], packages = new Map(release.packages.map(pkg => [pkg.skill_id, pkg]));
  const add = (parent, depth) => {
    for (const branch of release.index.filter(row => row.parent_skill_id === parent)) {
      const pkg = packages.get(branch.skill_id);
      for (const path of Object.keys(pkg.files).sort((a, b) => a === 'SKILL.md' ? -1 : b === 'SKILL.md' ? 1 : a.localeCompare(b))) {
        const content = pkg.files[path], haystack = [pkg.name, pkg.description, path, content].join('\n').toLocaleLowerCase();
        if (terms.every(term => haystack.includes(term))) rows.push({key: fileKey(pkg.skill_id, path), skillId: pkg.skill_id,
          name: pkg.name, description: pkg.description, path, content, depth, sha256: pkg.files_sha256[path]});
      }
      add(branch.skill_id, depth + 1);
    }
  };
  add(null, 0); return rows;
}

export class KnowledgeController {
  constructor({client, storage = storageOrNull(), uuid = () => globalThis.crypto.randomUUID(), onChange = () => {},
    schedule = (fn, ms) => setTimeout(fn, ms), unschedule = id => clearTimeout(id)} = {}) {
    this.client = client; this.storage = storage; this.uuid = uuid; this.onChange = onChange;
    this.schedule = schedule; this.unschedule = unschedule; this.timer = null;
    this.catalogueRead = 0; this.releaseRead = 0; this.jobRead = 0;
    this.state = {catalogue: null, ownerKey: null, selectedReleaseId: null, release: null, selectedFileKey: null, query: '',
      pending: null, job: null, storageBlocked: false, catalogueBusy: false, releaseBusy: false,
      actionBusy: false, recoveryBusy: false, jobBusy: false, message: '', error: false};
  }
  emit() { this.onChange(this.state); }
  message(message, error = false) { this.state.message = message; this.state.error = error; }
  get files() { return knowledgeFiles(this.state.release, this.state.query); }
  get working() { return !!this.state.job && !TERMINAL.has(this.state.job.state); }
  get actionAvailable() { return !!this.state.catalogue && !this.state.catalogueBusy && !this.state.actionBusy
    && !this.state.recoveryBusy && !this.state.pending && !this.working && !this.state.storageBlocked; }
  _cancelTimer() { if (this.timer !== null) this.unschedule(this.timer); this.timer = null; }
  stop() { ++this.catalogueRead; ++this.releaseRead; ++this.jobRead; this._cancelTimer(); }
  _schedule() {
    this._cancelTimer();
    if (this.working && this.state.catalogue) this.timer = this.schedule(() => { this.timer = null; this.pollJob(); }, 4000);
  }
  _restore(owner) {
    this.state.pending = null; this.state.storageBlocked = false;
    try {
      requireValue(this.storage && typeof this.storage.getItem === 'function', '会话存储不可用，暂不能发起操作；仍可阅读已发布版本。');
      const raw = this.storage.getItem(pendingKey(owner)); if (raw === null) return;
      const value = JSON.parse(raw); requireValue(['publish', 'activate'].includes(value.kind), '原操作类型不正确');
      const body = validBody(value.raw, value.kind);
      requireValue(value.schema === PENDING_SCHEMA && value.owner_key === owner && value.request_id === body.request_id
        && (value.kind !== 'activate' || (value.release_id === body.release_id && hash(value.release_sha256)))
        && (value.job_id === undefined || identifier(value.job_id)), '本浏览器保存的原操作无法核验，已阻止另建操作。');
      this.state.pending = Object.freeze(copy(value));
    } catch (error) { this.state.storageBlocked = true; this.message(error.message, true); }
  }
  _persist(value) {
    requireValue(this.storage && !this.state.storageBlocked, '原操作不能可靠保存，本次未提交。');
    const raw = JSON.stringify(value), key = pendingKey(value.owner_key);
    try { this.storage.setItem(key, raw); requireValue(this.storage.getItem(key) === raw, '会话记录未保存'); }
    catch { throw new Error('浏览器未能保存原请求，本次没有提交。请恢复会话存储后再试。'); }
  }
  _clear(pending) {
    try { this.storage.removeItem(pendingKey(pending.owner_key)); requireValue(this.storage.getItem(pendingKey(pending.owner_key)) === null, '记录未清理'); }
    catch { this.state.storageBlocked = true; }
    this.state.pending = null;
  }
  _selectFirstFile() { if (!this.files.some(file => file.key === this.state.selectedFileKey)) this.state.selectedFileKey = this.files[0]?.key ?? null; }
  async refresh(preferredReleaseId = null) {
    const generation = ++this.catalogueRead; ++this.releaseRead; ++this.jobRead; this._cancelTimer();
    this.state.catalogueBusy = true; this.state.releaseBusy = false; this.state.jobBusy = false; this.emit();
    try {
      const catalogue = validCatalogue(await this.client.catalogue());
      if (generation !== this.catalogueRead) return false;
      if (catalogue.owner_key !== this.state.ownerKey) {
        this.state.ownerKey = catalogue.owner_key; this.state.release = null; this.state.selectedReleaseId = null;
        this.state.selectedFileKey = null; this.state.query = ''; this.state.job = null;
        this.state.actionBusy = false; this.state.recoveryBusy = false; this._restore(catalogue.owner_key);
      } else if (this.state.storageBlocked) this._restore(catalogue.owner_key);
      this.state.catalogue = copy(catalogue); this.state.catalogueBusy = false;
      const pending = this.state.pending;
      this.state.job = pending?.kind === 'publish' ? catalogue.jobs.find(job => job.id === pending.job_id) ?? null
        : catalogue.jobs.find(job => !TERMINAL.has(job.state)) ?? catalogue.jobs[0] ?? null;
      if (pending?.job_id && this.state.job && TERMINAL.has(this.state.job.state)) this._acceptJob(this.state.job, pending);
      const selected = [preferredReleaseId, this.state.selectedReleaseId, catalogue.active_release_id, catalogue.releases[0]?.id]
        .find(id => catalogue.releases.some(row => row.id === id)) ?? null;
      if (selected) await this.selectRelease(selected);
      else {
        this.state.release = null; this.state.selectedReleaseId = null; this.state.selectedFileKey = null;
        if (!this.state.storageBlocked) this.message(pending ? '已找回原操作，请核对原请求。' : '尚无成功发布的知识库版本。');
      }
      if (generation !== this.catalogueRead) return false;
      if (pending?.job_id && !this.state.job) await this.pollJob(pending.job_id);
      else this._schedule();
      this.emit(); return true;
    } catch (error) {
      if (generation !== this.catalogueRead) return false;
      this.state.catalogue = null; this.state.release = null; this.state.selectedReleaseId = null; this.state.selectedFileKey = null;
      this.message('知识库目录读取失败：' + error.message + (this.state.pending ? '。原请求仍保留。' : ''), true); return false;
    } finally { if (generation === this.catalogueRead) { this.state.catalogueBusy = false; this.emit(); } }
  }
  async selectRelease(id) {
    const summary = this.state.catalogue?.releases.find(row => row.id === id);
    if (!summary || this.state.catalogueBusy) return false;
    const generation = ++this.releaseRead, owner = this.state.ownerKey;
    this.state.selectedReleaseId = id; this.state.release = null; this.state.selectedFileKey = null; this.state.releaseBusy = true; this.emit();
    try {
      const release = validRelease(await this.client.release(id), summary);
      if (generation !== this.releaseRead || owner !== this.state.ownerKey) return false;
      this.state.release = copy(release); this._selectFirstFile();
      if (!this.state.storageBlocked) this.message(this.state.pending ? '原操作尚待核对；当前仍可阅读已发布版本。'
        : '正在阅读整库第 ' + release.sequence + ' 版；搜索覆盖本版全部知识文件。');
      return true;
    } catch (error) {
      if (generation !== this.releaseRead || owner !== this.state.ownerKey) return false;
      this.message('所选产物未能核验：' + error.message, true); return false;
    } finally { if (generation === this.releaseRead && owner === this.state.ownerKey) { this.state.releaseBusy = false; this.emit(); } }
  }
  search(query) { this.state.query = String(query); this._selectFirstFile(); this.emit(); }
  selectFile(key) { if (!this.files.some(file => file.key === key)) return false; this.state.selectedFileKey = key; this.emit(); return true; }
  async publish() { return this._start('publish'); }
  async activate() {
    const release = this.state.catalogue?.releases.find(row => row.id === this.state.selectedReleaseId);
    if (!release || !this.state.release || release.id === this.state.catalogue.active_release_id) return false;
    return this._start('activate', release);
  }
  async _start(kind, release) {
    if (!this.actionAvailable) return false;
    let pending;
    try {
      const body = {request_id: this.uuid(), ...(kind === 'activate' ? {release_id: release.id} : {})};
      const raw = JSON.stringify(body); validBody(raw, kind);
      pending = Object.freeze({schema: PENDING_SCHEMA, kind, owner_key: this.state.ownerKey, request_id: body.request_id, raw,
        ...(kind === 'activate' ? {release_id: release.id, release_sha256: release.sha256} : {})});
      this._persist(pending);
    } catch (error) { this.message(error.message, true); this.emit(); return false; }
    this.state.pending = pending; return this._send(pending);
  }
  async retryPending() {
    if (!this.state.pending || this.state.pending.job_id || !this.state.catalogue || this.state.catalogueBusy
      || this.state.actionBusy || this.state.recoveryBusy || this.state.storageBlocked) return false;
    return this._send(this.state.pending);
  }
  _acceptJob(job, pending) {
    validJob(job); requireValue(job.request_id === pending.request_id, '发布任务与原请求不一致');
    this.state.job = copy(job);
    if (TERMINAL.has(job.state)) this._clear(pending);
    else {
      const known = Object.freeze({...pending, job_id: job.id});
      try { this._persist(known); this.state.pending = known; }
      catch { this.state.pending = pending; }
    }
    this.message(job.state === 'failed' ? '本次发布失败，当前线上版本保持不变。'
      : job.state === 'published' ? '新 WikiSkill 已发布，正在读取线上目录。' : '原发布任务已接通；后续只读取它的进度。', job.state === 'failed');
    this._schedule();
  }
  async _send(pending) {
    if (this.state.actionBusy || this.state.recoveryBusy || pending.owner_key !== this.state.ownerKey || !this.state.catalogue) return false;
    this.state.actionBusy = true; this.message(pending.kind === 'publish' ? '正在提交原发布请求…' : '正在切换线上版本…'); this.emit();
    try {
      const value = await (pending.kind === 'publish' ? this.client.publish(pending.raw, pending.owner_key) : this.client.activate(pending.raw, pending.owner_key));
      if (pending.owner_key !== this.state.ownerKey || this.state.pending !== pending) return false;
      if (pending.kind === 'publish') {
        this._acceptJob(value, pending);
        if (value.state === 'published') await this.refresh(value.release_id);
      } else {
        validActivation(value, pending); this._clear(pending); await this.refresh(value.release_id);
        this.message('原切换请求已完成；当前线上标记来自服务器最新目录。');
      }
      return true;
    } catch (error) {
      if (pending.owner_key !== this.state.ownerKey || this.state.pending !== pending) return false;
      if ([400, 401, 403, 404, 409, 413, 415, 422, 429].includes(error.status)) {
        this._clear(pending); this.message('操作被服务拒绝，未另建任务：' + error.message, true);
      } else this.message('原操作结果尚未确认。请求已保存在本浏览器；请核对原请求，或明确原样重送。', true);
      return false;
    } finally { if (pending.owner_key === this.state.ownerKey) { this.state.actionBusy = false; this.emit(); } }
  }
  async checkPending() {
    const pending = this.state.pending;
    if (!pending || !this.state.catalogue || this.state.catalogueBusy || this.state.actionBusy || this.state.recoveryBusy) return false;
    this.state.recoveryBusy = true; this.message('正在只读核对原操作，不会新建任务。'); this.emit();
    try {
      const value = await (pending.kind === 'publish' ? this.client.request(pending.request_id) : this.client.activationRequest(pending.request_id));
      if (pending.owner_key !== this.state.ownerKey || this.state.pending !== pending) return false;
      if (pending.kind === 'publish') { this._acceptJob(value, pending); if (value.state === 'published') await this.refresh(value.release_id); }
      else { validActivation(value, pending); this._clear(pending); await this.refresh(value.release_id); this.message('原切换已完成，当前线上标记已重新读取。'); }
      return true;
    } catch (error) {
      if (pending.owner_key !== this.state.ownerKey || this.state.pending !== pending) return false;
      this.message(error.status === 404 ? '服务器暂未找到原请求。原请求继续保留，不会自动重送或另建任务。'
        : '原操作仍未核实：' + error.message + '。原请求继续保留。', true); return false;
    } finally { if (pending.owner_key === this.state.ownerKey) { this.state.recoveryBusy = false; this.emit(); } }
  }
  async pollJob(id = this.state.job?.id) {
    if (!identifier(id) || !this.state.catalogue || this.state.jobBusy) return false;
    this._cancelTimer();
    const generation = ++this.jobRead, owner = this.state.ownerKey, prior = this.state.job;
    this.state.jobBusy = true; this.emit();
    try {
      const job = validJob(await this.client.job(id));
      if (generation !== this.jobRead || owner !== this.state.ownerKey) return false;
      requireValue(job.id === id && (!prior || prior.id !== id || (job.request_id === prior.request_id
        && job.owner === prior.owner && job.snapshot_id === prior.snapshot_id && job.snapshot_sha256 === prior.snapshot_sha256
        && job.input_sha256 === prior.input_sha256 && job.operation_id === prior.operation_id)), '原发布任务身份发生变化');
      const pending = this.state.pending;
      if (pending?.kind === 'publish' && (pending.job_id === id || (!pending.job_id && pending.request_id === job.request_id)))
        this._acceptJob(job, pending);
      else this.state.job = copy(job);
      if (job.state === 'published') { this.state.jobBusy = false; await this.refresh(job.release_id); }
      else if (job.state === 'failed') this.message('本次发布失败，当前线上版本保持不变。', true);
      return true;
    } catch (error) {
      if (generation !== this.jobRead || owner !== this.state.ownerKey) return false;
      this.message('原任务进度暂未核实：' + error.message + '。不会重建任务。', true); return false;
    } finally {
      if (generation === this.jobRead && owner === this.state.ownerKey) { this.state.jobBusy = false; this._schedule(); this.emit(); }
    }
  }
}

const STATE_LABELS = {queued: '等待提炼', uploading: '准备输入', prepared: '输入已准备', submitting: '提交原任务',
  running: 'Agent 提炼中', submission_unknown: '核对原任务', failed: '发布失败', published: '已发布'};
const timeLabel = value => new Date(value * 1000).toLocaleString('zh-CN', {hour12: false});

// Text-only Markdown subset: model-authored HTML and scripts are never interpreted.
export function renderKnowledgeMarkdown(document, target, source) {
  const make = (tag, content, className) => { const node = document.createElement(tag); node.textContent = content;
    if (className) node.className = className; return node; };
  const lines = source.split(/\r?\n/); let paragraph = [], list = null;
  const flush = () => { if (paragraph.length) { target.append(make('p', paragraph.join('\n'))); paragraph = []; } list = null; };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if ((i === 0 && line === '---') || /^\s*```/.test(line)) {
      flush(); const frontmatter = i === 0 && line === '---', block = [line];
      while (++i < lines.length) { block.push(lines[i]); if (frontmatter ? lines[i] === '---' : /^\s*```/.test(lines[i])) break; }
      target.append(make('pre', block.join('\n'), frontmatter ? 'frontmatter' : undefined)); continue;
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) { flush(); target.append(make('h' + Math.min(heading[1].length, 4), heading[2])); continue; }
    const item = /^\s*(?:[-*+]\s+|\d+\.\s+)(.+)$/.exec(line);
    if (item) { if (paragraph.length) flush(); if (!list) { list = document.createElement('ul'); target.append(list); } list.append(make('li', item[1])); continue; }
    if (!line.trim()) { flush(); continue; }
    if (list) list = null; paragraph.push(line);
  }
  flush();
}

export function mountKnowledgePage(document, controller) {
  const get = id => document.getElementById(id);
  const el = (tag, content, className) => { const node = document.createElement(tag);
    if (content !== undefined) node.textContent = String(content); if (className) node.className = className; return node; };
  const button = (label, action, className = 'secondary') => { const node = el('button', label, 'button ' + className);
    node.type = 'button'; node.addEventListener('click', action); return node; };
  function render(state) {
    const catalogue = state.catalogue, release = state.release, files = controller.files;
    get('knowledge-sync-status').textContent = state.catalogueBusy ? '正在读取知识库…' : catalogue
      ? catalogue.active_release_id ? '线上 WikiSkill 已就绪' : '尚无线上 WikiSkill' : '知识库暂不可用';
    get('knowledge-refresh').disabled = false;
    get('knowledge-publish').disabled = !controller.actionAvailable;
    get('knowledge-publish').textContent = state.actionBusy && state.pending?.kind === 'publish' ? '正在提交…' : '发布新版本';
    get('knowledge-message').textContent = state.message; get('knowledge-message').classList.toggle('error', state.error);
    get('knowledge-pending').hidden = !state.pending || (!!state.pending.job_id && state.job?.id === state.pending.job_id);
    get('knowledge-pending-check').disabled = !catalogue || state.catalogueBusy || state.actionBusy || state.recoveryBusy;
    get('knowledge-pending-retry').disabled = !catalogue || state.catalogueBusy || state.actionBusy || state.recoveryBusy
      || state.storageBlocked || !!state.pending?.job_id;
    const jobPanel = get('knowledge-job'); jobPanel.replaceChildren(); jobPanel.hidden = !state.job;
    if (state.job) {
      const job = state.job, heading = el('div', undefined, 'job-heading');
      const title = el('h2', '知识库发布任务'); title.id = 'knowledge-job-title';
      heading.append(title, el('span', STATE_LABELS[job.state], 'job-state ' + job.state)); jobPanel.append(heading);
      jobPanel.append(el('p', job.state === 'failed' ? '当前线上版本继续可用。效果库与已发布知识不会因本次失败而清除。'
        : job.state === 'published' ? '整库产物已发布；正式制作可按当前线上版本查阅。'
        : '正在从已冻结的效果库提炼 WikiSkill。浏览与刷新只读取进度，不会重跑样片或另建任务。', 'muted'));
      if (job.error) jobPanel.append(el('p', '任务记录：' + job.error.code, 'muted small'));
      jobPanel.append(el('p', '更新于 ' + timeLabel(job.updated_at), 'job-time'));
      if (!TERMINAL.has(job.state)) {
        const check = button(state.jobBusy ? '正在核对…' : '核对原任务进度', () => controller.pollJob(job.id)); check.disabled = state.jobBusy;
        jobPanel.append(check);
      }
    }
    const select = get('knowledge-release'); select.replaceChildren();
    if (!catalogue?.releases.length) { const option = el('option', state.catalogueBusy ? '正在读取…' : '尚无成功发布版本'); option.value = ''; select.append(option); }
    for (const row of catalogue?.releases ?? []) { const option = el('option', '第 ' + row.sequence + ' 版'
      + (row.id === catalogue.active_release_id ? ' · 当前线上' : '') + ' · ' + timeLabel(row.created_at)); option.value = row.id; select.append(option); }
    select.value = state.selectedReleaseId ?? ''; select.disabled = !catalogue?.releases.length || state.catalogueBusy;
    const summary = catalogue?.releases.find(row => row.id === state.selectedReleaseId);
    get('knowledge-release-meta').textContent = summary ? summary.package_count + ' 项方法 · ' + summary.file_count + ' 个文件'
      + (summary.id === catalogue.active_release_id ? ' · 当前线上版本' : ' · 历史只读版本') : '';
    const activate = get('knowledge-activate'); activate.disabled = !controller.actionAvailable || !release || release.id === catalogue?.active_release_id;
    activate.textContent = release?.id === catalogue?.active_release_id ? '当前线上版本' : '设为当前线上版本';
    const search = get('knowledge-search'); if (search.value !== state.query) search.value = state.query; search.disabled = !release || state.releaseBusy;
    get('knowledge-search-status').textContent = release ? '本版匹配 ' + files.length + ' / ' + knowledgeFiles(release).length + ' 个文件' : '';
    const tree = get('knowledge-files'); tree.replaceChildren(); let group = null, skillId = null;
    for (const file of files) {
      if (file.skillId !== skillId) { skillId = file.skillId; group = el('section', undefined, 'skill-branch depth-' + Math.min(file.depth, 2));
        group.append(el('h3', file.name)); const links = el('div', undefined, 'file-links'); group.append(links); tree.append(group); group = links; }
      const node = button(file.path, () => controller.selectFile(file.key), 'file-link' + (file.path === 'SKILL.md' ? '' : ' reference'));
      if (file.key === state.selectedFileKey) node.setAttribute('aria-current', 'page'); group.append(node);
    }
    if (!files.length) tree.append(el('p', release ? '本版没有匹配的知识文件。' : '发布成功后在这里显示分层方法与参考文件。', 'muted small'));
    const detail = get('knowledge-detail'); detail.replaceChildren();
    const selected = files.find(file => file.key === state.selectedFileKey);
    if (state.releaseBusy) detail.append(el('h2', '正在读取所选整库版本…'), el('p', '完成核验后再展示本版内容。', 'muted'));
    else if (selected) {
      detail.append(el('p', '整库第 ' + release.sequence + ' 版' + (release.id === catalogue.active_release_id ? ' · 当前线上' : ' · 历史版本'), 'eyebrow'),
        el('h2', selected.name), el('p', selected.path, 'file-path'));
      const body = el('article', undefined, 'skill-document'); renderKnowledgeMarkdown(document, body, selected.content); detail.append(body);
      if (release.limitations.length || release.exclusions.length) {
        const notes = el('details', undefined, 'release-notes'); notes.append(el('summary', '本版适用边界与未纳入内容'));
        const list = el('ul');
        for (const limitation of release.limitations) list.append(el('li', limitation));
        for (const exclusion of release.exclusions) list.append(el('li', exclusion.reason));
        notes.append(list); detail.append(notes);
      }
    } else if (release) detail.append(el('h2', '没有匹配的知识文件'), el('p', '换一个关键词，或清空搜索阅读本版全部内容。', 'muted'));
    else if (catalogue && !catalogue.releases.length) detail.append(el('h2', '尚无成功发布的 WikiSkill'),
      el('p', '效果库的经验还没有发布为正式知识。点击右上方“发布新版本”，由 Agent 整体提炼并校验；成功后才会在这里展示。', 'muted'));
    else detail.append(el('h2', state.catalogueBusy ? '正在读取知识库…' : '所选版本暂不可读'),
      el('p', '请手动刷新或重新选择版本。不会退回未发布的工作知识。', 'muted'));
  }
  controller.onChange = render;
  get('knowledge-refresh').addEventListener('click', () => controller.refresh());
  get('knowledge-publish').addEventListener('click', () => controller.publish());
  get('knowledge-release').addEventListener('change', () => controller.selectRelease(get('knowledge-release').value));
  get('knowledge-activate').addEventListener('click', () => controller.activate());
  get('knowledge-search').addEventListener('input', () => controller.search(get('knowledge-search').value));
  get('knowledge-pending-check').addEventListener('click', () => controller.checkPending());
  get('knowledge-pending-retry').addEventListener('click', () => controller.retryPending());
  document.defaultView?.addEventListener('pagehide', () => controller.stop());
  render(controller.state); controller.refresh(); return controller;
}

if (typeof document !== 'undefined' && document.getElementById('knowledge-app')) {
  mountKnowledgePage(document, new KnowledgeController({client: createKnowledgeClient()}));
}
