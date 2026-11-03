"""Server-only actual final-render rejection and retained-baseline probe.

Uses the real fixed LibreOffice/Poppler/Fitz validator, with no command factory,
monkeypatch, model, production project or publication authority. The enclosing
operator must provide a disposable, networkless validator container and record
its exact image, process stop and cleanup separately.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import traceback
from uuid import uuid4
import xml.etree.ElementTree as ET
import zipfile

from ppt_service.control.execution import ControlError
from ppt_service.control.final_pptx_validation import verify_final_pptx

SCHEMA = 'ppt-final-validator-failure-probe.v1'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
SHA = re.compile('[0-9a-f]{64}\\Z')
MAX_PPTX = 64 * 1024**2
MAX_PART = 16 * 1024**2
MAX_TOTAL = 128 * 1024**2
LABELS = ('ALPHARECOVERY', 'BETARECOVERY')
TOOLS = ('/usr/bin/pdfinfo', '/usr/bin/libreoffice', '/usr/bin/pdftoppm', '/usr/bin/fc-list')


def need(value, code):
    if not value:
        raise RuntimeError(code)


def no_links(path, *, directory=False):
    path = path.absolute()
    need('..' not in path.parts, 'unsafe_probe_path')
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        need(not stat.S_ISLNK(info.st_mode), 'linked_probe_path')
        if part != path or directory:
            need(stat.S_ISDIR(info.st_mode), 'nondirectory_probe_parent')
    return path


def read(path, limit):
    path = no_links(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
             and before.st_size <= limit, 'invalid_probe_regular_file')
        chunks, size = [], 0
        while True:
            chunk = os.read(descriptor, min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            need(size <= limit, 'probe_file_budget_exceeded')
        after = os.fstat(descriptor)
        live = path.lstat()
        identity = lambda row: (row.st_dev, row.st_ino, row.st_size,
                                row.st_mtime_ns, row.st_ctime_ns)
        need(identity(before) == identity(after) == identity(live)
             and size == before.st_size, 'probe_input_changed')
        return b''.join(chunks)
    finally:
        os.close(descriptor)


def measure(path, limit=MAX_PPTX):
    raw = read(path, limit)
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def wire(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + '\n').encode()


def write(path, raw, mode=0o600):
    no_links(path.parent, directory=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    try:
        offset = 0
        while offset < len(raw):
            count = os.write(descriptor, raw[offset:])
            need(count > 0, 'probe_short_write')
            offset += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def document(path, limit=4 * 1024**2):
    def pairs(items):
        value = {}
        for key, item in items:
            need(key not in value, 'probe_duplicate_json_key')
            value[key] = item
        return value
    def constant(_):
        raise RuntimeError('probe_invalid_json_number')
    return json.loads(read(path, limit), object_pairs_hook=pairs, parse_constant=constant)


def old_snapshot(baseline, render):
    need(os.statvfs(baseline).f_flag & os.ST_RDONLY
         and os.statvfs(render).f_flag & os.ST_RDONLY,
         'baseline_and_old_render_must_be_read_only_mounts')
    pptx = measure(baseline)
    retained = document(render / 'final-pptx-evidence.json')
    need(retained.get('status') == 'passed' and retained.get('pptx') == pptx
         and retained.get('deck', {}).get('slide_count') == 6,
         'old_synthetic_baseline_binding_invalid')
    pages = retained.get('pages')
    need(type(pages) is list and [row.get('ordinal') for row in pages] == list(range(1, 7))
         and [row.get('path') for row in pages] == [f'pages/{n}.png' for n in range(1, 7)],
         'old_synthetic_preview_roster_invalid')
    rows = {'presentation.pptx': pptx,
            'final-render/final-pptx-evidence.json': measure(render / 'final-pptx-evidence.json'),
            'final-render/text-layout.json': measure(render / 'text-layout.json'),
            'final-render/pdf/presentation.pdf': measure(render / 'pdf/presentation.pdf', MAX_TOTAL)}
    need({key: retained.get('pdf', {}).get(key) for key in ('bytes', 'sha256')}
         == rows['final-render/pdf/presentation.pdf'], 'old_synthetic_pdf_binding_invalid')
    for row in pages:
        actual = measure(render / row['path'], 8 * 1024**2)
        need(actual == {key: row.get(key) for key in ('bytes', 'sha256')},
             'old_synthetic_preview_binding_invalid')
        rows['final-render/' + row['path']] = actual
    need(sum(row['bytes'] for row in rows.values()) <= 256 * 1024**2,
         'old_synthetic_evidence_budget_exceeded')
    layout = document(render / 'text-layout.json')
    need(layout.get('binding', {}).get('pptx_sha256') == pptx['sha256']
         and layout.get('binding', {}).get('pdf_sha256')
             == rows['final-render/pdf/presentation.pdf']['sha256'],
         'old_synthetic_layout_binding_invalid')
    return rows


def rewrite_collision(raw):
    """Change only slide1.xml; retain every other native part byte-for-byte."""
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        entries = package.infolist()
        need(1 <= len(entries) <= 4096 and len({row.filename for row in entries}) == len(entries),
             'synthetic_zip_roster_invalid')
        parts, total = {}, 0
        for row in entries:
            name = row.filename
            need(name and len(name) <= 1024 and '\\' not in name and ':' not in name
                 and row.orig_filename == name and '\0' not in name
                 and not name.startswith('/') and not any(part in ('', '.', '..')
                     for part in name.rstrip('/').split('/'))
                 and not row.flag_bits & 1
                 and row.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                 and stat.S_IFMT(row.external_attr >> 16)
                     in ({0, stat.S_IFDIR} if row.is_dir() else {0, stat.S_IFREG})
                 and row.file_size <= MAX_PART
                 and row.file_size <= max(1, row.compress_size) * 1000,
                 'unsafe_synthetic_zip_part')
            total += row.file_size
            need(total <= MAX_TOTAL, 'synthetic_zip_expansion_budget_exceeded')
            content = package.read(row)
            need(len(content) == row.file_size, 'synthetic_zip_part_changed')
            parts[name] = content
    name = 'ppt/slides/slide1.xml'
    need(name in parts and len(parts[name]) <= 4 * 1024**2
         and b'<!DOCTYPE' not in parts[name].upper()
         and b'<!ENTITY' not in parts[name].upper(), 'unsafe_synthetic_slide_xml')
    root = ET.fromstring(parts[name])
    tree = root.find('{' + P + '}cSld/{' + P + '}spTree')
    need(tree is not None, 'synthetic_slide_tree_missing')
    shapes = [shape for shape in tree.findall('{' + P + '}sp')
              if shape.find('{' + P + '}txBody') is not None
              and any((text.text or '').strip() for text in shape.iter('{' + A + '}t'))]
    need(len(shapes) >= 2, 'two_native_text_frames_required')
    ids = []
    for index, shape in enumerate(shapes[:2]):
        props = shape.find('{' + P + '}nvSpPr/{' + P + '}cNvPr')
        need(props is not None and (props.get('id') or '').isdigit(), 'native_shape_id_missing')
        ids.append(int(props.get('id')))
        props.set('hidden', '0')
        old_props = shape.find('{' + P + '}spPr')
        need(old_props is not None, 'native_shape_properties_missing')
        old_props.clear()
        xfrm = ET.SubElement(old_props, '{' + A + '}xfrm')
        ET.SubElement(xfrm, '{' + A + '}off', x=str(1270000 + index * 12700),
                      y=str(2540000 + index * 25400))
        ET.SubElement(xfrm, '{' + A + '}ext', cx='7620000', cy='1270000')
        geom = ET.SubElement(old_props, '{' + A + '}prstGeom', prst='rect')
        ET.SubElement(geom, '{' + A + '}avLst')
        ET.SubElement(old_props, '{' + A + '}noFill')
        ET.SubElement(ET.SubElement(old_props, '{' + A + '}ln'), '{' + A + '}noFill')
        body = shape.find('{' + P + '}txBody')
        body.clear()
        body_props = ET.SubElement(body, '{' + A + '}bodyPr', wrap='none',
            lIns='0', rIns='0', tIns='0', bIns='0', anchor='t', numCol='1')
        ET.SubElement(body_props, '{' + A + '}noAutofit')
        ET.SubElement(body, '{' + A + '}lstStyle')
        paragraph = ET.SubElement(body, '{' + A + '}p')
        ET.SubElement(paragraph, '{' + A + '}pPr', marL='0', indent='0', algn='l')
        run = ET.SubElement(paragraph, '{' + A + '}r')
        properties = ET.SubElement(run, '{' + A + '}rPr', lang='en-US', sz='2400')
        fill = ET.SubElement(properties, '{' + A + '}solidFill')
        ET.SubElement(fill, '{' + A + '}srgbClr', val='111827' if index == 0 else '9F1239')
        for font in ('latin', 'ea', 'cs'):
            ET.SubElement(properties, '{' + A + '}' + font, typeface='Noto Sans CJK SC')
        ET.SubElement(run, '{' + A + '}t').text = LABELS[index]
        ET.SubElement(paragraph, '{' + A + '}endParaRPr', lang='en-US', sz='2400')
    need(len(set(ids)) == 2, 'native_text_shape_ids_not_unique')
    ET.register_namespace('p', P)
    ET.register_namespace('a', A)
    changed = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    need(changed != parts[name], 'synthetic_collision_was_not_changed')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for row in entries:
            target.writestr(row.filename, changed if row.filename == name else parts[row.filename])
    candidate = output.getvalue()
    need(0 < len(candidate) <= MAX_PPTX, 'synthetic_candidate_size_invalid')
    with zipfile.ZipFile(io.BytesIO(candidate)) as result:
        need(result.namelist() == [row.filename for row in entries]
             and all(result.read(key) == value for key, value in parts.items() if key != name),
             'unrelated_native_parts_changed')
    return candidate, {'changed_part': name, 'page': 1, 'shape_ids': ids,
                       'labels': list(LABELS), 'all_other_part_bytes_preserved': True,
                       'positions_pt': [[100, 200], [101, 202]], 'font_size_pt': 24}


def render_case(folder, raw, snapshot_sha, expected_code=None, collision_ids=None):
    folder.mkdir()
    (folder / 'delivery').mkdir()
    pptx = folder / 'delivery/presentation.pptx'
    write(pptx, raw, 0o444)
    expected = measure(pptx)
    manifest = {'schema': 'synthetic-final-validator-source.v1', 'files': [
        {'path': 'delivery/presentation.pptx', **expected}], 'production_authority': False}
    write(folder / 'source-manifest.json', wire(manifest))
    request = {'validation_nonce': str(uuid4()), 'pages': 6,
               'source_manifest_sha256': measure(folder / 'source-manifest.json')['sha256'],
               'input_snapshot_sha256': snapshot_sha}
    write(folder / 'validation-request.json', wire(request))
    output = folder / 'final-render'
    caught, report = None, None
    try:
        report = verify_final_pptx(pptx, expected, request, output)
    except Exception as error:
        caught = error
        write(folder / 'exception.json', wire({'error_type': type(error).__name__,
              'code': getattr(error, 'code', None), 'expected_code': expected_code}))
        write(folder / 'exception.txt', traceback.format_exc().encode())
    need(measure(pptx) == expected, 'candidate_changed_during_validation')
    if expected_code is not None:
        need(isinstance(caught, ControlError) and caught.code == expected_code,
             'expected_real_validator_rejection_not_observed')
        need(not (output / 'final-pptx-evidence.json').exists(),
             'failed_candidate_has_success_evidence')
        result = {'expected_rejection_verified': True, 'actual_error_code': caught.code,
                  'candidate': expected, 'validation_nonce': request['validation_nonce'],
                  'no_success_final_evidence': True}
        if collision_ids is not None:
            layout = document(output / 'text-layout.json')
            collisions = [row for row in layout.get('issues', [])
                          if row.get('code') == 'text_collision' and row.get('page') == 1
                          and {row.get('shape_id'), row.get('other_shape_id')} == set(collision_ids)]
            need(layout.get('status') == 'failed' and collisions
                 and layout.get('binding', {}).get('pptx_sha256') == expected['sha256']
                 and layout.get('binding', {}).get('controller_nonce') == request['validation_nonce'],
                 'specific_rendered_glyph_collision_not_observed')
            result['text_collision'] = collisions
            result['text_layout'] = measure(output / 'text-layout.json')
        return result
    if caught is not None:
        raise caught
    need(report is not None and report.get('status') == 'passed'
         and report.get('pptx') == expected and len(report.get('pages', [])) == 6
         and report.get('controller_nonce') == request['validation_nonce']
         and report.get('source_unchanged') is True
         and report.get('human_visual_acceptance') is False
         and report.get('publication_authorized') is False,
         'real_healthy_final_render_not_verified')
    return {'healthy_final_render_verified': True, 'candidate': expected,
            'validation_nonce': request['validation_nonce'],
            'final_evidence': measure(output / 'final-pptx-evidence.json'),
            'text_layout_status': report.get('text_layout', {}).get('status')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True, choices=('quality', 'renderer-unavailable', 'healthy'))
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--baseline-render', required=True, type=Path)
    parser.add_argument('--expected-baseline-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--disabled-tool', choices=TOOLS, default='/usr/bin/pdfinfo')
    args = parser.parse_args(argv)
    need(sys.platform == 'linux' and os.environ.get('PPT_RUN_FINAL_FAILURE_PROBE') == 'true',
         'explicit_server_only_probe_required')
    need(SHA.fullmatch(args.expected_baseline_sha256), 'expected_baseline_sha256_required')
    baseline = no_links(args.baseline)
    retained = no_links(args.baseline_render, directory=True)
    before = old_snapshot(baseline, retained)
    need(before['presentation.pptx']['sha256'] == args.expected_baseline_sha256,
         'exact_synthetic_baseline_mismatch')
    output = args.output.absolute()
    no_links(output.parent, directory=True)
    need(not output.exists() and not output.is_symlink()
         and not output.is_relative_to(baseline.parent)
         and not output.is_relative_to(retained), 'fresh_separate_probe_output_required')
    output.mkdir(mode=0o700)
    snapshot = {'schema': SCHEMA, 'case': args.case, 'baseline': before,
                'synthetic_only': True, 'production_authority': False}
    write(output / 'retained-baseline-before.json', wire(snapshot))
    snapshot_sha = measure(output / 'retained-baseline-before.json')['sha256']
    result = {'schema': SCHEMA, 'case': args.case, 'passed': False,
              'scope': 'actual_isolated_validator_component',
              'baseline': before['presentation.pptx'], 'results': {},
              'models_called': 0, 'production_business_rows_written': False,
              'agent_workflow_verified': False, 'owner_workflow_verified': False,
              'human_visual_acceptance': False, 'publication_authorized': False,
              'enclosing_container_stop_and_cleanup_verified_by_this_script': False}
    try:
        raw = read(baseline, MAX_PPTX)
        if args.case == 'quality':
            candidate, change = rewrite_collision(raw)
            write(output / 'synthetic-collision-change.json', wire(change))
            result['results']['rejected'] = render_case(output / 'rejected', candidate, snapshot_sha,
                'final_pptx_text_layout_failed', change['shape_ids'])
            result['results']['healthy_followup'] = render_case(output / 'healthy-followup', raw, snapshot_sha)
        elif args.case == 'renderer-unavailable':
            tool = no_links(Path(args.disabled_tool))
            info = tool.stat()
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                 and not info.st_mode & 0o111 and not os.access(tool, os.X_OK)
                 and os.statvfs(tool).f_flag & os.ST_RDONLY,
                 'fixed_tool_must_be_real_read_only_nonexecutable_file')
            result['disabled_fixed_tool'] = {'path': str(tool), **measure(tool, 256 * 1024),
                                            'executable': False, 'read_only_mount': True}
            result['results']['rejected'] = render_case(output / 'rejected', raw, snapshot_sha,
                                                       'final_pptx_renderer_unavailable')
        else:
            result['results']['healthy'] = render_case(output / 'healthy', raw, snapshot_sha)
        result['passed'] = True
    except Exception as error:
        result.update(passed=False, error_type=type(error).__name__,
                      error_code=getattr(error, 'code', None), failure=str(error)[:500])
        write(output / 'probe-failure.txt', traceback.format_exc().encode())
    finally:
        try:
            after = old_snapshot(baseline, retained)
            result['baseline_and_old_evidence_unchanged'] = after == before
            need(after == before, 'retained_baseline_or_evidence_changed')
            write(output / 'retained-baseline-after.json', wire(after))
        except Exception as error:
            result.update(passed=False, baseline_and_old_evidence_unchanged=False,
                          baseline_failure=str(error)[:500])
        write(output / 'summary.json', wire(result))
    print(json.dumps({'case': args.case, 'passed': result['passed'],
                      'summary': str(output / 'summary.json'), 'scope': result['scope']}, sort_keys=True))
    return 0 if result['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
