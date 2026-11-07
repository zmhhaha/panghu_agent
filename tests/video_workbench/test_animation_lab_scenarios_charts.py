"""Cross-domain chart semantics and clock reconstruction, not browser pixels."""
from copy import deepcopy
import json
import math
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from video_workbench.animation_lab_scenarios_charts import (
    SCENARIOS, SCHEMA, build_recipe, expected_observations,
)


def plan_for(identity):
    return dict(schema=SCHEMA, scenario_id=identity, **deepcopy(SCENARIOS[identity]))


def _tree(element):
    return dict(tag=element.tag, attrs=dict(element.attrib), text=element.text or '',
                children=[_tree(child) for child in element])


_RUNNER = r'''
const input=JSON.parse(require('fs').readFileSync(0,'utf8')),vm=require('vm');
const host=`
const plan=input.plan,variant=input.variant,data={plan};const nodes=new Map();
function makeNode(spec){const node={tagName:spec.tag,values:{...spec.attrs},ownText:spec.text,
getAttribute(key){return Object.hasOwn(this.values,key)?this.values[key]:null;},
setAttribute(key,value){this.values[key]=String(value);}};
node.children=spec.children.map(makeNode);
Object.defineProperty(node,'textContent',{get(){return this.ownText+this.children.map(n=>n.textContent).join('');},
set(value){this.ownText=String(value);this.children=[];}});if(node.values.id)nodes.set(node.values.id,node);return node;}
makeNode(input.tree);
const $=id=>{const node=nodes.get(id);if(!node)throw Error('missing:'+id);return node;};
const attrs=(id,values)=>{for(const [key,value] of Object.entries(values))$(id).setAttribute(key,value);};
const label=(id,text)=>{$(id).textContent=String(text);};const fade=(id,opacity)=>attrs(id,{opacity});
const move=(id,x,y)=>attrs(id,{transform:'translate('+x+' '+y+')'});
const clamp=v=>Math.max(0,Math.min(1,v));const progress=(t,start,duration)=>clamp((t-start)/duration);
const smooth=p=>{p=clamp(p);return p*p*(3-2*p);};
`;
const verify=`
function state(t){effectRender(t);return {semantic:effectState(),nodes:Object.fromEntries([...nodes].map(([id,n])=>[id,{attrs:{...n.values},text:n.textContent}]))};}
JSON.stringify({seeks:input.times.map(state),actions:input.actions.map(a=>({before:state(a.start),after:state(a.start+a.duration)}))});
`;
process.stdout.write(vm.runInNewContext(host+input.script+verify,{input},{timeout:2500}));
'''


def states_for(plan, variant, times):
    recipe = build_recipe(plan, variant)
    node = shutil.which('node')
    assert node, 'Node.js is required for scene script semantics tests'
    request = dict(plan=plan, variant=variant, times=times, script=recipe['script'],
                   actions=recipe['actions'], tree=_tree(ET.fromstring('<svg>'+recipe['markup']+'</svg>')))
    result = subprocess.run([node, '-e', _RUNNER], input=json.dumps(request), text=True,
                            encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_domains_require_different_data_models_and_reasoning_not_renaming():
    assert len(SCENARIOS) == len({row['domain'] for row in SCENARIOS.values()}) == 4
    assert len({frozenset(row['data']) for row in SCENARIOS.values()}) == 4
    assert len({row['requirement']['goal'] for row in SCENARIOS.values()}) == 4
    assert {row['difficulty'] for row in SCENARIOS.values()} == {'intermediate', 'boundary'}
    for row in SCENARIOS.values():
        requirement = row['requirement']
        assert len(requirement['questions']) >= 2
        assert len(requirement['required_entities']) >= 5
        assert len(requirement['boundaries']) >= 2
        assert len(row['checks']) >= 3
        assert row['duration_seconds'] in {18, 22, 24}
        assert '示意' in row['takeaway']


def test_queue_conservation_and_latency_model_are_shared_by_all_metrics():
    data = SCENARIOS['chart-compute-backlog']['data']
    queue = 0
    for arrived, capacity, done, expected, latency in zip(data['arrivals'], data['capacity'], data['completed'], data['backlog'], data['latency_ms']):
        assert 0 <= done <= min(capacity, queue+arrived)
        queue += arrived-done
        assert queue == expected
        assert latency == data['latency_model']['base_ms']+queue*data['latency_model']['per_queued_ms']
    assert max(data['backlog']) == 14 and queue == 0
    assert data['completed'][-1] < data['capacity'][-1]


def test_power_is_balanced_at_every_hour_without_confusing_energy_units():
    row = SCENARIOS['chart-energy-balance']
    data = row['data']
    assert all(solar+grid+battery == demand for solar, grid, battery, demand
               in zip(data['solar'], data['grid'], data['battery'], data['demand']))
    assert data['solar'][0] == data['solar'][-1] == 0
    assert data['grid'][-1] == 4 and data['battery'][-1] == 2 and data['demand'][-1] == 6
    assert data['unit'] == 'MW'
    assert any('不推算电量' in text for text in row['requirement']['boundaries'])


def test_cumulative_flow_and_stock_are_different_quantities():
    data = SCENARIOS['chart-logistics-stock']['data']
    incoming, outgoing = 0, 0
    for i in range(len(data['windows'])):
        incoming += data['incoming'][i]
        outgoing += data['outgoing'][i]
        assert incoming == data['cumulative_in'][i]
        assert outgoing == data['cumulative_out'][i]
        assert incoming-outgoing == data['stock'][i] >= 0
    assert incoming == outgoing == 36
    assert data['stock'][1] == 0 < data['stock'][2]


def test_educational_percentages_and_percentage_points_use_one_denominator():
    row = SCENARIOS['chart-learning-gains']
    data = row['data']
    for index in range(4):
        assert data['before_rate'][index] == data['before_count'][index]/data['total']*100
        assert data['after_rate'][index] == data['after_count'][index]/data['total']*100
        assert data['gain_pp'][index] == data['after_rate'][index]-data['before_rate'][index]
    assert data['gain_pp'][1] == 40
    assert data['after_count'][1]-data['before_count'][1] == 12
    assert '因果' in row['requirement']['boundaries'][-1]


@pytest.mark.parametrize('identity', SCENARIOS)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_svg_has_visible_size_floor_and_finite_real_action_targets(identity, variant):
    plan = plan_for(identity)
    original = deepcopy(plan)
    recipe = build_recipe(plan, variant)
    assert plan == original
    assert set(recipe) == {'markup', 'script', 'object_ids', 'actions'}
    root = ET.fromstring('<svg>'+recipe['markup']+'</svg>')
    ids = [node.attrib['id'] for node in root.iter() if 'id' in node.attrib]
    assert len(ids) == len(set(ids))
    assert set(recipe['object_ids']) <= set(ids)
    assert all(float(node.attrib['font-size']) >= 14 for node in root.iter() if node.tag == 'text')
    assert len(recipe['actions']) == 4
    for row in recipe['actions']:
        assert row['target'] in recipe['object_ids']
        assert 0 <= row['start'] < row['start']+row['duration'] <= plan['duration_seconds']
    assert all(token not in recipe['script'] for token in ('Math.random', 'Date.', 'requestAnimationFrame', 'fetch(', 'setInterval'))


@pytest.mark.parametrize('identity', SCENARIOS)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_absolute_time_reconstructs_scene_and_actions_change_their_named_targets(identity, variant):
    plan = plan_for(identity)
    result = states_for(plan, variant, [0, 7, 17, 3, plan['duration_seconds'], 0])
    assert result['seeks'][0] == result['seeks'][-1]
    for action, states in zip(build_recipe(plan, variant)['actions'], result['actions']):
        assert states['before']['nodes'][action['target']] != states['after']['nodes'][action['target']], action
    assert result['seeks'][0]['semantic']['domain'] == plan['domain']


@pytest.mark.parametrize('identity', SCENARIOS)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_independent_python_oracle_matches_script_dom_at_nonmonotonic_times(identity, variant):
    plan = plan_for(identity)
    times = [0, 3.5, 10.5, 17.958333333333332, 5.041666666666667, plan['duration_seconds'], 0]
    result = states_for(plan, variant, times)
    observed_ids = set(build_recipe(plan, variant)['object_ids'])
    for time, state in zip(times, result['seeks']):
        for key, expected in expected_observations(plan, time).items():
            assert key in observed_ids
            node = state['nodes'][key]
            if 'text' in expected:
                assert node['text'] == expected['text'], (identity, time, key)
            for name, value in expected.get('attributes', {}).items():
                assert node['attrs'][name] == value, (identity, time, key, name)


@pytest.mark.parametrize('identity', SCENARIOS)
def test_ab_changes_presentation_bytes_but_not_semantic_numbers(identity):
    plan = plan_for(identity)
    a, b = (build_recipe(plan, variant) for variant in ('baseline', 'candidate'))
    assert a['markup'] == b['markup']
    assert a['script'] != b['script']
    assert a['actions'] == b['actions']
    times = [0, 12, plan['duration_seconds']]
    baseline, candidate = (states_for(plan, variant, times) for variant in ('baseline', 'candidate'))
    for time, a_state, b_state in zip(times, baseline['seeks'], candidate['seeks']):
        for key, expected in expected_observations(plan, time).items():
            if 'text' in expected:
                assert a_state['nodes'][key]['text'] == b_state['nodes'][key]['text'] == expected['text']


@pytest.mark.parametrize('identity', SCENARIOS)
@pytest.mark.parametrize('mutation', [
    lambda plan: plan.update(schema='other'),
    lambda plan: plan.update(title='<script>alert(1)</script>'),
    lambda plan: plan.update(data={'unsafe': 'while(true){}'}),
    lambda plan: plan.update(script='fetch(secret)'),
    lambda plan: plan['requirement'].update(goal='change the conclusion'),
])
def test_unregistered_requirement_data_or_code_is_rejected(identity, mutation):
    plan = plan_for(identity)
    mutation(plan)
    with pytest.raises(ValueError, match='plan_mismatch'):
        build_recipe(plan, 'candidate')
    with pytest.raises(ValueError, match='plan_mismatch'):
        expected_observations(plan, 0)


@pytest.mark.parametrize('value', [None, {}, [], {'scenario_id': 4}, {'scenario_id': 'unregistered'}])
def test_unknown_recipe_is_rejected(value):
    with pytest.raises(ValueError):
        build_recipe(value, 'candidate')


@pytest.mark.parametrize('variant', [None, 1, 'custom'])
def test_unknown_variant_is_rejected(variant):
    with pytest.raises(ValueError, match='recipe_invalid'):
        build_recipe(plan_for('chart-energy-balance'), variant)


@pytest.mark.parametrize('time', [None, True, float('inf'), float('nan'), '12'])
def test_oracle_rejects_nonfinite_or_non_numeric_time(time):
    with pytest.raises(ValueError, match='time_invalid'):
        expected_observations(plan_for('chart-energy-balance'), time)


@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_compute_curve_coordinates_and_labels_share_two_explicit_zero_baselines(variant):
    plan = plan_for('chart-compute-backlog')
    nodes = states_for(plan, variant, [plan['duration_seconds']])['seeks'][0]['nodes']
    points = lambda key: [(float(x), float(y)) for x, y in re.findall(r'[ML](-?[\d.]+) (-?[\d.]+)', nodes[key]['attrs']['d'])]
    queue, latency = points('compute-backlog-path'), points('compute-latency-path')
    assert len(queue) == len(latency) == len(plan['data']['windows'])
    for index, ((x1, qy), (x2, ly)) in enumerate(zip(queue, latency)):
        assert x1 == x2 == 112+index*152
        assert (450-qy)/220*16 == pytest.approx(plan['data']['backlog'][index], abs=.001)
        assert (450-ly)/220*400 == pytest.approx(plan['data']['latency_ms'][index], abs=.01)


@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_energy_stack_geometry_sums_to_the_demand_curve_at_the_same_hour(variant):
    plan = plan_for('chart-energy-balance')
    nodes = states_for(plan, variant, [plan['duration_seconds']])['seeks'][0]['nodes']
    curve = [(float(x), float(y)) for x, y in re.findall(r'[ML](-?[\d.]+) (-?[\d.]+)', nodes['energy-demand']['attrs']['d'])]
    for index, (cx, cy) in enumerate(curve):
        bottom = 452
        for key in ('solar', 'grid', 'battery'):
            attrs = nodes[f'energy-{key}-{index}']['attrs']
            height, top = float(attrs['height']), float(attrs['y'])
            assert height == plan['data'][key][index]*22
            assert top+height == bottom
            bottom = top
        assert bottom == cy
        assert cx == 168+index*150
    assert float(nodes['energy-solar-4']['attrs']['height']) == 0


@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_logistics_vertical_gap_really_encodes_current_stock_and_final_lines_meet(variant):
    plan = plan_for('chart-logistics-stock')
    peak, final = [row['nodes'] for row in states_for(plan, variant, [9.5, 22])['seeks']]
    assert peak['logistics-stock-value']['text'] == '12'
    assert peak['logistics-gap']['attrs']['d'] == 'M492 298V364'
    assert (364-298)/5.5 == 12
    assert final['logistics-stock-value']['text'] == '0'
    assert final['logistics-gap']['attrs']['d'] == 'M872 254V254'
    assert final['logistics-in']['attrs']['d'].endswith('872 254')
    assert final['logistics-out']['attrs']['d'].endswith('872 254')
    assert build_recipe(plan, variant)['actions'][-1]['target'] == 'logistics-in-total'


@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_learning_bars_preserve_the_shared_percentage_scale_even_during_reveal(variant):
    plan = plan_for('chart-learning-gains')
    result = states_for(plan, variant, [0, 8, 24])
    for snapshot in result['seeks']:
        nodes = snapshot['nodes']
        for index in range(4):
            for key in ('before', 'after'):
                attrs = nodes[f'learning-{key}-{index}']['attrs']
                value = plan['data'][key+'_rate'][index]
                assert float(attrs['height'])/220*100 == pytest.approx(value)
                assert float(attrs['y'])+float(attrs['height']) == 452
                assert float(attrs['data-value']) == value
        assert nodes['learning-count-value']['text'] == '12 → 24'
