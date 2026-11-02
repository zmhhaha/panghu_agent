"""Bounded, read-only checks for a deliberately small SVG diagram vocabulary.

Explicit ``data-pptx-role=node`` rectangles and ``connector`` lines/polylines
form the strict contract. Optional data-pptx-source/target attributes refer to
those node IDs. Unannotated card diagrams get advisory findings only: a grid,
rule or ordinary chart is not rejected by this heuristic. This is geometry,
not proof that a diagram agrees with its source or that a slide looks good.
Unsupported drawing features are reported as unchecked, never called passed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import xml.etree.ElementTree as ET


SCHEMA = "ppt-service.diagram-geometry.v1"
_SVG = "{http://www.w3.org/2000/svg}"
_NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"
_NUM_RE = re.compile(_NUMBER)
_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,95}\Z")
_MAX_ELEMENTS = 8192
_MAX_DEPTH = 64
_MAX_SHAPES = 512
_MAX_COMPARISONS = 65536
_MAX_FINDINGS = 64
_MAX_NUMBER_LIST_CHARS = 8192
_MAX_NUMBERS = 256
_MAX_SCALAR_CHARS = 128
_MAX_TRANSFORM_CHARS = 4096
_MAX_TRANSFORM_OPERATIONS = 32
_MAX_TRANSFORM_ARGUMENT_CHARS = 256
_MAX_INLINE_STYLE_CHARS = 4096
_TOLERANCE = 2.0
_EPS = 1e-7


def _numbers(value, *, max_values=_MAX_NUMBERS, max_chars=_MAX_NUMBER_LIST_CHARS):
    # Reject attribute size before tokenization. In particular, never build a
    # list of millions of regex Match objects from one untrusted points value.
    if not value or len(value) > max_chars:
        raise ValueError
    values, position = [], 0
    for match in _NUM_RE.finditer(value):
        if (len(values) >= max_values or match.end() - match.start() > _MAX_SCALAR_CHARS
                or value[position:match.start()].strip(" ,\t\r\n")):
            raise ValueError
        number = float(match.group())
        if not math.isfinite(number) or abs(number) > 1e8:
            raise ValueError
        values.append(number)
        position = match.end()
    if not values or value[position:].strip(" ,\t\r\n"):
        raise ValueError
    return values


def _length(value, default=0.0):
    if value is None:
        return default
    if len(value) > _MAX_SCALAR_CHARS:
        raise ValueError
    value = value.strip()
    if value.endswith("px"):
        value = value[:-2]
    values = _numbers(value, max_values=1, max_chars=_MAX_SCALAR_CHARS)
    if len(values) != 1:
        raise ValueError
    return values[0]


def _multiply(left, right):
    a, d, e, f = left
    aa, dd, ee, ff = right
    result = (a * aa, d * dd, a * ee + e, d * ff + f)
    if not all(math.isfinite(v) and abs(v) <= 1e10 for v in result):
        raise ValueError
    return result


def _transform(value):
    result = (1.0, 1.0, 0.0, 0.0)
    if value is not None and len(value) > _MAX_TRANSFORM_CHARS:
        raise ValueError
    if not value or not value.strip():
        return result
    position, operations = 0, 0
    for match in re.finditer(r"([A-Za-z]+)\s*\(([^()]*)\)", value):
        operations += 1
        if operations > _MAX_TRANSFORM_OPERATIONS:
            raise ValueError
        if value[position:match.start()].strip(" ,\t\r\n"):
            raise ValueError
        name, raw = match.groups()
        values = _numbers(raw, max_values=6, max_chars=_MAX_TRANSFORM_ARGUMENT_CHARS)
        if name == "translate" and len(values) in (1, 2):
            operation = (1, 1, values[0], values[1] if len(values) == 2 else 0)
        elif name == "scale" and len(values) in (1, 2):
            operation = (values[0], values[-1], 0, 0)
        elif name == "matrix" and len(values) == 6 and values[1] == values[2] == 0:
            operation = (values[0], values[3], values[4], values[5])
        else:
            raise ValueError
        if operation[0] == 0 or operation[1] == 0:
            raise ValueError
        result = _multiply(result, operation)
        position = match.end()
    if position == 0 or value[position:].strip(" ,\t\r\n"):
        raise ValueError
    return result


def _point(transform, x, y):
    a, d, e, f = transform
    return a * x + e, d * y + f


def _paint(value):
    """Return alpha for known solid paint, or None for unsupported paint."""
    value = value.strip().lower()
    if value in ("none", "transparent"):
        return 0.0
    if re.fullmatch(r"#[0-9a-f]{3}(?:[0-9a-f])?", value):
        return int(value[-1], 16) / 15 if len(value) == 5 else 1.0
    if re.fullmatch(r"#[0-9a-f]{6}(?:[0-9a-f]{2})?", value):
        return int(value[-2:], 16) / 255 if len(value) == 9 else 1.0
    if re.fullmatch(r"[a-z]+", value) and value not in ("inherit", "currentcolor", "contextfill", "contextstroke"):
        return 1.0
    # rgb() is solid; gradients, variables, CSS color functions and resources
    # need a real renderer and are intentionally outside this geometry check.
    if re.fullmatch(r"rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)", value):
        return 1.0
    return None


def _properties(element, inherited):
    result = dict(inherited)
    values = dict(element.attrib)
    raw_style = element.get("style", "")
    if len(raw_style) > _MAX_INLINE_STYLE_CHARS:
        raise ValueError
    for declaration in raw_style.split(";"):
        if declaration.strip():
            key, separator, value = declaration.partition(":")
            if not separator:
                raise ValueError
            values[key.strip().lower()] = value.strip()
    for key in ("fill", "stroke", "fill-opacity", "stroke-opacity", "stroke-width", "stroke-dasharray", "visibility"):
        if key in values:
            result[key] = values[key]
    result["displayed"] = inherited["displayed"] and values.get("display", "") != "none"
    opacity = _length(values.get("opacity"), 1)
    if not 0 <= opacity <= 1:
        raise ValueError
    result["opacity"] = inherited["opacity"] * opacity
    for key in ("clip-path", "mask", "filter"):
        if values.get(key, "none") != "none":
            result["complex"] = True
    if result.get("stroke-dasharray", "none") not in ("none", ""):
        result["complex"] = True
    if values.get("mix-blend-mode", "normal") != "normal" or values.get("isolation", "auto") != "auto":
        result["complex"] = True
    # CSS transforms, shorthand declarations, and nonstandard visibility are
    # not silently approximated by the XML-attribute implementation.
    if "transform" in raw_style.lower() or values.get("visibility", "visible") not in ("visible", "hidden", "collapse"):
        result["complex"] = True
    return result


@dataclass(frozen=True)
class _Rect:
    identifier: str
    raw_id: str | None
    ordinal: int
    left: float
    top: float
    right: float
    bottom: float
    rx: float
    ry: float
    alpha: float
    explicit: bool

    def contains(self, point, tolerance=_TOLERANCE):
        x, y = point
        return self.left - tolerance <= x <= self.right + tolerance and self.top - tolerance <= y <= self.bottom + tolerance

    def boundary(self, point):
        if not self.contains(point):
            return False
        x, y = point
        # Straight edges and rounded corners share the same bounded tolerance.
        horizontal = self.left + self.rx <= x <= self.right - self.rx and min(abs(y - self.top), abs(y - self.bottom)) <= _TOLERANCE
        vertical = self.top + self.ry <= y <= self.bottom - self.ry and min(abs(x - self.left), abs(x - self.right)) <= _TOLERANCE
        if horizontal or vertical:
            return True
        if self.rx <= 0 or self.ry <= 0:
            return False
        cx = self.left + self.rx if x < self.left + self.rx else self.right - self.rx
        cy = self.top + self.ry if y < self.top + self.ry else self.bottom - self.ry
        corner = (x <= self.left + self.rx or x >= self.right - self.rx) and (y <= self.top + self.ry or y >= self.bottom - self.ry)
        if not corner:
            return False
        # Radial ellipse distance is only used to accept an approximate
        # endpoint, never to establish an obstacle intersection.
        distance = abs(math.hypot((x - cx) / self.rx, (y - cy) / self.ry) - 1)
        return distance * max(self.rx, self.ry) <= _TOLERANCE

    def crosses(self, start, end):
        # The union of these strips is guaranteed to be inside even a rounded
        # card. It avoids reporting a corner touch as a visual intersection.
        strips = ((self.left + self.rx, self.top, self.right - self.rx, self.bottom),
                  (self.left, self.top + self.ry, self.right, self.bottom - self.ry))
        return any(_segment_inside(start, end, bounds) for bounds in strips)


@dataclass(frozen=True)
class _Edge:
    identifier: str
    ordinal: int
    points: tuple
    explicit: bool
    source: str | None
    target: str | None


def _segment_inside(start, end, bounds):
    left, top, right, bottom = bounds
    left += _EPS; top += _EPS; right -= _EPS; bottom -= _EPS
    if left >= right or top >= bottom:
        return False
    x, y = start
    dx, dy = end[0] - x, end[1] - y
    lower, upper = 0.0, 1.0
    for origin, delta, low, high in ((x, dx, left, right), (y, dy, top, bottom)):
        if abs(delta) < _EPS:
            if not low < origin < high:
                return False
        else:
            first, last = sorted(((low - origin) / delta, (high - origin) / delta))
            lower, upper = max(lower, first), min(upper, last)
            if upper <= lower:
                return False
    return upper - lower > _EPS


def inspect_svg_diagram(svg: ET.Element):
    """Inspect XML already parsed by the bounded safe-source validator.

    Report fields contain fixed codes and bounded identifiers only, never SVG
    text, arbitrary attributes or paths. ``checked`` covers supported explicit
    geometry only; it does not certify semantic relations or other SVG shapes.
    """
    errors, advisories, unchecked = [], [], []
    counts = {"explicit_connectors": 0, "checked_connectors": 0,
              "legacy_candidates": 0, "unsupported_elements": 0,
              "comparisons": 0, "truncated": False}
    rectangles, edges, node_ids = [], [], {}
    total = 0

    def finding(destination, code, identifier, related=None):
        if len(destination) >= _MAX_FINDINGS:
            counts["truncated"] = True
            return
        row = {"code": code, "element_id": identifier}
        if related is not None:
            row["related_id"] = related
        if row not in destination:
            destination.append(row)

    def unsupported(code, identifier):
        counts["unsupported_elements"] += 1
        finding(unchecked, code, identifier)

    stylesheet = False
    # Discover declared node identities even beneath an unsupported/hidden
    # ancestor. A reference to such a node is unchecked, not falsely missing.
    for index, element in enumerate(svg.iter()):
        if index >= _MAX_ELEMENTS:
            counts["truncated"] = True
            unsupported("svg_diagram_element_limit", "svg-root")
            break
        stylesheet = stylesheet or element.tag == _SVG + "style" and bool((element.text or "").strip())
        raw_id = element.get("id")
        if element.get("data-pptx-role") == "node" and raw_id:
            node_ids[raw_id] = node_ids.get(raw_id, 0) + 1
    defaults = {"fill": "black", "stroke": "none", "fill-opacity": "1", "stroke-opacity": "1",
                "stroke-width": "1", "stroke-dasharray": "none", "visibility": "visible", "displayed": True,
                "opacity": 1.0, "complex": stylesheet}
    if stylesheet:
        unsupported("svg_diagram_stylesheet_unchecked", "svg-root")

    def walk(element, parent_transform, inherited, definition=False, depth=0):
        nonlocal total
        total += 1
        if total > _MAX_ELEMENTS:
            counts["truncated"] = True
            return
        ordinal = total
        raw_id = element.get("id")
        identifier = raw_id if raw_id and _ID.fullmatch(raw_id) else f"element-{ordinal}"
        if depth > _MAX_DEPTH:
            counts["truncated"] = True
            unsupported("svg_diagram_depth_limit", identifier)
            return
        tag = element.tag.removeprefix(_SVG) if isinstance(element.tag, str) else ""
        explicit = element.get("data-pptx-role")
        try:
            properties = _properties(element, inherited)
            transform = _multiply(parent_transform, _transform(element.get("transform")))
        except (ValueError, OverflowError):
            unsupported("svg_diagram_transform_or_style_unchecked", identifier)
            return
        definition = definition or tag in ("defs", "clipPath", "mask", "marker", "pattern", "symbol")
        if definition or not properties["displayed"] or properties["opacity"] == 0:
            return
        visible = properties["visibility"] == "visible"
        if properties["complex"] or (tag == "svg" and element is not svg):
            unsupported("svg_diagram_effect_unchecked", identifier)
            return
        try:
            if visible and tag == "rect":
                if len(rectangles) >= _MAX_SHAPES:
                    unsupported("svg_diagram_shape_limit", identifier)
                    return
                x, y = _length(element.get("x")), _length(element.get("y"))
                width, height = _length(element.get("width")), _length(element.get("height"))
                if width < 0 or height < 0:
                    raise ValueError
                if width and height:
                    fill = _paint(properties["fill"])
                    fill_opacity = _length(properties["fill-opacity"], 1)
                    if fill is None or not 0 <= fill_opacity <= 1:
                        raise ValueError
                    rx = _length(element.get("rx"), _length(element.get("ry")))
                    ry = _length(element.get("ry"), rx)
                    if rx < 0 or ry < 0:
                        raise ValueError
                    if rx == 0 or ry == 0:
                        rx = ry = 0
                    first, last = _point(transform, x, y), _point(transform, x + width, y + height)
                    left, right = sorted((first[0], last[0])); top, bottom = sorted((first[1], last[1]))
                    rectangles.append(_Rect(identifier, raw_id, ordinal, left, top, right, bottom,
                        min(rx, width / 2) * abs(transform[0]), min(ry, height / 2) * abs(transform[1]),
                        fill * fill_opacity * properties["opacity"], explicit == "node"))
            elif visible and tag in ("line", "polyline"):
                if explicit == "connector":
                    counts["explicit_connectors"] += 1
                stroke = _paint(properties["stroke"])
                stroke_opacity = _length(properties["stroke-opacity"], 1)
                stroke_width = _length(properties["stroke-width"], 1)
                if stroke is None or not 0 <= stroke_opacity <= 1 or stroke_width < 0:
                    raise ValueError
                if stroke and stroke_opacity and stroke_width:
                    if len(edges) >= _MAX_SHAPES:
                        unsupported("svg_diagram_shape_limit", identifier)
                        return
                    if tag == "line":
                        points = [(_length(element.get("x1")), _length(element.get("y1"))),
                                  (_length(element.get("x2")), _length(element.get("y2")))]
                    else:
                        numbers = _numbers(element.get("points", ""))
                        if len(numbers) < 4 or len(numbers) % 2 or len(numbers) > 256:
                            raise ValueError
                        points = list(zip(numbers[::2], numbers[1::2]))
                    transformed = tuple(_point(transform, *point) for point in points)
                    if any(abs(v) > 1e10 for point in transformed for v in point):
                        raise ValueError
                    edges.append(_Edge(identifier, ordinal, transformed, explicit == "connector",
                        element.get("data-pptx-source"), element.get("data-pptx-target")))
            elif visible and explicit in ("connector", "node"):
                if explicit == "connector":
                    counts["explicit_connectors"] += 1
                unsupported("svg_diagram_shape_unchecked", identifier)
        except (ValueError, OverflowError):
            unsupported("svg_diagram_geometry_unchecked", identifier)
        for child in element:
            if total >= _MAX_ELEMENTS:
                counts["truncated"] = True
                break
            walk(child, transform, properties, definition, depth + 1)

    walk(svg, (1, 1, 0, 0), defaults)
    nodes = {rect.raw_id: rect for rect in rectangles if rect.explicit and rect.raw_id and node_ids.get(rect.raw_id) == 1}
    for edge in edges:
        endpoints = (edge.points[0], edge.points[-1])
        excluded = set()
        if edge.explicit:
            counts["checked_connectors"] += 1
            for name, point in ((edge.source, endpoints[0]), (edge.target, endpoints[1])):
                if name is None:
                    continue
                node = nodes.get(name)
                if node is None:
                    invalid = node_ids.get(name, 0) > 1 or (name not in node_ids and not counts["truncated"])
                    code = "svg_diagram_node_reference_invalid" if invalid else "svg_diagram_node_reference_unchecked"
                    if code.endswith("unchecked"):
                        unsupported(code, edge.identifier)
                    else:
                        finding(errors, code, edge.identifier)
                else:
                    excluded.add(node.ordinal)
                    if not node.boundary(point):
                        finding(errors, "svg_diagram_endpoint_misaligned", edge.identifier, node.identifier)
            obstacles = [rect for rect in rectangles if rect.explicit]
        else:
            touching = [{rect.ordinal for rect in rectangles if rect.boundary(point)} for point in endpoints]
            if not touching[0] or not touching[1] or touching[0] & touching[1]:
                continue
            counts["legacy_candidates"] += 1
            excluded.update(touching[0] | touching[1])
            obstacles = rectangles
        for rect in obstacles:
            if counts["comparisons"] >= _MAX_COMPARISONS:
                counts["truncated"] = True
                unsupported("svg_diagram_comparison_limit", edge.identifier)
                break
            counts["comparisons"] += 1
            if rect.ordinal in excluded or rect.alpha <= 0 or any(rect.contains(point) for point in endpoints):
                continue
            # Legacy heuristics avoid complaining about a visually hidden
            # decorative line. Explicit edges promise a valid route even when
            # an opaque obstacle would cover the crossing in paint order.
            if not edge.explicit and rect.ordinal > edge.ordinal and rect.alpha == 1:
                continue
            if any(rect.crosses(first, last) for first, last in zip(edge.points, edge.points[1:])):
                finding(errors if edge.explicit else advisories,
                        "svg_diagram_connector_crosses_node", edge.identifier, rect.identifier)
    if errors:
        status = "needs_changes"
    elif advisories:
        status = "advisory"
    elif unchecked or counts["truncated"]:
        status = "unchecked"
    elif counts["checked_connectors"]:
        status = "checked"
    else:
        status = "not_applicable"
    return {"schema": SCHEMA, "status": status, "errors": errors,
            "advisories": advisories, "coverage": {**counts, "unchecked": unchecked,
            "semantic_relations_verified": False, "scope": "axis_aligned_rect_line_polyline"}}
