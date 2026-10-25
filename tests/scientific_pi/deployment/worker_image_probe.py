"""Network-none worker image packaging check; all task output is synthetic.

Runs as a nonroot user with an empty writable /data. It does not enable the
production runtime evidence gates, call a model, or test process containment.
"""
from datetime import timedelta
import base64
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid


def fixture(runtime_revision, bundle_revision):
    report = ('# Synthetic image fixture\r\nUnchanged opening.\r\n\r\n'
              '## Findings\r\n<!-- claim:c1 -->\r\nSynthetic finding [1].\r\n\r\n'
              '## Limitations\r\nUnchanged closing.\r\n')
    acquired = dict(available=True,obtained_at='2026-10-25T00:00:00Z')
    absent = dict(available=False,obtained_at=None)
    paper = dict(paper_id='p1',title='Synthetic image paper',authors=['Fixture Author'],year=2025,
        doi=None,pmid=None,url='https://example.org/fixture',sources=['fixture'],
        retrieved_at='2026-10-25T00:00:00Z',abstract='Synthetic finding',full_text_path=None,
        availability=dict(metadata=acquired,abstract=acquired,full_text=absent),
        verification=dict(status='unknown',checked_at=None,source=None,error=None))
    records = dict(papers=[paper],evidence=[dict(evidence_id='e1',paper_id='p1',finding='Synthetic finding',
        method='Fixture',limitations='Abstract only',access_level='abstract',locator='abstract')],
        claims=[dict(claim_id='c1',marker='<!-- claim:c1 -->',evidence_ids=['e1'],
                     assessment='supported',explanation='Synthetic recorded abstract only')],
        references=[dict(n=1,paper_id='p1')])
    artifacts = {key:(json.dumps(rows) if key == 'references' else
        ''.join(json.dumps(row)+'\n' for row in rows)).encode('utf-8') for key,rows in records.items()}
    all_files = dict(artifacts,report=report.encode('utf-8'))
    items = [dict(artifact_id=key,size=len(raw),sha256=hashlib.sha256(raw).hexdigest(),
        media_type='text/markdown' if key == 'report' else 'application/json' if key == 'references' else 'application/x-ndjson')
        for key,raw in all_files.items()]
    material = dict(report=report,items=items,artifacts={key:base64.b64encode(raw).decode('ascii') for key,raw in artifacts.items()})
    from scientific_agent.pi_contracts import utc_now, parse_timestamp
    from scientific_agent.pi_sections import index_sections
    from scientific_agent.pi_revision_contracts import revision_input_digest, revision_material_digest
    index = index_sections(report)
    section = next(row for row in index['sections'] if row['title'] == 'Findings')
    created = utc_now()
    job = dict(schema_version='1',task_kind='revision',task_id=str(uuid.uuid4()),topic='Synthetic image revision',
        user_id='synthetic-image-user',engine='pi',mode='review',timeout_seconds=600,created_at=created,
        execution_deadline_at=(parse_timestamp(created)+timedelta(seconds=600)).isoformat(),
        runtime_revision=runtime_revision,skill_bundle_revision=bundle_revision,
        intent_id=str(uuid.uuid4()),parent_version_id=str(uuid.uuid4()),root_report_id=str(uuid.uuid4()),
        base_sha256=index['report_sha256'],section_id=section['section_id'],section_sha256=section['sha256'],
        instruction='Explain the limitations of the recorded synthetic abstract.',
        input_sha256=revision_material_digest(material),material_policy='recorded_evidence_only')
    job['input_digest'] = revision_input_digest(job)
    return job,material,artifacts,section


def verify_installation():
    assert os.name == 'posix' and os.geteuid() != 0
    assert not any(os.environ.get(name) for name in ('SCIENTIFIC_PI_MODEL_API_KEY','LLM_SERVICE_TOKEN','SMTP_PASSWORD'))
    checks = []
    expected = dict(line.split('==',1) for line in Path('/tmp/scientific-pi-requirements.txt').read_text().splitlines()
                    if line and not line.startswith('#'))
    for name,version in expected.items():
        assert importlib.metadata.version(name) == version, name
    subprocess.run([sys.executable,'-m','pip','check'],capture_output=True,check=True)
    checks.append('locked_python_dependencies_and_pip_check')
    node_version = subprocess.check_output(['/usr/local/bin/node','--version'],text=True,timeout=30).strip()
    pi_version = subprocess.check_output(['/usr/local/bin/node',
        '/opt/pi/node_modules/@mariozechner/pi-coding-agent/dist/cli.js','--version'],text=True,timeout=30).strip()
    package = json.loads(Path('/opt/pi/node_modules/@mariozechner/pi-coding-agent/package.json').read_text())
    lock = json.loads(Path('/app/pi_worker/node/package-lock.json').read_text())
    from pi_worker.runtime.adapter import PI_VERSION
    assert package['version'] == PI_VERSION == lock['packages']['node_modules/@mariozechner/pi-coding-agent']['version']
    assert PI_VERSION in pi_version and int(node_version.removeprefix('v').split('.')[0]) >= 20
    checks.append('actual_pinned_pi_and_node_versions_without_model')
    return checks,expected,node_version,pi_version


def main(source_root=Path('/app'),data_root=Path('/data')):
    checks,expected,node_version,pi_version = verify_installation()
    from scientific_agent.pi_runtime_config import runtime_from_env, BUNDLE_MANIFEST_SHA256
    manifest_path = source_root / 'scientific_skills/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == BUNDLE_MANIFEST_SHA256
    for item in manifest['local_files']:
        assert hashlib.sha256((manifest_path.parent/item['path']).read_bytes()).hexdigest() == item['sha256'],item['path']
    checks.append('committed_skill_bundle_and_runtime_manifest_hashes_match')
    from pi_worker.app import create_app, WorkerSettings
    from pi_worker.store import QueueStore
    from scientific_agent.pi_revision_validation import compose_revision
    import uvicorn
    with tempfile.TemporaryDirectory(prefix='worker-image-',dir=data_root) as directory:
        root = Path(directory)
        actual_runtime = runtime_from_env(root)
        settings = actual_runtime.settings
        assert settings.fixture_mode is False
        assert not settings.capability_verified and not settings.model_verified and not settings.isolation_verified
        assert actual_runtime.health()['ready'] is False
        assert actual_runtime.capabilities['revision_job_versions'] == ['1']
        checks.append('default_production_evidence_gates_remain_closed')
        job,material,artifacts,section = fixture(settings.runtime_revision,manifest['bundle_revision'])
        worker_settings = WorkerSettings(root,'synthetic-image-token',runtime_revision=settings.runtime_revision,
                                         skill_bundle_revision=manifest['bundle_revision'],start_scheduler=False)
        store = QueueStore(root/'control/queue.sqlite')

        def request(method,path,payload=None,authorized=True):
            headers = {'Content-Type':'application/json'}
            if authorized: headers['Authorization'] = 'Bearer synthetic-image-token'
            req = urllib.request.Request('http://127.0.0.1:18082'+path,method=method,headers=headers,
                data=None if payload is None else json.dumps(payload).encode('utf-8'))
            try:
                with urllib.request.urlopen(req,timeout=10) as response: return response.status,response.read()
            except urllib.error.HTTPError as exc: return exc.code,exc.read()

        def serve(runtime,callback):
            app = create_app(worker_settings,store=store,runtime=runtime)
            server = uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18082,log_level='warning'))
            thread = threading.Thread(target=server.run,daemon=True)
            thread.start()
            try:
                deadline = time.monotonic()+10
                while not server.started and time.monotonic()<deadline: time.sleep(0.02)
                assert server.started
                callback()
            finally:
                server.should_exit = True
                thread.join(timeout=10)
            assert not thread.is_alive()

        def closed_probe():
            code,raw = request('GET','/v1/health',authorized=False)
            assert code == 401,raw
            code,raw = request('GET','/v1/health')
            assert code == 200 and json.loads(raw)['worker_ready'] is False,raw
            code,raw = request('PUT','/v1/revisions/'+job['task_id'],dict(job=job,input=material))
            assert code == 503 and json.loads(raw)['error']['code'] == 'revision_not_supported',raw
        serve(actual_runtime,closed_probe)
        checks.append('actual_worker_factory_authenticates_and_rejects_unverified_execution')

        class SyntheticReadyRuntime:
            ready = True
            capabilities = actual_runtime.capabilities
        def synthetic_probe():
            url = '/v1/revisions/'+job['task_id']
            code,raw = request('PUT',url,dict(job=job,input=material))
            assert code == 201 and json.loads(raw)['task_kind'] == 'revision',raw
            code,raw = request('PUT',url,dict(job=job,input=material))
            assert code == 200 and json.loads(raw)['id'] == job['task_id'],raw
            code,raw = request('POST','/v1/tasks/'+job['task_id']+'/cancel',{})
            assert code == 200 and json.loads(raw)['state'] == 'cancelled',raw
            with store._tx() as database:
                assert database.execute('SELECT COUNT(*) FROM revision_inputs').fetchone()[0] == 0
            code,raw = request('PUT',url,dict(job=job,input=material))
            assert code == 200 and json.loads(raw)['state'] == 'cancelled',raw
        serve(SyntheticReadyRuntime(),synthetic_probe)
        checks.append('synthetic_fixed_input_http_admission_idempotency_cancel_and_terminal_replay')
        output = dict(schema_version='1',section_markdown=(
            '## Findings\r\n<!-- claim:revision1 -->\r\n'
            'The synthetic finding is limited to recorded abstract evidence [1].\r\n\r\n'),
            claims=[dict(claim_id='revision1',marker='<!-- claim:revision1 -->',evidence_ids=['e1'],
                         assessment='supported',explanation='Synthetic recorded abstract only.')])
        composed = compose_revision(material['report'],material['items'],artifacts,job['section_id'],job['base_sha256'],output)
        old,new = material['report'].encode('utf-8'),composed['report_markdown'].encode('utf-8')
        assert new[:section['start_byte']] == old[:section['start_byte']]
        suffix = old[section['end_byte']:]
        assert new[-len(suffix):] == suffix
        for name in ('papers','evidence','references'): assert composed['artifacts'][name] == artifacts[name]
        checks.append('installed_revision_composer_preserves_external_bytes_and_recorded_evidence')
    print(json.dumps(dict(status='passed',architecture=platform.machine(),python=platform.python_version(),
        checks=checks,node_version=node_version,pi_version=pi_version,bundle_revision=manifest['bundle_revision'],
        bundle_manifest_sha256=BUNDLE_MANIFEST_SHA256,locked_distributions=expected,
        synthetic_runtime_only=True,real_model_executed=False,production_database_contacted=False,
        production_readiness_verified=False,process_isolation_verified=False),indent=2))


if __name__ == '__main__':
    main()
