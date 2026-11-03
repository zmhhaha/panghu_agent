"""Actual pinned SVG→native PPTX→Office-render regression, on Linux only.

Run in the reviewed runtime image with PPT_RUN_NATIVE_TEXT_REGRESSION=true.
This uses the official converter's diagnostic flat API with synthetic sources;
it does not claim production workflow/postflight provenance or human acceptance.
No substitute converter, mocked Office command or rasterized text is involved.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4
import xml.etree.ElementTree as ET
import zipfile

import pytest

from agent_service.profiles.ppt_text_layout import TEXT_LAYOUT_SVG


pytestmark = pytest.mark.skipif(
    os.environ.get('PPT_RUN_NATIVE_TEXT_REGRESSION') != 'true',
    reason='requires the pinned Linux exporter and actual Office renderer',
)

SCRIPTS = Path('/opt/ppt-master/skills/ppt-master/scripts')
NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
LABELS = ('tools', 'Consumer', 'deriveMessages', 'core/session', 'agent/assistant-stream')


def _export(tmp_path, svg):
    """Use the same pinned native package builder as the release CLI."""
    assert sys.platform == 'linux'
    assert (SCRIPTS / 'svg_to_pptx.py').is_file()
    sys.path.insert(0, str(SCRIPTS))
    try:
        from svg_to_pptx.pptx_package.builder import create_pptx_with_native_svg
    finally:
        sys.path.remove(str(SCRIPTS))
    project = tmp_path / 'source'
    project.mkdir()
    sources = []
    for ordinal in range(1, 4):
        source = project / f'{ordinal:02d}_synthetic.svg'
        source.write_text(svg, encoding='utf-8')
        sources.append(source)
    pptx = project / 'presentation.pptx'
    # Do not force a different text_flow: release CLI defaults to preserve.
    assert create_pptx_with_native_svg(sources, pptx, resource_root=project,
        pptx_structure='flat', enable_notes=False, transition=None,
        animation=None, native_objects=True, workers=1, verbose=False)
    raw = pptx.read_bytes()
    request = dict(validation_nonce=str(uuid4()), pages=3,
        source_manifest_sha256=hashlib.sha256(svg.encode('utf-8')).hexdigest(),
        input_snapshot_sha256=hashlib.sha256(b'synthetic-native-text-regression').hexdigest())
    return pptx, {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}, request


def test_official_native_text_frames_survive_actual_final_render(tmp_path):
    from ppt_service.control.final_pptx_validation import verify_final_pptx
    import fitz

    pptx, expected, request = _export(tmp_path, TEXT_LAYOUT_SVG)
    source = ET.fromstring(TEXT_LAYOUT_SVG)
    source_frames = {node.text: tuple(float(v) for v in node.attrib['data-pptx-frame'].split())
                     for node in source if node.tag.endswith('}text')}
    with zipfile.ZipFile(pptx) as package:
        for ordinal in range(1, 4):
            slide = ET.fromstring(package.read(f'ppt/slides/slide{ordinal}.xml'))
            assert slide.findall('.//p:pic', NS) == []
            actual = {}
            for shape in slide.findall('.//p:sp', NS):
                text = ''.join(node.text or '' for node in shape.findall('.//a:t', NS))
                if text not in source_frames:
                    continue
                assert text not in actual
                body = shape.find('p:txBody/a:bodyPr', NS)
                assert body is not None and body.get('wrap') == 'none'
                assert body.find('a:noAutofit', NS) is not None
                assert shape.findall('.//a:br', NS) == []
                transform = shape.find('p:spPr/a:xfrm', NS)
                assert transform is not None
                off, ext = transform.find('a:off', NS), transform.find('a:ext', NS)
                assert off is not None and ext is not None
                frame = tuple(int(value) / 9525 for value in (
                    off.get('x'), off.get('y'), ext.get('cx'), ext.get('cy')))
                assert frame == pytest.approx(source_frames[text], abs=.01)
                actual[text] = frame
            # All titles, labels and Chinese descriptions remain native text.
            assert set(actual) == set(source_frames)
    output = tmp_path / 'final-render'
    report = verify_final_pptx(pptx, expected, request, output)
    text_report = json.loads((output / 'text-layout.json').read_text())
    assert text_report['status'] == 'passed'
    assert text_report['issues'] == []
    assert report['human_visual_acceptance'] is False
    assert report['powerpoint_acceptance'] == 'pending'
    with fitz.open(output / 'pdf/presentation.pdf') as document:
        assert len(document) == 3
        for page in document:
            lines = [''.join(span['text'] for span in line['spans'])
                     for block in page.get_text('dict')['blocks'] if 'lines' in block
                     for line in block['lines']]
            # This observes final Office glyph lines, not source/XML strings.
            assert all(any(label in line for line in lines) for label in LABELS)


def test_actual_final_render_rejects_a_tight_native_identifier_frame(tmp_path):
    from ppt_service.control.execution import ControlError
    from ppt_service.control.final_pptx_validation import verify_final_pptx

    narrow = TEXT_LAYOUT_SVG.replace('data-pptx-frame="72 404 520 48"',
                                    'data-pptx-frame="72 404 80 48"')
    assert narrow != TEXT_LAYOUT_SVG
    pptx, expected, request = _export(tmp_path, narrow)
    output = tmp_path / 'final-render'
    with pytest.raises(ControlError) as failure:
        verify_final_pptx(pptx, expected, request, output)
    assert failure.value.code == 'final_pptx_text_layout_failed'
    text_report = json.loads((output / 'text-layout.json').read_text())
    assert text_report['status'] == 'failed'
    assert text_report['issues']
    assert text_report['human_visual_acceptance'] is False
