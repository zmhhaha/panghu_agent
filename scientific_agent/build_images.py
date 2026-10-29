"""Build business API/UI images on the server and push to the private registry."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_service.build_image import (BuildError, tagged_image, native_platform,
    native_push, read_regular)

DOCKERFILES = {'api': 'Dockerfile.scientific-pi-api', 'ui': 'Dockerfile.scientific-workbench'}


def build_context(target, destination):
    files = {DOCKERFILES[target]}
    if target == 'ui':
        files |= {'scientific_agent/__init__.py', 'scientific_agent/workbench_server.py',
                  'scientific_agent/requirements-api.lock'}
        for source in (ROOT / 'scientific_agent/web').rglob('*'):
            relative = source.relative_to(ROOT)
            if (source.is_file() and not any(p in {'node_modules', 'dist', '.vite', 'coverage'}
                    or p.startswith('.') for p in relative.parts)
                    and source.suffix in {'.ts', '.tsx', '.css', '.json', '.html'}):
                files.add(relative.as_posix())
    else:
        # COPY sources only: no operator rendered files, model credentials,
        # material uploads, Python bytecode, or unrelated repository content.
        for directory in ('scientific_agent', 'app/api', 'pi_worker/research_tools', 'tools'):
            for source in (ROOT / directory).glob('*.py'):
                files.add(source.relative_to(ROOT).as_posix())
        files |= {'app/__init__.py', 'pi_worker/__init__.py', 'scientific_agent/requirements-api.lock'}
        files |= {p.relative_to(ROOT).as_posix() for p in (ROOT / 'scientific_skills').rglob('*') if p.is_file()}
    with tarfile.open(destination, 'w') as archive:
        for name in sorted(files):
            raw = read_regular(ROOT / name, 4 * 1024 * 1024)
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(raw), 0o644, 0
            archive.addfile(info, io.BytesIO(raw))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True, choices=DOCKERFILES)
    parser.add_argument('--no-push', action='store_true')
    args = parser.parse_args()
    rendered = ROOT / 'scientific_agent/rendered'
    rendered.mkdir(exist_ok=True)
    receipt = rendered / (args.target + '-image.txt')
    receipt.unlink(missing_ok=True)
    try:
        tag = os.getenv('IMAGE_TAG', datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        registry = os.getenv('REGISTRY', 'arm-cluster-master:5000')
        image = os.getenv('IMAGE', registry + '/scientific-' + ('api' if args.target == 'api' else 'workbench') + ':' + tag)
        name = tagged_image(image)
        platform = os.getenv('PLATFORM', 'linux/arm64')
        if platform not in {'linux/arm64', 'linux/amd64'}:
            raise BuildError('invalid_build_platform')
        native_platform(subprocess.run, platform)
        with tempfile.TemporaryDirectory(prefix='scientific-build-') as directory:
            archive = Path(directory) / 'context.tar'
            build_context(args.target, archive)
            with archive.open('rb') as source:
                subprocess.run(['docker', 'build', '--platform', platform, '--file', DOCKERFILES[args.target],
                    '--tag', image, '-'], stdin=source, check=True,
                    env={**os.environ, 'DOCKER_BUILDKIT': '0'})
        if not args.no_push:
            checksum = native_push(subprocess.run, image, name, platform)
            receipt.write_text(name + '@' + checksum + '\n', encoding='utf-8')
            print('Published ' + name + '@' + checksum)
        else:
            print('Built locally; no deployable image receipt written.')
    except (OSError, ValueError, subprocess.SubprocessError, BuildError):
        print('Scientific image build failed; no deployable image receipt written.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
