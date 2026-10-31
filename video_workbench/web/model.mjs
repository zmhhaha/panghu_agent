/** Browser state rules shared by the workbench and its concurrency tests. */
export const ACTIVE_STATUSES = new Set([
  'pending', 'queued', 'submitting', 'submitted', 'preparing', 'dispatching', 'running',
  'validating', 'finalizing', 'publishing', 'cancelling', 'cancel_requested',
]);

const STATUS_LABELS = {
  pending: '等待处理', queued: '排队中', submitting: '正在提交', submitted: '已提交', preparing: '准备中',
  dispatching: '正在提交', running: '制作中', validating: '检查中', finalizing: '整理结果',
  publishing: '保存结果', cancelling: '正在取消', cancel_requested: '已申请取消',
  completed: '已完成', succeeded: '已完成', failed: '制作失败', cancelled: '已取消',
  timed_out: '执行超时', rejected: '未能提交', needs_input: '需要补充内容',
  quarantined: '等待服务处理', stopped: '已停止', uncertain: '提交结果待确认',
};

export function statusLabel(status) {
  return STATUS_LABELS[status] ?? '状态待确认';
}

export function isActiveOperation(operation) {
  return Boolean(operation?.id && ACTIVE_STATUSES.has(operation.status));
}

export function activeOperations(project) {
  const candidates = [project?.storyboard_operation,
    ...(project?.scenes ?? []).flatMap(scene => [scene.operation, scene.audio_operation])];
  const seen = new Set();
  return candidates.filter(operation => {
    if (!isActiveOperation(operation) || seen.has(operation.id)) return false;
    seen.add(operation.id);
    return true;
  });
}

export function createWorkspaceState() {
  return {
    projectId: null, epoch: 0, requestSequence: 0, appliedRequest: 0,
    project: null, sceneId: null, selectedVersions: new Map(), selectedAudioVersions: new Map(),
  };
}

export function selectProject(state, projectId) {
  state.projectId = projectId;
  state.epoch += 1;
  state.appliedRequest = 0;
  state.project = null;
  state.sceneId = null;
  state.selectedVersions.clear();
  state.selectedAudioVersions.clear();
}

/** Project detail reads also refresh the matching sidebar summary. */
export function updateProjectSummary(projects, project) {
  return projects.map(summary => summary.id === project.id ? {
    ...summary, title: project.title, scene_count: project.scenes?.length ?? 0,
    updated_at: project.updated_at ?? summary.updated_at,
  } : summary);
}

export function issueProjectRead(state) {
  return {
    projectId: state.projectId,
    epoch: state.epoch,
    sequence: ++state.requestSequence,
  };
}

export function orderedVersions(scene) {
  return [...(scene?.versions ?? [])].sort((a, b) => a.number - b.number);
}

export function latestVersion(scene) {
  return orderedVersions(scene).at(-1) ?? null;
}

/** A response belongs to both a selection epoch and a monotonic read. */
export function acceptProjectRead(state, ticket, project) {
  if (!project || ticket.projectId !== state.projectId || ticket.epoch !== state.epoch
      || project.id !== state.projectId || ticket.sequence < state.appliedRequest) return false;
  if (state.project && Number.isFinite(state.project.revision) && Number.isFinite(project.revision)
      && project.revision < state.project.revision) return false;
  state.project = project;
  state.appliedRequest = ticket.sequence;
  const scenes = project.scenes ?? [];
  if (!scenes.some(scene => scene.id === state.sceneId)) state.sceneId = scenes[0]?.id ?? null;
  for (const scene of scenes) {
    const versions = orderedVersions(scene);
    const selected = state.selectedVersions.get(scene.id);
    if (!versions.some(version => version.id === selected)) {
      const head = versions.find(version => version.id === scene.head_version_id);
      state.selectedVersions.set(scene.id, head?.id ?? versions.at(-1)?.id ?? null);
    }
    const audio = orderedAudioVersions(scene);
    if (!audio.some(version => version.id === state.selectedAudioVersions.get(scene.id))) {
      state.selectedAudioVersions.set(scene.id,
        audio.find(version => version.id === scene.head_audio_version_id)?.id ?? audio.at(-1)?.id ?? null);
    }
  }
  return true;
}

export function selectedScene(state) {
  return state.project?.scenes?.find(scene => scene.id === state.sceneId) ?? null;
}

export function selectScene(state, sceneId) {
  if (!state.project?.scenes?.some(scene => scene.id === sceneId)) return false;
  state.sceneId = sceneId;
  return true;
}

export function selectedVersion(state, scene = selectedScene(state)) {
  return scene?.versions?.find(version => version.id === state.selectedVersions.get(scene.id)) ?? null;
}

export function chooseVersion(state, sceneId, versionId) {
  const scene = state.project?.scenes?.find(candidate => candidate.id === sceneId);
  if (!scene?.versions?.some(version => version.id === versionId)) return false;
  state.selectedVersions.set(sceneId, versionId);
  return true;
}

export function createSceneDraft(scene = {}, revision = 0) {
  return {
    title: scene.title ?? '', narration: scene.narration ?? '',
    visual_description: scene.visual_description ?? '',
    duration_seconds: scene.duration_seconds ?? 8,
    format: scene.format ?? 'svg', expected_revision: revision, dirty: false,
  };
}

export function syncSceneDraft(draft, scene, revision) {
  return draft?.dirty ? draft : createSceneDraft(scene, revision);
}

/** Freeze the clicked values before rendering, polling or uncertain retries. */
export function createStoryboardRequest(project, maxScenes = 12) {
  if (!project?.id || !Number.isInteger(maxScenes) || maxScenes < 1 || maxScenes > 12) {
    throw new Error('Invalid storyboard limit');
  }
  return Object.freeze({
    path: `/api/projects/${encodeURIComponent(project.id)}/storyboard`,
    body: Object.freeze({ expected_revision: project.revision, max_scenes: maxScenes }),
  });
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort()
      .filter(key => value[key] !== undefined).map(key => [key, canonical(value[key])]));
  }
  return value;
}

export function intentFingerprint(method, path, payload) {
  return JSON.stringify([method, path, canonical(payload)]);
}

/** Reuse the key after an uncertain response; retire it only on success. */
function randomIntentKey() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function createIntentKeys(makeKey = randomIntentKey) {
  const keys = new Map();
  return {
    get(method, path, payload) {
      const fingerprint = intentFingerprint(method, path, payload);
      if (!keys.has(fingerprint)) keys.set(fingerprint, makeKey());
      return keys.get(fingerprint);
    },
    complete(method, path, payload) {
      keys.delete(intentFingerprint(method, path, payload));
    },
  };
}

export function versionPath(versionId, action = 'preview') {
  if (!['preview', 'source'].includes(action)) throw new Error('Unsupported version action');
  return `/api/versions/${encodeURIComponent(versionId)}/${action}`;
}

export function orderedAudioVersions(scene) {
  return [...(scene?.audio_versions ?? [])].sort((a, b) => (a.number ?? 0) - (b.number ?? 0));
}

export function selectedAudioVersion(state, scene = selectedScene(state)) {
  return scene?.audio_versions?.find(version => version.id === state.selectedAudioVersions.get(scene.id)) ?? null;
}

export function chooseAudioVersion(state, sceneId, versionId) {
  const scene = state.project?.scenes?.find(candidate => candidate.id === sceneId);
  if (!scene?.audio_versions?.some(version => version.id === versionId)) return false;
  state.selectedAudioVersions.set(sceneId, versionId);
  return true;
}

export function audioPath(versionId) {
  return `/api/audio-versions/${encodeURIComponent(versionId)}/audio`;
}

/** Name the scenes that still need resources for the selected delivery mode. */
export function deliveryIssues(project, { silent = false, subtitles = true } = {}) {
  return (project?.scenes ?? []).flatMap((scene, index) => {
    const reasons = [];
    if (!scene.head_version_id) reasons.push('missing_visual');
    if (!silent) {
      if (!scene.head_audio_version_id) reasons.push('missing_audio');
      else if (scene.audio_stale) reasons.push('stale_audio');
      if (subtitles) {
        const captions = scene.captions;
        if (!captions?.cues?.length) reasons.push('missing_captions');
        else if (captions.stale || captions.timing_stale || scene.audio_stale
            || (captions.audio_version_id && captions.audio_version_id !== scene.head_audio_version_id)) {
          reasons.push('stale_captions');
        }
      }
    }
    return reasons.length ? [{ sceneId: scene.id, number: index + 1, title: scene.title ?? '', reasons }] : [];
  });
}

/** One pronunciation override per line; duplicates are refused instead of lost. */
export function parsePronunciation(value) {
  const pairs = [];
  const seen = new Set();
  for (const line of String(value).split('\n')) {
    if (!line.trim()) continue;
    const position = line.indexOf('=');
    const word = line.slice(0, position).trim();
    const replacement = line.slice(position + 1).trim();
    if (position < 1 || !word || !replacement || seen.has(word)
        || pairs.length >= 32 || word.length > 80 || replacement.length > 200) {
      throw new Error('每行填写「原词=读法」，原词不能重复；原词最多80字、读法最多200字，最多32条。');
    }
    seen.add(word);
    pairs.push([word, replacement]);
  }
  return Object.fromEntries(pairs);
}

export function pronunciationText(value = {}) {
  return Object.entries(value).map(([word, replacement]) => `${word}=${replacement}`).join('\n');
}

/** Caption edits stay bound to the exact adopted audio and concurrency revision. */
export function createCaptionDraft(scene, revision) {
  return { expected_revision: revision, audio_version_id: scene.head_audio_version_id,
    leading_seconds: Number(scene.leading_seconds ?? 0), trailing_seconds: Number(scene.trailing_seconds ?? 0),
    cues: (scene.captions?.cues ?? []).map(cue => ({ start: cue.start, end: cue.end, text: cue.text })),
    dirty: false };
}

export function validateCaptionCues(cues, duration, { leadingSeconds = 0, audioSeconds = null } = {}) {
  if (!Array.isArray(cues) || cues.length < 1 || cues.length > 200 || !Number.isFinite(duration) || duration <= 0) return false;
  const endLimit = audioSeconds === null ? duration : Math.min(duration, leadingSeconds + audioSeconds);
  let previous = leadingSeconds;
  let characters = 0;
  return cues.every(cue => {
    characters += typeof cue.text === 'string' ? cue.text.length : 0;
    const valid = Number.isFinite(cue.start) && Number.isFinite(cue.end)
      && cue.start >= previous && cue.start >= leadingSeconds && cue.end > cue.start
      && Math.round(cue.end * 1000) > Math.round(cue.start * 1000)
      && cue.end <= endLimit && typeof cue.text === 'string'
      && cue.text.trim().length > 0 && cue.text.length <= 500 && characters <= 10000
      && !cue.text.replace(/\r\n?/g, '\n').includes('\n\n');
    previous = cue.end;
    return valid;
  });
}

/** Playback follows the server's measured timeline; audio is never cut to a shorter scene. */
export function timelinePosition(timeline, seconds) {
  if (!Array.isArray(timeline?.scenes) || !Number.isFinite(seconds) || seconds < 0) return null;
  for (const scene of timeline.scenes) {
    const start = Number(scene.start_seconds);
    const duration = Number(scene.duration_seconds);
    if (Number.isFinite(start) && Number.isFinite(duration) && duration > 0
        && seconds >= start && seconds < start + duration) {
      const sceneTime = seconds - start;
      const audioTime = sceneTime - Number(scene.leading_seconds ?? 0);
      const audioDuration = Number(scene.audio_duration_seconds ?? 0);
      return { scene, sceneTime, audioTime,
        speaking: Boolean(scene.audio_version_id && !scene.audio_stale && audioTime >= 0 && audioTime < audioDuration),
        caption: (scene.cues ?? []).find(cue => sceneTime >= cue.start && sceneTime < cue.end)?.text ?? '' };
    }
  }
  return null;
}
