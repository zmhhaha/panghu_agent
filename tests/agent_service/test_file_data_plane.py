"""Transactional data-plane regressions; synthetic stop is not host acceptance."""
from dataclasses import replace
import hashlib
import uuid

import pytest

from agent_service.checkpoints import checkpoint_manifest, compatible_checkpoint
from agent_service.contracts import InlineFile, ServiceError, digest
from agent_service.staging import stage_inputs
from agent_service.store import Store
from test_binding_handoff import binding, policy
from test_store import allocation_envelope


@pytest.fixture
def store(tmp_path):
    value = Store('sqlite:///' + str(tmp_path / 'data-plane.db'), allow_sqlite=True,
                  clock=lambda: 1000.0)
    value.initialize()
    yield value
    value.close()


def request(profile=None, **changes):
    profile = profile or policy()
    value = dict(api_version='1', profile=profile.name, profile_revision=profile.revision,
        business_ref='opaque-business-stage', task={'prompt': 'offline'}, inputs=[],
        timeout_seconds=600)
    return {**value, **changes}


def complete(store, configured, key, files, **changes):
    job, _ = store.submit('tenant', 'business', key, request(configured, **changes), configured.pins())
    claim = store.claim()
    assert claim['id'] == job['id']
    store.finish(claim['id'], claim['attempt_id'], claim['fence'],
        {'state': 'completed', 'error_code': None, 'usage': None, 'stop_receipt': None},
        [dict(name=name, content=content) for name, content in files], stopped=True)
    return job, claim


def expect_code(code, function):
    with pytest.raises(ServiceError) as failure:
        function()
    assert failure.value.code == code


def test_binary_checkpoint_owner_hash_and_transitive_retention_survive_restart(store):
    source = store.put_input('tenant', 'business', 'inputs/empty.bin', b'')
    configured = replace(policy(), supports_inputs=True)
    job, _ = complete(store, configured, 'source', [('project/asset.bin', b'\x00\xffbinary'),
        ('result/checkpoint.json', b'{}')],
        inputs=[dict(input_id=source['id'], sha256=source['sha256'])])
    manifest = store.get_artifact_manifest('tenant', 'business', job['id'])
    assert manifest['sha256'] == digest({key: item for key, item in manifest.items() if key != 'sha256'})
    assert manifest['publication_authorized'] is False
    checkpoint, created = store.create_checkpoint('tenant', 'business', job['id'], 'portable')
    assert created is True
    repeated, created = store.create_checkpoint('tenant', 'business', job['id'], 'portable')
    assert created is False and repeated == checkpoint
    ref = {key: checkpoint[key] for key in ('checkpoint_id', 'sha256', 'format')}
    expect_code('not_found', lambda: store.get_checkpoint('other-tenant', 'business', ref['checkpoint_id']))
    expect_code('checkpoint_hash_mismatch', lambda: store.submit('tenant', 'business', 'bad-hash',
        request(configured, checkpoint_ref={**ref, 'sha256': '0' * 64}), configured.pins()))
    recovered = replace(policy(binding(engine='hermes', binding_id='workspace-hermes')),
                        supports_inputs=True)
    replay, _ = store.submit('tenant', 'business', 'replay', request(recovered, checkpoint_ref=ref), recovered.pins())
    assert [item.name for item in store.checkpoint_files('tenant', 'business', ref, recovered.pins())] == [
        'project/asset.bin', 'result/checkpoint.json']
    store.release_job_resources('tenant', 'business', job['id'])
    assert store.delete_input('tenant', 'business', source['id'])['deleted'] is True
    first_artifact = manifest['files'][0]['artifact_id']
    expect_code('resource_retained', lambda: store.delete_artifact('tenant', 'business', first_artifact))
    expect_code('resource_retained', lambda: store.delete_checkpoint('tenant', 'business', ref['checkpoint_id']))
    recovery = store.save_recovery('tenant', 'business', job['id'],
        {'phase': 'candidate', 'checkpoint_id': ref['checkpoint_id']})
    store.cancel('tenant', 'business', replay['id'])
    store.release_job_resources('tenant', 'business', replay['id'])
    # Reinitialization must not resurrect the explicitly released references.
    store.initialize()
    expect_code('resource_retained', lambda: store.delete_checkpoint('tenant', 'business', ref['checkpoint_id']))
    expect_code('recovery_revision_conflict', lambda: store.save_recovery('tenant', 'business', job['id'], {}))
    store.save_recovery('tenant', 'business', job['id'], {}, expected_revision=recovery['revision'])
    assert store.delete_checkpoint('tenant', 'business', ref['checkpoint_id'])['deleted'] is True
    assert store.delete_artifact('tenant', 'business', first_artifact)['deleted'] is True
    expect_code('not_found', lambda: store.get_artifact('tenant', 'business', first_artifact))


@pytest.mark.parametrize('source,target,allowed', [
    ('ppt.plan_initial.v1', 'ppt.plan_options.v1', True),
    ('ppt.plan_options.v1', 'ppt.prepare.v1', True),
    ('ppt.prepare.v1', 'ppt.generate.v1', True),
    ('ppt.generate.v1', 'ppt.revise.v1', True),
    ('ppt.revise.v1', 'ppt.revise.v1', True),
    ('ppt.plan_initial.v1', 'ppt.generate.v1', False),
    ('ppt.generate.v1', 'research.generate.v1', False),
])
def test_checkpoint_stage_graph_and_bundle_identity_are_enforced(source, target, allowed):
    initial = binding(profile=source, profile_revision='v1', contract_sha256='1' * 64)
    changed = binding(profile=target, profile_revision='v1', engine='hermes',
        binding_id='target-hermes', contract_sha256='1' * 64 if source == target else '7' * 64)
    manifest = checkpoint_manifest(job_id=str(uuid.uuid4()), attempt_id=str(uuid.uuid4()),
        profile=source, profile_revision='v1', contract_sha256=initial.contract_sha256,
        execution_binding=initial.model_dump(mode='json'), artifacts=[])
    target_pins = policy(changed).pins()
    if allowed:
        assert compatible_checkpoint(manifest, target_pins) is True
    else:
        expect_code('checkpoint_format_incompatible', lambda: compatible_checkpoint(manifest, target_pins))
    altered = policy(binding(profile=target, profile_revision='v1', bundle_sha256='8' * 64)).pins()
    expect_code('checkpoint_format_incompatible', lambda: compatible_checkpoint(manifest, altered))


def test_staging_exposes_exactly_one_readonly_mount_subtree_and_rejects_aliases(tmp_path):
    files = [InlineFile.from_bytes('inputs/source.bin', b'\x00data')]
    attempt = str(uuid.uuid4())
    staged = stage_inputs(tmp_path / 'sources', attempt, files, max_bytes=100, max_files=2,
                          subtree='inputs', require_root=False)
    assert [path.name for path in staged.root.iterdir()] == ['inputs']
    assert (staged.root / 'inputs/source.bin').read_bytes() == b'\x00data'
    staged.cleanup()
    checkpoint = stage_inputs(tmp_path / 'checkpoint', attempt,
        [InlineFile.from_bytes('project/asset.bin', b'')], max_bytes=100, max_files=2,
        subtree='base', require_root=False)
    assert [path.name for path in checkpoint.root.iterdir()] == ['base']
    assert (checkpoint.root / 'base/project/asset.bin').is_file()
    checkpoint.cleanup()
    expect_code('duplicate_file_path', lambda: stage_inputs(tmp_path / 'collision', attempt,
        [InlineFile.from_bytes('source.bin', b'a'), InlineFile.from_bytes('inputs/source.bin', b'b')],
        max_bytes=100, max_files=2, subtree='inputs', require_root=False))


def test_retired_agent_and_preview_uids_cannot_cross_columns_between_attempts(store):
    configured = policy()
    job, _ = store.submit('tenant', 'business', 'uid-one', request(configured), configured.pins())
    claim = store.claim()
    envelope = allocation_envelope(claim, execution_binding=configured.execution_binding,
                                   **configured.execution_limits())
    resource = ('pool', 'agent-workers', 'agent-' + claim['attempt_id'], 'a' * 64)
    original = store.reserve_worker(envelope, *resource, agent_uid=70000, aux_uid=70001)
    assert store.reserve_worker(envelope, *resource, agent_uid=70000, aux_uid=70001) == original
    expect_code('invalid_worker_allocation', lambda: store.reserve_worker(envelope, *resource,
        agent_uid=70000, aux_uid=70000))
    store.finish(job['id'], claim['attempt_id'], claim['fence'],
        {'state': 'failed', 'error_code': 'offline-fixture'}, [], stopped=True)
    store.submit('tenant', 'business', 'uid-two', request(configured), configured.pins())
    second = store.claim()
    next_envelope = allocation_envelope(second, execution_binding=configured.execution_binding,
                                        **configured.execution_limits())
    following = ('other-pool', 'agent-workers', 'agent-' + second['attempt_id'], 'b' * 64)
    for agent, preview in ((70001, 70002), (70002, 70000)):
        expect_code('worker_allocation_conflict', lambda: store.reserve_worker(next_envelope,
            *following, agent_uid=agent, aux_uid=preview))
    assert store.reserve_worker(next_envelope, *following, agent_uid=70002, aux_uid=70003)['aux_uid'] == 70003
