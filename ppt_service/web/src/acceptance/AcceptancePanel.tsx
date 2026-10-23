import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, safeError } from '../api';
import { acceptanceClient, assertSnapshot, rasterPreviewUrl } from './client';
import { powerpointDraft, powerpointValidation, visualDraft } from './drafts';
import type { AcceptanceClient, AcceptanceCommand, AcceptancePage, AcceptanceRecord, AcceptanceSnapshot, CheckVerdict, IssueCategory, IssueSeverity, PageReview, PageVerdict, PowerPointReview, VisualReview } from './contracts';
import './acceptance.css';

const reviewLabels = { pending: '待检查', passed: '已通过', needs_changes: '需修改' };
const categories: Record<IssueCategory, string> = { layout: '版式', text: '文字', chart: '图表', image: '图片', other: '其他' };
const severities: Record<IssueSeverity, string> = { minor: '轻微', major: '重要', blocking: '阻断' };
const checkLabels = { opened: '在桌面 PowerPoint 中打开', content_edited: '实际编辑文字或其他可编辑内容', native_chart_edited: '编辑原生图表并核对数据', saved: '保存修改后的演示稿', reopened: '关闭后重新打开并核对结果' };
const kindLabels = { visual_review: '逐页视觉检查', powerpoint_review: 'PowerPoint 桌面检查', revoke: '撤销记录' };
const reasons: Record<string, string> = {
  independent_postflight_integration_pending: '独立程序检查尚未接入，完整验收保持待完成。',
  visual_review_pending: '逐页视觉检查尚未完成。', powerpoint_review_pending: 'PowerPoint 桌面检查尚未完成。',
  visual_review_needs_changes: '视觉检查发现待修改的问题。', powerpoint_review_needs_changes: '桌面检查发现待修改的问题。',
};

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return '验收记录或版本来源已变化，请重新读取记录后检查并提交。当前草稿未保存。';
    if (error.status === 422) return '检查结果未能保存，请核对问题说明、PowerPoint 版本和各项检查顺序。';
  }
  return safeError(error);
}

export interface AcceptancePanelProps { projectId: string; versionId: string; client?: AcceptanceClient }
/** A keyed boundary discards drafts and suppresses in-flight responses when the selected version changes. */
export function AcceptancePanel({ projectId, versionId, client = acceptanceClient }: AcceptancePanelProps) {
  return <AcceptanceWorkspace key={JSON.stringify([projectId, versionId])} projectId={projectId} versionId={versionId} client={client} />;
}

function AcceptanceWorkspace({ projectId, versionId, client }: Required<AcceptancePanelProps>) {
  const [snapshot, setSnapshot] = useState<AcceptanceSnapshot | null>(null);
  const [visual, setVisual] = useState<VisualReview | null>(null);
  const [powerpoint, setPowerpoint] = useState<PowerPointReview | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState('');
  const [selectedPage, setSelectedPage] = useState(0);
  const [revokeId, setRevokeId] = useState('');
  const [revokeReason, setRevokeReason] = useState('');
  const [loadedPreviews, setLoadedPreviews] = useState<Record<string, boolean>>({});
  const generation = useRef(0);
  const busy = useRef(false);
  const mounted = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const snapshotRef = useRef<AcceptanceSnapshot | null>(null);
  const draftsRef = useRef({ visual, powerpoint });
  draftsRef.current = { visual, powerpoint };

  const accept = useCallback((next: AcceptanceSnapshot, kind?: AcceptanceCommand['kind']) => {
    const previous = snapshotRef.current;
    const changed = !previous || JSON.stringify(previous.binding) !== JSON.stringify(next.binding);
    const visualDirty = previous && JSON.stringify(draftsRef.current.visual) !== JSON.stringify(visualDraft(previous));
    const powerpointDirty = previous && JSON.stringify(draftsRef.current.powerpoint) !== JSON.stringify(powerpointDraft(previous));
    if (changed || kind === 'visual_review' || (!kind && !visualDirty)) setVisual(visualDraft(next));
    if (changed || kind === 'powerpoint_review' || (!kind && !powerpointDirty)) setPowerpoint(powerpointDraft(next));
    if (changed) {
      setSelectedPage(0); setLoadedPreviews({});
      if (previous && (visualDirty || powerpointDirty)) setNotice('版本来源已变化，旧来源的未保存草稿已清空，请重新检查。');
    } else if (!kind && (visualDirty || powerpointDirty)) setNotice('已重新读取服务端记录，未保存草稿已保留。请核对最新记录后再保存。');
    snapshotRef.current = next; setSnapshot(next);
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
      if (mounted.current && generation.current === attempt && !abort.signal.aborted) setError(failure);
    } finally { if (mounted.current && generation.current === attempt) setLoading(false); }
  }, [accept, client, projectId, versionId]);

  useEffect(() => {
    mounted.current = true; busy.current = false; setSaving(false);
    snapshotRef.current = null; setSnapshot(null); setVisual(null); setPowerpoint(null); setLoadedPreviews({});
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
    } catch (failure) { if (mounted.current && generation.current === attempt) setError(failure); }
    finally { if (mounted.current && generation.current === attempt) { busy.current = false; setSaving(false); } }
  }

  const blocked = loading || saving || !snapshot?.can_review;
  const page = snapshot?.pages[selectedPage];
  const pageReview = visual?.pages[selectedPage];
  const commandBase = snapshot && { expected_revision: snapshot.revision, binding: snapshot.binding };
  function updatePage(value: PageReview) { setVisual(old => old && { ...old, pages: old.pages.map(p => p.number === value.number ? value : p) }); }
  const invalidIssues = visual?.pages.some(p => (p.verdict === 'pass' && p.issues.length > 0) || (p.verdict === 'needs_changes' && !p.issues.length) || p.issues.some(issue => !issue.description.trim()));
  const powerpointError = powerpoint && snapshot ? powerpointValidation(powerpoint, snapshot.native_chart_count) : '';
  const currentPreviewLoaded = page && loadedPreviews[`${page.number}:${page.preview_sha256}`] && !!rasterPreviewUrl(versionId, page.number, page.preview_url);
  const unseenPassingPages = visual?.pages.some(review => review.verdict === 'pass' && !loadedPreviews[`${review.number}:${review.preview_sha256}`]);

  return <section className="ppt-acceptance" aria-label={`版本 ${versionId} 的验收工作台`}>
    <header className="acceptance-heading"><div><span className="acceptance-kicker">VERSION REVIEW</span><h2>逐页检查与验收记录</h2><p>查看预览、记录问题，再在桌面 PowerPoint 中验证可编辑性。</p></div><button type="button" className="acceptance-secondary" onClick={() => void load()} disabled={loading || saving}>重新读取记录</button></header>
    <p className="acceptance-version">当前版本 <code>{versionId}</code> · 每次记录绑定此版本及其来源</p>
    {error != null && <div role="alert" className="acceptance-alert">{errorText(error)} {error instanceof ApiError && error.status === 401 && <a href="/oauth2/start">重新登录</a>}</div>}
    {notice && <p role="status" className="acceptance-notice">{notice}</p>}
    {loading && <p role="status">正在读取版本验收记录…</p>}
    {!loading && !snapshot && <p>暂时无法读取验收记录，请重试。</p>}
    {snapshot && visual && powerpoint && <>
      <div className="acceptance-status-grid" aria-label="服务端验收状态">
        <Status title="逐页视觉" value={reviewLabels[snapshot.visual_status]} />
        <Status title="PowerPoint 桌面" value={reviewLabels[snapshot.powerpoint_status]} />
        <Status title="人工检查" value={reviewLabels[snapshot.human_status]} />
        <Status title="完整验收" value={snapshot.acceptance_status === 'stale' ? '来源已变化' : snapshot.acceptance_status === 'unreviewable' ? '暂不可检查' : '待完成'} />
      </div>
      <div className="acceptance-notice"><strong>完整验收待完成</strong><p>人工记录由服务端保存并判定。程序检查尚未接入，勾选桌面检查不会使完整验收自动通过。</p>{snapshot.pending_reasons.filter(reason => reasons[reason] && reason !== 'independent_postflight_integration_pending').map(reason => <p key={reason}>{reasons[reason]}</p>)}</div>
      {!snapshot.can_review && <p role="status" className="acceptance-alert">当前版本来源已失效或暂不具备检查条件。请重新读取记录；历史记录保留供核对。</p>}
      <details className="acceptance-binding"><summary>查看版本与来源绑定</summary><dl><dt>项目修订</dt><dd>{snapshot.source_revision}</dd><dt>验收记录修订</dt><dd>{snapshot.revision}</dd><dt>执行后端</dt><dd>{snapshot.binding.runtime.engine} · {snapshot.binding.runtime.profile}</dd><dt>PPTX 指纹</dt><dd><code>{snapshot.binding.pptx_sha256}</code></dd><dt>输入快照</dt><dd><code>{snapshot.binding.input_snapshot_sha256}</code></dd><dt>源文件指纹</dt><dd><code>{snapshot.binding.source_fingerprint_sha256}</code></dd><dt>产物清单</dt><dd><code>{snapshot.binding.manifest_sha256}</code></dd><dt>检查证据</dt><dd><code>{snapshot.binding.evidence_id} · {snapshot.binding.evidence_sha256}</code></dd></dl></details>

      <form onSubmit={event => { event.preventDefault(); if (commandBase && !blocked && !invalidIssues && !unseenPassingPages) void save({ ...commandBase, kind: 'visual_review', ...visual }); }}>
        <div className="acceptance-section-heading"><div><span className="acceptance-kicker">01 / VISUAL REVIEW</span><h3>逐页视觉检查</h3></div><span>{snapshot.pages.length} 页</span></div>
        <p className="acceptance-help">预览是静态 PNG。请检查文字、版式、图片和图表；实际字体及编辑效果需在 PowerPoint 中确认。</p>
        <fieldset className="acceptance-fieldset"><legend className="acceptance-sr-only">逐页视觉检查表单</legend>
          <div className="acceptance-review-layout"><nav className="acceptance-page-list" aria-label="选择检查页面">{snapshot.pages.map((item, index) => <button key={item.number} type="button" aria-current={index === selectedPage ? 'page' : undefined} onClick={() => setSelectedPage(index)}><span>{String(item.number).padStart(2, '0')}</span><span>{item.title}<small>{visual.pages[index]?.verdict === 'pass' ? '检查通过' : visual.pages[index]?.verdict === 'needs_changes' ? '需修改' : '待检查'}</small></span></button>)}</nav>
            {page && pageReview && <div className="acceptance-page-workspace"><RasterPreview key={JSON.stringify([snapshot.binding, page.number, page.preview_sha256])} versionId={versionId} page={page} onAvailability={available => setLoadedPreviews(old => ({ ...old, [`${page.number}:${page.preview_sha256}`]: available }))} /><h4>第 {page.number} 页 · {page.title}</h4>
              <label>第 {page.number} 页检查结论<select disabled={blocked} value={pageReview.verdict} onChange={event => { if (event.target.value !== "pass" || currentPreviewLoaded) updatePage({ ...pageReview, verdict: event.target.value as PageVerdict }); }}><option value="pending">待检查</option><option value="pass" disabled={!currentPreviewLoaded}>检查通过</option><option value="needs_changes">需要修改</option></select></label>
              <div className="acceptance-issues-heading"><strong>问题记录</strong><button type="button" className="acceptance-secondary" disabled={blocked || pageReview.issues.length >= 50} onClick={() => updatePage({ ...pageReview, verdict: 'needs_changes', issues: [...pageReview.issues, { category: 'layout', severity: 'major', description: '' }] })}>添加问题</button></div>
              {!pageReview.issues.length && <p className="acceptance-help">尚无问题记录。完成检查后，请主动选择本页结论。</p>}
              {pageReview.issues.map((issue, index) => <div key={index} className="acceptance-issue"><div className="acceptance-issue-fields"><label>问题 {index + 1} 类型<select disabled={blocked} value={issue.category} onChange={event => updatePage({ ...pageReview, issues: pageReview.issues.map((current, i) => i === index ? { ...current, category: event.target.value as IssueCategory } : current) })}>{Object.entries(categories).map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label><label>问题 {index + 1} 严重程度<select disabled={blocked} value={issue.severity} onChange={event => updatePage({ ...pageReview, issues: pageReview.issues.map((current, i) => i === index ? { ...current, severity: event.target.value as IssueSeverity } : current) })}>{Object.entries(severities).map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label></div><label>问题 {index + 1} 说明<textarea disabled={blocked} rows={3} maxLength={2000} required value={issue.description} placeholder="例如：图表右侧标签被裁切，请增加右边距。" onChange={event => updatePage({ ...pageReview, issues: pageReview.issues.map((current, i) => i === index ? { ...current, description: event.target.value } : current) })} /></label><button type="button" className="acceptance-text-button" disabled={blocked} onClick={() => updatePage({ ...pageReview, verdict: 'pending', issues: pageReview.issues.filter((_, i) => i !== index) })}>移除问题 {index + 1}，重新检查本页</button></div>)}
            </div>}
          </div>
          <label>视觉检查备注<textarea disabled={blocked} rows={2} maxLength={4000} value={visual.note} onChange={event => setVisual({ ...visual, note: event.target.value })} /></label>
          {invalidIssues && <p className="acceptance-help">请填写每条问题说明；“需要修改”须有问题记录，“检查通过”须先解决所有问题。</p>}
          {unseenPassingPages && <p className="acceptance-help">请先加载并查看所有选择“检查通过”的页面预览，再保存视觉检查。</p>}<div className="acceptance-save-row"><button className="acceptance-primary" disabled={blocked || invalidIssues || unseenPassingPages}>保存视觉检查记录</button><span>允许保存未完成的检查。其余页面保持待检查。</span></div>
        </fieldset>
      </form>

      <form onSubmit={event => { event.preventDefault(); if (commandBase && !blocked && !powerpointError) void save({ ...commandBase, kind: 'powerpoint_review', ...powerpoint }); }}>
        <div className="acceptance-section-heading"><div><span className="acceptance-kicker">02 / DESKTOP CHECK</span><h3>PowerPoint 桌面检查</h3></div></div><p className="acceptance-help">请用此版本的 PPTX 在桌面 PowerPoint 实际操作后逐项填写。浏览器预览无法验证原生编辑。</p>
        <fieldset disabled={blocked} className="acceptance-fieldset"><legend className="acceptance-sr-only">PowerPoint 桌面检查表单</legend><div className="acceptance-desktop-fields"><label>PowerPoint 版本<input maxLength={200} value={powerpoint.application_version} placeholder="例如：Microsoft 365，版本 2509" onChange={event => setPowerpoint({ ...powerpoint, application_version: event.target.value })} /></label><label>操作系统<input maxLength={100} value={powerpoint.platform} placeholder="例如：Windows 11" onChange={event => setPowerpoint({ ...powerpoint, platform: event.target.value })} /></label></div>
          <div className="acceptance-checks">{Object.entries(checkLabels).map(([key, title], index) => { const name = key as keyof PowerPointReview['checks']; return <label key={key}><span><b>{index + 1}</b>{title}</span><select value={powerpoint.checks[name]} onChange={event => setPowerpoint({ ...powerpoint, checks: { ...powerpoint.checks, [name]: event.target.value as CheckVerdict | 'not_applicable' } })}><option value="pending">未检查</option><option value="pass">已操作，通过</option><option value="fail">已操作，发现问题</option>{name === 'native_chart_edited' && snapshot.native_chart_count === 0 && <option value="not_applicable">不适用：此版本无原生图表</option>}</select></label>; })}</div>
          <p className="acceptance-help">服务端记录此版本有 {snapshot.native_chart_count} 个原生图表。没有图表时也需要主动选择“不适用”。</p>
          {powerpointError && <p className="acceptance-help">{powerpointError}</p>}<label>桌面检查备注<textarea rows={3} maxLength={4000} value={powerpoint.note} placeholder="记录问题、复现步骤或保存重开后的核对结果。" onChange={event => setPowerpoint({ ...powerpoint, note: event.target.value })} /></label><div className="acceptance-save-row"><button className="acceptance-primary" disabled={blocked || !!powerpointError}>保存桌面检查记录</button><span>缺少的步骤仍为待检查。</span></div>
        </fieldset>
      </form>

      <section aria-label="验收历史" className="acceptance-history"><div className="acceptance-section-heading"><div><span className="acceptance-kicker">03 / SAVED HISTORY</span><h3>服务端检查记录</h3></div><span>{snapshot.records.length} 条</span></div><p className="acceptance-help">记录保留原始版本和来源。撤销最新记录后，该项恢复待检查，不会沿用更早的通过结果。</p>
        {!snapshot.records.length && <p>此版本还没有保存的检查记录。</p>}
        {[...snapshot.records].reverse().map(record => <article className="acceptance-history-row" key={record.id}><div><strong>{kindLabels[record.kind]}</strong><span className="acceptance-history-state">{record.revoked ? '已撤销' : record.stale ? '来源已失效' : '已记录'}</span></div><p><time dateTime={record.created_at}>{formatTime(record.created_at)}</time> · 检查人 {record.reviewer.sub} · 修订 {record.revision}</p><details><summary>查看记录内容与来源</summary><RecordDetails record={record} /><dl><dt>记录编号</dt><dd><code>{record.id}</code></dd><dt>版本</dt><dd><code>{record.binding.version_id}</code></dd><dt>PPTX 指纹</dt><dd><code>{record.binding.pptx_sha256}</code></dd></dl></details>
          {snapshot.can_revoke && record.kind !== 'revoke' && !record.revoked && <button type="button" className="acceptance-text-button" disabled={loading || saving} onClick={() => { setRevokeId(record.id); setRevokeReason(''); }}>撤销此记录</button>}
          {revokeId === record.id && <form className="acceptance-revoke" onSubmit={event => { event.preventDefault(); if (commandBase && revokeReason.trim()) void save({ ...commandBase, kind: 'revoke', record_id: record.id, reason: revokeReason.trim() }); }}><label>撤销原因<textarea required maxLength={2000} value={revokeReason} onChange={event => setRevokeReason(event.target.value)} /></label><div className="acceptance-save-row"><button className="acceptance-primary" disabled={loading || saving || !revokeReason.trim()}>确认撤销记录</button><button type="button" className="acceptance-secondary" disabled={saving} onClick={() => setRevokeId('')}>取消</button></div></form>}
        </article>)}
      </section>
      {saving && <p role="status">正在保存检查记录…</p>}
    </>}
  </section>;
}

function Status({ title, value }: { title: string; value: string }) { return <div><span>{title}</span><strong>{value}</strong></div>; }
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? '时间待核对' : date.toLocaleString('zh-CN'); }

function RasterPreview({ versionId, page, onAvailability }: { versionId: string; page: AcceptancePage; onAvailability: (available: boolean) => void }) {
  const [failed, setFailed] = useState(false); const [loaded, setLoaded] = useState(false); const [attempt, setAttempt] = useState(0);
  const url = rasterPreviewUrl(versionId, page.number, page.preview_url);
  return <div className="acceptance-preview">{!url ? <p role="status">此页预览地址不可用，请重新读取记录。</p> : failed ? <div role="status"><p>第 {page.number} 页预览暂不可用。</p><button type="button" className="acceptance-secondary" onClick={() => { setFailed(false); setLoaded(false); setAttempt(value => value + 1); }}>重试预览</button></div> : <>{!loaded && <span role="status">正在读取第 {page.number} 页预览…</span>}<img key={attempt} src={url} alt={`第 ${page.number} 页静态 PNG 预览：${page.title}`} onLoad={() => { setLoaded(true); onAvailability(true); }} onError={() => { setFailed(true); onAvailability(false); }} /></>}</div>;
}

function RecordDetails({ record }: { record: AcceptanceRecord }) {
  if (record.kind === 'revoke') { const value = record.payload as { record_id: string; reason: string }; return <p>撤销记录 {value.record_id}：{value.reason}</p>; }
  if (record.kind === 'visual_review') {
    const value = record.payload as VisualReview;
    return <><ul>{value.pages?.map(page => <li key={page.number}>第 {page.number} 页：{page.verdict === 'pass' ? '检查通过' : page.verdict === 'needs_changes' ? '需要修改' : '待检查'}{page.issues?.map((issue, index) => <p key={index}>{categories[issue.category]} / {severities[issue.severity]}：{issue.description}</p>)}</li>)}</ul>{value.note && <p>{value.note}</p>}</>;
  }
  const value = record.payload as PowerPointReview;
  return <><p>{value.application_version || '未填写 PowerPoint 版本'} · {value.platform || '未填写操作系统'}</p><ul>{Object.entries(checkLabels).map(([key, title]) => <li key={key}>{title}：{({ pending: '未检查', pass: '通过', fail: '发现问题', not_applicable: '不适用' })[value.checks?.[key as keyof PowerPointReview['checks']] ?? 'pending']}</li>)}</ul>{value.note && <p>{value.note}</p>}</>;
}
