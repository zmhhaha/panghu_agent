"""Finite cross-domain briefs compiled as genuine seekable SVG or HTML.

No generated requirement can inject executable code. The reviewed registry is
the only input authority; a suite freezes inputs before any explicit capture.
"""
from __future__ import annotations

from copy import deepcopy
import html
import math
import re

from . import animation_lab_scenarios_charts as charts
from . import animation_lab_scenarios_algorithms as algorithms
from . import animation_lab_scenarios_interfaces as interfaces
from . import animation_lab_scenarios_math as mathematics
from . import animation_lab_scenarios_spatial as spatial
from . import animation_lab_scenarios_fields as fields
from .motion import (FPS, GSAP_VERSION, HYPERFRAMES_VERSION, canonical, semantic_digest,
                     sha256, validate_alignment, validate_beats)

PLAN_SCHEMA = 'video.animation-lab-scenario-plan.v1'
COMPILER_VERSION = 'animation-lab-scenario-compiler-1'
FAMILIES = (charts, algorithms, interfaces, mathematics, spatial, fields)
SCENARIOS = {key: row for family in FAMILIES for key, row in family.SCENARIOS.items()}
_ID = re.compile(r'[a-z][a-z0-9-]{0,79}\Z')


def _require(value, code='animation_lab_scenario_invalid'):
    if not value:
        raise ValueError(code)


def example_scenario_plan(identity):
    _require(type(identity) is str and identity in SCENARIOS)
    return deepcopy(dict(schema=PLAN_SCHEMA, scenario_id=identity, **SCENARIOS[identity]))


def validate_scenario_plan(plan):
    _require(type(plan) is dict and type(plan.get('scenario_id')) is str
             and plan['scenario_id'] in SCENARIOS
             and plan == example_scenario_plan(plan['scenario_id']))
    return deepcopy(plan)


def family_for(plan):
    return next(family for family in FAMILIES if plan['scenario_id'] in family.SCENARIOS)


def recipe_for(plan, variant):
    plan = validate_scenario_plan(plan)
    _require(variant in {'baseline', 'candidate'})
    recipe = family_for(plan).build_recipe(plan, variant)
    _require(set(recipe) == {'markup', 'script', 'object_ids', 'actions'})
    _require(all(type(recipe[key]) is str and 0 < len(recipe[key]) <= 250000 for key in ('markup', 'script')))
    ids = recipe['object_ids']
    _require(type(ids) is list and 1 <= len(ids) <= 100 and len(ids) == len(set(ids))
             and all(type(value) is str and _ID.fullmatch(value) for value in ids))
    _require(type(recipe['actions']) is list and 1 <= len(recipe['actions']) <= 16)
    for action in recipe['actions']:
        _require(type(action) is dict and set(action) == {'target', 'kind', 'start', 'duration'}
                 and action['target'] in ids and _ID.fullmatch(action['kind']))
        _require(all(type(action[key]) in (int, float) and math.isfinite(action[key]) for key in ('start', 'duration'))
                 and action['start'] >= 0 and action['duration'] > 0
                 and action['start'] + action['duration'] <= plan['duration_seconds'])
    return recipe


def expected_observations(plan, time):
    return family_for(validate_scenario_plan(plan)).expected_observations(plan, time)


def compile_scenario_project(plan, beats, alignment, *, variant='candidate'):
    plan = validate_scenario_plan(plan)
    beats = validate_beats(beats)
    alignment = validate_alignment(alignment, beats)
    _require(alignment['audio_seconds'] == plan['duration_seconds'], 'animation_lab_scenario_duration_invalid')
    recipe = recipe_for(plan, variant)
    timeline = dict(schema='video.motion-timeline.v1', compiler=COMPILER_VERSION,
        engine='scenario-effects', rendering=plan['engine'], template=plan['scenario_id'], variant=variant,
        fps=FPS, width=1280, height=720, duration_seconds=plan['duration_seconds'],
        plan_sha256=sha256(plan), audio_sha256=alignment['audio_sha256'], semantic_sha256=semantic_digest(beats),
        actions=[dict(row, ordinal=i) for i, row in enumerate(recipe['actions'])],
        object_ids=recipe['object_ids'], deterministic=True)
    stage = ('<svg id="scenario-stage" viewBox="0 0 1280 720" role="img" aria-label="'
             + html.escape(plan['title'], quote=True) + '">' + recipe['markup'] + '</svg>'
             if plan['engine'] == 'svg' else '<section id="scenario-stage" aria-label="界面操作演示">'
             + recipe['markup'] + '</section>')
    if plan['engine'] == 'three':
        stage = stage.replace('<section id="scenario-stage" aria-label="界面操作演示">',
            '<section id="scenario-stage" style="top:100px;height:520px" aria-label="'
            + html.escape(plan['title'], quote=True) + '">')
    substitutions = {'__TITLE__': html.escape(plan['title']), '__TAKEAWAY__': html.escape(plan['takeaway']),
        '__ENGINE__': plan['engine'], '__DURATION__': str(plan['duration_seconds']), '__STAGE__': stage,
        '__SCRIPT__': recipe['script'],
        '__HOST__': _native_host() if plan['engine'] in {'canvas', 'three'} else _HOST,
        '__DATA__': canonical(dict(plan=plan, timeline=timeline)).decode('utf8').replace('<', '\\u003c').replace('&', '\\u0026')}
    page = re.sub('|'.join(map(re.escape, substitutions)), lambda match: substitutions[match.group()], _PAGE)
    files = {'index.html': page.encode('utf8'), 'plan.json': canonical(plan), 'beats.json': canonical(beats),
             'alignment.json': canonical(alignment), 'timeline.json': canonical(timeline)}
    manifest = dict(schema='video.motion-project.v1', compiler=COMPILER_VERSION, engine='scenario-effects',
        rendering=plan['engine'], template=plan['scenario_id'], developer_only=True, production_admitted=False,
        hyperframes=HYPERFRAMES_VERSION, gsap=GSAP_VERSION, width=1280, height=720, fps=FPS, animated=True,
        plan_sha256=sha256(plan), timeline_sha256=sha256(timeline), semantic_sha256=semantic_digest(beats),
        audio_sha256=alignment['audio_sha256'], runtime_fingerprint=None,
        files={name: sha256(raw) for name, raw in sorted(files.items())},
        required_assets=['assets/gsap.min.js', 'assets/NotoSansCJK-Regular.ttc'])
    if plan['engine'] == 'three':
        from .animation_lab_three import THREE_ASSET_SHA256, THREE_VERSION
        manifest.update(engine_assets=dict(THREE_ASSET_SHA256), engine_version=THREE_VERSION)
        manifest['required_assets'] += ['assets/'+name for name in THREE_ASSET_SHA256]
    files['manifest.json'] = canonical(manifest)
    return files


_PAGE = '''<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=1280,height=720"><title>__TITLE__</title>
<script src="assets/gsap.min.js"></script><style>
@font-face{font-family:VideoNoto;src:url('assets/NotoSansCJK-Regular.ttc');font-display:block}
*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#0b1422;color:#eaf3ff;font-family:VideoNoto,sans-serif}
button,input,select{font:inherit}#motion-root{position:relative;width:1280px;height:720px;overflow:hidden}
#scenario-heading{position:absolute;left:48px;top:28px;margin:0;font-size:30px;line-height:1.5;max-width:1184px}
#scenario-takeaway{position:absolute;left:48px;top:642px;max-width:1184px;margin:0;font-size:20px;line-height:1.5;color:#bad5e9}
#scenario-stage{position:absolute;inset:0;width:1280px;height:720px}
[data-engine="html"] #scenario-stage{top:100px;height:520px}svg text{font-family:VideoNoto,sans-serif}
#engine-error{position:absolute;left:48px;top:140px;padding:20px;background:#442332;color:white;font-size:24px;z-index:10}
</style></head><body><main id="motion-root" data-engine="__ENGINE__" data-composition-id="video-motion" data-width="1280" data-height="720" data-start="0" data-duration="__DURATION__" data-fps="24">
<h1 id="scenario-heading">__TITLE__</h1>__STAGE__<p id="scenario-takeaway">__TAKEAWAY__</p><p id="engine-error" hidden></p></main>
<script>'use strict';const data=__DATA__;
(()=>{const plan=data.plan,variant=data.timeline.variant;
const $=id=>{const node=document.getElementById(id);if(!node)throw Error('scenario_object_missing:'+id);return node;};
const attrs=(id,values)=>{for(const [key,value] of Object.entries(values))$(id).setAttribute(key,String(value));};
const label=(id,text)=>{$(id).textContent=String(text);};
const fade=(id,value)=>{const node=$(id);if(node instanceof SVGElement)node.setAttribute('opacity',String(value));else node.style.opacity=String(value);};
const move=(id,x,y)=>{const node=$(id);if(node instanceof SVGElement)node.setAttribute('transform','translate('+x+' '+y+')');else node.style.transform='translate('+x+'px,'+y+'px)';};
const clamp=value=>Math.max(0,Math.min(1,value)),progress=(time,start,duration)=>clamp((time-start)/duration);
const smooth=value=>{value=clamp(value);return value*value*(3-2*value);};
__SCRIPT__
__HOST__
})();</script></body></html>'''

_HOST = '''
const duration=data.timeline.duration_seconds,clock={time:0};let initialized=false,lastTime=0;
function paint(time){lastTime=Math.max(0,Math.min(duration,time));effectRender(lastTime);}
const tl=gsap.timeline({paused:true});tl.to(clock,{time:duration,duration,ease:'none',onUpdate:()=>{if(initialized)paint(clock.time);}},0);
window.__timelines=window.__timelines||{};window.__timelines['video-motion']=tl;
const ready=(async()=>{await document.fonts.load('22px VideoNoto');await document.fonts.ready;
 if(!document.fonts.check('22px VideoNoto'))throw Error('scenario_font_unavailable');
 for(const id of data.timeline.object_ids)$(id);initialized=true;paint(clock.time);})();
ready.catch(error=>{const box=$('engine-error');box.hidden=false;box.textContent='动画预览未就绪：'+error.message;});
const measuredAttributes=['opacity','transform','d','fill','stroke','stroke-dasharray','stroke-dashoffset','width','height','cx','cy','r','x','y','data-state','data-value','data-index','data-slot','aria-selected','aria-invalid','aria-checked','aria-expanded','style','class'];
function opacityOf(node){let value=1;while(node&&node instanceof Element){const s=getComputedStyle(node);value*=Number(s.opacity);if(s.display==='none'||s.visibility==='hidden'||node.hidden)value=0;node=node.parentElement;}return value;}
const boundsOf=node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
function measuredText(){const result=[],walker=document.createTreeWalker($('motion-root'),NodeFilter.SHOW_TEXT);
 while(walker.nextNode()){const n=walker.currentNode,p=n.parentElement;if(!n.textContent.trim()||!p||p.closest('style,script'))continue;
  const range=document.createRange();range.selectNodeContents(n);const r=range.getBoundingClientRect();
  result.push({text:n.textContent.trim(),opacity:opacityOf(p),font_size:parseFloat(getComputedStyle(p).fontSize),bounds:{x:r.x,y:r.y,width:r.width,height:r.height}});}
 for(const n of $('scenario-stage').querySelectorAll('input'))if(n.value)result.push({text:n.value,opacity:opacityOf(n),font_size:parseFloat(getComputedStyle(n).fontSize),bounds:boundsOf(n)});
 return result;}
window.__videoMotion={engine:'scenario-effects',compiler:data.timeline.compiler,ready,
 snapshot:()=>{if(!initialized)throw Error('scenario_not_ready');return {engine:'scenario-effects',context:plan.engine,
  time:lastTime,width:1280,height:720,scenario_id:plan.scenario_id,deterministic:true,
  objects:data.timeline.object_ids.map(id=>{const n=$(id);return {id,tag:n.tagName.toLowerCase(),bounds:boundsOf(n),
   opacity:opacityOf(n),text:n.textContent.trim(),attributes:Object.fromEntries(measuredAttributes.filter(k=>n.hasAttribute(k)).map(k=>[k,n.getAttribute(k)])),
   ...('value' in n?{value:String(n.value)}:{}),...('disabled' in n?{disabled:Boolean(n.disabled)}:{})};}),
  text_boxes:measuredText(),
  semantic:effectState()};}};
'''


def _native_host():
    """Native families wait for initialization and expose measured engine state.

    The ordinary SVG/HTML host remains independent of GPU/context initialization.
    """
    return _HOST.replace(
        'for(const id of data.timeline.object_ids)$(id);initialized=true;paint(clock.time);',
        "if(typeof effectNativeState!=='function')throw Error('scenario_native_observer_missing');"
        "if(plan.engine==='three'&&typeof effectReady!=='function')throw Error('scenario_native_initializer_missing');"
        "if(typeof effectReady==='function')await effectReady();"
        'for(const id of data.timeline.object_ids)$(id);initialized=true;paint(clock.time);').replace(
        'semantic:effectState()};}};',
        'semantic:effectState(),native_state:effectNativeState()};}};')
