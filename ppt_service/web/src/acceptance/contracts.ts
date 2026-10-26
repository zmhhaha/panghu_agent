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
export interface AcceptanceRecord {
  id: string;
  revision: number;
  kind: 'visual_review' | 'powerpoint_review' | 'revoke';
  created_at: string;
  reviewer: { issuer: string; sub: string };
  binding: AcceptanceBinding;
  payload: VisualReview | PowerPointReview | Revocation;
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
  publication_authorized: false;
  binding: AcceptanceBinding;
  pages: AcceptancePage[];
  native_chart_count: number;
  programmatic_status: 'pending';
  visual_status: ReviewStatus;
  powerpoint_status: ReviewStatus;
  human_status: ReviewStatus;
  acceptance_status: 'pending' | 'stale' | 'unreviewable';
  pending_reasons: string[];
  records: AcceptanceRecord[];
}
export type AcceptanceCommand = { expected_revision: number; binding: AcceptanceBinding } & (
  { kind: 'visual_review'; pages: PageReview[]; note: string } |
  { kind: 'powerpoint_review' } & PowerPointReview |
  { kind: 'revoke'; record_id: string; reason: string }
);
export interface AcceptanceClient {
  load(projectId: string, versionId: string, signal?: AbortSignal): Promise<AcceptanceSnapshot>;
  save(projectId: string, versionId: string, command: AcceptanceCommand): Promise<AcceptanceSnapshot>;
}
