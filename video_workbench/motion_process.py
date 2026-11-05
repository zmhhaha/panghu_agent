"""Local opt-in process animation: compiler-owned paths and visible states.

No profile registration, production admission, browser, network or TTS calls.
Old motion/demo/continuity compilers are not modified or reinterpreted here.
"""
from __future__ import annotations

from copy import deepcopy
import html
import math
import re

from agent_service.profiles.video_process_contract import (
    STATE_TRANSITIONS, validate_process_plan,
)
from agent_service.profiles.video_motion_contract import validate_beats
from .motion import (
    FPS, GSAP_VERSION, HEIGHT, HYPERFRAMES_VERSION, WIDTH,
    _number, _safe_script_json, canonical, semantic_digest, sha256, validate_alignment,
)
from agent_service.contracts import ServiceError


PROCESS_COMPILER_VERSION = 'motion-process-compiler-1'
PROCESS_TEMPLATE_VERSION = 'controlled-process-1'
PROCESS_LAYOUT = {
    'source': (64, 452, 320, 188), 'queue': (480, 452, 320, 188),
    'worker': (896, 452, 320, 188),
    'token_source': (156, 306, 136, 44), 'token_queue': (572, 306, 136, 44),
    'token_worker': (988, 306, 136, 44),
}
STATE_PRESENTATIONS = {
    'idle': dict(label='待命', symbol='circle', path='M12 3 A9 9 0 1 1 11.99 3 Z', color='#a8b7cc'),
    'waiting': dict(label='等待', symbol='queue', path='M4 6 H20 M4 12 H20 M4 18 H20', color='#e5c875'),
    'running': dict(label='处理中', symbol='triangle', path='M7 3 L20 12 L7 21 Z', color='#72d9e2'),
    'completed': dict(label='完成', symbol='check', path='M4 12 L10 18 L21 5', color='#8ae0a3'),
    'failed': dict(label='失败', symbol='cross', path='M5 5 L19 19 M19 5 L5 19', color='#f49b92'),
}
_ROUTE_SPECS = {
    ('source', 'queue'): ([[224, 328], [224, 240], [640, 240], [640, 328]], [312, 180, 240, 28]),
    ('queue', 'worker'): ([[640, 328], [640, 280], [1056, 280], [1056, 328]], [728, 220, 240, 28]),
    ('worker', 'queue'): ([[1056, 328], [1056, 384], [640, 384], [640, 328]], [728, 416, 240, 28]),
}


def _require(value, code='invalid_process_timeline'):
    if not value:
        raise ServiceError(code, 422)


def _intersects(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def process_paths(plan):
    """Return fixed center-line paths; never accept coordinates from a plan."""
    by_id = {row['id']: row for row in plan['elements']}
    result = {}
    for row in plan['relations']:
        pair = by_id[row['source']]['slot'], by_id[row['target']]['slot']
        _require(pair in _ROUTE_SPECS, 'process_relation_direction_invalid')
        points, box = deepcopy(_ROUTE_SPECS[pair])
        result[row['id']] = dict(points=points,
            path='M ' + ' L '.join(f'{x} {y}' for x, y in points), label_box=box,
            source_slot=pair[0], target_slot=pair[1],
            length=sum(math.dist(a, b) for a, b in zip(points, points[1:])))
    # Check complete swept token boxes with an extra 8px margin, including all
    # other relation labels. Lines alone do not establish a clear token route.
    token = next(row for row in plan['elements'] if row['kind'] == 'token')
    width, height = PROCESS_LAYOUT[token['slot']][2:]
    obstacles = [PROCESS_LAYOUT[row['slot']] for row in plan['elements'] if row['kind'] == 'card']
    obstacles += [result[row['id']]['label_box'] for row in plan['relations'] if row['label'].strip()]
    for path in result.values():
        for a, b in zip(path['points'], path['points'][1:]):
            swept = (min(a[0], b[0]) - width / 2 - 8, min(a[1], b[1]) - height / 2 - 8,
                     abs(b[0] - a[0]) + width + 16, abs(b[1] - a[1]) + height + 16)
            _require(0 <= swept[0] and swept[0] + swept[2] <= WIDTH and
                     0 <= swept[1] and swept[1] + swept[3] <= HEIGHT and
                     not any(_intersects(swept, box) for box in obstacles), 'process_path_obstructed')
    return result


def resolve_process_timeline(plan, beats, alignment, *, leading_seconds=0, trailing_seconds=0):
    """Join the independent plan to one existing recording/fixture alignment."""
    beats = validate_beats(beats)
    plan = validate_process_plan(plan, beats)
    alignment = validate_alignment(alignment, beats)
    leading = _number(leading_seconds, 0, 10)
    trailing = _number(trailing_seconds, 0, 10)
    paths = process_paths(plan)
    by_id = {row['id']: row for row in plan['elements']}
    anchors = {row['beat_id']: row for row in alignment['segments']}
    actions = []
    for ordinal, row in enumerate(plan['actions']):
        anchor = anchors[row['beat_id']]
        span = anchor['end'] - anchor['start']
        start = leading + anchor['start'] + {'start': .03, 'middle': .38, 'end': .72}[row['placement']] * span
        times = ({'quick': .75, 'normal': 1.2, 'slow': 1.8} if row['kind'] == 'transfer' else
                 {'quick': .25, 'normal': .45, 'slow': .7})
        duration = min(times[row['pace']], .24 * span)
        _require(duration >= 2 / FPS, 'process_beat_too_short')
        actions.append(dict(row, ordinal=ordinal, start=round(start, 6), duration=round(duration, 6),
                            until=leading + anchor['end']))
    actions.sort(key=lambda row: (row['start'], row['ordinal']))
    initial_visibility = {identity: (0.0 if row['kind'] == 'token' else 1.0) for identity, row in by_id.items()}
    first_visibility = set()
    for row in actions:
        if row['kind'] in {'reveal', 'hide'} and row['target'] not in first_visibility:
            first_visibility.add(row['target'])
            initial_visibility[row['target']] = 0.0 if row['kind'] == 'reveal' else 1.0
    states = {identity: 'idle' for identity, row in by_id.items() if row['kind'] == 'card'}
    initial_states = dict(states)
    token = next(row for row in plan['elements'] if row['kind'] == 'token')
    current_slot = token['slot'].removeprefix('token_')
    ends, drawn = {}, {}
    for row in actions:
        kind, target = row['kind'], row['target']
        prop = 'visibility' if kind in {'reveal', 'hide'} else kind
        key = target, prop
        _require(row['start'] >= ends.get(key, -1), 'conflicting_process_actions')
        ends[key] = row['start'] + row['duration']
        if kind == 'draw':
            _require(target not in drawn, 'process_relation_already_drawn')
            drawn[target] = row['start'] + row['duration']
        elif kind == 'transfer':
            relation = row['relation_id']
            path = paths[relation]
            _require(relation in drawn and drawn[relation] <= row['start'], 'process_path_not_drawn')
            _require(current_slot == path['source_slot'], 'process_token_source_mismatch')
            row.update(from_slot='token_' + current_slot, to_slot='token_' + path['target_slot'],
                       path_length=path['length'])
            current_slot = path['target_slot']
        elif kind == 'set_state':
            _require(row['state'] in STATE_TRANSITIONS[states[target]], 'process_state_transition_invalid')
            row['previous_state'] = states[target]
            states[target] = row['state']
    # State changes and transfers must be visible for their entire action
    # windows; an overlapping fade is not evidence of a clear process step.
    for row in (a for a in actions if a['kind'] in {'transfer', 'set_state'}):
        identities = (row['target'],)
        if row['kind'] == 'transfer':
            relation = next(r for r in plan['relations'] if r['id'] == row['relation_id'])
            identities += relation['source'], relation['target']
        for identity in identities:
            visible = initial_visibility[identity]
            for change in (a for a in actions if a['target'] == identity and a['kind'] in {'reveal', 'hide'}):
                if change['start'] + change['duration'] <= row['start']:
                    visible = 1.0 if change['kind'] == 'reveal' else 0.0
                elif change['start'] < row['start'] + row['duration']:
                    _require(False, 'process_element_not_visible')
            _require(visible == 1.0, 'process_element_not_visible')
    duration_seconds = math.ceil((leading + alignment['audio_seconds'] + trailing) * FPS) / FPS
    _require(all(row['start'] + row['duration'] <= duration_seconds - 1 / FPS + .000001
                 for row in actions), 'process_final_hold_required')
    return dict(schema='video.motion-timeline.v1', compiler=PROCESS_COMPILER_VERSION,
        plan_sha256=sha256(plan), semantic_sha256=semantic_digest(beats), alignment_sha256=sha256(alignment),
        audio_sha256=alignment['audio_sha256'], spoken_sha256=alignment['spoken_sha256'],
        audio_seconds=alignment['audio_seconds'], leading_seconds=leading, trailing_seconds=trailing,
        duration_seconds=duration_seconds,
        alignment_method=alignment['method'], review_status=alignment['review_status'],
        beats=[dict(row, start=leading + row['start'], end=leading + row['end']) for row in alignment['segments']],
        initial_visibility=initial_visibility, initial_states=initial_states, paths=paths, actions=actions)


def _state_markup(identity):
    layers = []
    for state, visual in STATE_PRESENTATIONS.items():
        layers.append(f'<div id="state-{identity}-{state}" class="state-layer" data-state="{state}" '
            f'data-symbol="{visual["symbol"]}" style="opacity:{1 if state == "idle" else 0};color:{visual["color"]}">'
            f'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="{visual["path"]}"/></svg>'
            f'<span class="state-label">{visual["label"]}</span></div>')
    return '<div class="state-badge">' + ''.join(layers) + '</div>'


def compile_process_project(plan, beats, alignment, *, animated=True, runtime_fingerprint=None,
                            leading_seconds=0, trailing_seconds=0):
    """Compile bounded data into the ordinary six frozen project files."""
    beats = validate_beats(beats)
    plan = validate_process_plan(plan, beats)
    alignment = validate_alignment(alignment, beats)
    _require(type(animated) is bool, 'invalid_process_options')
    timeline = resolve_process_timeline(plan, beats, alignment, leading_seconds=leading_seconds,
                                        trailing_seconds=trailing_seconds)
    boxes = {row['id']: PROCESS_LAYOUT[row['slot']] for row in plan['elements']}
    markup = []
    for row in plan['elements']:
        x, y, width, height = boxes[row['id']]
        detail = f'<div class="element-detail">{html.escape(row["detail"])}</div>' if row['detail'] else ''
        badge = _state_markup(row['id']) if row['kind'] == 'card' else ''
        opacity = timeline['initial_visibility'][row['id']]
        markup.append(f'<div id="el-{row["id"]}" class="element {row["kind"]}" data-motion-element="{row["id"]}" '
            f'style="left:{x}px;top:{y}px;width:{width}px;height:{height}px;opacity:{opacity}">'
            f'<div class="element-label">{html.escape(row["label"])}</div>{detail}{badge}</div>')
    connections = []
    for row in plan['relations']:
        path = timeline['paths'][row['id']]
        identity = row['id']
        x, y, width, height = path['label_box']
        connections.append(f'<g><defs><marker id="arrow-{identity}" markerWidth="10" markerHeight="10" '
            f'refX="8" refY="5" orient="auto" markerUnits="userSpaceOnUse">'
            f'<path id="tip-{identity}" d="M0 0 L10 5 L0 10 Z" fill="#72d9e2" opacity="0"/></marker></defs>'
            f'<path id="rel-{identity}" class="relation-active" d="{path["path"]}" pathLength="1" '
            f'marker-end="url(#arrow-{identity})"/>'
            f'<g id="label-{identity}" class="relation-label" data-motion-relation="{identity}" '
            f'data-has-label="{1 if row["label"].strip() else 0}" opacity="0">'
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="8"/>'
            f'<text x="{x + width / 2}" y="{y + 20}" text-anchor="middle">{html.escape(row["label"])}</text></g></g>')
    payload = dict(plan=plan, timeline=timeline, boxes=boxes, layout=PROCESS_LAYOUT, animated=animated)
    page = _PAGE
    replacements = dict(__DURATION__=str(timeline['duration_seconds']), __TITLE__=html.escape(plan['title']),
                        __TAKEAWAY__=html.escape(plan['takeaway']), __ELEMENTS__=''.join(markup),
                        __RELATIONS__=''.join(connections), __PAYLOAD__=_safe_script_json(payload),
                        __TIMELINE_SHA__=sha256(timeline))
    page = re.sub('|'.join(map(re.escape, replacements)), lambda match: replacements[match.group()], page)
    files = {'index.html': page.encode('utf-8'), 'plan.json': canonical(plan), 'beats.json': canonical(beats),
             'alignment.json': canonical(alignment), 'timeline.json': canonical(timeline)}
    manifest = dict(schema='video.motion-project.v1', compiler=PROCESS_COMPILER_VERSION,
        template=PROCESS_TEMPLATE_VERSION, hyperframes=HYPERFRAMES_VERSION, gsap=GSAP_VERSION,
        width=WIDTH, height=HEIGHT, fps=FPS, animated=animated, plan_sha256=sha256(plan),
        semantic_sha256=semantic_digest(beats), timeline_sha256=sha256(timeline),
        audio_sha256=alignment['audio_sha256'], runtime_fingerprint=deepcopy(runtime_fingerprint),
        files={name: sha256(data) for name, data in sorted(files.items())},
        required_assets=['assets/gsap.min.js', 'assets/NotoSansCJK-Regular.ttc'])
    files['manifest.json'] = canonical(manifest)
    return files


_PAGE = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=1280,height=720">
<title>受控过程演示</title><script src="assets/gsap.min.js"></script><style>
@font-face{font-family:VideoNoto;src:url('assets/NotoSansCJK-Regular.ttc');font-weight:100 900;font-display:block}
*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#0c1523;color:#edf2f7;font-family:VideoNoto,sans-serif}
#motion-root{position:relative;width:1280px;height:720px;overflow:hidden;background:#0c1523}
h1{position:absolute;left:64px;top:44px;width:1152px;margin:0;font-size:32px;line-height:1.45;overflow-wrap:anywhere}
.takeaway{position:absolute;left:64px;top:664px;width:1152px;max-height:48px;font-size:16px;line-height:24px;overflow:hidden;overflow-wrap:anywhere}
.element{position:absolute;box-sizing:border-box;overflow:hidden}
.card{z-index:10;padding:18px 20px;border:2px solid #415774;border-radius:16px;background:#16243a}
.card .element-label{font-size:22px;font-weight:700;line-height:32px;max-height:64px;overflow:hidden}
.element-detail{font-size:17px;line-height:24px;max-height:48px;margin-top:6px;color:#b5c4d8;overflow:hidden}
.token{z-index:20;display:flex;align-items:center;justify-content:center;padding:6px;border:2px solid #72d9e2;border-radius:22px;background:#164453;color:#ecffff;opacity:0}
.token .element-label{font-size:18px;line-height:26px;white-space:nowrap}
.state-badge{position:absolute;left:20px;right:20px;bottom:16px;height:32px}
.state-layer{position:absolute;inset:0;display:flex;align-items:center;gap:10px;font-size:18px;line-height:28px}
.state-layer svg{width:24px;height:24px;fill:none;stroke:currentColor;stroke-width:2.5;stroke-linecap:round;stroke-linejoin:round}
#connections{position:absolute;inset:0;width:1280px;height:720px;z-index:1;overflow:visible}
.relation-active{fill:none;stroke:#72d9e2;stroke-width:3;stroke-dasharray:1;stroke-dashoffset:1;opacity:0}
.relation-label{opacity:0}.relation-label rect{fill:#16243a;stroke:#415774;stroke-width:1}
.relation-label text{fill:#d4e7f4;font-size:16px}
</style></head><body><div id="motion-root" data-composition-id="video-motion" data-width="1280" data-height="720" data-start="0" data-duration="__DURATION__" data-fps="24">
<h1>__TITLE__</h1><svg id="connections" viewBox="0 0 1280 720">__RELATIONS__</svg>
__ELEMENTS__<div class="takeaway">__TAKEAWAY__</div></div><script>
'use strict';
(()=>{
const data=__PAYLOAD__,tl=gsap.timeline({paused:true,defaults:{ease:'power2.inOut'}});
const node=id=>document.getElementById('el-'+id),relation=id=>document.getElementById('rel-'+id);
const cubic=t=>t<.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2;
const inverse=t=>t<.5?Math.cbrt(t/4):1-Math.cbrt((1-t)/4);
const segmentEase=(a,b,lo,hi)=>u=>(cubic(lo+u*(hi-lo))-a)/(b-a);
for(const [id,opacity] of Object.entries(data.timeline.initial_visibility))gsap.set(node(id),{opacity});
const visibility={...data.timeline.initial_visibility};
for(const a of data.timeline.actions){
 const when=data.animated?a.start:0,duration=data.animated?a.duration:0;
 if(a.kind==='reveal'||a.kind==='hide'){
  const next=a.kind==='reveal'?1:0;
  tl.fromTo(node(a.target),{opacity:visibility[a.target]},{opacity:next,duration,immediateRender:false},when);
  visibility[a.target]=next;
 }else if(a.kind==='draw'){
  tl.fromTo(relation(a.target),{strokeDashoffset:1,opacity:0},{strokeDashoffset:0,opacity:1,duration,autoRound:false,immediateRender:false},when);
  const label=document.getElementById('label-'+a.target);
  if(label.dataset.hasLabel==='1')tl.fromTo(label,{opacity:0},{opacity:1,duration,immediateRender:false},when);
  tl.fromTo(document.getElementById('tip-'+a.target),{opacity:0},{opacity:1,duration:duration*.15,immediateRender:false},when+duration*.85);
 }else if(a.kind==='transfer'){
  const points=data.timeline.paths[a.relation_id].points,base=data.boxes[a.target],w=base[2],h=base[3];
  let distance=0;
  for(let i=1;i<points.length;i++){
   const before=points[i-1],after=points[i],length=Math.hypot(after[0]-before[0],after[1]-before[1]);
   const a0=distance/a.path_length,a1=(distance+length)/a.path_length,lo=inverse(a0),hi=inverse(a1);
   tl.fromTo(node(a.target),{x:before[0]-w/2-base[0],y:before[1]-h/2-base[1]},
    {x:after[0]-w/2-base[0],y:after[1]-h/2-base[1],duration:duration*(hi-lo),ease:segmentEase(a0,a1,lo,hi),immediateRender:false},when+duration*lo);
   distance+=length;
  }
 }else if(a.kind==='set_state'){
  tl.fromTo(document.getElementById('state-'+a.target+'-'+a.previous_state),{opacity:1},{opacity:0,duration:0,immediateRender:false},when);
  tl.fromTo(document.getElementById('state-'+a.target+'-'+a.state),{opacity:0},{opacity:1,duration:0,immediateRender:false},when);
 }
}
tl.set({}, {},data.timeline.duration_seconds);
window.__timelines=window.__timelines||{};window.__timelines['video-motion']=tl;
const rect=el=>{const r=el.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height};};
const boxMetrics=el=>({...rect(el),scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,
 scrollHeight:el.scrollHeight,clientHeight:el.clientHeight,
 overflow:el.scrollWidth>el.clientWidth||el.scrollHeight>el.clientHeight});
const metrics=el=>{
 const style=getComputedStyle(el),box=rect(el),children=Array.from(el.querySelectorAll('.element-label,.element-detail'));
 const layers=Array.from(el.querySelectorAll('.state-layer'));
 const layer=layers.find(s=>Number(getComputedStyle(s).opacity)>.5);
 const text_boxes=Object.fromEntries(children.map(c=>[c.className,boxMetrics(c)]));
 if(layer){text_boxes['state-label']=boxMetrics(layer.querySelector('.state-label'));
  text_boxes['state-symbol']=boxMetrics(layer.querySelector('svg'));}
 const text_area=el.classList.contains('card')?{left:box.x+20,top:box.y+18,right:box.x+box.width-20,bottom:box.y+box.height-18}:
  {left:box.x,top:box.y,right:box.x+box.width,bottom:box.y+box.height};
 return{...box,opacity:Number(style.opacity),borderColor:style.borderColor,backgroundColor:style.backgroundColor,
  scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,scrollHeight:el.scrollHeight,clientHeight:el.clientHeight,
  text_boxes,text_area,overflow:el.scrollWidth>el.clientWidth||el.scrollHeight>el.clientHeight||Object.values(text_boxes).some(r=>
   r.overflow||r.x<text_area.left-.5||r.y<text_area.top-.5||r.x+r.width>text_area.right+.5||r.y+r.height>text_area.bottom+.5
  ),stateOpacities:Object.fromEntries(layers.map(s=>[s.dataset.state,Number(getComputedStyle(s).opacity)])),
  state:layer?.dataset.state??null,stateLabel:layer?.querySelector('.state-label').textContent??null,
  stateSymbol:layer?.dataset.symbol??null,statePath:layer?.querySelector('path').getAttribute('d')??null};
};
window.__videoMotion={version:'motion-process-compiler-1',planSha256:data.timeline.plan_sha256,
 timelineSha256:'__TIMELINE_SHA__',duration:data.timeline.duration_seconds,
 inspect:()=>Object.fromEntries(data.plan.elements.map(e=>[e.id,metrics(node(e.id))])),
 inspectRelations:()=>Object.fromEntries(data.plan.relations.map(r=>[r.id,{
  opacity:Number(getComputedStyle(relation(r.id)).opacity),strokeDashoffset:parseFloat(getComputedStyle(relation(r.id)).strokeDashoffset),
  length:relation(r.id).getTotalLength(),markerOpacity:Number(getComputedStyle(document.getElementById('tip-'+r.id)).opacity),
  labelOpacity:Number(getComputedStyle(document.getElementById('label-'+r.id)).opacity),baseCount:0}])),
 inspectLayout:()=>({title:metrics(document.querySelector('h1')),takeaway:metrics(document.querySelector('.takeaway')),
  relations:data.plan.relations.map(r=>{const el=document.getElementById('label-'+r.id),plate=rect(el.querySelector('rect')),ink=rect(el.querySelector('text'));
   return{id:r.id,text:r.label,...plate,ink,opacity:Number(getComputedStyle(el).opacity),overflow:
    ink.x<plate.x-.5||ink.y<plate.y-.5||ink.x+ink.width>plate.x+plate.width+.5||ink.y+ink.height>plate.y+plate.height+.5||
    ink.x<0||ink.y<0||ink.x+ink.width>1280||ink.y+ink.height>720};}),
  fontReady:document.fonts.status==='loaded'&&document.fonts.check('20px VideoNoto')}),
 ready:document.fonts.ready.then(()=>true)};
tl.seek(0,false);
})();
</script></body></html>'''
