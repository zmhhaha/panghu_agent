"""Bounded orthogonal routing for compiler 5; no browser or model decisions.

Coordinates are in the fixed 1280x720 compiler canvas. A visibility grid routes
between actual card boundaries with an explicit clearance around every other
object. Relationship labels occupy their own measured, wrapped SVG plates.
"""
from __future__ import annotations

import heapq
import math
import unicodedata


BOUNDS = (40, 174, 1240, 710)
CLEARANCE = 12
FONT_SIZE = 18
LINE_HEIGHT = 26


def inflate(box, amount):
    x, y, width, height = box
    return x - amount, y - amount, width + 2 * amount, height + 2 * amount


def overlaps(first, second):
    x, y, width, height = first
    a, b, w, h = second
    return x < a + w and a < x + width and y < b + h and b < y + height


def _inside(point, box):
    x, y, width, height = box
    return x < point[0] < x + width and y < point[1] < y + height


def segment_hits_box(start, end, box):
    """Touching a clearance boundary is allowed; crossing its interior is not."""
    x, y, width, height = box
    if start[0] == end[0]:
        return x < start[0] < x + width and max(min(start[1], end[1]), y) < min(max(start[1], end[1]), y + height)
    if start[1] == end[1]:
        return y < start[1] < y + height and max(min(start[0], end[0]), x) < min(max(start[0], end[0]), x + width)
    raise ValueError('Only orthogonal segments can be checked')


def _ports(box):
    x, y, width, height = box
    cx, cy = x + width / 2, y + height / 2
    return [((x + width, cy), (x + width + CLEARANCE, cy), 0),
            ((x, cy), (x - CLEARANCE, cy), 0),
            ((cx, y + height), (cx, y + height + CLEARANCE), 1),
            ((cx, y), (cx, y - CLEARANCE), 1)]


def _within(point):
    return BOUNDS[0] <= point[0] <= BOUNDS[2] and BOUNDS[1] <= point[1] <= BOUNDS[3]


def _simplify(points):
    result = []
    for point in points:
        if result and point == result[-1]:
            continue
        if len(result) >= 2 and (result[-2][0] == result[-1][0] == point[0]
                                 or result[-2][1] == result[-1][1] == point[1]):
            result[-1] = point
        else:
            result.append(point)
    return result


def route(source, target, boxes, label_boxes=()):
    """Find a deterministic shortest path with a cost for unnecessary bends."""
    obstacles = [inflate(box, CLEARANCE) for box in boxes]
    obstacles.extend(inflate(box, 6) for box in label_boxes)
    source_ports = [port for port in _ports(source) if _within(port[1])
                    and not any(_inside(port[1], box) for box in obstacles)
                    and not any(segment_hits_box(port[0], port[1], inflate(box, 4))
                                for box in boxes if box != source)]
    target_ports = [port for port in _ports(target) if _within(port[1])
                    and not any(_inside(port[1], box) for box in obstacles)
                    and not any(segment_hits_box(port[0], port[1], inflate(box, 4))
                                for box in boxes if box != target)]
    if not source_ports or not target_ports:
        raise ValueError('motion_relation_layout_unavailable')
    exits = [port[1] for port in source_ports + target_ports]
    xs = sorted({BOUNDS[0], BOUNDS[2], *(point[0] for point in exits),
                 *(value for x, _, width, _ in obstacles for value in (x, x + width)
                   if BOUNDS[0] <= value <= BOUNDS[2])})
    ys = sorted({BOUNDS[1], BOUNDS[3], *(point[1] for point in exits),
                 *(value for _, y, _, height in obstacles for value in (y, y + height)
                   if BOUNDS[1] <= value <= BOUNDS[3])})
    points = {(x, y) for x in xs for y in ys if not any(_inside((x, y), box) for box in obstacles)}
    neighbors = {point: [] for point in points}
    for direction, rows in ((0, [[(x, y) for x in xs] for y in ys]),
                            (1, [[(x, y) for y in ys] for x in xs])):
        for row in rows:
            for start, end in zip(row, row[1:]):
                if start in points and end in points and not any(segment_hits_box(start, end, box) for box in obstacles):
                    distance = abs(start[0] - end[0]) + abs(start[1] - end[1])
                    neighbors[start].append((end, direction, distance))
                    neighbors[end].append((start, direction, distance))
    queue, costs, previous = [], {}, {}
    for port, exit_point, direction in source_ports:
        state = exit_point, direction
        if state not in costs:
            costs[state] = CLEARANCE
            previous[state] = (None, port)
            heapq.heappush(queue, (CLEARANCE, exit_point, direction))
    while queue:
        cost, point, direction = heapq.heappop(queue)
        state = point, direction
        if cost != costs.get(state):
            continue
        for following, next_direction, length in neighbors[point]:
            next_state = following, next_direction
            candidate = cost + length + (18 if direction != next_direction else 0)
            if candidate < costs.get(next_state, math.inf):
                costs[next_state] = candidate
                previous[next_state] = (state, None)
                heapq.heappush(queue, (candidate, following, next_direction))
    choices = [(costs[(exit_point, direction)] + CLEARANCE + (18 if direction != port_direction else 0),
                exit_point, direction, port)
               for port, exit_point, port_direction in target_ports for direction in (0, 1)
               if (exit_point, direction) in costs]
    if not choices:
        raise ValueError('motion_relation_layout_unavailable')
    _, point, direction, target_port = min(choices)
    state, result = (point, direction), [target_port]
    while state is not None:
        result.append(state[0])
        before, source_port = previous[state]
        if before is None:
            result.append(source_port)
        state = before
    return _simplify(list(reversed(result)))


def path_text(points):
    return 'M ' + ' L '.join(f'{x:g} {y:g}' for x, y in points)


def _glyph_width(character):
    if unicodedata.combining(character):
        return 0
    # The pinned CJK font's Latin W/M are much wider than average Latin ink.
    # A full em is a conservative bound for both scripts, never a font shrink.
    return FONT_SIZE


def wrap_label(text, maximum):
    lines, line, width = [], '', 0
    for character in text:
        advance = _glyph_width(character)
        if line and width + advance > maximum:
            lines.append(line)
            line, width = '', 0
        line += character
        width += advance
    if line:
        lines.append(line)
    return lines


def place_label(text, points, boxes, previous_labels=(), previous_paths=(), *, accept=None,
                maximum_widths=(180, 138, 96, 66)):
    """Keep label ink and its plate clear of cards, tokens and other labels.

    The plate is painted after every connector; the underlying own line is
    visually interrupted, so no stroke crosses the glyphs. Later lines route
    around the reserved plate instead of obscuring it.
    """
    if not text:
        return dict(box=(points[0][0], points[0][1], 0, 0), lines=[])
    lengths = [abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    obstacles = [inflate(box, 4) for box in boxes] + [inflate(box, 8) for box in previous_labels]
    choices, elapsed, checked = [], 0, set()

    def choose(candidates):
        for _, x, y, width, height, lines in sorted(set(candidates)):
            box = x, y, width, height
            if box in checked:
                continue
            checked.add(box)
            if accept is None or accept(box):
                return dict(box=box, lines=list(lines))
        return None

    for start, end, length in zip(points, points[1:], lengths):
        for ratio in (.5, .25, .75):
            cx, cy = start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio
            for maximum in maximum_widths:
                lines = wrap_label(text, maximum)
                width = max(sum(_glyph_width(c) for c in line) for line in lines) + 18
                height = len(lines) * LINE_HEIGHT + 8
                if len(lines) > 5:
                    continue
                for offset in (0, 28, -28, 52, -52, 80, -80, 112, -112):
                    x = cx + (offset if start[0] == end[0] else 0) - width / 2
                    y = cy + (offset if start[1] == end[1] else 0) - height / 2
                    box = x, y, width, height
                    if not (_within((x, y)) and _within((x + width, y + height))):
                        continue
                    if any(overlaps(box, obstacle) for obstacle in obstacles):
                        continue
                    if any(segment_hits_box(a, b, inflate(box, 3)) for path in previous_paths for a, b in zip(path, path[1:])):
                        continue
                    score = abs(offset) + abs(elapsed + length * ratio - total / 2) * .18 + (len(lines) - 1) * 12
                    choices.append((score, x, y, width, height, tuple(lines)))
        elapsed += length
    selected = choose(choices)
    if selected is not None:
        return selected
    choices = []
    if selected is None:
        # A moving token can occupy the entire nearest gap. Try the bounded
        # empty cells between object edges instead of placing a label under it.
        xs = sorted({BOUNDS[0], BOUNDS[2], *(value for x, _, width, _ in obstacles
                      for value in (x, x + width) if BOUNDS[0] <= value <= BOUNDS[2])})
        ys = sorted({BOUNDS[1], BOUNDS[3], *(value for _, y, _, height in obstacles
                      for value in (y, y + height) if BOUNDS[1] <= value <= BOUNDS[3])})
        centers = [((left + right) / 2, (top + bottom) / 2)
                   for left, right in zip(xs, xs[1:]) for top, bottom in zip(ys, ys[1:])]
        for maximum in maximum_widths:
            lines = wrap_label(text, maximum)
            width = max(sum(_glyph_width(c) for c in line) for line in lines) + 18
            height = len(lines) * LINE_HEIGHT + 8
            if len(lines) > 5:
                continue
            for cx, cy in centers:
                x, y = cx - width / 2, cy - height / 2
                box = x, y, width, height
                if not (_within((x, y)) and _within((x + width, y + height))):
                    continue
                if any(overlaps(box, obstacle) for obstacle in obstacles):
                    continue
                distance = min(abs(cx - min(max(cx, min(a[0], b[0])), max(a[0], b[0])))
                               + abs(cy - min(max(cy, min(a[1], b[1])), max(a[1], b[1])))
                               for a, b in zip(points, points[1:]))
                choices.append((distance + (len(lines) - 1) * 12, x, y, width, height, tuple(lines)))
    selected = choose(choices)
    if selected is None:
        raise ValueError('motion_relation_layout_unavailable')
    return selected


def connection_geometry(relations, boxes, reserved_boxes=(), label_obstacles=(), *,
                        maximum_widths=(180, 138, 96, 66)):
    labels, result = [], {}
    obstacles = [*boxes.values(), *reserved_boxes]
    for relation in relations:
        points = route(boxes[relation['source']], boxes[relation['target']], obstacles, labels)

        def preserves_connections(candidate):
            # A plate can fit a narrow gap yet close the only exit of a card
            # or token. Keep every relation reachable while reserving labels;
            # already placed labels must also retain their own anchor routes.
            placements = {identity: item['box'] for identity, item in result.items() if item['lines']}
            placements[relation['id']] = candidate
            try:
                for pending in relations:
                    other_labels = [box for identity, box in placements.items() if identity != pending['id']]
                    source, target = boxes[pending['source']], boxes[pending['target']]
                    if pending['id'] in placements:
                        x, y, width, height = placements[pending['id']]
                        anchor = x + width / 2, y + height / 2, 0, 0
                        route(source, anchor, obstacles, other_labels)
                        route(anchor, target, obstacles, other_labels)
                    else:
                        route(source, target, obstacles, other_labels)
            except ValueError:
                return False
            return True

        label = place_label(relation['label'], points, [*obstacles, *label_obstacles], labels,
                            accept=preserves_connections, maximum_widths=maximum_widths)
        result[relation['id']] = dict(points=points, path=path_text(points), **label)
        if label['lines']:
            labels.append(label['box'])
    # A later label can occupy a gap used by an earlier line. Re-route every
    # connector against all *other* plates; its own plate masks its own stroke.
    for relation in relations:
        own = result[relation['id']]
        other_labels = [item['box'] for identity, item in result.items()
                        if identity != relation['id'] and item['lines']]
        if own['lines']:
            x, y, width, height = own['box']
            anchor = x + width / 2, y + height / 2, 0, 0
            first = route(boxes[relation['source']], anchor, obstacles, other_labels)
            second = route(anchor, boxes[relation['target']], obstacles, other_labels)
            points = _simplify([*first, *second])
        else:
            points = route(boxes[relation['source']], boxes[relation['target']], obstacles, other_labels)
        own.update(points=points, path=path_text(points))
    return result
