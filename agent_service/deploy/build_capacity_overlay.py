"""Build a minimal capacity overlay on the exact inspected live control donor.

Run on the server from a clean Git checkout. No donor container is started,
no dependencies are installed, and no workload, database or Secret is opened.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import uuid


REPOSITORY = Path(__file__).resolve().parents[2]
SHA = re.compile(r'[0-9a-f]{64}\Z')
REVISION = re.compile(r'[0-9a-f]{40}\Z')
IMAGE = re.compile(r'[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}\Z')
TARGET = re.compile(r'[a-z0-9][a-z0-9._:/-]*:latest\Z')
PATCHED = ('config.py', 'registry.py')
HISTORY_FIELDS = ('capability_bundles', 'tool_environments', 'execution_bindings', 'kubernetes_workers')
SAFE_ENV_KEYS = frozenset({'PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'GPG_KEY', 'PYTHON_VERSION',
    'PYTHON_SHA256', 'PYTHON_PIP_VERSION', 'PYTHON_SETUPTOOLS_VERSION', 'PYTHON_GET_PIP_URL',
    'PYTHON_GET_PIP_SHA256', 'PYTHONDONTWRITEBYTECODE', 'PYTHONUNBUFFERED', 'PYTHONPATH',
    'PIP_NO_CACHE_DIR', 'HOME', 'TZ', 'DEBIAN_FRONTEND'})
RUNTIME_CONFIG_KEYS = ('Env', 'Entrypoint', 'Cmd', 'User', 'WorkingDir', 'Volumes',
                       'ExposedPorts', 'Healthcheck', 'StopSignal', 'Shell')


class OverlayError(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise OverlayError(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def replace_once(source, before, after):
    require(source.count(before) == 1, 'unrecognized_donor_patch_shape')
    return source.replace(before, after, 1)


def patch_sources(sources):
    """Keep every donor byte outside the explicitly reviewed replacements."""
    config, registry = sources['config.py'], sources['registry.py']
    for field in HISTORY_FIELDS:
        annotation = 'list[_KubernetesWorker]' if field == 'kubernetes_workers' else 'list[dict]'
        before = f'    {field}: {annotation} = Field(default_factory=list, max_length=100)'
        config = replace_once(config, before, before.replace('max_length=100', 'max_length=256'))
    registry = replace_once(registry, 'from .profiles import implementation\n',
        'from .profiles import implementation\nfrom .catalog_limits import validate_catalog_budget\n')
    registry = replace_once(registry,
        'def _index(cls, values, *, binding=False):\n    if type(values) not in (tuple, list) or len(values) > 100:',
        'def _index(cls, values, *, binding=False):\n'
        '    maximum = 100 if cls is TaskContract else 256\n'
        '    if type(values) not in (tuple, list) or len(values) > maximum:')
    registry = replace_once(registry,
        '                    execution_bindings, routes):\n        contracts = _index(TaskContract, task_contracts)',
        '                    execution_bindings, routes):\n'
        '        validate_catalog_budget(dict(task_contracts=task_contracts,\n'
        '            capability_bundles=capability_bundles, tool_environments=tool_environments,\n'
        '            execution_bindings=execution_bindings, routes=routes))\n'
        '        contracts = _index(TaskContract, task_contracts)')
    for source in (config, registry):
        ast.parse(source)
    return {'config.py': config.encode('utf-8'), 'registry.py': registry.encode('utf-8')}


def check_environment(config):
    require(not config.get('OnBuild'), 'donor_onbuild_forbidden')
    seen = set()
    for entry in config.get('Env') or []:
        require(type(entry) is str and '=' in entry, 'donor_environment_unreviewed')
        key, value = entry.split('=', 1)
        require(key in SAFE_ENV_KEYS and key not in seen and not any(ord(c) < 32 for c in value)
            and not re.search(r'://[^/\s]*@|(?:token|password|secret|api[_-]?key)=', value, re.I),
                'donor_environment_unreviewed')
        seen.add(key)
    return sorted(seen)


def command(arguments, *, input=None, stdout=subprocess.PIPE):
    # Docker credentials stay in the existing server CLI configuration. They
    # are never passed as build arguments, environment, context files or logs.
    result = subprocess.run(arguments, input=input, stdout=stdout, stderr=subprocess.PIPE, check=False)
    require(result.returncode == 0, 'overlay_command_failed')
    return result.stdout


def inspect_image(image):
    rows = json.loads(command(['docker', 'image', 'inspect', image]))
    require(type(rows) is list and len(rows) == 1, 'invalid_donor_inspection')
    return rows[0]


def filesystem_manifest(archive):
    """Hash every exported rootfs file and retain link/security metadata.

    Timestamps are excluded because Docker COPY changes directory timestamps.
    No file content is returned. Docker's immutable image tree is exported
    from a never-started container, not from any running service filesystem.
    """
    result, total = {}, 0
    with tarfile.open(fileobj=archive, mode='r|*') as stream:
        for item in stream:
            name = item.name.removeprefix('./').rstrip('/')
            if name in ('', '.'):
                continue
            require(not name.startswith('/') and '..' not in PurePosixPath(name).parts
                and name not in result and len(result) < 200000, 'invalid_exported_filesystem')
            descriptor = dict(mode=item.mode, uid=item.uid, gid=item.gid,
                              type=item.type.decode('ascii'), link=item.linkname,
                              device_major=item.devmajor, device_minor=item.devminor,
                              pax={key: value for key, value in item.pax_headers.items()
                                   if key not in ('mtime', 'atime', 'ctime')})
            if item.isfile():
                total += item.size
                require(total <= 16 * 1024**3, 'exported_filesystem_exceeds_bound')
                digest = hashlib.sha256()
                file = stream.extractfile(item)
                while chunk := file.read(1024 * 1024):
                    digest.update(chunk)
                descriptor.update(bytes=item.size, sha256=digest.hexdigest())
            result[name] = descriptor
    return result


def export_manifest(image):
    name = 'agent-capacity-inspect-' + uuid.uuid4().hex[:20]
    created = False
    try:
        command(['docker', 'create', '--name', name, '--network', 'none', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--entrypoint', '/usr/local/bin/python', image, '-c', 'pass'])
        created = True
        with tempfile.TemporaryFile() as stream:
            command(['docker', 'export', name], stdout=stream)
            stream.seek(0)
            return filesystem_manifest(stream)
    finally:
        if created:
            command(['docker', 'rm', name])


def package_root(manifest, inspection):
    candidates = [name[:-len('/config.py')] for name, row in manifest.items()
                  if name.endswith('/agent_service/config.py')
                  and row.get('sha256') == inspection['hashes']['config.py']['sha256']]
    require(len(candidates) == 1, 'donor_package_root_ambiguous')
    prefix = candidates[0]
    require(re.fullmatch(r'[A-Za-z0-9_./-]+', prefix), 'invalid_donor_package_path')
    for name, expected in inspection['hashes'].items():
        actual = manifest.get(prefix + '/' + name, {})
        require(actual.get('sha256') == expected['sha256'] and actual.get('bytes') == expected['bytes'],
                'donor_source_manifest_mismatch')
    python_paths = {name[len(prefix) + 1:] for name, row in manifest.items()
                    if name.startswith(prefix + '/') and name.endswith('.py') and 'sha256' in row}
    require(python_paths == set(inspection['hashes']), 'donor_python_file_set_mismatch')
    require(prefix + '/catalog_limits.py' not in manifest, 'donor_capacity_already_present')
    return prefix


def verify_delta(before, after, prefix, replacements):
    expected = {prefix + '/' + name for name in replacements}
    changed = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
    require(changed == expected, 'unexpected_rootfs_change')
    for name, raw in replacements.items():
        row = after[prefix + '/' + name]
        require(row.get('sha256') == sha(raw) and row.get('bytes') == len(raw)
            and row['mode'] == 0o644 and row['uid'] == row['gid'] == 0, 'overlay_file_mismatch')
    return sorted(changed)


def context(donor, prefix, replacements):
    dockerfile = ('FROM ' + donor + '\n' + ''.join(
        f'COPY --chown=0:0 {name} /{prefix}/{name}\n' for name in sorted(replacements))).encode('utf-8')
    files = {'Dockerfile': dockerfile, **replacements}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as stream:
        for name, raw in sorted(files.items()):
            item = tarfile.TarInfo(name)
            item.size, item.mode, item.uid, item.gid, item.mtime = len(raw), 0o644, 0, 0, 0
            stream.addfile(item, io.BytesIO(raw))
    return output.getvalue(), {name: sha(raw) for name, raw in files.items()}


def build(*, inspection, target, revision, output_dir, push=False):
    donor = inspection['image']
    require(type(donor) is str and IMAGE.fullmatch(donor) and TARGET.fullmatch(target)
        and REVISION.fullmatch(revision), 'invalid_overlay_identity')
    actual_revision = command(['git', '-C', str(REPOSITORY), 'rev-parse', 'HEAD']).decode('ascii').strip()
    require(actual_revision == revision, 'overlay_git_revision_mismatch')
    command(['git', '-C', str(REPOSITORY), 'diff', '--quiet', 'HEAD', '--',
             'agent_service/deploy/build_capacity_overlay.py', 'agent_service/catalog_limits.py'])
    command(['git', '-C', str(REPOSITORY), 'ls-files', '--error-unmatch', '--',
             'agent_service/deploy/build_capacity_overlay.py', 'agent_service/catalog_limits.py'])
    directory = Path(output_dir)
    require(not directory.exists() and not directory.is_symlink(), 'overlay_output_must_be_new')
    for name in PATCHED:
        raw = inspection['sources'][name].encode('utf-8')
        require(sha(raw) == inspection['hashes'][name]['sha256'], 'inspection_source_hash_mismatch')
    original = inspect_image(donor)
    require(original['Id'] == inspection['image_id'], 'donor_image_identity_mismatch')
    environment_keys = check_environment(original['Config'])
    before = export_manifest(donor)
    prefix = package_root(before, inspection)
    replacements = patch_sources(inspection['sources'])
    replacements['catalog_limits.py'] = (REPOSITORY / 'agent_service/catalog_limits.py').read_bytes()
    archive, source_hashes = context(donor, prefix, replacements)
    context_hash = sha(archive)
    command(['docker', 'build', '--network=none', '--pull=false', '--tag', target,
        '--label', 'org.opencontainers.image.revision=' + revision,
        '--label', 'agent-service.panghuer.top.capacity-overlay-base=' + donor,
        '--label', 'agent-service.panghuer.top.capacity-overlay-context=' + context_hash,
        '--label', 'agent-service.panghuer.top.capacity-overlay-schema=bounded-catalog-256.v1', '-'], input=archive)
    candidate = inspect_image(target)
    require(all(candidate['Config'].get(key) == original['Config'].get(key) for key in RUNTIME_CONFIG_KEYS),
            'donor_runtime_configuration_changed')
    require(candidate['RootFS']['Layers'][:len(original['RootFS']['Layers'])] == original['RootFS']['Layers'],
            'donor_layers_not_preserved')
    after = export_manifest(candidate['Id'])
    changed = verify_delta(before, after, prefix, replacements)
    reference = None
    if push:
        command(['docker', 'push', target])
        candidate = inspect_image(target)
        references = [value for value in candidate.get('RepoDigests', []) if value.startswith(target[:-7] + '@sha256:')]
        require(len(references) == 1 and IMAGE.fullmatch(references[0]), 'published_digest_unavailable')
        reference = references[0]
    report = dict(schema='agent-control-capacity-overlay.v1', source_revision=revision,
        donor=donor, donor_image_id=original['Id'], candidate_image_id=candidate['Id'],
        published_image=reference, pushed=push, package_root='/' + prefix,
        history_record_limit=256, contract_route_limit=100, catalog_reader_bytes=4 * 1024 * 1024,
        source_context_sha256=context_hash, source_files=source_hashes,
        full_rootfs_before_sha256=sha(canonical(before)), full_rootfs_after_sha256=sha(canonical(after)),
        full_rootfs_before_entries=len(before), full_rootfs_after_entries=len(after),
        changed_paths=changed, all_other_files_links_and_permissions_preserved=True,
        runtime_configuration_preserved=True, inherited_environment_keys=environment_keys,
        runtime_catalog_reader_verified=False,
        donor_containers_started=0, dependencies_installed=0, cluster_writes=0,
        database_access=False, runtime_secret_mounts_accessed=False,
        acceptance_created=False, publication_authorized=False)
    directory.mkdir(parents=True)
    for name, value in (('rootfs-before.json', before), ('rootfs-after.json', after), ('overlay.json', report)):
        (directory / name).write_bytes(canonical(value) + b'\n')
    if reference:
        (directory / 'image.txt').write_text(reference + '\n', encoding='ascii')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspection', required=True, type=Path)
    parser.add_argument('--image', required=True, help='Private registry repository with :latest tag')
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--push', action='store_true')
    args = parser.parse_args(argv)
    try:
        report = build(inspection=json.loads(args.inspection.read_bytes()), target=args.image,
            revision=args.source_revision, output_dir=args.output_dir, push=args.push)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError, SyntaxError,
            tarfile.TarError, OverlayError):
        print(json.dumps(dict(schema='agent-control-capacity-overlay.v1', status='blocked',
                              code='capacity_overlay_not_verified', publication_authorized=False)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
