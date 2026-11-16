const API = 'api/method-lab/';
const ACTIVE = new Set(['queued', 'running', 'cancelling', 'submission_unknown', 'planning', 'creating', 'reviewing', 'rendering']);
const SHA256 = /^[0-9a-f]{64}$/i;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const REASONS = {
  active: '仍有实验正在执行，请等待结束后再移除。',
  uncertain: '执行状态尚未核实，请先刷新核对。',
  stop_unproven: '尚未确认执行器已经停止，暂不能移除。',
  published: '包含已发布的版本，暂不能移除。',
  protected: '包含已发布或被正式制作引用的版本，暂不能移除。',
  already_trashed: '已在回收站中，请刷新查看。',
  referenced: '已被正式制作引用，暂不能移除。',
  formal_reference: '已被正式制作引用，暂不能移除。',
  snapshot_changed: '内容已经变化，请刷新后重新确认。',
};
const string = value => typeof value === 'string' ? value : '';
const list = value => Array.isArray(value) ? value : [];
function reasons(value) {
  return list(value?.reason_codes).map(code => REASONS[code] ?? '服务器尚未允许此操作，请刷新核对。').join(' ')
    || '服务器尚未允许此操作，请刷新核对。';
}
export function removalTimeLabel(value) {
  const date = typeof value === 'number' && Number.isFinite(value) ? new Date(value * 1000)
    : typeof value === 'string' && value.trim() ? new Date(value) : null;
  return date && Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN', {hour12: false}) : '未记录';
}

export function removalTarget(detail, scope) {
  const effect = detail?.effect, value = scope === 'effect' ? effect : detail;
  const policy = value?.deletion, id = string(value?.id);
  const active = ACTIVE.has(detail?.state) || scope === 'effect' && list(effect?.commissions).some(row => ACTIVE.has(row.state));
  const allowed = ['commission', 'effect'].includes(scope) && Boolean(id) && !detail?.ui_preview
    && !active && policy?.allowed === true && list(policy?.reason_codes).length === 0
    && SHA256.test(policy?.snapshot_sha256 ?? '');
  return {scope, target_id: id, title: string(value?.title) || (scope === 'effect' ? '未命名效果' : '未命名实验'),
    member_count: Number.isSafeInteger(policy?.member_count) && policy.member_count >= 1 ? policy.member_count : 1,
    snapshot_sha256: string(policy?.snapshot_sha256), allowed,
    reason: allowed ? '' : active ? REASONS.active : reasons(policy)};
}

export class EffectRecycleBinController {
  constructor({request, storage, storageKey, uuid = () => crypto.randomUUID(), onChange = () => {},
    getCatalog = () => null, refreshLibrary = async () => {}, afterTrash = () => {}, externalBusy = () => false}) {
    Object.assign(this, {request, storage, storageKey, uuid, onChange, getCatalog, refreshLibrary, afterTrash, externalBusy});
    this.closed = false;
    this.state = {scope: 'library', items: [], loaded: false, loading: false, busy: false,
      pending: null, error: '', notice: '', confirmation: null};
    try {
      const value = JSON.parse(storage?.getItem(storageKey) ?? 'null');
      if (value && ['trash', 'restore'].includes(value.action) && UUID.test(value.request_id)
        && ['commission', 'effect'].includes(value.scope) && string(value.target_id)
        && (value.action === 'trash' || string(value.trash_id))) this.state.pending = value;
      else if (value) this.state.error = '上次移除操作的本地记录损坏，请刷新并核对回收站。';
    } catch { this.state.error = '无法读取上次移除操作的本地记录，请核对回收站后再操作。'; }
  }
  get locked() { return this.state.busy || this.state.loading || Boolean(this.state.pending); }
  emit() { if (!this.closed) this.onChange({...this.state}); }
  setScope(scope) { this.state.scope = scope === 'trash' ? 'trash' : 'library'; this.emit(); }
  prepare(detail, scope) {
    if (this.locked || this.externalBusy()) return false;
    const target = removalTarget(detail, scope);
    if (!target.allowed) { this.state.error = target.reason; this.emit(); return false; }
    this.state.confirmation = target; this.state.error = ''; this.state.notice = ''; this.emit(); return true;
  }
  dismiss() { this.state.confirmation = null; this.emit(); }
  savePending(pending) {
    if (!this.storage) throw Error('浏览器无法保存操作编号，暂不能移除或恢复。');
    this.storage.setItem(this.storageKey, JSON.stringify(pending));
    this.state.pending = pending;
  }
  clearPending() {
    this.state.pending = null;
    try { this.storage?.removeItem(this.storageKey); }
    catch { this.state.error = '操作已确认，但本地记录未清除；重新打开后请刷新核对。'; }
  }
  async refresh() {
    if (this.closed || this.state.loading) return false;
    this.state.loading = true; this.state.error = ''; this.emit();
    try {
      const result = await this.request(API + 'trash');
      if (result?.schema !== 'video.animation-trash.v1' || !Array.isArray(result.items)
        || result.items.some(row => !string(row?.id) || !string(row?.target_id)
          || !['commission', 'effect'].includes(row.scope) || row.status !== 'trashed')) {
        throw Error('回收站响应不完整，请刷新核对。');
      }
      this.state.items = result.items; this.state.loaded = true;
      const pending = this.state.pending;
      if (pending && !this.state.busy) {
        if (pending.action === 'trash') {
          const found = result.items.find(row => row.request_id === pending.request_id
            && row.scope === pending.scope && row.target_id === pending.target_id);
          if (found) { this.afterTrash(found); this.clearPending(); this.state.notice = '已核实：目标已移入回收站。'; }
        }
        await this.refreshLibrary();
        if (pending.action === 'restore' && !result.items.some(row => row.id === pending.trash_id)) {
          const catalog = this.getCatalog(), values = pending.scope === 'effect' ? catalog?.effects : catalog?.commissions;
          if (list(values).some(row => row.id === pending.target_id)) {
            this.clearPending(); this.state.notice = '已核实：目标已恢复到效果库。';
          }
        }
        if (this.state.pending) this.state.notice = '上次操作结果尚未核实。请稍后刷新；系统不会自动重复提交。';
      }
      return true;
    } catch (error) { this.state.error = error.message || '读取回收站失败，请刷新。'; return false; }
    finally { this.state.loading = false; this.emit(); }
  }
  async confirm() {
    const target = this.state.confirmation;
    if (!target?.allowed || this.locked || this.externalBusy()) return false;
    return this.execute({action: 'trash', scope: target.scope, target_id: target.target_id,
      expected_snapshot_sha256: target.snapshot_sha256});
  }
  async restore(entry) {
    if (this.locked || this.externalBusy() || entry?.restore?.allowed !== true
      || list(entry?.restore?.reason_codes).length || !string(entry?.id)
      || !['commission', 'effect'].includes(entry?.scope) || !string(entry?.target_id)) return false;
    return this.execute({action: 'restore', scope: entry.scope, target_id: entry.target_id, trash_id: entry.id});
  }
  async retryPending() {
    const pending = this.state.pending;
    if (!pending || this.state.busy || this.state.loading || this.externalBusy()) return false;
    return this.execute(pending, pending.request_id);
  }
  async execute(input, originalRequestId = null) {
    this.state.error = ''; this.state.notice = '';
    const requestId = originalRequestId ?? this.uuid();
    if (!UUID.test(requestId ?? '')) { this.state.error = '无法生成有效的操作编号。'; this.emit(); return false; }
    const pending = {...input, request_id: requestId};
    try { this.savePending(pending); }
    catch (error) { this.state.error = error.message; this.emit(); return false; }
    this.state.confirmation = null; this.state.busy = true; this.emit();
    let result;
    try {
      const path = input.action === 'restore' ? 'trash/' + encodeURIComponent(input.trash_id) + '/restore'
        : (input.scope === 'effect' ? 'effects/' : 'commissions/') + encodeURIComponent(input.target_id) + '/trash';
      const body = {request_id: requestId, ...(input.action === 'trash'
        ? {expected_snapshot_sha256: input.expected_snapshot_sha256} : {})};
      result = await this.request(API + path, {method: 'POST', body: JSON.stringify(body)});
      if (result?.schema !== 'video.animation-trash-operation.v1' || result.action !== input.action
        || !string(result.entry?.id) || result.entry.scope !== input.scope || result.entry.target_id !== input.target_id) {
        throw Error('操作响应不完整。');
      }
    } catch (error) {
      if (Number.isInteger(error.status) && error.status >= 400 && error.status < 500) {
        this.clearPending(); this.state.error = error.code === 'animation_method_trash_stop_unproven'
          ? '尚未核实原任务已经停止，服务器没有移除任何记录。请稍后刷新后重试。' : error.status === 409
          ? '服务器未允许本次操作：目标可能仍在执行、已被保护或内容已变化。请刷新后重新确认。'
          : '本次操作未被接受，请刷新核对权限与目标。';
      } else {
        this.state.error = '提交结果尚未核实，请刷新回收站核对；不会自动重复提交。';
        this.state.scope = 'trash';
      }
      this.state.busy = false; this.emit(); return false;
    }
    this.clearPending();
    if (input.action === 'trash') this.afterTrash(result.entry);
    this.state.notice = input.action === 'trash' ? '已移入回收站，可以恢复。' : '已恢复到效果库。';
    try { await this.refreshLibrary(); await this.refresh(); }
    catch { this.state.error = '操作已完成，但列表刷新失败，请手动刷新。'; }
    finally { this.state.busy = false; this.emit(); }
    return true;
  }
  close() { this.closed = true; }
}

function element(doc, tag, className = '', content = '') {
  const node = doc.createElement(tag); node.className = className; node.textContent = content; return node;
}
function button(doc, title, handler, className = 'button secondary') {
  const node = element(doc, 'button', className, title); node.type = 'button'; node.addEventListener('click', handler); return node;
}
export function renderRemovalActions(doc, detail, onRemove, locked = false) {
  const section = element(doc, 'section', 'panel effect-removal');
  section.append(element(doc, 'h2', '', '整理效果与实验'), element(doc, 'p', 'muted small',
    '移入回收站后可恢复。正在执行、状态未核实、已发布或被正式制作引用的内容会受到保护。'));
  const actions = element(doc, 'div', 'detail-actions');
  for (const [scope, label] of [['commission', '移除本次实验'], ['effect', '移除整个效果']]) {
    const target = removalTarget(detail, scope), group = element(doc, 'div', 'effect-removal-action');
    const control = button(doc, label, () => onRemove(detail, scope), 'button secondary danger');
    control.disabled = locked || !target.allowed;
    if (!target.allowed) control.setAttribute('title', target.reason);
    group.append(control);
    if (!target.allowed) group.append(element(doc, 'p', 'muted small', target.reason));
    actions.append(group);
  }
  section.append(actions); return section;
}

export function mountEffectRecycleBin({document: doc, root, request, storage, uuid, refreshLibrary, getCatalog,
  afterTrash, externalBusy = () => false, onChange = () => {}}) {
  const $ = id => doc.getElementById(id);
  const library = $('gallery-library'), host = $('gallery-trash'), dialogHost = $('gallery-removal-dialog');
  let returnFocus = null, lastConfirmation = null, controller;
  const draw = state => {
    library.hidden = state.scope === 'trash'; host.hidden = state.scope !== 'trash';
    $('gallery-scope-library').setAttribute('aria-pressed', String(state.scope === 'library'));
    $('gallery-scope-trash').setAttribute('aria-pressed', String(state.scope === 'trash'));
    $('gallery-scope-trash').textContent = '回收站' + (state.loaded ? '（' + state.items.length + '）' : '');
    const message = element(doc, 'p', 'message' + (state.error ? ' error' : ''), state.error || state.notice);
    message.hidden = !message.textContent; message.setAttribute('role', 'status');
    const refresh = button(doc, state.loading ? '正在读取…' : '刷新回收站', () => void controller.refresh());
    refresh.disabled = state.loading || state.busy;
    const heading = element(doc, 'div', 'panel-heading');
    heading.append(element(doc, 'h2', '', '回收站'), refresh);
    host.replaceChildren(heading, element(doc, 'p', 'muted small', '可恢复移除的实验或整个效果。恢复整个效果时，之前单独移除的实验仍留在回收站。'), message);
    if (state.pending) {
      const retry = button(doc, '重试原操作', () => void controller.retryPending());
      retry.disabled = state.busy || state.loading || externalBusy();
      host.append(element(doc, 'p', 'small muted', '先刷新核对结果。仍未确认时，可使用同一操作编号重试原请求，不会创建新的移除或恢复操作。'), retry);
    }
    if (!state.items.length) host.append(element(doc, 'p', 'muted', state.loaded ? '回收站为空。' : '正在读取回收站…'));
    for (const entry of state.items) {
      const row = element(doc, 'article', 'trash-entry'), info = element(doc, 'div');
      info.append(element(doc, 'h3', '', string(entry.title) || '未命名记录'), element(doc, 'p', 'muted small',
        (entry.scope === 'effect' ? '整个效果' : '单次实验') + ' · ' + list(entry.member_ids).length + ' 条实验记录'),
      element(doc, 'p', 'muted small', '移除时间：' + removalTimeLabel(entry.deleted_at)));
      const restore = button(doc, '恢复到效果库', () => void controller.restore(entry));
      restore.disabled = controller.locked || externalBusy() || entry.restore?.allowed !== true || list(entry.restore?.reason_codes).length > 0;
      row.append(info, restore);
      if (entry.restore?.allowed !== true || list(entry.restore?.reason_codes).length) row.append(element(doc, 'p', 'muted small', reasons(entry.restore)));
      host.append(row);
    }
    const confirmation = state.confirmation;
    if (confirmation !== lastConfirmation) {
      lastConfirmation = confirmation;
      dialogHost.replaceChildren(); dialogHost.hidden = !confirmation;
      if (confirmation) {
        returnFocus = doc.activeElement;
        const dialog = element(doc, 'section', 'removal-confirmation panel');
        dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-modal', 'true'); dialog.setAttribute('aria-labelledby', 'removal-confirm-title');
        const title = element(doc, 'h2', '', confirmation.scope === 'effect' ? '移除整个效果？' : '移除本次实验？'); title.id = 'removal-confirm-title';
        const cancel = button(doc, '保留', () => controller.dismiss());
        const confirm = button(doc, '确认移入回收站', () => void controller.confirm(), 'button secondary danger');
        dialog.append(title, element(doc, 'p', '', confirmation.title), element(doc, 'p', 'small muted',
          (confirmation.scope === 'effect' ? '此效果关联 ' + confirmation.member_count + ' 条实验；本次移除整个效果，之前单独移除的实验保持原状。'
            : '仅移除当前实验及其样片；同一效果的其他实验和样片仍会保留。') + '之后可以恢复。'),
        element(doc, 'p', 'small muted', '确认时会核实任务已经停止；无法核实时不会移除，也不会取消任务。'),
        element(doc, 'p', 'small muted', '目标编号：' + confirmation.target_id));
        const actions = element(doc, 'div', 'detail-actions'); actions.append(cancel, confirm); dialog.append(actions);
        dialog.addEventListener('keydown', event => {
          if (event.key === 'Escape') { event.preventDefault(); controller.dismiss(); }
          if (event.key === 'Tab') {
            event.preventDefault(); (doc.activeElement === cancel ? confirm : cancel).focus();
          }
        });
        dialogHost.append(dialog); cancel.focus();
      } else { returnFocus?.focus?.(); returnFocus = null; }
    }
    onChange(state);
  };
  controller = new EffectRecycleBinController({request, storage,
    storageKey: 'animation-effect-recycle:' + new URL(root).href, uuid, refreshLibrary, getCatalog,
    afterTrash, externalBusy, onChange: draw});
  $('gallery-scope-library').addEventListener('click', () => controller.setScope('library'));
  $('gallery-scope-trash').addEventListener('click', () => { controller.setScope('trash'); void controller.refresh(); });
  controller.render = () => draw({...controller.state});
  controller.render();
  if (controller.state.pending) { controller.setScope('trash'); void controller.refresh(); }
  return controller;
}
