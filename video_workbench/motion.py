"""Pure, bounded motion compilation. No model, browser, filesystem or network calls.

The semantic plan deliberately has no audio identity or timestamps. Alignment is
bound to the *spoken* text and a specific recording; only resolution joins them.
The resulting page is executable only inside the separate renderer, never the
workbench's static preview iframe.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import html
import json
import math
import re

from agent_service.contracts import ServiceError
from agent_service.profiles.video_motion_contract import validate_beats, validate_motion_plan
from .motion_geometry import connection_geometry


COMPILER_VERSION = 'motion-compiler-5'
TEMPLATE_VERSION = 'technical-diagrams-5'
COMPILER_TEMPLATES = {'motion-compiler-1': 'technical-diagrams-1',
                      'motion-compiler-2': 'technical-diagrams-2',
                      'motion-compiler-3': 'technical-diagrams-3',
                      'motion-compiler-4': 'technical-diagrams-4',
                      COMPILER_VERSION: TEMPLATE_VERSION}
HYPERFRAMES_VERSION = '0.8.149'
GSAP_VERSION = '3.14.2'
WIDTH, HEIGHT, FPS = 1280, 720, 24
_SHA = re.compile(r'[0-9a-f]{64}')
_SENTENCE = re.compile(r'.+?(?:[。！？!?；;\n]+|[.](?=\s|$)|$)', re.DOTALL)
_ALIGN_KEYS = {'schema', 'method', 'review_status', 'audio_sha256', 'spoken_sha256',
               'audio_seconds', 'version', 'segments', 'reviewed_by', 'reviewed_at'}
_ALIGN_HISTORY_KEYS = {'provenance', 'corrections'}

# Coordinates are compiler-owned; an Agent can select a named slot, never CSS.
# Token slots sit above cards, so moving a request does not obscure the labels.
LAYOUTS = {
    'flow': {
        'source': (72, 292, 300, 156), 'queue': (490, 292, 300, 156),
        'worker': (908, 292, 300, 156), 'note': (72, 518, 1136, 100),
        'token_source': (162, 220, 120, 44), 'token_queue': (580, 220, 120, 44),
        'token_worker': (998, 220, 120, 44), 'caption': (72, 636, 1136, 42),
    },
    'compare': {
        'before': (72, 256, 532, 222), 'after': (676, 256, 532, 222),
        'note': (72, 526, 1136, 96), 'token_before': (278, 188, 120, 44),
        'token_after': (882, 188, 120, 44), 'caption': (72, 636, 1136, 42),
    },
    'focus': {
        'top': (490, 188, 300, 118), 'left': (72, 358, 300, 132),
        'center': (490, 358, 300, 132), 'right': (908, 358, 300, 132),
        'note': (72, 534, 1136, 90), 'token_top': (850, 238, 120, 44),
        'token_left': (162, 302, 120, 44), 'token_center': (580, 310, 120, 44),
        'token_right': (998, 302, 120, 44), 'caption': (72, 636, 1136, 42),
    },
}


def compiler_layout(plan, compiler_version):
    """Keep labels readable in v5 without changing historical slot geometry.

    Tokens use one shared width so a move between named slots retains its size.
    A conservative 18px-per-character allowance keeps the normal text size;
    actual font and overflow validation still runs in the isolated browser.
    """
    layout = LAYOUTS[plan['template']]
    if compiler_version != 'motion-compiler-5':
        return layout
    width = min(300, max([120, *(18 * len(row['label']) + 28 for row in plan['elements']
                                if row['kind'] == 'token')]))
    if width == 120:
        return layout
    result = dict(layout)
    for slot, (x, y, _, height) in layout.items():
        if slot.startswith('token_'):
            left = x + 60 - width / 2
            if plan['template'] == 'focus' and slot == 'token_top':
                left = max(left, layout['top'][0] + layout['top'][2] + 12)
            result[slot] = (left, y, width, height)
    return result


def _require(condition, code='invalid_motion_alignment'):
    if not condition:
        raise ServiceError(code, 422)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha256(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


def text_sha256(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _number(value, low, high):
    _require(type(value) in (int, float) and low <= value <= high and math.isfinite(value))
    return float(value)


def _text(value, maximum=200):
    _require(type(value) is str and 0 < len(value) <= maximum and value.strip()
             and not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value))
    return value


def _split_spans(value):
    result = []
    for match in _SENTENCE.finditer(value):
        if match.group().strip():
            result.append([match.start(), match.end()])
        elif result:
            result[-1][1] = match.end()
    if result and result[0][0] > 0:
        result[0][0] = 0
    return result


def make_beats(source_text, spoken_text=None):
    """Produce auditable sentence spans, without rewriting any words.

    IDs depend on sentence text and its duplicate index, not voice or timing.
    Changed pronunciation is supported when sentence boundaries still match.
    More complicated edits require explicit span mapping instead of guessing.
    """
    _require(type(source_text) is str and 0 < len(source_text) <= 10000,
             'invalid_motion_beats')
    spoken_text = source_text if spoken_text is None else spoken_text
    _require(type(spoken_text) is str and 0 < len(spoken_text) <= 10000,
             'invalid_motion_beats')
    source, spoken = _split_spans(source_text), _split_spans(spoken_text)
    _require(1 <= len(source) <= 24 and len(source) == len(spoken),
             'motion_beats_need_explicit_mapping')
    occurrences, segments = Counter(), []
    for (start, end), (sstart, send) in zip(source, spoken):
        text = source_text[start:end]
        occurrences[text] += 1
        stable = sha256({'text': text, 'occurrence': occurrences[text]})[:16]
        segments.append(dict(id='beat_' + stable, source_start=start, source_end=end,
                             spoken_start=sstart, spoken_end=send, source_text=text,
                             spoken_text=spoken_text[sstart:send]))
    return validate_beats(dict(schema='video.beats.v1', source_text=source_text,
                               spoken_text=spoken_text, segments=segments))


def semantic_digest(beats):
    beats = validate_beats(beats)
    return sha256({'source_text': beats['source_text'],
                   'segments': [{'id': row['id'], 'source_text': row['source_text']}
                                for row in beats['segments']]})


def validate_alignment(alignment, beats):
    """Reject stale recordings, unordered anchors and invented review claims."""
    beats = validate_beats(beats)
    _require(type(alignment) is dict and set(alignment) in
             (_ALIGN_KEYS, _ALIGN_KEYS | _ALIGN_HISTORY_KEYS))
    _require(alignment['schema'] == 'video.alignment.v1'
             and type(alignment['method']) is str
             and alignment['method'] in {'estimated', 'forced_alignment', 'manual_anchors'}
             and type(alignment['review_status']) is str
             and alignment['review_status'] in {'pending', 'verified'})
    _require(type(alignment['audio_sha256']) is str and _SHA.fullmatch(alignment['audio_sha256'])
             and alignment['spoken_sha256'] == text_sha256(beats['spoken_text']))
    seconds = _number(alignment['audio_seconds'], 0.1, 300)
    _text(alignment['version'], 120)
    if alignment['review_status'] == 'verified':
        _text(alignment['reviewed_by'], 120)
        _text(alignment['reviewed_at'], 60)
        try:
            reviewed_at = datetime.fromisoformat(alignment['reviewed_at'].replace('Z', '+00:00'))
            _require(reviewed_at.tzinfo is not None)
        except (ValueError, TypeError):
            raise ServiceError('invalid_motion_alignment', 422) from None
    else:
        _require(alignment['reviewed_by'] is None and alignment['reviewed_at'] is None)
    rows = alignment['segments']
    _require(type(rows) is list and len(rows) == len(beats['segments']))
    previous = 0
    for row, beat in zip(rows, beats['segments']):
        _require(type(row) is dict and set(row) == {'beat_id', 'start', 'end'}
                 and row['beat_id'] == beat['id'])
        start = _number(row['start'], 0, seconds)
        end = _number(row['end'], 0, seconds)
        _require(start >= previous and end - start >= 0.12)
        previous = end
    if 'provenance' in alignment:
        origin = alignment['provenance']
        _require(type(origin) is dict and set(origin) == {'method', 'version', 'alignment_sha256'})
        _require(type(origin['method']) is str
                 and origin['method'] in {'estimated', 'forced_alignment', 'manual_anchors'})
        _text(origin['version'], 120)
        _require(origin['alignment_sha256'] is None or
                 (type(origin['alignment_sha256']) is str and _SHA.fullmatch(origin['alignment_sha256'])))
        corrections = alignment['corrections']
        _require(type(corrections) is list and len(corrections) <= 24)
        seen, current = set(), {row['beat_id']: row for row in rows}
        for correction in corrections:
            _require(type(correction) is dict and set(correction) ==
                     {'beat_id', 'previous_start', 'previous_end', 'start', 'end', 'reason'})
            bid = correction['beat_id']
            _require(type(bid) is str and bid in current and bid not in seen)
            seen.add(bid)
            _text(correction['reason'], 240)
            for key in ('previous_start', 'previous_end', 'start', 'end'):
                _number(correction[key], 0, seconds)
            _require(correction['previous_start'] < correction['previous_end']
                     and correction['start'] == current[bid]['start']
                     and correction['end'] == current[bid]['end']
                     and origin['alignment_sha256'] is not None)
    return json.loads(canonical(alignment))


def estimated_alignment(beats, audio_sha256, audio_seconds):
    """Explicitly coarse timing; never reports forced alignment or review."""
    beats = validate_beats(beats)
    seconds = _number(audio_seconds, 0.1, 300)
    weights = [max(1, len(row['spoken_text'].strip())) for row in beats['segments']]
    total, elapsed, segments = sum(weights), 0.0, []
    for beat, weight in zip(beats['segments'], weights):
        start = elapsed
        elapsed += seconds * weight / total
        segments.append(dict(beat_id=beat['id'], start=round(start, 6), end=round(elapsed, 6)))
    segments[-1]['end'] = seconds
    result = dict(schema='video.alignment.v1', method='estimated', review_status='pending',
                  audio_sha256=audio_sha256, spoken_sha256=text_sha256(beats['spoken_text']),
                  audio_seconds=seconds, version='spoken-character-weight-1', segments=segments,
                  reviewed_by=None, reviewed_at=None,
                  provenance=dict(method='estimated', version='spoken-character-weight-1', alignment_sha256=None),
                  corrections=[])
    return validate_alignment(result, beats)


def manual_alignment(beats, audio_sha256, audio_seconds, anchors, *,
                     reviewed_by=None, reviewed_at=None, previous_alignment=None,
                     correction_reason='Manually adjusted audio anchors'):
    beats = validate_beats(beats)
    previous = None if previous_alignment is None else validate_alignment(previous_alignment, beats)
    if previous is not None:
        _require(previous['audio_sha256'] == audio_sha256 and previous['audio_seconds'] == audio_seconds)
    corrections = []
    if previous is not None:
        before = {row['beat_id']: row for row in previous['segments']}
        _require(type(anchors) is list)
        for row in anchors:
            _require(type(row) is dict and type(row.get('beat_id')) is str and row['beat_id'] in before)
            old = before[row['beat_id']]
            if (old['start'], old['end']) != (row.get('start'), row.get('end')):
                corrections.append(dict(beat_id=row['beat_id'], previous_start=old['start'],
                                        previous_end=old['end'], start=row.get('start'), end=row.get('end'),
                                        reason=correction_reason))
    result = dict(schema='video.alignment.v1', method='manual_anchors',
                  review_status='verified' if reviewed_by is not None else 'pending',
                  audio_sha256=audio_sha256, spoken_sha256=text_sha256(beats['spoken_text']),
                  audio_seconds=audio_seconds, version='manual-anchors-1', segments=anchors,
                  reviewed_by=reviewed_by, reviewed_at=reviewed_at,
                  provenance=dict(method=previous['method'] if previous else 'manual_anchors',
                                  version=previous['version'] if previous else 'manual-anchors-1',
                                  alignment_sha256=sha256(previous) if previous else None),
                  corrections=corrections)
    return validate_alignment(result, beats)


def resolve_timeline(plan, beats, alignment, *, leading_seconds=0, trailing_seconds=0,
                     compiler_version=COMPILER_VERSION):
    """Resolve semantic actions against this recording, without stretching it."""
    _require(type(compiler_version) is str and compiler_version in COMPILER_TEMPLATES,
             'motion_compiler_version_unsupported')
    beats = validate_beats(beats)
    plan = validate_motion_plan(plan, beats)
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
    # Different verbs can still conflict (reveal/hide both own opacity).
    ends = {}
    for row in actions:
        prop = {'reveal': 'opacity', 'hide': 'opacity', 'highlight': 'highlight',
                'move': 'position', 'draw': 'stroke'}[row['kind']]
        key = row['target'], prop
        _require(row['start'] >= ends.get(key, -1), 'conflicting_motion_actions')
        ends[key] = row['start'] + row['duration']
    return dict(schema='video.motion-timeline.v1', compiler=compiler_version,
                plan_sha256=sha256(plan), semantic_sha256=semantic_digest(beats),
                alignment_sha256=sha256(alignment), audio_sha256=alignment['audio_sha256'],
                spoken_sha256=alignment['spoken_sha256'], audio_seconds=alignment['audio_seconds'],
                leading_seconds=leading, trailing_seconds=trailing,
                duration_seconds=math.ceil((leading + alignment['audio_seconds'] + trailing) * FPS) / FPS,
                alignment_method=alignment['method'], review_status=alignment['review_status'],
                beats=[dict(row, start=leading + row['start'], end=leading + row['end'])
                       for row in alignment['segments']], actions=actions)


def _safe_script_json(value):
    # JSON inside a script tag needs HTML raw-text escaping as well as JSON quoting.
    return canonical(value).decode('utf-8').replace('<', '\\u003c').replace('>', '\\u003e').replace(
        '&', '\\u0026').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


def _relation_path(source, target, obstacles=()):
    x1, y1, w1, h1 = source
    x2, y2, w2, h2 = target
    if abs((y1 + h1 / 2) - (y2 + h2 / 2)) < 40:
        left_to_right = x1 < x2
        between = [b for b in obstacles if min(x1 + w1, x2 + w2) <= b[0]
                   and b[0] + b[2] <= max(x1, x2) and b[1] <= y1 + h1 / 2 <= b[1] + b[3]]
        if between:
            lane = max(y1 + h1, y2 + h2, *(b[1] + b[3] for b in between)) + 20
            sx, ex = x1 + (w1 if left_to_right else 0), x2 + (0 if left_to_right else w2)
            offset = 26 if left_to_right else -26
            return (f'M {sx} {y1 + h1 / 2} L {sx + offset} {y1 + h1 / 2} '
                    f'L {sx + offset} {lane} L {ex - offset} {lane} '
                    f'L {ex - offset} {y2 + h2 / 2} L {ex} {y2 + h2 / 2}')
        return f'M {x1 + (w1 if left_to_right else 0)} {y1 + h1 / 2} L {x2 + (0 if left_to_right else w2)} {y2 + h2 / 2}'
    down = y1 < y2
    return f'M {x1 + w1 / 2} {y1 + (h1 if down else 0)} L {x2 + w2 / 2} {y2 + (0 if down else h2)}'


def _relation_label(source, target, path):
    """Place a short label in the connector's gap, outside the card boxes."""
    x1, y1, w1, h1 = source
    x2, y2, w2, h2 = target
    if path.count(' L ') > 1:
        return (x1 + w1 / 2 + x2 + w2 / 2) / 2, max(y1 + h1, y2 + h2) + 38
    if abs((y1 + h1 / 2) - (y2 + h2 / 2)) < 40:
        left_to_right = x1 < x2
        sx, ex = x1 + (w1 if left_to_right else 0), x2 + (0 if left_to_right else w2)
        return (sx + ex) / 2, (y1 + h1 / 2 + y2 + h2 / 2) / 2 - 16
    down = y1 < y2
    sy, ey = y1 + (h1 if down else 0), y2 + (0 if down else h2)
    return (x1 + w1 / 2 + x2 + w2 / 2) / 2, (sy + ey) / 2 + 6


def compile_project(plan, beats, alignment, *, animated=True, runtime_fingerprint=None,
                    leading_seconds=0, trailing_seconds=0, compiler_version=COMPILER_VERSION):
    """Return deterministic project files as bytes; assets are supplied by renderer.

    ``assets/gsap.min.js`` and the Noto font are immutable renderer assets.
    Audio is muxed by the renderer from the separately hash-verified WAV, so this
    page cannot fetch a user-selected URL and cannot clip or stretch speech.
    """
    beats = validate_beats(beats)
    plan = validate_motion_plan(plan, beats)
    alignment = validate_alignment(alignment, beats)
    _require(type(animated) is bool, 'invalid_motion_options')
    timeline = resolve_timeline(plan, beats, alignment, leading_seconds=leading_seconds,
                                trailing_seconds=trailing_seconds, compiler_version=compiler_version)
    layout = compiler_layout(plan, compiler_version)
    elements = {row['id']: row for row in plan['elements']}
    boxes = {row['id']: layout[row['slot']] for row in plan['elements']}
    geometry = None
    if compiler_version == 'motion-compiler-5':
        try:
            reserved = [(72, 74, 1136, 112)]
            if not any(row['slot'] == 'caption' for row in plan['elements']):
                reserved.append((72, 636, 1136, 72))
            moving, positions = [], dict(boxes)
            for action in timeline['actions']:
                if action['kind'] != 'move':
                    continue
                origin, destination = positions[action['target']], layout[action['to_slot']]
                left, top = min(origin[0], destination[0]), min(origin[1], destination[1])
                right, bottom = max(origin[0] + origin[2], destination[0] + destination[2]), max(origin[1] + origin[3], destination[1] + destination[3])
                moving.append((left, top, right - left, bottom - top))
                positions[action['target']] = destination
            geometry = connection_geometry(plan['relations'], boxes, reserved, moving)
        except ValueError as error:
            raise ServiceError('motion_relation_layout_unavailable', 422) from error
    markup, arrows, relation_labels = [], [], []
    for row in plan['relations']:
        if geometry is None:
            path = _relation_path(boxes[row['source']], boxes[row['target']],
                                  [b for key, b in boxes.items() if key not in {row['source'], row['target']}])
            a, b = boxes[row['source']], boxes[row['target']]
            lx, ly = _relation_label(a, b, path)
        else:
            path = geometry[row['id']]['path']
        arrows.append(f'<marker id="arrow-{row["id"]}" viewBox="0 0 10 10" refX="9" refY="5" '
                      f'markerWidth="5" markerHeight="5" orient="auto-start-reverse">'
                      f'<path id="tip-{row["id"]}" class="arrow-tip" d="M 0 0 L 10 5 L 0 10 z" fill="#67ded0"/></marker>')
        base = '' if compiler_version in {'motion-compiler-4', 'motion-compiler-5'} else f'<path class="relation-base" d="{path}"/>'
        stroke = base + f'<path id="rel-{row["id"]}" class="relation-active" d="{path}" pathLength="1" marker-end="url(#arrow-{row["id"]})"/>'
        if geometry is None:
            markup.append(stroke + f'<text id="label-{row["id"]}" data-motion-relation="{row["id"]}" class="relation-label" x="{lx}" y="{ly}" text-anchor="middle">{html.escape(row["label"])}</text>')
        else:
            markup.append(stroke)
            x, y, width, height = geometry[row['id']]['box']
            cx = x + width / 2
            rows = ''.join(f'<tspan x="{cx:g}" y="{y + 24 + index * 26:g}">{html.escape(line)}</tspan>'
                           for index, line in enumerate(geometry[row['id']]['lines']))
            relation_labels.append(f'<g id="label-{row["id"]}" data-motion-relation="{row["id"]}" class="relation-label">'
                f'<rect class="relation-plate" x="{x:g}" y="{y:g}" width="{width:g}" height="{height:g}" rx="7"/>'
                f'<text text-anchor="middle">{rows}</text></g>')
    connections = ''.join(markup + relation_labels)
    markup = []
    for index, row in enumerate(plan['elements']):
        x, y, w, h = boxes[row['id']]
        kind = row['kind']
        badge = f'<span class="card-index">{index + 1:02d}</span>' if kind == 'card' and row['slot'] != 'note' else ''
        markup.append(f'<div id="el-{row["id"]}" class="element {kind} slot-{row["slot"]}" data-motion-element="{row["id"]}" '
                      f'style="left:{x}px;top:{y}px;width:{w}px;height:{h}px">{badge}'
                      f'<div class="element-copy"><div class="element-label">{html.escape(row["label"])}</div>'
                      f'<div class="element-detail">{html.escape(row["detail"])}</div></div></div>')
    payload = dict(plan=plan, timeline=timeline, boxes=boxes, layout=layout, animated=animated)
    page = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=1280,height=720"><title>技术动态图解</title>
<script src="assets/gsap.min.js"></script><style>
@font-face{font-family:VideoNoto;src:url('assets/NotoSansCJK-Regular.ttc');font-weight:100 900;font-display:block}
*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#0b1220;color:#eaf1ff;font-family:VideoNoto,sans-serif}
#motion-root{position:relative;width:1280px;height:720px;overflow:hidden;background:radial-gradient(ellipse at 50% 5%,#1c2d46 0%,#0b1220 70%)}
.eyebrow{position:absolute;left:72px;top:43px;color:#66d6cb;font-size:16px;letter-spacing:3px}
h1{position:absolute;left:72px;top:74px;margin:0;width:1136px;padding-bottom:4px;font-size:36px;font-weight:700;line-height:1.5;overflow-wrap:anywhere}
.takeaway{position:absolute;left:72px;top:636px;width:1136px;padding:2px 0;min-height:36px;max-height:72px;font-size:20px;color:#aebdd5;line-height:1.6;overflow:hidden;overflow-wrap:anywhere}
.element{position:absolute;border:2px solid #33445e;border-radius:18px;background:#16243a;display:flex;align-items:center;padding:24px;transform-origin:center;overflow:hidden;box-shadow:0 10px 28px #0002}
.element-copy{min-width:0;width:100%}.element-label{font-size:29px;line-height:1.3;font-weight:700;overflow-wrap:anywhere}.element-detail{font-size:20px;line-height:1.45;color:#b2c2da;margin-top:10px;overflow-wrap:anywhere}
.card-index{position:absolute;top:12px;right:16px;font-size:14px;color:#6e829f}
.slot-top{padding:18px}
.slot-note .element-copy{display:flex;align-items:center;gap:32px}.slot-note .element-detail{margin-top:0;flex-shrink:0}
.token{padding:8px 10px;border-radius:22px;border-color:#6de1d2;background:#174c4c;text-align:center;box-shadow:none}.token .element-label{font-size:18px}.token .element-detail{display:none}
.label{padding:0;border:0;background:none;box-shadow:none}.label .element-label{font-size:21px;color:#aebdd5}.label .element-detail{display:none}
.relation-base{fill:none;stroke:#33445e;stroke-width:3}.relation-active{fill:none;stroke:#67ded0;stroke-width:4;stroke-dasharray:1;stroke-dashoffset:1}.relation-label{fill:#90a5c3;font-size:17px;opacity:0}.arrow-tip{opacity:0}
#connections{position:absolute;inset:0;width:1280px;height:720px;overflow:visible}#progress{position:absolute;left:0;bottom:0;height:3px;width:1280px;background:#64dacc;transform-origin:left}
</style></head><body><div id="motion-root" data-composition-id="video-motion" data-width="1280" data-height="720" data-start="0" data-duration="__DURATION__" data-fps="24">
<div class="eyebrow">__EYEBROW__</div><h1>__TITLE__</h1>
<svg id="connections" viewBox="0 0 1280 720" aria-hidden="true"><defs>__ARROWS__</defs>__CONNECTIONS__</svg>
__ELEMENTS__<div class="takeaway">__TAKEAWAY__</div><div id="progress"></div>
</div><script>
'use strict';
(() => {
  const data = __PAYLOAD__;
  const tl = gsap.timeline({paused:true, defaults:{ease:'power2.inOut'}});
  const node = id => document.getElementById('el-'+id);
  const relation = id => document.getElementById('rel-'+id);
  const actions = data.timeline.actions;
  const visibilityFirst = new Map();
  for(const a of actions) if((a.kind==='reveal'||a.kind==='hide')&&!visibilityFirst.has(a.target)) visibilityFirst.set(a.target,a.kind);
  const revealTargets = new Set(Array.from(visibilityFirst.entries()).filter(([id,kind])=>kind==='reveal').map(([id])=>id));
  for (const id of revealTargets) gsap.set(node(id),{opacity:0});
  const position = Object.fromEntries(Object.keys(data.boxes).map(id=>[id,{x:0,y:0}]));
  const opacity = Object.fromEntries(Object.keys(data.boxes).map(id=>[id,revealTargets.has(id)?0:1]));
  for (const a of actions) {
    const when=data.animated?a.start:0, duration=data.animated?a.duration:0;
    if(a.kind==='reveal'||a.kind==='hide') {
      const next=a.kind==='reveal'?1:0;
      tl.fromTo(node(a.target),{opacity:opacity[a.target]},{opacity:next,duration,immediateRender:false},when);
      opacity[a.target]=next;
    } else if(a.kind==='highlight') {
      tl.fromTo(node(a.target),{borderColor:'#33445e',backgroundColor:'#16243a'},
        {borderColor:'#79e4d5',backgroundColor:'#21434e',duration,immediateRender:false},when);
    } else if(a.kind==='move') {
      const base=data.boxes[a.target], dest=data.layout[a.to_slot];
      const next={x:dest[0]-base[0],y:dest[1]-base[1]};
      tl.fromTo(node(a.target),position[a.target],{...next,duration,immediateRender:false},when);
      position[a.target]=next;
    } else if(a.kind==='draw') {
      tl.fromTo(relation(a.target),{strokeDashoffset:1},{strokeDashoffset:0,duration,autoRound:false,immediateRender:false},when);
      tl.fromTo(document.getElementById('label-'+a.target),{opacity:0},{opacity:1,duration,immediateRender:false},when);
      tl.fromTo(document.getElementById('tip-'+a.target),{opacity:0},
        {opacity:1,duration:duration*.15,immediateRender:false},when+duration*.85);
    }
  }
  tl.fromTo('#progress',{scaleX:0},{scaleX:1,duration:data.timeline.duration_seconds,ease:'none',immediateRender:true},0);
  tl.set({}, {}, data.timeline.duration_seconds);
  window.__timelines=window.__timelines||{};
  window.__timelines['video-motion']=tl;
  const metrics=el=>{
    const style=getComputedStyle(el),r=el.getBoundingClientRect();
    return {opacity:Number(style.opacity),x:r.x,y:r.y,width:r.width,height:r.height,
      borderColor:style.borderColor,backgroundColor:style.backgroundColor,
      scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,scrollHeight:el.scrollHeight,clientHeight:el.clientHeight,
      overflow:el.scrollWidth>el.clientWidth||el.scrollHeight>el.clientHeight};
  };
  window.__videoMotion={version:'motion-compiler-1',planSha256:data.timeline.plan_sha256,
    timelineSha256:'__TIMELINE_SHA__',duration:data.timeline.duration_seconds,
    inspect:()=>Object.fromEntries(Object.keys(data.boxes).map(id=>[id,metrics(node(id))])),
    inspectRelations:()=>Object.fromEntries(data.plan.relations.map(r=>{
      const el=relation(r.id),style=getComputedStyle(el);
      return [r.id,{strokeDashoffset:parseFloat(style.strokeDashoffset),length:el.getTotalLength(),opacity:Number(style.opacity),
        markerOpacity:Number(getComputedStyle(document.getElementById('tip-'+r.id)).opacity)}];
    })),
    inspectLayout:()=>({title:metrics(document.querySelector('h1')),
      takeaway:metrics(document.querySelector('.takeaway')),
      relations:Array.from(document.querySelectorAll('.relation-label')).map(el=>({
        id:el.dataset.motionRelation,text:el.textContent,...metrics(el),
        overflow:el.getBBox().x<0||el.getBBox().x+el.getBBox().width>1280||
          el.getBBox().y<0||el.getBBox().y+el.getBBox().height>720})),
      fontReady:document.fonts.status==='loaded'&&document.fonts.check('20px VideoNoto')}),
    ready:document.fonts.ready.then(()=>true)};
  tl.seek(0,false);
})();
</script></body></html>'''
    if compiler_version in {'motion-compiler-2', 'motion-compiler-3', 'motion-compiler-4', 'motion-compiler-5'}:
        # Keep the v1 page byte-for-byte reproducible. This bounded v2 layout
        # changes only compiler-owned typography, never candidate text, geometry,
        # actions or audio. Text that still does not fit remains a hard failure.
        page = page.replace('</style>', '''
.card{padding:14px}.card .element-label{font-size:26px;line-height:1.25}
.card .element-detail{font-size:18px;line-height:1.35;margin-top:6px}
.card.slot-top{padding:12px 14px}.card.slot-note{padding:10px 14px}
.card.slot-note .element-copy{display:block}
.card.slot-note .element-detail{margin-top:4px;flex-shrink:1}
</style>''')
        # Centered flex content can extend above a clipped parent's scroll box.
        # Check each text box against the actual padded content area as well as
        # retaining all original parent scroll/canvas checks.
        page = page.replace('const metrics=el=>{', '''const metrics=el=>{
    const textBoxes={};let textArea=null;
    if(el.classList.contains('element')){
      const parent=el.getBoundingClientRect(),css=getComputedStyle(el),px=key=>parseFloat(css[key])||0;
      const area={left:parent.left+px('borderLeftWidth')+px('paddingLeft'),
        top:parent.top+px('borderTopWidth')+px('paddingTop'),
        right:parent.right-px('borderRightWidth')-px('paddingRight'),
        bottom:parent.bottom-px('borderBottomWidth')-px('paddingBottom')};
      textArea=area;
      for(const [key,selector] of [['copy','.element-copy'],['label','.element-label'],['detail','.element-detail']]){
        const child=el.querySelector(selector);
        if(!child||getComputedStyle(child).display==='none')continue;
        const box=child.getBoundingClientRect();
        textBoxes[key]={x:box.x,y:box.y,width:box.width,height:box.height,
          scrollWidth:child.scrollWidth,clientWidth:child.clientWidth,
          scrollHeight:child.scrollHeight,clientHeight:child.clientHeight,
          overflow:child.scrollWidth>child.clientWidth||child.scrollHeight>child.clientHeight||
            box.left<area.left-1e-6||box.top<area.top-1e-6||
            box.right>area.right+1e-6||box.bottom>area.bottom+1e-6};
      }
    }''')
        page = page.replace('overflow:el.scrollWidth>el.clientWidth||el.scrollHeight>el.clientHeight};',
                            '''text_area:textArea,text_boxes:textBoxes,
      overflow:el.scrollWidth>el.clientWidth||el.scrollHeight>el.clientHeight||
        Object.values(textBoxes).some(box=>box.overflow)};''')
        page = page.replace("version:'motion-compiler-1'", "version:'motion-compiler-2'")
        if compiler_version in {'motion-compiler-3', 'motion-compiler-4', 'motion-compiler-5'}:
            # Integral line boxes leave room for the pinned CJK font's glyphs;
            # fractional v2 line heights can report a one-pixel scroll overflow
            # even when the outer card fits. Preserve both prior versions and
            # all strict parent/child checks instead of tolerating that overflow.
            page = page.replace('</style>', '''
.card .element-label{line-height:38px}.card .element-detail{line-height:27px}
.card.slot-top{padding:7px 14px}.card.slot-note{padding:8px 14px}
.token{padding:6px 10px}.token .element-label{line-height:27px}
.label .element-label{line-height:31px}
</style>''')
            page = page.replace("version:'motion-compiler-2'", "version:'motion-compiler-3'")
    if compiler_version in {'motion-compiler-4', 'motion-compiler-5'}:
        # Undrawn relations have no visible guide underneath. Hide the actual
        # stroke before JS/bootstrap too, and animate opacity with the draw so
        # exact forward and reverse seeks restore the same blank initial state.
        page = page.replace('.relation-active{fill:none;', '.relation-active{opacity:0;fill:none;')
        page = page.replace(
            '{strokeDashoffset:1},{strokeDashoffset:0,duration,autoRound:false,immediateRender:false}',
            '{strokeDashoffset:1,opacity:0},{strokeDashoffset:0,opacity:1,duration,autoRound:false,immediateRender:false}')
        page = page.replace(
            'markerOpacity:Number(getComputedStyle(document.getElementById(\'tip-\'+r.id)).opacity)',
            'markerOpacity:Number(getComputedStyle(document.getElementById(\'tip-\'+r.id)).opacity),'
            'labelOpacity:Number(getComputedStyle(document.getElementById(\'label-\'+r.id)).opacity),'
            'baseCount:document.querySelectorAll(\'.relation-base\').length')
        page = page.replace("version:'motion-compiler-3'", "version:'motion-compiler-4'")
    if compiler_version == 'motion-compiler-5':
        # Label plates are above *all* strokes, so the ink is never crossed by
        # an earlier relation. Later relations also route around reserved plates.
        page = page.replace('</style>', '''
.relation-label{font-size:18px;fill:#d6e8f5}.relation-plate{fill:#0b1728;stroke:#344c64;stroke-width:1}
.element{outline:0 solid #79e4d5;outline-offset:-4px}
.token .element-label{white-space:nowrap}
</style>''')
        page = page.replace("{borderColor:'#79e4d5',backgroundColor:'#21434e',duration,immediateRender:false},when);",
            """{borderColor:'#79e4d5',backgroundColor:'#21434e',duration,immediateRender:false},when);
      tl.fromTo(node(a.target),{outlineWidth:0},{outlineWidth:8,duration:duration/2,autoRound:false,immediateRender:false},when);
      tl.fromTo(node(a.target),{outlineWidth:8},{outlineWidth:0,duration:duration/2,autoRound:false,immediateRender:false},when+duration/2);""")
        page = page.replace('borderColor:style.borderColor,backgroundColor:style.backgroundColor,',
            'borderColor:style.borderColor,backgroundColor:style.backgroundColor,highlightWidth:parseFloat(style.outlineWidth),highlightColor:style.outlineColor,')
        page = page.replace("version:'motion-compiler-4'", "version:'motion-compiler-5'")
        page = page.replace('window.__videoMotion={', '''const relationLayout=el=>{
    const plain=box=>({x:box.x,y:box.y,width:box.width,height:box.height});
    const plate=el.querySelector('rect').getBBox(),text=el.querySelector('text');
    const intersects=box=>plate.width>0&&plate.height>0&&box.width>0&&box.height>0&&
      plate.x<box.x+box.width&&box.x<plate.x+plate.width&&plate.y<box.y+box.height&&box.y<plate.y+plate.height;
    return {plate:plain(plate),ink:text.textContent?plain(text.getBBox()):null,
      overlapsNodes:Object.keys(data.boxes).filter(id=>intersects(node(id).getBoundingClientRect())),
      overlapsLabels:Array.from(document.querySelectorAll('.relation-plate')).filter(other=>other.parentNode!==el&&intersects(other.getBBox())).map(other=>other.parentNode.dataset.motionRelation)};
  };
  window.__videoMotion={''')
        page = page.replace('id:el.dataset.motionRelation,text:el.textContent,...metrics(el),',
                            'id:el.dataset.motionRelation,text:el.textContent,...metrics(el),...relationLayout(el),')
        page = page.replace('el.getBBox().y<0||el.getBBox().y+el.getBBox().height>720',
            '''el.getBBox().y<0||el.getBBox().y+el.getBBox().height>720||
          Array.from(el.querySelectorAll('text')).some(text=>{
            if(!text.textContent)return false;
            const ink=text.getBBox(),plate=el.querySelector('rect').getBBox();
            return ink.x<plate.x||ink.y<plate.y||ink.x+ink.width>plate.x+plate.width||ink.y+ink.height>plate.y+plate.height;
          })''')
    replacements = {
        '__DURATION__': str(timeline['duration_seconds']),
        '__EYEBROW__': {'flow': '流程与路由', 'compare': '前后对照', 'focus': '结构聚焦'}[plan['template']],
        '__TITLE__': html.escape(plan['title']),
        '__TAKEAWAY__': '' if any(e['slot'] == 'caption' for e in plan['elements']) else html.escape(plan['takeaway']),
        '__ARROWS__': ''.join(arrows), '__CONNECTIONS__': connections, '__ELEMENTS__': ''.join(markup),
        '__PAYLOAD__': _safe_script_json(payload), '__TIMELINE_SHA__': sha256(timeline),
    }
    # Replace markers in the compiler template in one pass; user text resembling
    # a marker must not become a second interpolation opportunity.
    page = re.sub('|'.join(map(re.escape, replacements)), lambda m: replacements[m.group()], page)
    files = {'index.html': page.encode('utf-8'), 'plan.json': canonical(plan),
             'beats.json': canonical(beats), 'alignment.json': canonical(alignment),
             'timeline.json': canonical(timeline)}
    manifest = dict(schema='video.motion-project.v1', compiler=compiler_version,
                    template=COMPILER_TEMPLATES[compiler_version], hyperframes=HYPERFRAMES_VERSION, gsap=GSAP_VERSION,
                    width=WIDTH, height=HEIGHT, fps=FPS, animated=animated,
                    plan_sha256=sha256(plan), semantic_sha256=semantic_digest(beats),
                    timeline_sha256=sha256(timeline), audio_sha256=alignment['audio_sha256'],
                    runtime_fingerprint=runtime_fingerprint,
                    files={name: sha256(data) for name, data in sorted(files.items())},
                    required_assets=['assets/gsap.min.js', 'assets/NotoSansCJK-Regular.ttc'])
    files['manifest.json'] = canonical(manifest)
    return files


def action_windows(timeline):
    """Candidate observation times; actual states must still be checked in Chrome."""
    return [dict(target=a['target'], kind=a['kind'], beat_id=a['beat_id'],
                 before=max(0, a['start'] - 1 / FPS),
                 middle=a['start'] + a['duration'] / 2,
                 after=min(timeline['duration_seconds'] - 1 / FPS,
                           a['start'] + a['duration'] + 1 / FPS))
            for a in timeline['actions']]


def example_plan(beats, *, template='flow'):
    """Small honest fixture for rendering experiments, not an Agent result."""
    beats = validate_beats(beats)
    _require(template in LAYOUTS, 'invalid_motion_plan')
    ids = [row['id'] for row in beats['segments']]
    _require(len(ids) >= 3, 'motion_example_requires_three_beats')
    ids = ids[:3]
    if template == 'flow':
        slots, labels = ['source', 'queue', 'worker'], ['用户请求', '任务队列', '执行节点']
        token_slot = 'token_source'
        token_destinations = ['token_queue', 'token_worker']
        title, takeaway = '请求怎样变成结果？', '队列负责排队，执行节点按需工作。'
    elif template == 'compare':
        slots, labels = ['before', 'after'], ['每次从头开始', '保留历史，接着完成']
        token_slot, token_destinations = 'token_before', ['token_after']
        title, takeaway = '同一个任务，两种处理方式', '保留上下文，把时间用在新的工作上。'
    else:
        slots, labels = ['top', 'left', 'center', 'right'], ['总任务', '规划', '执行', '结果']
        token_slot, token_destinations = 'token_top', ['token_center', 'token_right']
        title, takeaway = '把复杂任务拆成清楚的分工', '先知道谁负责什么，再看它们怎样协作。'
    elements = [dict(id=f'node{i}', kind='card', label=label, detail='', slot=slot)
                for i, (slot, label) in enumerate(zip(slots, labels))]
    elements.append(dict(id='request', kind='token', label='新请求', detail='', slot=token_slot))
    relations = [dict(id=f'edge{i}', source=f'node{i}', target=f'node{i+1}', label='')
                 for i in range(len(slots) - 1)]
    actions = []
    for i, element in enumerate(elements[:-1]):
        actions.append(dict(beat_id=ids[min(i, 2)], target=element['id'], kind='highlight',
                            placement='start', pace='normal', to_slot=None))
    for i, relation in enumerate(relations):
        actions.append(dict(beat_id=ids[min(i + 1, 2)], target=relation['id'], kind='draw',
                            placement='start', pace='normal', to_slot=None))
    for i, destination in enumerate(token_destinations):
        actions.append(dict(beat_id=ids[min(i + 1, 2)], target='request', kind='move',
                            placement='middle', pace='slow', to_slot=destination))
    return validate_motion_plan(dict(schema='video.motion-plan.v1', template=template, title=title,
                                    takeaway=takeaway, elements=elements, relations=relations,
                                    actions=actions), beats)
