import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { api, ApiError } from '../api';
import { acceptanceClient } from '../acceptance/client';
import type { AcceptanceSnapshot, VisualReview } from '../acceptance/contracts';
import { syntheticSnapshot } from '../acceptance/fixtures';
import { defaultSettings, type Project, type Version } from '../domain';

vi.mock('../api', async original => ({ ...await original<typeof import('../api')>(), api: {
  me: vi.fn(), projects: vi.fn(), project: vi.fn(), versions: vi.fn(), job: vi.fn(),
  reviewRevise: vi.fn(), revise: vi.fn(), plan: vi.fn(), generate: vi.fn(), download: vi.fn(),
  preview: vi.fn((version: string, page: number) => `/api/v1/versions/${version}/slides/${page}/preview`),
} }));
vi.mock('../acceptance/client', async original => ({ ...await original<typeof import('../acceptance/client')>(), acceptanceClient: { load: vi.fn(), save: vi.fn() } }));

const project: Project = { id: 'p', title: '待审核演示项目', revision: 7, stage: 'awaiting_review', settings: defaultSettings,
  assets: [], input_snapshot_hash: 'b'.repeat(64), external_model_ack: true };
const draft: Version = { id: 'review-1', status: 'review', can_revise: false, created_at: '2026-10-27T00:00:00Z',
  slides: [{ number: 1, title: '审核页一' }, { number: 2, title: '审核页二' }] };
const published: Version = { ...draft, id: 'published-1', status: 'published', can_revise: true };
function snapshot(version = draft.id): AcceptanceSnapshot {
  const value = syntheticSnapshot(version);
  return { ...value, project_id: project.id, binding: { ...value.binding, project_id: project.id },
    pages: value.pages.map((page, i) => ({ ...page, title: draft.slides[i].title })) };
}
function savedIssue(value = snapshot()): AcceptanceSnapshot {
  const payload: VisualReview = { note: '', pages: value.pages.map(page => ({ number: page.number, preview_sha256: page.preview_sha256,
    verdict: page.number === 2 ? 'needs_changes' : 'pending', issues: page.number === 2 ? [{ category: 'text', severity: 'major', description: '标题与正文发生重叠' }] : [] })) };
  return { ...value, revision: 1, visual_status: 'needs_changes', human_status: 'needs_changes', can_revoke: true,
    records: [{ id: 'visual-record-1', revision: 1, kind: 'visual_review', created_at: '2026-10-27T01:00:00Z',
      reviewer: { issuer: 'test', sub: 'owner' }, binding: value.binding, payload, revoked: false, stale: false }] };
}
async function selectReview() {
  const user = userEvent.setup(); render(<App />);
  await screen.findByRole('heading', { name: project.title });
  await user.click(screen.getByRole('tab', { name: /预览与修改/ }));
  await user.click(screen.getAllByRole('radio')[0]);
  await screen.findByLabelText('第 1 页检查结论');
  return user;
}
beforeEach(() => {
  vi.clearAllMocks(); localStorage.setItem('ppt:selected-project', project.id);
  vi.mocked(api.me).mockResolvedValue({ subject: 'owner', name: '测试账号' });
  vi.mocked(api.projects).mockResolvedValue([project]); vi.mocked(api.project).mockResolvedValue(project);
  vi.mocked(api.versions).mockResolvedValue([draft, published]);
  vi.mocked(acceptanceClient.load).mockImplementation(async (_project, version) => snapshot(version));
  vi.mocked(api.reviewRevise).mockResolvedValue({ job_id: 'review-job' });
  vi.mocked(api.job).mockResolvedValue({ id: 'review-job', project_id: project.id, kind: 'revise', status: 'succeeded', stage: 'review', last_event_seq: 1 });
});

describe('review drafts in the real version workspace', () => {
  it('loads review only after explicit selection and shows one preview with independent model consent', async () => {
    const user = userEvent.setup(); render(<App />);
    await screen.findByRole('heading', { name: project.title });
    await user.click(screen.getByRole('tab', { name: /预览与修改/ }));
    expect(acceptanceClient.load).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: '创建新审核稿修改任务' })).not.toBeInTheDocument();
    await user.click(screen.getAllByRole('radio')[0]); await screen.findByLabelText('第 1 页检查结论');
    expect(acceptanceClient.load).toHaveBeenCalledWith(project.id, draft.id, expect.any(AbortSignal));
    expect(screen.getAllByRole('img')).toHaveLength(1);
    expect(screen.queryByRole('img', { name: '第 1 页静态预览' })).not.toBeInTheDocument();
    const form = within(screen.getByRole('region', { name: '审核稿修改' }));
    expect(form.getByRole('checkbox')).not.toBeChecked();
    fireEvent.change(form.getByLabelText('整份修改意见'), { target: { value: '调整标题间距' } });
    expect(form.getByRole('button', { name: '创建新审核稿修改任务' })).toBeDisabled();
    expect(api.reviewRevise).not.toHaveBeenCalled(); expect(api.revise).not.toHaveBeenCalled();
  });

  it('submits a manual draft revision with the exact saved binding and does not use published revision API', async () => {
    const user = await selectReview(); const form = within(screen.getByRole('region', { name: '审核稿修改' }));
    await user.type(form.getByLabelText('整份修改意见'), '调整标题间距'); await user.click(form.getByRole('checkbox'));
    await user.click(form.getByRole('button', { name: '创建新审核稿修改任务' }));
    await waitFor(() => expect(api.reviewRevise).toHaveBeenCalledOnce());
    expect(api.reviewRevise).toHaveBeenCalledWith(project, draft.id, [], '调整标题间距', snapshot().binding, 0, true);
    expect(api.revise).not.toHaveBeenCalled(); expect(api.plan).not.toHaveBeenCalled(); expect(api.generate).not.toHaveBeenCalled();
    expect(screen.getByText('待审核草稿 · 尚未正式发布')).toBeInTheDocument();
  });

  it('brings only saved current problems into explicit page feedback without starting a job', async () => {
    vi.mocked(acceptanceClient.load).mockResolvedValue(savedIssue()); const user = await selectReview();
    await user.click(screen.getByRole('button', { name: '将已保存问题带入修改' }));
    const form = within(screen.getByRole('region', { name: '审核稿修改' }));
    expect(form.getByLabelText('指定页面')).toBeChecked();
    expect(form.getByLabelText('第 2 页 · 审核页二')).toBeChecked(); expect(form.getByLabelText('第 1 页 · 审核页一')).not.toBeChecked();
    expect(form.getByLabelText('共同修改意见（可选）')).toHaveValue('第 2 页：审核页二\n- 文字 / 重要：标题与正文发生重叠');
    expect(form.getByRole('checkbox', { name: /我同意/ })).not.toBeChecked(); expect(api.reviewRevise).not.toHaveBeenCalled();
    await user.click(form.getByRole('checkbox', { name: /我同意/ })); await user.click(form.getByRole('button', { name: '创建新审核稿修改任务' }));
    await waitFor(() => expect(api.reviewRevise).toHaveBeenCalledWith(project, draft.id, [2], expect.stringContaining('标题与正文发生重叠'), snapshot().binding, 1, true));
  });

  it('keeps historical issues visible but blocks draft modification when the source is stale', async () => {
    const stale = savedIssue(); stale.can_review = false; stale.acceptance_status = 'stale'; stale.records[0].stale = true;
    vi.mocked(acceptanceClient.load).mockResolvedValue(stale); await selectReview();
    expect(screen.getByText('标题与正文发生重叠', { exact: false })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '将已保存问题带入修改' })).toBeDisabled();
    expect(screen.queryByRole('region', { name: '审核稿修改' })).not.toBeInTheDocument(); expect(api.reviewRevise).not.toHaveBeenCalled();
  });

  it('clears pending revision feedback and consent when the same version returns a changed source binding', async () => {
    const user=await selectReview();const form=within(screen.getByRole('region',{name:'审核稿修改'}));
    await user.type(form.getByLabelText('整份修改意见'),'旧来源的未提交意见');await user.click(form.getByRole('checkbox'));
    const changed=snapshot();changed.source_revision+=1;changed.binding={...changed.binding,source_revision:changed.source_revision,input_snapshot_sha256:'f'.repeat(64)};
    vi.mocked(acceptanceClient.load).mockResolvedValue(changed);await user.click(screen.getByRole('button',{name:'重新读取记录'}));
    await screen.findByText('审核稿来源已变化，未提交的修改意见已清空，请核对当前来源后重新填写。');
    expect(form.getByLabelText('整份修改意见')).toHaveValue('');expect(form.getByRole('checkbox')).not.toBeChecked();
    expect(api.reviewRevise).not.toHaveBeenCalled();
  });

  it('removes revision admission after a failed authoritative record refresh', async () => {
    const user=await selectReview();vi.mocked(acceptanceClient.load).mockRejectedValue(new ApiError('review_evidence_unavailable',503));
    await user.click(screen.getByRole('button',{name:'重新读取记录'}));await screen.findByRole('alert');
    expect(screen.queryByRole('region',{name:'审核稿修改'})).not.toBeInTheDocument();expect(api.reviewRevise).not.toHaveBeenCalled();
    expect(screen.getByRole('button',{name:'重新读取记录'})).toBeEnabled();
  });

  it('reloads saved records after remount and never carries consent or draft feedback to another base', async () => {
    const user = await selectReview(); const form = within(screen.getByRole('region', { name: '审核稿修改' }));
    await user.type(form.getByLabelText('整份修改意见'), '尚未提交的意见'); await user.click(form.getByRole('checkbox'));
    await user.click(screen.getAllByRole('radio')[1]);
    expect(screen.queryByRole('region', { name: /的验收工作台/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '基于所选版本创建修改任务' })).toBeInTheDocument();
    vi.mocked(acceptanceClient.load).mockResolvedValue(savedIssue()); await user.click(screen.getAllByRole('radio')[0]);
    await screen.findByLabelText('第 1 页检查结论');
    const fresh = within(screen.getByRole('region', { name: '审核稿修改' }));
    expect(fresh.getByLabelText('整份修改意见')).toHaveValue(''); expect(fresh.getByRole('checkbox')).not.toBeChecked();
    expect(screen.getByRole('button', { name: '将已保存问题带入修改' })).toBeEnabled();
    expect(acceptanceClient.load).toHaveBeenCalledTimes(2); expect(api.reviewRevise).not.toHaveBeenCalled();
  });
});
