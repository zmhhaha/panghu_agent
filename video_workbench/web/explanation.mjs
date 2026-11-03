/** Optional project explanation planning; generation never starts media work. */
import { isActiveOperation, isUnresolvedProductionOperation, statusLabel } from './model.mjs';

export const EXPLANATION_TONES = new Set(['conversational', 'precise', 'storytelling']);
const STRATEGIES = { flow: '流程与路由', compare: '前后对照', focus: '结构聚焦' };
const EXPLANATION_ERRORS = {
  submission_uncertain: '提交结果尚未确认，正在查询原任务，不会自动重复生成。',
  submission_unconfirmed: '到期前仍未确认提交结果，本次没有保存方案。',
  cancellation_uncertain: '取消结果尚未确认，正在继续查询任务状态。',
  cancellation_retry_exhausted: '多次申请取消后仍未确认，已停止重复申请；会继续查询真实状态。',
  explanation_disabled: '讲解方案功能尚未启用，现有制作流程仍可使用。',
  explanation_preflight_unavailable: '暂时无法确认讲解服务是否可用，请稍后重试。',
  explanation_profile_unavailable: '讲解服务暂未准备就绪，请稍后重试；现有镜头保持原样。',
  explanation_deadline_expired: '本次处理时间已到，请重新读取状态后再决定是否生成。',
  explanation_result_after_deadline: '方案在本次处理时间结束后返回，没有保存为候选。',
  explanation_plan_stale: '项目内容已变化，请重新生成匹配当前镜头的方案。',
  explanation_version_stale: '项目内容已变化，这份历史方案仅供审阅。',
  explanation_head_conflict: '已采用方案发生变化，请重新读取并审阅后再采用。',
  explanation_scene_order_mismatch: '镜头顺序已变化，请重新生成匹配当前项目的方案。',
  explanation_operation_in_progress: '项目已有讲解方案任务，请等待结束或申请取消。',
  explanation_queue_limit: '讲解服务正在处理其他任务，请稍后再提交。',
  explanation_operation_limit: '此项目的方案任务已达到保存上限。',
  explanation_candidate_limit: '此项目的候选方案已达到保存上限。',
  explanation_storage_limit: '方案存储空间已达到上限，暂时无法新增候选。',
  explanation_candidate_too_large: '返回的方案内容过大，本次没有保存候选。',
  explanation_input_too_large: '项目内容超过本次处理范围，请减少镜头或精简文稿。',
  explanation_narration_requires_split: '旁白或读音展开后过长，请先手动拆成更短的镜头，再生成讲解方案或配音。',
  agent_failed: '讲解生成未完成，现有镜头保持原样，可检查要求后重新提交。',
  agent_timed_out: '讲解生成超时，本次没有保存方案。',
  agent_needs_input: '讲解任务需要补充内容，请检查文稿与本次要求。',
  reconciliation_failed: '任务状态暂时无法更新，正在继续查询。',
};

export function explanationErrorLabel(code) {
  return typeof code === 'string' && Object.hasOwn(EXPLANATION_ERRORS, code) ? EXPLANATION_ERRORS[code]
    : '讲解任务暂未完成，可重新读取状态；现有镜头内容会保留。';
}

export function explanationDraft() {
  return { audience: '', goal: '', tone: 'conversational', instruction: '', dirty: false };
}

export function createExplanationRequest(project, draft) {
  if (!project?.id || !Number.isInteger(project.revision)) throw new Error('请先读取项目。');
  for (const [field, maximum] of [['audience', 300], ['goal', 1200], ['instruction', 2000]]) {
    if (typeof draft?.[field] !== 'string' || draft[field].length > maximum) {
      throw new Error('请检查讲解要求的内容和长度。');
    }
  }
  if (!EXPLANATION_TONES.has(draft.tone)) {
    throw new Error('请选择有效的讲述方式。');
  }
  return Object.freeze({
    path: `/api/projects/${encodeURIComponent(project.id)}/explanation`,
    body: Object.freeze({ expected_revision: project.revision,
      brief: Object.freeze({ audience: draft.audience.trim(), goal: draft.goal.trim(), tone: draft.tone }),
      instruction: draft.instruction.trim() }),
  });
}

export function createExplanationAdoption(project, data, version, { unsaved = false } = {}) {
  if (unsaved) throw new Error('请先保存或重新载入项目中的未保存修改，再采用整份方案；你的草稿会保留。');
  if (!project?.id || !Number.isInteger(project.revision) || !version?.id
      || !data?.versions?.some(row => row.id === version.id) || version.stale
      || version.adoptable !== true || version.id === data.head_version_id) {
    throw new Error('这份方案当前不能采用，请重新读取或生成匹配当前项目的方案。');
  }
  return Object.freeze({ path: `/api/projects/${encodeURIComponent(project.id)}/explanation/adopt`,
    body: Object.freeze({ expected_revision: project.revision, version_id: version.id,
      expected_head_version_id: data.head_version_id ?? null }) });
}

function timestamp(value) {
  return typeof value === 'number' ? value * 1000 : Date.parse(value) || 0;
}

export function orderedExplanationVersions(data) {
  return [...(data?.versions ?? [])].sort((a, b) => timestamp(a.created_at) - timestamp(b.created_at));
}

export function explanationSelection(data, selectedId) {
  const versions = orderedExplanationVersions(data);
  return versions.find(row => row.id === selectedId)
    ?? versions.find(row => row.id === data?.head_version_id) ?? versions.at(-1) ?? null;
}

export function explanationSceneReviews(project, version) {
  const old = new Map((project?.scenes ?? []).map(scene => [scene.id, scene]));
  return (version?.result?.scenes ?? []).map(proposed => ({
    previous: old.get(proposed.scene_id) ?? null, proposed,
    strategy: STRATEGIES[proposed.visual_strategy] ?? '待核对',
  }));
}

/** DOM and transport are injected so selection, recovery and review are testable. */
export function createExplanationPanel({ document, getContext, request, refresh, onError, onSuccess }) {
  const $ = id => document.getElementById(id);
  const cache = new Map(), drafts = new Map(), selections = new Map(), busy = new Set();
  const revisions = new Map(), readErrors = new Map();
  const restoredDefaults = new Set();
  let sequence = 0, reviewKey = null, confirmationKey = null;
  const readSequences = new Map();
  const current = () => getContext();
  const same = context => current().project?.id === context.project?.id && current().epoch === context.epoch;
  const dataFor = id => cache.get(id);
  const draftFor = id => {
    if (!drafts.has(id)) drafts.set(id, explanationDraft());
    return drafts.get(id);
  };
  const text = (tag, value, className) => {
    const element = document.createElement(tag);
    if (value !== undefined) element.textContent = value;
    if (className) element.className = className;
    return element;
  };
  const addText = (parent, heading, content) => {
    parent.append(text('h5', heading), text('p', content || '未填写', 'preserve-whitespace'));
  };
  const addList = (parent, heading, rows, ordered = false, emptyMessage = '未提供') => {
    parent.append(text('h5', heading));
    const list = text(ordered ? 'ol' : 'ul');
    for (const row of rows ?? []) list.append(text('li', row));
    if (!list.children.length) list.append(text('li', emptyMessage));
    parent.append(list);
  };

  function review(project, version, key) {
    if (reviewKey === key) return;
    reviewKey = key;
    const target = $('explanation-review');
    target.replaceChildren();
    if (!version) return;
    target.append(text('h3', '方案概览'), text('p', version.result?.summary ?? '', 'preserve-whitespace'));
    const warnings = text('div', undefined, 'explanation-warnings');
    addList(warnings, '需要人工核对', version.result?.warnings ?? [], false, '请核对事实、数字和术语。');
    warnings.append(text('p', '来源摘录用于追溯原稿，仍需核对改写后的事实、条件、数字与术语。', 'field-note'));
    target.append(warnings);
    explanationSceneReviews(project, version).forEach(({ previous, proposed, strategy }, index) => {
      const details = text('details', undefined, 'explanation-scene');
      const summary = text('summary', `镜头 ${index + 1} · ${proposed.title} · 预计 ${proposed.duration_seconds} 秒`);
      details.append(summary);
      const columns = text('div', undefined, 'explanation-comparison');
      const before = text('section'), after = text('section');
      before.append(text('h4', '当前已保存'));
      addText(before, '标题', previous?.title);
      addText(before, '旁白', previous?.narration);
      addText(before, '画面说明', previous?.visual_description);
      addText(before, '读音修正', pronunciation(previous?.speech_settings?.pronunciation));
      after.append(text('h4', '候选方案'));
      addText(after, '标题', proposed.title);
      addText(after, '旁白', proposed.narration);
      addText(after, '画面重点', proposed.visual_description);
      addList(after, '关键点', proposed.key_points);
      addText(after, '图解方式', strategy);
      addList(after, '画面展开步骤', proposed.visual_steps, true);
      addText(after, '读音修正', pronunciation(proposed.pronunciation));
      addText(after, '讲述建议', proposed.delivery_note);
      addList(after, '来源摘录', proposed.source_quotes);
      columns.append(before, after);
      details.append(columns);
      target.append(details);
    });
  }

  function pronunciation(mapping) {
    return Object.entries(mapping ?? {}).map(([word, spoken]) => `${word} = ${spoken}`).join('\n');
  }

  function render() {
    const context = current(), project = context.project;
    const existing = dataFor(project?.id);
    $('explanation-panel').hidden = !project || (!context.configured && !existing?.operation && !existing?.versions?.length);
    if (!project) return;
    const data = dataFor(project.id), draft = draftFor(project.id);
    const fresh = Boolean(data && revisions.get(project.id) === project.revision);
    const operation = data?.operation, active = isActiveOperation(operation);
    const submitting = busy.has(project.id);
    const blocked = Boolean(context.unsaved || context.projectBusy);
    for (const field of ['audience', 'goal', 'tone', 'instruction']) {
      const input = $(`explanation-${field}`);
      if (input.value !== draft[field]) input.value = draft[field];
      input.disabled = !context.configured;
    }
    $('generate-explanation').disabled = !context.configured || !fresh || !project.scenes?.length || active || submitting || blocked;
    $('generate-explanation').textContent = active || submitting ? '方案处理中…' : '生成一份讲解方案';
    $('reload-explanation').disabled = submitting;
    $('explanation-read-error').hidden = !readErrors.has(project.id);
    $('explanation-read-error').textContent = readErrors.get(project.id) ?? '';
    $('explanation-form-note').textContent = !context.configured
      ? '暂未开放新方案。历史方案仍可审阅，进行中的任务可继续查询或申请取消。'
      : context.unsaved ? '有未保存的项目修改。草稿会保留，请先保存或重新载入，再生成或采用方案。'
      : context.projectBusy ? '项目中还有正在处理的任务，请等待结束后再生成或采用。'
        : !project.scenes?.length ? '先按原有方式拆分或添加镜头，再整理项目讲解方案。'
          : draft.dirty ? '讲解要求已修改，轮询和切换镜头会保留这些内容。'
            : '按当前已保存的镜头生成候选。只整理方案，不会生成画面、配音或视频。';
    const banner = $('explanation-status');
    banner.hidden = !operation;
    banner.classList.toggle('active', active);
    banner.classList.toggle('failed', ['failed', 'rejected', 'timed_out', 'needs_input'].includes(operation?.status));
    $('explanation-status-text').textContent = operation
      ? `讲解方案 · ${statusLabel(operation.status)}${operation.error ? ` · ${explanationErrorLabel(operation.error)}` : ''}` : '';
    $('cancel-explanation').hidden = !active;
    $('cancel-explanation').disabled = submitting || ['cancelling', 'cancel_requested'].includes(operation?.status);
    $('cancel-explanation').textContent = ['cancelling', 'cancel_requested'].includes(operation?.status) ? '已申请取消' : '申请取消';
    const selected = explanationSelection(data, selections.get(project.id));
    if (selected) selections.set(project.id, selected.id);
    const versions = orderedExplanationVersions(data);
    const options = versions.map((version, index) => {
      const option = text('option', `方案 ${index + 1}${version.id === data.head_version_id ? ' · 已采用' : ' · 候选'}${version.stale ? ' · 内容已变化' : ''}`);
      option.value = version.id;
      return option;
    });
    if (!options.length) options.push(text('option', '尚无讲解方案'));
    $('explanation-version').replaceChildren(...options);
    $('explanation-version').disabled = !versions.length || submitting;
    if (selected) $('explanation-version').value = selected.id;
    $('explanation-empty').hidden = Boolean(selected);
    const binding = JSON.stringify([project.id, project.revision, selected?.id, data?.head_version_id]);
    if (confirmationKey !== binding) {
      confirmationKey = binding;
      $('confirm-explanation-adoption').checked = false;
    }
    review(project, selected, JSON.stringify([project.id, project.revision, selected?.id]));
    const adopted = selected?.id === data?.head_version_id;
    const eligible = context.configured && fresh && selected?.adoptable === true && !selected.stale && !adopted;
    $('explanation-adoption').hidden = !selected;
    $('explanation-adoption-note').textContent = !context.configured
      ? '当前仅开放历史审阅与在途任务取消，暂不能生成或采用新方案。'
      : context.unsaved ? '采用已暂停：先处理未保存的镜头、配音、留白或字幕设置。你的草稿不会被覆盖。'
      : adopted && selected?.stale ? '这份方案曾被采用，之后项目内容已有变化；历史方案仅供审阅。'
        : adopted ? '这份方案已采用。原始总文稿保留，镜头修改继续使用原有编辑入口。'
        : selected?.stale ? '项目内容已变化，这份历史方案只供审阅。请重新生成匹配当前内容的方案。'
          : !eligible ? '这份方案当前不能采用，可以重新读取状态或生成新方案。'
            : '采用会更新全部镜头的讲解内容。变更镜头的画面需重做，配音可能失效；原始总文稿和项目音色保留。';
    $('confirm-explanation-adoption').disabled = !eligible || blocked || active || submitting;
    $('adopt-explanation').disabled = !eligible || blocked || active || submitting || !$('confirm-explanation-adoption').checked;
    $('explanation-new-candidate').hidden = !selected || versions.at(-1)?.id === selected.id;
  }

  async function load(projectId, epoch) {
    const context = current();
    if (context.project?.id !== projectId || context.epoch !== epoch) return;
    const read = ++sequence;
    readSequences.set(projectId, read);
    try {
      const result = await request(`/api/projects/${encodeURIComponent(projectId)}/explanation`);
      if (!same(context) || readSequences.get(projectId) !== read) return;
      cache.set(projectId, result);
      revisions.set(projectId, context.project.revision);
      readErrors.delete(projectId);
      if (!restoredDefaults.has(projectId)) {
        const draft = draftFor(projectId), brief = result.brief;
        // Restore once per project in this page lifetime. Input made while the
        // first read was pending is newer than the server's saved defaults.
        if (!draft.dirty) {
          for (const [field, maximum] of [['audience', 300], ['goal', 1200]]) {
            if (typeof brief?.[field] === 'string' && brief[field].length <= maximum) draft[field] = brief[field];
          }
          if (EXPLANATION_TONES.has(brief?.tone)) draft.tone = brief.tone;
          if (typeof result.instruction === 'string' && result.instruction.length <= 2000) draft.instruction = result.instruction;
        }
        restoredDefaults.add(projectId);
      }
      render();
    } catch (error) {
      if (!same(context) || readSequences.get(projectId) !== read) return;
      readErrors.set(projectId, '讲解方案暂时无法读取，已有项目与未保存输入会保留。可点击「重新读取方案」重试。');
      render();
      if (error?.status === 401) onError(error);
    }
  }

  async function mutate(context, action, accepted) {
    const id = context.project.id;
    if (busy.has(id)) return;
    busy.add(id); render();
    try {
      const result = await action();
      if (same(context)) accepted?.(result);
    } catch (error) {
      if (same(context)) onError(error);
    } finally {
      busy.delete(id);
      if (same(context)) {
        // Query even after an uncertain submit response; never auto-resubmit.
        try { await refresh(); } catch (error) { onError(error); }
        render();
      }
    }
  }

  async function generate() {
    const context = current(), project = context.project;
    if (!project || !context.configured || context.unsaved || context.projectBusy
        || revisions.get(project.id) !== project.revision || !project.scenes?.length
        || isActiveOperation(dataFor(project.id)?.operation) || !$('explanation-form').reportValidity()) return;
    let intent;
    try { intent = createExplanationRequest(project, draftFor(project.id)); }
    catch (error) { onError(error); return; }
    await mutate(context, () => request(intent.path, { method: 'POST', body: intent.body }), operation => {
      cache.set(project.id, { ...(dataFor(project.id) ?? { versions: [], head_version_id: null }), operation });
      const draft = draftFor(project.id);
      if (draft.audience.trim() === intent.body.brief.audience && draft.goal.trim() === intent.body.brief.goal
          && draft.tone === intent.body.brief.tone && draft.instruction.trim() === intent.body.instruction) draft.dirty = false;
      onSuccess('讲解方案任务已提交。原镜头保持当前内容，生成后先审阅，再明确采用。');
    });
  }

  async function adopt() {
    const context = current(), project = context.project, data = dataFor(project?.id);
    if (!project || !context.configured || context.projectBusy || revisions.get(project.id) !== project.revision
        || isActiveOperation(data?.operation)
        || !$('confirm-explanation-adoption').checked) return;
    let intent;
    try { intent = createExplanationAdoption(project, data, explanationSelection(data, selections.get(project.id)), context); }
    catch (error) { onError(error); render(); return; }
    await mutate(context, () => request(intent.path, { method: 'POST', body: intent.body }), () => {
      $('confirm-explanation-adoption').checked = false;
      onSuccess('整份讲解方案已采用。请检查需重做的画面及配音，再沿用原有制作流程。');
    });
  }

  async function cancel() {
    const context = current(), operation = dataFor(context.project?.id)?.operation;
    if (!context.project || !isActiveOperation(operation) || ['cancelling', 'cancel_requested'].includes(operation.status)) return;
    await mutate(context, () => request(`/api/explanation-operations/${encodeURIComponent(operation.id)}/cancel`,
      { method: 'POST', body: {} }), result => {
      cache.set(context.project.id, { ...dataFor(context.project.id), operation: result });
      onSuccess('已申请取消方案任务，将继续查询真实状态。');
    });
  }

  for (const field of ['audience', 'goal', 'tone', 'instruction']) {
    $(`explanation-${field}`).addEventListener('input', event => {
      const id = current().project?.id;
      if (!id) return;
      Object.assign(draftFor(id), { [field]: event.target.value, dirty: true });
      render();
    });
  }
  $('explanation-form').addEventListener('submit', event => { event.preventDefault(); generate(); });
  $('adopt-explanation').addEventListener('click', adopt);
  $('cancel-explanation').addEventListener('click', cancel);
  $('confirm-explanation-adoption').addEventListener('change', render);
  $('explanation-version').addEventListener('change', event => {
    if (current().project) selections.set(current().project.id, event.target.value);
    render();
  });
  $('explanation-new-candidate').addEventListener('click', () => {
    const id = current().project?.id;
    if (id) selections.set(id, orderedExplanationVersions(dataFor(id)).at(-1)?.id);
    render();
  });
  $('reload-explanation').addEventListener('click', async () => {
    try { await refresh(); } catch (error) { onError(error); }
  });
  return { render, load, generate, adopt, cancel,
    hasDrafts: () => [...drafts.values()].some(draft => draft.dirty),
    hasActiveOperation: (projectId = current().project?.id) => isActiveOperation(dataFor(projectId)?.operation),
    hasUnresolvedOperation: (projectId = current().project?.id) => isUnresolvedProductionOperation(dataFor(projectId)?.operation),
  };
}
