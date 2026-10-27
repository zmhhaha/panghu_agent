"""Narrow source-only ARM64 overlay builds; registry publishing is explicit."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent
SERVICE = HERE.parents[1]
DIGEST = re.compile(r'[^\s@]+@sha256:[0-9a-f]{64}\Z')
IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}\Z')
TAG = re.compile(r'[^\s@]+:[A-Za-z0-9_][A-Za-z0-9_.-]*\Z')


def source_context(dockerfile: Path) -> tuple[bytes, str]:
    """No recursive home/venv/vendor copy, .env, tests or uploads enter a build."""
    files = [SERVICE / '__init__.py']
    for directory in ('control', 'server', 'worker'):
        files.extend(sorted((SERVICE / directory).glob('*.py')))
    # The dispatcher revalidates review-only revision authority against the
    # server-owned resolver. Include that production subpackage explicitly;
    # generated fixtures, demo assets and tests do not enter the overlay.
    for name in ('__init__.py', 'contracts.py', 'domain.py', 'router.py', 'store.py', 'resolver.py'):
        files.append(SERVICE / 'server/acceptance' / name)
    output = io.BytesIO()
    manifest = {}
    with tarfile.open(fileobj=output, mode='w') as archive:
        for path in files:
            if path.is_symlink() or not path.is_file():
                raise ValueError('runtime_source_must_be_regular')
            content = path.read_bytes()
            relative = path.relative_to(SERVICE).as_posix()
            manifest[relative] = hashlib.sha256(content).hexdigest()
            item = tarfile.TarInfo('code/' + relative)
            item.size = len(content)
            item.mode = 0o644
            archive.addfile(item, io.BytesIO(content))
        for name, raw in [('Dockerfile', dockerfile.read_bytes()),
                          ('requirements-control.lock', (HERE / 'requirements-control.lock').read_bytes()),
                          ('source-manifest.json', json.dumps(manifest, sort_keys=True).encode())]:
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            item.mode = 0o644
            archive.addfile(item, io.BytesIO(raw))
    return output.getvalue(), hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def build(*, site_base, worker_base, controller_tag, worker_tag, push=False,
          output=HERE / 'rendered', runner=subprocess.run):
    if not DIGEST.fullmatch(site_base):
        raise ValueError('site_base_requires_registry_digest')
    if not (DIGEST.fullmatch(worker_base) or IMAGE_ID.fullmatch(worker_base)):
        raise ValueError('worker_base_requires_explicit_image_id_or_registry_digest')
    for tag in (controller_tag, worker_tag):
        if not TAG.fullmatch(tag) or tag.rsplit(':', 1)[1] == 'latest':
            raise ValueError('explicit_non_latest_tags_required')
    if controller_tag == worker_tag:
        raise ValueError('distinct_image_tags_required')
    resolved_worker_base = worker_base
    if IMAGE_ID.fullmatch(worker_base):
        result = runner(['docker', 'image', 'inspect', worker_base, '--format', '{{json .}}'],
                        check=True, capture_output=True, text=True)
        cached = json.loads(result.stdout)
        if cached.get('Id') != worker_base or cached.get('Architecture') != 'arm64':
            raise ValueError('cached_worker_base_id_or_architecture_mismatch')
        # Some Dockerfile frontends parse sha256:<id> as a registry tag. Give
        # the verified cache entry a local build name; no pull or rebuild.
        resolved_worker_base = 'ppt-hermes-build-base:' + worker_base.split(':')[1][:20]
        runner(['docker', 'tag', worker_base, resolved_worker_base], check=True)
    receipt = {}
    for role, base, tag, argument in (
        ('worker', resolved_worker_base, worker_tag, 'PPT_HERMES_BASE_IMAGE'),
        ('controller', site_base, controller_tag, 'PPT_SITE_BASE_IMAGE'),
    ):
        context, source_hash = source_context(HERE / ('Dockerfile.' + role))
        runner(['docker', 'build', '--platform', 'linux/arm64', '--file', 'Dockerfile',
                '--build-arg', argument + '=' + base, '--tag', tag, '-'], input=context, check=True)
        receipt[role] = {'tag': tag, 'source_manifest_sha256': source_hash}
        if push:
            runner(['docker', 'push', tag], check=True)
            result = runner(['docker', 'image', 'inspect', tag, '--format', '{{json .RepoDigests}}'],
                            check=True, capture_output=True, text=True)
            repository = tag.rsplit(':', 1)[0]
            digests = [value for value in json.loads(result.stdout)
                       if value.startswith(repository + '@') and DIGEST.fullmatch(value)]
            if len(set(digests)) != 1:
                raise ValueError('pushed_repository_digest_unavailable')
            receipt[role]['image'] = digests[0]
    # Only write after both builds (and, when requested, both pushes) succeeded.
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'build.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    if push:
        for role in ('worker', 'controller'):
            (output / (role + '-image.txt')).write_text(receipt[role]['image'] + '\n', encoding='utf-8')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site-base', required=True)
    parser.add_argument('--worker-base', required=True)
    parser.add_argument('--controller-tag', required=True)
    parser.add_argument('--worker-tag', required=True)
    parser.add_argument('--push', action='store_true')
    parser.add_argument('--output', type=Path, default=HERE / 'rendered')
    args = parser.parse_args()
    build(**vars(args))
    print('Runtime images built' + (' and pushed; digest receipts saved.' if args.push else ' locally; not pushed.'))


if __name__ == '__main__':
    main()
