// Pure helpers shared by the media-only experiment UI and its checks.
export function experimentIdentity(experiment) {
  const value = experiment?.experiment_identity ?? experiment?.identity;
  return typeof value === 'string' && value ? value : null;
}

export function experimentLocalUrl(value, root) {
  if (typeof value !== 'string' || !value || /[\u0000-\u0020\\]/.test(value)) return null;
  try {
    const base = new URL(root);
    const url = new URL(value, base);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password
      || url.origin !== base.origin || !url.pathname.startsWith(base.pathname)) return null;
    return url.href;
  } catch { return null; }
}

export function experimentVersions(experiment, role = 'development') {
  if (!Array.isArray(experiment?.versions)) return [];
  return experiment.versions.filter(row => row && typeof row.id === 'string' && row.id
    && (row.input_role ?? 'development') === role);
}

export function experimentPlayable(version, root) {
  if (!version || !Number.isFinite(version.duration_seconds) || version.duration_seconds <= 0
    || ['failed', 'canceled', 'invalid', 'not_run', 'pending', 'running', 'generated'].includes(version.status)) return false;
  return Boolean(experimentLocalUrl(version.media_url, root));
}

export function experimentComparison(left, right) {
  if (!left || !right || left.id === right.id) return {allowed: false, reason: '选择两个不同的真实作品后才能比较。'};
  if ((left.input_role ?? 'development') !== (right.input_role ?? 'development')) {
    return {allowed: false, reason: '调优与复测输入不同，不能直接作同输入比较。'};
  }
  const leftInput = left.comparison_input_identity ?? left.input_identity;
  const rightInput = right.comparison_input_identity ?? right.input_identity;
  if (leftInput && rightInput && leftInput !== rightInput) return {allowed: false, reason: '两侧目标输入不同，不能归因为提示词改进。'};
  if (left.source !== right.source) return {allowed: true, reason: '来源不同：可以比较效果，不能当作提示词版本改进。'};
  if (left.source === 'agent' && left.prompt_revision !== right.prompt_revision) {
    return {allowed: true, reason: '提示词版本不同；还需核对模型与组件环境是否固定，单次偏好只说明这次作品的差异。'};
  }
  return {allowed: true, reason: '比较实际作品；结论按声明的改动范围记录。'};
}

export function experimentFrameTime(seconds, duration, fps = 24) {
  if (!Number.isFinite(duration) || duration <= 0 || !Number.isFinite(fps) || fps <= 0) return 0;
  const limit = Math.max(0, duration - 1 / fps);
  return Math.min(limit, Math.max(0, Number.isFinite(seconds) ? seconds : 0));
}

export function experimentIssuePayload(experiment, version, values) {
  const identity = experimentIdentity(experiment);
  const start = Number(values.start_seconds), end = Number(values.end_seconds);
  const comment = typeof values.comment === 'string' ? values.comment.trim() : '';
  if (!identity || !version || typeof version.identity !== 'string' || !version.identity || !Array.isArray(experiment?.versions)
    || !experiment.versions.some(row => row.id === version.id) || !Number.isFinite(version.duration_seconds)
    || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end < start
    || end > version.duration_seconds || !comment || comment.length > 4000) return null;
  return {experiment_identity: identity, kind: 'issue', version_ids: [version.id],
    version_identities: {[version.id]: version.identity},
    comment, start_seconds: start, end_seconds: end, preference: null};
}

export function experimentSourceLabel(source) {
  return source === 'agent' ? '真实 Agent 生成' : source === 'component' ? '手写组件实验' : '来源待核对';
}

export function experimentStatusLabel(status) {
  const labels = {definition: '实验定义 · 未运行', not_run: '尚未运行', unexecuted: '实验定义 · 未运行',
    has_results: '已有执行记录', generated: '已生成代码 · 尚未渲染', pending: '等待执行', running: '正在执行',
    rendered: '已渲染 · 待评价', ready: '已有作品', completed: '已有结果', failed: '执行失败',
    invalid: '产物未通过校验', canceled: '已取消'};
  return Object.hasOwn(labels, status) ? labels[status] : '状态待核对';
}

export function experimentTechnicalLabel(version) {
  if (version?.technical?.status === 'passed') return '技术检查通过 · 效果待评价';
  if (version?.technical?.status === 'failed') return '技术检查失败 · 保留视频供观察，效果待评价';
  if (!version?.technical && ['failed', 'generated', 'not_run', 'unexecuted', 'pending', 'running'].includes(version?.status)) {
    return '尚未执行技术检查 · 效果待评价';
  }
  return '技术检查待核验 · 效果待评价';
}

export function experimentTextDifference(before, after) {
  if (typeof before !== 'string' || typeof after !== 'string') return '实际文本未完整提供。';
  if (before === after) return '文本相同。';
  const left = before.split('\n'), right = after.split('\n');
  let prefix = 0, suffix = 0;
  while (prefix < Math.min(left.length, right.length) && left[prefix] === right[prefix]) prefix++;
  while (suffix < Math.min(left.length - prefix, right.length - prefix)
    && left[left.length - suffix - 1] === right[right.length - suffix - 1]) suffix++;
  return [`共同前缀 ${prefix} 行，公共后缀 ${suffix} 行；下列仅显示不同区间：`,
    '--- 左侧', ...left.slice(prefix, left.length - suffix).map(line => `− ${line}`),
    '+++ 右侧', ...right.slice(prefix, right.length - suffix).map(line => `+ ${line}`)].join('\n');
}
