import { render, screen, within, waitFor } from '@testing-library/react';
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
describe('actual official stage-2 projection',()=>{
 it('retains all original IDs, notes, six full pages, expressions and parameters',()=>{const project=adaptProject(raw());expect(project.plan!.directions!.map(d=>d.id)).toEqual(['evidence-handbook','acceptance-console','guided-walkthrough']);for(let i=0;i<3;i++){const direction=project.plan!.directions![i];expect(direction.description).toBe(fixture.design.directions[i].note);expect(direction.pages).toHaveLength(6);expect(direction.pages[1].number).toBe(2);expect(direction.pages[1].purpose).toBe(fixture.outline.pages[1].core_message);expect(direction.pages[1].points).toContain(fixture.outline.pages[1].content.find(c=>c.role==='body')!.text);expect(direction.production_settings).toEqual(fixture.design.production_parameters);expect(direction.page_expression).toEqual(fixture.design.directions[i].page_expression);}expect(restoredDirection(project)).toBe('');});
 it('shows recommendation only as advice, never selecting or confirming it',async()=>{await open();expect(screen.getByText('系统推荐仅供参考，不代表用户确认')).toBeInTheDocument();for(const radio of screen.getAllByRole('radio'))expect(radio).not.toBeChecked();expect(screen.getByRole('button',{name:'确认所选完整方案'})).toBeDisabled();expect(api.confirm).not.toHaveBeenCalled();expect(api.generate).not.toHaveBeenCalled();expect(screen.getAllByText(fixture.outline.pages[1].content.find(c=>c.role==='body')!.text).length).toBeGreaterThanOrEqual(3);for(const id of ['evidence-handbook','acceptance-console','guided-walkthrough'])expect(screen.getByText(id,{selector:'code'})).toBeInTheDocument();});
 it('confirms only a real user-selected exact direction id and current project payload',async()=>{const user=userEvent.setup();vi.mocked(api.confirm).mockResolvedValue(adaptProject(raw()));await open();await user.click(screen.getByRole('radio',{name:'选择B 验收工作台'}));await user.click(screen.getByRole('button',{name:'确认所选完整方案'}));await waitFor(()=>expect(api.confirm).toHaveBeenCalledWith(expect.objectContaining({revision:5,input_snapshot_hash:hash,plan:expect.objectContaining({revision:'7'})}),{direction_id:'acceptance-console'}));expect(api.generate).not.toHaveBeenCalled();});
 it('restores only the server-confirmed selection with matching current revision and snapshot',async()=>{const confirmed={...raw(),confirmed_plan_revision:7,plans:[{...raw().plans[0],confirmed_at:'2026-10-22T00:00:00Z',confirmation_selection:{direction_id:'guided-walkthrough'}}]};vi.mocked(api.project).mockResolvedValue(adaptProject(confirmed));await open();expect(screen.getByRole('radio',{name:'选择C 分步验收讲义'})).toBeChecked();expect(screen.getByRole('radio',{name:'选择A 证据手册（推荐）'})).not.toBeChecked();expect(restoredDirection(adaptProject({...confirmed,input_snapshot_hash:'new-upload'}))).toBe('');expect(restoredDirection(adaptProject({...confirmed,confirmed_plan_revision:6}))).toBe('');});
 it('renders unknown complex public fields as escaped inert text and details do not select a card',async()=>{const source=raw();source.plans[0].design=structuredClone(fixture.design);Object.assign(source.plans[0].design.directions[0],{extra_public_detail:{markup:'<img src=x onerror=alert(1)>'}});vi.mocked(api.project).mockResolvedValue(adaptProject(source));const user=userEvent.setup();await open();const first=screen.getAllByText('完整设计说明')[0];await user.click(first);expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();expect(document.querySelector('main img')).toBeNull();for(const radio of screen.getAllByRole('radio'))expect(radio).not.toBeChecked();});
 it('shows every page of object expressions as inert text without discarding extra rules or selecting a direction',async()=>{
  const source=raw();source.plans[0].design=structuredClone(fixture.design);
  const expressions=Array.from({length:6},(_,i)=>({ordinal:i+1,expression:`第${i+1}页的真实视觉表达`,extra:{constraint:i===0?'<img src=x onerror=alert(1)>':'保留来源和布局要求'}}));
  Object.assign(source.plans[0].design.directions[0],{page_expression:expressions});
  const project=adaptProject(source);
  expect(project.plan?.directions?.[0].page_expression).toEqual(expressions.map(item=>JSON.stringify(item,null,2)));
  vi.mocked(api.project).mockResolvedValue(project);await open();
  for(const item of expressions)expect(document.body.textContent).toContain(item.expression);
  expect(document.body.textContent).toContain('保留来源和布局要求');
  expect(document.body.textContent).toContain('<img src=x onerror=alert(1)>');
  expect(document.body.textContent).not.toContain('[object Object]');expect(document.querySelector('main img')).toBeNull();
  for(const radio of screen.getAllByRole('radio'))expect(radio).not.toBeChecked();
  expect(screen.getByRole('button',{name:'确认所选完整方案'})).toBeDisabled();expect(api.confirm).not.toHaveBeenCalled();
 });
});
