"""Keep published continuity v1 bytes while fixing async admission in v2."""
from copy import deepcopy
import json
import shutil
import subprocess

import pytest

from agent_service.contracts import ServiceError
from video_workbench.continuity import (
    CONTINUITY_COMPILER_VERSION, CONTINUITY_COMPILER_TEMPLATES, build_continuity_plan,
    compile_continuity_project, recompile_continuity_project, resolve_continuity,
)
from video_workbench.motion import sha256
from tests.video_workbench.test_continuity import scenes


V1 = 'motion-continuity-compiler-1'
V2 = 'motion-continuity-compiler-2'
V1_GOLDENS = {
    'flow':'1af62f8ed6dc21bf9a448ad6de2c139ef10706d4548b4463b83344f3abf1a9f2',
    'compare':'b8d5d30f40b83c4a0098f5be4d2c031eea1ba20e857ecafc4766e048ad32676f',
    'focus':'0acd153c69a1e06d62fc37ea2da9f1eb0ce39299db6f9c3685cf16dd6ca52a84',
    'copy':'2627c0a9306d1a129d9a7cc2ef8282f0f33e45c534bca36a75565011b46f0414',
}


def arguments(case):
    inputs = scenes(single=case == 'copy',template=case if case != 'copy' else 'flow')
    if case != 'copy':
        inputs[1]['motion']['plan']['elements'][0]['detail'] = '保留完整回答与尝试记录'
    plan = build_continuity_plan(inputs)
    if case == 'copy':
        plan['links'][0]['mode'] = 'copy'
    return inputs,plan


@pytest.mark.parametrize('case',list(V1_GOLDENS))
def test_published_v1_resolution_and_every_compiled_file_keep_their_original_bytes(case):
    inputs,plan = arguments(case)
    resolution = resolve_continuity(inputs,plan,compiler_version=V1)
    files = [{name:sha256(raw) for name,raw in compile_continuity_project(scene,segment).items()}
             for scene,segment in zip(inputs,resolution['segments'])]
    assert sha256(dict(resolution=resolution,files=files)) == V1_GOLDENS[case]


@pytest.mark.parametrize('compiler',[V1,V2])
def test_manifest_pair_and_recompile_follow_the_frozen_segment_compiler(compiler):
    inputs,plan = arguments('flow')
    segment = resolve_continuity(inputs,plan,compiler_version=compiler)['segments'][1]
    files = compile_continuity_project(inputs[1],segment)
    wrapper,beats,alignment = (json.loads(files[name]) for name in ('plan.json','beats.json','alignment.json'))
    assert recompile_continuity_project(wrapper,beats,alignment) == files
    manifest = json.loads(files['manifest.json'])
    assert manifest['compiler'] == compiler and manifest['template'] == CONTINUITY_COMPILER_TEMPLATES[compiler]


def test_new_default_changes_only_compiler_identity_and_initialization_order():
    inputs,plan = arguments('flow')
    old = resolve_continuity(inputs,plan,compiler_version=V1)
    current = resolve_continuity(inputs,plan)
    assert CONTINUITY_COMPILER_VERSION == current['compiler'] == V2
    for old_segment,new_segment,scene in zip(old['segments'],current['segments'],inputs):
        for key in ('base_timing','leading_transition_seconds','global_start_frame','global_end_frame',
                    'entity_map','copy_lineage','detail_updates','initial_checkpoint','final_checkpoint','initial_states','relations'):
            assert old_segment[key] == new_segment[key]
        old_timeline,new_timeline = deepcopy(old_segment['timeline']),deepcopy(new_segment['timeline'])
        old_timeline.pop('compiler')
        new_timeline.pop('compiler')
        assert old_timeline == new_timeline
        old_files,new_files = compile_continuity_project(scene,old_segment),compile_continuity_project(scene,new_segment)
        assert old_files['beats.json'] == new_files['beats.json']
        assert old_files['alignment.json'] == new_files['alignment.json']


def test_unknown_continuity_compiler_is_rejected_instead_of_using_current_defaults():
    inputs,plan = arguments('flow')
    with pytest.raises(ServiceError,match='continuity_compiler_version_unsupported'):
        resolve_continuity(inputs,plan,compiler_version='motion-continuity-compiler-99')


ASYNC_REGISTRY_RUNNER = r'''
import vm from 'node:vm';
import assert from 'node:assert/strict';
let raw='';for await(const chunk of process.stdin)raw+=chunk;
const {script,compiler,events,duration}=JSON.parse(raw);
let releaseFonts;
const ready=new Promise(resolve=>{releaseFonts=resolve;});
const sequence=[],timelines=[];
const registry=new Proxy({}, {set(object,key,value){
 if(key==='video-motion')sequence.push({stage:'registered',built:value.events.length,duration:value.duration});
 object[key]=value;return true;
}});
const window={};Object.defineProperty(window,'__timelines',{get(){return window.registry;},set(){window.registry=registry;}});
const dummy={querySelector(){return dummy;}};
const gsap={set(){},timeline(){const timeline={events:[],duration:0,
 fromTo(...args){this.events.push(args);sequence.push({stage:'event'});return this;},
 set(_target,_props,when){this.duration=when;sequence.push({stage:'end'});return this;},
 seek(time){sequence.push({stage:'seek',time});return this;}};timelines.push(timeline);return timeline;}};
vm.runInNewContext(script,{window,document:{fonts:{ready},getElementById(){return dummy;}},gsap});
assert.equal(timelines.length,1);
assert.equal(timelines[0].events.length,0);
if(compiler.endsWith('-2'))assert.equal(window.__timelines?.['video-motion'],undefined,'timeline leaked before the font-dependent build');
else assert.equal(window.__timelines['video-motion'],timelines[0]);
releaseFonts();await window.__videoMotion.ready;
assert.equal(window.__timelines['video-motion'],timelines[0]);
assert.equal(timelines[0].events.length,events);
assert.equal(timelines[0].duration,duration);
if(compiler.endsWith('-2')){
 assert.deepEqual(sequence.at(-1),{stage:'registered',built:events,duration});
 assert.equal(sequence.at(-2).stage,'seek');
}else assert.equal(sequence[0].built,0,'legacy regression fixture should expose the original early registration');
'''


@pytest.mark.parametrize('compiler',[V1,V2])
def test_real_async_callback_registers_complete_timeline_after_fonts_ready(compiler):
    """Execute the generated JS lifecycle; CSS/CLI encoding is checked separately."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node runtime unavailable for generated-JS lifecycle test')
    inputs = scenes(single=True)
    segment = resolve_continuity(inputs,build_continuity_plan(inputs),compiler_version=compiler)['segments'][0]
    page = compile_continuity_project(inputs[0],segment)['index.html'].decode()
    script = page.rsplit('<script>',1)[1].split('</script>',1)[0]
    process = subprocess.run([node,'--input-type=module','-e',ASYNC_REGISTRY_RUNNER],
        input=json.dumps(dict(script=script,compiler=compiler,events=len(segment['timeline']['actions']),
                              duration=segment['timeline']['duration_seconds'])),
        text=True,capture_output=True,timeout=20)
    assert process.returncode == 0, process.stderr
