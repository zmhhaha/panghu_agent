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
const experienceLabels = {execution_failure: '执行失败记录', failure: '失败记录', render: '实际渲染记录',
  sampled_review: '抽帧评审', authored_proposal: '创作提案', developer_feedback: '开发者评价',
  feedback_withdrawal: '评价撤回记录', knowledge_proposal: '知识更新提案',
  developer_knowledge_revision: '人工修订记录', knowledge_conflict: '知识版本冲突'};
export const experienceLabel = kind => experienceLabels[kind] ?? '实验记录';

export function experienceReading(detail) {
  const record = detail?.document && typeof detail.document === 'object' ? detail.document : {};
  const sections = [];
  const add = (title, value) => { if (typeof value === 'string' && value.trim()) sections.push({title, content: value}); };
  add('本次创作目标', record.goal);
  add(detail.kind === 'developer_feedback' ? '评价内容' : '记录摘要', record.summary || record.comment || record.change_note);
  add('记录的错误', typeof record.error === 'string' ? record.error : record.error?.message || record.error?.code);
  if (typeof record.outcome === 'string') add('记录结论', ({passed: '通过', failed: '未通过', incomplete: '证据不足'}[record.outcome] ?? record.outcome));
  const stringList = value => Array.isArray(value) ? value.filter(item => typeof item === 'string').join('\n') : '';
  add('仍需解决的问题', stringList(record.remaining_issues));
  add('后续建议', typeof record.next_guidance === 'string' ? record.next_guidance : stringList(record.next_guidance));
  for (const proposal of Array.isArray(record.proposals) ? record.proposals : []) {
    add(proposal?.body?.title || '知识更新提案', proposal?.body?.content);
  }
  const context = ({sampled_review: '这是基于实际传入抽帧的评审，不代表已经完整观看运动或试听声音。',
    render: '这份记录反映实际渲染结果，不能单独证明画面表现已获认可。',
    authored_proposal: '这是 Agent 的创作提案，不代表已经渲染、验收或发布。',
    knowledge_proposal: '这是 Agent 提出的知识更新，实际采用情况以对应知识页的版本为准。',
    feedback_withdrawal: '这条记录说明原评价已撤回；引用它的知识需要重新核对。'}[detail.kind]
    ?? '真实实验经验保留原貌。需要纠正理解时，可修订引用它的知识页面。');
  return {title: experienceLabel(detail.kind), context, sections};
}

export function mountKnowledgePage(document, controller) {
  const get = id => document.getElementById(id);
  let sourcePage = null, previousEditor = null;
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
  function listSection(target, title, items, fallback = '未记录') {
    target.append(el('h3', title));
    if (!items?.length) { target.append(el('p', fallback, 'muted')); return; }
    const list = el('ul', undefined, 'reading-list');
    for (const item of items) list.append(el('li', item));
    target.append(list);
  }
  function evidenceLabel(state, id, index) {
    const record = state.catalogue?.experiences.find(row => row.id === id);
    return record ? experienceLabel(record.kind) + ' · 证据 ' + (index + 1) : '实验证据 ' + (index + 1);
  }
  function renderLists(state) {
    const search = get('knowledge-search').value.trim().toLowerCase();
    for (const [kind, key, idKey] of [['page', 'pages', 'page_id'], ['experience', 'experiences', 'id']]) {
      const target = get('knowledge-' + key), all = state.catalogue?.[key] ?? [];
      const rows = all.filter(row => (JSON.stringify(row) + ' ' + (kind === 'experience' ? experienceLabel(row.kind) : '')).toLowerCase().includes(search));
      get('knowledge-' + (kind === 'page' ? 'page' : 'experience') + '-count').textContent = rows.length + ' / ' + all.length;
      target.replaceChildren();
      for (const [index, row] of rows.entries()) {
        const active = state.selection?.kind === kind && state.selection.id === row[idKey];
        const item = button('', () => { sourcePage = null; controller.select(kind, row[idKey]); }, state.busy || !!state.pending || !!state.editor,
          'record' + (active ? ' active' : ''));
        item.append(el('strong', kind === 'page' ? row.title : experienceLabel(row.kind)));
        if (kind === 'page') item.append(el('span', '第 ' + row.revision + ' 版 · ' + statusLabel(row.status)
          + (row.evidence_status === 'withdrawn_feedback' ? ' · 证据已撤回' : '')));
        else item.append(el('span', '实验记录 ' + (index + 1)));
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
    form.append(row);
    const label = el('label', '状态'), select = el('select');
    for (const value of STATUSES) { const option = el('option', statusLabel(value)); option.value = value; select.append(option); }
    select.value = editor.fields.status; select.disabled = state.busy || !!state.pending; fields.status = select;
    select.addEventListener('change', () => controller.update(Object.fromEntries(Object.entries(fields).map(([name, field]) => [name, field.value]))));
    label.append(select); form.append(label);
    const advanced = el('details'); advanced.append(el('summary', '证据、反例与关联页面（高级）'),
      input('evidence_ids', '实验证据 ID，每行一项', 3),
      input('counterexamples', '反例 JSON 数组', 5), input('relations', '关联页面 JSON 数组', 5));
    form.append(advanced, input('change_note', '修正原因（必填）', 3));
    const actions = el('div', undefined, 'actions');
    const submit = button('保存一个新版本', () => {}, state.busy || !!state.pending, 'primary'); submit.type = 'submit';
    actions.append(submit, button('取消编辑', () => controller.cancelEdit(), state.busy || !!state.pending));
    form.append(actions);
    form.addEventListener('submit', event => { event.preventDefault(); controller.save(); }); target.append(form);
    return fields.title;
  }
  function render(state) {
    const enteringEditor = !!state.editor && state.editor !== previousEditor;
    const leavingEditor = !!previousEditor && !state.editor;
    previousEditor = state.editor;
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
    if (state.draft && !state.editor) {
      target.append(el('p', '已保留基于第 ' + state.draft.from_revision
        + ' 版的修订草稿。先读取最新版本，再从高级维护中继续修订。', 'note'));
      target.append(button('读取最新版本并核对草稿', () => controller.select('page', state.draft.page_id), state.busy));
    }
    const {selection, detail} = state;
    if (!selection || !detail) {
      target.append(el('h2', '选择一页知识开始阅读'), el('p', '左侧也可以查看形成这些知识的真实实验经验。', 'muted'));
      return;
    }
    if (selection.kind === 'experience') {
      const reading = experienceReading(detail);
      if (sourcePage) target.append(button('← 返回引用它的知识页', () => controller.select('page', sourcePage.id, sourcePage.revision), state.busy));
      target.append(el('h2', reading.title), el('p', reading.context, 'scope-note'));
      for (const item of reading.sections) section(target, item.title, item.content);
      if (!reading.sections.length) target.append(el('p', '这份记录主要包含技术证据，完整原文保存在下方高级信息中。', 'muted'));
      target.append(el('p', '来源记录可能属于已归档或清除的实验；保留经验不表示原样片仍可访问。', 'muted small'));
      const advanced = el('details', undefined, 'maintenance');
      advanced.append(el('summary', '高级信息：来源编号与完整记录'));
      section(advanced, '经验 ID', detail.id); section(advanced, '来源 ID', detail.source_id);
      section(advanced, '内容摘要指纹', detail.content_sha256);
      json(advanced, '完整原始记录', detail); target.append(advanced); return;
    }
    const page = detail.page;
    target.append(el('h2', page.body.title));
    const badges = el('div', undefined, 'badges');
    for (const value of ['第 ' + page.revision + ' 版', statusLabel(page.status), selection.revision === null ? '当前页面' : '历史版本 · 只读'])
      badges.append(el('span', value, 'badge'));
    target.append(badges);
    if (selection.revision !== null) target.append(button('返回最新版本', () => controller.select('page', page.page_id),
      state.busy || !!state.pending || !!state.editor));
    if (detail.evidence_issues?.length) target.append(el('p', '有 ' + detail.evidence_issues.length
      + ' 条引用经验已被撤回，请核对适用范围和结论。', 'note'));
    section(target, '知识正文', page.body.content);
    listSection(target, '适用条件', page.body.applicability);
    listSection(target, '局限与注意事项', page.body.limitations);
    if (page.body.counterexamples?.length) listSection(target, '反例', page.body.counterexamples.map(row => row.description));
    const locked = state.busy || !!state.pending || !!state.editor;
    target.append(el('h3', '来源实验证据'), el('p', '查看支撑这页知识的真实经验。', 'muted small'));
    const evidence = el('div', undefined, 'evidence-list');
    for (const [index, id] of page.evidence_ids.entries()) evidence.append(button(evidenceLabel(state, id, index), () => {
      sourcePage = {id: page.page_id, revision: selection.revision}; controller.select('experience', id);
    }, locked));
    target.append(evidence);
    const history = el('details'); history.open = selection.revision !== null;
    history.append(el('summary', '查看历史版本（本次最多 64 条）'));
    const list = el('ul', undefined, 'history');
    for (const row of detail.history ?? []) {
      const li = el('li'); li.append(button('查看第 ' + row.revision + ' 版', () => controller.select('page', page.page_id, row.revision), locked),
        el('span', statusLabel(row.status)), el('p', row.change_note ?? '')); list.append(li);
    }
    if (selection.revision !== null) list.append(el('li', '这是历史正文；返回最新版本可查看历史目录并修订。', 'readonly'));
    history.append(list); target.append(history);
    const maintenance = el('details', undefined, 'maintenance');
    maintenance.open = !!state.editor || !!state.pending || state.reloadRequired === page.page_id;
    const maintenanceHeading = el('summary', '高级维护：人工修订与技术记录');
    maintenanceHeading.tabIndex = 0;
    maintenance.append(maintenanceHeading,
      el('p', '日常制作无需填写。仅在知识结论有误时修订；保存会创建新版本，不会启动实验或发布 Skill。', 'form-note'));
    let editorFocus = null;
    if (selection.revision === null) {
      const actions = el('div', undefined, 'actions');
      actions.append(button('修订当前页面', () => controller.edit(), locked || state.reloadRequired === page.page_id, 'primary'));
      if (state.draft) actions.append(button('重新读取最新版本', () => controller.select('page', page.page_id), locked));
      maintenance.append(actions); editorFocus = renderEditor(maintenance, state);
    }
    section(maintenance, '知识页 ID', page.page_id);
    if (detail.evidence_issues?.length) json(maintenance, '已撤回的证据 ID', detail.evidence_issues);
    json(maintenance, '关联页面与版本绑定', page.relations);
    json(maintenance, '完整页面记录与指纹', page); target.append(maintenance);
    if (enteringEditor) editorFocus?.focus?.();
    if (leavingEditor) maintenanceHeading.focus?.();
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
