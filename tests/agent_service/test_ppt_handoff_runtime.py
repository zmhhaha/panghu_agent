"""Host-exported advisory task boundaries; no model or real input mounts."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.deploy.acceptance_probe import fixture
from agent_service.domain_runtime import DomainExecutionProfile
from agent_service.mounts import ReadOnlyInputs


def domain():
    runtime = DomainExecutionProfile(SimpleNamespace(name='ppt.plan_initial.v1',
        runtime_revision='synthetic-fixed-runtime', max_timeout_seconds=300,
        engine='pi'), 'synthetic-fixed-bundle')
    runtime.agent_uid = os.geteuid() + 100000
    task, inputs = fixture('ppt.plan_initial.v1')
    wire = dict(schema_version='1', task_id=str(uuid4()),
        runtime_revision=runtime.policy.runtime_revision,
        skill_bundle_revision=runtime.bundle_revision, timeout_seconds=30,
        execution_deadline_at='2026-10-31T12:00:00+00:00', task=task)
    return runtime, wire, inputs


def stage_inputs(monkeypatch, inputs):
    def prepare(mounts):
        target = mounts.workspace / 'inputs'
        target.mkdir()
        for name, raw in inputs.items():
            (target / name).write_bytes(raw)
    # These tests validate the export and prompt, not Linux mount acceptance.
    monkeypatch.setattr(ReadOnlyInputs, 'prepare', prepare)


@pytest.fixture
def controller_parent(tmp_path):
    parent = tmp_path / 'controller-parent'
    parent.mkdir(mode=0o711)
    return parent


def test_runtime_exports_real_task_outside_agent_workspace_and_supplies_exact_command(tmp_path, monkeypatch):
    runtime, wire, inputs = domain()
    parent = tmp_path / "controller parent with ' quote"
    parent.mkdir(mode=0o711)
    workspace = parent / "attempt workspace with ' quote"
    workspace.mkdir()
    stage_inputs(monkeypatch, inputs)
    context = SimpleNamespace(input_root=None, checkpoint_root=None)
    with runtime.execution_context(context):
        runtime.prepare_workspace(wire, workspace)
        directory = runtime._handoff_inputs
        task_file = directory / 'task.json'
        assert directory.parent == workspace.parent
        assert not directory.is_relative_to(workspace)
        assert json.loads(task_file.read_bytes()) == wire['task']
        assert task_file.read_bytes() == canonical_bytes(wire['task'])
        assert stat.S_IMODE(directory.stat().st_mode) == 0o711
        assert stat.S_IMODE(task_file.stat().st_mode) == 0o444
        assert directory.stat().st_uid == task_file.stat().st_uid == os.geteuid()
        assert directory not in list(workspace.rglob('*'))
        prompt = runtime.build_prompt(wire)
        marker = 'run this exact image-owned read-only handoff preflight:\n'
        command = prompt.split(marker, 1)[1].split('\n', 1)[0]
        assert shlex.split(command) == ['/opt/agent-service/.venv/bin/python', '-I', '-B',
            '/opt/agent-service/agent_service/ppt_handoff.py',
            '--workspace', str(workspace), '--task-file', str(task_file)]
        assert 'never reconstruct, replace or edit it' in prompt
        assert 'It never writes or repairs files' in prompt
        assert 'all later gates remain required' in prompt
        assert 'advisory contract validation only' in prompt
        assert 'not "candidate_pptx"' in prompt
        assert not (workspace / 'result/checkpoint.json').exists()
    assert not directory.exists()
    assert runtime._handoff_inputs is runtime._handoff_workspace is None
    assert runtime._handoff_task_digest is None
    assert runtime._context is None


def test_prompt_cannot_invent_task_file_or_reuse_export_for_a_different_task(controller_parent):
    runtime, wire, _ = domain()
    with pytest.raises(ServiceError) as missing:
        runtime.build_prompt(wire)
    assert missing.value.code == 'ppt_handoff_export_unavailable'
    workspace = controller_parent / 'workspace'
    workspace.mkdir()
    runtime._export_handoff_task(workspace, wire['task'])
    try:
        changed = deepcopy(wire)
        changed['task']['feedback'] = 'A changed but schema-valid synthetic task.'
        with pytest.raises(ServiceError) as mismatch:
            runtime.build_prompt(changed)
        assert mismatch.value.code == 'ppt_handoff_export_unavailable'
        assert json.loads((runtime._handoff_inputs / 'task.json').read_bytes()) == wire['task']
    finally:
        runtime.release_inputs()


@pytest.mark.parametrize('mode', [0o770, 0o707, 0o777])
def test_export_refuses_a_parent_writable_by_other_identities(tmp_path, mode):
    runtime, wire, _ = domain()
    parent = tmp_path / 'untrusted-parent'
    parent.mkdir(mode=0o700)
    workspace = parent / 'workspace'
    workspace.mkdir()
    parent.chmod(mode)
    try:
        with pytest.raises(ServiceError) as failure:
            runtime._export_handoff_task(workspace, wire['task'])
        assert failure.value.code == 'ppt_handoff_export_unavailable'
        assert list(parent.iterdir()) == [workspace]
        assert runtime._handoff_inputs is None
    finally:
        parent.chmod(0o700)


def test_export_refuses_parent_that_agent_cannot_traverse(tmp_path):
    runtime, wire, _ = domain()
    parent = tmp_path / 'private-controller-parent'
    parent.mkdir(mode=0o700)
    workspace = parent / 'workspace'
    workspace.mkdir()
    with pytest.raises(ServiceError) as failure:
        runtime._export_handoff_task(workspace, wire['task'])
    assert failure.value.code == 'ppt_handoff_export_unavailable'
    assert list(parent.iterdir()) == [workspace]
    assert runtime._handoff_inputs is None


def test_export_refuses_agent_and_controller_sharing_an_identity(controller_parent):
    runtime, wire, _ = domain()
    runtime.agent_uid = os.geteuid()
    workspace = controller_parent / 'workspace'
    workspace.mkdir()
    with pytest.raises(ServiceError) as failure:
        runtime._export_handoff_task(workspace, wire['task'])
    assert failure.value.code == 'ppt_handoff_export_unavailable'
    assert runtime._handoff_inputs is None


def test_failed_unmount_retains_host_task_until_safe_release(controller_parent):
    runtime, wire, _ = domain()
    workspace = controller_parent / 'workspace'
    workspace.mkdir()
    runtime._export_handoff_task(workspace, wire['task'])
    directory = runtime._handoff_inputs
    context = runtime._context = object()
    def fail_close():
        raise ServiceError('input_unmount_unverified', 503)
    runtime._mounts = SimpleNamespace(close=fail_close)
    with pytest.raises(ServiceError):
        runtime.release_inputs()
    assert runtime._context is context
    assert runtime._handoff_inputs == directory
    assert (directory / 'task.json').read_bytes() == canonical_bytes(wire['task'])
    runtime._mounts = SimpleNamespace(close=lambda: None)
    runtime.release_inputs()
    assert not directory.exists()


def test_helper_cleanup_never_recurses_into_unexpected_entries(controller_parent):
    runtime, wire, _ = domain()
    workspace = controller_parent / 'workspace'
    workspace.mkdir()
    runtime._export_handoff_task(workspace, wire['task'])
    directory = runtime._handoff_inputs
    extra = directory / 'unexpected-host-file.md'
    extra.write_bytes(b'Preserve an unexpected entry rather than recursively deleting.\n')
    try:
        with pytest.raises(OSError):
            runtime.release_inputs()
        assert runtime._handoff_inputs == directory
        assert extra.read_bytes().startswith(b'Preserve an unexpected entry')
        assert not (directory / 'task.json').exists()
    finally:
        extra.unlink()
        runtime.release_inputs()


@pytest.mark.skipif(sys.platform != 'linux' or not hasattr(os, 'geteuid') or os.geteuid() != 0,
                    reason='Actual distinct-UID DAC check requires a Linux root controller')
def test_exported_snapshot_is_readable_but_not_writable_by_actual_agent_uid():
    runtime, wire, _ = domain()
    # pytest's private parent is normally mode 0700; use an owned /tmp child
    # to exercise the same ancestor traversal as the production /work mount.
    with tempfile.TemporaryDirectory(prefix='ppt-handoff-distinct-uid-', dir='/tmp') as temporary:
        parent = Path(temporary)
        parent.chmod(0o711)
        workspace = parent / 'workspace'
        workspace.mkdir()
        runtime._export_handoff_task(workspace, wire['task'])
        try:
            directory = runtime._handoff_inputs
            task_file = directory / 'task.json'
            expected = canonical_bytes(wire['task'])
            before = (task_file.read_bytes(), task_file.stat().st_mtime_ns,
                      task_file.stat().st_mode, directory.stat().st_mode)
            def as_agent(command):
                try:
                    return subprocess.run(command, user=runtime.agent_uid,
                        group=runtime.agent_uid, extra_groups=(),
                        capture_output=True, timeout=10, check=False)
                except PermissionError:
                    pytest.skip('Controller lacks SETUID/SETGID capability for the actual UID DAC check')
            read = as_agent(['/bin/cat', str(task_file)])
            assert read.returncode == 0, read.stderr
            assert read.stdout == expected
            # Ordinary DAC denies writing the file and both directory entries;
            # an Agent cannot replace a read-only snapshot through its parent.
            for target in (task_file, directory, parent):
                access = as_agent(['/usr/bin/test', '-w', str(target)])
                assert access.returncode == 1, access.stderr
            assert (task_file.read_bytes(), task_file.stat().st_mtime_ns,
                    task_file.stat().st_mode, directory.stat().st_mode) == before
            assert list(directory.iterdir()) == [task_file]
        finally:
            runtime.release_inputs()
