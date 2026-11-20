"""Reuse real local stopped packages; never substitute a new native receipt."""
import json
import hashlib
import time

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.production_run_store import packed
from video_workbench.hyperframes_film_snapshot import decode_film_execution_snapshot
from fastapi.testclient import TestClient
from video_workbench.app import create_app
from test_production_film_runs import env, finish_module, children, OWNER


def start(env, key, source=None):
    project = env.store.project(OWNER, env.project['id'])
    request = dict(expected_revision=project['revision'], joint_plan_version_id='adopted-plan-1',
        audio_mode='silent')
    if source is not None:
        request['reuse_completed_prefix_from_run_id'] = source
    return env.runs.start(OWNER, project['id'], request, key)


def failed_prefix(env):
    run = start(env, 'original-failed-silent-film')
    env.runs.tick()
    first = children(env, 'film_module_operations')[0]
    finish_module(env, first['id'])
    env.runs.tick()
    second = children(env, 'film_module_operations')[1]
    env.creation.update_operation('module', second['id'], status='timed_out', error='agent_timed_out',
        stop_proven=True, finished_at=time.time())
    env.runs.tick()
    state = env.runs.get(OWNER, run['id'])
    assert state['status'] == 'failed' and not state['draining']
    return state


def document(env, run_id):
    with env.store.transaction() as db:
        return env.runs._read(db, run_id)


def rewrite(env, run):
    with env.store.transaction() as db:
        env.runs._write(db, run)


def tamper_child(env, operation_id, **values):
    with env.store.transaction() as db:
        child = json.loads(db.execute('SELECT document FROM film_module_operations WHERE id=?',
            (operation_id,)).fetchone()[0])
        child.update(values)
        db.execute('UPDATE film_module_operations SET document=? WHERE id=?', (packed(child), operation_id))


def custody(env):
    with env.store.transaction() as db:
        return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table)]
            for table in ('production_runs', 'film_module_operations', 'film_module_versions')}


def test_reuse_retains_original_prefix_and_targets_authors_only_fresh_suffix_then_one_snapshot(env):
    source = failed_prefix(env)
    before = custody(env)
    original = document(env, source['id'])
    first = original['items'][0]
    package = env.creation.module_package(OWNER, first['module_version_id'])
    run = start(env, 'reuse-the-stopped-prefix', source['id'])
    receipt = run['module_reuse']
    assert receipt['source_run_id'] == source['id'] and receipt['source_run_sha256'] == digest(original)
    assert receipt['prefix_count'] == 1 and receipt['lineage_run_ids'] == [source['id']]
    assert run['module_work_budget'] == dict(reused_scene_modules=1, new_scene_modules=1,
        whole_film_renders=1, new_recordings=0)
    reused = document(env, run['id'])
    assert reused['module_targets'] == original['module_targets']
    assert reused['items'][0] == first
    assert reused['intents'] == {'module:0': original['intents']['module:0']}
    assert all(reused['items'][1][key] is None for key in
        ('module_operation_id', 'module_version_id', 'module_sha256', 'module_package_sha256'))
    assert start(env, 'reuse-the-stopped-prefix', source['id'])['id'] == run['id']
    after_get = custody(env)
    for _ in range(2):
        env.runs.get(OWNER, run['id']);env.runs.list(OWNER, env.project['id'])
    assert custody(env) == after_get
    env.runs.tick()
    operations = children(env, 'film_module_operations')
    assert len(operations) == 3 and not children(env, 'audio_operations')
    suffix = operations[-1]
    assert suffix['id'] != original['items'][1]['module_operation_id']
    assert suffix['request_key'] == 'production-run:' + run['id'] + ':module:1'
    assert suffix['request']['predecessor_version_id'] == first['module_version_id']
    assert suffix['request']['target'] == original['module_targets'][1]
    finish_module(env, suffix['id'])
    env.runs.tick()
    renders = children(env, 'film_render_operations')
    assert len(renders) == 1 and renders[0]['request']['production_run_id'] == run['id']
    snapshot = env.creation.render_snapshot(renders[0]['id'])
    files = decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'],
        runtime_fingerprint=env.creation.runtime, trusted_host_sha256=env.creation.hosts, image=env.creation.image)
    assert snapshot['production_run_id'] == run['id'] and snapshot['recording_sha256'] == {}
    for path, text in package['result']['module']['files'].items():
        assert files['project/' + path] == text.encode()
    after = custody(env)
    assert after['production_runs'][0] == before['production_runs'][0]
    assert after['film_module_operations'][:2] == before['film_module_operations']
    assert after['film_module_versions'][0] == before['film_module_versions'][0]


@pytest.mark.parametrize('change', ['not_failed', 'draining', 'blocked', 'render', 'missing_child',
    'unstopped', 'wrong_package', 'wrong_source', 'wrong_target', 'wrong_configuration', 'wrong_release',
    'hole', 'resources'])
def test_invalid_source_rejected_before_new_journal_or_child(env, change):
    source = failed_prefix(env)
    run = document(env, source['id'])
    if change == 'not_failed':run['status'] = 'completed'
    elif change == 'draining':run['draining'] = True
    elif change == 'blocked':run['blocked_reason'] = 'unconfirmed'
    elif change == 'render':run['draft_render_id'] = 'a-real-render-is-required'
    elif change == 'wrong_target':run['module_targets'][1]['namespace'] = 'remapped'
    elif change == 'wrong_configuration':run['film_creation_configuration']['configuration_sha256'] = 'c' * 64
    elif change == 'wrong_release':run['library_binding'] = {'release_id': 'different'}
    elif change == 'resources':run['resource_plan'] = {'nodes': []}
    elif change == 'wrong_package':run['items'][0]['module_package_sha256'] = 'f' * 64
    elif change == 'hole':
        run['items'][1]['module_version_id'] = run['items'][0]['module_version_id']
        for key in ('module_version_id', 'module_package_sha256', 'module_input_sha256', 'module_sha256', 'module_engine'):
            run['items'][0][key] = None
        tamper_child(env, run['items'][0]['module_operation_id'], version_id=None, status='timed_out')
    elif change == 'unstopped':
        tamper_child(env, run['items'][1]['module_operation_id'], stop_proven=False)
    elif change == 'missing_child':
        with env.store.transaction() as db:
            db.execute('DELETE FROM film_module_operations WHERE id=?', (run['items'][1]['module_operation_id'],))
    elif change == 'wrong_source':
        with env.store.transaction() as db:
            row = db.execute('SELECT package FROM film_module_versions WHERE id=?',
                (run['items'][0]['module_version_id'],)).fetchone()
            package = json.loads(row[0]);module = package['result']['module']
            module['files'][module['entry']] += '\n// illicit edit\n'
            db.execute('UPDATE film_module_versions SET package=? WHERE id=?',
                (packed(package), run['items'][0]['module_version_id']))
    rewrite(env, run)
    before = custody(env)
    with pytest.raises(ServiceError):start(env, 'reject-invalid-reuse-' + change, source['id'])
    assert custody(env) == before


@pytest.mark.parametrize('change', ['source_receipt', 'operation_receipt', 'reuse_receipt', 'cycle'])
def test_recheck_fails_closed_before_suffix_and_get_does_not_repair_tampering(env, change):
    source = failed_prefix(env)
    result = start(env, 'checked-before-suffix-work', source['id'])
    run = document(env, result['id'])
    if change == 'source_receipt':
        old = document(env, source['id']);old['error'] = 'changed history';rewrite(env, old)
    elif change == 'operation_receipt':
        old = document(env, source['id'])
        tamper_child(env, old['items'][0]['module_operation_id'], deadline_at=time.time() + 9999)
    elif change == 'reuse_receipt':
        run['module_reuse']['items'][0]['input_sha256'] = 'e' * 64
        run['module_reuse_sha256'] = digest(run['module_reuse']);rewrite(env, run)
    else:
        run['module_reuse']['source_run_id'] = run['id']
        run['module_reuse_sha256'] = digest(run['module_reuse']);rewrite(env, run)
    before = custody(env)
    env.runs.get(OWNER, result['id']);env.runs.list(OWNER, env.project['id'])
    assert custody(env) == before
    env.runs.tick()
    assert len(children(env, 'film_module_operations')) == 2
    assert not children(env, 'film_render_operations')
    assert env.runs.get(OWNER, result['id'])['status'] == 'failed'


def test_recheck_also_blocks_export_after_a_paid_suffix_finishes(env):
    source = failed_prefix(env)
    run = start(env, 'check-before-whole-film-export', source['id'])
    env.runs.tick();finish_module(env, children(env, 'film_module_operations')[-1]['id'])
    original = document(env, source['id']);original['finished_at'] += 1;rewrite(env, original)
    env.runs.tick()
    assert len(children(env, 'film_module_operations')) == 3
    assert not children(env, 'film_render_operations')
    assert env.runs.get(OWNER, run['id'])['status'] == 'failed'


def test_wrong_owner_and_audio_or_resource_reuse_cannot_select_prefix(env):
    source = failed_prefix(env)
    request = dict(expected_revision=env.project['revision'], joint_plan_version_id='adopted-plan-1',
        audio_mode='silent', reuse_completed_prefix_from_run_id=source['id'])
    with pytest.raises(ServiceError):
        env.runs.start('another-owner', env.project['id'], request, 'other-owner-prefix')
    for field, value in [('audio_mode', 'narration'), ('resource_plan', {}),
            ('resource_authoring', {}), ('reuse_audio_from_run_id', source['id'])]:
        bad = dict(request, **{field: value})
        with pytest.raises(ServiceError):
            env.runs.start(OWNER, env.project['id'], bad, 'unsupported-prefix-' + field)


def test_http_api_keeps_explicit_reuse_field_and_original_owner_csrf_rules(env):
    source = failed_prefix(env)
    env.facade.stop = lambda: None  # This local child-store facade owns no background writer.
    app = create_app(db_path=env.store.path, local_mode=False, background=False,
        production_runs_enabled=True, trusted_hosts={'testserver'}, film_creation=env.facade)
    project = env.store.project(OWNER, env.project['id'])
    body = dict(expected_revision=project['revision'], joint_plan_version_id='adopted-plan-1',
        audio_mode='silent', reuse_completed_prefix_from_run_id=source['id'])
    url = '/api/projects/' + project['id'] + '/production-runs'
    with TestClient(app) as client:
        client.headers['x-forwarded-user'] = OWNER
        session = client.get('/api/session').json()
        assert client.post(url, json=body, headers={'Idempotency-Key': 'http-prefix-admission'}).status_code == 403
        client.headers['x-csrf-token'] = session['csrf_token']
        response = client.post(url, json=body, headers={'Idempotency-Key': 'http-prefix-admission'})
        assert response.status_code == 202, response.text
        run = response.json()
        assert run['module_reuse']['source_run_id'] == source['id']
        assert run['module_work_budget']['new_scene_modules'] == 1
        assert client.post(url, json=body, headers={'Idempotency-Key': 'http-prefix-admission'}).json()['id'] == run['id']
        before = custody(env)
        assert client.get('/api/production-runs/' + run['id']).status_code == 200
        assert custody(env) == before
        client.headers['x-forwarded-user'] = 'foreign-owner'
        assert client.get('/api/production-runs/' + run['id']).status_code == 404


def test_repeated_failed_continuation_reuses_same_real_prefix_with_bounded_lineage(env, monkeypatch):
    from video_workbench import production_module_reuse as reuse
    source = failed_prefix(env)
    first = start(env, 'first-continuation-lineage', source['id'])
    env.runs.tick()
    suffix = children(env, 'film_module_operations')[-1]
    env.creation.update_operation('module', suffix['id'], status='timed_out', error='agent_timed_out',
        stop_proven=True, finished_at=time.time())
    env.runs.tick()
    assert env.runs.get(OWNER, first['id'])['status'] == 'failed'
    before = custody(env)
    monkeypatch.setattr(reuse, 'MAX_LINEAGE', 2)
    with pytest.raises(ServiceError, match='lineage_invalid'):
        start(env, 'lineage-limit-rejects-no-work', first['id'])
    assert custody(env) == before
    monkeypatch.setattr(reuse, 'MAX_LINEAGE', 16)
    second = start(env, 'second-continuation-lineage', first['id'])
    assert second['module_reuse']['lineage_run_ids'] == [first['id'], source['id']]
    assert second['items'][0]['module_version_id'] == source['items'][0]['module_version_id']
    env.runs.tick()
    assert len(children(env, 'film_module_operations')) == 4
    assert children(env, 'film_module_operations')[-1]['request_key'].startswith('production-run:' + second['id'])


def test_failed_first_child_has_no_prefix_to_reuse(env):
    source = start(env, 'no-completed-prefix-source')
    env.runs.tick()
    child = children(env, 'film_module_operations')[0]
    env.creation.update_operation('module', child['id'], status='timed_out', stop_proven=True,
        finished_at=time.time(), error='agent_timed_out')
    env.runs.tick()
    before = custody(env)
    with pytest.raises(ServiceError, match='prefix_invalid'):
        start(env, 'reject-zero-completed-prefix', source['id'])
    assert custody(env) == before


@pytest.mark.parametrize('key', ['module_reuse', 'module_reuse_sha256', 'module_work_budget'])
def test_orphan_reuse_metadata_is_never_treated_as_ordinary_history(env, key):
    source = failed_prefix(env)
    run = document(env, source['id'])
    run[key] = None
    with pytest.raises(ServiceError):env.runs._require_module_reuse(run)


@pytest.mark.parametrize('change', ['full_configuration', 'child_release', 'request_release', 'missing_asset'])
def test_initial_admission_rechecks_original_configuration_release_and_asset_bytes(env, monkeypatch, change):
    import test_production_film_runs as fixtures
    asset = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0L1 1"/></svg>'
    name = 'assets/custody-fixture.svg'
    checksum = hashlib.sha256(asset).hexdigest()
    env.creation.assets[name] = asset
    env.creation.runtime_capabilities['assets']['custody-fixture.svg'] = checksum
    env.creation.configuration['runtime_capabilities']['assets']['custody-fixture.svg'] = checksum
    env.creation.configuration['sha256'] = digest({k: v for k, v in env.creation.configuration.items() if k != 'sha256'})
    env.facade.identity = env.creation.configuration_sha256
    original_result = fixtures.raw_result
    def with_asset(document):
        result = original_result(document)
        result['module']['assets'] = [name]
        return result
    monkeypatch.setattr(fixtures, 'raw_result', with_asset)
    source = failed_prefix(env)
    run = document(env, source['id'])
    child_id = run['items'][0]['module_operation_id']
    if change == 'missing_asset':
        env.creation.assets[name] = b'different bytes at the same admitted path'
    elif change == 'full_configuration':
        with env.store.transaction() as db:
            child = json.loads(db.execute('SELECT document FROM film_module_operations WHERE id=?', (child_id,)).fetchone()[0])
        child['configuration']['runtime_capabilities']['assets']['custody-fixture.svg'] = 'd' * 64
        tamper_child(env, child_id, configuration=child['configuration'])
    elif change == 'child_release':
        tamper_child(env, child_id, library_binding={'use_wikiskill': True, 'release_id': 'foreign'})
    else:
        with env.store.transaction() as db:
            child = json.loads(db.execute('SELECT document FROM film_module_operations WHERE id=?', (child_id,)).fetchone()[0])
        child['request']['library_binding'] = {'release_id': 'foreign'}
        tamper_child(env, child_id, request=child['request'])
    before = custody(env)
    with pytest.raises(ServiceError):start(env, 'initial-custody-rejection-' + change, source['id'])
    assert custody(env) == before
