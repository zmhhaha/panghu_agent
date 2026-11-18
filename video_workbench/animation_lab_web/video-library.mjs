import {VIDEO_API, MAX_REFERENCE_BYTES, buildVideoProject, buildVideoExperiment, validateReferenceFile,
  videoAssetUrl, videoCandidates, latestVideoExperiment, videoProjectState, videoState,
  videoIsActive, videoDefaultCandidate, videoCandidateRole, videoProjectMatches,
  videoStopReason, videoRequestPath, videoArtifactPath, videoId, rows, text} from './video-model.mjs';

const PENDING_KEY = 'video.video-learning.pending.v1';
const ownerKey = value => typeof value === 'string' && value.length > 0 && value.length <= 300;
const pendingKey = owner => PENDING_KEY + ':' + encodeURIComponent(owner);
export function videoPendingStorage(indexedDB = globalThis.indexedDB) {
  let opened;
  const database = () => opened ??= new Promise((resolve, reject) => {
    if (!indexedDB?.open) { reject(Error('浏览器不支持保存原请求，请启用网站存储后再提交')); return; }
    let request;
    try { request = indexedDB.open('video.video-learning', 1); }
    catch { reject(Error('无法打开本地请求存储，请启用网站存储后再提交')); return; }
    request.onupgradeneeded = () => { if (!request.result.objectStoreNames.contains('pending')) request.result.createObjectStore('pending'); };
    request.onsuccess = () => {
      request.result.onversionchange = () => request.result.close(); resolve(request.result);
    };
    request.onerror = () => reject(Error('无法打开本地请求存储，请启用网站存储后再提交'));
    request.onblocked = () => reject(Error('本地请求存储正在更新，请关闭其他视频学习页面后重试'));
  });
  const transaction = async (mode, operation) => {
    const db = await database();
    return new Promise((resolve, reject) => {
      let tx, result;
      try {
        tx = db.transaction('pending', mode);
        const request = operation(tx.objectStore('pending'));
        request.onsuccess = () => { result = request.result; };
        tx.oncomplete = () => resolve(result);
        tx.onabort = tx.onerror = () => reject(Error('无法保存或读取原请求，请检查网站存储空间后重试'));
      } catch { reject(Error('无法使用本地请求存储，请刷新后重试')); }
    });
  };
  const compareAndSetItem = async (key, previous, value) => {
    const db = await database();
    return new Promise((resolve, reject) => {
      let conflict = false;
      const tx = db.transaction('pending', 'readwrite'), store = tx.objectStore('pending'), request = store.get(key);
      request.onsuccess = () => {
        if ((request.result ?? null) !== previous) { conflict = true; tx.abort(); return; }
        if (value === null) store.delete(key); else store.put(value, key);
      };
      tx.oncomplete = () => resolve();
      tx.onabort = tx.onerror = () => {
        const error = Error(conflict ? '另一个页面保存了待核对请求，请刷新核对后再提交' : '无法保存原请求，请检查网站存储空间后重试');
        if (conflict) error.code = 'pending_conflict'; reject(error);
      };
    });
  };
  return {getItem: key => transaction('readonly', store => store.get(key)), compareAndSetItem,
    setItem: (key, value) => transaction('readwrite', store => store.put(value, key)),
    removeItem: key => transaction('readwrite', store => store.delete(key))};
}
const browserStorage = () => videoPendingStorage();
const ERROR_MESSAGES = {
  video_learning_unavailable: '视频学习执行器尚未配置。', video_reference_invalid: '参考视频无法读取，请选择有效的 MP4 或 WebM。',
  video_project_active: '项目仍在运行，请先停止或等待本次实验结束。', video_budget_exhausted: '本次实验预算已用完。',
  video_reference_too_large: '参考视频超过大小上限，请缩小后再上传。', video_request_conflict: '同一请求编号的内容不一致，请核对原请求。',
  video_learning_baseline_not_rendered: '这份作品尚未生成视频，请技术重试或从零重做。',
  video_learning_candidate_not_rendered: '这份作品尚未生成视频，暂时不能采用为默认作品。',
  video_learning_retry_input_frozen: '技术重试沿用原始需求和预算；需要更改时，请继续优化或从零重做。',
};
const MODE_NAMES = {continue: '继续优化', fresh: '从零重做', retry: '重试失败的实验'};
function errorMessage(error) {
  return ERROR_MESSAGES[error?.code] ?? (error?.status === 403 ? '登录或页面授权已失效，请重新登录后刷新。'
    : error?.status === 413 ? '提交内容过大，请缩小参考视频后重试。' : text(error?.message) || '请求未完成，请刷新核对。');
}
function pendingOperation(value) {
  const valid = value && value.schema === 'video.video-learning-pending.v1'
    && ownerKey(value.owner_key)
    && typeof value.path === 'string' && /^api\/video-lab\/(?:references(?:\/[A-Za-z0-9_.:%-]+\/analyses(?:\/[A-Za-z0-9_.:%-]+\/stop)?)?|projects(?:\/[A-Za-z0-9_.:%-]+\/(?:experiments|adopt|stop))?)$/.test(value.path)
    && typeof value.body === 'object' && value.body !== null && !Array.isArray(value.body);
  if (!valid) return null;
  if (value.path === VIDEO_API + 'references') {
    if (![VIDEO_API + 'projects', VIDEO_API + 'references/pending/analyses'].includes(value.continuation?.path) || !value.continuation?.body?.request_id
      || typeof value.body.content_base64 !== 'string' || value.body.content_base64.length > Math.ceil(MAX_REFERENCE_BYTES / 3) * 4) return null;
  } else if (value.continuation !== undefined) return null;
  return value;
}
export async function referenceBody(file, requestId) {
  const description = validateReferenceFile(file), bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes.byteLength !== file.size || bytes.byteLength > MAX_REFERENCE_BYTES) throw Error('参考文件在读取时发生变化，请重新选择');
  let binary = '';
  for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return {request_id: requestId, ...description, content_base64: btoa(binary)};
}
export async function analyzeVideoSelection(controller, fields, file, onReference) {
  const success = await controller.analyze(fields, file);
  if (success && controller.state.analysis?.reference_id) onReference(videoId(controller.state.analysis.reference_id));
  return success;
}
export class VideoLibraryController {
  constructor({request, root, storage = null, uuid = () => globalThis.crypto.randomUUID(),
    schedule = (fn, ms) => setTimeout(fn, ms), unschedule = id => clearTimeout(id), onChange = () => {}}) {
    this.request = request; this.root = root; this.storage = storage; this.uuid = uuid;
    this.schedule = schedule; this.unschedule = unschedule; this.onChange = onChange; this.timer = null; this.closed = false;
    this.readEpoch = 0; this.detailEpoch = 0; this.ownerEpoch = 0; this.ownerLoad = null; this.writeReady = false;
    this.state = {catalog: null, detail: null, busy: false, message: '', pending: null, pendingInvalid: false, pendingLoading: false, owner_key: null, lastRead: null};
  }
  changed() { this.onChange(this.state); }
  enabled() { return this.writeReady && this.state.catalog?.availability?.enabled === true; }
  locked() { return this.state.busy || Boolean(this.state.pending) || this.state.pendingInvalid || this.state.pendingLoading; }
  setMessage(value) { this.state.message = value; this.changed(); }
  clearAnalysis() { this.state.analysis = null; this.changed(); }
  async useOwner(owner) {
    if (this.state.owner_key === owner && !this.state.pendingInvalid) return this.ownerLoad;
    const epoch = ++this.ownerEpoch;
    this.writeReady = false;
    this.state.owner_key = owner; this.state.detail = null; this.detailEpoch++;
    this.state.analysis = null;
    this.state.pending = null; this.state.pendingInvalid = false; this.state.pendingLoading = true;
    this.ownerLoad = (async () => {
      try {
        const raw = await this.storage?.getItem(pendingKey(owner));
        if (epoch !== this.ownerEpoch || this.closed) return;
        if (raw !== null && raw !== undefined) {
          const saved = pendingOperation(JSON.parse(raw));
          if (!saved || saved.owner_key !== owner) throw Error('invalid');
          this.state.pending = saved;
        }
      } catch {
        if (epoch === this.ownerEpoch && !this.closed) {
          this.state.pendingInvalid = true;
          this.state.message = '本浏览器保存的原请求无法读取。请先核对原实验并检查网站存储，当前不会创建新任务。';
        }
      } finally { if (epoch === this.ownerEpoch) this.state.pendingLoading = false; }
    })();
    return this.ownerLoad;
  }
  async setPending(operation, owner = this.state.owner_key) {
    if (!ownerKey(owner)) throw Error('请先刷新，取得当前账号的授权与身份');
    const epoch = this.ownerEpoch;
    const previous = this.state.pending?.owner_key === owner ? JSON.stringify(this.state.pending) : null;
    const save = async value => {
      try {
        if (this.storage?.compareAndSetItem) await this.storage.compareAndSetItem(pendingKey(owner), previous, value);
        else if (value === null) await this.storage?.removeItem(pendingKey(owner));
        else await this.storage?.setItem(pendingKey(owner), value);
      } catch (error) {
        if (error?.code === 'pending_conflict') {
          if (owner === this.state.owner_key && epoch === this.ownerEpoch) this.state.pendingInvalid = true;
          throw error;
        }
        throw Error('无法保存本次请求，请释放浏览器存储空间后再提交');
      }
    };
    if (operation) {
      const value = {schema: 'video.video-learning-pending.v1', owner_key: owner, ...structuredClone(operation)};
      await save(JSON.stringify(value));
      if (owner === this.state.owner_key && epoch === this.ownerEpoch && !this.closed) this.state.pending = value;
    } else {
      await save(null);
      if (owner === this.state.owner_key && epoch === this.ownerEpoch && !this.closed) this.state.pending = null;
    }
    return owner === this.state.owner_key && epoch === this.ownerEpoch && !this.closed;
  }
  poll() {
    if (this.timer !== null) this.unschedule(this.timer);
    this.timer = null;
    if (!this.closed && (videoIsActive(this.state.detail) || rows(this.state.catalog?.projects).some(videoIsActive)
      || rows(this.state.catalog?.analyses).some(row => !['completed', 'failed', 'cancelled'].includes(row.state)) || this.state.pending)) {
      this.timer = this.schedule(() => { this.timer = null; return this.refresh(); }, 5000);
    }
  }
  async refresh() {
    const epoch = ++this.readEpoch;
    try {
      const catalog = await this.request(VIDEO_API + 'catalog');
      if (epoch !== this.readEpoch || this.closed) return this.state.catalog;
      if (!ownerKey(catalog?.owner_key) || !text(catalog?.csrf_token)) throw Error('目录缺少当前账号的授权信息，请刷新核对');
      await this.useOwner(catalog.owner_key);
      if (epoch !== this.readEpoch || this.closed) return this.state.catalog;
      this.state.catalog = catalog; this.writeReady = !this.state.pendingInvalid;
      const detailId = this.state.detail?.id, detailEpoch = this.detailEpoch;
      if (detailId) {
        const detail = await this.request(videoRequestPath(detailId));
        if (epoch !== this.readEpoch || this.closed) return this.state.catalog;
        if (detailEpoch === this.detailEpoch && this.state.detail?.id === detailId) this.state.detail = detail;
      }
      const recovered = await this.reconcilePending();
      if (epoch !== this.readEpoch || this.closed) return this.state.catalog;
      if (recovered && !Array.isArray(recovered.experiments)) {
        const detailEpoch = this.detailEpoch, owner = this.state.owner_key;
        const detail = await this.request(videoRequestPath(recovered.id));
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
  async reconcilePending() {
    const pending = this.state.pending, requestId = pending?.body?.request_id;
    if (pending?.path === VIDEO_API + 'references') return;
    const projects = [...rows(this.state.catalog?.projects), ...(this.state.detail ? [this.state.detail] : [])];
    if (pending && !requestId) {
      const match = /^api\/video-lab\/projects\/([^/]+)\/(stop|adopt)$/.exec(pending.path);
      const project = match && projects.find(row => row.id === decodeURIComponent(match[1]));
      if (project && (match[2] === 'stop' && !videoIsActive(project)
        || match[2] === 'adopt' && project.adopted_candidate_id === pending.body.candidate_id)) await this.setPending(null);
      if (match) return;
    }
    if (/^api\/video-lab\/references\/[^/]+\/analyses/.test(pending?.path ?? '')) {
      const found = rows(this.state.catalog?.analyses).find(row => requestId ? row.request_id === requestId
        : pending.path.includes('/' + row.id + '/stop') && ['completed', 'failed', 'cancelled'].includes(row.state));
      if (found) { await this.setPending(null); return; }
    }
    if (!requestId) return;
    const found = projects.find(project => project.request_id === requestId || project.latest_experiment_request_id === requestId
      || rows(project.experiments).some(experiment => experiment.request_id === requestId));
    if (found) {
      const owner = this.state.owner_key, epoch = this.ownerEpoch;
      await this.setPending(null);
      if (owner !== this.state.owner_key || epoch !== this.ownerEpoch || this.closed) return;
      if (!this.state.detail) this.state.detail = found; return found;
    }
  }
  async open(projectId) {
    const epoch = ++this.detailEpoch, owner = this.state.owner_key;
    try {
      const detail = await this.request(videoRequestPath(projectId));
      if (epoch !== this.detailEpoch || owner !== this.state.owner_key || this.closed) return null;
      this.state.detail = detail; this.state.message = '';
    } catch (error) { if (epoch === this.detailEpoch) this.state.message = errorMessage(error); }
    this.changed(); this.poll(); return this.state.detail;
  }
  async send(path, body, continuation) {
    if (!this.writeReady || this.locked()) return false;
    this.state.busy = true; this.changed();
    let saved;
    try { saved = await this.setPending({path, body, ...(continuation ? {continuation} : {})}); }
    finally { this.state.busy = false; this.changed(); }
    return saved && this.writeReady ? this.replayPending() : false;
  }
  async replayPending() {
    if (!this.writeReady || this.state.busy || !this.state.pending || this.state.pending.owner_key !== this.state.owner_key) return false;
    let operation = structuredClone(this.state.pending);
    const epoch = this.ownerEpoch, current = () => epoch === this.ownerEpoch && operation.owner_key === this.state.owner_key && !this.closed;
    this.state.busy = true; this.state.message = ''; this.changed();
    try {
      let result = await this.request(operation.path, {method: 'POST', body: JSON.stringify(operation.body)});
      if (!current()) return false;
      if (operation.path === VIDEO_API + 'references') {
        const referenceId = result?.reference?.id ?? result?.reference_id ?? result?.id;
        videoId(referenceId);
        const next = structuredClone(operation.continuation);
        if (next.path === VIDEO_API + 'references/pending/analyses') next.path = VIDEO_API + 'references/' + encodeURIComponent(referenceId) + '/analyses';
        else next.body.reference_ids = [referenceId];
        if (!await this.setPending(next, operation.owner_key) || !current() || !this.writeReady) return false;
        operation = structuredClone(this.state.pending);
        this.changed();
        result = await this.request(operation.path, {method: 'POST', body: JSON.stringify(operation.body)});
        if (!current()) return false;
      }
      if (/^api\/video-lab\/references\/[^/]+\/analyses/.test(operation.path)) {
        await this.setPending(null, operation.owner_key);
        if (current()) { this.state.analysis = result; void this.refresh(); }
        return true;
      }
      const projectId = result?.project?.id ?? result?.project_id ?? result?.id;
      videoId(projectId);
      await this.setPending(null, operation.owner_key);
      if (!current()) return false;
      const detailEpoch = ++this.detailEpoch;
      const detail = result?.project ?? (Array.isArray(result?.experiments) ? result : await this.request(videoRequestPath(projectId)));
      if (!current() || detailEpoch !== this.detailEpoch) return false;
      this.state.detail = detail;
      return true;
    } catch (error) {
      if (!current()) return false;
      // Only definitive client rejection clears the immutable paid-operation key.
      if (Number.isInteger(error?.status) && error.status >= 400 && error.status < 500 && error.status !== 408) await this.setPending(null, operation.owner_key);
      if (!current()) return false;
      this.state.message = errorMessage(error) + (this.state.pending ? ' 原请求已保留，请核对或重送，勿重新提交。' : '');
      return false;
    } finally {
      this.state.busy = false; this.changed(); this.poll();
    }
  }
  async start(fields, file) {
    if (!this.enabled() || this.locked()) return false;
    // Validate the inexpensive form before uploading any pixels.
    const project = buildVideoProject(fields, ['reference-validation'], this.uuid());
    if (fields.reference_id) {
      project.reference_ids = [videoId(fields.reference_id)];
      return this.send(VIDEO_API + 'projects', project);
    }
    const owner = this.state.owner_key, epoch = this.ownerEpoch;
    this.state.busy = true; this.state.message = '正在保存参考视频…'; this.changed();
    let body;
    try { body = await referenceBody(file, this.uuid()); }
    catch (error) { this.state.message = errorMessage(error); return false; }
    finally { this.state.busy = false; this.changed(); }
    if (owner !== this.state.owner_key || epoch !== this.ownerEpoch || !this.enabled()) { this.setMessage('当前登录身份或授权已变化，请刷新后重新提交'); return false; }
    project.reference_ids = [];
    return this.send(VIDEO_API + 'references', body, {path: VIDEO_API + 'projects', body: project});
  }
  async analyze(fields, file) {
    if (!this.enabled() || this.locked()) return false;
    this.clearAnalysis();
    const goal = text(fields.goal).trim() || '识别适合学习的画面技法、运动机制与节奏片段。';
    const minutes = Number(fields.analysis_minutes ?? 10);
    if (goal.length > 8000 || !Number.isInteger(minutes) || minutes < 1 || minutes > 30) throw Error('分析预算应为 1–30 分钟，目标不超过 8000 字');
    const body = {request_id: this.uuid(), goal, max_seconds: minutes * 60};
    if (fields.reference_id) return this.send(VIDEO_API + 'references/' + encodeURIComponent(videoId(fields.reference_id)) + '/analyses', body);
    const owner = this.state.owner_key, epoch = this.ownerEpoch;
    this.state.busy = true; this.changed();
    let upload;
    try { upload = await referenceBody(file, this.uuid()); }
    finally { this.state.busy = false; this.changed(); }
    if (owner !== this.state.owner_key || epoch !== this.ownerEpoch) return false;
    return this.send(VIDEO_API + 'references', upload, {path: VIDEO_API + 'references/pending/analyses', body});
  }
  async stopAnalysis(analysis) {
    if (this.locked() || !analysis?.id || !analysis.reference_id) return false;
    return this.send(VIDEO_API + 'references/' + encodeURIComponent(videoId(analysis.reference_id)) + '/analyses/'
      + encodeURIComponent(videoId(analysis.id)) + '/stop', {});
  }
  async experiment(fields) {
    const detail = this.state.detail;
    if (!detail?.id || !this.enabled() || this.locked() || videoIsActive(detail)) return false;
    const base = videoDefaultCandidate(detail, fields.base_candidate_id);
    if ((fields.mode ?? 'continue') === 'continue'
      && (!videoMediaUrl(base, detail, this.root) || (base?.role ?? base?.work_kind) === 'transfer')) {
      this.setMessage('请先选择一份已实际生成视频的复现作品。只有源码时，可以技术重试或从零重做。'); return false;
    }
    return this.send(videoRequestPath(detail.id, 'experiments'), buildVideoExperiment(fields, this.uuid()));
  }
  async stop() {
    if (!this.state.detail?.id || this.locked() || !videoIsActive(this.state.detail)) return false;
    return this.send(videoRequestPath(this.state.detail.id, 'stop'), {});
  }
  async adopt(candidateId) {
    if (!this.state.detail?.id || this.locked() || videoIsActive(this.state.detail)) return false;
    videoId(candidateId);
    const candidate = videoCandidates(this.state.detail).find(row => row.id === candidateId);
    if (!candidate || !videoMediaUrl(candidate, this.state.detail, this.root)
      || (candidate.role ?? candidate.work_kind) === 'transfer') return false;
    return this.send(videoRequestPath(this.state.detail.id, 'adopt'), {candidate_id: candidateId});
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
  const url = videoAssetUrl(value, root); if (!url) return null;
  const link = el(doc, 'a', 'button secondary', label); link.href = url;
  if (download) link.download = ''; else { link.target = '_blank'; link.rel = 'noopener'; }
  return link;
}
function candidateArtifact(candidate, kind, projectId) {
  const direct = candidate?.[kind + '_url'];
  if (direct) return direct;
  const row = candidate?.artifacts?.[kind];
  if (typeof row === 'string') return videoArtifactPath(projectId, row);
  if (row?.url) return row.url;
  if (row?.id) return videoArtifactPath(projectId, row.id);
  const id = candidate?.[kind + '_artifact_id'];
  return id ? videoArtifactPath(projectId, id) : null;
}
export function videoMediaUrl(candidate, project, root) {
  return videoAssetUrl(candidateArtifact(candidate, 'media', project?.id)
    ?? candidate?.media_url, root);
}
function referenceUrl(reference, root) {
  return videoAssetUrl(reference?.media_url ?? reference?.image_url ?? reference?.url
    ?? (reference?.id ? VIDEO_API + 'references/' + encodeURIComponent(videoId(reference.id)) + '/media' : null), root);
}
function videoFigure(doc, title, url, note, root, segment = null) {
  const figure = el(doc, 'figure', 'video-figure'), caption = el(doc, 'figcaption', '', title), well = el(doc, 'div', 'video-image-well');
  if (url) {
    const player = el(doc, 'video'); player.src = url; player.controls = true; player.preload = 'metadata'; player.playsInline = true;
    player.setAttribute('aria-label', title);
    if (segment) {
      player.addEventListener('loadedmetadata', () => { player.currentTime = segment.start; });
      player.addEventListener('timeupdate', () => {
        if (player.currentTime >= segment.end) player.pause();
      });
      player.addEventListener('play', () => { if (player.currentTime >= segment.end || player.currentTime < segment.start) player.currentTime = segment.start; });
    }
    well.append(player); const open = externalArtifactLink(doc, '打开完整媒体', url, root); if (open) caption.append(open);
  } else well.append(el(doc, 'p', '', '视频尚未生成。完成后会展示实际渲染的完整片段。'));
  figure.append(caption, well); if (note) figure.append(el(doc, 'footer', '', note)); return figure;
}
function comparisonControls(doc, referenceFigure, outputFigure, segment) {
  const reference = referenceFigure.querySelector('video'), output = outputFigure.querySelector('video');
  if (!reference || !output || !segment || !(segment.end > segment.start)) return null;
  const group = el(doc, 'div', 'video-comparison-controls'), status = el(doc, 'span', 'small muted', '以片段起点对齐；可分别播放原音轨。');
  const timeline = el(doc, 'input'); timeline.type = 'range'; timeline.min = '0'; timeline.max = String(segment.end - segment.start); timeline.step = String(1/24); timeline.value = '0'; timeline.setAttribute('aria-label', '共同片段时间');
  const pause = () => { reference.pause(); output.pause(); };
  const seek = seconds => { reference.currentTime = segment.start + seconds; output.currentTime = seconds; timeline.value = String(seconds); };
  const play = button(doc, '同时播放画面对照', async () => {
    pause(); seek(Number(timeline.value) >= segment.end - segment.start - .05 ? 0 : Number(timeline.value));
    reference.muted = true; output.muted = true;
    try { await Promise.all([reference.play(), output.play()]); }
    catch { pause(); status.textContent = '请等待视频加载，或使用各自播放器查看。'; }
  });
  timeline.addEventListener('input', () => { pause(); seek(Number(timeline.value)); });
  output.addEventListener('timeupdate', () => {
    if (output.paused) return;
    const time = Math.min(segment.end - segment.start, output.currentTime); timeline.value = String(time);
    if (Math.abs(reference.currentTime - segment.start - time) > .2) reference.currentTime = segment.start + time;
    if (time >= segment.end - segment.start - .03) pause();
  });
  output.addEventListener('ended', pause);
  group.append(play, button(doc, '暂停对照', pause), timeline, status); return group;
}
function jsonSection(doc, title, value, open = false) {
  const section = el(doc, 'details', 'technical-details'); section.open = open;
  section.append(el(doc, 'summary', '', title), el(doc, 'pre', 'video-json', JSON.stringify(value, null, 2))); return section;
}
function reviewPanel(doc, review) {
  const panel = el(doc, 'section', 'panel'); panel.append(el(doc, 'h3', '', '独立比较与剩余问题'));
  if (!review) { panel.append(el(doc, 'p', 'muted', '尚无评审结果。视频存在不代表效果已达标。')); return panel; }
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
export function renderVideoCard(doc, project, {root, onOpen}) {
  const article = el(doc, 'article', 'effect-card video-card'), candidate = videoDefaultCandidate(project), url = videoAssetUrl(candidateArtifact(candidate, 'poster', project.id) ?? project.poster_url ?? project.preview_url, root);
  if (url) { const image = el(doc, 'img', 'video-card-image'); image.src = url; image.alt = text(project.title); image.loading = 'lazy'; article.append(image); }
  else article.append(el(doc, 'div', 'video-card-placeholder', '▧'));
  const body = el(doc, 'div', 'card-body'); body.append(button(doc, project.title || '未命名学习项目', () => onOpen(project.id), {className: 'effect-title'}));
  body.append(el(doc, 'p', 'card-goal', project.goal), el(doc, 'span', 'badge', videoState(videoProjectState(project))));
  if (project.adopted_candidate_id) body.append(el(doc, 'span', 'badge', '已选默认作品'));
  body.append(el(doc, 'div', 'card-meta', (project.experiments_count ?? rows(project.experiments).length) + ' 次实验'
    + (videoCandidates(project).length ? ' · ' + videoCandidates(project).length + ' 份作品' : ''))); article.append(body); return article;
}
export function renderVideoDetail(doc, project, {root, selection = null, onSelect = () => {}, onBack = () => {}, onStop = () => {}, onExperiment = () => {}, onAdopt = () => {}, onInspect = null, locked = false}) {
  const fragment = doc.createDocumentFragment(), header = el(doc, 'div', 'detail-heading');
  const intro = el(doc, 'div'); intro.append(el(doc, 'p', 'eyebrow', '视频学习项目'), el(doc, 'h1', '', project.title), el(doc, 'p', 'muted', project.goal));
  header.append(intro); fragment.append(button(doc, '返回视频学习库', onBack), header);
  const current = latestVideoExperiment(project), active = videoIsActive(project), candidate = videoDefaultCandidate(project, selection);
  const selectedMediaUrl = videoMediaUrl(candidate, project, root);
  const toolbar = el(doc, 'div', 'video-details-toolbar');
  if (active) toolbar.append(button(doc, ['stopping', 'cancelling'].includes(videoProjectState(project)) ? '正在核实停止' : '停止本次实验', onStop,
    {className: 'button danger', disabled: locked || ['stopping', 'cancelling'].includes(videoProjectState(project))}));
  else {
    if (candidate && selectedMediaUrl && (candidate.role ?? candidate.work_kind) !== 'transfer') toolbar.append(button(doc, '继续优化', () => onExperiment('continue'), {className: 'button primary', disabled: locked}));
    toolbar.append(button(doc, '从零重做', () => onExperiment('fresh'), {disabled: locked}));
    if (videoProjectState(project) === 'failed') toolbar.append(button(doc, '技术重试', () => onExperiment('retry'), {disabled: locked}));
  }
  const status = el(doc, 'div', 'video-status'); status.append(el(doc, 'strong', '', videoState(videoProjectState(project))));
  const reason = videoStopReason(current?.stop_reason ?? project.stop_reason); if (reason) status.append(el(doc, 'p', 'small muted', reason));
  status.append(el(doc, 'p', 'small muted', active ? '本次需求、参考和知识版本已经冻结。运行中只查看或停止，新的意见用于下一次实验。'
    : '内部最佳与默认作品分别保存。采用一幅视频不会发布知识，也不代表运动已经验证。'));
  if (typeof current?.frozen?.use_wikiskill === 'boolean') status.append(el(doc, 'p', 'small muted',
    current.frozen.use_wikiskill === false ? '本次未加载已发布 WikiSkill。'
      : current.frozen.library_binding?.release_id ? '本次固定 WikiSkill 版本：' + current.frozen.library_binding.release_id
        : '本次选择加载 WikiSkill，但尚无可用发布版，未注入技法正文。'));
  const failure = text(current?.error_code) || text(current?.error?.code) || text(current?.error);
  if (failure) status.append(el(doc, 'p', 'small muted', '执行信息：' + failure));
  fragment.append(toolbar, status);
  const candidates = videoCandidates(project), versions = el(doc, 'div', 'video-versions');
  for (let i = 0; i < candidates.length; i++) {
    const row = candidates[i], labels = [videoCandidateRole(row) + ' ' + (i + 1)];
    if (!videoMediaUrl(row, project, root)) labels.push('视频未完成');
    if (row.id === project.best_candidate_id) labels.push('内部最佳');
    if (row.id === project.adopted_candidate_id) labels.push('默认作品');
    const choice = button(doc, labels.join(' · '), () => onSelect(row.id)); choice.setAttribute('aria-pressed', String(candidate?.id === row.id)); versions.append(choice);
  }
  if (candidates.length) fragment.append(versions);
  const references = rows(project.references), reference = references[0] ?? (rows(project.reference_ids)[0] ? {id: project.reference_ids[0]} : null);
  const work = el(doc, 'div', 'video-workspace');
  const frozen = current?.frozen ?? {}, segment = {start: Number(frozen.start_seconds ?? project.start_seconds ?? 0), end: Number(frozen.end_seconds ?? project.end_seconds ?? reference?.duration_seconds ?? 0)};
  const referenceFigure = videoFigure(doc, '参考片段', referenceUrl(reference, root),
    (reference?.file_name ?? '') + ' · ' + segment.start + '–' + segment.end + ' 秒', root, segment);
  const videoUrl = selectedMediaUrl;
  const outputFigure = videoFigure(doc, candidate ? videoCandidateRole(candidate) : '实际动画结果', videoUrl,
    videoUrl ? '由 Agent 源码经 HyperFrames 渲染的完整片段。' : '源码与视频分别保存；尚未渲染的源码不能作为画面验收结果。', root);
  work.append(referenceFigure, outputFigure); fragment.append(work);
  const comparison = comparisonControls(doc, referenceFigure, outputFigure, segment); if (comparison) fragment.append(comparison);
  if (candidate) {
    const actions = el(doc, 'div', 'video-result-links');
    if (videoUrl) { const link = externalArtifactLink(doc, '下载视频', videoUrl, root, true); if (link) actions.append(link); }
    for (const [kind, label] of [['source', '下载绘制源码'], ['receipt', '下载渲染回执']]) {
      const link = externalArtifactLink(doc, label, candidateArtifact(candidate, kind, project.id), root, true); if (link) actions.append(link);
    }
    if (!active && videoUrl && (candidate.role ?? candidate.work_kind) !== 'transfer') actions.append(button(doc, candidate.id === project.adopted_candidate_id ? '已作为默认作品' : '采用为默认作品',
      () => onAdopt(candidate.id), {disabled: locked || candidate.id === project.adopted_candidate_id}));
    fragment.append(actions);
  }
  const evidence = el(doc, 'div', 'video-evidence-grid'); evidence.append(reviewPanel(doc, candidate?.review ?? candidate?.assessment));
  const scope = el(doc, 'section', 'panel'); scope.append(el(doc, 'h3', '', '本次验证范围'));
  const transfer = candidates.filter(row => (row.role ?? row.work_kind) === 'transfer');
  scope.append(el(doc, 'p', 'muted', '实际抽帧支持画面与关键状态对照；连续运动、声音与音画配合须实际观看或听审，不能由静帧补签。'),
    el(doc, 'p', '', transfer.length ? '已有换内容验证作品，请切换作品查看实际结果与评审。' : '换内容验证尚无产物；复现进展不能替代迁移通过。'));
  if ((candidate?.role ?? candidate?.work_kind) === 'transfer') {
    const experiment = rows(project.experiments).find(row => rows(row.candidates).some(item => item.id === candidate.id));
    if (experiment?.transfer_task) scope.append(jsonSection(doc, '本次换内容验证的任务', experiment.transfer_task, true));
  }
  if (candidate?.observation) scope.append(jsonSection(doc, '实际帧时间与覆盖', candidate.observation));
  if (candidate?.evidence_scope) scope.append(jsonSection(doc, '验证范围记录', candidate.evidence_scope));
  if (candidate?.method) scope.append(jsonSection(doc, '查看本次形成的方法', candidate.method));
  if (candidate?.observation_receipts) scope.append(jsonSection(doc, '参考与结果的实际输入记录', candidate.observation_receipts));
  evidence.append(scope); fragment.append(evidence);
  const history = el(doc, 'details', 'technical-details'); history.append(el(doc, 'summary', '', '实验历史与冻结需求（' + rows(project.experiments).length + '）'));
  const historyList = el(doc, 'ol', 'video-history');
  for (const experiment of rows(project.experiments)) {
    const item = el(doc, 'li'), content = el(doc, 'div');
    content.append(el(doc, 'strong', '', (MODE_NAMES[experiment.mode] ?? '首次学习') + ' · ' + videoState(experiment.state)));
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
        loaded = true; const output = el(doc, 'pre', 'video-json', '正在读取…'); inspection.append(output);
        try { output.textContent = JSON.stringify(await onInspect(experiment.id), null, 2); }
        catch (error) { output.textContent = errorMessage(error); loaded = false; }
      }); content.append(inspection);
    }
    item.append(content, el(doc, 'small', '', typeof experiment.created_at === 'number'
      ? new Date(experiment.created_at * 1000).toLocaleString() : experiment.created_at ?? experiment.id)); historyList.append(item);
  }
  history.append(historyList); fragment.append(history); return fragment;
}

export function mountVideoLibrary(doc, {root = new URL('./', globalThis.location?.href), fetcher = globalThis.fetch,
  storage = browserStorage(), uuid, schedule, unschedule, URLObject = globalThis.URL} = {}) {
  const $ = id => doc.getElementById(id), app = $('video-library-app'); if (!app) return null;
  let formMode = null, shown = 'home', selectedId = null, previewUrl = null, detailSignature = '', controller;
  let referencesSignature = '';
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
  const show = mode => { shown = mode; $('video-home').hidden = mode !== 'home'; $('video-detail').hidden = mode !== 'detail'; $('video-form-panel').hidden = mode !== 'form'; };
  const renderCards = () => {
    const projects = rows(controller.state.catalog?.projects), query = $('video-search').value;
    const filtered = projects.filter(project => videoProjectMatches(project, query));
    $('video-cards').replaceChildren(...filtered.map(project => renderVideoCard(doc, project, {root, onOpen: async id => {
      await controller.open(id); if (controller.state.detail?.id === id) { show('detail'); detailSignature = ''; render(controller.state); }
    }})));
    $('video-count').textContent = projects.length + ' 个学习项目';
    $('video-empty').hidden = projects.length > 0;
    if (projects.length && !filtered.length) $('video-cards').append(el(doc, 'p', 'muted', '没有符合条件的学习项目。'));
  };
  const render = state => {
    $('video-message').hidden = !state.message; $('video-message').textContent = state.message;
    $('video-pending').hidden = !state.pending; $('video-pending-retry').disabled = state.busy;
    $('video-new').disabled = !controller.enabled() || controller.locked(); $('video-empty-new').disabled = $('video-new').disabled;
    $('video-sync').textContent = state.busy ? '正在提交…' : state.lastRead ? '最近读取：' + state.lastRead.toLocaleTimeString() : '正在读取视频学习库…';
    $('video-availability').textContent = state.catalog?.availability?.enabled === false ? text(state.catalog.availability.reason) || '视频学习执行器尚未启用。' : '';
    $('video-submit').disabled = !controller.enabled() || controller.locked();
    for (const element of $('video-form').elements) element.disabled = state.busy || Boolean(state.pending)
      || formMode === 'retry' && element.id !== 'video-submit';
    $('video-submit').disabled = !controller.enabled() || controller.locked();
    renderCards();
    const refs = rows(state.catalog?.references), refSignature = JSON.stringify(refs.map(row => [row.id, row.file_name]));
    if (referencesSignature !== refSignature) {
      referencesSignature = refSignature; const select = $('video-saved-reference'), previous = select.value;
      const blank = el(doc, 'option', '', '使用新上传的视频'); blank.value = ''; select.replaceChildren(blank);
      for (const ref of refs) { const option = el(doc, 'option', '', ref.file_name + ' · ' + Number(ref.duration_seconds).toFixed(1) + ' 秒'); option.value = ref.id; select.append(option); }
      if (refs.some(ref => ref.id === previous)) select.value = previous;
    }
    const renderAnalyses = host => {
      host.replaceChildren();
      for (const analysis of rows(state.catalog?.analyses)) {
        const section = el(doc, 'section', 'panel');
        section.append(el(doc, 'h3', '', (refs.find(row => row.id === analysis.reference_id)?.file_name ?? '参考视频') + ' · ' + videoState(analysis.state)));
        if (!['completed', 'failed', 'cancelled'].includes(analysis.state)) section.append(button(doc, '停止本次分析', () => void controller.stopAnalysis(analysis), {disabled: controller.locked() || analysis.state === 'stopping'}));
        if (analysis.error) section.append(el(doc, 'p', 'small muted', '分析执行信息：' + (analysis.error.code ?? analysis.error)));
        for (const [candidateIndex, candidate] of rows(analysis.candidates ?? analysis.candidate_segments).entries()) {
          const row = el(doc, 'div', 'video-analysis-candidate');
          row.append(el(doc, 'strong', '', candidate.title), el(doc, 'p', 'small muted', candidate.start_seconds + '–' + candidate.end_seconds + ' 秒 · ' + candidate.learning_goal),
            button(doc, '选择这个片段', () => {
              openForm(); $('video-reference-id').value = analysis.reference_id; $('video-saved-reference').value = analysis.reference_id;
              $('video-reference').required = false; $('video-name').value = candidate.title; $('video-goal').value = candidate.learning_goal;
              $('video-start').value = candidate.start_seconds; $('video-end').value = candidate.end_seconds;
              $('video-analysis-id').value = analysis.id; $('video-segment-index').value = candidateIndex;
              controller.setMessage('已选择候选片段。请核对目标和预算，再点击“开始视频学习”。');
            }, {disabled: controller.locked()})); section.append(row);
        }
        section.append(jsonSection(doc, '实际观察范围与完整分析', analysis)); host.append(section);
      }
    };
    renderAnalyses($('video-analyses')); renderAnalyses($('video-form-analyses'));
    $('video-analyze').disabled = !controller.enabled() || controller.locked();
    if (state.detail && shown === 'detail') {
      const signature = JSON.stringify({detail: state.detail, locked: controller.locked(), selectedId});
      if (signature !== detailSignature) {
        detailSignature = signature;
        $('video-detail').replaceChildren(renderVideoDetail(doc, state.detail, {root, selection: selectedId, locked: controller.locked(),
          onSelect: id => { selectedId = id; detailSignature = ''; render(state); }, onBack: () => show('home'),
          onStop: () => void controller.stop(), onExperiment: mode => openForm(mode), onAdopt: id => void controller.adopt(id),
          onInspect: experimentId => request(videoRequestPath(state.detail.id, 'experiments/' + encodeURIComponent(videoId(experimentId)) + '/input'))}));
      }
    }
  };
  const openForm = (mode = null) => {
    if (controller.locked()) return;
    controller.clearAnalysis();
    formMode = mode; clearPreview(); $('video-form').reset(); $('video-reference-preview').replaceChildren(); $('video-reference-preview').hidden = true;
    $('video-reference-id').value = ''; $('video-saved-reference').value = '';
    $('video-new-fields').hidden = Boolean(mode); $('video-feedback-label').hidden = !mode || mode === 'retry';
    $('video-budget-fields').hidden = mode === 'retry';
    $('video-form-title').textContent = mode ? MODE_NAMES[mode] : '新建视频学习';
    $('video-submit').textContent = mode ? '开始' + MODE_NAMES[mode] : '开始视频学习';
    $('video-form-note').hidden = !mode;
    $('video-form-note').textContent = mode === 'fresh' ? '保留同一参考和学习目标，从零创作。旧源码、布局和专用方法不会作为起点；可在下方填写精简的问题。'
      : mode === 'retry' ? '保留原来的目标、参考、标准、预算和确切知识版本，重试失败环节。需要改变要求或预算时，请发起继续优化或从零重做。'
        : '保留同一参考和学习目标，在已有作品上继续改善。下方意见会冻结进下一次实验，不修改历史需求。';
    for (const id of ['video-name', 'video-goal', 'video-reference', 'video-width', 'video-height', 'video-start', 'video-end']) $(id).required = !mode;
    $('video-feedback').required = mode === 'continue';
    $('video-use-wikiskill').checked = mode === 'retry' ? latestVideoExperiment(controller.state.detail)?.frozen?.use_wikiskill !== false : true;
    $('video-skill-help').hidden = mode === 'retry';
    show('form'); render(controller.state); (mode === 'retry' ? $('video-submit') : mode ? $('video-feedback') : $('video-name')).focus();
  };
  controller = new VideoLibraryController({request, root, storage, uuid, schedule, unschedule, onChange: render});
  $('video-refresh').addEventListener('click', () => void controller.refresh());
  $('video-new').addEventListener('click', () => openForm()); $('video-empty-new').addEventListener('click', () => openForm());
  $('video-pending-retry').addEventListener('click', () => void controller.replayPending());
  $('video-form-close').addEventListener('click', () => { clearPreview(); show(formMode ? 'detail' : 'home'); detailSignature = ''; render(controller.state); });
  $('video-search').addEventListener('input', renderCards);
  $('video-reference').addEventListener('change', () => {
    controller.clearAnalysis();
    $('video-reference-id').value = ''; $('video-saved-reference').value = ''; $('video-reference').required = true;
    clearPreview(); const host = $('video-reference-preview'); host.replaceChildren(); host.hidden = true;
    const file = $('video-reference').files?.[0]; if (!file) return;
    try {
      validateReferenceFile(file); previewUrl = URLObject.createObjectURL(file);
      const image = el(doc, 'video'); image.src = previewUrl; image.controls = true; image.preload = 'metadata'; image.setAttribute('aria-label', '待上传的参考视频');
      host.append(image, el(doc, 'p', 'small muted', file.name)); host.hidden = false; controller.setMessage('');
    } catch (error) { $('video-reference').value = ''; controller.setMessage(errorMessage(error)); }
  });
  $('video-saved-reference').addEventListener('change', () => {
    controller.clearAnalysis();
    $('video-analysis-id').value = ''; $('video-segment-index').value = '';
    const referenceId = $('video-saved-reference').value; $('video-reference-id').value = referenceId;
    $('video-reference').required = !referenceId;
    if (referenceId) { $('video-reference').value = ''; clearPreview(); $('video-reference-preview').hidden = true; }
  });
  for (const id of ['video-start', 'video-end', 'video-reference']) $(id).addEventListener('input', () => {
    $('video-analysis-id').value = ''; $('video-segment-index').value = '';
  });
  $('video-analyze').addEventListener('click', async () => {
    try {
      await analyzeVideoSelection(controller, {reference_id: $('video-reference-id').value, goal: $('video-goal').value,
        analysis_minutes: $('video-analysis-minutes').value}, $('video-reference').files?.[0], referenceId => {
          $('video-reference-id').value = referenceId; $('video-reference').required = false;
        });
    } catch (error) {controller.setMessage(errorMessage(error));}
  });
  $('video-form').addEventListener('submit', async event => {
    event.preventDefault(); if (controller.locked()) return;
    const fields = Object.fromEntries(new FormData($('video-form'))); fields.use_wikiskill = $('video-use-wikiskill').checked; fields.learn_audio = $('video-learn-audio').checked;
    if (formMode === 'continue') fields.base_candidate_id = videoDefaultCandidate(controller.state.detail, selectedId)?.id;
    try {
      const success = formMode ? await controller.experiment({...fields, mode: formMode}) : await controller.start(fields, $('video-reference').files?.[0]);
      if (success && controller.state.detail) { clearPreview(); show('detail'); selectedId = null; detailSignature = ''; render(controller.state); void controller.refresh(); }
    } catch (error) { controller.setMessage(errorMessage(error)); }
  });
  const ready = controller.refresh();
  return {controller, ready, destroy: () => { clearPreview(); controller.destroy(); }};
}
if (typeof document !== 'undefined' && document.getElementById('video-library-app')) mountVideoLibrary(document);
