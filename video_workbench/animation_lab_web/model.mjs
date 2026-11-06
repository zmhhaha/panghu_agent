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
