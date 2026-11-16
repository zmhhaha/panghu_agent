"""ACL migration through real Store lifecycle; SQLite is not a PG lock test."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, insert, select, text, update
from sqlalchemy.sql import Select

from agent_service.contracts import InlineFile, ServiceError, WorkerResult, digest, sign_receipt, verify_receipt
from agent_service.deploy import migrate_caller_ownership as migration
from agent_service.store import Store, artifacts, attempts, file_tables, inputs, jobs, metadata, slot
from test_binding_handoff import binding, policy


TENANT, SOURCE, TARGET = 'internal', 'old-methods', 'skill-methods'
OLD, NEW = 'video.animation-method-author.v1', 'video.animation-method-author.v2'
KEY = b'offline-only-migration-signing-key'
OPTIONS = dict(tenant=TENANT, source_caller=SOURCE, target_caller=TARGET, allowed_profiles=[OLD])


@pytest.fixture
def store():
    value = Store('sqlite:///:memory:', allow_sqlite=True, clock=lambda: 1000.0)
    value.initialize()
    yield value
    value.close()


def complete(store, key, *, caller=SOURCE, profile=OLD, checkpoint_ref=None):
    configured = replace(policy(binding(profile=profile)), supports_inputs=True)
    source = store.put_input(TENANT, caller, 'source.txt', b'private manuscript not for stdout')
    request = dict(api_version='1', profile=profile, profile_revision='r1',
        business_ref='method-author-' + key, task={'prompt': 'private model instruction'},
        inputs=[dict(input_id=source['id'], sha256=source['sha256'])], timeout_seconds=600)
    if checkpoint_ref:
        request['checkpoint_ref'] = {k: checkpoint_ref[k] for k in ('checkpoint_id', 'sha256', 'format')}
    job, _ = store.submit(TENANT, caller, key, request, configured.pins())
    claim = store.claim()
    assert claim['id'] == job['id']
    with store._tx() as conn:
        envelope = store._worker_envelope(conn, store._find(conn, job['id']))
    result = WorkerResult(state='completed', artifacts=[InlineFile.from_bytes('result.txt', b'private creative code')])
    result = result.model_copy(update={'stop_receipt': sign_receipt(
        envelope, result, KEY, ('container:offline-fixture',), 1000.0)})
    assert verify_receipt(envelope, result, KEY, 1000.0)
    store.finish(claim['id'], claim['attempt_id'], claim['fence'],
        result.model_dump(mode='json', exclude={'artifacts'}),
        [dict(name='result.txt', content=b'private creative code')], stopped=True)
    return dict(job_id=job['id'], input_id=source['id'], envelope=envelope, result=result)


def snapshot(store):
    with store.engine.connect() as conn:
        return {name: sorted((migration._value(dict(row)) for row in conn.execute(select(table)).mappings()),
            key=lambda row: json.dumps(row, sort_keys=True)) for name, table in metadata.tables.items()}


def check_rejected(store, code, action):
    before = snapshot(store)
    with pytest.raises(ServiceError) as exc:
        action()
    assert exc.value.code == 'caller_migration_' + code
    assert snapshot(store) == before


def test_read_only_plan_and_apply_preserve_all_frozen_evidence_and_move_resource_closure(store):
    one = complete(store, 'method-1')
    checkpoint1, _ = store.create_checkpoint(TENANT, SOURCE, one['job_id'], 'cp-1')
    two = complete(store, 'method-2', checkpoint_ref=checkpoint1)
    checkpoint2, _ = store.create_checkpoint(TENANT, SOURCE, two['job_id'], 'cp-2')
    store.save_recovery(TENANT, SOURCE, two['job_id'],
        dict(phase='observed', checkpoint_id=checkpoint2['checkpoint_id'], data={'private': 'retain me'}))
    artifact_manifest = store.get_artifact_manifest(TENANT, SOURCE, one['job_id'])
    store.acknowledge_publication(TENANT, SOURCE, one['job_id'], 'delivery-1',
        dict(manifest_sha256=artifact_manifest['sha256'], delivery_ref='private-delivery-reference'))
    unused = store.put_input(TENANT, SOURCE, 'unused.txt', b'orphan data')
    target = complete(store, 'target-existing', caller=TARGET, profile=NEW)
    foreign = complete(store, 'foreign', caller='other-business', profile=NEW)
    before = snapshot(store)
    sql = []
    def observed(conn, cursor, statement, params, context, many):
        sql.append(statement)
    event.listen(store.engine, 'before_cursor_execute', observed)
    try:
        planned = migration.plan(store.engine, **OPTIONS)
    finally:
        event.remove(store.engine, 'before_cursor_execute', observed)
    assert snapshot(store) == before
    assert not any(s.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER')) for s in sql)
    assert planned['counts'] == dict(agent_jobs=2, agent_inputs=3, agent_checkpoints=2)
    public = json.dumps(planned)
    assert all(secret not in public for secret in ['private manuscript', 'private model instruction',
                                                  'private creative code', 'private-delivery-reference', 'retain me'])
    receipt = migration.apply(store.engine, expected_plan_sha256=planned['sha256'], **OPTIONS)
    assert receipt['status'] == 'applied'
    assert receipt['database_after_sha256'] == planned['projected_database_sha256']
    after = snapshot(store)
    expected = deepcopy(before)
    for table in (jobs, inputs, file_tables['checkpoints']):
        for row in expected[table.name]:
            if row['tenant'] == TENANT and row['caller'] == SOURCE:
                row['caller'] = TARGET
        expected[table.name].sort(key=lambda row: json.dumps(row, sort_keys=True))
    assert after == expected
    assert store.get_job(TENANT, TARGET, one['job_id'])['request']['profile'] == OLD
    assert store.get_job(TENANT, TARGET, target['job_id'])['request']['profile'] == NEW
    assert store.get_job(TENANT, 'other-business', foreign['job_id'])['request']['profile'] == NEW
    assert store.get_input(TENANT, TARGET, unused['id'])['content'] == b'orphan data'
    assert store.get_checkpoint(TENANT, TARGET, checkpoint2['checkpoint_id']) == checkpoint2
    assert store.get_artifact_manifest(TENANT, TARGET, one['job_id']) == artifact_manifest
    assert store.recovery(TENANT, TARGET, two['job_id'])['document']['checkpoint_id'] == checkpoint2['checkpoint_id']
    assert store.publication_acknowledgements(TENANT, TARGET, one['job_id'])
    with pytest.raises(ServiceError):
        store.get_job(TENANT, SOURCE, one['job_id'])
    with store._tx() as conn:
        envelope = store._worker_envelope(conn, store._find(conn, two['job_id']))
    assert envelope.request_sha256 == two['envelope'].request_sha256
    assert verify_receipt(envelope, two['result'], KEY, 1000.0)


@pytest.mark.parametrize('change', ['source-input', 'target-input', 'unrelated-caller'])
def test_apply_rechecks_exact_plan_and_rejects_stale_state_without_partial_migration(store, change):
    complete(store, 'source')
    planned = migration.plan(store.engine, **OPTIONS)
    caller = {'source-input': SOURCE, 'target-input': TARGET, 'unrelated-caller': 'other-business'}[change]
    store.put_input(TENANT, caller, 'new.txt', b'new work after plan')
    check_rejected(store, 'plan_changed', lambda: migration.apply(store.engine,
        expected_plan_sha256=planned['sha256'], **OPTIONS))


@pytest.mark.parametrize('kind', ['job', 'checkpoint'])
def test_destination_idempotency_collisions_are_rejected_without_renaming_frozen_keys(store, kind):
    one = complete(store, 'source-key')
    two = complete(store, 'source-key' if kind == 'job' else 'target-key', caller=TARGET, profile=NEW)
    if kind == 'checkpoint':
        store.create_checkpoint(TENANT, SOURCE, one['job_id'], 'same-key')
        store.create_checkpoint(TENANT, TARGET, two['job_id'], 'same-key')
    check_rejected(store, 'idempotency_conflict', lambda: migration.plan(store.engine, **OPTIONS))


def test_no_source_job_is_silently_skipped_by_profile_filter(store):
    complete(store, 'author')
    complete(store, 'review', profile='video.animation-method-review.v1')
    check_rejected(store, 'profile_outside_scope', lambda: migration.plan(store.engine, **OPTIONS))


@pytest.mark.parametrize('change,code', [
    ('queued', 'execution_not_stopped'), ('running', 'execution_not_stopped'),
    ('quarantined', 'execution_not_stopped'), ('unproven', 'stop_unproven'),
    ('wrong-receipt-job', 'stop_unproven'), ('wrong-attempt', 'attempt_mismatch'),
    ('slot', 'slot_occupied'),
])
def test_active_unknown_or_mismatched_stop_evidence_is_rejected(store, change, code):
    one = complete(store, 'source')
    with store._tx() as conn:
        row = store._find(conn, one['job_id'])
        if change in {'queued', 'running', 'quarantined'}:
            conn.execute(update(jobs).where(jobs.c.id == row['id']).values(state=change))
        elif change == 'wrong-attempt':
            conn.execute(update(jobs).where(jobs.c.id == row['id']).values(fence=row['fence'] + 1))
        elif change == 'slot':
            conn.execute(update(slot).where(slot.c.id == 1).values(job_id=row['id']))
        else:
            outcome = deepcopy(row['outcome'])
            if change == 'unproven':
                outcome['_service_stop_proven'] = False
            else:
                outcome['stop_receipt']['job_id'] = 'not-the-original-job'
            conn.execute(update(jobs).where(jobs.c.id == row['id']).values(outcome=outcome))
    check_rejected(store, code, lambda: migration.plan(store.engine, **OPTIONS))


@pytest.mark.parametrize('change', ['foreign-input-reference', 'source-foreign-input',
                                   'foreign-checkpoint', 'foreign-retention'])
def test_cross_owner_resource_graph_is_rejected(store, change):
    one = complete(store, 'source')
    two = complete(store, 'other', caller='other-business')
    with store._tx() as conn:
        if change == 'foreign-input-reference':
            row = store._find(conn, two['job_id'])
            source = store._find(conn, one['job_id'])
            request = deepcopy(row['request'])
            request['inputs'] = source['request']['inputs']
            conn.execute(update(jobs).where(jobs.c.id == row['id']).values(request=request, request_hash=digest(request)))
        elif change == 'source-foreign-input':
            conn.execute(update(inputs).where(inputs.c.id == one['input_id']).values(caller='other-business'))
        elif change == 'foreign-checkpoint':
            artifact_id = conn.execute(select(artifacts.c.id).where(artifacts.c.job_id == one['job_id'])).scalar_one()
            manifest = dict(parent_checkpoint_id=None, files=[dict(artifact_id=artifact_id)])
            conn.execute(insert(file_tables['checkpoints']).values(id='offline-foreign-cp', tenant=TENANT,
                caller='other-business', idempotency_key='foreign-cp', request_sha256='a' * 64,
                source_job_id=two['job_id'], manifest=manifest, created_at=1000.0))
        else:
            conn.execute(insert(file_tables['references']).values(resource_kind='input', resource_id=one['input_id'],
                owner_kind='job_input', owner_id=two['job_id']))
    check_rejected(store, 'reference_not_closed', lambda: migration.plan(store.engine, **OPTIONS))


@pytest.mark.parametrize('claimed', [False, True], ids=['cancelled-in-queue', 'uncreated-kubernetes-attempt'])
def test_authoritative_never_started_jobs_can_move_without_fabricating_stop_receipts(store, claimed):
    configured = policy(binding(profile=OLD))
    job, _ = store.submit(TENANT, SOURCE, 'never-started',
        dict(api_version='1', profile=OLD, profile_revision='r1', business_ref='offline',
             task={'prompt': 'none'}, inputs=[], timeout_seconds=600), configured.pins())
    if claimed:
        row = store.claim(provisioning={'pi': 'kubernetes_per_attempt'})
        with store._tx() as conn:
            envelope = store._worker_envelope(conn, store._find(conn, row['id']))
        assert store.resolve_uncreated_attempt(envelope)['state'] == 'failed'
    else:
        store.cancel(TENANT, SOURCE, job['id'])
    planned = migration.plan(store.engine, **OPTIONS)
    migration.apply(store.engine, expected_plan_sha256=planned['sha256'], **OPTIONS)
    assert store.get_job(TENANT, TARGET, job['id'])['outcome']['stop_receipt'] is None


def test_unexpected_trigger_mutation_rolls_back_every_acl_update(store):
    complete(store, 'source')
    with store.engine.begin() as conn:
        conn.exec_driver_sql("CREATE TRIGGER no_hidden_changes AFTER UPDATE OF caller ON agent_inputs "
            "BEGIN UPDATE agent_inputs SET name='unexpected' WHERE id=NEW.id; END")
    planned = migration.plan(store.engine, **OPTIONS)
    check_rejected(store, 'unexpected_change', lambda: migration.apply(store.engine,
        expected_plan_sha256=planned['sha256'], **OPTIONS))


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / 'not-created.db'
    engine = create_engine('sqlite:///' + str(path))
    try:
        with pytest.raises(ServiceError, match='caller_migration_database_missing'):
            migration.plan(engine, **OPTIONS)
        assert not path.exists()
    finally:
        engine.dispose()


def test_cli_defaults_to_plan_without_initializing_or_dispatching(store, monkeypatch, capsys):
    complete(store, 'source')
    def forbidden():
        pytest.fail('Migration must not initialize or dispatch')
    monkeypatch.setattr(store, 'initialize', forbidden)
    monkeypatch.setattr(store, 'close', lambda: None)
    monkeypatch.setattr('agent_service.config.load_configuration', lambda *args, **kwargs:
                        SimpleNamespace(build_store=lambda: store))
    args = ['--config', 'trusted-config.json', '--secrets', 'trusted-secrets.json', '--tenant', TENANT,
            '--source-caller', SOURCE, '--target-caller', TARGET, '--allowed-profile', OLD]
    assert migration.main(args) == 0
    result = capsys.readouterr()
    planned = json.loads(result.out)
    assert planned['schema'] == migration.SCHEMA and result.err == ''
    assert store.get_job(TENANT, SOURCE, planned['records']['agent_jobs'][0]['id'])


def test_cli_sanitizes_driver_or_configuration_failures(monkeypatch, capsys):
    def unavailable(*args, **kwargs):
        raise RuntimeError('postgresql://operator:private-password@private-host/database')
    monkeypatch.setattr('agent_service.config.load_configuration', unavailable)
    args = ['--config', 'trusted-config.json', '--secrets', 'trusted-secrets.json', '--tenant', TENANT,
            '--source-caller', SOURCE, '--target-caller', TARGET, '--allowed-profile', OLD]
    assert migration.main(args) == 2
    output = capsys.readouterr()
    assert output.out == ''
    assert json.loads(output.err) == dict(status='rejected', code='caller_migration_unavailable')
    assert 'private-password' not in output.err


def test_every_migration_table_scan_uses_one_row_server_side_batches(store):
    complete(store, 'source')
    scans = []
    def observed(conn, clause, multiparams, params, options):
        if isinstance(clause, Select):
            assert options.get('stream_results') is True
            assert options.get('yield_per') == 1
            scans.append(tuple(clause.selected_columns.keys()))
    event.listen(store.engine, 'before_execute', observed)
    try:
        planned = migration.plan(store.engine, **OPTIONS)
        migration.apply(store.engine, expected_plan_sha256=planned['sha256'], **OPTIONS)
    finally:
        event.remove(store.engine, 'before_execute', observed)
    assert len(scans) > 30
    # Whole-row scans remain required for source hashes and complete database
    # fingerprints, while relation/stop reads do not fetch arbitrary outcomes.
    assert any('stop_receipt' in columns and 'outcome' not in columns for columns in scans)
    assert any('provisioning' in columns and 'outcome' not in columns for columns in scans)


def test_incremental_row_hash_matches_original_canonical_digest_with_large_fields():
    row = dict(id='offline', content=b'\x00\xff' * 524288,
        payload={'unicode': '中文😀\n"\\' * 40000, 'items': [True, None, 1, 1.25, False]})
    assert migration._row_digest(row) == digest(migration._value(row))


@pytest.mark.parametrize('marker', [1, 'true'])
def test_minimal_stop_projection_preserves_exact_json_boolean_requirement(store, marker):
    one = complete(store, 'source')
    with store._tx() as conn:
        row = store._find(conn, one['job_id'])
        outcome = deepcopy(row['outcome'])
        outcome['_service_stop_proven'] = marker
        conn.execute(update(jobs).where(jobs.c.id == row['id']).values(outcome=outcome))
    check_rejected(store, 'stop_unproven', lambda: migration.plan(store.engine, **OPTIONS))
