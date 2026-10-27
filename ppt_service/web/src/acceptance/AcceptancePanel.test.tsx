import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api';
import { AcceptancePanel } from './AcceptancePanel';
import { assertSnapshot, rasterPreviewUrl } from './client';
import type { AcceptanceClient, AcceptanceCommand, AcceptanceSnapshot, VisualReview } from './contracts';
import { powerpointDraft, powerpointValidation, visualDraft } from './drafts';
import { syntheticSnapshot } from './fixtures';

function clientFor(snapshot = syntheticSnapshot()) {
  return { load: vi.fn<AcceptanceClient['load']>().mockResolvedValue(snapshot), save: vi.fn<AcceptanceClient['save']>() };
}
function setup(client = clientFor()) { const view = render(<AcceptancePanel projectId="demo-project" versionId="demo-v1" client={client} />); return { ...view, client }; }
async function ready() { await screen.findByLabelText('第 1 页检查结论'); }
function recordVisual(snapshot: AcceptanceSnapshot, command: AcceptanceCommand): AcceptanceSnapshot {
  if (command.kind !== 'visual_review') throw Error('test expected visual command');
  return { ...snapshot, revision: snapshot.revision + 1, can_revoke: true, records: [...snapshot.records, { id: `review-${snapshot.revision + 1}`, revision: snapshot.revision + 1, kind: 'visual_review', created_at: '2026-10-22T12:00:00Z', reviewer: { issuer: 'test-issuer', sub: 'test-reviewer' }, binding: snapshot.binding, payload: { pages: command.pages, note: command.note }, revoked: false, stale: false }] };
}

describe('P4 version-bound review workspace', () => {
  it('reports loaded snapshots and only offers saved current problems to the host revision form', async () => {
    const initial=syntheticSnapshot(); const client=clientFor(initial); const onSnapshot=vi.fn(),onUseFeedback=vi.fn();
    client.save.mockImplementation(async(_p,_v,command)=>recordVisual(initial,command));
    render(<AcceptancePanel projectId="demo-project" versionId="demo-v1" client={client} onSnapshot={onSnapshot} onUseFeedback={onUseFeedback}/>);await ready();
    expect(onSnapshot).toHaveBeenLastCalledWith(initial);
    fireEvent.click(screen.getByRole('button',{name:'添加问题'}));fireEvent.change(screen.getByLabelText('问题 1 说明'),{target:{value:'标题重叠'}});
    expect(screen.getByRole('button',{name:'将已保存问题带入修改'})).toBeDisabled();expect(onUseFeedback).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'保存视觉检查记录'}));await screen.findByText('检查记录已保存至服务端。');
    fireEvent.change(screen.getByLabelText('问题 1 说明'),{target:{value:'尚未保存的新问题'}});
    fireEvent.click(screen.getByRole('button',{name:'将已保存问题带入修改'}));
    expect(onUseFeedback).toHaveBeenCalledWith({binding:initial.binding,reviewRevision:1,pages:[1],feedback:expect.stringContaining('标题重叠')});
    expect(onUseFeedback.mock.calls[0][0].feedback).not.toContain('尚未保存');expect(onSnapshot.mock.calls.at(-1)?.[0].revision).toBe(1);
  });

  it('never revives an earlier problem record when the latest saved visual record is revoked', async () => {
    const initial=syntheticSnapshot();const visual=visualDraft(initial);visual.pages[0]={...visual.pages[0],verdict:'needs_changes',issues:[{category:'layout',severity:'major',description:'旧问题'}]};
    const first=recordVisual(initial,{expected_revision:0,binding:initial.binding,kind:'visual_review',...visual});
    const second=recordVisual(first,{expected_revision:1,binding:initial.binding,kind:'visual_review',...visual});second.records[1].revoked=true;
    const onUseFeedback=vi.fn();render(<AcceptancePanel projectId="demo-project" versionId="demo-v1" client={clientFor(second)} onUseFeedback={onUseFeedback}/>);await ready();
    expect(screen.getByRole('button',{name:'将已保存问题带入修改'})).toBeDisabled();expect(onUseFeedback).not.toHaveBeenCalled();
  });

  it('starts every human check pending and shows complete acceptance pending', async () => {
    setup(); await ready();
    expect(screen.getByLabelText('第 1 页检查结论')).toHaveValue('pending');
    for (const label of ['在桌面 PowerPoint 中打开', '实际编辑文字或其他可编辑内容', '编辑原生图表并核对数据', '保存修改后的演示稿', '关闭后重新打开并核对结果']) expect(screen.getByLabelText(new RegExp(label))).toHaveValue('pending');
    expect(screen.getByText('完整验收待完成')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /发布/ })).not.toBeInTheDocument();
  });

  it('saves an issue with full roster, immutable binding and expected revision; server controls displayed status', async () => {
    const snapshot = syntheticSnapshot(); const client = clientFor(snapshot);
    client.save.mockImplementation(async (_p, _v, command) => recordVisual(snapshot, command));
    setup(client); await ready();
    fireEvent.click(screen.getByRole('button', { name: '添加问题' }));
    fireEvent.change(screen.getByLabelText('问题 1 说明'), { target: { value: '右侧图表标签被裁切' } });
    fireEvent.click(screen.getByRole('button', { name: '保存视觉检查记录' }));
    await screen.findByText('检查记录已保存至服务端。');
    const command = client.save.mock.calls[0][2];
    expect(command).toMatchObject({ expected_revision: 0, binding: snapshot.binding, kind: 'visual_review', pages: [{ number: 1, preview_sha256: '1'.repeat(64), verdict: 'needs_changes', issues: [{ category: 'layout', severity: 'major', description: '右侧图表标签被裁切' }] }, { number: 2, verdict: 'pending', issues: [] }] });
    expect(screen.getByText('检查人 test-reviewer', { exact: false })).toBeInTheDocument();
    expect(within(screen.getByLabelText('服务端验收状态')).queryByText('需修改')).not.toBeInTheDocument();
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

  it('blocks stale source reviews, retaining history without copying stale passing decisions', async () => {
    const snapshot = syntheticSnapshot();
    const saved = recordVisual(snapshot, { expected_revision: 0, binding: snapshot.binding, kind: 'visual_review', pages: visualDraft(snapshot).pages.map(p => ({ ...p, verdict: 'pass' })), note: '旧版结论' });
    saved.can_review = false; saved.acceptance_status = 'stale'; saved.records[0].stale = true;
    setup(clientFor(saved)); await ready();
    expect(screen.getByRole('button', { name: '保存视觉检查记录' })).toBeDisabled();
    expect(screen.getByLabelText('第 1 页检查结论')).toHaveValue('pending');
    expect(screen.getByText('来源已失效')).toBeInTheDocument();
    expect(screen.getByText('旧版结论')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /让每一步都可核验/ }));
    expect(screen.getByLabelText('第 2 页检查结论')).toBeDisabled();
    expect(screen.getByRole('img', { name: /第 2 页静态 PNG/ })).toBeInTheDocument();
    fireEvent.error(screen.getByRole('img')); expect(screen.getByRole('button', { name: '重试预览' })).toBeEnabled();
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
    expect(screen.getByLabelText('第 1 页检查结论')).toHaveValue('pending');
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

  it('clears dirty drafts with an explanation after a source revision changes', async () => {
    const client = clientFor(); setup(client); await ready();
    const oldImage = screen.getByRole('img'); fireEvent.load(oldImage);
    fireEvent.change(screen.getByLabelText('视觉检查备注'), { target: { value: '旧来源的草稿' } });
    const next = syntheticSnapshot(); next.source_revision += 1; next.binding.source_revision += 1; client.load.mockResolvedValue(next);
    fireEvent.click(screen.getByRole('button', { name: '重新读取记录' }));
    await screen.findByText('版本来源已变化，旧来源的未保存草稿已清空，请重新检查。');
    expect(screen.getByLabelText('视觉检查备注')).toHaveValue('');
    expect(screen.getByRole('img')).not.toBe(oldImage);
    expect(within(screen.getByLabelText('第 1 页检查结论')).getByRole('option', { name: '检查通过' })).toBeDisabled();
    fireEvent.load(screen.getByRole('img'));
    expect(within(screen.getByLabelText('第 1 页检查结论')).getByRole('option', { name: '检查通过' })).toBeEnabled();
  });

  it('requires a loaded raster before a passing page conclusion and blocks saving after raster failure', async () => {
    setup(); await ready();
    const conclusion = screen.getByLabelText('第 1 页检查结论');
    expect(within(conclusion).getByRole('option', { name: '检查通过' })).toBeDisabled();
    fireEvent.change(conclusion, { target: { value: 'pass' } }); expect(conclusion).toHaveValue('pending');
    fireEvent.load(screen.getByRole('img')); fireEvent.change(conclusion, { target: { value: 'pass' } });
    expect(conclusion).toHaveValue('pass'); expect(screen.getByRole('button', { name: '保存视觉检查记录' })).toBeEnabled();
    fireEvent.error(screen.getByRole('img')); expect(screen.getByRole('button', { name: '保存视觉检查记录' })).toBeDisabled();
    expect(screen.getByText('请先加载并查看所有选择“检查通过”的页面预览，再保存视觉检查。')).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole('button', { name: '保存视觉检查记录' }));
    view.rerender(<AcceptancePanel projectId="demo-project" versionId="demo-v2" client={client} />); await ready();
    await act(async () => resolveSave(recordVisual(syntheticSnapshot(), client.save.mock.calls[0][2])));
    expect(screen.getByLabelText('视觉检查备注')).toHaveValue(''); expect(screen.queryByText('检查记录已保存至服务端。')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存视觉检查记录' })).toBeEnabled();
  });

  it('keeps a draft on revision conflict and does not expose server details', async () => {
    const client = clientFor(); client.save.mockRejectedValue(new ApiError('source_binding_changed', 409)); setup(client); await ready();
    fireEvent.change(screen.getByLabelText('视觉检查备注'), { target: { value: '需要保留的草稿' } }); fireEvent.click(screen.getByRole('button', { name: '保存视觉检查记录' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('重新读取记录'); expect(screen.getByLabelText('视觉检查备注')).toHaveValue('需要保留的草稿');
  });

  it('resets an in-flight operation when an injected client changes for the same version', async () => {
    let resolveOld!: (value: AcceptanceSnapshot) => void;
    const oldClient = clientFor(); oldClient.save.mockImplementation(() => new Promise(resolve => { resolveOld = resolve; }));
    const view = setup(oldClient); await ready(); fireEvent.click(screen.getByRole('button', { name: '保存视觉检查记录' }));
    const newClient = clientFor(); view.rerender(<AcceptancePanel projectId="demo-project" versionId="demo-v1" client={newClient} />); await ready();
    await act(async () => resolveOld({ ...syntheticSnapshot(), revision: 1 }));
    expect(newClient.load).toHaveBeenCalledOnce(); expect(screen.getByRole('button', { name: '保存视觉检查记录' })).toBeEnabled(); expect(screen.queryByText('检查记录已保存至服务端。')).not.toBeInTheDocument();
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
  it('fails closed for a malformed history payload and forged publication result', () => {
    const snapshot = syntheticSnapshot(); const saved = recordVisual(snapshot, { expected_revision: 0, binding: snapshot.binding, kind: 'visual_review', ...visualDraft(snapshot) });
    saved.records[0].payload = { pages: null, note: '' } as unknown as VisualReview;
    expect(() => assertSnapshot(saved, 'demo-project', 'demo-v1')).toThrow(ApiError);
    expect(() => assertSnapshot({ ...snapshot, publication_authorized: true } as unknown as AcceptanceSnapshot, 'demo-project', 'demo-v1')).toThrow(ApiError);
  });
});
