"""Algorithm truth and real shipped-script checks, without browser claims."""
from copy import deepcopy
import json
import shutil
import subprocess
from xml.etree import ElementTree as ET

import pytest

from video_workbench.animation_lab_scenarios_algorithms import SCENARIOS, SCHEMA, build_recipe, expected_observations


def _plan(scenario):
    return deepcopy(dict(schema=SCHEMA, scenario_id=scenario, **SCENARIOS[scenario]))


def _nodes(markup):
    def flatten(node):
        return dict(attrs=node.attrib, text=node.text or '', children=[flatten(c) for c in node])
    return flatten(ET.fromstring('<svg>'+markup+'</svg>'))


_HARNESS = r'''
const fs=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8')),byId={};
function make(raw){const node={attrs:{...raw.attrs},text:raw.text,children:raw.children.map(make)};
 Object.defineProperty(node,'textContent',{get(){return this.text+this.children.map(n=>n.textContent).join('');},set(v){this.text=String(v);this.children=[];}});
 node.setAttribute=(k,v)=>{node.attrs[k]=String(v);};node.getAttribute=k=>node.attrs[k]??null;
 if(node.attrs.id)byId[node.attrs.id]=node;return node;}
const root=make(input.nodes),$=id=>{if(!byId[id])throw Error('missing '+id);return byId[id];};
const attrs=(id,values)=>{for(const[k,v]of Object.entries(values))$(id).setAttribute(k,v);};
const label=(id,text)=>{$(id).textContent=text;},fade=(id,opacity)=>attrs(id,{opacity});
const move=(id,x,y)=>attrs(id,{transform:'translate('+x+' '+y+')'});
const clamp=v=>Math.max(0,Math.min(1,v)),progress=(t,s,d)=>clamp((t-s)/d),smooth=p=>{p=clamp(p);return p*p*(3-2*p);};
const ctx=vm.createContext({$,attrs,label,fade,move,clamp,progress,smooth,JSON,Math});
vm.runInContext(input.script,ctx,{timeout:1000});
const result=[];
for(const t of input.times){vm.runInContext('effectRender('+t+')',ctx,{timeout:1000});
 result.push(JSON.parse(JSON.stringify({state:vm.runInContext('effectState()',ctx),
  objects:Object.fromEntries(Object.entries(byId).map(([id,n])=>[id,{attrs:n.attrs,text:n.textContent}]))})));}
process.stdout.write(JSON.stringify(result));
'''


def _evaluate(scenario, variant, times):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for actual shipped script execution')
    recipe = build_recipe(_plan(scenario), variant)
    result = subprocess.run([node, '-e', _HARNESS], input=json.dumps(dict(
        script=recipe['script'], nodes=_nodes(recipe['markup']), times=times)),
        capture_output=True, text=True, encoding='utf-8', timeout=15, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_four_different_domains_and_algorithms_with_real_requirements():
    assert len(SCENARIOS) == 4
    assert len({r['domain'] for r in SCENARIOS.values()}) == 4
    assert len({r['data']['algorithm'] for r in SCENARIOS.values()}) == 4
    assert {r['difficulty'] for r in SCENARIOS.values()} == {'basic', 'intermediate', 'boundary'}
    for row in SCENARIOS.values():
        assert row['module'] == 'algorithms' and row['engine'] == 'svg'
        assert len(row['requirement']['questions']) >= 3
        assert len(row['requirement']['boundaries']) >= 3
        assert row['narrative'] and row['requirement']['expected_outcome']


def test_selection_sort_replays_permutations_and_correct_minimum_each_pass():
    data = SCENARIOS['algorithm-shop-selection']['data']
    prices = {r['id']: r['price'] for r in data['products']}
    order = [r['id'] for r in data['products']]
    for step, row in enumerate(data['trace']):
        assert row['before'] == order
        index = min(range(step, len(order)), key=lambda i: prices[order[i]])
        order[step], order[index] = order[index], order[step]
        assert row['after'] == order
        assert sorted(row['before']) == sorted(row['after'])
        assert row['prices'][:step+1] == sorted(prices.values())[:step+1]
    assert [prices[i] for i in order] == [18, 25, 30, 36, 48]


def test_binary_search_comparisons_prove_range_elimination():
    d = SCENARIOS['algorithm-library-binary']['data']
    assert [r['value'] for r in d['trace']] == [21, 35, 28]
    assert [(r['next_low'], r['next_high']) for r in d['trace']] == [(4, 6), (4, 4), (4, 4)]
    for row in d['trace']:
        assert row['middle'] == (row['low']+row['high'])//2
        assert d['values'][row['middle']] == row['value']
        assert d['target'] in d['values'][row['next_low']:row['next_high']+1]


def test_bfs_layers_and_parent_edges_are_a_real_shortest_hop_route():
    d = SCENARIOS['algorithm-metro-bfs']['data']
    assert d['trace']['levels'] == [['A'], ['B', 'C'], ['D', 'E'], ['F']]
    flat = sum(d['trace']['levels'], [])
    assert len(flat) == len(set(flat)) == 6
    route = d['route']
    assert route == ['A', 'B', 'D', 'F'] and d['trace']['distance']['F'] == len(route)-1 == 3
    edge_set = {frozenset(edge) for edge in d['edges']}
    for source, target in zip(route, route[1:]):
        assert frozenset((source, target)) in edge_set
        assert d['trace']['parent'][target] == source


def test_lru_hit_updates_recency_and_evicted_item_is_a_new_miss():
    trace = SCENARIOS['algorithm-device-lru']['data']['trace']
    assert [r['after'] for r in trace] == [['A'], ['A', 'B'], ['B', 'A'], ['A', 'C'], ['C', 'B']]
    assert [r['hit'] for r in trace] == [False, False, True, False, False]
    assert [r['evicted'] for r in trace] == [None, None, None, 'B', 'A']


@pytest.mark.parametrize('scenario', SCENARIOS)
def test_complete_registered_plan_only_and_input_immutability(scenario):
    plan = _plan(scenario)
    original = deepcopy(plan)
    build_recipe(plan, 'candidate')
    assert plan == original
    for key, value in [('title', '<script>bad()</script>'), ('schema', 'other'), ('data', {})]:
        bad = deepcopy(plan)
        bad[key] = value
        with pytest.raises(ValueError, match='scenario_invalid'):
            build_recipe(bad, 'candidate')
    for variant in ['other', '<script>', None, []]:
        with pytest.raises(ValueError, match='scenario_invalid'):
            build_recipe(plan, variant)


@pytest.mark.parametrize('scenario', SCENARIOS)
def test_svg_targets_bounded_text_and_comparison_semantics(scenario):
    plan = _plan(scenario)
    baseline, candidate = (build_recipe(plan, v) for v in ('baseline', 'candidate'))
    assert baseline['markup'] == candidate['markup']
    assert baseline['script'] != candidate['script']
    assert baseline['actions'] == candidate['actions']
    root = ET.fromstring('<svg>'+candidate['markup']+'</svg>')
    ids = [n.attrib['id'] for n in root.iter() if 'id' in n.attrib]
    assert len(ids) == len(set(ids))
    assert set(candidate['object_ids']) <= set(ids)
    for n in root.iter('text'):
        assert 14 <= float(n.attrib['font-size']) <= 64
    assert 3 <= len(candidate['actions']) <= 5
    for action in candidate['actions']:
        assert action['target'] in candidate['object_ids']
        assert 0 <= action['start'] < action['start']+action['duration'] <= plan['duration_seconds']


@pytest.mark.parametrize('scenario', SCENARIOS)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_real_shipped_script_is_reversible_and_every_declared_target_changes(scenario, variant):
    plan = _plan(scenario)
    recipe = build_recipe(plan, variant)
    duration = plan['duration_seconds']
    times = [0, duration-1/24, 0, duration/2, 0]
    states = _evaluate(scenario, variant, times)
    assert states[0] == states[2] == states[4]
    assert states[0] != states[1]
    for action in recipe['actions']:
        before, middle, after = _evaluate(scenario, variant, [max(0, action['start']-1/24),
            action['start']+action['duration']/2, min(duration-1/24, action['start']+action['duration']+1/24)])
        assert before['objects'][action['target']] != after['objects'][action['target']]
        assert middle != before and middle != after


@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_actual_dom_final_values_match_derived_algorithm_results(variant):
    sort = _evaluate('algorithm-shop-selection', variant, [21])[0]
    assert sort['state']['prices'] == [18, 25, 30, 36, 48]
    assert sort['state']['positions'] == {'p1': 4, 'p2': 0, 'p3': 1, 'p4': 2, 'p5': 3}
    binary = _evaluate('algorithm-library-binary', variant, [17])[0]
    assert binary['state']['found'] and binary['state']['low'] == binary['state']['high'] == 4
    assert binary['objects']['binary-hit']['attrs']['opacity'] == '1'
    bfs = _evaluate('algorithm-metro-bfs', variant, [23])[0]
    assert bfs['state']['target_distance'] == 3 and bfs['state']['route'] == ['A', 'B', 'D', 'F']
    assert bfs['objects']['bfs-route']['attrs']['opacity'] == '1'
    cache = _evaluate('algorithm-device-lru', variant, [23])[0]
    assert cache['state']['slots'] == ['C', 'B'] and cache['state']['hits'] == 1
    assert cache['objects']['cache-old']['text'] == 'C' and cache['objects']['cache-new']['text'] == 'B'


@pytest.mark.parametrize('scenario', SCENARIOS)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_independent_python_oracle_matches_real_dom_at_boundaries_and_reverse_seeks(scenario, variant):
    plan = _plan(scenario)
    recipe = build_recipe(plan, variant)
    times = [0, plan['duration_seconds']-1/24]
    for a in recipe['actions']:
        times.extend([max(0, a['start']-1/24), a['start'], a['start']+a['duration']/2,
                      a['start']+a['duration'], min(plan['duration_seconds']-1/24, a['start']+a['duration']+1/24)])
    times += [0, plan['duration_seconds']/2, 0]
    results = _evaluate(scenario, variant, times)
    for time, actual in zip(times, results):
        expected = expected_observations(plan, time)
        assert 3 <= len(expected) <= 5
        assert set(expected) <= set(recipe['object_ids'])
        for ident, required in expected.items():
            node = actual['objects'][ident]
            if 'text' in required:
                assert node['text'] == required['text'], (ident, time)
            for name, value in required.get('attributes', {}).items():
                assert node['attrs'][name] == value, (ident, name, time)


def test_oracle_rejects_unregistered_inputs_and_invalid_times():
    plan = _plan('algorithm-device-lru')
    for time in [-1, 25, float('inf'), float('nan'), True, '1']:
        with pytest.raises(ValueError):
            expected_observations(plan, time)
    plan['data']['capacity'] = 99
    with pytest.raises(ValueError):
        expected_observations(plan, 0)
