import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { api, ApiError, consumeEvents, safeError } from '../api';
import { defaultSettings, type Job, type Project } from '../domain';

vi.mock('../api',async original=>({...await original<typeof import('../api')>(),api:{
 me:vi.fn(),projects:vi.fn(),project:vi.fn(),versions:vi.fn(),job:vi.fn(),plan:vi.fn(),confirm:vi.fn(),generate:vi.fn(),cancel:vi.fn(),
},consumeEvents:vi.fn()}));

const failed:Job={id:'old-options',project_id:'p',kind:'plan_options',stage:'plan_options',status:'failed',last_event_seq:7,error_code:'execution_timeout'};
const running:Job={...failed,id:'new-options',status:'running',last_event_seq:2,error_code:undefined};
const initial:Project={
 id:'p',title:'规划恢复项目',revision:34,stage:'draft',settings:defaultSettings,input_snapshot_hash:'snapshot',external_model_ack:true,
 assets:[{id:'source',display_name:'学习资料.pdf',bytes:18000,parse_status:'ready'}],active_job_id:null,latest_job:failed,confirmed_plan_revision:'1',
 plan:{revision:'1',stage:'communication',input_snapshot_hash:'snapshot',confirmed_at:'2026-10-26T12:00:00Z',communication:{summary:'已核对的初步沟通',template_options:[]}},
};
let current:Project;
beforeEach(()=>{
 vi.resetAllMocks();Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});
 localStorage.setItem('ppt:selected-project','p');current=structuredClone(initial);
 vi.mocked(api.me).mockResolvedValue({subject:'owner',name:'测试账号'});vi.mocked(api.projects).mockResolvedValue([current]);
 vi.mocked(api.project).mockImplementation(async()=>current);vi.mocked(api.versions).mockResolvedValue([]);
 vi.mocked(api.job).mockResolvedValue(running);vi.mocked(consumeEvents).mockImplementation(()=>new Promise(()=>{}));
});
async function open() {const view=render(<App/>);await screen.findByRole('heading',{name:initial.title});return view;}
async function planning() {const user=userEvent.setup();await open();await user.click(screen.getByRole('tab',{name:/沟通与方案/}));return user;}

describe('planning failure recovery from server history',()=>{
 it('restores a terminal failure on reopen without job reads, event streams or a settled refresh loop',async()=>{
  // Each manual GET returns a new DTO object; object identity must not re-settle history.
  vi.mocked(api.project).mockImplementation(async()=>({...current,latest_job:{...failed}}));
  const view=await open();
  const panel=within(screen.getByRole('region',{name:'当前任务'}));
  expect(panel.getByRole('status')).toHaveTextContent('失败');expect(panel.getByText('任务 old-options')).toBeInTheDocument();
  expect(panel.getByRole('alert')).toHaveTextContent('TIMEOUT');expect(panel.getByText('三套设计方向规划超时')).toBeInTheDocument();
  expect(panel.getByText(/初步沟通仍已确认/)).toBeInTheDocument();expect(panel.queryByRole('button',{name:'取消任务'})).not.toBeInTheDocument();
  expect(api.project).toHaveBeenCalledOnce();expect(api.job).not.toHaveBeenCalled();expect(consumeEvents).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'刷新状态'}));await waitFor(()=>expect(api.project).toHaveBeenCalledTimes(2));
  await act(async()=>{});expect(api.project).toHaveBeenCalledTimes(2);expect(api.job).not.toHaveBeenCalled();expect(consumeEvents).not.toHaveBeenCalled();
  view.unmount();await open();expect(screen.getByText('三套设计方向规划超时')).toBeInTheDocument();
  expect(api.project).toHaveBeenCalledTimes(3);expect(api.job).not.toHaveBeenCalled();expect(api.plan).not.toHaveBeenCalled();expect(api.generate).not.toHaveBeenCalled();
 });

 it('subscribes to the current active task instead of showing an older failed task',async()=>{
  current={...current,revision:35,stage:'planning',active_job_id:running.id};await open();
  await screen.findByText('任务 new-options');await waitFor(()=>expect(consumeEvents).toHaveBeenCalledOnce());
  expect(screen.queryByText('任务 old-options')).not.toBeInTheDocument();expect(screen.queryByText('三套设计方向规划超时')).not.toBeInTheDocument();
  expect(screen.getByText(/单次执行上限为 20 分钟/)).toBeInTheDocument();expect(screen.getByRole('button',{name:'取消任务'})).toBeEnabled();
  expect(vi.mocked(api.job).mock.calls.map(([id])=>id)).toEqual([running.id]);expect(api.plan).not.toHaveBeenCalled();
 });

 it('reuses saved project consent for an explicit retry and keeps the new job ahead of an old project snapshot',async()=>{
  const user=await planning();const retry=screen.getByRole('button',{name:'生成三套设计方向'});
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();expect(retry).toBeEnabled();expect(api.plan).not.toHaveBeenCalled();
  vi.mocked(api.plan).mockImplementation(async()=>{
   // Simulate a project GET still carrying a previous active ID and revision.
   current={...current,active_job_id:'old-active'};return {job_id:running.id};
  });
  await user.click(retry);await screen.findByText('任务 new-options');
  await waitFor(()=>expect(consumeEvents).toHaveBeenCalledOnce());
  expect(api.plan).toHaveBeenCalledTimes(1);expect(vi.mocked(api.job).mock.calls.map(([id])=>id)).toEqual([running.id]);
  expect(screen.queryByText('任务 old-options')).not.toBeInTheDocument();expect(screen.queryByText('任务 old-active')).not.toBeInTheDocument();
  expect(screen.queryByText('三套设计方向规划超时')).not.toBeInTheDocument();expect(api.generate).not.toHaveBeenCalled();
 });

 it('does not let an earlier project read restore a failed task after a new submission is accepted',async()=>{
  const user=await planning();let resolveOld!:(value:Project)=>void;
  vi.mocked(api.project).mockImplementationOnce(()=>new Promise(resolve=>{resolveOld=resolve;})).mockImplementation(async()=>current);
  fireEvent.click(screen.getByRole('button',{name:'刷新状态'}));
  vi.mocked(api.plan).mockImplementation(async()=>{current={...current,revision:35,active_job_id:running.id,latest_job:running};return {job_id:running.id};});
  await user.click(screen.getByRole('button',{name:'生成三套设计方向'}));
  await screen.findByText('任务 new-options');await act(async()=>resolveOld(initial));
  expect(screen.getByText('任务 new-options')).toBeInTheDocument();expect(screen.queryByText('三套设计方向规划超时')).not.toBeInTheDocument();
  expect(vi.mocked(api.job).mock.calls.map(([id])=>id)).toEqual([running.id]);expect(api.plan).toHaveBeenCalledOnce();
 });

 it('requires re-confirmation for legacy cleared pointers while retaining project consent without silently re-planning',async()=>{
  current={...current,confirmed_plan_revision:null};const user=userEvent.setup();await open();
  await user.click(screen.getByRole('button',{name:'查看规划与重试'}));
  expect(screen.getByText('初步沟通需要重新确认')).toBeInTheDocument();expect(screen.getByRole('button',{name:'请先确认沟通内容'})).toBeDisabled();
  expect(screen.queryByRole('button',{name:'生成三套设计方向'})).not.toBeInTheDocument();expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  vi.mocked(api.confirm).mockImplementation(async()=>{current={...current,revision:35,confirmed_plan_revision:'1'};return current;});
  await user.click(screen.getByRole('button',{name:'确认沟通内容与设计方式'}));
  await screen.findByRole('button',{name:'生成三套设计方向'});
  expect(api.confirm).toHaveBeenCalledWith(expect.objectContaining({confirmed_plan_revision:null}),{design_mode:'free_design',template_id:null});
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();await waitFor(()=>expect(screen.getByRole('button',{name:'生成三套设计方向'})).toBeEnabled());
  expect(api.plan).not.toHaveBeenCalled();expect(api.generate).not.toHaveBeenCalled();
  vi.mocked(api.plan).mockImplementation(async()=>{current={...current,revision:36,active_job_id:running.id,latest_job:running};return {job_id:running.id};});
  await user.click(screen.getByRole('button',{name:'生成三套设计方向'}));
  await screen.findByText('任务 new-options');expect(api.plan).toHaveBeenCalledTimes(1);expect(api.generate).not.toHaveBeenCalled();
 });

 it('lets changed materials restart initial planning without feedback or confirmation of the obsolete plan',async()=>{
  current={...current,input_snapshot_hash:'new-materials',confirmed_plan_revision:null};const user=await planning();
  expect(screen.getByText(/当前方案确认已失效/)).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'确认沟通内容与设计方式'})).toBeDisabled();
  expect(screen.queryByText('初步沟通需要重新确认')).not.toBeInTheDocument();
  const restart=screen.getByRole('button',{name:'依据最新资料重新规划'});expect(restart).toBeEnabled();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();expect(api.plan).not.toHaveBeenCalled();
  vi.mocked(api.plan).mockImplementation(()=>new Promise(()=>{}));await user.click(restart);
  expect(api.plan).toHaveBeenCalledWith(expect.objectContaining({input_snapshot_hash:'new-materials',confirmed_plan_revision:null}),'',true);
  expect(api.confirm).not.toHaveBeenCalled();expect(api.generate).not.toHaveBeenCalled();
 });

 it('requires choosing and confirming a direction after planning completes and never starts generation automatically',async()=>{
  current={...current,revision:35,stage:'planning',active_job_id:running.id,latest_job:running};
  let event!:Parameters<typeof consumeEvents>[3];
  vi.mocked(consumeEvents).mockImplementation((_id,_cursor,_signal,receiver)=>{event=receiver;return new Promise(()=>{});});
  const user=await planning();await waitFor(()=>expect(consumeEvents).toHaveBeenCalledOnce());
  const finished={...running,status:'succeeded',last_event_seq:3};
  current={...current,revision:36,stage:'awaiting_confirmation',active_job_id:null,latest_job:finished,confirmed_plan_revision:null,
   plan:{revision:'2',stage:'directions',input_snapshot_hash:'snapshot',directions:['A','B','C'].map(id=>({id,title:`方案 ${id}`,description:'真实规划方向',pages:[{number:1,title:'封面'}],production_settings:{}}))}};
  vi.mocked(api.job).mockResolvedValue(finished);
  await act(async()=>event({seq:3,type:'job.succeeded',payload:{}}));
  await screen.findByRole('radio',{name:'选择方案 B'});
  expect(screen.getByRole('region',{name:'比较设计方案'})).toBeVisible();
  for(const radio of screen.getAllByRole('radio'))expect(radio).not.toBeChecked();
  expect(screen.getByRole('button',{name:'确认所选完整方案'})).toBeDisabled();expect(screen.queryByRole('button',{name:'生成演示稿'})).not.toBeInTheDocument();
  expect(api.confirm).not.toHaveBeenCalled();expect(api.generate).not.toHaveBeenCalled();expect(api.plan).not.toHaveBeenCalled();
  vi.mocked(api.confirm).mockImplementation(async()=>{current={...current,revision:37,confirmed_plan_revision:'2',plan:{...current.plan!,confirmed_at:'2026-10-27T00:00:00Z',confirmed_direction_id:'B'}};return current;});
  await user.click(screen.getByRole('radio',{name:'选择方案 B'}));await user.click(screen.getByRole('button',{name:'确认所选完整方案'}));
  await screen.findByRole('button',{name:'生成演示稿'});await waitFor(()=>expect(screen.getByRole('button',{name:'生成演示稿'})).toBeEnabled());
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();expect(api.generate).not.toHaveBeenCalled();
  expect(api.project).toHaveBeenCalledTimes(3);expect(api.job).toHaveBeenCalledTimes(2);
 });
});

describe('planning recovery errors',()=>{
 it.each([
  ['stale_project_revision','STALE_REVISION','刷新项目'],
  ['stale_plan_revision','STALE_PLAN','重新规划'],
  ['stale_input_snapshot','SNAPSHOT_MISMATCH','材料快照'],
  ['initial_confirmation_required','INITIAL_CONFIRMATION_REQUIRED','重新确认沟通内容'],
  ['options_confirmation_required','OPTIONS_CONFIRMATION_REQUIRED','选择一个设计方向'],
 ])('explains %s without falling back to an unknown service error', (raw,code,message)=>{
  const error=new ApiError(raw,409);expect(error.code).toBe(code);expect(safeError(error)).toContain(message);expect(safeError(error)).not.toContain('SERVICE_ERROR');
 });
});
