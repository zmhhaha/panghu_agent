import {
  activeOperations, chooseVersion, createIntentKeys, createSceneDraft, createStoryboardRequest, createWorkspaceState,
  acceptProjectRead, isActiveOperation, issueProjectRead, latestVersion, orderedVersions,
  selectProject, selectScene, selectedScene, selectedVersion, statusLabel, syncSceneDraft,
  updateProjectSummary, versionPath,
  audioPath, chooseAudioVersion, createCaptionDraft, orderedAudioVersions, parsePronunciation,
  pronunciationText, selectedAudioVersion, timelinePosition, validateCaptionCues, deliveryIssues,
} from './model.mjs';

const $ = id => document.getElementById(id);
const state = createWorkspaceState();
const intentKeys = createIntentKeys();
const drafts = new Map();
const feedbacks = new Map();
const pending = new Set();
const cancelRequests = new Set();
const speechDrafts = new Map();
const timingDrafts = new Map();
const captionDrafts = new Map();
const renderHistories = new Map();
let sessionSettings = {};
let displayedAudioVersion = null;
let displayedAudioScene = null;
let uploadSelection = null;
let renderedCaptionDraft = null;
let player = null;
let playerAnimation = null;
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
  video_scene_limit_exceeded: '返回的镜头超过本次上限，未保存分镜。',
  agent_not_configured: '尚未配置 Agent 服务，仍可手动整理文稿与镜头。',
  service_unavailable: 'Agent 服务暂时不可用，请稍后重试。',
  idempotency_conflict: '这个操作与先前提交的内容不一致，请刷新后重试。',
  not_found: '内容不存在或你没有访问权限。',
  speech_not_configured: '配音服务尚未连接，可以先上传自己的 WAV 配音。',
  audio_stale: '旁白或配音设置已更新，请生成或上传匹配当前旁白的配音。',
  audio_version_stale: '这条历史配音已不匹配当前旁白，请重新制作。',
  audio_head_conflict: '采用的配音已发生变化，请重新读取项目。',
  invalid_audio: '音频未通过校验，请上传完整、非静音的 PCM WAV。',
  narration_required: '请先填写并保存这个镜头的旁白。',
  speech_busy: '配音服务正在处理其他任务，请稍后重新提交。',
  submission_uncertain: '配音提交结果未确认。请检查已有任务，系统不会自动重复生成。',
  media_not_configured: '视频导出暂时不可用，仍可导出项目素材。',
  captions_stale: '配音已变化，请重新整理并校正字幕。',
  missing_audio: '部分镜头缺少可用的已采用配音，请先处理提示的镜头。',
  missing_visual: '部分镜头尚未采用画面，请先选择画面版本。',
  missing_current_audio: '部分镜头缺少当前旁白的已采用配音，请先生成或上传并采用。',
  unadopted_scenes: '请先为每个镜头采用一个画面版本。',
  missing_captions: '部分镜头还没有字幕，请先估算并校正，或取消「显示字幕」。',
  stale_captions: '配音或留白已变化，请重新整理相关镜头的字幕。',
  invalid_captions: '请检查字幕时间是否位于配音范围内，保持先后顺序，并填写字幕内容。',
  render_not_configured: '视频导出服务尚未连接，仍可导出项目素材。',
  render_in_progress: '已有成片正在导出，请等待完成或取消后再试。',
  audio_operation_in_progress: '这个镜头已有配音任务，请等待完成或取消后再试。',
  audio_narration_too_long: '这个镜头的旁白超过单次配音限制，请拆成更短的镜头。',
  speech_invalid_audio: '音频未通过校验，请使用完整、可解码的 PCM WAV。',
  speech_invalid_input: '请检查语速、读音修正与旁白长度。',
  audio_upload_too_large: '配音文件过大，单个文件最多 32 MiB。',
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

async function request(path, { method = 'GET', body, signal, output = 'json', raw = false, intentBody } = {}) {
  const headers = { Accept: output === 'json' ? 'application/json' : '*/*' };
  const mutating = !['GET', 'HEAD'].includes(method);
  if (mutating) {
    if (!csrfToken) throw new ApiError(401, {});
    headers['X-CSRF-Token'] = csrfToken;
    headers['Idempotency-Key'] = intentKeys.get(method, path, intentBody ?? body);
    headers['Content-Type'] = raw ? 'audio/wav' : 'application/json';
  }
  const response = await fetch(path, {
    method, headers, signal, credentials: 'same-origin', redirect: 'error',
    ...(body === undefined ? {} : { body: raw ? body : JSON.stringify(body) }),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new ApiError(response.status, detail);
  }
  const result = response.status === 204 ? null : output === 'blob' ? await response.blob()
    : output === 'text' ? await response.text() : await response.json();
  if (mutating) intentKeys.complete(method, path, intentBody ?? body);
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
        : scene.head_version_id ? '已采用画面' : scene.versions?.length ? '有候选版本' : '待制作'),
      node('span', scene.audio_stale ? '配音已过期' : scene.head_audio_version_id ? '已采用配音' : '未采用配音',
        `scene-audio-status${scene.audio_stale ? ' stale' : ''}`));
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

function adoptedAudio(scene) {
  return scene?.audio_versions?.find(version => version.id === scene.head_audio_version_id) ?? null;
}

function effectiveSceneSeconds(scene) {
  const audio = adoptedAudio(scene);
  return Math.ceil(24 * Math.max(Number(scene.duration_seconds), Number(audio?.duration_seconds ?? 0)
    + Number(scene.leading_seconds ?? 0) + Number(scene.trailing_seconds ?? 0))) / 24;
}

function validSceneCaptions(scene, cues) {
  return validateCaptionCues(cues, effectiveSceneSeconds(scene), {
    leadingSeconds: Number(scene.leading_seconds ?? 0), audioSeconds: Number(adoptedAudio(scene)?.duration_seconds ?? 0),
  });
}

function audioDraft(map, scene, factory) {
  const key = draftKey(scene.id);
  const previous = map.get(key);
  if (previous?.dirty) return previous;
  const draft = factory();
  map.set(key, draft);
  return draft;
}

function seconds(value) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(2).replace(/\.?0+$/, '') : '—';
}

function renderAudioOperation(operation) {
  const banner = $('audio-status');
  const active = isActiveOperation(operation);
  banner.hidden = !operation;
  if (!operation) return;
  banner.classList.toggle('active', active);
  banner.classList.toggle('failed', ['failed', 'timed_out', 'uncertain'].includes(operation.status));
  $('audio-status-text').textContent = `制作配音 · ${statusLabel(operation.status)}${operation.error ? ` · ${ERROR_LABELS[operation.error] ?? '请检查任务后重新提交。'}` : ''}`;
  $('cancel-audio-operation').hidden = !active;
  $('cancel-audio-operation').disabled = pending.has(`audio-cancel:${operation.id}`)
    || cancelRequests.has(operation.id) || operation.status === 'cancelling';
}

function renderCaptionRows(draft) {
  if (renderedCaptionDraft === draft) return;
  renderedCaptionDraft = draft;
  const fragment = document.createDocumentFragment();
  draft.cues.forEach((cue, index) => {
    const row = node('div', undefined, 'caption-cue');
    for (const field of ['start', 'end', 'text']) {
      const control = node(field === 'text' ? 'textarea' : 'input');
      control.value = cue[field];
      control.setAttribute('aria-label', `第${index + 1}条字幕${field === 'text' ? '内容' : field === 'start' ? '开始秒数' : '结束秒数'}`);
      if (field === 'text') { control.rows = 2; control.maxLength = 500; }
      else { control.type = 'number'; control.min = '0'; control.step = '0.01'; }
      control.addEventListener('input', () => {
        cue[field] = field === 'text' ? control.value : control.valueAsNumber;
        draft.dirty = true;
        updateCaptionButtons();
      });
      row.append(control);
    }
    const remove = node('button', '×', 'icon-button');
    remove.type = 'button';
    remove.setAttribute('aria-label', `删除第${index + 1}条字幕`);
    remove.addEventListener('click', () => {
      draft.cues.splice(index, 1); draft.dirty = true;
      renderedCaptionDraft = null;
      renderCaptionRows(draft); updateCaptionButtons();
    });
    row.append(remove); fragment.append(row);
  });
  $('caption-cues').replaceChildren(fragment);
}

function updateCaptionButtons() {
  const scene = selectedScene(state);
  if (!scene) return;
  const draft = captionDrafts.get(draftKey(scene.id));
  const audio = adoptedAudio(scene);
  const matches = draft?.audio_version_id === scene.head_audio_version_id && !scene.audio_stale
    && draft?.leading_seconds === Number(scene.leading_seconds ?? 0)
    && draft?.trailing_seconds === Number(scene.trailing_seconds ?? 0);
  const valid = matches && audio && draft?.cues.length > 0 && validSceneCaptions(scene, draft.cues);
  const needsReview = Boolean(scene.captions?.requires_review);
  $('save-captions').disabled = (!draft?.dirty && !needsReview) || !valid || pending.has(`captions:${scene.id}`);
  $('save-captions').textContent = needsReview ? '确认并保存字幕' : '保存校正字幕';
  $('add-caption').disabled = !matches || !audio || (draft?.cues.length ?? 0) >= 200;
  $('captions-draft-note').textContent = !matches && draft?.dirty ? '采用配音或留白已变化，请重新载入后校正。'
    : draft?.dirty && !valid ? '检查时间顺序、音频边界与字幕内容。' : draft?.dirty ? '有未保存的校正'
      : needsReview ? '试听后可确认当前时间，或直接修改。' : '';
}

function renderAudio(scene, project) {
  const settings = scene.speech_settings ?? {};
  const speech = audioDraft(speechDrafts, scene, () => ({
    speed: settings.speed ?? 1, pronunciation: pronunciationText(settings.pronunciation),
    expected_revision: project.revision, dirty: false,
  }));
  setValue('speech-speed', speech.speed);
  setValue('speech-pronunciation', speech.pronunciation);
  $('speech-draft-note').textContent = speech.dirty ? '配音设置尚未保存' : '使用当前已保存的配音设置';
  $('save-speech-settings').disabled = !speech.dirty || pending.has(`speech:${scene.id}`);
  const voice = settings.voice ?? sessionSettings.speech_voice ?? 'zm_yunjian';
  $('audio-voice-label').textContent = voice === 'zm_yunjian' ? '云健 · 中文男声' : voice;
  $('audio-provider-note').textContent = sessionSettings.speech_configured ? '使用当前镜头已保存的旁白制作，生成完成后可试听比较。'
    : '配音服务尚未连接，可以先上传自己的 WAV 配音。';
  const dirty = drafts.get(draftKey(scene.id))?.dirty;
  const busy = isActiveOperation(scene.audio_operation) || pending.has(`audio-generate:${scene.id}`);
  $('generate-audio').disabled = !sessionSettings.speech_configured || !scene.narration?.trim()
    || dirty || speech.dirty || busy;
  $('audio-generation-hint').textContent = dirty ? '请先保存镜头旁白。' : speech.dirty ? '请先保存语速与读音设置。'
    : busy ? '配音任务进行中，可以继续查看其他镜头。' : '生成后先试听，再选择采用。';
  renderAudioOperation(scene.audio_operation);
  const versions = orderedAudioVersions(scene);
  const candidate = selectedAudioVersion(state, scene);
  const options = versions.map(version => {
    const option = node('option', `A${version.number}${version.id === scene.head_audio_version_id ? ' · 已采用' : ' · 候选'}${version.stale ? ' · 需更新' : ''}`);
    option.value = version.id;
    return option;
  });
  $('audio-version-select').replaceChildren(...(options.length ? options : [node('option', '尚无配音')]));
  $('audio-version-select').disabled = !versions.length;
  if (candidate) $('audio-version-select').value = candidate.id;
  if (displayedAudioScene !== scene.id) {
    $('audio-preview').pause(); $('audio-upload-file').value = '';
    uploadSelection = null; displayedAudioScene = scene.id;
  }
  if (displayedAudioVersion !== candidate?.id) {
    $('audio-preview').pause();
    if (candidate) $('audio-preview').src = audioPath(candidate.id);
    else $('audio-preview').removeAttribute('src');
    $('audio-preview').load(); displayedAudioVersion = candidate?.id ?? null;
  }
  $('audio-preview').hidden = !candidate;
  const adopted = candidate?.id === scene.head_audio_version_id;
  $('audio-version-badge').hidden = !candidate;
  $('audio-version-badge').textContent = candidate?.stale ? '需更新' : adopted ? '已采用配音' : '候选配音';
  $('audio-version-summary').textContent = candidate
    ? `A${candidate.number} · ${candidate.source === 'upload' ? '上传配音' : '生成配音'} · ${seconds(candidate.duration_seconds)} 秒`
    : '生成或上传配音后，可以在这里试听。';
  $('audio-stale-note').hidden = !candidate?.stale;
  $('audio-adoption-note').textContent = adopted ? scene.audio_stale ? '当前采用的配音需要更新。' : '这条配音将用于项目预览与成片。'
    : '确认试听效果后再采用，画面采用保持独立。';
  $('adopt-audio').disabled = !candidate || candidate.stale || adopted || pending.has(`audio-adopt:${scene.id}`);
  $('adopt-audio').textContent = adopted ? '当前采用配音' : '采用此配音';
  $('download-audio').disabled = !candidate;
  $('upload-audio').disabled = !uploadSelection || pending.has(`audio-upload:${scene.id}`) || dirty || speech.dirty;
  const timing = audioDraft(timingDrafts, scene, () => ({
    leading_seconds: scene.leading_seconds ?? 0, trailing_seconds: scene.trailing_seconds ?? 0,
    expected_revision: project.revision, dirty: false,
  }));
  setValue('leading-seconds', timing.leading_seconds); setValue('trailing-seconds', timing.trailing_seconds);
  $('save-timing').disabled = !timing.dirty || pending.has(`timing:${scene.id}`);
  $('timing-draft-note').textContent = timing.dirty ? '留白尚未保存' : '';
  const head = adoptedAudio(scene);
  const duration = head && !scene.audio_stale ? Math.max(Number(scene.duration_seconds), Number(head.duration_seconds)
    + Number(scene.leading_seconds ?? 0) + Number(scene.trailing_seconds ?? 0)) : Number(scene.duration_seconds);
  $('timing-summary').textContent = head && !scene.audio_stale
    ? `配音 ${seconds(head.duration_seconds)} 秒 · 原设定 ${seconds(scene.duration_seconds)} 秒 · 含留白后镜头 ${seconds(duration)} 秒。完整保留旁白。`
    : scene.audio_stale ? '采用配音已过期，更新后重新安排时长与字幕。' : '采用配音后按实际音频时长安排，不截断旁白。';
  const previous = captionDrafts.get(draftKey(scene.id));
  let captions = previous;
  const signature = JSON.stringify([scene.head_audio_version_id, scene.leading_seconds, scene.trailing_seconds, scene.captions]);
  if (!captions?.dirty && captions?.signature !== signature) {
    captions = { ...createCaptionDraft(scene, project.revision), signature };
    captionDrafts.set(draftKey(scene.id), captions);
  }
  if (captions && !captions.dirty) captions.expected_revision = project.revision;
  renderCaptionRows(captions);
  const stale = scene.captions?.stale || scene.captions?.timing_stale || scene.audio_stale;
  $('captions-status').textContent = stale ? '旁白、配音或留白已变化，字幕需要重新整理。' : !scene.captions ? '采用配音后，先估算时间，再试听校正。'
    : scene.captions.requires_review ? '字幕时间为估算，需结合试听校正。' : '已保存校正字幕。更换配音后需重新校正。';
  $('estimate-captions').disabled = !head || scene.audio_stale || pending.has(`captions:${scene.id}`);
  updateCaptionButtons();
}

function renderDelivery(project) {
  const scenes = project.scenes ?? [];
  const visualCount = scenes.filter(scene => scene.head_version_id).length;
  const audioCount = scenes.filter(scene => scene.head_audio_version_id && !scene.audio_stale).length;
  $('delivery-summary').textContent = `已采用画面 ${visualCount}/${scenes.length} · 可用配音 ${audioCount}/${scenes.length}。预览和成片使用已采用的版本。`;
  const issues = deliveryIssues(project, { silent: $('render-silent').checked, subtitles: $('render-subtitles').checked });
  const reasons = { missing_visual: '未采用画面', missing_audio: '未采用配音', stale_audio: '配音已过期',
    missing_captions: '缺少字幕', stale_captions: '字幕已过期' };
  const issueList = document.createDocumentFragment();
  for (const issue of issues) {
    const item = node('li');
    const button = node('button', undefined, 'delivery-issue'); button.type = 'button';
    button.append(node('strong', `镜头 ${String(issue.number).padStart(2, '0')} · ${issue.title || '未命名镜头'}`),
      node('span', issue.reasons.map(reason => reasons[reason]).join(' · ')));
    button.addEventListener('click', () => {
      selectScene(state, issue.sceneId); editor = null; render();
      $('scene-workspace').scrollIntoView({ block: 'start' });
      $('toggle-scene-editor').focus({ preventScroll: true });
    });
    item.append(button); issueList.append(item);
  }
  $('delivery-issues').replaceChildren(issueList);
  $('delivery-issues').hidden = !issues.length;
  $('preview-project').disabled = !scenes.length || pending.has(`preview:${project.id}`);
  $('render-project').disabled = !scenes.length || !sessionSettings.media_configured || pending.has(`render:${project.id}`);
  $('download-subtitles').disabled = !scenes.length || pending.has(`subtitles:${project.id}`);
  $('render-hint').textContent = sessionSettings.media_configured ? '导出在后台进行，过程中可以继续编辑；结果保留导出开始时的版本。'
    : '视频导出暂时不可用，仍可预览并导出原项目素材。';
  const fragment = document.createDocumentFragment();
  for (const item of renderHistories.get(project.id) ?? []) {
    const row = node('div', undefined, 'render-item');
    const label = node('div');
    label.append(node('strong', `${item.silent ? '静音视频' : '带声音视频'} · ${statusLabel(item.status)}`),
      node('small', `文稿版本 ${item.project_revision ?? '—'}${item.project_revision !== project.revision ? ' · 项目已有后续修改' : ''}${item.error ? ` · ${ERROR_LABELS[item.error] ?? '导出未完成，请检查资源后重试。'}` : ''}`));
    row.append(label);
    if (['completed', 'succeeded'].includes(item.status)) {
      const button = node('button', '下载 MP4 ↓', 'button secondary'); button.type = 'button';
      button.addEventListener('click', () => download(`/api/renders/${encodeURIComponent(item.id)}/video`,
        `video-${item.id}.mp4`, `render-download:${item.id}`)); row.append(button);
    } else if (isActiveOperation(item)) {
      const button = node('button', '申请取消', 'text-button'); button.type = 'button';
      button.disabled = pending.has(`render-cancel:${item.id}`) || cancelRequests.has(item.id) || item.status === 'cancelling';
      button.addEventListener('click', () => perform(`render-cancel:${item.id}`, () => request(`/api/renders/${encodeURIComponent(item.id)}/cancel`,
        { method: 'POST', body: {} }), () => { cancelRequests.add(item.id); success('已申请取消导出。'); })); row.append(button);
    }
    fragment.append(row);
  }
  $('render-history').replaceChildren(fragment);
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
  $('storyboard-max-scenes').disabled = storyboardBusy || project.scenes.length > 0;
  $('create-storyboard').textContent = storyboardBusy ? '正在拆分镜头…' : '按文稿拆分镜头';
  $('add-scene').disabled = storyboardBusy;
  $('empty-add-scene').disabled = storyboardBusy;
  renderScenes(project);
  renderDelivery(project);
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
  renderAudio(scene, project);
}

function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }
function schedulePolling() {
  stopPolling();
  if (document.hidden || !state.projectId || (!activeOperations(state.project).length
      && !(renderHistories.get(state.projectId) ?? []).some(isActiveOperation))) return;
  pollTimer = setTimeout(() => loadProject().catch(showError), 3500);
}

async function loadProjects() {
  const sequence = ++listSequence;
  const result = await request('/api/projects');
  if (sequence !== listSequence) return;
  projects = result.projects ?? [];
  renderProjects();
}

async function loadRenders(projectId, epoch) {
  if (!sessionSettings.media_configured) return;
  const result = await request(`/api/projects/${encodeURIComponent(projectId)}/renders`);
  if (state.projectId !== projectId || state.epoch !== epoch) return;
  renderHistories.set(projectId, result.renders ?? []);
  renderDelivery(state.project);
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
      await loadRenders(ticket.projectId, ticket.epoch);
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
  $('audio-preview').pause();
  closeProjectPlayer();
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

function clockLabel(value) {
  const whole = Math.max(0, Math.floor(value));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

function playerSeconds() {
  if (!player) return 0;
  if (player.playing && player.speakingScene && !player.silent) {
    const audio = $('project-player-audio');
    const scene = player.speakingScene;
    const position = audio.readyState < 1 ? player.position : Number(scene.start_seconds)
      + Number(scene.leading_seconds ?? 0) + (audio.ended ? Number(scene.audio_duration_seconds) : audio.currentTime);
    return Math.min(player.timeline.duration_seconds, position);
  }
  return Math.min(player.timeline.duration_seconds,
    player.position + (player.playing ? (performance.now() - player.startedAt) / 1000 : 0));
}

function pauseProjectPlayer() {
  if (!player) return;
  player.position = playerSeconds(); player.playing = false;
  player.speakingScene = null;
  cancelAnimationFrame(playerAnimation); playerAnimation = null;
  $('project-player-audio').pause();
  $('project-player-toggle').textContent = '播放';
}

function closeProjectPlayer() {
  pauseProjectPlayer(); player = null;
  $('project-player-audio').removeAttribute('src'); $('project-player-audio').load();
  $('project-player-frame').removeAttribute('src');
  if ($('project-player-dialog').open) $('project-player-dialog').close();
}

function updatePlayerFrame() {
  if (!player) return;
  const position = playerSeconds();
  const current = timelinePosition(player.timeline, position);
  const audio = $('project-player-audio');
  $('project-player-seek').value = position;
  $('project-player-clock').textContent = `${clockLabel(position)} / ${clockLabel(player.timeline.duration_seconds)}`;
  if (!current) {
    audio.pause();
    $('project-player-caption').textContent = ''; $('project-player-caption').hidden = true;
    if (position >= player.timeline.duration_seconds) pauseProjectPlayer();
    return;
  }
  const { scene } = current;
  if (player.sceneId !== scene.id) {
    audio.pause();
    $('project-player-frame').src = versionPath(scene.visual_version_id);
    if (scene.audio_version_id && !player.silent) audio.src = audioPath(scene.audio_version_id);
    else audio.removeAttribute('src');
    audio.load(); player.sceneId = scene.id; player.speakingScene = null;
  }
  $('project-player-scene').textContent = `${scene.title ?? '镜头'} · ${seconds(current.sceneTime)} / ${seconds(scene.duration_seconds)} 秒${player.silent ? ' · 静音预览' : ''}`;
  const subtitle = player.subtitles ? current.caption : '';
  $('project-player-caption').textContent = subtitle;
  $('project-player-caption').hidden = !subtitle;
  if (player.playing && current.speaking && !player.silent) {
    if (!player.speakingScene) {
      player.position = position; player.startedAt = performance.now();
      player.speakingScene = scene; player.audioTarget = current.audioTime;
    }
    if (audio.readyState >= 1 && Math.abs(audio.currentTime - current.audioTime) > 0.35) audio.currentTime = current.audioTime;
    if (audio.paused && !player.audioStarting) {
      const snapshot = player;
      snapshot.audioStarting = true;
      audio.play().catch(error => {
        if (error.name === 'AbortError' || !snapshot.playing) return;
        if (player !== snapshot) return;
        pauseProjectPlayer();
        $('project-player-note').textContent = '配音未能开始播放，请检查连接后重新播放。';
      }).finally(() => { snapshot.audioStarting = false; });
    }
  } else {
    audio.pause();
    if (player.speakingScene) {
      player.position = position; player.startedAt = performance.now(); player.speakingScene = null;
    }
  }
  if (player.playing) playerAnimation = requestAnimationFrame(updatePlayerFrame);
}

async function openProjectPlayer() {
  if (!state.project) return;
  const projectId = state.projectId;
  const epoch = state.epoch;
  const silent = $('render-silent').checked;
  const subtitles = $('render-subtitles').checked;
  await perform(`preview:${projectId}`, () => request(`/api/projects/${encodeURIComponent(projectId)}/timeline?silent=${silent}&subtitles=${subtitles}`), timeline => {
    if (state.projectId !== projectId || state.epoch !== epoch) return;
    closeProjectPlayer();
    player = { timeline, silent, subtitles, position: 0, playing: false, startedAt: 0, sceneId: null, audioStarting: false };
    $('audio-preview').pause();
    $('project-player-heading').textContent = state.project.title;
    $('project-player-seek').max = timeline.duration_seconds;
    $('project-player-note').textContent = '预览使用打开时的采用版本。调整项目后，重新打开预览查看最新内容。';
    $('project-player-dialog').showModal();
    updatePlayerFrame();
  });
}

function captureSpeechDraft() {
  const scene = selectedScene(state);
  if (!scene) return;
  const draft = speechDrafts.get(draftKey(scene.id));
  Object.assign(draft, { speed: $('speech-speed').value, pronunciation: $('speech-pronunciation').value, dirty: true });
  $('speech-draft-note').textContent = '配音设置尚未保存';
  $('save-speech-settings').disabled = false;
  $('generate-audio').disabled = true;
  $('upload-audio').disabled = true;
  $('audio-generation-hint').textContent = '请先保存语速与读音设置。';
}

function showInputError(message) {
  showError(new ApiError(422, { detail: { message } }));
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
  $('generate-audio').disabled = true;
  $('upload-audio').disabled = true;
  $('audio-generation-hint').textContent = '请先保存镜头中的修改。';
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
  if (!state.project || state.project.scenes.length || !$('storyboard-max-scenes').reportValidity()) return;
  const id = state.projectId;
  const { path, body } = createStoryboardRequest(state.project, $('storyboard-max-scenes').valueAsNumber);
  perform(`storyboard:${id}`, () => request(path, {
    method: 'POST', body,
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

for (const id of ['speech-speed', 'speech-pronunciation']) $(id).addEventListener('input', captureSpeechDraft);
$('save-speech-settings').addEventListener('click', () => {
  const scene = selectedScene(state); if (!scene || !$('speech-speed').reportValidity()) return;
  const key = draftKey(scene.id); const draft = speechDrafts.get(key);
  let pronunciation;
  try { pronunciation = parsePronunciation(draft.pronunciation); }
  catch (error) { showInputError(error.message); return; }
  const body = { expected_revision: draft.expected_revision, speed: Number(draft.speed), pronunciation };
  perform(`speech:${scene.id}`, () => request(`/api/scenes/${encodeURIComponent(scene.id)}/speech`, { method: 'PATCH', body }),
    () => { speechDrafts.delete(key); success('配音设置已保存，已有配音的适用状态会同步更新。'); });
});
for (const [id, map] of [['reset-speech-settings', speechDrafts], ['reset-timing', timingDrafts], ['reset-captions', captionDrafts]]) {
  $(id).addEventListener('click', () => {
    const scene = selectedScene(state); if (!scene) return;
    const key = draftKey(scene.id);
    if (map.get(key)?.dirty && !window.confirm('重新载入会清除这里尚未保存的修改，继续吗？')) return;
    map.delete(key); renderedCaptionDraft = null; render();
  });
}
$('generate-audio').addEventListener('click', () => {
  const scene = selectedScene(state); if (!scene) return;
  const body = { expected_revision: state.project.revision };
  perform(`audio-generate:${scene.id}`, () => request(`/api/scenes/${encodeURIComponent(scene.id)}/audio/generate`,
    { method: 'POST', body }), () => success('配音任务已提交，完成后会保存为候选。'));
});
$('cancel-audio-operation').addEventListener('click', () => {
  const operation = selectedScene(state)?.audio_operation;
  if (!isActiveOperation(operation)) return;
  perform(`audio-cancel:${operation.id}`, () => request(`/api/audio-operations/${encodeURIComponent(operation.id)}/cancel`,
    { method: 'POST', body: {} }), () => { cancelRequests.add(operation.id); success('配音取消请求已提交。'); });
});
$('audio-version-select').addEventListener('change', event => {
  chooseAudioVersion(state, state.sceneId, event.target.value); render();
});
$('adopt-audio').addEventListener('click', () => {
  const scene = selectedScene(state); const version = selectedAudioVersion(state, scene);
  if (!scene || !version || version.stale || version.id === scene.head_audio_version_id) return;
  const body = { version_id: version.id, expected_head_audio_version_id: scene.head_audio_version_id ?? null };
  perform(`audio-adopt:${scene.id}`, () => request(`/api/scenes/${encodeURIComponent(scene.id)}/audio/adopt`,
    { method: 'POST', body }), () => success(`已采用配音 A${version.number}，请检查时长与字幕。`));
});
$('download-audio').addEventListener('click', () => {
  const version = selectedAudioVersion(state);
  if (version) download(audioPath(version.id), `narration-a${version.number}.wav`, `audio-download:${version.id}`);
});
$('audio-upload-file').addEventListener('change', event => {
  const file = event.target.files?.[0];
  if (file && (!file.size || file.size > 32 * 1024 * 1024 || !/\.wav$/i.test(file.name))) {
    event.target.value = ''; uploadSelection = null;
    showInputError('请选择非空的 WAV 文件，最大 32 MiB。');
  } else uploadSelection = file ? { projectId: state.projectId, sceneId: state.sceneId, file } : null;
  render();
});
$('upload-audio').addEventListener('click', () => {
  const selection = uploadSelection; const scene = selectedScene(state);
  if (!selection || !scene || selection.projectId !== state.projectId || selection.sceneId !== scene.id) return;
  const revision = state.project.revision;
  const path = `/api/scenes/${encodeURIComponent(scene.id)}/audio/upload?expected_revision=${revision}`;
  perform(`audio-upload:${scene.id}`, async () => {
    const bytes = await selection.file.arrayBuffer();
    const sha = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2, '0')).join('');
    return request(path, { method: 'POST', body: selection.file, raw: true,
      intentBody: { expected_revision: revision, audio_sha256: sha, bytes: selection.file.size } });
  }, () => { uploadSelection = null; $('audio-upload-file').value = ''; success('配音已上传为候选，请试听后采用。'); });
});
for (const id of ['leading-seconds', 'trailing-seconds']) $(id).addEventListener('input', () => {
  const scene = selectedScene(state); if (!scene) return;
  const draft = timingDrafts.get(draftKey(scene.id));
  Object.assign(draft, { leading_seconds: $('leading-seconds').valueAsNumber,
    trailing_seconds: $('trailing-seconds').valueAsNumber, dirty: true });
  $('save-timing').disabled = false; $('timing-draft-note').textContent = '留白尚未保存';
});
$('save-timing').addEventListener('click', () => {
  const scene = selectedScene(state);
  if (!scene || !$('leading-seconds').reportValidity() || !$('trailing-seconds').reportValidity()) return;
  const key = draftKey(scene.id); const { dirty, ...body } = timingDrafts.get(key);
  perform(`timing:${scene.id}`, () => request(`/api/scenes/${encodeURIComponent(scene.id)}/timing`, { method: 'PATCH', body }),
    () => { timingDrafts.delete(key); success('镜头留白已保存。'); });
});
$('estimate-captions').addEventListener('click', () => {
  const scene = selectedScene(state); if (!scene) return;
  const key = draftKey(scene.id); const body = { expected_revision: state.project.revision };
  if (captionDrafts.get(key)?.dirty && !window.confirm('重新估算会替换这里尚未保存的字幕校正，继续吗？')) return;
  perform(`captions:${scene.id}`, () => request(`/api/scenes/${encodeURIComponent(scene.id)}/captions/estimate`,
    { method: 'POST', body }), () => { captionDrafts.delete(key); renderedCaptionDraft = null; success('已生成估算时间，请试听并校正。'); });
});
$('add-caption').addEventListener('click', () => {
  const scene = selectedScene(state); const head = adoptedAudio(scene);
  const draft = captionDrafts.get(draftKey(scene?.id));
  if (!head || !draft || draft.cues.length >= 200) return;
  const duration = Number(scene.leading_seconds ?? 0) + head.duration_seconds;
  const start = Math.min(draft.cues.at(-1)?.end ?? Number(scene.leading_seconds ?? 0), Math.max(0, duration - 0.1));
  draft.cues.push({ start, end: Math.min(start + 2, duration), text: '' });
  draft.dirty = true; renderedCaptionDraft = null; renderCaptionRows(draft); updateCaptionButtons();
});
$('save-captions').addEventListener('click', () => {
  const scene = selectedScene(state); const draft = captionDrafts.get(draftKey(scene?.id));
  const head = adoptedAudio(scene);
  if (!scene || !draft || !head || !validSceneCaptions(scene, draft.cues)) return;
  const key = draftKey(scene.id);
  const body = { expected_revision: draft.expected_revision, audio_version_id: draft.audio_version_id,
    cues: draft.cues.map(cue => ({ ...cue })) };
  perform(`captions:${scene.id}`, () => request(`/api/scenes/${encodeURIComponent(scene.id)}/captions`,
    { method: 'PUT', body }), () => { captionDrafts.delete(key); renderedCaptionDraft = null; success('校正字幕已保存。'); });
});
$('download-subtitles').addEventListener('click', () => {
  if (state.projectId) download(`/api/projects/${encodeURIComponent(state.projectId)}/subtitles`,
    'video-subtitles.srt', `subtitles:${state.projectId}`);
});
$('render-project').addEventListener('click', () => {
  if (!state.project) return;
  const id = state.projectId;
  const body = { expected_revision: state.project.revision, silent: $('render-silent').checked,
    subtitles: $('render-subtitles').checked };
  perform(`render:${id}`, () => request(`/api/projects/${encodeURIComponent(id)}/renders`, { method: 'POST', body }),
    () => success('成片导出已开始，可在项目预览与成片中查看进度。'));
});
$('preview-project').addEventListener('click', openProjectPlayer);
for (const id of ['render-silent', 'render-subtitles']) $(id).addEventListener('change', () => {
  if (state.project) renderDelivery(state.project);
});
$('close-project-player').addEventListener('click', closeProjectPlayer);
$('project-player-dialog').addEventListener('cancel', event => { event.preventDefault(); closeProjectPlayer(); });
$('project-player-toggle').addEventListener('click', () => {
  if (!player) return;
  if (player.playing) pauseProjectPlayer();
  else {
    if (player.position >= player.timeline.duration_seconds) { player.position = 0; player.sceneId = null; }
    player.startedAt = performance.now(); player.playing = true;
    $('project-player-toggle').textContent = '暂停'; updatePlayerFrame();
  }
});
$('project-player-seek').addEventListener('input', event => {
  if (!player) return;
  const playing = player.playing; const position = event.target.valueAsNumber;
  pauseProjectPlayer(); player.position = position; player.sceneId = null;
  player.startedAt = performance.now(); player.playing = playing;
  $('project-player-toggle').textContent = playing ? '暂停' : '播放'; updatePlayerFrame();
});
$('project-player-audio').addEventListener('loadedmetadata', () => {
  if (player?.speakingScene && Number.isFinite(player.audioTarget)) {
    $('project-player-audio').currentTime = Math.min(player.audioTarget, $('project-player-audio').duration);
  }
});
$('project-player-audio').addEventListener('error', () => {
  if (!player) return;
  pauseProjectPlayer(); $('project-player-note').textContent = '配音读取失败，请检查连接后重新打开项目预览。';
});
$('dismiss-error').addEventListener('click', () => { $('global-error').hidden = true; });
async function refresh() {
  $('global-error').hidden = true;
  try {
    const session = await request('/api/session');
    csrfToken = session.csrf_token;
    sessionSettings = session;
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
window.addEventListener('pagehide', () => { stopPolling(); readController?.abort(); sourceController?.abort(); $('audio-preview').pause(); pauseProjectPlayer(); });
window.addEventListener('beforeunload', event => {
  if ([...drafts.values(), ...speechDrafts.values(), ...timingDrafts.values(), ...captionDrafts.values()].some(draft => draft.dirty)) {
    event.preventDefault(); event.returnValue = '';
  }
});

refresh();
