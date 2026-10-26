import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { api } from '../api';
import { defaultSettings, type Project } from '../domain';

vi.mock('../api',async original=>({...await original<typeof import('../api')>(),api:{
 me:vi.fn(),projects:vi.fn(),project:vi.fn(),versions:vi.fn(),plan:vi.fn(),generate:vi.fn(),confirm:vi.fn(),removeAsset:vi.fn(),
}}));
const project:Project={id:'parse-blocked',title:'资料解析前置检查',revision:1,stage:'draft',
 settings:defaultSettings,input_snapshot_hash:'snapshot',assets:[
  {id:'source',display_name:'学习资料.pdf',bytes:1000,parse_status:'failed',parse_error:'pdf_page_limit'},
 ]};
beforeEach(()=>{
 vi.resetAllMocks();localStorage.setItem('ppt:selected-project',project.id);
 vi.mocked(api.me).mockResolvedValue({subject:'fixture',name:'测试账号'});
 vi.mocked(api.projects).mockResolvedValue([project]);vi.mocked(api.project).mockResolvedValue(project);
 vi.mocked(api.versions).mockResolvedValue([]);
});
afterEach(()=>{vi.restoreAllMocks();});
async function openPlanning() {
 const user=userEvent.setup();render(<App/>);
 await screen.findByRole('heading',{name:project.title});
 await user.click(screen.getByRole('tab',{name:/沟通与方案/}));
 await user.click(screen.getByRole('checkbox'));
 return user;
}

describe('planning requires completed source parsing',()=>{
 it('shows the failed source and exact page-limit guidance before blocking an initial planning request',async()=>{
  const user=await openPlanning();
  const status=within(screen.getByRole('region',{name:'待处理资料解析状态'}));
  expect(status.getByText('学习资料.pdf')).toBeInTheDocument();
  expect(status.getByRole('status',{name:'学习资料.pdf解析状态'})).toHaveTextContent('解析失败');
  expect(status.getByText(/PDF 超过 150 页解析限制/)).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'开始规划'})).toBeDisabled();
  await user.click(screen.getByRole('button',{name:'开始规划'}));
  expect(api.plan).not.toHaveBeenCalled();expect(api.generate).not.toHaveBeenCalled();
 });

 it.each([
  ['queued','等待解析'],['parsing','解析中'],['stopping','正在停止解析'],['pending','等待解析'],
 ])('shows real %s status and prevents planning while a source is unfinished',async(parse_status,label)=>{
  vi.mocked(api.project).mockResolvedValue({...project,assets:[{...project.assets[0],parse_status,parse_error:null}]});
  await openPlanning();
  expect(screen.getByRole('status',{name:'学习资料.pdf解析状态'})).toHaveTextContent(label);
  expect(screen.getByRole('button',{name:'开始规划'})).toBeDisabled();
  expect(screen.getByText(/请先等待资料解析完成，或处理失败材料/)).toBeInTheDocument();
  expect(api.plan).not.toHaveBeenCalled();
 });

 it.each(['ready','parsed'])('allows an explicitly requested plan for successfully %s sources',async(parse_status)=>{
  vi.mocked(api.project).mockResolvedValue({...project,assets:[{...project.assets[0],parse_status,parse_error:null}]});
  vi.mocked(api.plan).mockImplementation(()=>new Promise<{job_id:string}>(()=>{}));
  const user=await openPlanning();
  expect(screen.queryByRole('region',{name:'待处理资料解析状态'})).not.toBeInTheDocument();
  const start=screen.getByRole('button',{name:'开始规划'});expect(start).toBeEnabled();
  await user.click(start);await waitFor(()=>expect(api.plan).toHaveBeenCalledTimes(1));
  expect(api.generate).not.toHaveBeenCalled();expect(api.confirm).not.toHaveBeenCalled();
 });

 it('blocks feedback replanning too and explains the text limit without echoing source internals',async()=>{
  vi.mocked(api.project).mockResolvedValue({...project,plan:{revision:'1',input_snapshot_hash:'snapshot',stage:'directions'},
   assets:[{...project.assets[0],parse_error:'text_limit'}]});
  const user=await openPlanning();
  expect(screen.getByText(/单文件文本超过 50 万字符解析限制/)).toBeInTheDocument();
  const replan=screen.getByRole('button',{name:'提交反馈并重新规划'});
  expect(replan).toBeDisabled();await user.click(replan);expect(api.plan).not.toHaveBeenCalled();
 });

 it('restores planning only after a real refreshed source state becomes ready',async()=>{
  vi.mocked(api.project).mockResolvedValue({...project,assets:[{...project.assets[0],parse_status:'queued',parse_error:null}]});
  const user=await openPlanning();expect(screen.getByRole('button',{name:'开始规划'})).toBeDisabled();
  vi.mocked(api.project).mockResolvedValue({...project,assets:[{...project.assets[0],parse_status:'ready',parse_error:null}]});
  await user.click(screen.getByRole('button',{name:'刷新状态'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'开始规划'})).toBeEnabled());
  expect(screen.queryByRole('region',{name:'待处理资料解析状态'})).not.toBeInTheDocument();
 });

 it('keeps an unknown failure private while still identifying the blocked source',async()=>{
  vi.mocked(api.project).mockResolvedValue({...project,assets:[{...project.assets[0],parse_error:'/work/private secret-token stack'}]});
  await openPlanning();
  expect(screen.getByText('学习资料.pdf')).toBeInTheDocument();
  expect(screen.getByText(/资料解析未完成，请检查文件后重新上传/)).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/secret-token|\/work\/private|stack/);
  expect(screen.getByRole('button',{name:'开始规划'})).toBeDisabled();
 });

 it('states the existing PDF and per-file text limits alongside upload size limits',async()=>{
  render(<App/>);await screen.findByRole('heading',{name:project.title});
  expect(screen.getByText(/带文本层 PDF 最多 150 页；单文件文本最多 50 万字符/)).toBeInTheDocument();
  expect(screen.getByText(/25 MiB \/ 文件/)).toBeInTheDocument();
 });

 it('removes a failed source only after confirmation and refreshes the remaining ready sources to unlock planning',async()=>{
  const ready={id:'usable',display_name:'可用资料.md',bytes:100,parse_status:'ready',parse_error:null};
  const initial={...project,assets:[...project.assets,ready]};
  const remaining={...project,revision:2,assets:[ready]};
  vi.mocked(api.project).mockResolvedValueOnce(initial).mockResolvedValue(remaining);
  vi.mocked(api.removeAsset).mockResolvedValue({revision:2});
  const confirm=vi.spyOn(window,'confirm').mockReturnValue(true);
  const user=await openPlanning();expect(screen.getByRole('button',{name:'开始规划'})).toBeDisabled();
  await user.click(screen.getByRole('tab',{name:/资料与要求/}));
  await user.click(screen.getByRole('button',{name:'移除资料 学习资料.pdf'}));
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('已有历史版本不受影响'));
  await waitFor(()=>expect(api.removeAsset).toHaveBeenCalledWith(initial,'source'));
  await waitFor(()=>expect(screen.queryByText('学习资料.pdf')).not.toBeInTheDocument());
  expect(screen.getByText('可用资料.md')).toBeInTheDocument();
  await user.click(screen.getByRole('tab',{name:/沟通与方案/}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'开始规划'})).toBeEnabled());
  expect(screen.queryByRole('region',{name:'待处理资料解析状态'})).not.toBeInTheDocument();
 });

 it('keeps a failed source and makes no removal request when confirmation is cancelled',async()=>{
  const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
  const user=userEvent.setup();render(<App/>);await screen.findByRole('heading',{name:project.title});
  await user.click(screen.getByRole('button',{name:'移除资料 学习资料.pdf'}));
  expect(confirm).toHaveBeenCalledTimes(1);expect(api.removeAsset).not.toHaveBeenCalled();
  expect(screen.getByText('学习资料.pdf')).toBeInTheDocument();
  expect(api.project).toHaveBeenCalledTimes(1);
 });
});
