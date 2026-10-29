import { act, fireEvent, render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, it, expect, vi } from 'vitest';
import fixture from './fixtures/official-stage2.json';
import App from '../App';
import { adaptProject, api } from '../api';
import { restoredDirection } from '../planning';
vi.mock('../api',async original=>({...await original<typeof import('../api')>(),api:{me:vi.fn(),projects:vi.fn(),project:vi.fn(),versions:vi.fn(),confirm:vi.fn(),generate:vi.fn()}}));
const hash=fixture.design.source_projection.source_input_snapshot_hash;
const raw=()=>({id:'p',title:'真实六页方案',revision:5,stage:'awaiting_confirmation',page_count:6,language:'zh',input_snapshot_hash:hash,confirmed_plan_revision:null,assets:[],plans:[{revision:7,phase:'plan_options',input_snapshot_hash:hash,outline:fixture.outline,design:fixture.design,confirmed_at:null,confirmation_selection:null}]});
beforeEach(()=>{vi.clearAllMocks();localStorage.setItem('ppt:selected-project','p');vi.mocked(api.me).mockResolvedValue({subject:'u',name:'测试账号'});vi.mocked(api.projects).mockResolvedValue([]);vi.mocked(api.project).mockResolvedValue(adaptProject(raw()));vi.mocked(api.versions).mockResolvedValue([]);});
async function open(){render(<App/>);await screen.findByRole('heading',{name:'真实六页方案'});await userEvent.click(screen.getByRole('tab',{name:/沟通与方案/}));}
function card(index:number) {return within(screen.getByRole('article',{name:fixture.design.directions[index].name}));}
function inspector(index:number) {return screen.getByRole('region',{name:`${fixture.design.directions[index].name}的完整方案`});}
const longBody=fixture.outline.pages[1].content.find(content=>content.role==='body')!.text;
describe('actual official stage-2 projection',()=>{
 it('retains all original IDs, notes, six full pages, expressions and parameters',()=>{const project=adaptProject(raw());expect(project.plan!.directions!.map(d=>d.id)).toEqual(['evidence-handbook','acceptance-console','guided-walkthrough']);for(let i=0;i<3;i++){const direction=project.plan!.directions![i];expect(direction.description).toBe(fixture.design.directions[i].note);expect(direction.pages).toHaveLength(6);expect(direction.pages[1].number).toBe(2);expect(direction.pages[1].purpose).toBe(fixture.outline.pages[1].core_message);expect(direction.pages[1].points).toContain(fixture.outline.pages[1].content.find(c=>c.role==='body')!.text);expect(direction.production_settings).toEqual(fixture.design.production_parameters);expect(direction.page_expression).toEqual(fixture.design.directions[i].page_expression);}expect(restoredDirection(project)).toBe('');});
 it('initially shows real palette and page comparisons without full text, technical ids or preselected advice',async()=>{
  await open();
  expect(screen.getByText('系统推荐仅供参考，不代表用户确认')).toBeInTheDocument();
  expect(screen.getByText('风格示意 · 实际页面将在确认后制作')).toBeInTheDocument();
  expect(screen.queryByText(longBody)).not.toBeInTheDocument();
  expect(screen.queryByText('完整设计说明',{selector:'summary'})).not.toBeInTheDocument();
  for(const [index,source] of fixture.design.directions.entries()){
   const option=card(index);
   expect(option.getByText(`${source.page_plan.pages.length} 页`)).toBeVisible();
   expect(option.getByText(source.note.split('。')[0]+'。')).toBeVisible();
   expect(option.getByText(source.color.name)).toBeVisible();
   const palette=option.getByRole('list',{name:`${source.name}的配色`});
   expect(within(palette).getAllByRole('listitem')).toHaveLength(Object.keys(source.color.palette).length);
   expect(within(palette).getByTitle(`背景 ${source.color.palette.background}`)).toHaveStyle({backgroundColor:source.color.palette.background});
   expect(within(palette).getByTitle(`强调色 ${source.color.palette.accent}`)).toHaveStyle({backgroundColor:source.color.palette.accent});
   expect(screen.queryByText(source.id,{selector:'code'})).not.toBeInTheDocument();
   expect(screen.queryByRole('region',{name:`${source.name}的完整方案`})).not.toBeInTheDocument();
  }
  expect(card(0).getAllByText('编辑排版').length).toBeGreaterThan(0);expect(card(1).getAllByText('深色科技').length).toBeGreaterThan(0);expect(card(2).getAllByText('教学引导').length).toBeGreaterThan(0);
  for(const radio of screen.getAllByRole('radio'))expect(radio).not.toBeChecked();
  expect(screen.getByRole('button',{name:'确认所选完整方案'})).toBeDisabled();
  expect(api.confirm).not.toHaveBeenCalled();expect(api.generate).not.toHaveBeenCalled();
 });
 it('keeps returned clarification questions available without placing them ahead of the initial comparison',async()=>{
  const source=raw();const questions=['请核对资料范围。','请核对目标受众。'];
  vi.mocked(api.project).mockResolvedValue(adaptProject({...source,plans:[{...source.plans[0],questions}]}));
  await open();
  for(const question of questions)expect(screen.getByText(question)).not.toBeVisible();
  await userEvent.click(screen.getByText(/待核对的信息/,{selector:'summary'}));
  for(const question of questions)expect(screen.getByText(question)).toBeVisible();
  expect(api.confirm).not.toHaveBeenCalled();expect(api.generate).not.toHaveBeenCalled();
 });
 it('opens one full inspection without selecting and preserves all pages, visual expressions and raw design fields',async()=>{
  const user=userEvent.setup();await open();
  await user.click(card(0).getByRole('button',{name:'查看内容与完整方案'}));
  const detail=within(inspector(0));
  expect(card(0).getByRole('button',{name:'查看内容与完整方案'})).toHaveAttribute('aria-expanded','true');
  expect(detail.getByText(fixture.design.directions[0].note)).toBeVisible();
  for(const radio of screen.getAllByRole('radio'))expect(radio).not.toBeChecked();
  expect(screen.getByRole('button',{name:'确认所选完整方案'})).toBeDisabled();
  expect(detail.getByText(longBody,{selector:'p'})).not.toBeVisible();
  await user.click(detail.getByText('查看逐页内容与来源',{selector:'summary'}));
  for(const page of fixture.outline.pages){
   expect(detail.getByText(`${String(page.ordinal).padStart(2,'0')} · ${page.title}`,{selector:'strong'})).toBeVisible();
   expect(detail.getByText(page.core_message,{selector:'p'})).toBeVisible();
  }
  expect(detail.getByText(longBody,{selector:'p'})).toBeVisible();
  await user.click(detail.getByText('逐页视觉表达',{selector:'summary'}));
  for(const expression of fixture.design.directions[0].page_expression)expect(detail.getByText(expression,{selector:'p'})).toBeVisible();
  await user.click(detail.getByText('完整设计说明',{selector:'summary'}));
  expect(detail.getByText(text=>text.includes(fixture.design.directions[0].typography.name),{selector:'pre'})).toBeVisible();
  expect(detail.getByText(text=>text.includes(fixture.design.directions[0].color.name),{selector:'pre'})).toBeVisible();
  await user.click(detail.getByText('制作设置',{selector:'summary'}));
  expect(detail.getAllByText(text=>text.includes(fixture.design.production_parameters.page_count.explanation),{selector:'pre'})[0]).toBeVisible();
  await user.click(detail.getByText('方案标识',{selector:'summary'}));
  expect(detail.getByText('evidence-handbook',{selector:'code'})).toBeVisible();
  await user.click(card(1).getByRole('button',{name:'查看内容与完整方案'}));
  expect(screen.queryByRole('region',{name:`${fixture.design.directions[0].name}的完整方案`})).not.toBeInTheDocument();
  expect(within(inspector(1)).getByText(fixture.design.directions[1].note)).toBeVisible();
  expect(card(0).getByRole('button',{name:'查看内容与完整方案'})).toHaveAttribute('aria-expanded','false');
  expect(screen.queryByRole('region',{name:`${fixture.design.directions[2].name}的完整方案`})).not.toBeInTheDocument();
  for(const radio of screen.getAllByRole('radio'))expect(radio).not.toBeChecked();
  expect(api.confirm).not.toHaveBeenCalled();expect(api.generate).not.toHaveBeenCalled();
 });
 it('confirms only a real user-selected exact direction id and current project payload',async()=>{const user=userEvent.setup();vi.mocked(api.confirm).mockResolvedValue(adaptProject(raw()));await open();await user.click(screen.getByRole('radio',{name:'选择B 验收工作台'}));await user.click(screen.getByRole('button',{name:'确认所选完整方案'}));await waitFor(()=>expect(api.confirm).toHaveBeenCalledWith(expect.objectContaining({revision:5,input_snapshot_hash:hash,plan:expect.objectContaining({revision:'7'})}),{direction_id:'acceptance-console'}));expect(api.generate).not.toHaveBeenCalled();});
 it('keeps the selected A payload when the user separately inspects B',async()=>{
  const user=userEvent.setup();vi.mocked(api.confirm).mockResolvedValue(adaptProject(raw()));await open();
  await user.click(screen.getByRole('radio',{name:'选择A 证据手册（推荐）'}));
  await user.click(card(1).getByRole('button',{name:'查看内容与完整方案'}));
  expect(inspector(1)).toBeVisible();
  expect(screen.getByRole('radio',{name:'选择A 证据手册（推荐）'})).toBeChecked();
  expect(screen.getByRole('radio',{name:'选择B 验收工作台'})).not.toBeChecked();
  expect(screen.getByText('将确认：',{selector:'.direction-confirm-context'})).toHaveTextContent('将确认：A 证据手册（推荐）');
  expect(api.confirm).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button',{name:'确认所选完整方案'}));
  await waitFor(()=>expect(api.confirm).toHaveBeenCalledWith(
   expect.objectContaining({revision:5,input_snapshot_hash:hash,plan:expect.objectContaining({revision:'7'})}),
   {direction_id:'evidence-handbook'},
  ));
  expect(api.confirm).toHaveBeenCalledTimes(1);
  expect(api.generate).not.toHaveBeenCalled();
 });
 it('restores only the server-confirmed selection with matching current revision and snapshot',async()=>{const confirmed={...raw(),confirmed_plan_revision:7,plans:[{...raw().plans[0],confirmed_at:'2026-10-22T00:00:00Z',confirmation_selection:{direction_id:'guided-walkthrough'}}]};vi.mocked(api.project).mockResolvedValue(adaptProject(confirmed));await open();expect(screen.getByRole('radio',{name:'选择C 分步验收讲义'})).toBeChecked();expect(screen.getByRole('radio',{name:'选择A 证据手册（推荐）'})).not.toBeChecked();expect(restoredDirection(adaptProject({...confirmed,input_snapshot_hash:'new-upload'}))).toBe('');expect(restoredDirection(adaptProject({...confirmed,confirmed_plan_revision:6}))).toBe('');});
 it('renders unknown complex public fields only in an explicit inspection as escaped inert text',async()=>{
  const source=raw();source.plans[0].design=structuredClone(fixture.design);
  Object.assign(source.plans[0].design.directions[0],{extra_public_detail:{markup:'<img src=x onerror=alert(1)>'}});
  vi.mocked(api.project).mockResolvedValue(adaptProject(source));const user=userEvent.setup();await open();
  expect(document.body.textContent).not.toContain('extra_public_detail');
  expect(document.body.textContent).not.toContain('<img src=x onerror=alert(1)>');
  await user.click(card(0).getByRole('button',{name:'查看内容与完整方案'}));
  await user.click(within(inspector(0)).getByText('完整设计说明',{selector:'summary'}));
  expect(within(inspector(0)).getByText(/<img src=x onerror=alert\(1\)>/,{selector:'pre'})).toBeVisible();
  expect(document.querySelector('main img')).toBeNull();
  for(const radio of screen.getAllByRole('radio'))expect(radio).not.toBeChecked();
  expect(api.confirm).not.toHaveBeenCalled();expect(api.generate).not.toHaveBeenCalled();
 });
 it('shows every page of object expressions as inert text without discarding extra rules or selecting a direction',async()=>{
  const source=raw();source.plans[0].design=structuredClone(fixture.design);
  const expressions=Array.from({length:6},(_,i)=>({ordinal:i+1,expression:`第${i+1}页的真实视觉表达`,extra:{constraint:i===0?'<img src=x onerror=alert(1)>':'保留来源和布局要求'}}));
  Object.assign(source.plans[0].design.directions[0],{page_expression:expressions});
  const project=adaptProject(source);
  expect(project.plan?.directions?.[0].page_expression).toEqual(expressions.map(item=>JSON.stringify(item,null,2)));
  vi.mocked(api.project).mockResolvedValue(project);const user=userEvent.setup();await open();
  for(const item of expressions)expect(document.body.textContent).not.toContain(item.expression);
  await user.click(card(0).getByRole('button',{name:'查看内容与完整方案'}));
  await user.click(within(inspector(0)).getByText('逐页视觉表达',{selector:'summary'}));
  for(const item of expressions)expect(within(inspector(0)).getByText(text=>text.includes(item.expression),{selector:'p'})).toBeVisible();
  expect(inspector(0).textContent).toContain('保留来源和布局要求');
  expect(inspector(0).textContent).toContain('<img src=x onerror=alert(1)>');
  expect(document.body.textContent).not.toContain('[object Object]');expect(document.querySelector('main img')).toBeNull();
  for(const radio of screen.getAllByRole('radio'))expect(radio).not.toBeChecked();
  expect(screen.getByRole('button',{name:'确认所选完整方案'})).toBeDisabled();expect(api.confirm).not.toHaveBeenCalled();
 });
 it('blocks changing or resubmitting a selection while confirmation is pending without blocking read-only inspection',async()=>{
  const user=userEvent.setup();let finish!:(value:ReturnType<typeof adaptProject>)=>void;
  vi.mocked(api.confirm).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));await open();
  await user.click(screen.getByRole('radio',{name:'选择A 证据手册（推荐）'}));
  await user.click(screen.getByRole('button',{name:'确认所选完整方案'}));
  for(const radio of screen.getAllByRole('radio'))expect(radio).toBeDisabled();
  expect(screen.getByRole('button',{name:'确认所选完整方案'})).toBeDisabled();
  await user.click(screen.getByRole('radio',{name:'选择B 验收工作台'}));
  await user.click(card(1).getByRole('button',{name:'查看内容与完整方案'}));
  expect(inspector(1)).toBeVisible();
  expect(screen.getByRole('radio',{name:'选择A 证据手册（推荐）'})).toBeChecked();
  await user.click(screen.getByRole('button',{name:'确认所选完整方案'}));
  expect(api.confirm).toHaveBeenCalledTimes(1);expect(api.generate).not.toHaveBeenCalled();
  await act(async()=>finish(adaptProject(raw())));
  await waitFor(()=>expect(screen.getByRole('radio',{name:'选择B 验收工作台'})).toBeEnabled());
 });
 it.each(['snapshot','plan revision'] as const)('clears an unconfirmed choice and inspection when the %s changes',async change=>{
  const user=userEvent.setup();let current=adaptProject(raw());
  vi.mocked(api.project).mockImplementation(async()=>current);await open();
  await user.click(screen.getByRole('radio',{name:'选择A 证据手册（推荐）'}));
  await user.click(card(1).getByRole('button',{name:'查看内容与完整方案'}));expect(inspector(1)).toBeVisible();
  const next=raw();next.revision=6;
  if(change==='snapshot')next.input_snapshot_hash='new-material-snapshot';
  else next.plans[0]={...next.plans[0],revision:8};
  current=adaptProject(next);fireEvent.click(screen.getByRole('button',{name:'刷新状态'}));
  await screen.findByText('项目修订 6');
  await waitFor(()=>{for(const radio of screen.getAllByRole('radio'))expect(radio).not.toBeChecked();});
  expect(screen.queryByRole('region',{name:`${fixture.design.directions[1].name}的完整方案`})).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'确认所选完整方案'})).toBeDisabled();
  if(change==='snapshot'){
   expect(screen.getByText(/当前方案确认已失效/)).toBeVisible();
   for(const radio of screen.getAllByRole('radio'))expect(radio).toBeDisabled();
   await user.click(screen.getByRole('radio',{name:'选择B 验收工作台'}));
   expect(screen.getByRole('radio',{name:'选择B 验收工作台'})).not.toBeChecked();
  }else for(const radio of screen.getAllByRole('radio'))expect(radio).toBeEnabled();
  expect(api.confirm).not.toHaveBeenCalled();expect(api.generate).not.toHaveBeenCalled();
 });
});
