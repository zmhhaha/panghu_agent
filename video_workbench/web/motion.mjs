/** Semantic motion proposals and recording-bound timing; no executable model HTML. */
import { isActiveOperation, isUnresolvedProductionOperation, statusLabel } from './model.mjs';

const STRATEGIES = { flow: '流程推演', compare: '前后对比', focus: '结构聚焦' };
const ACTIONS = { reveal: '出现', hide: '隐藏', highlight: '强调', move: '移动', draw: '连线展开' };
const PLACEMENTS = { start: '本段开始', middle: '本段中间', end: '本段末尾' };
const PACES = { quick: '较快', normal: '正常', slow: '较慢' };
const ERRORS = {
  motion_disabled: '动态图解暂未启用，历史仍可查看，正在执行的任务仍可取消。',
  motion_profile_unavailable: '动态图解生成服务尚未准备就绪，当前镜头会保留。',
  motion_render_not_configured: '动态图解渲染服务尚未连接，候选与时序仍会保留。',
  motion_render_service_unavailable: '动态图解渲染服务暂时无法连接，请重新读取任务状态。',
  motion_preflight_unavailable: '暂时无法确认动态图解服务状态，请稍后重新读取。',
  motion_plan_stale: '旁白或画面说明已经变化，请生成匹配当前内容的动态图解。',
  motion_semantics_stale: '旁白分段或读音已经变化，请生成匹配当前内容的动态图解。',
  motion_base_stale: '这个修改基础已经过期，请选择匹配当前内容的方案。',
  motion_head_conflict: '采用的动态图解已经变化，请重新读取后审阅。',
  motion_timing_needs_review: '动态时序仍待人工试听核对，完成后才能正式导出。',
  motion_alignment_conflict: '时序已经更新，请重新读取后再校正。',
  motion_audio_binding_mismatch: '配音已变化，请为当前已采用配音重新校正时序。',
  motion_operation_in_progress: '这个镜头已有动态图解任务，请等待结束或申请取消。',
  motion_queue_limit: '动态图解任务队列已满，请等待现有任务结束。',
  motion_result_after_deadline: '本次生成超过等待时间，历史方案与已采用版本仍保留。',
  motion_candidate_limit: '这个镜头的动态图解候选已达到上限，请先检查已有方案。',
  motion_beats_need_explicit_mapping: '旁白与读音分段不一致，请简化读音规则后重新生成。',
  motion_beat_too_short_for_animation: '部分语音片段过短，无法完成这些动画，请校正分段或简化方案。',
  missing_motion_plan: '请先采用一个有效的动态图解方案。',
  submission_uncertain: '提交结果尚未确认，正在查询原任务，不会自动重复生成。',
  cancellation_uncertain: '取消结果尚未确认，正在继续查询真实状态。',
  cancellation_retry_exhausted: '重复取消申请已停止，仍会继续查询真实状态。',
};

export function motionErrorLabel(code) {
  return typeof code === 'string' && Object.hasOwn(ERRORS, code) ? ERRORS[code]
    : '动态图解处理未完成，请重新读取状态并检查当前内容、配音和时序。';
}

export const hasMotionScenes = project => Boolean(project?.scenes?.some(scene => motionMetadata(scene).mode === 'motion'));
export const usesDynamicVideo = project => project?.production_mode === 'motion' || hasMotionScenes(project);
export function createDynamicProjectRequest(project, { unsaved = false, busy = false } = {}) {
  if (!project?.id || !Number.isInteger(project.revision)) throw new Error('请先读取项目。');
  if (unsaved) throw new Error('请先保存或重新载入未保存的修改，再切换制作方式。');
  const operations = [project.storyboard_operation, ...(project.scenes ?? []).flatMap(scene =>
    [scene.operation, scene.audio_operation, scene.motion?.operation])];
  if (busy || operations.some(isUnresolvedProductionOperation)) throw new Error('请等待当前制作任务结束，再切换制作方式。');
  return { path: `/api/projects/${encodeURIComponent(project.id)}/production-mode`,
    body: { mode: 'motion', expected_revision: project.revision } };
}
export const motionMetadata = scene => scene?.motion ?? {
  mode: scene?.production_mode === 'motion' ? 'motion' : 'static', versions: [], head_version_id: null,
};
export function motionSelection(scene, selectedId) {
  const data = motionMetadata(scene), rows = data.versions ?? [];
  return rows.find(row => row.id === selectedId) ?? rows.find(row => row.id === data.head_version_id) ?? rows.at(-1) ?? null;
}
export function currentMotionAudio(scene) {
  if (scene?.audio_stale) return null;
  return scene?.audio_versions?.find(row => row.id === scene.head_audio_version_id && !row.stale) ?? null;
}

export function createMotionRequest(project, scene, instruction = '', base = null) {
  if (!project?.id || !scene?.id || !Number.isInteger(project.revision)
      || typeof instruction !== 'string' || instruction.length > 8000 || base?.stale) {
    throw new Error('请先保存镜头，并检查动态图解要求。');
  }
  return { path: `/api/scenes/${encodeURIComponent(scene.id)}/motion`, body: {
    expected_revision: project.revision, instruction: instruction.trim(), ...(base ? { base_version_id: base.id } : {}),
  } };
}

export function createMotionAdoption(project, scene, candidate, { unsaved = false } = {}) {
  if (unsaved) throw new Error('请先保存或重新载入未保存的修改；你的草稿会保留。');
  if (!candidate?.id || candidate.stale || !motionMetadata(scene).versions?.some(row => row.id === candidate.id)) {
    throw new Error('请选择匹配当前镜头的动态图解方案。');
  }
  return { path: `/api/scenes/${encodeURIComponent(scene.id)}/motion/adopt`, body: {
    expected_revision: project.revision, version_id: candidate.id,
    expected_head_version_id: motionMetadata(scene).head_version_id ?? null,
  } };
}

export function validateMotionSegments(segments, timing) {
  if (!Array.isArray(segments) || segments.length !== timing?.beats?.segments?.length) return false;
  let previous = 0;
  return segments.every((row, index) => {
    const valid = row.beat_id === timing.beats.segments[index].id && Number.isFinite(row.start)
      && Number.isFinite(row.end) && row.start >= previous && row.start >= 0
      && row.end - row.start >= 0.12 - 1e-9 && row.end <= timing.audio.duration_seconds + 1e-9;
    previous = row.end;
    return valid;
  });
}

export function motionTimingDraft(timing) {
  return { expected_revision: timing.project_revision, audio_version_id: timing.audio_version_id,
    expected_alignment_sha256: timing.alignment_sha256,
    segments: timing.alignment.segments.map(row => ({ ...row })), correction_reason: '', dirty: false };
}

export function createMotionRenderRequest(project, { purpose = 'draft', scene, candidate, audio } = {}) {
  if (!project?.id || !Number.isInteger(project.revision) || !['draft', 'delivery'].includes(purpose)) {
    throw new Error('请先读取项目。');
  }
  const body = { expected_revision: project.revision, purpose, subtitles: false };
  if (purpose === 'draft') {
    if (!scene?.id || !candidate?.id || candidate.stale || !audio?.id || audio.stale) {
      throw new Error('请先选择有效动态图解，并采用匹配当前旁白的配音。');
    }
    Object.assign(body, { scene_id: scene.id, motion_version_id: candidate.id, audio_version_id: audio.id });
  }
  return { path: `/api/projects/${encodeURIComponent(project.id)}/motion-renders`, body };
}

export function createMotionPanel({ document, getContext, request, refresh, onError, onSuccess, onPreviewStateChange = () => {} }) {
  const $ = id => document.getElementById(id);
  const metadata = new Map(), versions = new Map(), timings = new Map(), drafts = new Map();
  const selections = new Map(), instructions = new Map(), busy = new Set(), readErrors = new Map();
  let sequence = 0, latestRead = 0, renderedTiming = null, renderedReview = null, confirmation = null;
  let draftSequence = 0, auditionEnd = null;
  const draftIdentities = new WeakMap();
  const context = () => getContext();
  // Explicit project reads must not rebuild the parent context: its busy checks
  // query the other panels. Keep scene fallback before metadata has loaded.
  const operationScenes = (projectId, fallbackScenes) => {
    const project = projectId === undefined ? context().project : { id: projectId, scenes: fallbackScenes };
    return metadata.get(project?.id)?.scenes ?? project?.scenes ?? [];
  };
  const same = value => context().project?.id === value.project?.id && context().epoch === value.epoch;
  const keyFor = value => `${value.project?.id}:${value.scene?.id}`;
  const timingKey = (id, audioId) => `${id}:${audioId}`;
  const sceneHasDrafts = value => [...drafts.values()].some(row => row.dirty
    && row.project_id === value.project?.id && row.scene_id === value.scene?.id);
  const current = () => {
    const value = context(), stored = metadata.get(value.project?.id);
    const scene = stored?.scenes?.find(row => row.id === value.scene?.id) ?? value.scene;
    const selected = motionSelection(scene, selections.get(keyFor(value)));
    const audio = currentMotionAudio(value.scene);
    const id = selected && !selected.stale && audio ? timingKey(selected.id, audio.id) : null;
    return { ...value, scene, selected, audio, key: keyFor(value), timingId: id,
      version: versions.get(selected?.id), timing: timings.get(id), draft: drafts.get(id),
      fresh: Boolean(stored && stored.revision === value.project?.revision),
    };
  };
  const text = (tag, value, className) => {
    const row = document.createElement(tag);
    if (value !== undefined) row.textContent = value;
    if (className) row.className = className;
    return row;
  };

  function review(value) {
    const signature = JSON.stringify([value.key, value.selected?.id, value.version?.plan_sha256]);
    if (renderedReview === signature) return;
    renderedReview = signature;
    const target = $('motion-review'); target.replaceChildren();
    if (!value.version) return;
    const { plan, beats } = value.version;
    target.append(text('h4', plan.title), text('p', plan.takeaway, 'preserve-whitespace'));
    const elements = new Map(plan.elements.map(row => [row.id, row.label]));
    const relations = new Map(plan.relations.map(row => [row.id,
      `${elements.get(row.source)} → ${elements.get(row.target)}${row.label ? `：${row.label}` : ''}`]));
    const names = new Map(beats.segments.map((row, index) => [row.id, `第 ${index + 1} 段` ]));
    const list = text('ol');
    for (const item of plan.elements) list.append(text('li', `${item.label}${item.detail ? `：${item.detail}` : ''}`));
    target.append(text('h5', `画面结构 · ${STRATEGIES[plan.template] ?? '图解'}`), list);
    if (relations.size) {
      const connections = text('ul');
      for (const label of relations.values()) connections.append(text('li', label));
      target.append(text('h5', '关系'), connections);
    }
    const actions = text('ol');
    for (const action of plan.actions) actions.append(text('li', `${names.get(action.beat_id) ?? '语音分段'} · ${elements.get(action.target) ?? relations.get(action.target) ?? '图形关系'} · ${ACTIONS[action.kind] ?? '变化'} · ${PLACEMENTS[action.placement] ?? ''} · ${PACES[action.pace] ?? ''}`));
    target.append(text('h5', '随旁白展开的变化'), actions);
  }

  function renderTiming(value) {
    const { timing, draft } = value;
    if (draft && !draftIdentities.has(draft)) draftIdentities.set(draft, ++draftSequence);
    const signature = JSON.stringify([value.timingId, draft && draftIdentities.get(draft), value.enabled, busy.has(value.key)]);
    if (renderedTiming === signature) return;
    renderedTiming = signature;
    const target = $('motion-timing-rows'); target.replaceChildren();
    if (!timing || !draft) return;
    timing.beats.segments.forEach((beat, index) => {
      const row = text('div', undefined, 'motion-timing-row');
      const sentence = text('div'); sentence.append(text('strong', `第 ${index + 1} 段`), text('p', beat.source_text));
      if (beat.spoken_text !== beat.source_text) sentence.append(text('small', `实际读法：${beat.spoken_text}`));
      row.append(sentence);
      for (const field of ['start', 'end']) {
        const label = text('label', field === 'start' ? '开始 / 秒' : '结束 / 秒');
        const input = text('input'); input.type = 'number'; input.min = '0'; input.max = String(timing.audio.duration_seconds);
        input.step = '0.01'; input.required = true; input.value = String(draft.segments[index][field]);
        input.disabled = !value.enabled || busy.has(value.key);
        input.addEventListener('input', () => {
          if (!current().enabled || busy.has(value.key)) return;
          draft.segments[index][field] = input.value === '' ? NaN : Number(input.value);
          draft.dirty = true; $('confirm-motion-timing').checked = false; render();
        });
        label.append(input); row.append(label);
      }
      const listen = text('button', '试听这一段', 'text-button'); listen.type = 'button';
      listen.addEventListener('click', async () => {
        const audio = $('motion-timing-audio');
        const start = draft.segments[index].start;
        if (!Number.isFinite(start)) return;
        auditionEnd = draft.segments[index].end;
        audio.currentTime = start;
        try { await audio.play(); } catch (error) { onError(error); }
      });
      row.append(listen); target.append(row);
    });
  }

  function render() {
    const value = current(), { project, scene, selected, audio, timing, draft } = value;
    const data = motionMetadata(scene), active = isActiveOperation(data.operation), inFlight = busy.has(value.key);
    $('motion-panel').hidden = !scene || (!value.enabled && !data.versions?.length && !data.operation && data.mode !== 'motion');
    $('motion-timing-section').hidden = !scene || !selected;
    if (!project || !scene) { $('preview-motion').disabled = true; onPreviewStateChange(false); return; }
    const blocked = !value.fresh || value.unsaved || value.projectBusy || inFlight;
    const signature = JSON.stringify([value.key, project.revision, selected?.id, data.head_version_id,
      timing?.alignment_sha256, audio?.id]);
    if (confirmation !== signature) {
      confirmation = signature; $('confirm-motion-adoption').checked = false; $('confirm-motion-timing').checked = false;
    }
    $('motion-mode-label').textContent = data.mode === 'motion'
      ? data.head_version_id ? data.head_stale ? '已采用图解需要更新' : '已采用动态图解' : '动态图解待制作'
      : scene.head_version_id ? '当前复用静态素材，可制作动态图解替换' : '为本镜头制作动态图解';
    $('motion-use-static').disabled = blocked || data.mode !== 'motion';
    $('motion-use-motion').disabled = blocked || !value.enabled || !data.head_version_id || data.head_stale || data.mode === 'motion';
    $('motion-instruction').value = instructions.get(value.key) ?? '';
    $('motion-instruction').disabled = !value.configured;
    $('generate-motion').disabled = !value.configured || blocked || active;
    $('revise-motion').disabled = !value.configured || blocked || active || !selected || selected.stale || !$('motion-instruction').value.trim();
    $('motion-form-note').textContent = !value.enabled ? '动态图解生成与修改暂未启用；可以查看历史、试听和取消在途任务。'
      : !value.configured ? '生成服务暂未连接；已有候选仍可审阅、采用与校正。'
      : value.unsaved ? '先保存或重新载入未保存的镜头、配音和时序修改，再继续；草稿会保留。'
        : '生成只增加候选。审阅图形变化后明确采用，再结合实际配音核对时序。';
    $('motion-read-error').hidden = !readErrors.has(value.key);
    $('motion-read-error').textContent = readErrors.get(value.key) ?? '';
    $('motion-status').hidden = !data.operation;
    $('motion-status-text').textContent = data.operation ? `${statusLabel(data.operation.status)}${data.operation.error ? ` · ${motionErrorLabel(data.operation.error)}` : ''}` : '';
    $('cancel-motion').hidden = !active;
    $('cancel-motion').disabled = inFlight || data.operation?.status === 'cancelling';
    const options = (data.versions ?? []).map((item, index) => {
      const option = text('option', `图解 ${index + 1}${item.id === data.head_version_id ? ' · 已采用' : ' · 候选'}${item.stale ? ' · 已过期' : ''}`);
      option.value = item.id; return option;
    });
    if (!options.length) options.push(text('option', '尚无动态图解'));
    const optionKey = JSON.stringify([value.key, data.versions?.map(row => [row.id, row.stale]), data.head_version_id]);
    if ($('motion-version').dataset?.optionsKey !== optionKey) {
      $('motion-version').replaceChildren(...options);
      if ($('motion-version').dataset) $('motion-version').dataset.optionsKey = optionKey;
    }
    $('motion-version').disabled = !data.versions?.length || inFlight;
    if (selected) $('motion-version').value = selected.id;
    review(value);
    const adopted = selected?.id === data.head_version_id;
    $('motion-adoption-note').textContent = selected?.stale ? '内容已变化，这份历史图解仅供审阅。'
      : adopted ? '已采用此动态图解。正式导出还需要可用配音与已核对时序。'
        : '采用只切换本镜头的画面模式，不会生成配音或自动导出。';
    const canAdopt = value.enabled && !blocked && !active && selected && !selected.stale && !adopted && value.version;
    $('confirm-motion-adoption').disabled = !canAdopt;
    $('adopt-motion').disabled = !canAdopt || !$('confirm-motion-adoption').checked;
    $('motion-timing-section').hidden = !selected;
    $('motion-timing-status').textContent = selected?.stale ? '旁白或画面说明已变化，历史方案不能校正当前配音时序。'
      : !audio ? '先采用匹配当前旁白的配音，时序会绑定这条实际录音。'
      : !timing ? '正在读取这条配音的时序；未生成新的语音。'
        : timing.alignment.review_status === 'verified' ? `已由人工核对 · 实际配音 ${audio.duration_seconds.toFixed(2)} 秒`
          : timing.alignment.method === 'estimated' ? '当前为按文字比例估算，尚未人工核对；可用于草稿预览。'
            : '手工时序已保存，尚未完成试听核对；可用于草稿预览。';
    const valid = timing && draft && validateMotionSegments(draft.segments, timing);
    const timingFresh = timing?.project_revision === project.revision && draft?.expected_revision === project.revision
      && timing?.alignment_sha256 === draft?.expected_alignment_sha256;
    $('motion-timing-note').textContent = timing && !timingFresh ? '项目或保存时序已变化，本地校正仍保留；请检查并重新载入时序后再保存或核对。'
      : draft?.dirty ? '时序尚未保存；刷新和切换镜头会保留校正。'
        : sceneHasDrafts(value) ? '本镜头其他配音或候选仍有未保存校正。切回原录音继续编辑，或重新载入并放弃本镜头校正。'
        : '时间从这条配音的开头计起，不包含镜头留白。估算与人工核对分别记录。';
    $('save-motion-timing').disabled = !value.enabled || blocked || !draft?.dirty || !valid || !timingFresh;
    $('reset-motion-timing').disabled = inFlight || ((!audio || !selected) && !sceneHasDrafts(value));
    $('confirm-motion-timing').disabled = !value.enabled || blocked || !valid || draft?.dirty || !timingFresh || timing?.alignment.review_status === 'verified';
    $('verify-motion-timing').disabled = $('confirm-motion-timing').disabled || !$('confirm-motion-timing').checked;
    $('motion-correction-reason').value = draft?.correction_reason ?? '';
    $('motion-correction-reason').disabled = !draft || !value.enabled || inFlight;
    $('preview-motion').disabled = !value.renderConfigured || blocked || !selected || selected.stale || !audio || !timing || draft?.dirty;
    onPreviewStateChange(!$('preview-motion').disabled);
    const player = $('motion-timing-audio');
    const audioPath = audio ? `/api/audio-versions/${encodeURIComponent(audio.id)}/audio` : null;
    if (player.dataset?.audioId !== (audio?.id ?? '')) {
      player.pause(); auditionEnd = null; if (audioPath) player.src = audioPath; else player.removeAttribute('src');
      if (player.dataset) player.dataset.audioId = audio?.id ?? '';
    }
    player.hidden = !audio;
    renderTiming(value);
  }

  async function readSelected(value, read) {
    const selected = motionSelection(value.scene, selections.get(keyFor(value))), audio = currentMotionAudio(value.scene);
    if (!selected) return;
    const candidate = await request(`/api/motion-versions/${encodeURIComponent(selected.id)}`);
    if (!same(value) || read !== latestRead) return;
    versions.set(selected.id, candidate);
    if (audio && !selected.stale) {
      const timing = await request(`/api/motion-versions/${encodeURIComponent(selected.id)}/timing?audio_version_id=${encodeURIComponent(audio.id)}`);
      if (!same(value) || read !== latestRead) return;
      const key = timingKey(selected.id, audio.id);
      timings.set(key, timing);
      if (!drafts.get(key)?.dirty) drafts.set(key, { ...motionTimingDraft(timing),
        project_id: value.project.id, scene_id: value.scene.id });
    }
  }

  async function load(projectId, epoch) {
    const value = context();
    if (!value.project || value.project.id !== projectId || value.epoch !== epoch) return;
    const read = ++sequence; latestRead = read;
    try {
      const project = await request(`/api/projects/${encodeURIComponent(projectId)}/motion`);
      if (!same(value) || read !== latestRead) return;
      metadata.set(projectId, project);
      const scene = project.scenes.find(row => row.id === value.scene?.id);
      if (scene && !selections.has(keyFor(value))) {
        const initial = motionSelection(scene);
        if (initial) selections.set(keyFor(value), initial.id);
      }
      if (scene) await readSelected({ ...value, project, scene }, read);
      if (!same(value) || read !== latestRead) return;
      readErrors.delete(keyFor(value)); render();
    } catch (error) {
      if (!same(value) || read !== latestRead) return;
      readErrors.set(keyFor(value), error?.code ? motionErrorLabel(error.code) : '动态图解暂时无法读取，现有内容与校正草稿会保留。');
      render(); if (error?.status === 401) onError(error);
    }
  }

  async function mutate(value, action, accepted) {
    if (busy.has(value.key)) return;
    busy.add(value.key); render();
    try { const result = await action(); if (same(value)) accepted?.(result); }
    catch (error) { if (same(value)) onError(error); }
    finally {
      busy.delete(value.key);
      if (same(value)) { try { await refresh(); } catch (error) { onError(error); } render(); }
    }
  }
  async function generate(revise = false) {
    const value = current();
    if (!value.scene || !value.configured || !value.fresh || value.unsaved || value.projectBusy
        || (revise && (!value.selected || value.selected.stale || !(instructions.get(value.key) ?? '').trim()))
        || isActiveOperation(motionMetadata(value.scene).operation)) return;
    let intent;
    try { intent = createMotionRequest(value.project, value.scene, instructions.get(value.key) ?? '', revise ? value.selected : null); }
    catch (error) { onError(error); return; }
    await mutate(value, () => request(intent.path, { method: 'POST', body: intent.body }), operation => {
      const scene = metadata.get(value.project.id)?.scenes.find(row => row.id === value.scene.id);
      if (scene) scene.motion.operation = operation;
      onSuccess('动态图解任务已提交。完成后先审阅，当前采用状态不会自动变化。');
    });
  }
  async function adopt() {
    const value = current();
    if (!value.enabled || !value.fresh || value.projectBusy || !value.version || busy.has(value.key)
        || isActiveOperation(motionMetadata(value.scene).operation) || !$('confirm-motion-adoption').checked
        || value.selected?.id === motionMetadata(value.scene).head_version_id) return;
    let intent;
    try { intent = createMotionAdoption(value.project, value.scene, value.selected, value); }
    catch (error) { onError(error); return; }
    await mutate(value, () => request(intent.path, { method: 'POST', body: intent.body }), () => onSuccess('已采用动态图解。请试听并核对实际配音时序，再正式导出。'));
  }
  async function mode(mode) {
    const value = current();
    if (!['static', 'motion'].includes(mode) || !value.scene || !value.fresh || value.unsaved || value.projectBusy
        || (!value.enabled && mode !== 'static')) return;
    const data = motionMetadata(value.scene);
    if (data.mode === mode || (mode === 'motion' && (!data.head_version_id || data.head_stale))) return;
    await mutate(value, () => request(`/api/scenes/${encodeURIComponent(value.scene.id)}/visual-mode`,
      { method: 'POST', body: { expected_revision: value.project.revision, mode } }), () => onSuccess(mode === 'static' ? '本镜头将复用静态素材，动态图解历史仍保留。' : '本镜头已切换为已采用的动态图解。'));
  }
  async function cancel() {
    const value = current(), op = motionMetadata(value.scene).operation;
    if (!isActiveOperation(op) || op.status === 'cancelling') return;
    await mutate(value, () => request(`/api/motion-operations/${encodeURIComponent(op.id)}/cancel`, { method: 'POST', body: {} }),
      operation => {
        const scene = metadata.get(value.project.id)?.scenes.find(row => row.id === value.scene.id);
        if (scene) scene.motion.operation = operation;
        onSuccess('已申请取消，将继续查询真实状态。');
      });
  }
  async function saveTiming(verify = false) {
    const value = current(), { timing, draft, project } = value;
    if (!value.enabled || !timing || !draft || value.unsaved || value.projectBusy || !value.fresh
        || timing.project_revision !== project.revision || draft.expected_revision !== project.revision
        || timing.alignment_sha256 !== draft.expected_alignment_sha256 || !validateMotionSegments(draft.segments, timing)) return;
    if (verify ? draft.dirty || !$('confirm-motion-timing').checked || timing.alignment.review_status === 'verified' : !draft.dirty) return;
    const body = { expected_revision: draft.expected_revision, audio_version_id: draft.audio_version_id,
      expected_alignment_sha256: draft.expected_alignment_sha256 };
    if (!verify) Object.assign(body, { segments: draft.segments.map(row => ({ ...row })), correction_reason: draft.correction_reason || '结合实际旁白手动校正' });
    await mutate(value, () => request(`/api/motion-versions/${encodeURIComponent(value.selected.id)}/timing${verify ? '/verify' : ''}`,
      { method: verify ? 'POST' : 'PATCH', body }), () => {
      drafts.delete(value.timingId); renderedTiming = null;
      onSuccess(verify ? '已记录这条实际配音的人工时序核对。' : '手工时序已保存，试听核对后再明确确认。');
    });
  }
  async function preview() {
    const value = current();
    if (!value.renderConfigured || !value.fresh || value.unsaved || value.projectBusy || value.draft?.dirty || !value.timing) return;
    let intent;
    try { intent = createMotionRenderRequest(value.project, { scene: value.scene, candidate: value.selected, audio: value.audio }); }
    catch (error) { onError(error); return; }
    await mutate(value, () => request(intent.path, { method: 'POST', body: intent.body }), () => onSuccess('已开始制作本镜头 MP4 草稿。在「项目预览与成片」中播放；这不会采用候选或确认时序。'));
  }
  $('motion-instruction').addEventListener('input', event => { instructions.set(current().key, event.target.value); render(); });
  $('motion-version').addEventListener('change', async event => {
    const value = current(); selections.set(value.key, event.target.value); $('motion-timing-audio').pause();
    render(); await load(value.project.id, value.epoch);
  });
  $('generate-motion').addEventListener('click', () => generate(false));
  $('revise-motion').addEventListener('click', () => generate(true));
  $('adopt-motion').addEventListener('click', adopt);
  $('motion-use-static').addEventListener('click', () => mode('static'));
  $('motion-use-motion').addEventListener('click', () => mode('motion'));
  $('cancel-motion').addEventListener('click', cancel);
  $('confirm-motion-adoption').addEventListener('change', render);
  $('confirm-motion-timing').addEventListener('change', render);
  $('save-motion-timing').addEventListener('click', () => saveTiming(false));
  $('verify-motion-timing').addEventListener('click', () => saveTiming(true));
  $('preview-motion').addEventListener('click', preview);
  $('reload-motion').addEventListener('click', () => refresh().catch(onError));
  $('reset-motion-timing').addEventListener('click', async () => {
    const value = current();
    for (const [key, draft] of drafts) {
      if (draft.project_id === value.project.id && draft.scene_id === value.scene.id) drafts.delete(key);
    }
    renderedTiming = null;
    await load(value.project.id, value.epoch);
  });
  $('motion-correction-reason').addEventListener('input', event => {
    const value = current();
    if (value.draft && value.enabled && !busy.has(value.key)) value.draft.correction_reason = event.target.value;
  });
  $('motion-timing-audio').addEventListener('timeupdate', () => {
    const audio = $('motion-timing-audio');
    if (Number.isFinite(auditionEnd) && audio.currentTime >= auditionEnd) { audio.pause(); auditionEnd = null; }
  });
  return { render, load, generate, adopt, mode, cancel, saveTiming, preview,
    select: (projectId, sceneId, versionId) => {
      if (projectId && sceneId && versionId) selections.set(`${projectId}:${sceneId}`, versionId);
    },
    hasActiveOperation: (projectId, fallbackScenes) => operationScenes(projectId, fallbackScenes).some(scene => isActiveOperation(scene.motion?.operation)),
    hasUnresolvedOperation: (projectId, fallbackScenes) => operationScenes(projectId, fallbackScenes).some(scene => isUnresolvedProductionOperation(scene.motion?.operation)),
    hasDrafts: () => [...drafts.values()].some(row => row.dirty),
    hasProjectDrafts: projectId => [...drafts.values()].some(row => row.dirty && row.project_id === projectId),
    pause: () => { auditionEnd = null; $('motion-timing-audio').pause(); },
  };
}
