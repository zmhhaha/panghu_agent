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
  observed('manim','0.19.0'),observed('animejs','4.5.0'),
]};

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
      authoring_tools:[{...observed('manim','0.19.0'),status:'requested'},
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
  append(...children) { this.children.push(...children); }
  addEventListener(type,handler) { this.handlers[type]=handler; }
  setAttribute() {}
}
const doc={createElement:tag=>new Element(tag)};

test('creation and motion badges are distinct and never invent GSAP use',()=>{
  const creation=renderAuthoringBadges(doc,sample,'creation');
  const motion=renderAuthoringBadges(doc,sample);
  assert.deepEqual(creation.map(row=>[row.textContent,row.dataset.capabilityLayer]),[['Manim 0.19.0','creation']]);
  assert.deepEqual(motion.map(row=>[row.textContent,row.dataset.capabilityLayer]),[['Anime.js 4.5.0','motion']]);
  assert.match(creation[0].title,/不代表效果已获认可/);
  assert.equal(renderAuthoringBadges(doc,{...sample,authoring_tools:[]}).length,0);
});

test('visible motion filter groups GSAP with Anime.js and excludes Manim',()=>{
  const filters=new Element('div'),engine={parentNode:{parentNode:filters}};
  const document={...doc,getElementById:id=>id==='gallery-engine'?engine:null};
  let changes=0;
  const controls=mountAuthoringControls(document,()=>{changes++;});
  const label=filters.children[0],select=label.children[0];
  assert.equal(label.textContent,'动作编排');
  assert.deepEqual(select.children.map(row=>[row.value,row.textContent]),[
    ['all','全部'],['gsap','GSAP'],['animejs','Anime.js'],
  ]);
  select.value='animejs';select.handlers.change();
  assert.equal(controls.selected,'animejs');assert.equal(changes,1);
  controls.resetFilter();assert.equal(controls.selected,'all');
});

test('effect card presents creation and motion as separate layers while retaining its real browser engine',()=>{
  const card=renderMethodCard(doc,{...sample,id:'geometry',state:'completed'},
    new URL('https://video-service.panghuer.top/dev/animations/'),()=>{});
  const capabilities=card.children[1].children[1].children;
  assert.deepEqual(capabilities.map(row=>row.textContent),[
    '画面创作','HTML/SVG','Manim 0.19.0','动作编排','Anime.js 4.5.0',
  ]);
  assert.equal(capabilities[1].dataset.renderEngine,'html-svg');
  assert.equal(capabilities[2].dataset.renderEngine,undefined);
  assert.equal(capabilities[2].dataset.capabilityLayer,'creation');
  assert.equal(capabilities[4].dataset.capabilityLayer,'motion');
});
