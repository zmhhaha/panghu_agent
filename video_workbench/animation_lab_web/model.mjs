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
