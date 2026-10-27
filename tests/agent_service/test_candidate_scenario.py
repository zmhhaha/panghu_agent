"""Offline scenario lineage/scope tests; all receipts and Pod evidence are synthetic.

These unit fixtures establish neither a real business confirmation nor actual
node/engine readiness. Production acceptance and routes are never created.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
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
            schema_version='agent-candidate-business-export.v1', source='ppt-control-test-database', task=task)))
    value = dict(schema_version='agent-candidate-scenario.v1', candidate_scope_only=True, scenario_id=str(uuid4()),
        profile=profile, timeout_seconds=600, task=task, inputs=rows, business_export=exported, parent=parent)
    path = root / 'scenario.json'
    save(path, value)
    return path, sources, value


@pytest.fixture
def stopped_parent(authority):
    run = authority()
    pod_uid = str(uuid4())
    run.register(pod_uid)
    task = run.envelope.task
    raw = b'Offline fixture notes, not real Agent output.\n'
    checkpoint = dict(schema_version=CHECKPOINT_SCHEMA, ppt_master_revision=PPT_MASTER_REVISION,
        project_id=task['project_id'], source_business_job_id=task['business_job_id'],
        source_job_id=run.envelope.job_id, source_attempt_id=run.envelope.attempt_id,
        phase='plan_initial', input_snapshot_sha256=task['input_snapshot_sha256'], project_root='demo',
        files=[dict(path='demo/notes.md', sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))],
        total_bytes=len(raw), publication_authorized=False)
    checkpoint['sha256'] = digest(checkpoint)
    result = WorkerResult(state='completed', artifacts=[InlineFile.from_bytes('project/demo/notes.md', raw),
        InlineFile.from_bytes('result/checkpoint.json', canonical_bytes(checkpoint))])
    proof = sign_receipt(run.envelope, result, run.receipt_key,
        ('pod:' + pod_uid, 'uid:' + str(run.uid), 'uid:' + str(run.uid + 1)), time.time())
    result = result.model_copy(update={'stop_receipt': proof})
    document = dict(result=result.model_dump(mode='json'), observations=dict(
        harness_sha256=probe.harness_sha256(), isolation={'uid': [run.uid + 2] * 3}, offline_fixture_only=True))
    run.result(document)
    pod = dict(metadata={'uid': pod_uid}, spec={'containers': [{'name': 'worker', 'image': 'registry.invalid/pi@' + run.envelope.image_digest}]},
        status={'phase': 'Succeeded', 'containerStatuses': [{'name': 'worker', 'restartCount': 0,
            'imageID': 'docker-pullable://registry.invalid/pi@' + run.envelope.image_digest,
            'state': {'terminated': {'exitCode': 0, 'finishedAt': datetime.now(timezone.utc).isoformat()}}}]})
    save(run.directory / 'worker-pod.json', pod)
    documents = {name: json.loads((run.directory / name).read_bytes()) for name in scenario.PARENT_FILES.values()}
    hashes = {key: hashlib.sha256((run.directory / name).read_bytes()).hexdigest() for key, name in scenario.PARENT_FILES.items()}
    return run, checkpoint, documents, hashes


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
