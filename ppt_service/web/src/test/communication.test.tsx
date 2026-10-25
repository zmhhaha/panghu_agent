import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, it, expect, vi } from 'vitest';
import fixture from './fixtures/official-stage1.json';
import App from '../App';
import { adaptProject, api } from '../api';
import { canGenerate } from '../domain';
import { adaptDirections, planningLabels } from '../planning';

vi.mock('../api',async original=>({...await original<typeof import('../api')>(),api:{me:vi.fn(),projects:vi.fn(),project:vi.fn(),versions:vi.fn(),confirm:vi.fn(),generate:vi.fn(),plan:vi.fn()}}));
const hash='a'.repeat(64);
const raw=()=>({id:'p',title:'真实沟通方案',revision:10,stage:'awaiting_confirmation',page_count:6,language:'zh',input_snapshot_hash:hash,confirmed_plan_revision:null,assets:[],plans:[{revision:1,phase:'plan_initial',input_snapshot_hash:hash,outline:{communication:fixture},design:{communication:fixture},confirmed_at:null,confirmation_selection:null}]});
beforeEach(()=>{
 vi.clearAllMocks();localStorage.setItem('ppt:selected-project','p');
 vi.mocked(api.me).mockResolvedValue({subject:'u',name:'测试账号'});
 vi.mocked(api.projects).mockResolvedValue([]);
 vi.mocked(api.project).mockResolvedValue(adaptProject(raw()));
 vi.mocked(api.versions).mockResolvedValue([]);
});
async function open(){render(<App/>);await screen.findByRole('heading',{name:'真实沟通方案'});await userEvent.click(screen.getByRole('tab',{name:/沟通与方案/}));}

describe('actual official Stage 1 communication',()=>{
 it('projects the original communication fields and questions without inventing confirmation',()=>{
  const source=raw(),before=structuredClone(source),project=adaptProject(source);
  expect(project.plan?.communication?.fields).toEqual(Object.fromEntries([
   'title','primary_language','audience','communication_intent','audience_outcome','core_message','delivery_context','artifact_afterlife','content_divergence','canvas','template_choice',
  ].map(key=>[key,fixture[key as keyof typeof fixture]])));
  expect(project.plan?.communication?.details).toEqual(fixture);
  expect(project.plan?.questions).toEqual(fixture.questions.map(item=>item.question));
  expect(project.plan?.confirmed_at).toBeNull();expect(canGenerate(project)).toBe(false);
  expect(project.plan?.communication?.template_options).toEqual([]);
  expect(source).toEqual(before);
 });
 it('shows all communication values, explanations, empty fields and complete source details for review',async()=>{
  await open();const communication=screen.getByRole('region',{name:'沟通内容'});
  for(const key of ['title','primary_language','audience','communication_intent','audience_outcome','core_message','delivery_context','artifact_afterlife','content_divergence','canvas','template_choice']){
   expect(within(communication).getAllByText(planningLabels[key]).length).toBeGreaterThan(0);
  }
  for(const value of [fixture.title.value,fixture.audience.value,fixture.communication_intent.value,fixture.audience_outcome.value,fixture.core_message.value,fixture.delivery_context.value,fixture.artifact_afterlife.value]){
   expect(within(communication).getByText(value,{selector:'pre'})).toBeVisible();
  }
  expect(within(communication).getByText('简体中文',{selector:'pre'})).toBeVisible();
  expect(within(communication).getByText('16:9 横屏',{selector:'pre'})).toBeVisible();
  expect(within(communication).getByText('自由设计',{selector:'pre'})).toBeVisible();
  expect(within(communication).getByText('未指定',{selector:'pre'})).toBeVisible();
  expect(within(communication).getByText(fixture.audience.explanation,{selector:'p'})).toBeVisible();
  await userEvent.click(within(communication).getByText('完整沟通说明'));
  expect(communication.textContent).toContain(fixture.template_choice.alternative);
  expect(communication.textContent).toContain(fixture.carried_requirements.source_boundary);
  expect(communication.textContent).toContain('"selection_confirmed": false');
  expect(screen.getByText(fixture.questions[0].question)).toBeVisible();
  expect(api.confirm).not.toHaveBeenCalled();expect(api.generate).not.toHaveBeenCalled();
 });
 it('keeps source HTML inert and confirms only after an explicit click with the original snapshot',async()=>{
  const source=structuredClone(raw());source.plans[0].outline.communication.audience.value='<img src=x onerror=alert(1)>';
  vi.mocked(api.project).mockResolvedValue(adaptProject(source));
  vi.mocked(api.confirm).mockResolvedValue(adaptProject(source));await open();
  const communication=screen.getByRole('region',{name:'沟通内容'});
  expect(within(communication).getByText('<img src=x onerror=alert(1)>',{selector:'pre'})).toBeVisible();
  expect(document.querySelector('main img')).toBeNull();expect(api.confirm).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button',{name:'确认沟通内容与设计方式'}));
  await waitFor(()=>expect(api.confirm).toHaveBeenCalledWith(expect.objectContaining({revision:10,input_snapshot_hash:hash,plan:expect.objectContaining({revision:'1',confirmed_at:null})}),{design_mode:'free_design',template_id:null}));
  expect(api.generate).not.toHaveBeenCalled();
 });
 it('keeps legacy summaries and validated template choices',()=>{
  const project=adaptProject({...raw(),plans:[{revision:2,phase:'plan_initial',outline:{summary:'旧版沟通摘要'},design:{validated_templates:['已验收模板A']}}]});
  expect(project.plan?.communication?.summary).toBe('旧版沟通摘要');
  expect(project.plan?.communication?.template_options).toEqual(['已验收模板A']);
  const direct=adaptProject({...raw(),plans:[],plan:{revision:2,stage:'communication',communication:{summary:'兼容摘要',template_options:['模板B']}}});
  expect(direct.plan?.communication?.summary).toBe('兼容摘要');
  expect(direct.plan?.communication?.template_options).toEqual(['模板B']);
 });
 it('shows optional task page count and style with their actual reasons',async()=>{
  const source=structuredClone(raw());
  Object.assign(source.plans[0].outline.communication,{
   page_count:{value:8,reason:'本任务要求八页'},
   style_brief:{value:'简洁清晰，突出代码结构',reason:'适合学习和查阅'},
  });
  vi.mocked(api.project).mockResolvedValue(adaptProject(source));await open();
  const communication=screen.getByRole('region',{name:'沟通内容'});
  expect(within(communication).getAllByText('页数').length).toBeGreaterThan(0);
  expect(within(communication).getByText('8',{selector:'pre'})).toBeVisible();
  expect(within(communication).getByText('本任务要求八页',{selector:'p'})).toBeVisible();
  expect(within(communication).getAllByText('风格要求').length).toBeGreaterThan(0);
  expect(within(communication).getByText('简洁清晰，突出代码结构',{selector:'pre'})).toBeVisible();
  expect(within(communication).getByText('适合学习和查阅',{selector:'p'})).toBeVisible();
  expect(api.confirm).not.toHaveBeenCalled();
 });
});

it('retains string and object content in new planning examples',()=>{
 const directions=adaptDirections([{id:'a',name:'方向A',page_plan:{pages:[{ordinal:1,title:'第一页',content:['真实要点',{text:'带来源的段落',source_refs:['F01']}]}]},production_parameters:{canvas:'ppt169'}}]);
 expect(directions?.[0].pages[0].points).toEqual(['真实要点','带来源的段落']);
});
