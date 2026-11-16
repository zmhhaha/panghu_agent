"""Download and revalidate the fixed Qwen alignment model asset closure.

Only JSON/text and safetensors are handled. No model code or pickle is loaded.
The complete manifest hash identifies the closure, not a single weight revision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

from agent_service.contracts import ServiceError, canonical_bytes


MODEL = 'Qwen/Qwen3-ForcedAligner-0.6B'
METADATA_URL = f'https://modelscope.cn/api/v1/models/{MODEL}/repo/files?Revision=master&Recursive=true'
EXPECTED_TOTAL = 1_840_073_172
EXPECTED_COUNT = 11
WEIGHT_BYTES = 1_835_544_544
WEIGHT_SHA256 = '47831d0e82f96b20e9034dba01a075ee06436654719f6a68289e49f1b65ce0e7'
WEIGHT_REVISION = '6f4d7c9606feb7adf282c9e4b139f28e8695d867'
MANIFEST_NAME = 'alignment-assets.json'
MAX_TOTAL_BYTES = 2 * 1024 ** 3
MAX_METADATA_BYTES = 256 * 1024
MAX_SMALL_FILE_BYTES = 8 * 1024 ** 2
CHUNK_BYTES = 1024 ** 2
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_REVISION = re.compile(r'[0-9a-f]{40}\Z')
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*\Z')


def _require(condition, code='invalid_alignment_assets'):
    if not condition:
        raise ServiceError(code, 422)


def _name(value):
    _require(type(value) is str and len(value) <= 100 and
             (value == '.gitattributes' or _NAME.fullmatch(value)))
    _require(value not in {MANIFEST_NAME, '.', '..'} and not value.endswith('.part'))
    _require(value == 'model.safetensors' or value == '.gitattributes' or
             value.endswith(('.json', '.txt', '.md')))
    return value


def _manifest(value):
    _require(type(value) is dict and set(value) == {'schema', 'model', 'source', 'files'})
    _require(value['schema'] == 'video.alignment-assets.v1' and value['model'] == MODEL
             and value['source'] == METADATA_URL)
    rows = value['files']
    _require(type(rows) is list and len(rows) == EXPECTED_COUNT)
    seen, normalized = set(), []
    for row in rows:
        _require(type(row) is dict and set(row) == {'name', 'size', 'sha256', 'revision'})
        name = _name(row['name'])
        _require(name not in seen)
        seen.add(name)
        _require(type(row['size']) is int and 0 <= row['size'] <= MAX_TOTAL_BYTES)
        _require(type(row['sha256']) is str and _SHA.fullmatch(row['sha256']))
        _require(type(row['revision']) is str and _REVISION.fullmatch(row['revision']))
        if name == 'model.safetensors':
            _require((row['size'], row['sha256'], row['revision']) ==
                     (WEIGHT_BYTES, WEIGHT_SHA256, WEIGHT_REVISION), 'alignment_weight_identity_changed')
        else:
            _require(row['size'] <= MAX_SMALL_FILE_BYTES)
        normalized.append(dict(row))
    _require('model.safetensors' in seen and 'config.json' in seen)
    total = sum(row['size'] for row in normalized)
    _require(total == EXPECTED_TOTAL and total <= MAX_TOTAL_BYTES,
             'alignment_asset_closure_changed')
    return dict(schema=value['schema'], model=MODEL, source=METADATA_URL,
                files=sorted(normalized, key=lambda r: r['name']))


def freeze_metadata(payload):
    """Freeze official ModelScope metadata into a strict complete-file manifest."""
    _require(type(payload) is dict and payload.get('Code') == 200 and
             payload.get('Success') is True)
    data = payload.get('Data')
    _require(type(data) is dict and type(data.get('Files')) is list)
    files = []
    for row in data['Files']:
        _require(type(row) is dict and row.get('Type') == 'blob')
        name = row.get('Path', row.get('Name'))
        _require(name == row.get('Name', name))
        files.append(dict(name=name, size=row.get('Size'), sha256=row.get('Sha256'),
                          revision=row.get('Revision')))
    return _manifest(dict(schema='video.alignment-assets.v1', model=MODEL,
                          source=METADATA_URL, files=files))


def _open(url, opener):
    return opener(url, timeout=30)


def fetch_metadata(*, opener=None):
    opener = urllib.request.urlopen if opener is None else opener
    with _open(METADATA_URL, opener) as response:
        raw = response.read(MAX_METADATA_BYTES + 1)
    _require(len(raw) <= MAX_METADATA_BYTES, 'alignment_metadata_too_large')
    return freeze_metadata(json.loads(raw))


def _file_identity(path):
    _require(not path.is_symlink() and path.is_file(), 'alignment_asset_missing')
    checksum, size = hashlib.sha256(), 0
    with path.open('rb') as stream:
        while chunk := stream.read(CHUNK_BYTES):
            size += len(chunk)
            _require(size <= MAX_TOTAL_BYTES, 'alignment_asset_too_large')
            checksum.update(chunk)
    return size, checksum.hexdigest()


def _check_file(path, row):
    _require(_file_identity(path) == (row['size'], row['sha256']),
             'alignment_asset_integrity_mismatch')


def verify_assets(model_dir):
    """Rehash all pinned assets and return the complete manifest identity."""
    root = Path(model_dir).resolve(strict=True)
    _require(root.is_dir(), 'alignment_asset_directory_required')
    manifest_path = root / MANIFEST_NAME
    _require(not manifest_path.is_symlink() and manifest_path.is_file(),
             'alignment_asset_manifest_missing')
    with manifest_path.open('rb') as stream:
        raw = stream.read(MAX_METADATA_BYTES + 1)
    _require(len(raw) <= MAX_METADATA_BYTES, 'alignment_metadata_too_large')
    manifest = _manifest(json.loads(raw))
    expected = {row['name'] for row in manifest['files']} | {MANIFEST_NAME}
    _require({p.name for p in root.iterdir()} == expected, 'alignment_asset_closure_mismatch')
    for row in manifest['files']:
        _check_file(root / row['name'], row)
    return dict(manifest_sha256=hashlib.sha256(canonical_bytes(manifest)).hexdigest(),
                files=manifest['files'], file_count=len(manifest['files']),
                total_bytes=sum(row['size'] for row in manifest['files']),
                model=MODEL)


def download_assets(model_dir, *, manifest=None, opener=None):
    """Fetch a fixed closure once, preserving incomplete .part files on failure.

    Existing complete files must pass hashes. An existing .part requires an
    operator decision; it is never silently overwritten or counted complete.
    """
    opener = urllib.request.urlopen if opener is None else opener
    root = Path(model_dir)
    _require(not root.is_symlink(), 'alignment_asset_directory_required')
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve(strict=True)
    manifest_path = root / MANIFEST_NAME
    if manifest_path.exists():
        _require(not manifest_path.is_symlink(), 'alignment_asset_manifest_invalid')
        with manifest_path.open('rb') as stream:
            raw = stream.read(MAX_METADATA_BYTES + 1)
        _require(len(raw) <= MAX_METADATA_BYTES, 'alignment_metadata_too_large')
        frozen = _manifest(json.loads(raw))
        if manifest is not None:
            _require(frozen == _manifest(manifest), 'alignment_asset_manifest_conflict')
        manifest = frozen
    else:
        manifest = fetch_metadata(opener=opener) if manifest is None else _manifest(manifest)
        with manifest_path.open('xb') as stream:
            stream.write(canonical_bytes(manifest) + b'\n')
    expected = {row['name'] for row in manifest['files']} | {MANIFEST_NAME}
    _require(all(p.name in expected or p.name in {r['name'] + '.part' for r in manifest['files']}
                 for p in root.iterdir()), 'alignment_asset_closure_mismatch')
    reused, downloaded = [], []
    for row in manifest['files']:
        target = root / row['name']
        if target.exists() or target.is_symlink():
            _check_file(target, row)
            reused.append(row['name'])
            continue
        partial = root / (row['name'] + '.part')
        _require(not partial.exists() and not partial.is_symlink(), 'alignment_asset_partial_exists')
        query = urllib.parse.urlencode(dict(Revision=row['revision'], FilePath=row['name']))
        url = f'https://modelscope.cn/api/v1/models/{MODEL}/repo?{query}'
        checksum, size = hashlib.sha256(), 0
        with partial.open('xb') as stream:
            with _open(url, opener) as response:
                while chunk := response.read(CHUNK_BYTES):
                    size += len(chunk)
                    _require(size <= row['size'], 'alignment_asset_too_large')
                    checksum.update(chunk)
                    stream.write(chunk)
        _require((size, checksum.hexdigest()) == (row['size'], row['sha256']),
                 'alignment_asset_integrity_mismatch')
        _require(not target.exists() and not target.is_symlink(), 'alignment_asset_target_exists')
        partial.rename(target)
        downloaded.append(row['name'])
    identity = verify_assets(root)
    return dict(**identity, downloaded=downloaded, reused=reused,
                model_directory=str(root), manifest_file=MANIFEST_NAME)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', required=True, type=Path)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        receipt = verify_assets(args.model_dir) if args.verify_only else download_assets(args.model_dir)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0
    except (ServiceError, OSError, ValueError) as error:
        parser.exit(2, 'alignment assets failed: ' + str(error) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
