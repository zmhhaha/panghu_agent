"""Opt-in, controlled demonstration compiler for the local motion experiment.

The published Agent and the three diagram templates retain their own admission
path. This compiler accepts bounded data, never executable model output, and
uses the existing spoken-text alignment without changing the recording.
"""
from __future__ import annotations

import html
import math
import re

from agent_service.profiles.video_motion_contract import (
    DEMONSTRATION_TEMPLATES, validate_beats, validate_motion_plan,
)
from .motion import (
    FPS, GSAP_VERSION, HEIGHT, HYPERFRAMES_VERSION, WIDTH, _number, _require,
    _safe_script_json, canonical, semantic_digest, sha256, validate_alignment,
)


DEMO_COMPILER_VERSION = 'motion-demo-compiler-1'
DEMO_TEMPLATE_VERSION = 'record-demonstration-1'

# Same-kind movable slots have exactly the same size. No coordinate, scale,
# camera, CSS or timing can be supplied by a plan.
DEMO_LAYOUT = {
    'log_center': (380, 204, 520, 380),
    'log_left': (72, 204, 520, 380),
    'context_right': (688, 204, 520, 380),
    'answer_center': (420, 286, 440, 88),
    'answer_left': (112, 286, 440, 88),
    'answer_right': (728, 286, 440, 88),
    'attempt_center': (420, 404, 440, 88),
    'attempt_left': (112, 404, 440, 88),
    'result_left': (72, 600, 520, 64),
    'result_right': (688, 600, 520, 64),
}


def resolve_demo_timeline(plan, beats, alignment, *, leading_seconds=0, trailing_seconds=0):
    """Resolve only the explicit demonstration contract against this audio."""
    beats = validate_beats(beats)
    plan = validate_motion_plan(plan, beats, allowed_templates=DEMONSTRATION_TEMPLATES)
    alignment = validate_alignment(alignment, beats)
    leading = _number(leading_seconds, 0, 10)
    trailing = _number(trailing_seconds, 0, 10)
    anchors = {row['beat_id']: row for row in alignment['segments']}
    actions = []
    for ordinal, action in enumerate(plan['actions']):
        anchor = anchors[action['beat_id']]
        span = anchor['end'] - anchor['start']
        start = leading + anchor['start'] + {'start': .03, 'middle': .38, 'end': .72}[action['placement']] * span
        duration = min({'quick': .35, 'normal': .65, 'slow': 1.0}[action['pace']], .24 * span)
        _require(duration >= 2 / FPS, 'motion_beat_too_short_for_animation')
        actions.append(dict(action, ordinal=ordinal, start=round(start, 6),
                            duration=round(duration, 6), until=leading + anchor['end']))
    actions.sort(key=lambda row: (row['start'], row['ordinal']))
    ends = {}
    for row in actions:
        key = row['target'], 'position' if row['kind'] == 'move' else 'opacity'
        _require(row['start'] >= ends.get(key, -1), 'conflicting_motion_actions')
        ends[key] = row['start'] + row['duration']
    return dict(schema='video.motion-timeline.v1', compiler=DEMO_COMPILER_VERSION,
                plan_sha256=sha256(plan), semantic_sha256=semantic_digest(beats),
                alignment_sha256=sha256(alignment), audio_sha256=alignment['audio_sha256'],
                spoken_sha256=alignment['spoken_sha256'], audio_seconds=alignment['audio_seconds'],
                leading_seconds=leading, trailing_seconds=trailing,
                duration_seconds=math.ceil((leading + alignment['audio_seconds'] + trailing) * FPS) / FPS,
                alignment_method=alignment['method'], review_status=alignment['review_status'],
                beats=[dict(row, start=leading + row['start'], end=leading + row['end'])
                       for row in alignment['segments']], actions=actions)


def _symbol(kind):
    if kind == 'record':
        return ('<span class="record-symbol" aria-hidden="true"><svg viewBox="0 0 32 32">'
                '<path d="M8 16 L14 22 L25 10"/></svg></span>')
    if kind == 'attempt':
        return ('<span class="record-symbol" aria-hidden="true"><svg viewBox="0 0 32 32">'
                '<path d="M16 7 L16 18 M16 24 L16 25"/></svg></span>')
    if kind == 'label':
        return '<span class="result-dot" aria-hidden="true"></span>'
    return ''


def compile_demo_project(plan, beats, alignment, *, animated=True, runtime_fingerprint=None,
                         leading_seconds=0, trailing_seconds=0):
    """Return a deterministic project; the renderer supplies local assets.

    Document and record layers move together through named slots. A fixture can
    reveal a second, identically labelled record and move that copy while the
    original stays in the log. No special copy code is admitted from the plan.
    """
    beats = validate_beats(beats)
    plan = validate_motion_plan(plan, beats, allowed_templates=DEMONSTRATION_TEMPLATES)
    alignment = validate_alignment(alignment, beats)
    _require(type(animated) is bool, 'invalid_motion_options')
    timeline = resolve_demo_timeline(plan, beats, alignment, leading_seconds=leading_seconds,
                                    trailing_seconds=trailing_seconds)
    boxes = {row['id']: DEMO_LAYOUT[row['slot']] for row in plan['elements']}
    markup = []
    for row in plan['elements']:
        x, y, w, h = boxes[row['id']]
        # An absent detail has no box. In particular, hiding an empty detail
        # would give it a (0, 0) client rect and spuriously fail the strict
        # descendant bounds check for a result label lower on the canvas.
        detail = (f'<div class="element-detail">{html.escape(row["detail"])}</div>'
                  if row['detail'].strip() else '')
        markup.append(
            f'<div id="el-{row["id"]}" class="element {row["kind"]} slot-{row["slot"]}" '
            f'data-motion-element="{row["id"]}" '
            f'style="left:{x}px;top:{y}px;width:{w}px;height:{h}px">'
            f'{_symbol(row["kind"])}<div class="element-copy">'
            f'<div class="element-label">{html.escape(row["label"])}</div>'
            f'{detail}</div></div>')
    payload = dict(plan=plan, timeline=timeline, boxes=boxes, layout=DEMO_LAYOUT, animated=animated)
    page = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=1280,height=720"><title>会话记录演示</title>
<script src="assets/gsap.min.js"></script><style>
@font-face{font-family:VideoNoto;src:url('assets/NotoSansCJK-Regular.ttc');font-weight:100 900;font-display:block}
*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#0c1523;color:#edf2f7;font-family:VideoNoto,sans-serif}
#motion-root{position:relative;width:1280px;height:720px;overflow:hidden;background:radial-gradient(ellipse at 50% 0%,#23364b 0%,#0c1523 75%)}
.eyebrow{position:absolute;left:72px;top:43px;color:#95c7c6;font-size:16px;line-height:1.5;letter-spacing:3px}
h1{position:absolute;left:72px;top:82px;margin:0;width:1136px;padding-bottom:4px;font-size:36px;font-weight:700;line-height:1.5;overflow-wrap:anywhere}
.takeaway{position:absolute;left:72px;top:672px;width:1136px;padding:2px 0;min-height:32px;max-height:36px;font-size:17px;line-height:1.6;color:#a9b8c9;overflow:hidden;overflow-wrap:anywhere}
.element{position:absolute;overflow:hidden;box-sizing:border-box}
.element-copy{min-width:0;width:100%}.element-label,.element-detail{overflow-wrap:anywhere}
.document,.terminal{z-index:10;border-radius:14px;border:2px solid;box-shadow:0 18px 44px #0004;padding:16px 28px}
.document{border-color:#d6cebc;background:#f7f2e7;color:#344134;background-image:repeating-linear-gradient(to bottom,transparent 0px,transparent 57px,#dcd6c73d 58px,#dcd6c73d 59px)}
.document:before{content:'';position:absolute;left:22px;top:78px;bottom:22px;width:1px;background:#ddbab0}
.document:after{content:'';position:absolute;right:0;bottom:0;width:28px;height:28px;background:linear-gradient(135deg,#e3dbc9 0%,#e3dbc9 49%,#172332 50%,#172332 100%)}
.terminal{border-color:#526781;background:#182b42;color:#eef5ff}
.document>.element-copy,.terminal>.element-copy{display:flex;align-items:center;justify-content:space-between;gap:14px}
.document .element-label,.terminal .element-label{font-size:28px;font-weight:700;line-height:1.5;flex-shrink:0}
.document .element-detail,.terminal .element-detail{font-size:16px;line-height:1.5;text-align:right}
.document .element-detail{color:#687568}.terminal .element-detail{color:#afc3da}
.terminal:after{content:'';position:absolute;left:28px;right:28px;top:66px;height:1px;background:#58718c66}
.record,.attempt{z-index:20;display:flex;align-items:center;gap:16px;padding:12px 18px;border:2px solid;border-radius:12px;box-shadow:0 7px 16px #0002}
.record{border-color:#84bd96;background:#e4f3e7;color:#24583a}.attempt{border-color:#d7a36b;background:#ffebd5;color:#8a491d}
.record-symbol{width:42px;height:42px;flex:0 0 42px;border-radius:50%;display:flex;align-items:center;justify-content:center}
.record .record-symbol{background:#badfc6}.attempt .record-symbol{background:#f5c994}
.record-symbol svg{width:30px;height:30px;fill:none;stroke:currentColor;stroke-width:3;stroke-linecap:round;stroke-linejoin:round}
.record .element-label,.attempt .element-label{font-size:24px;font-weight:700;line-height:1.5}
.record .element-detail,.attempt .element-detail{font-size:16px;line-height:1.5}
.record .element-detail{color:#4b7659}.attempt .element-detail{color:#9b6033}
.label{z-index:30;display:flex;align-items:center;gap:14px;padding:8px 14px;color:#e5e9ed}
.label .element-label{font-size:26px;font-weight:700;line-height:1.5}.label .element-detail{font-size:16px;line-height:1.5;color:#b2c3d2}
.result-dot{width:10px;height:10px;border-radius:50%;flex:0 0 10px;background:#dec69d}.slot-result_right .result-dot{background:#8adca6}.slot-result_right{color:#a6edbd}
#progress{position:absolute;left:0;bottom:0;height:3px;width:1280px;background:#83c5b0;transform-origin:left}
</style></head><body><div id="motion-root" data-composition-id="video-motion" data-width="1280" data-height="720" data-start="0" data-duration="__DURATION__" data-fps="24">
<div class="eyebrow">会话机制 · 过程演示</div><h1>__TITLE__</h1>
__ELEMENTS__<div class="takeaway">__TAKEAWAY__</div><div id="progress"></div>
</div><script>
'use strict';
(() => {
  const data = __PAYLOAD__;
  const tl = gsap.timeline({paused:true, defaults:{ease:'power2.inOut'}});
  const node=id=>document.getElementById('el-'+id);
  const firstVisibility=new Map();
  for(const a of data.timeline.actions) if((a.kind==='reveal'||a.kind==='hide')&&!firstVisibility.has(a.target)) firstVisibility.set(a.target,a.kind);
  const hidden=new Set(Array.from(firstVisibility.entries()).filter(([id,kind])=>kind==='reveal').map(([id])=>id));
  for(const id of hidden) gsap.set(node(id),{opacity:0});
  const position=Object.fromEntries(Object.keys(data.boxes).map(id=>[id,{x:0,y:0}]));
  const opacity=Object.fromEntries(Object.keys(data.boxes).map(id=>[id,hidden.has(id)?0:1]));
  for(const a of data.timeline.actions) {
    const when=data.animated?a.start:0,duration=data.animated?a.duration:0;
    if(a.kind==='reveal'||a.kind==='hide') {
      const next=a.kind==='reveal'?1:0;
      tl.fromTo(node(a.target),{opacity:opacity[a.target]},{opacity:next,duration,immediateRender:false},when);
      opacity[a.target]=next;
    } else if(a.kind==='move') {
      const base=data.boxes[a.target],dest=data.layout[a.to_slot];
      const next={x:dest[0]-base[0],y:dest[1]-base[1]};
      tl.fromTo(node(a.target),position[a.target],{...next,duration,immediateRender:false},when);
      position[a.target]=next;
    }
  }
  tl.fromTo('#progress',{scaleX:0},{scaleX:1,duration:data.timeline.duration_seconds,ease:'none',immediateRender:true},0);
  tl.set({}, {}, data.timeline.duration_seconds);
  window.__timelines=window.__timelines||{};
  window.__timelines['video-motion']=tl;
  const metrics=el=>{
    const style=getComputedStyle(el),r=el.getBoundingClientRect();
    const descendants=Array.from(el.querySelectorAll('.element-label,.element-detail'));
    return {opacity:Number(style.opacity),x:r.x,y:r.y,width:r.width,height:r.height,
      borderColor:style.borderColor,backgroundColor:style.backgroundColor,
      scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,scrollHeight:el.scrollHeight,clientHeight:el.clientHeight,
      overflow:el.scrollWidth>el.clientWidth||el.scrollHeight>el.clientHeight||descendants.some(child=>{
        const c=child.getBoundingClientRect();
        return child.scrollWidth>child.clientWidth||child.scrollHeight>child.clientHeight||c.x<r.x||c.y<r.y||c.right>r.right||c.bottom>r.bottom;
      })};
  };
  window.__videoMotion={version:'motion-demo-compiler-1',planSha256:data.timeline.plan_sha256,
    timelineSha256:'__TIMELINE_SHA__',duration:data.timeline.duration_seconds,
    inspect:()=>Object.fromEntries(Object.keys(data.boxes).map(id=>[id,metrics(node(id))])),
    inspectRelations:()=>({}),
    inspectLayout:()=>({title:metrics(document.querySelector('h1')),takeaway:metrics(document.querySelector('.takeaway')),
      relations:[],fontReady:document.fonts.status==='loaded'&&document.fonts.check('20px VideoNoto')}),
    ready:document.fonts.ready.then(()=>true)};
  tl.seek(0,false);
})();
</script></body></html>'''
    replacements = {
        '__DURATION__': str(timeline['duration_seconds']),
        '__TITLE__': html.escape(plan['title']), '__TAKEAWAY__': html.escape(plan['takeaway']),
        '__ELEMENTS__': ''.join(markup), '__PAYLOAD__': _safe_script_json(payload),
        '__TIMELINE_SHA__': sha256(timeline),
    }
    page = re.sub('|'.join(map(re.escape, replacements)), lambda m: replacements[m.group()], page)
    files = {'index.html': page.encode('utf-8'), 'plan.json': canonical(plan),
             'beats.json': canonical(beats), 'alignment.json': canonical(alignment),
             'timeline.json': canonical(timeline)}
    manifest = dict(schema='video.motion-project.v1', compiler=DEMO_COMPILER_VERSION,
                    template=DEMO_TEMPLATE_VERSION, hyperframes=HYPERFRAMES_VERSION, gsap=GSAP_VERSION,
                    width=WIDTH, height=HEIGHT, fps=FPS, animated=animated,
                    plan_sha256=sha256(plan), semantic_sha256=semantic_digest(beats),
                    timeline_sha256=sha256(timeline), audio_sha256=alignment['audio_sha256'],
                    runtime_fingerprint=runtime_fingerprint,
                    files={name: sha256(data) for name, data in sorted(files.items())},
                    required_assets=['assets/gsap.min.js', 'assets/NotoSansCJK-Regular.ttc'])
    files['manifest.json'] = canonical(manifest)
    return files
