"""Safe donor preservation; no Docker, network or local server is invoked."""
from copy import deepcopy
import io
import tarfile

import pytest

from agent_service.deploy.build_capacity_overlay import (
    HISTORY_FIELDS, OverlayError, check_environment, context, filesystem_manifest,
    patch_sources, sha, verify_delta,
)


def sources():
    config = 'class _Public:\n'
    for field in HISTORY_FIELDS:
        annotation = 'list[_KubernetesWorker]' if field == 'kubernetes_workers' else 'list[dict]'
        config += f'    {field}: {annotation} = Field(default_factory=list, max_length=100)\n'
    config += '    task_contracts: list[dict] = Field(default_factory=list, max_length=100)\n'
    config += 'NEWER_DONOR_BEHAVIOR = "preserve-this-exactly"\n'
    registry = ('from .profiles import implementation\n'
        'def _index(cls, values, *, binding=False):\n'
        '    if type(values) not in (tuple, list) or len(values) > 100:\n'
        '        raise ValueError("bounded")\n'
        'class DeploymentRegistry:\n'
        '    @classmethod\n'
        '    def from_config(cls, task_contracts, capability_bundles, tool_environments,\n'
        '                    execution_bindings, routes):\n'
        '        contracts = _index(TaskContract, task_contracts)\n'
        '        if len(routes) > 100:\n'
        '            raise ValueError("routes remain bounded")\n'
        'NEWER_VIDEO_IMPLEMENTATION = "do-not-replace-with-older-repo-code"\n')
    return {'config.py': config, 'registry.py': registry}


def test_only_reviewed_count_and_reader_guards_are_patched_into_actual_donor():
    original = sources()
    result = patch_sources(original)
    config, registry = (result[name].decode('utf-8') for name in ('config.py', 'registry.py'))
    assert config.count('max_length=256') == 4
    assert 'task_contracts: list[dict] = Field(default_factory=list, max_length=100)' in config
    assert 'maximum = 100 if cls is TaskContract else 256' in registry
    assert 'if len(routes) > 100:' in registry and 'validate_catalog_budget(dict(' in registry
    assert config.endswith(original['config.py'].splitlines(keepends=True)[-1])
    assert registry.endswith(original['registry.py'].splitlines(keepends=True)[-1])
    unknown = sources()
    unknown['config.py'] = unknown['config.py'].replace('max_length=100', 'max_length=99', 1)
    with pytest.raises(OverlayError, match='unrecognized_donor_patch_shape'):
        patch_sources(unknown)
    duplicate = sources()
    duplicate['registry.py'] += 'from .profiles import implementation\n'
    with pytest.raises(OverlayError, match='unrecognized_donor_patch_shape'):
        patch_sources(duplicate)


def test_overlay_context_contains_only_two_donor_patches_and_new_fixed_budget_module():
    replacements = {**patch_sources(sources()), 'catalog_limits.py': b'HISTORY_RECORD_LIMIT = 256\n'}
    donor = 'registry.invalid/agent-control:latest@sha256:' + 'a' * 64
    raw, hashes = context(donor, 'app/agent_service', replacements)
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        assert set(archive.getnames()) == {'Dockerfile', *replacements}
        dockerfile = archive.extractfile('Dockerfile').read().decode('utf-8')
    assert dockerfile.startswith('FROM ' + donor + '\n')
    assert dockerfile.count('COPY --chown=0:0 ') == 3
    assert not any(word in dockerfile for word in ('RUN ', 'ENV ', 'ARG ', 'ENTRYPOINT ', 'CMD '))
    assert hashes['registry.py'] == sha(replacements['registry.py'])


def test_full_rootfs_manifest_hashes_non_python_files_and_preserves_links():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as stream:
        for name, content in [('app/agent_service/config.py', b'config'),
                              ('usr/share/fonts/example.ttf', b'font-data'),
                              ('app/agent_service/resources/guide.md', b'newer-domain-guide')]:
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(content), 0o644
            stream.addfile(item, io.BytesIO(content))
        link = tarfile.TarInfo('usr/local/bin/python')
        link.type, link.linkname, link.mode = tarfile.SYMTYPE, 'python3', 0o777
        stream.addfile(link)
    manifest = filesystem_manifest(io.BytesIO(output.getvalue()))
    assert manifest['usr/share/fonts/example.ttf']['sha256'] == sha(b'font-data')
    assert manifest['app/agent_service/resources/guide.md']['sha256'] == sha(b'newer-domain-guide')
    assert manifest['usr/local/bin/python']['link'] == 'python3'
    before = deepcopy(manifest)
    replacement = b'capacity-config'
    after = deepcopy(before)
    after['app/agent_service/config.py'].update(sha256=sha(replacement), bytes=len(replacement))
    assert verify_delta(before, after, 'app/agent_service', {'config.py': replacement}) == ['app/agent_service/config.py']
    after['usr/share/fonts/example.ttf']['sha256'] = 'b' * 64
    with pytest.raises(OverlayError, match='unexpected_rootfs_change'):
        verify_delta(before, after, 'app/agent_service', {'config.py': replacement})


def test_donor_environment_and_onbuild_fail_closed_without_printing_credentials():
    assert check_environment({'Env': ['PATH=/usr/local/bin:/usr/bin', 'PYTHONUNBUFFERED=1']}) == ['PATH', 'PYTHONUNBUFFERED']
    for value in ({'OnBuild': ['RUN install-other-code']},
                  {'Env': ['PROVIDER_TOKEN=synthetic-private-value']},
                  {'Env': ['PYTHON_GET_PIP_URL=https://user:password@invalid.internal/install']},
                  {'Env': ['PATH=/usr/bin', 'PATH=/other/bin']}):
        with pytest.raises(OverlayError) as failure:
            check_environment(value)
        assert 'synthetic-private-value' not in str(failure.value)
