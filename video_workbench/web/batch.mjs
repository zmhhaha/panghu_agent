/** Project batches submit bounded work and keep review separate from adoption. */
import { statusLabel } from './model.mjs';
import { motionMetadata, motionRenderErrorLabel, usesDynamicVideo } from './motion.mjs';

const STEPS = ['motion', 'audio', 'draft'];
const STEP_LABELS = { motion: '动态图解', audio: '配音', draft: '镜头草稿' };
const TERMINAL = new Set(['completed', 'partial_failed', 'cancelled']);
const RETRYABLE = new Set(['failed']);
const LABELS = { completed: '已完成', partial_failed: '部分未完成', stale: '项目已变化',
  skipped: '已跳过', reused: '复用已有版本', blocked: '暂停，等待处理', uncertain: '提交结果待确认' };
const batchStatus = status => LABELS[status] ?? statusLabel(status);
const unresolved = batch => Boolean(batch?.id && !TERMINAL.has(batch.status));
const successful = item => Boolean(item?.version_id && ['completed', 'reused', 'skipped'].includes(item.status));
const projectPath = id => `/api/projects/${encodeURIComponent(id)}`;
const batchPath = id => `/api/batches/${encodeURIComponent(id)}`;
const keyFor = value => `${value.project?.id}:${value.epoch}`;
class BatchInputError extends Error {}

export function createBatchRequest(project, { sceneIds, steps, unsaved = false, busy = false } = {}) {
  if (!project?.id || !Number.isInteger(project.revision)) throw new BatchInputError('请先读取项目。');
  if (unsaved) throw new BatchInputError('请先保存或重新载入未保存的修改。');
  if (busy) throw new BatchInputError('请等待当前制作任务结束。');
  if (!Array.isArray(sceneIds) || !sceneIds.length || sceneIds.length > 12
      || new Set(sceneIds).size !== sceneIds.length
      || sceneIds.some(id => !project.scenes?.some(scene => scene.id === id))) {
    throw new BatchInputError('请选择本项目的 1 至 12 个镜头。');
  }
  if (!Array.isArray(steps) || !steps.length || new Set(steps).size !== steps.length
      || steps.some(step => !STEPS.includes(step))) throw new BatchInputError('请至少选择一个制作步骤。');
  return { path: `${projectPath(project.id)}/batches/preview`, body: {
    expected_revision: project.revision,
    scene_ids: project.scenes.filter(scene => sceneIds.includes(scene.id)).map(scene => scene.id),
    steps: STEPS.filter(step => steps.includes(step)), reuse_current: true,
  } };
}

export function batchSceneResults(batch) {
  const rows = new Map();
  for (const item of batch?.items ?? []) {
    if (!item.scene_id) continue;
    if (!rows.has(item.scene_id)) rows.set(item.scene_id, { scene_id: item.scene_id,
      title: item.scene_title, items: [], motion_version_id: null, audio_version_id: null, render_id: null });
    const row = rows.get(item.scene_id); row.items.push(item);
    if (item.step === 'motion' && successful(item)) row.motion_version_id = item.version_id;
    if (item.step === 'audio' && successful(item)) row.audio_version_id = item.version_id;
    if (item.step === 'draft' && item.render_id && ['completed', 'reused'].includes(item.status)) {
      row.render_id = item.render_id;
      // A rendered draft is evidence for this exact pair, never for "latest".
      row.draft_motion_version_id = item.motion_version_id;
      row.draft_audio_version_id = item.audio_version_id;
      row.motion_version_id = item.motion_version_id ?? row.motion_version_id;
      row.audio_version_id = item.audio_version_id ?? row.audio_version_id;
    }
  }
  return [...rows.values()].map(row => ({ ...row,
    motion_version_id: row.draft_motion_version_id ?? row.motion_version_id,
    audio_version_id: row.draft_audio_version_id ?? row.audio_version_id,
  }));
}

export function createBatchAdoption(project, selections, { unsaved = false, busy = false } = {}) {
  if (!project?.id || !Number.isInteger(project.revision)) throw new BatchInputError('请先读取项目。');
  if (unsaved || busy) throw new BatchInputError('请先保存修改并等待制作任务结束，再采用。');
  if (!Array.isArray(selections) || !selections.length || selections.length > 12
      || new Set(selections.map(row => row.scene_id)).size !== selections.length) throw new BatchInputError('请明确选择已审阅的候选。');
  const rows = selections.map(selection => {
    const scene = project.scenes.find(row => row.id === selection.scene_id);
    if (!scene || (!selection.motion_version_id && !selection.audio_version_id)) throw new BatchInputError('所选候选不属于当前项目。');
    const row = { scene_id: scene.id };
    if (selection.motion_version_id) Object.assign(row, {
      motion_version_id: selection.motion_version_id,
      expected_motion_head_version_id: motionMetadata(scene).head_version_id ?? null,
    });
    if (selection.audio_version_id) Object.assign(row, {
      audio_version_id: selection.audio_version_id,
      expected_audio_head_version_id: scene.head_audio_version_id ?? null,
    });
    return row;
  });
  return { path: `${projectPath(project.id)}/batch-adopt`, body: { expected_revision: project.revision, selections: rows } };
}

export function createBatchPanel({ document, getContext, request, refresh, onError, onSuccess,
  onInputError = () => {}, onOpenScene = () => {}, onPlayRender = () => {}, onProjectDraft = () => {}, onMediaPlay = () => {} }) {
  const $ = id => document.getElementById(id);
  const lists = new Map(), selected = new Map(), forms = new Map(), plans = new Map();
  const candidates = new Map(), reviewed = new Map(), retries = new Map(), expanded = new Map();
  const busy = new Set(), errors = new Map(), inputErrors = new Map();
  let sequence = 0, latestRead = 0, formSignature = null, rowSignature = null, confirmation = null, mediaProject = null;
  const context = () => getContext();
  const same = value => keyFor(context()) === keyFor(value);
  const selectionKey = value => `${value.project?.id}:${selected.get(value.project?.id) ?? ''}`;
  const batches = id => lists.get(id) ?? [];
  const batchFor = value => batches(value.project?.id).find(batch => batch.id === selected.get(value.project?.id));
  function formFor(project) {
    if (!forms.has(project.id)) forms.set(project.id, {
      sceneIds: new Set(project.scenes.filter(scene => motionMetadata(scene).mode === 'motion').slice(0, 12).map(scene => scene.id)),
      steps: new Set(STEPS),
    });
    return forms.get(project.id);
  }
  const text = (tag, value, className) => {
    const element = document.createElement(tag);
    if (value !== undefined) element.textContent = value;
    if (className) element.className = className;
    return element;
  };
  const button = (label, action, disabled = false) => {
    const element = text('button', label, 'button secondary'); element.type = 'button'; element.disabled = disabled;
    element.addEventListener('click', action); return element;
  };
  function clearPlan(projectId) {
    plans.delete(projectId); inputErrors.delete(projectId); $('confirm-batch-start').checked = false;
  }
  function inputError(value, error) {
    const message = error instanceof BatchInputError ? error.message : '请核对镜头和制作步骤后再试。';
    inputErrors.set(value.project?.id, message); render(); onInputError(message);
  }
  function selectionMap(map, value) {
    const key = selectionKey(value);
    if (!map.has(key)) map.set(key, new Map());
    return map.get(key);
  }
  function resultSignature(project, row) {
    const scene = project.scenes.find(scene => scene.id === row.scene_id);
    return JSON.stringify([row.motion_version_id, row.audio_version_id,
      motionMetadata(scene).head_version_id, scene?.head_audio_version_id]);
  }
  function resultInfo(project, row) {
    const scene = project.scenes.find(scene => scene.id === row.scene_id);
    const version = candidates.get(`${project.id}:${project.revision}:${row.motion_version_id}`);
    const audio = scene?.audio_versions?.find(version => version.id === row.audio_version_id);
    const valid = Boolean(scene && (row.motion_version_id || row.audio_version_id)
      && (!row.motion_version_id || (version && !version.stale && version.scene_id === scene.id && version.project_id === project.id))
      && (!row.audio_version_id || (audio && !audio.stale)));
    const unchanged = (!row.motion_version_id || motionMetadata(scene).head_version_id === row.motion_version_id)
      && (!row.audio_version_id || scene?.head_audio_version_id === row.audio_version_id);
    return { scene, version, audio, valid, unchanged };
  }
  function renderForm(value, blocked) {
    const { project } = value, form = formFor(project);
    const signature = JSON.stringify([project.id, project.scenes.map(scene => [scene.id, scene.title, motionMetadata(scene).mode]),
      [...form.sceneIds], [...form.steps], blocked]);
    if (signature === formSignature) return;
    formSignature = signature;
    const container = $('batch-scenes'); container.replaceChildren();
    project.scenes.forEach((scene, index) => {
      const label = text('label', undefined, 'batch-scene-choice');
      const checkbox = text('input'); checkbox.type = 'checkbox'; checkbox.checked = form.sceneIds.has(scene.id);
      checkbox.disabled = blocked || (!checkbox.checked && form.sceneIds.size >= 12);
      checkbox.setAttribute('aria-label', `选择镜头 ${index + 1} ${scene.title}`);
      checkbox.addEventListener('change', () => {
        checkbox.checked ? form.sceneIds.add(scene.id) : form.sceneIds.delete(scene.id);
        clearPlan(project.id); render();
      });
      label.append(checkbox, text('span', `镜头 ${index + 1} · ${scene.title}`),
        text('small', motionMetadata(scene).mode === 'motion' ? '动态图解' : '复用静态素材'));
      container.append(label);
    });
    for (const step of STEPS) {
      $(`batch-step-${step}`).checked = form.steps.has(step);
      $(`batch-step-${step}`).disabled = blocked;
    }
  }
  function renderPlan(value, blocked) {
    const plan = plans.get(value.project.id);
    const current = plan?.body.expected_revision === value.project.revision;
    const configured = { motion: value.motionConfigured, audio: value.audioConfigured, draft: value.renderConfigured };
    const unavailable = (plan?.body.steps ?? []).some(step => configured[step] === false);
    $('batch-plan').hidden = !plan;
    $('batch-plan-summary').textContent = !plan ? '' : !current ? '项目已变化，请重新核对制作范围。'
      : `本次任务上限：动态图解 ${Number(plan.data.estimated_tasks?.motion ?? 0)} 次，配音 ${Number(plan.data.estimated_tasks?.audio ?? 0)} 次，镜头草稿 ${Number(plan.data.estimated_tasks?.draft ?? 0)} 次。`;
    const list = $('batch-plan-items'); list.replaceChildren();
    for (const item of plan?.data.items ?? []) list.append(text('li',
      `${item.scene_title ?? '镜头'} · ${STEP_LABELS[item.step] ?? '制作步骤'} · ${item.reuse ? '复用已有版本' : '待制作（尚未启动）'}`));
    $('confirm-batch-start').disabled = blocked || !current || unavailable;
    $('start-batch').disabled = $('confirm-batch-start').disabled || !$('confirm-batch-start').checked;
  }
  function renderRows(value, batch, blocked) {
    const rows = batchSceneResults(batch), checks = selectionMap(reviewed, value), retry = selectionMap(retries, value);
    const signature = JSON.stringify([value.project.id, value.project.revision, batch, [...checks], [...retry],
      rows.map(row => Boolean(candidates.get(`${value.project.id}:${value.project.revision}:${row.motion_version_id}`))), blocked]);
    if (signature === rowSignature) return;
    rowSignature = signature;
    $('batch-progress-rows').replaceChildren(); $('batch-review').replaceChildren();
    for (const row of rows) {
      const info = resultInfo(value.project, row);
      const reviewChanged = checks.has(row.scene_id) && checks.get(row.scene_id) !== resultSignature(value.project, row);
      const progress = text('div', undefined, 'batch-progress-row');
      progress.append(text('strong', row.title ?? info.scene?.title ?? '镜头'));
      for (const step of STEPS) {
        const item = row.items.find(item => item.step === step);
        const cell = text('div', undefined, 'batch-step-state');
        cell.append(text('small', STEP_LABELS[step]), text('span', item ? batchStatus(item.status) : '本次未选择'));
        if (item?.error) cell.append(text('p', batchErrorLabel(item.error), 'batch-item-error'));
        if (item && RETRYABLE.has(item.status)) {
          const label = text('label', undefined, 'checkbox-label'), checkbox = text('input'); checkbox.type = 'checkbox';
          checkbox.checked = retry.has(item.id); checkbox.disabled = blocked || unresolved(batch);
          checkbox.addEventListener('change', () => { checkbox.checked ? retry.set(item.id, true) : retry.delete(item.id); render(); });
          label.append(checkbox, text('span', '补做此项')); cell.append(label);
        }
        progress.append(cell);
      }
      progress.append(button('打开镜头', () => onOpenScene(row.scene_id, {
        motionVersionId: row.motion_version_id, audioVersionId: row.audio_version_id, section: 'timing',
      })));
      $('batch-progress-rows').append(progress);
      if (!row.motion_version_id && !row.audio_version_id && !row.render_id) continue;
      const card = text('details', undefined, 'batch-review-card');
      const cardKey = `${selectionKey(value)}:${row.scene_id}`;
      card.open = expanded.get(cardKey) ?? true;
      card.addEventListener('toggle', () => expanded.set(cardKey, card.open));
      card.append(text('summary', `${row.title ?? info.scene?.title ?? '镜头'} · ${info.unchanged ? '当前已采用' : '候选待审阅'}`));
      const body = text('div', undefined, 'batch-review-body');
      body.append(text('p', info.scene?.narration ?? '', 'preserve-whitespace'));
      if (row.motion_version_id) {
        body.append(text('h4', info.version?.plan?.title ?? '正在读取这份图解候选'));
        if (info.version) {
          body.append(text('p', info.version.plan.takeaway ?? ''));
          const elements = text('ul');
          for (const element of info.version.plan.elements ?? []) elements.append(text('li', `${element.label}${element.detail ? `：${element.detail}` : ''}`));
          body.append(elements);
          const beats = text('ol');
          for (const beat of info.version.beats?.segments ?? []) beats.append(text('li', beat.source_text ?? beat.spoken_text ?? ''));
          body.append(text('h4', '讲解分段'), beats);
        }
      }
      const actions = text('div', undefined, 'batch-review-actions');
      if (row.audio_version_id) actions.append(button(`试听配音${info.audio?.number ? ` A${info.audio.number}` : ''}`, async () => {
        onMediaPlay(); const player = $('batch-audio-preview'); player.pause();
        player.src = `/api/audio-versions/${encodeURIComponent(row.audio_version_id)}/audio`; player.hidden = false;
        $('batch-audio-label').textContent = `${row.title ?? '镜头'} · 当前审阅配音`;
        try { await player.play(); } catch { onInputError('请在音频播放器中点击播放。'); }
      }));
      if (row.render_id) actions.append(button('播放对应草稿', () => onPlayRender({ id: row.render_id, purpose: 'draft' })));
      actions.append(button(info.unchanged ? '逐镜试听与校时' : '打开此候选审阅', () => onOpenScene(row.scene_id, {
        motionVersionId: row.motion_version_id, audioVersionId: row.audio_version_id, section: info.unchanged ? 'timing' : 'review',
      })));
      body.append(actions);
      const label = text('label', undefined, 'checkbox-label'), checkbox = text('input'); checkbox.type = 'checkbox';
      checkbox.checked = checks.has(row.scene_id); checkbox.disabled = blocked || unresolved(batch) || !info.valid || info.unchanged;
      checkbox.addEventListener('change', () => {
        checkbox.checked ? checks.set(row.scene_id, resultSignature(value.project, row)) : checks.delete(row.scene_id);
        $('confirm-batch-adoption').checked = false; render();
      });
      label.append(checkbox, text('span', '已审阅，选择采用此镜头的确切候选'));
      body.append(label, text('p', info.unchanged ? '这组候选已采用。时序仍需在单镜中试听核对。'
        : reviewChanged ? '候选或采用版本已变化，请取消勾选并重新审阅。'
        : info.valid ? '采用会切换所列图解或配音；不会自动确认人工时序。'
          : '候选正在读取、已过期或不可用，当前不能采用。', 'field-note'));
      card.append(body); $('batch-review').append(card);
    }
  }
  function render() {
    const value = context(), { project } = value;
    $('batch-panel').hidden = !project || (!usesDynamicVideo(project) && !batches(project.id).length);
    if (!project) { $('batch-audio-preview').pause(); return; }
    if (mediaProject !== project.id) {
      mediaProject = project.id; $('batch-audio-preview').pause(); $('batch-audio-preview').removeAttribute('src');
      $('batch-audio-preview').hidden = true; $('batch-audio-label').textContent = '';
    }
    const batch = batchFor(value), inFlight = batches(project.id).some(unresolved);
    const blocked = !value.enabled || value.unsaved || value.projectBusy || busy.has(project.id);
    renderForm(value, blocked || inFlight);
    const form = formFor(project);
    const selectedSceneIds = [...form.sceneIds].filter(id => project.scenes.some(scene => scene.id === id));
    const selectionIssue = !selectedSceneIds.length ? '请至少选择一个镜头。'
      : !form.steps.size ? '请至少选择一个制作步骤。' : '';
    $('batch-form-note').textContent = inputErrors.get(project.id) || (value.unsaved ? '请先保存或重新载入未保存的镜头、音色和时序修改。'
      : inFlight ? '当前批次尚未结束，可以审阅进度、试听和申请取消。'
        : selectionIssue || '每批最多 12 个镜头。复用当前可用版本；核对范围只计算任务上限，不提交生成。');
    const configured = { motion: value.motionConfigured, audio: value.audioConfigured, draft: value.renderConfigured };
    const unavailable = [...formFor(project).steps].filter(step => configured[step] === false);
    $('batch-provider-note').hidden = !unavailable.length;
    $('batch-provider-note').textContent = unavailable.length
      ? `${unavailable.map(step => STEP_LABELS[step]).join('、')}服务暂未连接。可先取消这些步骤，或等待服务恢复后再启动。` : '';
    $('preview-batch').disabled = blocked || inFlight || Boolean(selectionIssue);
    renderPlan(value, blocked || inFlight);
    const optionsKey = JSON.stringify([project.id, batches(project.id).map(row => [row.id, row.status])]);
    if ($('batch-select').dataset.optionsKey !== optionsKey) {
      const options = batches(project.id).map((row, index) => { const option = text('option', `批次 ${batches(project.id).length - index} · ${batchStatus(row.status)}`); option.value = row.id; return option; });
      $('batch-select').replaceChildren(...(options.length ? options : [text('option', '尚无制作批次')]));
      $('batch-select').dataset.optionsKey = optionsKey;
    }
    if (batch) $('batch-select').value = batch.id;
    $('batch-select').disabled = !batches(project.id).length || busy.has(project.id);
    $('batch-status').textContent = batch ? `${batchStatus(batch.status)} · ${batch.items?.filter(item => ['completed', 'reused'].includes(item.status)).length ?? 0}/${batch.items?.length ?? 0} 项完成或复用${batch.error ? ` · ${batchErrorLabel(batch.error)}` : ''}` : '尚未启动批量制作。';
    $('batch-read-error').hidden = !errors.has(project.id); $('batch-read-error').textContent = errors.get(project.id) ?? '';
    $('cancel-batch').hidden = !batch || !unresolved(batch);
    $('cancel-batch').disabled = busy.has(project.id) || batch?.status === 'cancelling';
    renderRows(value, batch, blocked);
    const checked = selectionMap(reviewed, value), rows = batchSceneResults(batch);
    const adoptable = rows.filter(row => checked.has(row.scene_id));
    const valid = adoptable.length > 0 && adoptable.every(row => {
      const info = resultInfo(project, row);
      return info.valid && !info.unchanged && checked.get(row.scene_id) === resultSignature(project, row);
    });
    const signature = JSON.stringify([project.id, project.revision, batch?.id, [...checked]]);
    if (confirmation !== signature) { confirmation = signature; $('confirm-batch-adoption').checked = false; }
    $('batch-adoption-note').textContent = `已选择 ${checked.size} 个镜头。批次完成、采用候选和人工时序核对分别进行。`;
    $('confirm-batch-adoption').disabled = blocked || unresolved(batch) || !valid;
    $('adopt-batch').disabled = $('confirm-batch-adoption').disabled || !$('confirm-batch-adoption').checked;
    const retry = selectionMap(retries, value);
    const retryItems = (batch?.items ?? []).filter(item => retry.has(item.id) && RETRYABLE.has(item.status));
    $('retry-batch').disabled = blocked || unresolved(batch) || !retryItems.length;
    $('batch-retry-note').textContent = `只补做勾选步骤：动态图解 ${retryItems.filter(item => item.step === 'motion').length} 次，配音 ${retryItems.filter(item => item.step === 'audio').length} 次，镜头草稿 ${retryItems.filter(item => item.step === 'draft').length} 次。后续步骤需要时可另行核对范围并制作。`;
    const adopted = project.scenes.length > 0 && project.scenes.every(scene => scene.head_audio_version_id && !scene.audio_stale
      && (motionMetadata(scene).mode === 'motion' ? motionMetadata(scene).head_version_id && !motionMetadata(scene).head_stale : scene.head_version_id));
    $('preview-batch-project').disabled = blocked || inFlight || !adopted || value.renderConfigured === false;
  }
  async function load(projectId, epoch) {
    const value = context(); if (value.project?.id !== projectId || value.epoch !== epoch) return;
    const read = ++sequence; latestRead = read;
    try {
      const response = await request(`${projectPath(projectId)}/batches`);
      if (!same(value) || read !== latestRead) return;
      lists.set(projectId, response.batches ?? []);
      if (!selected.has(projectId) && response.batches?.length) selected.set(projectId, response.batches[0].id);
      const id = selected.get(projectId);
      if (id) {
        const detail = await request(batchPath(id));
        if (!same(value) || read !== latestRead) return;
        lists.set(projectId, batches(projectId).map(batch => batch.id === id ? detail : batch));
        const ids = [...new Set(batchSceneResults(detail).map(row => row.motion_version_id).filter(Boolean))];
        const results = await Promise.allSettled(ids.map(async id => {
          const version = await request(`/api/motion-versions/${encodeURIComponent(id)}`);
          if (same(value) && read === latestRead) candidates.set(`${projectId}:${value.project.revision}:${id}`, version);
        }));
        if (!same(value) || read !== latestRead) return;
        for (const result of results) if (result.status === 'rejected') errors.set(projectId, '部分图解候选暂时无法读取，请重新读取后再审阅采用。');
        if (results.every(row => row.status === 'fulfilled')) errors.delete(projectId);
      } else errors.delete(projectId);
      render();
    } catch (error) {
      if (!same(value) || read !== latestRead) return;
      errors.set(projectId, '批次暂时无法读取，当前选择和已有结果仍保留。'); render();
      if (error?.status === 401) onError(error);
    }
  }
  async function mutate(value, action, accepted) {
    const id = value.project?.id; if (!id || busy.has(id)) return;
    busy.add(id); render();
    try { const result = await action(); if (same(value)) accepted?.(result); }
    catch (error) { if (same(value)) onError(error); }
    finally {
      busy.delete(id);
      if (same(value)) { try { await refresh(); } catch (error) { onError(error); } render(); }
    }
  }
  async function preview() {
    const value = context(); if (!value.project || !value.enabled || batches(value.project.id).some(unresolved)) return;
    let intent;
    try { const form = formFor(value.project); intent = createBatchRequest(value.project, {
      sceneIds: [...form.sceneIds], steps: [...form.steps], unsaved: value.unsaved, busy: value.projectBusy }); }
    catch (error) { inputError(value, error); return; }
    await mutate(value, () => request(intent.path, { method: 'POST', body: intent.body }), data => {
      plans.set(value.project.id, { body: intent.body, data }); $('confirm-batch-start').checked = false;
      inputErrors.delete(value.project.id);
      onSuccess('已核对本次制作范围与任务上限，尚未启动生成。');
    });
  }
  async function start() {
    const value = context(), plan = plans.get(value.project?.id);
    const configured = { motion: value.motionConfigured, audio: value.audioConfigured, draft: value.renderConfigured };
    if (!value.enabled || value.unsaved || value.projectBusy || !plan?.data.plan_sha256
        || plan.body.expected_revision !== value.project.revision || !$('confirm-batch-start').checked
        || plan.body.steps.some(step => configured[step] === false)
        || batches(value.project.id).some(unresolved)) return;
    await mutate(value, () => request(`${projectPath(value.project.id)}/batches`, {
      method: 'POST', body: { ...plan.body, plan_sha256: plan.data.plan_sha256 },
    }), batch => {
      selected.set(value.project.id, batch.id); lists.set(value.project.id, [batch, ...batches(value.project.id).filter(row => row.id !== batch.id)]);
      clearPlan(value.project.id); onSuccess('批量制作已启动，结果保存为候选，不会自动采用或确认时序。');
    });
  }
  async function cancel() {
    const value = context(), batch = batchFor(value);
    if (!batch || !unresolved(batch) || batch.status === 'cancelling') return;
    await mutate(value, () => request(`${batchPath(batch.id)}/cancel`, { method: 'POST', body: {} }), updated => {
      lists.set(value.project.id, batches(value.project.id).map(row => row.id === batch.id ? updated : row));
      onSuccess('已停止后续任务并申请取消已开始的任务，等待服务确认。');
    });
  }
  async function retry() {
    const value = context(), batch = batchFor(value);
    if (!batch || unresolved(batch) || !value.enabled || value.unsaved || value.projectBusy) return;
    const checks = selectionMap(retries, value);
    const item_ids = batch.items.filter(item => checks.has(item.id) && RETRYABLE.has(item.status)).map(item => item.id);
    if (!item_ids.length) return;
    await mutate(value, () => request(`${batchPath(batch.id)}/retry`, {
      method: 'POST', body: { expected_revision: value.project.revision, item_ids },
    }), updated => {
      selected.set(value.project.id, updated.id); lists.set(value.project.id, [updated, ...batches(value.project.id)]);
      onSuccess('已为勾选的失败项建立补做批次，成功结果继续保留。');
    });
  }
  async function adopt() {
    const value = context(), batch = batchFor(value);
    if (!value.enabled || !batch || unresolved(batch) || !$('confirm-batch-adoption').checked) return;
    const checks = selectionMap(reviewed, value), rows = batchSceneResults(batch).filter(row => checks.has(row.scene_id));
    if (!rows.length || rows.some(row => !resultInfo(value.project, row).valid
      || checks.get(row.scene_id) !== resultSignature(value.project, row))) return;
    let intent;
    try { intent = createBatchAdoption(value.project, rows, { unsaved: value.unsaved, busy: value.projectBusy }); }
    catch (error) { inputError(value, error); return; }
    await mutate(value, () => request(intent.path, { method: 'POST', body: intent.body }), () => {
      checks.clear(); $('confirm-batch-adoption').checked = false;
      onSuccess('已采用所选确切候选。请逐镜试听并核对时序，再正式导出整片。');
    });
  }
  for (const step of STEPS) $(`batch-step-${step}`).addEventListener('change', event => {
    const value = context(); if (!value.project) return;
    const form = formFor(value.project);
    event.target.checked ? form.steps.add(step) : form.steps.delete(step); clearPlan(value.project.id); render();
  });
  $('preview-batch').addEventListener('click', preview); $('start-batch').addEventListener('click', start);
  $('cancel-batch').addEventListener('click', cancel); $('retry-batch').addEventListener('click', retry);
  $('adopt-batch').addEventListener('click', adopt);
  $('confirm-batch-start').addEventListener('change', render); $('confirm-batch-adoption').addEventListener('change', render);
  $('batch-select').addEventListener('change', async event => {
    const value = context(); if (!value.project) return;
    selected.set(value.project.id, event.target.value); $('batch-audio-preview').pause(); render(); await load(value.project.id, value.epoch);
  });
  $('reload-batch').addEventListener('click', async () => { const value = context(); if (value.project) await load(value.project.id, value.epoch); });
  $('preview-batch-project').addEventListener('click', () => { if (!$('preview-batch-project').disabled) onProjectDraft(); });
  return { render, load, preview, start, cancel, retry, adopt,
    hasActiveOperation: (projectId = context().project?.id) => batches(projectId).some(unresolved),
    hasUnresolvedOperation: (projectId = context().project?.id) => batches(projectId).some(unresolved),
    hasDrafts: () => [...reviewed.values()].some(rows => rows.size > 0) || plans.size > 0,
    hasProjectDrafts: projectId => Boolean(plans.has(projectId)
      || [...reviewed.entries()].some(([key, rows]) => key.startsWith(`${projectId}:`) && rows.size > 0)),
    pause: () => $('batch-audio-preview').pause(),
  };
}

export function batchErrorLabel(code) {
  const labels = {
    revision_conflict: '项目已有更新，请重新读取并核对制作范围。',
    batch_plan_conflict: '制作范围已变化，请重新核对后启动。',
    batch_plan_changed: '制作范围已变化，请重新核对后启动。',
    batch_in_progress: '项目已有批次正在进行，请等待结束。',
    batch_project_busy: '项目仍有制作任务未结束，请等待处理或申请取消。',
    batch_inputs_changed: '文稿或设置已变化，本批次已暂停，请核对已有结果。',
    batch_retry_uncertain: '原任务结果尚未确认，请重新读取，避免重复提交。',
    batch_child_uncertain: '原任务结果尚未确认，请重新读取，避免重复提交。',
    batch_dependency_failed: '前置步骤未完成，请先处理提示的图解或配音。',
    batch_missing_motion_candidate: '请先制作图解候选，或在本次范围中选择动态图解步骤。',
    batch_missing_audio_candidate: '请先制作配音候选，或在本次范围中选择配音步骤。',
    submission_uncertain: '提交结果待确认，请重新读取原任务，不要重复提交。',
    cancelled: '已取消，已有成功候选继续保留。',
  };
  return Object.hasOwn(labels, code) ? labels[code] : motionRenderErrorLabel(code)
    ?? '本步骤未完成，请检查已有结果后手动补做。';
}
