"""Exact A-to-B custody fixtures; no live admission, models or actual MP4s."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles.video_animation_film_module import PROFILE
from video_workbench import film_source_runtime_admission as admission
from video_workbench.app import create_app
from video_workbench.film_creation_store import FilmCreationStore
from video_workbench.film_creation_workflow import FilmCreationWorkflow
from video_workbench.formal_drawing_libraries import enable_in_capabilities
from video_workbench.hyperframes_film_snapshot import decode_film_execution_snapshot
from video_workbench.ink_landscape_library import read_assets
from video_workbench.manim_film_admission import CONTEXT_SCHEMA, helpers
from video_workbench.manim_method_resources import PRODUCER_REQUIREMENT
from video_workbench.production_run_store import packed
from test_production_film_runs import env, children, OWNER
from test_production_module_reuse import (
    SilentCustodyRenderer, document, failed_whole_film, full_custody, start,
)


def seal(value):
    value['sha256'] = digest({key: item for key, item in value.items() if key != 'sha256'})
    return value


@pytest.fixture
def bridge(env, monkeypatch):
    """Create twelve genuinely frozen local packages with synthetic stop data."""
    import test_production_film_runs as fixtures
    source_execution, target_execution = '1' * 64, '2' * 64
    caps = deepcopy(env.creation.runtime_capabilities)
    caps['allowed_engines'].append('canvas')
    caps['runtime_identity']['admission_id'] = 'hyperframes-film-execution-v1@' + source_execution
    caps = enable_in_capabilities(caps)
    env.creation.runtime_capabilities = caps
    env.creation.assets = {'assets/' + name: raw for name, raw in read_assets().items()}
    env.creation.configuration['runtime_capabilities'] = deepcopy(caps)
    source_consumer = dict(schema=CONTEXT_SCHEMA, producer=deepcopy(PRODUCER_REQUIREMENT),
        resource_image='registry.internal/synthetic-resource:latest@sha256:' + '3' * 64,
        helpers=helpers(), formal_release=dict(image=env.creation.image,
            runtime_fingerprint=deepcopy(env.creation.runtime), trusted_host_sha256=deepcopy(env.creation.hosts)),
        admission_sha256='4' * 64)
    env.creation.configuration['resource_authoring'] = deepcopy(source_consumer)
    seal(env.creation.configuration)
    raw_result = fixtures.raw_result

    def fixture_result(document):
        result = raw_result(document)
        result['module']['engine'] = 'canvas'
        result['module']['assets'] = sorted(env.creation.assets)
        return result

    monkeypatch.setattr(fixtures, 'raw_result', fixture_result)
    source = failed_whole_film(env, count=12)
    source_creation = env.creation
    source_configuration = deepcopy(source_creation.configuration)
    source_render = source_creation.operation('render', source['draft_render_id'])
    packages = [source_creation.module_package(OWNER, item['module_version_id']) for item in source['items']]
    host_files = deepcopy(source_creation.host_files)
    host_files['hyperframes_film_browser.mjs'] += b'\n// Synthetic target-only host bytes; not an executor admission.\n'
    hosts = {name: hashlib.sha256(raw).hexdigest() for name, raw in host_files.items()}
    runtime = deepcopy(source_creation.runtime)
    runtime['image_digest'] = 'sha256:' + '9' * 64
    target_caps = deepcopy(caps)
    target_caps['runtime_identity'].update(image_digest=runtime['image_digest'], host_sha256=digest(hosts),
        admission_id='hyperframes-film-execution-v1@' + target_execution)
    image = 'registry.internal/renderer:latest@' + runtime['image_digest']
    target_consumer = deepcopy(source_consumer)
    target_consumer.update(formal_release=dict(image=image, runtime_fingerprint=deepcopy(runtime),
        trusted_host_sha256=deepcopy(hosts)), admission_sha256='5' * 64)
    target_kwargs = dict(runtime_capabilities=target_caps, runtime_fingerprint=runtime,
        host_files=host_files, trusted_host_sha256=hosts, image=image,
        assets=deepcopy(source_creation.assets), enabled=True, resource_authoring=target_consumer,
        execution_admission_sha256=target_execution)
    policy = seal(dict(schema=admission.SCHEMA, status='admitted', scope=admission.SCOPE,
        source=dict(owner=OWNER, project_id=source['project_id'], run_id=source['id'], run_sha256=digest(source),
            film_sha256=source['film_sha256'], render_id=source_render['id'],
            snapshot_sha256=source_render['snapshot_sha256'], configuration=source_configuration,
            runtime_identity=deepcopy(caps['runtime_identity']), execution_admission_sha256=source_execution,
            modules=[dict(scene_id=item['scene_id'], operation_id=item['module_operation_id'],
                version_id=item['module_version_id'], package_sha256=item['module_package_sha256'],
                input_sha256=item['module_input_sha256'], result_sha256=package['result_sha256'],
                module_sha256=item['module_sha256']) for item, package in zip(source['items'], packages)]),
        target=dict(image=image, runtime_identity=deepcopy(target_caps['runtime_identity']),
            runtime_fingerprint=deepcopy(runtime), trusted_host_sha256=deepcopy(hosts),
            runtime_capabilities=deepcopy(target_caps), execution_admission_sha256=target_execution,
            resource_authoring=deepcopy(target_consumer)),
        host_delta=dict(file='hyperframes_film_browser.mjs',
            source_sha256=source_creation.hosts['hyperframes_film_browser.mjs'],
            target_sha256=hosts['hyperframes_film_browser.mjs'], launch_flag='--disable-accelerated-2d-canvas'),
        evidence={name: dict(reference='synthetic-fixture/' + name,
            sha256=source_consumer['admission_sha256'] if name == 'source_consumer_admission' else
                target_consumer['admission_sha256'] if name == 'target_consumer_admission' else digest(name))
            for name in set(admission.EVIDENCE_FIELDS) | {'source_consumer_admission', 'target_consumer_admission'}}))
    assert admission.validate_policy(policy) == policy
    return SimpleNamespace(env=env, source=source, source_creation=source_creation,
        source_configuration=source_configuration, source_render=source_render, packages=packages,
        target_kwargs=target_kwargs, policy=policy)


def target_store(case, *, policy=True):
    return FilmCreationStore(case.env.store, **case.target_kwargs,
        **({'source_runtime_admission': deepcopy(case.policy)} if policy else {}))


def install_target(case, creation):
    env = case.env
    env.creation = creation
    env.renderer = SilentCustodyRenderer()
    gateway = SimpleNamespace(profile=PROFILE, profile_revision='1', preflight=lambda deadline: None)
    env.facade = FilmCreationWorkflow(creation, gateway, env.adapter, env.renderer,
        enabled=True, render_readiness=lambda: True)
    env.runs.film_creation = env.facade
    return env.facade


def receipt_and_run(case, configuration):
    run_id = 'synthetic-target-run'
    receipt = admission.make_receipt(case.policy, case.source, run_id, configuration)
    run = dict(id=run_id, film_sha256=case.source['film_sha256'],
        film_creation_configuration=dict(configuration_sha256=configuration['sha256']),
        module_work_budget=dict(reused_scene_modules=12, new_scene_modules=0, whole_film_renders=1,
            new_recordings=0), module_reuse=dict(source_run_id=case.source['id']),
        source_runtime_admission_receipt=receipt)
    return receipt, run


def test_operator_policy_and_target_configuration_have_no_hash_cycle_and_preserve_author_identity(bridge):
    creation = target_store(bridge)
    policy = bridge.policy
    configuration = creation.configuration
    assert set(policy['target']) == admission.TARGET_FIELDS | {'resource_authoring'}
    assert 'configuration_sha256' not in policy['target']
    assert configuration['source_runtime_admission'] == policy
    assert policy['sha256'] == digest({key: value for key, value in policy.items() if key != 'sha256'})
    assert configuration['sha256'] == digest({key: value for key, value in configuration.items() if key != 'sha256'})
    assert configuration['sha256'] not in canonical_bytes(policy).decode()
    assert target_store(bridge).configuration == configuration
    assert admission.validate_target_configuration(policy, configuration,
        execution_admission_sha256=policy['target']['execution_admission_sha256']) == policy
    assert bridge.source_creation.configuration == bridge.source_configuration
    a = bridge.source_configuration['resource_authoring']
    b = configuration['resource_authoring']
    assert a['schema'] == b['schema'] == CONTEXT_SCHEMA
    assert a['producer'] == b['producer'] == PRODUCER_REQUIREMENT
    assert a['resource_image'] == b['resource_image']
    assert a['helpers'] == b['helpers'] == helpers()
    assert a['admission_sha256'] != b['admission_sha256']
    assert a['formal_release']['image'] == policy['source']['configuration']['image']
    assert b['formal_release']['image'] == policy['target']['image']
    copy = creation.source_runtime_policy()
    copy['source']['owner'] = 'caller-mutation'
    assert creation.source_runtime_policy() == policy
    receipt, run = receipt_and_run(bridge, configuration)
    assert receipt['source_runtime_identity'] == policy['source']['runtime_identity']
    assert receipt['target_runtime_identity'] == policy['target']['runtime_identity']
    assert receipt['source_configuration_sha256'] == bridge.source_configuration['sha256']
    assert receipt['target_configuration_sha256'] == configuration['sha256']
    for index in range(12):
        assert admission.expected_child_configuration(run, 'module', index, policy=policy) == bridge.source_configuration['sha256']
    assert admission.expected_child_configuration(run, 'draft', policy=policy) == configuration['sha256']
    ordinary = deepcopy(run)
    ordinary.pop('source_runtime_admission_receipt')
    assert admission.expected_child_configuration(ordinary, 'module', 0, policy=None) == configuration['sha256']
    assert admission.expected_child_configuration(ordinary, 'draft', policy=None) == configuration['sha256']
    assert admission.validate_packages(policy, receipt, bridge.packages, film=bridge.source['film'],
        module_targets=bridge.source['module_targets'], target_run_id=run['id'],
        target_configuration_sha256=configuration['sha256']) == policy['source']['runtime_identity']


def test_policy_rejects_expanded_engines_assets_licenses_hosts_and_execution_identities(bridge):
    policy = bridge.policy
    changes = ('scope', 'status', 'source_image', 'engine', 'engine_runtime', 'asset', 'license',
        'host_map', 'host_delta', 'source_execution', 'target_execution', 'module_count', 'module_order_duplicate',
        'evidence', 'unauthorized_final_configuration', 'consumer_producer', 'consumer_resource_image',
        'consumer_helpers', 'consumer_admission', 'consumer_release', 'consumer_evidence')
    for change in changes:
        bad = deepcopy(policy)
        if change == 'scope':bad['scope'] = 'all-projects-and-new-authors'
        elif change == 'status':bad['status'] = 'unreviewed'
        elif change == 'source_image':
            bad['target']['image'] = bad['source']['configuration']['image']
            bad['target']['runtime_fingerprint']['image_digest'] = bad['source']['runtime_identity']['image_digest']
            bad['target']['runtime_identity']['image_digest'] = bad['source']['runtime_identity']['image_digest']
            bad['target']['runtime_capabilities']['runtime_identity']['image_digest'] = bad['source']['runtime_identity']['image_digest']
            bad['target']['resource_authoring']['formal_release'].update(image=bad['target']['image'],
                runtime_fingerprint=deepcopy(bad['target']['runtime_fingerprint']))
        elif change == 'engine':bad['target']['runtime_capabilities']['allowed_engines'].remove('html-svg')
        elif change == 'engine_runtime':
            bad['target']['runtime_fingerprint']['engine_runtime_sha256'] = 'a' * 64
            bad['target']['runtime_identity']['engine_runtime_sha256'] = 'a' * 64
            bad['target']['runtime_capabilities']['runtime_identity']['engine_runtime_sha256'] = 'a' * 64
            bad['target']['resource_authoring']['formal_release']['runtime_fingerprint'] = deepcopy(bad['target']['runtime_fingerprint'])
        elif change == 'asset':bad['target']['runtime_capabilities']['assets']['ink-landscape.mjs'] = 'a' * 64
        elif change == 'license':bad['target']['runtime_capabilities']['drawing_libraries']['ink-landscape']['license'] = 'GPL'
        elif change == 'host_map':
            bad['target']['trusted_host_sha256']['hyperframes_film.mjs'] = 'a' * 64
            bad['target']['runtime_identity']['host_sha256'] = digest(bad['target']['trusted_host_sha256'])
            bad['target']['runtime_capabilities']['runtime_identity']['host_sha256'] = bad['target']['runtime_identity']['host_sha256']
            bad['target']['resource_authoring']['formal_release']['trusted_host_sha256'] = deepcopy(bad['target']['trusted_host_sha256'])
        elif change == 'host_delta':bad['host_delta']['launch_flag'] = '--disable-web-security'
        elif change == 'source_execution':bad['source']['execution_admission_sha256'] = 'a' * 64
        elif change == 'target_execution':bad['target']['execution_admission_sha256'] = 'a' * 64
        elif change == 'module_count':bad['source']['modules'].pop()
        elif change == 'module_order_duplicate':bad['source']['modules'][1] = deepcopy(bad['source']['modules'][0])
        elif change == 'evidence':bad['evidence'].pop('code_and_license_manifest')
        elif change == 'consumer_producer':bad['target']['resource_authoring']['producer']['manim']['version'] = '99.0.0'
        elif change == 'consumer_resource_image':bad['target']['resource_authoring']['resource_image'] = 'registry.internal/other-resource:latest@sha256:' + 'a' * 64
        elif change == 'consumer_helpers':bad['target']['resource_authoring']['helpers']['manim_frame_layer.mjs'] = 'a' * 64
        elif change == 'consumer_admission':
            bad['target']['resource_authoring']['admission_sha256'] = bad['source']['configuration']['resource_authoring']['admission_sha256']
            bad['evidence']['target_consumer_admission']['sha256'] = bad['target']['resource_authoring']['admission_sha256']
        elif change == 'consumer_release':bad['target']['resource_authoring']['formal_release'] = deepcopy(bad['source']['configuration']['resource_authoring']['formal_release'])
        elif change == 'consumer_evidence':bad['evidence'].pop('target_consumer_admission')
        else:bad['target']['configuration_sha256'] = 'a' * 64
        seal(bad)
        with pytest.raises(ServiceError):admission.validate_policy(bad)


def test_receipts_and_packages_cannot_change_scope_or_author_configuration(bridge):
    creation = target_store(bridge)
    receipt, run = receipt_and_run(bridge, creation.configuration)
    for field in ('policy_sha256', 'source_run_id', 'source_run_sha256', 'source_configuration_sha256',
            'target_run_id', 'target_configuration_sha256', 'owner', 'project_id', 'film_sha256',
            'module_package_sha256s', 'new_scene_modules'):
        bad = deepcopy(receipt)
        bad[field] = (list(reversed(bad[field])) if field == 'module_package_sha256s' else
            1 if field == 'new_scene_modules' else 'a' * 64)
        seal(bad)
        with pytest.raises(ServiceError):
            admission.validate_receipt(bridge.policy, bad, target_run_id=run['id'],
                target_configuration_sha256=creation.configuration_sha256, film_sha256=run['film_sha256'])
    for change in ('order', 'owner', 'runtime', 'asset', 'profile', 'source'):
        packages = deepcopy(bridge.packages)
        if change == 'order':packages.reverse()
        elif change == 'owner':packages[0]['input_document']['skill_catalog']['recipient_owner'] = 'foreign-owner'
        elif change == 'runtime':packages[0]['input_document']['constraints']['runtime_identity'] = deepcopy(bridge.policy['target']['runtime_identity'])
        elif change == 'asset':packages[0]['input_document']['assets']['assets/ink-landscape.LICENSE.txt'] = 'a' * 64
        elif change == 'profile':packages[0]['profile']['name'] = 'another-native-profile'
        else:
            module = packages[0]['result']['module']
            module['files'][module['entry']] += '\n// substituted source\n'
        with pytest.raises(ServiceError):
            admission.validate_packages(bridge.policy, receipt, packages, film=bridge.source['film'],
                module_targets=bridge.source['module_targets'], target_run_id=run['id'],
                target_configuration_sha256=creation.configuration_sha256)
    for phase, index in (('module', None), ('module', -1), ('module', 12), ('module', True),
            ('audio', None), ('draft', 0)):
        with pytest.raises(ServiceError):admission.expected_child_configuration(run, phase, index, policy=bridge.policy)
    with pytest.raises(ServiceError):admission.expected_child_configuration(run, 'draft', policy=None)
    new_work = deepcopy(run)
    new_work['module_work_budget']['new_scene_modules'] = 1
    with pytest.raises(ServiceError):admission.expected_child_configuration(new_work, 'draft', policy=bridge.policy)
    another_source = deepcopy(run)
    another_source['module_reuse']['source_run_id'] = 'another-original-run'
    with pytest.raises(ServiceError):admission.expected_child_configuration(another_source, 'module', 0, policy=bridge.policy)


def test_source_and_current_target_changes_fail_before_reuse_admission(bridge):
    creation = target_store(bridge)
    for change in ('owner', 'project', 'run', 'film', 'partial', 'active', 'cancelled', 'order',
            'resource_plan', 'resource_authoring', 'resource_plan_registration', 'prepared_resource_references', 'audio_reuse'):
        source = deepcopy(bridge.source)
        if change == 'owner':source['owner'] = 'foreign-owner'
        elif change == 'project':source['project_id'] = 'foreign-project'
        elif change == 'run':source['id'] = 'foreign-run'
        elif change == 'film':source['film_sha256'] = 'a' * 64
        elif change == 'partial':source['items'][-1]['module_version_id'] = None
        elif change == 'active':source['status'] = 'running'
        elif change == 'cancelled':source['cancel_requested'] = True
        elif change == 'order':source['items'].reverse()
        else:source[change] = {}
        policy = deepcopy(bridge.policy)
        policy['source']['run_sha256'] = digest(source)
        seal(policy)
        with pytest.raises(ServiceError):admission.validate_source_run(policy, source)
    for change in ('profile', 'module_bundle', 'host_map', 'image', 'policy', 'extra_field'):
        configuration = deepcopy(creation.configuration)
        if change == 'profile':configuration['profile'] = 'another-module-profile'
        elif change == 'module_bundle':configuration['bundle_manifest_sha256'] = 'a' * 64
        elif change == 'host_map':configuration['trusted_host_sha256']['engine_runtime.mjs'] = 'a' * 64
        elif change == 'image':configuration['image'] = configuration['image'].replace('9' * 64, '8' * 64)
        elif change == 'policy':configuration['source_runtime_admission']['evidence']['whole_frame']['sha256'] = 'a' * 64
        else:configuration['request_selected_runtime'] = True
        seal(configuration)
        with pytest.raises(ServiceError):admission.validate_target_configuration(bridge.policy, configuration)
    with pytest.raises(ServiceError):
        admission.validate_target_configuration(bridge.policy, creation.configuration,
            execution_admission_sha256='a' * 64)


def test_real_http_bridge_reuses_twelve_a_packages_and_renders_once_with_target_c_configuration(bridge, monkeypatch):
    env, source = bridge.env, bridge.source
    creation = target_store(bridge)
    install_target(bridge, creation)
    before = full_custody(env)

    def no_new_authors_or_audio(*args, **kwargs):
        raise AssertionError('The exact-runtime bridge grants no new author or audio admissions')

    monkeypatch.setattr(env.facade, 'start_module', no_new_authors_or_audio)
    monkeypatch.setenv('SERVICE_AUTH_ISSUER', 'https://login.example.test')
    app = create_app(db_path=env.store.path, local_mode=False, background=False,
        production_runs_enabled=True, trusted_hosts={'testserver'}, film_creation=env.facade)
    monkeypatch.setattr(app.state.production_run_store.store, 'start_audio', no_new_authors_or_audio)
    project = env.store.project(OWNER, source['project_id'])
    body = dict(expected_revision=project['revision'], joint_plan_version_id='adopted-plan-1',
        audio_mode='silent', reuse_completed_prefix_from_run_id=source['id'])
    url = '/api/projects/' + project['id'] + '/production-runs'
    headers = {'Idempotency-Key': 'exact-a-to-b-http-render'}
    with TestClient(app, base_url='https://testserver', headers={'origin': 'https://testserver'}) as client:
        client.headers['x-forwarded-user'] = OWNER
        session = client.get('/api/session').json()
        assert client.post(url, json=body, headers=headers).status_code == 403
        client.headers['x-csrf-token'] = session['csrf_token']
        client.headers['x-forwarded-user'] = 'foreign-owner'
        foreign_session = client.get('/api/session').json()
        client.headers['x-csrf-token'] = foreign_session['csrf_token']
        assert client.post(url, json=body, headers=headers).status_code == 404
        client.headers['x-forwarded-user'] = OWNER
        client.headers['x-csrf-token'] = client.get('/api/session').json()['csrf_token']
        for field in ('source_runtime_admission', 'source_runtime_admission_file', 'source_runtime_admission_receipt'):
            response = client.post(url, json=dict(body, **{field: bridge.policy}), headers=headers)
            assert response.status_code == 422, (field, response.text)
        assert full_custody(env) == before
        response = client.post(url, json=body, headers=headers)
        assert response.status_code == 202, response.text
        run = document(env, response.json()['id'])
        receipt = run['source_runtime_admission_receipt']
        assert run['items'] == source['items'] and run['module_targets'] == source['module_targets']
        assert run['film_creation_configuration']['configuration_sha256'] == creation.configuration_sha256
        assert receipt['source_configuration_sha256'] == bridge.source_configuration['sha256']
        assert receipt['target_configuration_sha256'] == creation.configuration_sha256
        assert client.post(url, json=body, headers=headers).json()['id'] == run['id']
        after_admission = full_custody(env)
        client.get('/api/production-runs/' + run['id']);client.get(url)
        assert full_custody(env) == after_admission
        app.state.production_run_store.tick()
        fresh = children(env, 'film_render_operations')[-1]
        assert len(children(env, 'film_render_operations')) == 2
        assert fresh['request']['source_runtime_admission_receipt'] == receipt
        assert fresh['configuration'] == creation.configuration
        assert fresh['configuration_sha256'] == creation.configuration_sha256
        assert fresh['request']['module_version_ids'] == [item['module_version_id'] for item in source['items']]
        for index, item in enumerate(source['items']):
            original = env.facade.operation('module', item['module_operation_id'], OWNER)
            assert original['configuration'] == bridge.source_configuration
            assert app.state.production_run_store._lookup(run, run['intents']['module:' + str(index)])['id'] == original['id']
        snapshot = creation.render_snapshot(fresh['id'])
        assert snapshot['production_run_id'] == run['id']
        assert snapshot['image'] == bridge.policy['target']['image']
        assert snapshot['clock']['frame_count'] == 7200 and snapshot['recording_sha256'] == {}
        files = decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'],
            runtime_fingerprint=creation.runtime, trusted_host_sha256=creation.hosts, image=creation.image)
        for package in bridge.packages:
            assert package['input_document']['constraints']['runtime_identity'] == bridge.policy['source']['runtime_identity']
            for path, source_text in package['result']['module']['files'].items():
                assert files['project/' + path] == source_text.encode()
        for name, raw in creation.assets.items():assert files['project/' + name] == raw
        env.facade.tick();app.state.production_run_store.tick()
        completed = client.get('/api/production-runs/' + run['id']).json()
        assert completed['status'] == 'completed', completed
        assert env.renderer.calls == [fresh['id']]
        app.state.production_run_store.tick()
        assert len(children(env, 'film_render_operations')) == 2
    completed_document = document(env, run['id'])
    alias = deepcopy(completed_document)
    alias.update(status='failed', phase='draft')
    with env.store.transaction() as db:env.runs._write(db, alias)
    alias_before = full_custody(env)
    with pytest.raises(ServiceError, match='production_run_module_reuse_mismatch'):
        start(env, 'bridged-export-is-not-another-alias', alias['id'])
    assert full_custody(env) == alias_before
    with env.store.transaction() as db:env.runs._write(db, completed_document)
    after = full_custody(env)
    assert after['production_runs'][:len(before['production_runs'])] == before['production_runs']
    assert after['film_render_operations'][:1] == before['film_render_operations']
    for table in ('film_module_operations', 'film_module_versions', 'projects'):
        assert after[table] == before[table]
    assert not children(env, 'audio_operations') and not children(env, 'motion_operations')


def test_absent_policy_keeps_strict_runtime_matching_and_scoped_policy_cannot_select_another_source(bridge):
    env = bridge.env
    no_policy = target_store(bridge, policy=False)
    install_target(bridge, no_policy)
    before = full_custody(env)
    with pytest.raises(ServiceError):start(env, 'no-server-owned-runtime-policy', bridge.source['id'])
    assert full_custody(env) == before
    request = deepcopy(bridge.source_render['request'])
    request['configuration_sha256'] = no_policy.configuration_sha256
    request['production_run_id'] = 'synthetic-unadmitted-runtime-change'
    with pytest.raises(ServiceError, match='runtime_binding_mismatch'):
        no_policy.start_render(OWNER, bridge.source['project_id'], request, 'strict-default-render-no-policy')
    assert full_custody(env) == before
    install_target(bridge, target_store(bridge))
    source = deepcopy(bridge.source)
    source['id'] = 'another-source-run'
    with env.store.transaction() as db:
        db.execute('INSERT INTO production_runs VALUES(?,?,?,?,?,?)',
            (source['id'], OWNER, source['project_id'], 'another-source-key', 'a' * 64, packed(source)))
    before = full_custody(env)
    with pytest.raises(ServiceError):start(env, 'wrong-source-for-runtime-policy', source['id'])
    assert full_custody(env) == before


def test_changed_receipt_policy_or_target_is_rejected_before_a_new_export(bridge):
    env = bridge.env
    creation = target_store(bridge)
    install_target(bridge, creation)
    run = start(env, 'runtime-receipt-recheck-before-export', bridge.source['id'])
    stored = document(env, run['id'])
    receipt = stored['source_runtime_admission_receipt']
    receipt['target_configuration_sha256'] = 'a' * 64
    seal(receipt)
    with env.store.transaction() as db:env.runs._write(db, stored)
    before = full_custody(env)
    env.runs.get(OWNER, run['id']);env.runs.list(OWNER, bridge.source['project_id'])
    assert full_custody(env) == before
    env.runs.tick()
    assert env.runs.get(OWNER, run['id'])['status'] == 'failed'
    assert len(children(env, 'film_render_operations')) == 1
    assert not env.renderer.calls


def test_operator_policy_file_is_reopened_and_changed_policy_cannot_reauthorize_a_frozen_configuration(bridge):
    path = bridge.env.path / 'synthetic-source-runtime-policy.json'
    path.write_bytes(canonical_bytes(bridge.policy))
    creation = FilmCreationStore(bridge.env.store, **bridge.target_kwargs,
        source_runtime_admission=deepcopy(bridge.policy), source_runtime_admission_file=path)
    assert creation.source_runtime_policy() == bridge.policy
    configuration = deepcopy(creation.configuration)
    changed = deepcopy(bridge.policy)
    changed['evidence']['whole_frame']['sha256'] = 'a' * 64
    seal(changed)
    path.write_bytes(canonical_bytes(changed))
    with pytest.raises(ServiceError, match='policy_changed'):
        creation.source_runtime_policy()
    assert creation.configuration == configuration


def test_mutually_rehashed_run_and_receipt_cannot_nominate_another_current_target(bridge):
    env = bridge.env
    creation = target_store(bridge)
    install_target(bridge, creation)
    run = start(env, 'mutually-forged-target-hashes', bridge.source['id'])
    stored = document(env, run['id'])
    stored['film_creation_configuration']['configuration_sha256'] = 'a' * 64
    stored['source_runtime_admission_receipt']['target_configuration_sha256'] = 'a' * 64
    seal(stored['source_runtime_admission_receipt'])
    with env.store.transaction() as db:env.runs._write(db, stored)
    env.runs.tick()
    assert env.runs.get(OWNER, run['id'])['status'] == 'failed'
    assert len(children(env, 'film_render_operations')) == 1
    assert not env.renderer.calls


def test_queued_export_rechecks_operator_file_receipt_and_all_thirteen_source_children_before_submit(bridge):
    env = bridge.env
    path = env.path / 'queued-synthetic-operator-policy.json'
    policy_raw = canonical_bytes(bridge.policy)
    path.write_bytes(policy_raw)
    creation = FilmCreationStore(env.store, **bridge.target_kwargs,
        source_runtime_admission=deepcopy(bridge.policy), source_runtime_admission_file=path)
    install_target(bridge, creation)
    run = start(env, 'queued-runtime-custody-guard', bridge.source['id'])
    env.runs.tick()
    fresh = children(env, 'film_render_operations')[-1]
    stored = document(env, run['id'])
    assert len(stored['module_reuse']['stopped_child_receipts']) == 13
    assert fresh['status'] == 'queued' and fresh['submit_attempted'] is False
    source_before = document(env, bridge.source['id'])
    packages_before = deepcopy(bridge.packages)
    receipt_before = deepcopy(fresh['request']['source_runtime_admission_receipt'])
    configuration_before = deepcopy(creation.configuration)

    changed = deepcopy(bridge.policy)
    changed['evidence']['whole_frame']['sha256'] = 'a' * 64
    seal(changed)
    path.write_bytes(canonical_bytes(changed))
    with pytest.raises(ServiceError, match='policy_changed'):env.facade._render(fresh)
    path.write_bytes(policy_raw)

    changes = (
        ('render', bridge.source['draft_render_id'], 'finished_at'),
        ('render', bridge.source['draft_render_id'], 'request_sha256'),
        ('module', bridge.source['items'][0]['module_operation_id'], 'request_sha256'),
        ('module', bridge.source['items'][-1]['module_operation_id'], 'finished_at'),
        ('render', bridge.source['draft_render_id'], 'sql_request_hash'),
        ('module', bridge.source['items'][0]['module_operation_id'], 'sql_request_hash'),
    )
    for kind, operation_id, field in changes:
        table = 'film_render_operations' if kind == 'render' else 'film_module_operations'
        with env.store.transaction() as db:
            row = db.execute('SELECT document,request_hash FROM ' + table + ' WHERE id=?', (operation_id,)).fetchone()
            original_document, original_request_hash = row
            child = json.loads(original_document)
            if field == 'sql_request_hash':
                db.execute('UPDATE ' + table + ' SET request_hash=? WHERE id=?', ('a' * 64, operation_id))
            else:
                child[field] = child[field] + .5 if field == 'finished_at' else 'a' * 64
                db.execute('UPDATE ' + table + ' SET document=? WHERE id=?', (packed(child), operation_id))
        try:
            assert document(env, bridge.source['id']) == source_before
            assert creation.source_runtime_policy() == bridge.policy
            assert creation.configuration == configuration_before
            assert creation.operation('render', fresh['id'])['request']['source_runtime_admission_receipt'] == receipt_before
            assert [creation.module_package(OWNER, item['module_version_id']) for item in bridge.source['items']] == packages_before
            with pytest.raises(ServiceError):env.facade._render(fresh)
            current = creation.operation('render', fresh['id'])
            assert current['status'] == 'queued' and current['submit_attempted'] is False
            assert not env.renderer.calls
        finally:
            with env.store.transaction() as db:
                db.execute('UPDATE ' + table + ' SET document=?,request_hash=? WHERE id=?',
                    (original_document, original_request_hash, operation_id))

    original_fresh = creation.operation('render', fresh['id'])
    forged = deepcopy(original_fresh)
    forged['request']['source_runtime_admission_receipt']['policy_sha256'] = 'a' * 64
    seal(forged['request']['source_runtime_admission_receipt'])
    forged['request_sha256'] = digest(forged['request'])
    with env.store.transaction() as db:
        db.execute('UPDATE film_render_operations SET document=? WHERE id=?', (packed(forged), fresh['id']))
    with pytest.raises(ServiceError, match='receipt_mismatch'):env.facade._render(fresh)
    with env.store.transaction() as db:
        db.execute('UPDATE film_render_operations SET document=? WHERE id=?', (packed(original_fresh), fresh['id']))
    assert not env.renderer.calls
    env.facade._render(fresh)
    assert env.renderer.calls == [fresh['id']]
    env.runs.tick()
    assert env.runs.get(OWNER, run['id'])['status'] == 'completed'
