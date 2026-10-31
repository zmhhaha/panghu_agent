"""Read-only handoff diagnostics are advisory, never stopped acceptance."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.deploy.acceptance_probe import fixture
from agent_service.engines import StopEvidence
from agent_service.profiles.ppt import (CHECKPOINT_SCHEMA, PPT_MASTER_REVISION,
    PPTProfile, _manifest_example, checkpoint_descriptor)


def write_file(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def measured(raw):
    return dict(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


@pytest.fixture
def generation(tmp_path):
    """Small, explicitly synthetic files; this exercises structure, not PPT QA."""
    workspace = tmp_path / 'workspace'
    task, _ = fixture('ppt.plan_initial.v1')
    root = 'actual/restored-deck'
    approved = {
        'design_spec.md': b'# Synthetic approved design\nNo real source claim.\n',
        'spec_lock.md': b'# Synthetic approved lock\nNo real acceptance.\n',
        'reports/gates.json': b'{"synthetic":true,"not_a_real_gate":true}\n',
    }
    rows = [dict(path=root + '/' + key, **measured(raw))
            for key, raw in sorted(approved.items())]
    checkpoint = dict(schema_version=CHECKPOINT_SCHEMA,
        ppt_master_revision=PPT_MASTER_REVISION, project_id=task['project_id'],
        source_business_job_id=str(uuid4()), source_job_id=str(uuid4()),
        source_attempt_id=str(uuid4()), phase='prepare',
        input_snapshot_sha256=task['input_snapshot_sha256'], project_root=root,
        files=rows, total_bytes=sum(row['bytes'] for row in rows),
        publication_authorized=False)
    checkpoint['sha256'] = digest(checkpoint)
    first = dict(channel='website_database', plan_revision=1,
        input_snapshot_sha256=task['input_snapshot_sha256'], selection={'confirmed': True},
        outline={}, design={}, confirmed_at='2026-10-31T00:00:00Z',
        plan_id=str(uuid4()), plan_sha256='1' * 64)
    second = deepcopy(first)
    selected = {'id': 'A', 'name': 'Explicitly synthetic direction'}
    second.update(plan_revision=2, plan_id=str(uuid4()), plan_sha256='2' * 64,
        selection={'direction_id': 'A'}, selected_direction=selected,
        outline={'pages': [dict(ordinal=n, title=f'Synthetic {n}', purpose='Fixture only')
                           for n in range(1, 4)]},
        design={'directions': [selected], 'production_parameters': {'aspect_ratio': '16:9'}})
    task.update(schema_version='ppt-generate.v1', phase='generate',
        checkpoint=checkpoint_descriptor(checkpoint),
        confirmations={'stage1': first, 'stage2': second})
    profile = PPTProfile('generate')
    profile.validate_task(task)
    manifest = _manifest_example(task)
    # Keep fixture construction independent of the prompt-example root fix.
    manifest['project_root'] = root
    for row in manifest['artifacts']:
        raw = approved.get(row['path'], ('Synthetic structural fixture: ' + row['path'] + '\n').encode())
        write_file(workspace / 'project' / root / row['path'], raw)
        row.update(measured(raw))
    for key, raw in approved.items():
        write_file(workspace / 'base/project' / root / key, raw)
    write_file(workspace / 'base/result/checkpoint.json', canonical_bytes(checkpoint))
    write_file(workspace / 'result/manifest.json', canonical_bytes(manifest))
    task_file = tmp_path / 'host-handoff/task.json'
    write_file(task_file, canonical_bytes(task))
    envelope = SimpleNamespace(task=task, profile=profile.name,
        job_id=str(uuid4()), attempt_id=str(uuid4()))
    return SimpleNamespace(workspace=workspace, task=task, task_file=task_file,
        manifest=manifest, profile=profile, envelope=envelope, root=root)


def write_manifest(case):
    write_file(case.workspace / 'result/manifest.json', canonical_bytes(case.manifest))


def stopped_collect(case):
    return case.profile.collect_output(case.workspace, case.envelope,
        StopEvidence(case.envelope.attempt_id, True, ('synthetic-stopped-writers',)))


def tree_snapshot(root):
    """Catch created/deleted files, content changes and rewrites of same bytes."""
    return {path.relative_to(root).as_posix():
        (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
        for path in root.rglob('*') if path.is_file()}


def test_preflight_is_read_only_and_formal_collection_remains_unpublished(generation):
    case = generation
    before = tree_snapshot(case.workspace.parent)
    advisory = case.profile.preflight_output(case.workspace, case.task)
    assert type(advisory) is dict
    assert advisory['schema'] == 'ppt-handoff-preflight.v1'
    assert advisory['status'] == 'valid'
    assert advisory['scope'] == 'handoff_contract_only'
    assert advisory['advisory_only'] is True and advisory['host_collection_required'] is True
    assert advisory['publication_authorized'] is False
    assert advisory['phase'] == 'generate'
    assert advisory['file_count'] == len(case.manifest['artifacts'])
    assert advisory['manifest_sha256'] == hashlib.sha256(canonical_bytes(case.manifest)).hexdigest()
    assert not {'checkpoint', 'artifacts', 'stop_receipt', 'acceptance'} & set(advisory)
    assert tree_snapshot(case.workspace.parent) == before
    assert not (case.workspace / 'result/checkpoint.json').exists()
    collected = {row.name: row for row in stopped_collect(case)}
    checkpoint = json.loads(collected['result/checkpoint.json'].content())
    assert checkpoint['project_root'] == case.root
    assert checkpoint['publication_authorized'] is False
    assert checkpoint['files'] and checkpoint['total_bytes'] > 0
    # Collector returns measured files; even it does not publish or persist a checkpoint.
    assert tree_snapshot(case.workspace.parent) == before


def test_success_example_uses_actual_checkpoint_root(generation):
    assert _manifest_example(generation.task)['project_root'] == generation.root


@pytest.mark.parametrize('mutation', [
    'project_prefix', 'candidate_pptx', 'missing_artifact', 'wrong_sha256',
    'wrong_bytes', 'unlisted_hidden_file', 'quality_prose', 'quality_missing_file',
    'quality_wrong_sha256', 'missing_slide', 'changed_approved_spec',
])
def test_advisory_and_stopped_collector_reject_same_real_contract_failure(generation, mutation):
    case = generation
    if mutation == 'project_prefix':
        case.manifest['project_root'] = 'project/' + case.root
    elif mutation == 'candidate_pptx':
        next(row for row in case.manifest['artifacts'] if row['role'] == 'pptx')['role'] = 'candidate_pptx'
    elif mutation == 'missing_artifact':
        case.manifest['artifacts'].pop()
    elif mutation == 'wrong_sha256':
        case.manifest['artifacts'][0]['sha256'] = 'f' * 64
    elif mutation == 'wrong_bytes':
        case.manifest['artifacts'][0]['bytes'] += 1
    elif mutation == 'unlisted_hidden_file':
        write_file(case.workspace / 'project' / case.root / '.hidden/not-declared.md', b'Synthetic hidden file\n')
    elif mutation == 'quality_prose':
        case.manifest['quality']['status'] = 'All good, trust this prose'
    elif mutation == 'quality_missing_file':
        case.manifest['quality'] = {'reports': [{'path': 'reports/absent.json'}]}
    elif mutation == 'quality_wrong_sha256':
        case.manifest['quality']['reports'][0]['sha256'] = 'f' * 64
    elif mutation == 'missing_slide':
        case.manifest['slides'].pop()
    elif mutation == 'changed_approved_spec':
        raw = b'# Changed after approval\nExplicitly synthetic.\n'
        write_file(case.workspace / 'project' / case.root / 'design_spec.md', raw)
        next(row for row in case.manifest['artifacts'] if row['path'] == 'design_spec.md').update(measured(raw))
    write_manifest(case)
    before = tree_snapshot(case.workspace.parent)
    with pytest.raises(ServiceError) as advisory:
        case.profile.preflight_output(case.workspace, case.task)
    with pytest.raises(ServiceError) as collector:
        stopped_collect(case)
    assert advisory.value.code == collector.value.code
    if mutation == 'candidate_pptx':
        assert advisory.value.code == 'ppt_candidate_incomplete'
    if mutation == 'changed_approved_spec':
        assert advisory.value.code == 'ppt_approved_spec_changed'
    assert tree_snapshot(case.workspace.parent) == before


def test_preflight_rechecks_restored_checkpoint_binding(generation):
    case = generation
    path = case.workspace / 'base/result/checkpoint.json'
    checkpoint = json.loads(path.read_bytes())
    checkpoint['source_attempt_id'] = str(uuid4())
    checkpoint['sha256'] = digest({key: value for key, value in checkpoint.items() if key != 'sha256'})
    path.write_bytes(canonical_bytes(checkpoint))
    with pytest.raises(ServiceError) as advisory:
        case.profile.preflight_output(case.workspace, case.task)
    with pytest.raises(ServiceError) as collector:
        stopped_collect(case)
    assert advisory.value.code == collector.value.code == 'ppt_checkpoint_binding_mismatch'


def test_only_advisory_ignores_two_active_host_preview_locks(generation):
    case = generation
    locks = [case.workspace / 'project' / case.root / key
             for key in ('live_preview/lock.json', '.live_preview.lock')]
    for path in locks:
        write_file(path, b'{"synthetic_active_host_lock":true}\n')
    before = tree_snapshot(case.workspace.parent)
    case.profile.preflight_output(case.workspace, case.task)
    # Host collection still requires its own stopped-preview cleanup.
    with pytest.raises(ServiceError):
        stopped_collect(case)
    assert tree_snapshot(case.workspace.parent) == before
    for path in locks:
        path.unlink()
    assert stopped_collect(case)


@pytest.mark.parametrize('key', ['live_preview/other.json', 'nested/live_preview/lock.json',
                               'nested/.live_preview.lock'])
def test_preflight_has_no_arbitrary_preview_directory_exclusion(generation, key):
    case = generation
    write_file(case.workspace / 'project' / case.root / key, b'Synthetic undeclared file\n')
    with pytest.raises(ServiceError):
        case.profile.preflight_output(case.workspace, case.task)


def test_manifest_cannot_declare_an_excluded_active_lock(generation):
    case = generation
    raw = b'{"synthetic_active_host_lock":true}\n'
    write_file(case.workspace / 'project' / case.root / 'live_preview/lock.json', raw)
    case.manifest['artifacts'].append(dict(path='live_preview/lock.json', role='preview_lock',
        media_type='application/json', **measured(raw)))
    write_manifest(case)
    with pytest.raises(ServiceError):
        case.profile.preflight_output(case.workspace, case.task)


@pytest.mark.parametrize('proof_kind', ['none', 'duck', 'not_stopped', 'wrong_execution'])
def test_preflight_success_does_not_supply_or_bypass_stop_evidence(generation, proof_kind):
    case = generation
    case.profile.preflight_output(case.workspace, case.task)
    proofs = {
        'none': None,
        'duck': SimpleNamespace(execution_id=case.envelope.attempt_id,
            all_writers_stopped=True, resource_ids=('pretend',)),
        'not_stopped': StopEvidence(case.envelope.attempt_id, False, ('synthetic',)),
        'wrong_execution': StopEvidence(str(uuid4()), True, ('synthetic',)),
    }
    # Stop validation must precede even parsing a now-invalid manifest.
    (case.workspace / 'result/manifest.json').write_bytes(b'not JSON')
    with pytest.raises(ServiceError) as caught:
        case.profile.collect_output(case.workspace, case.envelope, proofs[proof_kind])
    assert caught.value.code == 'writers_not_stopped'


@pytest.mark.parametrize('valid', [True, False])
def test_cli_prints_safe_diagnostic_without_writes_stop_evidence_or_collection(
        generation, capsys, monkeypatch, valid):
    from agent_service import engines, ppt_handoff
    case = generation
    if not valid:
        case.manifest['quality']['status'] = 'PRIVATE_SOURCE_SENTINEL must never be echoed'
        write_manifest(case)
    before = tree_snapshot(case.workspace.parent)
    def prohibited(*args, **kwargs):
        pytest.fail('Advisory CLI must not create stop evidence or call stopped collection')
    monkeypatch.setattr(PPTProfile, 'collect_output', prohibited)
    monkeypatch.setattr(engines, 'StopEvidence', prohibited)
    result = ppt_handoff.main(['--workspace', str(case.workspace), '--task-file', str(case.task_file)])
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.out)
    assert result == (0 if valid else 1)
    assert diagnostic['publication_authorized'] is False
    assert diagnostic['advisory_only'] is True
    assert diagnostic['host_collection_required'] is True
    assert diagnostic['scope'] == 'handoff_contract_only'
    assert diagnostic['status'] == ('valid' if valid else 'invalid')
    if not valid:
        assert diagnostic['code'] == 'invalid_ppt_quality_refs'
    assert 'PRIVATE_SOURCE_SENTINEL' not in captured.out + captured.err
    assert 'Traceback' not in captured.out + captured.err
    assert tree_snapshot(case.workspace.parent) == before
    assert not (case.workspace / 'result/checkpoint.json').exists()


def test_cli_invalid_task_is_a_safe_failure_without_echoing_input(generation, capsys):
    from agent_service import ppt_handoff
    case = generation
    case.task_file.write_bytes(b'{"phase":"PRIVATE_TASK_SENTINEL", "broken":')
    before = tree_snapshot(case.workspace.parent)
    result = ppt_handoff.main(['--workspace', str(case.workspace), '--task-file', str(case.task_file)])
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.out)
    assert result == 1 and diagnostic['status'] == 'invalid'
    assert diagnostic['advisory_only'] is True
    assert diagnostic['publication_authorized'] is False
    assert type(diagnostic['code']) is str
    assert 'PRIVATE_TASK_SENTINEL' not in captured.out + captured.err
    assert 'Traceback' not in captured.out + captured.err
    assert tree_snapshot(case.workspace.parent) == before


@pytest.mark.skipif(sys.platform != 'linux', reason='Fixed Worker CLI runs on Linux only')
def test_isolated_direct_cli_entry_ignores_cwd_and_pythonpath_package_shadow(generation):
    from agent_service import ppt_handoff
    case = generation
    attacker = case.workspace.parent / 'attacker-cwd'
    marker = attacker / 'SHADOW_IMPORTED'
    shadow = ('from pathlib import Path\n'
              f'Path({str(marker)!r}).write_text("shadow imported", encoding="utf-8")\n'
              'raise RuntimeError("ATTACKER_PACKAGE_SENTINEL")\n')
    write_file(attacker / 'agent_service/__init__.py', shadow.encode())
    script = Path(ppt_handoff.__file__).resolve()
    before = tree_snapshot(case.workspace.parent)
    completed = subprocess.run([sys.executable, '-I', '-B', str(script),
        '--workspace', str(case.workspace), '--task-file', str(case.task_file)],
        cwd=attacker, env={**os.environ, 'PYTHONPATH': str(attacker)},
        capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
    diagnostic = json.loads(completed.stdout)
    assert diagnostic['code'] == 'ppt_handoff_valid'
    assert diagnostic['status'] == 'valid'
    assert diagnostic['scope'] == 'handoff_contract_only'
    assert diagnostic['advisory_only'] is True
    assert diagnostic['host_collection_required'] is True
    assert diagnostic['publication_authorized'] is False
    assert diagnostic['manifest_sha256'] == hashlib.sha256(canonical_bytes(case.manifest)).hexdigest()
    assert not marker.exists()
    assert 'ATTACKER_PACKAGE_SENTINEL' not in completed.stdout + completed.stderr
    assert tree_snapshot(case.workspace.parent) == before


@pytest.mark.skipif(sys.platform != 'linux', reason='Actual directory DAC check requires Linux')
def test_low_permission_cli_rejects_a_real_unreadable_project_subtree(generation):
    from agent_service import ppt_handoff
    case = generation
    module_root = Path(ppt_handoff.__file__).resolve().parent.parent
    # Import image-owned modules while the interpreter can still access the
    # checkout/venv, then execute every filesystem check without root privilege.
    bootstrap = '''import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agent_service import ppt_handoff
if os.geteuid() == 0:
    try:
        os.setgroups([])
        os.setgid(100000)
        os.setuid(100000)
    except PermissionError:
        print("UID_SWITCH_UNAVAILABLE")
        raise SystemExit(77)
assert os.geteuid() != 0, "Filesystem validation must run without root privilege"
if sys.argv[4]:
    try:
        list(os.scandir(sys.argv[4]))
    except PermissionError:
        pass
    else:
        raise AssertionError("The actual denied directory must reject scanning")
raise SystemExit(ppt_handoff.main(["--workspace", sys.argv[2], "--task-file", sys.argv[3]]))
'''
    with tempfile.TemporaryDirectory(prefix='ppt-handoff-low-permission-', dir='/tmp') as temporary:
        parent = Path(temporary)
        parent.chmod(0o711)
        workspace = parent / 'workspace'
        shutil.copytree(case.workspace, workspace)
        task_file = parent / 'host-handoff/task.json'
        write_file(task_file, case.task_file.read_bytes())
        for path in parent.rglob('*'):
            path.chmod(0o711 if path.is_dir() else 0o444)
        def run_cli(denied=''):
            result = subprocess.run([sys.executable, '-I', '-B', '-c', bootstrap,
                str(module_root), str(workspace), str(task_file), str(denied)],
                cwd='/tmp', capture_output=True, text=True, timeout=30, check=False)
            if result.returncode == 77 and result.stdout.strip() == 'UID_SWITCH_UNAVAILABLE':
                pytest.skip('Controller lacks SETUID/SETGID capability for low-permission filesystem check')
            return result
        before = tree_snapshot(parent)
        baseline = run_cli()
        assert baseline.returncode == 0, baseline.stderr
        assert json.loads(baseline.stdout)['code'] == 'ppt_handoff_valid'
        assert tree_snapshot(parent) == before
        denied = workspace / 'project' / case.root / 'PRIVATE_PATH_SENTINEL'
        write_file(denied / 'unlisted-source.md', b'PRIVATE_SOURCE_SENTINEL must never be echoed\n')
        (denied / 'unlisted-source.md').chmod(0o444)
        before = tree_snapshot(parent)
        denied.chmod(0o000)
        try:
            failure = run_cli(denied)
            assert failure.returncode == 1, failure.stderr
            diagnostic = json.loads(failure.stdout)
            assert diagnostic['status'] == 'invalid'
            assert diagnostic['code'] == 'ppt_artifact_unreadable'
            assert diagnostic['hint'] == (
                'Every project directory must be readable and traversable so the complete actual '
                'file tree can be measured; a failed scan cannot be omitted.')
            assert diagnostic['advisory_only'] is True
            assert diagnostic['publication_authorized'] is False
            for private in ('PRIVATE_PATH_SENTINEL', 'PRIVATE_SOURCE_SENTINEL', str(parent)):
                assert private not in failure.stdout + failure.stderr
            assert 'Traceback' not in failure.stdout + failure.stderr
        finally:
            denied.chmod(0o711)
        assert tree_snapshot(parent) == before


@pytest.mark.parametrize('code', ['ppt_artifact_unreadable', 'ppt_handoff_export_unavailable'])
def test_operator_diagnostics_preserve_fixed_handoff_codes_without_private_context(code, caplog, tmp_path):
    from agent_service.diagnostics import log_execution_failure
    failure = ServiceError(code, 422)
    failure.args = ('PRIVATE_INPUT_SENTINEL', str(tmp_path / 'PRIVATE_PATH_SENTINEL'))
    with caplog.at_level('WARNING', logger='agent_service.diagnostics'):
        log_execution_failure('worker_host', 'collect_output', failure)
    records = [record for record in caplog.records if record.name == 'agent_service.diagnostics']
    assert len(records) == 1
    assert records[0].exc_info is None and not records[0].stack_info
    assert json.loads(records[0].getMessage()) == dict(event='execution_failure',
        component='worker_host', stage='collect_output', category='service_error', code=code, errno=None)
    assert 'PRIVATE_INPUT_SENTINEL' not in caplog.text
    assert 'PRIVATE_PATH_SENTINEL' not in caplog.text
    assert str(tmp_path) not in caplog.text
