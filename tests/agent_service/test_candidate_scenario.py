"""Offline scenario lineage/scope tests; all receipts and Pod evidence are synthetic.

These unit fixtures establish neither a real business confirmation nor actual
node/engine readiness. Production acceptance and routes are never created.
"""
from copy import deepcopy
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from agent_service.contracts import InlineFile, ServiceError, WorkerResult, canonical_bytes, digest, sign_receipt
from agent_service.deploy import acceptance_probe as probe, candidate_scenario as scenario
from agent_service.profiles.ppt import CHECKPOINT_SCHEMA, PPT_MASTER_REVISION, checkpoint_descriptor
from agent_service.worker_entry import host_policy
from test_acceptance_probe import authority, config
from test_catalog import image_packages


class _OwnerStat:
    """Preserve real inode/mode/link metadata; only this fixture's UID differs."""
    def __init__(self, observed, uid):
        self.observed, self.st_uid = observed, uid

    def __getattr__(self, name):
        return getattr(self.observed, name)


@pytest.fixture(autouse=True)
def synthetic_source_root_owner(tmp_path, monkeypatch):
    # Linux regression runs as UID10000. Only scenario reads of this unit
    # test's synthetic tmp tree get the explicit administrator-owner double.
    # Real production checks and all paths outside this test stay unchanged.
    native = type(Path())
    controlled = tmp_path.absolute()
    class SyntheticRootPath(native):
        def lstat(self, *args, **kwargs):
            observed = super().lstat(*args, **kwargs)
            try:
                self.relative_to(controlled)
            except ValueError:
                return observed
            return _OwnerStat(observed, 0)
    monkeypatch.setattr(scenario, 'Path', SyntheticRootPath)


def save(path, value):
    raw = value if type(value) is bytes else canonical_bytes(value)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def write_scenario(root, profile, task, files, *, parent=None, export=False):
    root.mkdir()
    sources = root / 'sources'
    sources.mkdir()
    rows = []
    for name, raw in files.items():
        target = sources / name
        target.parent.mkdir(parents=True, exist_ok=True)
        rows.append(dict(name=name, sha256=save(target, raw), bytes=len(raw)))
    exported = None
    if export:
        exported = dict(name='business-export.json', sha256=save(sources / 'business-export.json', dict(
            schema_version='agent-candidate-business-export.v1',
            source='ppt-control-test-database' if profile.startswith('ppt.') else
                'video-workbench-test-database' if profile.startswith('video.') else 'scientific-test-database',
            task=task)))
    value = dict(schema_version='agent-candidate-scenario.v1', candidate_scope_only=True, scenario_id=str(uuid4()),
        profile=profile, timeout_seconds=600, task=task, inputs=rows, business_export=exported, parent=parent)
    path = root / 'scenario.json'
    save(path, value)
    return path, sources, value


@pytest.mark.parametrize('stage', ['plan', 'retrieve', 'outline', 'draft'])
def test_staged_research_candidate_requires_exact_context_and_business_export(image_packages, tmp_path, stage):
    from test_research_workflow import context, plan_context, outline_context, draft_context, task
    from scientific_agent.workflow_contracts import canonical_bytes as workflow_bytes
    ctx = {'plan': context, 'retrieve': plan_context, 'outline': outline_context, 'draft': draft_context}[stage]()
    profile = 'research.' + stage + '.v1'
    request = task(stage, ctx)
    path, sources, _ = write_scenario(tmp_path / 'workspace-candidate', profile, request,
        {'context.json': workflow_bytes(ctx)}, export=True)
    policy = host_policy(config(profile))
    loaded = scenario.load_scenario(path, sources, None, policy)
    assert loaded.files == {'context.json': workflow_bytes(ctx)}
    assert loaded.metadata['production_acceptance_created'] is False
    assert loaded.metadata['business_export_sha256']


def test_staged_candidate_without_business_export_or_with_changed_context_is_rejected(image_packages, tmp_path):
    from test_research_workflow import context, task
    from scientific_agent.workflow_contracts import canonical_bytes as workflow_bytes
    ctx = context()
    profile = 'research.plan.v1'
    request = task('plan', ctx)
    path, sources, _ = write_scenario(tmp_path / 'missing-export', profile, request,
        {'context.json': workflow_bytes(ctx)})
    with pytest.raises(ServiceError, match='candidate_business_export_required'):
        scenario.load_scenario(path, sources, None, host_policy(config(profile)))
    ctx['requirements']['question'] = 'Changed context after freezing task'
    path, sources, _ = write_scenario(tmp_path / 'changed-context', profile, request,
        {'context.json': workflow_bytes(ctx)}, export=True)
    with pytest.raises(ServiceError, match='candidate_workflow_context_invalid'):
        scenario.load_scenario(path, sources, None, host_policy(config(profile)))


@pytest.mark.parametrize('kind,format', [('storyboard', 'svg'), ('scene', 'svg'), ('scene', 'html')])
def test_video_scenario_and_manifest_bind_only_canonical_business_input(image_packages, tmp_path, kind, format):
    from test_video_profile import storyboard, scene
    document = storyboard() if kind == 'storyboard' else scene(format)
    raw = canonical_bytes(document)
    profile = 'video.' + kind + '.v1'
    path, sources, _ = write_scenario(tmp_path / 'video-candidate', profile,
        {'input_sha256': hashlib.sha256(raw).hexdigest()}, {'video-input.json': raw}, export=True)
    host = config(profile)
    loaded = scenario.load_scenario(path, sources, None, host_policy(host))
    assert loaded.files == {'video-input.json': raw}
    assert loaded.metadata['production_acceptance_created'] is False
    assert not loaded.checkpoint_files and loaded.checkpoint_ref is None
    image = 'registry.invalid/agent-pi:latest@' + host.profile.image_digest
    manifest = probe.render_probe(host, name='video-offline-candidate', worker_image=image,
        node='offline-node', uid_start=2100000000, bootstrap_secret='offline-bootstrap',
        upstream_secret='offline-upstream', scenario=path, source_dir=sources)
    worker = next(item for item in manifest['items'] if item.get('kind') == 'Pod'
                  and item['metadata']['namespace'] == 'agent-workers')
    assert worker['spec']['containers'][0]['image'] == image
    assert worker['spec']['restartPolicy'] == 'Never'


@pytest.mark.parametrize('change', ['task_sha', 'noncanonical', 'wrong_kind', 'wrong_filename'])
def test_video_scenario_refuses_rebound_or_malformed_input(image_packages, tmp_path, change):
    from test_video_profile import storyboard
    document = storyboard()
    if change == 'wrong_kind':
        document['kind'] = 'scene'
    raw = canonical_bytes(document)
    if change == 'noncanonical':
        raw += b'\n'
    task = {'input_sha256': '0' * 64 if change == 'task_sha' else hashlib.sha256(raw).hexdigest()}
    name = 'other.json' if change == 'wrong_filename' else 'video-input.json'
    path, sources, _ = write_scenario(tmp_path / 'video-invalid', 'video.storyboard.v1', task, {name: raw})
    with pytest.raises(ServiceError):
        scenario.load_scenario(path, sources, None, host_policy(config('video.storyboard.v1')))


def record_stopped_parent(run, artifacts):
    """Synthetic signed result/Pod evidence; never real execution or publication."""
    pod_uid = str(uuid4())
    run.register(pod_uid)
    result = WorkerResult(state='completed', artifacts=[InlineFile.from_bytes(name, raw)
        for name, raw in artifacts.items()])
    proof = sign_receipt(run.envelope, result, run.receipt_key,
        ('pod:' + pod_uid, 'uid:' + str(run.uid), 'uid:' + str(run.uid + 1)), time.time())
    result = result.model_copy(update={'stop_receipt': proof})
    document = dict(result=result.model_dump(mode='json'), observations=dict(
        harness_sha256=probe.harness_sha256(), isolation={'uid': [run.uid + 2] * 3}, offline_fixture_only=True))
    if run.scenario is not None:
        document['observations']['scenario'] = run.scenario.metadata
    run.result(document)
    pod = dict(metadata={'uid': pod_uid}, spec={'restartPolicy': 'Never',
        'containers': [{'name': 'worker', 'image': 'registry.invalid/pi@' + run.envelope.image_digest}]},
        status={'phase': 'Succeeded', 'containerStatuses': [{'name': 'worker', 'restartCount': 0,
            'imageID': 'docker-pullable://registry.invalid/pi@' + run.envelope.image_digest,
            'state': {'terminated': {'exitCode': 0, 'finishedAt': datetime.now(timezone.utc).isoformat()}}}]})
    save(run.directory / 'worker-pod.json', pod)
    documents = {name: json.loads((run.directory / name).read_bytes()) for name in scenario.PARENT_FILES.values()}
    hashes = {key: hashlib.sha256((run.directory / name).read_bytes()).hexdigest() for key, name in scenario.PARENT_FILES.items()}
    return documents, hashes


@pytest.fixture
def stopped_parent(authority):
    run = authority()
    task = run.envelope.task
    raw = b'Offline fixture notes, not real Agent output.\n'
    checkpoint = dict(schema_version=CHECKPOINT_SCHEMA, ppt_master_revision=PPT_MASTER_REVISION,
        project_id=task['project_id'], source_business_job_id=task['business_job_id'],
        source_job_id=run.envelope.job_id, source_attempt_id=run.envelope.attempt_id,
        phase='plan_initial', input_snapshot_sha256=task['input_snapshot_sha256'], project_root='demo',
        files=[dict(path='demo/notes.md', sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))],
        total_bytes=len(raw), publication_authorized=False)
    checkpoint['sha256'] = digest(checkpoint)
    documents, hashes = record_stopped_parent(run, {'project/demo/notes.md': raw,
        'result/checkpoint.json': canonical_bytes(checkpoint)})
    return run, checkpoint, documents, hashes


@pytest.fixture
def research_revision_from_generation(image_packages, tmp_path, request):
    """Use the real revision business constructor over an explicit offline base."""
    from scientific_agent.agent_client import ScientificAgentClient
    from scientific_agent.agent_projection import ScientificAgentProjection
    from scientific_agent.pi_report_versions import ReportVersionStore

    template, files = probe.fixture('research.revise.v1')
    material = json.loads(files['revision-materials.json'])
    generation = {key: template[key] for key in ('schema_version', 'business_task_id', 'topic', 'mode',
        'created_at', 'execution_deadline_at')}
    generation.update(mode='brief', source_policy={'providers': ['crossref'], 'max_results': 1})
    path, sources, _ = write_scenario(tmp_path / 'generation', 'research.generate.v1', generation, {})
    value = config('research.generate.v1')
    prepared = scenario.load_scenario(path, sources, None, host_policy(value))
    run = probe.ProbeAuthority(value, tmp_path / 'generation-authority', 'offline-upstream',
        'offline-bootstrap', 'research-worker', 'agent-workers', 2100000000, scenario=prepared)
    run.proxy.client.close()
    run.proxy.client = httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('No model calls')))
    artifacts = {name: files[name] for name in ('papers.jsonl', 'evidence.jsonl', 'claims.jsonl', 'references.json')}
    artifacts['report.md'] = files['input-report.md']
    if getattr(request, 'param', True) is False:
        del artifacts['report.md']
    _, hashes = record_stopped_parent(run, artifacts)
    database = sqlite3.connect(':memory:')
    database.row_factory = sqlite3.Row
    def execute(statement):
        database.execute(statement)
        database.commit()
    db = SimpleNamespace(_execute=execute, _query=lambda statement: [dict(row) for row in database.execute(statement)])
    def offline_capability(method, url, *_):
        assert method == 'GET' and url == 'http://offline.invalid/v1/capabilities'
        return 200, {}, canonical_bytes({'profiles': [{'profile': 'research.revise.v1',
            'profile_revision': 'v1', 'ready': True}]})
    worker = ScientificAgentClient(db, 'http://offline.invalid', 'offline-' + 'x' * 32,
        transport=offline_capability)
    projection = ScientificAgentProjection(db, worker)
    projection.initialize()
    versions = ReportVersionStore(db)
    versions.initialize()
    projection.versions = worker.versions = versions
    try:
        base = versions.ensure_base('offline-owner', generation['business_task_id'], material['report'], generation['topic'])
        snapshot = versions.save_snapshot('offline-owner', base['version_id'], material['items'],
            {name: base64.b64decode(raw) for name, raw in material['artifacts'].items()}, 'offline-snapshot')
        intent = versions.prepare_intent('offline-owner', base['version_id'], template['base_sha256'],
            template['section_id'], template['section_sha256'], template['instruction'], 'offline-intent',
            snapshot['snapshot_id'], timeout_seconds=600)
        row = projection.prepare_revision('offline-owner', intent['intent_id'])
        task = json.loads(worker._row(row['id'])['task'])
        files['revision-materials.json'] = canonical_bytes(versions.revision_materials('offline-owner', row['id']))
        yield run, task, files, hashes
    finally:
        database.close()
        run.proxy.client.close()
        run.store.close()


def options_task(parent, checkpoint):
    task = deepcopy(parent.envelope.task)
    task.update(schema_version='ppt-plan_options.v1', phase='plan_options', business_job_id=str(uuid4()),
        checkpoint=checkpoint_descriptor(checkpoint), confirmations={'stage1': dict(channel='website_database',
            plan_revision=1, input_snapshot_sha256=task['input_snapshot_sha256'], selection={'confirmed': True},
            outline={}, design={}, confirmed_at=datetime.now(timezone.utc).isoformat(),
            plan_id=str(uuid4()), plan_sha256='e' * 64)})
    return task


def test_initial_scenario_uses_exact_sources_without_creating_approvals(image_packages, tmp_path):
    task, files = probe.fixture('ppt.plan_initial.v1')
    path, sources, value = write_scenario(tmp_path / 'initial', 'ppt.plan_initial.v1', task, files)
    candidate = scenario.load_scenario(path, sources, None, host_policy(config()))
    assert candidate.files == files and candidate.checkpoint_files == () and candidate.checkpoint_ref is None
    assert candidate.metadata['candidate_scope_only'] is True
    assert candidate.metadata['production_acceptance_created'] is False
    assert candidate.metadata['production_artifact_limit'] == 256 * 1024 * 1024
    assert candidate.metadata['scenario_sha256'] == digest(value)
    rendered = probe.render_probe(config(), name='scenario-offline', worker_image='registry.invalid/pi@' + config().profile.image_digest,
        node='reviewed-node', uid_start=2100000010, bootstrap_secret='offline-bootstrap', upstream_secret='offline-upstream',
        scenario=path, source_dir=sources)
    proxy = next(item for item in rendered['items'] if item['kind'] == 'Pod' and item['metadata']['namespace'] == 'agent-service')
    worker = next(item for item in rendered['items'] if item['kind'] == 'Pod' and item['metadata']['namespace'] == 'agent-workers')
    assert proxy['spec']['nodeSelector'] == {'kubernetes.io/hostname': 'reviewed-node'}
    source = next(volume for volume in proxy['spec']['volumes'] if volume['name'] == 'scenario-sources')
    assert source['hostPath'] == {'path': str(sources), 'type': 'Directory'}
    assert next(m for m in proxy['spec']['containers'][0]['volumeMounts'] if m['name'] == 'scenario-sources')['readOnly'] is True
    assert not any('hostPath' in volume for volume in worker['spec']['volumes'])
    for item in rendered['items']:
        if item['kind'] == 'ConfigMap':
            assert 'scenario.json' in item['data'] and 'candidate_scenario.py' in item['data']
            assert not any(raw.decode() in canonical_bytes(item).decode() for raw in files.values())


def test_real_file_checkpoint_scope_is_fixed_before_allocation_and_grant(stopped_parent, tmp_path):
    parent, checkpoint, _, hashes = stopped_parent
    task = options_task(parent, checkpoint)
    files = {file.name: file.content() for file in parent.envelope.inputs}
    path, sources, _ = write_scenario(tmp_path / 'options', 'ppt.plan_options.v1', task, files, parent=hashes, export=True)
    value = config('ppt.plan_options.v1')
    candidate = scenario.load_scenario(path, sources, parent.directory, host_policy(value))
    assert candidate.metadata['parent']['external_all_containers_stopped'] is True
    with pytest.raises(ServiceError, match='candidate_parent_uid_reuse'):
        candidate.reserve_uid(parent.uid + 1)
    authority = probe.ProbeAuthority(value, tmp_path / 'next-authority', 'offline-upstream', 'offline-bootstrap',
        'new-worker', 'agent-workers', parent.uid + 3, scenario=candidate)
    try:
        authority.proxy.client.close()
        authority.proxy.client = httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('No model calls')))
        envelope = authority.envelope
        assert envelope.checkpoint_ref.model_dump(mode='json') == candidate.checkpoint_ref
        assert envelope.checkpoint_inputs == list(candidate.checkpoint_files)
        assert envelope.task['project_id'] == parent.envelope.task['project_id']
        grant = authority.proxy.authorize(authority.grant)
        assert grant['request'] == envelope.request_sha256
        assert authority.store.get_worker_allocation(envelope.attempt_id)['request_sha256'] == envelope.request_sha256
        assert authority.register(str(uuid4()))['envelope']['task'] == task
        changed = envelope.model_copy(update={'checkpoint_inputs': []})
        assert changed.request_sha256 != grant['request']
        saved = json.loads((authority.directory / 'candidate-request.json').read_bytes())
        assert saved['envelope']['checkpoint_inputs']
        assert 'offline-upstream' not in canonical_bytes(saved).decode()
    finally:
        authority.proxy.client.close()
        authority.store.close()


@pytest.mark.parametrize('change', ['running', 'restart', 'exit', 'digest', 'uid', 'content', 'request'])
def test_parent_with_unknown_writer_or_changed_scope_cannot_continue(stopped_parent, change):
    _, _, original, _ = stopped_parent
    documents = deepcopy(original)
    pod = documents['worker-pod.json']
    if change == 'running':
        pod['status']['containerStatuses'][0]['state'] = {'running': {}}
    elif change == 'restart':
        pod['status']['containerStatuses'][0]['restartCount'] = 1
    elif change == 'exit':
        pod['status']['containerStatuses'][0]['state']['terminated']['exitCode'] = 2
    elif change == 'digest':
        pod['status']['containerStatuses'][0]['imageID'] = 'registry.invalid/pi@sha256:' + '0' * 64
    elif change == 'uid':
        pod['metadata']['uid'] = str(uuid4())
    elif change == 'content':
        documents['candidate-result.json']['result']['artifacts'][0]['content_base64'] = 'dGFtcGVyZWQ='
    else:
        documents['candidate-request.json']['envelope']['task']['feedback'] = 'tampered'
    with pytest.raises(ServiceError):
        scenario.verify_parent(documents)


def test_parent_pod_phase_can_lag_completed_never_restart_containers(stopped_parent):
    _, _, original, _ = stopped_parent
    for phase in ('Running', 'Succeeded'):
        documents = deepcopy(original)
        documents['worker-pod.json']['status']['phase'] = phase
        envelope, result = scenario.verify_parent(documents)
        assert result.state == 'completed'
        assert envelope.attempt_id == result.stop_receipt.attempt_id


def test_parent_stop_requires_explicit_never_restart_policy(stopped_parent):
    _, _, original, _ = stopped_parent
    for phase in ('Running', 'Succeeded'):
        for policy in ('Always', 'OnFailure', None):
            documents = deepcopy(original)
            pod = documents['worker-pod.json']
            pod['status']['phase'] = phase
            if policy is None:
                del pod['spec']['restartPolicy']
            else:
                pod['spec']['restartPolicy'] = policy
            with pytest.raises(ServiceError, match='candidate_parent_stop_unverified'):
                scenario.verify_parent(documents)


def test_lagging_running_phase_does_not_waive_container_stop_checks(stopped_parent):
    _, _, original, _ = stopped_parent
    for change in ('running', 'waiting', 'missing', 'extra-running', 'restart', 'exit'):
        documents = deepcopy(original)
        pod = documents['worker-pod.json']
        pod['status']['phase'] = 'Running'
        status = pod['status']['containerStatuses'][0]
        if change in ('running', 'waiting'):
            status['state'] = {change: {}}
        elif change == 'missing':
            pod['status']['containerStatuses'] = []
        elif change == 'extra-running':
            pod['spec']['containers'].append(dict(pod['spec']['containers'][0], name='other'))
            pod['status']['containerStatuses'].append(dict(status, name='other', state={'running': {}}))
        elif change == 'restart':
            status['restartCount'] = 1
        else:
            status['state']['terminated']['exitCode'] = 1
        with pytest.raises(ServiceError, match='candidate_parent_stop_unverified'):
            scenario.verify_parent(documents)


def test_other_pod_phases_cannot_prove_parent_stop(stopped_parent):
    _, _, original, _ = stopped_parent
    for phase in ('Pending', 'Unknown', 'Failed', None):
        documents = deepcopy(original)
        pod = documents['worker-pod.json']
        if phase is None:
            del pod['status']['phase']
        else:
            pod['status']['phase'] = phase
        with pytest.raises(ServiceError, match='candidate_parent_stop_unverified'):
            scenario.verify_parent(documents)


@pytest.mark.parametrize('change', ['extra', 'source', 'hash', 'no-export', 'different-project', 'parent-hash'])
def test_scenario_never_selects_unlisted_sources_or_unrelated_parent(stopped_parent, tmp_path, change):
    parent, checkpoint, _, hashes = stopped_parent
    task = options_task(parent, checkpoint)
    files = {file.name: file.content() for file in parent.envelope.inputs}
    path, sources, value = write_scenario(tmp_path / change, 'ppt.plan_options.v1', task, files, parent=hashes, export=True)
    if change == 'extra':
        (sources / 'unlisted-secret.txt').write_text('unlisted fixture')
    elif change == 'source':
        (sources / next(iter(files))).write_bytes(b'tampered')
    elif change == 'hash':
        value['inputs'][0]['sha256'] = '0' * 64
    elif change == 'no-export':
        value['business_export'] = None
        (sources / 'business-export.json').unlink()
    elif change == 'different-project':
        value['task']['project_id'] = str(uuid4())
    else:
        value['parent']['pod_sha256'] = '0' * 64
    save(path, value)
    with pytest.raises(ServiceError):
        scenario.load_scenario(path, sources, parent.directory, host_policy(config('ppt.plan_options.v1')))


def test_source_hardlink_cannot_be_imported(image_packages, tmp_path):
    task, files = probe.fixture('ppt.plan_initial.v1')
    path, sources, _ = write_scenario(tmp_path / 'hardlink', 'ppt.plan_initial.v1', task, files)
    name = next(iter(files))
    alias = tmp_path / 'external-alias.md'
    os.link(sources / name, alias)
    with pytest.raises(ServiceError):
        scenario.load_scenario(path, sources, None, host_policy(config()))


@pytest.mark.parametrize('operation', ['read', 'members'])
def test_real_nonroot_source_metadata_is_rejected_without_administrator_owner_double(tmp_path, monkeypatch, operation):
    sources = tmp_path / 'untrusted-user-source'
    sources.mkdir()
    (sources / 'source.txt').write_bytes(b'Untrusted nonroot-owned fixture')
    native = type(Path())
    # In the real UID10000 Linux run, this uses the actual nonroot st_uid.
    # Windows/root hosts preserve every real stat field and simulate only UID.
    class NonRootPath(native):
        def lstat(self, *args, **kwargs):
            observed = super().lstat(*args, **kwargs)
            return observed if observed.st_uid != 0 else _OwnerStat(observed, 10000)
    class PosixOwnershipOS:
        name = 'posix'
        def __getattr__(self, name):
            return getattr(os, name)
    monkeypatch.setattr(scenario, 'Path', NonRootPath)
    monkeypatch.setattr(scenario, 'os', PosixOwnershipOS())
    action = (lambda: scenario._read(sources, 'source.txt')) if operation == 'read' else (
        lambda: scenario._source_members(NonRootPath(sources), {'source.txt'}))
    with pytest.raises(ServiceError, match='candidate_source_ownership_invalid'):
        action()


def test_even_empty_unlisted_source_directories_are_not_projected(image_packages, tmp_path):
    task, files = probe.fixture('ppt.plan_initial.v1')
    path, sources, _ = write_scenario(tmp_path / 'extra-directory', 'ppt.plan_initial.v1', task, files)
    (sources / 'unlisted').mkdir()
    with pytest.raises(ServiceError, match='candidate_source_members_mismatch'):
        scenario.load_scenario(path, sources, None, host_policy(config()))


def test_research_generation_uses_explicit_source_policy_with_no_fabricated_revision(image_packages, tmp_path):
    now = time.time()
    task = dict(schema_version='1', business_task_id=str(uuid4()), topic='Offline evidence query fixture',
        mode='brief', created_at=datetime.fromtimestamp(now, timezone.utc).isoformat(),
        execution_deadline_at=datetime.fromtimestamp(now + 600, timezone.utc).isoformat(),
        source_policy={'providers': ['crossref'], 'max_results': 1})
    path, sources, _ = write_scenario(tmp_path / 'research', 'research.generate.v1', task, {})
    value = config('research.generate.v1')
    prepared = scenario.load_scenario(path, sources, None, host_policy(value))
    assert prepared.files == {} and prepared.checkpoint_files == ()
    authority = probe.ProbeAuthority(value, tmp_path / 'research-authority', 'offline-upstream', 'offline-bootstrap',
        'research-worker', 'agent-workers', 2100000000, scenario=prepared)
    try:
        assert authority.envelope.task == task
        # The explicit aware task timestamp is authoritative. Its conversion
        # from a raw float may already have rounded at microsecond precision.
        assert authority.envelope.deadline_at == datetime.fromisoformat(task['execution_deadline_at']).timestamp()
        assert authority.envelope.profile == 'research.generate.v1'
        assert authority.scenario.metadata['production_acceptance_created'] is False
    finally:
        authority.proxy.client.close()
        authority.store.close()


def test_research_revision_accepts_real_business_constructor_new_task(research_revision_from_generation, tmp_path):
    parent, task, files, hashes = research_revision_from_generation
    assert task['business_task_id'] != parent.envelope.task['business_task_id']
    assert task['root_report_id'] == parent.envelope.task['business_task_id']
    path, sources, _ = write_scenario(tmp_path / 'revision', 'research.revise.v1', task, files,
        parent=hashes, export=True)
    prepared = scenario.load_scenario(path, sources, parent.directory, host_policy(config('research.revise.v1')))
    assert prepared.document['task'] == task
    assert prepared.files == files
    assert prepared.metadata['parent']['external_all_containers_stopped'] is True
    assert prepared.metadata['production_acceptance_created'] is False


@pytest.mark.parametrize('research_revision_from_generation', [False], indirect=True)
def test_research_revision_rejects_missing_parent_and_input_report(research_revision_from_generation, tmp_path):
    parent, task, files, hashes = research_revision_from_generation
    del files['input-report.md']
    path, sources, _ = write_scenario(tmp_path / 'revision', 'research.revise.v1', task, files,
        parent=hashes, export=True)
    with pytest.raises(ServiceError, match='candidate_parent_lineage_invalid'):
        scenario.load_scenario(path, sources, parent.directory, host_policy(config('research.revise.v1')))


@pytest.mark.parametrize('change', ['root', 'reused_task', 'base_hash', 'topic', 'report',
    'papers.jsonl', 'evidence.jsonl', 'claims.jsonl', 'references.json'])
def test_research_revision_requires_direct_generation_lineage_and_exact_bytes(
        research_revision_from_generation, tmp_path, change):
    parent, task, files, hashes = research_revision_from_generation
    task, files = deepcopy(task), deepcopy(files)
    if change == 'root':
        task['root_report_id'] = str(uuid4())
    elif change == 'reused_task':
        task['business_task_id'] = parent.envelope.task['business_task_id']
    elif change == 'base_hash':
        task['base_sha256'] = '0' * 64
    elif change == 'topic':
        task['topic'] = 'Unrelated topic'
    elif change == 'report':
        files['input-report.md'] += b'\nUnrelated report bytes.\n'
    else:
        files[change] += b' \n'
    path, sources, _ = write_scenario(tmp_path / 'revision', 'research.revise.v1', task, files,
        parent=hashes, export=True)
    with pytest.raises(ServiceError, match='candidate_parent_lineage_invalid'):
        scenario.load_scenario(path, sources, parent.directory, host_policy(config('research.revise.v1')))


def test_research_revision_parent_requires_unavailable_published_version_mapping(authority, tmp_path):
    parent = authority('research.revise.v1')
    files = {file.name: file.content() for file in parent.envelope.inputs}
    artifacts = {name: files[name] for name in ('papers.jsonl', 'evidence.jsonl', 'claims.jsonl', 'references.json')}
    artifacts['report.md'] = files['input-report.md']
    _, hashes = record_stopped_parent(parent, artifacts)
    task = deepcopy(parent.envelope.task)
    task['business_task_id'] = str(uuid4())
    task['intent_id'], task['parent_version_id'] = str(uuid4()), str(uuid4())
    path, sources, _ = write_scenario(tmp_path / 'revision', 'research.revise.v1', task, files,
        parent=hashes, export=True)
    with pytest.raises(ServiceError, match='candidate_research_revision_parent_unsupported'):
        scenario.load_scenario(path, sources, parent.directory, host_policy(config('research.revise.v1')))
