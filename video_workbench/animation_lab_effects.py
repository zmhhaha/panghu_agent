"""Fixed explanatory SVG recipes for the local developer animation library.

Only the eight bundled recipes are accepted. This is not an arbitrary SVG or
JavaScript execution API, nor a change to production template admission.
"""
from __future__ import annotations

from copy import deepcopy
import html
import math
import re

from .animation_lab_effects_logic import RECIPES as LOGIC_RECIPES, build_recipe as logic_recipe
from .animation_lab_effects_visual import RECIPES as VISUAL_RECIPES, build_recipe as visual_recipe
from .motion import (FPS, GSAP_VERSION, HYPERFRAMES_VERSION, canonical, semantic_digest,
                     sha256, validate_alignment, validate_beats)


COMPILER_VERSION = 'animation-lab-effects-compiler-1'
PLAN_SCHEMA = 'video.animation-lab-effect-plan.v1'
RECIPES = {**LOGIC_RECIPES, **VISUAL_RECIPES}
EFFECT_IDS = ('condition-branch', 'parallel-wait', 'architecture-expand', 'focus-shift',
              'data-compare', 'code-execution', 'local-replace', 'svg-morph')
_ID = re.compile(r'[a-z][a-z0-9-]{0,63}\Z')


def _require(value, code='animation_lab_effect_recipe_invalid'):
    if not value:
        raise ValueError(code)


def example_effect_plan(effect):
    _require(type(effect) is str and effect in RECIPES)
    recipe = RECIPES[effect]
    return deepcopy(dict(schema=PLAN_SCHEMA, effect=effect, title=recipe['title'],
                         takeaway=recipe['takeaway'], labels=recipe['labels']))


def validate_effect_plan(plan):
    _require(type(plan) is dict and type(plan.get('effect')) is str
             and plan['effect'] in RECIPES and plan == example_effect_plan(plan['effect']))
    return deepcopy(plan)


def recipe_for(plan, variant):
    plan = validate_effect_plan(plan)
    _require(type(variant) is str and variant in {'baseline', 'candidate'})
    recipe = (logic_recipe if plan['effect'] in LOGIC_RECIPES else visual_recipe)(plan, variant)
    _require(type(recipe) is dict and set(recipe) == {'markup', 'script', 'object_ids', 'actions'})
    _require(all(type(recipe[key]) is str and 0 < len(recipe[key]) <= 150000 for key in ('markup', 'script')))
    ids = recipe['object_ids']
    _require(type(ids) is list and 1 <= len(ids) <= 48
             and all(type(value) is str and _ID.fullmatch(value) for value in ids)
             and len(set(ids)) == len(ids))
    actions = recipe['actions']
    _require(type(actions) is list and 1 <= len(actions) <= 10)
    for row in actions:
        _require(type(row) is dict and {'start', 'duration', 'kind', 'target'} <= set(row)
                 and row['target'] in ids and type(row['kind']) is str and _ID.fullmatch(row['kind']))
        for key in ('start', 'duration'):
            _require(type(row[key]) in (int, float) and math.isfinite(row[key]))
        _require(row['start'] >= 0 and row['duration'] > 0 and row['start']+row['duration'] <= 18)
    return recipe


def compile_effect_project(plan, beats, alignment, *, variant='candidate'):
    plan = validate_effect_plan(plan)
    recipe = recipe_for(plan, variant)
    beats = validate_beats(beats)
    alignment = validate_alignment(alignment, beats)
    _require(alignment['audio_seconds'] == 18, 'animation_lab_effect_duration_invalid')
    actions = [dict(row, ordinal=index) for index, row in enumerate(recipe['actions'])]
    timeline = dict(schema='video.motion-timeline.v1', compiler=COMPILER_VERSION,
        engine='svg-effects', template=plan['effect'], variant=variant, fps=FPS,
        width=1280, height=720, duration_seconds=18, plan_sha256=sha256(plan),
        audio_sha256=alignment['audio_sha256'], semantic_sha256=semantic_digest(beats),
        actions=actions, object_ids=recipe['object_ids'], deterministic=True)
    substitutions = dict(__TITLE__=html.escape(plan['title']), __TAKEAWAY__=html.escape(plan['takeaway']),
        __MARKUP__=recipe['markup'], __RECIPE__=recipe['script'], __HOST__=_HOST,
        __DATA__=canonical(dict(plan=plan, timeline=timeline)).decode('utf-8').replace('<', '\\u003c').replace('&', '\\u0026'))
    # Replace once: trusted recipe content is never treated as new placeholders.
    page = re.sub('|'.join(map(re.escape, substitutions)), lambda match: substitutions[match.group()], _PAGE)
    files = {'index.html': page.encode('utf-8'), 'plan.json': canonical(plan), 'beats.json': canonical(beats),
             'alignment.json': canonical(alignment), 'timeline.json': canonical(timeline)}
    manifest = dict(schema='video.motion-project.v1', compiler=COMPILER_VERSION,
        template=plan['effect'], engine='svg-effects', developer_only=True,
        hyperframes=HYPERFRAMES_VERSION, gsap=GSAP_VERSION, width=1280, height=720, fps=FPS,
        animated=True, plan_sha256=sha256(plan), semantic_sha256=semantic_digest(beats),
        timeline_sha256=sha256(timeline), audio_sha256=alignment['audio_sha256'],
        runtime_fingerprint=None, files={name: sha256(raw) for name, raw in sorted(files.items())},
        required_assets=['assets/gsap.min.js', 'assets/NotoSansCJK-Regular.ttc'])
    files['manifest.json'] = canonical(manifest)
    return files


_PAGE = '''<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=1280,height=720"><title>__TITLE__</title>
<script src="assets/gsap.min.js"></script><style>
@font-face{font-family:VideoNoto;src:url('assets/NotoSansCJK-Regular.ttc');font-display:block}
*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#0b1422;color:#edf5ff;font-family:VideoNoto,sans-serif}
#motion-root{position:relative;width:1280px;height:720px;overflow:hidden}svg{display:block;width:1280px;height:720px}
svg text{font-family:VideoNoto,sans-serif}#engine-error{position:absolute;inset:160px 64px auto;background:#442332;color:#fff;padding:24px;font-size:24px}
</style></head><body><main id="motion-root" data-composition-id="video-motion" data-width="1280" data-height="720" data-start="0" data-duration="18" data-fps="24">
<svg id="effect-scene" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" fill="#e4effa" role="img" aria-label="__TITLE__">
<rect width="1280" height="720" fill="#0b1422"/>
<text x="64" y="58" font-size="31" font-weight="700">__TITLE__</text>
<path d="M64 88H1216" stroke="#24384d"/>
__MARKUP__
<text x="64" y="674" font-size="22" fill="#b9cfe2">__TAKEAWAY__</text>
</svg><p id="engine-error" hidden></p></main>
<script>'use strict';const data=__DATA__;
(()=>{const variant=data.timeline.variant,plan=data.plan;
const $=id=>{const value=document.getElementById(id);if(!value)throw Error('effect_object_missing:'+id);return value;};
const attrs=(id,values)=>{for(const [key,value] of Object.entries(values))$(id).setAttribute(key,String(value));};
const label=(id,text)=>{$(id).textContent=String(text);};
const fade=(id,opacity)=>attrs(id,{opacity});
const move=(id,x,y)=>attrs(id,{transform:'translate('+x+' '+y+')'});
const clamp=v=>Math.max(0,Math.min(1,v)),progress=(t,start,duration)=>clamp((t-start)/duration);
const smooth=p=>{p=clamp(p);return p*p*(3-2*p);};
__RECIPE__
__HOST__
})();</script></body></html>'''


_HOST = '''
const clock={time:0};let initialized=false,lastTime=0;
function paint(t){lastTime=Math.max(0,Math.min(18,t));effectRender(lastTime);}
const tl=gsap.timeline({paused:true});
tl.to(clock,{time:18,duration:18,ease:'none',onUpdate:()=>{if(initialized)paint(clock.time);}},0);
window.__timelines=window.__timelines||{};window.__timelines['video-motion']=tl;
const ready=(async()=>{await document.fonts.load('22px VideoNoto');await document.fonts.ready;
 if(!document.fonts.check('22px VideoNoto'))throw Error('effect_font_unavailable');
 for(const id of data.timeline.object_ids)$(id);
 initialized=true;paint(clock.time);
})();
ready.catch(error=>{const box=$('engine-error');box.hidden=false;box.textContent='动画预览未就绪：'+error.message;});
const attributes=['transform','d','fill','stroke','stroke-dasharray','stroke-dashoffset','width','height','cx','cy','r','x','y','data-state'];
function effectiveOpacity(node){let value=1;while(node&&node instanceof SVGElement){const style=getComputedStyle(node);value*=Number(style.opacity);if(style.display==='none'||style.visibility==='hidden')value=0;node=node.parentElement;}return value;}
window.__videoMotion={engine:'svg-effects',compiler:data.timeline.compiler,ready,
 snapshot:()=>{if(!initialized)throw Error('effect_not_ready');return {engine:'svg-effects',context:'svg',
  time:lastTime,width:1280,height:720,effect:plan.effect,deterministic:true,
  objects:data.timeline.object_ids.map(id=>{const node=$(id),box=node.getBoundingClientRect();
   return {id,tag:node.tagName.toLowerCase(),bounds:{x:box.x,y:box.y,width:box.width,height:box.height},
    opacity:effectiveOpacity(node),text:node.textContent.trim(),attributes:Object.fromEntries(attributes.filter(key=>node.hasAttribute(key)).map(key=>[key,node.getAttribute(key)]))};}),
  text_boxes:Array.from($('effect-scene').querySelectorAll('text')).map(node=>{const box=node.getBoundingClientRect();return {
   text:node.textContent.trim(),opacity:effectiveOpacity(node),font_size:parseFloat(getComputedStyle(node).fontSize),
   bounds:{x:box.x,y:box.y,width:box.width,height:box.height}};}),
  semantic:effectState()};}};
'''
