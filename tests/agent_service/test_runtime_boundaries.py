"""Offline filesystem boundaries; simulated mounts are not Linux acceptance."""
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import httpx
import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service import mounts, tool_client
from agent_service.domain_runtime import DomainExecutionProfile
from agent_service.preview import TrustedPreview
from test_binding_handoff import policy


def fails(code, action):
    with pytest.raises(ServiceError) as failure:
        action()
    assert failure.value.code == code


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    result = workspace / 'result'
    result.mkdir(parents=True)
    target = result / 'academic-observations.json'
    value = dict(schema_version='1', attempt_id='00000000-0000-0000-0000-000000000001',
        request_sha256='a' * 64, observations=[])
    recorded = []
    original_client = httpx.Client

    def install(*, status=200, raw=None):
        def handle(request):
            recorded.append(request)
            return httpx.Response(status, content=json.dumps(value).encode() if raw is None else raw)

        def client(**options):
            assert options['trust_env'] is False and options['follow_redirects'] is False
            # Replace only the transport. No live connection or provider token.
            options['transport'].close()
            options['transport'] = httpx.MockTransport(handle)
            return original_client(**options)

        monkeypatch.setattr(tool_client.httpx, 'Client', client)

    install()
    return SimpleNamespace(workspace=workspace, result=result, target=target,
        value=value, recorded=recorded, install=install,
        save=lambda **options: tool_client.save_academic_observations(
            'https://task-proxy.invalid/v1/', 'offline-attempt-grant', workspace, **options))


def test_bridge_replaces_model_written_bytes_with_measured_canonical_observations(bridge):
    bridge.target.write_text('{"forged":"model-written observations"}')
    bridge.save()
    assert bridge.target.read_bytes() == canonical_bytes(bridge.value)
    assert len(bridge.recorded) == 1
    request = bridge.recorded[0]
    assert request.method == 'GET'
    assert str(request.url) == 'https://task-proxy.invalid/v1/tools/observations'
    assert request.headers['authorization'] == 'Bearer offline-attempt-grant'
    assert bridge.target.stat().st_nlink == 1
    if os.name == 'posix':
        assert stat.S_IMODE(bridge.target.stat().st_mode) == 0o400
        assert bridge.target.stat().st_uid == os.geteuid()


def test_bridge_does_not_replace_hardlinked_agent_file_or_change_its_alias(bridge, tmp_path):
    outside = tmp_path / 'outside.json'
    outside.write_bytes(b'outside bytes')
    try:
        os.link(outside, bridge.target)
    except OSError as exc:
        pytest.skip('Host cannot create hardlinks: ' + str(exc))
    fails('academic_observation_path_invalid', bridge.save)
    assert outside.read_bytes() == b'outside bytes'
    assert bridge.target.stat().st_nlink == 2


def test_bridge_rejects_directory_at_reserved_observation_name(bridge):
    bridge.target.mkdir()
    fails('academic_observation_path_invalid', bridge.save)
    assert bridge.target.is_dir()


@pytest.mark.parametrize('status,raw,options,code', [
    (503, b'private upstream details', {}, 'academic_observation_unavailable'),
    (302, b'', {}, 'academic_observation_unavailable'),
    (200, b'0123456789', {'max_bytes': 8}, 'academic_observation_limit'),
])
def test_failed_observation_fetch_never_replaces_existing_file(bridge, status, raw, options, code):
    bridge.target.write_bytes(b'existing candidate')
    bridge.install(status=status, raw=raw)
    fails(code, lambda: bridge.save(**options))
    assert bridge.target.read_bytes() == b'existing candidate'
    assert len(bridge.recorded) == 1


@pytest.mark.parametrize('where', ['file', 'parent'])
def test_bridge_rejects_symlink_and_preserves_external_target(bridge, tmp_path, where):
    outside = tmp_path / 'external'
    outside.mkdir()
    file = outside / 'academic-observations.json'
    file.write_bytes(b'external protected bytes')
    try:
        if where == 'file':
            bridge.target.symlink_to(file)
        else:
            bridge.result.rmdir()
            bridge.result.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip('Host cannot create symlinks: ' + str(exc))
    fails('academic_observation_path_invalid', bridge.save)
    assert file.read_bytes() == b'external protected bytes'


class FakeLibc:
    """Records Linux mount calls without requesting OS privileges."""
    def __init__(self, results=(), *, unmount_result=0):
        self.results = iter(results)
        self.mounts, self.unmounts = [], []
        self.unmount_result = unmount_result

    def mount(self, *args):
        self.mounts.append(args)
        return next(self.results, 0)

    def umount2(self, *args):
        self.unmounts.append(args)
        return self.unmount_result


@pytest.fixture
def simulated_mount(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    source = tmp_path / 'source'
    child = source / 'inputs'
    child.mkdir(parents=True)
    (child / 'source.bin').write_bytes(b'staged source')
    original_lstat = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == child:
            # Only simulate source ownership/mode; directory layout and source
            # bytes remain real. No process privileges or real mounts change.
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o555, st_uid=0)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'lstat', lstat)
    context = SimpleNamespace(input_root=source, checkpoint_root=None)
    boundary = mounts.ReadOnlyInputs(workspace, context)
    return SimpleNamespace(workspace=workspace, source=source, child=child,
        context=context, boundary=boundary)


def test_unavailable_mount_host_fails_before_creating_writable_input_copy(simulated_mount, monkeypatch):
    staged = simulated_mount
    def unavailable():
        raise ServiceError('read_only_mounts_unavailable', 503)
    monkeypatch.setattr(mounts.ReadOnlyInputs, '_libc', staticmethod(unavailable))
    fails('read_only_mounts_unavailable', staged.boundary.prepare)
    assert list(staged.workspace.iterdir()) == []
    assert (staged.child / 'source.bin').read_bytes() == b'staged source'


@pytest.mark.parametrize('results,expected_unmounts', [([-1], 0), ([0, -1], 1)])
def test_bind_or_remount_failure_never_falls_back_to_copy(simulated_mount, monkeypatch, results, expected_unmounts):
    staged = simulated_mount
    libc = FakeLibc(results)
    monkeypatch.setattr(mounts.ReadOnlyInputs, '_libc', staticmethod(lambda: libc))
    fails('read_only_mount_failed', staged.boundary.prepare)
    assert list((staged.workspace / 'inputs').iterdir()) == []
    assert (staged.child / 'source.bin').read_bytes() == b'staged source'
    assert len(libc.unmounts) == expected_unmounts
    assert all(flags == 0 for target, flags in libc.unmounts)
    assert staged.boundary.targets == []


def test_mount_verification_failure_requires_real_unmount_and_retains_uncertainty(simulated_mount, monkeypatch):
    staged = simulated_mount
    libc = FakeLibc(unmount_result=-1)
    monkeypatch.setattr(mounts.ReadOnlyInputs, '_libc', staticmethod(lambda: libc))
    # Successful syscalls cannot substitute for independent kernel mount checks.
    monkeypatch.setattr(mounts, 'verified_read_only_mount', lambda path: False)
    fails('input_unmount_unverified', staged.boundary.prepare)
    assert staged.boundary.targets == [staged.workspace / 'inputs']
    assert len(libc.unmounts) == 1 and libc.unmounts[0][1] == 0
    assert not (staged.workspace / 'inputs/source.bin').exists()
    libc.unmount_result = 0
    staged.boundary.close()
    assert staged.boundary.targets == []


def test_mountinfo_accepts_same_filesystem_bind_without_ismount_heuristic():
    # Captured mountinfo form: the bind has the same device as its parent, but
    # Linux records a separate exact mountpoint with the required VFS flags.
    document = ('83 31 0:27 / /work rw,relatime - overlay overlay rw\n'
        '95 83 0:27 /run/agent-control/input-sources/id/inputs /work/id/inputs '
        'ro,nosuid,nodev,noexec,relatime - overlay overlay rw\n')
    assert mounts.mountinfo_is_read_only(document, '/work/id/inputs')
    assert not mounts.mountinfo_is_read_only(document, '/work/id/inputs/nested')


@pytest.mark.parametrize('flags', ['rw,nosuid,nodev,noexec', 'ro,nodev,noexec', 'ro,nosuid,noexec', 'ro,nosuid,nodev'])
def test_mountinfo_rejects_missing_required_vfs_flag(flags):
    assert not mounts.mountinfo_is_read_only(
        f'95 83 0:27 /source /work/id/inputs {flags} - overlay overlay rw\n', '/work/id/inputs')


def test_mountinfo_decodes_kernel_escaping_and_rejects_stacked_mounts():
    row = '95 83 0:27 /source /work/id/space\\040name ro,nosuid,nodev,noexec - overlay overlay rw\n'
    assert mounts.mountinfo_is_read_only(row, '/work/id/space name')
    assert not mounts.mountinfo_is_read_only(row + row, '/work/id/space name')
    assert not mounts.mountinfo_is_read_only('malformed\n', '/work/id/space name')


def test_domain_workspace_requires_staging_context_and_rejects_overlapping_contexts(tmp_path):
    runtime = DomainExecutionProfile(policy(), 'offline-bundle')
    wire = dict(schema_version='1', task_id='00000000-0000-0000-0000-000000000001',
        runtime_revision=runtime.policy.runtime_revision, skill_bundle_revision='offline-bundle',
        timeout_seconds=30, execution_deadline_at='2026-10-26T12:00:00+00:00', task={'prompt': 'offline'})
    fails('input_staging_not_ready', lambda: runtime.prepare_workspace(wire, tmp_path))
    context = SimpleNamespace(input_root=None, checkpoint_root=None)
    with runtime.execution_context(context):
        fails('runtime_busy', lambda: runtime.execution_context(context).__enter__())
        runtime.prepare_workspace(wire, tmp_path)
        assert (tmp_path / 'result').is_dir()
        assert not (tmp_path / 'base').exists() and not (tmp_path / 'inputs').exists()
    assert runtime._context is None and runtime._mounts is None


@pytest.fixture
def preview(tmp_path):
    project = tmp_path / 'project/deck'
    (project / 'live_preview').mkdir(parents=True)
    return TrustedPreview(project, tmp_path, tmp_path / 'control', 100001, 100002, 0)


def test_stopped_preview_cleanup_deletes_only_official_lock_files(preview):
    targets = [preview.project / '.live_preview.lock', preview.project / 'live_preview/lock.json']
    for path in targets:
        path.write_bytes(b'ephemeral lock')
    preserved = preview.project / 'live_preview/review.json'
    preserved.write_bytes(b'measured review')
    preview.cleanup_after_stop()
    assert all(not path.exists() for path in targets)
    assert preserved.read_bytes() == b'measured review'
    preview.cleanup_after_stop()


def test_stopped_preview_cleanup_rejects_hardlinked_lock(preview, tmp_path):
    external = tmp_path / 'external.lock'
    external.write_bytes(b'other writer lock')
    try:
        os.link(external, preview.project / '.live_preview.lock')
    except OSError as exc:
        pytest.skip('Host cannot create hardlinks: ' + str(exc))
    fails('preview_cleanup_unverified', preview.cleanup_after_stop)
    assert external.read_bytes() == b'other writer lock'
    assert external.stat().st_nlink == 2


def test_stopped_preview_cleanup_rejects_linked_parent_directory(preview, tmp_path):
    external = tmp_path / 'external-preview'
    external.mkdir()
    (external / 'lock.json').write_bytes(b'other preview lock')
    (preview.project / 'live_preview').rmdir()
    try:
        (preview.project / 'live_preview').symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip('Host cannot create symlinks: ' + str(exc))
    fails('preview_cleanup_unverified', preview.cleanup_after_stop)
    assert (external / 'lock.json').read_bytes() == b'other preview lock'
