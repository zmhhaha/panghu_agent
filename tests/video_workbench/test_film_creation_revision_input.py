"""Exact prior-module custody through the frozen v1 role; no live generation."""
from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_film_module as P
from agent_service.profiles.video_animation_film_module_contract import MAX_SOURCE_FILE_BYTES
from video_workbench.film_creation_store import FilmCreationStore, PENDING_SCHEMA
from video_workbench.film_creation_workflow import FilmCreationWorkflow
from video_workbench.film_module_gateway import FilmModuleAgentGateway
from video_workbench.production_film_context import freeze_production_inputs
import test_film_creation_workflow as fixtures
from test_animation_skill_production import published
from test_film_creation_workflow import OWNER, completed_modules, env, module_request, start_module
from test_production_runs import wav
from test_skill_production_gateway import Peer, TOKEN


def original(env):
    op = start_module(env)
    env.workflow.tick()
    result = env.workflow.get_module(OWNER, op['id'])
    assert result['status'] == 'completed'
    return result


def revision_request(env, base, index=0, predecessor=None):
    request = module_request(env, index, predecessor)
    request.update(base_module_version_id=base['version_id'], instruction='保留原有对象和旁白，改善路径与遮挡。')
    return request


def revise(env, request, key='film-revision-input-1', creation=None):
    index = request['current_scene_index']
    peer = creation or env.workflow
    return peer.start_module(OWNER, env.project['id'], env.project['scenes'][index]['id'], request, key)


def reconfigure(env, *, caps=None, assets=None):
    old = env.creation
    return FilmCreationStore(env.store, runtime_capabilities=caps or old.runtime_capabilities,
        runtime_fingerprint=old.runtime, host_files=old.host_files, trusted_host_sha256=old.hosts,
        image=old.image, assets=old.assets if assets is None else assets, enabled=True)


def test_exact_previous_module_files_and_states_reach_same_frozen_author_without_rewriting(env):
    env.methods.append(published(0))
    base = original(env)
    package = env.workflow.module_package(OWNER, base['version_id'])
    original_bytes = canonical_bytes(package)
    request = revision_request(env, base)
    op = revise(env, request)
    pending = env.creation.operation('module', op['id'])
    assert pending['document']['schema'] == PENDING_SCHEMA
    assert pending['document']['core_task']['base_module'] == package['result']['module']
    assert pending['base_module_reference'] == dict(version_id=base['version_id'],
        package_sha256=digest(package), input_sha256=package['input_sha256'], result_sha256=package['result_sha256'])
    assert pending['request_sha256'] == digest(request) and pending['submit_attempted'] is False
    assert len(env.gateway.jobs) == 1
    env.workflow.tick()
    revised = env.workflow.get_module(OWNER, op['id'])
    assert revised['status'] == 'completed' and revised['version_id'] != base['version_id']
    delivered = env.gateway.documents[op['id']]
    assert delivered['base_module'] == package['result']['module']
    assert delivered['scene']['instruction'] == request['instruction']
    assert delivered['scene']['audio_sha256'] == package['input_document']['scene']['audio_sha256']
    assert delivered['constraints']['runtime_identity'] == package['input_document']['constraints']['runtime_identity']
    assert canonical_bytes(env.workflow.module_package(OWNER, base['version_id'])) == original_bytes
    assert env.gateway.profile == P.PROFILE
    assert P.VideoAnimationFilmModuleProfile().required_tools == ('read_inputs', 'skill_search', 'skill_read', 'write')


def test_second_scene_receives_both_exact_base_and_current_predecessor_reference(env):
    first, base = completed_modules(env)
    original_package = env.workflow.module_package(OWNER, base['version_id'])
    predecessor_package = env.workflow.module_package(OWNER, first['version_id'])
    request = revision_request(env, base, 1, first['version_id'])
    op = revise(env, request)
    core = env.creation.operation('module', op['id'])['document']['core_task']
    assert core['base_module'] == original_package['result']['module']
    assert core['film_context']['predecessor']['module']['states'] == predecessor_package['result']['module']['states']
    assert 'APP-FROZEN-PREDECESSOR-APPEARANCE' in core['scene']['instruction']
    assert canonical_bytes(predecessor_package['result']['module']['files']).decode() in core['scene']['instruction']
    env.workflow.tick()
    assert env.workflow.get_module(OWNER, op['id'])['status'] == 'completed'


def test_real_http_client_upload_preserves_complete_base_without_new_tools_or_job(env):
    base = original(env)
    expected = env.workflow.module_package(OWNER, base['version_id'])['result']['module']
    op = revise(env, revision_request(env, base))
    prepared = env.creation.prepare_module(op['id'], env.adapter)
    peer = Peer()
    peer.profile['profile'] = P.PROFILE
    with AgentClient('http://agent.invalid', TOKEN, transport=httpx.MockTransport(peer)) as client:
        gateway = FilmModuleAgentGateway(client, env.adapter)
        receipt = gateway.upload(prepared['document'])
        uploaded = json.loads(peer.uploads[P.INPUT_NAME])
        assert uploaded == prepared['document'] and uploaded['base_module'] == expected
        gateway.prepare_submission(op['id'], prepared['document'], prepared['deadline_at'], receipt)
    assert peer.count('POST', '/v1/jobs') == 0
    assert env.creation.operation('module', op['id'])['submit_attempted'] is False


def test_omitted_optional_field_keeps_legacy_request_and_history_fingerprints(env):
    request = module_request(env)
    op = start_module(env)
    with env.store.transaction() as db:
        original_row = tuple(db.execute('SELECT request_hash,document FROM film_module_operations WHERE id=?',
            (op['id'],)).fetchone())
    assert original_row[0] == digest(dict(project_id=env.project['id'], scene_id=env.project['scenes'][0]['id'], request=request))
    assert op['request_sha256'] == digest(request)
    recovery = FilmCreationStore.for_recovery(env.store)
    replay = recovery.start_module(OWNER, env.project['id'], env.project['scenes'][0]['id'], request, op['request_key'])
    assert replay['id'] == op['id']
    with env.store.transaction() as db:
        assert tuple(db.execute('SELECT request_hash,document FROM film_module_operations WHERE id=?',
            (op['id'],)).fetchone()) == original_row
    assert 'base_module_version_id' not in env.creation.operation('module', op['id'])['request']


def test_revision_request_replay_and_lost_submission_recover_frozen_base_without_source_lookup(env):
    base = original(env)
    request = revision_request(env, base)
    op = revise(env, request)
    env.gateway.lose_submit = True
    env.workflow.tick()
    pending = env.creation.operation('module', op['id'])
    assert pending['submit_attempted'] and pending['status'] == 'submitting'
    frozen = canonical_bytes(pending['document'])
    with env.store.transaction() as db:
        db.execute('DELETE FROM film_module_versions WHERE id=?', (base['version_id'],))
    recovery_store = FilmCreationStore.for_recovery(env.store)
    assert revise(env, request, creation=recovery_store)['id'] == op['id']
    resumed = FilmCreationWorkflow(recovery_store, env.gateway, env.adapter, env.renderer, enabled=False)
    resumed.tick()
    result = resumed.get_module(OWNER, op['id'])
    assert result['status'] == 'completed' and result['stop_proven'] is True
    assert canonical_bytes(recovery_store.operation('module', op['id'])['document']) == frozen
    assert len([call for call in env.gateway.calls if call[0] == 'submit' and call[1] == op['id']]) == 1
    assert len([call for call in env.gateway.calls if call[0] == 'lookup' and call[1] == op['id']]) == 1


def test_same_key_cannot_swap_base_or_add_a_base_to_a_legacy_operation(env):
    first = original(env)
    request = revision_request(env, first)
    op = revise(env, request)
    changed = dict(request, base_module_version_id='another-version')
    with pytest.raises(ServiceError, match='^idempotency_conflict$'):
        revise(env, changed)
    with pytest.raises(ServiceError, match='^idempotency_conflict$'):
        revise(env, request, key=first['request_key'])
    assert env.creation.operation('module', op['id'])['document']['core_task']['base_module'] is not None
    assert len(env.gateway.jobs) == 1


@pytest.mark.parametrize('invalid', [None, '', True, 'v' * 101])
def test_optional_base_field_must_be_omitted_or_an_exact_version_id(env, invalid):
    request = dict(module_request(env), base_module_version_id=invalid)
    with pytest.raises(ServiceError, match='^film_creation_request_invalid$'):
        revise(env, request)
    assert env.creation.active_operations() == [] and env.gateway.calls == []


def test_missing_or_unfinished_base_is_rejected_before_new_skill_or_agent_work(env):
    unfinished = start_module(env)
    before = list(env.catalog_calls), list(env.gateway.calls)
    request = revision_request(env, dict(version_id=unfinished['id']))
    with pytest.raises(ServiceError, match='^not_found$'):
        revise(env, request)
    assert (env.catalog_calls, env.gateway.calls) == before
    assert len(env.creation.active_operations()) == 1


@pytest.mark.parametrize('mismatch', ['owner', 'project', 'scene', 'target', 'transition_target', 'film_audio'])
def test_base_must_belong_to_same_owner_project_scene_film_audio_and_target(env, mismatch):
    first, second = completed_modules(env)
    index = 1 if mismatch in {'scene', 'transition_target'} else 0
    base = first if mismatch == 'scene' else second if index else first
    request = revision_request(env, base, index, first['version_id'] if index else None)
    expected = 'film_creation_base_module_mismatch'
    if mismatch == 'owner':
        with env.store.transaction() as db:
            db.execute('UPDATE film_module_versions SET owner=? WHERE id=?', ('different-owner', first['version_id']))
        expected = 'not_found'
    elif mismatch == 'project':
        other = env.store.create_project(OWNER, dict(title='另一个项目', script='另一个项目。'))
        with env.store.transaction() as db:
            row = db.execute('SELECT document FROM film_module_versions WHERE id=?', (first['version_id'],)).fetchone()
            version = json.loads(row[0]); version['project_id'] = other['id']
            db.execute('UPDATE film_module_versions SET project_id=?,document=? WHERE id=?',
                (other['id'], canonical_bytes(version).decode(), first['version_id']))
    elif mismatch == 'target':
        request['target']['module_id'] = 'different-target'
    elif mismatch == 'transition_target':
        request['target']['transition_target'] = dict(module_id='different-transition', namespace='different_transition')
    elif mismatch == 'film_audio':
        data = bytearray(wav(1)); data[44:46] = b'\x01\x00'
        env.project = env.store.upload_audio(OWNER, env.project['scenes'][0]['id'],
            dict(expected_revision=env.project['revision']), 'revised-original-recording', bytes(data))
        audio = env.project['scenes'][0]['audio_versions'][-1]
        request['film']['scenes'][0].update(audio_version_id=audio['id'], audio_sha256=audio['sha256'])
        request['film']['content_binding_sha256'] = digest(freeze_production_inputs(env.store, env.project, film_aware=True))
        request['film_sha256'], request['expected_revision'] = digest(request['film']), env.project['revision']
    before = list(env.gateway.calls), list(env.catalog_calls)
    with pytest.raises(ServiceError, match='^' + expected + '$'):
        revise(env, request)
    assert (env.gateway.calls, env.catalog_calls) == before
    assert env.creation.active_operations() == []


@pytest.mark.parametrize('mismatch', ['runtime', 'engine'])
def test_prior_module_needs_exact_admitted_runtime_and_an_admitted_engine(env, mismatch):
    base = original(env)
    caps = deepcopy(env.creation.runtime_capabilities)
    if mismatch == 'runtime':
        caps['runtime_identity']['admission_id'] = 'different-synthetic-admission'
    else:
        caps['allowed_engines'] = ['canvas']
    new_store = reconfigure(env, caps=caps)
    request = revision_request(env, base)
    request['configuration_sha256'] = new_store.configuration_sha256
    before = list(env.gateway.calls), list(env.catalog_calls)
    with pytest.raises(ServiceError, match='^film_creation_base_module_runtime_mismatch$'):
        revise(env, request, creation=new_store)
    assert (env.gateway.calls, env.catalog_calls) == before


def test_original_used_asset_checksums_cannot_silently_change(env, monkeypatch):
    old_raw = fixtures.raw_result

    def uses_asset(document):
        result = old_raw(document)
        result['module']['assets'] = ['assets/gsap.min.js']
        return result

    raw_asset = b'synthetic-custody-asset-not-executed'
    caps = deepcopy(env.creation.runtime_capabilities)
    caps['assets'] = {'gsap.min.js': hashlib.sha256(raw_asset).hexdigest()}
    creation = reconfigure(env, caps=caps, assets={'assets/gsap.min.js': raw_asset})
    env.creation = env.workflow.creation = env.gateway.creation = creation
    monkeypatch.setattr(fixtures, 'raw_result', uses_asset)
    base = original(env)
    changed_asset = b'changed-synthetic-asset-not-executed'
    caps['assets']['gsap.min.js'] = hashlib.sha256(changed_asset).hexdigest()
    new_store = reconfigure(env, caps=caps, assets={'assets/gsap.min.js': changed_asset})
    request = revision_request(env, base)
    request['configuration_sha256'] = new_store.configuration_sha256
    with pytest.raises(ServiceError, match='^film_creation_base_module_assets_changed$'):
        revise(env, request, creation=new_store)
    assert len(env.gateway.jobs) == 1


@pytest.mark.parametrize('source_bytes,code', [
    (2 * 1024 * 1024, 'film_module_input_too_large'),
    (1280 * 1024, 'film_creation_base_module_input_too_large'),
])
def test_actual_core_budgets_reject_before_reservation_without_truncating_source(env, monkeypatch, source_bytes, code):
    old_raw = fixtures.raw_result

    def large_original(document):
        result = old_raw(document)
        module = result['module']
        entry, source = module['entry'], module['files'][module['entry']]
        module['files'][entry] = source + '\n//' + '\\' * (MAX_SOURCE_FILE_BYTES - len(source.encode()) - 3)
        remaining = source_bytes - MAX_SOURCE_FILE_BYTES
        index = 0
        while remaining:
            size = min(remaining, MAX_SOURCE_FILE_BYTES)
            module['files'][f"modules/{module['namespace']}/reference-{index}.js"] = '//' + '\\' * (size - 2)
            remaining -= size
            index += 1
        return result

    monkeypatch.setattr(fixtures, 'raw_result', large_original)
    base = original(env)
    package = env.workflow.module_package(OWNER, base['version_id'])
    original_source = canonical_bytes(package['result']['module'])
    assert sum(len(raw.encode()) for raw in package['result']['module']['files'].values()) == source_bytes
    request = revision_request(env, base)
    calls_before = list(env.gateway.calls)
    with pytest.raises(ServiceError, match='^' + code + '$') as exc:
        revise(env, request)
    assert exc.value.status == 413
    assert env.creation.active_operations() == []
    assert env.gateway.calls == calls_before
    assert canonical_bytes(env.workflow.module_package(OWNER, base['version_id'])['result']['module']) == original_source
