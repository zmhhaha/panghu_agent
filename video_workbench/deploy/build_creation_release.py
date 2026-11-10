"""Build the creation release from one clean Git checkout on the server.

Uses the ordinary Agent Dockerfiles and the standalone executor Dockerfile.
Docker caches dependency layers; no application-image overlays are assembled.
This command builds/pushes only and never opens runtime credentials or deploys.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = ('agent_service', 'video_workbench', 'scientific_agent', 'pi_worker', 'tools', 'scientific_skills')
EXECUTOR_DOCKERFILE = 'video_workbench/Dockerfile.animation-creation-executor'
IMAGE = re.compile(r'[a-z0-9][a-z0-9._:/-]*:latest@sha256:[0-9a-f]{64}\Z')


def require(value, code):
    if not value:
        raise RuntimeError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def command(argv, *, cwd=ROOT, env=None, timeout=1800, output=None):
    result = subprocess.run(argv, cwd=cwd, env=env, stdout=output or subprocess.PIPE,
        stderr=output or subprocess.PIPE, timeout=timeout, check=False)
    require(result.returncode == 0, 'release_command_failed:' + argv[0])
    return result.stdout


def source_measurement(root, revision):
    """Shared read-only provenance used by build and deployment preparation."""
    root = Path(root).resolve()
    require(re.fullmatch('[0-9a-f]{40}', revision or '') is not None, 'full_git_revision_required')
    git = ['git', '--no-optional-locks', '-C', str(root)]
    require(command(git + ['rev-parse', 'HEAD']).decode().strip() == revision, 'source_revision_mismatch')
    require(not command(git + ['status', '--porcelain', '--untracked-files=all', '--', *SOURCE_ROOTS]).strip(),
        'clean_application_source_required')
    paths = command(git + ['ls-files', '-z', '--', *SOURCE_ROOTS]).decode().split('\0')
    rows = []
    for name in sorted(path for path in paths if path):
        path = root / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 4 * 1024 * 1024,
            'ordinary_bounded_git_source_required')
        raw = path.read_bytes()
        rows.append(dict(path=name, bytes=len(raw), sha256=sha(raw)))
    require(rows and len(rows) <= 10000, 'bounded_source_manifest_required')
    return dict(revision=revision, source_files_manifest_sha256=sha(canonical(rows)),
        source_files=rows, source_git_clean=True)


def save(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def inspected(image):
    rows = json.loads(command(['docker', 'image', 'inspect', image], timeout=60))
    require(len(rows) == 1, 'single_release_image_required')
    return rows[0]


def receipt(role, image, source, dockerfile, *, context_sha256=None):
    require(IMAGE.fullmatch(image) is not None, 'pushed_latest_digest_required')
    info = inspected(image)
    labels = info['Config'].get('Labels') or {}
    require(labels.get('org.opencontainers.image.revision') == source['revision'], 'image_revision_label_mismatch')
    if context_sha256 is not None:
        require(labels.get('io.panghu.source.context-sha256') == context_sha256, 'image_context_label_mismatch')
    return dict(schema='agent-full-source-image-build.v1', role=role, revision=source['revision'],
        image=image, pushed=True, source_files_manifest_sha256=source['source_files_manifest_sha256'],
        source_git_clean=True, dockerfile=dockerfile, dockerfile_sha256=sha((ROOT / dockerfile).read_bytes()),
        built_image_id=info['Id'], source_context_sha256=context_sha256,
        revision_label_verified=True, model_requests=0, deployments=0)


def build(args):
    source = source_measurement(ROOT, args.revision)
    output = args.output.resolve()
    require(output.is_absolute() and output.parent.is_dir() and not output.exists(), 'new_release_output_required')
    require(all(not p.is_symlink() for p in (output.parent, *output.parent.parents)), 'ordinary_output_path_required')
    output.mkdir(mode=0o700)
    save(output / 'source-manifest.json', source)
    sys.path.insert(0, str(ROOT))
    from agent_service.build_image import build as build_agent, immutable_image
    immutable_image(os.environ.get('PI_CAGE_IMAGE'))
    immutable_image(os.environ.get('PPT_DONOR_IMAGE'))
    results = {}
    for role, target in (('control', 'control'), ('worker', 'pi')):
        environ = {**os.environ, 'SOURCE_REVISION': args.revision,
                   'IMAGE_TAG': 'latest', 'IMAGE': args.registry + '/agent-service-' + target + ':latest'}
        with (output / (role + '-build.log')).open('xb') as log:
            def runner(argv, **options):
                # Preserve inspection stdout for the ordinary builder; only
                # inherited build/push output is redirected into the build log.
                if 'stdout' not in options:
                    options['stdout'] = log
                if 'stderr' not in options:
                    options['stderr'] = log
                return subprocess.run(argv, **options)
            image = build_agent(target, environ=environ, runner=runner)
        command(['docker', 'pull', image])
        info = inspected(image)
        context_sha = info['Config']['Labels']['io.panghu.source.context-sha256']
        value = receipt(role, image, source, 'agent_service/Dockerfile.' + ('control' if role == 'control' else 'agent-pi'),
                        context_sha256=context_sha)
        save(output / (role + '-build.json'), value)
        results[role] = image
        print(json.dumps(dict(status='full_source_image_built', role=role, image=image)), flush=True)
    target = args.registry + '/video-animation-creation:latest'
    with (output / 'executor-build.log').open('xb') as log:
        command(['docker', 'build', '--pull=false', '-f', EXECUTOR_DOCKERFILE,
            '--build-arg', 'SOURCE_REVISION=' + args.revision, '-t', target, '.'], output=log)
        built = inspected(target)
        command(['docker', 'push', target], output=log)
    pushed = inspected(target)
    require(built['Id'] == pushed['Id'], 'executor_latest_identity_changed')
    prefix = target.removesuffix(':latest') + '@sha256:'
    values = [value for value in pushed.get('RepoDigests', []) if value.startswith(prefix)]
    require(len(values) == 1, 'executor_pushed_digest_unknown')
    image = target + '@' + values[0].split('@', 1)[1]
    save(output / 'executor-build.json', receipt('executor', image, source, EXECUTOR_DOCKERFILE))
    results['executor'] = image
    require(source_measurement(ROOT, args.revision) == source, 'source_changed_during_build')
    result = dict(status='full_source_release_built', revision=args.revision, images=results,
        source_files_manifest_sha256=source['source_files_manifest_sha256'], deployments=0, model_requests=0)
    save(output / 'build-completed.json', result)
    print(json.dumps(result, sort_keys=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--registry', default='arm-cluster-master:5000')
    args = parser.parse_args(argv)
    require(re.fullmatch(r'[a-z0-9][a-z0-9.:-]*(?:/[a-z0-9._-]+)*', args.registry) is not None,
            'explicit_registry_required')
    build(args)


if __name__ == '__main__':
    main()
