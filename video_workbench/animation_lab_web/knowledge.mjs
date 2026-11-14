const ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/;
const SHA = /^[0-9a-f]{64}$/;
const STATUSES = ['provisional', 'active', 'deprecated'];
const copy = value => JSON.parse(JSON.stringify(value));
const lines = value => String(value ?? '').split(/\r?\n/).map(item => item.trim()).filter(Boolean);
const requireValue = (condition, message) => { if (!condition) throw new Error(message); };
const identifier = value => { requireValue(typeof value === 'string' && ID.test(value), '记录 ID 格式不正确'); return value; };
const revisionNumber = value => {
  requireValue(Number.isInteger(value) && value > 0 && value <= 1000000, '版本号不正确'); return value;
};

export class KnowledgeHTTPError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export function createKnowledgeClient({fetch: request = globalThis.fetch, root = new URL('./', import.meta.url)} = {}) {
  const base = new URL(root);
  requireValue(['http:', 'https:'].includes(base.protocol) && !base.search && !base.hash && !base.username && !base.password,
    '知识接口地址不正确');
  const endpoint = new URL('api/method-lab/knowledge', base).href;
  let token = '';
  async function send(suffix, body) {
    const options = {method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error'};
    if (body !== undefined) {
      requireValue(token, '请先刷新知识目录以取得当前会话凭证');
      options.headers = {'Content-Type': 'application/json', 'X-Animation-Lab-Token': token};
      options.body = body;
    }
    const response = await request(endpoint + suffix, options);
    let value;
    try { value = await response.json(); }
    catch { throw new KnowledgeHTTPError(response.ok ? 0 : response.status, '服务响应无法解析，请核对当前版本'); }
    if (!response.ok) throw new KnowledgeHTTPError(response.status, String(value?.error ?? '知识接口请求失败'));
    return value;
  }
  return {
    async catalogue() {
      const value = await send('');
      requireValue(Array.isArray(value?.pages) && Array.isArray(value?.experiences) && typeof value?.csrf_token === 'string'
        && value.csrf_token.length > 0, '知识目录响应不完整');
      token = value.csrf_token;
      return value;
    },
    page: id => send('/pages/' + encodeURIComponent(identifier(id))),
    revision: (id, revision) => send('/pages/' + encodeURIComponent(identifier(id)) + '/revisions/' + revisionNumber(revision)),
    experience: id => send('/experiences/' + encodeURIComponent(identifier(id))),
    revise: (id, frozenBody) => {
      requireValue(typeof frozenBody === 'string', '修订请求必须先冻结');
      return send('/pages/' + encodeURIComponent(identifier(id)), frozenBody);
    },
  };
}

export function draftFromPage(page) {
  return {title: page.body.title, content: page.body.content, applicability: (page.body.applicability ?? []).join('\n'),
    limitations: (page.body.limitations ?? []).join('\n'), evidence_ids: page.evidence_ids.join('\n'), status: page.status,
    counterexamples: JSON.stringify(page.body.counterexamples ?? [], null, 2),
    relations: JSON.stringify(page.relations ?? [], null, 2), change_note: ''};
}

export function buildKnowledgeRevision(page, fields, requestId) {
  identifier(page.page_id); identifier(requestId); revisionNumber(page.revision);
  requireValue(typeof fields.title === 'string' && fields.title.trim(), '请填写标题');
  requireValue(typeof fields.content === 'string' && fields.content.trim(), '请填写知识正文');
  requireValue(typeof fields.change_note === 'string' && fields.change_note.trim(), '请说明修正原因');
  requireValue(STATUSES.includes(fields.status), '页面状态不正确');
  const evidence = lines(fields.evidence_ids);
  requireValue(evidence.length > 0 && evidence.every(item => ID.test(item)), '至少保留一条有效的实验证据 ID');
  let counterexamples, relations;
  try { counterexamples = JSON.parse(fields.counterexamples); relations = JSON.parse(fields.relations); }
  catch { throw new Error('反例或关联页面的 JSON 格式不正确'); }
  requireValue(Array.isArray(counterexamples) && Array.isArray(relations), '反例和关联页面必须为 JSON 数组');
  return {request_id: requestId, expected_revision: page.revision,
    body: {title: fields.title.trim(), content: fields.content.trim(), applicability: lines(fields.applicability),
      limitations: lines(fields.limitations), counterexamples},
    evidence_ids: evidence, status: fields.status, relations,
    change_kind: fields.status === 'deprecated' ? 'deprecate' : 'correct', change_note: fields.change_note.trim()};
}

function validPage(page, id) {
  requireValue(page?.schema === 'video.animation-knowledge-page.v1' && page.page_id === id
    && Number.isInteger(page.revision) && page.revision > 0 && page.revision <= 1000000 && typeof page.body?.title === 'string'
    && typeof page.body?.content === 'string' && Array.isArray(page.evidence_ids)
    && Array.isArray(page.body.applicability) && Array.isArray(page.body.limitations) && Array.isArray(page.body.counterexamples)
    && Array.isArray(page.relations) && STATUSES.includes(page.status)
    && typeof page.content_sha256 === 'string' && SHA.test(page.content_sha256)
    && typeof page.sha256 === 'string' && SHA.test(page.sha256), '知识页面响应不完整');
  return page;
}

export class KnowledgeController {
  constructor({client, uuid = () => globalThis.crypto.randomUUID(), onChange = () => {}}) {
    this.client = client; this.uuid = uuid; this.onChange = onChange;
    this.state = {catalogue: null, selection: null, detail: null, editor: null, draft: null, pending: null,
      reloadRequired: null, busy: false, error: false, message: ''};
  }
  emit() { this.onChange(this.state); }
  message(message, error = false) { this.state.message = message; this.state.error = error; }
  async read(work) {
    if (this.state.busy) return false;
    this.state.busy = true; this.emit();
    try { await work(); return true; }
    catch (error) { this.message(error.message, true); return false; }
    finally { this.state.busy = false; this.emit(); }
  }
  async refresh() {
    return this.read(async () => {
      this.state.catalogue = await this.client.catalogue();
      this.message(this.state.pending ? '上次提交结果尚未确认，请核对版本或重送原请求。' : '目录已读取。查看记录不会启动实验。');
    });
  }
  async select(kind, id, revision = null) {
    if (this.state.pending || this.state.editor) return false;
    return this.read(async () => {
      identifier(id); requireValue(['page', 'experience'].includes(kind), '记录类型不正确');
      const detail = kind === 'experience' ? await this.client.experience(id)
        : revision === null ? await this.client.page(id) : await this.client.revision(id, revisionNumber(revision));
      if (kind === 'page') {
        validPage(detail?.page, id);
        requireValue(revision === null || detail.page.revision === revision, '历史版本响应不匹配');
      } else requireValue(detail?.id === id && typeof detail.kind === 'string', '经验记录响应不完整');
      this.state.selection = {kind, id, revision}; this.state.detail = detail;
      if (kind === 'page' && revision === null && this.state.reloadRequired === id) this.state.reloadRequired = null;
      this.message(revision === null ? '已读取完整记录。' : '正在查看历史正文，仅可阅读。');
    });
  }
  edit() {
    if (this.state.busy || this.state.pending || this.state.selection?.kind !== 'page'
        || this.state.selection.revision !== null || this.state.reloadRequired === this.state.selection.id) return false;
    const page = this.state.detail.page;
    this.state.editor = {page: copy(page), fields: this.state.draft?.page_id === page.page_id
      ? copy(this.state.draft.fields) : draftFromPage(page)};
    this.message(this.state.draft ? '草稿已保留。请对照当前版本重新检查，再明确保存。' : '填写修正原因后，保存会创建一个新版本。');
    this.emit(); return true;
  }
  update(fields) { if (this.state.editor && !this.state.pending) this.state.editor.fields = copy(fields); }
  cancelEdit() {
    if (this.state.busy || this.state.pending) return;
    this.state.editor = null; this.emit();
  }
  async save() {
    if (this.state.busy || this.state.pending || !this.state.editor) return false;
    try {
      const editor = this.state.editor;
      const body = buildKnowledgeRevision(editor.page, editor.fields, this.uuid());
      this.state.draft = {page_id: editor.page.page_id, from_revision: editor.page.revision, fields: copy(editor.fields)};
      this.state.pending = Object.freeze({page_id: editor.page.page_id, raw: JSON.stringify(body)});
    } catch (error) { this.message(error.message, true); this.emit(); return false; }
    return this.submitPending();
  }
  async submitPending() {
    if (this.state.busy || !this.state.pending) return false;
    const pending = this.state.pending, request = JSON.parse(pending.raw);
    return this.read(async () => {
      let result;
      try {
        result = await this.client.revise(pending.page_id, pending.raw);
        validPage(result, pending.page_id);
        requireValue(result.revision === request.expected_revision + 1 && result.change_note === request.change_note
          && result.body.title === request.body.title && result.body.content === request.body.content
          && result.status === request.status && result.change_kind === request.change_kind,
        '修订响应不匹配，提交结果需要核对');
      } catch (error) {
        if (error.status === 409) {
          this.state.pending = null; this.state.editor = null;
          this.state.reloadRequired = pending.page_id;
          this.message('版本冲突，草稿已保留。请先读取最新版本，再核对并重新保存。', true);
        } else if ([400, 401, 403, 404, 413, 415, 422].includes(error.status)) {
          this.state.pending = null;
          this.message('服务拒绝了修订，草稿已保留：' + error.message, true);
        } else this.message('提交结果尚不确定，原请求已冻结。请核对当前版本，或明确重送完全相同的原请求。', true);
        return;
      }
      this.state.pending = null; this.state.editor = null; this.state.draft = null;
      // An idempotent replay can return an older revision. Always read the current head.
      this.state.detail = null; this.state.selection = null;
      const detail = await this.client.page(pending.page_id);
      validPage(detail?.page, pending.page_id);
      this.state.selection = {kind: 'page', id: pending.page_id, revision: null}; this.state.detail = detail;
      this.message('修订已保存，已读取当前最新版本。已发布 Skill 保持原版本。');
      try { this.state.catalogue = await this.client.catalogue(); }
      catch { this.message('修订已保存并读到最新版本；目录刷新失败，可稍后手动刷新。'); }
    });
  }
  async checkPending() {
    if (!this.state.pending) return false;
    return this.read(async () => {
      const detail = await this.client.page(this.state.pending.page_id);
      validPage(detail?.page, this.state.pending.page_id);
      this.state.detail = detail;
      this.state.selection = {kind: 'page', id: this.state.pending.page_id, revision: null};
      this.message('当前服务端为第 ' + detail.page.revision + ' 版。仅查询版本不能确认原请求结果；可重送原请求取得幂等回执。');
    });
  }
}

const statusLabel = value => ({provisional: '待验证', active: '适用中', deprecated: '已停用'}[value] ?? value);

export function mountKnowledgePage(document, controller) {
  const get = id => document.getElementById(id);
  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function button(text, action, disabled = false, className) {
    const node = el('button', text, className); node.type = 'button'; node.disabled = disabled;
    node.addEventListener('click', action); return node;
  }
  function section(target, title, content) { target.append(el('h3', title), el('div', content, 'prose')); }
  function json(target, title, value) {
    const details = el('details'); details.append(el('summary', title), el('pre', JSON.stringify(value, null, 2), 'json'));
    target.append(details);
  }
  function renderLists(state) {
    const search = get('knowledge-search').value.trim().toLowerCase();
    for (const [kind, key, idKey] of [['page', 'pages', 'page_id'], ['experience', 'experiences', 'id']]) {
      const target = get('knowledge-' + key), all = state.catalogue?.[key] ?? [];
      const rows = all.filter(row => JSON.stringify(row).toLowerCase().includes(search));
      get('knowledge-' + (kind === 'page' ? 'page' : 'experience') + '-count').textContent = rows.length + ' / ' + all.length;
      target.replaceChildren();
      for (const row of rows) {
        const active = state.selection?.kind === kind && state.selection.id === row[idKey];
        const item = button('', () => controller.select(kind, row[idKey]), state.busy || !!state.pending || !!state.editor,
          'record' + (active ? ' active' : ''));
        item.append(el('strong', row.title || row.kind || row[idKey]), el('span', row[idKey]));
        if (kind === 'page') item.append(el('span', '第 ' + row.revision + ' 版 · ' + statusLabel(row.status)
          + (row.evidence_status === 'withdrawn_feedback' ? ' · 证据已撤回' : '')));
        else item.append(el('span', row.source_id));
        target.append(item);
      }
      if (!rows.length) target.append(el('p', search ? '本次目录没有匹配记录。' : '暂无记录。', 'muted'));
    }
  }
  function renderEditor(target, state) {
    const editor = state.editor;
    if (!editor) return;
    const form = el('form', undefined, 'editor'), fields = {};
    form.append(el('h3', '明确修订 · 基于第 ' + editor.page.revision + ' 版'));
    form.append(el('p', '保存只修正创作知识，不运行 Agent，也不发布正式 Skill。', 'form-note'));
    const input = (key, title, rows) => {
      const label = el('label', title), node = el(rows ? 'textarea' : 'input');
      node.name = key; if (rows) node.rows = rows;
      node.value = editor.fields[key]; node.disabled = state.busy || !!state.pending;
      node.addEventListener('input', () => controller.update(Object.fromEntries(Object.entries(fields).map(([name, field]) => [name, field.value]))));
      fields[key] = node; label.append(node); return label;
    };
    form.append(input('title', '标题'), input('content', '知识正文', 10));
    const row = el('div', undefined, 'row');
    row.append(input('applicability', '适用条件，每行一项', 4), input('limitations', '局限与注意事项，每行一项', 4));
    form.append(row, input('evidence_ids', '实验证据 ID，每行一项', 3));
    const label = el('label', '状态'), select = el('select');
    for (const value of STATUSES) { const option = el('option', statusLabel(value)); option.value = value; select.append(option); }
    select.value = editor.fields.status; select.disabled = state.busy || !!state.pending; fields.status = select;
    select.addEventListener('change', () => controller.update(Object.fromEntries(Object.entries(fields).map(([name, field]) => [name, field.value]))));
    label.append(select); form.append(label);
    const advanced = el('details'); advanced.append(el('summary', '反例与关联页面（高级）'),
      input('counterexamples', '反例 JSON 数组', 5), input('relations', '关联页面 JSON 数组', 5));
    form.append(advanced, input('change_note', '修正原因（必填）', 3));
    const actions = el('div', undefined, 'actions');
    const submit = button('保存一个新版本', () => {}, state.busy || !!state.pending, 'primary'); submit.type = 'submit';
    actions.append(submit, button('取消编辑', () => controller.cancelEdit(), state.busy || !!state.pending));
    form.append(actions);
    form.addEventListener('submit', event => { event.preventDefault(); controller.save(); }); target.append(form);
  }
  function render(state) {
    const message = get('knowledge-message'); message.textContent = state.message; message.classList.toggle('error', state.error);
    get('knowledge-refresh').disabled = state.busy;
    get('knowledge-lookup').disabled = state.busy || !!state.pending || !!state.editor;
    renderLists(state);
    const target = get('knowledge-detail'); target.replaceChildren();
    if (state.pending) {
      target.append(el('p', '修订请求已冻结。请勿关闭本页，避免丢失尚未确认的请求和草稿。', 'note'));
      const actions = el('div', undefined, 'actions');
      actions.append(button('核对当前版本', () => controller.checkPending(), state.busy),
        button('重送原请求', () => controller.submitPending(), state.busy)); target.append(actions);
    }
    if (state.draft && !state.editor) target.append(el('p', '已保留基于第 ' + state.draft.from_revision
      + ' 版的修订草稿。先读取最新版本，再点击“修订当前页面”核对。', 'note'));
    const {selection, detail} = state;
    if (!selection || !detail) { target.append(el('p', '选择目录记录，查看正文、证据和历史版本。', 'muted')); return; }
    if (selection.kind === 'experience') {
      target.append(el('h2', '实验经验 · ' + detail.kind), el('p', detail.id, 'muted'),
        el('p', '真实经验保留原貌，不在这里改写。需要修正理解时，请修订引用它的知识页面。', 'note'));
      section(target, '来源', detail.source_id); section(target, '内容摘要指纹', detail.content_sha256);
      target.append(el('h3', '完整经验正文'), el('pre', JSON.stringify(detail.document, null, 2), 'json'));
      json(target, '查看完整原始记录', detail); return;
    }
    const page = detail.page;
    target.append(el('h2', page.body.title), el('p', page.page_id, 'muted'));
    const badges = el('div', undefined, 'badges');
    for (const value of ['第 ' + page.revision + ' 版', statusLabel(page.status), selection.revision === null ? '当前页面' : '历史版本 · 只读'])
      badges.append(el('span', value, 'badge'));
    target.append(badges);
    if (detail.evidence_issues?.length) target.append(el('p', '部分引用经验已被撤回：' + detail.evidence_issues.join('、')
      + '。请核对适用范围和结论。', 'note'));
    section(target, '知识正文', page.body.content);
    section(target, '适用条件', (page.body.applicability ?? []).join('\n') || '未记录');
    section(target, '局限与注意事项', (page.body.limitations ?? []).join('\n') || '未记录');
    json(target, '反例', page.body.counterexamples ?? []); json(target, '关联页面', page.relations);
    const locked = state.busy || !!state.pending || !!state.editor;
    const actions = el('div', undefined, 'actions');
    actions.append(selection.revision === null ? button('修订当前页面', () => controller.edit(), locked || state.reloadRequired === page.page_id, 'primary')
      : button('返回最新版本', () => controller.select('page', page.page_id), locked));
    if (state.draft && selection.revision === null) actions.append(button('重新读取最新版本', () => controller.select('page', page.page_id), locked));
    target.append(actions); renderEditor(target, state);
    target.append(el('h3', '引用的实验证据'));
    const evidence = el('div', undefined, 'evidence-list');
    for (const id of page.evidence_ids) evidence.append(button(id, () => controller.select('experience', id), locked));
    target.append(evidence);
    const history = el('details'); history.open = selection.revision !== null;
    history.append(el('summary', '历史版本（本次最多 64 条）'));
    const list = el('ul', undefined, 'history');
    for (const row of detail.history ?? []) {
      const li = el('li'); li.append(button('查看第 ' + row.revision + ' 版', () => controller.select('page', page.page_id, row.revision), locked),
        el('span', statusLabel(row.status)), el('p', row.change_note ?? '')); list.append(li);
    }
    if (selection.revision !== null) list.append(el('li', '这是历史正文；返回最新版本可查看历史目录并修订。', 'readonly'));
    history.append(list); target.append(history); json(target, '完整页面记录与指纹', page);
  }
  controller.onChange = render;
  get('knowledge-refresh').addEventListener('click', () => controller.refresh());
  get('knowledge-search').addEventListener('input', () => renderLists(controller.state));
  get('knowledge-lookup').addEventListener('click', () => controller.select(get('knowledge-lookup-kind').value, get('knowledge-lookup-id').value.trim()));
  document.defaultView?.addEventListener('beforeunload', event => {
    if (controller.state.pending || controller.state.editor) { event.preventDefault(); event.returnValue = ''; }
  });
  render(controller.state); controller.refresh(); return controller;
}

if (typeof document !== 'undefined' && document.getElementById('knowledge-app')) {
  mountKnowledgePage(document, new KnowledgeController({client: createKnowledgeClient()}));
}
