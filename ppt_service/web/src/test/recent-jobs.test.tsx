import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { api, ApiError, consumeEvents } from '../api';
import { defaultSettings, type Job, type Project } from '../domain';
import { RecentJobs } from '../RecentJobs';

vi.mock('../api',async original=>({...await original<typeof import('../api')>(),api:{
 me:vi.fn(),projects:vi.fn(),project:vi.fn(),versions:vi.fn(),job:vi.fn(),jobs:vi.fn(),cancel:vi.fn(),generate:vi.fn(),plan:vi.fn(),revise:vi.fn(),reviewRevise:vi.fn(),
},consumeEvents:vi.fn()}));

const oldId='43133abb-2e94-4560-860a-2c1504f8ed42';
const failed:Job={id:oldId,project_id:'p',kind:'generate',stage:'validating',status:'failed',last_event_seq:17,error_code:'quality_gate_failed',created_at:'2026-11-03T01:00:00Z',deadline:'2026-11-03T01:30:00Z',phase_timeout_seconds:1800,quality_diagnostics:{schema_version:1,error_code:'external_svg_resource',stage:'source_binding',title:'页面引用检查未通过',message:'<img src=x onerror="alert(1)">',suggestion:'请管理员核对引用类型。',retry_stage:'validation',publication_authorized:false,diagnostic_download_url:`/api/v1/jobs/${oldId}/diagnostics`}};
const running:Job={id:'current-job',project_id:'p',kind:'revise',stage:'revise',status:'running',last_event_seq:2};
const project:Project={id:'p',title:'查看过往制作记录',revision:9,stage:'revising',settings:defaultSettings,assets:[],input_snapshot_hash:'snapshot',external_model_ack:true,active_job_id:running.id,latest_job:running};

beforeEach(()=>{
 vi.resetAllMocks();Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});
 vi.mocked(api.me).mockResolvedValue({subject:'owner',name:'测试账号'});
 vi.mocked(api.projects).mockResolvedValue([project]);vi.mocked(api.project).mockResolvedValue(project);
 vi.mocked(api.versions).mockResolvedValue([]);vi.mocked(api.job).mockResolvedValue(running);
 vi.mocked(api.jobs).mockResolvedValue([failed]);vi.mocked(consumeEvents).mockImplementation(()=>new Promise(()=>{}));
});

async function expand() {
 await userEvent.click(screen.getByText('最近任务',{selector:'summary'}));
 return screen.findByRole('list',{name:'最近任务记录'});
}

describe('read-only recent task history',()=>{
 it('reads only on expansion and can reselect an older failure and its diagnostic after a full reload',async()=>{
  const view=render(<RecentJobs projectId="p" refreshKey="9"/>);
  expect(screen.getByText('最近任务').closest('details')).not.toHaveAttribute('open');
  expect(api.jobs).not.toHaveBeenCalled();
  await expand();await userEvent.click(screen.getByRole('button',{name:`查看制作演示稿任务 ${oldId}`}));
  let detail=within(screen.getByRole('region',{name:'所选任务详情'}));
  expect(detail.getByText('页面引用检查未通过')).toBeInTheDocument();
  expect(detail.getByText('<img src=x onerror="alert(1)">')).toBeInTheDocument();
  expect(screen.getByRole('region',{name:'所选任务详情'}).querySelector('img')).toBeNull();
  expect(detail.getByRole('link',{name:'下载诊断报告（JSON）'})).toHaveAttribute('href',`/api/v1/jobs/${oldId}/diagnostics`);
  expect(detail.getByText(/本阶段原执行截止时间/)).toBeInTheDocument();
  expect(detail.getByRole('group',{name:'任务时间'}).querySelectorAll('time')[0]).toHaveAttribute('datetime','2026-11-03T01:00:00.000Z');
  expect(detail.queryByRole('button')).not.toBeInTheDocument();
  view.unmount();render(<RecentJobs projectId="p" refreshKey="9"/>);
  expect(screen.queryByRole('region',{name:'所选任务详情'})).not.toBeInTheDocument();
  await expand();await userEvent.click(screen.getByRole('button',{name:`查看制作演示稿任务 ${oldId}`}));
  detail=within(screen.getByRole('region',{name:'所选任务详情'}));
  expect(detail.getByText('页面引用检查未通过')).toBeInTheDocument();
  expect(api.jobs).toHaveBeenCalledTimes(2);expect(api.job).not.toHaveBeenCalled();expect(consumeEvents).not.toHaveBeenCalled();
 });

 it('leaves the active task and its live stream untouched when selecting terminal history',async()=>{
  localStorage.setItem('ppt:selected-project','p');vi.mocked(api.jobs).mockResolvedValue([running,failed]);
  render(<App/>);await screen.findByRole('heading',{name:project.title});
  await waitFor(()=>expect(consumeEvents).toHaveBeenCalledOnce());
  await expand();
  const revisionRow=screen.getByRole('button',{name:`查看按反馈修改任务 ${running.id}`});
  expect(revisionRow).toHaveTextContent('按反馈修改');
  await userEvent.click(revisionRow);
  expect(within(screen.getByRole('region',{name:'所选任务详情'})).getByRole('heading',{name:'按反馈修改 · 执行中'})).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button',{name:`查看制作演示稿任务 ${oldId}`}));
  const current=within(screen.getByRole('region',{name:'当前任务'}));
  expect(current.getByText(`任务 ${running.id}`)).toBeInTheDocument();expect(current.getByRole('status')).toHaveTextContent('执行中');
  expect(current.getByText('按反馈修改')).toBeInTheDocument();
  expect(current.getByRole('button',{name:'取消任务'})).toBeEnabled();
  expect(within(screen.getByRole('region',{name:'所选任务详情'})).queryByRole('button')).not.toBeInTheDocument();
  expect(vi.mocked(api.job).mock.calls.map(([id])=>id)).toEqual([running.id]);
  expect(vi.mocked(consumeEvents).mock.calls.map(([id])=>id)).toEqual([running.id]);
  expect(api.cancel).not.toHaveBeenCalled();expect(api.generate).not.toHaveBeenCalled();expect(api.plan).not.toHaveBeenCalled();expect(api.revise).not.toHaveBeenCalled();expect(api.reviewRevise).not.toHaveBeenCalled();
 });

 it.each([401,404])('removes previously visible history if refreshing loses access (%s)',async status=>{
  render(<RecentJobs projectId="p" refreshKey="9"/>);await expand();
  await userEvent.click(screen.getByRole('button',{name:`查看制作演示稿任务 ${oldId}`}));
  expect(screen.getByRole('region',{name:'所选任务详情'})).toBeInTheDocument();
  vi.mocked(api.jobs).mockRejectedValue(new ApiError('private /work/server SECRET',status));
  await userEvent.click(screen.getByRole('button',{name:'刷新任务记录'}));
  await waitFor(()=>expect(screen.queryByRole('region',{name:'所选任务详情'})).not.toBeInTheDocument());
  expect(screen.getByRole('alert')).toBeInTheDocument();
  expect(screen.queryByRole('list',{name:'最近任务记录'})).not.toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/SECRET|\/work\/server/);
  if(status===401)expect(screen.getByRole('link',{name:'重新登录'})).toHaveAttribute('href','/oauth2/start');
 });

 it('aborts an old project read and ignores its late response after the project changes',async()=>{
  let resolveOld!:(jobs:Job[])=>void;
  vi.mocked(api.jobs).mockImplementationOnce(()=>new Promise(resolve=>{resolveOld=resolve;})).mockResolvedValue([{...failed,id:'other-history',project_id:'q',quality_diagnostics:undefined}]);
  const view=render(<RecentJobs projectId="p" refreshKey="9"/>);
  await userEvent.click(screen.getByText('最近任务',{selector:'summary'}));await waitFor(()=>expect(api.jobs).toHaveBeenCalledOnce());
  const oldSignal=vi.mocked(api.jobs).mock.calls[0][1];
  view.rerender(<RecentJobs projectId="q" refreshKey="1"/>);
  await screen.findByRole('button',{name:'查看制作演示稿任务 other-history'});
  expect(oldSignal?.aborted).toBe(true);
  await act(async()=>resolveOld([failed]));
  expect(screen.queryByRole('button',{name:`查看制作演示稿任务 ${oldId}`})).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'查看制作演示稿任务 other-history'})).toBeInTheDocument();
 });
});
