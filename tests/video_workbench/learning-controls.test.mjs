import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {learningOptions, readLearningControls, restoreLearningControls, updateLearningControls,
  renderLearningProgress, renderInternalAttempts, LEARNING_CREATION, LEARNING_MOTION} from '../../video_workbench/animation_lab_web/learning-controls.mjs';
import {buildPictureProject, buildPictureExperiment} from '../../video_workbench/animation_lab_web/picture-model.mjs';
import {buildVideoProject, buildVideoExperiment} from '../../video_workbench/animation_lab_web/video-model.mjs';

const uuid = '00000000-0000-4000-8000-000000000001';
const fields = {title:'Learn material',goal:'Observe actual reference and make an editable work',start_seconds:0,end_seconds:4};
function controls(prefix) {
  const nodes = new Map();
  for (const [group, choices] of [['creation', LEARNING_CREATION], ['motion', LEARNING_MOTION]]) for (const choice of choices) nodes.set(prefix+'-'+group+'-'+choice, {checked:false,disabled:false});
  for (const suffix of ['allow-probes','allow-collaboration','learning-options','manim-help','motion-help']) nodes.set(prefix+'-'+suffix,{checked:true,disabled:false,textContent:''});
  return {getElementById:id=>nodes.get(id)??null};
}
const catalog = {capabilities:{creation:['html-svg','canvas','three','manim'],motion:['native-js','gsap'],
  manim:{admitted:true,version:'0.22.1',max_frames:60,fps:24,latex_available:false}}};

test('image and video requests freeze the same explicit capability and bounded strategy controls', () => {
  const options = {capability_selection:{creation:['three','canvas'],motion:['gsap']},allow_probes:false,allow_collaboration:true};
  for (const build of [buildPictureProject,buildVideoProject]) {
    const body = build({...fields,...options},['reference'],uuid);
    assert.deepEqual(body.capability_selection,{creation:['canvas','three'],motion:['gsap']});
    assert.equal(body.allow_probes,false); assert.equal(body.allow_collaboration,true);
  }
  for (const build of [buildPictureExperiment,buildVideoExperiment]) {
    assert.equal(build({...options,mode:'continue',feedback:'Try a different material'},uuid).allow_probes,false);
    assert.deepEqual(build({...options,mode:'fresh'},uuid).capability_selection.creation,['canvas','three']);
    assert.deepEqual(build({...options,mode:'retry',allow_probes:'invalid'},uuid),{request_id:uuid,mode:'retry'});
  }
  assert.deepEqual(learningOptions({}),{capability_selection:{},allow_collaboration:true,allow_probes:true});
  for (const bad of [{allow_probes:'false'}, {allow_collaboration:0}, {capability_selection:{creation:['canvas','canvas']}},
    {capability_selection:{creation:['unknown']}}, {capability_selection:{creation:['manim'],motion:['gsap']}}]) assert.throws(()=>learningOptions(bad));
});

test('Manim-only clears browser orchestration, while mixed choices permit admitted browser tools', () => {
  const doc=controls('video');
  restoreLearningControls(doc,'video',{capability_selection:{creation:['manim'],motion:['gsap']},allow_collaboration:false});
  updateLearningControls(doc,'video',catalog);
  assert.equal(doc.getElementById('video-motion-gsap').checked,false);
  assert.equal(doc.getElementById('video-motion-gsap').disabled,true);
  assert.deepEqual(readLearningControls(doc,'video'),{capability_selection:{creation:['manim'],motion:[]},allow_collaboration:false,allow_probes:true});
  assert.match(doc.getElementById('video-manim-help').textContent,/60 帧/);
  assert.match(doc.getElementById('video-motion-help').textContent,/Manim 使用自身动画/);
  doc.getElementById('video-creation-canvas').checked=true; updateLearningControls(doc,'video',catalog);
  assert.equal(doc.getElementById('video-motion-gsap').disabled,false);
  assert.equal(doc.getElementById('video-motion-animejs').disabled,true);
  doc.getElementById('video-motion-gsap').checked=true;
  assert.deepEqual(readLearningControls(doc,'video').capability_selection,{creation:['canvas','manim'],motion:['gsap']});
});

test('catalogue admission gates controls and retry cannot silently rewrite historical selections', () => {
  const doc=controls('picture');
  restoreLearningControls(doc,'picture',{capability_selection:{creation:['manim']},allow_probes:false});
  updateLearningControls(doc,'picture',{capabilities:{creation:['canvas'],motion:[],manim:{admitted:false}}},{retry:true});
  assert.equal(doc.getElementById('picture-creation-manim').checked,true);
  assert.equal(doc.getElementById('picture-creation-manim').disabled,true);
  assert.equal(doc.getElementById('picture-learning-options').hidden,true);
  updateLearningControls(doc,'picture',{capabilities:{creation:['canvas'],motion:[],manim:{admitted:false}}});
  assert.equal(doc.getElementById('picture-creation-manim').checked,false);
  assert.equal(doc.getElementById('picture-creation-canvas').disabled,false);
  assert.equal(doc.getElementById('picture-creation-three').disabled,true);
  assert.match(doc.getElementById('picture-manim-help').textContent,/尚未准入/);
  updateLearningControls(doc,'picture',catalog,{locked:true});
  assert.equal(doc.getElementById('picture-allow-probes').disabled,true);
});

class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this._text='';}
  set textContent(value){this._text=String(value);this.children=[];} get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}
  append(...items){this.children.push(...items);}
}
const doc={createElement:tag=>new Element(tag)};
test('actual progress and internal evidence are separate from final work and quality claims', () => {
  assert.equal(renderLearningProgress(doc,{}),null);
  const progress=renderLearningProgress(doc,{learning_progress:{phase:'probe_render',probes:[{id:'texture',goal:'dry texture',status:'authored'}],components:[]}});
  assert.match(progress.textContent,/渲染技法小样/); assert.match(progress.textContent,/按依赖顺序/);
  assert.match(progress.textContent,/不计为效果库作品/); assert.doesNotMatch(progress.textContent,/并发|已通过|效果更好/);
  const calls=[], attempts=renderInternalAttempts(doc,{candidates:[{id:'final'}],internal_attempts:[{id:'probe'}]},row=>{calls.push(row.id);return doc.createElement('figure');});
  assert.deepEqual(calls,['probe']); assert.match(attempts.textContent,/内部技法小样与证据（1）/);
  assert.equal(renderInternalAttempts(doc,{candidates:[{id:'final'}]},()=>{throw Error('not an internal study');}),null);
});

test('both OpenAPI contracts expose structured options, current admission and distinct internal evidence', () => {
  const base=new URL('../../video_workbench/animation_lab_web/',import.meta.url);
  for (const name of ['picture','video']) {
    const api=JSON.parse(readFileSync(new URL(name+'-api.openapi.json',base),'utf8'));
    for (const schema of ['ProjectStart','ExperimentStart']) {
      const props=api.components.schemas[schema].properties;
      assert.equal(props.allow_probes.default,true);assert.equal(props.allow_collaboration.default,true);
      assert.equal(props.capability_selection.$ref,'#/components/schemas/CapabilitySelection');
    }
    const retry=api.components.schemas.ExperimentStart.allOf[0].then.not.anyOf.map(row=>row.required[0]);
    for(const key of ['capability_selection','allow_probes','allow_collaboration'])assert.ok(retry.includes(key));
    const experiment=api.components.schemas.Project.properties.experiments.items.properties;
    assert.ok(experiment.learning_progress);assert.ok(experiment.internal_attempts);
    const html=readFileSync(new URL(name+'-library.html',base),'utf8');
    assert.match(html,new RegExp('id="'+name+'-allow-probes"'));assert.match(html,new RegExp('id="'+name+'-creation-manim"'));
  }
});
