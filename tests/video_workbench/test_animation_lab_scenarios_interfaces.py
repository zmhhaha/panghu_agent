"""Finite HTML interface requirement and clock-state contracts.

The JavaScript harness is a DOM-shaped unit fixture, not native browser or pixel
evidence. Server capture checks must observe actual HTML separately.
"""
from copy import deepcopy
from html.parser import HTMLParser
import json
import re
import shutil
import subprocess

import pytest

from video_workbench.animation_lab_scenarios_interfaces import (
    SCHEMA, SCENARIOS, build_recipe, expected_observations,
)


def plan_for(scenario_id):
    return deepcopy(dict(schema=SCHEMA, scenario_id=scenario_id, **SCENARIOS[scenario_id]))


class Elements(HTMLParser):
    """Minimal HTML tree fixture; production rendering uses the browser DOM."""
    def __init__(self, markup):
        super().__init__()
        self.tree = dict(tag='root', attrs={}, text='', children=[])
        self.stack = [self.tree]
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        node = dict(tag=tag, attrs=dict(attrs), text='', children=[])
        self.stack[-1]['children'].append(node)
        if tag not in {'input', 'br', 'hr', 'img', 'meta', 'link'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if self.stack[-1]['tag'] == tag:
            self.stack.pop()

    def handle_data(self, data):
        self.stack[-1]['text'] += data


_NODE = r'''
const input=JSON.parse(require('fs').readFileSync(0,'utf8'));
const vm=require('vm');
const host=`
const plan=input.plan,variant=input.variant;
const nodes=new Map();
function makeNode(spec){
 const node={tagName:spec.tag,values:{...spec.attrs},ownText:spec.text,style:{},value:spec.attrs.value||'',disabled:Object.hasOwn(spec.attrs,'disabled'),
  getAttribute(k){return Object.hasOwn(this.values,k)?this.values[k]:null;},setAttribute(k,v){this.values[k]=String(v);}};
 node.children=spec.children.map(makeNode);
 Object.defineProperty(node,'textContent',{get(){return this.ownText+this.children.map(c=>c.textContent).join('');},set(v){this.ownText=String(v);this.children=[];}});
 if(node.values.id)nodes.set(node.values.id,node);return node;
}
makeNode(input.tree);
const $=id=>{if(!nodes.has(id))throw Error('missing:'+id);return nodes.get(id);};
const attrs=(id,values)=>{for(const [k,v]of Object.entries(values))$(id).setAttribute(k,v);};
const label=(id,text)=>{$(id).textContent=String(text);};
const fade=(id,opacity)=>{$(id).style.opacity=String(opacity);};
const move=(id,x,y)=>{$(id).style.transform='translate('+x+'px,'+y+'px)';};
const clamp=v=>Math.max(0,Math.min(1,v)),progress=(t,s,d)=>clamp((t-s)/d),smooth=p=>{p=clamp(p);return p*p*(3-2*p);};
`;
const run=`
function capture(t){effectRender(t);return {semantic:effectState(),objects:Object.fromEntries([...nodes].map(([id,n])=>[id,{text:n.textContent,value:n.value,attributes:{...n.values},style:{...n.style}}]))};}
JSON.stringify({seeks:input.times.map(capture),actions:input.actions.map(a=>({before:capture(Math.max(0,a.start-1/24)),after:capture(a.start+a.duration)}))});
`;
process.stdout.write(vm.runInNewContext(host+input.script+run,{input},{timeout:2000}));
'''


def states_for(plan, variant, times):
    node = shutil.which('node')
    assert node, 'Node.js is required for finite interface clock-state checks'
    recipe = build_recipe(plan, variant)
    payload = dict(plan=plan, variant=variant, tree=Elements(recipe['markup']).tree,
                   script=recipe['script'], actions=recipe['actions'], times=times)
    result = subprocess.run([node, '-e', _NODE], input=json.dumps(payload), text=True,
                            encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return recipe, json.loads(result.stdout)


def test_interface_domains_have_distinct_requirements_and_operation_traces():
    assert len(SCENARIOS) == 4
    assert len({r['domain'] for r in SCENARIOS.values()}) == 4
    assert len({tuple(e['operation'] for e in r['data']['events']) for r in SCENARIOS.values()}) == 4
    assert {r['difficulty'] for r in SCENARIOS.values()} == {'basic', 'intermediate', 'boundary'}
    for row in SCENARIOS.values():
        assert row['module'] == 'interface' and row['engine'] == 'html'
        assert len(row['requirement']['questions']) >= 2
        assert len(row['requirement']['required_entities']) >= 4
        assert row['requirement']['expected_outcome'] and len(row['requirement']['boundaries']) >= 2
        assert row['narrative'] and 18 <= row['duration_seconds'] <= 24


@pytest.mark.parametrize('scenario_id', SCENARIOS)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_native_html_targets_have_finite_actions_and_seek_restores_state(scenario_id, variant):
    plan = plan_for(scenario_id)
    times = [0, 3, 8, 13, 19, plan['duration_seconds']-1/24, 0, 8]
    recipe, states = states_for(plan, variant, times)
    assert '<article' in recipe['markup'] and '<svg' not in recipe['markup']
    assert '<form' not in recipe['markup'] and '<iframe' not in recipe['markup']
    assert len(recipe['object_ids']) == len(set(recipe['object_ids']))
    assert {a['target'] for a in recipe['actions']} <= set(recipe['object_ids'])
    assert len(recipe['actions']) == 4
    assert all(a['start'] >= 0 and a['duration'] > 0 and a['start']+a['duration'] <= plan['duration_seconds'] for a in recipe['actions'])
    assert states['seeks'][0] == states['seeks'][-2]
    assert states['seeks'][2] == states['seeks'][-1]
    for action, observed in zip(recipe['actions'], states['actions']):
        assert observed['before']['objects'][action['target']] != observed['after']['objects'][action['target']]
    for time, state in zip(times, states['seeks']):
        for target, expected in expected_observations(plan, time).items():
            actual = state['objects'][target]
            for key, value in expected.items():
                if key == 'attributes':
                    assert {name: actual['attributes'].get(name) for name in value} == value
                else:
                    assert actual[key] == value
    # Inputs remain demonstrations; recipe code has no network or wall clock.
    assert not re.search(r'\b(fetch|XMLHttpRequest|WebSocket|requestAnimationFrame|setInterval|Date)\b', recipe['script'])


@pytest.mark.parametrize('scenario_id', SCENARIOS)
def test_ab_preserves_event_semantics_and_changes_only_visual_emphasis(scenario_id):
    plan = plan_for(scenario_id)
    _, a = states_for(plan, 'baseline', [0, 8, 19])
    _, b = states_for(plan, 'candidate', [0, 8, 19])
    assert [r['semantic'] for r in a['seeks']] == [r['semantic'] for r in b['seeks']]
    assert a['seeks'][1]['objects']['ui-step-1']['style'] != b['seeks'][1]['objects']['ui-step-1']['style']


@pytest.mark.parametrize('scenario_id', SCENARIOS)
def test_registered_requirement_cannot_be_mutated_or_execute_supplied_content(scenario_id):
    original = plan_for(scenario_id)
    for key, value in [('title', '<script>bad()</script>'), ('duration_seconds', 999), ('engine', 'webgl')]:
        changed = deepcopy(original)
        changed[key] = value
        with pytest.raises(ValueError, match='scenario_invalid'):
            build_recipe(changed, 'candidate')
    changed = deepcopy(original)
    changed['data']['events'][0]['target'] = 'unregistered-target'
    with pytest.raises(ValueError, match='scenario_invalid'):
        build_recipe(changed, 'candidate')
    with pytest.raises(ValueError, match='scenario_invalid'):
        build_recipe(original, 'custom')
    assert original == plan_for(scenario_id)


def test_filter_results_are_recomputed_from_two_conditions_and_sorted():
    d = SCENARIOS['interface-shop-filter']['data']
    eligible = sorted((p for p in d['products'] if p['wireless'] and p['price'] <= d['limit']), key=lambda p: p['price'])
    assert [p['id'] for p in eligible] == d['final_ids']
    assert [p['price'] for p in eligible] == [159, 249]


def test_acknowledgement_preserves_alarm_until_later_measured_recovery():
    plan = plan_for('interface-plant-alarm')
    assert plan['data']['initial_temperature'] > plan['data']['threshold'] > plan['data']['restored_temperature']
    early = expected_observations(plan, 8)
    assert early['plant-ack']['text'] == '已确认 · 已有人接手'
    assert early['plant-temperature']['text'] == '86 °C'
    assert early['plant-reading']['attributes']['data-state'] == 'above-threshold'
    assert expected_observations(plan, 19)['plant-temperature']['text'] == '74 °C'


@pytest.mark.parametrize('bad_time', [-1, float('nan'), float('inf'), 100])
def test_observation_expectations_reject_invalid_clock(bad_time):
    with pytest.raises(ValueError, match='time_invalid'):
        expected_observations(plan_for('interface-library-search'), bad_time)
