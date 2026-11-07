"""Finite Three.js projects for the developer lab, with an absolute seek clock.

This module does not grant the compiler production admission. Third-party bytes
are served locally by the lab and measured separately from generated projects.
"""
from __future__ import annotations

from copy import deepcopy
import html
import math
from pathlib import Path
import re

from .motion import (FPS, GSAP_VERSION, HYPERFRAMES_VERSION, canonical,
                     semantic_digest, sha256, validate_alignment, validate_beats)


THREE_VERSION = '0.170.0'
THREE_REVISION = '170'
COMPILER_VERSION = 'animation-lab-three-compiler-1'
# Measured from the integrity-verified official three@0.170.0 package.
THREE_ASSET_SHA256 = {'three.module.min.js': '08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7'}
ENGINE_ASSETS = THREE_ASSET_SHA256
SCHEMA = 'video.animation-lab-three-plan.v1'
_ID = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')
_COLOR = re.compile(r'#[0-9a-fA-F]{6}\Z')


def _require(condition, code='animation_lab_three_plan_invalid'):
    if not condition:
        raise ValueError(code)


def _text(value, maximum):
    _require(type(value) is str and 0 < len(value) <= maximum and bool(value.strip())
             and not any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value))
    return value


def validate_three_plan(plan):
    _require(type(plan) is dict and set(plan) == {'schema', 'title', 'takeaway', 'layers'}
             and plan['schema'] == SCHEMA)
    _text(plan['title'], 50)
    _text(plan['takeaway'], 100)
    layers = plan['layers']
    _require(type(layers) is list and 3 <= len(layers) <= 5)
    ids = set()
    for row in layers:
        _require(type(row) is dict and set(row) == {'id', 'label', 'detail', 'color'}
                 and type(row['id']) is str and _ID.fullmatch(row['id']) is not None
                 and row['id'] not in ids
                 and type(row['color']) is str and _COLOR.fullmatch(row['color']) is not None)
        _text(row['label'], 18)
        _text(row['detail'], 48)
        ids.add(row['id'])
    return deepcopy(plan)


def example_three_plan():
    return dict(schema=SCHEMA, title='一个服务，为什么分成三层？',
        takeaway='分层让职责清楚；组合起来，才是完整服务。',
        layers=[dict(id='interface', label='交互层', detail='接收请求，展示结果', color='#68d5bc'),
                dict(id='logic', label='业务层', detail='执行规则，协调任务', color='#75aef2'),
                dict(id='storage', label='数据层', detail='持久保存，可靠读取', color='#d7a7f1')])


def compile_three_project(plan, beats, alignment, *, variant='candidate'):
    plan = validate_three_plan(plan)
    beats = validate_beats(beats)
    alignment = validate_alignment(alignment, beats)
    _require(type(variant) is str and variant in {'baseline', 'candidate'}, 'animation_lab_three_variant_invalid')
    duration = math.ceil(alignment['audio_seconds'] * FPS) / FPS
    _require(8 <= duration <= 60, 'animation_lab_three_duration_invalid')
    # Both versions show the same layers and explosion; only the candidate adds
    # an explanatory camera move. Every declared action changes actual state.
    actions = [dict(kind='move', target='layers', start=duration*.12, duration=duration*.18)]
    if variant == 'candidate':
        actions.append(dict(kind='move', target='camera', start=duration*.32, duration=duration*.4))
    actions.append(dict(kind='move', target='layers', start=duration*.8, duration=duration*.16))
    for ordinal, action in enumerate(actions):
        action['ordinal'] = ordinal
    timeline = dict(schema='video.motion-timeline.v1', compiler=COMPILER_VERSION,
        engine='three', template='layered-system', variant=variant, width=1280, height=720, fps=FPS,
        duration_seconds=duration, audio_seconds=alignment['audio_seconds'],
        plan_sha256=sha256(plan), audio_sha256=alignment['audio_sha256'],
        beats_sha256=sha256(beats), alignment_sha256=sha256(alignment),
        actions=actions, alignment_method=alignment['method'], review_status=alignment['review_status'])
    payload = canonical(dict(plan=plan, timeline=timeline, variant=variant)).decode('utf-8')
    payload = payload.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    payload = payload.replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    source = Path(__file__).with_name('animation_lab_engines').joinpath('three.mjs').read_text(encoding='utf-8')
    source = source.replace('__PAYLOAD__', payload)
    cards = ''.join('<article class="layer-key" data-layer="'+html.escape(row['id'], quote=True)+'">'
        '<span class="swatch" style="background:'+row['color']+'"></span><div><strong>'
        +html.escape(row['label'])+'</strong><p>'+html.escape(row['detail'])+'</p></div></article>'
        for row in plan['layers'])
    replacements = dict(__TITLE__=html.escape(plan['title']), __TAKEAWAY__=html.escape(plan['takeaway']),
                        __DURATION__=str(duration), __CARDS__=cards, __ENGINE__=source)
    page = re.sub('|'.join(map(re.escape, replacements)), lambda match: replacements[match.group()], _PAGE)
    files = {'index.html': page.encode('utf-8'), 'plan.json': canonical(plan), 'beats.json': canonical(beats),
             'alignment.json': canonical(alignment), 'timeline.json': canonical(timeline)}
    assets = dict(THREE_ASSET_SHA256)
    manifest = dict(schema='video.motion-project.v1', compiler=COMPILER_VERSION, engine='three',
        template='layered-system', hyperframes=HYPERFRAMES_VERSION, gsap=GSAP_VERSION,
        width=1280, height=720, fps=FPS, animated=True, plan_sha256=sha256(plan),
        semantic_sha256=semantic_digest(beats), timeline_sha256=sha256(timeline),
        audio_sha256=alignment['audio_sha256'], files={name: sha256(raw) for name, raw in sorted(files.items())},
        engine_version=THREE_VERSION, engine_assets=assets,
        required_assets=['assets/gsap.min.js', 'assets/NotoSansCJK-Regular.ttc']+['assets/'+name for name in assets],
        scope='developer_sample_not_production_admitted')
    files['manifest.json'] = canonical(manifest)
    return files


_PAGE = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=1280,height=720">
<title>Three.js · __TITLE__</title><script src="assets/gsap.min.js"></script><style>
@font-face{font-family:VideoNoto;src:url('assets/NotoSansCJK-Regular.ttc');font-weight:100 900;font-display:block}
*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#0c1523;color:#eef4fc;font-family:VideoNoto,sans-serif}
#motion-root{position:relative;width:1280px;height:720px;overflow:hidden}canvas{position:absolute;inset:0;width:1280px;height:720px}
header{position:absolute;left:62px;top:38px;z-index:1}.eyebrow{font-size:13px;letter-spacing:2px;color:#79d5c2}h1{margin:12px 0 0;font-size:32px;font-weight:600;max-width:1120px}
#keys{position:absolute;right:58px;top:224px;width:238px;z-index:1}.layer-key{display:flex;gap:12px;padding:16px 14px;margin:10px 0;border:1px solid #304055;border-radius:12px;background:#101e31}
.layer-key.active{border-color:#8dd7c5;background:#152a38}.swatch{width:10px;height:10px;border-radius:3px;margin-top:7px;flex:none}.layer-key strong{font-size:19px;font-weight:500}.layer-key p{font-size:13px;line-height:1.6;margin:7px 0 0;color:#b4c4d9}
#phase{position:absolute;left:64px;top:580px;color:#a5dce3;font-size:18px;z-index:1}.takeaway{position:absolute;left:64px;bottom:34px;font-size:19px;color:#d8e8f4;z-index:1}#engine-error{position:absolute;inset:180px 80px;background:#301c28;border:1px solid #b46f86;border-radius:14px;padding:32px;z-index:5;color:#ffd9e4;white-space:pre-wrap}
</style></head><body><main id="motion-root" data-composition-id="video-motion" data-width="1280" data-height="720" data-start="0" data-duration="__DURATION__" data-fps="24">
<canvas id="three-scene" width="1280" height="720" aria-label="服务分层的真实三维拆解动画"></canvas><header><div class="eyebrow">THREE.JS / LAYERED SYSTEM</div><h1>__TITLE__</h1></header><aside id="keys">__CARDS__</aside><p id="phase">完整服务：各层共同工作</p><p class="takeaway">__TAKEAWAY__</p><p id="engine-error" hidden></p></main>
<script>
window.__videoMotion={engine:'three',error:null};
window.__videoMotion.ready=new Promise((resolve,reject)=>{window.__videoMotion.resolve=resolve;window.__videoMotion.reject=reject;});
window.__videoMotion.ready.catch(()=>{});
window.__videoMotion.fail=(message)=>{window.__videoMotion.error=String(message);const e=document.getElementById('engine-error');e.hidden=false;e.textContent='Three.js 初始化失败：'+message;window.__videoMotion.reject(new Error(message));};
window.addEventListener('error',event=>window.__videoMotion.fail(event.message||'本地 Three.js 资源未能加载'));
</script><script type="module">__ENGINE__</script></body></html>'''
