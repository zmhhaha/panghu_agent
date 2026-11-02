"""Geometry regressions; these checks do not certify diagram semantics."""
import xml.etree.ElementTree as ET

import pytest

from ppt_service.control.diagram_geometry import inspect_svg_diagram


def inspect(body):
    return inspect_svg_diagram(ET.fromstring(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">' + body + '</svg>'))


def node(identifier, x, y, width=120, height=80, extra=""):
    return (f'<rect id="{identifier}" data-pptx-role="node" x="{x}" y="{y}" '
            f'width="{width}" height="{height}" fill="#123456" {extra}/>')


def connector(points='x1="60" y1="80" x2="60" y2="300"', extra=""):
    return (f'<line id="edge" data-pptx-role="connector" stroke="#0af" '
            f'{points} {extra}/>')


def test_original_third_page_routes_are_advisory_not_a_fabricated_semantic_pass():
    # Exact card and line geometry from 03_profiles.svg, including rounded
    # corners, inherited colors, the containing background and outline base.
    report = inspect('<rect width="1280" height="720" fill="#071426"/>'
        '<g fill="#10253D"><rect x="56" y="220" width="396" height="120" rx="12"/>'
        '<rect x="476" y="220" width="396" height="120" rx="12"/>'
        '<rect x="56" y="364" width="396" height="120" rx="12"/>'
        '<rect x="476" y="364" width="396" height="120" rx="12"/></g>'
        '<g stroke="#46E3D0" stroke-width="2"><line x1="64" y1="340" x2="64" y2="524"/>'
        '<line x1="484" y1="340" x2="484" y2="524"/>'
        '<line x1="430" y1="484" x2="430" y2="524"/>'
        '<line x1="850" y1="484" x2="850" y2="524"/></g>'
        '<rect x="56" y="524" width="816" height="80" rx="12" fill="none" stroke="#46E3D0"/>')
    assert report['errors'] == []
    assert len(report['advisories']) == 2
    assert {finding['code'] for finding in report['advisories']} == {'svg_diagram_connector_crosses_node'}
    assert report['coverage']['legacy_candidates'] == 4
    assert report['coverage']['semantic_relations_verified'] is False


def test_explicit_route_through_unrelated_node_is_strict():
    report = inspect(node('source', 0, 0) + node('blocker', 0, 120)
        + node('target', 0, 300) + connector(extra='data-pptx-source="source" data-pptx-target="target"'))
    assert report['status'] == 'needs_changes'
    assert report['errors'] == [{'code': 'svg_diagram_connector_crosses_node',
        'element_id': 'edge', 'related_id': 'blocker'}]


def test_correct_peer_profiles_to_shared_base_have_no_intermediate_obstacles():
    cards = ''.join(node(f'profile{i}', 20 + i * 240, 180, 200, 100) for i in range(4))
    base = node('base', 20, 420, 920, 100)
    links = ''.join(f'<line id="edge{i}" data-pptx-role="connector" '
        f'data-pptx-source="profile{i}" data-pptx-target="base" '
        f'x1="{120 + i * 240}" y1="280" x2="{120 + i * 240}" y2="420" stroke="cyan"/>' for i in range(4))
    report = inspect(cards + base + node('independent', 1000, 180, 200, 340) + links)
    assert report['status'] == 'checked'
    assert report['errors'] == report['advisories'] == []
    assert report['coverage']['checked_connectors'] == 4


def test_shared_grouping_without_lines_does_not_claim_connector_validation():
    report = inspect(node('group', 10, 10, 900, 600) + node('web', 50, 100) + node('sdk', 250, 100))
    assert report['status'] == 'not_applicable'
    assert report['coverage']['checked_connectors'] == 0


def test_background_container_and_in_card_separator_are_not_crossings():
    report = inspect(node('panel', 0, 0, 800, 600) + node('card', 50, 100, 200, 200)
        + connector('x1="50" y1="200" x2="250" y2="200"'))
    assert report['errors'] == report['advisories'] == []


def test_grid_lines_and_decoration_do_not_become_strict_connectors():
    report = inspect('<rect x="100" y="100" width="100" height="100" fill="white"/>'
        '<g stroke="gray"><line x1="0" y1="150" x2="500" y2="150"/>'
        '<line x1="0" y1="250" x2="500" y2="250"/></g>')
    assert report['errors'] == report['advisories'] == []
    assert report['coverage']['legacy_candidates'] == 0


@pytest.mark.parametrize('extra', ['fill="none"', 'fill="transparent"', 'fill="#0000"', 'fill="#00000000"',
    'fill="red" fill-opacity="0"', 'fill="red" opacity="0"', 'fill="red" display="none"', 'fill="red" visibility="hidden"'])
def test_invisible_cards_are_not_obstacles(extra):
    report = inspect(f'<rect data-pptx-role="node" x="0" y="120" width="120" height="80" {extra}/>'
        + connector())
    assert report['errors'] == []


@pytest.mark.parametrize('extra', ['stroke="none"', 'stroke="transparent"', 'stroke="blue" stroke-opacity="0"',
    'stroke="blue" stroke-width="0"', 'stroke="blue" opacity="0"', 'stroke="blue" display="none"'])
def test_invisible_connectors_are_not_crossings(extra):
    report = inspect(node('obstacle', 0, 120)
        + f'<line data-pptx-role="connector" x1="60" y1="80" x2="60" y2="300" {extra}/>' )
    assert report['errors'] == []


def test_explicit_route_crossing_is_invalid_even_if_card_is_painted_over_it():
    report = inspect(connector() + node('obstacle', 0, 120))
    assert report['errors'][0]['code'] == 'svg_diagram_connector_crosses_node'
    translucent = inspect(connector() + node('obstacle', 0, 120, extra='opacity="0.5"'))
    assert len(translucent['errors']) == 1


def test_legacy_opaque_card_painted_over_line_does_not_trigger_visual_advisory():
    report = inspect(node('source', 0, 0) + node('target', 0, 300)
        + '<line x1="60" y1="80" x2="60" y2="300" stroke="cyan"/>' + node('obstacle', 0, 120))
    assert report['errors'] == report['advisories'] == []
    assert report['coverage']['legacy_candidates'] == 1


@pytest.mark.parametrize('transform', ['translate(10,20) scale(2)', 'matrix(2 0 0 2 10 20)',
    'translate(100 200) scale(-2 -3)'])
def test_axis_aligned_group_transforms_preserve_crossings(transform):
    report = inspect(f'<g transform="{transform}">' + node('obstacle', 0, 120) + connector() + '</g>')
    assert report['errors'][0]['code'] == 'svg_diagram_connector_crosses_node'


@pytest.mark.parametrize('feature', ['transform="rotate(20)"', 'transform="matrix(1 1 0 1 0 0)"',
    'clip-path="url(#clip)"', 'mask="url(#mask)"', 'filter="url(#shadow)"', 'style="transform:rotate(20deg)"',
    'stroke-dasharray="10 20"', 'style="stroke-dasharray: 10 20"', 'style="mix-blend-mode:multiply"'])
def test_complex_effects_are_unchecked_not_a_pass(feature):
    report = inspect(f'<g {feature}>' + node('obstacle', 0, 120) + connector() + '</g>')
    assert report['status'] == 'unchecked'
    assert report['coverage']['unsupported_elements'] > 0
    assert report['errors'] == []


def test_stylesheet_and_curve_do_not_claim_geometry_coverage():
    styled = inspect('<style>.wire { stroke:none }</style>' + node('obstacle', 0, 120) + connector())
    assert styled['status'] == 'unchecked'
    curved = inspect(node('obstacle', 0, 120)
        + '<path id="curve" data-pptx-role="connector" d="M60 80 C60 180 80 250 60 300" stroke="cyan"/>')
    assert curved['status'] == 'unchecked'
    assert curved['coverage']['explicit_connectors'] == 1
    assert curved['coverage']['unchecked'][0]['code'] == 'svg_diagram_shape_unchecked'


def test_polyline_can_route_around_obstacle_and_rejects_a_crossing_segment():
    obstacle = node('obstacle', 0, 120)
    clear = inspect(obstacle + '<polyline data-pptx-role="connector" points="60,80 150,80 150,300 60,300" fill="none" stroke="cyan"/>')
    assert clear['errors'] == []
    crossed = inspect(obstacle + '<polyline data-pptx-role="connector" points="60,80 60,250 150,300" fill="none" stroke="cyan"/>')
    assert crossed['errors'][0]['code'] == 'svg_diagram_connector_crosses_node'


@pytest.mark.parametrize('points', ['x1="-20" y1="120" x2="200" y2="120"',
    'x1="0" y1="80" x2="0" y2="300"', 'x1="60" y1="80" x2="60" y2="120"'])
def test_touching_or_following_border_is_not_interior_crossing(points):
    report = inspect(node('obstacle', 0, 120) + connector(points))
    assert report['errors'] == []


def test_rounded_corner_touch_is_not_a_false_crossing():
    report = inspect(node('obstacle', 100, 100, 100, 100, 'rx="20"')
        + connector('x1="95" y1="105" x2="105" y2="95"'))
    assert report['errors'] == []


def test_endpoint_alignment_requires_explicit_reference():
    cards = node('source', 0, 0) + node('target', 0, 300)
    unbound = inspect(cards + connector('x1="60" y1="70" x2="60" y2="310"'))
    assert unbound['errors'] == []
    bound = inspect(cards + connector('x1="60" y1="70" x2="60" y2="310"',
        'data-pptx-source="source" data-pptx-target="target"'))
    assert len(bound['errors']) == 2
    assert {row['code'] for row in bound['errors']} == {'svg_diagram_endpoint_misaligned'}


def test_missing_or_duplicate_reference_is_rejected_without_leaking_values():
    report = inspect(node('source', 0, 0) + node('source', 200, 0)
        + connector(extra='data-pptx-source="source" data-pptx-target="not-a-node"'))
    assert report['errors'] == [{'code': 'svg_diagram_node_reference_invalid', 'element_id': 'edge'}]


def test_reference_to_unsupported_node_is_unchecked_not_invalid():
    report = inspect('<ellipse id="round" data-pptx-role="node" cx="60" cy="60" rx="20" ry="20"/>'
        + connector(extra='data-pptx-source="round"'))
    assert report['errors'] == []
    assert any(row['code'] == 'svg_diagram_node_reference_unchecked' for row in report['coverage']['unchecked'])


def test_node_under_unsupported_ancestor_is_not_falsely_a_missing_reference():
    report = inspect('<g transform="rotate(30)">' + node('source', 0, 0) + '</g>'
        + connector(extra='data-pptx-source="source"'))
    assert report['errors'] == []
    assert report['status'] == 'unchecked'
    assert any(row['code'] == 'svg_diagram_node_reference_unchecked' for row in report['coverage']['unchecked'])


def test_malformed_or_unbounded_coordinates_are_unchecked():
    for value in ('NaN', 'inf', '1e100', '50%', '2cm'):
        report = inspect(node('obstacle', 0, 120) + connector(f'x1="{value}" y1="80" x2="60" y2="300"'))
        assert report['status'] == 'unchecked'
        assert report['errors'] == []


def test_definition_geometry_and_hidden_parent_are_not_obstacles():
    report = inspect('<defs>' + node('def', 0, 120) + '</defs>'
        + '<g opacity="0">' + node('hidden', 0, 120) + '</g>' + connector())
    assert report['errors'] == []


def test_report_is_bounded_and_source_xml_is_unchanged():
    source = ET.fromstring('<svg xmlns="http://www.w3.org/2000/svg">'
        + ''.join(node(f'obstacle{i}', 0, 120) for i in range(100)) + connector() + '</svg>')
    original = ET.tostring(source)
    report = inspect_svg_diagram(source)
    assert len(report['errors']) == 64
    assert report['coverage']['truncated'] is True
    assert ET.tostring(source) == original


def test_deep_svg_reports_coverage_limit_instead_of_recursing_unboundedly():
    report = inspect('<g>' * 100 + node('obstacle', 0, 120) + connector() + '</g>' * 100)
    assert report['status'] == 'unchecked'
    assert report['coverage']['truncated'] is True
    assert any(row['code'] == 'svg_diagram_depth_limit' for row in report['coverage']['unchecked'])
