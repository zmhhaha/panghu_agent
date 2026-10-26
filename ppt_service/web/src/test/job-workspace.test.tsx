import { act,fireEvent,render,screen,waitFor,within } from '@testing-library/react';
import { beforeEach,describe,expect,it,vi } from 'vitest';
import App from '../App';
import { api,consumeEvents } from '../api';
import { defaultSettings,type Job,type Project } from '../domain';

vi.mock('../api',async original=>({...await original<typeof import('../api')>(),api:{me:vi.fn(),projects:vi.fn(),project:vi.fn(),versions:vi.fn(),job:vi.fn(),generate:vi.fn()},consumeEvents:vi.fn()}));
const project:Project={id:'p',title:'运行中的规划项目',revision:2,stage:'planning',settings:defaultSettings,assets:[],input_snapshot_hash:'hash',active_job_id:'j'};
const queued={id:'j',project_id:'p',kind:'plan_initial',status:'queued',stage:'plan_initial',last_event_seq:1};
beforeEach(()=>{
 vi.clearAllMocks();Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});localStorage.setItem('ppt:selected-project','p');
 vi.mocked(api.me).mockResolvedValue({subject:'owner',name:'测试账号'});vi.mocked(api.projects).mockResolvedValue([project]);
 vi.mocked(api.project).mockResolvedValue(project);vi.mocked(api.versions).mockResolvedValue([]);vi.mocked(consumeEvents).mockImplementation(()=>new Promise(()=>{}));
});

describe('workspace job synchronization',()=>{
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
  const jobs:Record<string,Job>={prepare:{...queued,id:'prepare',kind:'prepare',stage:'prepare',status:'running'},generate:{...queued,id:'generate',kind:'generate',stage:'generate',status:'running'}};
  const receivers=new Map<string,Parameters<typeof consumeEvents>[3]>();
  vi.mocked(api.project).mockImplementation(async()=>currentProject);
  vi.mocked(api.job).mockImplementation(async id=>jobs[id]);
  vi.mocked(consumeEvents).mockImplementation((id,_cursor,_signal,receiver)=>{receivers.set(id,receiver);return new Promise(()=>{});});
  vi.mocked(api.generate).mockImplementation(async()=>{currentProject={...currentProject,revision:3,stage:'generating',active_job_id:'prepare'};return {job_id:'prepare'};});
  render(<App/>);await screen.findByRole('heading',{name:project.title});
  fireEvent.click(screen.getByRole('tab',{name:/沟通与方案/}));
  fireEvent.click(screen.getByRole('checkbox',{name:/我同意将本项目必要/}));
  fireEvent.click(screen.getByRole('button',{name:'生成演示稿'}));
  await screen.findByText('任务 prepare');expect(screen.getByText('制作准备')).toBeInTheDocument();
  await waitFor(()=>expect(receivers.has('prepare')).toBe(true));

  jobs.prepare={...jobs.prepare,status:'succeeded',last_event_seq:2};
  currentProject={...currentProject,revision:4,active_job_id:'generate'};
  await act(async()=>receivers.get('prepare')!({seq:2,type:'job.succeeded',payload:{}}));
  await screen.findByText('任务 generate');expect(screen.getByText('制作演示稿')).toBeInTheDocument();
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
