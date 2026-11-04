import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, safeError } from '../api';
import { acceptanceClient, assertSnapshot, rasterPreviewUrl, renderedPptxPreviewUrl } from './client';
import { powerpointDraft, powerpointValidation, visualDraft } from './drafts';
import type { AcceptanceBinding, AcceptanceClient, AcceptanceCommand, AcceptancePage, AcceptanceRecord, AcceptanceSnapshot, CheckVerdict, IssueCategory, IssueSeverity, PowerPointReview, PublicationRecord, RenderedPptxPage, VisualReview } from './contracts';
import './acceptance.css';

const reviewLabels = { pending: '待检查', passed: '已通过', needs_changes: '需修改' };
const categories: Record<IssueCategory, string> = { layout: '版式', text: '文字', chart: '图表', image: '图片', other: '其他' };
const severities: Record<IssueSeverity, string> = { minor: '轻微', major: '重要', blocking: '阻断' };
const checkLabels = { opened: '在桌面 PowerPoint 中打开', content_edited: '实际编辑文字或其他可编辑内容', native_chart_edited: '编辑原生图表并核对数据', saved: '保存修改后的演示稿', reopened: '关闭后重新打开并核对结果' };
const kindLabels = { visual_review: '逐页视觉检查', powerpoint_review: 'PowerPoint 桌面检查', revoke: '撤销记录', publish: '正式发布' };
const reasons: Record<string, string> = {
  independent_postflight_integration_pending: '独立程序验收尚待完成；后台空闲后检查，可稍后刷新查看。',
  independent_programmatic_pending: '独立程序验收尚未完成。',
  independent_programmatic_needs_changes: '独立程序检查发现问题，需要修复后重新核验。',
  independent_export_build_contract: '完整导出核验尚未完成。',
  independent_postflight_and_delivery: '独立导出与交付检查尚未完成。',
  final_pptx_render: '最终 PPTX 的独立渲染尚未完成。',
  independent_final_pptx_render: '最终 PPTX 的独立渲染尚未完成。',
  independent_final_pptx_rendering: '最终 PPTX 的独立渲染尚未完成。',
  postflight_warnings_review: '质量检查提示仍需复核。',
  source_stale: '资料或设置已经变化，请使用当前来源重新生成并检查。',
  active_job_exists: '项目仍有正在执行的任务，请等待完成后重新读取记录。',
  publication_active_job: '项目仍有正在执行的任务，请等待完成后重新读取记录。',
  visual_review_pending: '逐页视觉检查尚未完成。', powerpoint_review_pending: 'PowerPoint 桌面检查尚未完成。',
  visual_review_needs_changes: '视觉检查发现待修改的问题。', powerpoint_review_needs_changes: '桌面检查发现待修改的问题。',
};
const gateLabels: Record<string, string> = {
  independent_export_build_contract: '完整导出核验', independent_postflight_and_delivery: '独立导出与交付检查',
  independent_raster_regeneration: '源文件预览重建', independent_native_inspection: '原生文字、形状与图表检查',
  independent_raster_regeneration_and_binding: '源文件预览重建与版本核对',
  independent_delivery_audit: '交付文件完整性检查',
  independent_native_ooxml_and_chart_data: '原生文字、形状与图表数据检查',
  independent_postflight_source_and_quality: '源文件与质量检查',
  final_pptx_render: '最终 PPTX 独立渲染', independent_final_pptx_render: '最终 PPTX 独立渲染',
  independent_final_pptx_rendering: '最终 PPTX 独立渲染',
  postflight_warnings_review: '质量提示复核',
};

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return '验收记录或版本来源已变化，请重新读取记录后检查并提交。当前草稿未保存。';
    if (error.status === 422) return '检查结果未能保存，请核对问题说明、PowerPoint 版本和各项检查顺序。';
  }
  return safeError(error);
}

export interface SavedReviewFeedback { binding: AcceptanceBinding; reviewRevision: number; pages: number[]; feedback: string; items?: { page: number; feedback: string }[] }
export interface AcceptancePanelProps {
  projectId: string;
  versionId: string;
  client?: AcceptanceClient;
  onSnapshot?: (snapshot: AcceptanceSnapshot | null) => void;
  onUseFeedback?: (feedback: SavedReviewFeedback) => void;
  onPublished?: (snapshot: AcceptanceSnapshot) => void;
}
/** A keyed boundary discards drafts and suppresses in-flight responses when the selected version changes. */
export function AcceptancePanel({ projectId, versionId, client = acceptanceClient, onSnapshot, onUseFeedback, onPublished }: AcceptancePanelProps) {
  return <AcceptanceWorkspace key={JSON.stringify([projectId, versionId])} projectId={projectId} versionId={versionId} client={client} onSnapshot={onSnapshot} onUseFeedback={onUseFeedback} onPublished={onPublished} />;
}

function AcceptanceWorkspace({ projectId, versionId, client, onSnapshot, onUseFeedback, onPublished }: AcceptancePanelProps & { client: AcceptanceClient }) {
  const [snapshot, setSnapshot] = useState<AcceptanceSnapshot | null>(null);
  const [visual, setVisual] = useState<VisualReview | null>(null);
  const [powerpoint, setPowerpoint] = useState<PowerPointReview | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [publishConfirmed, setPublishConfirmed] = useState(false);
  const [visualConfirmed, setVisualConfirmed] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState('');
  const [selectedPage, setSelectedPage] = useState(0);
  const [revokeId, setRevokeId] = useState('');
  const [revokeReason, setRevokeReason] = useState('');
  const generation = useRef(0);
  const busy = useRef(false);
  const mounted = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const snapshotRef = useRef<AcceptanceSnapshot | null>(null);
  const draftsRef = useRef({ visual, powerpoint });
  draftsRef.current = { visual, powerpoint };
  const snapshotObserver = useRef(onSnapshot); snapshotObserver.current = onSnapshot;

  const accept = useCallback((next: AcceptanceSnapshot, kind?: AcceptanceRecord['kind']) => {
    const previous = snapshotRef.current;
    const changed = !previous || JSON.stringify(previous.binding) !== JSON.stringify(next.binding);
    const visualDirty = previous && JSON.stringify(draftsRef.current.visual) !== JSON.stringify(visualDraft(previous));
    const powerpointDirty = previous && JSON.stringify(draftsRef.current.powerpoint) !== JSON.stringify(powerpointDraft(previous));
    if (changed || kind === 'visual_review' || (!kind && !visualDirty)) setVisual(visualDraft(next));
    if (changed || kind === 'powerpoint_review' || (!kind && !powerpointDirty)) setPowerpoint(powerpointDraft(next));
    if (changed) {
      setSelectedPage(0);
      if (previous && (visualDirty || powerpointDirty)) setNotice('版本来源已变化，旧来源的未保存草稿已清空，请重新检查。');
    } else if (!kind && (visualDirty || powerpointDirty)) setNotice('已重新读取服务端记录，未保存草稿已保留。请核对最新记录后再保存。');
    snapshotRef.current = next; setSnapshot(next); setPublishConfirmed(false); setVisualConfirmed(false); snapshotObserver.current?.(next);
  }, []);

  const load = useCallback(async () => {
    if (busy.current) return;
    controller.current?.abort();
    const abort = new AbortController(); controller.current = abort;
    const attempt = ++generation.current;
    setLoading(true); setError(null); setNotice('');
    try {
      const next = assertSnapshot(await client.load(projectId, versionId, abort.signal), projectId, versionId);
      if (mounted.current && generation.current === attempt) { accept(next); setSelectedPage(0); setRevokeId(''); setRevokeReason(''); }
    } catch (failure) {
      if (mounted.current && generation.current === attempt && !abort.signal.aborted) { setError(failure); snapshotObserver.current?.(null); }
    } finally { if (mounted.current && generation.current === attempt) setLoading(false); }
  }, [accept, client, projectId, versionId]);

  useEffect(() => {
    mounted.current = true; busy.current = false; setSaving(false); setPublishing(false); setPublishConfirmed(false); setVisualConfirmed(false);
    snapshotRef.current = null; setSnapshot(null); setVisual(null); setPowerpoint(null);
    snapshotObserver.current?.(null);
    void load();
    return () => { mounted.current = false; ++generation.current; controller.current?.abort(); };
  }, [load]);

  async function save(command: AcceptanceCommand) {
    if (busy.current || loading) return;
    busy.current = true; setSaving(true); setError(null); setNotice('');
    const attempt = generation.current;
    try {
      const next = assertSnapshot(await client.save(projectId, versionId, command), projectId, versionId);
      if (next.revision <= command.expected_revision) throw new ApiError('INVALID_RESPONSE');
      if (mounted.current && generation.current === attempt) {
        const affectedKind = command.kind === 'revoke' ? snapshotRef.current?.records.find(record => record.id === command.record_id)?.kind : command.kind;
        accept(next, affectedKind); setNotice(command.kind === 'revoke' ? '已保存撤销记录，历史检查仍可查看。' : '检查记录已保存至服务端。');
        setRevokeId(''); setRevokeReason('');
      }
    } catch (failure) { if (mounted.current && generation.current === attempt) { setError(failure); if (failure instanceof ApiError && [401, 403, 404, 409].includes(failure.status)) snapshotObserver.current?.(null); } }
    finally { if (mounted.current && generation.current === attempt) { busy.current = false; setSaving(false); } }
  }

  async function publish() {
    const current = snapshotRef.current;
    if (!current || !client.publish || busy.current || loading || error != null || !current.can_publish || !publishConfirmed || draftsDirty) return;
    busy.current = true; setSaving(true); setPublishing(true); setError(null); setNotice('');
    const attempt = generation.current;
    const command = { kind: 'publish' as const, expected_revision: current.revision, binding: current.binding, confirm: true as const };
    try {
      const next = assertSnapshot(await client.publish(projectId, versionId, command), projectId, versionId);
      if (next.revision <= command.expected_revision || !next.publication_authorized || next.publication?.status !== 'published') throw new ApiError('INVALID_RESPONSE');
      if (mounted.current && generation.current === attempt) {
        accept(next, 'publish'); setNotice('此版本已正式发布，发布决定和所依据的检查记录已保存。'); onPublished?.(next);
      }
    } catch (failure) {
      if (mounted.current && generation.current === attempt) { setError(failure); setPublishConfirmed(false); snapshotObserver.current?.(null); }
    } finally { if (mounted.current && generation.current === attempt) { busy.current = false; setSaving(false); setPublishing(false); } }
  }

  const blocked = loading || saving || error != null || !snapshot?.can_review;
  const page = snapshot?.pages[selectedPage];
  const renderedPage = snapshot?.programmatic?.rendered_pages?.find(item => item.number === page?.number);
  const commandBase = snapshot && { expected_revision: snapshot.revision, binding: snapshot.binding };
  const draftsDirty = !!snapshot && (visualConfirmed || JSON.stringify(visual) !== JSON.stringify(visualDraft(snapshot)) || JSON.stringify(powerpoint) !== JSON.stringify(powerpointDraft(snapshot)));
  const powerpointError = powerpoint && snapshot ? powerpointValidation(powerpoint, snapshot.native_chart_count) : '';
  // The latest saved observation owns the conclusion; revocation never revives
  // an older record. Unsaved form edits are deliberately excluded from feedback.
  const latestVisual = snapshot?.records.slice().reverse().find(record => record.kind === 'visual_review');
  const savedChanges = snapshot?.can_review && latestVisual && !latestVisual.stale && !latestVisual.revoked && JSON.stringify(latestVisual.binding) === JSON.stringify(snapshot.binding)
    ? (latestVisual.payload as VisualReview).pages.filter(review => review.verdict === 'needs_changes' && review.issues.length > 0) : [];
  const useSavedFeedback = () => {
    if (!snapshot || error != null || !savedChanges.length || !onUseFeedback) return;
    onUseFeedback({ binding: snapshot.binding, reviewRevision: snapshot.revision, pages: savedChanges.map(review => review.number),
      feedback: savedChanges.map(review => `第 ${review.number} 页：${snapshot.pages.find(page => page.number === review.number)?.title ?? ''}\n${review.issues.map(issue => `- ${categories[issue.category]} / ${severities[issue.severity]}：${issue.description}`).join('\n')}`).join('\n\n'),
      items: savedChanges.map(review => ({ page: review.number, feedback: review.issues.map(issue => `${categories[issue.category]} / ${severities[issue.severity]}：${issue.description}`).join('\n') })) });
  };

  return <section className="ppt-acceptance" aria-label={`版本 ${versionId} 的验收工作台`}>
    <header className="acceptance-heading"><div><span className="acceptance-kicker">PREVIEW</span><h2>预览演示稿</h2><p>浏览页面，在下方意见清单中逐条写下想调整的地方。</p></div><button type="button" className="acceptance-secondary" onClick={() => void load()} disabled={loading || saving}>重新读取记录</button></header>
    <p className="acceptance-version">当前版本 <code>{versionId}</code> · 每次记录绑定此版本及其来源</p>
    {error != null && <div role="alert" className="acceptance-alert">{errorText(error)} {error instanceof ApiError && error.status === 401 && <a href="/oauth2/start">重新登录</a>}</div>}
    {notice && <p role="status" className="acceptance-notice">{notice}</p>}
    {loading && <p role="status">正在读取版本验收记录…</p>}
    {!loading && !snapshot && <p>暂时无法读取验收记录，请重试。</p>}
    {snapshot && visual && powerpoint && <>
      {snapshot.programmatic?.diagnostics && <section className="acceptance-layout-diagnostics" aria-label="文字排版检查发现的问题">
        <div><strong>{snapshot.programmatic.diagnostics.title}</strong><span>{snapshot.programmatic.diagnostics.total_issue_count} 项待调整</span></div>
        <p>{snapshot.programmatic.diagnostics.message}</p>
        <ul>{snapshot.programmatic.diagnostics.items.map((item, index) => <li key={`${item.page}-${item.shape_id}-${item.code}-${index}`}>
          <button type="button" className="acceptance-text-button" aria-label={`查看第 ${item.page} 页的问题 ${index + 1}`} onClick={() => setSelectedPage(item.page - 1)}>第 {item.page} 页</button>
          <div><strong>{item.message}</strong><small>{item.suggestion}</small></div>
        </li>)}</ul>
        {snapshot.programmatic.diagnostics.truncated && <p>当前展示前 {snapshot.programmatic.diagnostics.items.length} 项，共 {snapshot.programmatic.diagnostics.total_issue_count} 项。</p>}
        <p>{snapshot.programmatic.diagnostics.suggestion}</p>
      </section>}
      {snapshot.programmatic?.source_diagnostics && <section className="acceptance-layout-diagnostics" aria-label="源文件结构检查提示">
        <div><strong>源文件结构提示需要复核</strong><span>{snapshot.programmatic.source_diagnostics.items.reduce((count, item) => count + item.warning_count, snapshot.programmatic.source_diagnostics.project_warning_count)} 项提示</span></div>
        <p>源文件结构检查仍有提示，完整导出与交付检查尚未通过。结构提示不等于最终 PPTX 存在视觉错误，请结合预览核对。</p>
        {snapshot.programmatic.source_diagnostics.items.length > 0 && <ul>{snapshot.programmatic.source_diagnostics.items.map(item => <li key={item.page}>
          <button type="button" className="acceptance-text-button" aria-label={`查看第 ${item.page} 页的结构提示`} onClick={() => setSelectedPage(item.page - 1)}>第 {item.page} 页</button>
          <div><strong>{item.warning_count} 项结构提示</strong><small>请核对文字组织与分组是否符合原意；若需要调整，可在下方意见清单中说明。</small></div>
        </li>)}</ul>}
        {snapshot.programmatic.source_diagnostics.project_warning_count > 0 && <p>整份演示稿另有 {snapshot.programmatic.source_diagnostics.project_warning_count} 项结构提示，未关联到具体页码。</p>}
        <p>若预览正常但提示仍存在，请联系管理员复核；查看提示或切换页面不会保存验收结果或发起修改任务。</p>
      </section>}
      <section className="acceptance-main-preview" aria-label="演示稿预览">
        <div className="acceptance-review-layout"><nav className="acceptance-page-list" aria-label="选择预览页面">{snapshot.pages.map((item, index) => <button key={item.number} type="button" aria-current={index === selectedPage ? 'page' : undefined} onClick={() => setSelectedPage(index)}><span>{String(item.number).padStart(2, '0')}</span><span>{item.title}</span></button>)}</nav>
          {page && <div className="acceptance-page-workspace">{renderedPage ? <RenderedPptxPreview key={JSON.stringify([snapshot.binding, snapshot.programmatic?.receipt_sha256, renderedPage])} projectId={projectId} versionId={versionId} receipt={snapshot.programmatic?.receipt_sha256 ?? null} page={renderedPage} /> : <RasterPreview key={JSON.stringify([snapshot.binding, page.number, page.preview_sha256])} versionId={versionId} page={page} />}<h3>第 {page.number} 页 · {page.title}</h3><p className="acceptance-preview-origin">{renderedPage ? '最终 PPTX 渲染预览' : '源文件参考预览 · 最终 PPTX 渲染尚未就绪'}</p></div>}
        </div>
        <p className="acceptance-help">有问题直接添加一条意见，页码可选。浏览预览不会自动记录验收通过。</p>
      </section>
      {onUseFeedback && <div className="acceptance-save-row"><button type="button" className="acceptance-secondary" disabled={loading || saving || error != null || !savedChanges.length} onClick={useSavedFeedback}>将已保存问题带入修改</button><span>带入此版本仍有效的历史问题，核对后统一提交。</span></div>}
      {!snapshot.can_review && <p role="status" className="acceptance-alert">当前版本来源已失效或暂不具备检查条件。请重新读取记录；历史记录保留供核对。</p>}
      <details className="acceptance-formal"><summary>验收与发布</summary><p className="acceptance-help">需要正式发布时，再确认整份演示稿的视觉效果，并记录桌面 PowerPoint 的实际操作。修改意见可直接在下方提交。</p>
      <div className="acceptance-status-grid" aria-label="服务端验收状态">
        <Status title="独立程序验收" value={reviewLabels[snapshot.programmatic_status]} />
        <Status title="逐页视觉" value={reviewLabels[snapshot.visual_status]} />
        <Status title="PowerPoint 桌面" value={reviewLabels[snapshot.powerpoint_status]} />
        <Status title="完整验收" value={snapshot.acceptance_status === 'passed' ? '已通过' : snapshot.acceptance_status === 'stale' ? '来源已变化' : snapshot.acceptance_status === 'unreviewable' ? '暂不可检查' : '待完成'} />
      </div>
      <div className="acceptance-notice"><strong>{snapshot.publication_authorized ? '此版本已正式发布' : snapshot.acceptance_status === 'passed' ? '完整验收已通过，请确认发布' : '完整验收待完成'}</strong><p>{snapshot.publication_authorized ? '发布记录绑定此版本、独立程序证据和人工检查记录，可在下方核对。' : '独立程序检查、逐页视觉检查和桌面实际操作均由服务端核对。保存检查记录后，还需明确确认发布。'}</p>{snapshot.pending_reasons.map(reason => <p key={reason}>{reasons[reason] ?? '仍有检查未完成，请核对下方验收状态；若原因不明，请联系管理员。'}</p>)}</div>
      <details className="acceptance-binding"><summary>查看版本与来源绑定</summary><dl><dt>项目修订</dt><dd>{snapshot.source_revision}</dd><dt>验收记录修订</dt><dd>{snapshot.revision}</dd><dt>执行后端</dt><dd>{snapshot.binding.runtime.engine} · {snapshot.binding.runtime.profile}</dd><dt>PPTX 指纹</dt><dd><code>{snapshot.binding.pptx_sha256}</code></dd><dt>输入快照</dt><dd><code>{snapshot.binding.input_snapshot_sha256}</code></dd><dt>源文件指纹</dt><dd><code>{snapshot.binding.source_fingerprint_sha256}</code></dd><dt>产物清单</dt><dd><code>{snapshot.binding.manifest_sha256}</code></dd><dt>检查证据</dt><dd><code>{snapshot.binding.evidence_id} · {snapshot.binding.evidence_sha256}</code></dd></dl></details>

      <section className="acceptance-programmatic" aria-label="独立程序检查证据"><div className="acceptance-section-heading"><div><span className="acceptance-kicker">INDEPENDENT CHECKS</span><h3>最终文件核验</h3></div><span>{reviewLabels[snapshot.programmatic_status]}</span></div><p className="acceptance-help">独立检查直接核对当前 PPTX 与来源。PPTX 渲染用于核对导出结果；桌面编辑、保存和重开仍需实际操作。</p>
        {snapshot.programmatic ? <><p>{snapshot.programmatic.detail}</p>{snapshot.programmatic.renderer && <p className="acceptance-help">PPTX 渲染环境：{snapshot.programmatic.renderer}</p>}{snapshot.programmatic.completed_gates.length > 0 && <ul className="acceptance-gates" aria-label="已完成的独立检查">{snapshot.programmatic.completed_gates.map(gate => <li key={gate}><span aria-hidden="true">✓</span> {gateLabels[gate] ?? '独立程序检查项已完成'}</li>)}</ul>}{snapshot.programmatic.pending_gates.length > 0 && <ul className="acceptance-gates acceptance-gates-pending" aria-label="待完成的独立检查">{snapshot.programmatic.pending_gates.map(gate => <li key={gate}><span aria-hidden="true">○</span> {gateLabels[gate] ?? '独立程序检查项待完成'}</li>)}</ul>}{snapshot.programmatic.receipt_sha256 && <details className="acceptance-binding"><summary>查看独立检查凭据</summary><code>{snapshot.programmatic.receipt_sha256}</code></details>}</> : <p className="acceptance-help">尚无完整独立验收凭据，当前仍可保存人工检查记录。</p>}
      </section>

      <form onSubmit={event => { event.preventDefault(); if (commandBase && !blocked && visualConfirmed && snapshot.pages.length > 0) void save({ ...commandBase, kind: 'visual_review', pages: snapshot.pages.map(item => ({ number: item.number, preview_sha256: item.preview_sha256, verdict: 'pass', issues: [] })), note: visual.note }); }}>
        <div className="acceptance-section-heading"><div><span className="acceptance-kicker">01 / VISUAL REVIEW</span><h3>整份视觉确认</h3></div><span>{reviewLabels[snapshot.visual_status]}</span></div>
        <p className="acceptance-help">发现问题时先填写意见并修改。确认整份演示稿已符合要求后，只需一次明确确认；此操作不会提交修改或替代桌面检查。</p>
        <fieldset disabled={blocked} className="acceptance-fieldset"><legend className="acceptance-sr-only">整份视觉确认表单</legend>
          <label className="acceptance-confirm"><input type="checkbox" checked={visualConfirmed} onChange={event => setVisualConfirmed(event.target.checked)} /><span>我已检查此版本全部 {snapshot.pages.length} 页，确认视觉效果通过。</span></label>
          <label>视觉检查备注<textarea rows={2} maxLength={4000} value={visual.note} onChange={event => setVisual({ ...visual, note: event.target.value })} /></label>
          <div className="acceptance-save-row"><button className="acceptance-primary" disabled={blocked || !visualConfirmed || snapshot.pages.length === 0}>确认视觉检查通过</button><span>仅在明确确认并保存后，才记录整份视觉检查通过。</span></div>
        </fieldset>
      </form>
      <form onSubmit={event => { event.preventDefault(); if (commandBase && !blocked && !powerpointError) void save({ ...commandBase, kind: 'powerpoint_review', ...powerpoint }); }}>
        <div className="acceptance-section-heading"><div><span className="acceptance-kicker">02 / DESKTOP CHECK</span><h3>PowerPoint 桌面检查</h3></div></div><p className="acceptance-help">请用此版本的 PPTX 在桌面 PowerPoint 实际操作后逐项填写。浏览器预览无法验证原生编辑。</p>
        <fieldset disabled={blocked} className="acceptance-fieldset"><legend className="acceptance-sr-only">PowerPoint 桌面检查表单</legend><div className="acceptance-desktop-fields"><label>PowerPoint 版本<input maxLength={200} value={powerpoint.application_version} placeholder="例如：Microsoft 365，版本 2509" onChange={event => setPowerpoint({ ...powerpoint, application_version: event.target.value })} /></label><label>操作系统<input maxLength={100} value={powerpoint.platform} placeholder="例如：Windows 11" onChange={event => setPowerpoint({ ...powerpoint, platform: event.target.value })} /></label></div>
          <div className="acceptance-checks">{Object.entries(checkLabels).map(([key, title], index) => { const name = key as keyof PowerPointReview['checks']; return <label key={key}><span><b>{index + 1}</b>{title}</span><select value={powerpoint.checks[name]} onChange={event => setPowerpoint({ ...powerpoint, checks: { ...powerpoint.checks, [name]: event.target.value as CheckVerdict | 'not_applicable' } })}><option value="pending">未检查</option><option value="pass">已操作，通过</option><option value="fail">已操作，发现问题</option>{name === 'native_chart_edited' && snapshot.native_chart_count === 0 && <option value="not_applicable">不适用：此版本无原生图表</option>}</select></label>; })}</div>
          <p className="acceptance-help">服务端记录此版本有 {snapshot.native_chart_count} 个原生图表。没有图表时也需要主动选择“不适用”。原生箭头可编辑，并不自动证明连接线会随模块移动；请按实际使用需要核对。</p>
          {powerpointError && <p className="acceptance-help">{powerpointError}</p>}<label>桌面检查备注<textarea rows={3} maxLength={4000} value={powerpoint.note} placeholder="记录问题、复现步骤或保存重开后的核对结果。" onChange={event => setPowerpoint({ ...powerpoint, note: event.target.value })} /></label><div className="acceptance-save-row"><button className="acceptance-primary" disabled={blocked || !!powerpointError}>保存桌面检查记录</button><span>缺少的步骤仍为待检查。</span></div>
        </fieldset>
      </form>

      {client.publish && <section className="acceptance-publication" aria-label="正式发布"><div className="acceptance-section-heading"><div><span className="acceptance-kicker">03 / PUBLICATION</span><h3>确认正式发布</h3></div><span>{snapshot.publication_authorized ? '已发布' : '待发布'}</span></div>
        {snapshot.publication_authorized ? <p role="status" className="acceptance-notice">已于 {formatTime(snapshot.publication!.published_at!)} 发布此版本。发布记录保留在下方历史中。</p> : <><p className="acceptance-help">发布将保留当前审核稿及检查记录，并把这一版标记为可正式使用。此操作不会重新生成内容或调用模型。</p>{!snapshot.can_publish && <p className="acceptance-help">完成独立程序验收、全部页面检查和 PowerPoint 实际操作后，请重新读取记录；服务端确认具备条件后才可发布。</p>}{draftsDirty && <p className="acceptance-alert" role="status">有尚未保存的检查结果，请先保存并核对服务端结论，再确认发布。</p>}<label className="acceptance-confirm"><input type="checkbox" checked={publishConfirmed} disabled={loading || saving || error != null || !snapshot.can_publish || draftsDirty} onChange={event => setPublishConfirmed(event.target.checked)} /><span>我已核对此版本的完整验收记录，确认将它正式发布。</span></label><button type="button" className="acceptance-primary" disabled={loading || saving || error != null || !snapshot.can_publish || !publishConfirmed || draftsDirty} onClick={() => void publish()}>{publishing ? '正在保存发布决定…' : '确认发布此版本'}</button></>}
      </section>}

      <section aria-label="验收历史" className="acceptance-history"><div className="acceptance-section-heading"><div><span className="acceptance-kicker">04 / SAVED HISTORY</span><h3>服务端检查记录</h3></div><span>{snapshot.records.length} 条</span></div><p className="acceptance-help">记录保留原始版本和来源。撤销最新检查后，该项恢复待检查，不会沿用更早的通过结果；发布决定失效时需重新核验并确认。</p>
        {!snapshot.records.length && <p>此版本还没有保存的检查记录。</p>}
        {[...snapshot.records].reverse().map(record => <article className="acceptance-history-row" key={record.id}><div><strong>{kindLabels[record.kind]}</strong><span className="acceptance-history-state">{record.revoked ? '已撤销' : record.stale ? '来源已失效' : '已记录'}</span></div><p><time dateTime={record.created_at}>{formatTime(record.created_at)}</time> · 检查人 {record.reviewer.sub} · 修订 {record.revision}</p><details><summary>查看记录内容与来源</summary><RecordDetails record={record} /><dl><dt>记录编号</dt><dd><code>{record.id}</code></dd><dt>版本</dt><dd><code>{record.binding.version_id}</code></dd><dt>PPTX 指纹</dt><dd><code>{record.binding.pptx_sha256}</code></dd></dl></details>
          {snapshot.can_revoke && record.kind !== 'revoke' && !record.revoked && <button type="button" className="acceptance-text-button" disabled={loading || saving} onClick={() => { setRevokeId(record.id); setRevokeReason(''); }}>撤销此记录</button>}
          {revokeId === record.id && <form className="acceptance-revoke" onSubmit={event => { event.preventDefault(); if (commandBase && revokeReason.trim()) void save({ ...commandBase, kind: 'revoke', record_id: record.id, reason: revokeReason.trim() }); }}><label>撤销原因<textarea required maxLength={2000} value={revokeReason} onChange={event => setRevokeReason(event.target.value)} /></label><div className="acceptance-save-row"><button className="acceptance-primary" disabled={loading || saving || !revokeReason.trim()}>确认撤销记录</button><button type="button" className="acceptance-secondary" disabled={saving} onClick={() => setRevokeId('')}>取消</button></div></form>}
        </article>)}
      </section>
      </details>
      {saving && <p role="status">{publishing ? '正在核对验收记录并保存发布决定…' : '正在保存检查记录…'}</p>}
    </>}
  </section>;
}

function Status({ title, value }: { title: string; value: string }) { return <div><span>{title}</span><strong>{value}</strong></div>; }
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? '时间待核对' : date.toLocaleString('zh-CN'); }

function RenderedPptxPreview({ projectId, versionId, receipt, page }: { projectId: string; versionId: string; receipt: string | null; page: RenderedPptxPage }) {
  const [failed, setFailed] = useState(false); const [loaded, setLoaded] = useState(false); const [attempt, setAttempt] = useState(0);
  const url = renderedPptxPreviewUrl(projectId, versionId, page.number, receipt, page.preview_url);
  return <div className="acceptance-preview">{!url ? <p role="status">此页 PPTX 渲染地址不可用，请重新读取记录。</p> : failed ? <div role="status"><p>第 {page.number} 页 PPTX 渲染图暂不可用；若检查证据已更新，请重新读取记录。</p><button type="button" className="acceptance-secondary" onClick={() => { setFailed(false); setLoaded(false); setAttempt(value => value + 1); }}>重试 PPTX 渲染图</button></div> : <>{!loaded && <span role="status">正在读取第 {page.number} 页 PPTX 渲染图…</span>}<img key={attempt} src={url} alt={`最终 PPTX 第 ${page.number} 页独立渲染`} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} /></>}</div>;
}

function RasterPreview({ versionId, page }: { versionId: string; page: AcceptancePage }) {
  const [failed, setFailed] = useState(false); const [loaded, setLoaded] = useState(false); const [attempt, setAttempt] = useState(0);
  const url = rasterPreviewUrl(versionId, page.number, page.preview_url);
  return <div className="acceptance-preview">{!url ? <p role="status">此页预览地址不可用，请重新读取记录。</p> : failed ? <div role="status"><p>第 {page.number} 页预览暂不可用。</p><button type="button" className="acceptance-secondary" onClick={() => { setFailed(false); setLoaded(false); setAttempt(value => value + 1); }}>重试预览</button></div> : <>{!loaded && <span role="status">正在读取第 {page.number} 页预览…</span>}<img key={attempt} src={url} alt={`第 ${page.number} 页静态 PNG 预览：${page.title}`} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} /></>}</div>;
}

function RecordDetails({ record }: { record: AcceptanceRecord }) {
  if (record.kind === 'revoke') { const value = record.payload as { record_id: string; reason: string }; return <p>撤销记录 {value.record_id}：{value.reason}</p>; }
  if (record.kind === 'publish') { const value = record.payload as PublicationRecord; return <dl><dt>视觉记录</dt><dd><code>{value.visual_record_id}</code></dd><dt>桌面检查记录</dt><dd><code>{value.powerpoint_record_id}</code></dd><dt>独立程序凭据</dt><dd><code>{value.programmatic_receipt_sha256}</code></dd></dl>; }
  if (record.kind === 'visual_review') {
    const value = record.payload as VisualReview;
    return <><ul>{value.pages?.map(page => <li key={page.number}>第 {page.number} 页：{page.verdict === 'pass' ? '检查通过' : page.verdict === 'needs_changes' ? '需要修改' : '待检查'}{page.issues?.map((issue, index) => <p key={index}>{categories[issue.category]} / {severities[issue.severity]}：{issue.description}</p>)}</li>)}</ul>{value.note && <p>{value.note}</p>}</>;
  }
  const value = record.payload as PowerPointReview;
  return <><p>{value.application_version || '未填写 PowerPoint 版本'} · {value.platform || '未填写操作系统'}</p><ul>{Object.entries(checkLabels).map(([key, title]) => <li key={key}>{title}：{({ pending: '未检查', pass: '通过', fail: '发现问题', not_applicable: '不适用' })[value.checks?.[key as keyof PowerPointReview['checks']] ?? 'pending']}</li>)}</ul>{value.note && <p>{value.note}</p>}</>;
}
