"""One-shot caller ACL migration; never rewrites accepted execution evidence.

The default action is a read-only plan. Applying requires its exact digest and
changes only caller columns on jobs, inputs and checkpoints, in one Store-lock
transaction. It does not initialize storage, dispatch work or grant profiles.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sys

from sqlalchemy import inspect, select, text, update

from agent_service.api import _public_stop_proof
from agent_service.contracts import ServiceError, digest
from agent_service.store import (attempts, file_tables, inputs, jobs, metadata, slot,
                                 worker_allocations)


SCHEMA = 'agent-caller-ownership-migration.v1'
_STOPPED = frozenset({'completed', 'needs_input', 'failed', 'cancelled', 'timed_out'})
_OWNED = (jobs, inputs, file_tables['checkpoints'])
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _require(value, code):
    if not value:
        raise ServiceError('caller_migration_' + code, 409)


def _parameters(tenant, source_caller, target_caller, allowed_profiles):
    for value in (tenant, source_caller, target_caller):
        _require(type(value) is str and 1 <= len(value) <= 128
                 and all(ord(c) >= 32 and ord(c) != 127 for c in value), 'identity_invalid')
    _require(source_caller != target_caller, 'identity_invalid')
    _require(isinstance(allowed_profiles, (list, tuple, set, frozenset)) and allowed_profiles
             and all(type(p) is str and re.fullmatch(r'[a-z][a-z0-9_.-]{0,79}', p)
                     for p in allowed_profiles), 'profiles_required')
    return dict(tenant=tenant, source_caller=source_caller, target_caller=target_caller,
                allowed_profiles=sorted(set(allowed_profiles)))


@contextmanager
def _transaction(engine, *, write):
    _require(engine.dialect.name in {'sqlite', 'postgresql'}, 'database_unsupported')
    if engine.dialect.name == 'sqlite' and engine.url.database not in {None, '', ':memory:'}:
        _require(Path(engine.url.database).is_file(), 'database_missing')
    with engine.connect() as conn:
        sqlite = engine.dialect.name == 'sqlite'
        if sqlite:
            conn.exec_driver_sql('PRAGMA query_only=' + ('OFF' if write else 'ON'))
            conn.commit()
            conn.exec_driver_sql('BEGIN IMMEDIATE' if write else 'BEGIN')
        else:
            conn.begin()
            if write:
                # Same global mutation lock as Store._tx().
                conn.execute(text('SELECT pg_advisory_xact_lock(73124001)'))
            else:
                conn.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        try:
            yield conn
            if write:
                conn.commit()
            else:
                conn.rollback()
        except BaseException:
            conn.rollback()
            raise
        finally:
            if sqlite:
                conn.rollback()
                conn.exec_driver_sql('PRAGMA query_only=OFF')
                conn.commit()


def _value(value):
    # Blob bytes are measured, never copied into a plan or terminal output.
    if isinstance(value, (bytes, bytearray, memoryview)):
        return dict(type='blob', size=len(value), sha256=hashlib.sha256(value).hexdigest())
    if isinstance(value, dict):
        return {key: _value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_value(item) for item in value]
    return value


def _fingerprints(conn, parameters):
    actual = set(inspect(conn).get_table_names())
    _require(actual == set(metadata.tables), 'schema_changed')
    before, projected = {}, {}
    for name, table in sorted(metadata.tables.items()):
        # The trusted schema is fixed; this tool must be reviewed again when
        # storage adds another table rather than guessing its ownership rules.
        columns = inspect(conn).get_columns(name)
        _require({c['name'] for c in columns} == set(table.c.keys()), 'schema_changed')
        schema = [dict(name=c['name'], type=str(c['type']), nullable=c['nullable'],
                       default=c.get('default'), primary_key=bool(c.get('primary_key')))
                  for c in columns]
        rows, moved_rows = [], []
        for raw in conn.execute(select(table)).mappings():
            row = dict(raw)
            rows.append(digest(_value(row)))
            if (table in _OWNED and row['tenant'] == parameters['tenant']
                    and row['caller'] == parameters['source_caller']):
                row['caller'] = parameters['target_caller']
            moved_rows.append(digest(_value(row)))
            _require(len(rows) <= 1000000, 'database_limit')
        before[name] = dict(rows=len(rows), schema_sha256=digest(schema), rows_sha256=digest(sorted(rows)))
        projected[name] = dict(rows=len(rows), schema_sha256=digest(schema), rows_sha256=digest(sorted(moved_rows)))
    return before, projected


def _stopped(conn, row):
    _require(row['state'] in _STOPPED, 'execution_not_stopped')
    active_attempts = [dict(r) for r in conn.execute(select(attempts).where(
        attempts.c.job_id == row['id'])).mappings()]
    _require(all(r['state'] in _STOPPED for r in active_attempts), 'execution_not_stopped')
    if row['attempt_id'] is None:
        _require(row['fence'] == 0 and not active_attempts and row['state'] in {'cancelled', 'timed_out'}
                 and _public_stop_proof(row, row['outcome'] or {}).get('not_started') is True,
                 'stop_unproven')
        return
    matching = [a for a in active_attempts if a['id'] == row['attempt_id']
                and a['fence'] == row['fence'] and a['state'] == row['state']]
    _require(len(matching) == 1, 'attempt_mismatch')
    if _public_stop_proof(row, row['outcome'] or {}).get('stop_proven') is True:
        return
    outcome = row['outcome'] or {}
    allocation = conn.execute(select(worker_allocations).where(
        worker_allocations.c.attempt_id == row['attempt_id'])).mappings().first()
    _require(outcome.get('_service_not_started') is True
        and row['state'] in {'failed', 'cancelled', 'timed_out'}
        and matching[0]['dispatch_intent'].get('provisioning') == 'kubernetes_per_attempt'
        and (allocation is None or (
            allocation['job_id'] == row['id'] and allocation['fence'] == row['fence']
            and allocation['retired'] and not allocation['create_claimed']
            and not allocation['worker_start_claimed'] and allocation['pod_uid'] is None
            and allocation['candidate_result'] is None and allocation['stop_observed_at'] is None)),
        'stop_unproven')


def _closed_graph(conn, parameters, source_jobs, source_inputs, source_checkpoints):
    all_jobs = {r['id']: dict(r) for r in conn.execute(select(jobs)).mappings()}
    all_inputs = {r.id: (r.tenant, r.caller, r.sha256) for r in conn.execute(
        select(inputs.c.id, inputs.c.tenant, inputs.c.caller, inputs.c.sha256))}
    checkpoints = file_tables['checkpoints']
    all_checkpoints = {r['id']: dict(r) for r in conn.execute(select(checkpoints)).mappings()}
    from agent_service.store import artifacts
    artifact_jobs = {r[0]: r[1] for r in conn.execute(select(artifacts.c.id, artifacts.c.job_id))}
    source_artifacts = {key for key, value in artifact_jobs.items() if value in source_jobs}
    for job_id, row in all_jobs.items():
        owned = job_id in source_jobs
        for ref in row['request'].get('inputs', []):
            identifier = ref['input_id']
            if owned or identifier in source_inputs:
                _require(owned and identifier in source_inputs and identifier in all_inputs
                    and all_inputs[identifier] == (parameters['tenant'], parameters['source_caller'], ref['sha256']),
                    'reference_not_closed')
        reference = row['request'].get('checkpoint_ref')
        if reference and (owned or reference['checkpoint_id'] in source_checkpoints):
            _require(owned and reference['checkpoint_id'] in source_checkpoints
                and all_checkpoints[reference['checkpoint_id']]['manifest']['sha256'] == reference['sha256'],
                'reference_not_closed')
    for checkpoint_id, row in all_checkpoints.items():
        owned = checkpoint_id in source_checkpoints
        parent = row['manifest'].get('parent_checkpoint_id')
        if owned or row['source_job_id'] in source_jobs or parent in source_checkpoints:
            _require(owned and row['source_job_id'] in source_jobs
                and (parent is None or parent in source_checkpoints)
                and all(artifact_jobs.get(f['artifact_id']) in source_jobs for f in row['manifest']['files']),
                'reference_not_closed')
        elif any(f['artifact_id'] in source_artifacts for f in row['manifest']['files']):
            _require(False, 'reference_not_closed')
    for row in conn.execute(select(file_tables['recoveries'])).mappings():
        checkpoint_id = row['document'].get('checkpoint_id')
        if checkpoint_id and (row['job_id'] in source_jobs or checkpoint_id in source_checkpoints):
            _require(row['job_id'] in source_jobs and checkpoint_id in source_checkpoints,
                     'reference_not_closed')
    for row in conn.execute(select(file_tables['references'])).mappings():
        kind, identifier, owner_kind, owner_id = (row[k] for k in
            ('resource_kind', 'resource_id', 'owner_kind', 'owner_id'))
        affected = ((kind == 'input' and identifier in source_inputs)
            or (kind == 'checkpoint' and identifier in source_checkpoints)
            or (kind == 'artifact' and identifier in source_artifacts))
        if affected:
            expected = ({'job_input': source_jobs} if kind == 'input' else
                {'job_checkpoint': source_jobs, 'business_recovery': source_jobs,
                 'checkpoint_parent': source_checkpoints} if kind == 'checkpoint' else
                {'job_output': source_jobs, 'checkpoint': source_checkpoints})
            _require(owner_id in expected.get(owner_kind, set()), 'reference_not_closed')


def _plan(conn, parameters):
    # Whole old ownership scope: no silently filtered or skipped source jobs.
    sources, records = {}, {}
    for table in _OWNED:
        sources[table.name], records[table.name] = [], []
        statement = select(table).where(table.c.tenant == parameters['tenant'],
            table.c.caller == parameters['source_caller']).execution_options(stream_results=True)
        for raw in conn.execute(statement).mappings():
            row = dict(raw)
            records[table.name].append(dict(id=row['id'], sha256=digest(_value(row))))
            if table is inputs:
                row.pop('content')
            sources[table.name].append(row)
    source_jobs = sources[jobs.name]
    _require(source_jobs, 'source_empty')
    _require(all(r['request'].get('profile') in parameters['allowed_profiles']
        and r['pins'].get('profile') == r['request']['profile'] for r in source_jobs), 'profile_outside_scope')
    affected_jobs = conn.execute(select(jobs).where(jobs.c.tenant == parameters['tenant'],
        jobs.c.caller.in_([parameters['source_caller'], parameters['target_caller']]))).mappings()
    affected_ids = set()
    for row in affected_jobs:
        affected_ids.add(row['id'])
        _stopped(conn, dict(row))
    _require(not any(r.job_id in affected_ids for r in conn.execute(select(slot.c.job_id))), 'slot_occupied')
    for table in (jobs, file_tables['checkpoints']):
        target_keys = set(conn.execute(select(table.c.idempotency_key).where(
            table.c.tenant == parameters['tenant'], table.c.caller == parameters['target_caller'])).scalars())
        _require(not target_keys.intersection(r['idempotency_key'] for r in sources[table.name]),
                 'idempotency_conflict')
    identifiers = {name: {r['id'] for r in rows} for name, rows in sources.items()}
    _closed_graph(conn, parameters, identifiers[jobs.name], identifiers[inputs.name],
                  identifiers[file_tables['checkpoints'].name])
    before, projected = _fingerprints(conn, parameters)
    body = dict(schema=SCHEMA, **parameters,
        records={name: sorted(rows, key=lambda r: r['id']) for name, rows in records.items()},
        counts={name: len(rows) for name, rows in sources.items()},
        database_sha256=digest(before), projected_database_sha256=digest(projected))
    return dict(body, sha256=digest(body))


def plan(engine, *, tenant, source_caller, target_caller, allowed_profiles):
    """Read-only consistent plan. Does not create tables or initialize a Store."""
    parameters = _parameters(tenant, source_caller, target_caller, allowed_profiles)
    with _transaction(engine, write=False) as conn:
        return _plan(conn, parameters)


def apply(engine, *, expected_plan_sha256, tenant, source_caller, target_caller, allowed_profiles):
    """Recheck and atomically transfer ACL rows, preserving every other field."""
    parameters = _parameters(tenant, source_caller, target_caller, allowed_profiles)
    _require(type(expected_plan_sha256) is str and _SHA.fullmatch(expected_plan_sha256), 'plan_required')
    with _transaction(engine, write=True) as conn:
        current = _plan(conn, parameters)
        _require(current['sha256'] == expected_plan_sha256, 'plan_changed')
        moved = {}
        for table in _OWNED:
            result = conn.execute(update(table).where(table.c.tenant == tenant,
                table.c.caller == source_caller).values(caller=target_caller))
            _require(result.rowcount == current['counts'][table.name], 'row_count_changed')
            moved[table.name] = result.rowcount
        after, _ = _fingerprints(conn, parameters)
        _require(digest(after) == current['projected_database_sha256'], 'unexpected_change')
        return dict(schema=SCHEMA, status='applied', plan_sha256=current['sha256'], counts=moved,
                    database_sha256=current['database_sha256'], database_after_sha256=digest(after))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'apply'), nargs='?', default='plan')
    parser.add_argument('--config', required=True)
    parser.add_argument('--secrets', required=True)
    parser.add_argument('--tenant', required=True)
    parser.add_argument('--source-caller', required=True)
    parser.add_argument('--target-caller', required=True)
    parser.add_argument('--allowed-profile', action='append', required=True)
    parser.add_argument('--expected-plan-sha256')
    parser.add_argument('--allow-sqlite', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'apply' and not args.expected_plan_sha256:
        parser.error('apply requires --expected-plan-sha256')
    if args.action == 'plan' and args.expected_plan_sha256:
        parser.error('plan does not take --expected-plan-sha256')
    store = None
    try:
        from agent_service.config import load_configuration
        config = load_configuration(args.config, args.secrets, allow_sqlite=args.allow_sqlite)
        store = config.build_store()  # Intentionally no initialize()/dispatcher.
        options = dict(tenant=args.tenant, source_caller=args.source_caller,
                       target_caller=args.target_caller, allowed_profiles=args.allowed_profile)
        result = (plan(store.engine, **options) if args.action == 'plan' else
                  apply(store.engine, expected_plan_sha256=args.expected_plan_sha256, **options))
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except ServiceError as exc:
        print(json.dumps(dict(status='rejected', code=exc.code)), file=sys.stderr)
        return 2
    except Exception:
        # SQL/driver/config errors can contain connection strings or content.
        print(json.dumps(dict(status='rejected', code='caller_migration_unavailable')), file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


if __name__ == '__main__':
    raise SystemExit(main())
