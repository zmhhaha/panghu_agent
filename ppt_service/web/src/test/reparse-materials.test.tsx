import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { api, ApiError } from '../api';
import { defaultSettings, type Project } from '../domain';

vi.mock('../api',async original=>({...await original<typeof import('../api')>(),api:{
 me:vi.fn(),projects:vi.fn(),project:vi.fn(),versions:vi.fn(),plan:vi.fn(),generate:vi.fn(),confirm:vi.fn(),
 removeAsset:vi.fn(),reparseAsset:vi.fn(),job:vi.fn(),
}}));

const fileName='需要重试的资料.pdf';
const project:Project={id:'retry-project',title:'资料解析重试',revision:1,stage:'draft',
 settings:defaultSettings,input_snapshot_hash:'snapshot',assets:[
  {id:'retry-source',display_name:fileName,bytes:1000,parse_status:'failed',parse_error:'parse_timeout'},
 ]};
const queued:Project={...project,revision:2,assets:[{...project.assets[0],parse_status:'queued',parse_error:null}]};
const retryName=`重新解析文件 ${fileName}`;

beforeEach(()=>{
 vi.resetAllMocks();localStorage.setItem('ppt:selected-project',project.id);
 vi.mocked(api.me).mockResolvedValue({subject:'fixture',name:'测试账号'});
 vi.mocked(api.projects).mockResolvedValue([project]);vi.mocked(api.project).mockResolvedValue(project);
 vi.mocked(api.versions).mockResolvedValue([]);
 vi.mocked(api.reparseAsset).mockResolvedValue({revision:2,asset_id:'retry-source',parse_status:'queued'});
});

async function openTab(name='资料与要求') {
 const user=userEvent.setup();render(<App/>);
 await screen.findByRole('heading',{name:project.title});
 if(name==='沟通与方案')await user.click(screen.getByRole('tab',{name:/沟通与方案/}));
 return user;
}

function expectNoModelTask() {
 expect(api.plan).not.toHaveBeenCalled();expect(api.generate).not.toHaveBeenCalled();expect(api.confirm).not.toHaveBeenCalled();
}

describe('retrying a failed material parser',()=>{
 it.each(['ready','parsed','queued','parsing','pending','stopping','unknown'])('does not offer another parse for a %s file in either tab',async(parse_status)=>{
  vi.mocked(api.project).mockResolvedValue({...project,assets:[{...project.assets[0],parse_status,parse_error:null}]});
  const user=await openTab();
  expect(screen.queryByRole('button',{name:retryName})).not.toBeInTheDocument();
  await user.click(screen.getByRole('tab',{name:/沟通与方案/}));
  expect(screen.queryByRole('button',{name:retryName})).not.toBeInTheDocument();
  expect(api.reparseAsset).not.toHaveBeenCalled();expectNoModelTask();
 });

 it('offers a visible retry for a failed file in both tabs before model consent',async()=>{
  const user=await openTab();
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  expect(screen.getByRole('button',{name:retryName})).toBeEnabled();
  expect(screen.getByText(/重新解析仅处理文件，不会启动规划/)).toBeInTheDocument();
  await user.click(screen.getByRole('tab',{name:/沟通与方案/}));
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  expect(screen.getByRole('button',{name:retryName})).toBeEnabled();
  expect(api.reparseAsset).not.toHaveBeenCalled();expectNoModelTask();
 });

 it.each(['资料与要求','沟通与方案'])('retries the selected file once without consent from %s and refreshes its queued state',async(tab)=>{
  vi.mocked(api.project).mockResolvedValueOnce(project).mockResolvedValue(queued);
  const user=await openTab(tab);
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  await user.click(screen.getByRole('button',{name:retryName}));
  await waitFor(()=>expect(api.reparseAsset).toHaveBeenCalledWith(project,'retry-source'));
  expect(api.reparseAsset).toHaveBeenCalledTimes(1);
  await waitFor(()=>expect(screen.getByRole('status',{name:`${fileName}解析状态`})).toHaveTextContent('等待解析'));
  expect(screen.queryByRole('button',{name:retryName})).not.toBeInTheDocument();
  expect(screen.getByRole('checkbox')).not.toBeChecked();expectNoModelTask();
 });

 it('uses the refreshed project state when the retry response alone says queued',async()=>{
  const user=await openTab();
  await user.click(screen.getByRole('button',{name:retryName}));
  await waitFor(()=>expect(api.project).toHaveBeenCalledTimes(2));
  await waitFor(()=>expect(screen.getByRole('button',{name:retryName})).toBeEnabled());
  expect(screen.getByRole('status',{name:`${fileName}解析状态`})).toHaveTextContent('解析失败');
  expect(api.reparseAsset).toHaveBeenCalledTimes(1);expectNoModelTask();
 });

 it('disables retry across both tabs while its request is pending and prevents duplicate requests',async()=>{
  vi.mocked(api.reparseAsset).mockImplementation(()=>new Promise<never>(()=>{}));
  const user=await openTab();
  await user.click(screen.getByRole('button',{name:retryName}));
  await waitFor(()=>expect(api.reparseAsset).toHaveBeenCalledTimes(1));
  expect(screen.getByRole('button',{name:retryName})).toBeDisabled();
  await user.click(screen.getByRole('button',{name:retryName}));
  await user.click(screen.getByRole('tab',{name:/沟通与方案/}));
  expect(screen.getByRole('button',{name:retryName})).toBeDisabled();
  await user.click(screen.getByRole('button',{name:retryName}));
  expect(api.reparseAsset).toHaveBeenCalledTimes(1);expectNoModelTask();
 });

 it('disables retry in both tabs while the project has an active model job',async()=>{
  vi.mocked(api.project).mockResolvedValue({...project,active_job_id:'active-job'});
  vi.mocked(api.job).mockImplementation(()=>new Promise<never>(()=>{}));
  const user=await openTab();
  expect(screen.getByRole('button',{name:retryName})).toBeDisabled();
  await user.click(screen.getByRole('button',{name:retryName}));
  await user.click(screen.getByRole('tab',{name:/沟通与方案/}));
  expect(screen.getByRole('button',{name:retryName})).toBeDisabled();
  await user.click(screen.getByRole('button',{name:retryName}));
  expect(api.reparseAsset).not.toHaveBeenCalled();expectNoModelTask();
 });

 it('shows a safe retry failure and retains the failed file without starting a model task',async()=>{
  vi.mocked(api.reparseAsset).mockRejectedValue(new ApiError('ASSET_PARSE_BUSY',409));
  const user=await openTab('沟通与方案');
  await user.click(screen.getByRole('button',{name:retryName}));
  expect(await screen.findByText(/ASSET_PARSE_BUSY/)).toHaveTextContent('HTTP 409');
  expect(screen.getByRole('status',{name:`${fileName}解析状态`})).toHaveTextContent('解析失败');
  expect(screen.getByRole('button',{name:retryName})).toBeEnabled();
  expect(api.reparseAsset).toHaveBeenCalledTimes(1);expectNoModelTask();
 });
});
