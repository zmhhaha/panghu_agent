"""Record exact installed site code and compiled HTTP assets during Docker build."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import sys

ASSET_SUFFIXES = {'.js', '.css', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.ico', '.woff', '.woff2'}


def describe(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024**2:
        raise ValueError('invalid installed site file')
    data = path.read_bytes()
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


def generate(root: Path) -> dict:
    sources = json.loads((root / 'build-info/source-inputs.json').read_text(encoding='utf-8'))
    manifest = {}
    for name, expected in sources.items():
        if name.startswith('code/'):
            actual = describe(root / name.removeprefix('code/'))
            if actual != expected:
                raise ValueError('installed site source differs from build input')
            manifest[name] = actual
    index = root / 'web/dist/index.html'
    if not index.read_bytes().strip():
        raise ValueError('empty compiled frontend')
    manifest['web/dist/index.html'] = describe(index)
    for path in sorted((root / 'web/dist').rglob('*')):
        if path.is_symlink():
            raise ValueError('linked frontend asset')
        if path.is_dir():
            continue
        relative = path.relative_to(root / 'web/dist')
        if relative.as_posix() == 'index.html':
            continue
        if any(part.startswith('.') for part in relative.parts) or path.suffix not in ASSET_SUFFIXES:
            raise ValueError('unexpected compiled frontend asset')
        manifest['web/dist/' + relative.as_posix()] = describe(path)
    if len(manifest) > 1024 or sum(item['bytes'] for item in manifest.values()) > 64 * 1024**2:
        raise ValueError('installed site exceeds build limits')
    (root / 'build-info/site-inputs.json').write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    generate(Path(sys.argv[1]))
