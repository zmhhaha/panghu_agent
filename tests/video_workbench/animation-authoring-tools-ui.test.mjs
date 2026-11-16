import test from 'node:test';
import assert from 'node:assert/strict';
import {observedTools,observedMotionOrchestration,matchesAuthoringTool,matchesVisualCreation,renderAuthoringBadges,renderCapabilityLabels,resourceAuthoringRequest,toolSearchText,mountAuthoringControls}
  from '../../video_workbench/animation_lab_web/authoring-tools.mjs';

const motion={id:'animejs',version:'4.5.0',status:'render_observed',
  evidence_scope:'render_loaded_dependency_not_visual_acceptance',
  source_sha256:'a'.repeat(64),media_sha256:'b'.repeat(64),render_operation_id:'render-operation'};
const nativeMotion={...motion,id:'native-js',version:undefined,status:'source_verified',
  evidence_scope:'source_inspection_not_visual_acceptance'};
const sample={engine_status:'rendered',authoring_tools:[{id:'animejs',version:'4.5.0',status:'loaded',
  evidence_scope:'render_loaded_dependency_not_visual_application_or_acceptance'}],motion_orchestration:[motion]};

test('motion filters require source and exact render evidence independently of loaded dependencies',()=>{
  assert.equal(matchesAuthoringTool(sample,'animejs'),true);
  assert.equal(matchesAuthoringTool(sample,'manim'),false);
  assert.equal(matchesAuthoringTool({},'all'),true);
  assert.match(toolSearchText(sample),/anime\.js/);
  for(const value of [{authoring_tools:sample.authoring_tools},
    {...sample,engine_status:'unverified'},
    {engine_status:'rendered',runtime_requirements:{authoring_libraries:{animejs:{}}}}]) {
    assert.deepEqual(observedTools(value),[]);
  }
  const loadedOnly={engine_status:'rendered',authoring_tools:sample.authoring_tools};
  assert.equal(observedTools(loadedOnly).length,1);
  assert.deepEqual(observedMotionOrchestration(loadedOnly),[]);
  assert.equal(matchesAuthoringTool(loadedOnly,'animejs'),false);
  assert.doesNotMatch(toolSearchText(loadedOnly),/anime/);
});

test('tool names and markup from metadata cannot create a badge',()=>{
  const value={...sample,authoring_tools:[...sample.authoring_tools,
    {...sample.authoring_tools[0],id:'<script>'},
    {...sample.authoring_tools[0],id:'manim',status:'requested'}]};
  assert.deepEqual(observedTools(value),[{id:'animejs',label:'Anime.js',version:'4.5.0'}]);
});

test('native motion labels and filters need positive inspected source evidence',()=>{
  const value={engine:'canvas',engine_status:'rendered',motion_orchestration:[nativeMotion]};
  assert.equal(matchesAuthoringTool(value,'native-js'),true);
  assert.equal(matchesAuthoringTool(value,'animejs'),false);
  assert.match(toolSearchText(value),/原生 javascript/);
  const badge=renderAuthoringBadges(labelDocument(),value)[0];
  assert.equal(badge.textContent,'原生 JavaScript');
  assert.equal(badge.dataset.evidenceStatus,'source_verified');
  assert.match(badge.title,/源码已核验时间驱动的画面更新/);
  assert.match(badge.title,/不代表连续动作或视觉质量已获认可/);
  assert.equal(renderCapabilityLabels(labelDocument(),value).children[1].textContent,'动作编排：原生 JavaScript');
  assert.deepEqual(observedTools({engine_status:'rendered',authoring_tools:[{
    ...sample.authoring_tools[0],id:'native-js'}]}),[]);
});

test('GSAP requires the rendered source-backed projection and has its own searchable label',()=>{
  const gsap={...motion,id:'gsap',version:'3.14.2'};
  const value={engine:'html-svg',engine_status:'rendered',motion_orchestration:[gsap]};
  assert.equal(matchesAuthoringTool(value,'gsap'),true);
  assert.match(toolSearchText(value),/gsap/);
  const badge=renderAuthoringBadges(labelDocument(),value)[0];
  assert.equal(badge.textContent,'GSAP');
  assert.equal(badge.dataset.evidenceStatus,'render_observed');
  assert.match(badge.title,/源码已核验实质调用，渲染已加载固定版本依赖/);
  assert.match(badge.title,/GSAP 3\.14\.2/);
});

test('malformed, requested, unbound or mismatched motion evidence cannot be rendered or filtered',()=>{
  for(const row of [{...motion,id:'<script>'},{...motion,status:'loaded'},
    {...motion,evidence_scope:'source_inspection_not_visual_acceptance'},
    {...motion,source_sha256:''},{...motion,source_sha256:'A'.repeat(64)},
    {...motion,media_sha256:null},{...motion,media_sha256:['b'.repeat(64)]},
    {...motion,render_operation_id:' '},{...motion,version:''},
    {...nativeMotion,status:'render_observed'},
    {...nativeMotion,evidence_scope:'render_loaded_dependency_not_visual_acceptance'}]) {
    const value={engine_status:'rendered',motion_orchestration:[row]};
    assert.deepEqual(observedMotionOrchestration(value),[]);
    assert.deepEqual(renderAuthoringBadges(labelDocument(),value),[]);
    assert.equal(matchesAuthoringTool(value,row.id),false);
  }
  assert.deepEqual(observedMotionOrchestration({engine_status:'unverified',motion_orchestration:[motion]}),[]);
  assert.deepEqual(observedMotionOrchestration({motion_orchestration:[motion]}),[]);
});

test('observed Manim resources appear in creation cards and filters independently of their HTML host',()=>{
  const value={...sample,engine:'html-svg',authoring_tools:[...sample.authoring_tools,
    {id:'manim',version:'0.22.1',status:'loaded',
      evidence_scope:'render_loaded_dependency_not_visual_application_or_acceptance'}]};
  const doc={createElement(){return {dataset:{}};}};
  assert.equal(matchesVisualCreation(value,'manim'),true);
  assert.equal(matchesVisualCreation(value,'html-svg'),true);
  assert.equal(matchesAuthoringTool(value,'manim'),false);
  assert.equal(matchesAuthoringTool(value,'animejs'),true);
  assert.deepEqual(renderAuthoringBadges(doc,value,'creation').map(row=>row.textContent),['Manim']);
  assert.deepEqual(renderAuthoringBadges(doc,value).map(row=>row.textContent),['Anime.js']);
  assert.match(renderAuthoringBadges(doc,value,'creation')[0].title,/Manim 0\.22\.1/);
  assert.match(renderAuthoringBadges(doc,value)[0].title,/Anime\.js 4\.5\.0/);
  assert.equal(matchesVisualCreation({engine_status:'rendered',engine:'html-svg',
    resource_authoring:{kind:'manim'}},'manim'),false);
});

function labelDocument() {
  class Element {
    constructor(tag){this.tagName=tag;this.dataset={};this.children=[];this.ownText='';this.handlers={};this.checked=false;this.disabled=false;this.hidden=false;}
    set textContent(value){this.ownText=String(value);this.children=[];}
    get textContent(){return this.ownText+this.children.map(row=>row.textContent).join('');}
    append(...nodes){this.children.push(...nodes);}
    addEventListener(event,handler){this.handlers[event]=handler;}
  }
  return {createElement:tag=>new Element(tag)};
}

test('motion selector offers native JavaScript alongside the two admitted libraries',()=>{
  const doc=labelDocument(),container=doc.createElement('div');
  doc.getElementById=id=>id==='gallery-engine'?{parentNode:{parentNode:container}}:null;
  const create=doc.createElement;
  doc.createElement=tag=>{
    const element=create(tag);
    element.addEventListener=(event,handler)=>{element[event]=handler;};
    return element;
  };
  let changes=0;
  const controls=mountAuthoringControls(doc,()=>{changes++;});
  const label=container.children[0],select=label.children[0];
  assert.equal(label.ownText,'动作编排');
  assert.deepEqual(select.children.map(row=>[row.value,row.textContent]),[
    ['all','全部'],['native-js','原生 JavaScript'],['gsap','GSAP'],['animejs','Anime.js']]);
  select.value='native-js';select.change();
  assert.equal(controls.selected,'native-js');
  assert.equal(changes,1);
  controls.resetFilter();
  assert.equal(controls.selected,'all');
});

test('every rendered card keeps both named capability rows and explicit missing motion evidence',()=>{
  for(const [engine,label]of [['html-svg','HTML/SVG'],['canvas','Canvas 2D'],['three','Three.js']]) {
    const labels=renderCapabilityLabels(labelDocument(),{engine_status:'rendered',engine});
    assert.equal(labels.children.length,2);
    const [creation,motion]=labels.children;
    assert.equal(creation.dataset.capabilityLayer,'creation');
    assert.equal(motion.dataset.capabilityLayer,'motion');
    assert.equal(creation.textContent,'画面创作：'+label);
    assert.equal(motion.textContent,'动作编排：未记录');
    assert.match(creation.children[1].children[0].title,/画面创作/);
    assert.match(motion.children[1].children[0].title,/不代表样片没有动画/);
    assert.doesNotMatch(labels.textContent,/GSAP|原生/);
  }
});

test('Manim and its HTML host copy as separate names while exact versions remain in tooltips',()=>{
  const value={...sample,engine:'html-svg',authoring_tools:[...sample.authoring_tools,
    {id:'manim',version:'0.22.1',status:'loaded',
      evidence_scope:'render_loaded_dependency_not_visual_application_or_acceptance'}]};
  const [creation,motion]=renderCapabilityLabels(labelDocument(),value).children;
  assert.equal(creation.textContent,'画面创作：HTML/SVG\u00a0Manim');
  assert.equal(motion.textContent,'动作编排：Anime.js');
  const badges=creation.children[1].children.filter(row=>row.className.includes('engine-badge'));
  assert.deepEqual(badges.map(row=>row.textContent),['HTML/SVG','Manim']);
  assert.match(badges[1].title,/Manim 0\.22\.1/);
  assert.match(motion.children[1].children[0].title,/Anime\.js 4\.5\.0/);
  assert.doesNotMatch(creation.textContent+motion.textContent,/0\.22\.1|4\.5\.0|HTML\/SVGManim/);
});

test('cards without a confirmed sample retain two pending rows without inferred libraries',()=>{
  const declared={engine:'canvas',engine_status:'rendered',...sample};
  for(const [value,noSample]of [[{},false],[{...declared,engine_status:'unverified'},false],[declared,true]]) {
    const labels=renderCapabilityLabels(labelDocument(),value,noSample);
    assert.deepEqual(labels.children.map(row=>row.textContent),['画面创作：待确认','动作编排：待确认']);
    assert.doesNotMatch(labels.textContent,/Anime|GSAP|Canvas/);
  }
});

test('Manim resource request is opt-in and bounded independently of total duration',()=>{
  assert.deepEqual(resourceAuthoringRequest({},24),{});
  assert.deepEqual(resourceAuthoringRequest({use_manim_resource:'on',manim_resource_seconds:'2'},24),
    {resource_authoring:{kind:'manim',width:640,height:360,frame_count:48,global_start_frame:0,seed:17}});
  for(const duration of ['0','3','NaN','1.01']) {
    assert.throws(()=>resourceAuthoringRequest({use_manim_resource:'on',manim_resource_seconds:duration},24));
  }
  assert.throws(()=>resourceAuthoringRequest({use_manim_resource:'on',manim_resource_seconds:'2.5'},2));
});

function authoringForm() {
  const doc=labelDocument(),form=doc.createElement('form');
  doc.getElementById=id=>id==='experiment-form'?form:null;
  const controls=mountAuthoringControls(doc,()=>{});
  const all=()=>{
    const entries=[];
    const walk=node=>{entries.push(node);for(const child of node.children)walk(child);};
    walk(form);return entries;
  };
  return {doc,form,controls,all,input:name=>all().find(row=>row.tagName==='input'&&row.name===name),
    fields:()=>Object.fromEntries(all().filter(row=>row.tagName==='input'&&!row.disabled
      &&(row.type!=='checkbox'||row.checked)).map(row=>[row.name,row.type==='checkbox'?'on':row.value]))};
}
const allCapabilities={enabled:true,allowed_engines:['html-svg','canvas','three'],
  motion_tools:['native-js','gsap','animejs'],resource_authoring:{manim:{enabled:true}},
  authoring_libraries:{animejs:{version:'4.5.0'}}};

test('new experiments expose two peer capability groups with automatic selection and no Manim parent disclosure',()=>{
  const view=authoringForm(),groups=view.all().filter(row=>row.tagName==='fieldset');
  assert.deepEqual(groups.map(row=>row.children[0].textContent),['画面创作','动作编排']);
  assert.deepEqual(groups.map(row=>row.dataset.capabilityLayer),['creation','motion']);
  assert.deepEqual(groups.map(row=>row.children.filter(child=>child.tagName==='label')
    .filter(child=>child.children[0]?.type==='checkbox').map(child=>child.children[1].textContent)),
  [['HTML/SVG','Canvas 2D','Three.js','Manim'],['原生 JavaScript','GSAP','Anime.js']]);
  assert.equal(view.all().some(row=>row.tagName==='details'),false);
  assert.equal(view.all().some(row=>row.name==='use_manim_resource'),false);
  assert.equal(view.all().filter(row=>row.type==='checkbox').every(row=>!row.checked&&row.disabled),true);
  assert.match(groups[0].textContent,/自动选择/);assert.match(groups[1].textContent,/自动选择/);
  view.controls.update(allCapabilities,false);
  assert.deepEqual(resourceAuthoringRequest(view.fields(),24),{});
});

test('independent checkbox names preserve multi-axis combinations through Object.fromEntries form collection',()=>{
  const view=authoringForm();view.controls.update(allCapabilities,false);
  const names=view.all().filter(row=>row.type==='checkbox').map(row=>row.name);
  assert.equal(new Set(names).size,7);
  for(const name of ['creation_html_svg','creation_canvas','creation_three','motion_gsap','motion_animejs']) {
    view.input(name).checked=true;view.input(name).handlers.change();
  }
  assert.deepEqual(resourceAuthoringRequest(view.fields(),12),{
    capability_selection:{creation:['html-svg','canvas','three'],motion:['gsap','animejs']}});
  assert.match(view.all().filter(row=>row.tagName==='fieldset')[0].textContent,/从选中能力中选择或组合/);
  assert.deepEqual(resourceAuthoringRequest({motion_native_js:'on'},12),{
    capability_selection:{creation:[],motion:['native-js']}});
  assert.deepEqual(resourceAuthoringRequest({creation_canvas:true,motion_native_js:'off'},12),{
    capability_selection:{creation:['canvas'],motion:[]}});
});

test('Manim settings are subordinate to its creation choice and preserve frame and duration boundaries',()=>{
  const view=authoringForm();view.controls.update(allCapabilities,false);
  const manim=view.input('creation_manim'),seconds=view.input('manim_resource_seconds');
  const length=view.all().find(row=>row.tagName==='label'&&row.children.includes(seconds));
  assert.equal(length.hidden,true);assert.equal(seconds.disabled,true);
  manim.checked=true;manim.handlers.change();
  assert.equal(length.hidden,false);assert.equal(seconds.disabled,false);
  assert.equal(seconds.min,'.5');assert.equal(seconds.max,'2.5');
  for(const [value,frames]of [['.5',12],['1.25',30],['2.5',60]]) {
    seconds.value=value;
    assert.deepEqual(resourceAuthoringRequest(view.fields(),3),{
      capability_selection:{creation:['manim'],motion:[]},
      resource_authoring:{kind:'manim',width:640,height:360,frame_count:frames,global_start_frame:0,seed:17}});
  }
  for(const value of ['0.49','2.51','1.01','NaN','']) {
    seconds.value=value;assert.throws(()=>resourceAuthoringRequest(view.fields(),3),/Manim 片段时长/);
  }
  seconds.value='2.5';assert.throws(()=>resourceAuthoringRequest(view.fields(),2),/Manim 片段时长/);
  manim.checked=false;manim.handlers.change();
  assert.equal(length.hidden,true);assert.equal(seconds.disabled,true);
  assert.deepEqual(resourceAuthoringRequest(view.fields(),2),{});
});

test('server admission controls each checkbox and authoritative motion_tools overrides descriptors and defaults',()=>{
  const view=authoringForm();
  view.controls.update({...allCapabilities,allowed_engines:['canvas'],motion_tools:['native-js'],
    resource_authoring:{manim:{enabled:false}}},false);
  assert.equal(view.input('creation_canvas').disabled,false);
  assert.equal(view.input('motion_native_js').disabled,false);
  for(const name of ['creation_html_svg','creation_three','creation_manim','motion_gsap','motion_animejs']) {
    assert.equal(view.input(name).disabled,true,name);
  }
  view.controls.update({...allCapabilities,motion_tools:[]},false);
  for(const name of ['motion_native_js','motion_gsap','motion_animejs'])assert.equal(view.input(name).disabled,true);
  view.controls.update({...allCapabilities,enabled:false},false);
  assert.equal(view.all().filter(row=>row.type==='checkbox').every(row=>row.disabled),true);
});

test('older catalogs expose fixed HTML GSAP and only a ready declared Anime.js library',()=>{
  const view=authoringForm(),older={enabled:true,allowed_engines:['html-svg']};
  view.controls.update(older,false);
  assert.equal(view.input('motion_native_js').disabled,false);
  assert.equal(view.input('motion_gsap').disabled,false);
  assert.equal(view.input('motion_animejs').disabled,true);
  for(const descriptor of [false,{}, {version:'4.5.0',enabled:false}]) {
    view.controls.update({...older,authoring_libraries:{animejs:descriptor}},false);
    assert.equal(view.input('motion_animejs').disabled,true);
  }
  view.controls.update({...older,authoring_libraries:{animejs:{version:'4.5.0'}}},false);
  assert.equal(view.input('motion_animejs').disabled,false);
  view.controls.update({...older,authoring_libraries:{gsap:{version:'3.14.2',enabled:false}}},false);
  assert.equal(view.input('motion_gsap').disabled,true);
});

test('continuations restore both axes and Manim duration while busy state preserves the choices',()=>{
  const view=authoringForm();view.controls.update(allCapabilities,false);
  view.controls.resetExperiment({kind:'manim',frame_count:30},{creation:['html-svg','manim'],motion:['animejs']});
  assert.equal(view.input('creation_html_svg').checked,true);
  assert.equal(view.input('creation_manim').checked,true);
  assert.equal(view.input('creation_canvas').checked,false);
  assert.equal(view.input('motion_animejs').checked,true);
  assert.equal(view.input('motion_gsap').checked,false);
  assert.equal(view.input('manim_resource_seconds').value,'1.25');
  view.controls.update(allCapabilities,true);
  assert.equal(view.all().filter(row=>row.type==='checkbox').every(row=>row.disabled),true);
  assert.equal(view.input('creation_manim').checked,true);
  assert.equal(view.input('manim_resource_seconds').disabled,true);
  view.controls.update(allCapabilities,false);
  assert.equal(view.input('manim_resource_seconds').disabled,false);
  view.controls.resetExperiment(null,null,{collaboration:false});
  assert.equal(view.all().filter(row=>row.type==='checkbox').every(row=>!row.checked),true);
  assert.deepEqual(resourceAuthoringRequest(view.fields(),24),{});
});

test('collaboration keeps all admitted browser choices and motion tools while excluding only Manim resources',()=>{
  const view=authoringForm();view.controls.update(allCapabilities,false);
  view.controls.resetExperiment({kind:'manim',frame_count:48},{creation:['canvas','three','manim'],motion:['animejs']});
  view.controls.update(allCapabilities,false,{collaboration:true});
  assert.equal(view.input('creation_manim').disabled,true);
  assert.equal(view.input('creation_manim').checked,false);
  assert.equal(view.input('manim_resource_seconds').disabled,true);
  for(const name of ['creation_html_svg','creation_canvas','creation_three','motion_native_js','motion_gsap','motion_animejs']) {
    assert.equal(view.input(name).disabled,false,name);
  }
  assert.deepEqual(resourceAuthoringRequest(view.fields(),12),{
    capability_selection:{creation:['canvas','three'],motion:['animejs']}});
  assert.match(view.form.textContent,/协作创作可使用已开放的 HTML\/SVG、Canvas 2D 和 Three\.js/);
  assert.match(view.form.textContent,/Manim 预制资源暂未接入协作/);
  view.controls.resetExperiment(null,{creation:['html-svg'],motion:['gsap']},{collaboration:false});
  assert.equal(view.input('creation_manim').disabled,false);
  assert.deepEqual(resourceAuthoringRequest(view.fields(),12),{
    capability_selection:{creation:['html-svg'],motion:['gsap']}});
});
