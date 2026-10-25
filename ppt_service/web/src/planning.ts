import type { Communication, Direction, Page, Project } from './domain';
type ObjectValue = Record<string, unknown>;
const object = (value:unknown):ObjectValue => value!==null&&typeof value==='object'&&!Array.isArray(value)?value as ObjectValue:{};
const text = (value:unknown) => typeof value==='string'?value:'';
const strings = (value:unknown) => Array.isArray(value)?value.filter((item):item is string=>typeof item==='string'):[];
function remainder(raw:ObjectValue, known:string[]) {return Object.fromEntries(Object.entries(raw).filter(([key])=>!known.includes(key)));}
export const communicationFields = ['title','primary_language','page_count','audience','communication_intent','audience_outcome','core_message','delivery_context','artifact_afterlife','content_divergence','canvas','style_brief','template_choice'];
export function adaptCommunication(value:unknown):Communication {
 const raw=object(value),outline=object(raw.outline),design=object(raw.design);
 const source=[outline.communication,design.communication,raw.communication].map(object).find(item=>Object.keys(item).length)??{};
 return {summary:text(design.summary)||text(outline.summary)||text(raw.outline)||text(source.summary),
  template_options:strings(design.validated_templates??source.template_options),
  fields:Object.fromEntries(communicationFields.filter(key=>Object.hasOwn(source,key)).map(key=>[key,source[key]])),
  details:source};
}
export function adaptPlanQuestions(value:unknown):string[] {
 return Array.isArray(value)?value.map(item=>typeof item==='string'?item:text(object(item).question)).filter(Boolean):[];
}
function expressionTexts(value:unknown):string[] {
 const rows=Array.isArray(value)?value:typeof value==='string'?[value]:[];
 // Object expressions include page identity and may carry extra layout rules.
 // Preserve every public field as escaped text instead of coercing or dropping it.
 return rows.flatMap(item=>typeof item==='string'?[item]:item!==null&&typeof item==='object'?[JSON.stringify(item,null,2)]:[]);
}
export function adaptPlanPages(value:unknown):Page[] {
 const rawPages=Array.isArray(value)?value:object(value).pages;
 if(!Array.isArray(rawPages))return [];
 return rawPages.map((value)=>{
  const raw=object(value);const number=raw.number??raw.ordinal;
  if(!Number.isInteger(number)||Number(number)<1||!text(raw.title))return null;
  const content=Array.isArray(raw.content)?raw.content.map(item=>typeof item==='string'?item:text(object(item).text)).filter(Boolean):[];
  return {number:Number(number),title:text(raw.title),purpose:text(raw.purpose)||text(raw.core_message),points:Array.isArray(raw.points)?strings(raw.points):content,sources:strings(raw.sources??raw.source_refs),details:remainder(raw,['number','ordinal','title','purpose','core_message','points','sources','source_refs'])} as Page;
 }).filter((page):page is Page=>page!==null);
}
export function adaptDirections(value:unknown):Direction[]|undefined {
 if(!Array.isArray(value))return undefined;
 return value.map(item=>{
  const raw=object(item);const id=text(raw.id)||text(raw.direction_id);
  if(!id)return null;
  return {id,title:text(raw.title)||text(raw.name),description:text(raw.description)||text(raw.note),pages:adaptPlanPages(raw.pages??raw.page_plan),production_settings:object(raw.production_settings??raw.production_parameters),page_expression:expressionTexts(raw.page_expression),details:remainder(raw,['id','direction_id','name','title','note','description','pages','page_plan','production_settings','production_parameters','page_expression'])} as Direction;
 }).filter((direction):direction is Direction=>direction!==null);
}
export function restoredDirection(project:Project) {
 const plan=project.plan;const id=plan?.confirmed_direction_id;
 return !!project.input_snapshot_hash&&plan?.input_snapshot_hash===project.input_snapshot_hash&&project.confirmed_plan_revision===plan?.revision&&plan?.directions?.some(d=>d.id===id)?id??'':'';
}
export const planningLabels:Record<string,string>={title:'文稿名称',primary_language:'主要语言',audience:'目标受众',communication_intent:'沟通目的',audience_outcome:'读者收获',core_message:'核心信息',delivery_context:'使用场景',artifact_afterlife:'后续用途',content_divergence:'材料保留与改写要求',canvas:'画布',style_brief:'风格要求',template_choice:'设计方式建议',carried_requirements:'已有要求',missing_source_context:'材料缺项',questions:'待确认问题',stage_boundary:'当前阶段边界',refine_spec_offer:'规范审阅建议',mode:'表达方式',mode_behavior:'内容组织',mode_references:'表达参考',visual_style:'视觉风格',visual_style_behavior:'视觉表达',visual_style_references:'风格参考',icons:'图标方式',icon_explanation:'图标说明',color:'配色',typography:'字体与字号',image_strategy:'配图策略',page_count:'页数',design_spec_depth:'设计规范深度',refine_spec:'额外规范审阅',native_charts:'原生图表',visual_review:'逐页视觉复核',image_notes:'图片要求',recommend:'制作建议',proactive_speaker_notes:'演讲备注',proactive_custom_animations:'动画',proactive_narration_audio:'旁白',template_handoff:'设计方式交接',composition_reference:'布局说明',review_focus:'复核要点',audience_move:'读者目标',relationships:'内容关系',rhythm:'阅读节奏',visualization:'可视化',images:'图片',native_chart_keys:'原生图表标识',id:'页面标识'};
