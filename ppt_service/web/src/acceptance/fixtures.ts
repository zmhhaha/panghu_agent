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

/** Contract fixture only; no document was rendered or checked. */
export function syntheticLayoutFailure(versionId = 'demo-v1'): AcceptanceSnapshot {
  const value = syntheticSnapshot(versionId);
  return { ...value, programmatic_status: 'needs_changes',
    pending_reasons: ['independent_programmatic_needs_changes', ...value.pending_reasons],
    programmatic: { status: 'needs_changes', receipt_sha256: 'f'.repeat(64), completed_gates: [],
      pending_gates: ['independent_final_pptx_rendering', 'independent_export_build_contract', 'independent_postflight_and_delivery'],
      renderer: 'LibreOffice Impress', detail: '最终 PPTX 的文字排版检查未通过，以下页项需要修复；原审核稿仍可查看。', rendered_pages: [],
      diagnostics: { error_code: 'final_pptx_text_layout_failed', title: 'PPTX 文字排版需要修复',
        message: '最终 PPTX 的实际文字检查发现以下页面存在排版问题。',
        suggestion: '请在修改意见中说明这些页面需要调整的内容；原资料、方案与审核稿仍保留。',
        items: [{ page: 2, code: 'text_collision', shape_id: 17, other_shape_id: 22,
          message: '两个文字区域出现明显重叠。', suggestion: '拉开两个文字区域，检查标题与正文之间的间距。' }],
        total_issue_count: 1, truncated: false } } };
}

/** Contract fixture only; counts do not represent a real quality inspection. */
export function syntheticSourceWarnings(versionId = 'demo-v1'): AcceptanceSnapshot {
  const value = syntheticSnapshot(versionId);
  const receipt = 'f'.repeat(64);
  return { ...value, programmatic_status: 'needs_changes',
    pending_reasons: ['independent_programmatic_needs_changes', 'visual_review_pending', 'powerpoint_review_pending'],
    programmatic: { status: 'needs_changes', receipt_sha256: receipt,
      completed_gates: ['independent_raster_regeneration_and_binding', 'independent_delivery_audit', 'independent_native_ooxml_and_chart_data', 'independent_final_pptx_rendering'],
      pending_gates: ['independent_export_build_contract', 'independent_postflight_and_delivery'],
      renderer: 'LibreOffice Impress', detail: '最终 PPTX 已独立渲染；完整导出与交付验收仍有待完成项目。',
      rendered_pages: value.pages.map(page => ({ number: page.number, preview_sha256: page.preview_sha256,
        preview_url: `/api/v1/projects/${value.project_id}/versions/${versionId}/acceptance/rendered-slides/${page.number}/preview?receipt_sha256=${receipt}` })),
      source_diagnostics: { error_code: 'source_quality_warnings', items: [{ page: 2, warning_count: 2 }], project_warning_count: 1 } } };
}
