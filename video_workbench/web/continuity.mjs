/** Project continuity is reviewed data; it never generates speech or approves timing. */
const MODES = new Set(['retain', 'copy']);
const projectPath = id => `/api/projects/${encodeURIComponent(id)}/continuity`;
const keyOf = link => JSON.stringify([link.from_scene_id, link.from_element_id, link.to_scene_id, link.to_element_id]);
const copyLinks = links => links.map(link => ({ from_scene_id: link.from_scene_id,
  from_element_id: link.from_element_id, to_scene_id: link.to_scene_id,
  to_element_id: link.to_element_id, mode: link.mode }));
const linksSignature = links => JSON.stringify(copyLinks(links).sort((a, b) => keyOf(a).localeCompare(keyOf(b))));

export function continuityErrorLabel(code) {
  const labels = {
    continuity_plan_stale: '镜头内容或已采用图解已变化，请重新规划连续方案。',
    continuity_head_conflict: '已采用的连续方案已变化，请重新读取并核对。',
    continuity_requires_scenes: '连续图解至少需要两个镜头。',
    continuity_requires_motion: '请先为全部镜头采用有效动态图解。',
    continuity_missing_motion: '请先为全部镜头采用有效动态图解。',
    continuity_motion_stale: '已采用图解与当前镜头内容不匹配，请先重新检查并采用有效图解。',
    continuity_inputs_unavailable: '连续方案所需图解尚未就绪，请按镜头提示补齐后重新读取。',
    continuity_no_links: '未找到可以衔接的对象，请先检查相邻镜头的图解内容。',
    continuity_not_adopted: '正式导出需要先明确采用这份连续方案。',
    continuity_storage_limit: '连续方案记录已达到当前存储限制。',
    continuity_candidate_limit: '本项目的连续方案候选已达到数量限制。',
    continuity_inputs_unavailable: '请先为至少两个镜头采用有效动态图解。',
    continuity_motion_stale: '已采用图解对应旧内容，请先更新图解。',
    continuity_copy_archive_unavailable: '复制后没有安全的原件位置，请减少同屏对象或改为延续。',
    continuity_unsafe_transition: '对象移动路径发生重叠，请减少同屏对象或取消该处关联。',
    continuity_source_not_visible: '这个对象在前一镜结束时已隐藏，请调整图解或取消关联。',
    continuity_ambiguous_link: '对象关联不明确，请检查是否重复选择了同一来源或目标。',
    continuity_candidate_limit: '本项目的连续方案候选已达到数量上限。',
    continuity_plan_invalid: '连续方案记录未通过校验，请重新规划。',
    continuity_copy_space_unavailable: '复制后没有安全的原件保留位置，请减少画面对象，或将这处衔接改为延续。',
    continuity_not_configured: '连续图解服务尚未连接，已有方案与录音仍保留。',
    continuity_snapshot_corrupt: '本次连续制作输入未通过校验，请重新读取项目后再制作草稿。',
    invalid_continuity_plan: '对象衔接方案无效，请重新规划并审阅。',
    revision_conflict: '项目已更新，请重新读取后核对。',
  };
  return Object.hasOwn(labels, code) ? labels[code] : '连续方案暂未完成，请重新读取状态后检查。';
}

export function continuityIssues(project, { audio = false } = {}) {
  const issues = [];
  if ((project?.scenes?.length ?? 0) < 2) issues.push('连续图解至少需要两个镜头。');
  for (const [index, scene] of (project?.scenes ?? []).entries()) {
    const title = `镜头 ${index + 1} · ${scene.title || '未命名镜头'}`;
    const motion = scene.motion;
    const head = motion?.versions?.find(row => row.id === motion.head_version_id);
    if (motion?.mode !== 'motion' || !motion.head_version_id || motion.head_stale || !head || head.stale) {
      issues.push(`${title}：请先采用有效动态图解。`);
    }
    if (audio) {
      const recording = scene.audio_versions?.find(row => row.id === scene.head_audio_version_id);
      if (!scene.head_audio_version_id || scene.audio_stale || !recording || recording.stale) {
        issues.push(`${title}：请先试听并采用匹配当前旁白和项目音色的配音。`);
      }
    }
  }
  return issues;
}

function requireProject(project) {
  if (!project?.id || !Number.isInteger(project.revision)) throw new Error('请先读取项目。');
}

export function createContinuityProposal(project, links) {
  requireProject(project);
  const body = { expected_revision: project.revision };
  if (links !== undefined) {
    if (!Array.isArray(links) || links.some(row => !MODES.has(row.mode)
        || ['from_scene_id', 'from_element_id', 'to_scene_id', 'to_element_id'].some(key => typeof row[key] !== 'string' || !row[key]))) {
      throw new Error('请检查对象的衔接方式。');
    }
    body.links = copyLinks(links);
  }
  return { path: `${projectPath(project.id)}/proposals`, body };
}

export function createContinuityAdoption(project, data, version) {
  requireProject(project);
  if (!version?.id || version.stale || version.adoptable === false || data?.head_version_id === version.id) throw new Error('请先审阅有效的新候选。');
  return { path: `${projectPath(project.id)}/adopt`, body: { expected_revision: project.revision,
    version_id: version.id, expected_head_version_id: data?.head_version_id ?? null } };
}

export function createContinuityDraftRequest(project, version, { endHoldSeconds = 0 } = {}) {
  requireProject(project);
  if (!version?.id || version.stale || continuityIssues(project, { audio: true }).length) {
    throw new Error('请先补齐有效图解和已采用录音，再制作连续草稿。');
  }
  return { path: `/api/projects/${encodeURIComponent(project.id)}/motion-renders`, body: {
    expected_revision: project.revision, purpose: 'draft', subtitles: false,
    continuity_version_id: version.id, ...(endHoldSeconds ? { end_hold_seconds: endHoldSeconds } : {}),
  } };
}

export function createContinuityPanel({ document, getContext, request, refresh, onError,
  onSuccess, onInputError = () => {}, onDraft = () => {}, onDelivery = () => {} }) {
  const $ = id => document.getElementById(id);
  const cache = new Map(), revisions = new Map(), selections = new Map(), drafts = new Map();
  const busy = new Set(), readErrors = new Map(), reads = new Map();
  let sequence = 0, reviewKey = null, confirmationKey = null;
  const context = () => getContext();
  const same = value => context().project?.id === value.project?.id && context().epoch === value.epoch;
  const node = (tag, content, className) => {
    const element = document.createElement(tag);
    if (content !== undefined) element.textContent = content;
    if (className) element.className = className;
    return element;
  };
  function selected(value = context()) {
    const data = cache.get(value.project?.id);
    const version = data?.versions?.find(row => row.id === selections.get(value.project?.id))
      ?? data?.versions?.find(row => row.id === data.head_version_id) ?? data?.versions?.at(-1) ?? null;
    if (version && value.project?.id) selections.set(value.project.id, version.id);
    return version;
  }
  function draftFor(projectId, version) {
    if (!version) return null;
    const key = JSON.stringify([projectId, version.id]);
    if (!drafts.has(key)) drafts.set(key, { projectId, links: copyLinks(version.plan?.links ?? []), dirty: false });
    return drafts.get(key);
  }
  function current() {
    const value = context(), project = value.project;
    const data = cache.get(project?.id), version = selected(value), draft = draftFor(project?.id, version);
    return { ...value, data, version, draft,
      fresh: Boolean(project && data && !readErrors.has(project.id) && revisions.get(project.id) === project.revision
        && (data.project_revision === undefined || data.project_revision === project.revision)),
      blocked: Boolean(value.unsaved || value.projectBusy || busy.has(project?.id)) };
  }
  function sceneSummaries(value) {
    return value.version?.scenes ?? value.version?.scene_summaries ?? value.data?.scene_summaries ?? value.data?.scenes ?? [];
  }
  function serverIssues(value) {
    return (value.data?.issues ?? []).map(issue => `${issue.scene_title ? `${issue.scene_title}：` : ''}${continuityErrorLabel(issue.reason ?? issue.code)}`);
  }
  function elementFor(value, sceneId, elementId) {
    const scene = sceneSummaries(value).find(row => (row.scene_id ?? row.id) === sceneId);
    const element = scene?.elements?.find(row => row.id === elementId);
    return element ?? { label: elementId, detail: '' };
  }
  function renderReview(value) {
    const { project, version, draft } = value;
    const signature = JSON.stringify([project.id, project.revision, version?.id, version?.stale,
      value.fresh, value.enabled, value.blocked, draft?.links]);
    if (reviewKey === signature) return;
    reviewKey = signature;
    const target = $('continuity-review'); target.replaceChildren();
    if (!version) return;
    if (typeof version.summary === 'string') target.append(node('p', version.summary, 'field-note'));
    const summaries = sceneSummaries(value);
    const scenes = (summaries.length ? summaries : project.scenes ?? [])
      .map(row => ({ id: row.scene_id ?? row.id, title: row.title }));
    for (let index = 1; index < scenes.length; index += 1) {
      const previous = scenes[index - 1], next = scenes[index];
      const card = node('section', undefined, 'batch-review-body audio-review');
      card.append(node('h4', `镜头 ${index}「${previous.title || '未命名镜头'}」 → 镜头 ${index + 1}「${next.title || '未命名镜头'}」`));
      const links = (version.plan?.links ?? []).filter(row => row.from_scene_id === previous.id && row.to_scene_id === next.id);
      if (!links.length) card.append(node('p', '这处边界没有建议关联的对象，两段分别展开。', 'field-note'));
      for (const original of links) {
        const link = draft.links.find(row => keyOf(row) === keyOf(original));
        const source = elementFor(value, previous.id, original.from_element_id);
        const destination = elementFor(value, next.id, original.to_element_id);
        const detailChanged = source.detail !== destination.detail;
        const label = node('label', `「${source.label}」 → 「${destination.label}」`);
        if (detailChanged) label.append(node('span', `说明更新：「${source.detail || '无'}」 → 「${destination.detail || '无'}」。延续外框，说明渐变更新；内容不同不能作为复制。`, 'field-note'));
        const select = node('select');
        select.setAttribute('aria-label', `${previous.title || '上一镜头'}与${next.title || '下一镜头'}中对象的衔接方式`);
        for (const [mode, title] of [['retain', '延续同一对象'], ['copy', '复制并保留原件'], ['none', '不关联']]) {
          const option = node('option', title); option.value = mode;
          option.disabled = mode === 'copy' && detailChanged; select.append(option);
        }
        select.value = link?.mode ?? 'none';
        select.disabled = value.blocked || !value.fresh || version.stale || value.enabled === false;
        select.addEventListener('change', () => {
          if (!['retain', 'copy', 'none'].includes(select.value) || select.disabled
              || (select.value === 'copy' && detailChanged)) return;
          draft.links = draft.links.filter(row => keyOf(row) !== keyOf(original));
          if (select.value !== 'none') draft.links.push({ ...original, mode: select.value });
          draft.dirty = linksSignature(draft.links) !== linksSignature(version.plan?.links ?? []);
          render();
        });
        label.append(select); card.append(label);
      }
      target.append(card);
    }
  }
  function render() {
    const value = current(), { project, data, version, draft } = value;
    $('continuity-panel').hidden = !project || value.enabled === false;
    if (!project) return;
    const issues = [...continuityIssues(project), ...serverIssues(value)];
    const audioIssues = [...new Set([...continuityIssues(project, { audio: true }), ...serverIssues(value)])];
    const unavailable = value.blocked || !value.fresh || value.enabled === false;
    $('continuity-read-error').textContent = readErrors.get(project.id) ?? '';
    $('continuity-read-error').hidden = !readErrors.has(project.id);
    $('continuity-note').textContent = value.unsaved ? '请先处理未保存的修改。连续方案草稿会保留。'
      : value.projectBusy || busy.has(project.id) ? '请等待当前制作或保存操作结束。'
        : '根据相邻图解中名称和类型一致、没有歧义的对象提出建议。请确认是否为同一对象；说明变化会单列，复制要求内容完全一致。';
    $('propose-continuity').disabled = unavailable || issues.length > 0 || Boolean(draft?.dirty);
    $('propose-continuity').textContent = busy.has(project.id) ? '正在保存方案…' : '规划一份连续方案';
    $('continuity-issues').replaceChildren(...audioIssues.map(issue => node('li', issue)));
    $('continuity-issues').hidden = !audioIssues.length;
    const select = $('continuity-version'); select.replaceChildren();
    for (const [index, row] of (data?.versions ?? []).entries()) {
      const option = node('option', `连续方案 ${index + 1}${row.id === data.head_version_id ? ' · 已采用' : ' · 候选'}${row.stale ? ' · 图解已变化' : ''}${drafts.get(JSON.stringify([project.id, row.id]))?.dirty ? ' · 有未保存修改' : ''}`);
      option.value = row.id; select.append(option);
    }
    select.value = version?.id ?? ''; select.disabled = busy.has(project.id) || !version;
    const binding = JSON.stringify([project.id, project.revision, data?.head_version_id, version?.id, draft?.links]);
    if (binding !== confirmationKey) { confirmationKey = binding; $('confirm-continuity-adoption').checked = false; }
    const ready = !unavailable && version && !version.stale && !draft?.dirty && !issues.length;
    $('save-continuity-changes').disabled = unavailable || !version || version.stale || !draft?.dirty;
    $('reset-continuity-changes').disabled = busy.has(project.id) || !draft?.dirty;
    $('confirm-continuity-adoption').disabled = !ready || version.adoptable === false || version.id === data?.head_version_id;
    $('adopt-continuity').disabled = $('confirm-continuity-adoption').disabled || !$('confirm-continuity-adoption').checked;
    $('preview-continuity').disabled = !ready || audioIssues.length > 0 || value.renderConfigured === false;
    $('deliver-continuity').disabled = !ready || version.id !== data?.head_version_id
      || audioIssues.length > 0 || value.renderConfigured === false || value.deliveryReady === false;
    $('continuity-adoption-note').textContent = draft?.dirty ? '衔接方式有未保存修改。先保存为新候选，再审阅、采用或制作草稿。'
      : version?.stale ? '已采用图解或镜头内容已变化；这份方案仅供历史审阅，请重新规划。'
        : version?.id === data?.head_version_id && version ? '这份连续方案已采用。可使用现有录音制作草稿；正式导出仍需人工核对时序。'
          : version ? '采用只确定对象衔接，不改写旁白或生成录音。可先制作候选草稿，确认效果后再采用。'
            : '先规划一份连续方案，再逐边界检查对象如何延续。';
    $('continuity-render-note').textContent = value.renderConfigured === false ? '动态渲染服务暂未连接，方案与已有录音仍会保留。'
      : '连续草稿复用当前已采用录音，保留项目统一音色。正式连续导出还需先采用方案、逐镜人工核对时序；普通逐镜视频入口仍独立。进度、播放和下载在「项目预览与成片」中查看。';
    renderReview(value);
  }
  async function load(projectId, epoch) {
    const value = context();
    if (value.project?.id !== projectId || value.epoch !== epoch) return;
    if (value.enabled === false) { render(); return; }
    const revision = value.project.revision;
    const sequenceId = ++sequence; reads.set(projectId, sequenceId);
    try {
      const result = await request(projectPath(projectId));
      if (!same(value) || reads.get(projectId) !== sequenceId || context().project.revision !== revision) return;
      cache.set(projectId, result); revisions.set(projectId, revision); readErrors.delete(projectId); render();
    } catch (error) {
      if (!same(value) || reads.get(projectId) !== sequenceId) return;
      readErrors.set(projectId, '连续方案暂时无法读取，当前审阅和修改仍保留。请重新读取，避免重复提交。');
      render(); if (error?.status === 401) onError(error);
    }
  }
  async function mutate(value, intent, accepted) {
    const id = value.project.id;
    if (busy.has(id)) return;
    busy.add(id); render();
    try {
      const result = await request(intent.path, { method: 'POST', body: intent.body });
      if (same(value)) accepted?.(result);
    } catch (error) {
      if (same(value)) {
        onInputError(error?.code ? continuityErrorLabel(error.code) : '连接中断，尚未确认保存结果。请重新读取状态，不会自动重试。');
        if (error?.status === 401) onError(error);
      }
    } finally {
      busy.delete(id);
      if (same(value)) {
        try { await refresh(); } catch (error) { onError(error); }
        render();
      }
    }
  }
  async function propose(saveChanges = false) {
    const value = current();
    if (!value.project || value.blocked || !value.fresh || value.enabled === false
        || continuityIssues(value.project).length || serverIssues(value).length
        || (saveChanges ? !value.draft?.dirty || value.version?.stale : value.draft?.dirty)) return;
    const intent = createContinuityProposal(value.project, saveChanges ? value.draft.links : undefined);
    await mutate(value, intent, version => {
      if (saveChanges && value.draft) drafts.delete(JSON.stringify([value.project.id, value.version.id]));
      selections.set(value.project.id, version.id);
      const data = cache.get(value.project.id) ?? { versions: [], head_version_id: null };
      cache.set(value.project.id, { ...data, versions: [...(data.versions ?? []).filter(row => row.id !== version.id), version] });
      onSuccess('连续方案已保存为候选，请逐边界审阅，再决定采用或制作草稿。');
    });
  }
  async function adopt() {
    const value = current();
    if (!value.project || value.blocked || !value.fresh || value.enabled === false || value.draft?.dirty
        || !value.version || value.version.stale || value.version.adoptable === false || !$('confirm-continuity-adoption').checked
        || continuityIssues(value.project).length || serverIssues(value).length
        || value.version.id === value.data?.head_version_id) return;
    await mutate(value, createContinuityAdoption(value.project, value.data, value.version), () => {
      $('confirm-continuity-adoption').checked = false; onSuccess('已采用连续方案，现有旁白和录音保留。');
    });
  }
  async function preview() {
    const value = current();
    if (!value.project || value.blocked || !value.fresh || value.enabled === false || value.draft?.dirty
        || !value.version || value.version.stale || value.renderConfigured === false
        || continuityIssues(value.project, { audio: true }).length || serverIssues(value).length) return;
    await onDraft(value.version);
  }
  async function deliver() {
    const value = current();
    if (!value.project || value.blocked || !value.fresh || value.enabled === false || value.draft?.dirty
        || !value.version || value.version.stale || value.version.id !== value.data?.head_version_id
        || value.renderConfigured === false || value.deliveryReady === false
        || continuityIssues(value.project, { audio: true }).length || serverIssues(value).length) return;
    await onDelivery(value.version);
  }
  $('propose-continuity').addEventListener('click', () => propose());
  $('save-continuity-changes').addEventListener('click', () => propose(true));
  $('adopt-continuity').addEventListener('click', adopt);
  $('preview-continuity').addEventListener('click', preview);
  $('deliver-continuity').addEventListener('click', deliver);
  $('confirm-continuity-adoption').addEventListener('change', render);
  $('continuity-version').addEventListener('change', event => {
    const value = current(); if (!value.project || busy.has(value.project.id)) return;
    selections.set(value.project.id, event.target.value); render();
  });
  $('reset-continuity-changes').addEventListener('click', () => {
    const value = current(); if (!value.version || busy.has(value.project?.id)) return;
    drafts.delete(JSON.stringify([value.project.id, value.version.id])); render();
  });
  $('reload-continuity').addEventListener('click', async () => {
    try { await refresh(); } catch (error) { onError(error); }
  });
  return { render, load, propose, adopt, preview, deliver,
    hasDrafts: () => [...drafts.values()].some(row => row.dirty),
    hasProjectDrafts: id => [...drafts.values()].some(row => row.projectId === id && row.dirty),
    hasUnresolvedOperation: id => busy.has(id),
    adoptedVersionId: id => cache.get(id)?.head_version_id ?? null,
  };
}
