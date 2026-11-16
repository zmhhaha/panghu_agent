"""Logic/DOM contract checks. Real layout and screenshot review run in the host.

The tiny DOM below executes the shipped script, including reversible seeks and
visible text/attribute changes; it deliberately makes no browser pixel claim.
"""
from copy import deepcopy
import json
import shutil
import subprocess
from xml.etree import ElementTree as ET

import pytest

from video_workbench.animation_lab_effects_logic import RECIPES, build_recipe


def _plan(effect):
    recipe = RECIPES[effect]
    return deepcopy(dict(schema='video.animation-lab-effect-plan.v1', effect=effect,
                         title=recipe['title'], takeaway=recipe['takeaway'], labels=recipe['labels']))


def _nodes(markup):
    def flatten(node):
        return dict(attrs=node.attrib, text=node.text or '', tag=node.tag,
                    children=[flatten(child) for child in node])
    return flatten(ET.fromstring('<svg>'+markup+'</svg>'))


_HARNESS = r'''
const fs=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8')),byId={};
function make(raw){
 const node={attrs:{...raw.attrs},text:raw.text,tag:raw.tag,children:raw.children.map(make)};
 Object.defineProperty(node,'textContent',{get(){return this.text+this.children.map(n=>n.textContent).join('');},
  set(text){this.text=String(text);this.children=[];}});
 node.setAttribute=(key,value)=>{node.attrs[key]=String(value);};
 if(node.attrs.id)byId[node.attrs.id]=node;return node;
}
const root=make(input.nodes),$=id=>{if(!byId[id])throw Error('missing '+id);return byId[id];};
const attrs=(id,values)=>{for(const [key,value] of Object.entries(values))$(id).setAttribute(key,value);};
const label=(id,text)=>{$(id).textContent=text;},fade=(id,opacity)=>attrs(id,{opacity});
const move=(id,x,y)=>attrs(id,{transform:'translate('+x+' '+y+')'});
const clamp=v=>Math.max(0,Math.min(1,v)),progress=(t,s,d)=>clamp((t-s)/d);
const smooth=p=>{p=clamp(p);return p*p*(3-2*p);};
const context=vm.createContext({$,attrs,label,fade,move,clamp,progress,smooth,JSON,Math});
vm.runInContext(input.script,context,{timeout:1000});
function serialize(node){return {attrs:node.attrs,text:node.text,tag:node.tag,children:node.children.map(serialize)};}
const snapshots=[];
for(const time of input.times){
 vm.runInContext('effectRender('+JSON.stringify(time)+')',context,{timeout:1000});
 snapshots.push(JSON.parse(JSON.stringify({time,state:vm.runInContext('effectState()',context),
  objects:Object.fromEntries(input.ids.map(id=>[id,{attrs:$(id).attrs,text:$(id).textContent}])),tree:serialize(root)})));
}
process.stdout.write(JSON.stringify(snapshots));
'''


def _evaluate(effect, variant, times):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node runtime required to execute bundled effect scripts')
    recipe = build_recipe(_plan(effect), variant)
    result = subprocess.run([node, '-e', _HARNESS], input=json.dumps(dict(
        nodes=_nodes(recipe['markup']), script=recipe['script'], ids=recipe['object_ids'], times=times)),
        capture_output=True, text=True, encoding='utf-8', timeout=15, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_exact_bundled_effects_and_modules():
    assert {key: value['module'] for key, value in RECIPES.items()} == {
        'condition-branch': 'process', 'parallel-wait': 'process',
        'code-execution': 'process', 'local-replace': 'diagram'}
    for recipe in RECIPES.values():
        assert recipe['title'] and recipe['takeaway'] and recipe['description']
        assert len(recipe['checks']) >= 3


@pytest.mark.parametrize('effect', RECIPES)
def test_finite_geometry_targets_and_bounded_actions(effect):
    recipe = build_recipe(_plan(effect), 'candidate')
    root = ET.fromstring('<svg>'+recipe['markup']+'</svg>')
    by_id = {node.attrib['id']: node for node in root.iter() if 'id' in node.attrib}
    assert len(by_id) == sum('id' in node.attrib for node in root.iter())
    assert len(recipe['object_ids']) == len(set(recipe['object_ids']))
    for ident in recipe['object_ids']:
        target = by_id[ident]
        assert target.tag in {'g', 'circle', 'rect'}
        assert any((node.tag == 'rect' and float(node.attrib.get('width', 0)) > 0
                    and float(node.attrib.get('height', 0)) > 0)
                   or (node.tag == 'circle' and float(node.attrib.get('r', 0)) > 0)
                   or (node.tag == 'path' and node.attrib.get('d')) for node in target.iter())
    for node in root.iter('rect'):
        assert 64 <= float(node.attrib['x']) <= 1216
        assert 110 <= float(node.attrib['y']) <= 620
        assert float(node.attrib['x'])+float(node.attrib['width']) <= 1216
        assert float(node.attrib['y'])+float(node.attrib['height']) <= 620
    assert 1 <= len(recipe['actions']) <= 10
    for action in recipe['actions']:
        assert action['target'] in recipe['object_ids']
        assert 0 <= action['start'] < action['start']+action['duration'] <= 18
    assert not any(term in recipe['script'] for term in (
        'requestAnimationFrame', 'Date.', 'Math.random', 'fetch(', 'XMLHttpRequest', 'setInterval'))


@pytest.mark.parametrize('effect', RECIPES)
def test_variants_preserve_labels_and_do_not_mutate_inputs(effect):
    plan = _plan(effect)
    previous = deepcopy(plan)
    baseline = build_recipe(plan, 'baseline')
    candidate = build_recipe(plan, 'candidate')
    assert plan == previous
    assert baseline['markup'] == candidate['markup']
    assert baseline['script'] != candidate['script']
    assert baseline['actions'] == candidate['actions']
    assert baseline['object_ids'] == candidate['object_ids']


@pytest.mark.parametrize('effect', RECIPES)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_every_action_changes_its_real_target_text_or_attributes(effect, variant):
    recipe = build_recipe(_plan(effect), variant)
    times = [time for row in recipe['actions']
             for time in (max(0, row['start']-.01), row['start']+row['duration']+.01)]
    snapshots = _evaluate(effect, variant, times)
    for index, action in enumerate(recipe['actions']):
        before, after = snapshots[index*2:index*2+2]
        ident = action['target']
        assert before['objects'][ident] != after['objects'][ident], (effect, variant, action)


@pytest.mark.parametrize('effect', RECIPES)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_nonmonotonic_seeks_reconstruct_all_mutated_dom_and_semantics(effect, variant):
    snapshots = _evaluate(effect, variant, [0, 18, 3.1, 14.8, 7.4, 0])
    assert snapshots[0]['tree'] == snapshots[-1]['tree']
    assert snapshots[0]['state'] == snapshots[-1]['state']
    assert snapshots[0]['objects'] == snapshots[-1]['objects']


@pytest.mark.parametrize('effect', RECIPES)
def test_variants_reach_identical_semantic_endpoints(effect):
    times = [0, 3, 9, 14, 18]
    baseline, candidate = (_evaluate(effect, variant, times) for variant in ('baseline', 'candidate'))
    # Positions may differ during easing; stable endpoint state must remain equal.
    assert baseline[0]['state'] == candidate[0]['state']
    assert baseline[-1]['state'] == candidate[-1]['state']


def test_branch_routes_both_request_identities_then_merges():
    frames = _evaluate('condition-branch', 'candidate', [0, 6, 9, 13, 18])
    assert frames[0]['state']['completed'] == 0
    assert frames[1]['state']['phase'] == 'normal-route'
    assert frames[2]['state']['completed'] == 1
    assert frames[3]['state']['phase'] == 'priority-route'
    assert frames[3]['state']['completed'] == 1
    assert frames[-1]['state']['completed'] == 2
    assert [(row['id'], row['route'], row['done']) for row in frames[-1]['state']['requests']] == [
        ('A', 'normal', True), ('B', 'priority', True)]


@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_branch_packets_follow_the_drawn_orthogonal_route(variant):
    frames = _evaluate('condition-branch', variant, [5.5, 6, 6.5, 12.5, 13, 13.5])
    for index, frame in enumerate(frames):
        x, y = frame['state']['requests'][0 if index < 3 else 1]['position']
        destination_y = 250 if index < 3 else 540
        assert ((y == 365 and 430 <= x <= 540)
                or (x == 540 and min(365, destination_y) <= y <= max(365, destination_y))
                or (y == destination_y and 540 <= x <= 685))


def test_parallel_join_never_releases_with_missing_results():
    frames = _evaluate('parallel-wait', 'candidate', [2.99, 3, 7, 10, 12.99, 13, 18])
    assert [frame['state']['completed'] for frame in frames] == [0, 0, 1, 2, 2, 3, 3]
    assert [frame['state']['can_continue'] for frame in frames] == [False]*5+[True]*2
    assert all(row['progress'] == 0 for row in frames[1]['state']['tasks'])
    assert all(row['status'] == 'done' for row in frames[-1]['state']['tasks'])


def test_execution_trace_changes_total_only_on_add_and_prints_final_total():
    frames = _evaluate('code-execution', 'candidate', [0, 3, 5, 7, 9, 11, 13, 15, 18])
    assert [(frame['state']['line'], frame['state']['price'], frame['state']['total']) for frame in frames] == [
        (1, None, 0), (2, 12, 0), (3, 12, 12), (2, 8, 12), (3, 8, 20),
        (2, 5, 20), (3, 5, 25), (4, 5, 25), (4, 5, 25)]
    assert all(frame['state']['output'] is None for frame in frames[:7])
    assert frames[-1]['objects']['code-output']['text'].endswith('25')


def test_replacement_preserves_the_surrounding_graph_and_labels_illustrative_metric():
    frames = _evaluate('local-replace', 'candidate', [0, 4, 8, 12, 18])
    for ident in ('replace-source', 'replace-queue', 'replace-storage'):
        assert all(frame['objects'][ident] == frames[0]['objects'][ident] for frame in frames)
        assert all('transform' not in frame['objects'][ident]['attrs'] for frame in frames)
    assert all('transform' not in frame['objects']['replace-module']['attrs'] for frame in frames)
    assert frames[0]['state']['module'] == 'single-lane'
    assert frames[-1]['state']['module'] == 'dual-lane'
    assert frames[-1]['state']['illustrative_capacity'] == 2
    assert '示意' in frames[-1]['objects']['replace-module']['text']


def test_replacement_static_backplate_is_isolated_from_dynamic_text_and_channels():
    recipe = build_recipe(_plan('local-replace'), 'candidate')
    root = ET.fromstring('<svg>'+recipe['markup']+'</svg>')
    direct_children = {node.attrib.get('id'): node for node in root}
    plate, content = (direct_children[key] for key in ('replace-module-backplate', 'replace-module'))
    assert plate.tag == 'rect'
    assert {key: plate.attrib[key] for key in ('x', 'y', 'width', 'height')} == dict(
        x='575', y='235', width='310', height='245')
    assert 'rx' not in plate.attrib and 'ry' not in plate.attrib
    assert plate.attrib['fill'] == '#153349' and plate.attrib['stroke'] == '#7fbef3'
    assert any(node.tag == 'text' for node in content)
    assert {node.attrib['id'] for node in content if node.tag == 'rect'} == {
        'replace-lane-a', 'replace-lane-b'}
    frames = _evaluate('local-replace', 'candidate', [0, 8.5, 18, 0])
    plates = [next(child for child in frame['tree']['children']
                   if child['attrs'].get('id') == 'replace-module-backplate') for frame in frames]
    assert all(plate == plates[0] for plate in plates)


@pytest.mark.parametrize('effect', RECIPES)
def test_connectors_start_hidden_in_actual_dom(effect):
    tree = _evaluate(effect, 'candidate', [0])[0]['tree']
    def paths(node):
        return ([node] if node['tag'] == 'path' else [])+[
            item for child in node['children'] for item in paths(child)]
    connectors = [node for node in paths(tree)
                  if node['attrs'].get('id') and node['attrs'].get('fill') == 'none']
    assert all(float(node['attrs']['opacity']) == 0 for node in connectors)


@pytest.mark.parametrize('variant', [None, 1, [], 'arbitrary'])
def test_unknown_variants_are_rejected(variant):
    with pytest.raises(ValueError, match='animation_lab_logic_recipe_invalid'):
        build_recipe(_plan('condition-branch'), variant)


@pytest.mark.parametrize('mutation', ['labels', 'effect', 'extra', 'title'])
def test_only_exact_finite_plans_are_accepted(mutation):
    plan = _plan('condition-branch')
    if mutation == 'labels':
        plan['labels']['source'] = '<script>arbitrary()</script>'
    else:
        plan[mutation] = 'arbitrary'
    with pytest.raises(ValueError, match='animation_lab_logic_recipe_invalid'):
        build_recipe(plan, 'candidate')
