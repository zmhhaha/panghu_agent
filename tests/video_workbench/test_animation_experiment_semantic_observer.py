"""Actual Chromium checks of the isolated-world semantic observer.

No model or render-job submission. These are bounded observer fixtures, not
creative samples or proof of video aesthetics.
"""
import json
from pathlib import Path

import pytest


HOST = Path(__file__).parents[2] / 'video_workbench' / 'animation_experiment_host.mjs'


@pytest.fixture(scope='module')
def browser():
    api = pytest.importorskip('playwright.sync_api')
    with api.sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch(headless=True)
        except api.Error as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip('Local Chromium unavailable; no download attempted.')
            raise
        yield instance
        instance.close()


@pytest.fixture
def observe(browser):
    source = HOST.read_text(encoding='utf-8').split('// BEGIN TRUSTED SEMANTIC OBSERVER\n', 1)[1]
    source = source.split('// END TRUSTED SEMANTIC OBSERVER', 1)[0].strip()
    context = browser.new_context(viewport={'width': 1280, 'height': 720})
    page = context.new_page()
    page.set_content('<html><body></body></html>')
    session = context.new_cdp_session(page)
    frame = session.send('Page.getFrameTree')['frameTree']['frame']['id']
    world = session.send('Page.createIsolatedWorld', {
        'frameId': frame, 'worldName': 'trusted-semantic-observer-test'})['executionContextId']

    def measure(markup, script=''):
        page.set_content('<html><head><style>body{margin:0;color:white;background:#111827;font:24px sans-serif}'
            'svg{display:block;overflow:hidden}text{font:24px sans-serif;fill:white}'
            'circle,ellipse,polygon{fill:#2563eb}line{stroke:white;stroke-width:3}'
            '</style></head><body>' + markup + '</body></html>')
        if script:
            page.evaluate(script)
        response = session.send('Runtime.evaluate', dict(contextId=world, returnByValue=True,
            expression='(' + source + ')()'))
        assert 'exceptionDetails' not in response, response.get('exceptionDetails')
        return {row['id']: row for row in response['result']['value']}

    yield measure
    context.close()


def svg(contents):
    return '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">' + contents + '</svg>'


def test_metrics_are_measured_visible_glyphs_and_entity_labels_attach_to_actual_group(observe):
    actual = observe(svg('<g data-semantic-id="entity-A" transform="translate(100 100)">'
        '<rect x="0" y="0" width="240" height="150" fill="#2563eb"/>'
        '<text data-semantic-id="identity-A" x="10" y="30">A</text>'
        '<text data-semantic-id="value-A" x="10" y="60">8</text>'
        '<text data-semantic-id="selection-A" x="10" y="90">保留</text></g>'
        '<text data-semantic-id="metric-count" x="500" y="200" data-value="999">5</text>'))
    assert actual['identity-A']['text'] == 'A'
    assert actual['value-A']['entity_attachment'] == 'entity-A'
    assert actual['selection-A']['text'] == '保留'
    assert actual['entity-A']['visible']
    assert actual['metric-count']['text'] == '5'
    assert actual['metric-count']['text_status'] == 'measured'


@pytest.mark.parametrize('style', ['display:none', 'visibility:hidden', 'opacity:0',
    'fill:transparent', 'fill-opacity:0', 'font-size:0', 'clip-path:circle(0)', 'transform:scale(0)'])
def test_hidden_metric_glyphs_are_not_observations_even_when_attributes_claim_success(observe, style):
    actual = observe(svg('<text data-semantic-id="metric-stock" x="100" y="100" '
        'data-value="4" data-check="passed" style="' + style + '">4</text>'))['metric-stock']
    assert actual['text_status'] == 'missing'
    assert actual['text'] == ''


def test_clipped_and_metadata_text_are_not_counted_as_visible_values(observe):
    actual = observe('<div data-semantic-id="metric-count" style="width:2px;height:2px;overflow:hidden">'
        '<span>123</span><script>777</script><span style="display:none">999</span></div>')['metric-count']
    assert actual['text_status'] == 'missing'
    assert actual['text'] == ''


def test_svg_geometry_is_screen_transformed_instead_of_reading_coordinate_claims(observe):
    actual = observe(svg('<g data-semantic-id="entity-P" transform="translate(640 390) scale(90 -90)">'
        '<circle data-semantic-id="point-P" data-x="999" data-y="999" cx="1.2" cy=".8" r=".08"/>'
        '<line data-semantic-id="basis-x" x1="0" y1="0" x2="1.2" y2=".2"/>'
        '<polygon data-semantic-id="unit-area" points="0,0 1.2,.2 1.8,1.1 .6,.9"/>'
        '</g>'))
    assert actual['point-P']['geometry']['point'] == pytest.approx([748, 318])
    assert actual['point-P']['entity_attachment'] == 'entity-P'
    assert actual['basis-x']['geometry']['points'][0] == pytest.approx([640, 390])
    assert actual['basis-x']['geometry']['points'][1] == pytest.approx([748, 372])
    assert actual['unit-area']['geometry']['points'][2] == pytest.approx([802, 291])


def test_singular_polygon_remains_measured_and_unpainted_line_is_rejected(observe):
    actual = observe(svg('<polygon data-semantic-id="unit-area" points="100,100 200,100 300,100 200,100"/>'
        '<line data-semantic-id="basis-x" x1="100" y1="100" x2="200" y2="100" style="stroke:none"/>'))
    assert actual['unit-area']['geometry']['kind'] == 'polygon'
    assert actual['unit-area']['rendered'] is True
    assert actual['unit-area']['bounds']['height'] == 0
    assert actual['basis-x']['rendered'] is False


def test_main_world_prototype_tampering_does_not_change_observed_text_or_coordinates(observe):
    actual = observe('<div data-semantic-id="metric-stock">4</div>',
        "() => { Element.prototype.getAttribute=()=> 'fake'; Element.prototype.getBoundingClientRect=()=>({x:999,y:999,width:1,height:1}); }")
    assert actual['metric-stock']['text'] == '4'
    assert actual['metric-stock']['bounds']['x'] == 0


def test_duplicate_semantic_markers_remain_duplicates_for_python_rejection(browser):
    source = HOST.read_text(encoding='utf-8').split('// BEGIN TRUSTED SEMANTIC OBSERVER\n', 1)[1]
    source = source.split('// END TRUSTED SEMANTIC OBSERVER', 1)[0].strip()
    page = browser.new_page()
    page.set_content('<div data-semantic-id="metric-stock">4</div><div data-semantic-id="metric-stock">7</div>')
    actual = page.evaluate('(' + source + ')()')
    assert [row['id'] for row in actual] == ['metric-stock', 'metric-stock']
    assert [row['text'] for row in actual] == ['4', '7']
    page.close()
