"""Neutral scientific admission/publication with durable SQL and an offline wire peer."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.scientific_agent import create_app
from scientific_agent.agent_client import FILES, ScientificAgentClient
from scientific_agent.agent_config import AgentServiceSettings, execution_backend
from scientific_agent.agent_projection import ScientificAgentProjection
from scientific_agent.pi_client import WorkerError
from scientific_agent.pi_contracts import report_sha256, result_digest
from test_api import MemoryDB


JOB, ATTEMPT = (str(UUID(int=i)) for i in (101, 102))
TOKEN = 'offline-scientific-service-token-0000000000000'


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class Peer:
    """Models admission ambiguity and transport loss, never a native Agent."""
    def __init__(self):
        self.calls = []
        self.job = None
        self.fail_post_after_commit = False
        self.drop_post = False
        self.fail_ack = False
        self.post_entered = threading.Event()
        self.post_release = None
        self.blobs = {}

    def __call__(self, method, url, headers, body, timeout, limit):
        path = urlsplit(url).path
        self.calls.append((method, path, headers, body))
        if path == '/v1/jobs' and method == 'POST':
            request = json.loads(body)
            self.post_entered.set()
            if self.post_release is not None:
                assert self.post_release.wait(5), 'test did not release admission'
            if self.drop_post:
                raise TimeoutError()
            self.job = dict(job_id=JOB, business_ref=request['business_ref'], state='queued',
                pins=dict(profile=request['profile'], profile_revision=request['profile_revision'],
                    engine='pi', runtime_revision='runtime-test', image_digest='sha256:' + 'a' * 64,
                    execution_binding={'sha256': 'b' * 64}),
                attempt=dict(attempt_id=ATTEMPT, fence=1))
            if self.fail_post_after_commit:
                raise TimeoutError()
            return 202, {}, wire(self.job)
        if path.startswith('/v1/jobs/by-idempotency/') or path == '/v1/jobs/' + JOB:
            if self.job is None:
                return 404, {}, wire({'detail': {'code': 'job_not_found'}})
            return 200, {}, wire(self.job)
        if path == '/v1/jobs/' + JOB + '/cancel':
            self.job['state'] = 'cancelled'
            return 200, {}, wire(self.job)
        if path == '/v1/jobs/' + JOB + '/artifact-manifest':
            return 200, {}, wire(self.manifest)
        if path.startswith('/v1/artifacts/'):
            return 200, {}, self.blobs[path.split('/')[3]]
        if path == '/v1/jobs/' + JOB + '/publication-ack':
            if self.fail_ack:
                raise TimeoutError()
            return 200, {}, wire({'job_id': JOB, 'recorded': True, 'publication_authorized': False})
        if path == '/v1/capabilities':
            return 200, {}, wire({'profiles': [dict(profile=name, profile_revision='v1', ready=True)
                for name in ('research.generate.v1', 'research.revise.v1')]})
        raise AssertionError((method, path))

    def count(self, method, suffix):
        return sum(m == method and path.endswith(suffix) for m, path, _, _ in self.calls)

    def complete(self, *, invalid_report=False, bad_observation=False):
        search = dict(source='crossref', query='controlled empty result', searched_at='2026-01-01T00:00:00Z',
                      status='success', result_count=0, error=None)
        request = dict(tool='academic_search', payload={'query': search['query'], 'providers': ['crossref'], 'limit': 1})
        observed = dict(schema_version='1', attempt_id=ATTEMPT,
            request_sha256='d' * 64, observations=[dict(call_sha256=hashlib.sha256(wire(request)).hexdigest(),
                observed_at=123.0, state='complete', request=request,
                response=dict(schema_version='1', searches=[search], papers=[], database_total=None, notes=[]))])
        if bad_observation:
            observed['request_sha256'] = '0' * 64
        report = ('# Scope\nControlled fixture.\n# Methods\nOne controlled search returned zero rows.\n'
            '# Findings\nInsufficient evidence.\n# Limitations\nNo scientific conclusion is supported.\n'
            '# References\nNone.\n')
        if invalid_report:
            report = 'Unsupported factual assertion with no required report sections.'
        contents = {'report.md': report.encode(), 'searches.jsonl': wire(search) + b'\n',
            'papers.jsonl': b'', 'screening.jsonl': b'', 'evidence.jsonl': b'', 'claims.jsonl': b'',
            'references.json': b'[]', 'academic-observations.json': wire(observed)}
        # The legitimate fixture uses the real offline validator. The negative
        # fixture falsely self-reports success and must fail business revalidation.
        from pi_worker.research_tools.validation import validate_research_outputs
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, raw in contents.items():
                (directory / name).write_bytes(raw)
            validation = validate_research_outputs(directory)
        if invalid_report:
            validation['valid'] = True
        contents['validation.json'] = wire(validation)
        provenance = dict(profile='research.generate.v1', profile_revision='v1',
            engine='pi', runtime_revision='runtime-test', image_digest='sha256:' + 'a' * 64,
            execution_binding_sha256='b' * 64, execution_attempt_id=ATTEMPT, execution_request_sha256='d' * 64)
        result = dict(schema_version='1', task_id=self.job['business_ref'], attempt=1,
            report_sha256=report_sha256(report), provenance=provenance, validation=validation,
            artifacts=[dict(artifact_id=role, media_type=media, size=len(contents[name]),
                sha256=hashlib.sha256(contents[name]).hexdigest())
                for role, (name, media) in FILES.items() if role != 'revision_output'])
        result['result_digest'] = result_digest(result)
        contents['research-result.json'] = wire(result)
        entries = []
        for index, (name, raw) in enumerate(sorted(contents.items()), 200):
            artifact = str(UUID(int=index))
            self.blobs[artifact] = raw
            entries.append(dict(name=name, artifact_id=artifact, size=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        manifest = dict(schema_version='agent-artifacts.v1', job_id=JOB, attempt_id=ATTEMPT,
            publication_authorized=False, files=entries, total_bytes=sum(map(len, contents.values())))
        manifest['sha256'] = hashlib.sha256(wire(manifest)).hexdigest()
        self.manifest = manifest
        self.job.update(state='completed', artifact_manifest_sha256=manifest['sha256'],
            stop_proven=True, publication_authorized=False,
            stop_proof=dict(stop_proven=True, attempt_id=ATTEMPT, fence=1, request_sha256='d' * 64))
        return report


class ScientificAgentTests(unittest.TestCase):
    def setUp(self):
        self.db, self.peer = MemoryDB(), Peer()
        self.addCleanup(self.db.connection.close)
        self.db.init_db('scientific')
        self.worker = self.new_worker()
        self.projection = ScientificAgentProjection(self.db, self.worker)
        self.projection.initialize()
        self.payload = dict(topic='Controlled research', user_id='owner', engine='pi', mode='review', timeout_seconds=600)

    def new_worker(self, **kwargs):
        return ScientificAgentClient(self.db, 'http://agent.invalid', TOKEN, transport=self.peer,
                                     providers=('crossref',), max_results=1, **kwargs)

    def prepare(self, key='fixed-key'):
        row = self.projection.prepare(self.payload, key=key)
        return json.loads(row['job'])

    def test_lost_admission_response_recovers_read_only_after_restart(self):
        job = self.prepare()
        self.peer.fail_post_after_commit = True
        with self.assertRaises(WorkerError):
            self.worker.put(job)
        worker = self.new_worker(generation_revision='later')
        worker.initialize()
        snapshot = worker.put(job)
        self.assertEqual(snapshot['state'], 'queued')
        self.assertEqual(self.peer.count('POST', '/v1/jobs'), 1)
        self.assertEqual(self.peer.job['pins']['profile_revision'], 'v1')
        request = json.loads(self.peer.calls[0][3])
        self.assertFalse({'engine', 'model', 'runtime_revision', 'user_id', 'skill_bundle_revision'} & set(request))
        self.assertFalse({'engine', 'runtime_revision', 'user_id', 'skill_bundle_revision'} & set(request['task']))

    def test_uncertain_admission_never_resubmits_and_expires_at_original_deadline(self):
        job = self.prepare()
        self.peer.drop_post = True
        with self.assertRaises(WorkerError):
            self.worker.put(job)
        snapshot = self.worker.put(job)
        self.assertEqual(snapshot['state'], 'queued')
        self.assertEqual(snapshot['error_code'], 'admission_uncertain')
        with patch('scientific_agent.agent_client.utc_now', return_value=job['execution_deadline_at']):
            self.worker.pending()
        self.assertEqual(self.worker.get(job['task_id'])['state'], 'timed_out')
        self.assertEqual(self.peer.count('POST', '/v1/jobs'), 1)

    def test_concurrent_submitters_only_one_admission_owner_posts(self):
        job = self.prepare()
        self.peer.post_release = threading.Event()
        errors = []
        def submit():
            try:
                self.worker.put(job)
            except BaseException as error:
                errors.append(error)
        first = threading.Thread(target=submit)
        first.start()
        self.assertTrue(self.peer.post_entered.wait(5))
        try:
            self.assertEqual(self.worker.put(job)['state'], 'queued')
        finally:
            self.peer.post_release.set()
            first.join(5)
        self.assertFalse(first.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.peer.count('POST', '/v1/jobs'), 1)

    def test_cancel_prepared_task_does_not_submit(self):
        job = self.prepare()
        self.assertEqual(self.worker.cancel(job['task_id'])['state'], 'cancelled')
        self.assertEqual(self.worker.put(job)['state'], 'cancelled')
        self.assertEqual(self.peer.count('POST', '/v1/jobs'), 0)

    def test_cancel_after_lost_admission_response_finds_original_and_cancels_it(self):
        job = self.prepare()
        self.peer.fail_post_after_commit = True
        with self.assertRaises(WorkerError):
            self.worker.put(job)
        self.assertEqual(self.worker.cancel(job['task_id'])['state'], 'cancelled')
        self.assertEqual(self.peer.count('POST', '/v1/jobs'), 1)
        self.assertEqual(self.peer.count('POST', '/cancel'), 1)

    def test_real_output_validation_then_publication_and_ack_outbox_replay(self):
        job = self.prepare()
        self.worker.put(job)
        report = self.peer.complete()
        self.assertEqual(self.worker.get(job['task_id'])['state'], 'publishing')
        self.db.skip_report_insert = True
        self.projection.reconcile()
        self.assertIsNone(self.worker._row(job['task_id'])['publication_ack'])
        self.assertEqual(self.peer.count('POST', '/publication-ack'), 0)
        self.db.skip_report_insert = False
        self.peer.fail_ack = True
        self.projection.reconcile()
        self.assertEqual(self.db.get_report(job['task_id'])['content'], report)
        self.assertEqual(self.worker.get(job['task_id'])['state'], 'succeeded')
        self.assertIsNone(self.worker._row(job['task_id'])['delivery_ack'])
        self.peer.fail_ack = False
        worker = self.new_worker()
        worker.initialize()
        worker.replay_delivery_acknowledgements()
        self.assertIsNotNone(worker._row(job['task_id'])['delivery_ack'])
        self.assertEqual(self.peer.count('POST', '/v1/jobs'), 1)
        self.assertEqual(self.peer.count('POST', '/publication-ack'), 2)

    def test_forged_validator_success_cannot_publish_invalid_report(self):
        job = self.prepare()
        self.worker.put(job)
        self.peer.complete(invalid_report=True)
        with self.assertRaises(WorkerError) as caught:
            self.worker.result(job['task_id'])
        self.assertEqual(caught.exception.code, 'publication_conflict')
        self.assertIsNone(self.worker._row(job['task_id'])['result_digest'])

    def test_observations_for_another_attempt_request_cannot_publish(self):
        job = self.prepare()
        self.worker.put(job)
        self.peer.complete(bad_observation=True)
        with self.assertRaises(WorkerError) as caught:
            self.worker.result(job['task_id'])
        self.assertEqual(caught.exception.code, 'publication_conflict')

    def test_download_digest_mismatch_cannot_publish(self):
        job = self.prepare()
        self.worker.put(job)
        self.peer.complete()
        item = next(item for item in self.peer.manifest['files'] if item['name'] == 'report.md')
        self.peer.blobs[item['artifact_id']] = b'x' * item['size']
        with self.assertRaises(WorkerError) as caught:
            self.worker.result(job['task_id'])
        self.assertEqual(caught.exception.code, 'artifact_size_mismatch')

    def test_agent_backend_public_api_uses_same_publication_workflow(self):
        app = create_app(self.db, self.worker, self.projection, start_reconciler=False)
        with TestClient(app) as client:
            client.headers['X-Forwarded-User'] = 'owner'
            response = client.post('/scientific-research', json={'topic': 'API fixture', 'engine': 'agent-service'},
                headers={'Idempotency-Key': 'api-fixture'})
            self.assertEqual(response.status_code, 200, response.text)
            task = response.json()['id']
            self.peer.complete()
            self.projection.reconcile()
            response = client.get('/scientific-research/' + task)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['status'], 'done')
            self.assertEqual(response.json()['engine'], 'agent-service')


class ScientificAgentConfigTests(unittest.TestCase):
    def test_operator_selects_backend_and_config_has_no_provider_credentials(self):
        self.assertEqual(execution_backend({}), 'pi')
        self.assertEqual(execution_backend({'SCIENTIFIC_EXECUTION_BACKEND': 'agent-service'}), 'agent-service')
        with self.assertRaises(ValueError):
            execution_backend({'SCIENTIFIC_EXECUTION_BACKEND': 'unknown'})
        with tempfile.TemporaryDirectory() as temporary:
            credential = Path(temporary) / 'token'
            credential.write_text(TOKEN)
            config = AgentServiceSettings.from_env({'SCIENTIFIC_AGENT_SERVICE_URL': 'http://agent.invalid',
                'SCIENTIFIC_AGENT_SERVICE_TOKEN_FILE': str(credential)})
            self.assertEqual(config.token, TOKEN)
            self.assertNotIn(TOKEN, repr(config))
        self.assertEqual(config.generation_revision, 'v1')
        self.assertFalse(hasattr(config, 'model'))


if __name__ == '__main__':
    unittest.main()
