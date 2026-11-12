import {
  activeOperations, chooseVersion, createIntentKeys, createSceneDraft, createStoryboardRequest, createWorkspaceState,
  acceptProjectRead, isActiveOperation, isUnresolvedProductionOperation, issueProjectRead, latestVersion, orderedVersions,
  selectProject, selectScene, selectedScene, selectedVersion, statusLabel, syncSceneDraft,
  updateProjectSummary, versionPath, renderSourceLabel, renderProgressLabel,
  audioPath, chooseAudioVersion, createCaptionDraft, orderedAudioVersions, parsePronunciation,
  pronunciationText, selectedAudioVersion, timelinePosition, validateCaptionCues, deliveryIssues,
  speechVoices, speechVoiceName, projectSpeechVoice, syncProjectSpeechDraft, projectVoiceOptions,
  projectSpeechCanGenerate, audioVoiceLabel, audioOperationCandidate, mutationErrorLabel,
  operationElapsedLabel, operationNeedsRefresh, operationServiceErrorLabel,
} from './model.mjs';
import { createExplanationPanel, explanationErrorLabel } from './explanation.mjs';
import { createJointPlanPanel, jointPlanErrorLabel } from './joint-plan.mjs';
import { createMotionPanel, createMotionRenderRequest, createProjectMotionDraftRequest, createDynamicProjectRequest, hasMotionScenes, usesDynamicVideo, motionMetadata, motionErrorLabel, motionFailureSource, motionFailureDetails } from './motion.mjs';
import { createBatchPanel, batchErrorLabel } from './batch.mjs';
import { createContinuityPanel, createContinuityDraftRequest, continuityErrorLabel } from './continuity.mjs';
import { projectDurationSummary, durationSummaryLines, plannedSceneSeconds } from './duration.mjs';

const $ = id => document.getElementById(id);
const nativeMediaIds = ['audio-preview', 'motion-timing-audio', 'batch-audio-preview',
  'project-player-audio', 'motion-render-video'];
const state = createWorkspaceState();
const intentKeys = createIntentKeys();
const drafts = new Map();
const feedbacks = new Map();
const pending = new Set();
const cancelRequests = new Set();
const speechDrafts = new Map();
const projectSpeechDrafts = new Map();
const timingDrafts = new Map();
const captionDrafts = new Map();
const renderHistories = new Map();
const deliveryWriteErrors = new Map();
const storyboardWriteErrors = new Map();
const renderFailureVersions = new Map();
let motionRenderSequence = 0;
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
let statusClockTimer = null;
let recoveryPromise = null;
let renderReadSequence = 0;
let renderElapsedDisplays = [];
let successTimer = null;
let editor = null;
let previewMode = 'preview';
let displayedVersion = null;
let sourceVersion = null;
let loading = false;
let staticMaterialsScene = null;

// Pass project identity to status queries so peer-panel busy checks never
// re-enter a context builder while that same context is being constructed.
const jointPlanPanel = createJointPlanPanel({
  document, request, refresh: () => loadProject(), onError: showError, onSuccess: success,
  getContext: () => ({ project: state.project, epoch: state.epoch,
    configured: sessionSettings.joint_plan_configured === true,
    unsaved: projectHasUnsavedEdits(state.projectId),
    projectBusy: activeOperations(state.project).length > 0 || pending.size > 0
      || explanationPanel.hasUnresolvedOperation(state.projectId)
      || motionPanel.hasUnresolvedOperation(state.projectId, state.project?.scenes)
      || batchPanel.hasUnresolvedOperation(state.projectId)
      || continuityPanel.hasUnresolvedOperation(state.projectId)
      || (renderHistories.get(state.projectId) ?? []).some(isActiveOperation),
  }),
});
const explanationPanel = createExplanationPanel({
  document, request, refresh: () => loadProject(), onError: showError, onSuccess: success,
  getContext: () => ({ project: state.project, epoch: state.epoch,
    configured: false, // Historical review only; new projects use whole-video planning.
    unsaved: projectHasUnsavedEdits(state.projectId),
    projectBusy: activeOperations(state.project).length > 0 || pending.size > 0
      || jointPlanPanel.hasUnresolvedOperation(state.projectId)
      || batchPanel.hasUnresolvedOperation(state.projectId)
      || continuityPanel.hasUnresolvedOperation(state.projectId)
      || (renderHistories.get(state.projectId) ?? []).some(isActiveOperation),
  }),
});

const motionPanel = createMotionPanel({
  document, request, refresh: () => loadProject(), onError: showError, onSuccess: success,
  onPreviewStateChange: ready => {
    if (usesDynamicVideo(state.project)) $('preview-project').disabled = !ready;
  },
  getContext: () => ({ project: state.project, scene: editor === 'new' ? null : selectedScene(state), epoch: state.epoch,
    enabled: sessionSettings.motion_enabled === true,
    configured: sessionSettings.motion_configured === true,
    processConfigured: sessionSettings.process_configured === true,
    creativeConfigured: sessionSettings.creative_configured === true,
    renderConfigured: sessionSettings.motion_render_configured === true,
    unsaved: projectHasUnsavedEdits(state.projectId, false),
    projectBusy: activeOperations(state.project).length > 0 || pending.size > 0 || explanationPanel.hasActiveOperation(state.projectId)
      || jointPlanPanel.hasUnresolvedOperation(state.projectId)
      || batchPanel.hasUnresolvedOperation(state.projectId)
      || continuityPanel.hasUnresolvedOperation(state.projectId)
      || (renderHistories.get(state.projectId) ?? []).some(isActiveOperation),
  }),
});

const batchPanel = createBatchPanel({
  document, request, refresh: () => loadProject(), onError: showError, onInputError: showInputError, onSuccess: success,
  getContext: () => ({ project: state.project, epoch: state.epoch,
    enabled: sessionSettings.batch_enabled === true && sessionSettings.motion_enabled === true,
    motionConfigured: sessionSettings.motion_configured === true,
    creativeConfigured: sessionSettings.creative_configured === true,
    audioConfigured: sessionSettings.speech_configured === true,
    renderConfigured: sessionSettings.motion_render_configured === true,
    unsaved: projectHasUnsavedEdits(state.projectId),
    projectBusy: activeOperations(state.project).length > 0 || pending.size > 0
      || explanationPanel.hasUnresolvedOperation(state.projectId) || jointPlanPanel.hasUnresolvedOperation(state.projectId)
      || motionPanel.hasUnresolvedOperation(state.projectId, state.project?.scenes)
      || continuityPanel.hasUnresolvedOperation(state.projectId)
      || (renderHistories.get(state.projectId) ?? []).some(isActiveOperation),
  }),
  onOpenScene: (sceneId, { motionVersionId, audioVersionId } = {}) => {
    if (!state.project?.scenes.some(scene => scene.id === sceneId)) return;
    batchPanel.pause(); motionPanel.pause(); $('audio-preview').pause();
    selectScene(state, sceneId); editor = null;
    if (motionVersionId) motionPanel.select(state.projectId, sceneId, motionVersionId);
    if (audioVersionId) chooseAudioVersion(state, sceneId, audioVersionId);
    render(); motionPanel.load(state.projectId, state.epoch);
    $('scene-workspace').scrollIntoView({ block: 'start' });
    $('motion-panel').open = true;
  },
  onPlayRender: item => openMotionRender(item),
  onProjectDraft: () => {
    if (!state.project || batchPanel.hasUnresolvedOperation(state.projectId) || projectHasUnsavedEdits(state.projectId)) return;
    const intent = createProjectMotionDraftRequest(state.project, { endHoldSeconds: $('render-end-hold').checked ? 4 : 0 });
    perform(`render:${state.projectId}`, () => request(intent.path, { method: 'POST', body: intent.body }),
      () => success('整片草稿制作已开始，使用当前已采用版本；不会确认人工时序或替你正式导出。'));
  },
  onMediaPlay: () => { motionPanel.pause(); $('audio-preview').pause(); closeMotionRender(); pauseProjectPlayer(); },
});

const continuityPanel = createContinuityPanel({
  document, request, refresh: () => loadProject(), onError: showError, onInputError: showInputError, onSuccess: success,
  getContext: () => ({ project: state.project, epoch: state.epoch,
    enabled: sessionSettings.continuity_enabled === true,
    renderConfigured: sessionSettings.motion_render_configured === true,
    deliveryReady: !deliveryIssues(state.project, { silent: false, subtitles: false }).length,
    unsaved: projectHasUnsavedEdits(state.projectId, true, false),
    projectBusy: activeOperations(state.project).length > 0 || pending.size > 0
      || explanationPanel.hasUnresolvedOperation(state.projectId)
      || jointPlanPanel.hasUnresolvedOperation(state.projectId)
      || motionPanel.hasUnresolvedOperation(state.projectId, state.project?.scenes)
      || batchPanel.hasUnresolvedOperation(state.projectId)
      || (renderHistories.get(state.projectId) ?? []).some(isActiveOperation),
  }),
  onDraft: version => {
    if (!state.project) return;
    const intent = createContinuityDraftRequest(state.project, version,
      { endHoldSeconds: $('render-end-hold').checked ? 4 : 0 });
    return perform(`render:${state.projectId}`, () => request(intent.path, { method: 'POST', body: intent.body }),
      () => success('连续草稿已开始制作，复用已采用录音。请在「项目预览与成片」查看进度。'));
  },
  onDelivery: version => {
    if (!state.project || version.id !== continuityPanel.adoptedVersionId(state.projectId)) return;
    const intent = createMotionRenderRequest(state.project, { purpose: 'delivery',
      endHoldSeconds: $('render-end-hold').checked ? 4 : 0 });
    intent.body.continuity_version_id = version.id;
    return perform(`render:${state.projectId}`, () => request(intent.path, { method: 'POST', body: intent.body }),
      () => success('连续成片导出已开始，使用明确采用的连续方案、图解、录音及已核准时序。'));
  },
});

function projectHasUnsavedEdits(projectId, includeMotion = true, includeContinuity = true) {
  return Boolean(projectSpeechDrafts.get(projectId)?.dirty)
    || Boolean(includeMotion && motionPanel.hasProjectDrafts(projectId))
    || Boolean(includeContinuity && continuityPanel.hasProjectDrafts(projectId))
    || [drafts, speechDrafts, timingDrafts, captionDrafts].some(map =>
      [...map.entries()].some(([key, draft]) => key.startsWith(`${projectId}:`) && draft.dirty));
}

function productionTransitionBusy(project) {
  const operations = [project?.storyboard_operation, ...(project?.scenes ?? []).flatMap(scene =>
    [scene.operation, scene.audio_operation, scene.motion?.operation]), ...(renderHistories.get(project?.id) ?? [])];
  return pending.size > 0 || operations.some(isUnresolvedProductionOperation)
    || motionPanel.hasUnresolvedOperation(project?.id ?? null, project?.scenes)
    || explanationPanel.hasUnresolvedOperation(project?.id ?? null)
    || jointPlanPanel.hasUnresolvedOperation(project?.id ?? null)
    || continuityPanel.hasUnresolvedOperation(project?.id ?? null)
    || batchPanel.hasUnresolvedOperation(project?.id ?? null) || batchPanel.hasProjectDrafts(project?.id);
}

function renderProductionTransition(project) {
  $('convert-project-motion').disabled = !sessionSettings.motion_enabled || projectHasUnsavedEdits(project.id)
    || productionTransitionBusy(project);
}

const ERROR_LABELS = {
  video_references_require_split: '参考链接超过可读容量，请将来源分配到更多镜头后再生成。',
  video_reference_sensitive_url: '参考链接包含访问凭据，请使用不含凭据的公开来源链接。',
  video_reference_too_long: '参考链接过长，无法完整清楚展示，请整理来源后再生成。',
  video_reference_invalid: '参考链接格式无效，请检查完整链接。',
  video_reference_not_in_input: '生成结果包含输入未提供的参考链接，未保存该候选，请核对来源后重新生成。',
  explanation_visual_requires_split: '当前镜头的画面要求过长，无法完整保留讲解步骤和来源，请拆分镜头后再生成。',
  motion_source_reading_time_too_short: '完整来源至少需要 5 秒可读时间，请增加该镜头的停留时间后再制作。',
  unauthorized: '登录已失效，请重新登录。', authentication_required: '请先登录工作台。',
  csrf_invalid: '登录状态已更新，请刷新后重试。', invalid_csrf: '登录状态已更新，请刷新后重试。',
  csrf_rejected: '登录状态已更新，请刷新后重试。',
  revision_conflict: '项目已有更新。你的草稿已保留，请重新载入最新内容后再保存。',
  scene_edit_conflict: '当前镜头的原始内容已被修改。你的草稿已保留，请重新载入并核对后再保存。',
  motion_relation_layout_unavailable: '图解关系过密，暂时无法清晰排布。请减少节点或缩短关系标签后重新生成图解，已有配音可以保留。',
  motion_tail_hold_exceeds_limit: '最后一个镜头加上片尾停留会超过 60 秒。请缩短最后一镜，或关闭片尾停留后再制作。',
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
  speech_voice_unavailable: '这个音色当前不可用，请刷新并在项目配音中选择可用音色。历史配音仍可试听。',
  speech_voice_catalog_unavailable: '暂时无法读取可用音色，请稍后刷新；历史配音仍可试听。',
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
  missing_captions: '部分镜头还没有字幕，请先估算并校正，或取消「附加字幕」。',
  stale_captions: '配音或留白已变化，请重新整理相关镜头的字幕。',
  invalid_captions: '请检查字幕时间是否位于配音范围内，保持先后顺序，并填写字幕内容。',
  render_not_configured: '视频导出服务尚未连接，仍可导出项目素材。',
  render_in_progress: '已有成片正在导出，请等待完成或取消后再试。',
  audio_operation_in_progress: '这个镜头已有配音任务，请等待完成或取消后再试。',
  audio_narration_too_long: '这个镜头的旁白超过单次配音限制，请拆成更短的镜头。',
  speech_invalid_audio: '音频未通过校验，请使用完整、可解码的 PCM WAV。',
  speech_invalid_input: '请检查语速、读音修正与旁白长度。',
  audio_upload_too_large: '配音文件过大，单个文件最多 32 MiB。',
  explanation_not_configured: '讲解方案服务尚未启用，仍可使用现有镜头编辑与制作功能。',
  explanation_version_stale: '项目内容已变化，请重新生成匹配当前镜头的讲解方案。',
  explanation_in_progress: '项目已有讲解方案任务，请等待结束或申请取消。',
};

class ApiError extends Error {
  constructor(status, body) {
    const detail = body?.detail;
    const code = typeof detail === 'object' && !Array.isArray(detail) ? detail?.code : null;
    const message = typeof detail?.message === 'string' ? detail.message : null;
    super(ERROR_LABELS[code] ?? operationServiceErrorLabel(code) ?? (code?.startsWith('explanation_') ? explanationErrorLabel(code) : null)
      ?? (code?.startsWith('joint_plan_') ? jointPlanErrorLabel(code) : null)
      ?? (code?.startsWith('batch_') ? batchErrorLabel(code) : null)
      ?? (code?.startsWith('continuity_') || code === 'invalid_continuity_plan' ? continuityErrorLabel(code) : null)
      ?? (code?.startsWith('motion_') || code?.startsWith('process_') ? motionErrorLabel(code) : null) ?? message ?? (status === 401 ? '请先登录工作台。'
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
  clearTimeout(successTimer);
  $('success-message').hidden = true;
  $('error-text').textContent = error?.status === 409 ? mutationErrorLabel(error) : error instanceof ApiError ? error.message
    : mutationErrorLabel(error);
  $('global-error').hidden = false;
  $('login-link').hidden = error?.status !== 401;
  if ($('create-project-dialog').open) {
    $('create-error').textContent = $('error-text').textContent;
    $('create-error').hidden = false;
  }
}

function success(message) {
  $('global-error').hidden = true;
  $('login-link').hidden = true;
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
    const motion = motionMetadata(scene);
    const button = node('button', undefined, `scene-card${scene.id === state.sceneId && editor !== 'new' ? ' selected' : ''}`);
    button.type = 'button';
    button.dataset.sceneId = scene.id;
    button.setAttribute('aria-pressed', String(scene.id === state.sceneId && editor !== 'new'));
    const top = node('div', undefined, 'scene-card-top');
    top.append(node('span', `镜头 ${String(index + 1).padStart(2, '0')}`), node('span', `${scene.duration_seconds} 秒`));
    const bottom = node('div', undefined, 'scene-card-bottom');
    bottom.append(node('span', motion.mode === 'motion' ? '动态图解' : scene.format === 'html' ? 'HTML' : 'SVG'),
      node('span', isActiveOperation(motion.operation) ? statusLabel(motion.operation.status)
        : isActiveOperation(scene.operation) ? statusLabel(scene.operation.status)
        : motion.mode === 'motion' ? !motion.head_version_id ? '动态图解待制作'
          : motion.head_stale ? '图解已过期' : '已采用图解'
          : scene.head_version_id ? '已采用画面' : scene.versions?.length ? '有候选版本' : '待制作'),
      node('span', scene.audio_stale ? '配音已过期' : scene.head_audio_version_id ? '已采用配音' : '未采用配音',
        `scene-audio-status${scene.audio_stale ? ' stale' : ''}`));
    button.append(top, node('h3', scene.title), node('p', scene.narration || scene.visual_description || '还没有旁白'), bottom);
    button.addEventListener('click', () => {
      motionPanel.pause();
      selectScene(state, scene.id);
      editor = null;
      render();
      motionPanel.load(project.id, state.epoch);
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
  const writeError = prefix === 'storyboard' ? storyboardWriteErrors.get(state.projectId) : null;
  if (prefix === 'storyboard') $('reload-storyboard').hidden = !writeError;
  banner.hidden = !operation && !writeError;
  if (!operation && writeError) {
    banner.classList.toggle('active', false); banner.classList.toggle('failed', true);
    text.textContent = writeError; cancel.hidden = true; return;
  }
  if (!operation) return;
  banner.classList.toggle('active', active);
  banner.classList.toggle('failed', ['failed', 'timed_out', 'rejected', 'needs_input'].includes(operation.status));
  const label = prefix === 'storyboard' ? '拆分镜头' : '制作画面';
  text.textContent = [label, statusLabel(operation.status), operationElapsedLabel(operation),
    operation.error ? ERROR_LABELS[operation.error] ?? operationServiceErrorLabel(operation.error) ?? '任务未完成，请重新读取并联系维护者。' : null, writeError].filter(Boolean).join(' · ');
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
  const dynamic = editor === 'new' ? project.production_mode === 'motion' : motionMetadata(scene).mode === 'motion';
  $('scene-duration').max = dynamic ? String(Math.max(60, Number(scene?.duration_seconds ?? 0))) : '120';
  $('scene-static-settings').hidden = dynamic && editor === 'new';
  renderSceneEditStatus(draft, project, dynamic);
  $('save-scene').disabled = pending.has(`save:${project.id}`) || batchPanel.hasUnresolvedOperation(state.projectId);
  $('save-scene').textContent = pending.has(`save:${project.id}`) ? '正在保存…' : '保存镜头';
}

function renderSceneEditStatus(draft, project, dynamic = false) {
  const locked = batchPanel.hasUnresolvedOperation(project.id);
  $('scene-edit-note').textContent = locked
    ? '批量制作正在使用已保存的镜头，暂时不能保存修改。可以先整理草稿；批次结束或取消完成后可保存。收起编辑会保留草稿。'
    : '收起编辑会保留未保存的草稿；重新载入会放弃本镜头的草稿并读取最新内容。';
  $('draft-status').textContent = locked ? draft.dirty ? '草稿未保存，等待批次结束后可保存' : '批量制作期间暂不能保存'
    : draft.dirty ? '有未保存的修改'
    : dynamic ? '保存后制作动态图解；动态片段最多 60 秒，长旁白建议拆镜。'
      : editor === 'new' ? '保存后即可制作镜头' : '已载入当前镜头';
}

function clearPreview() {
  sourceController?.abort();
  displayedVersion = null;
  sourceVersion = null;
  $('scene-preview').removeAttribute('src');
  $('source-view').textContent = '';
}

function renderVersion(scene) {
  const dynamic = motionMetadata(scene).mode === 'motion';
  $('preview-tab').textContent = dynamic ? '静态候选预览' : '画面预览';
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
  $('adopt-version').textContent = dynamic ? adopted ? '当前静态采用版本' : '设为静态采用版本'
    : adopted ? '当前采用版本' : '采用此版本';
  $('adoption-note').textContent = dynamic ? '本镜头当前使用动态图解。此处审阅静态候选；需要用于成片时，请点击“复用静态素材”。'
    : adopted ? '这个版本将用于项目导出。'
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

function projectSpeechDraft(project = state.project) {
  if (!project) return null;
  const draft = syncProjectSpeechDraft(projectSpeechDrafts.get(project.id), project, sessionSettings);
  projectSpeechDrafts.set(project.id, draft);
  return draft;
}

function renderProjectSpeech(project) {
  const draft = projectSpeechDraft(project);
  const batchBusy = batchPanel.hasUnresolvedOperation(state.projectId);
  const busy = pending.has(`project-speech:${project.id}`) || batchBusy;
  const catalog = speechVoices(sessionSettings);
  const options = projectVoiceOptions(draft.voice, sessionSettings).map(item => {
    const option = node('option', item.label); option.value = item.value; option.disabled = item.disabled;
    return option;
  });
  $('project-speech-voice').replaceChildren(...options);
  $('project-speech-voice').value = draft.voice;
  $('project-speech-voice').disabled = busy || !sessionSettings.speech_configured || !catalog.length;
  $('save-project-speech').disabled = busy || !draft.dirty || !sessionSettings.speech_configured
    || !catalog.some(voice => voice.id === draft.voice);
  $('reset-project-speech').disabled = busy;
  const saved = projectSpeechVoice(project, sessionSettings);
  $('project-speech-status').textContent = batchBusy ? '批量制作尚未结束，项目音色暂不可更改。已有配音仍可试听或下载。'
    : draft.dirty ? '项目音色尚未保存，请先保存后再生成配音。'
    : !sessionSettings.speech_configured ? '配音服务尚未连接，历史配音仍可试听或下载。'
      : !catalog.some(voice => voice.id === saved) ? '当前项目音色暂不可用，请选择可用音色并保存。历史配音仍可试听。'
        : `已保存项目音色：${speechVoiceName(saved, sessionSettings)}。`;
}

function renderAudioOperation(scene, unsavedVoice) {
  const operation = scene.audio_operation;
  const banner = $('audio-status');
  const active = isActiveOperation(operation);
  const audition = $('audition-operation-audio');
  audition.hidden = true;
  audition.disabled = true;
  banner.hidden = !operation;
  if (!operation) return;
  banner.classList.toggle('active', active);
  banner.classList.toggle('failed', ['failed', 'timed_out', 'uncertain'].includes(operation.status));
  let text = `${operation.kind === 'audio_upload' ? '上传配音' : '制作配音'} · ${statusLabel(operation.status)}${operation.error ? ` · ${ERROR_LABELS[operation.error] ?? '请检查任务后重新提交。'}` : ''}`;
  if (operation.status === 'completed') {
    const candidate = audioOperationCandidate(scene);
    const selected = selectedAudioVersion(state, scene);
    if (candidate) {
      const label = `A${candidate.number} · ${audioVoiceLabel(candidate, sessionSettings)}`;
      text += ` · 本次配音 ${label}`;
      text += candidate.stale ? ' · 设置已变化，需更新；历史配音仍可从试听版本查看。'
        : candidate.id === selected?.id ? ' · 已选为试听版本。'
          : ' · 当前试听版本保持不变，可点击试听本次配音。';
      audition.textContent = `试听本次配音 ${label}`;
      audition.hidden = audition.disabled = candidate.stale || candidate.id === selected?.id;
    } else text += ' · 请刷新后在试听版本中查看本次结果。';
    if (unsavedVoice) text += ' 项目音色尚未保存，本次配音对应已保存的设置。';
  }
  $('audio-status-text').textContent = text;
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
  const batchBusy = batchPanel.hasUnresolvedOperation(state.projectId);
  const settings = scene.speech_settings ?? {};
  const speech = audioDraft(speechDrafts, scene, () => ({
    speed: settings.speed ?? 1, pronunciation: pronunciationText(settings.pronunciation),
    expected_revision: project.revision, dirty: false,
  }));
  setValue('speech-speed', speech.speed);
  setValue('speech-pronunciation', speech.pronunciation);
  $('speech-speed').disabled = batchBusy; $('speech-pronunciation').disabled = batchBusy;
  $('reset-speech-settings').disabled = batchBusy;
  $('speech-draft-note').textContent = batchBusy ? '批量制作期间保持当前语速与读音设置。'
    : speech.dirty ? '配音设置尚未保存' : '使用当前已保存的配音设置';
  $('save-speech-settings').disabled = batchBusy || !speech.dirty || pending.has(`speech:${scene.id}`);
  const voice = projectSpeechVoice(project, sessionSettings);
  $('audio-voice-label').textContent = `项目音色 · ${speechVoiceName(voice, sessionSettings)}`;
  $('audio-provider-note').textContent = sessionSettings.speech_configured ? '使用当前镜头已保存的旁白制作，生成完成后可试听比较。'
    : '配音服务尚未连接，可以先上传自己的 WAV 配音。';
  const dirty = drafts.get(draftKey(scene.id))?.dirty;
  const busy = isActiveOperation(scene.audio_operation) || pending.has(`audio-generate:${scene.id}`);
  const projectVoiceDraft = projectSpeechDraft(project);
  const projectVoiceBusy = pending.has(`project-speech:${project.id}`);
  const voiceReady = projectSpeechCanGenerate(project, sessionSettings, projectVoiceDraft);
  $('generate-audio').disabled = !voiceReady || projectVoiceBusy || !scene.narration?.trim()
    || dirty || speech.dirty || busy || batchPanel.hasUnresolvedOperation(state.projectId);
  $('audio-generation-hint').textContent = projectVoiceDraft.dirty || projectVoiceBusy ? '请先保存项目音色。'
    : sessionSettings.speech_configured && !voiceReady ? '当前项目音色不可用，请在项目配音中选择并保存。'
      : dirty ? '请先保存镜头旁白。' : speech.dirty ? '请先保存语速与读音设置。'
    : busy ? '配音任务进行中，可以继续查看其他镜头。' : '生成后先试听，再选择采用。';
  renderAudioOperation(scene, projectVoiceDraft.dirty);
  const versions = orderedAudioVersions(scene);
  const candidate = selectedAudioVersion(state, scene);
  const options = versions.map(version => {
    const option = node('option', `A${version.number} · ${audioVoiceLabel(version, sessionSettings)}${version.id === scene.head_audio_version_id ? ' · 已采用' : ' · 候选'}${version.stale ? ' · 需更新' : ''}`);
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
    ? `A${candidate.number} · ${audioVoiceLabel(candidate, sessionSettings)} · ${seconds(candidate.duration_seconds)} 秒`
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
  $('leading-seconds').disabled = batchBusy; $('trailing-seconds').disabled = batchBusy;
  $('reset-timing').disabled = batchBusy;
  $('save-timing').disabled = batchBusy || !timing.dirty || pending.has(`timing:${scene.id}`);
  $('timing-draft-note').textContent = batchBusy ? '批量制作期间保持当前镜头留白。' : timing.dirty ? '留白尚未保存' : '';
  const head = adoptedAudio(scene);
  const duration = plannedSceneSeconds(scene) ?? Number(scene.duration_seconds);
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

function renderProjectDuration(project) {
  const summary = projectDurationSummary(project, renderHistories.get(project.id) ?? [],
    { endHoldSeconds: usesDynamicVideo(project) && $('render-end-hold').checked ? 4 : 0 });
  $('project-duration-summary').replaceChildren(...durationSummaryLines(summary).map(line => node('p', line)));
}

function renderDelivery(project) {
  renderElapsedDisplays = [];
  $('delivery-error').hidden = !deliveryWriteErrors.has(project.id);
  $('delivery-error-text').textContent = deliveryWriteErrors.get(project.id) ?? '';
  renderProjectDuration(project);
  const scenes = project.scenes ?? [];
  const dynamic = usesDynamicVideo(project);
  const visualCount = scenes.filter(scene => motionMetadata(scene).mode === 'motion'
    ? motionMetadata(scene).head_version_id && !motionMetadata(scene).head_stale : scene.head_version_id).length;
  const audioCount = scenes.filter(scene => scene.head_audio_version_id && !scene.audio_stale).length;
  $('delivery-summary').textContent = `已采用画面 ${visualCount}/${scenes.length} · 可用配音 ${audioCount}/${scenes.length}。预览和成片使用已采用的版本。`;
  const issues = deliveryIssues(project, { silent: dynamic ? false : $('render-silent').checked,
    subtitles: dynamic ? false : $('render-subtitles').checked });
  const reasons = { missing_visual: '未采用画面', missing_audio: '未采用配音', stale_audio: '配音已过期',
    missing_captions: '缺少字幕', stale_captions: '字幕已过期', stale_motion: '动态图解已过期',
    missing_motion_plan: '动态图解待制作或采用' };
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
      motionPanel.load(project.id, state.epoch);
    });
    item.append(button); issueList.append(item);
  }
  $('delivery-issues').replaceChildren(issueList);
  $('delivery-issues').hidden = !issues.length;
  $('preview-project').disabled = dynamic ? $('preview-motion').disabled
    : !scenes.length || pending.has(`preview:${project.id}`);
  $('preview-project').textContent = dynamic ? '制作当前镜头草稿' : '预览整个项目';
  $('render-project').hidden = dynamic;
  $('render-project').disabled = dynamic || !scenes.length || !sessionSettings.media_configured || pending.has(`render:${project.id}`);
  $('render-motion-project').hidden = !dynamic;
  $('motion-delivery-note').hidden = !dynamic;
  $('delivery-legacy-options').hidden = dynamic;
  $('delivery-end-options').hidden = !dynamic;
  $('delivery-options-note').textContent = dynamic ? '关键点由画面逐步表达，保留旁白，不附加字幕。'
    : '关键点默认由画面表达。关闭附加字幕仍保留旁白，预览与导出使用同一选择。';
  $('captions-section').hidden = dynamic;
  $('render-motion-project').disabled = !dynamic || !sessionSettings.motion_render_configured || projectHasUnsavedEdits(project.id)
    || pending.has(`render:${project.id}`) || issues.length > 0 || !scenes.length
    || batchPanel.hasUnresolvedOperation(state.projectId)
    || (renderHistories.get(project.id) ?? []).some(isActiveOperation);
  $('render-silent').disabled = dynamic;
  $('render-subtitles').disabled = dynamic;
  $('download-subtitles').disabled = !scenes.length || pending.has(`subtitles:${project.id}`);
  $('download-subtitles').hidden = dynamic;
  $('render-hint').textContent = dynamic ? '当前草稿与正式视频均为 1280×720（16:9）、24 fps；草稿用于先看效果，正式导出还需核对录音时序。保留旁白，不附加字幕。'
    : sessionSettings.media_configured ? '导出在后台进行，过程中可以继续编辑；结果保留导出开始时的版本。'
    : '视频导出暂时不可用，仍可预览并导出原项目素材。';
  const fragment = document.createDocumentFragment();
  for (const item of renderHistories.get(project.id) ?? []) {
    const row = node('div', undefined, 'render-item');
    const label = node('div');
    const subtitleLabel = item.subtitles === true ? '有字幕' : item.subtitles === false ? '无字幕' : '字幕状态未记录';
    const kind = item.continuity_version_id ? (item.purpose === 'draft' ? '连续 MP4 草稿' : '连续动态成片')
      : item.purpose === 'draft' ? 'MP4 草稿' : item.visual_mode === 'mixed' ? '动态成片' : item.silent ? '静音视频' : '有声视频';
    label.append(node('strong', `${kind} · ${renderSourceLabel(item)} · ${subtitleLabel} · ${statusLabel(item.status)}`),
      node('small', `文稿版本 ${item.project_revision ?? '未记录'}${item.content_state === 'changed' ? ' · 此产物对应的内容或采用版本已有变化' : item.content_state === 'current' ? ' · 对应内容仍有效' : ''}${item.width && item.height ? ` · ${item.width}×${item.height} · ${item.fps} fps` : ''}${Number.isFinite(item.actual_duration_seconds) && item.actual_duration_seconds > 0 ? ` · 实际 ${seconds(item.actual_duration_seconds)} 秒` : ''}${item.created_at ? ` · ${new Date(item.created_at * 1000).toLocaleString('zh-CN')}` : ''}${item.error ? ` · ${ERROR_LABELS[item.error] ?? (item.error.startsWith('motion_') ? motionErrorLabel(item.error) : '导出未完成，请检查资源后重试。')}` : ''}`));
    const progress = renderProgressLabel(item);
    if (progress) {
      const display = node('small', progress); label.append(display);
      renderElapsedDisplays.push({ display, item });
    }
    const source = motionFailureSource(item);
    const failure = motionFailureDetails(item, renderFailureVersions.get(`${project.id}:${source?.visual_version_id}`), project.id);
    if (failure) {
      label.append(node('p', failure.text, 'batch-item-error'));
      if (failure.bound && project.scenes.some(scene => scene.id === source.scene_id)) {
        const locate = node('button', '查看问题图元与动作', 'text-button'); locate.type = 'button';
        locate.addEventListener('click', async () => {
          motionPanel.pause(); batchPanel.pause(); $('audio-preview').pause();
          selectScene(state, source.scene_id); editor = null;
          motionPanel.select(project.id, source.scene_id, source.visual_version_id);
          motionPanel.focusDiagnostic(source.visual_version_id, failure.context);
          render(); await motionPanel.load(project.id, state.epoch);
          $('motion-panel').open = true; $('motion-panel').scrollIntoView({ block: 'start' });
        });
        label.append(locate);
      }
    }
    row.append(label);
    if (['completed', 'succeeded'].includes(item.status)) {
      const play = node('button', '播放 MP4', 'button secondary'); play.type = 'button';
      play.addEventListener('click', () => openMotionRender(item)); row.append(play);
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
  jointPlanPanel.render();
  explanationPanel.render();
  motionPanel.render();
  batchPanel.render();
  continuityPanel.render();
  if (!project) return;
  project.scenes ??= [];
  $('project-title').textContent = project.title;
  const duration = project.scenes.reduce((sum, scene) => sum + Number(scene.duration_seconds || 0), 0);
  $('project-meta').textContent = `${project.scenes.length} 个镜头 · 方案预计 ${seconds(duration)} 秒 · 文稿版本 ${project.revision}`;
  $('project-script').textContent = project.script;
  $('project-style').textContent = project.style || '还没有指定视觉方向。';
  const dynamicIntent = project.production_mode === 'motion';
  $('production-mode-label').textContent = dynamicIntent ? '动态视频制作' : '历史项目 · 素材保留';
  $('production-mode-note').textContent = dynamicIntent
    ? '新镜头按动态图解制作。已有画面与录音保留，可作为静态片段混合使用。'
    : '原有画面、录音和导出保持可用。转为动态制作后，未完成镜头按动态制作，已有素材继续保留。';
  $('convert-project-motion').hidden = dynamicIntent;
  renderProductionTransition(project);
  const staticExportReady = project.scenes.length > 0 && project.scenes.every(scene => Boolean(scene.head_version_id));
  $('export-project').disabled = !staticExportReady || pending.has(`export:${project.id}`);
  $('static-export-note').textContent = staticExportReady ? '包含整个项目已采用的静态 SVG/HTML 素材。'
    : '静态 ZIP 需要每个镜头都有已采用的静态素材；动态视频请使用项目 MP4 导出。';
  renderOperation(project.storyboard_operation, 'storyboard');
  const storyboardBusy = isActiveOperation(project.storyboard_operation) || pending.has(`storyboard:${project.id}`);
  $('create-storyboard').disabled = storyboardBusy || project.scenes.length > 0;
  $('storyboard-max-scenes').disabled = storyboardBusy || project.scenes.length > 0;
  $('create-storyboard').textContent = storyboardBusy ? '正在拆分镜头…' : '按文稿拆分镜头';
  $('add-scene').disabled = storyboardBusy;
  $('empty-add-scene').disabled = storyboardBusy;
  renderScenes(project);
  renderDelivery(project);
  renderProjectSpeech(project);
  const scene = selectedScene(state);
  $('scene-workspace').hidden = !scene && editor !== 'new';
  $('select-scene-empty').hidden = Boolean(scene) || editor === 'new';
  if (scene || editor === 'new') {
    $('scene-number').textContent = editor === 'new' ? '新增镜头' : `镜头 ${String(project.scenes.indexOf(scene) + 1).padStart(2, '0')}`;
    $('scene-title').textContent = scene?.title ?? '安排一个新镜头';
    renderEditor(scene, project);
  }
  if (!scene || editor === 'new') return;
  const materialsKey = `${project.id}:${scene.id}`;
  if (staticMaterialsScene !== materialsKey) {
    staticMaterialsScene = materialsKey;
    $('static-assets-panel').open = false;
    $('motion-panel').open = true;
  }
  $('scene-narration-text').textContent = scene.narration || '还没有旁白，可在编辑镜头中添加。';
  renderVersion(scene);
  renderOperation(scene.operation, 'scene');
  setValue('feedback', feedbacks.get(draftKey()) ?? '');
  updateGenerationButtons();
  renderAudio(scene, project);
}

function stopPolling() {
  clearTimeout(pollTimer); pollTimer = null;
  clearTimeout(statusClockTimer); statusClockTimer = null;
}

function projectOperations() {
  return [state.project?.storyboard_operation,
    ...(state.project?.scenes ?? []).flatMap(scene => [scene.operation, scene.audio_operation])];
}

function needsStatusRefresh() {
  return projectOperations().some(operationNeedsRefresh)
    || (renderHistories.get(state.projectId) ?? []).some(operationNeedsRefresh)
    || explanationPanel.hasRefreshableOperation?.(state.projectId)
    || jointPlanPanel.hasRefreshableOperation(state.projectId)
    || motionPanel.hasRefreshableOperation(state.projectId, state.project?.scenes)
    || batchPanel.hasActiveOperation(state.projectId)
    || continuityPanel.hasUnresolvedOperation(state.projectId);
}

function refreshStatusClocks() {
  if (!state.project || document.hidden) return;
  renderOperation(state.project.storyboard_operation, 'storyboard');
  const scene = selectedScene(state);
  if (scene) renderOperation(scene.operation, 'scene');
  for (const { display, item } of renderElapsedDisplays) display.textContent = renderProgressLabel(item);
  jointPlanPanel.refreshElapsed(); explanationPanel.refreshElapsed?.(); motionPanel.refreshElapsed(); batchPanel.refreshElapsed();
}

function scheduleStatusClock() {
  clearTimeout(statusClockTimer); statusClockTimer = null;
  if (document.hidden || !state.projectId || !needsStatusRefresh()) return;
  statusClockTimer = setTimeout(() => { refreshStatusClocks(); scheduleStatusClock(); }, 1000);
}

function schedulePolling() {
  stopPolling();
  if (document.hidden || !state.projectId || !needsStatusRefresh()) return;
  const active = activeOperations(state.project).length
    || (renderHistories.get(state.projectId) ?? []).some(isActiveOperation)
    || explanationPanel.hasActiveOperation(state.projectId)
    || jointPlanPanel.hasActiveOperation(state.projectId)
    || motionPanel.hasActiveOperation(state.projectId, state.project?.scenes)
    || batchPanel.hasActiveOperation(state.projectId) || continuityPanel.hasUnresolvedOperation(state.projectId);
  pollTimer = setTimeout(() => loadProject().catch(showError), active ? 3500 : 15000);
  scheduleStatusClock();
}

async function loadProjects() {
  const sequence = ++listSequence;
  const result = await request('/api/projects');
  if (sequence !== listSequence) return;
  projects = result.projects ?? [];
  renderProjects();
}

async function loadRenders(projectId, epoch) {
  const sequence = ++renderReadSequence;
  const result = await request(`/api/projects/${encodeURIComponent(projectId)}/renders`);
  if (state.projectId !== projectId || state.epoch !== epoch || sequence !== renderReadSequence) return;
  renderHistories.set(projectId, result.renders ?? []);
  renderDelivery(state.project);
  const ids = [...new Set((result.renders ?? []).map(motionFailureSource).filter(Boolean).map(source => source.visual_version_id))];
  await Promise.allSettled(ids.filter(id => !renderFailureVersions.has(`${projectId}:${id}`)).map(async id => {
    const version = await request(`/api/motion-versions/${encodeURIComponent(id)}`);
    if (state.projectId === projectId && state.epoch === epoch && sequence === renderReadSequence) renderFailureVersions.set(`${projectId}:${id}`, version);
  }));
  if (state.projectId === projectId && state.epoch === epoch && sequence === renderReadSequence) renderDelivery(state.project);
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
      const related = await Promise.allSettled([
        loadRenders(ticket.projectId, ticket.epoch), jointPlanPanel.load(ticket.projectId, ticket.epoch),
        explanationPanel.load(ticket.projectId, ticket.epoch),
        motionPanel.load(ticket.projectId, ticket.epoch), batchPanel.load(ticket.projectId, ticket.epoch),
        continuityPanel.load(ticket.projectId, ticket.epoch),
      ]);
      for (const result of related) if (result.status === 'rejected') throw result.reason;
      if (state.projectId === ticket.projectId && state.epoch === ticket.epoch) render();
    }
  } catch (error) {
    if (ticket.epoch === state.epoch && ticket.sequence === state.requestSequence && error.name !== 'AbortError') {
      loading = false;
      render();
      throw error;
    }
  } finally {
    if (ticket.epoch === state.epoch && ticket.sequence === state.requestSequence) schedulePolling();
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
  motionPanel.pause();
  batchPanel.pause();
  closeProjectPlayer();
  closeMotionRender();
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
    if (key === `render:${projectId}`) deliveryWriteErrors.delete(projectId);
    if (key === `storyboard:${projectId}`) storyboardWriteErrors.delete(projectId);
    if (state.epoch === epoch && state.projectId === projectId) await after?.(result);
    await loadProjects();
    if (state.epoch === epoch && state.projectId === projectId) await loadProject();
    return result;
  } catch (error) {
    // A failed POST response does not prove the write failed. Reconcile only
    // with GETs; retain the exact intent/idempotency key for an explicit retry.
    if (state.epoch === epoch && state.projectId === projectId) {
      if (key === `render:${projectId}`) {
        deliveryWriteErrors.set(projectId, mutationErrorLabel(error));
        $('delivery-panel').open = true;
      }
      if (key === `storyboard:${projectId}`) storyboardWriteErrors.set(projectId, mutationErrorLabel(error));
      try { await loadProject(); } catch { /* Keep the original write error visible. */ }
      showError(error);
    }
  } finally {
    pending.delete(key);
    render();
  }
}

function startNewScene() {
  if (!state.project) return;
  motionPanel.pause();
  editor = 'new';
  const key = draftKey(null);
  if (!drafts.has(key)) drafts.set(key, createSceneDraft({}, state.project.revision));
  render();
  $('scene-title-input').focus();
}

async function saveScene(event) {
  event.preventDefault();
  if (batchPanel.hasUnresolvedOperation(state.projectId) || !$('scene-form').reportValidity() || !state.project) return;
  const projectId = state.projectId;
  const sceneId = editor === 'new' ? null : state.sceneId;
  const key = draftKey(sceneId);
  const draft = drafts.get(key);
  const { expected_revision, base_fields, dirty, ...fields } = draft;
  const payload = sceneId ? { ...fields, expected_revision, base_fields } : fields;
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

function pauseOtherMedia(activeId) {
  // Stop controller state as well as native playback, including a project's
  // silent lead-in and a timing audition's pending end boundary.
  if (activeId !== 'motion-timing-audio') motionPanel.pause();
  if (activeId !== 'project-player-audio') pauseProjectPlayer();
  for (const id of nativeMediaIds) if (id !== activeId) $(id).pause();
}

function closeProjectPlayer() {
  pauseProjectPlayer(); player = null;
  $('project-player-audio').removeAttribute('src'); $('project-player-audio').load();
  $('project-player-frame').removeAttribute('src');
  if ($('project-player-dialog').open) $('project-player-dialog').close();
}

function closeMotionRender() {
  motionRenderSequence++;
  const video = $('motion-render-video');
  video.pause(); video.removeAttribute('src'); video.load();
  if ($('motion-render-dialog').open) $('motion-render-dialog').close();
}

async function openMotionRender(item) {
  const sequence = ++motionRenderSequence;
  // Batch rows carry a render ID, not a historical project revision. Resolve
  // the original record rather than substituting the current project's data.
  if (item.project_revision == null) {
    const projectId = state.projectId, epoch = state.epoch;
    try {
      const recorded = (renderHistories.get(projectId) ?? []).find(row => row.id === item.id);
      const rows = recorded ? null : (await request(`/api/projects/${encodeURIComponent(projectId)}/renders`)).renders;
      if (state.projectId !== projectId || state.epoch !== epoch || sequence !== motionRenderSequence) return;
      if (rows) renderHistories.set(projectId, rows);
      item = recorded ?? rows?.find(row => row.id === item.id);
      if (!item) { showInputError('未找到这条视频的原始制作记录，请重新读取状态。'); return; }
    } catch (error) { if (sequence === motionRenderSequence) showError(error); return; }
  }
  closeMotionRender(); closeProjectPlayer(); motionPanel.pause(); batchPanel.pause(); $('audio-preview').pause();
  $('motion-render-heading').textContent = item.purpose === 'draft' ? 'MP4 草稿' : '视频成片';
  $('motion-render-note').textContent = item.purpose === 'draft'
    ? `${renderSourceLabel(item)} · 文稿版本 ${item.project_revision ?? '未记录'} 的候选草稿。画面变化可能基于待核对的估算时序；播放不代表采用或已核对。`
    : `${renderSourceLabel(item)} · 文稿版本 ${item.project_revision ?? '未记录'} 的采用版本快照。项目后续修改不会改变这条成片。`;
  $('motion-render-video').src = `/api/renders/${encodeURIComponent(item.id)}/video`;
  $('motion-render-dialog').showModal();
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
  if (!state.project || hasMotionScenes(state.project)) return;
  const projectId = state.projectId;
  const epoch = state.epoch;
  const silent = $('render-silent').checked;
  const subtitles = $('render-subtitles').checked;
  await perform(`preview:${projectId}`, () => request(`/api/projects/${encodeURIComponent(projectId)}/timeline?silent=${silent}&subtitles=${subtitles}`), timeline => {
    if (state.projectId !== projectId || state.epoch !== epoch) return;
    closeProjectPlayer();
    closeMotionRender(); motionPanel.pause();
    player = { timeline, silent, subtitles, position: 0, playing: false, startedAt: 0, sceneId: null, audioStarting: false };
    $('audio-preview').pause();
    $('project-player-heading').textContent = state.project.title;
    $('project-player-seek').max = timeline.duration_seconds;
    const modeLabel = silent ? '静音 · 无字幕' : subtitles ? '保留旁白 · 有字幕' : '保留旁白 · 无字幕';
    $('project-player-note').textContent = `本次预览：${modeLabel}。使用打开时的采用版本；调整项目后，重新打开预览查看最新内容。`;
    $('project-player-dialog').showModal();
    updatePlayerFrame();
  });
}

function captureSpeechDraft() {
  if (batchPanel.hasUnresolvedOperation(state.projectId)) return;
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
  const body = { title: $('new-title').value.trim(), script: $('new-script').value.trim(), style: $('new-style').value.trim(),
    production_mode: 'motion' };
  try {
    const project = await request('/api/projects', { method: 'POST', body });
    $('create-project-dialog').close();
    $('create-project-form').reset();
    await loadProjects();
    await openProject(project.id);
    success('动态视频项目已创建。可以拆分镜头或手动添加，再制作图解与配音。');
  } catch (error) { showError(error); }
  finally { pending.delete('create-project'); $('submit-create-project').disabled = false; }
});

$('add-scene').addEventListener('click', startNewScene);
$('convert-project-motion').addEventListener('click', () => {
  const project = state.project;
  if (!project || project.production_mode === 'motion' || !sessionSettings.motion_enabled) return;
  let intent;
  try {
    intent = createDynamicProjectRequest(project, { unsaved: projectHasUnsavedEdits(project.id),
      busy: productionTransitionBusy(project) });
  } catch (error) { showInputError(error.message); return; }
  perform(`production-mode:${project.id}`, () => request(intent.path, { method: 'PATCH', body: intent.body }),
    () => success('项目已转为动态制作。已有画面与录音保留，未完成镜头按动态图解制作。'));
});
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
  renderSceneEditStatus(draft, state.project);
  updateGenerationButtons();
  $('generate-audio').disabled = true;
  $('upload-audio').disabled = true;
  $('audio-generation-hint').textContent = '请先保存镜头中的修改。';
});
$('toggle-scene-editor').addEventListener('click', () => { editor = editor ? null : 'existing'; render(); });
$('cancel-scene-edit').addEventListener('click', () => {
  const dirty = currentDraft()?.dirty;
  editor = null; render();
  if (dirty) success('已收起编辑，未保存的草稿已保留。再次编辑可继续；重新载入可放弃草稿。');
});
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

$('project-speech-voice').addEventListener('change', () => {
  if (batchPanel.hasUnresolvedOperation(state.projectId)) return;
  const draft = projectSpeechDraft();
  if (!draft) return;
  Object.assign(draft, { voice: $('project-speech-voice').value, dirty: true });
  render();
});
$('save-project-speech').addEventListener('click', () => {
  const project = state.project; const draft = projectSpeechDraft();
  if (!project || !draft?.dirty || !sessionSettings.speech_configured
      || batchPanel.hasUnresolvedOperation(state.projectId)
      || !speechVoices(sessionSettings).some(voice => voice.id === draft.voice)) return;
  const body = { expected_revision: draft.expected_revision, voice: draft.voice };
  perform(`project-speech:${project.id}`, () => request(`/api/projects/${encodeURIComponent(project.id)}/speech`,
    { method: 'PATCH', body }), () => { projectSpeechDrafts.delete(project.id);
    success('项目音色已保存。有效的动态图解可继续使用；请重新生成并试听采用配音，再核对画面时序。'); });
});
$('reset-project-speech').addEventListener('click', () => {
  const project = state.project;
  if (!project || batchPanel.hasUnresolvedOperation(state.projectId) || pending.has(`project-speech:${project.id}`)) return;
  if (projectSpeechDrafts.get(project.id)?.dirty && !window.confirm('重新载入会清除尚未保存的项目音色，继续吗？')) return;
  projectSpeechDrafts.delete(project.id); render();
});
for (const id of ['speech-speed', 'speech-pronunciation']) $(id).addEventListener('input', captureSpeechDraft);
$('save-speech-settings').addEventListener('click', () => {
  if (batchPanel.hasUnresolvedOperation(state.projectId)) return;
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
    if (id !== 'reset-captions' && batchPanel.hasUnresolvedOperation(state.projectId)) return;
    const scene = selectedScene(state); if (!scene) return;
    const key = draftKey(scene.id);
    if (map.get(key)?.dirty && !window.confirm('重新载入会清除这里尚未保存的修改，继续吗？')) return;
    map.delete(key); renderedCaptionDraft = null; render();
  });
}
$('generate-audio').addEventListener('click', () => {
  const scene = selectedScene(state); if (!scene) return;
  if (batchPanel.hasUnresolvedOperation(state.projectId)) return;
  if (!projectSpeechCanGenerate(state.project, sessionSettings, projectSpeechDraft())
      || pending.has(`project-speech:${state.projectId}`)) return;
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
$('audition-operation-audio').addEventListener('click', async () => {
  const scene = selectedScene(state);
  const candidate = audioOperationCandidate(scene);
  if (!candidate || candidate.stale || candidate.id === selectedAudioVersion(state, scene)?.id) return;
  const epoch = state.epoch;
  if (!chooseAudioVersion(state, scene.id, candidate.id)) return;
  render();
  const audio = $('audio-preview');
  audio.currentTime = 0;
  try { await audio.play(); }
  catch {
    if (state.epoch === epoch && state.sceneId === scene.id && selectedAudioVersion(state)?.id === candidate.id) {
      success(`已切换到配音 A${candidate.number}，请在音频播放器中点击播放。`);
    }
  }
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
  if (batchPanel.hasUnresolvedOperation(state.projectId)) return;
  const scene = selectedScene(state); if (!scene) return;
  const draft = timingDrafts.get(draftKey(scene.id));
  Object.assign(draft, { leading_seconds: $('leading-seconds').valueAsNumber,
    trailing_seconds: $('trailing-seconds').valueAsNumber, dirty: true });
  $('save-timing').disabled = false; $('timing-draft-note').textContent = '留白尚未保存';
});
$('save-timing').addEventListener('click', () => {
  if (batchPanel.hasUnresolvedOperation(state.projectId)) return;
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
  if (!state.project || usesDynamicVideo(state.project)) return;
  const id = state.projectId;
  const body = { expected_revision: state.project.revision, silent: $('render-silent').checked,
    subtitles: $('render-subtitles').checked };
  perform(`render:${id}`, () => request(`/api/projects/${encodeURIComponent(id)}/renders`, { method: 'POST', body }),
    () => success('成片导出已开始，可在项目预览与成片中查看进度。'));
});
$('render-motion-project').addEventListener('click', () => {
  if (!state.project || !usesDynamicVideo(state.project) || !sessionSettings.motion_render_configured
      || batchPanel.hasUnresolvedOperation(state.projectId) || projectHasUnsavedEdits(state.projectId) || deliveryIssues(state.project, { silent: false, subtitles: false }).length
      || (renderHistories.get(state.projectId) ?? []).some(isActiveOperation)) return;
  const intent = createMotionRenderRequest(state.project, { purpose: 'delivery', endHoldSeconds: $('render-end-hold').checked ? 4 : 0 });
  perform(`render:${state.projectId}`, () => request(intent.path, { method: 'POST', body: intent.body }),
    () => success('动态成片导出已开始，使用已采用版本和已人工核对的时序。'));
});
$('close-motion-render').addEventListener('click', closeMotionRender);
for (const id of nativeMediaIds) $(id).addEventListener('play', () => pauseOtherMedia(id));
$('motion-render-dialog').addEventListener('cancel', event => { event.preventDefault(); closeMotionRender(); });
$('motion-render-video').addEventListener('error', () => showInputError('视频暂时无法播放，请重新读取导出状态后再试。'));
$('preview-project').addEventListener('click', () => {
  if (usesDynamicVideo(state.project)) {
    if (!$('preview-project').disabled) motionPanel.preview();
  } else openProjectPlayer();
});
for (const id of ['render-silent', 'render-subtitles', 'render-end-hold']) $(id).addEventListener('change', () => {
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
$('reload-delivery').addEventListener('click', () => loadProject().catch(showError));
$('reload-storyboard').addEventListener('click', () => loadProject().catch(showError));
$('refresh-all').addEventListener('click', refresh);
function resumeProject() {
  if (document.hidden || !state.projectId) return;
  refreshStatusClocks();
  // Browsers may emit visibility, focus and pageshow together. Coalesce them
  // into one complete GET refresh, including every panel and render history.
  if (!recoveryPromise) {
    const recovery = loadProject().catch(showError).finally(() => { if (recoveryPromise === recovery) recoveryPromise = null; });
    recoveryPromise = recovery;
  }
  return recoveryPromise;
}
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { stopPolling(); recoveryPromise = null; readController?.abort(); motionPanel.pause(); batchPanel.pause(); $('motion-render-video').pause(); }
  else return resumeProject();
});
window.addEventListener('focus', resumeProject);
window.addEventListener('pageshow', resumeProject);
window.addEventListener('pagehide', () => { stopPolling(); recoveryPromise = null; readController?.abort(); sourceController?.abort(); $('audio-preview').pause(); pauseProjectPlayer(); motionPanel.pause(); batchPanel.pause(); $('motion-render-video').pause(); });
window.addEventListener('beforeunload', event => {
  if (jointPlanPanel.hasDrafts() || explanationPanel.hasDrafts() || motionPanel.hasDrafts() || batchPanel.hasDrafts() || continuityPanel.hasDrafts() || [...drafts.values(), ...projectSpeechDrafts.values(), ...speechDrafts.values(), ...timingDrafts.values(), ...captionDrafts.values()].some(draft => draft.dirty)) {
    event.preventDefault(); event.returnValue = '';
  }
});

for (const eventName of ['input', 'change']) {
  $('workspace').addEventListener(eventName, event => {
    // The panel owns its controls. In particular a native select emits input
    // before change; rebuilding it here would undo the new review selection.
    if (!$('joint-plan-panel').contains(event.target)) jointPlanPanel.render();
    if (!$('explanation-panel').contains(event.target)) explanationPanel.render();
    if (!$('motion-panel').contains(event.target) && !$('motion-timing-section').contains(event.target)) motionPanel.render();
    if (!$('batch-panel').contains(event.target)) batchPanel.render();
    if (!$('continuity-panel').contains(event.target)) continuityPanel.render();
  });
}

refresh();
