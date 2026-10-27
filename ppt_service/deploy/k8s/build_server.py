"""Build an allowlisted server context, verify the image, and record its pushed digest."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile

SERVICE = Path(__file__).resolve().parents[2]
SERVER_FILES = (
    '__init__.py', 'app.py', 'auth.py', 'middleware.py', 'migrate.py',
    'models.py', 'repository.py', 'review_revisions.py', 'site.py', 'storage.py',
    'acceptance/__init__.py', 'acceptance/contracts.py', 'acceptance/domain.py',
    'acceptance/router.py', 'acceptance/store.py', 'acceptance/resolver.py',
)
WEB_FILES = (
    'index.html', 'package.json', 'pnpm-lock.yaml', 'vite.config.ts', 'tsconfig.json',
    'src/main.tsx', 'src/App.tsx', 'src/SourceFiles.tsx', 'src/api.ts', 'src/domain.ts', 'src/parse.ts',
    'src/planning.ts', 'src/styles.css', 'src/useJob.ts', 'src/useParsePolling.ts',
    'src/acceptance/index.ts', 'src/acceptance/AcceptancePanel.tsx',
    'src/acceptance/acceptance.css', 'src/acceptance/client.ts',
    'src/acceptance/contracts.ts', 'src/acceptance/drafts.ts',
)
ENTRYPOINT = [
    '/usr/local/bin/python', '-m', 'uvicorn', 'ppt_service.server.site:create_site',
    '--factory', '--host', '127.0.0.1', '--port', '8000', '--no-proxy-headers',
]
DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')


def read_regular(path: Path, limit: int = 16 * 1024**2) -> bytes:
    if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
           for p in (path, *path.parents)):
        raise ValueError('linked build source rejected')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise ValueError('invalid build source')
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as stream:
        data = stream.read(limit + 1)
    if (len(data) != info.st_size or len(data) > limit
            or path.stat().st_mtime_ns != info.st_mtime_ns):
        raise ValueError('build source changed during snapshot')
    return data


def context_inputs(service: Path) -> dict[str, bytes]:
    inputs = {'code/ppt_service/__init__.py': read_regular(service / '__init__.py')}
    for name in SERVER_FILES:
        inputs['code/ppt_service/server/' + name] = read_regular(service / 'server' / name)
    for name in WEB_FILES:
        inputs['web/' + name] = read_regular(service / 'web' / name)
    inputs['requirements-api.lock'] = read_regular(service / 'requirements-api.lock')
    for name in ('Dockerfile.site.server', 'verify_image.py', 'write_site_manifest.py'):
        inputs[name] = read_regular(service / 'deploy/k8s' / name)
    if len(inputs) > 256 or sum(map(len, inputs.values())) > 32 * 1024**2:
        raise ValueError('build input exceeds snapshot limits')
    manifest = {name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                for name, data in sorted(inputs.items())}
    inputs['build-info/source-inputs.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    return inputs


def write_context(path: Path, inputs: dict[str, bytes]) -> str:
    with tarfile.open(path, 'w') as archive:
        for name, data in sorted(inputs.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def image_repository(image: str) -> str:
    # Require an explicit registry and tag; @sha256 is a publication output.
    if '@' in image or '/' not in image or ':' not in image.rsplit('/', 1)[-1]:
        raise ValueError('IMAGE must be registry/repository:tag')
    repository, tag = image.rsplit(':', 1)
    registry = repository.split('/', 1)[0]
    if (not re.fullmatch(r'[a-z0-9][a-z0-9._:/-]*', repository)
            or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', tag)
            or '//' in repository or '..' in repository or repository.endswith('/')
            or not ('.' in registry or ':' in registry or registry == 'localhost')):
        raise ValueError('invalid registry/repository:tag')
    return repository


def docker(*arguments: str, capture: bool = False, stdin=None):
    return subprocess.run(['docker', *arguments], check=True, stdin=stdin,
                          stdout=subprocess.PIPE if capture else None,
                          text=True if capture else None)


def inspect_image(image: str) -> dict:
    return json.loads(docker('image', 'inspect', '--format', '{{json .}}', image,
                             capture=True).stdout)


def verify_contract(image: dict, platform: str) -> str:
    image_id = image.get('Id', '')
    if not DIGEST.fullmatch(image_id):
        raise ValueError('Docker did not return an immutable local image ID')
    config = image.get('Config', {})
    if (config.get('User') != '10000:10000' or config.get('Entrypoint') != ENTRYPOINT
            or image.get('Os') != 'linux' or image.get('Architecture') != platform.split('/')[1]
            or 'PPT_EXECUTION_ENABLED=false' not in config.get('Env', [])
            or any(value.startswith('PPT_LOCAL_TEST_ONLY=') for value in config.get('Env', []))):
        raise ValueError('built image violates the site runtime contract')
    return image_id


def reset_receipt(service: Path) -> Path:
    rendered = service / 'rendered'
    if rendered.is_symlink() or (hasattr(rendered, 'is_junction') and rendered.is_junction()):
        raise ValueError('linked rendered directory rejected')
    rendered.mkdir(exist_ok=True)
    for name in ('image.txt', 'source.json'):
        path = rendered / name
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('linked build receipt rejected')
        path.unlink(missing_ok=True)
    return rendered


def build(image: str, platform: str, *, push: bool, service: Path = SERVICE) -> str | None:
    repository = image_repository(image)
    if platform not in ('linux/arm64', 'linux/amd64'):
        raise ValueError('PLATFORM must be linux/arm64 or linux/amd64')
    rendered = reset_receipt(service)
    inputs = context_inputs(service)
    with tempfile.TemporaryDirectory(prefix='ppt-service-site-build-') as temporary:
        archive = Path(temporary) / 'context.tar'
        context_hash = write_context(archive, inputs)
        arguments = ['build', '--platform', platform, '--file', 'Dockerfile.site.server', '--tag', image]
        for variable, default in (
            ('NODE_BUILD_IMAGE', 'node:22.20.0-bookworm-slim'),
            ('PYTHON_RUNTIME_IMAGE', 'python:3.13.5-slim-bookworm'),
            ('PIP_INDEX_URL', 'https://pypi.tuna.tsinghua.edu.cn/simple'),
            ('NPM_REGISTRY', 'https://registry.npmmirror.com'),
        ):
            arguments.extend(('--build-arg', variable + '=' + os.environ.get(variable, default)))
        print('Building ' + image, flush=True)
        with archive.open('rb') as stream:
            docker(*arguments, '-', stdin=stream)
        built = inspect_image(image)
        image_id = verify_contract(built, platform)
        docker('run', '--rm', '--pull=never', '--network', 'none', '--read-only',
               '--user', '10000:10000', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges', '--memory', '512m',
               '--cpus', '1', '--pids-limit', '128',
               '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m,uid=10000,gid=10000',
               '--entrypoint', '/usr/local/bin/python', image_id,
               '/opt/ppt-service/verify_image.py')
        if not push:
            print('Built and verified locally; no registry digest was written.', flush=True)
            return None
        if inspect_image(image).get('Id') != image_id:
            raise ValueError('local image tag changed after verification')
        docker('push', image)
        published = inspect_image(image)
        if published.get('Id') != image_id:
            raise ValueError('local image tag changed during publication')
        matches = {item for item in published.get('RepoDigests', [])
                   if item.startswith(repository + '@') and DIGEST.fullmatch(item[len(repository) + 1:])}
        if len(matches) != 1:
            raise ValueError('push did not produce one matching repository manifest digest')
        digest = matches.pop()
        provenance = {
            'image': digest, 'tag': image, 'local_image_id': image_id, 'platform': platform,
            'context_sha256': context_hash,
            'sources': json.loads(inputs['build-info/source-inputs.json']),
            'verification': 'verify_image.py; network=none; uid=10000; read_only=true',
            'model_requests': 0, 'production_ready': False,
        }
        (rendered / 'source.json').write_text(json.dumps(provenance, sort_keys=True, indent=2) + '\n', encoding='utf-8')
        # Only a successful verified push creates the deploy entrypoint receipt.
        (rendered / 'image.txt').write_text(digest + '\n', encoding='utf-8')
        print('Published ' + digest, flush=True)
        return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--platform', default='linux/arm64')
    parser.add_argument('--no-push', action='store_true')
    args = parser.parse_args()
    try:
        build(args.image, args.platform, push=not args.no_push)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, 'Site build failed: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
