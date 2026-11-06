"""Compiler 6 semantic layouts and deterministic pre-render checks.

The historical compiler 1--5 never imports these rules. Named containment and
ordered lanes remain data, rather than arbitrary model-authored HTML or CSS.
"""
from __future__ import annotations

import unicodedata

from agent_service.contracts import ServiceError
from .motion_geometry import (connection_geometry, inflate, overlaps, path_text,
                              route, wrap_label)


LAYOUTS = {
    'sequence': {
        'step_1': (72, 332, 240, 148), 'step_2': (370, 332, 240, 148),
        'step_3': (668, 332, 240, 148), 'step_4': (966, 332, 240, 148),
        'note': (72, 534, 1136, 90), 'caption': (72, 636, 1136, 42),
        'token_1': (132, 198, 120, 44), 'token_2': (430, 198, 120, 44),
        'token_3': (728, 198, 120, 44), 'token_4': (1026, 198, 120, 44),
    },
    'nested': {
        'outer': (72, 204, 770, 352), 'inner': (98, 296, 718, 234),
        'request': (120, 394, 284, 110), 'tool': (510, 394, 284, 110),
        'result': (908, 260, 300, 132), 'decision': (908, 440, 300, 116),
        'caption': (72, 636, 1136, 42),
    },
}


def text_width(value, size):
    """Conservative CJK/Latin advance budget, not a substitute for DOM review."""
    return sum(0 if unicodedata.combining(c) else size * (
        1 if unicodedata.east_asian_width(c) in {'F', 'W'} or c in 'MW@%' else .62)
               for c in value)


def text_lines(value, width, size):
    if not value:
        return 0
    # The generated HTML uses normal whitespace, not pre/pre-wrap.
    value = ' '.join(value.split())
    count, advance = 1, 0
    for char in value:
        step = text_width(char, size)
        if advance and advance + step > width:
            count, advance = count + 1, 0
        advance += step
    return count


def source_layout(plan):
    if not plan.get('sources'):
        return None
    takeaway_height = max(32, text_lines(plan['takeaway'], 1100, 20) * 32)
    shared_label = (plan['sources'][0]['label'] if len({s['label'] for s in plan['sources']}) == 1
                    else None)
    source_height = (text_lines(shared_label, 1100, 18) * 25 +
                     sum(text_lines(s['url'], 1100, 18) * 25 for s in plan['sources']) + 8
                     if shared_label is not None else
                     sum((text_lines(s['label'], 1100, 18) +
                          text_lines(s['url'], 1100, 18)) * 25 + 8 for s in plan['sources']))
    if takeaway_height + source_height + 8 > 140:
        raise ServiceError('motion_text_overflow', 422)
    return dict(top=568, height=140, takeaway_height=takeaway_height,
                source_top=568 + takeaway_height + 4, shared_label=shared_label)


def layout_for_sources(plan, layout):
    if not plan.get('sources'):
        return dict(layout)
    result = dict(layout)
    if plan['template'] == 'flow':
        for key in ('source', 'queue', 'worker'):
            x, _, w, h = result[key]
            result[key] = x, 272, w, h
        for key in ('token_source', 'token_queue', 'token_worker'):
            x, _, w, h = result[key]
            result[key] = x, 210, w, h
        result['note'] = (72, 452, 1136, 102)
    elif plan['template'] == 'compare':
        for key in ('before', 'after'):
            x, _, w, _ = result[key]
            result[key] = x, 256, w, 182
        result['note'] = (72, 452, 1136, 102)
    elif plan['template'] == 'focus':
        for key in ('left', 'center', 'right'):
            x, _, w, _ = result[key]
            result[key] = x, 322, w, 128
        for key in ('token_left', 'token_right'):
            x, _, w, h = result[key]
            result[key] = x, 268, w, h
        # The center token cannot stay above the lower center card after the
        # footer makes that gap smaller: it would cover the top card. Use the
        # free upper-left area, keeping both token slots separate if occupied.
        _, _, width, height = result['token_center']
        result['token_center'] = 222 - width / 2, 204, width, height
        result['note'] = (72, 476, 1136, 78)
    elif plan['template'] == 'sequence':
        result['note'] = (72, 488, 1136, 66)
    result['caption'] = (72, 568, 1136, 36)
    return result


def adapt_layout(plan, layout):
    """Use available vertical space before asking users to shorten valid text."""
    result = layout_for_sources(plan, layout)
    for row in plan['elements']:
        if row['kind'] != 'card' or row['slot'] in {'note'}:
            continue
        x, y, width, height = result[row['slot']]
        padding = 18 if row['slot'] == 'top' else 32
        needed = text_lines(row['label'], width - 32, 26) * 38 + (
            text_lines(row['detail'], width - 32, 18) * 27 + 6 if row['detail'] else 0) + padding
        # Real line boxes remain integral, at the same readable font sizes.
        bottom = 554 if plan.get('sources') else 518
        if row['slot'] == 'top':
            bottom = min(bottom, 340)
        elif plan['template'] == 'sequence':
            bottom = min(bottom, 480)
        if needed > height and y + needed <= bottom:
            result[row['slot']] = x, y, width, needed
    return result


def obstacle_boxes(plan, boxes):
    """Transparent container bodies allow wires; their headers remain solid."""
    return {row['id']: ((*boxes[row['id']][:3], 96) if row['kind'] == 'container'
                       else boxes[row['id']]) for row in plan['elements']}


def geometry(plan, boxes, reserved, moving=()):
    obstacles = obstacle_boxes(plan, boxes)
    if plan['template'] != 'sequence':
        return connection_geometry(plan['relations'], obstacles, reserved, moving,
                                   maximum_widths=(360, 270, 216, 180, 138, 96, 66))
    # Sequence meaning is positional. Give a return path a different corridor
    # from the left-to-right request path, even for the same pair of endpoints.
    result, labels = {}, []
    all_boxes = [*obstacles.values(), *reserved, *moving]
    for relation in plan['relations']:
        source, target = boxes[relation['source']], boxes[relation['target']]
        forward = source[0] < target[0]
        lane = 278 if forward else 510
        midpoint = (source[0] + source[2] / 2 + target[0] + target[2] / 2) / 2
        lines = wrap_label(relation['label'], 216)
        if len(lines) > 2:
            raise ValueError('motion_relation_layout_unavailable')
        width = max((18 * len(line) for line in lines), default=0) + (18 if lines else 0)
        height = len(lines) * 26 + (8 if lines else 0)
        selected = None
        candidates = [(dx, dy) for dy in (0, -26, 26, -52, 52)
                      for dx in (0, -80, 80, -160, 160)]
        for horizontal, offset in candidates:
            cx, cy = midpoint + horizontal, lane + offset
            label_box = cx - width / 2, cy - height / 2, width, height
            if any(overlaps(label_box, inflate(other, 6)) for other in [*all_boxes, *labels]):
                continue
            if (label_box[0] < 40 or label_box[0] + width > 1240 or
                    label_box[1] < 190 or label_box[1] + height > 558):
                continue
            anchor = cx, cy, 0, 0
            try:
                first = route(source, anchor, all_boxes, labels)
                second = route(anchor, target, all_boxes, labels)
            except ValueError:
                continue
            selected = dict(points=[*first, *second[1:]], box=label_box, lines=lines)
            break
        if selected is None:
            raise ValueError('motion_relation_layout_unavailable')
        selected['path'] = path_text(selected['points'])
        result[relation['id']] = selected
        if lines:
            labels.append(selected['box'])
    # Reroute against every other plate, including plates selected later.
    for relation in plan['relations']:
        own = result[relation['id']]
        x, y, w, h = own['box']
        anchor = x + w / 2, y + h / 2, 0, 0
        others = [r['box'] for identity, r in result.items() if identity != relation['id'] and r['lines']]
        first = route(boxes[relation['source']], anchor, all_boxes, others)
        second = route(anchor, boxes[relation['target']], all_boxes, others)
        own.update(points=[*first, *second[1:]], path=path_text([*first, *second[1:]]))
    return result


def check_text(plan, boxes):
    """Reject predictable overflow before audio or a render Job is requested."""
    if text_lines(plan['title'], 1136, 36) > 2:
        raise ServiceError('motion_text_overflow', 422)
    for row in plan['elements']:
        _, _, width, height = boxes[row['id']]
        kind = row['kind']
        if kind == 'token':
            needed = 27 + 16
            fits = text_width(row['label'], 18) <= width - 24
        elif kind == 'label':
            needed = text_lines(row['label'], width, 21) * 31
            fits = True
        else:
            padding, margin = (20, 4) if row['slot'] == 'note' else (
                (18, 6) if row['slot'] == 'top' else (32, 6))
            needed = text_lines(row['label'], width - 32, 26) * 38 + (
                text_lines(row['detail'], width - 32, 18) * 27 + margin if row['detail'] else 0) + padding
            fits = True
            if kind == 'container':
                height = 96
                needed = text_lines(row['label'], width - 32, 24) * 36 + (
                    text_lines(row['detail'], width - 32, 18) * 27 + 4 if row['detail'] else 0) + 24
            elif row['slot'] == 'note' and plan.get('sources'):
                # Share the row horizontally instead of shrinking typography or
                # losing the original version/date copy to the source footer.
                needed = max(text_lines(row['label'], (width - 32) * .4, 26) * 38,
                             text_lines(row['detail'], (width - 32) * .6 - 16, 18) * 27) + 20
        if not fits or needed > height:
            raise ServiceError('motion_text_overflow', 422)
    source_layout(plan)
