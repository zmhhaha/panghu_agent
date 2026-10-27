"""Create an allowlisted, credential-free Docker context; no install or build."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile

SERVICE = Path(__file__).resolve().parents[2]
SERVER_FILES = ('__init__.py', 'app.py', 'auth.py', 'middleware.py', 'migrate.py',
                'models.py', 'phase_limits.py', 'repository.py', 'review_revisions.py', 'site.py', 'storage.py',
                'acceptance/__init__.py', 'acceptance/contracts.py', 'acceptance/domain.py',
                'acceptance/router.py', 'acceptance/store.py', 'acceptance/resolver.py')
ASSETS = {'.js', '.css', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.ico', '.woff', '.woff2'}


def read_regular(path: Path, limit=16 * 1024**2) -> bytes:
    if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
           for p in (path, *path.parents)):
        raise ValueError('symlink/reparse source rejected')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise ValueError('invalid build input')
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as stream:
        data = stream.read(limit + 1)
    if len(data) != info.st_size or len(data) > limit or path.stat().st_mtime_ns != info.st_mtime_ns:
        raise ValueError('source changed during snapshot')
    return data


def package(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    inputs = {}
    inputs['code/ppt_service/__init__.py'] = read_regular(SERVICE / '__init__.py')
    for name in SERVER_FILES:
        inputs['code/ppt_service/server/' + name] = read_regular(SERVICE / 'server' / name)
    index = read_regular(SERVICE / 'web/dist/index.html', 1024**2)
    if not index.strip():
        raise ValueError('empty frontend build')
    inputs['web/dist/index.html'] = index
    for n, path in enumerate(sorted((SERVICE / 'web/dist/assets').rglob('*')), 1):
        if n > 2048:
            raise ValueError('too many frontend entries')
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('linked frontend asset')
        if path.is_file():
            rel = path.relative_to(SERVICE / 'web/dist')
            if any(part.startswith('.') for part in rel.parts) or path.suffix not in ASSETS:
                raise ValueError('unexpected frontend build input')
            inputs['web/dist/' + rel.as_posix()] = read_regular(path)
    if len(inputs) > 1024 or sum(map(len, inputs.values())) > 64 * 1024**2:
        raise ValueError('build exceeds site snapshot limits')
    web_sources = {}
    for path in sorted((SERVICE / 'web/src').rglob('*')):
        if path.is_file():
            web_sources[path.relative_to(SERVICE / 'web').as_posix()] = hashlib.sha256(read_regular(path)).hexdigest()
    for name in ('package.json', 'pnpm-lock.yaml', 'vite.config.ts', 'tsconfig.json'):
        path = SERVICE / 'web' / name
        if path.exists():
            web_sources[name] = hashlib.sha256(read_regular(path)).hexdigest()
    manifest = {name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                for name, data in sorted(inputs.items())}
    inputs['build-info/site-inputs.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    inputs['build-info/web-sources.json'] = (json.dumps(web_sources, indent=2) + '\n').encode()
    for name in ('Dockerfile.site', 'verify_image.py'):
        inputs[name] = read_regular(Path(__file__).parent / name)
    for name, data in inputs.items():
        target = output / 'context' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    tar_path = output / 'context.tar'
    with tarfile.open(tar_path, 'w') as archive:
        for name, data in sorted(inputs.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data))
    receipt = {'context_sha256': hashlib.sha256(tar_path.read_bytes()).hexdigest(),
               'files': len(inputs), 'runtime_inputs': manifest,
               'model_requests': 0, 'production_ready': False}
    (output / 'package.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    receipt = package(args.output)
    print(json.dumps({k: v for k, v in receipt.items() if k != 'runtime_inputs'}))
