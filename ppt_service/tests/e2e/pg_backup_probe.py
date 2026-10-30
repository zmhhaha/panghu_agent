"""Opt-in pg_dump/pg_restore companion for an isolated P5 runner.

The runner alone owns PostgreSQL/container tools. This probe has no Docker
socket, production connection, model transport, pg_dump or pg_restore code.
Seed intentionally retains its dedicated synthetic schema/files for the runner
to dump. Verify requires the exact other test database and a new data copy.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import json
from pathlib import Path
import re
import shutil
import tempfile
from types import SimpleNamespace
import unittest

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from ppt_service.server.app import create_app
from ppt_service.server.auth import Settings
from ppt_service.server.models import now

from .backup_helpers import assert_quiescent, files_inventory, metadata_snapshot
from .harness import AUDIENCE, E2ECase, ISSUER, ORIGIN, test_database_url
from .test_security import parse_sse, seed_synthetic_historical_version


def database_for(action):
    value = test_database_url()
    expected = 'ppt_p5_backup' if action == 'seed' else 'ppt_p5_restore'
    if not value or make_url(value).database != expected:
        raise RuntimeError('P5 backup probe requires its exact dedicated ' + expected + ' database')
    return value


def seed(evidence):
    database_for('seed')
    if evidence.exists():
        raise RuntimeError('Seed evidence directory must be new')
    evidence.mkdir(parents=True)
    previous_tempdir = tempfile.tempdir
    tempfile.tempdir = str(evidence)
    case = E2ECase()
    try:
        case.setUp()
    finally:
        tempfile.tempdir = previous_tempdir
    try:
        pid = case.create_project()['id']
        case.upload(pid)
        body = {'expected_revision': case.project(pid)['revision'], 'phase': 'plan_initial'}
        key = 'pg-restore-plan-request'
        queued = case.client.post(f'/api/v1/projects/{pid}/plans', json=body,
                                 headers=case.headers() | {'Idempotency-Key': key})
        case.assertEqual(queued.status_code, 202)
        job = queued.json()
        case.assertEqual(case.complete_fake_phase(job['id'])['status'], 'succeeded')
        case.confirm(pid, {'design_mode': 'free_design', 'template_id': None})
        first, _ = seed_synthetic_historical_version(case, pid)
        second, _ = seed_synthetic_historical_version(case, pid, parent_version_id=first)
        project = case.project(pid)
        versions = case.client.get(f'/api/v1/projects/{pid}/versions', headers=case.headers()).json()
        events = parse_sse(case.client.get(f'/api/v1/jobs/{job["id"]}/events', headers=case.headers()))
        case.assertFalse(case.worker.running)
        case.assertFalse(case.dispatcher.owned)
        case.client.close()
        assert_quiescent(case.sessions)
        manifest = {
            'schema_version': 1, 'fixture_only': True, 'database_kind': 'postgresql',
            'quiescent': True, 'schema': case.schema, 'source_database': 'ppt_p5_backup',
            'target_database': 'ppt_p5_restore',
            'source_data': case.data_root.relative_to(evidence).as_posix(),
            'files': files_inventory(case.data_root), 'metadata': metadata_snapshot(case.engine),
            'project': project, 'job': job, 'plan_body': body, 'idempotency_key': key,
            'versions': versions, 'events': events,
            'synthetic_versions': [first, second], 'publication_verified': False,
            'powerpoint_acceptance': 'pending',
        }
        (evidence / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding='utf-8')
        print(json.dumps({'status': 'seeded-quiescent', 'schema': case.schema,
                          'manifest': str(evidence / 'manifest.json'),
                          'files': len(manifest['files']), 'tables': len(manifest['metadata']),
                          'model_requests': 0}))
    except BaseException:
        case.doCleanups()
        raise
    finally:
        case.client.close()
        case.engine.dispose()
        case._schema_engine.dispose()
    # Do NOT call registered cleanup after success: this dedicated schema and
    # fixture are precisely what the external isolated runner now has to dump.


def verify(evidence):
    database_url = database_for('verify')
    manifest = json.loads((evidence / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('fixture_only') is not True or manifest.get('database_kind') != 'postgresql'
            or not re.fullmatch(r'p5_[a-f0-9]{32}', manifest.get('schema', ''))):
        raise RuntimeError('Expected the isolated P5 PostgreSQL seed manifest')
    source = (evidence / manifest['source_data']).resolve()
    if not source.is_relative_to(evidence) or source == evidence:
        raise RuntimeError('Seed filesystem must be inside its evidence directory')
    restored = evidence / 'restored-data'
    if restored.exists():
        raise RuntimeError('Restored filesystem must be new')
    if files_inventory(source) != manifest['files']:
        raise RuntimeError('Seed filesystem changed before restore')
    shutil.copytree(source, restored, copy_function=shutil.copy2)
    check = unittest.TestCase()
    engine = create_engine(database_url, connect_args={'options': '-csearch_path=' + manifest['schema']})
    try:
        check.assertEqual(metadata_snapshot(engine), manifest['metadata'])
        check.assertEqual(files_inventory(restored), manifest['files'])
        assert_quiescent(sessionmaker(engine))
        settings = Settings(database_url, str(restored), oidc_issuer=ISSUER,
                            oidc_audience=AUDIENCE, trusted_proxy_cidrs=('127.0.0.1/32',),
                            allowed_origins=(ORIGIN,))
        app = create_app(settings, engine)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        app.state.jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=key.public_key()))
        def token(who='alice'):
            return jwt.encode({'iss': ISSUER, 'sub': who, 'aud': AUDIENCE,
                               'email': who + '@ppt.test', 'email_verified': True,
                               'iat': now(), 'exp': now() + timedelta(minutes=5)}, key, algorithm='RS256')
        with TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 12345)) as client:
            pid = manifest['project']['id']
            check.assertEqual(client.get(f'/api/v1/projects/{pid}').status_code, 401)
            me = client.get('/api/v1/me', headers={'Authorization': 'Bearer ' + token()})
            check.assertEqual(me.status_code, 200)
            headers = {'Authorization': 'Bearer ' + token(), 'Origin': ORIGIN,
                       'X-CSRF-Token': me.json()['csrf_token']}
            check.assertEqual(client.get(f'/api/v1/projects/{pid}', headers=headers).json(), manifest['project'])
            check.assertEqual(client.get(f'/api/v1/projects/{pid}/versions', headers=headers).json(), manifest['versions'])
            events = parse_sse(client.get(f'/api/v1/jobs/{manifest["job"]["id"]}/events', headers=headers))
            check.assertEqual(json.loads(json.dumps(events)), manifest['events'])
            for version in manifest['versions']:
                for route in [version['download_url'], version['slides'][0]['preview_url']]:
                    check.assertEqual(client.get(route, headers=headers).status_code, 200)
                    check.assertEqual(client.get(route, headers={'Authorization': 'Bearer ' + token('bob')}).status_code, 404)
            check.assertEqual(client.get(f'/api/v1/projects/{pid}', headers={
                'Authorization': 'Bearer ' + token('bob')}).status_code, 404)
            replay = client.post(f'/api/v1/projects/{pid}/plans', json=manifest['plan_body'],
                                 headers=headers | {'Idempotency-Key': manifest['idempotency_key']})
            check.assertEqual(replay.status_code, 202)
            check.assertEqual(replay.json(), manifest['job'])
            conflict = client.post(f'/api/v1/projects/{pid}/plans',
                json=manifest['plan_body'] | {'feedback': 'changed request'},
                headers=headers | {'Idempotency-Key': manifest['idempotency_key']})
            check.assertEqual(conflict.status_code, 409)
            check.assertEqual(conflict.json()['detail']['code'], 'idempotency_conflict')
            check.assertEqual(metadata_snapshot(engine), manifest['metadata'])
            write = client.post('/api/v1/projects', json={'title': 'Synthetic restored PG write'}, headers=headers)
            check.assertEqual(write.status_code, 201)
        check.assertEqual(files_inventory(source), manifest['files'])
        report = {'status': 'restored-and-verified', 'database_kind': 'postgresql',
                  'source_database': 'ppt_p5_backup', 'target_database': 'ppt_p5_restore',
                  'schema': manifest['schema'], 'files': len(manifest['files']),
                  'tables': len(manifest['metadata']), 'all_hashes_and_file_metadata_equal': True,
                  'all_database_metadata_equal_before_restore_write': True,
                  'auth_and_cross_owner_acl_verified': True, 'history_and_idempotency_verified': True,
                  'new_instance_writable': True, 'model_requests': 0,
                  'publication_verified': False, 'powerpoint_acceptance': 'pending'}
        (evidence / 'verification.json').write_text(json.dumps(report, indent=2, sort_keys=True), encoding='utf-8')
        print(json.dumps(report))
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('seed', 'verify'))
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    if not evidence.is_relative_to(Path('/evidence').resolve()) or evidence == Path('/evidence').resolve():
        raise RuntimeError('Evidence must be a dedicated child of the isolated /evidence mount')
    (seed if args.action == 'seed' else verify)(evidence)


if __name__ == '__main__':
    main()
