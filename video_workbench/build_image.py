"""Build the workbench from an exact allowlist and verify any pushed digest.

Run with ``python -m video_workbench.build_image``. ``--context FILE`` only
creates an inspectable TAR; ``--no-push`` builds locally without a release
receipt. No deployment, model call, service credential or Agent runtime is used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import uuid

from agent_service.build_image import (
    BuildError, PYTHON_BASE, SHA256, VIDEO_FILES, VIDEO_MANIFEST_SHA256,
    checked_directory, immutable_image, local_image, native_platform,
    native_push, read_regular, reset_receipt, tagged_image, unique_pairs,
)


ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = 'video_workbench/Dockerfile'
RESOURCE = 'agent_service/resources/video-workbench-v1/'
# This is a file allowlist, not an extension/directory glob. In particular,
# configuration, credentials, databases, runtime state and deploy code never
# enter the context, even if placed beside application code or web assets.
SOURCE_FILES = frozenset({
    DOCKERFILE,
    'video_workbench/__init__.py', 'video_workbench/__main__.py',
    'video_workbench/agent.py', 'video_workbench/app.py', 'video_workbench/config.py',
    'video_workbench/service.py', 'video_workbench/store.py',
    'video_workbench/speech.py', 'video_workbench/audio_store.py',
    'video_workbench/audio_workflow.py', 'video_workbench/media.py',
    'video_workbench/production.py', 'video_workbench/static_render_worker.py',
    'video_workbench/requirements.txt',
    'video_workbench/web/index.html', 'video_workbench/web/app.mjs',
    'video_workbench/web/model.mjs', 'video_workbench/web/styles.css',
    'agent_service/__init__.py', 'agent_service/client.py',
    'agent_service/contracts.py', 'agent_service/artifacts.py',
    'agent_service/engines.py', 'agent_service/diagnostics.py',
    'agent_service/profiles/__init__.py', 'agent_service/profiles/base.py',
    'agent_service/profiles/video.py', RESOURCE + 'manifest.json',
    *(RESOURCE + name for name in VIDEO_FILES),
})
MAX_CONTEXT_BYTES = 8 * 1024 * 1024
LABEL_PREFIX = 'video-workbench.panghuer.top/'


def _verify_resource(files):
    raw = files[RESOURCE + 'manifest.json']
    try:
        manifest = json.loads(raw, object_pairs_hook=unique_pairs)
        rows = manifest['local_files']
        if (hashlib.sha256(raw).hexdigest() != VIDEO_MANIFEST_SHA256
                or manifest.get('bundle_revision') != 'video-workbench-v1'
                or type(rows) is not list or len(rows) != len(VIDEO_FILES)
                or any(type(row) is not dict or set(row) != {'path', 'sha256'} for row in rows)
                or {row['path'] for row in rows} != VIDEO_FILES
                or any(hashlib.sha256(files[RESOURCE + row['path']]).hexdigest() != row['sha256']
                       for row in rows)):
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise BuildError('invalid_video_resource_manifest') from None


def _destination(path):
    path = Path(path).absolute()
    checked_directory(path.parent)
    if path.exists() or path.is_symlink():
        read_regular(path, MAX_CONTEXT_BYTES + 1024 * 1024)
    return path


def context_archive(root, destination):
    """Write a deterministic ordinary-file TAR and return its exact identities."""
    root = checked_directory(root)
    destination = _destination(destination)
    if destination in {root / name for name in SOURCE_FILES}:
        raise BuildError('build_destination_is_source')
    files = {name: read_regular(root / name) for name in sorted(SOURCE_FILES)}
    if sum(map(len, files.values())) > MAX_CONTEXT_BYTES:
        raise BuildError('build_context_too_large')
    _verify_resource(files)
    manifest = [dict(path=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                for name, raw in sorted(files.items())]
    identity = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode('utf-8')
    pending = destination.with_name('.video-context-' + uuid.uuid4().hex + '.tmp')
    try:
        with pending.open('xb') as output:
            with tarfile.open(fileobj=output, mode='w', format=tarfile.USTAR_FORMAT) as archive:
                for name, content in sorted(files.items()):
                    entry = tarfile.TarInfo(name)
                    entry.size, entry.mode, entry.mtime = len(content), 0o644, 0
                    archive.addfile(entry, io.BytesIO(content))
            output.flush()
            os.fsync(output.fileno())
        os.replace(pending, destination)
    finally:
        pending.unlink(missing_ok=True)
    return dict(source_sha256=hashlib.sha256(identity).hexdigest(),
                context_sha256=hashlib.sha256(read_regular(destination, MAX_CONTEXT_BYTES + 1024 * 1024)).hexdigest(),
                files=len(files))


def _write_receipt(receipt, image_digest):
    checked_directory(receipt.parent)
    pending = receipt.with_name('.workbench-image-' + uuid.uuid4().hex + '.tmp')
    try:
        with pending.open('x', encoding='utf-8', newline='\n') as output:
            output.write(image_digest + '\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(pending, receipt)
    finally:
        pending.unlink(missing_ok=True)


def build(*, root=ROOT, push=True, native=False, environ=None, runner=None):
    """Build once; a receipt exists only after this invocation verifies its push."""
    receipt = reset_receipt(Path(root) / 'video_workbench', 'workbench')
    if type(push) is not bool or type(native) is not bool:
        raise BuildError('invalid_build_mode')
    environ = os.environ if environ is None else environ
    runner = subprocess.run if runner is None else runner
    registry = environ.get('REGISTRY', 'arm-cluster-master:5000')
    tag = environ.get('IMAGE_TAG', 'latest')
    image = environ.get('IMAGE', registry + '/video-workbench:' + tag)
    name = tagged_image(image)
    if tag != 'latest' or image.rsplit(':', 1)[1] != 'latest':
        raise BuildError('latest_image_tag_required')
    platform = environ.get('PLATFORM', 'linux/arm64')
    if platform not in {'linux/amd64', 'linux/arm64'}:
        raise BuildError('invalid_build_platform')
    revision = environ.get('SOURCE_REVISION')
    if revision is not None and (type(revision) is not str or not re.fullmatch('[0-9a-f]{7,64}', revision)):
        raise BuildError('invalid_source_revision')
    base = immutable_image(environ.get('PYTHON_BASE_IMAGE', PYTHON_BASE))
    with tempfile.TemporaryDirectory(prefix='video-workbench-build-') as temporary:
        archive, metadata = Path(temporary) / 'context.tar', Path(temporary) / 'metadata.json'
        identities = context_archive(root, archive)
        arguments = ['--platform', platform, '--file', DOCKERFILE, '--tag', image,
                     '--build-arg', 'PYTHON_BASE_IMAGE=' + base,
                     '--label', 'org.opencontainers.image.created=' + datetime.now(timezone.utc).isoformat(),
                     '--label', LABEL_PREFIX + 'source-sha256=' + identities['source_sha256'],
                     '--label', LABEL_PREFIX + 'context-sha256=' + identities['context_sha256']]
        if revision is not None:
            arguments += ['--label', 'org.opencontainers.image.revision=' + revision]
        use_buildx = False
        if not native:
            probe = runner(['docker', 'buildx', 'version'], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, check=False)
            use_buildx = probe.returncode == 0
        if use_buildx:
            arguments = ['docker', 'buildx', 'build', *arguments, '--push' if push else '--load',
                         '--metadata-file', str(metadata), '-']
        else:
            native_platform(runner, platform)
            arguments = ['docker', 'build', *arguments, '-']
        with archive.open('rb') as stream:
            options = dict(stdin=stream, check=True)
            if not use_buildx:
                options['env'] = {**os.environ, 'DOCKER_BUILDKIT': '0'}
            runner(arguments, **options)
        if not push:
            local_image(runner, image, platform)
            return None
        if use_buildx:
            try:
                report = json.loads(read_regular(metadata, 128 * 1024), object_pairs_hook=unique_pairs)
                checksum = report['containerimage.digest']
                if (type(checksum) is not str or not SHA256.fullmatch(checksum)
                        or report.get('image.name', image) != image):
                    raise ValueError()
            except (ValueError, TypeError, KeyError, UnicodeError):
                raise BuildError('invalid_build_metadata') from None
            result = runner(['docker', 'buildx', 'imagetools', 'inspect', '--raw', image],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            raw = result.stdout
            if (type(raw) is not bytes or not 0 < len(raw) <= 4 * 1024 * 1024
                    or 'sha256:' + hashlib.sha256(raw).hexdigest() != checksum):
                raise BuildError('pushed_manifest_mismatch')
        else:
            checksum = native_push(runner, image, name, platform)
        image_digest = image + '@' + checksum
        _write_receipt(receipt, image_digest)
        return image_digest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        'Environment: REGISTRY, IMAGE (explicit registry, latest tag only), IMAGE_TAG (latest only), '
        'PLATFORM (default linux/arm64), SOURCE_REVISION, PYTHON_BASE_IMAGE (immutable digest). '
        'Only a verified push writes video_workbench/rendered/workbench-image.txt.'))
    parser.add_argument('--context', metavar='FILE', help='仅生成可审查的构建 TAR，不调用 Docker')
    parser.add_argument('--no-push', action='store_true', help='只在本地构建，不生成发布回执')
    parser.add_argument('--native', action='store_true', help='使用与当前 Docker 主机一致的平台构建')
    args = parser.parse_args(argv)
    if args.context and (args.no_push or args.native):
        parser.error('--context 不能与构建选项一起使用')
    try:
        if args.context:
            identities = context_archive(ROOT, args.context)
            print(json.dumps(identities, sort_keys=True))
        else:
            result = build(push=not args.no_push, native=args.native)
            print('Published ' + result if result else 'Built locally; no registry digest receipt was retained.')
    except (BuildError, OSError, subprocess.SubprocessError):
        print('Workbench build failed; no new registry digest receipt was retained.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
