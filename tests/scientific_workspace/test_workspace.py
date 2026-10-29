"""Real SQLite workflow, admission and artifact boundaries; no network/models."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import sqlite3
import threading
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.scientific_workspace import create_router
from scientific_agent.pi_client import WorkerError
from scientific_agent.pi_contracts import result_digest
from scientific_agent.pi_sections import index_sections
from scientific_agent.workflow_contracts import digest, stage_result_digest, trace_artifacts, trace_manifest
from scientific_agent.workspace import WorkspaceStore, WorkspaceError
from scientific_agent.workspace_execution import WorkspaceService
from scientific_agent.workspace_materials import parse_material, doi_material, MaterialError, MAX_UPLOAD_BYTES


class SQLite:
    def __init__(self):
        self.connection = sqlite3.connect(':memory:', check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.Lock()

    def _execute(self, sql):
        with self.lock:
            self.connection.execute(sql)
            self.connection.commit()

    def _query(self, sql):
        with self.lock:
            return [dict(row) for row in self.connection.execute(sql).fetchall()]


PLAN = dict(questions=[], research_questions=['What does the supplied evidence support?'],
    search_queries=['reproducible build methods'], inclusion_criteria=['Relevant methods'],
    exclusion_criteria=['Unrelated methods'], scope='Bounded overview', limitations=['Not a systematic review'])


def retrieval(task, attempt_id, request_sha256):
    stamp = task['created_at']
    av = lambda value: dict(available=value, obtained_at=stamp if value else None)
    paper = dict(paper_id='doi:10.1234/fixture', title='Synthetic recorded source',
        authors=['Fixture Author'], year=2026, doi='10.1234/fixture', pmid=None,
        url='https://example.invalid/fixture', sources=['crossref'], retrieved_at=stamp,
        abstract='Synthetic recorded abstract; not scientific evidence.', full_text_path=None,
        availability=dict(metadata=av(True), abstract=av(True), full_text=av(False)),
        verification=dict(status='unknown', checked_at=None, source=None, error=None))
    search = dict(source='crossref', query='reproducible build methods', searched_at=stamp,
                  status='success', result_count=1, error=None)
    policy = {key: task['source_policy'][key] for key in ('providers', 'max_results')}
    req = dict(tool='academic_search', payload=dict(query=search['query'], providers=['crossref'], limit=2))
    observations = dict(schema_version='1', attempt_id=attempt_id, request_sha256=request_sha256,
        observations=[dict(call_sha256=digest(req), observed_at=123.5, state='complete', request=req,
            response=dict(schema_version='1', searches=[search], papers=[paper], database_total=None, notes=[]))])
    return dict(searches=[search], papers=[paper], gaps=[], observations=observations,
                retrieval=dict(attempt_id=attempt_id, request_sha256=request_sha256, source_policy=policy))


def outline():
    return dict(evidence=[dict(evidence_id='e1', paper_id='doi:10.1234/fixture',
        finding='Only synthetic recorded access was tested', method='Fixture', limitations='Abstract only',
        access_level='abstract', locator='abstract')],
        sections=[dict(section_id='findings', title='Findings', questions=['What is recorded?'], evidence_ids=['e1'])],
        gaps=[])


def draft():
    return dict(report_markdown='# Findings\n<!-- claim:c1 -->\nSynthetic recorded finding [1].\n'
        '# Limitations\nAbstract only; identifier verification is unknown.\n'
        '# References\n[1] Synthetic recorded source. https://example.invalid/fixture\n',
        claims=[dict(claim_id='c1', marker='<!-- claim:c1 -->', evidence_ids=['e1'],
                     assessment='supported', explanation='Only synthetic recorded access')],
        references=[dict(n=1, paper_id='doi:10.1234/fixture')])


class Agent:
    providers, max_results = ('crossref',), 2

    def __init__(self, store):
        self.store, self.uploads, self.jobs, self.idempotency, self.artifacts = store, {}, {}, {}, {}
        self.posts, self.lookups, self.acks = 0, 0, 0
        self.lose_response, self.explicit_rejection = False, False

    def _upload(self, name, raw):
        input_id = str(uuid4())
        self.uploads[input_id] = (name, raw)
        return dict(input_id=input_id, sha256=hashlib.sha256(raw).hexdigest())

    def _request(self, method, path, payload=None, *, raw=False, limit=None, headers=None):
        if method == 'POST' and path == '/v1/jobs':
            self.posts += 1
            # Assert the real SQL project already owns the immutable admission intent.
            rows = self.store.db._query('SELECT document FROM scientific_workspace_projects')
            matching = [j for row in rows for j in json.loads(row['document'])['jobs']
                        if j['id'] == payload['business_ref']]
            assert matching and matching[0]['admission_started'] and matching[0]['request'] == payload
            if self.explicit_rejection:
                raise WorkerError(429, 'queue_full', 'Synthetic queue full', True)
            job_id = str(uuid4())
            result = dict(job_id=job_id, business_ref=payload['business_ref'], state='queued',
                pins=dict(profile=payload['profile'], profile_revision=payload['profile_revision']),
                request=deepcopy(payload))
            self.jobs[job_id] = result
            self.idempotency[headers['Idempotency-Key']] = job_id
            if self.lose_response:
                self.lose_response = False
                raise WorkerError(503, 'agent_unavailable', 'Response lost after commit', True)
            return deepcopy(result)
        if method == 'GET' and path.startswith('/v1/jobs/by-idempotency/'):
            from urllib.parse import unquote
            self.lookups += 1
            key = unquote(path.rsplit('/', 1)[-1])
            if key not in self.idempotency:
                raise WorkerError(404, 'job_not_found', 'Not found')
            return deepcopy(self.jobs[self.idempotency[key]])
        if method == 'GET' and path.startswith('/v1/jobs/') and path.endswith('/artifact-manifest'):
            return deepcopy(self.jobs[path.split('/')[3]]['manifest'])
        if method == 'GET' and path.startswith('/v1/jobs/'):
            return deepcopy(self.jobs[path.split('/')[3]])
        if method == 'POST' and path.endswith('/cancel'):
            job = self.jobs[path.split('/')[3]]
            job['state'] = 'cancelled'
            return deepcopy(job)
        if method == 'POST' and path.endswith('/publication-ack'):
            self.acks += 1
            return dict(recorded=True, job_id=path.split('/')[3])
        if method == 'GET' and path.startswith('/v1/artifacts/'):
            return self.artifacts[path.split('/')[3]], {}
        raise AssertionError((method, path))

    def complete(self, owner, project_id, task_id, *, tamper=False):
        project = self.store.raw(owner, project_id)
        local = next(j for j in project['jobs'] if j['id'] == task_id)
        remote = self.jobs[local['remote_job_id']]
        attempt_id = str(uuid4())
        request_hash = 'a' * 64
        stage = local['stage']
        if stage == 'revise':
            return self.complete_revision(local, remote, attempt_id, request_hash, tamper=tamper)
        output = deepcopy(PLAN) if stage == 'plan' else retrieval(local['task'], attempt_id, request_hash) \
            if stage == 'retrieve' else outline() if stage == 'outline' else draft()
        result = dict(schema_version='1', project_id=project_id, task_id=task_id,
            stage=stage, context_sha256=local['task']['context_sha256'], output=output,
            provenance=dict(profile=local['profile'], profile_revision='v1',
                execution_attempt_id=attempt_id, execution_request_sha256=request_hash,
                context_sha256=local['task']['context_sha256'], retrieval_performed=stage == 'retrieve'), artifacts=[])
        files = {}
        if stage == 'draft':
            traces = trace_artifacts(local['frozen_context'], output)
            result['provenance'].update(
                evidence_snapshot_sha256=digest(local['frozen_context']['evidence_snapshot']),
                outline_sha256=digest(local['frozen_context']['outline']),
                confirmation_id=local['frozen_context']['confirmation']['confirmation_id'])
            result['artifacts'] = trace_manifest(output['report_markdown'], traces)
            files = {'report.md': output['report_markdown'].encode('utf-8'),
                **{role + ('.json' if role == 'references' else '.jsonl'): raw for role, raw in traces.items()}}
        result['result_digest'] = stage_result_digest(result)
        if tamper:
            result['project_id'] = str(uuid4())
        files['stage-result.json'] = json.dumps(result, sort_keys=True, ensure_ascii=False,
                                              separators=(',', ':')).encode('utf-8')
        self.publish(remote, files, attempt_id, request_hash)

    def publish(self, remote, files, attempt_id, request_hash):
        entries = []
        for name, raw in sorted(files.items()):
            artifact_id = str(uuid4())
            entries.append(dict(artifact_id=artifact_id, name=name, size=len(raw),
                                sha256=hashlib.sha256(raw).hexdigest()))
            self.artifacts[artifact_id] = raw
        manifest = dict(schema_version='agent-artifacts.v1', job_id=remote['job_id'],
            attempt_id=attempt_id, publication_authorized=False, files=entries,
            total_bytes=sum(len(raw) for raw in files.values()))
        manifest['sha256'] = digest(manifest)
        remote.update(state='completed', stop_proven=True, publication_authorized=False,
            attempt=dict(attempt_id=attempt_id, fence=1),
            stop_proof=dict(stop_proven=True, attempt_id=attempt_id, fence=1, request_sha256=request_hash),
            artifact_manifest_sha256=manifest['sha256'], manifest=manifest)

    def complete_revision(self, local, remote, attempt_id, request_hash, *, tamper=False):
        from scientific_agent.pi_revision_validation import compose_revision
        material, task = local['revision_material'], local['revision_job']
        raw = {role: base64.b64decode(value) for role, value in material['artifacts'].items()}
        output = dict(schema_version='1', section_markdown='# Findings\n<!-- claim:c2 -->\n'
            'Revised synthetic finding from the same recorded abstract [1].\n',
            claims=[dict(claim_id='c2', marker='<!-- claim:c2 -->', evidence_ids=['e1'],
                         assessment='supported', explanation='Same fixed abstract')])
        composed = compose_revision(material['report'], material['items'], raw,
                                    task['section_id'], task['base_sha256'], output)
        output_raw = json.dumps(output, ensure_ascii=False).encode('utf-8')
        result = dict(schema_version='1', task_id=local['id'], attempt=1,
            report_sha256=composed['report_sha256'], validation=composed['validation'],
            artifacts=composed['items'] + [dict(artifact_id='revision_output', size=len(output_raw),
                sha256=hashlib.sha256(output_raw).hexdigest(), media_type='application/json')],
            provenance=dict(profile='research.revise.v1', profile_revision='v1',
                execution_attempt_id=attempt_id, execution_request_sha256=request_hash,
                retrieval_performed=False))
        files = {role + ('.json' if role == 'references' else '.jsonl'): value
                 for role, value in composed['artifacts'].items()}
        files['report.md'] = composed['report_markdown'].encode('utf-8')
        if tamper:
            files['report.md'] += b'\nAn unauthorized suffix change.'
        files['revision-output.json'] = output_raw
        result['result_digest'] = result_digest(result)
        files['research-result.json'] = json.dumps(result, ensure_ascii=False).encode('utf-8')
        self.publish(remote, files, attempt_id, request_hash)


@pytest.fixture
def setup():
    db = SQLite()
    store = WorkspaceStore(db)
    store.initialize()
    agent = Agent(store)
    service = WorkspaceService(store, agent)
    yield store, service, agent
    db.connection.close()


def create(store, owner='alice'):
    return store.create(owner, dict(requirements=dict(question='Reproducible builds: methods and limitations')),
                        str(uuid4()))


def action(store, project, name, payload=None, owner='alice'):
    return store.action(owner, project['id'], name,
        dict(expected_revision=project['revision'], **(payload or {})), str(uuid4()),
        source_policy=dict(providers=['crossref'], max_results=2, max_search_calls=3))


def finish(store, service, agent, project, stage):
    service.reconcile('alice', project['id'])
    raw = store.raw('alice', project['id'])
    job = next(j for j in raw['jobs'] if j['id'] == raw['active_job_id'])
    assert job['stage'] == stage
    agent.complete('alice', project['id'], job['id'])
    service.reconcile('alice', project['id'])
    return store.get('alice', project['id'])


def planned(setup):
    store, service, agent = setup
    return finish(store, service, agent, action(store, create(store), 'plan'), 'plan')


def outlined(setup):
    store, service, agent = setup
    project = action(store, planned(setup), 'confirm_plan')
    project = finish(store, service, agent, project, 'retrieve')
    return finish(store, service, agent, project, 'outline')


def drafted(setup):
    store, service, agent = setup
    return finish(store, service, agent, action(store, outlined(setup), 'confirm_evidence'), 'draft')


def test_owner_and_create_idempotency(setup):
    store, _, _ = setup
    body = dict(requirements=dict(question='A sufficiently long question ' * 50))
    first = store.create('alice', body, 'create-key')
    assert len(first['requirements']['question']) > 500
    assert store.create('alice', body, 'create-key')['id'] == first['id']
    with pytest.raises(WorkspaceError, match='idempotency_conflict'):
        store.create('alice', dict(requirements=dict(question='Other')), 'create-key')
    with pytest.raises(WorkspaceError) as exc:
        store.get('bob', first['id'])
    assert exc.value.status == 404
    assert store.list('bob') == []


def test_revision_cas_single_winner_and_receipt_replay(setup):
    store, _, _ = setup
    project = create(store)
    barrier = threading.Barrier(2)
    original_cas = store._cas
    def concurrent_cas(previous, changed):
        barrier.wait(timeout=5)
        original_cas(previous, changed)
    store._cas = concurrent_cas
    def update(number):
        body = dict(expected_revision=0, title='Title ' + str(number))
        try:
            return store.patch('alice', project['id'], body, 'edit-' + str(number))
        except WorkspaceError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(update, (1, 2)))
    assert sum(isinstance(item, dict) for item in results) == 1
    assert 'project_revision_conflict' in results
    winner = next(item for item in results if isinstance(item, dict))
    number = winner['title'].rsplit(' ', 1)[1]
    body = dict(expected_revision=0, title=winner['title'])
    assert store.patch('alice', project['id'], body, 'edit-' + number)['revision'] == 1
    body['title'] = 'Different replay'
    with pytest.raises(WorkspaceError, match='idempotency_conflict'):
        store.patch('alice', project['id'], body, 'edit-' + number)


def test_two_actual_gates_and_draft_chain(setup):
    store, service, agent = setup
    project = create(store)
    for forbidden in ('confirm_plan', 'outline', 'confirm_evidence'):
        with pytest.raises(WorkspaceError):
            action(store, project, forbidden)
    project = outlined(setup)
    assert [j['stage'] for j in project['jobs']] == ['plan', 'retrieve', 'outline']
    assert project['active_job_id'] is None
    assert project['confirmations']['plan'] and not project['confirmations']['evidence']
    project = finish(store, service, agent, action(store, project, 'confirm_evidence'), 'draft')
    assert project['stage'] == 'review' and project['active_job_id'] is None
    assert agent.posts == 4 and agent.acks == 4
    version = store.version('alice', project['id'], project['current_version_id'])
    assert version['references'] == [dict(n=1, paper_id='doi:10.1234/fixture')]
    assert version['evidence_snapshot_id'] == project['evidence']['id']
    assert version['confirmation_id'] == project['confirmations']['evidence']['id']
    assert '<!-- claim:c1 -->' in version['report_markdown']
    assert version['section_index']['sections']


def test_requirements_and_selection_invalidate_appropriate_gate(setup):
    store, _, _ = setup
    project = outlined(setup)
    old_confirmation = deepcopy(project['confirmations']['plan'])
    project = store.patch('alice', project['id'], dict(expected_revision=project['revision'],
        selections=[dict(source_id='doi:10.1234/fixture', included=False, reason='Out of scope')]), 'exclude')
    assert project['confirmations']['plan'] == old_confirmation
    assert project['evidence'] is None and project['outline'] is None
    project = store.patch('alice', project['id'], dict(expected_revision=project['revision'],
        requirements=dict(question='A materially different question')), 'question')
    assert project['confirmations']['plan'] is None and project['stage'] == 'goal'
    assert project['search_batches'] == []
    assert project['confirmation_history'] and project['evidence_history']


def test_frozen_inputs_and_stale_results_do_not_advance(setup):
    store, service, agent = setup
    project = action(store, create(store), 'plan')
    service.reconcile('alice', project['id'])
    raw = store.raw('alice', project['id'])
    job = raw['jobs'][0]
    frozen = deepcopy(job['frozen_context'])
    project = store.patch('alice', project['id'], dict(expected_revision=raw['revision'],
        requirements=dict(question='Changed while execution runs')), 'update-question')
    agent.complete('alice', project['id'], job['id'])
    service.reconcile('alice', project['id'])
    saved = store.raw('alice', project['id'])
    assert saved['jobs'][0]['frozen_context'] == frozen
    assert saved['jobs'][0]['stale'] and saved['jobs'][0]['state'] == 'completed'
    assert saved['plan'] is None and saved['stage'] == 'goal'
    assert saved['plan_history'][0]['stale']


def test_ambiguous_post_recovered_by_lookup_without_duplicate_submission(setup):
    store, service, agent = setup
    project = action(store, create(store), 'plan')
    agent.lose_response = True
    with pytest.raises(WorkerError):
        service.reconcile('alice', project['id'])
    assert agent.posts == 1
    restarted = WorkspaceService(WorkspaceStore(store.db), agent)
    restarted.reconcile('alice', project['id'])
    assert agent.posts == 1 and agent.lookups >= 1
    saved = store.raw('alice', project['id'])
    assert saved['jobs'][0]['remote_job_id'] and saved['jobs'][0]['state'] == 'queued'


def test_lost_local_commit_response_is_resolved_by_mutation_receipt(setup):
    store, service, agent = setup
    project = create(store)
    body = dict(expected_revision=project['revision'])
    original_execute = store.db._execute
    lost = False
    def execute_then_disconnect(statement):
        nonlocal lost
        original_execute(statement)
        if not lost and statement.startswith('UPDATE scientific_workspace_projects'):
            lost = True
            raise ConnectionResetError('Synthetic SQL response lost after commit')
    store.db._execute = execute_then_disconnect
    with pytest.raises(ConnectionResetError):
        service.action('alice', project['id'], 'plan', body, 'durable-plan')
    assert agent.posts == 0
    saved = service.action('alice', project['id'], 'plan', body, 'durable-plan')
    assert len(saved['jobs']) == 1 and agent.posts == 1


def test_unresolved_admission_lookup_never_blindly_posts_again(setup):
    store, service, agent = setup
    project = action(store, create(store), 'plan')
    original_request = agent._request
    def lose_before_admission(method, path, payload=None, **kwargs):
        if method == 'POST' and path == '/v1/jobs':
            agent.posts += 1
            raise WorkerError(503, 'agent_unavailable', 'Admission outcome unknown', True)
        return original_request(method, path, payload, **kwargs)
    agent._request = lose_before_admission
    with pytest.raises(WorkerError):
        service.reconcile('alice', project['id'])
    for _ in range(2):
        WorkspaceService(store, agent).reconcile('alice', project['id'])
    saved = store.get('alice', project['id'])
    assert agent.posts == 1 and agent.lookups == 2
    assert saved['jobs'][0]['state'] == 'admission_uncertain'
    with pytest.raises(WorkspaceError, match='job_retry_not_allowed'):
        action(store, saved, 'retry', dict(job_id=saved['jobs'][0]['id']))


def test_cancel_before_admission_and_after_restart(setup):
    store, service, agent = setup
    project = action(store, create(store), 'plan')
    task_id = project['active_job_id']
    project = action(store, project, 'cancel', dict(job_id=task_id))
    service.reconcile('alice', project['id'])
    assert agent.posts == 0 and project['jobs'][0]['state'] == 'cancelled'
    project = action(store, project, 'retry', dict(job_id=task_id))
    service.reconcile('alice', project['id'])
    project = store.get('alice', project['id'])
    project = action(store, project, 'cancel', dict(job_id=project['active_job_id']))
    WorkspaceService(store, agent).reconcile('alice', project['id'])
    assert agent.posts == 1
    assert store.get('alice', project['id'])['jobs'][-1]['state'] == 'cancelled'


def test_tampered_result_cannot_enter_current_state(setup):
    store, service, agent = setup
    project = action(store, create(store), 'plan')
    service.reconcile('alice', project['id'])
    raw = store.raw('alice', project['id'])
    agent.complete('alice', project['id'], raw['active_job_id'], tamper=True)
    service.reconcile('alice', project['id'])
    project = store.get('alice', project['id'])
    assert project['plan'] is None and project['jobs'][0]['state'] == 'invalid_result'
    assert agent.acks == 0


def test_material_parsing_is_bounded_and_honest(setup):
    store, _, _ = setup
    project = create(store)
    good = parse_material('notes.md', '科研参考资料'.encode('utf-8'))
    assert good['access_level'] == 'full_text' and good['status'] == 'ready'
    bad = parse_material('scan.pdf', b'%PDF-1.4\nnot a valid document')
    assert bad['status'] == 'failed' and bad['access_level'] == 'unavailable' and not bad['text']
    doi = doi_material('https://doi.org/10.1234/supplied')
    assert doi['access_level'] == 'metadata' and doi['text'] == ''
    with pytest.raises(MaterialError):
        doi_material('https://private.invalid/internal')
    with pytest.raises(MaterialError) as exc:
        parse_material('large.txt', b'a' * (MAX_UPLOAD_BYTES + 1))
    assert exc.value.status == 413
    with pytest.raises(MaterialError):
        parse_material('payload.exe', b'not text')
    project = store.add_material('alice', project['id'], project['revision'], 'upload-failed',
                                 filename='scan.pdf', raw=b'%PDF-bad')
    assert project['materials'][0]['parse_error']


def test_failed_upload_must_be_explicitly_excluded_before_retrieval(setup):
    store, service, agent = setup
    project = create(store)
    project = store.add_material('alice', project['id'], project['revision'], 'bad-pdf',
        filename='scan.pdf', raw=b'%PDF-bad')
    project = finish(store, service, agent, action(store, project, 'plan'), 'plan')
    with pytest.raises(WorkspaceError, match='unready_materials_must_be_excluded'):
        action(store, project, 'confirm_plan')
    assert agent.posts == 1
    project = store.remove_material('alice', project['id'], project['materials'][0]['material_id'],
                                   project['revision'], 'exclude-failed-material')
    # Excluding material after planning changes the first gate's input and
    # explicitly requires a new plan rather than inventing its confirmation.
    assert project['plan'] is None
    project = finish(store, service, agent, action(store, project, 'plan'), 'plan')
    project = action(store, project, 'confirm_plan')
    assert project['confirmations']['plan']


def test_outline_edit_stays_bound_to_real_evidence_and_title_does_not_invalidate(setup):
    store, _, _ = setup
    project = outlined(setup)
    before = deepcopy(project['confirmations']['plan'])
    project = store.patch('alice', project['id'], dict(expected_revision=project['revision'],
        title='Renamed project'), 'title-only')
    assert project['confirmations']['plan'] == before and project['outline']
    edited = deepcopy(project['outline']['content'])
    edited['sections'][0]['title'] = 'Recorded findings'
    project = store.patch('alice', project['id'], dict(expected_revision=project['revision'],
        outline=edited), 'outline-edit')
    assert project['outline']['content']['sections'][0]['title'] == 'Recorded findings'
    assert project['confirmations']['plan'] == before
    edited['evidence'][0]['paper_id'] = 'invented-paper'
    with pytest.raises(ValueError):
        store.patch('alice', project['id'], dict(expected_revision=project['revision'],
            outline=edited), 'fabricated-source')


def test_supplement_keeps_existing_report_and_requires_new_evidence_confirmation(setup):
    store, service, agent = setup
    project = drafted(setup)
    version_id = project['current_version_id']
    old_report = store.version('alice', project['id'], version_id)['report_markdown']
    project = action(store, project, 'supplement', dict(feedback='Find more in-scope comparisons'))
    assert project['current_version_id'] == version_id and project['confirmations']['evidence'] is None
    project = finish(store, service, agent, project, 'retrieve')
    project = finish(store, service, agent, project, 'outline')
    assert len(project['search_batches']) == 2 and project['active_job_id'] is None
    assert project['current_version_id'] == version_id
    assert store.version('alice', project['id'], version_id)['report_markdown'] == old_report
    assert project['confirmations']['evidence'] is None


def test_private_finalization_annotations_and_real_formats(setup):
    store, service, agent = setup
    project = drafted(setup)
    version_id = project['current_version_id']
    version = store.version('alice', project['id'], version_id)
    section_id = version['section_index']['sections'][0]['section_id']
    project = store.annotate('alice', project['id'], dict(expected_revision=project['revision'],
        version_id=version_id, section_id=section_id, text='Clarify this limit'), 'annotation')
    project = action(store, project, 'finalize', dict(version_id=version_id, unresolved_notes='Synthetic only'))
    assert project['stage'] == 'final'
    assert project['finalizations'][0]['publication_authorized'] is False
    assert project['annotations'][0]['section_id'] == section_id
    app = FastAPI()
    app.include_router(create_router(service))
    client = TestClient(app, headers={'X-Forwarded-User': 'alice'})
    url = '/api/v1/research/projects/' + project['id'] + '/versions/' + version_id
    assert client.get(url + '/download?format=markdown').text == version['report_markdown']
    assert client.get(url + '/download?format=sources').json()['status'] == 'final'
    assert client.get(url + '/download?format=pdf').status_code == 422
    assert client.get(url, headers={'X-Forwarded-User': 'bob'}).status_code == 404
    assert client.get('/api/v1/research/projects', headers={'X-Forwarded-User': ''}).status_code == 401


def test_fixed_evidence_revision_is_candidate_until_explicit_adoption(setup):
    store, service, agent = setup
    project = drafted(setup)
    original_id = project['current_version_id']
    original = store.version('alice', project['id'], original_id)
    section = original['section_index']['sections'][0]
    project = action(store, project, 'revise', dict(version_id=original_id,
        section_ids=[section['section_id']], feedback='Clarify the finding using the same abstract'))
    project = finish(store, service, agent, project, 'revise')
    assert project['jobs'][-1]['state'] == 'completed'
    assert project['current_version_id'] == original_id
    candidate = project['versions'][-1]
    comparison = store.compare('alice', project['id'], original_id, candidate['id'])
    assert comparison['changed'] and 'Revised synthetic finding' in comparison['diff']
    fixed = store.raw('alice', project['id'])['versions']
    assert fixed[0]['trace_artifacts']['papers'] == fixed[1]['trace_artifacts']['papers']
    assert fixed[0]['trace_artifacts']['evidence'] == fixed[1]['trace_artifacts']['evidence']
    suffix = original['report_markdown'].encode('utf-8')[section['end_byte']:]
    assert fixed[1]['report_markdown'].encode('utf-8').endswith(suffix)
    project = action(store, project, 'adopt', dict(version_id=candidate['id']))
    assert project['current_version_id'] == candidate['id']
    assert original['report_markdown'] == store.version('alice', project['id'], original_id)['report_markdown']


def test_failed_candidate_preserves_parent_and_changed_inputs_cannot_finalize(setup):
    store, service, agent = setup
    project = drafted(setup)
    original_id = project['current_version_id']
    version = store.version('alice', project['id'], original_id)
    project = action(store, project, 'revise', dict(version_id=original_id,
        section_ids=[version['section_index']['sections'][0]['section_id']], feedback='Clarify wording'))
    service.reconcile('alice', project['id'])
    raw = store.raw('alice', project['id'])
    agent.complete('alice', project['id'], raw['active_job_id'], tamper=True)
    service.reconcile('alice', project['id'])
    project = store.get('alice', project['id'])
    assert project['jobs'][-1]['state'] == 'invalid_result'
    assert len(project['versions']) == 1 and project['current_version_id'] == original_id
    project = store.patch('alice', project['id'], dict(expected_revision=project['revision'],
        requirements=dict(question='Changed research scope')), 'new-scope')
    with pytest.raises(WorkspaceError, match='stale_version_requires_reconfirmation'):
        action(store, project, 'finalize', dict(version_id=original_id))
    assert store.version('alice', project['id'], original_id)['stale']
