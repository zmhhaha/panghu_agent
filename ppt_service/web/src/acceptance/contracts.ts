/** Standalone P4 contract. All conclusions and record identities come from the server. */
export type ReviewStatus = 'pending' | 'passed' | 'needs_changes';
export type PageVerdict = 'pending' | 'pass' | 'needs_changes';
export type CheckVerdict = 'pending' | 'pass' | 'fail';
export type IssueCategory = 'layout' | 'text' | 'chart' | 'image' | 'other';
export type IssueSeverity = 'minor' | 'major' | 'blocking';
export interface AcceptanceBinding {
  project_id: string;
  version_id: string;
  source_revision: number;
  evidence_id: string;
  evidence_sha256: string;
  runtime: { engine: string; runtime_revision: string; profile: string; checkpoint_format: string };
  manifest_sha256: string;
  input_snapshot_sha256: string;
  pptx_sha256: string;
  source_fingerprint_sha256: string;
}
export interface AcceptancePage {
  number: number;
  title: string;
  preview_url: string;
  preview_sha256: string;
  source_sha256: string;
}
export interface VisualIssue { category: IssueCategory; severity: IssueSeverity; description: string }
export interface PageReview { number: number; preview_sha256: string; verdict: PageVerdict; issues: VisualIssue[] }
export interface VisualReview { pages: PageReview[]; note: string }
export interface PowerPointReview {
  application_version: string;
  platform: string;
  checks: { opened: CheckVerdict; content_edited: CheckVerdict; native_chart_edited: CheckVerdict | 'not_applicable'; saved: CheckVerdict; reopened: CheckVerdict };
  note: string;
}
export interface Revocation { record_id: string; reason: string }
export interface RenderedPptxPage { number: number; preview_url: string; preview_sha256: string }
export type LayoutDiagnosticCode = 'short_token_unplanned_wrap' | 'short_token_orphan_fragment' | 'short_label_orphan_line' | 'text_outside_frame' | 'text_outside_slide' | 'text_collision';
export interface LayoutDiagnosticItem {
  page: number;
  code: LayoutDiagnosticCode;
  shape_id: number;
  other_shape_id?: number | null;
  message: string;
  suggestion: string;
}
export interface LayoutFailureDiagnostic {
  error_code: 'final_pptx_text_layout_failed';
  title: string;
  message: string;
  suggestion: string;
  items: LayoutDiagnosticItem[];
  total_issue_count: number;
  truncated: boolean;
}
export interface SourceQualityDiagnostic {
  error_code: 'source_quality_warnings';
  items: { page: number; warning_count: number }[];
  project_warning_count: number;
}
export interface ProgrammaticAcceptance {
  status: ReviewStatus;
  receipt_sha256: string | null;
  completed_gates: string[];
  pending_gates: string[];
  renderer: string | null;
  detail: string;
  rendered_pages?: RenderedPptxPage[];
  diagnostics?: LayoutFailureDiagnostic | null;
  source_diagnostics?: SourceQualityDiagnostic | null;
}
export interface PublicationState {
  status: 'draft' | 'published';
  record_id: string | null;
  published_at: string | null;
}
export interface PublicationRecord {
  visual_record_id: string;
  powerpoint_record_id: string;
  programmatic_receipt_sha256: string;
}
export interface AcceptanceRecord {
  id: string;
  revision: number;
  kind: 'visual_review' | 'powerpoint_review' | 'revoke' | 'publish';
  created_at: string;
  reviewer: { issuer: string; sub: string };
  binding: AcceptanceBinding;
  payload: VisualReview | PowerPointReview | Revocation | PublicationRecord;
  revoked: boolean;
  stale: boolean;
}
export interface AcceptanceSnapshot {
  project_id: string;
  version_id: string;
  revision: number;
  source_revision: number;
  can_review: boolean;
  can_revoke: boolean;
  publication_authorized: boolean;
  can_publish?: boolean;
  publication?: PublicationState;
  programmatic?: ProgrammaticAcceptance;
  binding: AcceptanceBinding;
  pages: AcceptancePage[];
  native_chart_count: number;
  programmatic_status: ReviewStatus;
  visual_status: ReviewStatus;
  powerpoint_status: ReviewStatus;
  human_status: ReviewStatus;
  acceptance_status: 'pending' | 'passed' | 'stale' | 'unreviewable';
  pending_reasons: string[];
  records: AcceptanceRecord[];
}
export type AcceptanceCommand = { expected_revision: number; binding: AcceptanceBinding } & (
  { kind: 'visual_review'; pages: PageReview[]; note: string } |
  { kind: 'powerpoint_review' } & PowerPointReview |
  { kind: 'revoke'; record_id: string; reason: string }
);
export interface PublicationCommand { kind: 'publish'; expected_revision: number; binding: AcceptanceBinding; confirm: true }
export interface AcceptanceClient {
  load(projectId: string, versionId: string, signal?: AbortSignal): Promise<AcceptanceSnapshot>;
  save(projectId: string, versionId: string, command: AcceptanceCommand): Promise<AcceptanceSnapshot>;
  publish?(projectId: string, versionId: string, command: PublicationCommand): Promise<AcceptanceSnapshot>;
}
