import type { AcceptanceSnapshot, PowerPointReview, VisualReview } from './contracts';

export function visualDraft(snapshot: AcceptanceSnapshot): VisualReview {
  const latest = [...snapshot.records].reverse().find(r => r.kind === 'visual_review');
  const saved = latest && !latest.revoked && !latest.stale ? latest.payload as VisualReview : null;
  return {
    pages: snapshot.pages.map(p => {
      const previous = saved?.pages?.find(item => item.number === p.number && item.preview_sha256 === p.preview_sha256);
      return { number: p.number, preview_sha256: p.preview_sha256, verdict: previous?.verdict ?? 'pending', issues: previous?.issues.map(issue => ({ ...issue })) ?? [] };
    }),
    note: saved?.note ?? '',
  };
}

export function powerpointDraft(snapshot: AcceptanceSnapshot): PowerPointReview {
  const latest = [...snapshot.records].reverse().find(r => r.kind === 'powerpoint_review');
  if (latest && !latest.revoked && !latest.stale) {
    const saved = latest.payload as PowerPointReview;
    return { ...saved, checks: { ...saved.checks } };
  }
  return { application_version: '', platform: '', checks: { opened: 'pending', content_edited: 'pending', native_chart_edited: 'pending', saved: 'pending', reopened: 'pending' }, note: '' };
}

export function powerpointValidation(value: PowerPointReview, chartCount: number): string {
  const checks = value.checks;
  if (Object.values(checks).some(check => check === 'pass' || check === 'fail') && (!value.application_version.trim() || !value.platform.trim())) return '请填写实际使用的 PowerPoint 版本和操作系统。';
  if ([checks.content_edited, checks.native_chart_edited, checks.saved, checks.reopened].some(check => check === 'pass') && checks.opened !== 'pass') return '请先完成桌面 PowerPoint 打开检查，再记录后续步骤通过。';
  if (checks.reopened === 'pass' && checks.saved !== 'pass') return '请先完成保存检查，再记录重新打开通过。';
  if (chartCount === 0 && checks.native_chart_edited === 'pass') return '此版本没有原生图表，请选择“不适用”。';
  if (chartCount > 0 && checks.native_chart_edited === 'not_applicable') return '此版本含原生图表，需要实际检查其可编辑性。';
  return '';
}
