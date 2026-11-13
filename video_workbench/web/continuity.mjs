/** Project continuity is reviewed data; it never generates speech or approves timing. */
const MODES = new Set(['retain', 'copy']);
const PROCESS_COMPILER = 'motion-process-continuity-compiler-1';
const PROCESS_SOURCE_COMPILER = 'motion-process-compiler-2';
const STATES = { idle: '待命', waiting: '等待', running: '处理中', completed: '完成', failed: '失败' };
const projectPath = id => `/api/projects/${encodeURIComponent(id)}/continuity`;
const keyOf = link => JSON.stringify([link.from_scene_id, link.from_element_id, link.to_scene_id, link.to_element_id]);
const copyLinks = links => links.map(link => ({ from_scene_id: link.from_scene_id,
  from_element_id: link.from_element_id, to_scene_id: link.to_scene_id,
  to_element_id: link.to_element_id, mode: link.mode }));
const linksSignature = links => JSON.stringify(copyLinks(links).sort((a, b) => keyOf(a).localeCompare(keyOf(b))));

export function continuityErrorLabel(code) {
  const labels = {
    process_continuity_not_supported: '过程演示暂不支持跨镜连续衔接，请使用单镜或普通整片草稿。',
    continuity_semantic_layout_not_supported: '新流程、层级图解或完整来源面板暂不支持对象跨镜继承。请先制作单镜或普通整片草稿；已有画面和录音保留。',
    process_continuity_mixed_not_supported: '当前连续方案需要全部使用过程演示，或全部使用通用图解；两种方式混合时可制作普通整片草稿。',
    process_continuity_source_unsupported: '这份过程演示使用的版本尚不支持连续衔接，请生成并采用新的过程候选；原画面和录音仍保留。',
    process_continuity_source_invalid: '过程演示的状态或请求流转尚不能连续衔接，请检查该镜头后修改方案。',
    creative_continuity_not_supported: '创意动画暂不支持在镜头之间继承图形对象，请使用普通整片草稿或导出；已有动画和配音保留。',
    invalid_process_continuity_plan: '过程对象的连续关系不符合要求，请核对相邻镜头后重新保存候选。',
    process_continuity_requires_process: '这份连续方案需要全部镜头采用有效过程演示。',
    process_continuity_compiler_unsupported: '这份过程演示的制作版本暂不支持连续衔接，请生成并采用新的候选。',
    process_continuity_source_not_visible: '上一镜结束时关联对象不可见，请修改过程方案或取消关联。',
    process_continuity_slot_conflict: '延续对象的位置与下一镜起点不同，请修改过程方案或取消关联。',
    process_continuity_state_conflict: '延续后的节点状态无法接上下一镜动作，请修改过程方案或取消关联。',
    process_continuity_card_copy_not_supported: '过程演示目前只支持复制请求对象，处理节点请使用延续或取消关联。',
    process_continuity_copy_content_changed: '请求内容不同，不能作为保持原内容的复制。',
    process_continuity_copy_archive_unavailable: '没有可供保留原请求的安全位置，请改为延续或取消这处关联。',
    process_continuity_unsafe_transition: '过程衔接存在位置冲突，请修改方案或取消该处关联。',
    continuity_compiler_version_unsupported: '这份连续方案的制作版本暂不支持，请重新规划；历史记录仍保留。',
    continuity_compiler_schema_mismatch: '连续方案的制作方式与图解不匹配，请重新规划；历史记录仍保留。',
    continuity_source_stale: '连续制作对应的图解、录音或时序已变化，请重新读取并制作新的草稿。',
    continuity_segment_budget_exceeded: '加入跨镜衔接后的镜头超过当前制作限制，请缩短旁白或拆分镜头。',
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
  const issues = [], kinds = new Set();
  if ((project?.scenes?.length ?? 0) < 2) issues.push('连续图解至少需要两个镜头。');
  for (const [index, scene] of (project?.scenes ?? []).entries()) {
    const title = `镜头 ${index + 1} · ${scene.title || '未命名镜头'}`;
    const motion = scene.motion;
    const head = motion?.versions?.find(row => row.id === motion.head_version_id);
    if (motion?.mode !== 'motion' || !motion.head_version_id || motion.head_stale || !head || head.stale) {
      issues.push(`${title}：请先采用有效动态图解。`);
    }
    if (head) {
      if (head.diagram_kind === 'creative' || ['video.creative-scene.v1', 'video.creative-scene.v2', 'video.creative-scene.v3', 'video.creative-scene.v4'].includes(head.plan?.schema)) {
        kinds.add('creative');
        issues.push(`${title}：${continuityErrorLabel('creative_continuity_not_supported')}`);
      } else {
        const process = head.diagram_kind === 'process' || head.plan?.schema === 'video.process-plan.v1';
        kinds.add(process ? 'process' : 'diagram');
        if (process && head.compiler_version !== PROCESS_SOURCE_COMPILER) {
          issues.push(`${title}：${continuityErrorLabel('process_continuity_source_unsupported')}`);
        }
      }
    }
    if (audio) {
      const recording = scene.audio_versions?.find(row => row.id === scene.head_audio_version_id);
      if (!scene.head_audio_version_id || scene.audio_stale || !recording || recording.stale) {
        issues.push(`${title}：请先试听并采用匹配当前旁白和项目音色的配音。`);
      }
    }
  }
  if (kinds.size > 1 && kinds.has('process')) {
    issues.push(continuityErrorLabel('process_continuity_mixed_not_supported'));
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
  function processSummary(value, sceneId) {
    return sceneSummaries(value).find(row => (row.scene_id ?? row.id) === sceneId);
  }
  function slotLabel(value, sceneId, slot) {
    const cardSlot = slot?.replace(/^token_/, '');
    return processSummary(value, sceneId)?.elements?.find(row => row.kind === 'card' && row.slot === cardSlot)?.label
      ?? ({ source: '起始节点', queue: '中间节点', worker: '处理节点' }[cardSlot] ?? '未知位置');
  }
  function processLink(value, source, destination) {
    return value.version?.compiler === PROCESS_COMPILER || source.initial_state !== undefined
      || source.initial_slot !== undefined || destination.initial_state !== undefined || destination.initial_slot !== undefined;
  }
  function copyIssue(value, source, destination) {
    if (source.detail !== destination.detail) return '说明内容不同，不能作为保持原内容的复制。';
    if (!processLink(value, source, destination)) return '';
    if (source.kind !== 'token' || destination.kind !== 'token') return '过程演示只支持复制请求对象，处理节点可以延续。';
    if (source.label !== destination.label) return '请求名称不同，不能直接复制为同一内容。';
    if (source.final_visible !== true) return '上一镜结束时请求不可见，不能据此复制。';
    if (!source.final_slot || !destination.initial_slot) return '请求的停靠位置尚未核对，请重新读取方案。';
    return '';
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
    if (version.compiler === PROCESS_COMPILER && draft?.dirty) {
      target.append(node('p', '衔接方式有未保存修改。下面的状态和停靠摘要来自已保存候选；保存为新候选后会重新核对，当前不能制作草稿。', 'field-note'));
    }
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
        const isProcess = processLink(value, source, destination);
        const copyBlocked = copyIssue(value, source, destination);
        const retainBlocked = isProcess && source.kind === 'token' && source.final_slot && destination.initial_slot
          && source.final_slot !== destination.initial_slot;
        const label = node('label', `「${source.label}」 → 「${destination.label}」`);
        if (detailChanged) label.append(node('span', `说明更新：「${source.detail || '无'}」 → 「${destination.detail || '无'}」。延续外框，说明渐变更新；内容不同不能作为复制。`, 'field-note'));
        if (isProcess) {
          if (source.kind === 'card') {
            const before = STATES[source.final_state] ?? '待核对';
            const initial = STATES[destination.standalone_initial_state ?? destination.initial_state] ?? '待核对';
            const inherited = STATES[destination.initial_state] ?? '待核对';
            const final = STATES[destination.final_state] ?? '待核对';
            label.append(node('span', `前镜终态：${before}；下一镜独立起态：${initial}，衔接起态：${inherited}，衔接终态：${final}。延续将继承前镜状态，按下一镜动作继续变化。`, 'field-note'));
          } else if (source.kind === 'token') {
            label.append(node('span', `前镜结束位置：${slotLabel(value, previous.id, source.final_slot)}${source.final_visible ? '，请求可见' : '，请求已隐藏'}；下一镜起点：${slotLabel(value, next.id, destination.initial_slot)}，结束位置：${slotLabel(value, next.id, destination.final_slot)}。复制将保留上一镜原件；实际停靠和留存空间在制作时核验。`, 'field-note'));
            if (retainBlocked) label.append(node('span', '延续不可用：请求结束位置与下一镜起点不同，可以复制并保留原件，或取消关联。', 'field-note'));
          }
          if (copyBlocked) label.append(node('span', `复制不可用：${copyBlocked}`, 'field-note'));
        }
        const select = node('select');
        select.setAttribute('aria-label', `${previous.title || '上一镜头'}与${next.title || '下一镜头'}中对象的衔接方式`);
        for (const [mode, title] of [['retain', '延续同一对象'], ['copy', '复制并保留原件'], ['none', '不关联']]) {
          const option = node('option', title); option.value = mode;
          option.disabled = mode === 'copy' && Boolean(copyBlocked) || mode === 'retain' && Boolean(retainBlocked); select.append(option);
        }
        select.value = link?.mode ?? 'none';
        select.disabled = value.blocked || !value.fresh || version.stale || value.enabled === false;
        select.addEventListener('change', () => {
          if (!['retain', 'copy', 'none'].includes(select.value) || select.disabled
              || (select.value === 'copy' && copyBlocked) || (select.value === 'retain' && retainBlocked)) return;
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
