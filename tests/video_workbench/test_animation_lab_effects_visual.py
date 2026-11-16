"""Recipe contracts and absolute-clock DOM state; no claim about browser pixels."""
from copy import deepcopy
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from video_workbench.animation_lab_effects_visual import RECIPES, build_recipe


def plan_for(effect):
    recipe = RECIPES[effect]
    return deepcopy(dict(schema='video.animation-lab-effect-plan.v1', effect=effect,
                         title=recipe['title'], takeaway=recipe['takeaway'], labels=recipe['labels']))


def dom_tree(element):
    return dict(tag=element.tag, attrs=dict(element.attrib), text=element.text or '',
                children=[dom_tree(child) for child in element])


_NODE_RUNNER = r'''
const input=JSON.parse(require('fs').readFileSync(0,'utf8'));
const vm=require('vm');
const host=`
const variant=input.variant;
const nodes=new Map();
function makeNode(spec){
 const node={tagName:spec.tag,values:{...spec.attrs},ownText:spec.text,
   getAttribute(key){return Object.hasOwn(this.values,key)?this.values[key]:null;},
   setAttribute(key,value){this.values[key]=String(value);}};
 node.children=spec.children.map(makeNode);
 Object.defineProperty(node,'textContent',{
   get(){return this.ownText+this.children.map(n=>n.textContent).join('');},
   set(value){this.ownText=String(value);this.children=[];}});
 if(node.values.id)nodes.set(node.values.id,node);
 return node;
}
const root=makeNode(input.tree);
const $=id=>{const node=nodes.get(id);if(!node)throw Error('missing:'+id);return node;};
const attrs=(id,values)=>{for(const [key,value] of Object.entries(values))$(id).setAttribute(key,value);};
const label=(id,text)=>{$(id).textContent=String(text);};
const fade=(id,opacity)=>attrs(id,{opacity});
const move=(id,x,y)=>attrs(id,{transform:'translate('+x+' '+y+')'});
const clamp=v=>Math.max(0,Math.min(1,v));
const progress=(t,start,duration)=>clamp((t-start)/duration);
const smooth=p=>{p=clamp(p);return p*p*(3-2*p);};
`;
const check=`
function nodeState(node){return {tag:node.tagName,attrs:{...node.values},text:node.textContent,
 children:node.children.map(nodeState)};}
function capture(t){effectRender(t);return {semantic:effectState(),
 nodes:Object.fromEntries([...nodes].map(([id,node])=>[id,nodeState(node)]))};}
JSON.stringify({seeks:input.times.map(capture),
 actions:input.actions.map(row=>({before:capture(row.start),after:capture(row.start+row.duration)}))});
`;
process.stdout.write(vm.runInNewContext(host+input.script+check,{input},{timeout:2000}));
'''


def states_for(recipe, variant, times):
    node = shutil.which('node')
    assert node, 'Node.js is required to verify bundled SVG recipe scripts'
    root = ET.fromstring('<svg>'+recipe['markup']+'</svg>')
    request = dict(tree=dom_tree(root), variant=variant, script=recipe['script'],
                   times=times, actions=recipe['actions'])
    result = subprocess.run([node, '-e', _NODE_RUNNER], input=json.dumps(request),
                            text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('effect', RECIPES)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_each_visual_recipe_has_valid_svg_and_real_timed_targets(effect, variant):
    plan = plan_for(effect)
    original = deepcopy(plan)
    recipe = build_recipe(plan, variant)
    assert plan == original
    assert set(recipe) == {'markup', 'script', 'object_ids', 'actions'}
    root = ET.fromstring('<svg>'+recipe['markup']+'</svg>')
    ids = [node.attrib['id'] for node in root.iter() if 'id' in node.attrib]
    assert len(ids) == len(set(ids))
    assert set(recipe['object_ids']) <= set(ids)
    assert len(recipe['object_ids']) == len(set(recipe['object_ids']))
    assert 1 <= len(recipe['actions']) <= 10
    for row in recipe['actions']:
        assert row['target'] in recipe['object_ids']
        assert 0 <= row['start'] < row['start']+row['duration'] <= 18
    assert 'function effectRender(t)' in recipe['script']
    assert 'function effectState()' in recipe['script']
    assert all(word not in recipe['script'] for word in ('Math.random', 'Date.', 'requestAnimationFrame', 'fetch('))


@pytest.mark.parametrize('effect', RECIPES)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_nonmonotonic_seek_restores_dom_and_every_action_changes_its_target(effect, variant):
    recipe = build_recipe(plan_for(effect), variant)
    result = states_for(recipe, variant, [0, 5, 12, 3, 18, 0])
    assert result['seeks'][0] == result['seeks'][-1]
    for action, states in zip(recipe['actions'], result['actions']):
        target = action['target']
        assert states['before']['nodes'][target] != states['after']['nodes'][target], action
    for snapshot in result['seeks']:
        assert snapshot['semantic']['effect'] == effect
        assert [obj['id'] for obj in snapshot['semantic']['objects']] == recipe['object_ids']


@pytest.mark.parametrize('effect', RECIPES)
@pytest.mark.parametrize('mutate', [
    lambda p: p.update(title='arbitrary title'),
    lambda p: p.update(script='while(true){}'),
    lambda p: p.update(schema='other'),
    lambda p: p['labels'].update(extra='unexpected'),
    lambda p: p.update(takeaway='<script>alert(1)</script>'),
])
def test_fixed_recipe_cannot_become_an_arbitrary_program(effect, mutate):
    plan = plan_for(effect)
    mutate(plan)
    with pytest.raises(ValueError, match='plan_mismatch'):
        build_recipe(plan, 'candidate')


@pytest.mark.parametrize('variant', [None, 1, 'arbitrary-animation'])
def test_invalid_variant_is_rejected(variant):
    with pytest.raises(ValueError, match='recipe_invalid'):
        build_recipe(plan_for('svg-morph'), variant)


@pytest.mark.parametrize('plan', [None, [], {}, {'effect': 'unknown'}, {'effect': 1}])
def test_unknown_or_malformed_recipe_is_rejected(plan):
    with pytest.raises(ValueError):
        build_recipe(plan, 'candidate')


@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_correspondence_paths_are_hidden_until_their_explanatory_stage(variant):
    arch = build_recipe(plan_for('architecture-expand'), variant)
    rows = states_for(arch, variant, [0, 7])['seeks']
    assert float(rows[0]['nodes']['arch-route']['attrs']['opacity']) == 0
    assert float(rows[0]['nodes']['arch-detail']['attrs']['opacity']) == 0
    assert float(rows[1]['nodes']['arch-route']['attrs']['stroke-dashoffset']) == 0
    focus = build_recipe(plan_for('focus-shift'), variant)
    rows = states_for(focus, variant, [0, 7, 11])['seeks']
    for identity in ('focus-route-entry', 'focus-route-worker'):
        assert float(rows[0]['nodes'][identity]['attrs']['opacity']) == 0
        assert float(rows[0]['nodes'][identity]['attrs']['stroke-dashoffset']) == 1
    assert float(rows[1]['nodes']['focus-route-entry']['attrs']['opacity']) == 1
    assert float(rows[2]['nodes']['focus-route-worker']['attrs']['opacity']) == 1
    data = build_recipe(plan_for('data-compare'), variant)
    rows = states_for(data, variant, [0, 12])['seeks']
    assert float(rows[0]['nodes']['data-trend-before']['attrs']['opacity']) == 0
    assert float(rows[0]['nodes']['data-trend']['attrs']['stroke-dashoffset']) == 1
    assert float(rows[1]['nodes']['data-trend-before']['attrs']['opacity']) == .72


@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_data_axes_units_and_values_meet_the_native_minimum_font_size(variant):
    recipe = build_recipe(plan_for('data-compare'), variant)
    root = ET.fromstring('<svg>'+recipe['markup']+'</svg>')
    labels = [node for node in root.iter() if node.tag in {'text', 'tspan'}]
    assert len(labels) >= 16
    assert all(float(node.attrib['font-size']) >= 14 for node in labels)


@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_data_bars_values_and_takeaway_share_the_same_comparison(variant):
    recipe = build_recipe(plan_for('data-compare'), variant)
    final = states_for(recipe, variant, [18])['seeks'][0]['nodes']
    values = {}
    for series in ('before', 'after'):
        values[series] = []
        for key in ('queue', 'work', 'total'):
            value = int(final[f'data-{series}-{key}-value']['text'])
            values[series].append(value)
            assert float(final[f'data-{series}-{key}']['attrs']['height']) == pytest.approx(value*.605)
        assert sum(values[series][:2]) == values[series][2]
    assert values == {'before': [120, 280, 400], 'after': [70, 170, 240]}
    assert (values['before'][2]-values['after'][2])/values['before'][2] == .4
    assert '40%' in final['data-phase']['text']
    # The trend uses one common zero baseline (y=465); its end point must
    # represent the same 400→240 reduction as the bars rather than merely fall.
    ends = {}
    for series, identity in [('before', 'data-trend-before'), ('after', 'data-trend')]:
        ends[series] = float(re.findall(r'-?\d+(?:\.\d+)?', final[identity]['attrs']['d'])[-1])
    assert (465-ends['after'])/(465-ends['before']) == pytest.approx(240/400)


@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_morph_preserves_path_command_structure_across_all_transitions(variant):
    recipe = build_recipe(plan_for('svg-morph'), variant)
    rows = states_for(recipe, variant, [0, 3, 5, 6.5, 8, 10, 11.5, 13, 18])['seeks']
    paths = [row['nodes']['morph-body']['attrs']['d'] for row in rows]
    assert all(re.findall(r'[A-Za-z]', path) == ['M', 'C', 'C', 'C', 'C', 'Z'] for path in paths)
    assert paths[0] != paths[4] != paths[7]
    assert paths[0] == paths[1] == paths[2]
    assert paths[7] == paths[8]
