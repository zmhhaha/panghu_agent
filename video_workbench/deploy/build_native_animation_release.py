"""Build the complete native-animation release from one clean Git revision.

Only builds and pushes latest images. Does not deploy, open service credentials,
submit Agent work, or reuse application layers from a deployed donor image.
"""
import argparse
import json
from pathlib import Path

from . import build_creation_release as base


def build(args):
    args.roles = ('worker', 'executor') if args.skip_control else ('control', 'worker', 'executor')
    base.build(args)
    source = base.source_measurement(base.ROOT, args.revision)
    images = {}
    roles = (
        ('workbench', 'video_workbench/Dockerfile', '.', 'video-workbench'),
        ('renderer', 'video_workbench/motion_renderer/Dockerfile',
         'video_workbench/motion_renderer', 'video-motion-renderer'),
    )
    for role, dockerfile, directory, name in roles:
        tag = args.registry + '/' + name + ':latest'
        command = ['docker', 'build', '--pull=false', '-f', dockerfile,
                   '--build-arg', 'SOURCE_REVISION=' + args.revision, '-t', tag]
        command.append(directory)
        print(json.dumps(dict(phase='build', role=role, revision=args.revision)), flush=True)
        with (args.output / (role + '-build.log')).open('xb') as log:
            base.command(command, output=log)
            built = base.inspected(tag)
            base.command(['docker', 'push', tag], output=log)
        pushed = base.inspected(tag)
        base.require(built['Id'] == pushed['Id'] and pushed['Architecture'] == 'arm64',
                     'native_release_pushed_image_mismatch')
        refs = [value for value in pushed.get('RepoDigests', [])
                if value.startswith(tag.removesuffix(':latest') + '@sha256:')]
        base.require(len(refs) == 1, 'native_release_single_registry_digest_required')
        image = tag + '@' + refs[0].split('@', 1)[1]
        base.save(args.output / (role + '-build.json'), base.receipt(role, image, source, dockerfile))
        images[role] = image
        print(json.dumps(dict(phase='built', role=role, image=image)), flush=True)
    base.require(base.source_measurement(base.ROOT, args.revision) == source,
                 'native_release_source_changed')
    result = json.loads((args.output / 'build-completed.json').read_bytes())
    result['images'].update(images)
    result.update(schema='video.native-animation-release-build.v1', deployments=0, model_requests=0)
    base.save(args.output / 'native-build-completed.json', result)
    print(json.dumps(result, sort_keys=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--skip-control', action='store_true',
                        help='Reuse control only after confirming it accepts the new inputs and bundles.')
    parser.add_argument('--registry', default='arm-cluster-master:5000')
    build(parser.parse_args())


if __name__ == '__main__':
    main()
