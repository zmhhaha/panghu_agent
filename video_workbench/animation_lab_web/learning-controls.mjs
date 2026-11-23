export const LEARNING_CREATION = ['html-svg', 'canvas', 'three', 'manim'];
export const LEARNING_MOTION = ['native-js', 'gsap', 'animejs'];

// Selections constrain the author; they do not establish observed tool use.
export function learningOptions(fields) {
  const selection = fields.capability_selection ?? {};
  if (!selection || typeof selection !== 'object' || Array.isArray(selection)
      || Object.keys(selection).some(key => !['creation', 'motion'].includes(key))) throw Error('请选择有效的创作能力');
  const value = {};
  for (const [key, choices] of [['creation', LEARNING_CREATION], ['motion', LEARNING_MOTION]]) {
    if (selection[key] === undefined) continue;
    const rows = selection[key];
    if (!Array.isArray(rows) || rows.some(row => !choices.includes(row)) || new Set(rows).size !== rows.length) throw Error('请选择有效的创作能力');
    value[key] = choices.filter(choice => rows.includes(choice));
  }
  if (value.creation?.length === 1 && value.creation[0] === 'manim' && value.motion?.length) throw Error('仅使用 Manim 时不选择浏览器动作编排');
  const flags = {};
  for (const key of ['allow_collaboration', 'allow_probes']) {
    if (fields[key] !== undefined && typeof fields[key] !== 'boolean') throw Error('请明确是否允许内部小样与分工创作');
    flags[key] = fields[key] !== false;
  }
  return {capability_selection: value, ...flags};
}

export function readLearningControls(doc, prefix) {
  const checked = (group, choices) => choices.filter(value => doc.getElementById(prefix + '-' + group + '-' + value)?.checked);
  return {capability_selection: {creation: checked('creation', LEARNING_CREATION),
    ...(prefix === 'video' ? {motion: checked('motion', LEARNING_MOTION)} : {})},
  allow_collaboration: doc.getElementById(prefix + '-allow-collaboration')?.checked !== false,
  allow_probes: doc.getElementById(prefix + '-allow-probes')?.checked !== false};
}

export function restoreLearningControls(doc, prefix, frozen = {}) {
  for (const [group, choices] of [['creation', LEARNING_CREATION], ['motion', LEARNING_MOTION]]) {
    for (const value of choices) {
      const input = doc.getElementById(prefix + '-' + group + '-' + value);
      if (input) input.checked = frozen.capability_selection?.[group]?.includes(value) ?? false;
    }
  }
  for (const [suffix, key] of [['allow-collaboration', 'allow_collaboration'], ['allow-probes', 'allow_probes']]) {
    const input = doc.getElementById(prefix + '-' + suffix); if (input) input.checked = frozen[key] !== false;
  }
}

export function updateLearningControls(doc, prefix, catalog, {locked = false, retry = false} = {}) {
  const get = suffix => doc.getElementById(prefix + '-' + suffix), capabilities = catalog?.capabilities;
  const pureManim = readLearningControls(doc, prefix).capability_selection.creation.join(',') === 'manim';
  for (const [group, choices] of [['creation', LEARNING_CREATION], ['motion', LEARNING_MOTION]]) {
    const admitted = Array.isArray(capabilities?.[group]) ? capabilities[group] : [];
    for (const value of choices) {
      const input = get(group + '-' + value); if (!input) continue;
      const available = admitted.includes(value);
      // Historical retry values remain visible but cannot be changed.
      if (!retry && (!available || group === 'motion' && pureManim)) input.checked = false;
      input.disabled = locked || retry || !available || group === 'motion' && pureManim;
      input.title = available ? '' : '当前执行环境尚未准入此能力';
    }
  }
  for (const suffix of ['allow-collaboration', 'allow-probes']) if (get(suffix)) get(suffix).disabled = locked || retry;
  if (get('learning-options')) get('learning-options').hidden = retry;
  if (get('motion-help')) get('motion-help').textContent = pureManim
    ? 'Manim 使用自身动画接口，浏览器动作编排已清空并停用。'
    : '可多选，Agent 从所选能力中按目标选择或组合；留空则按已准入能力自主选择。';
  if (get('manim-help')) {
    const limits = capabilities?.manim;
    const specification = [typeof limits?.version === 'string' ? 'Manim ' + limits.version : '',
      Number.isInteger(limits?.max_frames) ? '资源最多 ' + limits.max_frames + ' 帧' : '',
      Number.isFinite(limits?.fps) ? limits.fps + ' fps' : '', limits?.latex_available === false ? '不含 LaTeX' : ''].filter(Boolean).join(' · ');
    get('manim-help').textContent = limits?.admitted
      ? specification + '。通过真实预制资源参加学习；采用 Manim 资源时，本次不组合内部小样或分工子任务。'
      : '当前执行环境尚未准入 Manim，不能用 HTML/SVG 替代后声称使用了 Manim。';
  }
}

const PHASES = {plan: '拟定创作方案', probe_author: '制作技法小样', probe_render: '渲染技法小样', probe_review: '对照评审技法小样',
  component: '按依赖制作部件', resources: '制作预制资源', compose: '整合完整作品', complete: '内部探索已完成'};
const STATES = {planned: '待制作', authored: '源码已生成', rendered: '已渲染', reviewed: '已评审'};
function element(doc, tag, content, className = '') { const node = doc.createElement(tag); if (className) node.className = className; if (content) node.textContent = content; return node; }
export function renderLearningProgress(doc, experiment) {
  const progress = experiment?.learning_progress; if (!progress) return null;
  const panel = element(doc, 'section', '', 'panel learning-progress');
  panel.append(element(doc, 'h3', '本次学习过程'));
  const probes = Array.isArray(progress.probes) ? progress.probes : [], components = Array.isArray(progress.components) ? progress.components : [];
  panel.append(element(doc, 'p', PHASES[progress.phase] ?? '正在执行学习任务'));
  if (!probes.length && !components.length && progress.phase === 'complete') panel.append(element(doc, 'p', '本次直接创作完整作品，未调度内部小样或分工任务。', 'small muted'));
  else {
    panel.append(element(doc, 'p', '内部小样 ' + probes.filter(row => row.status === 'reviewed').length + ' / ' + probes.length
      + ' · 已完成部件 ' + components.length + '。所有任务共用本次预算，按依赖顺序执行。', 'small muted'));
    const list = element(doc, 'ul');
    for (const row of probes) list.append(element(doc, 'li', '小样：' + (row.goal || row.id) + ' · ' + (STATES[row.status] ?? row.status)));
    for (const row of components) list.append(element(doc, 'li', '部件：' + row.id + ' · ' + (STATES[row.status] ?? row.status)));
    if (probes.length || components.length) panel.append(list);
  }
  panel.append(element(doc, 'p', '小样仅用于检验局部技法，不计为效果库作品；完整作品仍需实际渲染、评审与换内容验证。', 'small muted'));
  return panel;
}

export function renderInternalAttempts(doc, experiment, renderAttempt) {
  const attempts = Array.isArray(experiment?.internal_attempts) ? experiment.internal_attempts : [];
  if (!attempts.length) return null;
  const details = element(doc, 'details', '', 'technical-details internal-attempts');
  details.append(element(doc, 'summary', '内部技法小样与证据（' + attempts.length + '）'),
    element(doc, 'p', '仅记录本次真实制作的内部小样及其评审，不作为独立作品或迁移通过的证据。', 'small muted'));
  for (const attempt of attempts) details.append(renderAttempt(attempt));
  return details;
}
