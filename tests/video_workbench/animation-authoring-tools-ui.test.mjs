import test from 'node:test';
import assert from 'node:assert/strict';
import {observedTools,matchesAuthoringTool,resourceAuthoringRequest,toolSearchText}
  from '../../video_workbench/animation_lab_web/authoring-tools.mjs';

const sample={engine_status:'rendered',authoring_tools:[{id:'animejs',version:'4.5.0',status:'loaded',
  evidence_scope:'render_loaded_dependency_not_visual_application_or_acceptance'}]};

test('tool filters use only actual observed dependencies',()=>{
  assert.equal(matchesAuthoringTool(sample,'animejs'),true);
  assert.equal(matchesAuthoringTool(sample,'manim'),false);
  assert.equal(matchesAuthoringTool({},'all'),true);
  assert.match(toolSearchText(sample),/anime\.js/);
  for(const value of [{authoring_tools:sample.authoring_tools},
    {...sample,engine_status:'unverified'},
    {engine_status:'rendered',runtime_requirements:{authoring_libraries:{animejs:{}}}}]) {
    assert.deepEqual(observedTools(value),[]);
  }
});

test('tool names and markup from metadata cannot create a badge',()=>{
  const value={...sample,authoring_tools:[...sample.authoring_tools,
    {...sample.authoring_tools[0],id:'<script>'},
    {...sample.authoring_tools[0],id:'manim',status:'requested'}]};
  assert.deepEqual(observedTools(value),[{id:'animejs',label:'Anime.js',version:'4.5.0'}]);
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
