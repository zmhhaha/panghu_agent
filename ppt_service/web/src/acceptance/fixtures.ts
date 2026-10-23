/** SYNTHETIC ONLY: isolated UI tests and the opt-in demo, never used by the site. */
import type { AcceptanceSnapshot } from './contracts';

export function syntheticSnapshot(versionId = 'demo-v1'): AcceptanceSnapshot {
  const digest = (digit: string) => digit.repeat(64);
  return {
    project_id: 'demo-project', version_id: versionId, source_revision: 7, revision: 0,
    binding: { project_id: 'demo-project', version_id: versionId, source_revision: 7, manifest_sha256: digest('a'), input_snapshot_sha256: digest('b'), pptx_sha256: digest('c'), source_fingerprint_sha256: digest('d'), evidence_id: 'demo-evidence', evidence_sha256: digest('e'), runtime: { engine: 'pi', runtime_revision: 'synthetic-demo', profile: 'ppt', checkpoint_format: 'demo-v1' } },
    pages: [{ number: 1, title: '从现状到下一步', preview_sha256: digest('1'), source_sha256: digest('2'), preview_url: `/api/v1/versions/${versionId}/slides/1/preview` }, { number: 2, title: '让每一步都可核验', preview_sha256: digest('3'), source_sha256: digest('4'), preview_url: `/api/v1/versions/${versionId}/slides/2/preview` }],
    native_chart_count: 1, programmatic_status: 'pending', visual_status: 'pending', powerpoint_status: 'pending', human_status: 'pending', acceptance_status: 'pending', pending_reasons: ['independent_postflight_integration_pending', 'visual_review_pending', 'powerpoint_review_pending'], can_review: true, can_revoke: false, publication_authorized: false, records: [],
  };
}
