import { ApiError, mutate, request } from '../api';
import type { AcceptanceClient, AcceptanceRecord, AcceptanceSnapshot, PowerPointReview, VisualReview } from './contracts';

const path = (project: string, version: string) => `/projects/${encodeURIComponent(project)}/versions/${encodeURIComponent(version)}/acceptance`;
export const acceptanceClient: AcceptanceClient = {
  load: (project, version, signal) => request(path(project, version), { signal }),
  save: (project, version, command) => mutate(`${path(project, version)}/records`, command),
};

/** An authenticated image route only; source SVG, arbitrary URLs and redirects are not UI inputs. */
export function rasterPreviewUrl(versionId: string, page: number, candidate: string): string | null {
  if (!Number.isSafeInteger(page) || page < 1 || page > 100) return null;
  const expected = `/api/v1/versions/${encodeURIComponent(versionId)}/slides/${page}/preview`;
  return candidate === expected ? expected : null;
}

export function assertSnapshot(value: AcceptanceSnapshot, project: string, version: string): AcceptanceSnapshot {
  const sha = /^[a-f0-9]{64}$/;
  const states = ['pending', 'passed', 'needs_changes'];
  if (!value || value.project_id !== project || value.version_id !== version ||
    !Number.isSafeInteger(value.revision) || value.revision < 0 || !value.binding ||
    !['manifest_sha256', 'input_snapshot_sha256', 'pptx_sha256', 'source_fingerprint_sha256', 'evidence_sha256'].every(k => sha.test((value.binding as unknown as Record<string, string>)[k])) ||
    value.binding.project_id !== project || value.binding.version_id !== version || value.binding.source_revision !== value.source_revision || typeof value.binding.evidence_id !== 'string' ||
    !value.binding.runtime || !['engine', 'runtime_revision', 'profile', 'checkpoint_format'].every(k => typeof (value.binding.runtime as Record<string, string>)[k] === 'string') ||
    !Number.isSafeInteger(value.source_revision) || value.source_revision < 0 || typeof value.can_review !== 'boolean' || typeof value.can_revoke !== 'boolean' || value.publication_authorized !== false ||
    !Array.isArray(value.pages) || !value.pages.length || value.pages.length > 100 ||
    !value.pages.every((p, index) => p && p.number === index + 1 && typeof p.title === 'string' && typeof p.preview_url === 'string' && sha.test(p.preview_sha256) && sha.test(p.source_sha256)) ||
    new Set(value.pages.map(p => p.number)).size !== value.pages.length ||
    !Number.isSafeInteger(value.native_chart_count) || value.native_chart_count < 0 ||
    value.programmatic_status !== 'pending' || !states.includes(value.visual_status) || !states.includes(value.powerpoint_status) || !states.includes(value.human_status) ||
    !['pending', 'stale', 'unreviewable'].includes(value.acceptance_status) ||
    !Array.isArray(value.pending_reasons) || !value.pending_reasons.every(r => typeof r === 'string') ||
    !Array.isArray(value.records) || !value.records.every(r => r && typeof r.id === 'string' && Number.isSafeInteger(r.revision) &&
      ['visual_review', 'powerpoint_review', 'revoke'].includes(r.kind) && typeof r.created_at === 'string' && r.reviewer &&
      typeof r.reviewer.sub === 'string' && typeof r.reviewer.issuer === 'string' && r.binding && r.payload &&
      typeof r.revoked === 'boolean' && typeof r.stale === 'boolean' && r.binding.project_id === project && r.binding.version_id === version && validPayload(r))) throw new ApiError('INVALID_RESPONSE');
  return value;
}

function validPayload(record: AcceptanceRecord): boolean {
  if (record.kind === 'visual_review') {
    const payload = record.payload as VisualReview;
    return typeof payload.note === 'string' && Array.isArray(payload.pages) && payload.pages.length > 0 && payload.pages.length <= 100 && payload.pages.every(page =>
      page && Number.isSafeInteger(page.number) && typeof page.preview_sha256 === 'string' && ['pending', 'pass', 'needs_changes'].includes(page.verdict) && Array.isArray(page.issues) && page.issues.length <= 50 && page.issues.every(issue =>
        issue && ['layout', 'text', 'chart', 'image', 'other'].includes(issue.category) && ['minor', 'major', 'blocking'].includes(issue.severity) && typeof issue.description === 'string'));
  }
  if (record.kind === 'powerpoint_review') {
    const payload = record.payload as PowerPointReview;
    return typeof payload.application_version === 'string' && typeof payload.platform === 'string' && typeof payload.note === 'string' && !!payload.checks &&
      [payload.checks.opened, payload.checks.saved, payload.checks.reopened].every(value => ['pending', 'pass', 'fail'].includes(value)) &&
      (['pending', 'pass', 'fail'].includes(payload.checks.content_edited) || (record.stale && payload.checks.content_edited === undefined)) &&
      ['pending', 'pass', 'fail', 'not_applicable'].includes(payload.checks.native_chart_edited);
  }
  const payload = record.payload as { record_id: string; reason: string };
  return typeof payload.record_id === 'string' && typeof payload.reason === 'string';
}
