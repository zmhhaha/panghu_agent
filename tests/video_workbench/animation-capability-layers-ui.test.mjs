import test from 'node:test';
import assert from 'node:assert/strict';
import {
  toolLayer, observedTools, matchesVisualCreation, matchesAuthoringTool,
  renderAuthoringBadges, mountAuthoringControls,
} from '../../video_workbench/animation_lab_web/authoring-tools.mjs';
import {renderMethodCard} from '../../video_workbench/animation_lab_web/method-gallery.mjs';

const observed = (id,version) => ({id,version,status:'loaded',
  evidence_scope:'render_loaded_dependency_not_visual_application_or_acceptance'});
const sample = {engine:'html-svg',engine_status:'rendered',authoring_tools:[
  observed('manim','0.22.1'),observed('animejs','4.5.0'),
],motion_orchestration:[{id:'animejs',version:'4.5.0',status:'render_observed',
  evidence_scope:'render_loaded_dependency_not_visual_acceptance',source_sha256:'a'.repeat(64),
  media_sha256:'b'.repeat(64),render_operation_id:'render-operation'}]};

test('Manim is a creation capability and both timeline libraries belong to motion orchestration',()=>{
  assert.equal(toolLayer('manim'),'creation');
  assert.equal(toolLayer('gsap'),'motion');
  assert.equal(toolLayer('animejs'),'motion');
  assert.equal(toolLayer('unknown'),null);
  assert.equal(matchesVisualCreation(sample,'manim'),true);
  assert.equal(matchesVisualCreation(sample,'html-svg'),true);
  assert.equal(matchesVisualCreation(sample,'canvas'),false);
  assert.equal(matchesAuthoringTool(sample,'animejs'),true);
  assert.equal(matchesAuthoringTool(sample,'manim'),false);
  assert.equal(matchesAuthoringTool(sample,'gsap'),false);
});

test('capability filters require the displayed render evidence, never declarations or family history',()=>{
  for (const value of [
    {...sample,engine_status:'unverified'},
    {engine:'html-svg',engine_status:'rendered',runtime_requirements:{producer:'manim',authoring_libraries:{gsap:{}}}},
    {engine:'html-svg',engine_status:'rendered',commissions:[sample],
      authoring_tools:[{...observed('manim','0.22.1'),status:'requested'},
        {...observed('gsap','3.14.2'),evidence_scope:'requested'}]},
  ]) {
    assert.equal(matchesVisualCreation(value,'manim'),false);
    assert.equal(matchesAuthoringTool(value,'gsap'),false);
    assert.deepEqual(observedTools(value),[]);
  }
  assert.equal(matchesVisualCreation({engine:'manim',engine_status:'rendered'},'manim'),false);
  assert.equal(matchesVisualCreation({engine:'manim',engine_status:'rendered'},'unknown'),true);
});

class Element {
  constructor(tag) { this.tagName=tag;this.children=[];this.dataset={};this.handlers={};this.classList={add(){}}; }
  set textContent(value) { this.ownText=String(value);this.children=[]; }
  get textContent() { return (this.ownText??'')+this.children.map(row=>row.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  addEventListener(type,handler) { this.handlers[type]=handler; }
  setAttribute() {}
}
const doc={createElement:tag=>new Element(tag)};

test('creation and motion badges are distinct and never invent GSAP use',()=>{
  const creation=renderAuthoringBadges(doc,sample,'creation');
  const motion=renderAuthoringBadges(doc,sample);
  assert.deepEqual(creation.map(row=>[row.textContent,row.dataset.capabilityLayer]),[['Manim','creation']]);
  assert.deepEqual(motion.map(row=>[row.textContent,row.dataset.capabilityLayer]),[['Anime.js','motion']]);
  assert.match(creation[0].title,/Manim 0\.22\.1/);
  assert.match(motion[0].title,/Anime\.js 4\.5\.0/);
  assert.match(creation[0].title,/不代表效果已获认可/);
  assert.equal(renderAuthoringBadges(doc,{...sample,authoring_tools:[]},'creation').length,0);
  assert.equal(renderAuthoringBadges(doc,{...sample,motion_orchestration:[]}).length,0);
});

test('visible motion filter groups GSAP with Anime.js and excludes Manim',()=>{
  const filters=new Element('div'),engine={parentNode:{parentNode:filters}};
  const document={...doc,getElementById:id=>id==='gallery-engine'?engine:null};
  let changes=0;
  const controls=mountAuthoringControls(document,()=>{changes++;});
  const label=filters.children[0],select=label.children[0];
  assert.equal(label.ownText,'动作编排');
  assert.deepEqual(select.children.map(row=>[row.value,row.textContent]),[
    ['all','全部'],['native-js','原生 JavaScript'],['gsap','GSAP'],['animejs','Anime.js'],
  ]);
  select.value='animejs';select.handlers.change();
  assert.equal(controls.selected,'animejs');assert.equal(changes,1);
  controls.resetFilter();assert.equal(controls.selected,'all');
});

test('effect card presents creation and motion as separate layers while retaining its real browser engine',()=>{
  const card=renderMethodCard(doc,{...sample,id:'geometry',state:'completed',
    media_url:'/api/method-lab/commissions/geometry/candidates/example/media'},
    new URL('https://video-service.panghuer.top/dev/animations/'),()=>{});
  const capabilities=card.children[1].children.find(row=>row.className?.includes('capability-labels')).children;
  assert.deepEqual(capabilities.map(row=>row.children[0].textContent),['画面创作：','动作编排：']);
  const [creation,motion]=capabilities.map(row=>row.children[1].children.filter(badge=>badge.dataset.capabilityLayer));
  assert.deepEqual(creation.map(row=>row.textContent),['HTML/SVG','Manim']);
  assert.deepEqual(motion.map(row=>row.textContent),['Anime.js']);
  assert.equal(creation[0].dataset.renderEngine,'html-svg');
  assert.equal(creation[1].dataset.renderEngine,undefined);
  assert.equal(creation[1].dataset.capabilityLayer,'creation');
  assert.equal(motion[0].dataset.capabilityLayer,'motion');
});
