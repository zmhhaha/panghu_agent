import { act,fireEvent,render,screen,waitFor,within } from '@testing-library/react';
import { beforeEach,describe,expect,it,vi } from 'vitest';
import App from '../App';
import { api,consumeEvents } from '../api';
import { defaultSettings,type Job,type Project } from '../domain';
import { JobTiming } from '../JobTiming';

vi.mock('../api',async original=>({...await original<typeof import('../api')>(),api:{me:vi.fn(),projects:vi.fn(),project:vi.fn(),versions:vi.fn(),job:vi.fn(),generate:vi.fn()},consumeEvents:vi.fn()}));
const project:Project={id:'p',title:'运行中的规划项目',revision:2,stage:'planning',settings:defaultSettings,assets:[],input_snapshot_hash:'hash',active_job_id:'j',external_model_ack:true};
const queued={id:'j',project_id:'p',kind:'plan_initial',status:'queued',stage:'plan_initial',last_event_seq:1};
beforeEach(()=>{
 vi.clearAllMocks();Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});localStorage.setItem('ppt:selected-project','p');
 vi.mocked(api.me).mockResolvedValue({subject:'owner',name:'测试账号'});vi.mocked(api.projects).mockResolvedValue([project]);
 vi.mocked(api.project).mockResolvedValue(project);vi.mocked(api.versions).mockResolvedValue([]);vi.mocked(consumeEvents).mockImplementation(()=>new Promise(()=>{}));
});

describe('workspace job synchronization',()=>{
 it('shows queue and recorded deadline timing from refreshed server snapshots without inventing progress',async()=>{
  const created='2026-10-29T01:00:00+00:00',deadline='2026-10-29T01:30:00+00:00';
  vi.mocked(api.job).mockResolvedValueOnce({...queued,created_at:created,deadline:null,phase_timeout_seconds:600})
   .mockResolvedValue({...queued,status:'validating',stage:'validating',last_event_seq:3,created_at:created,deadline,phase_timeout_seconds:600});
  render(<App/>);await screen.findByText('正在排队，尚未开始本阶段计时。');
  let timing=within(screen.getByRole('group',{name:'任务时间'}));
  expect(timing.getByText('当前阶段配置时限：10 分钟，不是预计完成时间。')).toBeInTheDocument();
  expect(timing.queryByText(/执行截止时间/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'刷新状态'}));
  await waitFor(()=>expect(within(screen.getByRole('region',{name:'当前任务'})).getByRole('status')).toHaveTextContent('检查与渲染'));
  timing=within(screen.getByRole('group',{name:'任务时间'}));
  expect(timing.queryByText(/正在排队/)).not.toBeInTheDocument();
  expect(timing.getByText(/本阶段执行截止时间/)).toBeInTheDocument();
  expect(screen.getByRole('group',{name:'任务时间'}).querySelectorAll('time')[1]).toHaveAttribute('datetime','2026-10-29T01:30:00.000Z');
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
 });

 it('keeps legacy and malformed timing optional and labels a terminal deadline as historical',()=>{
  const view=render(<JobTiming job={queued}/>);
  expect(screen.queryByRole('group',{name:'任务时间'})).not.toBeInTheDocument();
  view.rerender(<JobTiming job={{...queued,created_at:'not-a-date',deadline:'invalid',phase_timeout_seconds:-1}}/>);
  expect(screen.queryByRole('group',{name:'任务时间'})).not.toBeInTheDocument();
  view.rerender(<JobTiming job={{...queued,status:'failed',kind:'generate',created_at:'2026-10-28T15:46:15+00:00',deadline:'2026-10-28T16:16:17+00:00',phase_timeout_seconds:1800}}/>);
  expect(screen.getByText(/本阶段原执行截止时间/)).toBeInTheDocument();
  expect(screen.getByText('当前阶段配置时限：30 分钟，不是预计完成时间。')).toBeInTheDocument();
  expect(screen.queryByText(/正在排队/)).not.toBeInTheDocument();
 });

 it('manual refresh reads the active job as well as project and versions',async()=>{
  vi.mocked(api.job).mockResolvedValueOnce(queued).mockResolvedValue({...queued,status:'running',last_event_seq:2});
  render(<App/>);await screen.findByText('排队中');
  const reads=vi.mocked(api.project).mock.calls.length,versions=vi.mocked(api.versions).mock.calls.length;
  fireEvent.click(screen.getByRole('button',{name:'刷新状态'}));
  await waitFor(()=>expect(within(screen.getByRole('region',{name:'当前任务'})).getByRole('status')).toHaveTextContent('执行中'));
  expect(api.job).toHaveBeenCalledTimes(2);expect(vi.mocked(api.project).mock.calls.length).toBeGreaterThan(reads);expect(vi.mocked(api.versions).mock.calls.length).toBeGreaterThan(versions);
 });

 it('a real empty-payload terminal event refreshes the project plan from the server',async()=>{
  let event!:Parameters<typeof consumeEvents>[3];
  vi.mocked(api.job).mockResolvedValueOnce({...queued,status:'running'}).mockResolvedValue({...queued,status:'succeeded',last_event_seq:3});
  vi.mocked(consumeEvents).mockImplementation((_id,_cursor,_signal,receiver)=>{event=receiver;return new Promise(()=>{});});
  render(<App/>);await screen.findByText('执行中');
  fireEvent.click(screen.getByRole('tab',{name:/沟通与方案/}));
  vi.mocked(api.project).mockResolvedValue({...project,active_job_id:null,stage:'awaiting_confirmation',plan:{revision:'1',stage:'communication',input_snapshot_hash:'hash',communication:{summary:'服务端真实规划结果',template_options:[]}}});
  await act(async()=>event({seq:3,type:'job.succeeded',payload:{}}));
  await screen.findByText('服务端真实规划结果');expect(api.project).toHaveBeenCalledTimes(2);
 });

 it('keeps the terminal plan when an earlier manual project fetch arrives late',async()=>{
  let resolveOld!:(value:Project)=>void;
  vi.mocked(api.job).mockResolvedValueOnce({...queued,status:'running'}).mockResolvedValue({...queued,status:'succeeded',last_event_seq:3});
  render(<App/>);await screen.findByText('执行中');fireEvent.click(screen.getByRole('tab',{name:/沟通与方案/}));
  vi.mocked(api.project).mockImplementationOnce(()=>new Promise(resolve=>{resolveOld=resolve;})).mockResolvedValue({...project,revision:3,active_job_id:null,stage:'awaiting_confirmation',plan:{revision:'1',stage:'communication',input_snapshot_hash:'hash',communication:{summary:'终态后保存的方案',template_options:[]}}});
  fireEvent.click(screen.getByRole('button',{name:'刷新状态'}));await screen.findByText('终态后保存的方案');
  await act(async()=>resolveOld(project));
  expect(screen.getByText('终态后保存的方案')).toBeInTheDocument();expect(api.job).toHaveBeenCalledTimes(2);
 });

 it('keeps the failed generate task after an automatic prepare handoff clears the active job',async()=>{
  let currentProject:Project={...project,active_job_id:null,stage:'awaiting_confirmation',confirmed_plan_revision:'2',plan:{revision:'2',stage:'directions',input_snapshot_hash:'hash',confirmed_direction_id:'direction-a',directions:['a','b','c'].map(id=>({id:`direction-${id}`,title:`方向 ${id}`,description:'已确认方案',pages:[{number:1,title:'第一页'}],production_settings:{}}))}};
  const jobs:Record<string,Job>={prepare:{...queued,id:'prepare',kind:'prepare',stage:'prepare',status:'running',created_at:'2026-10-29T01:00:00Z',deadline:'2026-10-29T01:30:00Z',phase_timeout_seconds:1800},generate:{...queued,id:'generate',kind:'generate',stage:'generate',status:'running',created_at:'2026-10-29T01:20:00Z',deadline:'2026-10-29T01:50:00Z',phase_timeout_seconds:1800}};
  const receivers=new Map<string,Parameters<typeof consumeEvents>[3]>();
  vi.mocked(api.project).mockImplementation(async()=>currentProject);
  vi.mocked(api.job).mockImplementation(async id=>jobs[id]);
  vi.mocked(consumeEvents).mockImplementation((id,_cursor,_signal,receiver)=>{receivers.set(id,receiver);return new Promise(()=>{});});
  vi.mocked(api.generate).mockImplementation(async()=>{currentProject={...currentProject,revision:3,stage:'generating',active_job_id:'prepare'};return {job_id:'prepare'};});
  render(<App/>);await screen.findByRole('heading',{name:project.title});
  fireEvent.click(screen.getByRole('tab',{name:/沟通与方案/}));
  expect(screen.queryByRole('checkbox',{name:/我同意将本项目必要/})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'生成演示稿'}));
  await screen.findByText('任务 prepare');expect(screen.getByText('制作准备')).toBeInTheDocument();
  expect(screen.getByText('制作准备完成后会自动进入演示稿制作，新阶段使用新的任务号。')).toBeInTheDocument();
  await waitFor(()=>expect(receivers.has('prepare')).toBe(true));

  jobs.prepare={...jobs.prepare,status:'succeeded',last_event_seq:2};
  currentProject={...currentProject,revision:4,active_job_id:'generate'};
  await act(async()=>receivers.get('prepare')!({seq:2,type:'job.succeeded',payload:{}}));
  await screen.findByText('任务 generate');expect(screen.getByText('制作演示稿')).toBeInTheDocument();
  expect(screen.queryByText(/制作准备完成后会自动进入/)).not.toBeInTheDocument();
  expect(screen.getByRole('group',{name:'任务时间'}).querySelectorAll('time')[1]).toHaveAttribute('datetime','2026-10-29T01:50:00.000Z');
  await waitFor(()=>expect(receivers.has('generate')).toBe(true));

  jobs.generate={...jobs.generate,status:'failed',last_event_seq:2,error_code:'render_failed'};
  currentProject={...currentProject,revision:5,stage:'draft',active_job_id:null};
  await act(async()=>receivers.get('generate')!({seq:2,type:'job.failed',payload:{}}));
  await screen.findByText('项目修订 5');
  const panel=within(screen.getByRole('region',{name:'当前任务'}));
  expect(panel.getByRole('status')).toHaveTextContent('失败');
  expect(panel.getByText('任务 generate')).toBeInTheDocument();
  expect(panel.getByRole('alert')).toHaveTextContent('预览渲染失败');
  expect(panel.getByRole('alert')).toHaveTextContent('RENDER_FAILED');
  expect(panel.queryByText('任务 prepare')).not.toBeInTheDocument();
  expect(panel.queryByText('已完成')).not.toBeInTheDocument();
  expect(vi.mocked(api.job).mock.calls.map(([id])=>id)).toEqual(['prepare','prepare','generate','generate']);

  fireEvent.click(screen.getByRole('button',{name:'刷新状态'}));
  await waitFor(()=>expect(api.project).toHaveBeenCalledTimes(5));
  expect(panel.getByRole('status')).toHaveTextContent('失败');
  expect(panel.getByText('任务 generate')).toBeInTheDocument();
 });
});
