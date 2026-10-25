import { act,fireEvent,render,screen,waitFor,within } from '@testing-library/react';
import { beforeEach,describe,expect,it,vi } from 'vitest';
import App from '../App';
import { api,consumeEvents } from '../api';
import { defaultSettings,type Project } from '../domain';

vi.mock('../api',async original=>({...await original<typeof import('../api')>(),api:{me:vi.fn(),projects:vi.fn(),project:vi.fn(),versions:vi.fn(),job:vi.fn()},consumeEvents:vi.fn()}));
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
});
