import { ApiError, mutate, request } from '../api';
import type { AcceptanceClient, AcceptanceRecord, AcceptanceSnapshot, LayoutDiagnosticCode, PowerPointReview, PublicationRecord, VisualReview } from './contracts';

const path = (project: string, version: string) => `/projects/${encodeURIComponent(project)}/versions/${encodeURIComponent(version)}/acceptance`;
export const acceptanceClient: AcceptanceClient = {
  load: (project, version, signal) => request(path(project, version), { signal }),
  save: (project, version, command) => mutate(`${path(project, version)}/records`, command),
  publish: (project, version, command) => mutate(`${path(project, version)}/publication`, command),
};

/** An authenticated image route only; source SVG, arbitrary URLs and redirects are not UI inputs. */
export function rasterPreviewUrl(versionId: string, page: number, candidate: string): string | null {
  if (!Number.isSafeInteger(page) || page < 1 || page > 100) return null;
  const expected = `/api/v1/versions/${encodeURIComponent(versionId)}/slides/${page}/preview`;
  return candidate === expected ? expected : null;
}

/** The receipt in the URL pins this PNG to the displayed independent evidence. */
export function renderedPptxPreviewUrl(projectId: string, versionId: string, page: number, receipt: string | null, candidate: string): string | null {
  if (!Number.isSafeInteger(page) || page < 1 || page > 100 || !receipt || !/^[a-f0-9]{64}$/.test(receipt)) return null;
  const expected = `/api/v1/projects/${encodeURIComponent(projectId)}/versions/${encodeURIComponent(versionId)}/acceptance/rendered-slides/${page}/preview?receipt_sha256=${receipt}`;
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
    !Number.isSafeInteger(value.source_revision) || value.source_revision < 0 || typeof value.can_review !== 'boolean' || typeof value.can_revoke !== 'boolean' || typeof value.publication_authorized !== 'boolean' ||
    !Array.isArray(value.pages) || !value.pages.length || value.pages.length > 100 ||
    !value.pages.every((p, index) => p && p.number === index + 1 && typeof p.title === 'string' && typeof p.preview_url === 'string' && sha.test(p.preview_sha256) && sha.test(p.source_sha256)) ||
    new Set(value.pages.map(p => p.number)).size !== value.pages.length ||
    !Number.isSafeInteger(value.native_chart_count) || value.native_chart_count < 0 ||
    !states.includes(value.programmatic_status) || !states.includes(value.visual_status) || !states.includes(value.powerpoint_status) || !states.includes(value.human_status) ||
    !['pending', 'passed', 'stale', 'unreviewable'].includes(value.acceptance_status) ||
    !Array.isArray(value.pending_reasons) || !value.pending_reasons.every(r => typeof r === 'string') ||
    !Array.isArray(value.records) || !value.records.every(r => r && typeof r.id === 'string' && Number.isSafeInteger(r.revision) &&
      ['visual_review', 'powerpoint_review', 'revoke', 'publish'].includes(r.kind) && typeof r.created_at === 'string' && r.reviewer &&
      typeof r.reviewer.sub === 'string' && typeof r.reviewer.issuer === 'string' && r.binding && r.payload &&
      typeof r.revoked === 'boolean' && typeof r.stale === 'boolean' && r.binding.project_id === project && r.binding.version_id === version && validPayload(r)) ||
    !validPublication(value) || !validLayoutDiagnostics(value) || !validSourceDiagnostics(value)) throw new ApiError('INVALID_RESPONSE');
  return value;
}

const layoutMessages: Record<LayoutDiagnosticCode, readonly [string, string]> = {
  short_token_unplanned_wrap: ['短词被拆成了多行。', '加宽文字框或调整字距，保持短词完整。'],
  short_token_orphan_fragment: ['短词末尾出现了单独一行的字母。', '合并被拆开的短词，并为文字框留出足够宽度。'],
  short_label_orphan_line: ['短标签末尾有一个字单独换行。', '加宽文字框，或重新安排标签的行分布。'],
  text_outside_frame: ['文字超出了对应文本框。', '调整文字框的宽高、内边距或字号。'],
  text_outside_slide: ['文字超出了页面边界。', '移动或缩小文字区域，使内容完整位于页面内。'],
  text_collision: ['两个文字区域出现明显重叠。', '拉开两个文字区域，检查标题与正文之间的间距。'],
};

/** Only the server's fixed, bounded failure projection is a public UI input. */
function validLayoutDiagnostics(value: AcceptanceSnapshot): boolean {
  const programmatic = value.programmatic;
  const diagnostic = programmatic?.diagnostics;
  if (diagnostic === undefined || diagnostic === null) return true;
  const object = (item: unknown): item is Record<string, unknown> => !!item && typeof item === 'object' && !Array.isArray(item);
  const integer = (item: unknown, max: number): item is number => typeof item === 'number' && Number.isSafeInteger(item) && item >= 1 && item <= max;
  const exactKeys = (item: Record<string, unknown>, keys: readonly string[]) => Object.keys(item).length === keys.length && keys.every(key => Object.hasOwn(item, key));
  if (!programmatic || programmatic.status !== 'needs_changes' || !programmatic.receipt_sha256 ||
      !programmatic.pending_gates.includes('independent_final_pptx_rendering') || programmatic.completed_gates.length > 0 ||
      !!programmatic.rendered_pages?.length || value.can_publish || value.publication_authorized ||
      !object(diagnostic) || !exactKeys(diagnostic, ['error_code', 'title', 'message', 'suggestion', 'items', 'total_issue_count', 'truncated']) ||
      diagnostic.error_code !== 'final_pptx_text_layout_failed' || diagnostic.title !== 'PPTX 文字排版需要修复' ||
      diagnostic.message !== '最终 PPTX 的实际文字检查发现以下页面存在排版问题。' ||
      diagnostic.suggestion !== '请在修改意见中说明这些页面需要调整的内容；原资料、方案与审核稿仍保留。' ||
      !Array.isArray(diagnostic.items) || !diagnostic.items.length || diagnostic.items.length > 100 ||
      !integer(diagnostic.total_issue_count, 1000000) || diagnostic.total_issue_count < diagnostic.items.length ||
      typeof diagnostic.truncated !== 'boolean' || diagnostic.truncated !== (diagnostic.total_issue_count > diagnostic.items.length)) return false;
  return diagnostic.items.every(item => {
    if (!object(item) || !exactKeys(item, ['page', 'code', 'shape_id', 'message', 'suggestion', ...(Object.hasOwn(item, 'other_shape_id') ? ['other_shape_id'] : [])]) ||
        !integer(item.page, value.pages.length) || !integer(item.shape_id, 2147483647) ||
        !(item.other_shape_id === undefined || item.other_shape_id === null || integer(item.other_shape_id, 2147483647) && item.other_shape_id !== item.shape_id) ||
        typeof item.code !== 'string' || !Object.hasOwn(layoutMessages, item.code)) return false;
    const expected = layoutMessages[item.code as LayoutDiagnosticCode];
    return item.message === expected[0] && item.suggestion === expected[1];
  });
}

/** Source reports expose counts only; report text and paths are never UI inputs. */
function validSourceDiagnostics(value: AcceptanceSnapshot): boolean {
  const programmatic = value.programmatic;
  const diagnostic = programmatic?.source_diagnostics;
  if (diagnostic === undefined || diagnostic === null) return true;
  const object = (item: unknown): item is Record<string, unknown> => !!item && typeof item === 'object' && !Array.isArray(item);
  const integer = (item: unknown, min: number, max: number): item is number => typeof item === 'number' && Number.isSafeInteger(item) && item >= min && item <= max;
  const exactKeys = (item: Record<string, unknown>, keys: readonly string[]) => Object.keys(item).length === keys.length && keys.every(key => Object.hasOwn(item, key));
  if (!programmatic || programmatic.status !== 'needs_changes' || !programmatic.receipt_sha256 ||
      !programmatic.completed_gates.includes('independent_final_pptx_rendering') ||
      programmatic.completed_gates.includes('independent_postflight_source_and_quality') ||
      programmatic.completed_gates.includes('independent_postflight_and_delivery') ||
      !programmatic.pending_gates.includes('independent_postflight_and_delivery') ||
      programmatic.rendered_pages?.length !== value.pages.length ||
      value.can_publish || value.publication_authorized || !object(diagnostic) ||
      !exactKeys(diagnostic, ['error_code', 'items', 'project_warning_count']) || diagnostic.error_code !== 'source_quality_warnings' ||
      !Array.isArray(diagnostic.items) || diagnostic.items.length > 100 ||
      !integer(diagnostic.project_warning_count, 0, 1000000) || !diagnostic.items.length && !diagnostic.project_warning_count) return false;
  let previous = 0;
  return diagnostic.items.every(item => {
    if (!object(item) || !exactKeys(item, ['page', 'warning_count']) ||
        !integer(item.page, 1, value.pages.length) || item.page <= previous ||
        !integer(item.warning_count, 1, 1000000)) return false;
    previous = item.page;
    return true;
  });
}

function validPublication(value: AcceptanceSnapshot): boolean {
  const sha = /^[a-f0-9]{64}$/;
  const programmatic = value.programmatic;
  const publication = value.publication;
  if (value.can_publish !== undefined && typeof value.can_publish !== 'boolean') return false;
  if (programmatic && (!['pending', 'passed', 'needs_changes'].includes(programmatic.status) || programmatic.status !== value.programmatic_status ||
    !(programmatic.receipt_sha256 === null || typeof programmatic.receipt_sha256 === 'string' && sha.test(programmatic.receipt_sha256)) ||
    !Array.isArray(programmatic.completed_gates) || !programmatic.completed_gates.every(gate => typeof gate === 'string') ||
    !Array.isArray(programmatic.pending_gates) || !programmatic.pending_gates.every(gate => typeof gate === 'string') ||
    !(programmatic.renderer === null || typeof programmatic.renderer === 'string') || typeof programmatic.detail !== 'string' ||
    (programmatic.rendered_pages !== undefined && (!Array.isArray(programmatic.rendered_pages) || programmatic.rendered_pages.length > 100 ||
      !programmatic.rendered_pages.every((page, index) => page && page.number === index + 1 && typeof page.preview_url === 'string' && sha.test(page.preview_sha256)))))) return false;
  if (publication && (!['draft', 'published'].includes(publication.status) ||
    !(publication.record_id === null || typeof publication.record_id === 'string') ||
    !(publication.published_at === null || typeof publication.published_at === 'string' && Number.isFinite(Date.parse(publication.published_at))))) return false;
  if (value.programmatic_status === 'passed' && (!programmatic || !programmatic.receipt_sha256 || programmatic.pending_gates.length ||
    !programmatic.renderer || programmatic.rendered_pages?.length !== value.pages.length ||
    !['independent_export_build_contract', 'independent_postflight_and_delivery', 'independent_final_pptx_rendering'].every(gate => programmatic.completed_gates.includes(gate)))) return false;
  const complete = value.programmatic_status === 'passed' && value.visual_status === 'passed' && value.powerpoint_status === 'passed' && value.human_status === 'passed';
  if (value.acceptance_status === 'passed' && !complete) return false;
  if (value.can_publish && (!value.can_review || value.acceptance_status !== 'passed' || !complete || value.pending_reasons.length || value.publication_authorized || publication?.status !== 'draft')) return false;
  if (value.publication_authorized !== (publication?.status === 'published')) return false;
  if (publication?.status === 'published') {
    const record = value.records.find(record => record.id === publication.record_id);
    if (!publication.record_id || !publication.published_at || !record || record.kind !== 'publish' || record.revoked || record.stale ||
      record.created_at !== publication.published_at || JSON.stringify(record.binding) !== JSON.stringify(value.binding)) return false;
    const payload = record.payload as PublicationRecord;
    if (payload.programmatic_receipt_sha256 !== programmatic?.receipt_sha256 || !complete || value.acceptance_status !== 'passed' || value.pending_reasons.length) return false;
    for (const [kind, id] of [['visual_review', payload.visual_record_id], ['powerpoint_review', payload.powerpoint_record_id]]) {
      const latest = value.records.slice().reverse().find(record => record.kind === kind);
      if (!latest || latest.id !== id || latest.revoked || latest.stale) return false;
    }
  } else if (publication && (publication.record_id !== null || publication.published_at !== null)) return false;
  return true;
}

function validPayload(record: AcceptanceRecord): boolean {
  if (record.kind === 'publish') {
    const payload = record.payload as PublicationRecord;
    return typeof payload.visual_record_id === 'string' && !!payload.visual_record_id &&
      typeof payload.powerpoint_record_id === 'string' && !!payload.powerpoint_record_id &&
      typeof payload.programmatic_receipt_sha256 === 'string' && /^[a-f0-9]{64}$/.test(payload.programmatic_receipt_sha256);
  }
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
