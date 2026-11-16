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
    constructor(tag){this.tagName=tag;this.dataset={};this.children=[];this.ownText='';}
    set textContent(value){this.ownText=String(value);this.children=[];}
    get textContent(){return this.ownText+this.children.map(row=>row.textContent).join('');}
    append(...nodes){this.children.push(...nodes);}
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
