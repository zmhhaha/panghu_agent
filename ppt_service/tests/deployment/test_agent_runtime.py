"""Cutover guards with explicit synthetic Kubernetes/DB peers; no live I/O."""
import json
from types import SimpleNamespace

import pytest

from ppt_service.deploy.agent import runtime


def manifest(enabled=True):
    return runtime.render(controller_image='registry.invalid/ppt-controller@sha256:' + 'a' * 64,
        worker_image='registry.invalid/ppt-validator@sha256:' + 'b' * 64,
        kube_api_cidr=['192.168.137.101/32'], enabled=enabled)


class Scenario:
    def __init__(self):
        self.calls = []
        self.config_enabled, self.running_env = 'false', 'disabled'
        self.controller_replicas, self.pods = 0, []
        self.state = dict(revision=4, active_jobs=0, occupied_slots=0, pending_outbox=0,
                          parsing_assets=0, outbox_runtime_access=True)
        self.profiles = [dict(profile='ppt.' + phase + '.v1', profile_revision='v1',
                              configured=True, ready=True)
                         for phase in ('plan_initial','plan_options','prepare','generate','revise')]

    def runner(self, args, **kwargs):
        self.calls.append(args)
        value = None
        if 'pvc' in args:
            value = dict(spec={'accessModes': ['ReadWriteMany']}, status={'phase': 'Bound'})
        elif 'secret' in args:
            key = 'DATABASE_URL' if 'ppt-database-runtime' in args else 'token'
            value = {'data': {key: 'c3ludGhldGlj'}}
        elif 'get' in args and 'configmap' in args:
            value = {'data': {'PPT_EXECUTION_ENABLED': self.config_enabled}}
        elif 'get' in args and 'deployment' in args and args[-1] == 'json':
            value = {'spec': {'replicas': self.controller_replicas if runtime.CONTROLLER in args else 1}}
        elif 'get' in args and 'pods' in args:
            value = {'items': self.pods}
        elif 'exec' in args and 'deploy/ppt-site' in args:
            assert args[args.index('-c') + 1] == 'site'
            return SimpleNamespace(returncode=0, stdout=self.running_env)
        elif 'exec' in args and 'postgres-0' in args:
            assert args[args.index('-n') + 1] == 'data'
            assert args[args.index('-d') + 1] == 'ppt_service'
            assert 'INSERT' not in runtime.DRAIN_QUERY.replace("'INSERT'", '')
            value = self.state
        elif 'exec' in args and 'deploy/agent-service-api' in args:
            value = {'profiles': self.profiles}
        return SimpleNamespace(returncode=0, stdout=json.dumps(value) if value is not None else 'existing')


@pytest.mark.parametrize('changed,error', [
    ('config', 'site_admission_must_be_paused'),
    ('process', 'website_must_restart'),
    ('controller', 'old_controller_must_be_drained'),
    ('pod', 'old_execution_pods_must_be_gone'),
    ('revision', 'schema4_runtime_access'),
    ('queued_successor', 'schema4_runtime_access'),
    ('slot', 'schema4_runtime_access'),
    ('uncertain_submission', 'schema4_runtime_access'),
    ('parser', 'schema4_runtime_access'),
    ('grants', 'schema4_runtime_access'),
    ('capabilities', 'all_five_ppt_capabilities'),
])
def test_switch_rejects_unsettled_old_backend_before_any_cluster_write(changed, error):
    scenario = Scenario()
    if changed == 'config': scenario.config_enabled = 'true'
    elif changed == 'process': scenario.running_env = 'enabled'
    elif changed == 'controller': scenario.controller_replicas = 1
    elif changed == 'pod': scenario.pods = [{'metadata': {'name': 'stopping-worker'}}]
    elif changed == 'revision': scenario.state['revision'] = 3
    elif changed == 'queued_successor': scenario.state['active_jobs'] = 1
    elif changed == 'slot': scenario.state['occupied_slots'] = 1
    elif changed == 'uncertain_submission': scenario.state['pending_outbox'] = 1
    elif changed == 'parser': scenario.state['parsing_assets'] = 1
    elif changed == 'grants': scenario.state['outbox_runtime_access'] = False
    else: scenario.profiles[0]['ready'] = False
    with pytest.raises(RuntimeError, match=error):
        runtime.deploy(manifest(), runner=scenario.runner, enable_site=True)
    assert not any('apply' in call or 'patch' in call or 'scale' in call for call in scenario.calls)


def test_enabled_switch_checks_actual_readiness_before_apply_and_site_admission():
    scenario = Scenario()
    runtime.deploy(manifest(), runner=scenario.runner, enable_site=True)
    checks = next(n for n, call in enumerate(scenario.calls) if 'deploy/agent-service-api' in call)
    apply = next(n for n, call in enumerate(scenario.calls) if 'apply' in call)
    patch = next(n for n, call in enumerate(scenario.calls) if 'patch' in call)
    assert checks < apply < patch
    assert 'deployment/' + runtime.CONTROLLER in scenario.calls[patch - 1]
    assert json.loads(scenario.calls[patch][-1])['data'] == {
        'PPT_EXECUTION_ENABLED': 'true', 'PPT_EXECUTION_BACKEND': 'agent-service'}


def test_disabled_staging_still_requires_drain_but_can_precede_ppt_route_acceptance():
    scenario = Scenario()
    scenario.profiles = []
    runtime.deploy(manifest(enabled=False), runner=scenario.runner)
    assert any('apply' in call for call in scenario.calls)
    assert not any('deploy/agent-service-api' in call or 'patch' in call for call in scenario.calls)


def test_readiness_selects_the_requested_revision_without_discarding_legacy_profiles():
    scenario = Scenario()
    scenario.profiles = [dict(row, configured=False, ready=False) for row in scenario.profiles] + [
        dict(row, profile_revision='v2') for row in scenario.profiles]
    runtime.require_capabilities('agent-service', 'v2', runner=scenario.runner)
    scenario.profiles[-1]['ready'] = False
    with pytest.raises(RuntimeError, match='all_five_ppt_capabilities'):
        runtime.require_capabilities('agent-service', 'v2', runner=scenario.runner)


def test_legacy_readiness_cannot_substitute_for_a_missing_target_revision():
    scenario = Scenario()
    with pytest.raises(RuntimeError, match='all_five_ppt_capabilities'):
        runtime.require_capabilities('agent-service', 'v2', runner=scenario.runner)
