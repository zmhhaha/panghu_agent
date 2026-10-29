"""Generate an unaccepted deployment catalog from fixed image-owned packages.

This command measures local files and writes public JSON only. It never starts
an Agent, resolves a model, contacts a registry, reads a Secret, or deploys a Pod.
Run it inside a reviewed domain Worker image with networking disabled.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from .contracts import canonical_bytes, digest, safe_relative_path
from .profiles import implementation
from .pi_cage import PI_CAGE_REPOSITORY, PI_CAGE_REVISION, PI_PACKAGE, PI_VERSION
from .registry import (CapabilityBundle, DeploymentRegistry, ExecutionBinding,
                       ModelPolicy, TaskContract, ToolEnvironment, binding_fingerprint)


PPT_COMMIT = 'c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7'
PPT_ROOT = Path('/opt/ppt-master')
PPT_BUILD_COMMIT = Path('/opt/ppt-service/build-info/ppt-master.commit')
RESEARCH_ROOT = Path(__file__).resolve().parent.parent / 'scientific_skills'
RESEARCH_REVISION = 'local-procedures-v2'
RESEARCH_MANIFEST_SHA256 = '34645ae7322a971bc79da2006d8d4faa65ab59075d9720e410be740b0abebd41'
PPT_PROFILES = tuple('ppt.' + stage + '.v1' for stage in (
    'plan_initial', 'plan_options', 'prepare', 'generate', 'revise'))
RESEARCH_PROFILES = ('research.generate.v1', 'research.revise.v1', 'research.plan.v1',
                     'research.retrieve.v1', 'research.outline.v1', 'research.draft.v1')
CATALOG_KEYS = ('task_contracts', 'capability_bundles', 'tool_environments',
                'execution_bindings', 'routes')
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_BYTES = 128 * 1024 * 1024
MAX_RESOURCES = 2048
REVISION = re.compile(r'[A-Za-z0-9_.-]{1,100}\Z')
IMAGE = re.compile(
    r'(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?/)?'
    r'[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*'
    r'@sha256:[0-9a-f]{64}\Z')


class CatalogError(RuntimeError):
    """Fixed diagnostic category; no package contents or private state."""


def _require(condition, code):
    if not condition:
        raise CatalogError(code)


def _directory(path, *, package=True):
    path = Path(path).absolute()
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        _require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            and not getattr(info, 'st_file_attributes', 0) & 0x400, 'unsafe_catalog_directory')
        if package:
            _require(os.name == 'posix' and info.st_uid == 0 and not info.st_mode & 0o022,
                     'package_not_image_owned')
    return path


def _read(path, limit=MAX_FILE_BYTES):
    path = Path(path).absolute()
    _directory(path.parent)
    info = path.lstat()
    _require(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
        and info.st_nlink == 1 and info.st_uid == 0 and not info.st_mode & 0o022
        and not getattr(info, 'st_file_attributes', 0) & 0x400
        and 0 <= info.st_size <= limit, 'unsafe_package_file')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        _require(os.path.samestat(info, opened), 'package_changed_during_measurement')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    _require(len(raw) == info.st_size and len(raw) <= limit
        and after.st_size == info.st_size and after.st_mtime_ns == info.st_mtime_ns,
        'package_changed_during_measurement')
    return raw


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            _require(key not in value, 'duplicate_package_manifest_key')
            value[key] = item
        return value
    try:
        return json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise CatalogError('invalid_package_manifest') from None


def _tree_files(root, relative):
    base = _directory(root / relative)
    result = []
    for path in sorted(base.iterdir()):
        info = path.lstat()
        _require(not stat.S_ISLNK(info.st_mode)
            and not getattr(info, 'st_file_attributes', 0) & 0x400, 'linked_package_resource')
        name = path.relative_to(root).as_posix()
        safe_relative_path(name)
        if stat.S_ISDIR(info.st_mode):
            result.extend(_tree_files(root, name))
        else:
            _require(stat.S_ISREG(info.st_mode), 'unsafe_package_file')
            result.append(name)
        _require(len(result) <= MAX_RESOURCES, 'package_resource_limit')
    return result


def _measure(root, names):
    root = _directory(root)
    _require(len(names) == len(set(names)) and 1 <= len(names) <= MAX_RESOURCES,
             'package_resource_limit')
    total, resources = 0, []
    for name in sorted(names):
        safe_relative_path(name)
        raw = _read(root / name)
        total += len(raw)
        _require(total <= MAX_PACKAGE_BYTES, 'package_resource_limit')
        resources.append(dict(name=name, sha256=hashlib.sha256(raw).hexdigest()))
    return resources, total


def _ppt_bundle():
    _require(_read(PPT_BUILD_COMMIT, 128).decode('ascii').strip() == PPT_COMMIT,
             'ppt_master_commit_mismatch')
    _require(_read(PPT_ROOT / 'LICENSE', 65536).decode('utf-8').startswith('MIT License'),
             'ppt_master_license_mismatch')
    skill = _directory(PPT_ROOT / 'skills/ppt-master')
    # Procedural resources are shared by every PPT stage. The complete official
    # executable/template inventory is independently pinned by environment image
    # digest; thousands of icon/template files are not misrepresented as skills.
    names = ['LICENSE']
    for path in sorted(skill.iterdir()):
        info = path.lstat()
        _require(not stat.S_ISLNK(info.st_mode), 'linked_package_resource')
        if stat.S_ISREG(info.st_mode):
            names.append(path.relative_to(PPT_ROOT).as_posix())
    names.extend(_tree_files(PPT_ROOT, 'skills/ppt-master/references'))
    _require('skills/ppt-master/SKILL.md' in names and 'skills/ppt-master/LICENSE' in names,
             'ppt_master_resources_missing')
    resources, total = _measure(PPT_ROOT, names)
    bundle = CapabilityBundle.model_validate(dict(name='ppt-master', revision=PPT_COMMIT,
        resources=resources, source='https://github.com/hugohe3/ppt-master/tree/' + PPT_COMMIT,
        license='MIT', required_tools=['read', 'write', 'bash']))
    return bundle, total


def _research_bundle():
    raw = _read(RESEARCH_ROOT / 'manifest.json', 65536)
    _require(hashlib.sha256(raw).hexdigest() == RESEARCH_MANIFEST_SHA256,
             'research_bundle_integrity')
    manifest = _json(raw)
    _require(type(manifest) is dict and manifest.get('bundle_revision') == RESEARCH_REVISION
        and manifest.get('license') == 'MIT' and manifest.get('upstream_content_vendored') is False
        and manifest.get('automatic_updates') is False, 'research_bundle_integrity')
    expected = {'research-procedure/SKILL.md', 'task-template.md', 'LICENSE'}
    rows = manifest.get('local_files')
    _require(type(rows) is list and len(rows) == len(expected)
        and all(type(row) is dict for row in rows)
        and {row.get('path') for row in rows} == expected, 'research_bundle_integrity')
    resources, total = _measure(RESEARCH_ROOT, ['manifest.json', *sorted(expected)])
    measured = {row['name']: row['sha256'] for row in resources}
    _require(all(measured[row['path']] == row['sha256'] for row in rows), 'research_bundle_integrity')
    bundle = CapabilityBundle.model_validate(dict(name='research-procedures', revision=RESEARCH_REVISION,
        resources=resources, source='original-local-procedures-v2', license='MIT',
        required_tools=['read', 'write']))
    return bundle, total


def _image(value):
    _require(type(value) is str and IMAGE.fullmatch(value) is not None
        and value.rsplit(':', 1)[-1] != '0' * 64, 'immutable_worker_image_required')
    origin = value.rsplit('@', 1)[0].split('/', 1)[0]
    if ':' in origin:
        _require(1 <= int(origin.rsplit(':', 1)[1]) <= 65535, 'immutable_worker_image_required')
    return value


def _contract(name):
    handler = implementation(name)
    capabilities = ['text', 'tool_calls'] + (['vision'] if 'image' in handler.required_tools else [])
    return TaskContract.model_validate(dict(name=name, revision='v1', task_schema=handler.task_schema,
        output_schema=handler.output_schema, required_tools=list(handler.required_tools),
        required_model_capabilities=capabilities, max_timeout_seconds=3600,
        **handler.limits.as_dict()))


def _fixed_upstream(value):
    _require(type(value) is str and 1 <= len(value) <= 300, 'invalid_fixed_model_upstream')
    parsed = urlsplit(value)
    _require(parsed.scheme in {'http', 'https'} and bool(parsed.hostname)
        and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
        and parsed.path.rstrip('/') == '/v1' and '\\' not in value
        and not any(ord(character) <= 32 or ord(character) == 127 for character in value),
        'invalid_fixed_model_upstream')
    if parsed.port is not None:
        _require(1 <= parsed.port <= 65535, 'invalid_fixed_model_upstream')
    return value


def generate(*, pi_image=None, hermes_image=None, engine='pi', runtime_revision,
             model='llm-trusted', provider='task-model',
             model_proxy_url='http://agent-model-proxy.agent-service.svc.cluster.local:8080/v1',
             upstream_base_url='http://llm-service.llm.svc.cluster.local/v1',
             pod_cidrs=('10.244.0.0/16',)):
    """Return public catalog/hosts/summary without model, network or database I/O."""
    _require(os.name == 'posix' and os.geteuid() == 0, 'run_inside_root_owned_worker_image')
    _require(type(runtime_revision) is str and REVISION.fullmatch(runtime_revision),
             'runtime_revision_required')
    images = {key: _image(value) for key, value in (('pi', pi_image), ('hermes', hermes_image))
              if value is not None}
    _require(engine in images, 'selected_engine_image_required')
    upstream_base_url = _fixed_upstream(upstream_base_url)
    networks = []
    for item in pod_cidrs:
        network = ipaddress.ip_network(item, strict=True)
        _require(network.is_private and network.prefixlen > 0 and not network.is_loopback
            and not network.is_link_local and not network.is_multicast, 'private_pod_cidr_required')
        networks.append(str(network))
    _require(1 <= len(networks) <= 8 and len(networks) == len(set(networks)), 'private_pod_cidr_required')
    networks.sort()
    ppt_bundle, ppt_bytes = _ppt_bundle()
    research_bundle, research_bytes = _research_bundle()
    contracts = [_contract(name) for name in (*PPT_PROFILES, *RESEARCH_PROFILES)]
    environments, bindings, routes, pools = [], [], [], []
    host_selections = []
    for native, image in sorted(images.items()):
        groups = {}
        for contract in contracts:
            groups.setdefault(tuple(sorted(contract.required_tools)), []).append(contract)
        environment_by_tools = {}
        for tools, members in sorted(groups.items()):
            data = dict(name='tools-' + native + '-' + digest(list(tools))[:12],
                tools=list(tools), capabilities=sorted({item.output_schema for item in members}),
                image_digest=image.rsplit('@', 1)[1], runtime_revision=runtime_revision)
            data['revision'] = 'env-' + digest(data)[:20]
            environment = ToolEnvironment.model_validate(data)
            environments.append(environment.model_dump(mode='json'))
            environment_by_tools[tools] = environment
        for contract in contracts:
            bundle = ppt_bundle if contract.name.startswith('ppt.') else research_bundle
            environment = environment_by_tools[tuple(sorted(contract.required_tools))]
            model_data = dict(api_mode='openai-responses', model=model, provider=provider,
                              capabilities=list(contract.required_model_capabilities))
            model_data['revision'] = 'model-' + digest(model_data)[:20]
            policy = ModelPolicy.model_validate(model_data)
            binding_id = contract.name.removesuffix('.v1').replace('.', '-').replace('_', '-') + '-' + native
            data = dict(id=binding_id, profile=contract.name, profile_revision=contract.revision,
                bundle=dict(name=bundle.name, revision=bundle.revision),
                tool_environment=dict(name=environment.name, revision=environment.revision),
                engine=native, adapter_revision='pi-cage-adapter-v1' if native == 'pi' else 'hermes-adapter-v1',
                model_policy=policy.model_dump(mode='json'))
            # The combination identity includes measured resource hashes and
            # contract quotas, not merely bundle/environment revision labels.
            # binding_fingerprint deliberately excludes the binding's own ID/
            # revision, so deriving its revision here is not recursive.
            data['revision'] = 'v1'
            candidate = ExecutionBinding.model_validate(data)
            fingerprint = binding_fingerprint(contract, bundle, environment, candidate)
            data['revision'] = 'binding-' + fingerprint[:20]
            binding = ExecutionBinding.model_validate(data)
            bindings.append(binding.model_dump(mode='json', exclude_none=True))
            host_name = binding_id + '-' + fingerprint[:20]
            pools.append(dict(pool_id=host_name, engine=native, binding_id=binding.id,
                binding_revision=binding.revision, namespace='agent-workers', image=image,
                host_config_map=host_name, pod_cidrs=networks, enabled=False, port=8090,
                uid_start=None, uid_count=65536, read_only_mounts=False,
                token_ref=native + '-worker-pool', receipt_key_ref=native + '-worker-pool'))
            host_selections.append((host_name, contract, binding, image))
            if native == engine:
                routes.append(dict(profile=contract.name, profile_revision=contract.revision,
                    binding_id=binding.id, binding_revision=binding.revision, enabled=False))
    catalog = dict(task_contracts=[item.model_dump(mode='json') for item in contracts],
        capability_bundles=[ppt_bundle.model_dump(mode='json'), research_bundle.model_dump(mode='json')],
        tool_environments=environments, execution_bindings=bindings, routes=routes)
    registry = DeploymentRegistry.from_config(**catalog)
    public = dict(schema_version=2, callers=[
        dict(tenant='internal', caller='ppt-service', profiles=list(PPT_PROFILES), token_ref='ppt-service'),
        dict(tenant='internal', caller='scientific-service', profiles=list(RESEARCH_PROFILES),
             token_ref='scientific-service')], **catalog, workers=[], kubernetes_workers=pools,
        api=dict(host='0.0.0.0', port=8080, max_request_bytes=1024 * 1024, max_input_bytes=25 * 1024 * 1024),
        dispatcher=dict(poll_seconds=1.0, lease_seconds=60), store=dict(max_events=1000, max_queue=1000),
        model_proxy=dict(upstream_base_url=upstream_base_url, upstream_token_ref='model-upstream',
            signing_key_ref='model-signing', max_calls_per_attempt=128, max_output_tokens=32768))
    # Parse only public operator data. No load_configuration(), Secret, store,
    # model capability probe, subprocess or runtime assembly is involved.
    from .config import _Public
    from .worker_entry import HostConfig, host_policy
    _Public.model_validate(public)
    hosts = {}
    for host_name, contract, binding, image in host_selections:
        host = HostConfig.model_validate(dict(schema_version=1, engine=binding.engine,
            binding_id=binding.id, binding_revision=binding.revision,
            profile=dict(name=contract.name, revision=contract.revision,
                runtime_revision=runtime_revision, image_digest=image.rsplit('@', 1)[1],
                enabled=False, max_timeout_seconds=contract.max_timeout_seconds),
            model=dict(model=model, provider=provider, api='openai-responses', proxy_base_url=model_proxy_url),
            execution_catalog=catalog))
        resolved = host_policy(host)
        _require(resolved.execution_binding == registry.binding_policy(binding.id, binding.revision).execution_binding
            and resolved.enabled is False, 'generated_host_binding_mismatch')
        hosts[host_name] = host.model_dump(mode='json', exclude_none=True)
    summary = dict(schema_version='agent-deployment-catalog.v1', selected_new_job_engine=engine,
        routes_enabled=False, worker_pools_enabled=False, acceptance_created=False,
        model_probe_performed=False, network_requests=0, secrets_created=False,
        public_config_sha256=digest(public), catalog=registry.summary(),
        packages=[dict(name=ppt_bundle.name, revision=ppt_bundle.revision,
            resources=len(ppt_bundle.resources), measured_bytes=ppt_bytes,
            scope='skill_entrypoints_and_references_complete_tool_inventory_pinned_by_image'),
            dict(name=research_bundle.name, revision=research_bundle.revision,
                resources=len(research_bundle.resources), measured_bytes=research_bytes,
                scope='original_local_procedures_not_upstream_k_dense')],
        worker_configs=[dict(host_config_map=name, path='worker-configs/' + name + '/config.json',
            engine=host['engine'], binding_id=host['binding_id'], binding_revision=host['binding_revision'])
            for name, host in sorted(hosts.items())])
    if 'pi' in images:
        summary['pi_runtime_requirement'] = dict(repository=PI_CAGE_REPOSITORY,
            revision=PI_CAGE_REVISION, package=PI_PACKAGE, version=PI_VERSION,
            adapter_revision='pi-cage-adapter-v1', verification_required_at=['worker_build', 'worker_readiness'])
    return public, hosts, summary


def _write(path, value):
    raw = canonical_bytes(value) + b'\n'
    _require(len(raw) <= 4 * 1024 * 1024, 'generated_configuration_too_large')
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags, 0o644), 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def write_catalog(output, public, hosts, summary):
    """Write only into a fresh/empty ordinary directory; never replace a catalog."""
    directory = Path(output).absolute()
    missing = []
    current = directory
    while not current.exists():
        _require(not current.is_symlink(), 'linked_catalog_output')
        missing.append(current)
        current = current.parent
    _directory(current, package=False)
    for path in reversed(missing):
        path.mkdir(mode=0o755)
    directory = _directory(directory, package=False)
    _require(not any(directory.iterdir()), 'catalog_output_must_be_empty')
    workers = directory / 'worker-configs'
    workers.mkdir(mode=0o755)
    for name, value in sorted(hosts.items()):
        _require(type(name) is str and re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', name),
                 'invalid_generated_host_name')
        target = workers / name
        target.mkdir(mode=0o755)
        _write(target / 'config.json', value)
    _write(directory / 'config.public.json', public)
    _write(directory / 'summary.json', summary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pi-image', help='Immutable Pi domain Worker repository@sha256 reference')
    parser.add_argument('--hermes-image', help='Immutable Hermes domain Worker repository@sha256 reference')
    parser.add_argument('--engine', choices=('pi', 'hermes'), default='pi', help='Initial disabled routes for new Jobs')
    parser.add_argument('--runtime-revision', required=True)
    parser.add_argument('--model', default='llm-trusted')
    parser.add_argument('--provider', default='task-model')
    parser.add_argument('--model-proxy-url', default='http://agent-model-proxy.agent-service.svc.cluster.local:8080/v1')
    parser.add_argument('--upstream-base-url', default='http://llm-service.llm.svc.cluster.local/v1')
    parser.add_argument('--pod-cidr', action='append', help='Actual private worker Pod CIDR; default 10.244.0.0/16')
    parser.add_argument('--output', required=True, help='New or empty public output directory')
    args = parser.parse_args(argv)
    try:
        public, hosts, summary = generate(pi_image=args.pi_image, hermes_image=args.hermes_image,
            engine=args.engine, runtime_revision=args.runtime_revision, model=args.model, provider=args.provider,
            model_proxy_url=args.model_proxy_url, upstream_base_url=args.upstream_base_url,
            pod_cidrs=tuple(args.pod_cidr or ('10.244.0.0/16',)))
        write_catalog(args.output, public, hosts, summary)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, RecursionError):
        print('ERROR: Invalid or unavailable image package/catalog input; no model or deployment was started')
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
