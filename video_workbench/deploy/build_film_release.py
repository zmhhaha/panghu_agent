"""Build film author/control/workbench/renderer from one measured Git revision.

Uses ordinary Dockerfiles, readable Git staging and latest tags with digest
receipts. Creates no tasks, credentials, routes or deployment changes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

if __package__:
    from . import build_joint_plan_release as joint
else:
    import build_joint_plan_release as joint


def build(args):
    base = joint.base
    result = joint.build(args)
    source = base.source_measurement(base.ROOT, args.revision)
    output = Path(args.output)
    renderer_source = output / 'workbench-source' / 'video_workbench' / 'motion_renderer'
    rows = [row for row in source['source_files'] if row['path'].startswith('video_workbench/motion_renderer/')]
    context_sha = base.sha(base.canonical(rows))
    dockerfile = 'video_workbench/motion_renderer/Dockerfile'
    tag = args.registry + '/video-motion-renderer:latest'
    with (output / 'renderer-build.log').open('xb') as log:
        base.command(['docker', 'build', '--pull=false', '-f', 'Dockerfile',
            '--build-arg', 'SOURCE_REVISION=' + args.revision,
            '--label', 'io.panghu.source.context-sha256=' + context_sha, '-t', tag, '.'],
            cwd=renderer_source, output=log)
        built = base.inspected(tag)
        base.command(['docker', 'push', tag], output=log)
    pushed = base.inspected(tag)
    base.require(built['Id'] == pushed['Id'] and pushed.get('Architecture') == 'arm64',
                 'film_renderer_latest_identity_changed')
    prefix = tag.removesuffix(':latest') + '@sha256:'
    refs = [ref for ref in pushed.get('RepoDigests', []) if ref.startswith(prefix)]
    base.require(len(refs) == 1, 'film_renderer_pushed_digest_unknown')
    image = tag + '@' + refs[0].split('@', 1)[1]
    base.save(output / 'renderer-build.json',
        base.receipt('renderer', image, source, dockerfile, context_sha256=context_sha))
    runtime = json.loads(base.command(['docker', 'run', '--rm', '--network', 'none',
        '--read-only', '--entrypoint', 'cat', image, '/opt/renderer/runtime.json']))
    base.save(output / 'renderer-runtime.json', runtime)
    base.require(base.source_measurement(base.ROOT, args.revision) == source, 'source_changed_during_build')
    result = dict(result, schema='video.film-release-build.v1',
        images=dict(result['images'], renderer=image))
    base.save(output / 'film-build-completed.json', result)
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
