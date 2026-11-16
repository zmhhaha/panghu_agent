"""Explicit media-only Lab publication. Does not call Agent or modify databases.

Run `bundle` on the trusted host, then `install` in a short-lived trusted PVC
writer. Existing run directories are immutable; identical content is reusable.
No generated HTML/JS file is copied or evaluated.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
from uuid import uuid4

MAX_ARCHIVE = 256 * 1024 * 1024
MAX_FILES = 1100
SCHEMA = 'video.animation-experiment-publication.v1'
MANIFEST = '_publication.json'


def need(value, code):
    if not value:
        raise RuntimeError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()


def api():
    from video_workbench.animation_experiments import read_run, _ordinary_file
    return read_run, _ordinary_file


def verified_files(directory):
    read_run, ordinary_file = api()
    directory = Path(directory)
    row = read_run(directory)
    need(row['id'] == directory.name and row['status'] in {'rendered', 'failed'},
         'publication_requires_terminal_run')
    files = {'experiment-run.json': ordinary_file(directory, 'experiment-run.json', 3 * 1024 * 1024)}
    for name, entry in row['artifacts'].items():
        raw = ordinary_file(directory, name, 32 * 1024 * 1024)
        need(len(raw) == entry['size'] and sha(raw) == entry['sha256'],
             'publication_artifact_mismatch')
        files[name] = raw
    need('report.json' in files, 'publication_report_required')
    frames = {name: raw for name, raw in files.items() if name.startswith('frames/')}
    need(len(frames) <= 240, 'publication_frame_limit')
    report = json.loads(files['report.json'])
    if row['status'] == 'rendered':
        outcome = report.get('renderer', {})
        receipt = outcome.get('receipt', {})
        count = int(row['input_document']['constraints']['duration_seconds'] * 24)
        need(row['provenance'].get('stop_proven') is True
             and outcome.get('status') == 'rendered'
             and outcome.get('cleanup', {}).get('status') in {'removed_and_confirmed_absent', 'confirmed_absent'}
             and receipt.get('status') == 'rendered' and receipt.get('decode_verified') is True,
             'publication_stop_render_proof_required')
        need(len(frames) == count == receipt.get('frame_count')
             and set(frames) == {'frames/%06d.jpg' % index for index in range(count)},
             'publication_frame_sequence')
        need(files['output.mp4'][4:8] == b'ftyp'
             and sha(files['output.mp4']) == receipt.get('output_sha256'),
             'publication_media_receipt_mismatch')
        source_frames = receipt.get('frames')
        need(isinstance(source_frames, list) and len(source_frames) == count + 1,
             'publication_capture_receipt_invalid')
        for index in range(count):
            raw = frames['frames/%06d.jpg' % index]
            observed = source_frames[index]
            need(observed.get('name') == 'frame-%06d.jpg' % index
                 and observed.get('sha256') == sha(raw) and observed.get('bytes') == len(raw)
                 and raw[:2] == b'\xff\xd8' and raw[-2:] == b'\xff\xd9',
                 'publication_frame_receipt_mismatch')
        published = report.get('published_frames')
        need(isinstance(published, list) and len(published) == count,
             'publication_report_frames_invalid')
        need(all(entry == dict(index=index, seconds=index / 24, artifact='frames/%06d.jpg' % index)
                 for index, entry in enumerate(published)), 'publication_report_frames_invalid')
    else:
        need(not frames and 'output.mp4' not in files, 'publication_failed_run_has_media')
    return row, files


def bundle(args):
    output = Path(args.output)
    need(not output.exists(), 'publication_archive_exists')
    records, all_files = [], {}
    for directory in args.run:
        row, files = verified_files(directory)
        need(row['id'] not in {record['id'] for record in records}, 'publication_duplicate_run')
        records.append(dict(id=row['id'], identity=row['identity'], status=row['status'],
                            input_role=row['input_role'], input_sha256=row['input_sha256']))
        all_files.update({row['id'] + '/' + name: raw for name, raw in files.items()})
    need(1 <= len(records) <= 5 and len(all_files) <= MAX_FILES
         and sum(map(len, all_files.values())) <= MAX_ARCHIVE, 'publication_size_limit')
    manifest = dict(schema=SCHEMA, records=records,
                    files={name: dict(bytes=len(raw), sha256=sha(raw)) for name, raw in sorted(all_files.items())},
                    generated_sources='JSON_text_only', model_calls=0, database_writes=0,
                    aesthetic_approval=False, production_adoption=False)
    manifest_raw = canonical(manifest)
    memory = io.BytesIO()
    with tarfile.open(fileobj=memory, mode='w') as archive:
        for name, raw in [(MANIFEST, manifest_raw), *sorted(all_files.items())]:
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            item.uid = item.gid = 10001
            item.mode = 0o644
            item.mtime = 0
            archive.addfile(item, io.BytesIO(raw))
    raw = memory.getvalue()
    need(len(raw) <= MAX_ARCHIVE, 'publication_archive_limit')
    with output.open('xb') as stream:
        stream.write(raw)
    output.chmod(0o600)
    receipt = dict(schema=SCHEMA, status='bundled', archive_sha256=sha(raw),
                   archive_bytes=len(raw), manifest_sha256=sha(manifest_raw), records=records,
                   copied_generated_executable_files=0, model_calls=0, database_writes=0)
    print(json.dumps(receipt, sort_keys=True))


def unpack_publication(raw, manifest_sha256):
    need(re.fullmatch(r'[0-9a-f]{64}', manifest_sha256), 'publication_manifest_sha_required')
    need(0 < len(raw) <= MAX_ARCHIVE, 'publication_archive_limit')
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            need(member.isfile() and not member.issym() and not member.islnk()
                 and member.name not in files and not name.is_absolute()
                 and '..' not in name.parts and '.' not in name.parts
                 and str(name) == member.name and 0 < member.size <= 32 * 1024 * 1024,
                 'publication_archive_member_invalid')
            files[member.name] = archive.extractfile(member).read()
            need(len(files) <= MAX_FILES + 1, 'publication_file_limit')
    need(MANIFEST in files, 'publication_manifest_required')
    manifest_raw = files.pop(MANIFEST)
    need(sha(manifest_raw) == manifest_sha256, 'publication_manifest_changed')
    manifest = json.loads(manifest_raw)
    need(manifest.get('schema') == SCHEMA and manifest.get('generated_sources') == 'JSON_text_only',
         'publication_manifest_invalid')
    need(set(files) == set(manifest['files']), 'publication_file_set_mismatch')
    for name, raw in files.items():
        need(manifest['files'][name] == dict(bytes=len(raw), sha256=sha(raw)),
             'publication_file_mismatch')
    expected_ids = {row['id'] for row in manifest['records']}
    need(1 <= len(expected_ids) == len(manifest['records']) <= 5
         and all(re.fullmatch(r'[a-z][a-z0-9-]{0,63}', name) for name in expected_ids),
         'publication_run_ids_invalid')
    need({PurePosixPath(name).parts[0] for name in files} == expected_ids, 'publication_run_set_invalid')
    for name in files:
        parts = PurePosixPath(name).parts
        need((len(parts) == 2 and parts[1] in {'experiment-run.json', 'report.json', 'output.mp4'})
             or (len(parts) == 3 and parts[1] == 'frames' and re.fullmatch(r'[0-9]{6}\.jpg', parts[2])),
             'publication_file_type_forbidden')
    return manifest, files


def install_validated(root, manifest, files):
    """Private writer core; CLI enforces the exact mounted destination first."""
    root = Path(root).absolute()
    need(not any(path.is_symlink() for path in (root, *root.parents)), 'publication_symlink')
    root.mkdir(parents=True, exist_ok=True)
    stage = root / ('.publish-' + uuid4().hex)
    stage.mkdir(mode=0o755)
    for name, raw in files.items():
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        target.chmod(0o644)
    states = []
    # Validate every new directory and every collision before exposing a run.
    for expected in manifest['records']:
        run_id = expected['id']
        row, copied = verified_files(stage / run_id)
        need(all(row[key] == expected[key] for key in expected), 'publication_record_changed')
        need(set(copied) == {str(PurePosixPath(name).relative_to(run_id))
                            for name in files if PurePosixPath(name).parts[0] == run_id},
             'publication_unexpected_file')
        destination = root / run_id
        if destination.exists():
            old, previous = verified_files(destination)
            need(old['identity'] == row['identity'] and previous == copied,
                 'publication_existing_run_immutable')
        states.append((run_id, destination.exists()))
    for run_id, existing in states:
        if not existing:
            (stage / run_id).rename(root / run_id)
    if not any(stage.iterdir()):
        stage.rmdir()
    # An identical retry leaves only the small hidden staging directory; it is
    # intentionally retained instead of recursively deleting an evidence path.
    return dict(schema=SCHEMA, status='published', records=manifest['records'],
                existing_runs_preserved=True, html_js_routes_added=0, model_calls=0, database_writes=0,
                aesthetic_approval=False, production_adoption=False)


def install(args):
    root = Path(args.destination_root).absolute()
    need(root == Path('/evaluations/capability-experiments'), 'publication_exact_destination_required')
    raw = sys.stdin.buffer.read(MAX_ARCHIVE + 1)
    manifest, files = unpack_publication(raw, args.manifest_sha256)
    summary = install_validated(root, manifest, files)
    print(json.dumps(dict(summary, manifest_sha256=args.manifest_sha256), sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-tree', type=Path,
                        help='reviewed trusted source tree for bundle import; omit in installed Lab image')
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('bundle')
    prepare.add_argument('--run', action='append', required=True, type=Path)
    prepare.add_argument('--output', required=True, type=Path)
    publish = sub.add_parser('install')
    publish.add_argument('--destination-root', default='/evaluations/capability-experiments')
    publish.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    if args.source_tree:
        need(args.command == 'bundle' and args.source_tree.is_dir(), 'publication_trusted_source_invalid')
        sys.path.insert(0, str(args.source_tree.resolve(strict=True)))
    (bundle if args.command == 'bundle' else install)(args)


if __name__ == '__main__':
    main()
