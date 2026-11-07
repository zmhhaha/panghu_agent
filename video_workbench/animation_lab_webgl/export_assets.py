"""Verify and copy installed pinned Three.js bytes into an existing lab asset dir.

Run npm ci --ignore-scripts --no-audit --no-fund in this script's directory first.
This exporter has no network operations and never replaces differing files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from video_workbench.animation_lab_three import THREE_ASSET_SHA256, THREE_VERSION


INTEGRITY = 'sha512-FQK+LEpYc0fBD+J8g6oSEyyNzjp+Q7Ks1C568WWaoMRLW+TkNNWmenWeGgJjV105Gd+p/2ql1ZcjYvNiPZBhuQ=='


def export_assets(assets_dir, *, installation=None):
    here = Path(__file__).resolve().parent
    package = Path(installation) if installation is not None else here / 'node_modules' / 'three'
    destination = Path(assets_dir)
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError('three_assets_directory_invalid')
    lock_raw = (here / 'package-lock.json').read_bytes()
    locked = json.loads(lock_raw)['packages']['node_modules/three']
    installed = json.loads((package / 'package.json').read_bytes())
    if (locked['version'] != THREE_VERSION or installed['version'] != THREE_VERSION
            or locked['integrity'] != INTEGRITY
            or locked['resolved'] != 'https://registry.npmjs.org/three/-/three-'+THREE_VERSION+'.tgz'):
        raise ValueError('three_package_identity_mismatch')
    pending = {}
    for name, digest in THREE_ASSET_SHA256.items():
        source = package / 'build' / name
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('three_asset_source_invalid')
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('three_asset_digest_mismatch')
        pending[name] = raw
    if not pending:
        raise ValueError('three_asset_manifest_missing')
    license_raw = (package / 'LICENSE').read_bytes()
    if len(license_raw) > 16384 or b'MIT License' not in license_raw:
        raise ValueError('three_license_missing')
    pending['three.LICENSE.txt'] = license_raw
    receipt = dict(schema='video.animation-lab-three-assets.v1', version=THREE_VERSION,
        npm_integrity=INTEGRITY, package_lock_sha256=hashlib.sha256(lock_raw).hexdigest(),
        assets={name: dict(sha256=digest, bytes=len(pending[name])) for name, digest in THREE_ASSET_SHA256.items()},
        source='installed_integrity_locked_official_npm_package', network_calls=0)
    pending['three-assets.json'] = (json.dumps(receipt, sort_keys=True, indent=2)+'\n').encode('utf-8')
    for name, raw in pending.items():
        target = destination / name
        if target.is_symlink() or (target.exists() and (not target.is_file() or target.read_bytes() != raw)):
            raise ValueError('three_asset_destination_conflict')
    for name, raw in pending.items():
        target = destination / name
        if not target.exists():
            with target.open('xb') as output:
                output.write(raw)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export_assets(args.assets_dir), sort_keys=True))


if __name__ == '__main__':
    main()
