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

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles.video_animation_create import source_sha256
from video_workbench.scene_space_plan import compile_evaluator, freeze_plan
from video_workbench.scene_space_renderer import (
    HOST,RUNTIME,SURFACE_RUNTIME,SURFACE_BUDGET,SURFACE_WORK_BYTES,SURFACE_RETAINED_BYTES,MAX_SURFACE_FIELDS,
    GEOMETRY_RUNTIME,GEOMETRY_BUDGET,GEOMETRY_WORK_BYTES,GEOMETRY_RETAINED_BYTES,MAX_GEOMETRY_HANDLES,
    SceneSpaceRenderer,prepare_scene_render,render_capability,
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


def coverage_plan():
    from video_workbench.scene_space_plan import automatic_plan,default_budget
    world=dict(schema='video.scene-world.v1',id='coverage-world',revision=1,seed=19,style='flat-color-test',
        space=dict(projection='planar',bounds=dict(x=0,y=0,width=1280,height=720)),
        layers=[dict(id='far',order=0,parallax=1),dict(id='near',order=1,parallax=1)],
        objects=[dict(id='background',kind='background',layer_id='far',parent_id=None,
            bounds=dict(x=0,y=0,width=1280,height=720),transform=dict(x=0,y=0,scale_x=1,scale_y=1,rotation=0),
            depth=dict(kind='ordinal',value=0),anchors={}),
            dict(id='pale-mountain',kind='mountain',layer_id='near',parent_id=None,
            bounds=dict(x=0,y=0,width=600,height=500),transform=dict(x=200,y=100,scale_x=1,scale_y=1,rotation=0),
            depth=dict(kind='ordinal',value=1),anchors={})],
        layout=dict(grid=None,points=[],regions=[]),
        relations=[dict(id='mountain-front',kind='occlusion',front_id='pale-mountain',back_id='background')])
    return automatic_plan([world],id='coverage-plan',viewport=dict(width=1280,height=720),duration_frames=1,
        budget=default_budget(),intents=[dict(world_id=world['id'],weight=1,view='hold',focus=dict(x=640,y=360))])


def coverage_source(mode='solid'):
    value=source()
    coverage=("ctx.fillStyle='#000';" if mode!='gradient' else
        "const gradient=ctx.createLinearGradient(0,0,600,0);gradient.addColorStop(0,'rgba(0,0,0,0)');gradient.addColorStop(1,'#000');ctx.fillStyle=gradient;")
    coverage+="ctx.beginPath();ctx.ellipse(300,250,240,220,0,0,Math.PI*2);ctx.fill();return {substrate:[240,235,220]};"
    if mode=='async':coverage="return Promise.resolve({substrate:[240,235,220]});"
    if mode=='options':coverage="return {substrate:[240,235,220],opacity:.5};"
    if mode=='resize':coverage="ctx.canvas.width=12;return {substrate:[240,235,220]};"
    value['files']['scene.js']="""window.__sceneSpaceArtwork={
paintObject(ctx,object,world,tools){const b=object.bounds;
 if(object.id==='background'){ctx.fillStyle='rgb(30,90,150)';ctx.fillRect(0,0,b.width,b.height);ctx.fillStyle='rgb(170,40,80)';ctx.fillRect(0,280,1280,40);return;}
 if(!Object.isFrozen(tools)||!Object.isFrozen(tools.surface))throw Error('surface_tools_not_frozen');
 const field=tools.surface.createSurfaceField({bounds:b,outline:[[0,0],[600,0],[600,500],[0,500]],ridges:[],valleys:[],grid:{columns:3,rows:3}});
 const sample=tools.surface.sampleSurface(field,300,200);if(!Number.isFinite(sample.height))throw Error('surface_sample_missing');
 ctx.fillStyle='rgba(20,25,30,0.08)';ctx.fillRect(0,0,b.width,b.height);
},paintCoverage(ctx,object){if(object.id!=='pale-mountain')return null;
"""+coverage+"}};"
    value['source_sha256']=source_sha256({key:value[key] for key in ('engine','strategy','files')})
    return value


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
    assert render['surface_runtime_sha256']==hashlib.sha256(SURFACE_RUNTIME.read_bytes()).hexdigest()
    assert render['artwork_api_revision']==3
    assert render['geometry_runtime_sha256']==hashlib.sha256(GEOMETRY_RUNTIME.read_bytes()).hexdigest()
    assert render['geometry_budget_sha256']==hashlib.sha256(GEOMETRY_BUDGET.read_bytes()).hexdigest()
    assert render['coverage']['max_mask_pixels']==max(row['pixels'] for row in render['object_caches'])
    assert render['native_context_budget']==len(render['object_caches'])+3
    assert render['surface_budget_sha256']==hashlib.sha256(SURFACE_BUDGET.read_bytes()).hexdigest()
    assert render['surface_retained_bytes']==SURFACE_RETAINED_BYTES
    assert render['geometry_retained_bytes']==GEOMETRY_RETAINED_BYTES
    assert render['geometry_work_bytes']==GEOMETRY_WORK_BYTES
    assert render['temporary_bytes']==(4*(render['scratch']['pixels']+1280*720+render['coverage']['max_mask_pixels'])
        +SURFACE_WORK_BYTES+SURFACE_RETAINED_BYTES+GEOMETRY_WORK_BYTES+GEOMETRY_RETAINED_BYTES)
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


def test_coverage_and_surface_work_are_preflighted_before_source_execution(monkeypatch):
    plan=coverage_plan()
    prepared=prepare_scene_render(plan,source_sha256=source()['source_sha256'])
    plan['budget']['render']['max_temporary_bytes']=prepared['temporary_bytes']-1
    freeze_plan(plan)  # The former color-only layer estimate still fits.
    with pytest.raises(ServiceError,match='compositor_temporary_budget'):
        prepare_scene_render(plan,source_sha256=source()['source_sha256'])
    plan=coverage_plan()
    monkeypatch.setattr('video_workbench.scene_space_renderer.MAX_CONTEXTS',4)
    with pytest.raises(ServiceError,match='context_budget'):
        prepare_scene_render(plan,source_sha256=source()['source_sha256'])


def test_surface_dependency_is_pinned_in_the_request_and_frozen_capability(renderer,plan):
    cap=render_capability()
    assert cap['surface_runtime_sha256']==hashlib.sha256(SURFACE_RUNTIME.read_bytes()).hexdigest()
    assert cap['artwork_api_revision']==3
    document=dict(constraints=dict(width=1280,height=720,scene_space_capability=deepcopy(cap)),assets={})
    binding=renderer.validate_request('surface-pin',source(),document,scene_space=plan)
    assert binding['request']['surface_runtime_sha256']==cap['surface_runtime_sha256']
    document['constraints']['scene_space_capability']['surface_runtime_sha256']='0'*64
    with pytest.raises(ServiceError,match='frozen_runtime_changed'):
        renderer.validate_request('surface-drift',source(),document,scene_space=plan)


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
            **{k:request[k] for k in ('host_sha256','scene_runtime_sha256','surface_runtime_sha256','surface_budget_sha256',
                'geometry_runtime_sha256','geometry_budget_sha256','artwork_api_revision','sidecar_sha256','image_digest')},
            output_sha256=hashlib.sha256(raw).hexdigest(),output_bytes=len(raw),captured_frames=1,
            measured_render_seconds=.25,sample_frames=[],
            checks=dict(repeat_passed=True,reverse_passed=True,reload_passed=True),
            engine_observation=dict(engine='canvas',revision='animation-engine-runtime-1',contexts=[
                dict(kind='2d',connected=index==0,visible=index==0,width=1280,height=720) for index in range(len(sidecar['object_caches'])+2)]),
            project_runtime=dict(core_sha256=request['hyperframes_core_sha256'],project_sha256=request['project_sha256'],core_seek_count=1),
            scene_space_observation=dict(plan_sha256=sidecar['frozen']['plan_sha256'],recipes_executed=0,
                objects_painted=len(sidecar['object_caches']),runtime_revision='scene-space-compositor-3',
                surface_runtime_sha256=request['surface_runtime_sha256'],surface_budget_sha256=request['surface_budget_sha256'],artwork_api_revision=3,
                geometry_runtime_sha256=request['geometry_runtime_sha256'],geometry_budget_sha256=request['geometry_budget_sha256'],
                geometry_helper_calls={},geometry_resource_usage=dict(meshes_created=0,projections_created=0,handles_reserved=0,
                    numeric_bytes_reserved=0,max_handles=MAX_GEOMETRY_HANDLES,max_total_bytes=GEOMETRY_RETAINED_BYTES),
                coverage_callbacks=0,coverage_objects=0,coverage_contexts=0,coverage_peak_pixels=0,surface_helper_calls={},
                surface_field_usage=dict(fields_created=0,fields_reserved=0,numeric_bytes_reserved=0,
                    max_fields=MAX_SURFACE_FIELDS,max_total_field_bytes=SURFACE_RETAINED_BYTES)))
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


@pytest.mark.parametrize('name,error',[
    ('scene_space_surface.mjs','surface_runtime_changed'),
    ('scene_space_surface_budget.mjs','surface_budget_changed'),
    ('scene_space_geometry.mjs','geometry_runtime_changed'),
    ('scene_space_geometry_budget.mjs','geometry_budget_changed'),
])
def test_changed_surface_helper_is_rejected_even_after_a_successful_render(renderer,plan,monkeypatch,name,error):
    calls=[];monkeypatch.setattr(execution,'execute_method_render',synthetic_execution(calls))
    renderer.render('helper-tamper',source(),dict(constraints=dict(width=1280,height=720),assets={}),scene_space=plan)
    (calls[0]/'input'/name).write_text('changed',encoding='utf-8')
    with pytest.raises(ServiceError,match=error):
        renderer.lookup('helper-tamper')


def test_direct_author_offer_pins_its_surface_helper_before_claim(renderer,plan):
    constraints=renderer.authoring_constraints()
    constraints.update(width=1280,height=720)
    constraints['runtime_identity']['surface_runtime_sha256']='0'*64
    with pytest.raises(ServiceError,match='frozen_surface_runtime_changed'):
        renderer.validate_request('direct-helper-drift',source(),dict(constraints=constraints,assets=constraints['assets']),scene_space=plan)
    assert renderer.lookup('direct-helper-drift') is None


def test_geometry_dependencies_and_budget_are_bound_before_claim(renderer,plan):
    cap=render_capability()
    assert cap['artwork_extensions']['geometry']=='tools.geometry'
    assert cap['geometry_runtime_sha256']==hashlib.sha256(GEOMETRY_RUNTIME.read_bytes()).hexdigest()
    assert cap['geometry_budget_sha256']==hashlib.sha256(GEOMETRY_BUDGET.read_bytes()).hexdigest()
    assert cap['limits']['max_geometry_retained_bytes']==GEOMETRY_RETAINED_BYTES
    assert cap['limits']['max_geometry_work_bytes']==GEOMETRY_WORK_BYTES
    for key in ['geometry_runtime_sha256','geometry_budget_sha256']:
        changed=deepcopy(cap);changed[key]='0'*64
        document=dict(constraints=dict(width=1280,height=720,scene_space_capability=changed),assets={})
        with pytest.raises(ServiceError,match='frozen_runtime_changed'):
            renderer.validate_request('geometry-cap-'+key,source(),document,scene_space=plan)
        constraints=renderer.authoring_constraints();constraints.update(width=1280,height=720)
        constraints['runtime_identity'][key]='0'*64
        with pytest.raises(ServiceError,match='frozen_geometry_runtime_changed'):
            renderer.validate_request('geometry-offer-'+key,source(),dict(constraints=constraints,assets=constraints['assets']),scene_space=plan)
    assert not renderer.root.exists()


@pytest.mark.parametrize('change,error',[
    ('receipt','geometry_receipt_changed'),('usage','geometry_consumption_unproven'),
    ('negative-meshes','geometry_consumption_unproven'),
])
def test_geometry_receipt_and_resource_usage_are_verified(renderer,plan,monkeypatch,change,error):
    calls=[];execute=synthetic_execution(calls)
    def corrupt(directory):
        execute(directory)
        p=directory/'work/render-receipt.json';receipt=json.loads(p.read_bytes())
        if change=='receipt':receipt['geometry_budget_sha256']='0'*64
        elif change=='negative-meshes':
            receipt['scene_space_observation']['geometry_resource_usage'].update(meshes_created=-1,projections_created=1)
        else:receipt['scene_space_observation']['geometry_resource_usage']['handles_reserved']=MAX_GEOMETRY_HANDLES+1
        p.write_bytes(canonical_bytes(receipt))
    monkeypatch.setattr(execution,'execute_method_render',corrupt)
    with pytest.raises(ServiceError,match=error):
        renderer.render('geometry-proof-'+change,source(),dict(constraints=dict(width=1280,height=720),assets={}),scene_space=plan)
    assert len(calls)==1


@pytest.mark.parametrize('revision',[1,2])
def test_historical_v1_v2_formats_remain_readable_without_geometry_rebinding(renderer,plan,monkeypatch,revision):
    """Inert historical-format fixture; no claim of running old host pixels."""
    calls=[];execute=synthetic_execution(calls)
    def legacy_format(directory):
        execute(directory)
        binding=json.loads((directory/'binding.json').read_bytes());request=binding['request'];sidecar=binding['scene_space']
        geometry_keys=['geometry_runtime_sha256','geometry_budget_sha256','geometry_work_bytes','geometry_retained_bytes','geometry_max_handles']
        for obj in [request,sidecar]:
            for key in geometry_keys:obj.pop(key,None)
            obj['artwork_api_revision']=revision
        sidecar['temporary_bytes']-=GEOMETRY_RETAINED_BYTES+GEOMETRY_WORK_BYTES
        receipt_path=directory/'work/render-receipt.json';receipt=json.loads(receipt_path.read_bytes());obs=receipt['scene_space_observation']
        for obj in [receipt,obs]:
            obj.pop('geometry_runtime_sha256',None);obj.pop('geometry_budget_sha256',None);obj['artwork_api_revision']=revision
        obs.pop('geometry_helper_calls');obs.pop('geometry_resource_usage');obs['runtime_revision']='scene-space-compositor-'+str(revision)
        for name in ['scene_space_geometry.mjs','scene_space_geometry_budget.mjs']:(directory/'input'/name).unlink()
        if revision==1:
            surface_keys=['surface_runtime_sha256','surface_budget_sha256','artwork_api_revision']
            for obj in [request,sidecar,receipt,obs]:
                for key in surface_keys:obj.pop(key,None)
            for key in ['surface_work_bytes','surface_retained_bytes','surface_max_fields','coverage','native_context_budget']:sidecar.pop(key,None)
            sidecar['temporary_bytes']-=SURFACE_WORK_BYTES+SURFACE_RETAINED_BYTES
            for key in ['surface_helper_calls','surface_field_usage','coverage_callbacks','coverage_objects','coverage_contexts','coverage_peak_pixels']:obs.pop(key,None)
            for name in ['scene_space_surface.mjs','scene_space_surface_budget.mjs']:(directory/'input'/name).unlink()
        sidecar.pop('sha256');sidecar['sha256']=digest(sidecar)
        request['sidecar_sha256']=hashlib.sha256(canonical_bytes(sidecar)).hexdigest()
        request.pop('identity');request['identity']=digest(request);binding['request_sha256']=digest(request)
        receipt.update(request_identity=request['identity'],request_sha256=binding['request_sha256'],sidecar_sha256=request['sidecar_sha256'])
        outcome_path=directory/'render-outcome.json';outcome=json.loads(outcome_path.read_bytes())
        outcome.update(request_identity=request['identity'],request_sha256=binding['request_sha256'])
        (directory/'binding.json').write_bytes(canonical_bytes(binding));(directory/'input/request.json').write_bytes(canonical_bytes(request))
        (directory/'input/scene-space.json').write_bytes(canonical_bytes(sidecar));receipt_path.write_bytes(canonical_bytes(receipt));outcome_path.write_bytes(canonical_bytes(outcome))
        with renderer.owner._connect(write=True) as db:
            db.execute('UPDATE '+execution._TABLE+' SET binding=?,binding_sha256=? WHERE operation_id=?',
                (canonical_bytes(binding),digest(binding),binding['operation_id']));db.commit()
    monkeypatch.setattr(execution,'execute_method_render',legacy_format)
    operation='old-format-'+str(revision)
    result=renderer.render(operation,source(),dict(constraints=dict(width=1280,height=720),assets={}),scene_space=plan)
    assert result['technical_passed'] and result['stop_proven']
    assert renderer.lookup(operation)==result and len(calls)==1
    assert 'geometry_resource_usage' not in result['runtime']['scene_space']


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


def test_native_browser_independent_coverage_and_surface_tools(renderer,tmp_path):
    """Actual pixels distinguish coverage from pigment, shape, masks and opacity."""
    required=['SCENE_SPACE_BROWSER','SCENE_SPACE_PLAYWRIGHT_PACKAGE','SCENE_SPACE_HYPERFRAMES_DIST']
    if not all(os.environ.get(name) for name in required):pytest.skip('Native browser acceptance not configured')
    node=shutil.which('node');ffmpeg=shutil.which('ffmpeg');ffprobe=shutil.which('ffprobe')
    if not all((node,ffmpeg,ffprobe)):pytest.skip('Native Node/FFmpeg required')
    from PIL import Image
    cases=[]
    for name in ['legacy','solid','gradient','opacity-mask','form-wash','async','options','resize']:
        value=coverage_plan();art=coverage_source(name)
        if name=='legacy':
            art['files']['scene.js']=art['files']['scene.js'].split(',paintCoverage')[0]+'};'
            art['source_sha256']=source_sha256({key:art[key] for key in ('engine','strategy','files')})
        if name=='opacity-mask':
            layer=value['worlds'][0]['layers'][-1];layer['opacity']=.5;layer['mask_region_id']='top'
            value['worlds'][0]['layout']['regions']=[dict(id='top',kind='rect',bounds=dict(x=0,y=0,width=1280,height=330))]
            value['shots'][0]['local_animation']=[dict(id='object-alpha',target_id='pale-mountain',property='opacity',
                keyframes=[dict(frame=0,value=.5,easing='linear')])]
        if name=='form-wash':
            art['files']['scene.js']=art['files']['scene.js'].replace(
                'ridges:[],valleys:[],grid:{columns:3,rows:3}',
                "ridges:[{id:'ridge',points:[[80,420,5],[250,80,160],[400,360,25]],width:100}],"
                "valleys:[{id:'valley',points:[[400,50,20],[450,280,60],[560,450,10]],width:90}],grid:{columns:12,rows:10}").replace(
                "ctx.fillStyle='rgba(20,25,30,0.08)';ctx.fillRect(0,0,b.width,b.height);",
                "tools.surface.paintSurfaceWash(ctx,field,{paper:[240,235,220],ink:[20,25,30],light:[-.8,-.4,.7],inkRange:[.08,.8]});")
            art['source_sha256']=source_sha256({key:art[key] for key in ('engine','strategy','files')})
        binding=renderer.validate_request('coverage-'+name,art,dict(constraints=dict(width=1280,height=720),assets={}),scene_space=value)
        path=tmp_path/(name+'.json');path.write_bytes(canonical_bytes(binding))
        case=dict(name=name,binding=str(path),output=str(tmp_path/name),assets={})
        if name in {'async','options','resize'}:
            case['expect_error']={'async':'scene_space_async_coverage_not_admitted',
                'options':'scene_space_coverage_options_invalid','resize':'scene_space_coverage_resized_cache'}[name]
        cases.append(case)
    config=dict(playwright_package=os.environ[required[1]],browser=os.environ[required[0]],
        hyperframes_dist=os.environ[required[2]],host=str(HOST),scene_runtime=str(RUNTIME),surface_runtime=str(SURFACE_RUNTIME),
        engine_runtime=str(ROOT/'video_workbench/motion_renderer/engine_runtime.mjs'),
        project_runtime=str(ROOT/'video_workbench/motion_renderer/hyperframes_project.mjs'),
        ffmpeg=ffmpeg,ffprobe=ffprobe,cases=cases,output=str(tmp_path/'coverage-summary.json'))
    path=tmp_path/'coverage-config.json';path.write_bytes(canonical_bytes(config))
    subprocess.run([node,str(Path(__file__).with_name('scene_space_browser_probe.mjs')),str(path)],check=True,timeout=120)
    results={r['name']:r for r in json.loads((tmp_path/'coverage-summary.json').read_bytes())['results']}
    for name in ['legacy','solid','gradient','opacity-mask','form-wash']:
        value=results[name];observation=value['scene_space_observation']
        assert value['checks']['repeat_passed'] and value['checks']['reverse_passed'] and value['checks']['reload_passed']
        assert observation['objects_painted']==2 and observation['cache_hits']==8
        assert observation['coverage_callbacks']==(0 if name=='legacy' else 2)
        assert observation['coverage_objects']==(0 if name=='legacy' else 1)
        assert observation['coverage_contexts']==(0 if name=='legacy' else 1)
        assert observation['coverage_peak_pixels']==(0 if name=='legacy' else 1280*720)
        assert observation['surface_helper_calls']==dict(createSurfaceField=1,sampleSurface=1,
            **(dict(paintSurfaceWash=1) if name=='form-wash' else {}))
        assert observation['surface_runtime_sha256']==hashlib.sha256(SURFACE_RUNTIME.read_bytes()).hexdigest()
        assert observation['surface_budget_sha256']==hashlib.sha256(SURFACE_BUDGET.read_bytes()).hexdigest()
        assert observation['surface_field_usage']['fields_created']==1
        assert observation['surface_field_usage']['fields_reserved']==1
        assert 0<observation['surface_field_usage']['numeric_bytes_reserved']<=SURFACE_RETAINED_BYTES
    pixels={}
    for name in ['legacy','solid','gradient','opacity-mask']:
        with Image.open(tmp_path/name/'output.png') as image:
            rgb=image.convert('RGB')
            pixels[name]=dict(interior=rgb.getpixel((500,300)),outside=rgb.getpixel((210,110)),clipped=rgb.getpixel((500,350)))
    far=(170,40,80)
    # The same faint pigment has a nearly paper-white opaque support now. A
    # background stripe cannot pass through its interior, and the ellipse's
    # corner leaves the background untouched rather than filling a rectangle.
    assert pixels['solid']['interior']==pytest.approx((222,218,205),abs=1)
    assert pixels['solid']['outside']==(30,90,150)
    assert pixels['legacy']['interior']==pytest.approx(tuple(round(.08*x+.92*y) for x,y in zip((20,25,30),far)),abs=1)
    assert pixels['legacy']['outside']!=(30,90,150)
    assert pixels['gradient']['interior']==pytest.approx(tuple(round((x+y)/2) for x,y in zip(pixels['solid']['interior'],far)),abs=2)
    assert pixels['opacity-mask']['interior']==pytest.approx(tuple(round(.25*x+.75*y) for x,y in zip(pixels['solid']['interior'],far)),abs=2)
    assert pixels['opacity-mask']['clipped']==(30,90,150)
    with Image.open(tmp_path/'form-wash/output.png') as image:
        rgb=image.convert('RGB')
        sample=[rgb.getpixel((x,300))[0] for x in range(310,691)]
        assert max(sample)-min(sample)>20  # Real shape-relative form values, not a constant grey fill.
        jumps=[]
        for index in range(2,10):
            x=round(200+index*600/11)
            jumps.append(max(abs(a-b) for a,b in zip(rgb.getpixel((x-1,300)),rgb.getpixel((x+1,300)))))
        for index in range(2,8):
            y=round(100+index*500/9)
            jumps.append(max(abs(a-b) for a,b in zip(rgb.getpixel((500,y-1)),rgb.getpixel((500,y+1)))))
        assert max(jumps)<12  # No visible rectangular cell boundary in the wash.
        pixels['form-wash']=dict(grid_boundary_max_rgb_jump=max(jumps),surface_value_range=max(sample)-min(sample))
    for name in ['async','options','resize']:
        assert results[name]['captured_frames']==0 and results[name]['expected_rejection']
        assert not (tmp_path/name/'output.png').exists()
    (tmp_path/'coverage-pixel-check.json').write_bytes(canonical_bytes(dict(
        scope='actual_coverage_shape_gradient_local_mask_and_single_application_of_object_layer_alpha',pixels=pixels,
        source_reference='same_faint_pigment_and_background_for_each_case',artistic_acceptance=False)))


def test_native_browser_shared_mesh_depth_and_silhouette(renderer,tmp_path):
    """Real pixels use one mesh's nearest face, not component draw order."""
    required=['SCENE_SPACE_BROWSER','SCENE_SPACE_PLAYWRIGHT_PACKAGE','SCENE_SPACE_HYPERFRAMES_DIST']
    if not all(os.environ.get(name) for name in required):pytest.skip('Native browser acceptance not configured')
    node=shutil.which('node');ffmpeg=shutil.which('ffmpeg');ffprobe=shutil.which('ffprobe')
    if not all((node,ffmpeg,ffprobe)):pytest.skip('Native Node/FFmpeg required')
    from PIL import Image
    art=source()
    art['files']['scene.js']='''let projection;
window.__sceneSpaceArtwork={paintObject(ctx,object,world,tools){
 const b=object.bounds;
 if(object.id==='background'){ctx.fillStyle='rgb(30,90,150)';ctx.fillRect(0,0,b.width,b.height);return;}
 if(!Object.isFrozen(tools)||!Object.isFrozen(tools.geometry))throw Error('geometry_tools_not_frozen');
 const g=tools.geometry,mesh=g.createMesh({
  vertices:[[-2,-2,0],[2,-2,0],[0,2,0],[-.8,-.8,2],[.8,-.8,2],[0,.8,2]],
  triangles:[[0,1,2],[3,4,5]]});
 projection=g.projectMesh(mesh,{width:150,height:125,
  camera:{position:[0,0,10],target:[0,0,0],viewHeight:5}});
 const near=g.projectPoint(projection,[0,0,2],{bounds:b}),far=g.projectPoint(projection,[0,0,0],{bounds:b});
 if(!near.visible||far.visible||near.triangle!==1||far.triangle!==1)throw Error('nearest_face_visibility_failed');
 if(world.style==='fractional-coverage-test'){ctx.fillStyle='rgb(180,40,80)';ctx.fillRect(b.x,b.y,b.width,b.height);return;}
 for(let row=0;row<125;row++)for(let col=0;col<150;col++){
  const x=b.x+(col+.5)*4,y=b.y+(row+.5)*4,sample=g.sampleProjection(projection,x,y,{bounds:b});
  if(!sample.covered)continue;
  if(!sample.position.every(Number.isFinite)||!sample.normal.every(Number.isFinite))throw Error('mesh_guide_invalid');
  ctx.fillStyle=sample.triangle===1?'rgb(180,40,80)':'rgb(30,90,180)';ctx.fillRect(x-2,y-2,4,4);
 }
},paintCoverage(ctx,object,world,tools){
 if(object.id!=='pale-mountain')return null;
 const report=tools.geometry.paintCoverage(ctx,projection,{bounds:object.bounds});
 if(report.covered_pixels!==projection.covered_pixels)throw Error('mesh_silhouette_mismatch');
 return {substrate:[240,235,220]};
}};'''
    art['source_sha256']=source_sha256({key:art[key] for key in ('engine','strategy','files')})
    cases=[]
    for name in ['mesh-depth','fractional-coverage']:
        value=coverage_plan()
        if name=='fractional-coverage':
            world=value['worlds'][0];world['style']='fractional-coverage-test'
            world['objects'][-1]['transform'].update(x=200.25,y=30.25,scale_x=1.3,scale_y=1.3)
        binding=renderer.validate_request('shared-'+name,art,dict(constraints=dict(width=1280,height=720),assets={}),scene_space=value)
        path=tmp_path/(name+'.json');path.write_bytes(canonical_bytes(binding))
        cases.append(dict(name=name,binding=str(path),output=str(tmp_path/name),assets={}))
    config=dict(playwright_package=os.environ[required[1]],browser=os.environ[required[0]],
        hyperframes_dist=os.environ[required[2]],host=str(HOST),scene_runtime=str(RUNTIME),
        engine_runtime=str(ROOT/'video_workbench/motion_renderer/engine_runtime.mjs'),
        project_runtime=str(ROOT/'video_workbench/motion_renderer/hyperframes_project.mjs'),
        ffmpeg=ffmpeg,ffprobe=ffprobe,cases=cases,
        output=str(tmp_path/'geometry-summary.json'))
    path=tmp_path/'geometry-config.json';path.write_bytes(canonical_bytes(config))
    subprocess.run([node,str(Path(__file__).with_name('scene_space_browser_probe.mjs')),str(path)],check=True,timeout=120)
    results={row['name']:row for row in json.loads((tmp_path/'geometry-summary.json').read_bytes())['results']}
    for name,result in results.items():
        assert all(result['checks'][key] for key in ['repeat_passed','reverse_passed','reload_passed'])
        observation=result['scene_space_observation']
        assert observation['artwork_api_revision']==3 and observation['runtime_revision']=='scene-space-compositor-3'
        assert observation['geometry_runtime_sha256']==hashlib.sha256(GEOMETRY_RUNTIME.read_bytes()).hexdigest()
        assert observation['geometry_budget_sha256']==hashlib.sha256(GEOMETRY_BUDGET.read_bytes()).hexdigest()
        assert observation['geometry_helper_calls']==dict(createMesh=1,projectMesh=1,projectPoint=2,paintCoverage=1,
            **(dict(sampleProjection=150*125) if name=='mesh-depth' else {}))
        usage=observation['geometry_resource_usage']
        assert usage['meshes_created']==1 and usage['projections_created']==1 and usage['handles_reserved']==2
        assert 0<usage['numeric_bytes_reserved']<=GEOMETRY_RETAINED_BYTES
        assert observation['surface_helper_calls']=={} and observation['surface_field_usage']['fields_created']==0
        assert observation['objects_painted']==2 and observation['coverage_objects']==1
    with Image.open(tmp_path/'mesh-depth/output.png') as image:
        rgb=image.convert('RGB')
        pixels=dict(near=rgb.getpixel((500,350)),far=rgb.getpixel((400,450)),outside=rgb.getpixel((300,350)))
    assert pixels==dict(near=(180,40,80),far=(30,90,180),outside=(30,90,150))
    with Image.open(tmp_path/'fractional-coverage/output.png') as image:
        rgb=image.convert('RGB');column=[rgb.getpixel((590,y)) for y in range(120,591)]
    assert set(column)=={(180,40,80)}  # One union fill has no AA row seams at density 1.3.
    (tmp_path/'geometry-pixel-check.json').write_bytes(canonical_bytes(dict(
        scope='actual_nearest_triangle_depth_and_mesh_silhouette_in_static_canvas_cache',pixels=pixels,
        fractional_coverage=dict(cache_density=1.3,transform_translation=[200.25,30.25],
            interior_column_pixels=len(column),interior_rgb=list(column[0]),uniform_interior=True),
        source_fixture='two_overlapping_disconnected_triangles_in_one_indexed_mesh',
        excludes=['connected_terrain_quality','camera_orbit','artistic_acceptance'])))
