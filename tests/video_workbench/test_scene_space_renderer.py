"""New rendering identity, shared math and bounded real object raster caches."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import io
import os

import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.profiles.video_animation_create import source_sha256
from video_workbench.scene_space_plan import compile_evaluator, freeze_plan
from video_workbench.scene_space_renderer import (
    HOST,RUNTIME,SceneSpaceRenderer,prepare_scene_render,render_capability,
)
from video_workbench import animation_method_renderer as execution

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture
def plan():
    return json.loads((ROOT/'tests/fixtures/scene_space/plan.json').read_text(encoding='utf-8'))


def source():
    value=dict(engine='canvas',strategy=dict(description='Object painters under a shared camera',tradeoffs='No continuous observation'),
        files={'scene.html':'<!doctype html><style>html,body{margin:0}canvas{display:block}</style><canvas data-scene-space-output></canvas><script src="scene.js"></script>',
            'scene.js':"window.__sceneSpaceArtwork={paintObject(ctx,object){ctx.fillStyle=object.id==='near-tree'?'#482c1e':'#5b83ad';ctx.fillRect(object.bounds.x,object.bounds.y,object.bounds.width,object.bounds.height);}};",
            'scene.css':'/* Scene layout is host owned. */'})
    return dict(value,source_sha256=source_sha256(value))


def test_scene_coverage_is_collected_before_document_script_ids_expire():
    node=shutil.which('node')
    if not node:pytest.skip('Node unavailable')
    subprocess.run([node,'--test',str(Path(__file__).with_name('scene-space-coverage.test.mjs'))],
        check=True,timeout=30)


def test_binding_bounds_resolution_before_any_painter_runs(plan):
    original=deepcopy(plan)
    render=prepare_scene_render(plan,source_sha256=source()['source_sha256'])
    assert plan==original
    assert render['schema']=='video.scene-space-render-binding.v1'
    assert render['runtime_sha256']==hashlib.sha256(RUNTIME.read_bytes()).hexdigest()
    assert render['total_cached_pixels']==sum(row['pixels'] for row in render['object_caches'])
    assert len(render['object_caches'])==len(plan['worlds'][0]['objects'])
    assert all(row['width']>0 and row['height']>0 for row in render['object_caches'])
    assert render['frame_range']==dict(start_frame=0,end_frame=96)
    assert render['evidence_scope']=='render_inputs_not_visual_acceptance'


def test_intervals_share_the_same_full_plan_raster_resolution(plan):
    first=prepare_scene_render(plan,source_sha256=source()['source_sha256'],frame_range=dict(start_frame=0,end_frame=48))
    last=prepare_scene_render(plan,source_sha256=source()['source_sha256'],frame_range=dict(start_frame=48,end_frame=96))
    assert first['object_caches']==last['object_caches']
    assert first['frozen']==last['frozen']
    assert first['sha256']!=last['sha256']


def test_object_cache_budget_is_checked_beyond_the_layer_view_estimate(plan):
    world=plan['worlds'][0]
    for index in range(30):
        duplicate=deepcopy(world['objects'][1]);duplicate['id']='extra-peak-'+str(index)
        world['objects'].append(duplicate)
    plan['budget']['render']['max_cached_pixels']=4_000_000
    freeze_plan(plan)  # Layer view coverage itself still fits.
    with pytest.raises(ServiceError,match='object_cache_budget'):
        prepare_scene_render(plan,source_sha256=source()['source_sha256'])


def test_unadmitted_backends_do_not_gain_runtime_authority(plan):
    assert render_capability(admitted_engines=['html-svg'])['admitted'] is False
    assert render_capability(admitted_engines=['canvas','three'])['engines']==['canvas']
    with pytest.raises(ServiceError,match='backend_not_admitted'):
        prepare_scene_render(plan,source_sha256=source()['source_sha256'],engine='three')
    plan['worlds'][0]['layers'][0]['cache_policy']='dynamic'
    with pytest.raises(ServiceError,match='cache_policy_not_admitted'):
        prepare_scene_render(plan,source_sha256=source()['source_sha256'])


def test_python_and_browser_runtime_agree_on_absolute_frame_geometry(plan,tmp_path):
    node=shutil.which('node')
    if not node:pytest.skip('Node unavailable')
    plan['worlds'][0]['layers'][-1]['opacity']=.4
    for shot in plan['shots']:
        shot['local_animation']=[dict(id='tree-opacity',target_id='near-tree',property='opacity',
            keyframes=[dict(frame=shot['start_frame'],value=.5)]),
            dict(id='leaf-opacity',target_id='leaf-group',property='opacity',
            keyframes=[dict(frame=shot['start_frame'],value=.75)])]
    frozen=freeze_plan(plan)
    data=tmp_path/'plan.json';data.write_bytes(canonical_bytes(frozen))
    script=tmp_path/'probe.mjs'
    script.write_text("import {readFileSync} from 'node:fs';\n"
        "import {evaluateSceneFrame} from "+json.dumps(RUNTIME.resolve().as_uri())+";\n"
        "const p=JSON.parse(readFileSync(process.argv[2]));\n"
        "console.log(JSON.stringify([95,0,48,96,95].map(f=>evaluateSceneFrame(p,f))));\n",encoding='utf-8')
    states=json.loads(subprocess.check_output([node,str(script),str(data)],text=True))
    evaluator=compile_evaluator(frozen)
    assert states[0]==states[-1]
    assert next(obj['opacity'] for obj in states[0]['objects'] if obj['id']=='leaf-group')==pytest.approx(.15)
    for actual in states:
        expected=evaluator.frame(actual['frame'])
        assert actual['shot_id']==expected['shot_id'] and actual['interval_id']==expected['interval_id']
        assert actual['camera']==pytest.approx(expected['camera'])
        for a,e in zip(actual['objects'],expected['objects']):
            assert a['id']==e['id']
            assert a['screen_matrix']==pytest.approx(e['screen_matrix'])
            assert a['screen_bounds']==pytest.approx(e['screen_bounds'])
            assert a['opacity']==pytest.approx(e['opacity'])
            for name in a['screen_anchors']:
                assert a['screen_anchors'][name]==pytest.approx(e['screen_anchors'][name])


def test_svg_and_three_adapters_preserve_native_parenting_and_reject_missing_compositing(plan,tmp_path):
    node=shutil.which('node')
    if not node:pytest.skip('Node unavailable')
    path=tmp_path/'plan.json';path.write_bytes(canonical_bytes(freeze_plan(plan)))
    script=tmp_path/'adapters.mjs'
    script.write_text("import {readFileSync} from 'node:fs';\n"
        "import {evaluateSceneFrame,applySvgFrame,applyThreeFrame} from "+json.dumps(RUNTIME.resolve().as_uri())+";\n"
        "const p=JSON.parse(readFileSync(process.argv[2])),s=evaluateSceneFrame(p,70);\n"
        "const svg=new Map(s.objects.map(o=>[o.id,{parentNode:null,values:{},setAttribute(k,v){this.values[k]=v;}}]));\n"
        "svg.get('leaf-group').parentNode=svg.get('near-tree');applySvgFrame(s,{objects:svg});\n"
        "const three=new Map(s.objects.map(o=>[o.id,{parent:null,matrix:{set(...v){this.values=v;}}}]));\n"
        "three.get('leaf-group').parent=three.get('near-tree');applyThreeFrame(s,{objects:three});\n"
        "s.layers[0].mask_region_id='mask';let rejected=false;try{applySvgFrame(s,{objects:svg});}catch(e){rejected=e.message==='scene_space_layer_adapter_required';}\n"
        "console.log(JSON.stringify({svg:svg.get('leaf-group').values,three:three.get('leaf-group').matrix.values,rejected}));\n",
        encoding='utf-8')
    result=json.loads(subprocess.check_output([node,str(script),str(path)],text=True))
    assert [float(n) for n in result['svg']['transform'].removeprefix('matrix(').removesuffix(')').split()]==pytest.approx([1,0,0,1,70,130])
    assert result['three'][3]==pytest.approx(70)
    assert result['three'][7]==pytest.approx(130)
    assert result['rejected'] is True


def test_new_renderer_snapshot_uses_its_own_host_and_sidecar(plan,tmp_path):
    runtime=SimpleNamespace(image='registry.example/renderer:latest@sha256:'+'a'*64,
        assets_dir=None,enabled=True,timeout=60,
        capabilities=lambda:dict(allowed_engines=['canvas'],assets={}))
    renderer=SceneSpaceRenderer(tmp_path/'scene-space',runtime_renderer=runtime)
    document=dict(constraints=dict(width=1280,height=720,duration_seconds=4),assets={})
    binding=renderer.validate_request('fixture',source(),document,scene_space=plan)
    assert not renderer.root.exists()  # No claim, queue, output directory or execution.
    assert binding['request']['schema']=='video.scene-space-render-request.v1'
    assert binding['request']['host_sha256']==hashlib.sha256(HOST.read_bytes()).hexdigest()
    assert binding['request']['sidecar_sha256']==hashlib.sha256(canonical_bytes(binding['scene_space'])).hexdigest()
    assert renderer.lookup('missing') is None
    changed=deepcopy(document);changed['constraints']['duration_seconds']=3
    with pytest.raises(ServiceError,match='duration_changed'):
        renderer._binding('fixture',source(),changed,plan)


def test_still_render_freezes_one_frame_without_observing_a_camera(plan,tmp_path):
    runtime=SimpleNamespace(image='registry.example/renderer:latest@sha256:'+'a'*64,
        assets_dir=None,enabled=True,timeout=60,
        capabilities=lambda:dict(allowed_engines=['canvas'],assets={}))
    renderer=SceneSpaceRenderer(tmp_path/'scene-space',runtime_renderer=runtime)
    binding=renderer._binding('still',source(),dict(constraints=dict(width=1280,height=720),assets={}),plan)
    assert binding['request']['static'] is True
    assert binding['scene_space']['frame_range']==dict(start_frame=0,end_frame=1)
    assert binding['scene_space']['frozen']['plan']['duration_frames']==96


@pytest.fixture
def renderer(tmp_path):
    runtime=SimpleNamespace(image='registry.example/renderer:latest@sha256:'+'a'*64,
        assets_dir=None,enabled=True,timeout=60,
        capabilities=lambda:dict(allowed_engines=['canvas'],assets={}))
    return SceneSpaceRenderer(tmp_path/'render',runtime_renderer=runtime)


def synthetic_execution(calls):
    def execute(directory):
        from PIL import Image
        calls.append(directory)
        request=json.loads((directory/'input/request.json').read_bytes())
        sidecar=json.loads((directory/'input/scene-space.json').read_bytes())
        work=directory/'work';work.mkdir()
        image=io.BytesIO();Image.new('RGB',(1280,720),'white').save(image,format='PNG')
        raw=image.getvalue();(work/'output.png').write_bytes(raw)
        receipt=dict(schema='video.scene-space-render-receipt.v1',status='rendered',
            request_identity=request['identity'],request_sha256=hashlib.sha256(canonical_bytes(request)).hexdigest(),
            **{k:request[k] for k in ('host_sha256','scene_runtime_sha256','sidecar_sha256','image_digest')},
            output_sha256=hashlib.sha256(raw).hexdigest(),output_bytes=len(raw),captured_frames=1,
            measured_render_seconds=.25,sample_frames=[],
            checks=dict(repeat_passed=True,reverse_passed=True,reload_passed=True),
            engine_observation=dict(engine='canvas',revision='animation-engine-runtime-1',contexts=[
                dict(kind='2d',connected=index==0,visible=index==0,width=1280,height=720) for index in range(len(sidecar['object_caches'])+2)]),
            project_runtime=dict(core_sha256=request['hyperframes_core_sha256'],project_sha256=request['project_sha256'],core_seek_count=1),
            scene_space_observation=dict(plan_sha256=sidecar['frozen']['plan_sha256'],recipes_executed=0,
                objects_painted=len(sidecar['object_caches'])))
        (work/'render-receipt.json').write_bytes(canonical_bytes(receipt))
        outcome=dict(schema='video.animation-method-render-outcome.v1',request_identity=request['identity'],
            request_sha256=hashlib.sha256(canonical_bytes(request)).hexdigest(),image='registry.example/renderer:latest@sha256:'+'a'*64,
            container_id='c'*64,actual_image_id='sha256:'+'d'*64,status='rendered',automatic_retry=False,
            cleanup=dict(status='removed_and_confirmed_absent',container_id='c'*64))
        (directory/'render-outcome.json').write_bytes(canonical_bytes(outcome))
    return execute


def test_new_claim_is_durable_and_technical_output_has_compatible_shape(renderer,plan,monkeypatch):
    calls=[];monkeypatch.setattr(execution,'execute_method_render',synthetic_execution(calls))
    doc=dict(constraints=dict(width=1280,height=720),assets={})
    result=renderer.render('still',source(),doc,scene_space=plan)
    assert result['technical_passed'] and result['stop_proven']
    assert result['technical']['status']=='passed' and result['media']['audio_sha256'] is None
    assert result['measured_render_seconds']==.25
    assert json.loads(Path(result['source_path']).read_bytes())==source()
    assert renderer.render('still',source(),doc,scene_space=plan)==result
    assert renderer.lookup('still')==result and len(calls)==1
    assert renderer.media_path('still')==Path(result['artifact_path'])
    assert renderer.frames('still')==[]
    assert not result['whole_video_watched'] and not result['motion_observed']


def test_lost_execution_response_is_not_a_proven_never_created_failure(renderer,plan,monkeypatch):
    calls=[]
    def lost(directory):
        calls.append(directory)
        raise TimeoutError('Response lost after dispatch')
    monkeypatch.setattr(execution,'execute_method_render',lost)
    doc=dict(constraints=dict(width=1280,height=720),assets={})
    result=renderer.render('lost',source(),doc,scene_space=plan)
    assert result['status']=='unknown' and not result['stop_proven']
    assert renderer.render('lost',source(),doc,scene_space=plan)==result and len(calls)==1
    assert not (calls[0]/'render-outcome.json').exists()


def test_changed_bound_source_is_rejected_after_render(renderer,plan,monkeypatch):
    calls=[];monkeypatch.setattr(execution,'execute_method_render',synthetic_execution(calls))
    renderer.render('tamper',source(),dict(constraints=dict(width=1280,height=720),assets={}),scene_space=plan)
    (calls[0]/'input/scene/scene.js').write_text('changed',encoding='utf-8')
    with pytest.raises(ServiceError,match='source_changed'):
        renderer.lookup('tamper')


def test_expected_request_is_checked_before_any_claim_or_execution(renderer,plan,monkeypatch):
    calls=[];monkeypatch.setattr(execution,'execute_method_render',synthetic_execution(calls))
    doc=dict(constraints=dict(width=1280,height=720),assets={})
    prepared=renderer.validate_request('expected',source(),doc,scene_space=plan)
    with pytest.raises(ServiceError,match='expected_request_changed') as mismatch:
        renderer.render('expected',source(),doc,scene_space=plan,expected_request_sha256='0'*64)
    assert mismatch.value.not_dispatched is True and mismatch.value.stop_proven is True
    assert not renderer.root.exists() and calls==[]
    result=renderer.render('expected',source(),doc,scene_space=plan,
        expected_request_sha256=prepared['request_sha256'])
    assert result['render_request_sha256']==prepared['request_sha256'] and len(calls)==1


def test_prior_uncertain_operation_cannot_gain_stop_proof_from_a_later_mismatch(renderer,plan,monkeypatch):
    def lost(directory):raise TimeoutError('Unknown dispatch response')
    monkeypatch.setattr(execution,'execute_method_render',lost)
    doc=dict(constraints=dict(width=1280,height=720),assets={})
    assert renderer.render('unknown',source(),doc,scene_space=plan)['status']=='unknown'
    with pytest.raises(ServiceError,match='expected_request_changed') as mismatch:
        renderer.render('unknown',source(),doc,scene_space=plan,expected_request_sha256='0'*64)
    assert getattr(mismatch.value,'stop_proven',False) is False
    assert renderer.lookup('unknown')['stop_proven'] is False


def test_direct_learning_render_retains_frozen_host_and_image_identity(renderer,plan):
    cap=render_capability(admitted_engines=['canvas'])
    document=dict(constraints=dict(width=1280,height=720,
        runtime_identity=dict(image_digest=renderer.owner.image_digest),
        scene_space_learning=dict(schema='video.scene-space-learning-offer.v1',runtime=deepcopy(cap))),assets={})
    renderer.validate_request('current',source(),document,scene_space=plan)
    document['constraints']['scene_space_learning']['runtime']['host_sha256']='0'*64
    with pytest.raises(ServiceError,match='frozen_runtime_changed'):
        renderer.validate_request('old-host',source(),document,scene_space=plan)
    document['constraints']['scene_space_learning']['runtime']=cap
    document['constraints']['runtime_identity']['image_digest']='sha256:'+'b'*64
    with pytest.raises(ServiceError,match='frozen_image_changed'):
        renderer.validate_request('old-image',source(),document,scene_space=plan)
    assert not renderer.root.exists()


def test_effect_marker_binds_the_final_selected_host_and_compositor(renderer,plan):
    descriptor=render_capability(admitted_engines=['canvas'])
    document=dict(constraints=dict(width=1280,height=720,scene_space_capability=descriptor),assets={})
    renderer.validate_request('current-effect',source(),document,scene_space=plan)
    document['constraints']['scene_space_capability']['runtime_sha256']='0'*64
    with pytest.raises(ServiceError,match='frozen_runtime_changed'):
        renderer.validate_request('changed-effect',source(),document,scene_space=plan)
    assert not renderer.root.exists()


def test_frozen_engine_guard_and_pure_deadline_are_checked_before_claim(renderer,plan):
    document=dict(constraints=dict(width=1280,height=720,
        runtime_identity=dict(image_digest=renderer.owner.image_digest,engine_runtime_sha256='0'*64)),assets={})
    with pytest.raises(ServiceError,match='frozen_engine_runtime_changed'):
        renderer.validate_request('guard',source(),document,scene_space=plan)
    del document['constraints']['runtime_identity']
    with pytest.raises(ServiceError,match='deadline_insufficient'):
        renderer.validate_request('deadline',source(),document,scene_space=plan,deadline_at=1)
    assert not renderer.root.exists()
    with pytest.raises(ServiceError,match='deadline_insufficient') as error:
        renderer.render('deadline',source(),document,scene_space=plan,deadline_at=1)
    assert error.value.not_dispatched and error.value.stop_proven
    assert renderer.lookup('deadline') is None


def test_orphaned_directory_cannot_be_reclassified_as_not_created(renderer,plan):
    with renderer.owner._connect(create=True) as db:db.commit()
    directory=renderer.owner._directory('orphan');directory.mkdir(parents=True)
    (directory/'unknown-state.txt').write_text('Earlier execution evidence unavailable',encoding='utf-8')
    with pytest.raises(ServiceError,match='orphan_directory') as error:
        renderer.render('orphan',source(),dict(constraints=dict(width=1280,height=720),assets={}),scene_space=plan)
    assert getattr(error.value,'stop_proven',False) is False
    assert not (directory/'render-outcome.json').exists()


def test_disabled_renderer_does_not_admit_new_scene_work(renderer,plan):
    renderer.owner.enabled=False
    assert renderer.capabilities()['admitted'] is False
    with pytest.raises(ServiceError,match='renderer_disabled'):
        renderer.validate_request('disabled',source(),dict(constraints=dict(width=1280,height=720),assets={}),scene_space=plan)
    assert not renderer.root.exists()


def test_authoring_offer_explains_real_ink_cache_coordinate_contract(renderer):
    api=renderer.authoring_constraints()['drawing_library_api']
    assert 'object.bounds.x=0' in api and 'object.bounds.y=0' in api
    assert 'object.transform' in api and 'drawImage' in api
    assert 'clear:false' in api and 'clear:true' in api
    assert 'resets its internal context transform' in api
    assert 'Each seek must derive state' not in api


def test_offered_but_unused_ink_is_not_added_to_new_render_request(renderer,plan):
    from video_workbench.ink_landscape_library import descriptor
    library=descriptor()
    document=dict(constraints=dict(width=1280,height=720,drawing_libraries={'ink-landscape':library}),
        assets=library['assets'])
    binding=renderer._binding('unused',source(),document,plan)
    assert binding['request']['assets_sha256']=={}
    assert 'drawing_libraries' not in binding['request']
    assert binding['document']['constraints']['drawing_libraries']=={'ink-landscape':library}


def test_native_browser_camera_cache_mask_and_current_ink_pin(renderer,plan,tmp_path):
    """Opt-in real browser test; no mock pixels, container or model claims."""
    required=['SCENE_SPACE_BROWSER','SCENE_SPACE_PLAYWRIGHT_PACKAGE','SCENE_SPACE_HYPERFRAMES_DIST']
    if not all(os.environ.get(name) for name in required):
        pytest.skip('Set SCENE_SPACE_BROWSER, SCENE_SPACE_PLAYWRIGHT_PACKAGE, SCENE_SPACE_HYPERFRAMES_DIST for native acceptance')
    node=shutil.which('node');ffmpeg=shutil.which('ffmpeg');ffprobe=shutil.which('ffprobe')
    if not all((node,ffmpeg,ffprobe)):pytest.skip('Native Node/FFmpeg required')
    from PIL import Image
    from video_workbench.ink_landscape_library import descriptor,ROOT as INK_ROOT
    library=descriptor();cases=[]
    names=['video','still','mask','ink','ink-paint']
    font_path=Path(os.environ['SCENE_SPACE_TEST_FONT']) if os.environ.get('SCENE_SPACE_TEST_FONT') else None
    if font_path is not None:names.append('font')
    for name in names:
        value=deepcopy(plan);art=source();assets={};asset_paths={};bounds=dict(width=1280,height=720);frame_range=None
        if name=='video':bounds['duration_seconds']=4
        if name=='mask':value['worlds'][0]['layers'][-1]['mask_region_id']='bank-support'
        if name=='ink':
            art['files']['scene.html']=art['files']['scene.html'].replace('<script src=','<script type="module" src=')
            art['files']['scene.js']='import * as ink from "./assets/ink-landscape.mjs";\n'+art['files']['scene.js'].replace(
                "ctx.fillStyle=","ink.createRandom(31)();ctx.fillStyle=")
            art['source_sha256']=source_sha256({k:art[k] for k in ('engine','strategy','files')})
            bounds['drawing_libraries']={'ink-landscape':library};assets=library['assets']
            asset_paths={asset:str(INK_ROOT/asset) for asset in assets}
        if name=='ink-paint':
            world=value['worlds'][0]
            world['objects']=[obj for obj in world['objects'] if obj['id']=='near-tree']
            world['relations']=[]
            frame_range=dict(start_frame=95,end_frame=96)
            art['files']['scene.html']=art['files']['scene.html'].replace('<script src=','<script type="module" src=')
            art['files']['scene.js']='''import * as ink from "./assets/ink-landscape.mjs";
window.__sceneSpaceArtwork={paintObject(ctx,object,world){
 const b=object.bounds,before=ctx.getTransform();
 if(b.x!==0||b.y!==0||!(before.a>1))throw Error('ink_fixture_requires_zero_origin_and_overscan');
 const document=ink.createStrokeGroupDocument([{id:'native-two-strokes',role:'structure',strokes:[
  {id:'ridge',points:[{x:b.width*.2,y:b.height*.35},{x:b.width*.5,y:b.height*.4},{x:b.width*.8,y:b.height*.3}],options:{width:12}},
  {id:'branch',points:[{x:b.width*.3,y:b.height*.5},{x:b.width*.5,y:b.height*.7}],options:{width:9}}
 ]}],{width:b.width,height:b.height,seed:world.seed,ink:'#151515',dryness:.4,opacity:.95});
 const report=ink.renderCanvas(ctx,document.result,{paperTexture:0,clear:false});
 const after=ctx.getTransform();
 if(report.strokeCount!==2||['a','b','c','d','e','f'].some(k=>before[k]!==after[k]))throw Error('ink_render_or_context_restore_failed');
}};'''
            art['source_sha256']=source_sha256({k:art[k] for k in ('engine','strategy','files')})
            bounds['drawing_libraries']={'ink-landscape':library};assets=library['assets']
            asset_paths={asset:str(INK_ROOT/asset) for asset in assets}
        if name=='font':
            assets={'fixture-font.ttf':hashlib.sha256(font_path.read_bytes()).hexdigest()}
            asset_paths={'fixture-font.ttf':str(font_path)}
            renderer.runtime_renderer.capabilities=lambda:dict(allowed_engines=['canvas'],assets=assets)
            art['files']['scene.html']=art['files']['scene.html'].replace('</style>',
                '@font-face{font-family:FixtureFont;src:url("./assets/fixture-font.ttf")}</style>')
            art['files']['scene.js']="window.__sceneSpaceArtwork={paintObject(ctx,object){if([...document.fonts].some(f=>f.status!=='loaded'))throw Error('font_not_loaded_before_cache');ctx.font='24px FixtureFont';ctx.fillStyle='#000';ctx.fillText('山水',object.bounds.x+10,object.bounds.y+32);}};"
            art['source_sha256']=source_sha256({k:art[k] for k in ('engine','strategy','files')})
        binding=renderer.validate_request(name,art,dict(constraints=bounds,assets=assets),scene_space=value,frame_range=frame_range)
        path=tmp_path/(name+'.json');path.write_bytes(canonical_bytes(binding))
        cases.append(dict(name=name,binding=str(path),output=str(tmp_path/name),assets=asset_paths))
    config=dict(playwright_package=os.environ[required[1]],browser=os.environ[required[0]],
        hyperframes_dist=os.environ[required[2]],host=str(HOST),scene_runtime=str(RUNTIME),
        engine_runtime=str(ROOT/'video_workbench/motion_renderer/engine_runtime.mjs'),
        project_runtime=str(ROOT/'video_workbench/motion_renderer/hyperframes_project.mjs'),
        ffmpeg=ffmpeg,ffprobe=ffprobe,cases=cases,output=str(tmp_path/'native-summary.json'))
    config_path=tmp_path/'probe-config.json';config_path.write_bytes(canonical_bytes(config))
    subprocess.run([node,str(Path(__file__).with_name('scene_space_browser_probe.mjs')),str(config_path)],check=True,timeout=120)
    summary=json.loads((tmp_path/'native-summary.json').read_text(encoding='utf-8'))
    results={row['name']:row for row in summary['results']}
    for value in results.values():
        assert value['checks']['repeat_passed'] and value['checks']['reverse_passed'] and value['checks']['reload_passed']
        assert value['scene_space_observation']['objects_painted']==(1 if value['name']=='ink-paint' else 5)
        assert value['engine_observation']['engine']=='canvas'
    video=results['video']
    assert video['captured_frames']==96 and video['scene_space_observation']['cache_hits']>96*5
    assert len({row['sha256'] for row in video['checks']['samples']})>1
    stream=next(s for s in video['media_probe']['streams'] if s['codec_type']=='video')
    assert stream['codec_name']=='h264' and int(stream['nb_read_frames'])==96
    assert stream['avg_frame_rate']=='24/1'
    with Image.open(tmp_path/'still/output.png') as still,Image.open(tmp_path/'mask/output.png') as mask:
        assert still.convert('RGB').getpixel((200,300))==(72,44,30)
        assert mask.convert('RGB').getpixel((200,300))==(91,131,173)
    execution=results['ink']['drawing_library_observation']['api_execution']
    assert execution['module_sha256']==library['assets']['ink-landscape.mjs']
    assert any('createRandom' in row['function_name'] and row['call_count']>0 for row in execution['functions'])
    painted=results['ink-paint']
    functions=painted['drawing_library_observation']['api_execution']['functions']
    assert any(row['function_name']=='createStrokeGroupDocument' and row['call_count']>0 for row in functions)
    assert any(row['function_name']=='renderCanvas' and row['call_count']>0 for row in functions)
    with Image.open(tmp_path/'ink-paint/output.png') as image:
        rgb=image.convert('RGB')
        dark=[(x,y) for y in range(rgb.height) for x in range(rgb.width) if max(rgb.getpixel((x,y)))<220]
    assert len(dark)>100  # Actual brush pixels, beyond successful document creation/API calls.
    xs,ys=zip(*dark);actual=(min(xs),min(ys),max(xs)+1,max(ys)+1)
    projected=compile_evaluator(json.loads((tmp_path/'ink-paint.json').read_bytes())['scene_space']['frozen']).frame(95)
    box=next(obj['screen_bounds'] for obj in projected['objects'] if obj['id']=='near-tree')
    assert box['x']-1<=actual[0]<actual[2]<=box['x']+box['width']+1
    assert box['y']-1<=actual[1]<actual[3]<=box['y']+box['height']+1
    assert abs(actual[0]-(box['x']+.2*box['width']))<20
    assert abs(actual[2]-(box['x']+.8*box['width']))<20
    assert abs(actual[1]-(box['y']+.3*box['height']))<25
    assert abs(actual[3]-(box['y']+.7*box['height']))<25
    (tmp_path/'ink-paint/pixel-check.json').write_bytes(canonical_bytes(dict(
        schema='video.scene-space-ink-pixel-check.v1',dark_pixel_count=len(dark),pixel_bounds=list(actual),
        declared_screen_bounds=box,scope='two_actual_brush_strokes_zero_origin_object_cache_not_artistic_acceptance',
        transform_compatibility='renderCanvas resets its internal transform; zero-origin document matches cache dimensions and finally restores the caller context')))
