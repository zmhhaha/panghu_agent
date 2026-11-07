"""Spatial contracts; synthetic GPU/DOM fixtures never count as rendered evidence.

When the already-installed pinned Three.js module is available, Node executes
the actual recipe with real Three.js geometry/cameras and an explicitly fake
renderer/DOM. A separate browser capture is required for WebGL visual evidence.
No dependency download, network, model or TTS is performed here.
"""
from copy import deepcopy
import itertools
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_lab_scenarios_spatial import (
    SCENARIOS, build_recipe, expected_observations, plan_for, spatial_state,
)
from video_workbench.animation_lab_scenarios_spatial_review import (
    camera_oracle, part_bounds, projected_oracle, translation_matrix, verify_native_observation,
)
from video_workbench.animation_lab_three import THREE_ASSET_SHA256
from video_workbench.motion import sha256


def synthetic_native(identity='spatial-bolt-assembly', time=10.5):
    """Observation-shaped contract data, not a GPU measurement or screenshot."""
    plan = plan_for(identity)
    expected = spatial_state(plan,time)
    zoom = 1+1.2*expected['assembly_metric'] if identity == 'spatial-bolt-assembly' else 1
    parts,caps = [],[]
    axis = plan['data']['axis']
    normal = [0,0,0]
    normal[axis] = -1
    for index,p in enumerate(plan['data']['parts']):
        position = expected['parts'][p['id']]
        lo,hi = part_bounds(p)
        geometry = dict(type=dict(box='BoxGeometry',cylinder='CylinderGeometry',ring='TorusGeometry',
            plate='ExtrudeGeometry',nut='ExtrudeGeometry')[p['shape']],position_item_size=3,
            position_count=24,index_count=36,bounds=dict(min=lo,max=hi),shape_holes=0,hole_radii=[],parameters={})
        if p['shape'] == 'box':
            geometry['parameters'] = {k:p[k] for k in ('width','height','depth')}
        elif p['shape'] == 'cylinder':
            geometry.update(position_count=p['segments']*6,index_count=p['segments']*12,
                parameters=dict(radiusTop=p['radius'],radiusBottom=p['radius'],height=p['height'],radialSegments=p['segments']))
        elif p['shape'] == 'ring':
            geometry.update(position_count=297,index_count=1536,parameters=dict(radius=p['radius'],tube=p['tube']))
        else:
            geometry.update(position_count=600,index_count=0,shape_holes=1,
                hole_radii=[[[p['hole'],p['hole']]]],parameters=dict(depth=p['height']))
        ndc,pixel = projected_oracle(position,zoom)
        parts.append(dict(id=p['id'],is_mesh=True,visible=True,position=position,rotation=[0,0,0],scale=[1,1,1],
            matrix_world=translation_matrix(position),geometry=geometry,
            material=dict(type='MeshStandardMaterial',side=2,opacity=1,
                clipping_planes=[dict(normal=normal[:],constant=expected['constant'])]),
            projection=dict(ndc=ndc,pixel=pixel),marker=dict(text=str(index+1),font_size=14,
                opacity=(.15 if position[axis] > expected['constant'] else .92) if p['id'] == expected['focus'] else 0,
                bounds=dict(x=pixel[0]-11,y=pixel[1]-11,width=22,height=22))))
        if p['shape'] == 'box':
            half = (p['width'] if axis == 0 else p['depth'])/2
            visible = position[axis]-half+.001 < expected['constant'] < position[axis]+half-.001
            cap_position = position[:]
            cap_position[axis] = expected['constant']-.002
            width = p['depth'] if axis == 0 else p['width']
            caps.append(dict(id=p['id'],visible=visible,position=cap_position,
                matrix_world=([0,0,-1,0,0,1,0,0,1,0,0,0,*cap_position,1] if axis == 0 else translation_matrix(cap_position)),
                geometry=dict(type='PlaneGeometry',position_count=4,index_count=6,parameters=dict(width=width,height=p['height']))))
    camera = camera_oracle(zoom)
    camera.pop('basis')
    camera.update(type='PerspectiveCamera',zoom=zoom,fov=40,aspect=880/500,near=.1,far=100)
    return plan, dict(engine='three',source='actual_three_scene_and_webgl',time=time,
        renderer=dict(is_webgl_renderer=True,local_clipping_enabled=True,width=880,height=500,
            context_lost=False,calls=len(parts)+sum(c['visible'] for c in caps),triangles=1000),
        canvas_rect=dict(x=24,y=100,width=880,height=500),camera=camera,parts=parts,caps=caps,
        pixel_probe=dict(source='webgl_readPixels',sample_count=1100,foreground_samples=320,
                         unique_colors=88,checksum='deadbeef',gl_error=0))


def test_spatial_registry_contains_four_distinct_domains_and_bounded_native_work():
    assert len(SCENARIOS) == len({p['domain'] for p in SCENARIOS.values()}) == 4
    for identity,row in SCENARIOS.items():
        assert row['module'] == 'spatial' and row['engine'] == 'three'
        assert len(row['requirement']['questions']) >= 2 and len(row['requirement']['boundaries']) >= 2
        assert row['duration_seconds'] == 24
        plan = plan_for(identity)
        a,b = [build_recipe(plan,v) for v in ('baseline','candidate')]
        assert a['markup'] == b['markup'] and a['actions'] == b['actions'] and a['script'] != b['script']
        assert len(a['object_ids']) == len(set(a['object_ids']))
        assert '<canvas' in a['markup'] and '<svg' not in a['markup']
        assert {action['target'] for action in a['actions']} <= set(a['object_ids'])
        assert {action['kind'] for action in a['actions']} == {'clip','assemble','focus','reveal'}
        for action in a['actions']:
            assert 0 <= action['start'] < action['start']+action['duration'] <= 24
        for phrase in ('new T.WebGLRenderer','new T.Plane','localClippingEnabled=true',
                       'clippingPlanes:[spatialPlane]','gl.readPixels','mesh.matrixWorld.toArray()',
                       'new T.ExtrudeGeometry','function effectReady()'):
            assert phrase in a['script']
        assert a['script'].count("import('./assets/three.module.min.js')") == 1
        for phrase in ('https://','fetch(','Math.random','requestAnimationFrame','setInterval'):
            assert phrase not in a['script']


@pytest.mark.parametrize('identity', SCENARIOS)
@pytest.mark.parametrize('variant', ['baseline','candidate'])
def test_synthetic_native_contract_accepts_finite_cross_domain_geometry_without_quality_approval(identity,variant):
    for time in (0,4.5,10.5,15.5,20.5,23.95):
        plan,state = synthetic_native(identity,time)
        assert verify_native_observation(plan,time,variant,state) >= 40
        assert expected_observations(plan,time)


@pytest.mark.parametrize('failure', ['renderer','clipping_disabled','plane_missing','stationary_plane',
    'parts_missing','assembly_missing','world_matrix','flat_geometry','hole_filled','render_missing',
    'blank_pixels','false_projection','wrong_label','small_label','overlapping_markers','clock','nonfinite'])
def test_missing_real_clip_assembly_geometry_and_gpu_measurements_fail_closed(failure):
    plan,state = synthetic_native(time=4.5 if failure == 'assembly_missing' else 10.5)
    if failure == 'renderer':state['renderer']['is_webgl_renderer'] = False
    elif failure == 'clipping_disabled':state['renderer']['local_clipping_enabled'] = False
    elif failure == 'plane_missing':state['parts'][0]['material']['clipping_planes'] = []
    elif failure == 'stationary_plane':state['parts'][0]['material']['clipping_planes'][0]['constant'] = plan['data']['cut']['initial']
    elif failure == 'parts_missing':state['parts'].pop()
    elif failure == 'assembly_missing':state['parts'][0]['position'] = plan['data']['parts'][0]['exploded'][:]
    elif failure == 'world_matrix':state['parts'][0]['matrix_world'][13] += 1
    elif failure == 'flat_geometry':state['parts'][0]['geometry']['bounds']['min'][2] = state['parts'][0]['geometry']['bounds']['max'][2]
    elif failure == 'hole_filled':state['parts'][2]['geometry']['shape_holes'] = 0
    elif failure == 'render_missing':state['renderer']['calls'] = 0
    elif failure == 'blank_pixels':state['pixel_probe']['foreground_samples'] = 0
    elif failure == 'false_projection':state['parts'][0]['projection']['pixel'][0] += 40
    elif failure == 'wrong_label':state['parts'][0]['marker']['text'] = '2'
    elif failure == 'small_label':state['parts'][0]['marker']['font_size'] = 10
    elif failure == 'overlapping_markers':state['parts'][0]['marker']['opacity'] = .92
    elif failure == 'clock':state['time'] = 0
    else:state['parts'][0]['matrix_world'][0] = float('nan')
    with pytest.raises(ServiceError):verify_native_observation(plan,state['time'] if failure != 'clock' else 10.5,'candidate',state)


@pytest.mark.parametrize('failure', ['missing','hidden','misplaced'])
def test_geology_section_must_have_correct_actual_cap_planes(failure):
    plan,state = synthetic_native('spatial-geology-section',10.5)
    if failure == 'missing':state['caps'].pop()
    elif failure == 'hidden':state['caps'][0]['visible'] = False
    else:state['caps'][0]['position'][0] += .5
    with pytest.raises(ServiceError):verify_native_observation(plan,10.5,'candidate',state)


_NODE = r'''
const fs=require('fs'),vm=require('vm');const input=JSON.parse(fs.readFileSync(0,'utf8'));
(async()=>{const T=await import('data:text/javascript;base64,'+fs.readFileSync(input.asset).toString('base64'));
 const gl={drawingBufferWidth:880,drawingBufferHeight:500,RGBA:6408,UNSIGNED_BYTE:5121,isContextLost:()=>false,getError:()=>0,
  readPixels(x,y,w,h,format,type,pixels){for(let y=0;y<h;y++)for(let x=0;x<w;x++){const i=(y*w+x)*4;
   const foreground=x>200&&x<650&&y>110&&y<390;pixels[i]=foreground?80+x%60:13;pixels[i+1]=foreground?90+y%80:26;pixels[i+2]=foreground?140:43;pixels[i+3]=255;}}};
 class SyntheticRenderer{constructor(){this.isWebGLRenderer=true;this.info={render:{calls:0,triangles:0}};}
  setPixelRatio(){}setSize(){}setClearColor(){}getContext(){return gl;}
  render(scene,camera){scene.updateMatrixWorld(true);camera.updateMatrixWorld(true);camera.matrixWorldInverse.copy(camera.matrixWorld).invert();let calls=0,triangles=0;
   scene.traverse(o=>{if(o.isMesh&&o.visible){calls++;triangles+=(o.geometry.index?o.geometry.index.count:o.geometry.getAttribute('position').count)/3;}});this.info.render={calls,triangles};}}
 const nodes=new Map();for(const id of input.ids)nodes.set(id,{id,textContent:'',style:{},attrs:{},
  setAttribute(k,v){this.attrs[k]=String(v);},getAttribute(k){return this.attrs[k]??null;},
  getBoundingClientRect(){return this.id==='spatial-viewport'?{x:24,y:100,width:880,height:500}:{x:Number.parseFloat(this.style.left||'0'),y:100+Number.parseFloat(this.style.top||'0'),width:22,height:22};}});
 for(const [i,p]of input.plan.data.parts.entries()){nodes.get('spatial-marker-'+p.id).textContent=String(i+1);nodes.get('spatial-label-'+p.id).textContent=(i+1)+' · '+p.label;}
 const context=vm.createContext({injectedThree:{...T,WebGLRenderer:SyntheticRenderer},input,nodes,Uint8Array,
  getComputedStyle:n=>({fontSize:'14px',opacity:n.style.opacity||'1'})});
 const host=`const plan=input.plan,variant=input.variant;const $=id=>nodes.get(id);const label=(id,v)=>{$(id).textContent=String(v);};
 const attrs=(id,v)=>{for(const[k,a]of Object.entries(v))$(id).setAttribute(k,a);};const fade=(id,v)=>{$(id).style.opacity=String(v);};
 const progress=(t,s,d)=>Math.max(0,Math.min(1,(t-s)/d));const smooth=p=>p*p*(3-2*p);`;
 const script=input.script.replace("await import('./assets/three.module.min.js')",'injectedThree');
 await vm.runInContext(host+script+';effectReady()',context);
 const observations=[];for(const time of input.times){context.sampleTime=time;observations.push(vm.runInContext(`effectRender(sampleTime);({native:effectNativeState(),semantic:effectState(),dom:Object.fromEntries([...nodes].map(([id,n])=>[id,{text:n.textContent,attributes:{...n.attrs}}]))})`,context));}
 process.stdout.write(JSON.stringify(observations));
})().catch(error=>{process.stderr.write(error.stack);process.exitCode=1;});
'''


def real_three_math(identity,variant,times):
    root = Path(__file__).resolve().parents[2]
    choices = [root/'video_workbench/animation_lab_webgl/node_modules/three/build/three.module.min.js',
               root/'video_workbench/rendered/feedback-20261106/animation-lab/assets/three.module.min.js']
    asset = next((p for p in choices if p.is_file() and sha256(p.read_bytes()) == THREE_ASSET_SHA256['three.module.min.js']),None)
    if asset is None or shutil.which('node') is None:
        pytest.skip('Optional local pinned Three.js/Node absent; no download; browser GPU capture is separate')
    plan = plan_for(identity)
    recipe = build_recipe(plan,variant)
    payload = dict(plan=plan,variant=variant,script=recipe['script'],ids=recipe['object_ids'],times=times,asset=str(asset))
    result = subprocess.run([shutil.which('node'),'-e',_NODE],input=json.dumps(payload),text=True,
                            encoding='utf-8',capture_output=True,timeout=20)
    assert result.returncode == 0,result.stderr
    return plan,recipe,json.loads(result.stdout)


@pytest.mark.parametrize('identity', SCENARIOS)
@pytest.mark.parametrize('variant', ['baseline','candidate'])
def test_actual_three_geometry_and_recipe_clock_with_explicitly_synthetic_gpu_and_dom(identity,variant):
    actions = build_recipe(plan_for(identity),variant)['actions']
    times = [0,4.5,10.5,15.5,20.5,23.95]
    times += [time for action in actions for time in (max(0,action['start']-1/24),
        action['start']+action['duration']/2,min(24-1/24,action['start']+action['duration']+1/24))]
    times += [0,10.5]
    plan,recipe,observations = real_three_math(identity,variant,times)
    assert observations[0] == observations[-2] and observations[2] == observations[-1]
    for time,observed in zip(times,observations):
        assert verify_native_observation(plan,time,variant,observed['native']) >= 40
        assert sum(row['marker']['opacity'] > 0 for row in observed['native']['parts']) == 1
        if identity == 'spatial-bolt-assembly':
            # Check the actual geometry extents as well as its center marker:
            # the closer assembled view must not crop a head, plate or nut.
            for part in observed['native']['parts']:
                bounds = part['geometry']['bounds']
                for corner in itertools.product(*zip(bounds['min'],bounds['max'])):
                    world = [v+p for v,p in zip(corner,part['position'])]
                    ndc,_ = projected_oracle(world,observed['native']['camera']['zoom'])
                    assert all(-.95 < value < .95 for value in ndc[:2])
        for key,condition in expected_observations(plan,time).items():
            actual = observed['dom'][key]
            if 'text' in condition:assert actual['text'] == condition['text']
            for name,value in condition.get('attributes',{}).items():assert actual['attributes'][name] == value
    # A real geometry matrix changes during assembly; the actual material plane
    # moves independently during cutting, even in the baseline presentation.
    assert observations[0]['native']['parts'] != observations[1]['native']['parts'] or identity in {'spatial-building-cutaway','spatial-geology-section'}
    assert observations[0]['native']['parts'][0]['material']['clipping_planes'] != observations[2]['native']['parts'][0]['material']['clipping_planes']
    if identity == 'spatial-bolt-assembly':
        assert observations[0]['native']['camera']['zoom'] == 1
        assert observations[1]['native']['camera']['zoom'] == 1.6
        assert observations[2]['native']['camera']['zoom'] == 2.2


def test_unreviewed_spatial_plan_cannot_inject_additional_geometry_or_scripts():
    plan = plan_for('spatial-bolt-assembly')
    plan['data']['parts'][0]['shape'] = '<script>free-form-code</script>'
    with pytest.raises(ValueError):build_recipe(plan,'candidate')
