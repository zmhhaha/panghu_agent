"""Historical bytes stay available after the former execution service is gone."""
import copy
from contextlib import contextmanager
import hashlib
import json
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.scientific_agent import create_app
from scientific_agent.agent_client import ScientificAgentClient
from scientific_agent.pi_client import WorkerError
from scientific_agent.pi_contracts import canonical_json, report_sha256, result_digest, input_digest
from scientific_agent.pi_projection import PiProjection, sql
from scientific_agent.retired_pi_archive import RetiredPiArchive, archive_from_worker, TASKS, FILES
from test_api import MemoryDB


class Source:
    """Explicit source fixture, not a live model/research-quality assertion."""
    def __init__(self):
        self.snapshots, self.results, self.contents, self.expiry = {}, {}, {}, {}
        self.calls = []

    def add(self, database, number=1, state='succeeded'):
        task_id = str(UUID(int=number))
        payload = dict(user_id='owner', topic='Historical research 科研', engine='pi',
            mode='brief', timeout_seconds=600)
        job = dict(payload, schema_version='1', task_id=task_id, input_digest=input_digest(payload),
            created_at='2026-10-22T00:00:00Z', execution_deadline_at='2026-10-22T00:10:00Z',
            runtime_revision='pi-jsonl-0.73.1-v1', skill_bundle_revision='local-procedures-v2')
        row = [task_id, 'owner', job['input_digest'], canonical_json(job), '', 'accepted',
            job['created_at'], job['created_at']]
        database._execute('INSERT INTO scientific_pi_index '
            '(id,user_id,input_digest,job,email,dispatch,created_at,updated_at) VALUES ('
            + ','.join(map(sql, row)) + ')')
        snapshot = dict(schema_version='1', id=task_id, engine='pi', topic=job['topic'],
            mode='brief', state=state, status='done' if state == 'succeeded' else 'failed',
            revision=4, attempt=1, stage=None, progress=None, created_at=job['created_at'],
            execution_deadline_at=job['execution_deadline_at'], updated_at='2026-10-22T00:01:00Z',
            started_at='2026-10-22T00:00:01Z', finished_at='2026-10-22T00:01:00Z',
            error_code=None, error=None, report_id=task_id if state == 'succeeded' else None,
            result_digest=None, report_sha256=None, report=None)
        self.snapshots[task_id] = snapshot
        if state == 'succeeded':
            report = '# 历史科研\nExact preserved bytes.\r\n'
            contents = dict(report=report.encode('utf-8'), papers=b'', evidence=b'', claims=b'',
                searches=b'', screening=b'', references=b'[]', validation=b'{"valid":true}')
            media = {'report': 'text/markdown', 'references': 'application/json', 'validation': 'application/json'}
            artifacts = [dict(artifact_id=key, path=key + '.txt', media_type=media.get(key, 'application/x-ndjson'),
                size=len(raw), sha256=hashlib.sha256(raw).hexdigest()) for key, raw in sorted(contents.items())]
            result = dict(schema_version='1', task_id=task_id, attempt=1, report_markdown=report,
                report_sha256=report_sha256(report), artifacts=artifacts,
                provenance={'runtime_revision': job['runtime_revision'], 'model': 'controlled-fixture'},
                validation={'valid': True})
            result['result_digest'] = result_digest(result)
            result['manifest'] = {key: result[key] for key in ('schema_version', 'task_id', 'attempt',
                'artifacts', 'provenance', 'result_digest')}
            contents['manifest'] = canonical_json(result['manifest']).encode('utf-8')
            snapshot.update(report_sha256=result['report_sha256'], result_digest=result['result_digest'])
            self.results[task_id], self.contents[task_id] = result, contents
            database._execute('INSERT INTO scientific_reports (id,task_id,topic,content) VALUES ('
                + ','.join(map(sql, (task_id, task_id, job['topic'], report))) + ')')
        return task_id

    def get(self, task_id):
        self.calls.append(('get', task_id))
        return copy.deepcopy(self.snapshots[task_id])

    def result(self, task_id):
        self.calls.append(('result', task_id))
        if task_id not in self.results:
            raise WorkerError(409, 'result_not_ready', 'No validated output')
        if self.expiry.get(task_id) == 'all':
            raise WorkerError(410, 'artifact_expired', '/private/source/token')
        return copy.deepcopy(self.results[task_id])

    def artifacts(self, task_id):
        self.calls.append(('artifacts', task_id))
        if task_id not in self.results:
            raise WorkerError(409, 'result_not_ready', 'No validated output')
        if self.expiry.get(task_id) in ('all', 'listing'):
            raise WorkerError(410, 'artifact_expired', '/private/source/token')
        return {'items': [{key: entry[key] for key in ('artifact_id', 'media_type', 'size', 'sha256')}
            for entry in self.results[task_id]['artifacts']]}

    def artifact(self, task_id, artifact_id, max_bytes=None):
        self.calls.append(('artifact', task_id, artifact_id))
        if self.expiry.get(task_id) == artifact_id:
            raise WorkerError(410, 'artifact_expired', '/private/source/token')
        return self.contents[task_id][artifact_id], {'Content-Type': 'application/octet-stream'}


@pytest.fixture
def history():
    database = MemoryDB()
    database.init_db('scientific')
    PiProjection(database, None).initialize()
    source, archive = Source(), RetiredPiArchive(database)
    archive.initialize()
    yield database, source, archive
    database.connection.close()


@contextmanager
def error(status, code):
    with pytest.raises(WorkerError) as caught:
        yield caught
    assert (caught.value.status, caught.value.code) == (status, code)


def test_preserves_terminal_status_result_all_files_and_manifest_without_source(history):
    database, source, archive = history
    task_id = source.add(database)
    original = copy.deepcopy(source.snapshots[task_id])
    receipt = archive_from_worker(database, source, [task_id])
    assert receipt['task_count'] == 1
    assert receipt['tasks'][0]['artifact_count'] == 9
    source.calls.clear()
    writes = list(database.sql)
    assert archive.get(task_id) == original
    assert archive.result(task_id) == source.results[task_id]
    assert len(archive.artifacts(task_id)['items']) == 8
    for name, raw in source.contents[task_id].items():
        actual, headers = archive.artifact(task_id, name, max_bytes=len(raw))
        assert actual == raw and headers['Content-Type']
    assert archive.cancel(task_id) == original
    assert archive.pending() == {'items': [], 'next_cursor': None}
    assert database.sql == writes and source.calls == []


@pytest.mark.parametrize('state', ['failed', 'cancelled', 'timed_out'])
def test_failed_and_cancelled_tasks_preserve_observed_terminal_state_without_fabricating_output(history, state):
    database, source, archive = history
    task_id = source.add(database, state=state)
    source.snapshots[task_id].update(error_code='engine_error', error='/run/secrets/actual-secret')
    archive_from_worker(database, source, [task_id])
    assert archive.get(task_id)['state'] == state
    assert archive.get(task_id)['error'] == 'engine_error'
    assert 'actual-secret' not in str(database._query(f'SELECT * FROM {TASKS}'))
    with error(409, 'result_not_ready'):
        archive.result(task_id)
    with error(409, 'result_not_ready'):
        archive.artifacts(task_id)


def test_missing_known_history_returns_retired_but_unknown_uuid_remains_not_found(history):
    database, source, archive = history
    task_id = source.add(database)
    with error(410, 'legacy_execution_retired'):
        archive.get(task_id)
    with error(410, 'legacy_execution_retired'):
        archive.cancel(task_id)
    with error(404, 'task_not_found'):
        archive.get(str(UUID(int=999)))


@pytest.mark.parametrize('operation', ['put', 'put_revision', 'ack'])
def test_no_mutating_execution_or_publication_path(history, operation):
    database, source, archive = history
    task_id = source.add(database)
    args = ({'task_id': task_id},) if operation == 'put' else (
        ({'task_id': task_id}, {}) if operation == 'put_revision' else (task_id, {}))
    writes = list(database.sql)
    with error(410, 'legacy_execution_retired'):
        getattr(archive, operation)(*args)
    assert database.sql == writes


def test_all_new_sources_must_be_terminal_before_first_task_is_committed(history):
    database, source, archive = history
    first = source.add(database)
    active = source.add(database, 2, 'running')
    with error(409, 'legacy_task_not_terminal'):
        archive_from_worker(database, source, [first, active])
    assert database._query(f'SELECT * FROM {TASKS}') == []


def test_rejects_result_or_file_digest_mismatch_before_publication(history):
    database, source, archive = history
    task_id = source.add(database)
    source.contents[task_id]['report'] = b'bad'
    with error(409, 'legacy_archive_invalid'):
        archive_from_worker(database, source, [task_id])
    assert database._query(f'SELECT * FROM {TASKS}') == []


@pytest.mark.parametrize('changed', ['result_digest', 'manifest_identity'])
def test_original_result_and_manifest_identities_cannot_be_rebound(history, changed):
    database, source, archive = history
    task_id = source.add(database)
    if changed == 'result_digest':
        source.results[task_id]['result_digest'] = '0' * 64
    else:
        source.results[task_id]['manifest']['task_id'] = str(UUID(int=123))
        source.contents[task_id]['manifest'] = canonical_json(source.results[task_id]['manifest']).encode()
    with error(409, 'legacy_archive_invalid'):
        archive_from_worker(database, source, [task_id])
    assert database._query(f'SELECT * FROM {TASKS}') == []


def test_source_error_details_are_not_copied_into_archive_errors(history):
    database, source, archive = history
    task_id = source.add(database)
    with patch.object(source, 'result', side_effect=WorkerError(503, 'worker_unavailable', '/private/token-value')):
        with error(503, 'worker_unavailable') as caught:
            archive_from_worker(database, source, [task_id])
    assert 'token-value' not in str(caught.value)
    assert database._query(f'SELECT * FROM {TASKS}') == []


def test_partial_sql_write_is_hidden_and_identical_retry_finishes_it(history):
    database, source, archive = history
    task_id = source.add(database)
    execute = database._execute
    count = 0
    def interrupt(statement):
        nonlocal count
        if statement.startswith(f'INSERT INTO {FILES}'):
            count += 1
            if count == 2:
                raise OSError('/private/credential')
        return execute(statement)
    with patch.object(database, '_execute', side_effect=interrupt):
        with error(503, 'legacy_archive_unavailable'):
            archive_from_worker(database, source, [task_id])
    with error(410, 'legacy_execution_retired'):
        archive.get(task_id)
    assert database._query(f'SELECT committed FROM {TASKS}')[0]['committed'] == 0
    archive_from_worker(database, source, [task_id])
    assert archive.get(task_id)['state'] == 'succeeded'
    assert len(database._query(f'SELECT * FROM {FILES}')) == 9


def test_different_replay_cannot_overwrite_an_uncommitted_archive(history):
    database, source, archive = history
    task_id = source.add(database)
    execute = database._execute
    def interrupt(statement):
        if statement.startswith(f'UPDATE {TASKS} SET committed=1'):
            raise OSError('interrupted before publish')
        return execute(statement)
    with patch.object(database, '_execute', side_effect=interrupt):
        with error(503, 'legacy_archive_unavailable'):
            archive_from_worker(database, source, [task_id])
    before = database._query(f'SELECT * FROM {TASKS}')
    source.snapshots[task_id]['revision'] += 1
    with error(409, 'legacy_archive_conflict'):
        archive_from_worker(database, source, [task_id])
    assert database._query(f'SELECT * FROM {TASKS}') == before
    with error(410, 'legacy_execution_retired'):
        archive.get(task_id)


@pytest.mark.parametrize('expiry', ['listing', 'papers', 'manifest', 'all'])
def test_true_source_expiry_remains_a_410_not_an_empty_success(history, expiry):
    database, source, archive = history
    task_id = source.add(database)
    source.expiry[task_id] = expiry
    receipt = archive_from_worker(database, source, [task_id])
    assert receipt['tasks'][0]['artifacts_state'] == 'expired'
    assert archive.get(task_id)['state'] == 'succeeded'
    assert database._query(f'SELECT * FROM {FILES}') == []
    if expiry != 'all':
        assert archive.result(task_id) == source.results[task_id]
    else:
        with error(410, 'artifact_expired'):
            archive.result(task_id)
    for operation, args in ((archive.artifacts, (task_id,)), (archive.artifact, (task_id, 'report'))):
        with error(410, 'artifact_expired'):
            operation(*args)


def test_import_replay_does_not_contact_source_or_overwrite_a_committed_archive(history):
    database, source, archive = history
    task_id = source.add(database)
    original = archive_from_worker(database, source, [task_id])
    source.snapshots.clear()
    calls = list(source.calls)
    assert archive_from_worker(database, source, [task_id]) == original
    assert calls == source.calls
    assert len(database._query(f'SELECT * FROM {TASKS}')) == 1


def test_tampered_attachment_is_not_returned_and_cannot_be_silently_overwritten(history):
    database, source, archive = history
    task_id = source.add(database)
    archive_from_worker(database, source, [task_id])
    database._execute(f'UPDATE {FILES} SET body_base64=' + sql('eA==') + " WHERE artifact_id='report'")
    with error(409, 'legacy_archive_corrupt'):
        archive.artifact(task_id, 'report')
    with error(409, 'legacy_archive_corrupt'):
        archive_from_worker(database, source, [task_id])


def test_lookup_remains_bound_to_original_owner_and_current_jobs_are_never_imported(history):
    database, source, archive = history
    task_id = source.add(database)
    archive_from_worker(database, source, [task_id])
    row = database._query('SELECT job FROM scientific_pi_index')[0]
    job = json.loads(row['job'])
    job['user_id'] = 'other'
    database._execute('UPDATE scientific_pi_index SET user_id=\'other\',job=' + sql(canonical_json(job)))
    with error(409, 'legacy_archive_invalid'):
        archive.get(task_id)
    job['runtime_revision'] = 'agent-task-contract-v1'
    database._execute('UPDATE scientific_pi_index SET job=' + sql(canonical_json(job)))
    with error(404, 'task_not_found'):
        archive_from_worker(database, source, [task_id])


def test_public_api_uses_static_history_even_if_retired_url_and_secret_are_present(history):
    database, source, archive = history
    task_id = source.add(database)
    archive_from_worker(database, source, [task_id])
    env = dict(SCIENTIFIC_EXECUTION_BACKEND='agent-service',
        SCIENTIFIC_AGENT_SERVICE_URL='http://agent.invalid', SCIENTIFIC_AGENT_SERVICE_TOKEN='a' * 40,
        SCIENTIFIC_AGENT_SERVICE_TOKEN_FILE='', SCIENTIFIC_PI_WORKER_URL='http://old-worker.invalid',
        SCIENTIFIC_PI_WORKER_TOKEN='old-worker-secret')
    with patch.dict('os.environ', env), patch('app.api.scientific_agent.PiClient', side_effect=AssertionError('old network client constructed')):
        app = create_app(database=database, start_reconciler=False)
    assert isinstance(app.state.worker, ScientificAgentClient)
    assert isinstance(app.state.worker.legacy_worker, RetiredPiArchive)
    with TestClient(app) as client:
        client.headers['X-Forwarded-User'] = 'owner'
        response = client.get('/scientific-research/' + task_id)
        assert response.status_code == 200 and response.json()['status'] == 'done'
        response = client.get('/scientific-research/' + task_id + '/artifacts/report')
        assert response.status_code == 200 and response.content == source.contents[task_id]['report']
        response = client.get('/scientific-download/' + task_id)
        assert response.status_code == 200 and response.content == source.contents[task_id]['report']
        client.headers['X-Forwarded-User'] = 'other'
        assert client.get('/scientific-research/' + task_id).status_code == 404
        assert client.get('/scientific-research/' + task_id + '/artifacts/report').status_code == 404
