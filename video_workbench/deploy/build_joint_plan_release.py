"""Build and push the three joint-planning images from one clean Git revision.

Uses the ordinary Agent builder and the workbench Dockerfile, with latest tags
and immutable digest receipts. Does not deploy, open credentials, submit Agent
work, or build the animation executor, renderer or laboratory.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

if __package__:
    from . import build_creation_release as base
else:  # Also support python video_workbench/deploy/build_joint_plan_release.py.
    import build_creation_release as base


DOCKERFILE = 'video_workbench/Dockerfile'
CONTEXT_ROOTS = {'agent_service', 'video_workbench'}


def _workbench_context(output, source):
    """Copy only measured Git files, never ignored local or runtime material."""
    context = output / 'workbench-source'
    context.mkdir(mode=0o700)
    rows = [row for row in source['source_files']
            if Path(row['path']).parts[0] in CONTEXT_ROOTS]
    base.require(any(row['path'] == DOCKERFILE for row in rows), 'workbench_dockerfile_required')
    for row in rows:
        relative = Path(row['path'])
        base.require(not relative.is_absolute() and '..' not in relative.parts,
                     'ordinary_bounded_git_source_required')
        path = base.ROOT / relative
        base.require(path.is_file() and not path.is_symlink(), 'ordinary_bounded_git_source_required')
        raw = path.read_bytes()
        base.require(len(raw) == row['bytes'] and base.sha(raw) == row['sha256'],
                     'source_changed_during_build')
        destination = context / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(raw)
    return context, base.sha(base.canonical(rows))


def build(args):
    base.require(re.fullmatch('[0-9a-f]{40}', args.revision or '') is not None,
                 'full_git_revision_required')
    base.require(re.fullmatch(r'[a-z0-9][a-z0-9.:-]*(?:/[a-z0-9._-]+)*', args.registry or '') is not None
                 and '..' not in args.registry,
                 'explicit_registry_required')
    output = Path(args.output)
    base.require(output.is_absolute() and output.parent.is_dir() and not output.exists(),
                 'new_release_output_required')
    base.require(all(not path.is_symlink() and not (hasattr(path, 'is_junction') and path.is_junction())
                     for path in (output, *output.parents)), 'ordinary_output_path_required')
    output = output.resolve()
    base.require(all(not output.is_relative_to(base.ROOT / directory) for directory in base.SOURCE_ROOTS),
                 'release_output_outside_application_required')
    source = base.source_measurement(base.ROOT, args.revision)
    sys.path.insert(0, str(base.ROOT))
    from agent_service.build_image import build as build_agent, immutable_image
    # Validate both ordinary-worker dependencies before creating build output.
    immutable_image(os.environ.get('PI_CAGE_IMAGE'))
    immutable_image(os.environ.get('PPT_DONOR_IMAGE'))
    output.mkdir(mode=0o700)
    base.save(output / 'source-manifest.json', source)
    context, workbench_context_sha = _workbench_context(output, source)
    results = {}
    for role, target, dockerfile in (
        ('control', 'control', 'agent_service/Dockerfile.control'),
        ('worker', 'pi', 'agent_service/Dockerfile.agent-pi'),
    ):
        environ = {**os.environ, 'SOURCE_REVISION': args.revision, 'PLATFORM': 'linux/arm64',
                   'IMAGE_TAG': 'latest', 'IMAGE': args.registry + '/agent-service-' + target + ':latest'}
        with (output / (role + '-build.log')).open('xb') as log:
            def runner(argv, **options):
                # The ordinary builder still needs stdout for its inspections.
                options.setdefault('stdout', log)
                options.setdefault('stderr', log)
                return subprocess.run(argv, **options)
            image = build_agent(target, service=base.ROOT / 'agent_service', environ=environ, runner=runner)
        base.command(['docker', 'pull', image])
        info = base.inspected(image)
        context_sha = (info.get('Config', {}).get('Labels') or {}).get('io.panghu.source.context-sha256')
        base.require(re.fullmatch('[0-9a-f]{64}', context_sha or '') is not None,
                     'image_context_label_required')
        base.save(output / (role + '-build.json'),
                  base.receipt(role, image, source, dockerfile, context_sha256=context_sha))
        results[role] = image
        print(json.dumps(dict(status='full_source_image_built', role=role, image=image)), flush=True)

    tag = args.registry + '/video-workbench:latest'
    with (output / 'workbench-build.log').open('xb') as log:
        base.command(['docker', 'build', '--pull=false', '-f', DOCKERFILE,
            '--build-arg', 'SOURCE_REVISION=' + args.revision,
            '--label', 'io.panghu.source.context-sha256=' + workbench_context_sha,
            '-t', tag, '.'], cwd=context, output=log)
        built = base.inspected(tag)
        base.command(['docker', 'push', tag], output=log)
    pushed = base.inspected(tag)
    base.require(built['Id'] == pushed['Id'] and pushed.get('Architecture') == 'arm64',
                 'workbench_latest_identity_changed')
    prefix = tag.removesuffix(':latest') + '@sha256:'
    refs = [value for value in pushed.get('RepoDigests', []) if value.startswith(prefix)]
    base.require(len(refs) == 1, 'workbench_pushed_digest_unknown')
    image = tag + '@' + refs[0].split('@', 1)[1]
    base.save(output / 'workbench-build.json',
              base.receipt('workbench', image, source, DOCKERFILE, context_sha256=workbench_context_sha))
    results['workbench'] = image
    base.require(base.source_measurement(base.ROOT, args.revision) == source, 'source_changed_during_build')
    result = dict(schema='video.joint-plan-release-build.v1', status='full_source_release_built',
        revision=args.revision, images=results,
        source_files_manifest_sha256=source['source_files_manifest_sha256'], deployments=0, model_requests=0)
    base.save(output / 'build-completed.json', result)
    print(json.dumps(result, sort_keys=True), flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--registry', default='arm-cluster-master:5000')
    return build(parser.parse_args(argv))


if __name__ == '__main__':
    main()
