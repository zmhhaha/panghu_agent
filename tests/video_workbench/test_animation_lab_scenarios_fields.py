"""Real shipped JavaScript with a synthetic Canvas; not native browser proof."""
from copy import deepcopy
import json
import math
import shutil
import subprocess
from xml.etree import ElementTree as ET

import pytest

from video_workbench.animation_lab_scenarios_fields import (SCENARIOS, SCHEMA, build_recipe,
    expected_observations, review_native_observation, verify_native_observation)


def _plan(identity):
    return deepcopy(dict(schema=SCHEMA, scenario_id=identity, **SCENARIOS[identity]))


_HARNESS = r'''
const fs=require('node:fs'),vm=require('node:vm'),input=JSON.parse(fs.readFileSync(0,'utf8'));
const nodes=Object.fromEntries(['field-canvas','field-status','field-focus','field-note'].map(id=>[id,{attrs:{},text:''}]));
nodes['field-note'].text='示意输入；不作真实测量、科学预测或设备诊断。';
let background='#000000',shapes=[],path=[];
const ctx={font:'500 16px VideoNoto',fillStyle:'#000000',strokeStyle:'#000000',lineWidth:1,globalAlpha:1,
 beginPath(){path=[];},moveTo(x,y){path.push([x,y]);},lineTo(x,y){path.push([x,y]);},closePath(){},
 stroke(){if(!input.drop_stroke)shapes.push({points:path.slice(),color:this.strokeStyle,width:this.lineWidth});},
 arc(x,y,r){path=[{circle:[x,y,r]}];},
 fillRect(){background=this.fillStyle;shapes=[];},
 fill(){if(!input.drop_fill&&path[0]?.circle)shapes.push({circle:path[0].circle,color:this.fillStyle});},
 fillText(){},measureText(text){const size=Number(this.font.match(/([0-9]+)px/)[1]);return{actualBoundingBoxLeft:0,
  actualBoundingBoxRight:text.length*size*.55,actualBoundingBoxAscent:size*.8,actualBoundingBoxDescent:size*.2};},
 getImageData(x,y){let color=background;for(const s of shapes){if(s.circle){const[a,b,r]=s.circle;if(Math.hypot(x+.5-a,y+.5-b)<r)color=s.color;}
  else for(let i=1;i<s.points.length;i++){const[a,b]=s.points[i-1],[c,d]=s.points[i],dx=c-a,dy=d-b,len=dx*dx+dy*dy;
   const p=len?Math.max(0,Math.min(1,((x+.5-a)*dx+(y+.5-b)*dy)/len)):0;if(Math.hypot(x+.5-a-p*dx,y+.5-b-p*dy)<=s.width/2)color=s.color;}}
 return{data:[parseInt(color.slice(1,3),16),parseInt(color.slice(3,5),16),parseInt(color.slice(5,7),16),255]};}
};
nodes['field-canvas'].getContext=()=>ctx;
const $=id=>{if(!nodes[id])throw Error('missing '+id);return nodes[id];};
const attrs=(id,values)=>{for(const[k,v]of Object.entries(values))$(id).attrs[k]=String(v);};
const label=(id,text)=>{$(id).text=String(text);};
const context=vm.createContext({$,attrs,label,JSON,Math});
vm.runInContext(input.script,context,{timeout:1000});
const output=[];
for(const time of input.times){vm.runInContext('effectRender('+time+')',context,{timeout:1000});
 output.push(JSON.parse(JSON.stringify({native:vm.runInContext('effectNativeState()',context),
  semantic:vm.runInContext('effectState()',context),nodes:Object.fromEntries(Object.entries(nodes).map(([id,n])=>[id,{text:n.text,attrs:n.attrs}]))})));}
process.stdout.write(JSON.stringify(output));
'''


def _execute(identity, variant, times, *, drop_fill=False, drop_stroke=False):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for real shipped-script execution')
    recipe = build_recipe(_plan(identity), variant)
    run = subprocess.run([node, '-e', _HARNESS], input=json.dumps(dict(script=recipe['script'],
        times=times, drop_fill=drop_fill, drop_stroke=drop_stroke)), capture_output=True, encoding='utf8', timeout=15)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_four_domains_have_distinct_canvas_requirements_and_explicit_scientific_limits():
    assert len(SCENARIOS) == len({row['domain'] for row in SCENARIOS.values()}) == 4
    assert len({row['expression'] for row in SCENARIOS.values()}) == 4
    for row in SCENARIOS.values():
        assert row['module'] == 'fields' and row['engine'] == 'canvas'
        assert type(row['data']['seed']) is int
        assert len(row['requirement']['questions']) == 3
        assert len(row['requirement']['boundaries']) == 3
        assert row['narrative'] and row['requirement']['expected_outcome']


@pytest.mark.parametrize('identity', SCENARIOS)
def test_registered_plan_only_real_canvas_and_same_ab_semantics(identity):
    plan = _plan(identity)
    original = deepcopy(plan)
    a, b = (build_recipe(plan, variant) for variant in ('baseline', 'candidate'))
    assert plan == original
    assert a['markup'] == b['markup'] and a['actions'] == b['actions']
    assert a['script'] != b['script']
    root = ET.fromstring('<section>'+a['markup']+'</section>')
    canvas = root.find('canvas')
    assert canvas is not None and canvas.get('width') == '1152' and canvas.get('height') == '440'
    ids = [n.get('id') for n in root.iter() if n.get('id')]
    assert len(ids) == len(set(ids)) and set(ids) == set(a['object_ids'])
    assert all(action['target'] == 'field-canvas' for action in a['actions'])
    for field, value in [('title', '<script>bad()</script>'), ('data', {}), ('schema', 'other')]:
        invalid = deepcopy(plan); invalid[field] = value
        with pytest.raises(ValueError, match='fields_plan_invalid'):
            build_recipe(invalid, 'candidate')
    for variant in ('other', [], None, True):
        with pytest.raises(ValueError, match='fields_variant_invalid'):
            build_recipe(plan, variant)


def test_sensor_three_point_mean_really_uses_available_neighbors():
    data = SCENARIOS['field-sensor-waveform']['data']
    assert len(data['raw']) == len(data['filtered']) == 65
    for index, value in enumerate(data['filtered']):
        neighbors = data['raw'][max(0, index-1):min(65, index+2)]
        assert value == round(sum(neighbors)/len(neighbors), 6)


@pytest.mark.parametrize('identity', SCENARIOS)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_real_script_matches_independent_geometry_dom_pixels_and_reverse_seek(identity, variant):
    plan = _plan(identity)
    times = [0, 1, 2.5, 4+1/24, 6, 7.5, 9+1/24, 11, 12.5, 14+1/24, 16, 17.5,
             19+1/24, plan['duration_seconds']-1/24, 0, plan['duration_seconds']/2, 0]
    rows = _execute(identity, variant, times)
    assert rows[0] == rows[-1] == rows[-3]
    for time, row in zip(times, rows):
        report = review_native_observation(plan, time, variant, row['native'])
        verified = verify_native_observation(plan, time, variant, row['native'])
        assert type(verified) is int and verified > 0
        assert report['technical_status'] == 'passed'
        assert report['checked_draws'] >= 3 and report['checked_pixels'] >= 3
        assert len(row['native']['draws']) < 100
        assert len(json.dumps(row['native'])) < 40000
        for ident, wanted in expected_observations(plan, time).items():
            if 'text' in wanted:
                assert row['nodes'][ident]['text'] == wanted['text']
            for name, value in wanted.get('attributes', {}).items():
                assert row['nodes'][ident]['attrs'][name] == value
    for action in build_recipe(plan, variant)['actions']:
        before, after = _execute(identity, variant, [action['start']-1/24, action['start']+action['duration']+1/24])
        assert before['nodes'][action['target']] != after['nodes'][action['target']]
        assert before['native'] != after['native']


@pytest.mark.parametrize('identity', SCENARIOS)
def test_actual_paint_is_required_even_when_geometry_and_semantic_claims_are_present(identity):
    plan = _plan(identity)
    row = _execute(identity, 'candidate', [12], drop_fill=True)[0]
    assert row['native']['draws'] and row['semantic']
    with pytest.raises(ValueError, match='fields_pixel_mismatch'):
        verify_native_observation(plan, 12, 'candidate', row['native'])


@pytest.mark.parametrize('identity', SCENARIOS)
def test_actual_curve_or_arrow_stroke_is_required_even_when_markers_remain(identity):
    plan = _plan(identity)
    row = _execute(identity, 'candidate', [12], drop_stroke=True)[0]
    with pytest.raises(ValueError, match='fields_stroke_pixel_mismatch'):
        verify_native_observation(plan, 12, 'candidate', row['native'])


@pytest.mark.parametrize('identity', SCENARIOS)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_fractional_frame_seek_binds_actual_gsap_paint_clock_without_relaxing_geometry(identity, variant):
    plan = _plan(identity)
    requested = 4+1/24
    measured = math.floor(requested*1_000_000+.5)/1_000_000
    row = _execute(identity, variant, [measured])[0]
    assert verify_native_observation(plan, requested, variant, row['native']) > 0
    invalid = deepcopy(row['native'])
    invalid['time'] += .00001
    with pytest.raises(ValueError, match='fields_geometry_mismatch'):
        verify_native_observation(plan, requested, variant, invalid)
    invalid = deepcopy(row['native'])
    circle = next(shape for shape in invalid['draws'] if shape['kind'] == 'circle')
    circle['x'] += .000002
    with pytest.raises(ValueError, match='fields_geometry_mismatch'):
        verify_native_observation(plan, requested, variant, invalid)


@pytest.mark.parametrize('mutation', ['coordinate', 'point', 'color', 'pixel', 'missing_draw', 'text', 'text_bounds', 'time', 'variant'])
def test_tampered_native_evidence_is_rejected_without_scientific_summary_trust(mutation):
    plan = _plan('field-acoustics-interference')
    row = _execute(plan['scenario_id'], 'candidate', [12])[0]
    native = row['native']
    if mutation == 'coordinate':
        native['draws'][-1]['x'] += 2
    elif mutation == 'point':
        native['draws'][2]['points'][12][1] += 2
    elif mutation == 'color':
        native['draws'][-1]['fill'] = '#ffffff'
    elif mutation == 'pixel':
        native['pixels'][1]['rgba'] = [11, 20, 34, 255]
    elif mutation == 'missing_draw':
        native['draws'].pop()
    elif mutation == 'text':
        native['text_boxes'][0]['text'] = 'claimed other wave'
    elif mutation == 'text_bounds':
        native['text_boxes'][0]['bounds']['x'] = -100
    elif mutation == 'time':
        native['time'] = 2
    else:
        native['variant'] = 'baseline'
    with pytest.raises(ValueError, match='animation_lab_fields_'):
        verify_native_observation(plan, 12, 'candidate', native)


def test_fixed_seed_and_time_are_finite_bounded_inputs():
    plan = _plan('field-river-particles')
    for time in (-1, 25, float('nan'), float('inf'), True, '1'):
        with pytest.raises(ValueError, match='fields_time_invalid'):
            expected_observations(plan, time)
    invalid = deepcopy(plan); invalid['data']['seed'] = 999
    with pytest.raises(ValueError, match='fields_plan_invalid'):
        expected_observations(invalid, 0)
    for variant in ([], None, True, 'other'):
        with pytest.raises(ValueError, match='fields_variant_invalid'):
            verify_native_observation(plan, 0, variant, {})


def test_same_time_reconstructs_instead_of_integrating_a_simulation_loop():
    rows = _execute('field-river-particles', 'candidate', [0, 20, 3, 23, 3])
    assert rows[2] == rows[4]
    assert rows[0] != rows[1]
    positions = [shape for shape in rows[1]['native']['draws'] if shape['kind'] == 'circle']
    assert len(positions) == len({shape['id'] for shape in positions}) == 18
    assert all(45 <= p['x'] < 1105 and 125 <= p['y'] <= 331 for p in positions)


def test_sound_input_waves_remain_visible_when_sum_cancels():
    state = _execute('field-acoustics-interference', 'candidate', [17])[0]['native']
    waves = [shape['points'] for shape in state['draws'] if shape['id'] in {'wave-0', 'wave-1', 'wave-2'}]
    assert len(waves) == 3
    assert max(y for _, y in waves[0])-min(y for _, y in waves[0]) > 60
    assert max(y for _, y in waves[1])-min(y for _, y in waves[1]) > 60
    assert all(abs(y-355) < 1e-9 for _, y in waves[2])


def test_electric_arrows_start_at_fixed_grid_positions_and_grow_only_after_intro():
    first, middle, later = _execute('field-electric-dipole', 'candidate', [0, 3, 8])
    assert not any(d['id'].startswith('vector-') for d in first['native']['draws'])
    a = [d for d in middle['native']['draws'] if d['kind'] == 'path']
    b = [d for d in later['native']['draws'] if d['kind'] == 'path']
    assert len(a) == len(b) == 24
    assert [d['points'][0] for d in a] == [d['points'][0] for d in b]
    assert all(math.dist(d['points'][0], d['points'][1]) < 28 for d in a)
    assert all(math.isclose(math.dist(d['points'][0], d['points'][1]), 28) for d in b)
