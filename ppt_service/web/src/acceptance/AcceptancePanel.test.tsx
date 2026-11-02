import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api';
import { AcceptancePanel } from './AcceptancePanel';
import { assertSnapshot, rasterPreviewUrl, renderedPptxPreviewUrl } from './client';
import type { AcceptanceClient, AcceptanceCommand, AcceptanceSnapshot, VisualReview } from './contracts';
import { powerpointDraft, powerpointValidation, visualDraft } from './drafts';
import { syntheticSnapshot } from './fixtures';

function clientFor(snapshot = syntheticSnapshot()) {
  return { load: vi.fn<AcceptanceClient['load']>().mockResolvedValue(snapshot), save: vi.fn<AcceptanceClient['save']>() };
}
function setup(client = clientFor()) { const view = render(<AcceptancePanel projectId="demo-project" versionId="demo-v1" client={client} />); return { ...view, client }; }
async function ready() { await screen.findByRole('region', { name: '演示稿预览' }); const summary = screen.getByText('验收与发布'); if (!summary.closest('details')?.open) fireEvent.click(summary); }
function confirmVisual() { fireEvent.click(screen.getByLabelText(/我已检查此版本全部/)); }
function recordVisual(snapshot: AcceptanceSnapshot, command: AcceptanceCommand): AcceptanceSnapshot {
  if (command.kind !== 'visual_review') throw Error('test expected visual command');
  return { ...snapshot, revision: snapshot.revision + 1, can_revoke: true, records: [...snapshot.records, { id: `review-${snapshot.revision + 1}`, revision: snapshot.revision + 1, kind: 'visual_review', created_at: '2026-10-22T12:00:00Z', reviewer: { issuer: 'test-issuer', sub: 'test-reviewer' }, binding: snapshot.binding, payload: { pages: command.pages, note: command.note }, revoked: false, stale: false }] };
}

function completeSnapshot(): AcceptanceSnapshot {
  const initial = syntheticSnapshot();
  const visual = recordVisual(initial, { kind: 'visual_review', expected_revision: 0, binding: initial.binding,
    pages: visualDraft(initial).pages.map(page => ({ ...page, verdict: 'pass' })), note: '' });
  const desktop = { application_version: 'Microsoft 365 2509', platform: 'Windows 11', note: '',
    checks: { opened: 'pass', content_edited: 'pass', native_chart_edited: 'pass', saved: 'pass', reopened: 'pass' } } as const;
  return { ...visual, revision: 2, programmatic_status: 'passed', visual_status: 'passed', powerpoint_status: 'passed', human_status: 'passed', acceptance_status: 'passed',
    programmatic: { status: 'passed', receipt_sha256: 'f'.repeat(64), completed_gates: ['independent_export_build_contract', 'independent_postflight_and_delivery', 'independent_final_pptx_rendering'], pending_gates: [], renderer: 'LibreOffice 24.2', detail: '最终文件独立核验通过。',
      rendered_pages: initial.pages.map(page => ({ number: page.number, preview_sha256: `${page.number + 4}`.repeat(64), preview_url: `/api/v1/projects/demo-project/versions/demo-v1/acceptance/rendered-slides/${page.number}/preview?receipt_sha256=${'f'.repeat(64)}` })) },
    can_publish: true, publication: { status: 'draft', record_id: null, published_at: null }, pending_reasons: [], records: [...visual.records,
      { id: 'desktop-2', revision: 2, kind: 'powerpoint_review', created_at: '2026-11-01T07:00:00Z', reviewer: { issuer: 'test', sub: 'owner' }, binding: initial.binding, payload: desktop, revoked: false, stale: false }] };
}

function publishedSnapshot(initial = completeSnapshot()): AcceptanceSnapshot {
  const record = { id: 'publish-3', revision: 3, kind: 'publish' as const, created_at: '2026-11-01T07:10:00Z', reviewer: { issuer: 'test', sub: 'owner' }, binding: initial.binding,
    payload: { visual_record_id: initial.records[0].id, powerpoint_record_id: 'desktop-2', programmatic_receipt_sha256: 'f'.repeat(64) }, revoked: false, stale: false };
  return { ...initial, revision: 3, can_publish: false, publication_authorized: true, publication: { status: 'published', record_id: record.id, published_at: record.created_at }, records: [...initial.records, record] };
}

describe('explicit version publication', () => {
  it('uses one receipt-bound final PPTX preview and never records acceptance by viewing it', async () => {
    const complete = completeSnapshot(); const initial: AcceptanceSnapshot = { ...syntheticSnapshot(), programmatic: { ...complete.programmatic!, status: 'pending', completed_gates: ['independent_final_pptx_rendering'], pending_gates: ['independent_export_build_contract', 'independent_postflight_and_delivery'] } };
    const client = clientFor(initial); setup(client); await ready();
    const rendered = within(screen.getByRole('region', { name: '演示稿预览' }));
    const first = rendered.getByRole('img', { name: '最终 PPTX 第 1 页独立渲染' });
    expect(first).toHaveAttribute('src', initial.programmatic!.rendered_pages![0].preview_url); fireEvent.load(first);
    expect(screen.getAllByRole('img')).toHaveLength(1);
    expect(screen.queryByRole('region', { name: 'PPTX 导出效果' })).not.toBeInTheDocument();
    fireEvent.click(rendered.getByRole('button', { name: /让每一步都可核验/ }));
    expect(rendered.getByRole('img', { name: '最终 PPTX 第 2 页独立渲染' })).toHaveAttribute('src', initial.programmatic!.rendered_pages![1].preview_url);
    expect(screen.getByLabelText(/我已检查此版本全部/)).not.toBeChecked();
    expect(screen.queryByRole('img', { name: /静态 PNG/ })).not.toBeInTheDocument();
    expect(client.save).not.toHaveBeenCalled();
  });

  it('starts with technical acceptance collapsed and labels the source fallback honestly', async () => {
    const client = clientFor(); setup(client); await screen.findByRole('region', { name: '演示稿预览' });
    expect(screen.getByText('验收与发布').closest('details')).not.toHaveAttribute('open');
    expect(screen.getByText('源文件参考预览 · 最终 PPTX 渲染尚未就绪')).toBeInTheDocument();
    expect(screen.getAllByRole('img')).toHaveLength(1);
    expect(screen.getByRole('button', { name: '确认视觉检查通过' })).not.toBeVisible();
    expect(client.save).not.toHaveBeenCalled();
  });

  it('fails closed for an invalid final-render URL and does not silently show the source as final output', async () => {
    const initial = completeSnapshot(); initial.programmatic!.rendered_pages![0].preview_url = 'https://other.example/render.png';
    const client = clientFor(initial); const view = setup(client); await ready();
    expect(view.container.querySelector('img')).toBeNull();
    expect(screen.getByText('此页 PPTX 渲染地址不可用，请重新读取记录。')).toBeInTheDocument();
    expect(screen.queryByRole('img', { name: /静态 PNG/ })).not.toBeInTheDocument();
    expect(client.save).not.toHaveBeenCalled();
  });

  it('keeps final-render image digests separate from the source roster in a human confirmation', async () => {
    const complete = completeSnapshot(); const initial = { ...syntheticSnapshot(), programmatic: { ...complete.programmatic!, status: 'pending' as const, pending_gates: ['independent_export_build_contract'] } };
    const client = clientFor(initial); client.save.mockImplementation(async (_p, _v, command) => recordVisual(initial, command)); setup(client); await ready();
    fireEvent.load(screen.getByRole('img')); confirmVisual(); fireEvent.click(screen.getByRole('button', { name: '确认视觉检查通过' }));
    await waitFor(() => expect(client.save).toHaveBeenCalledOnce());
    const command = client.save.mock.calls[0][2];
    expect(command).toMatchObject({ kind: 'visual_review', pages: initial.pages.map(page => ({ number: page.number, preview_sha256: page.preview_sha256, verdict: 'pass' })) });
    expect(command).not.toMatchObject({ pages: [{ preview_sha256: complete.programmatic!.rendered_pages![0].preview_sha256 }] });
  });

  it('keeps publish disabled while any gate is pending and never equates rendering with desktop editing', async () => {
    const initial = syntheticSnapshot(); const client = { ...clientFor(initial), publish: vi.fn<NonNullable<AcceptanceClient['publish']>>() };
    setup(client); await ready();
    expect(screen.getByRole('button', { name: '确认发布此版本' })).toBeDisabled();
    expect(screen.getByLabelText(/我已核对此版本的完整验收记录/)).toBeDisabled();
    expect(screen.getByLabelText(/实际编辑文字或其他可编辑内容/)).toHaveValue('pending');
    expect(client.publish).not.toHaveBeenCalled();
  });

  it('requires an explicit current-version confirmation and sends only the bound publication command', async () => {
    const initial = completeSnapshot(); const client = { ...clientFor(initial), publish: vi.fn<NonNullable<AcceptanceClient['publish']>>().mockResolvedValue(publishedSnapshot(initial)) };
    const onPublished = vi.fn(); render(<AcceptancePanel projectId="demo-project" versionId="demo-v1" client={client} onPublished={onPublished} />); await ready();
    const button = screen.getByRole('button', { name: '确认发布此版本' }); expect(button).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/我已核对此版本的完整验收记录/)); fireEvent.click(button);
    await screen.findByText('此版本已正式发布，发布决定和所依据的检查记录已保存。');
    expect(client.publish).toHaveBeenCalledOnce(); expect(client.publish).toHaveBeenCalledWith('demo-project', 'demo-v1', { kind: 'publish', expected_revision: 2, binding: initial.binding, confirm: true });
    expect(onPublished).toHaveBeenCalledWith(publishedSnapshot(initial)); expect(client.save).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: '确认发布此版本' })).not.toBeInTheDocument();
  });

  it('blocks publication of unsaved edits and clears confirmation after authoritative refresh', async () => {
    const initial = completeSnapshot(); const client = { ...clientFor(initial), publish: vi.fn<NonNullable<AcceptanceClient['publish']>>() };
    setup(client); await ready();
    fireEvent.click(screen.getByLabelText(/我已核对此版本的完整验收记录/));
    fireEvent.change(screen.getByLabelText('桌面检查备注'), { target: { value: '尚未保存的新问题' } });
    expect(screen.getByRole('button', { name: '确认发布此版本' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '重新读取记录' }));
    await waitFor(() => expect(client.load).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText(/我已核对此版本的完整验收记录/)).not.toBeChecked();
    expect(screen.getByLabelText('桌面检查备注')).toHaveValue('尚未保存的新问题'); expect(client.publish).not.toHaveBeenCalled();
  });

  it('requires reloading after publication conflict and ignores late results from a previous version', async () => {
    const initial = completeSnapshot(); const client = { ...clientFor(initial), publish: vi.fn<NonNullable<AcceptanceClient['publish']>>().mockRejectedValueOnce(new ApiError('revision_conflict', 409)) };
    const view = setup(client); await ready();
    fireEvent.click(screen.getByLabelText(/我已核对此版本的完整验收记录/)); fireEvent.click(screen.getByRole('button', { name: '确认发布此版本' }));
    await screen.findByRole('alert'); expect(screen.getByRole('button', { name: '确认发布此版本' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '重新读取记录' })); await waitFor(() => expect(client.load).toHaveBeenCalledTimes(2));
    let resolvePublish!: (value: AcceptanceSnapshot) => void; client.publish.mockImplementationOnce(() => new Promise(resolve => { resolvePublish = resolve; }));
    fireEvent.click(screen.getByLabelText(/我已核对此版本的完整验收记录/)); fireEvent.click(screen.getByRole('button', { name: '确认发布此版本' }));
    client.load.mockResolvedValueOnce(syntheticSnapshot('demo-v2')); view.rerender(<AcceptancePanel projectId="demo-project" versionId="demo-v2" client={client} />); await ready();
    await act(async () => resolvePublish(publishedSnapshot(initial)));
    expect(screen.queryByText('此版本已正式发布，发布决定和所依据的检查记录已保存。')).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: '版本 demo-v2 的验收工作台' })).toBeInTheDocument();
  });
});

describe('P4 version-bound review workspace', () => {
  it('reports snapshots and imports structured historical feedback without creating a new human decision', async () => {
    const initial=syntheticSnapshot(); const visual=visualDraft(initial); visual.pages[0]={...visual.pages[0],verdict:'needs_changes',issues:[{category:'layout',severity:'major',description:'标题重叠'}]};
    const saved=recordVisual(initial,{expected_revision:0,binding:initial.binding,kind:'visual_review',...visual});
    const client=clientFor(saved); const onSnapshot=vi.fn(),onUseFeedback=vi.fn();
    render(<AcceptancePanel projectId="demo-project" versionId="demo-v1" client={client} onSnapshot={onSnapshot} onUseFeedback={onUseFeedback}/>);await ready();
    expect(onSnapshot).toHaveBeenLastCalledWith(saved);
    fireEvent.change(screen.getByLabelText('视觉检查备注'),{target:{value:'尚未保存的新备注'}});
    fireEvent.click(screen.getByRole('button',{name:'将已保存问题带入修改'}));
    expect(onUseFeedback).toHaveBeenCalledWith({binding:initial.binding,reviewRevision:1,pages:[1],feedback:expect.stringContaining('标题重叠'),items:[{page:1,feedback:'版式 / 重要：标题重叠'}]});
    expect(onUseFeedback.mock.calls[0][0].feedback).not.toContain('尚未保存');expect(client.save).not.toHaveBeenCalled();
  });

  it('never revives an earlier problem record when the latest saved visual record is revoked', async () => {
    const initial=syntheticSnapshot();const visual=visualDraft(initial);visual.pages[0]={...visual.pages[0],verdict:'needs_changes',issues:[{category:'layout',severity:'major',description:'旧问题'}]};
    const first=recordVisual(initial,{expected_revision:0,binding:initial.binding,kind:'visual_review',...visual});
    const second=recordVisual(first,{expected_revision:1,binding:initial.binding,kind:'visual_review',...visual});second.records[1].revoked=true;
    const onUseFeedback=vi.fn();render(<AcceptancePanel projectId="demo-project" versionId="demo-v1" client={clientFor(second)} onUseFeedback={onUseFeedback}/>);await ready();
    expect(screen.getByRole('button',{name:'将已保存问题带入修改'})).toBeDisabled();expect(onUseFeedback).not.toHaveBeenCalled();
  });

  it('starts every human check pending without requiring a verdict widget for each page', async () => {
    setup(); await ready();
    expect(screen.getByLabelText(/我已检查此版本全部/)).not.toBeChecked();
    expect(screen.getByRole('button',{name:'确认视觉检查通过'})).toBeDisabled();
    expect(screen.queryByLabelText('第 1 页检查结论')).not.toBeInTheDocument();
    for (const label of ['在桌面 PowerPoint 中打开', '实际编辑文字或其他可编辑内容', '编辑原生图表并核对数据', '保存修改后的演示稿', '关闭后重新打开并核对结果']) expect(screen.getByLabelText(new RegExp(label))).toHaveValue('pending');
    expect(screen.getByText('完整验收待完成')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /发布/ })).not.toBeInTheDocument();
  });

  it('saves an explicitly confirmed whole-deck decision with the original full roster and binding', async () => {
    const snapshot=syntheticSnapshot(); const client=clientFor(snapshot);
    client.save.mockImplementation(async(_p,_v,command)=>recordVisual(snapshot,command));setup(client);await ready();
    fireEvent.load(screen.getByRole('img'));expect(client.save).not.toHaveBeenCalled();
    confirmVisual();expect(client.save).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'确认视觉检查通过'}));await screen.findByText('检查记录已保存至服务端。');
    expect(client.save.mock.calls[0][2]).toEqual({expected_revision:0,binding:snapshot.binding,kind:'visual_review',pages:snapshot.pages.map(page=>({number:page.number,preview_sha256:page.preview_sha256,verdict:'pass',issues:[]})),note:''});
    expect(screen.getByLabelText(/我已检查此版本全部/)).not.toBeChecked();
    expect(within(screen.getByLabelText('服务端验收状态')).queryByText('已通过')).not.toBeInTheDocument();
  });

  it('retains unfinished desktop steps and requires actual application details before successful checks', async () => {
    const client = clientFor(); setup(client); await ready();
    fireEvent.change(screen.getByLabelText(/在桌面 PowerPoint 中打开/), { target: { value: 'pass' } });
    expect(screen.getByRole('button', { name: '保存桌面检查记录' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('PowerPoint 版本'), { target: { value: 'Microsoft 365 2509' } });
    fireEvent.change(screen.getByLabelText('操作系统'), { target: { value: 'Windows 11' } });
    client.save.mockResolvedValue({ ...syntheticSnapshot(), revision: 1 });
    fireEvent.click(screen.getByRole('button', { name: '保存桌面检查记录' }));
    await waitFor(() => expect(client.save).toHaveBeenCalledOnce());
    expect(client.save.mock.calls[0][2]).toMatchObject({ kind: 'powerpoint_review', application_version: 'Microsoft 365 2509', checks: { opened: 'pass', content_edited: 'pending', native_chart_edited: 'pending', saved: 'pending', reopened: 'pending' } });
  });

  it('offers not applicable only for a server roster with no native charts; never preselects it', async () => {
    const snapshot = { ...syntheticSnapshot(), native_chart_count: 0 }; setup(clientFor(snapshot)); await ready();
    expect(screen.getByRole('option', { name: /不适用/ })).toBeInTheDocument();
    expect(screen.getByLabelText(/编辑原生图表/)).toHaveValue('pending');
    const value = powerpointDraft(snapshot); value.checks.reopened = 'pass';
    expect(powerpointValidation(value, 0)).toContain('PowerPoint 版本');
    value.application_version = '365'; value.platform = 'Windows'; expect(powerpointValidation(value, 0)).toContain('打开检查');
    value.checks.opened = 'pass'; expect(powerpointValidation(value, 0)).toContain('保存检查');
  });

  it('blocks stale source confirmation, retaining history and the browsable preview', async () => {
    const snapshot=syntheticSnapshot(); const saved=recordVisual(snapshot,{expected_revision:0,binding:snapshot.binding,kind:'visual_review',pages:visualDraft(snapshot).pages.map(p=>({...p,verdict:'pass'})),note:'旧版结论'});
    saved.can_review=false;saved.acceptance_status='stale';saved.records[0].stale=true;setup(clientFor(saved));await ready();
    expect(screen.getByRole('button',{name:'确认视觉检查通过'})).toBeDisabled();expect(screen.getByLabelText(/我已检查此版本全部/)).not.toBeChecked();
    expect(screen.getByText('来源已失效')).toBeInTheDocument();expect(screen.getByText('旧版结论')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:/让每一步都可核验/}));expect(screen.getByRole('img',{name:/第 2 页静态 PNG/})).toBeInTheDocument();
    fireEvent.error(screen.getByRole('img'));expect(screen.getByRole('button',{name:'重试预览'})).toBeEnabled();
  });

  it('revokes explicitly with a reason and never resurrects an older successful review', async () => {
    const snapshot = syntheticSnapshot();
    const command: AcceptanceCommand = { expected_revision: 0, binding: snapshot.binding, kind: 'visual_review', pages: visualDraft(snapshot).pages.map(p => ({ ...p, verdict: 'pass' })), note: '' };
    const first = recordVisual(snapshot, command); const second = recordVisual(first, { ...command, expected_revision: 1 });
    const client = clientFor(second);
    client.save.mockResolvedValue({ ...second, revision: 3, records: [second.records[0], { ...second.records[1], revoked: true }] });
    setup(client); await ready();
    fireEvent.change(screen.getByLabelText('PowerPoint 版本'), { target: { value: '尚未保存的桌面版本' } });
    fireEvent.click(screen.getAllByRole('button', { name: '撤销此记录' })[0]);
    expect(screen.getByRole('button', { name: '确认撤销记录' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('撤销原因'), { target: { value: '实际 PowerPoint 字体不正确，需要重查' } });
    fireEvent.click(screen.getByRole('button', { name: '确认撤销记录' }));
    await screen.findByText('已保存撤销记录，历史检查仍可查看。');
    expect(client.save.mock.calls[0][2]).toMatchObject({ kind: 'revoke', record_id: 'review-2', expected_revision: 2, reason: '实际 PowerPoint 字体不正确，需要重查' });
    expect(screen.getByLabelText(/我已检查此版本全部/)).not.toBeChecked();
    expect(screen.getByLabelText('PowerPoint 版本')).toHaveValue('尚未保存的桌面版本');
  });

  it('preserves both dirty drafts during same-source refresh and warns before they are resubmitted', async () => {
    const client = clientFor(); setup(client); await ready();
    fireEvent.change(screen.getByLabelText('视觉检查备注'), { target: { value: '未保存的视觉备注' } });
    fireEvent.change(screen.getByLabelText('PowerPoint 版本'), { target: { value: '未保存的应用版本' } });
    client.load.mockResolvedValue({ ...syntheticSnapshot(), revision: 1 });
    fireEvent.click(screen.getByRole('button', { name: '重新读取记录' }));
    await screen.findByText('已重新读取服务端记录，未保存草稿已保留。请核对最新记录后再保存。');
    expect(screen.getByLabelText('视觉检查备注')).toHaveValue('未保存的视觉备注'); expect(screen.getByLabelText('PowerPoint 版本')).toHaveValue('未保存的应用版本');
  });

  it('clears draft notes and explicit confirmation after a source revision changes', async () => {
    const client=clientFor();setup(client);await ready();const oldImage=screen.getByRole('img');fireEvent.load(oldImage);confirmVisual();
    fireEvent.change(screen.getByLabelText('视觉检查备注'),{target:{value:'旧来源的草稿'}});
    const next=syntheticSnapshot();next.source_revision+=1;next.binding.source_revision+=1;client.load.mockResolvedValue(next);
    fireEvent.click(screen.getByRole('button',{name:'重新读取记录'}));await screen.findByText('版本来源已变化，旧来源的未保存草稿已清空，请重新检查。');
    expect(screen.getByLabelText('视觉检查备注')).toHaveValue('');expect(screen.getByRole('img')).not.toBe(oldImage);
    expect(screen.getByLabelText(/我已检查此版本全部/)).not.toBeChecked();expect(screen.getByRole('button',{name:'确认视觉检查通过'})).toBeDisabled();
  });

  it('never treats a successful or failed preview load as an acceptance decision', async () => {
    const client=clientFor();setup(client);await ready();const image=screen.getByRole('img');fireEvent.load(image);
    expect(screen.getByLabelText(/我已检查此版本全部/)).not.toBeChecked();expect(screen.getByRole('button',{name:'确认视觉检查通过'})).toBeDisabled();
    fireEvent.error(image);expect(screen.getByRole('button',{name:'重试预览'})).toBeEnabled();expect(client.save).not.toHaveBeenCalled();
    expect(screen.getByRole('button',{name:'确认视觉检查通过'})).toBeDisabled();
  });

  it('keeps content-editing pending for chartless presentations even when the other desktop steps pass', async () => {
    const client = clientFor({ ...syntheticSnapshot(), native_chart_count: 0 }); client.save.mockResolvedValue({ ...syntheticSnapshot(), native_chart_count: 0, revision: 1 });
    setup(client); await ready();
    fireEvent.change(screen.getByLabelText('PowerPoint 版本'), { target: { value: 'Microsoft 365' } }); fireEvent.change(screen.getByLabelText('操作系统'), { target: { value: 'Windows 11' } });
    for (const label of ['在桌面 PowerPoint 中打开', '保存修改后的演示稿', '关闭后重新打开并核对结果']) fireEvent.change(screen.getByLabelText(new RegExp(label)), { target: { value: 'pass' } });
    fireEvent.change(screen.getByLabelText(/编辑原生图表/), { target: { value: 'not_applicable' } });
    fireEvent.click(screen.getByRole('button', { name: '保存桌面检查记录' }));
    await waitFor(() => expect(client.save).toHaveBeenCalledOnce());
    expect(client.save.mock.calls[0][2]).toMatchObject({ checks: { opened: 'pass', content_edited: 'pending', native_chart_edited: 'not_applicable', saved: 'pass', reopened: 'pass' } });
  });

  it('rejects a wrong-version response before rendering its private data', async () => {
    const snapshot = syntheticSnapshot('other-version'); snapshot.pages[0].title = 'OTHER PRIVATE VERSION';
    setup(clientFor(snapshot));
    expect(await screen.findByRole('alert')).toHaveTextContent('INVALID_RESPONSE');
    expect(screen.queryByText('OTHER PRIVATE VERSION')).not.toBeInTheDocument();
  });

  it('resets drafts and ignores late load responses after switching versions', async () => {
    let resolveOld!: (value: AcceptanceSnapshot) => void;
    const client = clientFor(); client.load.mockImplementation((_p, version) => version === 'demo-v1' ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve(syntheticSnapshot(version)));
    const view = setup(client); view.rerender(<AcceptancePanel projectId="demo-project" versionId="demo-v2" client={client} />);
    await ready(); fireEvent.change(screen.getByLabelText('视觉检查备注'), { target: { value: '新版本草稿' } });
    await act(async () => resolveOld(syntheticSnapshot()));
    expect(screen.getByLabelText('视觉检查备注')).toHaveValue('新版本草稿');
    expect(screen.getByRole('region', { name: '版本 demo-v2 的验收工作台' })).toBeInTheDocument();
    expect(client.load.mock.calls[0][2]?.aborted).toBe(true);
  });

  it('ignores a late save response and message after switching versions', async () => {
    let resolveSave!: (value: AcceptanceSnapshot) => void;
    const client = clientFor(); client.load.mockImplementation(async (_p, version) => syntheticSnapshot(version)); client.save.mockImplementation(() => new Promise(resolve => { resolveSave = resolve; }));
    const view = setup(client); await ready(); fireEvent.change(screen.getByLabelText('视觉检查备注'), { target: { value: '旧版本私有备注' } });
    confirmVisual(); fireEvent.click(screen.getByRole('button', { name: '确认视觉检查通过' }));
    view.rerender(<AcceptancePanel projectId="demo-project" versionId="demo-v2" client={client} />); await ready();
    await act(async () => resolveSave(recordVisual(syntheticSnapshot(), client.save.mock.calls[0][2])));
    expect(screen.getByLabelText('视觉检查备注')).toHaveValue(''); expect(screen.queryByText('检查记录已保存至服务端。')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认视觉检查通过' })).toBeDisabled();
  });

  it('keeps a draft on revision conflict and does not expose server details', async () => {
    const client = clientFor(); client.save.mockRejectedValue(new ApiError('source_binding_changed', 409)); setup(client); await ready();
    fireEvent.change(screen.getByLabelText('视觉检查备注'), { target: { value: '需要保留的草稿' } }); confirmVisual(); fireEvent.click(screen.getByRole('button', { name: '确认视觉检查通过' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('重新读取记录'); expect(screen.getByLabelText('视觉检查备注')).toHaveValue('需要保留的草稿');
  });

  it('resets an in-flight operation when an injected client changes for the same version', async () => {
    let resolveOld!: (value: AcceptanceSnapshot) => void;
    const oldClient = clientFor(); oldClient.save.mockImplementation(() => new Promise(resolve => { resolveOld = resolve; }));
    const view = setup(oldClient); await ready(); confirmVisual(); fireEvent.click(screen.getByRole('button', { name: '确认视觉检查通过' }));
    const newClient = clientFor(); view.rerender(<AcceptancePanel projectId="demo-project" versionId="demo-v1" client={newClient} />); await ready();
    await act(async () => resolveOld({ ...syntheticSnapshot(), revision: 1 }));
    expect(newClient.load).toHaveBeenCalledOnce(); expect(screen.getByRole('button', { name: '确认视觉检查通过' })).toBeDisabled(); expect(screen.queryByText('检查记录已保存至服务端。')).not.toBeInTheDocument();
  });

  it('shows safe auth errors, supports retry, and rejects unsafe image URLs without rendering them', async () => {
    const client = clientFor(); client.load.mockRejectedValueOnce(new ApiError('UNAUTHENTICATED', 401));
    const snapshot = syntheticSnapshot(); snapshot.pages[0].preview_url = 'https://evil.example/private.svg'; client.load.mockResolvedValueOnce(snapshot);
    const view = setup(client); await screen.findByRole('link', { name: '重新登录' }); fireEvent.click(screen.getByRole('button', { name: '重新读取记录' })); await ready();
    expect(view.container.querySelector('img')).toBeNull(); expect(screen.getByText('此页预览地址不可用，请重新读取记录。')).toBeInTheDocument();
  });

  it('renders untrusted issue text as text and handles failed previews', async () => {
    const snapshot = syntheticSnapshot(); const payload: VisualReview = { ...visualDraft(snapshot), note: '<script>alert(1)</script>' }; payload.pages[0].issues = [{ category: 'text', severity: 'major', description: '<img src=x onerror=alert(1)>' }]; payload.pages[0].verdict = 'needs_changes';
    const saved = recordVisual(snapshot, { expected_revision: 0, binding: snapshot.binding, kind: 'visual_review', ...payload }); const view = setup(clientFor(saved)); await ready();
    expect(view.container.querySelector('script')).toBeNull(); expect(view.container.querySelectorAll('img')).toHaveLength(1);
    fireEvent.error(screen.getByRole('img')); expect(screen.getByRole('button', { name: '重试预览' })).toBeInTheDocument();
  });
});

describe('P4 client contract', () => {
  it('only permits the exact selected version raster route', () => {
    const expected = '/api/v1/versions/v1/slides/1/preview'; expect(rasterPreviewUrl('v1', 1, expected)).toBe(expected);
    for (const bad of ['https://host' + expected, '//host' + expected, expected + '?format=svg', expected.replace('v1/slides', 'v2/slides'), 'data:image/svg+xml,<svg/>', 'javascript:alert(1)']) expect(rasterPreviewUrl('v1', 1, bad)).toBeNull();
  });
  it('only permits final render URLs for the selected project, version, page and exact receipt', () => {
    const receipt = 'f'.repeat(64); const route = `/api/v1/projects/p/versions/v/acceptance/rendered-slides/1/preview?receipt_sha256=${receipt}`;
    expect(renderedPptxPreviewUrl('p', 'v', 1, receipt, route)).toBe(route);
    for (const bad of ['https://host' + route, route.replace('/p/', '/other/'), route.replace('/v/', '/other/'), route.replace('/1/', '/2/'), route.replace(receipt, 'e'.repeat(64)), route + '&format=svg', route.replace('receipt_sha256', 'evidence_sha256')]) expect(renderedPptxPreviewUrl('p', 'v', 1, receipt, bad)).toBeNull();
  });
  it('fails closed for a malformed history payload and forged publication result', () => {
    const snapshot = syntheticSnapshot(); const saved = recordVisual(snapshot, { expected_revision: 0, binding: snapshot.binding, kind: 'visual_review', ...visualDraft(snapshot) });
    saved.records[0].payload = { pages: null, note: '' } as unknown as VisualReview;
    expect(() => assertSnapshot(saved, 'demo-project', 'demo-v1')).toThrow(ApiError);
    expect(() => assertSnapshot({ ...snapshot, publication_authorized: true } as unknown as AcceptanceSnapshot, 'demo-project', 'demo-v1')).toThrow(ApiError);
  });
  it('rejects contradictory completed gates and publication that no longer binds the current reviews', () => {
    const complete = completeSnapshot();
    expect(assertSnapshot(complete, 'demo-project', 'demo-v1').can_publish).toBe(true);
    expect(() => assertSnapshot({ ...complete, programmatic: { ...complete.programmatic!, receipt_sha256: null } }, 'demo-project', 'demo-v1')).toThrow(ApiError);
    expect(() => assertSnapshot({ ...complete, powerpoint_status: 'pending' }, 'demo-project', 'demo-v1')).toThrow(ApiError);
    const published = publishedSnapshot();
    expect(assertSnapshot(published, 'demo-project', 'demo-v1').publication_authorized).toBe(true);
    published.records[0].revoked = true;
    expect(() => assertSnapshot(published, 'demo-project', 'demo-v1')).toThrow(ApiError);
  });
});
