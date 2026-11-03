"""Server-only, synthetic offline native-export sample and retained revision.

Run in the reviewed Linux worker image, with no network, business data or model
credentials: PPT_RUN_COMPLEX_NATIVE_DECK=true python -m
ppt_service.tests.acceptance.complex_native_deck --output /out/fresh-sample

This exercises the fixed exporter's diagnostic flat API and the real independent
PPTX -> Office PDF -> PNG validator. It is not an Agent task, an owner workflow,
official full exporter postflight, human acceptance or publication evidence.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
from html import escape
import io
import json
import os
from pathlib import Path
import posixpath
import subprocess
import sys
import traceback
from uuid import uuid4
import xml.etree.ElementTree as ET
import zipfile


SCRIPTS = Path('/opt/ppt-master/skills/ppt-master/scripts')
FONT = 'Noto Sans CJK SC'
SVG = '{http://www.w3.org/2000/svg}'
NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
# Trusted literal oracle, deliberately separate from authored chart metadata.
EXPECTED_CHARTS = [{'slide': 4, 'type': 'column', 'categories': ['A', 'B', 'C'],
                    'series': [{'name': '合成计数', 'values': [12, 18, 25]}]}]


def require(value, code):
    if not value:
        raise RuntimeError(code)


def measure(path):
    raw = path.read_bytes()
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def write_json(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def text(value, x, top, width, *, size=24, height=44, color='#334155', bold=False):
    weight = ' font-weight="bold"' if bold else ''
    return (f'<text x="{x}" y="{top + size + 5}" font-family="{FONT}" font-size="{size}"'
            f' fill="{color}"{weight} data-pptx-frame="{x} {top} {width} {height}"'
            f' data-pptx-text-baseline="metrics-v1">{escape(value)}</text>')


def lines(values, x, top, width, *, size=24, step=58):
    return ''.join(text(value, x, top + index * step, width, size=size)
                   for index, value in enumerate(values))


def card(x, y, width, height, color='#E2E8F0', *, node=None):
    identity = f' id="{node}" data-pptx-diagram="node"' if node else ''
    return f'<rect{identity} x="{x}" y="{y}" width="{width}" height="{height}" rx="16" fill="{color}"/>'


def slide(number, title, body, revised):
    label = '说明与版式修订稿' if revised else '初稿'
    return ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
            ' width="1280" height="720" viewBox="0 0 1280 720" lang="zh-CN">'
            '<rect width="1280" height="720" fill="#F8FAFC"/>'
            + text(title, 64, 30, 1152, size=36, height=66, color='#0F172A', bold=True)
            + body
            + '<line x1="64" y1="636" x2="1216" y2="636" stroke="#CBD5E1" stroke-width="2"/>'
            + text(f'合成离线样稿 · {label} · {number}/6 · 待人工审核',
                   64, 655, 1152, size=18, height=34, color='#64748B') + '</svg>')


def chart_fallback():
    authored = {'name': 'synthetic-column', 'type': 'column', 'x': 80, 'y': 150,
                'width': 820, 'height': 450, 'title': '合成计数',
                'categories': ['A', 'B', 'C'],
                'series': [{'name': '合成计数', 'values': [12, 18, 25]}],
                'data_labels': {'show_value': True, 'position': 'outside_end', 'number_format': '0',
                                'font_size': 22, 'font_family': FONT, 'color': '#334155'},
                'show_legend': False, 'show_value_axis_labels': False,
                'axes': {'category': {'visible': True, 'label_position': 'next_to',
                                      'major_gridlines': False, 'tick_marks': 'none'},
                         'value': {'visible': True, 'label_position': 'none',
                                   'major_gridlines': False, 'tick_marks': 'none'}},
                'style': {'font_family': FONT, 'title_font_size': 24, 'axis_font_size': 22,
                          'text_color': '#334155', 'axis_color': '#64748B', 'grid_color': '#CBD5E1',
                          'colors': ['#2563EB'], 'chart_area_fill': '#F8FAFC', 'plot_area_fill': '#F8FAFC'}}
    visible = text('合成计数', 112, 168, 740, size=24, bold=True)
    visible += '<line id="chart-category-axis" x1="160" y1="550" x2="840" y2="550" stroke="#64748B" stroke-width="2"/>'
    visible += '<line id="chart-value-axis" x1="160" y1="550" x2="160" y2="230" stroke="#64748B" stroke-width="2"/>'
    for x, height, category, value in ((250, 132, 'A', '12'), (470, 198, 'B', '18'), (690, 275, 'C', '25')):
        visible += f'<rect x="{x}" y="{550 - height}" width="90" height="{height}" fill="#2563EB"/>'
        visible += text(value, x, 505 - height, 110, size=22)
        visible += text(category, x + 20, 560, 90, size=22)
    return ('<g id="synthetic-column" data-pptx-replace-with="chart"><metadata type="application/json">'
            + escape(json.dumps(authored, ensure_ascii=False, separators=(',', ':')))
            + '</metadata>' + visible + '</g>')


def slides(revised):
    cover = (text('原生文字、示意图片、图表与中文长文本', 80, 150, 1120, size=30, height=54)
             + card(80, 260, 1120, 240, '#DBEAFE')
             + lines(['用途：检查复杂内容的原生导出与最终渲染。',
                      '范围：全部内容为独立构造，不含真实项目材料。',
                      '状态：程序检查完成后，仍需人工与 PowerPoint 验证。'], 112, 292, 1056))
    long_text = lines([
        '本页用连续中文说明，检查较长文字在原生文本框中的完整保留。',
        '资料上传、内容规划、视觉表达和最终文件检查，各自负责不同问题。',
        '程序可以发现一部分结构错误，却不能证明业务结论与真实资料一致。',
        '本样稿主动保留适当行距与边距，每一行都拥有独立的可编辑文本框。',
        '当说明过长时，应先重组段落和版式，而不是把所有文字缩到难以阅读。',
        '初稿与修订稿使用同一组合成数据，避免版式调整悄悄改变图表含义。',
        '缺少资料的地方会明确说明，不将推测、示例或程序结果写成事实。',
        '这些约束只服务于本次离线样稿检查，不代表完整业务流程已经验收。',
    ], 80, 142, 1120, size=25, step=55)
    diagram = ''
    descriptions = [('构造材料', ['明确标记合成内容', '不读取真实用户资料']),
                    ('原生导出', ['保留文字与图表数据', '只调整说明和版式']),
                    ('最终检查', ['从文件渲染六页预览', '人工验收仍然待办'])]
    for index, (title, body) in enumerate(descriptions):
        x = 80 + index * 410
        y = 190 if revised else 180
        diagram += card(x, y, 300, 270, '#E0E7FF', node=f'process-{index}')
        diagram += text(title, x + 24, y + 25, 252, size=28, height=50, bold=True)
        diagram += lines(body, x + 24, y + 108, 252, size=21, step=58)
        if index < 2:
            diagram += (f'<line data-pptx-diagram="connector" data-pptx-source="process-{index}"'
                        f' data-pptx-target="process-{index + 1}" x1="{x + 300}" y1="{y + 135}"'
                        f' x2="{x + 410}" y2="{y + 135}" stroke="#4F46E5" stroke-width="4"/>'
                        f'<path d="M {x + 397} {y + 127} L {x + 410} {y + 135} L {x + 397} {y + 143}"'
                        ' fill="none" stroke="#4F46E5" stroke-width="4"/>')
    diagram += lines(['连线只表达从材料到导出、再到检查的顺序。',
                      '同一轮离线样稿中的步骤关系，不代表真实服务的调用拓扑。'],
                     80, 502, 1120, size=24, step=52)
    chart = chart_fallback() + card(940, 164, 260, 400, '#FEF3C7')
    chart += lines(['明确的合成数据', '类别：A、B、C', '数值：12、18、25', '不代表真实测量',
                    '修订前后数据相同'], 962, 190, 220, size=20, step=65)
    image = ('<image x="80" y="172" width="720" height="400" href="schematic.png"'
             ' xlink:href="schematic.png" preserveAspectRatio="xMidYMid meet"/>'
             + lines(['左侧图片由固定几何构造。', '不使用外部素材或网络。', '图片应作为图片保留。',
                      '右侧说明应仍可编辑。', '图形只作流程示意。'], 846, 190, 360, size=23, step=70))
    caveats = card(80, 150, 1120, 410, '#F1F5F9')
    caveats += lines(['尚缺：真实用户材料、业务上下文与经过确认的研究结论。',
                      '因此不能用本样稿判断事实准确性、引用完整性或业务交付质量。',
                      '本次仅核查六页原生对象、合成图表数据与最终文件渲染。',
                      '尚待：人工逐页观察，以及 PowerPoint 编辑、保存和重新打开。',
                      ('本次修订：分组说明并增加留白，合成数据与图片内容保持一致。'
                       if revised else '后续修订只改说明与版式，不补造缺失资料或更改图表数值。')],
                     112, 184, 1056, size=25, step=70)
    return [slide(i, title, body, revised) for i, (title, body) in enumerate([
        ('复杂原生 PPTX 合成样稿', cover), ('长文本：保留完整说明与可读行距', long_text),
        ('图解：清晰的步骤关系', diagram), ('原生图表：明确标记合成数据', chart),
        ('示意图片：与原生说明文字共同排版', image), ('资料边界与尚待完成的验证', caveats)], 1)]


def create_image(path):
    from PIL import Image, ImageDraw
    image = Image.new('RGBA', (720, 400), '#E0F2FE')
    draw = ImageDraw.Draw(image)
    for index, x in enumerate((50, 275, 500)):
        draw.rounded_rectangle((x, 110, x + 170, 290), radius=22, fill=('#DBEAFE', '#C7D2FE', '#BFDBFE')[index],
                               outline='#2563EB', width=3)
        draw.ellipse((x + 55, 140, x + 115, 200), fill='#2563EB')
        draw.rounded_rectangle((x + 35, 220, x + 135, 244), radius=8, fill='#64748B')
        if index < 2:
            draw.line((x + 170, 200, x + 220, 200), fill='#2563EB', width=5)
            draw.polygon([(x + 210, 190), (x + 225, 200), (x + 210, 210)], fill='#2563EB')
    image.save(path, format='PNG', optimize=False)


def source_rows(folder):
    return [{'path': p.name, **measure(p)} for p in sorted(folder.iterdir()) if p.is_file()]


def expected_native_text(path):
    expected = []
    def visit(node, chart=False):
        chart = chart or node.get('data-pptx-replace-with') == 'chart'
        if node.tag == SVG + 'text':
            require(node.get('data-pptx-frame') and node.get('data-pptx-text-baseline') == 'metrics-v1',
                    'authored_native_text_frame_missing')
            if not chart:
                expected.append(''.join(node.itertext()))
        for child in node:
            visit(child, chart)
    visit(ET.parse(path).getroot())
    return expected


def inspect_native(pptx, sources, image):
    from PIL import Image
    from ppt_service.worker.candidate_validation import inspect_pptx
    report = inspect_pptx(pptx, expected_charts=deepcopy(EXPECTED_CHARTS))
    require(report['slide_count'] == 6 and report['notes_count'] == 6
            and report['chart_count'] == 1 and report['chart_data_verified'],
            'native_chart_cache_or_workbook_unverified')
    picture_count, text_count = 0, 0
    with Image.open(image) as wanted:
        wanted_pixels = wanted.convert('RGBA').tobytes()
    embedded_image_found = False
    with zipfile.ZipFile(pptx) as package:
        for ordinal, source in enumerate(sources, 1):
            slide_root = ET.fromstring(package.read(f'ppt/slides/slide{ordinal}.xml'))
            native_text = [''.join(n.text or '' for n in shape.findall('.//a:t', NS))
                           for shape in slide_root.findall('.//p:sp', NS)]
            wanted_text = expected_native_text(source)
            require(all(value in native_text for value in wanted_text), 'authored_text_not_preserved_as_native')
            text_count += len(wanted_text)
            pictures = slide_root.findall('.//p:pic', NS)
            picture_count += len(pictures)
            require(len(pictures) == (1 if ordinal == 5 else 0), 'unexpected_rasterized_slide_content')
            if ordinal == 5:
                relationships = ET.fromstring(package.read('ppt/slides/_rels/slide5.xml.rels'))
                targets = {r.get('Id'): r.get('Target') for r in relationships
                           if r.get('Type', '').endswith('/image') and r.get('TargetMode', 'Internal') == 'Internal'}
                blip = pictures[0].find('.//a:blip', NS)
                rid = blip.get('{' + NS['r'] + '}embed') if blip is not None else None
                require(rid in targets, 'synthetic_image_relationship_missing')
                part = posixpath.normpath('ppt/slides/' + targets[rid])
                with Image.open(io.BytesIO(package.read(part))) as actual:
                    embedded_image_found = actual.size == (720, 400) and actual.convert('RGBA').tobytes() == wanted_pixels
    require(embedded_image_found, 'synthetic_image_pixels_changed')
    return {**report, 'native_authored_text_shapes_checked': text_count, 'picture_nodes': picture_count,
            'synthetic_image_pixels_preserved': True, 'independent_chart_oracle': EXPECTED_CHARTS,
            'human_visual_acceptance': False, 'powerpoint_acceptance': 'pending', 'publication_authorized': False}


def run_case(root, name, revised, image, input_hash):
    from ppt_service.control.final_pptx_validation import verify_final_pptx
    from svg_to_pptx.pptx_package.builder import create_pptx_with_native_svg
    case = root / name
    case.mkdir()
    source, delivery = case / 'source', case / 'delivery'
    source.mkdir()
    delivery.mkdir()
    (source / 'schematic.png').write_bytes(image.read_bytes())
    paths, notes = [], {}
    for ordinal, raw in enumerate(slides(revised), 1):
        path = source / f'{ordinal:02d}_synthetic.svg'
        path.write_text(raw, encoding='utf-8')
        paths.append(path)
        notes[path.stem] = ('本页为明确标记的合成离线样稿。图表采用 A、B、C 与数值12、18、25，'
                            '不代表任何真实测量。修订只涉及说明与版式，仍待人工与 PowerPoint 验证。')
    write_json(source / 'notes.json', notes)
    # The real fixed script stamps a visible, complete fallback before export.
    with (case / 'fallback.stdout.txt').open('xb') as out, (case / 'fallback.stderr.txt').open('xb') as err:
        stamped = subprocess.run([sys.executable, str(SCRIPTS / 'stamp_native_fallbacks.py'), str(source), '--write'],
                                 stdout=out, stderr=err, timeout=60, check=False)
    require(stamped.returncode == 0, 'fixed_native_fallback_stamp_failed')
    before = source_rows(source)
    manifest = {'schema': 'ppt-complex-native-synthetic-source.v1', 'case': name, 'files': before,
                'synthetic_only': True, 'owner_workflow_verified': False, 'publication_authorized': False}
    manifest_path = case / 'source-manifest.json'
    write_json(manifest_path, manifest)
    pptx = delivery / 'presentation.pptx'
    require(create_pptx_with_native_svg(paths, pptx, resource_root=source,
        pptx_structure='flat', native_objects=True, workers=1, transition=None, animation=None,
        notes=notes, enable_notes=True, image_optimize=False, verbose=False), 'fixed_native_export_failed')
    expected = measure(pptx)
    request = {'validation_nonce': str(uuid4()), 'pages': 6,
               'source_manifest_sha256': measure(manifest_path)['sha256'], 'input_snapshot_sha256': input_hash}
    write_json(case / 'validation-request.json', request)
    native = inspect_native(pptx, paths, image)
    write_json(case / 'native-object-check.json', native)
    rendered = verify_final_pptx(pptx, expected, request, case / 'final-render')
    require(source_rows(source) == before and measure(pptx) == expected, 'synthetic_source_or_delivery_changed')
    require(rendered['status'] == 'passed' and len(rendered['pages']) == 6, 'final_render_not_complete')
    return {'case': name, 'status': 'programmatic_checks_passed', 'pptx': {'path': f'{name}/delivery/presentation.pptx', **expected},
            'source_manifest': {'path': f'{name}/source-manifest.json', **measure(manifest_path)},
            'binding': request, 'source_unchanged': True, 'native_objects': native,
            'final_render': {'path': f'{name}/final-render/final-pptx-evidence.json',
                             **measure(case / 'final-render/final-pptx-evidence.json')},
            'previews': [{**row, 'path': f'{name}/final-render/' + row['path']} for row in rendered['pages']],
            'final_pdf': {'path': f'{name}/final-render/pdf/presentation.pdf',
                          **measure(case / 'final-render/pdf/presentation.pdf')},
            'human_visual_acceptance': False, 'powerpoint_acceptance': 'pending', 'publication_authorized': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='A new, nonexistent output directory')
    args = parser.parse_args(argv)
    require(sys.platform == 'linux' and os.environ.get('PPT_RUN_COMPLEX_NATIVE_DECK') == 'true',
            'explicit_server_only_execution_required')
    for name in ('stamp_native_fallbacks.py', 'svg_to_pptx/pptx_package/builder.py'):
        require((SCRIPTS / name).is_file(), 'fixed_exporter_unavailable')
    output = args.output.absolute()
    require(not output.exists() and not output.is_symlink(), 'output_directory_must_be_new')
    for parent in output.parents:
        require(not parent.is_symlink(), 'linked_output_parent')
    output.mkdir(parents=True)
    sys.path.insert(0, str(SCRIPTS))
    image = output / 'schematic.png'
    create_image(image)
    snapshot = {'schema': 'ppt-complex-native-synthetic-input.v1', 'synthetic_only': True,
                'expected_charts': EXPECTED_CHARTS, 'image': measure(image), 'pages': 6,
                'missing_materials': 'No real user sources, confirmed research conclusions or citations.'}
    write_json(output / 'synthetic-input.json', snapshot)
    input_hash = measure(output / 'synthetic-input.json')['sha256']
    results = []
    for name, revised in (('initial', False), ('revised', True)):
        try:
            result = run_case(output, name, revised, image, input_hash)
        except Exception as error:
            case = output / name
            case.mkdir(exist_ok=True)
            (case / 'failure.txt').write_text(traceback.format_exc(), encoding='utf-8')
            result = {'case': name, 'status': 'failed', 'error_class': type(error).__name__,
                      'error_code': getattr(error, 'code', None), 'diagnostics': f'{name}/failure.txt',
                      'previews': [{'ordinal': int(p.stem), 'path': p.relative_to(output).as_posix(), **measure(p)}
                                   for p in sorted((case / 'final-render/pages').glob('*.png'))],
                      'human_visual_acceptance': False, 'powerpoint_acceptance': 'pending', 'publication_authorized': False}
        write_json(output / name / 'summary.json', result)
        results.append(result)
    passed = all(row['status'] == 'programmatic_checks_passed' for row in results)
    summary = {'schema': 'ppt-complex-native-synthetic-sample.v1',
               'scope': 'synthetic_offline_exporter_sample', 'status': 'passed' if passed else 'failed',
               'cases': results, 'input_snapshot_sha256': input_hash,
               'runtime': {'python': sys.executable, 'sample_runner': measure(Path(__file__)),
                           'fixed_builder': measure(SCRIPTS / 'svg_to_pptx/pptx_package/builder.py'),
                           'fixed_fallback_stamper': measure(SCRIPTS / 'stamp_native_fallbacks.py'),
                           'declared_runtime_image': os.environ.get('PPT_RUNTIME_WORKER_IMAGE'),
                           'runtime_image_verified_by_this_script': False},
               'revision_scope': 'Authored descriptions and layout only; chart data and image pixels stay identical.',
               'owner_workflow_verified': False, 'agent_execution_verified': False,
               'official_full_workflow_postflight_verified': False,
               'human_visual_acceptance': False, 'powerpoint_acceptance': 'pending', 'publication_authorized': False}
    write_json(output / 'summary.json', summary)
    print(json.dumps({'status': summary['status'], 'summary': str(output / 'summary.json'),
                      'scope': summary['scope'], 'publication_authorized': False}, sort_keys=True))
    return 0 if passed else 2


if __name__ == '__main__':
    raise SystemExit(main())
