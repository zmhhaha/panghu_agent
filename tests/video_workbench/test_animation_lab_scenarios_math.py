"""Independent arithmetic and the shipped SVG script, without browser claims."""
from copy import deepcopy
from fractions import Fraction
import json
import math
import re
import shutil
import subprocess
from xml.etree import ElementTree as ET

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_lab_scenarios_math import (
    SCENARIOS, SCHEMA, build_recipe, expected_observations,
)


def _plan(identity):
    return deepcopy(dict(schema=SCHEMA, scenario_id=identity, **SCENARIOS[identity]))


def _tree(node):
    return dict(attrs=dict(node.attrib), text=node.text or '', children=[_tree(child) for child in node])


_HARNESS = r'''
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8')),vm=require('node:vm'),nodes={};
function make(raw){const n={values:{...raw.attrs},own:raw.text,children:raw.children.map(make)};
 Object.defineProperty(n,'textContent',{get(){return this.own+this.children.map(c=>c.textContent).join('');},set(v){this.own=String(v);this.children=[];}});
 n.setAttribute=(k,v)=>{n.values[k]=String(v);};n.getAttribute=k=>n.values[k]??null;
 if(n.values.id)nodes[n.values.id]=n;return n;}
make(input.tree);const $=id=>{if(!nodes[id])throw Error('missing '+id);return nodes[id];};
const attrs=(id,values)=>{for(const[k,v]of Object.entries(values))$(id).setAttribute(k,v);};
const label=(id,text)=>{$(id).textContent=String(text);},fade=(id,opacity)=>attrs(id,{opacity});
const move=(id,x,y)=>attrs(id,{transform:'translate('+x+' '+y+')'});
const clamp=v=>Math.max(0,Math.min(1,v)),progress=(t,s,d)=>clamp((t-s)/d),smooth=p=>{p=clamp(p);return p*p*(3-2*p);};
const ctx=vm.createContext({$,attrs,label,fade,move,clamp,progress,smooth,JSON,Math});
vm.runInContext(input.script,ctx,{timeout:1000});
const states=[];
for(const time of input.times){vm.runInContext('effectRender('+time+')',ctx,{timeout:1000});
 states.push(JSON.parse(JSON.stringify({semantic:vm.runInContext('effectState()',ctx),
  objects:Object.fromEntries(Object.entries(nodes).map(([id,n])=>[id,{attributes:{...n.values},text:n.textContent}]))})));}
process.stdout.write(JSON.stringify(states));
'''


def _states(identity, variant, times):
    node = shutil.which('node')
    assert node, 'Node.js is required to execute the shipped math scene script'
    recipe = build_recipe(_plan(identity), variant)
    result = subprocess.run([node, '-e', _HARNESS], text=True, encoding='utf-8', capture_output=True,
        input=json.dumps(dict(script=recipe['script'], times=times,
                             tree=_tree(ET.fromstring('<svg>'+recipe['markup']+'</svg>')))), timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_four_distinct_mathematical_questions_have_complete_premises_and_narratives():
    assert len(SCENARIOS) == len({row['domain'] for row in SCENARIOS.values()}) == 4
    assert len({row['data']['kind'] for row in SCENARIOS.values()}) == 4
    for scenario in SCENARIOS.values():
        assert scenario['module'] == 'math' and scenario['engine'] == 'svg'
        assert len(scenario['narrative']) >= 120
        assert len(scenario['requirement']['questions']) >= 3
        assert len(scenario['requirement']['required_entities']) >= 6
        assert len(scenario['requirement']['boundaries']) >= 3
        assert len(scenario['checks']) >= 3
        assert len(scenario['data']['rules']) == 4
        assert scenario['requirement']['expected_outcome']


def test_equation_preserves_equality_on_both_sides_and_substitution_verifies_the_original():
    d = SCENARIOS['math-commerce-balance']['data']
    price = Fraction(d['total']-d['fee'], d['count'])
    assert price == 8
    assert d['count']*price+d['fee'] == d['total'] == 30
    assert (d['count']*price+d['fee'])-d['fee'] == d['total']-d['fee'] == 24
    assert Fraction(d['total']-d['fee'], d['count']) == price


def test_bayes_uses_two_populations_and_the_posterior_is_not_sensitivity():
    d = SCENARIOS['math-health-bayes']['data']
    disease = d['population']*Fraction(*d['prevalence'])
    healthy = d['population']-disease
    tp = disease*Fraction(*d['sensitivity'])
    fp = healthy*Fraction(*d['false_positive_rate'])
    assert (disease, healthy, tp, fp) == (10, 990, 9, 99)
    assert Fraction(tp, tp+fp) == Fraction(1, 12)
    assert Fraction(tp, tp+fp) != Fraction(*d['sensitivity'])
    assert Fraction(tp, disease) == Fraction(*d['sensitivity'])


def test_conversion_factors_represent_one_and_leave_the_expected_dimensions():
    d = SCENARIOS['math-transport-units']['data']
    # Use a separate SI unit table to check the stated conversion factors.
    units = dict(m=Fraction(1), km=Fraction(1000), s=Fraction(1), h=Fraction(3600))
    assert d['metres_per_kilometre']*units['m']/units['km'] == 1
    assert units['h']/(d['seconds_per_hour']*units['s']) == 1
    speed = d['speed_kmh']*units['km']/units['h']
    assert speed == 20 and speed*d['time_seconds'] == 60
    assert any('匀速' in s for s in SCENARIOS['math-transport-units']['requirement']['boundaries'])


@pytest.mark.parametrize('identity', SCENARIOS)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_recipe_has_real_terms_measurable_geometry_and_four_finite_derivations(identity, variant):
    plan = _plan(identity)
    original = deepcopy(plan)
    recipe = build_recipe(plan, variant)
    assert plan == original
    assert set(recipe) == {'markup', 'script', 'object_ids', 'actions'}
    root = ET.fromstring('<svg>'+recipe['markup']+'</svg>')
    ids = [node.attrib['id'] for node in root.iter() if 'id' in node.attrib]
    assert len(ids) == len(set(ids))
    assert set(recipe['object_ids']) <= set(ids) and len(recipe['object_ids']) <= 100
    assert all(float(node.attrib['font-size']) >= 14 for node in root.iter() if node.tag == 'text')
    assert len(recipe['actions']) == 4
    for action in recipe['actions']:
        assert action['target'] in recipe['object_ids']
        assert 0 <= action['start'] < action['start']+action['duration'] <= plan['duration_seconds']
    assert all(token not in recipe['script'] for token in ('Date.', 'Math.random', 'fetch(', 'setInterval', 'requestAnimationFrame'))


@pytest.mark.parametrize('identity', SCENARIOS)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_shipped_script_matches_independent_arithmetic_and_svg_oracle_after_reverse_seeks(identity, variant):
    plan = _plan(identity)
    times = [0, plan['duration_seconds']-1/24, 3.5, 5, 6, 7.5, 9, 10, 11.5, 13, 14, 15.5, 17, 0]
    actual = _states(identity, variant, times)
    assert actual[0] == actual[-1]
    observed = set(build_recipe(plan, variant)['object_ids'])
    for time, state in zip(times, actual):
        for key, expectation in expected_observations(plan, time).items():
            assert key in observed
            node = state['objects'][key]
            if 'text' in expectation:
                assert node['text'] == expectation['text'], (identity, time, key)
            for attribute, value in expectation.get('attributes', {}).items():
                assert node['attributes'][attribute] == value, (identity, time, key, attribute)


@pytest.mark.parametrize('identity', SCENARIOS)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_each_named_derivation_target_really_changes_and_zero_seek_fully_resets(identity, variant):
    recipe = build_recipe(_plan(identity), variant)
    times = [0]
    for row in recipe['actions']:
        times.extend([max(0, row['start']-1/24), row['start']+row['duration']+1/24])
    times.extend([22 if identity != 'math-health-bayes' else 24, 0])
    states = _states(identity, variant, times)
    assert states[0] == states[-1]
    for index, action in enumerate(recipe['actions']):
        before, after = states[1+index*2:3+index*2]
        assert before['objects'][action['target']] != after['objects'][action['target']], action


@pytest.mark.parametrize('identity', SCENARIOS)
def test_ab_changes_guidance_but_preserves_all_measured_math_and_duration(identity):
    plan = _plan(identity)
    baseline, candidate = [build_recipe(plan, variant) for variant in ('baseline', 'candidate')]
    assert baseline['markup'] == candidate['markup']
    assert baseline['script'] != candidate['script']
    assert baseline['actions'] == candidate['actions']
    a, b = [_states(identity, variant, [3.5, 11.5, 21]) for variant in ('baseline', 'candidate')]
    for time, left, right in zip([3.5, 11.5, 21], a, b):
        assert left['semantic'] == right['semantic']
        for key in expected_observations(plan, time):
            assert left['objects'][key] == right['objects'][key]
        assert left['objects']['math-focus']['attributes']['opacity'] == '0'
        assert right['objects']['math-focus']['attributes']['opacity'] == '0.9'


def _polygon_area(path):
    points = [(float(x), float(y)) for x, y in re.findall(r'[ML](-?[\d.]+) (-?[\d.]+)', path)]
    return abs(sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(points, points[1:]+points[:1])))/2


@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_real_triangle_paths_are_halves_at_one_scale_and_translation_never_resizes(variant):
    d = SCENARIOS['math-geometry-triangle']['data']
    states = _states('math-geometry-triangle', variant, [9, 13, 22])
    final = states[-1]['objects']
    rectangle = final['area-rectangle']['attributes']
    width, height = float(rectangle['width']), float(rectangle['height'])
    assert width/height == Fraction(d['base_cm'], d['height_cm'])
    assert width/d['base_cm'] == height/d['height_cm'] == d['pixels_per_cm']
    a, b = (final[key]['attributes']['d'] for key in ('area-first', 'area-second-path'))
    assert _polygon_area(a) == _polygon_area(b) == width*height/2
    assert _polygon_area(a)/d['pixels_per_cm']**2 == 48
    assert states[0]['objects']['area-second']['attributes']['transform'] == 'translate(0 0)'
    assert final['area-second']['attributes']['transform'] == 'translate(90 60)'
    assert all(s['objects']['area-second-path']['attributes']['d'] == b for s in states)
    assert final['area-numerator']['attributes']['y'] < final['area-denominator']['attributes']['y']


@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_true_and_false_positive_rectangles_use_the_same_scale_and_touch_without_a_gap(variant):
    nodes = _states('math-health-bayes', variant, [24])[0]['objects']
    true, false = (nodes[key]['attributes'] for key in ('bayes-true-positive', 'bayes-false-positive'))
    assert float(true['width'])/9 == float(false['width'])/99 == 8
    assert float(true['x'])+float(true['width']) == float(false['x'])
    assert true['y'] == false['y'] and true['height'] == false['height']
    assert nodes['bayes-denominator']['text'] == '9 + 99 = 108'
    assert nodes['bayes-posterior']['text'] == '= 1 / 12 ≈ 8.3%'


@pytest.mark.parametrize('identity,key,attribute,wrong', [
    ('math-commerce-balance', 'balance-variable', 'transform', 'translate(0 0)'),
    ('math-commerce-balance', 'balance-total', 'data-value', '30'),
    ('math-geometry-triangle', 'area-second-path', 'd', 'M170 250 L554 250 L554 506 L170 506 Z'),
    ('math-geometry-triangle', 'area-rectangle', 'height', '200'),
    ('math-health-bayes', 'bayes-false-positive', 'width', '72'),
    ('math-health-bayes', 'bayes-denominator', 'text', '1000'),
    ('math-transport-units', 'units-time-denominator', 'text', '3600 h'),
    ('math-transport-units', 'units-cancel-hour-top', 'd', 'M644 253 L644 253'),
])
def test_correct_result_label_cannot_hide_incorrect_actual_terms_units_or_geometry(monkeypatch, identity, key, attribute, wrong):
    from video_workbench import animation_lab_scenarios_review as review

    # Route only this family's independent oracle into the existing production
    # gate; registry integration belongs to the host, not this family module.
    monkeypatch.setattr(review, 'expected_observations', expected_observations)
    plan = _plan(identity)
    state = _states(identity, 'candidate', [21])[0]
    objects = state['objects']
    assert review._oracle(plan, 21, objects) >= 20
    result_before = deepcopy(objects)
    if attribute == 'text':
        objects[key]['text'] = wrong
    else:
        objects[key]['attributes'][attribute] = wrong
    assert state['semantic'] == _states(identity, 'candidate', [21])[0]['semantic']
    assert objects != result_before
    with pytest.raises(ServiceError, match='animation_lab_scenario_semantic_mismatch'):
        review._oracle(plan, 21, objects)


@pytest.mark.parametrize('identity', SCENARIOS)
@pytest.mark.parametrize('part', ['data', 'narrative', 'requirement', 'schema', 'unknown'])
def test_only_complete_registered_briefs_can_be_compiled_or_reviewed(identity, part):
    plan = _plan(identity)
    if part == 'data':
        plan['data']['premise'] = 'change the mathematical premise'
    elif part == 'requirement':
        plan['requirement']['expected_outcome'] = 'change the conclusion'
    elif part == 'unknown':
        plan['script'] = 'fetch(secret)'
    else:
        plan[part] = '<script>unsafe</script>'
    with pytest.raises(ValueError, match='plan_mismatch'):
        build_recipe(plan, 'candidate')
    with pytest.raises(ValueError, match='plan_mismatch'):
        expected_observations(plan, 0)


@pytest.mark.parametrize('variant', [None, 1, {}, 'unregistered'])
def test_only_baseline_and_candidate_can_be_built(variant):
    with pytest.raises(ValueError, match='recipe_invalid'):
        build_recipe(_plan('math-health-bayes'), variant)


@pytest.mark.parametrize('time', [-1, 100, True, None, '3', math.inf, math.nan])
def test_oracle_rejects_invalid_or_unbounded_time(time):
    with pytest.raises(ValueError, match='time_invalid'):
        expected_observations(_plan('math-health-bayes'), time)
