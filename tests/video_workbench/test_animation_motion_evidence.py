"""Static/source and sealed-render labels make no visual acceptance claim."""
from copy import deepcopy
import json

import pytest

from agent_service.contracts import canonical_bytes
from agent_service.profiles.video_animation_create import source_sha256
from video_workbench import animation_motion_evidence as evidence
from video_workbench.animation_method_service import _sample_engine
from video_workbench.animejs_library import descriptor


NATIVE = '''const ctx=document.querySelector('canvas').getContext('2d');
function draw(time){const x=Math.sin(time)*100;ctx.fillRect(x,0,8,8);}
window.__animationExperiment={seek(seconds){draw(seconds);}};'''


def source(js=NATIVE, *, before='', engine='canvas'):
    return dict(engine=engine, strategy=dict(description='Unit test source only', tradeoffs='No visual claim'),
        files={'scene.html': '<html><body><canvas width="320" height="180"></canvas>' + before
               + '<script src="./scene.js"></script></body></html>', 'scene.js': js})


def fixture(src=None, runtime=None):
    src = source() if src is None else src
    candidate = dict(id='candidate', source=src, operation_id='render-operation',
        media={'sha256': 'a' * 64}, technical={'status': 'passed'})
    demo = dict(source=deepcopy(src), runtime=runtime or dict(engine=src['engine'], admission={'status': 'admitted'}),
        media={'sha256': 'a' * 64}, technical={'status': 'passed'},
        provenance={'candidate_id': 'candidate', 'render_operation_id': 'render-operation'})
    return candidate, {'candidate': [demo]}


def motion(candidate, demos):
    return _sample_engine(candidate, demos).get('motion_orchestration', [])


def test_native_motion_uses_positive_registered_clock_dataflow_and_exact_provenance():
    candidate, demos = fixture()
    before = deepcopy((candidate, demos))
    assert motion(candidate, demos) == [dict(id='native-js', label='原生 JavaScript',
        status='source_verified', evidence_scope='source_inspection_not_visual_acceptance',
        source_sha256=source_sha256(candidate['source']), media_sha256='a' * 64,
        render_operation_id='render-operation')]
    assert (candidate, demos) == before


@pytest.mark.parametrize('js', [
    'window.__animationExperiment={seek(seconds){}};',
    '''function unused(t){ctx.fillRect(Math.sin(t),0,8,8);}
       window.__animationExperiment={seek(seconds){ctx.fillRect(0,0,8,8);}};''',
    '''const note="window.__animationExperiment={seek(t){ctx.fillRect(t*2,0,8,8)}}";''',
    '''/* window.__animationExperiment={seek(t){ctx.fillRect(t*2,0,8,8)}}; */''',
    '''function draw(t){ctx.fillRect(t*2,0,8,8);}''',
    '''function unused(t){return Math.sin(t)}
       window.__animationExperiment={seek(seconds){ctx.fillRect(0,0,8,8);unused(seconds);}};''',
    '''window.__animationExperiment={seek(seconds){const values=new Map();values.set('t',seconds);}};''',
])
def test_no_library_record_or_unused_code_never_establishes_native_motion(js):
    assert motion(*fixture(source(js))) == []


def test_css_and_unloaded_extra_source_do_not_establish_native_motion():
    src = source('window.__animationExperiment={seek(seconds){}};')
    src['files']['scene.html'] = '<style>@keyframes pulse{to{opacity:0}}canvas{animation:pulse 2s}</style>'
    src['files']['scene.js'] = NATIVE
    assert motion(*fixture(src)) == []


def test_registered_alias_can_forward_clock_to_three_position_updates():
    js = '''window.__animationExperiment={seek:function(seconds){}};
const api=window.__animationExperiment;
function draw(seconds){const theta=seconds*2;crank.rotation.set(0,0,theta);}
api.seek=draw;'''
    assert motion(*fixture(source(js, engine='three')))[0]['id'] == 'native-js'


def test_registered_seek_can_forward_clock_to_assigned_function_expression():
    js = '''let compose=null;
window.__animationExperiment={seek(seconds){if(compose)compose(seconds);}};
compose=function(seconds){const number=Number(seconds),t=Math.max(0,number);
ctx.globalAlpha=Math.min(1,t/10);ctx.drawImage(painting,0,0);};'''
    assert motion(*fixture(source(js)))[0]['id'] == 'native-js'
    unrelated = js.replace('if(compose)compose(seconds);', 'ctx.drawImage(painting,0,0);')
    assert motion(*fixture(source(unrelated))) == []


def test_svg_helper_and_template_motion_are_recognized_without_promoting_unknown_syntax():
    js = '''const set=(id,k,v)=>document.getElementById(id).setAttribute(k,String(v));
function seek(seconds){const x=seconds*20;set('subject','transform',`translate(${x} 0)`);}
window.__animationExperiment={seek};'''
    assert motion(*fixture(source(js, engine='html-svg')))[0]['id'] == 'native-js'


@pytest.mark.parametrize('change', ['source', 'media', 'operation', 'technical', 'admission'])
def test_labels_cannot_cross_changed_source_media_operation_or_admission(change):
    candidate, demos = fixture()
    demo = demos['candidate'][0]
    if change == 'source':
        demo['source']['files']['scene.js'] += '\n// different frozen bytes'
    elif change == 'media':
        demo['media']['sha256'] = 'b' * 64
    elif change == 'operation':
        demo['provenance']['render_operation_id'] = 'other-operation'
    elif change == 'technical':
        demo['technical']['status'] = 'failed'
    else:
        demo['runtime']['admission']['status'] = 'not_admitted'
    assert motion(candidate, demos) == []


def anime_runtime():
    libraries = {'animejs': descriptor()}
    return dict(engine='html-svg', admission={'status': 'admitted'}, authoring_libraries=libraries,
        authoring_library_observation=dict(scope='loaded_scripts_not_api_calls_or_visual_application',
            authoring_libraries=deepcopy(libraries), used_libraries=['animejs'],
            loaded_scripts=['/assets/anime.esm.min.js', '/assets/anime_timeline.mjs']))


ANIME = '''import {createFrameTimeline} from './assets/anime_timeline.mjs';
const timeline=createFrameTimeline({fps:24,durationFrames:24});timeline.add(target,{x:[0,100]},0);
window.__animationExperiment={seek(t){timeline.renderAt({global_frame:t*24});}};'''


def test_anime_motion_requires_real_library_observation_and_substantive_source_calls():
    candidate, demos = fixture(source(ANIME, engine='html-svg'), anime_runtime())
    assert motion(candidate, demos)[0]['id'] == 'animejs'
    assert motion(candidate, demos)[0]['status'] == 'render_observed'
    assert len(motion(candidate, demos)) == 1
    demos['candidate'][0]['runtime']['authoring_library_observation']['used_libraries'] = []
    assert motion(candidate, demos) == []


def test_loaded_anime_dependency_with_no_timeline_calls_is_not_a_motion_claim():
    assert motion(*fixture(source('window.__animationExperiment={seek(t){}};', engine='html-svg'), anime_runtime())) == []


GSAP = '''const timeline=gsap.timeline({paused:true});timeline.to(target,{x:100});
window.__animationExperiment={seek(t){timeline.seek(t);}};'''


def gsap_fixture():
    runtime = dict(engine='html-svg', admission={'status': 'admitted'},
        library_inventory={'gsap': dict(version=evidence.GSAP_VERSION, sha256=evidence.GSAP_SHA256)},
        authoring_library_observation=dict(scope='loaded_scripts_not_api_calls_or_visual_application',
            loaded_scripts=['/assets/gsap.min.js', '/scene.js']))
    return fixture(source(GSAP, before='<script src="./assets/gsap.min.js"></script>', engine='html-svg'), runtime)


def test_gsap_motion_requires_source_calls_and_exact_measured_loaded_library():
    candidate, demos = gsap_fixture()
    assert motion(candidate, demos)[0]['id'] == 'gsap'
    assert motion(candidate, demos)[0]['version'] == evidence.GSAP_VERSION


@pytest.mark.parametrize('change', ['inventory', 'asset', 'version', 'loaded', 'source_calls', 'local_import'])
def test_default_installed_or_loaded_gsap_never_establishes_api_usage(change):
    candidate, demos = gsap_fixture()
    runtime = demos['candidate'][0]['runtime']
    if change == 'inventory':
        runtime.pop('library_inventory')
    elif change == 'asset':
        runtime['library_inventory']['gsap']['sha256'] = 'f' * 64
    elif change == 'version':
        runtime['library_inventory']['gsap']['version'] = '0.0.0'
    elif change == 'loaded':
        runtime['authoring_library_observation']['loaded_scripts'] = []
    else:
        if change == 'source_calls':
            candidate['source']['files']['scene.js'] = 'window.__animationExperiment={seek(t){}};'
        else:
            candidate['source']['files']['scene.html'] = candidate['source']['files']['scene.html'].replace(
                '<script src="./assets/gsap.min.js"></script>', '')
        demos['candidate'][0]['source'] = deepcopy(candidate['source'])
    assert motion(candidate, demos) == []


def measured_receipt():
    return dict(runtime=dict(schema='video.motion-runtime.v3', gsap=evidence.GSAP_VERSION,
                             gsap_sha256=evidence.GSAP_SHA256))


def test_inventory_requires_both_actual_host_measurement_and_requested_pinned_bytes():
    receipt = measured_receipt()
    request = dict(assets_sha256={'gsap.min.js': evidence.GSAP_SHA256})
    expected = {'gsap': dict(version=evidence.GSAP_VERSION, sha256=evidence.GSAP_SHA256)}
    assert evidence.measured_library_inventory(receipt, request) == expected
    request['assets_sha256']['gsap.min.js'] = 'f' * 64
    assert evidence.measured_library_inventory(receipt, request) == {}
    assert evidence.measured_library_inventory({}, request) == {}


@pytest.mark.parametrize('historical', [False, True])
def test_renderer_emits_measured_inventory_without_rewriting_sealed_historical_results(tmp_path, monkeypatch, historical):
    from test_animation_method_renderer import SyntheticExecution, IMAGE, SOURCE, document
    from video_workbench import animation_method_renderer as renderer

    execution = SyntheticExecution()

    def measured_execution(directory):
        outcome = execution(directory)
        path = directory / 'work' / 'render-receipt.json'
        receipt = json.loads(path.read_bytes())
        receipt.update(measured_receipt())
        path.write_bytes(canonical_bytes(receipt))
        return outcome

    monkeypatch.setattr(renderer, 'execute_method_render', measured_execution)
    original = evidence.measured_library_inventory
    if historical:
        monkeypatch.setattr(evidence, 'measured_library_inventory', lambda *_: {})
    value = renderer.MethodAnimationRenderer(tmp_path / 'render', IMAGE, enabled=True)
    initial = value.render('measured-library', SOURCE, document())
    if historical:
        assert 'library_inventory' not in initial['runtime']
        monkeypatch.setattr(evidence, 'measured_library_inventory', original)
    else:
        assert initial['runtime']['library_inventory']['gsap']['sha256'] == evidence.GSAP_SHA256
    assert value.lookup('measured-library') == initial
    assert value.media_path('measured-library').is_file()
    assert execution.calls == 1
