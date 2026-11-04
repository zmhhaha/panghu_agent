#!/usr/bin/env python3
"""R2: exercise the image-owned parser CLI with actual production limits.

Outer orchestration is Linux/root/stdlib-only. Fixture construction and parsing
run in a disposable non-root, network-none container using the exact installed
validator image. No production data, database, credentials or model is mounted.
This does not claim the Kubernetes coordinator's deadline/lifecycle acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import runpy
import stat
import struct
import subprocess
import sys
import time
import uuid
import zipfile


LABEL = 'ppt.parser-acceptance.owner'
DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
MIB = 1024**2
TYPES = b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
DOCUMENT = (b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b'<w:p><w:r><w:t>R2 synthetic source.</w:t></w:r></w:p></w:document>')
IMAGE = re.compile(r'(?:[A-Za-z0-9][A-Za-z0-9._:/-]*@)?sha256:[a-f0-9]{64}\Z')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(65536):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def package(path, extra=(), *, document=DOCUMENT, compression=zipfile.ZIP_STORED):
    with zipfile.ZipFile(path, 'w', compression=compression) as archive:
        archive.writestr('[Content_Types].xml', TYPES)
        archive.writestr('word/document.xml', document)
        for name, data in extra:
            archive.writestr(name, data)
    return path


def controlled_expansion(path, expanded_total):
    """A real <25 MiB DOCX with ~3:1 compression and the requested total.

    Each 8 KiB random block repeats exactly three times. New random blocks avoid
    accidentally exceeding the real 100:1 per-entry guard. No limits are patched.
    """
    remaining = expanded_total - len(TYPES) - len(DOCUMENT)
    rng = random.Random(20261104)
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr('[Content_Types].xml', TYPES)
        archive.writestr('word/document.xml', DOCUMENT)
        part = 0
        while remaining:
            size = min(16 * MIB, remaining)
            with archive.open(f'word/payload-{part}.data', 'w') as stream:
                left = size
                while left:
                    block = rng.randbytes(8192) * 3
                    block = block[:left]
                    stream.write(block)
                    left -= len(block)
            remaining -= size
            part += 1
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        assert sum(x.file_size for x in entries) == expanded_total
        assert all(x.file_size <= 16 * MIB for x in entries)
        assert all(x.file_size <= max(1, x.compress_size) * 100 for x in entries)
    assert path.stat().st_size <= 25 * MIB


def source(path, mime, *, key=None):
    return {'id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'r2:' + path.name)),
            'path': key or path.name, 'sha256': digest(path),
            'bytes': path.stat().st_size, 'media_type': mime}


def build_case(root, kind):
    """Return (rows, expected_error, assertions) for a fresh private directory."""
    from PIL import Image
    import fitz

    path = root / 'synthetic.md'
    expect, checks = None, {}
    if kind in ('markdown', 'text-at-limit', 'text-over-limit', 'project-at-limit',
                'project-over-limit', 'invalid-text', 'invalid-utf8', 'source-hash',
                'too-many-sources', 'symlink', 'hardlink', 'inventory-traversal'):
        length = 500000 if 'at-limit' in kind or kind == 'project-over-limit' else 500001 if kind == 'text-over-limit' else 20
        path.write_text('字' * length, encoding='utf-8')
        if kind == 'invalid-text':
            path.write_bytes(b'PRIVATE_SENTINEL\x00text')
            expect = 'invalid_text'
        if kind == 'invalid-utf8':
            path.write_bytes(b'\xff\xfePRIVATE_SENTINEL')
            expect = 'parse_failed'
        rows = [source(path, 'text/markdown')]
        if kind in ('project-at-limit', 'project-over-limit'):
            for index in (2, 3):
                other = root / f'synthetic-{index}.md'
                other.write_text('字' * 500000, encoding='utf-8')
                rows.append(source(other, 'text/markdown'))
            if kind == 'project-over-limit':
                other = root / 'synthetic-4.md'
                other.write_text('字', encoding='utf-8')
                rows.append(source(other, 'text/markdown'))
                expect = 'project_text_limit'
            else:
                checks['total_text'] = 1500000
        if kind == 'text-at-limit':
            checks['total_text'] = 500000
        if kind == 'text-over-limit':
            expect = 'text_limit'
        if kind == 'source-hash':
            rows[0]['sha256'] = 'f' * 64
            expect = 'source_hash_mismatch'
        if kind == 'too-many-sources':
            rows *= 11
            expect = 'source_limit'
        if kind in ('symlink', 'hardlink'):
            linked = root / 'linked.md'
            linked.symlink_to(path.name) if kind == 'symlink' else os.link(path, linked)
            rows[0]['path'] = linked.name
            expect = 'parse_failed'
        if kind == 'inventory-traversal':
            rows[0]['path'] = '../PRIVATE_SENTINEL.md'
            expect = 'parse_failed'
        return rows, expect, checks
    if kind.startswith('png') or kind in ('jpeg', 'image-width-over', 'image-pixels-over'):
        path = root / ('synthetic.jpg' if kind == 'jpeg' else 'synthetic.png')
        dimensions = {'image-width-over': (20001, 1), 'image-pixels-over': (5000, 4001),
                      'png-width-at-limit': (20000, 1), 'png-pixels-at-limit': (5000, 4000)}.get(kind, (40, 30))
        Image.new('RGB', dimensions, '#54708a').save(path)
        mime = 'image/jpeg' if kind == 'jpeg' else 'image/png'
        if kind in ('png-bytes-at-limit', 'png-bytes-over-limit'):
            size = 25 * MIB + int(kind.endswith('over-limit'))
            with path.open('ab') as stream:
                stream.truncate(size)
            checks['source_bytes'] = size
        if kind == 'png-corrupt':
            path.write_bytes(b'\x89PNG\r\n\x1a\nPRIVATE_SENTINEL')
            expect = 'parse_failed'
        elif kind in ('image-width-over', 'image-pixels-over'):
            expect = 'image_limit'
        elif kind == 'png-bytes-over-limit':
            expect = 'parse_failed'
        else:
            checks['image'] = {'width': dimensions[0], 'height': dimensions[1]}
        return [source(path, mime)], expect, checks
    if kind.startswith('pdf'):
        path = root / 'synthetic.pdf'
        pages = 150 if kind == 'pdf-pages-at-limit' else 151 if kind == 'pdf-pages-over-limit' else 1
        with fitz.open() as document:
            for index in range(pages):
                page = document.new_page()
                if kind != 'pdf-scanned':
                    page.insert_text((30, 30), f'R2 synthetic page {index + 1}')
            if kind == 'pdf-attachment':
                document.embfile_add('PRIVATE_SENTINEL.txt', b'PRIVATE_SENTINEL')
            if kind == 'pdf-form':
                widget = fitz.Widget()
                widget.field_name = 'PRIVATE_SENTINEL'
                widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
                widget.rect = fitz.Rect(50, 50, 150, 70)
                page.add_widget(widget)
            options = {'encryption': fitz.PDF_ENCRYPT_AES_256, 'owner_pw': 'synthetic-owner', 'user_pw': 'synthetic-reader'} if kind == 'pdf-encrypted' else {}
            document.save(path, **options)
        expect = {'pdf-pages-over-limit': 'pdf_page_limit', 'pdf-encrypted': 'unsupported_pdf',
                  'pdf-attachment': 'unsupported_pdf', 'pdf-form': 'pdf_active_form',
                  'pdf-scanned': 'scanned_pdf_unsupported', 'pdf-corrupt': 'parse_failed'}.get(kind)
        if kind == 'pdf-corrupt':
            path.write_bytes(b'%PDF-1.7\nPRIVATE_SENTINEL')
        elif expect is None:
            checks['pages'] = pages
        return [source(path, 'application/pdf')], expect, checks
    if kind == 'unsupported-media':
        path.write_bytes(b'PRIVATE_SENTINEL')
        return [source(path, 'application/x-executable')], 'unsupported_media', checks

    path = root / 'synthetic.docx'
    if kind in ('docx-expanded-at-limit', 'docx-expanded-over-limit'):
        controlled_expansion(path, 64 * MIB + int(kind.endswith('over-limit')))
        expect = 'zip_expansion_limit' if kind.endswith('over-limit') else None
        checks['expanded_bytes'] = 64 * MIB + int(kind.endswith('over-limit'))
    elif kind in ('docx-part-at-limit', 'docx-part-over-limit'):
        size = 16 * MIB + int(kind.endswith('over-limit'))
        package(path, [('word/payload.data', b'x' * size)])
        expect = 'zip_expansion_limit' if kind.endswith('over-limit') else None
    elif kind in ('docx-entry-at-limit', 'docx-entry-over-limit'):
        count = 2048 + int(kind.endswith('over-limit'))
        package(path, [(f'word/part-{i}.data', b'') for i in range(count - 2)])
        expect = 'zip_entry_limit' if kind.endswith('over-limit') else None
    elif kind in ('docx-xml-bytes-at-limit', 'docx-xml-bytes-over-limit'):
        size = 8 * MIB + int(kind.endswith('over-limit'))
        package(path, [('word/extra.xml', b'<a>' + b'x' * (size - 7) + b'</a>')])
        expect = 'unsafe_xml' if kind.endswith('over-limit') else None
    elif kind in ('docx-xml-nodes-at-limit', 'docx-xml-nodes-over-limit'):
        nodes = 100000 + int(kind.endswith('over-limit'))
        package(path, [('word/extra.xml', b'<root>' + b'<a/>' * (nodes - 1) + b'</root>')])
        expect = 'xml_limit' if kind.endswith('over-limit') else None
    elif kind in ('docx-xml-depth-at-limit', 'docx-xml-depth-over-limit'):
        levels = 65 + int(kind.endswith('over-limit'))
        package(path, [('word/extra.xml', b'<a>' * levels + b'</a>' * levels)])
        expect = 'xml_limit' if kind.endswith('over-limit') else None
    else:
        extras = {
            'docx-macro': [('word/vbaProject.bin', b'PRIVATE_SENTINEL')],
            'docx-active': [('word/activeX/control.xml', b'PRIVATE_SENTINEL')],
            'docx-encrypted-part': [('EncryptedPackage', b'PRIVATE_SENTINEL')],
            'docx-external': [('word/_rels/document.xml.rels', b'<Relationships><Relationship TargetMode="External" Target="https://example.invalid/PRIVATE_SENTINEL"/></Relationships>')],
            'docx-path-parent': [('../PRIVATE_SENTINEL.xml', b'PRIVATE_SENTINEL')],
            'docx-path-absolute': [('/PRIVATE_SENTINEL.xml', b'PRIVATE_SENTINEL')],
            'docx-path-backslash': [('word\\PRIVATE_SENTINEL.xml', b'PRIVATE_SENTINEL')],
            'docx-path-drive': [('C:/PRIVATE_SENTINEL.xml', b'PRIVATE_SENTINEL')],
            'docx-duplicate': [('WORD/DOCUMENT.XML', DOCUMENT)],
            'docx-bomb': [('word/expansion.data', b'x' * 200000)],
            'docx-doctype': [('word/extra.xml', b'<!DOCTYPE a><a/>')],
            'docx-entity': [('word/extra.xml', b'<!ENTITY a "PRIVATE_SENTINEL"><a/>')],
        }
        package(path, extras.get(kind, []), compression=zipfile.ZIP_DEFLATED if kind == 'docx-bomb' else zipfile.ZIP_STORED)
        expect = {
            'docx-macro': 'active_office_content', 'docx-active': 'active_office_content',
            'docx-encrypted-part': 'active_office_content', 'docx-external': 'external_office_reference',
            'docx-path-parent': 'parse_failed', 'docx-path-absolute': 'parse_failed',
            'docx-path-backslash': 'parse_failed', 'docx-path-drive': 'parse_failed',
            'docx-duplicate': 'duplicate_zip_part', 'docx-bomb': 'zip_expansion_limit',
            'docx-doctype': 'unsafe_xml', 'docx-entity': 'unsafe_xml',
            'docx-encrypted-flag': 'unsupported_zip_part', 'docx-symlink': 'unsupported_zip_part',
            'docx-corrupt': 'parse_failed',
        }.get(kind)
        if kind == 'docx-encrypted-flag':
            raw = bytearray(path.read_bytes())
            for signature, offset in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):
                cursor = raw.find(signature)
                while cursor >= 0:
                    flags = struct.unpack_from('<H', raw, cursor + offset)[0]
                    struct.pack_into('<H', raw, cursor + offset, flags | 1)
                    cursor = raw.find(signature, cursor + 4)
            path.write_bytes(raw)
        if kind == 'docx-symlink':
            link = zipfile.ZipInfo('word/link')
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(path, 'a') as archive:
                archive.writestr(link, b'/PRIVATE_SENTINEL')
        if kind == 'docx-corrupt':
            path.write_bytes(b'PK\x03\x04PRIVATE_SENTINEL')
    return [source(path, DOCX)], expect, checks


CASES = (
    'markdown', 'docx', 'pdf', 'png', 'jpeg',
    'text-at-limit', 'text-over-limit', 'project-at-limit', 'project-over-limit',
    'png-bytes-at-limit', 'png-bytes-over-limit', 'png-width-at-limit',
    'image-width-over', 'png-pixels-at-limit', 'image-pixels-over',
    'pdf-pages-at-limit', 'pdf-pages-over-limit', 'pdf-encrypted', 'pdf-attachment',
    'pdf-form', 'pdf-scanned', 'pdf-corrupt', 'png-corrupt',
    'docx-expanded-at-limit', 'docx-expanded-over-limit', 'docx-part-at-limit',
    'docx-part-over-limit', 'docx-entry-at-limit', 'docx-entry-over-limit',
    'docx-xml-bytes-at-limit', 'docx-xml-bytes-over-limit',
    'docx-xml-nodes-at-limit', 'docx-xml-nodes-over-limit',
    'docx-xml-depth-at-limit', 'docx-xml-depth-over-limit',
    'docx-macro', 'docx-active', 'docx-encrypted-part', 'docx-external',
    'docx-path-parent', 'docx-path-absolute', 'docx-path-backslash', 'docx-path-drive',
    'docx-duplicate', 'docx-bomb', 'docx-doctype', 'docx-entity',
    'docx-encrypted-flag', 'docx-symlink', 'docx-corrupt',
    'invalid-text', 'invalid-utf8', 'source-hash', 'too-many-sources',
    'symlink', 'hardlink', 'inventory-traversal', 'unsupported-media',
)


def inner(output):
    from ppt_service.control import parsing
    from ppt_service.control import execution
    import fitz
    import PIL

    if Path(parsing.__file__).resolve() != Path('/opt/ppt-service/ppt_service/control/parsing.py'):
        raise RuntimeError('parser_must_be_image_owned')
    constants = {k: getattr(parsing, k) for k in ('MAX_BYTES', 'MAX_TEXT', 'MAX_TOTAL_TEXT', 'MAX_PAGES', 'MAX_ZIP')}
    if constants != {'MAX_BYTES': 25 * MIB, 'MAX_TEXT': 500000, 'MAX_TOTAL_TEXT': 1500000, 'MAX_PAGES': 150, 'MAX_ZIP': 64 * MIB}:
        raise RuntimeError('unexpected_production_limits')
    write_json(output / 'installed-parser.json', {
        'module': parsing.__file__, 'sha256': digest(parsing.__file__),
        'execution_sha256': digest(execution.__file__), 'limits': constants,
        'python': sys.version, 'pymupdf': fitz.VersionBind, 'pillow': PIL.__version__,
        'uid': os.getuid(), 'gid': os.getgid(), 'root_readonly': os.statvfs('/').f_flag & os.ST_RDONLY != 0,
        'proc_status': [line for line in Path('/proc/self/status').read_text().splitlines()
                        if line.startswith(('Uid:', 'Gid:', 'CapEff:', 'NoNewPrivs:', 'Seccomp:'))],
    })
    case_root = output / 'cases'
    case_root.mkdir()
    (case_root / 'PRIVATE_SENTINEL.md').write_text('PRIVATE_SENTINEL', encoding='utf-8')
    results = []
    for name in CASES:
        folder = case_root / name
        inputs, outputs = folder / 'inputs', folder / 'output'
        inputs.mkdir(parents=True)
        outputs.mkdir()
        start = time.monotonic()
        result = {'case': name, 'passed': False}
        try:
            rows, expected, checks = build_case(inputs, name)
            snapshot = hashlib.sha256(json.dumps({'case': name, 'sources': rows}, sort_keys=True).encode()).hexdigest()
            write_json(inputs / 'inventory.json', {'schema_version': 1, 'input_snapshot_sha256': snapshot, 'sources': rows})
            before = {p.name: digest(p) for p in inputs.iterdir() if p.is_file() and not p.is_symlink()}
            for path in inputs.iterdir():
                if not path.is_symlink():
                    path.chmod(0o444)
            inputs.chmod(0o555)
            command = [sys.executable, '-m', 'ppt_service.control.parsing', '--inputs', str(inputs), '--output', str(outputs / 'parsed.json')]
            run = subprocess.run(command, cwd='/', stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                                 env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'PYTHONPATH': '/opt/ppt-service',
                                      'PYTHONDONTWRITEBYTECODE': '1', 'HOME': '/tmp'})
            if len(run.stdout) + len(run.stderr) > MIB:
                raise RuntimeError('unexpected_parser_log_size')
            (outputs / 'stdout.log').write_bytes(run.stdout)
            (outputs / 'stderr.log').write_bytes(run.stderr)
            report = json.loads((outputs / 'parsed.json').read_text())
            assert report['input_snapshot_sha256'] == snapshot
            if expected is not None:
                assert run.returncode == 2
                assert report == {'schema': 'ppt-service.parse-failure.v1', 'status': 'failed',
                                  'input_snapshot_sha256': snapshot, 'error_code': expected}
                assert 'PRIVATE_SENTINEL' not in (outputs / 'parsed.json').read_text()
            else:
                assert run.returncode == 0
                assert report['schema_version'] == 1 and len(report['sources']) == len(rows)
                for actual, row in zip(report['sources'], rows):
                    assert all(actual[key] == row[key] for key in ('id', 'path', 'sha256'))
                    assert actual['parse_status'] == 'succeeded'
                if 'total_text' in checks:
                    assert sum(len(x['text']) for x in report['sources']) == checks['total_text']
                if 'pages' in checks:
                    assert report['sources'][0]['pages'] == checks['pages']
                if 'image' in checks:
                    assert report['sources'][0]['image'] == checks['image']
            after = {p.name: digest(p) for p in inputs.iterdir() if p.is_file() and not p.is_symlink()}
            assert before == after
            result.update(passed=True, exit_code=run.returncode, expected_error=expected,
                          observed_error=report.get('error_code'), snapshot_sha256=snapshot,
                          input_unchanged=True, input_bytes=sum(x['bytes'] for x in rows),
                          checks=checks, report_sha256=digest(outputs / 'parsed.json'))
        except Exception as exc:
            result.update(error=type(exc).__name__ + ':' + str(exc))
        result['elapsed_seconds'] = round(time.monotonic() - start, 3)
        results.append(result)
        write_json(output / 'case-results.json', results)
        print(json.dumps(result), flush=True)
    status = {'passed': len(results) == len(CASES) and all(x['passed'] for x in results),
              'cases': len(results), 'failed': [x['case'] for x in results if not x['passed']],
              'scope': 'actual image-owned parser CLI; isolated Docker, synthetic files, real unchanged limits',
              'not_proven': ['current Kubernetes coordinator timeout/stop policy', 'real Casdoor upload UI',
                             'production database asset status transitions', 'production source content']}
    write_json(output / 'inner-status.json', status)
    return 0 if status['passed'] else 1


def outer(args):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('run_outer_on_linux_server_as_root')
    if not IMAGE.fullmatch(args.image):
        raise RuntimeError('immutable_present_image_required')
    source = args.source.resolve(strict=True)
    output = args.output.resolve(strict=False)
    if output.exists() or output.is_symlink() or source in output.parents or source == output:
        raise RuntimeError('output_must_be_new_and_outside_source')
    if ',' in str(output) or ',' in str(source):
        raise RuntimeError('invalid_mount_path')
    helper = runpy.run_path(str(source / 'ppt_service/tests/e2e/run_current_security_linux.py'))
    command, clean_git, snapshot_git = helper['command'], helper['clean_git'], helper['snapshot_git']
    commit = clean_git(source)
    image_info = json.loads(command(['docker', 'image', 'inspect', args.image]).stdout)[0]
    output.mkdir(mode=0o755)
    snapshot, evidence = output / 'source', output / 'evidence'
    evidence.mkdir(mode=0o700)
    os.chown(evidence, 10000, 10000)
    source_rows = snapshot_git(source, snapshot, commit)
    write_json(evidence / 'source.json', {'commit': commit, 'files': source_rows})
    write_json(evidence / 'image.json', {'requested': args.image, 'image_id': image_info['Id'],
               'repo_digests': image_info.get('RepoDigests', []), 'architecture': image_info.get('Architecture')})
    run_id = 'ppt-parser-r2-' + uuid.uuid4().hex
    code, cleaned, error = None, False, None
    try:
        command(['docker', 'create', '--pull=never', '--name', run_id, '--label', LABEL + '=' + run_id,
                 '--network', 'none', '--user', '10000:10000', '--read-only', '--cap-drop', 'ALL',
                 '--security-opt', 'no-new-privileges', '--pids-limit', '128', '--memory', '1024m',
                 '--cpus', '2', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=128m,mode=1777',
                 '--mount', f'type=bind,src={snapshot},dst=/harness,readonly',
                 '--mount', f'type=bind,src={evidence},dst=/evidence',
                 '--env', 'PYTHONDONTWRITEBYTECODE=1', '--env', 'PYTHONPATH=/opt/ppt-service',
                 '--env', 'HOME=/tmp', '--workdir', '/', '--entrypoint', '/usr/local/bin/python',
                 args.image, '/harness/ppt_service/tests/acceptance/current_parser_image.py',
                 '--inner', '--output', '/evidence'])
        result = command(['docker', 'start', '--attach', run_id], timeout=900, check=False)
        (evidence / 'container.log').write_text(result.stdout, encoding='utf-8')
        state = json.loads(command(['docker', 'inspect', run_id]).stdout)[0]
        if state['Config']['Labels'].get(LABEL) != run_id:
            raise RuntimeError('container_owner_mismatch')
        code = state['State']['ExitCode']
        write_json(evidence / 'container-state.json', {'id': state['Id'], 'image_id': state['Image'],
                   'state': state['State'], 'network_mode': state['HostConfig']['NetworkMode'],
                   'user': state['Config']['User'], 'readonly_rootfs': state['HostConfig']['ReadonlyRootfs'],
                   'security_opt': state['HostConfig']['SecurityOpt'], 'cap_drop': state['HostConfig']['CapDrop']})
    except Exception as exc:
        error = type(exc).__name__ + ':' + str(exc)
    finally:
        owner = command(['docker', 'inspect', '--format', '{{index .Config.Labels "' + LABEL + '"}}', run_id], check=False)
        if owner.returncode == 0 and owner.stdout.strip() == run_id:
            removed = command(['docker', 'rm', '--force', '--volumes', run_id], check=False)
            inventory = command(['docker', 'ps', '-aq', '--filter', 'label=' + LABEL + '=' + run_id], check=False)
            cleaned = removed.returncode == 0 and inventory.returncode == 0 and not inventory.stdout.strip()
        write_json(evidence / 'cleanup.json', {'run_id': run_id, 'owned_resources_removed': cleaned})
    installed = json.loads((evidence / 'installed-parser.json').read_text()) if (evidence / 'installed-parser.json').is_file() else {}
    expected = {x['path']: x['sha256'] for x in source_rows}
    same_parser = installed.get('sha256') == expected['ppt_service/control/parsing.py']
    same_execution = installed.get('execution_sha256') == expected['ppt_service/control/execution.py']
    unchanged = all((snapshot / x['path']).is_file() and digest(snapshot / x['path']) == x['sha256'] for x in source_rows)
    internal = json.loads((evidence / 'inner-status.json').read_text()) if (evidence / 'inner-status.json').is_file() else {}
    status = {'passed': code == 0 and cleaned and error is None and unchanged and same_parser and same_execution and internal.get('passed') is True,
              'commit': commit, 'image': args.image, 'exit_code': code, 'error': error,
              'image_parser_matches_git': same_parser, 'image_execution_contract_matches_git': same_execution,
              'source_unchanged': unchanged, 'owned_resources_removed': cleaned, 'inner': internal}
    write_json(evidence / 'status.json', status)
    print(json.dumps(status, ensure_ascii=False))
    return 0 if status['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--image')
    parser.add_argument('--inner', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inner:
        return inner(args.output)
    if args.source is None or args.image is None:
        parser.error('--source and --image are required for outer orchestration')
    return outer(args)


if __name__ == '__main__':
    raise SystemExit(main())
