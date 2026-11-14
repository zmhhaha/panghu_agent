"""Formal resource custody and HTTP behavior; synthetic peers prove no admission."""
from contextlib import closing, contextmanager
from copy import deepcopy
import json
import threading
import time
import sys
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.animation_creation_http import CreationHTTPServer, CreationAPIClient
from video_workbench.animation_skill_production import SkillProductionAdapter
from video_workbench.manim_method_coordinator import ManimMethodCoordinator
from video_workbench.manim_film_resource_service import ManimFilmResourceService, validate_request
from video_workbench.manim_film_resource_http import ManimFilmResourceClient
from test_manim_method_integration import RecipeGateway, ResourceExecutor, OWNER
from test_animation_creation_executor import config, NoNetworkAgentClient


TOKEN = 'formal-resource-transport-test-' + 'a' * 32


def request(**changes):
    value = dict(operation_id=str(uuid4()), project_id='project', scene_id='scene',
        film_sha256='a' * 64, goal='Explain an original shear preserving area.', guidance='',
        duration_seconds=4, scene_start_frame=24, scene_end_frame=72,
        resource_authoring=dict(kind='manim', width=320, height=180,
            frame_count=3, global_start_frame=30), deadline_at=time.time() + 300)
    value.update(changes)
    return value


def setup(tmp_path, *, candidate=True):
    gateway, executor = RecipeGateway(), ResourceExecutor()
    adapter = SkillProductionAdapter(lambda owner: {'methods': []}, None, tmp_path / 'skills.sqlite3')
    coordinator = ManimMethodCoordinator(tmp_path / 'coordinator', gateway=gateway,
        executor=executor, skill_adapter=adapter, candidate=candidate)
    service = ManimFilmResourceService(tmp_path / 'formal', coordinator=coordinator, candidate=candidate)
    return service, gateway, executor


def finish(service, op):
    for _ in range(15):
        service.advance(OWNER, op)
        result = service.get(OWNER, op)
        if result['status'] in {'completed', 'failed', 'cancelled'}:
            return result
    raise AssertionError('resource did not terminate')


def test_formal_resource_is_owner_scoped_and_generated_only_by_advance(tmp_path):
    service, gateway, executor = setup(tmp_path)
    req = request()
    initial = service.start(OWNER, req)
    assert initial['status'] == 'queued'
    assert initial['request_sha256'] == digest(req)
    assert not service.capabilities()['enabled']
    for _ in range(3):
        assert service.get(OWNER, req['operation_id'])['status'] == 'queued'
    assert gateway.submissions == executor.calls == 0
    result = finish(service, req['operation_id'])
    assert result['status'] == 'completed' and result['stop_proven']
    assert result['descriptor']['scope']['experiment_id'] == 'film-' + req['operation_id']
    assert gateway.submissions == executor.calls == 1
    env = service.envelope(OWNER, req['operation_id'])
    assert env['sha256'] == result['descriptor']['envelope_sha256']
    assert service.start(OWNER, req) == result
    assert gateway.submissions == executor.calls == 1
    with pytest.raises(ServiceError, match='not_found'):
        service.envelope('another-owner', req['operation_id'])


def test_changed_request_and_resource_scope_are_not_reinterpreted(tmp_path):
    service, _, _ = setup(tmp_path)
    req = request()
    service.start(OWNER, req)
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        service.start(OWNER, req | {'goal': 'Different story'})


def test_restart_after_unknown_recipe_admission_never_submits_twice(tmp_path):
    service, gateway, executor = setup(tmp_path)
    gateway.unknown = True
    req = request()
    service.start(OWNER, req)
    for _ in range(3):
        service.advance(OWNER, req['operation_id'])
    assert service.get(OWNER, req['operation_id'])['status'] == 'recipe_unknown'
    resumed = ManimFilmResourceService(service.root, coordinator=service.coordinator, candidate=True)
    assert finish(resumed, req['operation_id'])['status'] == 'completed'
    assert gateway.submissions == executor.calls == 1


def test_cancelled_before_claim_never_schedules_recipe_or_resource(tmp_path):
    service, gateway, executor = setup(tmp_path)
    req = request()
    service.start(OWNER, req)
    service.cancel(OWNER, req['operation_id'])
    result = finish(service, req['operation_id'])
    assert result['status'] == 'cancelled' and result['stop_proven']
    assert gateway.submissions == executor.calls == 0
    with pytest.raises(ServiceError, match='not_ready'):
        service.envelope(OWNER, req['operation_id'])


def test_cancel_unknown_reservation_creates_terminal_tombstone_without_advance(tmp_path, monkeypatch):
    service, gateway, executor = setup(tmp_path, candidate=False)
    req = request(deadline_at=time.time() - 60)
    def forbidden(*args, **kwargs):
        raise AssertionError('cancellation must not advance producer')
    monkeypatch.setattr(service.coordinator, 'advance', forbidden)
    result = service.cancel_reservation(OWNER, req)
    assert result['status'] == 'cancelled' and result['stop_proven']
    assert result['cancel_requested'] and result['request_sha256'] == digest(req)
    assert result['project_id'] == req['project_id'] and result['film_sha256'] == req['film_sha256']
    assert service.pending() == []
    service.advance(OWNER, req['operation_id'])
    assert service.start(OWNER, req) == result  # A delayed original POST cannot revive it.
    assert service.cancel_reservation(OWNER, req) == result
    assert service.coordinator._get(OWNER, 'film-' + req['operation_id']) is None
    assert gateway.submissions == executor.calls == 0


def test_cancel_exact_existing_reservation_keeps_stop_lifecycle(tmp_path, monkeypatch):
    service, gateway, executor = setup(tmp_path)
    req = request()
    service.start(OWNER, req)
    original = service.coordinator.advance
    monkeypatch.setattr(service.coordinator, 'advance', lambda *a, **kw: pytest.fail('unexpected advance'))
    result = service.cancel_reservation(OWNER, req)
    assert result['cancel_requested'] and not result['stop_proven']
    assert result['status'] == 'queued' and service.pending()
    assert gateway.submissions == executor.calls == 0
    monkeypatch.setattr(service.coordinator, 'advance', original)
    assert finish(service, req['operation_id'])['status'] == 'cancelled'
    assert gateway.submissions == executor.calls == 0


def test_cancel_reservation_cannot_change_request_or_existing_owner(tmp_path):
    service, gateway, executor = setup(tmp_path)
    req = request()
    before = service.start(OWNER, req)
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        service.cancel_reservation(OWNER, req | {'guidance': 'changed request'})
    with pytest.raises(ServiceError, match='not_found'):
        service.cancel_reservation('another-owner', req)
    with pytest.raises(ServiceError, match='not_found'):
        service.get('another-owner', req['operation_id'])
    assert service.get(OWNER, req['operation_id']) == before
    service.cancel_reservation(OWNER, req)
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        service.start(OWNER, req | {'goal': 'changed after cancellation'})
    assert gateway.submissions == executor.calls == 0


def test_cancel_unknown_reservation_fails_closed_if_producer_custody_survived(tmp_path):
    service, gateway, executor = setup(tmp_path)
    req = request()
    service.start(OWNER, req)
    service.advance(OWNER, req['operation_id'])
    with closing(service._db()) as db:
        db.execute('DELETE FROM film_resources WHERE owner=? AND id=?', (OWNER, req['operation_id']))
    with pytest.raises(ServiceError, match='ledger_missing'):
        service.cancel_reservation(OWNER, req)
    assert gateway.submissions == executor.calls == 0


def test_missing_formal_ledger_cannot_adopt_coordinator_history(tmp_path):
    service, _, _ = setup(tmp_path)
    req = request()
    service.start(OWNER, req)
    finish(service, req['operation_id'])
    service.path.unlink()
    with pytest.raises(ServiceError, match='ledger_missing'):
        ManimFilmResourceService(service.root, coordinator=service.coordinator, candidate=True)


def test_default_disabled_does_not_borrow_laboratory_admission(tmp_path):
    service, gateway, executor = setup(tmp_path, candidate=False)
    service.coordinator.enabled = True  # A laboratory-like generation gate is insufficient.
    with pytest.raises(ServiceError, match='not_admitted'):
        service.start(OWNER, request())
    assert not service.capabilities()['enabled']
    assert gateway.submissions == executor.calls == 0


def test_cancel_between_recipe_prepare_and_paid_claim_is_rechecked(tmp_path):
    service, gateway, executor = setup(tmp_path)
    req = request()
    service.start(OWNER, req)
    prepare = gateway.prepare_submission
    def cancel_after_prepare(*args, **kwargs):
        admit = prepare(*args, **kwargs)
        service.cancel(OWNER, req['operation_id'])
        return admit
    gateway.prepare_submission = cancel_after_prepare
    result = finish(service, req['operation_id'])
    assert result['status'] == 'cancelled' and result['stop_proven']
    assert gateway.submissions == executor.calls == 0


def test_cancel_before_native_execution_never_runs_container(tmp_path):
    service, gateway, executor = setup(tmp_path)
    req = request()
    service.start(OWNER, req)
    for _ in range(5):
        service.advance(OWNER, req['operation_id'])
    assert service.get(OWNER, req['operation_id'])['status'] == 'resource_claimed'
    lookup = executor.lookup
    def cancel_after_lookup(**scope):
        result = lookup(**scope)
        service.cancel(OWNER, req['operation_id'])
        return result
    executor.lookup = cancel_after_lookup
    result = finish(service, req['operation_id'])
    assert result['status'] == 'cancelled' and result['stop_proven']
    assert gateway.submissions == 1 and executor.calls == 0


def test_disabled_gate_holds_unsubmitted_work_after_restart(tmp_path):
    service, gateway, executor = setup(tmp_path)
    req = request()
    service.start(OWNER, req)
    service.advance(OWNER, req['operation_id'])
    service.advance(OWNER, req['operation_id'])
    assert service.get(OWNER, req['operation_id'])['status'] == 'uploaded'
    service.candidate = False
    service.advance(OWNER, req['operation_id'])
    result = service.get(OWNER, req['operation_id'])
    assert result['status'] == 'uploaded' and result['error'] == 'film_manim_not_admitted'
    assert gateway.submissions == executor.calls == 0


def test_deadline_before_claim_cancels_without_new_work(tmp_path):
    service, gateway, executor = setup(tmp_path)
    req = request()
    service.start(OWNER, req)
    service.clock = lambda: req['deadline_at'] + 1
    assert finish(service, req['operation_id'])['status'] == 'cancelled'
    assert gateway.submissions == executor.calls == 0


def test_envelope_is_revalidated_against_exact_stopped_resource(tmp_path):
    service, _, _ = setup(tmp_path)
    req = request()
    service.start(OWNER, req)
    finish(service, req['operation_id'])
    original = service.coordinator._get(OWNER, 'film-' + req['operation_id'])
    changed = deepcopy(original)
    entry = next(iter(changed['envelope']['files'].values()))
    entry['sha256'] = '0' * 64
    service.coordinator._save(changed, original)
    with pytest.raises(ServiceError):
        service.envelope(OWNER, req['operation_id'])


@pytest.mark.parametrize('changes', [
    {'operation_id': '../escape'}, {'duration_seconds': 61}, {'duration_seconds': float('nan')},
    {'scene_start_frame': 31}, {'scene_end_frame': 32}, {'scene_end_frame': 97},
    {'resource_authoring': {'kind': 'manim', 'frame_count': 61}},
    {'resource_authoring': {'kind': 'manim', 'source': 'not caller supplied'}},
    {'deadline_at': True}, {'goal': ''}, {'unexpected': 'field'},
])
def test_untrusted_resource_options_cannot_expand_clock_code_or_bounds(changes):
    with pytest.raises(ServiceError):
        validate_request(request(**changes))


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_http_roundtrip_and_read_only_get_envelope(tmp_path):
    service, gateway, executor = setup(tmp_path)
    unused = SimpleNamespace(pending=lambda **_: [])
    server = CreationHTTPServer(unused, TOKEN, pump=False, film_resource_service=service)
    with serving(server):
        client = ManimFilmResourceClient('http://127.0.0.1:' + str(server.server_port), TOKEN)
        assert not client.capabilities(OWNER)['enabled']
        req = request()
        assert client.start(OWNER, req)['status'] == 'queued'
        assert client.get(OWNER, req['operation_id'])['request_sha256'] == digest(req)
        assert gateway.submissions == executor.calls == 0
        finish(service, req['operation_id'])
        envelope = client.envelope(OWNER, req['operation_id'])
        assert envelope['sha256'] == client.get(OWNER, req['operation_id'])['descriptor']['envelope_sha256']
        with pytest.raises(ServiceError, match='not_found'):
            client.get('another-owner', req['operation_id'])
        with pytest.raises(ServiceError, match='unauthorized'):
            ManimFilmResourceClient(client.base_url, 'b' * 40).get(OWNER, req['operation_id'])
        assert gateway.submissions == executor.calls == 1


def test_http_cancel_and_unconfigured_capability_are_read_only(tmp_path):
    service, gateway, executor = setup(tmp_path)
    unused = SimpleNamespace(pending=lambda **_: [])
    with serving(CreationHTTPServer(unused, TOKEN, pump=False, film_resource_service=service)) as server:
        client = ManimFilmResourceClient('http://127.0.0.1:' + str(server.server_port), TOKEN)
        req = request()
        client.start(OWNER, req)
        assert client.cancel(OWNER, req['operation_id'])['cancel_requested']
        finish(service, req['operation_id'])
        assert client.get(OWNER, req['operation_id'])['status'] == 'cancelled'
        assert gateway.submissions == executor.calls == 0
    with serving(CreationHTTPServer(unused, TOKEN, pump=False)) as server:
        client = ManimFilmResourceClient('http://127.0.0.1:' + str(server.server_port), TOKEN)
        assert client.capabilities(OWNER)['status'] == 'not_configured'


def test_http_cancel_unknown_reservation_and_exact_conflicts(tmp_path, monkeypatch):
    service, gateway, executor = setup(tmp_path)
    monkeypatch.setattr(service.coordinator, 'advance', lambda *a, **kw: pytest.fail('unexpected advance'))
    unused = SimpleNamespace(pending=lambda **_: [])
    with serving(CreationHTTPServer(unused, TOKEN, pump=False, film_resource_service=service)) as server:
        client = ManimFilmResourceClient('http://127.0.0.1:' + str(server.server_port), TOKEN)
        req = request()
        result = client.cancel_reservation(OWNER, req)
        assert result['status'] == 'cancelled' and result['stop_proven']
        assert result['request_sha256'] == digest(req)
        assert client.start(OWNER, req) == result
        assert client.cancel_reservation(OWNER, req) == result
        with pytest.raises(ServiceError, match='idempotency_conflict'):
            client.cancel_reservation(OWNER, req | {'goal': 'different'})
        with pytest.raises(ServiceError, match='not_found'):
            client.cancel_reservation('another-owner', req)
        with pytest.raises(ServiceError, match='cancel_request_invalid'):
            client._request(OWNER, 'POST', '/api/film-manim/operations/'
                + str(uuid4()) + '/cancel-reservation', req)
        existing = request()
        client.start(OWNER, existing)
        cancelled = client.cancel_reservation(OWNER, existing)
        assert cancelled['cancel_requested'] and not cancelled['stop_proven']
        assert client.get(OWNER, existing['operation_id']) == cancelled
        assert gateway.submissions == executor.calls == 0


def recipe_config(config):
    token = config['root'].parent / 'recipe-token'
    token.write_text('dedicated-recipe-test-' + 'r' * 40, encoding='ascii')
    return dict(config, method_project_runtime='hyperframes-core-bridge',
        manim_recipe_agent_token_file=token, manim_resource_image=ResourceExecutor.image)


def test_executor_configures_separate_formal_coordinator_without_borrowing_lab_gate(config):
    from video_workbench import animation_creation_executor as executor
    server, client = executor.create_server(**recipe_config(config))
    try:
        formal = server.film_resource_service
        assert formal is not None and not formal.enabled and formal.admission is None
        assert not formal.coordinator.enabled and formal.pending() == []
        assert formal.coordinator.root != server.method_service.manim.root
        assert formal.coordinator.gateway.skill_adapter is not server.method_service.manim.skill_adapter
        assert formal.coordinator.executor is server.method_service.manim.executor
        assert server.film_resource_pump.thread.is_alive()
        assert all(peer.calls == [] for peer in NoNetworkAgentClient.instances)
    finally:
        server.server_close()
        client.close()
    assert not server.film_resource_pump.thread.is_alive()


def test_executor_enables_formal_only_after_independent_validator_returns(config, monkeypatch):
    from video_workbench import animation_creation_executor as executor
    seen = []
    def checked(path, *, resource_service):
        assert resource_service.enabled is resource_service.coordinator.enabled is False
        assert resource_service.pending() == []
        seen.append(path)
        return {'sha256': 'f' * 64}  # Configuration fixture, not a real admission.
    module = ModuleType('video_workbench.manim_film_admission')
    module.validate_admission = checked
    monkeypatch.setitem(sys.modules, module.__name__, module)
    expected = config['root'].parent / 'independent-film-admission.json'
    server, client = executor.create_server(**dict(recipe_config(config), film_manim_admission_file=expected))
    try:
        assert seen == [expected]
        assert server.film_resource_service.capabilities()['admission_sha256'] == 'f' * 64
        assert server.film_resource_service.enabled is server.film_resource_service.coordinator.enabled is True
        assert all(peer.calls == [] for peer in NoNetworkAgentClient.instances)
    finally:
        server.server_close()
        client.close()


def test_executor_rejected_formal_admission_closes_clients_without_starting_work(config, monkeypatch):
    from video_workbench import animation_creation_executor as executor
    def refused(*args, **kwargs):
        raise ServiceError('film_manim_evidence_changed', 503)
    module = ModuleType('video_workbench.manim_film_admission')
    module.validate_admission = refused
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(ServiceError, match='evidence_changed'):
        executor.create_server(**dict(recipe_config(config), film_manim_admission_file='not-evidence'))
    assert all(peer.close_count == 1 and peer.calls == [] for peer in NoNetworkAgentClient.instances)
