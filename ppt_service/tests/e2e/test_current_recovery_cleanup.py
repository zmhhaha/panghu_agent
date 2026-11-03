"""Focused server-only tests of the real recovery runner's cleanup CAS safety.

Read the adjacent Git-controlled source and extract its actual functions with
AST; never import or invoke the administrator entrypoint. Kubernetes and writes
are finite in-memory fixtures. No Docker, network, files outside this source
read, database, application task, or Kubernetes resource is accessed.

The repository owner runs this file only on the server. Do not run locally.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


RUNNER = Path(__file__).with_name('run_current_recovery_kubernetes.py')
OWNER = 'ppt.panghuer.top/recovery-owner'
ROLE = 'ppt.panghuer.top/recovery-role'
FINALIZER = 'agent-service.panghuer.top/observed-stop'
UID = '5ee27a9d-9f1c-4aad-8b43-c5119ed34027'
NAMESPACE = 'ppt-recovery-synthetic-cleanup-test'
RUN_ID = 'synthetic-cleanup-test'
DIGEST = 'sha256:' + 'a' * 64
IMAGE = 'registry.invalid/isolated-fixture:latest@' + DIGEST
SECRET = b'SYNTHETIC-SECRET-TO-REDACT'


def real_functions():
    """Compile actual functions only; importing the live runner is prohibited."""
    tree = ast.parse(RUNNER.read_text(encoding='utf-8'), filename=str(RUNNER))
    names = ('Failure', 'require', 'terminated', 'never_started_observation')
    nodes = []
    for name in names:
        found = [node for node in tree.body
                 if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == name]
        if len(found) != 1:
            raise AssertionError('Expected one real top-level definition: ' + name)
        nodes.append(found[0])
    mains = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main']
    if len(mains) != 1:
        raise AssertionError('Expected one administrator main function')
    for name in ('validate_owned_pod', 'cleanup_finalizer'):
        found = [node for node in mains[0].body if isinstance(node, ast.FunctionDef) and node.name == name]
        if len(found) != 1:
            raise AssertionError('Expected one real nested definition: ' + name)
        nodes.append(found[0])
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    return compile(module, str(RUNNER), 'exec')


def pod(version, *, name='agent-fixture', role='worker', finalizers=None, stopped=True):
    metadata = {'name': name, 'namespace': NAMESPACE, 'uid': UID,
        'resourceVersion': version, 'labels': {OWNER: RUN_ID, ROLE: role},
        'finalizers': [FINALIZER] if finalizers is None else list(finalizers)}
    if role == 'worker':
        metadata['deletionTimestamp'] = '2026-11-03T01:00:00Z'
    state = ({'terminated': {'exitCode': 0, 'containerID': 'containerd://synthetic',
        'finishedAt': '2026-11-03T01:00:01Z', 'reason': 'Completed'}}
        if stopped else {'running': {'startedAt': '2026-11-03T00:59:00Z'}})
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': metadata,
        'spec': {'containers': [{'name': role, 'image': IMAGE}]},
        'status': {'phase': 'Succeeded' if stopped else 'Running', 'containerStatuses': [{
            'name': role, 'imageID': IMAGE, 'restartCount': 0, 'state': state}]}}


def response(value=None, *, failed=False):
    return SimpleNamespace(returncode=1 if failed else 0,
        stdout=b'' if value is None else json.dumps(value).encode(),
        stderr=b'Conflict: ' + SECRET if failed else b'')


class Capsule:
    """Finite observations and API responses; real identity/state logic is kept."""
    def __init__(self, observations, responses):
        self.observations = list(deepcopy(observations))
        self.responses = list(responses)
        self.events, self.patches, self.files = [], [], {}
        self.namespace = {'json': json, 'OWNER': OWNER, 'ROLE': ROLE, 'FINALIZER': FINALIZER,
            'namespace': NAMESPACE, 'reservation': {'run_id': RUN_ID}, 'pod_history': {},
            'args': SimpleNamespace(worker_image=IMAGE, control_image=IMAGE, postgres_image=IMAGE),
            'output': Path('/in-memory-cleanup-evidence'), 'private_strings': {SECRET},
            'sha': lambda raw: hashlib.sha256(raw).hexdigest(),
            'own_pod': self.own_pod, 'kube': self.kube, 'write': self.write}
        exec(real_functions(), self.namespace)
        self.Failure = self.namespace['Failure']

    def own_pod(self, name, uid=None, role=None):
        if not self.observations:
            raise AssertionError('Unexpected additional Pod observation')
        value = self.observations.pop(0)
        if value is None:
            raise self.Failure('fixture_exact_pod_not_found')
        self.events.append(('get', value['metadata']['resourceVersion']))
        return self.namespace['validate_owned_pod'](value, name, uid, role)

    def kube(self, arguments, payload=None, *, check=True):
        if payload is not None or check is not False or 'patch' not in arguments:
            raise AssertionError('Only explicit, response-observed fake patch calls are allowed')
        patch = json.loads(arguments[arguments.index('-p') + 1])
        by_path = {row['path']: row['value'] for row in patch if row['op'] == 'test'}
        if by_path.get('/metadata/uid') != UID or '/metadata/resourceVersion' not in by_path:
            raise AssertionError('Patch lost the exact UID/resourceVersion preconditions')
        self.patches.append(patch)
        self.events.append(('patch', by_path['/metadata/resourceVersion']))
        if not self.responses:
            raise AssertionError('Unexpected additional patch attempt')
        return self.responses.pop(0)

    def write(self, path, value):
        name = path.name
        if name in self.files:
            raise AssertionError('Evidence was overwritten: ' + name)
        self.files[name] = deepcopy(value)

    def run(self, *, name='agent-fixture', adding=False):
        return self.namespace['cleanup_finalizer'](name, UID, adding=adding)

    def resource_versions(self):
        return [next(row['value'] for row in patch
            if row['op'] == 'test' and row['path'] == '/metadata/resourceVersion')
            for patch in self.patches]

    def results(self):
        return [value for name, value in self.files.items() if name.endswith('-result.private.json')]


class CleanupCasSafetyTests(unittest.TestCase):
    def test_stale_version_uses_another_fresh_get_and_new_cas(self):
        fixture = Capsule([pod('1'), pod('2'), pod('3')],
            [response(failed=True), response(pod('4', finalizers=[]))])
        result = fixture.run()
        self.assertEqual(result['metadata']['finalizers'], [])
        self.assertEqual(fixture.events, [('get', '1'), ('patch', '1'),
            ('get', '2'), ('get', '3'), ('patch', '3')])
        self.assertEqual(fixture.resource_versions(), ['1', '3'])
        for patch in fixture.patches:
            self.assertIn({'op': 'test', 'path': '/metadata/finalizers', 'value': [FINALIZER]}, patch)
        evidence = fixture.results()
        self.assertTrue(evidence[0]['observed_resource_version_change'])
        self.assertEqual(evidence[0]['outcome'], 'reobserve_and_revalidate_before_next_cas')
        self.assertIn('[REDACTED]', evidence[0]['stderr'])
        self.assertNotIn(SECRET.decode(), json.dumps(evidence))
        self.assertEqual(evidence[1]['outcome'], 'exact_uid_patch_acknowledged')

    def test_same_resource_version_failure_is_not_retried(self):
        fixture = Capsule([pod('1'), pod('1')], [response(failed=True)])
        with self.assertRaisesRegex(fixture.Failure, 'failed_without_resource_version_change'):
            fixture.run()
        self.assertEqual(fixture.resource_versions(), ['1'])
        self.assertEqual(len(fixture.results()), 1)

    def test_changed_identity_or_unsafe_state_after_conflict_is_rejected(self):
        changed_uid = pod('2')
        changed_uid['metadata']['uid'] = 'c66bf416-29a2-4087-88c1-88db95293579'
        changed_owner = pod('2')
        changed_owner['metadata']['labels'][OWNER] = 'unrelated-owner'
        unknown_finalizer = pod('2', finalizers=[FINALIZER, 'unrelated.example/retain'])
        running = pod('2', stopped=False)
        node_lost = pod('2')
        node_lost['status']['reason'] = 'NodeLost'
        for name, changed, code in (
                ('uid', changed_uid, 'owned_pod_identity_mismatch'),
                ('owner', changed_owner, 'owned_pod_identity_mismatch'),
                ('finalizer', unknown_finalizer, 'cleanup_finalizer_set_changed'),
                ('running', running, 'without_full_actual_termination'),
                ('node-lost', node_lost, 'without_full_actual_termination'),
                ('missing', None, 'fixture_exact_pod_not_found')):
            with self.subTest(name=name):
                fixture = Capsule([pod('1'), changed], [response(failed=True)])
                with self.assertRaisesRegex(fixture.Failure, code):
                    fixture.run()
                self.assertEqual(len(fixture.patches), 1)
                self.assertEqual(len(fixture.results()), 1)

    def test_three_changed_version_conflicts_exhaust_the_bound(self):
        fixture = Capsule([pod(str(number)) for number in range(1, 7)],
            [response(failed=True) for _ in range(3)])
        with self.assertRaisesRegex(fixture.Failure, 'conflict_attempts_exhausted'):
            fixture.run()
        self.assertEqual(fixture.resource_versions(), ['1', '3', '5'])
        self.assertEqual(len(fixture.results()), 3)
        self.assertTrue(all(row['patch_replayed'] is False for row in fixture.results()))

    def test_failed_response_with_observed_target_does_not_patch_again(self):
        fixture = Capsule([pod('1'), pod('2', finalizers=[])], [response(failed=True)])
        result = fixture.run()
        self.assertEqual(result['metadata']['finalizers'], [])
        self.assertEqual(len(fixture.patches), 1)
        self.assertEqual(fixture.results()[0]['outcome'], 'target_observed_after_failed_response')

    def test_acknowledged_patch_still_requires_terminated_pod(self):
        fixture = Capsule([pod('1')], [response(pod('2', finalizers=[], stopped=False))])
        with self.assertRaisesRegex(fixture.Failure, 'without_full_actual_termination'):
            fixture.run()
        self.assertEqual(len(fixture.patches), 1)

    def test_helper_addition_has_the_same_fresh_cas_rules(self):
        def helper(version, finalizers=()):
            return pod(version, name='control', role='control', finalizers=finalizers, stopped=False)
        fixture = Capsule([helper('1'), helper('2'), helper('3')],
            [response(failed=True), response(helper('4', [FINALIZER]))])
        result = fixture.run(name='control', adding=True)
        self.assertEqual(result['metadata']['finalizers'], [FINALIZER])
        self.assertEqual(fixture.resource_versions(), ['1', '3'])
        worker = Capsule([pod('1', finalizers=[])], [])
        with self.assertRaisesRegex(worker.Failure, 'cleanup_finalizer_role_invalid'):
            worker.run(adding=True)
        self.assertEqual(worker.patches, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
