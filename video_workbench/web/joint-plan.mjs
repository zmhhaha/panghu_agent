/** Whole-video planning from the saved script. Media production is a separate step. */
import { isActiveOperation, isUnresolvedProductionOperation, operationNeedsRefresh,
  operationElapsedLabel, mutationErrorLabel, operationServiceErrorLabel, statusLabel } from './model.mjs';

const TONES = new Set(['conversational', 'precise', 'storytelling']);
const ERRORS = {
  submission_uncertain: '提交结果尚未确认，正在查询原任务，不会自动重复生成。',
  submission_unconfirmed: '本次提交未能确认，请重新读取状态后再决定是否生成。',
  cancellation_uncertain: '取消结果尚未确认，正在继续查询原任务。',
  cancellation_retry_exhausted: '取消结果仍未确认，已停止重复申请；会继续查询真实状态。',
  joint_plan_disabled: '整片方案尚未启用，已有项目仍可查看。',
  joint_plan_preflight_unavailable: '暂时无法确认方案服务是否可用，请稍后重试。',
  joint_plan_profile_unavailable: '方案服务暂未准备就绪，请稍后重试。',
  joint_plan_deadline_expired: '本次规划时间已到，请重新读取状态。',
  joint_plan_result_after_deadline: '方案返回时已超过本次处理时限，没有保存为候选。',
  joint_plan_stale: '项目内容已变化，请重新生成匹配当前文稿的方案。',
  joint_plan_plan_stale: '项目内容已变化，请重新生成匹配当前文稿的方案。',
  joint_plan_version_stale: '项目内容已变化，这份历史方案仅供查看。',
  joint_plan_head_conflict: '已采用方案发生变化，请重新读取后再确认。',
  joint_plan_operation_in_progress: '项目已有方案任务，请等待结束或申请取消。',
  joint_plan_queue_limit: '方案服务正在处理其他任务，请稍后再提交。',
  joint_plan_operation_limit: '此项目的规划记录已达到保存上限。',
  joint_plan_candidate_limit: '此项目的候选方案已达到保存上限。',
  joint_plan_storage_limit: '方案存储空间已达到上限，暂时无法新增候选。',
  joint_plan_candidate_too_large: '返回的方案内容过大，本次没有保存候选。',
  joint_plan_input_too_large: '文稿或项目内容过长，请精简后再规划。',
  joint_plan_narration_requires_split: '方案中的个别段落过长，请补充拆分要求后重新规划。',
  joint_plan_visual_requires_split: '方案中的个别画面过于复杂，请补充拆分或简化要求后重新规划。',
  joint_plan_script_not_preserved: '这份方案未能完整保留原稿措辞，没有保存为候选；请检查原稿和保留原稿选项。',
  agent_failed: '本次方案生成未完成，请检查要求后重新提交。',
  agent_timed_out: '本次方案生成超时，没有保存候选。',
  agent_needs_input: '请检查文稿和制作要求，补充缺少的信息。',
  reconciliation_failed: '任务状态暂时无法更新，正在继续查询。',
};

export function jointPlanErrorLabel(code) {
  return operationServiceErrorLabel(code) ?? (typeof code === 'string' && Object.hasOwn(ERRORS, code) ? ERRORS[code]
    : '整片方案暂未完成，请重新读取状态。');
}

export function jointPlanDraft() {
  return { audience: '', goal: '', tone: 'conversational', instruction: '', preserve_script: false, dirty: false };
}

export function createJointPlanRequest(project, draft) {
  if (!project?.id || !Number.isInteger(project.revision)) throw new Error('请先读取项目。');
  if (typeof project.script !== 'string' || !project.script.trim()) throw new Error('请先保存一份非空的总文稿。');
  for (const [field, maximum] of [['audience', 300], ['goal', 1200], ['instruction', 2000]]) {
    if (typeof draft?.[field] !== 'string' || draft[field].length > maximum) throw new Error('请检查制作要求的内容和长度。');
  }
  if (!TONES.has(draft.tone) || typeof draft.preserve_script !== 'boolean') throw new Error('请选择有效的讲述方式与原稿选项。');
  return Object.freeze({ path: `/api/projects/${encodeURIComponent(project.id)}/joint-plan`,
    body: Object.freeze({ expected_revision: project.revision,
      brief: Object.freeze({ audience: draft.audience.trim(), goal: draft.goal.trim(), tone: draft.tone }),
      instruction: draft.instruction.trim(), preserve_script: draft.preserve_script }) });
}

export function createJointPlanAdoption(project, data, version, { unsaved = false } = {}) {
  if (unsaved) throw new Error('请先保存或重新载入未保存的项目修改，再采用整份方案。你的草稿会保留。');
  if (!project?.id || !Number.isInteger(project.revision) || !version?.id
      || !data?.versions?.some(row => row.id === version.id) || version.stale
      || version.adoptable !== true || version.id === data.head_version_id) {
    throw new Error('这份方案当前不能采用，请重新读取或生成匹配当前文稿的方案。');
  }
  return Object.freeze({ path: `/api/projects/${encodeURIComponent(project.id)}/joint-plan/adopt`,
    body: Object.freeze({ expected_revision: project.revision, version_id: version.id,
      expected_head_version_id: data.head_version_id ?? null }) });
}

const timestamp = value => typeof value === 'number' ? value * 1000 : Date.parse(value) || 0;
export const orderedJointPlanVersions = data => [...(data?.versions ?? [])].sort((a, b) => timestamp(a.created_at) - timestamp(b.created_at));
export function jointPlanSelection(data, id) {
  const versions = orderedJointPlanVersions(data);
  return versions.find(row => row.id === id) ?? versions.find(row => row.id === data?.head_version_id) ?? versions.at(-1) ?? null;
}
const validEstimate = value => typeof value === 'number' && Number.isFinite(value) && value > 0;
export function jointPlanEstimate(version) {
  const scenes = version?.result?.scenes;
  if (!scenes?.length || !scenes.every(scene => validEstimate(scene.duration_seconds))) return null;
  return Math.round(scenes.reduce((sum, scene) => sum + scene.duration_seconds, 0) * 10) / 10;
}

export function createJointPlanPanel({ document, getContext, request, refresh, onError, onSuccess }) {
  const $ = id => document.getElementById(id);
  const cache = new Map(), drafts = new Map(), selections = new Map(), busy = new Set();
  const revisions = new Map(), readErrors = new Map(), writeErrors = new Map(), readSequences = new Map();
  const restored = new Set();
  let sequence = 0, reviewKey = null, confirmationKey = null;
  const current = () => getContext();
  const same = context => current().project?.id === context.project?.id && current().epoch === context.epoch;
  const dataFor = id => cache.get(id);
  const draftFor = id => { if (!drafts.has(id)) drafts.set(id, jointPlanDraft()); return drafts.get(id); };
  const text = (tag, value, className) => {
    const element = document.createElement(tag);
    if (value !== undefined) element.textContent = String(value);
    if (className) element.className = className;
    return element;
  };
  const addText = (parent, heading, value) => {
    parent.append(text('h5', heading), text('p', value || '未提供', 'preserve-whitespace'));
  };
  const addList = (parent, heading, rows, ordered = false, fallback = '未提供') => {
    parent.append(text('h5', heading));
    const list = text(ordered ? 'ol' : 'ul');
    for (const row of Array.isArray(rows) ? rows : []) list.append(text('li', row));
    if (!list.children.length) list.append(text('li', fallback));
    parent.append(list);
  };

  function review(project, version, key) {
    if (reviewKey === key) return;
    reviewKey = key;
    const target = $('joint-plan-review'); target.replaceChildren();
    if (!version) return;
    const result = version.result ?? {}, estimate = jointPlanEstimate(version);
    target.append(text('h3', '整片方案'), text('p', result.summary ?? '', 'preserve-whitespace'));
    addText(target, '希望观众理解什么', result.understanding_goal);
    const overview = text('div', undefined, 'joint-plan-overview');
    const style = text('section'), continuity = text('section');
    addText(style, '全片视觉方向', result.visual_style);
    addText(continuity, '段落之间如何衔接', result.continuity);
    overview.append(style, continuity); target.append(overview);
    target.append(text('p', `${result.scenes?.length ?? 0} 个表达段落${estimate === null ? '' : ` · 预计 ${estimate} 秒`}。时长为文本估算，实际以配音录音为准。全片沿用项目统一音色。`, 'field-note'));
    // Recommendations appear only when this exact candidate contains a decision.
    if (result.method_decision != null) {
      const decision = text('details', undefined, 'joint-plan-method');
      decision.append(text('summary', '本方案的表达方法建议'));
      if (typeof result.method_decision === 'string') decision.append(text('p', result.method_decision, 'preserve-whitespace'));
      else {
        const segments = new Map((result.scenes ?? []).map((scene, index) => [scene.segment_id, `段落 ${index + 1} · ${scene.title}`]));
        for (const [index, row] of (result.method_decision.decisions ?? []).entries()) {
          const label = { apply: '使用', adapt: '调整后使用', reject: '本片不使用' }[row.action] ?? '待核对';
          addText(decision, `方法建议 ${index + 1} · ${label}`, row.reason);
          const targets = (row.segment_ids ?? []).map(id => segments.get(id)).filter(Boolean);
          if (targets.length) decision.append(text('p', `用于：${targets.join('；')}`, 'field-note'));
        }
      }
      target.append(decision);
    }
    const warnings = text('div', undefined, 'explanation-warnings');
    addList(warnings, '内容核对', result.warnings, false, '请核对事实、数字和术语。');
    warnings.append(text('p', '来源摘录用于追溯总文稿，请检查改写后的事实和条件。', 'field-note'));
    target.append(warnings);
    for (const [index, scene] of (result.scenes ?? []).entries()) {
      const details = text('details', undefined, 'explanation-scene');
      details.append(text('summary', `段落 ${index + 1} · ${scene.title ?? ''}${validEstimate(scene.duration_seconds) ? ` · 预计 ${scene.duration_seconds} 秒` : ''}`));
      const content = text('div', undefined, 'joint-plan-segment');
      addText(content, '旁白', scene.narration);
      addText(content, '用什么画面讲清楚', scene.visual_description);
      addText(content, '表达方式', scene.expression);
      addList(content, '关键点', scene.key_points);
      addList(content, '画面如何展开', scene.visual_steps, true);
      if (scene.delivery_note) addText(content, '讲述建议', scene.delivery_note);
      if (Object.keys(scene.pronunciation ?? {}).length) addText(content, '读音修正', Object.entries(scene.pronunciation).map(([word, spoken]) => `${word} = ${spoken}`).join('\n'));
      addList(content, '来源摘录', scene.source_quotes);
      details.append(content); target.append(details);
    }
  }

  function render() {
    const context = current(), project = context.project, existing = dataFor(project?.id);
    $('joint-plan-panel').hidden = !project || (!context.configured && !existing?.operation && !existing?.versions?.length);
    if (!project) return;
    const data = dataFor(project.id), draft = draftFor(project.id);
    const fresh = Boolean(data && revisions.get(project.id) === project.revision);
    const operation = data?.operation, active = isActiveOperation(operation), unresolved = isUnresolvedProductionOperation(operation), submitting = busy.has(project.id);
    const blocked = Boolean(context.unsaved || context.projectBusy);
    for (const field of ['audience', 'goal', 'tone', 'instruction']) {
      const input = $(`joint-plan-${field}`);
      if (input.value !== draft[field]) input.value = draft[field];
      input.disabled = !context.configured;
    }
    $('joint-plan-preserve-script').checked = draft.preserve_script;
    $('joint-plan-preserve-script').disabled = !context.configured;
    $('generate-joint-plan').disabled = !context.configured || !fresh || !project.script?.trim() || unresolved || submitting || blocked;
    $('generate-joint-plan').textContent = active || submitting ? '正在规划整片…' : unresolved ? '正在确认任务状态…' : '生成整片方案';
    $('reload-joint-plan').disabled = submitting;
    const error = writeErrors.get(project.id) ?? readErrors.get(project.id);
    $('joint-plan-read-error').hidden = !error; $('joint-plan-read-error').textContent = error ?? '';
    $('joint-plan-form-note').textContent = !context.configured ? '暂未开放新方案，已有方案仍可查看。'
      : context.unsaved ? '请先保存或重新载入未保存的项目修改。你的草稿会保留。'
      : context.projectBusy ? '项目中还有正在处理的任务，请等待结束后再规划或采用。'
      : !project.script?.trim() ? '请先保存一份总文稿。无需提前拆分镜头。'
      : draft.dirty ? '制作要求已修改，切换项目或读取进度会保留这些输入。'
      : '从总文稿统筹旁白、画面与衔接，由 Agent 决定段落数量；先审阅，再整体确认。';
    const banner = $('joint-plan-status'); banner.hidden = !operation;
    banner.classList.toggle('active', active);
    banner.classList.toggle('failed', ['failed', 'rejected', 'timed_out', 'needs_input'].includes(operation?.status));
    const elapsed = operationElapsedLabel(operation);
    $('joint-plan-status-text').textContent = operation ? `整片方案 · ${statusLabel(operation.status)}${elapsed ? ` · ${elapsed}` : ''}${operation.error ? ` · ${jointPlanErrorLabel(operation.error)}` : ''}` : '';
    $('cancel-joint-plan').hidden = !active;
    $('cancel-joint-plan').disabled = submitting || ['cancelling', 'cancel_requested'].includes(operation?.status);
    $('cancel-joint-plan').textContent = ['cancelling', 'cancel_requested'].includes(operation?.status) ? '已申请取消' : '申请取消';
    const selected = jointPlanSelection(data, selections.get(project.id)), versions = orderedJointPlanVersions(data);
    if (selected) selections.set(project.id, selected.id);
    const options = versions.map((version, index) => {
      const option = text('option', `方案 ${index + 1}${version.id === data.head_version_id ? ' · 已采用' : ' · 候选'}${version.stale ? ' · 内容已变化' : ''}`);
      option.value = version.id; return option;
    });
    $('joint-plan-version').replaceChildren(...(options.length ? options : [text('option', '尚无整片方案')]));
    $('joint-plan-version').disabled = !versions.length || submitting;
    if (selected) $('joint-plan-version').value = selected.id;
    $('joint-plan-empty').hidden = Boolean(selected);
    const binding = JSON.stringify([project.id, project.revision, selected?.id, data?.head_version_id, selected?.stale, selected?.adoptable]);
    if (confirmationKey !== binding) { confirmationKey = binding; $('confirm-joint-plan-adoption').checked = false; }
    review(project, selected, JSON.stringify([project.id, project.revision, selected?.id]));
    const adopted = selected?.id === data?.head_version_id;
    const eligible = context.configured && fresh && selected?.adoptable === true && !selected.stale && !adopted;
    $('joint-plan-adoption').hidden = !selected;
    $('joint-plan-adoption-note').textContent = !context.configured ? '当前仅能查看已有方案。'
      : context.unsaved ? '先处理未保存的项目修改，再采用方案。你的输入不会被覆盖。'
      : adopted && (selected?.stale || data?.head_stale) ? '这份方案曾被采用，项目之后已有变化；可生成新的整片方案。'
      : adopted ? '这份方案已采用。段落已保存，可继续制作画面与配音。'
      : selected?.stale ? '项目内容已变化，这份历史方案仅供查看。请重新生成。'
      : !eligible ? '这份方案当前不能采用，请重新读取状态。'
      : `一次确认整份方案，将${project.scenes?.length ? '替换现有镜头结构并保存旧镜头备份' : '创建全部表达段落'}。总文稿和项目统一音色保留；采用后再继续制作画面与配音。`;
    $('confirm-joint-plan-adoption').disabled = !eligible || blocked || unresolved || submitting;
    $('adopt-joint-plan').disabled = !eligible || blocked || unresolved || submitting || !$('confirm-joint-plan-adoption').checked;
    $('joint-plan-new-candidate').hidden = !selected || versions.at(-1)?.id === selected.id;
  }

  async function load(projectId, epoch) {
    const context = current();
    if (context.project?.id !== projectId || context.epoch !== epoch) return;
    const read = ++sequence; readSequences.set(projectId, read);
    try {
      const result = await request(`/api/projects/${encodeURIComponent(projectId)}/joint-plan`);
      if (!same(context) || readSequences.get(projectId) !== read) return;
      cache.set(projectId, result); revisions.set(projectId, context.project.revision); readErrors.delete(projectId);
      if (!restored.has(projectId)) {
        const draft = draftFor(projectId);
        if (!draft.dirty) {
          for (const [field, max] of [['audience', 300], ['goal', 1200]]) {
            if (typeof result.brief?.[field] === 'string' && result.brief[field].length <= max) draft[field] = result.brief[field];
          }
          if (TONES.has(result.brief?.tone)) draft.tone = result.brief.tone;
          if (typeof result.instruction === 'string' && result.instruction.length <= 2000) draft.instruction = result.instruction;
          if (typeof result.preserve_script === 'boolean') draft.preserve_script = result.preserve_script;
        }
        restored.add(projectId);
      }
      render();
    } catch (error) {
      if (!same(context) || readSequences.get(projectId) !== read) return;
      readErrors.set(projectId, '整片方案暂时无法读取，已有项目和未保存输入会保留。可点击「重新读取方案」重试。');
      render(); if (error?.status === 401) onError(error);
    }
  }

  async function mutate(context, action, accepted) {
    const id = context.project.id;
    if (busy.has(id)) return;
    busy.add(id); render();
    try {
      const result = await action();
      if (same(context)) { writeErrors.delete(id); accepted?.(result); }
    } catch (error) {
      if (same(context)) {
        const message = mutationErrorLabel(error); writeErrors.set(id, message);
        onError(error instanceof Error && Number.isInteger(error.status) ? error : new Error(message));
      }
    } finally {
      busy.delete(id);
      if (same(context)) {
        // An uncertain response must discover the original operation, never repeat POST.
        try { await refresh(); } catch (error) { onError(error); }
        render();
      }
    }
  }

  async function generate() {
    const context = current(), project = context.project;
    if (!project || !context.configured || context.unsaved || context.projectBusy || busy.has(project.id)
        || revisions.get(project.id) !== project.revision || isUnresolvedProductionOperation(dataFor(project.id)?.operation)
        || !$('joint-plan-form').reportValidity()) return;
    let intent;
    try { intent = createJointPlanRequest(project, draftFor(project.id)); } catch (error) { onError(error); return; }
    await mutate(context, () => request(intent.path, { method: 'POST', body: intent.body }), operation => {
      cache.set(project.id, { ...(dataFor(project.id) ?? { versions: [], head_version_id: null }), operation });
      const draft = draftFor(project.id), body = intent.body;
      if (draft.audience.trim() === body.brief.audience && draft.goal.trim() === body.brief.goal
          && draft.tone === body.brief.tone && draft.instruction.trim() === body.instruction && draft.preserve_script === body.preserve_script) draft.dirty = false;
      onSuccess('整片方案已开始生成。完成后查看旁白、画面与衔接，再一次确认整份方案。');
    });
  }

  async function adopt() {
    const context = current(), project = context.project, data = dataFor(project?.id);
    if (!project || !context.configured || context.projectBusy || busy.has(project.id) || revisions.get(project.id) !== project.revision
        || isUnresolvedProductionOperation(data?.operation) || !$('confirm-joint-plan-adoption').checked) return;
    let intent;
    try { intent = createJointPlanAdoption(project, data, jointPlanSelection(data, selections.get(project.id)), context); }
    catch (error) { onError(error); render(); return; }
    await mutate(context, () => request(intent.path, { method: 'POST', body: intent.body }), () => {
      $('confirm-joint-plan-adoption').checked = false;
      onSuccess('整份方案已采用，全部表达段落已保存。接下来可制作画面与配音。');
    });
  }

  async function cancel() {
    const context = current(), operation = dataFor(context.project?.id)?.operation;
    if (!context.project || !isActiveOperation(operation) || ['cancelling', 'cancel_requested'].includes(operation.status)) return;
    await mutate(context, () => request(`/api/joint-plan-operations/${encodeURIComponent(operation.id)}/cancel`, { method: 'POST', body: {} }), result => {
      cache.set(context.project.id, { ...dataFor(context.project.id), operation: result });
      onSuccess('已申请取消，将继续查询真实任务状态。');
    });
  }

  for (const field of ['audience', 'goal', 'tone', 'instruction']) {
    $(`joint-plan-${field}`).addEventListener('input', event => {
      const id = current().project?.id; if (!id) return;
      Object.assign(draftFor(id), { [field]: event.target.value, dirty: true }); render();
    });
  }
  $('joint-plan-preserve-script').addEventListener('change', event => {
    const id = current().project?.id; if (!id) return;
    Object.assign(draftFor(id), { preserve_script: event.target.checked, dirty: true }); render();
  });
  $('joint-plan-form').addEventListener('submit', event => { event.preventDefault(); generate(); });
  $('adopt-joint-plan').addEventListener('click', adopt);
  $('cancel-joint-plan').addEventListener('click', cancel);
  $('confirm-joint-plan-adoption').addEventListener('change', render);
  $('joint-plan-version').addEventListener('change', event => {
    if (current().project) selections.set(current().project.id, event.target.value); render();
  });
  $('joint-plan-new-candidate').addEventListener('click', () => {
    const id = current().project?.id; if (id) selections.set(id, orderedJointPlanVersions(dataFor(id)).at(-1)?.id); render();
  });
  $('reload-joint-plan').addEventListener('click', async () => { try { await refresh(); } catch (error) { onError(error); } });
  return { render, load, generate, adopt, cancel, refreshElapsed: render,
    hasDrafts: () => [...drafts.values()].some(draft => draft.dirty),
    hasActiveOperation: (id = current().project?.id) => isActiveOperation(dataFor(id)?.operation),
    hasRefreshableOperation: (id = current().project?.id) => operationNeedsRefresh(dataFor(id)?.operation),
    hasUnresolvedOperation: (id = current().project?.id) => isUnresolvedProductionOperation(dataFor(id)?.operation),
  };
}
