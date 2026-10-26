import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { api, ApiError } from '../api';
import { defaultSettings, type Project } from '../domain';

vi.mock('../api',async original=>({...await original<typeof import('../api')>(),api:{
 me:vi.fn(),projects:vi.fn(),project:vi.fn(),versions:vi.fn(),acknowledge:vi.fn(),upload:vi.fn(),
}}));

const project:Project={id:'upload-project',title:'上传状态项目',revision:1,stage:'draft',
 settings:defaultSettings,assets:[],input_snapshot_hash:''};
function deferred<T>() {
 let resolve!:(value:T)=>void;
 const promise=new Promise<T>(done=>{resolve=done;});
 return {promise,resolve};
}
beforeEach(()=>{
 vi.resetAllMocks();localStorage.setItem('ppt:selected-project',project.id);
 vi.mocked(api.me).mockResolvedValue({subject:'fixture-owner',name:'测试账号'});
 vi.mocked(api.projects).mockResolvedValue([project]);
 vi.mocked(api.project).mockResolvedValue(project);
 vi.mocked(api.versions).mockResolvedValue([]);
 vi.mocked(api.acknowledge).mockResolvedValue({...project,external_model_ack:true});
});
async function open() {
 const user=userEvent.setup();render(<App/>);
 await screen.findByRole('heading',{name:project.title});
 await user.click(screen.getByRole('checkbox'));
 return user;
}

describe('upload waiting and recovery',()=>{
 it('shows the current file and batch count, cancels waiting and unlocks without sending the next file',async()=>{
  let signal:AbortSignal|undefined;
  vi.mocked(api.upload).mockImplementation((_project,_file,requestSignal)=>new Promise((_,reject)=>{
   signal=requestSignal;signal?.addEventListener('abort',()=>reject(new ApiError('REQUEST_CANCELLED')),{once:true});
  }));
  const user=await open();
  await user.upload(screen.getByLabelText('上传源资料'),[new File(['first'],'第一份.md'),new File(['second'],'第二份.md')]);
  await screen.findByText('正在上传第 1 / 2 个文件');
  expect(screen.getByText('第一份.md')).toBeInTheDocument();
  expect(screen.getByLabelText('上传源资料')).toBeDisabled();
  expect(screen.getByRole('button',{name:'刷新状态'})).toBeDisabled();
  await user.click(screen.getByRole('tab',{name:/沟通与方案/}));
  expect(screen.getByText('正在上传第 1 / 2 个文件')).toBeInTheDocument();
  await user.click(screen.getByRole('button',{name:'取消等待'}));
  await screen.findByText('上传等待已结束');
  expect(signal?.aborted).toBe(true);
  expect(screen.getByRole('alert')).toHaveTextContent('文件可能已被服务器收到');
  await user.click(screen.getByRole('tab',{name:/资料与要求/}));
  expect(screen.getByLabelText('上传源资料')).toBeEnabled();
  expect(screen.getByRole('button',{name:'刷新状态'})).toBeEnabled();
  expect(api.upload).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole('button',{name:'刷新状态'}));
  await waitFor(()=>expect(screen.queryByText('上传等待已结束')).not.toBeInTheDocument());
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
 });

 it('advances the actual batch and keeps feedback and cancellation until the final refresh settles',async()=>{
  const first=deferred<unknown>();const second=deferred<unknown>();const versions=deferred<[]>();
  vi.mocked(api.upload).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  vi.mocked(api.versions).mockResolvedValueOnce([]).mockReturnValueOnce(versions.promise);
  const user=await open();
  await user.upload(screen.getByLabelText('上传源资料'),[new File(['first'],'第一份.md'),new File(['second'],'第二份.md')]);
  await screen.findByText('正在上传第 1 / 2 个文件');
  await act(async()=>{first.resolve({id:'first'});});
  await screen.findByText('正在上传第 2 / 2 个文件');
  expect(screen.getByText('第二份.md')).toBeInTheDocument();
  await act(async()=>{second.resolve({id:'second'});});
  await screen.findByText('正在更新资料列表');
  expect(screen.getByText(/2 \/ 2 个文件已确认收到/)).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'取消等待'})).toBeEnabled();
  expect(screen.getByLabelText('上传源资料')).toBeDisabled();
  await act(async()=>{versions.resolve([]);});
  await screen.findByText('资料上传完成');
  expect(screen.getByLabelText('上传源资料')).toBeEnabled();
  expect(screen.queryByRole('button',{name:'取消等待'})).not.toBeInTheDocument();
 });

 it('describes refresh failure after a confirmed upload without calling the upload failed or sending it again',async()=>{
  vi.mocked(api.upload).mockResolvedValue({id:'received-asset'});
  vi.mocked(api.project).mockResolvedValueOnce(project).mockRejectedValueOnce(new ApiError('NETWORK_ERROR'));
  const user=await open();
  await user.upload(screen.getByLabelText('上传源资料'),new File(['received'],'已收到.md'));
  await screen.findByRole('alert');
  expect(screen.getByRole('alert')).toHaveTextContent('文件已确认上传，部分状态尚未更新');
  expect(screen.getByText(/1 \/ 1 个文件已确认收到/)).toBeInTheDocument();
  expect(document.body.textContent).not.toContain('上传失败');
  expect(api.upload).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText('上传源资料')).toBeEnabled();
 });

 it('restores upload and refresh controls after timeout and explains the uncertain server result',async()=>{
  vi.mocked(api.upload).mockRejectedValue(new ApiError('REQUEST_TIMEOUT'));
  const user=await open();
  await user.upload(screen.getByLabelText('上传源资料'),new File(['maybe received'],'待核对.md'));
  await waitFor(()=>expect(screen.getByRole('alert')).toHaveTextContent('等待上传结果超时'));
  expect(screen.getByRole('alert')).toHaveTextContent('文件可能已被服务器收到');
  expect(screen.getByLabelText('上传源资料')).toBeEnabled();
  expect(screen.getByRole('button',{name:'刷新状态'})).toBeEnabled();
  expect(api.upload).toHaveBeenCalledTimes(1);
 });

 it('keeps the first confirmed receipt when its refresh fails and never sends the rest of the batch',async()=>{
  vi.mocked(api.upload).mockResolvedValue({id:'first-received'});
  vi.mocked(api.project).mockResolvedValueOnce(project).mockRejectedValueOnce(new ApiError('NETWORK_ERROR'));
  const user=await open();
  await user.upload(screen.getByLabelText('上传源资料'),[new File(['received'],'第一份.md'),new File(['not sent'],'第二份.md')]);
  await screen.findByRole('alert');
  expect(screen.getByRole('alert')).toHaveTextContent('已确认收到部分文件');
  expect(screen.getByRole('alert')).toHaveTextContent('本批其余文件尚未发送');
  expect(screen.getByText(/1 \/ 2 个文件已确认收到/)).toBeInTheDocument();
  expect(api.upload).toHaveBeenCalledTimes(1);
  expect(vi.mocked(api.upload).mock.calls[0][1].name).toBe('第一份.md');
  expect(screen.getByLabelText('上传源资料')).toBeEnabled();
  expect(screen.getByRole('button',{name:'刷新状态'})).toBeEnabled();
 });
});
