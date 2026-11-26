/** One explicit whole-video production intent; media stays a draft for review. */
import { mutationErrorLabel, speechVoiceName } from './model.mjs';

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'stale']);
const STATUS = { queued: '等待制作', running: '制作中', completed: '草稿待审阅', failed: '制作未完成', cancelled: '已取消', stale: '项目内容已变化' };
const PHASE = { audio: '制作全片配音', resources: '按方案准备动画与声音资源', motion: '按实际录音制作动画', module: '编排完整视频画面', draft: '渲染整片草稿', completed: '草稿待审阅' };
Object.assign(PHASE,{scene_space_author:'规划并绘制完整场景',scene_space_preview:'制作取景预演',
  scene_space_clip_preview:'制作短片预演',scene_space_repair:'修正本次预演画面',
  scene_space_review:'核对预演画面',scene_space_planned:'准备场景漫游',scene_space_rendering:'渲染完整漫游'});
const animationReady = (run, item) => run.renderer_mode === 'scene-space'
  ? Boolean(item.world_source_sha256 && item.scene_space_plan_sha256===run.scene_space?.plan_sha256)
  : run.renderer_mode === 'hyperframes-film'
  ? Boolean(item.module_version_id) : Boolean(item.motion_version_id);
const uncertain = error => typeof error === 'string' && /uncertain|unconfirmed/.test(error);
export const productionRunUnresolved = run => Boolean(run?.id && (!TERMINAL.has(run.status)
  || run.blocked_reason || run.draining === true || uncertain(run.error) || run.items?.some(item => uncertain(item.error))));
const pathFor = id => `/api/projects/${encodeURIComponent(id)}/production-runs`;
const positive = value => typeof value === 'number' && Number.isFinite(value) && value > 0;
const seconds = value => Math.round(value * 1000) / 1000;

export function productionResourceProgress(run) {
  const nodes = Array.isArray(run?.resources?.nodes)
    ? run.resources.nodes.filter(node => node?.producer !== 'verified-recording') : [];
  if (!nodes.length) return '';
  const ready = nodes.filter(node => node.status === 'completed' && node.stop_proven === true).length;
  return `预制资源 ${ready}/${nodes.length}`;
}

/** Current-content projection is independent of the history dropdown. */
export function currentProductionRun(project, runs = []) {
  if (!project?.id || !project.joint_plan?.version_id || !project.scenes?.length) return null;
  const matching = runs.filter(run => run.project_id === project.id && run.current === true
    && run.joint_plan_version_id === project.joint_plan.version_id
    && Array.isArray(run.items) && run.items.length === project.scenes.length
    && new Set(run.items.map(item => item.scene_id)).size === project.scenes.length
    && project.scenes.every((scene, index) => run.items[index].scene_id === scene.id))
    .sort((a, b) => Number(a.created_at ?? 0) - Number(b.created_at ?? 0));
  const latest = matching.at(-1);
  // A newer failed attempt must not borrow older successful candidates.
  return latest && ['queued', 'running', 'completed'].includes(latest.status)
    && !latest.error && !latest.blocked_reason && latest.draining !== true ? latest : null;
}

export function productionRunDraftReady(run) {
  return Boolean(run?.status === 'completed' && run.current === true && run.draft_render_id
    && !run.error && !run.blocked_reason && run.draining !== true
    && run.items?.length && run.items.every(item => item.candidate_current === true
      && (run.audio_mode==='silent' ? item.audio_status==='not_requested' && item.audio_version_id==null
        : item.audio_version_id) && animationReady(run, item) && !item.error));
}

export function productionRunActualSeconds(run, renders = []) {
  if (!productionRunDraftReady(run)) return null;
  const render = renders.find(item => item.id === run.draft_render_id && item.status === 'completed'
    && item.selection === 'draft_only' && item.content_state === 'current'
    && positive(item.actual_duration_seconds));
  return render?.actual_duration_seconds ?? null;
}

export function productionRunErrorLabel(code) {
  const labels = {
    scene_space_preview_quality_not_met:'预演画面尚未达到本次目标，已保留预演和意见，尚未导出整片。',
    scene_space_review_frame_transport_incomplete:'预演审阅尚未完成，已保留原画面并停止后续导出。',
    scene_space_export_budget_unavailable:'制作额度不足或实际用量尚未确认，已保留已有结果。',
    scene_space_usage_unknown:'部分实际用量尚未确认，已保留已有结果，暂未完成整片。',
    scene_space_budget_exhausted:'本次制作额度已用尽，已保留预演与已有结果。',
    scene_space_render_budget_insufficient:'本次可用渲染时间不足，已保留规划和已有预演，没有继续导出整片。',
    scene_space_preview_coverage_budget_exceeded:'本次场景覆盖超过预演限额，已保留规划和绘制结果，尚未启动整片导出。',
    scene_space_model_budget_unavailable:'本次学习与评审额度不足或用量未确认，已保留预演，没有继续导出整片。',
    scene_space_budget_unavailable:'本次制作额度不足或用量未确认，已保留规划和已有预演。',
    scene_space_budget_reservation_exceeded:'剩余制作额度不足，已保留规划和已有预演。',
    scene_space_repair_plan_changed:'本次修正改变了原场景规划，未采用修改，已保留原预演。',
    scene_space_repair_unchanged:'本次修正未产生有效修改，已保留原预演和意见。',
    scene_space_preview_samples_missing:'短片预演的画面证据不完整，已停止后续导出。',
    production_run_film_plan_too_long: '当前整片制作支持最长 5 分钟，请先缩短制作方案。尚未开始配音。',
    production_run_film_too_long: '实际录音与留白超过当前 5 分钟制作上限，已保留录音，请缩短内容后再制作。',
    production_run_film_configuration_changed: '制作服务版本已变化，已暂停后续制作。已有结果保留，请重新核对。',
    film_creation_configuration_changed: '制作服务版本已变化，已暂停后续制作。已有结果保留，请重新核对。',
    production_run_film_unavailable: '整片制作依赖暂未就绪，请稍后再试。',
    production_runs_disabled: '整片草稿制作暂未启用，已有草稿仍可查看。',
    production_skill_new_plan_required: '请先重新统筹并采用整片方案，以固定本次制作的技能版本。已有作品保留。',
    production_run_in_progress: '项目已有整片制作任务，请等待完成或申请取消。',
    production_run_project_busy: '项目还有其他制作任务，请等待结束后再制作整片草稿。',
    production_run_inputs_changed: '已采用方案、文稿或配音设置已变化，已停止后续制作；请核对当前内容。',
    production_run_cancellation_pending: '已申请取消，正在等待制作任务确认停止；请继续读取状态。',
    production_run_stale: '项目内容或音色已变化，请核对并采用匹配当前内容的整片方案。',
    joint_plan_stale: '整片方案与当前内容不匹配，请重新核对方案。',
    missing_current_audio: '旁白录音暂未准备好，后续动画尚未开始。',
  };
  if (uncertain(code)) return '上游任务的真实结果尚未确认，已停止后续制作；请读取原任务状态，不要重复生成。';
  return labels[code] ?? '本次制作未完成，已保留生成记录和成功结果；请核对问题后再处理。';
}

export function createProductionRunRequest(project, { unsaved = false, busy = false, audioMode } = {}) {
  if (!project?.id || !Number.isInteger(project.revision)) throw new Error('请先读取项目。');
  if (unsaved) throw new Error('请先保存或重新载入未保存的修改，再制作整片草稿。');
  if (busy) throw new Error('项目已有制作任务，请等待结束或申请取消。');
  if (!project.joint_plan?.version_id || !project.scenes?.length) throw new Error('请先审阅并采用一份整片制作方案。');
  if (audioMode!==undefined && !['narration','silent'].includes(audioMode)) throw new Error('请选择有效的声音方式。');
  return { path: pathFor(project.id), body: {
    expected_revision: project.revision, joint_plan_version_id: project.joint_plan.version_id,
    ...(audioMode===undefined?{}:{audio_mode:audioMode}),
  } };
}

export function createProductionRunsPanel({ document, getContext, request, refresh,
  onError, onSuccess, onChange = () => {}, onMediaPlay = () => {},
  makeRequestKey = () => `production-run:${globalThis.crypto.randomUUID()}` }) {
  const $ = id => document.getElementById(id);
  const lists = new Map(), selected = new Map(), sequences = new Map(), errors = new Map();
  const busy = new Set(), unresolvedStarts = new Map(), cancelPending = new Set(), loaded = new Map();
  let sequence = 0, mediaKey = null;
  const context = () => getContext();
  const same = value => context().project?.id === value.project?.id && context().epoch === value.epoch;
  const rowsFor = id => lists.get(id) ?? [];
  const ordered = rows => [...rows].sort((a,b) => Number(a.created_at ?? 0) - Number(b.created_at ?? 0));
  const selectedRun = id => rowsFor(id).find(run => run.id === selected.get(id))
    ?? rowsFor(id).find(productionRunUnresolved) ?? ordered(rowsFor(id)).at(-1) ?? null;
  const hasUnresolvedOperation = (id = context().project?.id) => Boolean(id &&
    (busy.has(id) || unresolvedStarts.has(id) || rowsFor(id).some(productionRunUnresolved)));
  const text = (tag, content, className) => {
    const node = document.createElement(tag);
    if (content != null) node.textContent = String(content);
    if (className) node.className = className;
    return node;
  };
  function pause() { $('production-run-video').pause(); }
  function clearMedia() {
    pause(); mediaKey = null;
    $('production-run-video').removeAttribute('src');
    $('download-production-run').removeAttribute('href');
  }
  function notify() { render(); onChange(); }
  const currentRun = project => project?.id && loaded.get(project.id) === project.revision && !errors.has(project.id)
    && !unresolvedStarts.has(project?.id) ? currentProductionRun(project, rowsFor(project.id)) : null;

  function render() {
    const value = context(), project = value.project, id = project?.id;
    const rows = ordered(rowsFor(id)), run = selectedRun(id), waiting = unresolvedStarts.has(id);
    $('production-runs-panel').hidden = !project || (!value.enabled && !rows.length && !waiting);
    if (!project) { clearMedia(); return; }
    const running = rows.some(productionRunUnresolved), adopted = Boolean(project.joint_plan?.version_id && project.scenes?.length);
    const audioChoice=value.settings?.scene_space_production===true;
    const silent=audioChoice && $('production-run-audio-mode').value==='silent';
    const narrationUnavailable=audioChoice && !silent && value.settings?.speech_configured===false;
    $('production-run-audio-choice').hidden=!audioChoice;
    $('production-run-audio-mode').disabled=running || busy.has(id);
    $('start-production-run').disabled = !value.enabled || loaded.get(id) !== project.revision || !adopted
      || value.unsaved || value.projectBusy || hasUnresolvedOperation(id) || narrationUnavailable;
    $('start-production-run').textContent = waiting ? '正在确认原制作请求…' : silent ? '制作整片无旁白草稿' : '制作整片有声草稿';
    $('production-run-note').textContent = !value.enabled ? '暂未开放新的整片制作，已有记录仍可查看。'
      : waiting ? '提交结果尚未确认，正在查询原请求。不会自动重复提交。'
      : loaded.get(id) !== project.revision ? '正在读取已有制作记录，请稍候。'
      : !adopted ? '先在上方审阅并采用整片方案，再一键制作有声草稿。'
      : value.unsaved ? '请先保存或重新载入未保存的项目修改。草稿输入会保留。'
      : running ? '本次制作使用已确认方案和统一音色。可查看进度或申请取消。'
      : value.projectBusy ? '项目中还有其他任务，请等待结束后再制作整片草稿。'
      : '使用已采用的整片方案：先准备配音及所需资源，再编排画面并渲染整片草稿。无需逐镜确认。';
    if(silent && value.enabled && adopted && !running && !waiting && !value.unsaved && !value.projectBusy){
      $('production-run-note').textContent='先规划并绘制完整场景，再核对预演，复用场景完成镜头漫游。无需逐镜确认。';
    }
    if(narrationUnavailable && value.enabled && !running && !waiting){
      $('production-run-note').textContent='旁白服务尚未就绪，可选择「无旁白，安静看景」制作场景漫游。';
    }
    if (value.enabled && positive(value.settings?.production_run_max_seconds)) {
      $('production-run-note').textContent += ` 当前单次最多 ${value.settings.production_run_max_seconds} 秒。`;
    }
    $('production-run-error').hidden = !errors.get(id);
    $('production-run-error').textContent = errors.get(id) ?? '';
    $('production-run-history').hidden = rows.length < 2;
    const options = rows.map((item,index) => { const option = text('option', `制作 ${index+1} · ${STATUS[item.status] ?? '状态待确认'}${item.current === false ? ' · 历史内容' : ''}`); option.value = item.id; return option; });
    $('production-run-select').replaceChildren(...options);
    if (run) { selected.set(id, run.id); $('production-run-select').value = run.id; }
    $('production-run-result').hidden = !run;
    if (!run) { clearMedia(); $('production-run-player').hidden = true; return; }
    const total = run.items?.length ?? 0;
    const contentKnown = loaded.get(id) === project.revision && !errors.has(id);
    const steps = Number.isInteger(run.progress?.total) && run.progress.total > 0;
    const progress = steps ? `${run.progress.done}/${run.progress.total} 步`
      : total ? `${run.items.filter(item => animationReady(run, item)).length}/${total} 段动画` : '';
    const status = STATUS[run.status] ?? '状态待确认';
    const labels = [status];
    if (!TERMINAL.has(run.status) && PHASE[run.phase] && PHASE[run.phase] !== status) labels.push(PHASE[run.phase]);
    if (progress) labels.push(progress);
    const resources = productionResourceProgress(run);
    if (resources) labels.push(resources);
    if (run.current === false) labels.push('历史内容，项目已变化');
    else if (!contentKnown) labels.push('内容匹配状态待读取');
    $('production-run-status').textContent = labels.join(' · ');
    $('production-run-voice').textContent = run.audio_mode==='silent' ? '本次无旁白。'
      : `统一音色：${speechVoiceName(run.voice, value.settings ?? {})}。`;
    if (run.library_binding) $('production-run-voice').textContent += run.library_binding.release_id
      ? ` 本批次固定使用技能版本 ${run.library_binding.release_sequence}。`
      : ' 本批次使用空技能目录，可自由创作。';
    $('production-run-problem').hidden = !run.error && !run.blocked_reason;
    $('production-run-problem').textContent = run.blocked_reason
      ? '本次制作仍在等待上游停止或结果确认，已暂停后续任务；请继续读取状态，不要重复制作。'
      : run.error ? productionRunErrorLabel(run.error) : '';
    const previewPrefix=`/api/production-runs/${encodeURIComponent(run.id)}/scene-previews/`;
    const previews=(run.scene_space?.previews ?? []).filter(item=>item.status==='completed'
      && typeof item.url==='string' && item.url.startsWith(previewPrefix));
    const clips=(run.scene_space?.preview_clips ?? []).filter(item=>item.status==='completed'
      && typeof item.url==='string' && item.url.startsWith(previewPrefix));
    const history=(run.scene_space?.retained_attempts ?? []).flatMap(attempt=>(attempt.previews ?? [])
      .filter(item=>typeof item.url==='string' && item.url.startsWith(previewPrefix))
      .map(item=>({...item,attempt:attempt.attempt})));
    $('production-run-previews').hidden=!previews.length&&!clips.length&&!history.length;
    $('production-run-preview-images').replaceChildren(...previews.map((item,index)=>{
      const figure=text('figure'),link=text('a'),image=document.createElement('img');
      link.href=item.url;link.target='_blank';link.rel='noopener';
      image.src=item.url;image.alt=`预演画面 ${index+1}`;image.loading='lazy';link.append(image);
      figure.append(link,text('figcaption',`画面 ${index+1} · ${seconds(item.frame/24)} 秒`));return figure;
    }),...clips.map((item,index)=>{
      const figure=text('figure'),video=document.createElement('video');
      video.src=item.url;video.controls=true;video.muted=true;video.preload='metadata';
      figure.append(video,text('figcaption',`短片预演 ${index+1} · ${(item.end_frame-item.start_frame)/24} 秒`));
      return figure;
    }),...history.map(item=>{
      const link=text('a',`修正前预演 ${item.attempt+1} · 第 ${item.frame+1} 帧`);
      link.href=item.url;link.target='_blank';link.rel='noopener';return link;
    }));
    $('cancel-production-run').hidden = !['queued', 'running'].includes(run.status);
    $('cancel-production-run').disabled = cancelPending.has(run.id) || busy.has(id);
    $('cancel-production-run').textContent = cancelPending.has(run.id) ? '已申请取消，正在读取状态' : '申请取消本次制作';
    const items = (run.items ?? []).map((item,index) => {
      const node = text('li', undefined, 'production-run-item');
      const stopped = TERMINAL.has(run.status) && run.status !== 'completed';
      const seconds = run.audio_mode==='silent' ? '无旁白'
        : Number.isFinite(item.audio_seconds) && item.audio_seconds > 0 ? `实测录音 ${Math.round(item.audio_seconds * 100) / 100} 秒`
        : stopped ? '配音尚未完成' : item.audio_operation_id ? '正在制作配音' : '配音待制作';
      const operation = run.renderer_mode === 'hyperframes-film' ? item.module_operation_id : item.motion_operation_id;
      const motion = animationReady(run, item) ? run.renderer_mode==='scene-space' ? '共用场景已准备' : '动画候选已生成'
        : stopped ? '动画尚未完成' : operation ? '正在制作动画' : '动画待制作';
      node.append(text('strong', `段落 ${index+1} · ${item.title ?? ''}`), text('span', `${seconds} · ${motion}`));
      if (item.error) node.append(text('p', productionRunErrorLabel(item.error), 'batch-item-error'));
      return node;
    });
    $('production-run-items').replaceChildren(...items);
    const playable = run.status === 'completed' && typeof run.draft_render_id === 'string' && run.draft_render_id.length > 0;
    $('production-run-player').hidden = !playable;
    if (playable) {
      const key = JSON.stringify([id, run.id, run.draft_render_id]);
      if (mediaKey !== key) {
        pause(); mediaKey = key;
        const url = `/api/renders/${encodeURIComponent(run.draft_render_id)}/video`;
        $('production-run-video').src = url;
        $('download-production-run').href = url;
        $('download-production-run').setAttribute('download', `draft-${run.draft_render_id}.mp4`);
      }
      const actual = (value.renders ?? []).find(item => item.id === run.draft_render_id
        && item.status === 'completed' && positive(item.actual_duration_seconds))?.actual_duration_seconds;
      const duration = positive(actual) ? `视频实际 ${seconds(actual)} 秒。`
        : positive(run.duration_seconds) ? `${run.audio_mode==='silent'?'按画面排程':'按录音与留白排程'} ${seconds(run.duration_seconds)} 秒；视频实测时长待读取。` : '';
      $('production-run-player-heading').textContent = run.current === false ? '历史整片预览' : '整片预览';
      $('production-run-review-note').textContent = `${run.current === false ? '这份草稿对应的项目内容已变化。' : !contentKnown ? '此草稿的内容匹配状态尚未确认。' : ''}${duration}${run.audio_mode==='silent'?'请完整观看画面，静态预演评审不能代表完整视频已审阅。':'请完整观看画面与旁白；制作完成、播放或下载都不代表你已试听、采用或核准时序。'}`;
    } else if (mediaKey) {
      clearMedia();
    }
  }

  async function load(projectId, epoch) {
    const value = context();
    if (value.project?.id !== projectId || value.epoch !== epoch) return;
    const ticket = ++sequence; sequences.set(projectId, ticket);
    try {
      const result = await request(pathFor(projectId));
      if (!same(value) || sequences.get(projectId) !== ticket) return;
      const rows = (result.runs ?? []).filter(run => run?.project_id === projectId);
      lists.set(projectId, rows); loaded.set(projectId, value.project.revision);
      const pending = unresolvedStarts.get(projectId);
      const found = pending && rows.find(run => run.request_key === pending.key);
      if (found) { unresolvedStarts.delete(projectId); selected.set(projectId, found.id); }
      errors.delete(projectId); notify();
    } catch (error) {
      if (!same(value) || sequences.get(projectId) !== ticket) return;
      loaded.delete(projectId);
      errors.set(projectId, '整片制作记录暂时无法读取。已有草稿保留，请重新读取状态。'); notify();
      if (error?.status === 401) onError(error);
    }
  }

  async function start() {
    const value = context(), project = value.project, id = project?.id;
    if (!value.enabled || !project || loaded.get(id) !== project.revision || hasUnresolvedOperation(id)) return;
    if(value.settings?.scene_space_production===true && value.settings?.speech_configured===false
      && $('production-run-audio-mode').value!=='silent') { onError(new Error('旁白服务尚未就绪，请选择无旁白制作。'));return; }
    let intent;
    try { intent = createProductionRunRequest(project, { unsaved:value.unsaved, busy:value.projectBusy,
      audioMode:value.settings?.scene_space_production===true && $('production-run-audio-mode').value==='silent'?'silent':undefined }); }
    catch (error) { onError(error); return; }
    const key = makeRequestKey();
    sequences.set(id, ++sequence);
    unresolvedStarts.set(id, { key }); busy.add(id); errors.delete(id); notify();
    try {
      const run = await request(intent.path, { method: 'POST', body: intent.body, idempotencyKey:key });
      if (typeof run?.id !== 'string' || run.project_id !== id || run.request_key !== key) throw new Error('production_run_response_unconfirmed');
      sequences.set(id, ++sequence);
      lists.set(id, [...rowsFor(id).filter(row => row.id !== run.id), run]);
      unresolvedStarts.delete(id); selected.set(id, run.id);
      if (same(value)) onSuccess(intent.body.audio_mode==='silent'
        ? '无旁白草稿制作已开始。将自动规划场景、核对预演并完成漫游，完成后请看整片。'
        : '整片草稿制作已开始。将自动完成配音、动画与合成，完成后请看整片。');
    } catch (error) {
      if (Number.isInteger(error?.status) && error.status < 500) unresolvedStarts.delete(id);
      errors.set(id, mutationErrorLabel(error));
      if (same(value)) onError(error);
    } finally {
      busy.delete(id);
      if (same(value)) { try { await refresh(); } catch (error) { onError(error); } notify(); }
    }
  }

  async function cancel() {
    const value = context(), id = value.project?.id, run = selectedRun(id);
    if (!run || !['queued', 'running'].includes(run.status) || cancelPending.has(run.id)) return;
    cancelPending.add(run.id); notify();
    try {
      const result = await request(`/api/production-runs/${encodeURIComponent(run.id)}/cancel`, { method:'POST', body:{} });
      lists.set(id, rowsFor(id).map(row => row.id === result.id ? result : row));
      if (same(value)) onSuccess('已申请取消，将继续查询本次制作的真实状态。');
    } catch (error) {
      if (Number.isInteger(error?.status) && error.status < 500) cancelPending.delete(run.id);
      errors.set(id, mutationErrorLabel(error)); if (same(value)) onError(error);
    } finally {
      if (same(value)) { try { await refresh(); } catch (error) { onError(error); } notify(); }
    }
  }

  $('start-production-run').addEventListener('click', start);
  $('production-run-audio-mode').addEventListener('change',notify);
  $('cancel-production-run').addEventListener('click', cancel);
  $('reload-production-runs').addEventListener('click', async () => { try { await refresh(); } catch (error) { onError(error); } });
  $('production-run-select').addEventListener('change', event => {
    const id = context().project?.id; if (id) selected.set(id, event.target.value); notify();
  });
  $('production-run-video').addEventListener('play', onMediaPlay);
  return { render, load, start, cancel, pause, hasUnresolvedOperation, currentRun,
    managedRenderIds: projectId => new Set(rowsFor(projectId).filter(run => run.status === 'completed'
      && run.draft_render_id).map(run => run.draft_render_id)),
    hasActiveOperation: hasUnresolvedOperation, hasRefreshableOperation: hasUnresolvedOperation };
}
