const textField = value => typeof value === 'string' ? value : '';
const ENGINE_LABELS = {svg: 'SVG', 'html-svg': 'HTML / SVG', html: 'HTML 界面', canvas: 'Canvas 2D', three: 'Three.js / WebGL'};

export function animationEngineLabel(engine) {
  return (typeof engine === 'string' && Object.hasOwn(ENGINE_LABELS, engine) ? ENGINE_LABELS[engine] : textField(engine)) || '未分类';
}

// Every filter is an intersection; an empty match must never select an unrelated
// fixture. Evaluation deep links are handled separately as explicit selections.
export function filterAnimationCases(cases, filters = {}, reviewed = new Set()) {
  if (!Array.isArray(cases)) return [];
  if (filters.review && !['all', 'unreviewed', 'reviewed'].includes(filters.review)) return [];
  const query = textField(filters.query).trim().toLocaleLowerCase();
  const hasReview = row => reviewed instanceof Set && reviewed.has(`${row.id}:${row.case_identity ?? row.identity ?? ''}`);
  return cases.filter(row => row && typeof row === 'object' && !row.evaluation
    && ['module', 'domain', 'engine'].every(key => !filters[key] || filters[key] === 'all' || row[key] === filters[key])
    && [row.title, row.description, row.domain_label, row.narrative, ...(Array.isArray(row.tags) ? row.tags : [])]
      .map(textField).join(' ').toLocaleLowerCase().includes(query)
    && (!filters.review || filters.review === 'all' || hasReview(row) === (filters.review === 'reviewed')));
}

export function animationFacetOptions(cases, key) {
  if (!['domain', 'engine'].includes(key) || !Array.isArray(cases)) return [];
  const groups = new Map();
  for (const row of cases) {
    if (!row || row.evaluation || typeof row[key] !== 'string' || !row[key]) continue;
    const label = key === 'domain' ? textField(row.domain_label) || row.domain : animationEngineLabel(row.engine);
    const previous = groups.get(row[key]);
    groups.set(row[key], {id: row[key], label: previous?.label ?? label, count: (previous?.count ?? 0) + 1});
  }
  return [...groups.values()].sort((a, b) => a.id.localeCompare(b.id));
}

export function animationCoverage(cases, modules = []) {
  const fixtures = filterAnimationCases(cases);
  const moduleRows = Array.isArray(modules) ? modules : [];
  const ids = [...new Set(fixtures.map(row => row.module).filter(value => typeof value === 'string' && value))];
  return {
    total: fixtures.length,
    matrixCases: fixtures.filter(row => row.is_matrix_case === true).length,
    domains: animationFacetOptions(fixtures, 'domain'),
    engines: animationFacetOptions(fixtures, 'engine'),
    modules: ids.map(id => {
      const selected = fixtures.filter(row => row.module === id);
      return {id, label: moduleRows.find(row => row.id === id)?.label ?? id, cases: selected.length,
        domains: animationFacetOptions(selected, 'domain'), engines: animationFacetOptions(selected, 'engine')};
    }),
  };
}

export function animationRequirementRows(row) {
  const requirement = row?.requirement;
  if (!requirement || typeof requirement !== 'object' || Array.isArray(requirement)) return [];
  const specs = [['audience', '面向谁'], ['goal', '表达目标'], ['questions', '需要讲清楚'],
    ['required_entities', '必须展示'], ['expected_outcome', '预期结果'], ['boundaries', '范围说明']];
  const result = [];
  for (const [key, label] of specs) {
    const values = Array.isArray(requirement[key]) ? requirement[key].filter(value => typeof value === 'string')
      : typeof requirement[key] === 'string' && requirement[key] ? [requirement[key]] : [];
    if (values.length) result.push({label, values});
  }
  if (typeof row.narrative === 'string' && row.narrative) result.push({label: '示例文稿', values: [row.narrative]});
  return result;
}

export function animationRequirementsUrl(applicationRoot, filters = {}, seed = 20261107) {
  try {
    const base = new URL(applicationRoot);
    if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password
        || !Number.isSafeInteger(seed) || seed < 0 || seed > 2**32-1) return null;
    const url = new URL('./api/requirements', base);
    url.searchParams.set('seed', String(seed));
    for (const key of ['module', 'domain', 'engine']) {
      const value = textField(filters[key]) || 'all';
      url.searchParams.set(key, value);
    }
    return url.origin === base.origin ? url.href : null;
  } catch { return null; }
}

// An annotation belongs to one exact frozen visual experiment. Missing or old
// identities remain useful history, but cannot approve or locate current frames.
export function isCurrentAnnotation(annotation, currentIdentity) {
  return typeof currentIdentity === 'string' && currentIdentity.length > 0
    && typeof annotation?.case_identity === 'string'
    && annotation.case_identity === currentIdentity;
}

export function issueLocation(annotation, currentIdentity, variants) {
  if (!isCurrentAnnotation(annotation, currentIdentity) || annotation.kind !== 'issue') return null;
  const variant = variants.find(row => row.id === annotation.variant_id);
  const start = annotation.start_seconds, end = annotation.end_seconds;
  if (!variant || !Number.isFinite(variant.duration_seconds) || variant.duration_seconds <= 0
      || !Number.isFinite(start) || !Number.isFinite(end)
      || start < 0 || end < start || end > variant.duration_seconds) return null;
  const region = annotation.region;
  if (region !== null && region !== undefined) {
    if (typeof region !== 'object' || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(region[key]))
        || region.x < 0 || region.y < 0 || region.width <= 0 || region.height <= 0
        || region.x + region.width > 1 + 1e-9 || region.y + region.height > 1 + 1e-9) return null;
  }
  return {variant_id: variant.id, time: start,
    region: region ? {x: region.x, y: region.y, width: region.width, height: region.height} : null};
}

// Saved automatic reviews compare a different frozen pair from the ordinary
// fixture even when their case and variant names overlap. Both run and report
// identities must match before any machine finding can locate a preview.
export function isCurrentEvaluation(evaluation, currentCase) {
  const expected = currentCase?.evaluation;
  return Boolean(expected && evaluation
    && ['id', 'run_identity', 'report_identity'].every(key => typeof expected[key] === 'string'
      && expected[key].length > 0 && evaluation[key] === expected[key]));
}

export function evaluationFindingLocation(evaluation, currentCase, findingId, frameId) {
  if (!isCurrentEvaluation(evaluation, currentCase)) return null;
  const finding = evaluation.findings?.find(row => row.id === findingId);
  if (!finding || !['technical', 'model'].includes(finding.source)
      || (finding.source === 'model' && evaluation.model?.status !== 'reviewed')) return null;
  const frame = finding.frames?.find(row => row.id === frameId);
  if (!frame || frame.variant_id !== finding.variant_id) return null;
  const variant = currentCase.variants?.find(row => row.id === frame.variant_id);
  if (!variant || !Number.isFinite(variant.duration_seconds) || variant.duration_seconds <= 0
      || !Number.isFinite(frame.time_seconds) || frame.time_seconds < 0
      || frame.time_seconds > variant.duration_seconds || typeof frame.clip_id !== 'string'
      || !frame.clip_id.length || typeof frame.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(frame.sha256)) return null;
  const region = finding.region;
  if (region !== undefined && region !== null && (typeof region !== 'object'
      || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(region[key]))
      || region.x < 0 || region.y < 0 || region.width <= 0 || region.height <= 0
      || region.x + region.width > 1 + 1e-9 || region.y + region.height > 1 + 1e-9)) return null;
  return {variant_id: variant.id, time: frame.time_seconds,
    region: region ? {x: region.x, y: region.y, width: region.width, height: region.height} : null};
}

export function currentEvaluationDecisions(evaluation, currentCase) {
  if (!isCurrentEvaluation(evaluation, currentCase)) return [];
  const known = new Set((evaluation.findings ?? []).map(row => row.id));
  const latest = new Map();
  for (const row of evaluation.decisions ?? []) {
    if (!known.has(row.finding_id) || !['needs_fix', 'dismiss', 'defer'].includes(row.decision)
        || (row.report_identity !== undefined && row.report_identity !== evaluation.report_identity)) continue;
    latest.set(row.finding_id, row);
  }
  return [...latest.values()];
}

export function localResourceUrl(value, origin) {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return null;
  try {
    const url = new URL(value, origin);
    return url.origin === origin && ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

// A task can display only the repository's real PR URL. A user comment or an
// imported receipt must never turn an arbitrary link into a trusted action.
export function iterationPullRequestUrl(value) {
  return typeof value === 'string'
    && value === value.trim()
    && /^https:\/\/github\.com\/zmhhaha\/panghu_agent\/pull\/[1-9][0-9]{0,9}$/.test(value)
    ? value : null;
}

export function iterationResourceUrl(value, origin, applicationPath) {
  const target = localResourceUrl(value, origin);
  if (!target || typeof applicationPath !== 'string' || !applicationPath.startsWith('/')
      || applicationPath.startsWith('//') || applicationPath.includes('\\')
      || !applicationPath.endsWith('/')) return null;
  const path = new URL(target).pathname;
  return path.startsWith(applicationPath) && !/%(?:2f|5c|2e|00)/i.test(path) ? target : null;
}

export function isIterationOverview(value, module) {
  const digest = input => typeof input === 'string' && /^[0-9a-f]{64}$/.test(input);
  return Boolean(value && typeof module === 'string' && module.length && value.module === module
    && digest(value.current_cursor) && (value.module_cursor === null || digest(value.module_cursor))
    && Array.isArray(value.current_effective_feedback) && Array.isArray(value.preview_delta)
    && Array.isArray(value.tasks));
}

export function iterationStateLabel(state) {
  return ({prepared: '已生成 · 待同步', synced: '已同步 · 待执行', running: '开发执行中',
    pr_open: 'PR 待核看', candidate_ready: '候选待验收', accepted: '开发样例验收通过',
    rejected: '开发样例需改进', failed: '执行失败', canceled: '已取消'})[state] ?? '状态未知 · 请刷新核对';
}

export function currentIterationAttemptEvents(task) {
  const events = Array.isArray(task?.events) ? task.events : [];
  let start = 0;
  for (let index = 0; index < events.length; index++) if (events[index]?.new_attempt === true) start = index;
  return events.slice(start);
}

export function iterationAttemptReceipt(task) {
  const result = {};
  for (const event of currentIterationAttemptEvents(task)) {
    for (const key of ['pr_url', 'candidate_url', 'run_id', 'branch', 'commit'])
      if (typeof event?.receipt?.[key] === 'string') result[key] = event.receipt[key];
  }
  return result;
}

export function canImportIterationReceipt(task) {
  return ['prepared', 'synced', 'running', 'pr_open', 'candidate_ready', 'rejected', 'failed', 'canceled'].includes(task?.state);
}

export function verifiedIterationCandidate(task) {
  const candidate = currentIterationAttemptEvents(task).findLast(event => event?.state === 'candidate_ready');
  const proof = candidate?.verified_candidate;
  const digest = value => typeof value === 'string' && value.length === 64 && /^[0-9a-f]+$/.test(value);
  const source = value => typeof value === 'string' && [40, 64].includes(value.length) && /^[0-9a-f]+$/.test(value);
  const feedback = Array.isArray(task?.package?.feedback) ? task.package.feedback : null;
  const covered = feedback?.filter(row => row.case_id === proof?.case_id).map(row => row.key).sort();
  const uncovered = feedback?.filter(row => row.case_id !== proof?.case_id).map(row => row.key).sort();
  const sameKeys = (actual, expected) => Array.isArray(actual) && Array.isArray(expected)
    && actual.every(key => typeof key === 'string' && key.length > 0)
    && new Set(actual).size === actual.length
    && JSON.stringify([...actual].sort()) === JSON.stringify(expected);
  const valid = Boolean(typeof task?.task_id === 'string' && digest(task.task_id)
    && task.package?.identity === task.task_id
    && ['diagram', 'process', 'continuity', 'canvas', 'three', 'charts', 'algorithms', 'interface', 'math', 'spatial', 'fields'].includes(task.package.module)
    && source(task.package.source_revision)
    && task.acceptance_scope === 'developer_sample_only' && task.production_adopted === false
    && proof?.task_identity === task.task_id && proof.module === task.package.module
    && proof.source_revision === task.package.source_revision && source(proof.commit)
    && proof.verification_scope === 'frozen_report_and_developer_publication_binding'
    && typeof proof.case_id === 'string' && proof.case_id.length > 0
    && sameKeys(proof.covered_feedback_keys, covered) && sameKeys(proof.uncovered_feedback_keys, uncovered)
    && candidate.receipt?.commit === proof.commit
    && typeof proof.run_slug === 'string' && /^[a-z0-9][a-z0-9-]{0,63}$/.test(proof.run_slug)
    && candidate.receipt?.run_slug === proof.run_slug
    && digest(proof.report_identity) && candidate.receipt?.report_identity === proof.report_identity
    && digest(proof.run_identity) && digest(proof.semantic_input_sha256) && digest(proof.publication_identity)
    && Array.isArray(proof.artifact_sha256s) && proof.artifact_sha256s.length > 0
    && proof.artifact_sha256s.every(digest)
    && typeof proof.candidate_url === 'string'
    && proof.candidate_url.startsWith('/') && !proof.candidate_url.startsWith('//')
    && !proof.candidate_url.includes('\\') && !proof.candidate_url.includes('?')
    && proof.candidate_url === candidate.receipt?.candidate_url
    && proof.candidate_url.endsWith(`/#evaluation-${proof.run_slug}-${proof.run_identity.slice(0, 16)}`));
  return valid ? proof : null;
}

export function canReviewIteration(task) {
  return Boolean(task?.state === 'candidate_ready' && task.current_feedback_matches === true
    && verifiedIterationCandidate(task));
}

export function canAcceptIteration(task) {
  return Boolean(canReviewIteration(task) && verifiedIterationCandidate(task).uncovered_feedback_keys.length === 0);
}

// Finish the first download before navigating the immutable preview player,
// whose short initialization deadline should measure animation startup only.
export async function warmPreviewAssets(variants, {origin, signal, timeoutMs = 600000,
  fetcher = globalThis.fetch, onProgress = () => {}} = {}) {
  const allowed = new Set(['gsap.min.js', 'NotoSansCJK-Regular.ttc', 'three.module.min.js']);
  const assets = new Map();
  for (const variant of variants) {
    if (variant.available === false) continue;
    if (!Array.isArray(variant.preload_assets) || variant.preload_assets.length > allowed.size)
      throw new Error('预览资源清单无效，请刷新工作台。');
    for (const asset of variant.preload_assets) {
      const url = localResourceUrl(asset?.asset_url, origin);
      if (!allowed.has(asset?.name) || !/^[0-9a-f]{64}$/.test(asset?.sha256 ?? '')
          || !Number.isSafeInteger(asset?.bytes) || asset.bytes <= 0 || asset.bytes > 32 * 1024 * 1024
          || !url || !new URL(url).pathname.endsWith(`/assets/${asset.sha256}/${asset.name}`)
          || new URL(url).search || new URL(url).hash)
        throw new Error('预览资源清单无效，请刷新工作台。');
      const previous = assets.get(url);
      if (previous && (previous.sha256 !== asset.sha256 || previous.bytes !== asset.bytes))
        throw new Error('预览资源清单冲突，请刷新工作台。');
      assets.set(url, asset);
    }
  }
  const controller = new AbortController();
  let timer, rejectStopped;
  const stopped = new Promise((_, reject) => { rejectStopped = reject; });
  const abort = () => {
    controller.abort();
    rejectStopped(new DOMException('已切换样例，停止加载。', 'AbortError'));
  };
  const work = async () => {
    if (signal?.aborted) { abort(); return stopped; }
    signal?.addEventListener('abort', abort, {once: true});
    timer = setTimeout(() => {
      controller.abort();
      rejectStopped(new Error('动画资源加载超时，请检查网络后重新打开样例。'));
    }, timeoutMs);
    let completed = 0;
    onProgress({completed, total: assets.size});
    await Promise.all([...assets].map(async ([url, asset]) => {
      const response = await fetcher(url, {credentials: 'same-origin', signal: controller.signal});
      if (!response.ok || response.redirected) throw new Error('动画资源加载失败，请确认登录状态后刷新。');
      // Reading the full body is essential: fetch resolves before a large font
      // finishes downloading, and the original player still has a 20s limit.
      const bytes = await response.arrayBuffer();
      if (controller.signal.aborted) throw new DOMException('已停止加载。', 'AbortError');
      if (bytes.byteLength !== asset.bytes) throw new Error('动画资源不完整，请刷新后重试。');
      const hash = await globalThis.crypto.subtle.digest('SHA-256', bytes);
      const digest = [...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, '0')).join('');
      if (digest !== asset.sha256) throw new Error('动画资源校验失败，请刷新后重试。');
      if (!controller.signal.aborted) onProgress({completed: ++completed, total: assets.size});
    }));
  };
  try {
    await Promise.race([work(), stopped]);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
    controller.abort();
  }
}
