"""Offline candidate-factory wiring; synthetic preflights are not acceptance."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from pydantic import SecretStr
import pytest

from agent_service.contracts import ServiceError
from agent_service.deploy import acceptance_probe as probe
from agent_service.engines import EngineRequest, StopEvidence
from agent_service import resources, runtime, tool_client
from test_acceptance_probe import config
from test_catalog import image_packages


class OfflineRuntime:
    """No subprocess, academic query, model request or actual kernel preflight."""
    def __init__(self, settings, *, profile):
        self.settings, self.profile = settings, profile

    def _verify_resources(self):
        pass

    def _readiness_versions(self):
        return '0.73.1', '24.21.0'

    def execute(self, wire, workspace, emit, cancel):
        pass


class Stopper:
    consumed = False
    def __init__(self, *, proven=True):
        self.proven, self.events = proven, []

    def stop(self, execution_id):
        self.events.append('stop')

    def prove_stopped(self, execution_id):
        self.events.append('proof')
        return StopEvidence(execution_id, self.proven, ('offline-fixture-writer',))


@pytest.fixture
def factory(image_packages, monkeypatch, tmp_path):
    monkeypatch.setattr(resources, 'measured_package', lambda *args: (
        tmp_path, tmp_path / 'synthetic-manifest.json', 'c' * 64,
        (tmp_path / 'SKILL.md',), ('d' * 64,)))
    monkeypatch.setattr(runtime, 'PiRuntime', OfflineRuntime)

    def build(profile, *, stopper=None):
        value = config(profile)
        credentials = SimpleNamespace(model_proxy_token=SecretStr('offline-attempt-only-grant'),
                                      model_proxy_expires_at=time.time() + 300)
        scope = SimpleNamespace(attempt_id=str(uuid4()), agent_uid=2100000000,
                                preview_uid=2100000001)
        observations = dict(model_round_trip={'status': 200}, isolation=dict.fromkeys(
            ('control_unreadable', 'input_write_denied', 'caps_empty', 'no_new_privileges',
             'read_only_mount_measured', 'arbitrary_mount_denied', 'detached_writers_stopped',
             'private_kernel_scope'), True))
        driver = probe.build_candidate_driver(value, credentials, scope, stopper or Stopper(), observations)
        return SimpleNamespace(driver=driver, config=value, credentials=credentials,
                               scope=scope, observations=observations)
    return build


@pytest.mark.parametrize('profile', sorted(probe.PROFILES))
def test_all_seven_candidate_factories_keep_exact_contract_tools_and_capabilities(factory, profile):
    run = factory(profile)
    driver = run.driver
    expected = ('text', 'image') if profile in {'ppt.generate.v1', 'ppt.revise.v1'} else ('text',)
    assert driver.runtime.settings.model_input == expected
    assert driver.runtime.settings.model_api == 'openai-responses'
    assert driver.runtime.settings.model_env == {'AGENT_MODEL_API_KEY': 'offline-attempt-only-grant'}
    academic = profile == 'research.generate.v1'
    assert driver.runtime.profile.extension_paths == (
        (Path('/opt/pi/node_modules/agent-service-academic.mjs'),) if academic else ())
    assert callable(getattr(driver, 'academic_observer', None)) is academic
    assert driver.runtime.profile.policy.enabled is False
    assert run.config.profile.enabled is False
    assert all(not route['enabled'] for route in run.config.execution_catalog['routes'])
    assert run.observations['capability']['engine'] == 'pi'


@pytest.mark.parametrize('profile', sorted(probe.PROFILES - probe.FIXTURE_PROFILES))
def test_noninitial_stages_require_an_explicit_scenario_instead_of_fabricated_history(profile):
    with pytest.raises(ServiceError) as failure:
        probe.fixture(profile)
    assert failure.value.code == 'candidate_scenario_required'


@pytest.mark.parametrize('profile', ['research.generate.v1', 'research.revise.v1', 'ppt.generate.v1'])
def test_only_research_generation_proxy_gets_public_https_for_fixed_academic_bridge(image_packages, profile):
    value = config(profile)
    rendered = probe.render_probe(value, name='candidate-academic',
        worker_image='registry.invalid/pi@' + value.profile.image_digest, node='reviewed-node',
        uid_start=2100000000, bootstrap_secret='offline-bootstrap', upstream_secret='offline-upstream')
    policy = next(item for item in rendered['items'] if item['kind'] == 'NetworkPolicy')
    public = [destination['ipBlock'] for rule in policy['spec']['egress']
              for destination in rule['to'] if 'ipBlock' in destination]
    if profile == 'research.generate.v1':
        assert len(public) == 1 and public[0]['cidr'] == '0.0.0.0/0'
        assert {'10.0.0.0/8', '127.0.0.0/8', '169.254.0.0/16', '192.168.0.0/16'} <= set(public[0]['except'])
        assert policy['spec']['egress'][-1]['ports'] == [{'protocol': 'TCP', 'port': 443}]
    else:
        assert public == []


@pytest.mark.parametrize('proven', [False, True])
def test_candidate_academic_observations_are_saved_only_after_proven_native_stop(factory, monkeypatch, tmp_path, proven):
    stopper = Stopper(proven=proven)
    run = factory('research.generate.v1', stopper=stopper)
    saved = []
    def observe(origin, token, workspace):
        assert stopper.events[-1] == 'proof'
        saved.append((origin, token, workspace))
    monkeypatch.setattr(tool_client, 'save_academic_observations', observe)
    created = datetime.now(timezone.utc)
    deadline = created + timedelta(seconds=30)
    task = dict(schema_version='1', business_task_id=str(uuid4()), topic='Offline research fixture',
        mode='brief', created_at=created.isoformat(), execution_deadline_at=deadline.isoformat(),
        source_policy={'providers': ['crossref'], 'max_results': 1})
    request = EngineRequest(run.scope.attempt_id, 'research.generate.v1', task, 30, deadline)
    result = run.driver.execute_with_context(request, tmp_path / 'workspace', lambda _: None,
        threading.Event(), context=SimpleNamespace(inputs=None, checkpoint=None))
    if proven:
        assert result.status == 'completed' and result.stopped is True
        assert saved == [(run.config.model.proxy_base_url, 'offline-attempt-only-grant', tmp_path / 'workspace')]
    else:
        assert result.status == 'quarantined' and saved == []
