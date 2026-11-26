"""Reuse real local stopped packages; never substitute a new native receipt."""
import json
import hashlib
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.production_run_store import packed
from video_workbench.hyperframes_film_snapshot import decode_film_execution_snapshot
from fastapi.testclient import TestClient
from video_workbench.app import create_app
from video_workbench.film_creation_workflow import FilmCreationWorkflow
from agent_service.profiles.video_animation_film_module import PROFILE
from test_production_film_runs import env, finish_module, children, OWNER
from test_production_runs import seed


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


def full_custody(env):
    with env.store.transaction() as db:
        return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table)]
            for table in ('production_runs', 'film_module_operations', 'film_module_versions',
                'film_render_operations', 'projects')}


class SilentCustodyRenderer:
    """Exercise the real workflow with explicit nonmedia fixture bytes only."""
    def __init__(self):
        self.calls = []

    def render(self, snapshot, directory, **options):
        self.calls.append(options['operation_id'])
        data = b'synthetic-silent-render-custody-not-media'
        path = Path(directory) / 'video.mp4'
        path.write_bytes(data)
        count = snapshot['clock']['frame_count']
        return dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
            snapshot_sha256=snapshot['sha256'], runtime_fingerprint=snapshot['runtime_fingerprint'],
            visual_mode='hyperframes-film', quality='draft', silent=True, contains_narration=False,
            subtitles=False, audio_sample_rate=48000, audio_channels=2, width=snapshot['width'],
            height=snapshot['height'], fps=24, video_codec='h264', audio_codec='aac',
            duration_seconds=count / 24, expected_duration_seconds=count / 24,
            film_export=dict(bundle_sha256=snapshot['bundle_sha256'], film_sha256=snapshot['film_sha256'],
                project_manifest_sha256=snapshot['project_manifest_sha256'], concatenated_scene_videos=False,
                clock=dict(fps=24, frame_count=count, sample_rate=48000, sample_count=count * 2000),
                **{name: 'b' * 64 for name in ('export_receipt_sha256', 'capture_sha256', 'audio_mix_sha256',
                    'job_result_sha256', 'job_binding', 'admission_sha256')}))


def real_film_facade(env):
    gateway = SimpleNamespace(profile=PROFILE, profile_revision='1', preflight=lambda deadline: None)
    env.renderer = SilentCustodyRenderer()
    env.facade = FilmCreationWorkflow(env.creation, gateway, env.adapter, env.renderer,
        enabled=True, render_readiness=lambda: True)
    env.runs.film_creation = env.facade


def failed_whole_film(env, *, count=None):
    if count is not None:
        env.project = seed(env.store, env.joint, count=count)
        with env.store.transaction() as db:
            project = env.store._project(db, OWNER, env.project['id'])
            for scene in project['scenes']:
                scene['duration_seconds'] = 25
            db.execute('DELETE FROM joint_plan_heads WHERE owner=? AND project_id=?', (OWNER, project['id']))
            db.execute('INSERT INTO joint_plan_heads VALUES (?,?,?,?)',
                (OWNER, project['id'], 'adopted-plan-1', env.joint._binding(project)))
            env.store._save(db, project)
        env.project = env.store.project(OWNER, env.project['id'])
    real_film_facade(env)
    run = start(env, 'original-whole-film-render-failure')
    env.runs.tick()
    for _ in env.project['scenes']:
        module = children(env, 'film_module_operations')[-1]
        finish_module(env, module['id'])
        env.runs.tick()
    render = children(env, 'film_render_operations')[-1]
    env.creation.claim_submission('render', render['id'])
    env.creation.update_operation('render', render['id'], status='failed',
        error='hyperframes_film_execution_failed', stop_proven=True, finished_at=time.time())
    env.runs.tick()
    source = document(env, run['id'])
    assert source['status'] == 'failed' and source['phase'] == 'draft'
    assert all(item['module_status'] == 'completed' for item in source['items'])
    return source


def tamper_render(env, render_id, change):
    with env.store.transaction() as db:
        child = json.loads(db.execute('SELECT document FROM film_render_operations WHERE id=?',
            (render_id,)).fetchone()[0])
        change(child)
        db.execute('UPDATE film_render_operations SET document=? WHERE id=?', (packed(child), render_id))


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
    # Exercise the production facade's private-name barrier, not the permissive
    # local fixture facade used by older store-only orchestration tests.
    gateway = SimpleNamespace(profile=PROFILE, profile_revision='1', preflight=lambda deadline: None)
    env.facade = FilmCreationWorkflow(env.creation, gateway, env.adapter, object(),
        enabled=True, render_readiness=lambda: True)
    env.runs.film_creation = env.facade
    source = failed_prefix(env)
    with pytest.raises(AttributeError):getattr(env.facade, '_version')
    with pytest.raises(AttributeError):getattr(env.facade, '_assets_for_operations')
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
        app.state.production_run_store.tick()
        assert len(children(env, 'film_module_operations')) == 3
        finish_module(env, children(env, 'film_module_operations')[-1]['id'])
        app.state.production_run_store.tick()
        renders = children(env, 'film_render_operations')
        assert len(renders) == 1 and renders[0]['request']['production_run_id'] == run['id']
        client.headers['x-forwarded-user'] = 'foreign-owner'
        assert client.get('/api/production-runs/' + run['id']).status_code == 404


def test_real_facade_reuse_material_uses_existing_query_only_connection_and_owner(env, monkeypatch):
    source = failed_prefix(env)
    original = document(env, source['id'])
    version_id = source['items'][0]['module_version_id']
    facade = FilmCreationWorkflow(env.creation, None, None, None)
    with env.store.transaction() as db:
        db.execute('PRAGMA query_only=ON')
        before = db.total_changes
        def no_nested_transaction():
            raise AssertionError('reuse material must use its caller connection')
        monkeypatch.setattr(env.store, 'transaction', no_nested_transaction)
        material = facade.module_reuse_material(db, OWNER, version_id)
        assert material['version']['id'] == version_id
        assert material['package']['input_sha256'] == original['items'][0]['module_input_sha256']
        assert isinstance(material['assets'], dict) and db.total_changes == before
        with pytest.raises(ServiceError):facade.module_reuse_material(db, 'foreign-owner', version_id)
        with pytest.raises(ServiceError):facade.module_reuse_material(db, OWNER, 'missing-version')
        monkeypatch.undo()


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


def test_http_full_twelve_scene_reuse_creates_one_render_without_new_authors_or_audio(env, monkeypatch):
    source = failed_whole_film(env, count=12)
    before = full_custody(env)
    old_render = env.creation.operation('render', source['draft_render_id'])
    old_snapshot = env.creation.render_snapshot(old_render['id'])
    assert old_snapshot['clock']['frame_count'] == 7200

    def no_new_work(*args, **kwargs):
        raise AssertionError('A full prefix must not admit any new author or audio work')

    monkeypatch.setattr(env.facade, 'start_module', no_new_work)
    monkeypatch.setattr(env.store, 'start_audio', no_new_work)
    app = create_app(db_path=env.store.path, local_mode=False, background=False,
        production_runs_enabled=True, trusted_hosts={'testserver'}, film_creation=env.facade)
    monkeypatch.setattr(app.state.production_run_store.store, 'start_audio', no_new_work)
    project = env.store.project(OWNER, env.project['id'])
    body = dict(expected_revision=project['revision'], joint_plan_version_id='adopted-plan-1',
        audio_mode='silent', reuse_completed_prefix_from_run_id=source['id'])
    url = '/api/projects/' + project['id'] + '/production-runs'
    headers = {'Idempotency-Key': 'http-retry-stopped-whole-film'}
    with TestClient(app) as client:
        client.headers['x-forwarded-user'] = OWNER
        session = client.get('/api/session').json()
        assert client.post(url, json=body, headers=headers).status_code == 403
        client.headers['x-csrf-token'] = session['csrf_token']
        client.headers['x-forwarded-user'] = 'foreign-owner'
        foreign_session = client.get('/api/session').json()
        client.headers['x-csrf-token'] = foreign_session['csrf_token']
        assert client.post(url, json=body, headers=headers).status_code == 404
        client.headers['x-forwarded-user'] = OWNER
        client.headers['x-csrf-token'] = session['csrf_token']
        response = client.post(url, json=body, headers=headers)
        assert response.status_code == 202, response.text
        run = response.json()
        assert run['module_reuse']['prefix_count'] == 12
        assert run['module_work_budget'] == dict(reused_scene_modules=12, new_scene_modules=0,
            new_recordings=0, whole_film_renders=1)
        assert run['module_reuse']['stopped_child_receipts'][old_render['id']] == digest(old_render)
        current = document(env, run['id'])
        assert current['items'] == source['items'] and current['module_targets'] == source['module_targets']
        assert current['intents'] == {name: intent for name, intent in source['intents'].items() if name != 'draft'}
        assert current['draft_render_id'] is None and current.get('render_snapshot_sha256') is None
        assert client.post(url, json=body, headers=headers).json()['id'] == run['id']
        after_admission = full_custody(env)
        for _ in range(2):
            assert client.get('/api/production-runs/' + run['id']).status_code == 200
            assert client.get(url).status_code == 200
        assert full_custody(env) == after_admission
        app.state.production_run_store.tick()
        renders = children(env, 'film_render_operations')
        assert len(renders) == 2
        fresh = renders[-1]
        assert fresh['id'] != old_render['id']
        assert fresh['request']['production_run_id'] == run['id']
        assert fresh['request']['module_version_ids'] == [item['module_version_id'] for item in source['items']]
        assert fresh['configuration'] == old_render['configuration']
        snapshot = env.creation.render_snapshot(fresh['id'])
        assert snapshot['production_run_id'] == run['id']
        assert snapshot['clock'] == old_snapshot['clock'] and snapshot['recording_sha256'] == {}
        assert snapshot['module_sha256'] == old_snapshot['module_sha256']
        new_files = decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'],
            runtime_fingerprint=env.creation.runtime, trusted_host_sha256=env.creation.hosts, image=env.creation.image)
        old_files = decode_film_execution_snapshot(old_snapshot, expected_snapshot_sha256=old_snapshot['sha256'],
            runtime_fingerprint=env.creation.runtime, trusted_host_sha256=env.creation.hosts, image=env.creation.image)
        assert new_files == old_files
        env.facade.tick()
        app.state.production_run_store.tick()
        assert env.renderer.calls == [fresh['id']]
        completed = client.get('/api/production-runs/' + run['id']).json()
        assert completed['status'] == completed['phase'] == 'completed'
        assert completed['draft_render_id'] == fresh['id']
        app.state.production_run_store.tick()
        assert len(children(env, 'film_render_operations')) == 2
        assert client.post(url, json=body, headers=headers).json()['id'] == run['id']
    after = full_custody(env)
    assert after['production_runs'][:len(before['production_runs'])] == before['production_runs']
    assert after['film_render_operations'][:1] == before['film_render_operations']
    for table in ('film_module_operations', 'film_module_versions', 'projects'):
        assert after[table] == before[table]
    assert not children(env, 'audio_operations') and not children(env, 'motion_operations')
    assert not children(env, 'video_renders')


@pytest.mark.parametrize('change', ['unstopped_render', 'active_render', 'cancelled_render', 'completed_render',
    'unfinished_render', 'render_deadline', 'missing_render', 'extra_render', 'render_owner', 'render_project',
    'render_key', 'render_request_hash', 'outer_request_hash',
    'render_versions', 'render_targets', 'render_film', 'render_configuration', 'render_snapshot',
    'assembly_manifest', 'missing_draft_intent', 'active_draft_intent', 'snapshot_pointer',
    'unstopped_module', 'changed_module_source', 'changed_current_configuration'])
def test_full_prefix_rejects_unstopped_or_changed_originals_before_new_journal(env, change):
    source = failed_whole_film(env)
    render_id = source['draft_render_id']
    if change == 'unstopped_render':
        tamper_render(env, render_id, lambda child: child.update(stop_proven=False))
    elif change == 'active_render':
        tamper_render(env, render_id, lambda child: child.update(status='running'))
    elif change == 'cancelled_render':
        tamper_render(env, render_id, lambda child: child.update(cancel_requested=True))
    elif change == 'completed_render':
        tamper_render(env, render_id, lambda child: child.update(status='completed'))
    elif change == 'unfinished_render':
        tamper_render(env, render_id, lambda child: child.update(finished_at=None))
    elif change == 'render_deadline':
        tamper_render(env, render_id, lambda child: child.update(deadline_at=child['deadline_at'] + 1))
    elif change == 'outer_request_hash':
        with env.store.transaction() as db:
            db.execute('UPDATE film_render_operations SET request_hash=? WHERE id=?', ('d' * 64, render_id))
    elif change == 'missing_render':
        with env.store.transaction() as db:
            db.execute('DELETE FROM film_render_operations WHERE id=?', (render_id,))
    elif change == 'extra_render':
        with env.store.transaction() as db:
            row = list(db.execute('SELECT * FROM film_render_operations WHERE id=?', (render_id,)).fetchone())
            child = json.loads(row[5]);child.update(id=str(uuid4()), request_key='unrecorded-extra-render')
            row[0], row[3], row[5] = child['id'], child['request_key'], packed(child)
            db.execute('INSERT INTO film_render_operations VALUES(?,?,?,?,?,?,?,?)', row)
    elif change in {'render_owner', 'render_project', 'render_key', 'render_request_hash', 'render_configuration'}:
        field = {'render_owner': 'owner', 'render_project': 'project_id', 'render_key': 'request_key',
            'render_request_hash': 'request_sha256', 'render_configuration': 'configuration_sha256'}[change]
        tamper_render(env, render_id, lambda child: child.update({field: 'unrelated-identity'}))
    elif change in {'render_versions', 'render_targets', 'render_film'}:
        field = {'render_versions': 'module_version_ids', 'render_targets': 'module_targets', 'render_film': 'film'}[change]
        def alter_request(child):
            if field == 'film':child['request'][field]['title'] = 'Different original film'
            else:child['request'][field].reverse()
            child['request_sha256'] = digest(child['request'])
            source['intents']['draft']['request_sha256'] = child['request_sha256']
        tamper_render(env, render_id, alter_request)
        child = env.creation.operation('render', render_id)
        with env.store.transaction() as db:
            db.execute('UPDATE film_render_operations SET request_hash=? WHERE id=?',
                (digest(dict(project_id=source['project_id'], request=child['request'])), render_id))
        rewrite(env, source)
    elif change == 'render_snapshot':
        with env.store.transaction() as db:
            row = db.execute('SELECT snapshot FROM film_render_operations WHERE id=?', (render_id,)).fetchone()
            snapshot = json.loads(row[0]);snapshot['archive_base64'] = 'broken-original-archive'
            db.execute('UPDATE film_render_operations SET snapshot=? WHERE id=?', (packed(snapshot), render_id))
    elif change == 'assembly_manifest':
        tamper_render(env, render_id, lambda child: child['assembly_manifest'].update(module_sha256={}))
    elif change in {'missing_draft_intent', 'active_draft_intent', 'snapshot_pointer'}:
        if change == 'missing_draft_intent':source['intents'].pop('draft')
        elif change == 'active_draft_intent':source['intents']['draft']['active'] = True
        else:source['render_snapshot_sha256'] = 'd' * 64
        rewrite(env, source)
    elif change == 'unstopped_module':
        tamper_child(env, source['items'][-1]['module_operation_id'], stop_proven=False)
    elif change == 'changed_module_source':
        with env.store.transaction() as db:
            item = source['items'][-1]
            row = db.execute('SELECT package FROM film_module_versions WHERE id=?', (item['module_version_id'],)).fetchone()
            package = json.loads(row[0]);module = package['result']['module']
            module['files'][module['entry']] += '\n// illicit source change\n'
            db.execute('UPDATE film_module_versions SET package=? WHERE id=?', (packed(package), item['module_version_id']))
    else:
        env.creation.configuration['max_seconds'] = 299
        env.creation.configuration['sha256'] = digest({key: value for key, value in env.creation.configuration.items() if key != 'sha256'})
    before = full_custody(env)
    with pytest.raises(ServiceError):start(env, 'reject-full-prefix-' + change, source['id'])
    assert full_custody(env) == before


@pytest.mark.parametrize('change', ['render_receipt', 'snapshot', 'module_receipt'])
def test_full_prefix_rechecked_before_export_and_gets_never_repair_history(env, change):
    source = failed_whole_film(env)
    run = start(env, 'full-prefix-recheck-before-export', source['id'])
    if change == 'render_receipt':
        tamper_render(env, source['draft_render_id'], lambda child: child.update(error='rewritten failure'))
    elif change == 'module_receipt':
        item = source['items'][-1]
        child = env.creation.operation('module', item['module_operation_id'])
        tamper_child(env, child['id'], deadline_at=child['deadline_at'] + 1)
    else:
        with env.store.transaction() as db:
            db.execute('UPDATE film_render_operations SET snapshot=? WHERE id=?',
                ('{}', source['draft_render_id']))
    before = full_custody(env)
    env.runs.get(OWNER, run['id']);env.runs.list(OWNER, env.project['id'])
    assert full_custody(env) == before
    env.runs.tick()
    assert env.runs.get(OWNER, run['id'])['status'] == 'failed'
    after = full_custody(env)
    assert after['film_render_operations'] == before['film_render_operations']
    assert after['film_module_operations'] == before['film_module_operations']
    assert not env.renderer.calls


def transition_and_asset_fixture(env, monkeypatch):
    import test_production_film_runs as fixtures
    name = 'assets/original-paper.svg'
    env.creation.assets[name] = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0L1 1"/></svg>'
    env.creation.runtime_capabilities['assets'][name.removeprefix('assets/')] = hashlib.sha256(env.creation.assets[name]).hexdigest()
    env.creation.configuration['runtime_capabilities'] = deepcopy(env.creation.runtime_capabilities)
    env.creation.configuration['sha256'] = digest({key: value for key, value in env.creation.configuration.items() if key != 'sha256'})
    original_result = fixtures.raw_result

    def result_with_original_transition(document):
        index = document['film_context']['current_scene_index']
        result = original_result(document, transform=index == 1)
        if index:
            at = round(document['film_context']['film']['scenes'][index]['start_seconds'] * 24)
            result['incoming_boundary']['transition'].update(start_frame=at - 3, end_frame=at + 3)
            result['incoming_boundary']['transition']['module']['assets'] = [name]
        return result

    monkeypatch.setattr(fixtures, 'raw_result', result_with_original_transition)
    return name


def test_full_prefix_preserves_original_transition_and_declared_asset_bytes(env, monkeypatch):
    name = transition_and_asset_fixture(env, monkeypatch)
    source = failed_whole_film(env)
    before = full_custody(env)
    original = env.creation.render_snapshot(source['draft_render_id'])
    assert len(original['module_sha256']) == 3
    run = start(env, 'reuse-full-prefix-with-transition', source['id'])
    env.runs.tick()
    fresh = children(env, 'film_render_operations')[-1]
    assert fresh['request']['production_run_id'] == run['id']
    snapshot = env.creation.render_snapshot(fresh['id'])
    files = decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'],
        runtime_fingerprint=env.creation.runtime, trusted_host_sha256=env.creation.hosts, image=env.creation.image)
    assert snapshot['module_sha256'] == original['module_sha256']
    assert files['project/' + name] == env.creation.assets[name]
    for item in source['items']:
        package = env.creation.module_package(OWNER, item['module_version_id'])
        incoming = package['result']['incoming_boundary']
        modules = [package['result']['module']]
        if incoming is not None:
            modules.append(incoming['transition_module'])
        for module in modules:
            for path, text in module['files'].items():
                assert files['project/' + path] == text.encode()
    after = full_custody(env)
    for table in ('film_module_operations', 'film_module_versions', 'projects'):
        assert after[table] == before[table]
    assert after['film_render_operations'][:1] == before['film_render_operations']


def test_full_prefix_rejects_a_self_consistent_snapshot_with_substituted_declared_asset(env, monkeypatch):
    from video_workbench.hyperframes_film_project import compile_film_project
    from video_workbench.hyperframes_film_browser import HOST_NAMES, compile_film_browser_bundle
    from video_workbench.hyperframes_film_snapshot import freeze_film_execution_snapshot
    name = transition_and_asset_fixture(env, monkeypatch)
    source = failed_whole_film(env)
    child = env.creation.operation('render', source['draft_render_id'])
    packages = [env.creation.module_package(OWNER, item['module_version_id']) for item in source['items']]
    modules, boundaries = [], []
    for package in packages:
        modules.append(package['result']['module'])
        incoming = package['result']['incoming_boundary']
        if incoming is not None:
            modules.append(incoming['transition_module'])
            boundaries.append(incoming['boundary'])
    project = compile_film_project(film=source['film'], film_sha256=source['film_sha256'],
        modules=modules, boundaries=boundaries, recordings=[], assets={name: b'substituted passive asset'},
        runtime_fingerprint=env.creation.runtime)
    bundle = compile_film_browser_bundle(project,
        expected_project_manifest_sha256=hashlib.sha256(project['manifest.json']).hexdigest(),
        width=1280, height=720, host_files=env.creation.host_files)
    snapshot = freeze_film_execution_snapshot(bundle,
        expected_bundle_sha256=hashlib.sha256(bundle['browser-manifest.json']).hexdigest(),
        runtime_fingerprint=env.creation.runtime, trusted_host_sha256=env.creation.hosts, image=env.creation.image,
        project_revision=child['request']['expected_revision'], production_run_id=source['id'],
        timeout_seconds=child['request']['timeout_seconds'])
    # The replacement passes the standalone snapshot verifier; comparison with
    # the immutable author packages/assets must still reject it before admission.
    decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'],
        runtime_fingerprint=env.creation.runtime, trusted_host_sha256=env.creation.hosts, image=env.creation.image)
    tamper_render(env, child['id'], lambda value: value.update(snapshot_sha256=snapshot['sha256']))
    with env.store.transaction() as db:
        db.execute('UPDATE film_render_operations SET snapshot=? WHERE id=?', (packed(snapshot), child['id']))
    before = full_custody(env)
    with pytest.raises(ServiceError):start(env, 'reject-self-consistent-wrong-asset', source['id'])
    assert full_custody(env) == before


def test_repeated_stopped_full_export_failure_keeps_bounded_lineage_and_all_original_modules(env):
    source = failed_whole_film(env)
    first = start(env, 'first-full-export-recovery', source['id'])
    env.runs.tick()
    render = children(env, 'film_render_operations')[-1]
    env.creation.claim_submission('render', render['id'])
    env.creation.update_operation('render', render['id'], status='failed', stop_proven=True,
        error='hyperframes_film_execution_failed', finished_at=time.time())
    env.runs.tick()
    second = start(env, 'second-full-export-recovery', first['id'])
    assert second['module_reuse']['lineage_run_ids'] == [first['id'], source['id']]
    assert [item['module_version_id'] for item in second['items']] == [item['module_version_id'] for item in source['items']]
    env.runs.tick()
    assert len(children(env, 'film_module_operations')) == 2
    assert len(children(env, 'film_render_operations')) == 3
    assert not children(env, 'audio_operations')
