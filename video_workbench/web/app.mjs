import {
  activeOperations, chooseVersion, createIntentKeys, createSceneDraft, createWorkspaceState,
  acceptProjectRead, isActiveOperation, issueProjectRead, latestVersion, orderedVersions,
  selectProject, selectScene, selectedScene, selectedVersion, statusLabel, syncSceneDraft,
  updateProjectSummary, versionPath,
} from './model.mjs';

const $ = id => document.getElementById(id);
const state = createWorkspaceState();
const intentKeys = createIntentKeys();
const drafts = new Map();
const feedbacks = new Map();
const pending = new Set();
const cancelRequests = new Set();
let csrfToken = null;
let projects = [];
let listSequence = 0;
let readController = null;
let sourceController = null;
let pollTimer = null;
let successTimer = null;
let editor = null;
let previewMode = 'preview';
let displayedVersion = null;
let sourceVersion = null;
let loading = false;

const ERROR_LABELS = {
  unauthorized: '登录已失效，请重新登录。', authentication_required: '请先登录工作台。',
  csrf_invalid: '登录状态已更新，请刷新后重试。', invalid_csrf: '登录状态已更新，请刷新后重试。',
  csrf_rejected: '登录状态已更新，请刷新后重试。',
  revision_conflict: '项目已有更新。你的草稿已保留，请重新载入最新镜头后再保存。',
  head_conflict: '采用的版本已发生变化，请刷新项目后重试。',
  operation_in_progress: '这个镜头还有正在执行的任务，请等待任务结束。',
  active_operation: '当前任务尚未结束，请稍后重试。',
  storyboard_exists: '项目已经有镜头，请逐个编辑或添加镜头。',
  agent_not_configured: '尚未配置 Agent 服务，仍可手动整理文稿与镜头。',
  service_unavailable: 'Agent 服务暂时不可用，请稍后重试。',
  idempotency_conflict: '这个操作与先前提交的内容不一致，请刷新后重试。',
  not_found: '内容不存在或你没有访问权限。',
};

class ApiError extends Error {
  constructor(status, body) {
    const detail = body?.detail;
    const code = typeof detail === 'object' && !Array.isArray(detail) ? detail?.code : null;
    const message = typeof detail?.message === 'string' ? detail.message : null;
    super(ERROR_LABELS[code] ?? message ?? (status === 401 ? '请先登录工作台。'
      : status === 409 ? '项目已更新或已有任务执行中，请刷新后重试。'
        : status === 422 ? '请检查填写的内容和长度。' : `请求未完成（${status}），请稍后重试。`));
    this.status = status;
    this.code = code;
  }
}

async function request(path, { method = 'GET', body, signal, output = 'json' } = {}) {
  const headers = { Accept: output === 'json' ? 'application/json' : '*/*' };
  const mutating = !['GET', 'HEAD'].includes(method);
  if (mutating) {
    if (!csrfToken) throw new ApiError(401, {});
    headers['X-CSRF-Token'] = csrfToken;
    headers['Idempotency-Key'] = intentKeys.get(method, path, body);
    headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(path, {
    method, headers, signal, credentials: 'same-origin', redirect: 'error',
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new ApiError(response.status, detail);
  }
  const result = response.status === 204 ? null : output === 'blob' ? await response.blob()
    : output === 'text' ? await response.text() : await response.json();
  if (mutating) intentKeys.complete(method, path, body);
  return result;
}

function showError(error) {
  if (error?.name === 'AbortError') return;
  $('error-text').textContent = error instanceof ApiError ? error.message
    : '连接中断，尚未确认操作结果。可重新读取状态，或重试同一个操作。';
  $('global-error').hidden = false;
  $('login-link').hidden = error?.status !== 401;
  if ($('create-project-dialog').open) {
    $('create-error').textContent = $('error-text').textContent;
    $('create-error').hidden = false;
  }
}

function success(message) {
  clearTimeout(successTimer);
  $('success-message').textContent = message;
  $('success-message').hidden = false;
  successTimer = setTimeout(() => { $('success-message').hidden = true; }, 5000);
}

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function shortDate(value) {
  if (!value) return '';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}

function draftKey(sceneId = state.sceneId) { return `${state.projectId}:${sceneId ?? 'new'}`; }
function currentDraft() { return drafts.get(draftKey(editor === 'new' ? null : state.sceneId)); }

function renderProjects() {
  $('project-count').textContent = projects.length;
  const fragment = document.createDocumentFragment();
  for (const project of projects) {
    const button = node('button', undefined, `project-item${project.id === state.projectId ? ' selected' : ''}`);
    button.type = 'button';
    button.dataset.projectId = project.id;
    button.setAttribute('aria-current', project.id === state.projectId ? 'true' : 'false');
    button.append(node('span', '▧', 'project-symbol'));
    const label = node('span', undefined, 'project-item-text');
    label.append(node('strong', project.title), node('small', `${project.scene_count ?? 0} 个镜头${shortDate(project.updated_at) ? ` · ${shortDate(project.updated_at)}` : ''}`));
    button.append(label);
    button.addEventListener('click', () => openProject(project.id));
    fragment.append(button);
  }
  if (!projects.length) fragment.append(node('p', '还没有项目，从文稿开始创建。', 'sidebar-empty'));
  $('project-list').replaceChildren(fragment);
}

function renderScenes(project) {
  const fragment = document.createDocumentFragment();
  for (const [index, scene] of project.scenes.entries()) {
    const button = node('button', undefined, `scene-card${scene.id === state.sceneId && editor !== 'new' ? ' selected' : ''}`);
    button.type = 'button';
    button.dataset.sceneId = scene.id;
    button.setAttribute('aria-pressed', String(scene.id === state.sceneId && editor !== 'new'));
    const top = node('div', undefined, 'scene-card-top');
    top.append(node('span', `镜头 ${String(index + 1).padStart(2, '0')}`), node('span', `${scene.duration_seconds} 秒`));
    const bottom = node('div', undefined, 'scene-card-bottom');
    bottom.append(node('span', scene.format === 'html' ? 'HTML' : 'SVG'),
      node('span', isActiveOperation(scene.operation) ? statusLabel(scene.operation.status)
        : scene.head_version_id ? '已采用画面' : scene.versions?.length ? '有候选版本' : '待制作'));
    button.append(top, node('h3', scene.title), node('p', scene.narration || scene.visual_description || '还没有旁白'), bottom);
    button.addEventListener('click', () => {
      selectScene(state, scene.id);
      editor = null;
      render();
    });
    fragment.append(button);
  }
  $('scene-list').replaceChildren(fragment);
  $('scene-count').textContent = `${project.scenes.length} 个镜头`;
  $('scenes-empty').hidden = project.scenes.length > 0;
}

function renderOperation(operation, prefix) {
  const banner = $(prefix === 'storyboard' ? 'storyboard-status' : 'scene-status');
  const text = $(prefix === 'storyboard' ? 'storyboard-status-text' : 'scene-status-text');
  const cancel = $(prefix === 'storyboard' ? 'cancel-storyboard' : 'cancel-operation');
  const active = isActiveOperation(operation);
  banner.hidden = !operation;
  if (!operation) return;
  banner.classList.toggle('active', active);
  banner.classList.toggle('failed', ['failed', 'timed_out', 'rejected', 'needs_input'].includes(operation.status));
  const label = prefix === 'storyboard' ? '拆分镜头' : '制作画面';
  text.textContent = `${label} · ${statusLabel(operation.status)}${operation.error ? ` · ${ERROR_LABELS[operation.error] ?? operation.error}` : ''}`;
  cancel.hidden = !active;
  cancel.disabled = pending.has(`cancel:${operation.id}`) || cancelRequests.has(operation.id)
    || ['cancelling', 'cancel_requested'].includes(operation.status);
  cancel.textContent = cancel.disabled ? '已申请取消' : '申请取消';
}

function setValue(id, value) {
  const control = $(id);
  if (control.value !== String(value ?? '')) control.value = value ?? '';
}

function renderEditor(scene, project) {
  $('scene-form').hidden = editor === null;
  $('scene-detail').hidden = editor === 'new';
  $('toggle-scene-editor').hidden = editor === 'new';
  if (!editor) return;
  const key = draftKey(editor === 'new' ? null : scene?.id);
  const draft = editor === 'new' ? drafts.get(key) ?? createSceneDraft({}, project.revision)
    : syncSceneDraft(drafts.get(key), scene, project.revision);
  drafts.set(key, draft);
  $('scene-form-heading').textContent = editor === 'new' ? '新增镜头' : '编辑镜头';
  $('scene-title').textContent = editor === 'new' ? '安排一个新镜头' : scene.title;
  setValue('scene-title-input', draft.title);
  setValue('scene-narration', draft.narration);
  setValue('scene-visual', draft.visual_description);
  setValue('scene-duration', draft.duration_seconds);
  setValue('scene-format', draft.format);
  $('draft-status').textContent = draft.dirty ? '有未保存的修改'
    : editor === 'new' ? '保存后即可制作画面' : '已载入当前镜头';
  $('save-scene').disabled = pending.has(`save:${project.id}`);
  $('save-scene').textContent = pending.has(`save:${project.id}`) ? '正在保存…' : '保存镜头';
}

function clearPreview() {
  sourceController?.abort();
  displayedVersion = null;
  sourceVersion = null;
  $('scene-preview').removeAttribute('src');
  $('source-view').textContent = '';
}

function renderVersion(scene) {
  const version = selectedVersion(state, scene);
  const versions = orderedVersions(scene);
  const select = $('version-select');
  const options = versions.map(item => {
    const option = node('option', `V${item.number}${item.id === scene.head_version_id ? ' · 已采用' : ' · 候选'}`);
    option.value = item.id;
    return option;
  });
  if (!options.length) options.push(node('option', '尚无版本'));
  select.replaceChildren(...options);
  select.disabled = !versions.length;
  if (version) select.value = version.id;
  $('preview-empty').hidden = Boolean(version);
  $('scene-preview').hidden = !version || previewMode !== 'preview';
  $('source-view').hidden = !version || previewMode !== 'source';
  $('preview-tab').setAttribute('aria-selected', String(previewMode === 'preview'));
  $('source-tab').setAttribute('aria-selected', String(previewMode === 'source'));
  $('version-badge').hidden = !version;
  $('version-badge').textContent = version?.id === scene.head_version_id ? '已采用' : '候选';
  $('version-summary').textContent = version?.summary || (version ? `版本 V${version.number}` : '生成后会保留独立的候选版本');
  $('download-source').disabled = !version;
  const adopted = version?.id === scene.head_version_id;
  $('adopt-version').disabled = !version || adopted || pending.has(`adopt:${scene.id}`);
  $('adopt-version').textContent = adopted ? '当前采用版本' : '采用此版本';
  $('adoption-note').textContent = adopted ? '这个版本将用于项目导出。'
    : version ? '候选版本已保存，确认效果后可采用。' : '制作完成后，可以预览并选择采用的版本。';
  const latest = latestVersion(scene);
  const newer = version && latest && latest.number > version.number;
  $('new-version-notice').hidden = !newer;
  $('new-version-text').textContent = newer ? `已有 V${latest.number} 候选，当前仍在查看 V${version.number}。` : '';
  $('revision-base').textContent = version ? `基于 V${version.number} 修改` : '先生成第一个画面';
  if (version && displayedVersion !== version.id) {
    sourceController?.abort();
    sourceVersion = null;
    $('source-view').textContent = '';
    $('scene-preview').src = versionPath(version.id);
    displayedVersion = version.id;
  } else if (!version && displayedVersion) clearPreview();
  if (version && previewMode === 'source' && sourceVersion !== version.id) loadSource(version.id);
}

function updateGenerationButtons() {
  const scene = selectedScene(state);
  if (!scene) return;
  const dirty = drafts.get(draftKey(scene.id))?.dirty;
  const busy = pending.has(`generate:${scene.id}`) || pending.has(`revise:${scene.id}`)
    || isActiveOperation(scene.operation) || isActiveOperation(state.project?.storyboard_operation);
  $('generate-scene').disabled = busy || dirty;
  const version = selectedVersion(state, scene);
  const matchingFormat = version && version.format === scene.format;
  $('revise-scene').disabled = busy || dirty || !matchingFormat || !$('feedback').value.trim();
  if (version && !matchingFormat) $('revision-base').textContent = '画面格式已更新，请重新生成';
  $('generation-hint').textContent = dirty ? '请先保存镜头中的修改。' : busy ? '任务进行中，完成后会出现新的候选版本。'
    : '根据镜头说明制作候选画面，确认后可采用。';
}

function render() {
  const project = state.project;
  $('loading-view').hidden = !loading || Boolean(project);
  $('welcome').hidden = Boolean(state.projectId);
  $('workspace').hidden = !project;
  renderProjects();
  if (!project) return;
  project.scenes ??= [];
  $('project-title').textContent = project.title;
  const duration = project.scenes.reduce((sum, scene) => sum + Number(scene.duration_seconds || 0), 0);
  $('project-meta').textContent = `${project.scenes.length} 个镜头 · 预计 ${duration} 秒 · 文稿版本 ${project.revision}`;
  $('project-script').textContent = project.script;
  $('project-style').textContent = project.style || '还没有指定视觉方向。';
  $('export-project').disabled = pending.has(`export:${project.id}`);
  renderOperation(project.storyboard_operation, 'storyboard');
  const storyboardBusy = isActiveOperation(project.storyboard_operation) || pending.has(`storyboard:${project.id}`);
  $('create-storyboard').disabled = storyboardBusy || project.scenes.length > 0;
  $('create-storyboard').textContent = storyboardBusy ? '正在拆分镜头…' : '按文稿拆分镜头';
  $('add-scene').disabled = storyboardBusy;
  $('empty-add-scene').disabled = storyboardBusy;
  renderScenes(project);
  const scene = selectedScene(state);
  $('scene-workspace').hidden = !scene && editor !== 'new';
  $('select-scene-empty').hidden = Boolean(scene) || editor === 'new';
  if (scene || editor === 'new') {
    $('scene-number').textContent = editor === 'new' ? '新增镜头' : `镜头 ${String(project.scenes.indexOf(scene) + 1).padStart(2, '0')}`;
    $('scene-title').textContent = scene?.title ?? '安排一个新镜头';
    renderEditor(scene, project);
  }
  if (!scene || editor === 'new') return;
  $('scene-narration-text').textContent = scene.narration || '还没有旁白，可在编辑镜头中添加。';
  renderVersion(scene);
  renderOperation(scene.operation, 'scene');
  setValue('feedback', feedbacks.get(draftKey()) ?? '');
  updateGenerationButtons();
}

function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }
function schedulePolling() {
  stopPolling();
  if (document.hidden || !state.projectId || !activeOperations(state.project).length) return;
  pollTimer = setTimeout(() => loadProject().catch(showError), 3500);
}

async function loadProjects() {
  const sequence = ++listSequence;
  const result = await request('/api/projects');
  if (sequence !== listSequence) return;
  projects = result.projects ?? [];
  renderProjects();
}

async function loadProject() {
  if (!state.projectId || document.hidden) return;
  const ticket = issueProjectRead(state);
  readController?.abort();
  readController = new AbortController();
  try {
    const project = await request(`/api/projects/${encodeURIComponent(ticket.projectId)}`, { signal: readController.signal });
    if (acceptProjectRead(state, ticket, project)) {
      projects = updateProjectSummary(projects, project);
      loading = false;
      render();
    }
  } catch (error) {
    if (ticket.epoch === state.epoch && error.name !== 'AbortError') {
      loading = false;
      render();
      throw error;
    }
  } finally {
    if (ticket.epoch === state.epoch) schedulePolling();
  }
}

async function openProject(id) {
  if (state.projectId === id && state.project) return;
  stopPolling();
  readController?.abort();
  selectProject(state, id);
  editor = null;
  loading = true;
  previewMode = 'preview';
  clearPreview();
  $('global-error').hidden = true;
  render();
  await loadProject().catch(showError);
}

async function loadSource(versionId) {
  sourceController?.abort();
  sourceController = new AbortController();
  sourceVersion = versionId;
  $('source-view').textContent = '正在读取源码…';
  try {
    const source = await request(versionPath(versionId, 'source'), { output: 'text', signal: sourceController.signal });
    if (displayedVersion !== versionId || sourceVersion !== versionId) return;
    $('source-view').textContent = source.length > 524288 ? `${source.slice(0, 524288)}\n\n预览内容过长，请下载完整源码。` : source;
  } catch (error) {
    if (error.name === 'AbortError' || displayedVersion !== versionId) return;
    sourceVersion = null;
    $('source-view').textContent = '源码读取失败，重新选择「查看源码」可重试。';
    showError(error);
  }
}

async function perform(key, action, after) {
  if (pending.has(key)) return;
  const projectId = state.projectId;
  const epoch = state.epoch;
  pending.add(key);
  $('global-error').hidden = true;
  render();
  try {
    const result = await action();
    if (state.epoch === epoch && state.projectId === projectId) await after?.(result);
    await loadProjects();
    if (state.epoch === epoch && state.projectId === projectId) await loadProject();
    return result;
  } catch (error) {
    showError(error);
  } finally {
    pending.delete(key);
    render();
  }
}

function startNewScene() {
  if (!state.project) return;
  editor = 'new';
  const key = draftKey(null);
  if (!drafts.has(key)) drafts.set(key, createSceneDraft({}, state.project.revision));
  render();
  $('scene-title-input').focus();
}

async function saveScene(event) {
  event.preventDefault();
  if (!$('scene-form').reportValidity() || !state.project) return;
  const projectId = state.projectId;
  const sceneId = editor === 'new' ? null : state.sceneId;
  const key = draftKey(sceneId);
  const draft = drafts.get(key);
  const { expected_revision, dirty, ...fields } = draft;
  const payload = sceneId ? { ...fields, expected_revision } : fields;
  await perform(`save:${projectId}`, () => request(sceneId ? `/api/scenes/${encodeURIComponent(sceneId)}`
    : `/api/projects/${encodeURIComponent(projectId)}/scenes`, { method: sceneId ? 'PATCH' : 'POST', body: payload }), result => {
    drafts.delete(key);
    editor = null;
    if (!sceneId) state.sceneId = result?.scenes?.at(-1)?.id ?? result?.id ?? state.sceneId;
    success('镜头已保存。');
  });
}

async function generate(kind) {
  const scene = selectedScene(state);
  if (!scene) return;
  const base = selectedVersion(state, scene);
  const instruction = $('feedback').value.trim();
  if (kind === 'revise' && (!base || !instruction)) return;
  const payload = { base_version_id: kind === 'revise' ? base?.id ?? null : null,
    instruction, expected_revision: state.project.revision };
  await perform(`${kind}:${scene.id}`, () => request(`/api/scenes/${encodeURIComponent(scene.id)}/${kind}`,
    { method: 'POST', body: payload }), () => success('任务已提交，可以继续查看其他镜头。'));
}

async function cancelOperation(operation) {
  if (!isActiveOperation(operation)) return;
  await perform(`cancel:${operation.id}`, () => request(`/api/operations/${encodeURIComponent(operation.id)}/cancel`,
    { method: 'POST', body: {} }), () => {
    cancelRequests.add(operation.id);
    success('取消请求已提交，任务状态将继续更新。');
  });
}

async function download(path, filename, key) {
  if (pending.has(key)) return;
  pending.add(key);
  render();
  try {
    const blob = await request(path, { output: 'blob' });
    const url = URL.createObjectURL(blob);
    const anchor = node('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { showError(error); }
  finally { pending.delete(key); render(); }
}

function openCreateDialog() {
  $('create-error').hidden = true;
  if (!$('create-project-dialog').open) $('create-project-dialog').showModal();
  $('new-title').focus();
}
function closeCreateDialog() {
  if (!pending.has('create-project')) $('create-project-dialog').close();
}

$('new-project').addEventListener('click', openCreateDialog);
$('welcome-create').addEventListener('click', openCreateDialog);
for (const id of ['close-project-dialog', 'cancel-create-project']) $(id).addEventListener('click', closeCreateDialog);
$('create-project-dialog').addEventListener('cancel', event => {
  if (pending.has('create-project')) event.preventDefault();
});
$('create-project-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (pending.has('create-project') || !$('create-project-form').reportValidity()) return;
  pending.add('create-project');
  $('submit-create-project').disabled = true;
  const body = { title: $('new-title').value.trim(), script: $('new-script').value.trim(), style: $('new-style').value.trim() };
  try {
    const project = await request('/api/projects', { method: 'POST', body });
    $('create-project-dialog').close();
    $('create-project-form').reset();
    await loadProjects();
    await openProject(project.id);
    success('项目已创建。可以拆分镜头或手动添加。');
  } catch (error) { showError(error); }
  finally { pending.delete('create-project'); $('submit-create-project').disabled = false; }
});

$('add-scene').addEventListener('click', startNewScene);
$('empty-add-scene').addEventListener('click', startNewScene);
$('scene-form').addEventListener('submit', saveScene);
$('scene-form').addEventListener('input', () => {
  const draft = currentDraft();
  if (!draft) return;
  Object.assign(draft, {
    title: $('scene-title-input').value, narration: $('scene-narration').value,
    visual_description: $('scene-visual').value, duration_seconds: Number($('scene-duration').value),
    format: $('scene-format').value, dirty: true,
  });
  $('draft-status').textContent = '有未保存的修改';
  updateGenerationButtons();
});
$('toggle-scene-editor').addEventListener('click', () => { editor = editor ? null : 'existing'; render(); });
$('cancel-scene-edit').addEventListener('click', () => { editor = null; render(); });
$('reset-scene-draft').addEventListener('click', () => {
  const key = draftKey(editor === 'new' ? null : state.sceneId);
  if (drafts.get(key)?.dirty && !window.confirm('重新载入会清除这个镜头尚未保存的修改，继续吗？')) return;
  drafts.set(key, createSceneDraft(editor === 'new' ? {} : selectedScene(state), state.project.revision));
  render();
});
$('create-storyboard').addEventListener('click', () => {
  if (!state.project || state.project.scenes.length) return;
  const id = state.projectId;
  perform(`storyboard:${id}`, () => request(`/api/projects/${encodeURIComponent(id)}/storyboard`, {
    method: 'POST', body: { expected_revision: state.project.revision },
  }), () => success('拆分任务已提交，镜头准备好后会出现在左侧。'));
});
$('feedback').addEventListener('input', () => { feedbacks.set(draftKey(), $('feedback').value); updateGenerationButtons(); });
$('generate-scene').addEventListener('click', () => generate('generate'));
$('revise-scene').addEventListener('click', () => generate('revise'));
$('cancel-storyboard').addEventListener('click', () => cancelOperation(state.project?.storyboard_operation));
$('cancel-operation').addEventListener('click', () => cancelOperation(selectedScene(state)?.operation));
$('version-select').addEventListener('change', event => {
  chooseVersion(state, state.sceneId, event.target.value);
  render();
});
$('view-latest').addEventListener('click', () => {
  const scene = selectedScene(state);
  const latest = latestVersion(scene);
  if (latest) chooseVersion(state, scene.id, latest.id);
  render();
});
for (const mode of ['preview', 'source']) $(`${mode}-tab`).addEventListener('click', () => { previewMode = mode; render(); });
$('adopt-version').addEventListener('click', () => {
  const scene = selectedScene(state);
  const version = selectedVersion(state, scene);
  if (!scene || !version || version.id === scene.head_version_id) return;
  perform(`adopt:${scene.id}`, () => request(`/api/scenes/${encodeURIComponent(scene.id)}/adopt`, {
    method: 'POST', body: { version_id: version.id, expected_head_version_id: scene.head_version_id },
  }), () => success(`已采用 V${version.number}。`));
});
$('download-source').addEventListener('click', () => {
  const version = selectedVersion(state);
  if (version) download(versionPath(version.id, 'source'), `scene-v${version.number}.${version.format === 'html' ? 'html' : 'svg'}`, `source:${version.id}`);
});
$('export-project').addEventListener('click', () => {
  if (state.projectId) download(`/api/projects/${encodeURIComponent(state.projectId)}/export`, 'video-workbench-project.zip', `export:${state.projectId}`);
});
$('dismiss-error').addEventListener('click', () => { $('global-error').hidden = true; });
async function refresh() {
  $('global-error').hidden = true;
  try {
    const session = await request('/api/session');
    csrfToken = session.csrf_token;
    $('session-label').textContent = session.user_id ? '已连接工作台' : '已连接';
    await loadProjects();
    if (state.projectId) await loadProject();
    else if (projects.length) await openProject(projects[0].id);
    else render();
  } catch (error) { $('session-label').textContent = '连接未完成'; showError(error); }
}
$('retry-read').addEventListener('click', refresh);
$('refresh-all').addEventListener('click', refresh);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { stopPolling(); readController?.abort(); }
  else if (state.projectId) loadProject().catch(showError);
});
window.addEventListener('pagehide', () => { stopPolling(); readController?.abort(); sourceController?.abort(); });
window.addEventListener('beforeunload', event => {
  if ([...drafts.values()].some(draft => draft.dirty)) { event.preventDefault(); event.returnValue = ''; }
});

refresh();
