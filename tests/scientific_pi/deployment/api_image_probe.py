"""Run the actual API entrypoint against loopback fixtures in a network-none image.

Only the image's production dependencies are needed. This verifies installation,
startup and API wiring; synthetic worker health is not production Pi acceptance.
"""
from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid


LEGACY_ID = '35c75439-6629-4370-9159-5d2176a1efa0'
LEGACY_REPORT = '# Historical report\r\nExact bytes: 保留旧报告。\r\n'
REPORT = ('合成验收前言。\r\n\r\n# Pi report\r\n\r\n'
          '## 分析\r\n<!-- claim:c1 -->\r\nSynthetic finding [1]. 科研\r\n\r\n'
          '```python\r\n# Not a section\r\n```\r\n\r\n'
          '## 分析\r\nSynthetic limitations [1].\r\n')


def fixture_records(report):
    """Self-contained recorded-evidence fixture; no remote academic source."""
    acquired = dict(available=True, obtained_at='2026-10-25T00:00:00Z')
    absent = dict(available=False, obtained_at=None)
    paper = dict(paper_id='p1', title='Synthetic image acceptance paper', authors=['Fixture Author'], year=2025,
                 doi=None, pmid=None, url='https://example.org/synthetic', sources=['fixture'],
                 retrieved_at='2026-10-25T00:00:00Z', abstract='Synthetic abstract.', full_text_path=None,
                 availability=dict(metadata=acquired, abstract=acquired, full_text=absent),
                 verification=dict(status='unknown', checked_at=None, source=None, error=None))
    rows = dict(papers=[paper],
                evidence=[dict(evidence_id='e1', paper_id='p1', finding='Synthetic finding', method='Fixture',
                               limitations='Abstract only', access_level='abstract', locator='abstract')],
                claims=[dict(claim_id='c1', marker='<!-- claim:c1 -->', evidence_ids=['e1'],
                             assessment='supported', explanation='Synthetic recorded evidence only')],
                references=[dict(n=1, paper_id='p1')])
    artifacts = {name: (json.dumps(value, ensure_ascii=False) if name == 'references' else
                       ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in value)).encode('utf-8')
                 for name, value in rows.items()}
    all_content = dict(artifacts, report=report.encode('utf-8'))
    items = [dict(artifact_id=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                  media_type='text/markdown' if name == 'report' else
                  'application/json' if name == 'references' else 'application/x-ndjson')
             for name, raw in all_content.items()]
    return items, all_content


def verify_installation():
    assert importlib.util.find_spec('crewai') is None, 'CrewAI must be absent'
    assert not Path('/app/scientific_agent/crew.py').exists()
    installed = {dist.metadata['Name'].lower().replace('_', '-'): dist.version
                 for dist in importlib.metadata.distributions()}
    assert not any(name.startswith('crewai') for name in installed)
    lock = Path('/tmp/scientific-api-requirements.txt').read_text().splitlines()
    expected = dict(line.split('==', 1) for line in lock if line and not line.startswith('#'))
    for name, version in expected.items():
        assert importlib.metadata.version(name) == version, name
    pip_check = subprocess.run([sys.executable, '-m', 'pip', 'check'], text=True, capture_output=True, check=True)
    return expected,pip_check


def main():
    expected,pip_check = verify_installation()

    # All SQL is local, in-memory, and reachable only from this network namespace.
    sql = sqlite3.connect(':memory:', check_same_thread=False)
    sql.row_factory = sqlite3.Row
    lock_sql = threading.RLock()
    tasks, results, revision_jobs, revision_materials = {}, {}, {}, {}
    task_manifests, task_artifacts = {}, {}
    manifest, artifact_content = fixture_records(REPORT)
    worker_puts, artifact_reads = [], []
    expired_artifacts = set()
    from scientific_agent.pi_contracts import report_sha256, result_digest, utc_now

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, value):
            raw = json.dumps(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def send_artifact(self, task_id, artifact_id):
            raw = task_artifacts.get(task_id, artifact_content)[artifact_id]
            metadata = next(row for row in task_manifests.get(task_id, manifest) if row['artifact_id'] == artifact_id)
            self.send_response(200)
            self.send_header('Content-Type', metadata['media_type'])
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == '/v1/health':
                return self.send(200, dict(alive=True, queue_writable=True, runtime_ready=True, worker_ready=True,
                    capabilities=dict(revision_job_versions=['1'], revision_input_scope='recorded_evidence_only',
                                      max_revision_input_bytes=16*1024*1024)))
            if self.path.startswith('/v1/tasks?'):
                return self.send(200, dict(items=[dict(t) for t in tasks.values() if t['state'] == 'publishing'], next_cursor=None))
            parts = self.path.split('/')
            if len(parts) >= 4 and parts[3] in tasks:
                if len(parts) >= 5 and parts[4] == 'artifacts':
                    artifact_reads.append(self.path)
                    if parts[3] in expired_artifacts:
                        return self.send(410, {'error': {'code': 'artifact_expired'}})
                    if len(parts) == 5:
                        return self.send(200, {'items': task_manifests.get(parts[3], manifest)})
                    if len(parts) == 6 and parts[5] in task_artifacts.get(parts[3], artifact_content):
                        return self.send_artifact(parts[3], parts[5])
                    return self.send(404, {'error': {'code': 'artifact_not_found'}})
                if self.path.endswith('/result'):
                    return self.send(200, results[parts[3]])
                return self.send(200, tasks[parts[3]])
            self.send(404, {'error': {'code': 'task_not_found'}})

        def do_PUT(self):
            job = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path.startswith('/v1/revisions/'):
                job, material = job['job'], job['input']
                revision_jobs[job['task_id']] = job
                revision_materials[job['task_id']] = material
            worker_puts.append(job['task_id'])
            task = dict(id=job['task_id'], topic=job['topic'], engine='pi', mode=job['mode'], state='queued',
                        attempt=0, revision=1, created_at=job['created_at'], updated_at=utc_now(),
                        execution_deadline_at=job['execution_deadline_at'])
            tasks[job['task_id']] = task
            self.send(200, task)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path in ('/execute', '/query'):
                with lock_sql:
                    cursor = sql.execute(body['sql'])
                    rows = [dict(row) for row in cursor.fetchall()] if self.path == '/query' else []
                    sql.commit()
                return self.send(200, {'rows': rows})
            task_id = self.path.split('/')[3]
            if self.path.endswith('/cancel'):
                tasks[task_id].update(state='cancelled', revision=2)
            elif self.path.endswith('/publication-ack'):
                tasks[task_id].update(state='succeeded', report_id=task_id, revision=3)
            else:
                return self.send(404, {'error': {'code': 'task_not_found'}})
            self.send(200, tasks[task_id])

    fixture = ThreadingHTTPServer(('127.0.0.1', 18081), Handler)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    os.environ.update(SCIENTIFIC_PI_WORKER_URL='http://127.0.0.1:18081', SCIENTIFIC_PI_WORKER_TOKEN='synthetic-control',
                      SCIENTIFIC_PI_RUNTIME_REVISION='pi-jsonl-0.73.1-v1', SCIENTIFIC_PI_SKILL_BUNDLE_REVISION='local-procedures-v1')
    assert not os.getenv('LLM_BASE_URL') and not os.getenv('LLM_SERVICE_TOKEN')
    from tools import sqlite_client as database
    database.SQLITE_URL = 'http://127.0.0.1:18081'
    database.init_db('scientific')
    with lock_sql:
        sql.execute('INSERT INTO scientific_tasks (id,topic,status,user_id) VALUES (?,?,?,?)',
                    (LEGACY_ID, 'Legacy research', 'running', 'old-user'))
        sql.execute('INSERT INTO scientific_reports (id,task_id,topic,content) VALUES (?,?,?,?)',
                    (LEGACY_ID, LEGACY_ID, 'Historical research', LEGACY_REPORT))
        sql.commit()

    # Load the Docker entrypoint's real module-level app and its full lifespan.
    import uvicorn
    from app.api import scientific_agent as api
    config = uvicorn.Config('app.api.scientific_agent:app', host='127.0.0.1', port=18080, log_level='warning')
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    def request(method, path, payload=None, user='acceptance-user', key=None):
        headers = {'Content-Type': 'application/json', 'X-Forwarded-User': user}
        if key is not None:
            headers['Idempotency-Key'] = key
        req = urllib.request.Request('http://127.0.0.1:18080' + path,
                                     data=None if payload is None else json.dumps(payload).encode(),
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def writing_counts():
        with lock_sql:
            return {kind: sql.execute('SELECT COUNT(*) FROM scientific_report_' + kind).fetchone()[0]
                    for kind in ('versions', 'snapshots', 'revision_intents')}

    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, 'Actual uvicorn API did not start'
    checks = []
    try:
        code, raw = request('GET', '/scientific-health')
        health = json.loads(raw)
        assert code == 200 and health['backend'] == 'pi' and health['worker_ready'] is True
        assert health['model_probe_performed'] is False
        checks.append('api_health_uses_worker_without_direct_model_configuration')

        workspace = '/api/v1/research/projects'
        code, _ = request('GET', workspace, user='')
        assert code == 401
        payload = {'requirements': {'question': 'Image workspace persistence check'}}
        code, raw = request('POST', workspace, payload, key='image-workspace-create')
        project = json.loads(raw)
        assert code == 201 and project['stage'] == 'goal' and project['jobs'] == [], raw
        project_path = workspace + '/' + project['id']
        code, replay = request('POST', workspace, payload, key='image-workspace-create')
        assert code == 201 and json.loads(replay)['id'] == project['id']
        code, _ = request('GET', project_path, user='different-user')
        assert code == 404
        code, raw = request('PATCH', project_path, {'expected_revision': project['revision'],
            'requirements': {'purpose': 'Saved without starting a model'}}, key='image-workspace-save')
        project = json.loads(raw)
        assert code == 200 and project['revision'] == 1 and project['jobs'] == [], raw
        code, raw = request('POST', project_path + '/actions/confirm_evidence',
            {'expected_revision': project['revision']}, key='image-forbidden-draft')
        assert code == 409 and json.loads(raw)['detail']['code'] == 'plan_confirmation_required', raw
        code, raw = request('GET', project_path)
        assert code == 200 and json.loads(raw)['requirements']['purpose'] == 'Saved without starting a model'
        checks.append('workspace_real_image_persistence_idempotency_ownership_and_no_ungated_draft')

        code, _ = request('POST', '/scientific-research', dict(topic='Rejected old engine', engine='crewai'))
        assert code == 422
        checks.append('new_crewai_request_rejected')
        code, _ = request('POST', '/scientific-research', dict(topic='Missing identity'), user='')
        assert code == 401
        checks.append('missing_trusted_identity_rejected')

        code, raw = request('GET', '/scientific-research/' + LEGACY_ID)
        assert code == 200 and json.loads(raw)['status'] == 'running'
        code, raw = request('GET', '/scientific-download/' + LEGACY_ID)
        assert code == 200 and raw == LEGACY_REPORT.encode()
        checks.append('legacy_task_unchanged_and_report_bytes_preserved')
        code, raw = request('GET', '/scientific-research/' + LEGACY_ID + '/citations')
        assert code == 200 and json.loads(raw)['availability'] == 'legacy', raw
        assert not artifact_reads
        checks.append('legacy_citations_degrade_without_worker_access')

        code, raw = request('POST', '/scientific-research', dict(topic='Pi cancellation check', mode='brief'))
        assert code == 200, raw
        task_id = json.loads(raw)['id']
        assert json.loads(raw)['engine'] == 'pi'
        code, _ = request('GET', '/scientific-research/' + task_id, user='different-user')
        assert code == 404
        code, raw = request('POST', '/scientific-research/' + task_id + '/cancel', {})
        assert code == 200 and json.loads(raw)['state'] == 'cancelled'
        checks.append('pi_submit_owner_isolation_and_cancel')

        code, raw = request('POST', '/scientific-research', dict(topic='Pi publication check', mode='review'))
        assert code == 200, raw
        task_id = json.loads(raw)['id']
        tasks[task_id].update(state='publishing', attempt=1, revision=2)
        result = dict(schema_version='1', task_id=task_id, attempt=1, report_markdown=REPORT,
                      report_sha256=report_sha256(REPORT), artifacts=manifest,
                      provenance={'model': 'synthetic-model'}, validation={})
        result['result_digest'] = result_digest(result)
        results[task_id] = result
        api.app.state.projection.reconcile()
        code, raw = request('GET', '/scientific-download/' + task_id)
        assert code == 200 and raw == REPORT.encode()
        code, _ = request('GET', '/scientific-download/' + task_id, user='different-user')
        assert code == 404
        checks.append('publication_ack_and_exact_owner_report_download')

        # Exercise the actual mounted image modules through real HTTP, without
        # importing the test suite or substituting the production application.
        report_path = '/scientific-reports/' + task_id
        citations_path = '/scientific-research/' + task_id + '/citations'
        preview_path = report_path + '/revision-previews'
        before = writing_counts()
        assert before == dict(versions=0, snapshots=0, revision_intents=0), before
        reads_before = len(artifact_reads)
        for user, expected_code in (('', 401), ('different-user', 404)):
            for path in (citations_path, report_path + '/sections', report_path + '/versions', report_path + '/writing-state'):
                code, raw = request('GET', path, user=user)
                assert code == expected_code, (path, code, raw)
        assert len(artifact_reads) == reads_before
        checks.append('writing_and_citations_authorize_before_worker_read')

        code, raw = request('GET', citations_path)
        trace = json.loads(raw)
        assert code == 200 and trace['availability'] == 'available', raw
        assert trace['report_sha256'] == report_sha256(REPORT)
        assert trace['citations'][0]['paper']['title'] == 'Synthetic image acceptance paper'
        assert trace['citations'][0]['evidence'][0]['access_level'] == 'abstract'
        assert trace['citations'][0]['claims'][0]['claim_id'] == 'c1'
        assert 'full_text_path' not in trace['citations'][0]['paper']
        assert len(artifact_reads) == reads_before + 5
        checks.append('report_bound_citations_and_record_transfers')

        code, raw = request('GET', report_path + '/sections')
        section_index = json.loads(raw)
        assert code == 200 and section_index['report_sha256'] == report_sha256(REPORT), raw
        repeated = [row for row in section_index['sections'] if row['title'] == '分析']
        assert len(repeated) == 2 and repeated[0]['section_id'] != repeated[1]['section_id']
        assert 'Not a section' not in [row['title'] for row in section_index['sections']]
        code, raw = request('GET', report_path + '/versions')
        assert code == 200 and json.loads(raw)['items'] == [], raw
        assert writing_counts() == before
        checks.append('exact_sections_duplicate_headings_and_read_only_history')

        selected = repeated[0]
        payload = dict(base_sha256=section_index['report_sha256'], section_id=selected['section_id'],
                       instruction='Explain the limits of the existing evidence. 保留引用。', timeout_seconds=600)
        reads_before = len(artifact_reads)
        for user, expected_code in (('', 401), ('different-user', 404)):
            code, raw = request('POST', preview_path, payload, user=user, key='image-private-preview')
            assert code == expected_code, (code, raw)
        assert len(artifact_reads) == reads_before and writing_counts() == before
        code, raw = request('POST', preview_path, payload, key='image-preview')
        prepared = json.loads(raw)
        assert code == 200, raw
        assert prepared['dispatch_available'] is False and prepared['intent']['status'] == 'prepared'
        assert prepared['section']['text'].encode('utf-8') == REPORT.encode('utf-8')[selected['start_byte']:selected['end_byte']]
        assert prepared['snapshot']['material_policy'] == 'recorded_evidence_only'
        assert len(prepared['snapshot']['snapshot_sha256']) == 64
        assert not any(field in prepared['snapshot'] for field in ('report', 'artifacts', 'items', 'full_text_path'))
        assert writing_counts() == dict(versions=1, snapshots=1, revision_intents=1)
        assert len(worker_puts) == 2, 'Revision preparation dispatched an unexpected worker task'
        checks.append('fixed_private_revision_preview_without_model_dispatch')

        reads_after_prepare = len(artifact_reads)
        code, raw = request('POST', preview_path, payload, key='image-preview')
        replay = json.loads(raw)
        assert code == 200 and replay['intent']['intent_id'] == prepared['intent']['intent_id'], raw
        assert replay['snapshot']['snapshot_id'] == prepared['snapshot']['snapshot_id']
        code, raw = request('POST', preview_path, dict(payload, instruction='A different instruction'), key='image-preview')
        assert code == 409 and json.loads(raw)['detail']['code'] == 'intent_key_conflict', raw
        assert len(artifact_reads) == reads_after_prepare
        assert writing_counts() == dict(versions=1, snapshots=1, revision_intents=1)
        checks.append('preview_idempotent_retry_and_changed_fingerprint_conflict')

        version_id = prepared['parent']['version_id']
        snapshot_id = prepared['snapshot']['snapshot_id']
        intent_id = prepared['intent']['intent_id']
        version_path = '/scientific-report-versions/' + version_id
        private_paths = (version_path, version_path + '/sections', version_path + '/download',
                         '/scientific-report-snapshots/' + snapshot_id, '/scientific-revision-previews/' + intent_id)
        before = writing_counts()
        for path in private_paths:
            code, raw = request('GET', path)
            assert code == 200, (path, code, raw)
            for user, expected_code in (('', 401), ('different-user', 404)):
                code, raw = request('GET', path, user=user)
                assert code == expected_code, (path, code, raw)
        code, raw = request('GET', version_path)
        assert code == 200 and json.loads(raw)['content'] == REPORT
        code, raw = request('GET', version_path + '/download')
        assert code == 200 and raw == REPORT.encode('utf-8')
        code, raw = request('GET', version_path + '/sections')
        assert code == 200 and json.loads(raw)['sections'] == section_index['sections']
        code, raw = request('GET', '/scientific-revision-previews/' + intent_id)
        assert code == 200 and json.loads(raw)['dispatch_available'] is False
        code, raw = request('GET', report_path + '/versions')
        assert code == 200 and [row['version_id'] for row in json.loads(raw)['items']] == [version_id]
        assert writing_counts() == before
        checks.append('private_version_download_snapshot_and_intent_owner_isolation')

        # A later source expiry cannot change the already-fixed input. A new
        # instruction reuses that snapshot, while read-only citations degrade.
        expired_artifacts.add(task_id)
        code, raw = request('POST', preview_path, dict(payload, instruction='Explain uncertainty from the same records.'),
                            key='image-preview-after-source-expiry')
        after_expiry = json.loads(raw)
        assert code == 200 and after_expiry['snapshot']['snapshot_id'] == snapshot_id, raw
        assert after_expiry['intent']['intent_id'] != intent_id
        assert len(artifact_reads) == reads_after_prepare and len(worker_puts) == 2
        code, raw = request('GET', citations_path)
        assert code == 200 and json.loads(raw)['availability'] == 'expired', raw
        for path in ('/scientific-download/' + task_id, version_path + '/download'):
            code, raw = request('GET', path)
            assert code == 200 and raw == REPORT.encode('utf-8'), (path, code, raw)
        assert writing_counts() == dict(versions=1, snapshots=1, revision_intents=2)
        checks.append('fixed_input_reuse_after_source_expiry_and_original_preserved')

        # Real installed API -> HTTP PiClient -> synthetic fixed-input worker
        # completion. No model or production storage is used by this probe.
        code, raw = request('GET', report_path + '/writing-state')
        state = json.loads(raw)
        assert code == 200 and state['current_version_id'] == version_id and state['selection_revision'] == 0, raw
        start_path = '/scientific-revision-previews/' + intent_id + '/start'
        code, raw = request('POST', start_path, {}, user='different-user')
        assert code == 404, raw
        code, raw = request('POST', start_path, {})
        assert code == 200, raw
        revision_id = json.loads(raw)['id']
        assert json.loads(raw)['task_kind'] == 'revision'
        code, raw = request('POST', start_path, {})
        assert code == 200 and json.loads(raw)['id'] == revision_id and len(worker_puts) == 3, raw
        fixed, job = revision_materials[revision_id], revision_jobs[revision_id]
        from scientific_agent.pi_revision_validation import compose_revision
        artifacts = {name: base64.b64decode(value) for name, value in fixed['artifacts'].items()}
        output = dict(schema_version='1',section_markdown=(
            '## 分析\r\n\r\n<!-- claim:image_revision -->\r\n'
            'The synthetic abstract records a limited finding; full text has not been restored [1].\r\n\r\n'),
            claims=[dict(claim_id='image_revision',marker='<!-- claim:image_revision -->',evidence_ids=['e1'],
                         assessment='supported',explanation='Synthetic recorded abstract only.')])
        composed = compose_revision(fixed['report'],fixed['items'],artifacts,job['section_id'],job['base_sha256'],output)
        raw_output = json.dumps(output,ensure_ascii=False).encode('utf-8')
        task_artifacts[revision_id] = dict(composed['artifacts'],revision_output=raw_output)
        task_manifests[revision_id] = composed['items'] + [dict(artifact_id='revision_output',media_type='application/json',
            size=len(raw_output),sha256=hashlib.sha256(raw_output).hexdigest())]
        result = dict(schema_version='1',task_id=revision_id,attempt=1,report_markdown=composed['report_markdown'],
            report_sha256=report_sha256(composed['report_markdown']),artifacts=task_manifests[revision_id],
            provenance=dict(model='synthetic-fixed-records',material_policy='recorded_evidence_only',
                retrieval_performed=False,full_text_restored=False,
                revision={key:job[key] for key in ('intent_id','parent_version_id','root_report_id',
                    'base_sha256','section_id','section_sha256','input_sha256')}),validation=composed['validation'])
        result['result_digest'] = result_digest(result)
        results[revision_id] = result
        tasks[revision_id].update(state='publishing',attempt=1,revision=2)
        api.app.state.projection.reconcile()
        code, raw = request('GET','/scientific-research/' + revision_id)
        completed = json.loads(raw)
        assert code == 200 and completed['state'] == 'succeeded', raw
        candidate_id = completed['candidate_version_id']
        code, raw = request('GET',report_path + '/writing-state')
        assert code == 200 and json.loads(raw)['current_version_id'] == version_id, raw
        checks.append('revision_dispatch_replay_and_candidate_publication_without_automatic_adoption')

        comparison_path = '/scientific-report-versions/' + candidate_id + '/comparison'
        code, raw = request('GET',comparison_path,user='different-user')
        assert code == 404, raw
        code, raw = request('GET',comparison_path)
        comparison = json.loads(raw)
        assert code == 200 and comparison['adoption']['can_adopt'] is True, raw
        assert comparison['section']['before'] == prepared['section']['text']
        assert comparison['section']['after'] == output['section_markdown']
        assert comparison['diff']['available'] is True
        adopt_body = {key:comparison['adoption'][key] for key in ('expected_current_version_id',
            'expected_selection_revision','base_sha256','candidate_sha256','comparison_token')}
        adopt_path = '/scientific-report-versions/' + candidate_id + '/adopt'
        code, raw = request('POST',adopt_path,adopt_body,key='image-adoption',user='different-user')
        assert code == 404, raw
        for _ in range(2):
            code, raw = request('POST',adopt_path,adopt_body,key='image-adoption')
            adopted = json.loads(raw)
            assert code == 200 and adopted['current_version_id'] == candidate_id and adopted['selection_revision'] == 1, raw
        code, raw = request('POST',adopt_path,adopt_body,key='image-stale-adoption')
        assert code == 409, raw
        for path, expected_bytes in (('/scientific-download/' + task_id,REPORT.encode('utf-8')),
                ('/scientific-report-versions/' + candidate_id + '/download',composed['report_markdown'].encode('utf-8'))):
            code, raw = request('GET',path)
            assert code == 200 and raw == expected_bytes, (path,code,raw)
        checks.append('candidate_diff_explicit_adoption_idempotent_replay_conflict_and_exact_original_downloads')
        assert not any(name.startswith('crewai') for name in sys.modules)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        fixture.shutdown()
        sql.close()
    assert not thread.is_alive()
    print(json.dumps(dict(status='passed', architecture=platform.machine(), python=platform.python_version(),
                          crewai_installed=False, crewai_imported=False, direct_model_configuration_present=False,
                          api_entrypoint='app.api.scientific_agent:app', pip_check=pip_check.stdout.strip(),
                          checks=checks, locked_distributions=expected, report_sha256=hashlib.sha256(REPORT.encode()).hexdigest(),
                          writing_material_policy='recorded_evidence_only', revision_dispatch_performed=True,
                          real_model_executed=False, explicit_adoption_verified=True,
                          worker_submissions=len(worker_puts), artifact_http_reads=len(artifact_reads),
                          fixture_scope='loopback SQLite in memory and synthetic Pi HTTP worker; no live model or production DB',
                          graceful_shutdown=True), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
