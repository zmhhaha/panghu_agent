"""Real browser regressions for one host-owned canonical material authority.

These tiny static scenes exercise module imports and denied mutations only.
They do not call a model, create artistic content or claim container admission.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_service.contracts import canonical_bytes
from agent_service.profiles.video_animation_create import source_sha256
from video_workbench.scene_space_plan import automatic_plan, default_budget
from video_workbench.scene_space_renderer import HOST, RUNTIME
from test_scene_space_renderer import renderer


ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT = ('SCENE_SPACE_BROWSER', 'SCENE_SPACE_PLAYWRIGHT_PACKAGE', 'SCENE_SPACE_HYPERFRAMES_DIST')


@pytest.fixture
def native_dependencies():
    if not all(os.environ.get(name) for name in ENVIRONMENT):
        pytest.skip('Native browser acceptance dependencies not configured')
    commands = {name: shutil.which(name) for name in ('node', 'ffmpeg', 'ffprobe')}
    if not all(commands.values()):
        pytest.skip('Native Node/FFmpeg required')
    return commands


def tiny_plan():
    world = dict(schema='video.scene-world.v1', id='authority-world', revision=1, seed=17,
        style='plain-material-contract', space=dict(projection='planar', bounds=dict(x=0, y=0, width=320, height=180)),
        layers=[dict(id='single', order=0, parallax=1)],
        objects=[dict(id='sample', kind='rock', layer_id='single', parent_id=None,
            bounds=dict(x=0, y=0, width=4, height=4), transform=dict(x=10, y=10, scale_x=1, scale_y=1, rotation=0),
            depth=dict(kind='ordinal', value=0), anchors={})],
        layout=dict(grid=None, points=[], regions=[]), relations=[])
    return automatic_plan([world], id='authority-plan', viewport=dict(width=320, height=180),
        duration_frames=1, budget=default_budget(),
        intents=[dict(world_id=world['id'], weight=1, view='hold', focus=dict(x=160, y=90))])


def source(script):
    result = dict(engine='canvas', strategy=dict(description='A four-pixel material authority check',
        tradeoffs='Static native evidence only'), files={
        'scene.html': '<!doctype html><style>html,body{margin:0}canvas{display:block}</style>'
                      '<canvas data-scene-space-output></canvas><script src="scene.js"></script>',
        'scene.js': script, 'scene.css': '/* No autonomous animation. */'})
    return dict(result, source_sha256=source_sha256(result))


def policy_document():
    return dict(assets={}, constraints=dict(width=320, height=180, scene_space_quality=dict(
        intent=dict(schema='video.spatial-rendering-intent.v1', space_mode='layered', engine='canvas',
            camera_mode='static', required_features=['form', 'scale', 'coverage'], optional_features=['texture'],
            quality=dict(structural_errors='block', optional_degradation='declared')),
        configuration_sha256='a' * 64, usage_lineage='native-authority-check', usage_sha256='b' * 64,
        estimated_material_samples=2, material_noise_limit=2, usage_known=True)))


def native_case(renderer, dependencies, tmp_path, name, script, *, expect_error=None):
    binding = renderer.validate_request(name, source(script), policy_document(), scene_space=tiny_plan())
    policy = binding['scene_space']['appearance_policy']
    assert policy['actual_parameters']['material_quality'] == 'coarse'
    binding_path = tmp_path / 'binding.json'; binding_path.write_bytes(canonical_bytes(binding))
    case = dict(name=name, binding=str(binding_path), output=str(tmp_path / 'capture'), assets={})
    if expect_error:
        case['expect_error'] = expect_error
    config = dict(playwright_package=os.environ[ENVIRONMENT[1]], browser=os.environ[ENVIRONMENT[0]],
        hyperframes_dist=os.environ[ENVIRONMENT[2]], host=str(HOST), scene_runtime=str(RUNTIME),
        engine_runtime=str(ROOT / 'video_workbench/motion_renderer/engine_runtime.mjs'),
        project_runtime=str(ROOT / 'video_workbench/motion_renderer/hyperframes_project.mjs'),
        ffmpeg=dependencies['ffmpeg'], ffprobe=dependencies['ffprobe'], cases=[case],
        output=str(tmp_path / 'native-summary.json'))
    config_path = tmp_path / 'probe.json'; config_path.write_bytes(canonical_bytes(config))
    completed = subprocess.run([dependencies['node'], str(Path(__file__).with_name('scene_space_browser_probe.mjs')),
        str(config_path)], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    evidence = json.loads((tmp_path / 'native-summary.json').read_text(encoding='utf-8'))['results'][0]
    if not expect_error:
        assert evidence['captured_frames'] == 1
        assert all(evidence['checks'][key] for key in ('repeat_passed', 'reverse_passed', 'reload_passed'))
        observed = evidence['scene_space_observation']['appearance_observation']
        assert observed['policy'] == policy
        assert observed['material_noise_evaluations'] == 2
        assert observed['material_lattice_evaluations'] == 16
        assert observed['material_derivative_evaluations'] == 0
    return evidence


OPTIONS = "const P=[.37,.91,1.43],options={seed:17,weights:[.5,.3,.2],normalStrength:.5};"
FILL = "ctx.fillStyle='#32465a';ctx.fillRect(0,0,4,4);"


def test_native_top_level_direct_import_and_tools_share_coarse_policy_and_one_counter(
        renderer, native_dependencies, tmp_path):
    script = OPTIONS + """
let direct;
const ready=import('/runtime/scene_space_surface.mjs').then(module=>{
 direct=module;
 if(direct.appearancePolicy()?.actual_parameters.material_quality!=='coarse')
  throw Error('policy_not_installed_before_author_import');
});
window.__sceneSpaceArtwork={ready,paintObject(ctx,object,world,tools){
 const a=direct.sampleMaterial(P,options),b=tools.surface.sampleMaterial(P,options);
 for(const sample of [a,b])if(sample.work.noise_evaluations!==1||sample.normal_delta.some(v=>v!==0))
  throw Error('direct_import_escaped_coarse');
 if(direct.appearanceObservation().material_noise_evaluations!==2)
  throw Error('direct_and_tools_work_not_shared');
""" + FILL + '}};'
    evidence = native_case(renderer, native_dependencies, tmp_path, 'direct-and-tools', script)
    # Only one call passed through tools; both calls reached the same primitive.
    assert evidence['scene_space_observation']['surface_helper_calls']['sampleMaterial'] == 1


def test_native_fragment_alias_cannot_escape_the_canonical_material_limit(
        renderer, native_dependencies, tmp_path):
    script = OPTIONS + """
let direct,alias;
const ready=Promise.all([import('/runtime/scene_space_surface.mjs'),
 import('/runtime/scene_space_surface.mjs#alias')]).then(modules=>{[direct,alias]=modules;});
window.__sceneSpaceArtwork={ready,paintObject(ctx,object,world,tools){
 direct.sampleMaterial(P,options);alias.sampleMaterial(P,options);
 if(direct.appearanceObservation().material_noise_evaluations!==2
    ||alias.appearanceObservation().material_noise_evaluations!==2)
  throw Error('fragment_alias_has_separate_authority');
 tools.surface.sampleMaterial(P,options);
 throw Error('fragment_alias_bypassed_hard_limit');
}};
"""
    evidence = native_case(renderer, native_dependencies, tmp_path, 'fragment-over-limit', script,
        expect_error='scene_space_material_noise_budget_exceeded')
    assert evidence['captured_frames'] == 0
    assert evidence['expected_rejection'] == 'scene_space_material_noise_budget_exceeded'


def test_native_author_cannot_replace_or_mutate_the_host_policy_before_import(
        renderer, native_dependencies, tmp_path):
    script = OPTIONS + """
const property=Object.getOwnPropertyDescriptor(globalThis,'__trustedSceneAppearancePolicy');
const original=globalThis.__trustedSceneAppearancePolicy;
if(!property||property.configurable||property.writable||!Object.isFrozen(original)
 ||!Object.isFrozen(original.actual_parameters)||!Object.isFrozen(original.intent)
 ||!Object.isFrozen(original.intent.optional_features))throw Error('host_policy_not_deeply_frozen');
let rejected=false;
try{Object.defineProperty(globalThis,'__trustedSceneAppearancePolicy',
 {value:{...original,material_noise_limit:999999}});}catch(error){rejected=true;}
try{original.material_noise_limit=999999;original.actual_parameters.material_quality='full';}catch(error){}
if(!rejected||globalThis.__trustedSceneAppearancePolicy!==original||original.material_noise_limit!==2
 ||original.actual_parameters.material_quality!=='coarse')throw Error('author_replaced_host_policy');
let direct;
const ready=import('/runtime/scene_space_surface.mjs').then(module=>{direct=module;});
window.__sceneSpaceArtwork={ready,paintObject(ctx,object,world,tools){
 direct.sampleMaterial(P,options);tools.surface.sampleMaterial(P,options);
 let blocked=false;
 try{direct.sampleMaterial(P,options);}catch(error){blocked=String(error).includes('scene_space_material_noise_budget_exceeded');}
 if(!blocked)throw Error('author_increased_material_allowance');
""" + FILL + '}};'
    native_case(renderer, native_dependencies, tmp_path, 'immutable-host-authority', script)
