"""Actual Chromium DOM measurements of the exact isolated-world host observer.

These fixtures exercise visible glyph geometry, not generated self-reports.
They use no network, model, production service or media-generation budget.
"""
from pathlib import Path
import json

import pytest

from video_workbench.animation_experiments import constraints


HOST = Path(__file__).parents[2] / 'video_workbench' / 'animation_experiment_host.mjs'


@pytest.fixture(scope='module')
def browser():
    api = pytest.importorskip('playwright.sync_api')
    with api.sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch(headless=True)
        except api.Error as error:
            if 'Executable doesn\'t exist' in str(error):
                pytest.skip('Local Chromium is not installed; no browser download is attempted.')
            raise
        yield instance
        instance.close()


@pytest.fixture
def observe(browser):
    source = HOST.read_text(encoding='utf-8').split('// BEGIN TRUSTED LABEL OBSERVER\n', 1)[1]
    source = source.split('// END TRUSTED LABEL OBSERVER', 1)[0].strip()
    context = browser.new_context(viewport={'width': 1280, 'height': 720})
    page = context.new_page()
    page.set_content('<html><body></body></html>')
    session = context.new_cdp_session(page)
    frame = session.send('Page.getFrameTree')['frameTree']['frame']['id']
    world = session.send('Page.createIsolatedWorld', {
        'frameId': frame, 'worldName': 'trusted-label-observer-test'})['executionContextId']

    def measure(markup, labels=None):
        page.set_content('<html><head><style>body{margin:0}svg{display:block;overflow:hidden}'
            'text{font:32px sans-serif;fill:white;text-anchor:middle;dominant-baseline:central}'
            'rect{fill:#2563eb}</style></head><body>' + markup + '</body></html>')
        response = session.send('Runtime.evaluate', dict(contextId=world, returnByValue=True,
            expression='(' + source + ')(' + json.dumps(labels or [{'id': 'A', 'label': 'A'}]) + ')'))
        assert 'exceptionDetails' not in response, response.get('exceptionDetails')
        return response['result']['value']

    yield measure
    context.close()


def svg(contents):
    return '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">' + contents + '</svg>'


def square(label='<text x="0" y="0">A</text>', *, marker_on_group=False, group_style=''):
    group_marker = ' data-object-id="A"' if marker_on_group else ''
    rect_marker = '' if marker_on_group else ' data-object-id="A"'
    return svg('<g transform="translate(320 360)"' + group_marker + group_style + '>'
        '<rect x="-56" y="-56" width="112" height="112"' + rect_marker + '/>' + label + '</g>')


def test_rect_sibling_and_group_descendant_labels_have_same_independent_identity(observe):
    rect, = observe(square())
    group, = observe(square(marker_on_group=True))
    assert rect['text'] == group['text'] == 'A'
    assert rect['bounds'] == group['bounds'] == dict(x=264, y=304, width=112, height=112)
    assert rect['visible'] is group['visible'] is True
    assert rect['label_observation']['status'] == group['label_observation']['status'] == 'measured'
    assert rect['label_observation']['attachment'] == 'nearest_svg_group'
    assert group['label_observation']['attachment'] == 'marked_object_descendants'
    candidate, = rect['label_observation']['candidates']
    assert candidate['text'] == 'A' and candidate['within_object']
    assert candidate['bounds']['width'] > 10 and candidate['bounds']['height'] > 10


@pytest.mark.parametrize('slot,group_marker', [('development-v1', True), ('development-v2', False),
    ('held-out-v1', False), ('held-out-v2', False)])
def test_all_four_archived_layout_forms_and_sizes_have_measured_attached_labels(observe, slot, group_marker):
    targets = constraints('held_out' if slot.startswith('held-out') else 'development')['objects']
    contents = []
    for target in targets:
        marker = ' data-object-id="' + target['id'] + '"'
        size = target['width']
        contents.append('<g transform="translate(%s %s)"%s><rect x="%s" y="%s" width="%s" height="%s"%s/>'
            '<text x="0" y="0" style="pointer-events:none">%s</text></g>' % (
                *target['initial'], marker if group_marker else '', -size/2, -size/2, size, size,
                '' if group_marker else marker, target['label']))
    measured = observe(svg(''.join(contents)), [{'id': item['id'], 'label': item['label']} for item in targets])
    for actual, target in zip(measured, targets):
        assert actual['text'] == target['label'] and actual['visible']
        assert actual['bounds']['width'] == actual['bounds']['height'] == target['width']
        assert actual['label_observation']['status'] == 'measured'
        assert actual['label_observation']['candidates'][0]['within_object']


@pytest.mark.parametrize('label,style', [
    ('<text x="0" y="0" style="display:none">A</text>', ''),
    ('<text x="0" y="0" style="visibility:hidden">A</text>', ''),
    ('<text x="0" y="0" style="opacity:0">A</text>', ''),
    ('<text x="0" y="0" style="fill:transparent">A</text>', ''),
    ('<text x="0" y="0" style="fill-opacity:0">A</text>', ''),
    ('<text x="0" y="0" style="font-size:0">A</text>', ''),
    ('<text x="0" y="0" transform="scale(0)">A</text>', ''),
    ('<text x="0" y="0">B</text>', ''),
    ('<text x="200" y="0">A</text>', ''),
    ('<text x="0" y="0">A</text><text x="0" y="20">A</text>', ''),
    ('<title>A</title><desc>A</desc>', ''),
    ('<text x="0" y="0">A</text>', ' style="opacity:0"'),
    ('<text x="0" y="0" style="clip-path:circle(0)">A</text>', ''),
])
def test_hidden_wrong_detached_or_ambiguous_labels_cannot_be_certified(observe, label, style):
    actual, = observe(square(label, group_style=style))
    assert actual['text'] == ''
    assert actual['label_observation']['status'] != 'measured'


def test_another_object_in_same_group_is_not_an_identity_attachment(observe):
    markup = square('<rect data-object-id="B" x="144" y="-56" width="112" height="112"/>'
                    '<text x="0" y="0">A</text>')
    rows = observe(markup, [{'id': 'A', 'label': 'A'}, {'id': 'B', 'label': 'B'}])
    assert all(row['text'] == '' and row['label_observation']['status'] == 'attachment_ambiguous' for row in rows)


def test_text_in_another_group_or_metadata_is_not_a_visible_attached_label(observe):
    markup = svg('<g transform="translate(320 360)"><rect data-object-id="A" x="-56" y="-56"'
        ' width="112" height="112"/><title>A</title></g>'
        '<g transform="translate(320 360)"><text x="0" y="0">A</text></g>')
    actual, = observe(markup)
    assert actual['label_observation']['status'] == 'missing' and actual['text'] == ''


def test_rendered_html_text_is_measured_without_trusting_attributes(observe):
    actual, = observe('<div data-object-id="A" aria-label="not trusted" style="position:absolute;'
        'left:264px;top:304px;width:112px;height:112px;background:#2563eb;color:white;'
        'font:32px sans-serif;display:flex;align-items:center;justify-content:center">'
        '<span>A</span><span style="display:none">B</span></div>')
    assert actual['text'] == 'A' and actual['label_observation']['status'] == 'measured'
    assert actual['label_observation']['candidates'][0]['within_object']
