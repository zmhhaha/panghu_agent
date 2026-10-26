"""Offline composition of one Pi attempt: stage, execute, stop, freeze.

Every step is an already-verified component; this module only fixes the order
and the fail-closed boundaries between them. It is not a dispatch route.
"""
from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from .execution import Attempt, ControlError, Layout, RuntimeConfig, StopProof
from .pi_collect import FrozenCandidate, freeze_candidate
from .preview_launch import launch_preview, _safe_tree, _safe_directory_chain, _mutate, _trusted_parent
from .stop_gate import combined_stop_proof, stop_uids
from ..worker.pi_runner import FixtureExecution, run_prepared_fixture

PREVIEW_MARKERS = ('design_spec.md', 'spec_lock.md')


@dataclass(frozen=True)
class AttemptOutcome:
    frozen: FrozenCandidate
    proof: StopProof
    layout: Layout
    execution: FixtureExecution
    preview_uid: int | None = None


def _require_offline_linux():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ControlError('attempt_pipeline_requires_linux_root')


def _marked_project(project):
    """Select the single direct child that carries the official preview markers."""
    _safe_tree(Path(project))
    roots = []
    for child in sorted(Path(project).iterdir()):
        if child.is_dir() and all((child / marker).is_file() for marker in PREVIEW_MARKERS):
            roots.append(child)
    if len(roots) != 1:
        raise ControlError('preview_project_missing')
    return roots[0]


def _open_chain(config, layout):
    """Let the preview identity traverse from the data root down to the attempt."""
    root, data_root = Path(layout.root), Path(config.data_root)
    if (not root.is_absolute() or not data_root.is_absolute()
            or '..' in root.parts or '..' in data_root.parts
            or root == data_root or data_root not in root.parents):
        raise ControlError('unsafe_attempt_root')
    _safe_directory_chain(root)
    chain = [root]
    for parent in root.parents:
        if parent == data_root:
            break
        chain.append(parent)
    for path in chain:
        _mutate(path, mode=0o711)


def _handoff(config, layout, project, uid):
    """Give one stopped-time writer temporary ownership of the project tree.

    Only used while no other identity is writing, so no shared group is needed.
    The Pi writer must already be proven stopped before this runs.
    """
    rows = _safe_tree(Path(layout.project))
    if (Path(layout.project) not in Path(project).parents
            or Path(layout.root) not in Path(layout.project).parents):
        raise ControlError('unsafe_preview_project')
    _open_chain(config, layout)
    _mutate(Path(layout.project), mode=0o711)
    selected = [(path, info) for path, info in rows
                if path == project or project in path.parents]
    for path, info in selected:
        _mutate(path, mode=0o755 if stat.S_ISDIR(info.st_mode) else 0o640)
    for path, info in selected:
        _mutate(path, uid=uid)


def _private_rows(audit):
    _trusted_parent(Path(audit))
    rows = []
    for name in ('preview-home', 'preview-tmp'):
        path = Path(audit) / name
        if path.exists() or path.is_symlink():
            rows.extend(_safe_tree(path))
    return rows


def _reclaim_private(audit):
    for path, info in _private_rows(audit):
        _mutate(path, uid=0, mode=0o700 if stat.S_ISDIR(info.st_mode) else 0o600)


def _preview_locks(project):
    rows = dict(_safe_tree(Path(project)))
    locks = (Path(project) / 'live_preview' / 'lock.json', Path(project) / '.live_preview.lock')
    for path in locks:
        if path in rows and not stat.S_ISREG(rows[path].st_mode):
            raise ControlError('unsafe_preview_project')
    return [path for path in locks if path in rows]


def _clear_preview_state(project):
    """Retain preview evidence; unlink only validated exact transient locks."""
    locks = _preview_locks(project)
    for path in locks:
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.unlink(path.name, dir_fd=descriptor)
        finally:
            os.close(descriptor)


def _reclaim(project):
    for path, info in _safe_tree(Path(project)):
        _mutate(path, uid=0, mode=0o755 if stat.S_ISDIR(info.st_mode) else 0o644)


def _stop_all(writers):
    try:
        stop_uids(tuple(writers))
    except Exception as exc:
        raise ControlError('writers_not_stopped') from exc


def run_offline_attempt(config, stager, attempt, runtime_settings, scratch, *, allocator,
                        parsed=None, checkpoint_manifest=None, extra_writers=(), timeout=60,
                        candidate_id=None, preview=None, sandbox=None):
    """Run one fixture attempt end to end and return a frozen candidate.

    `parsed` is the trusted parser result for this snapshot. `extra_writers`
    must list every additional dedicated UID the attempt may have started; a
    preview started here is added automatically. Nothing is frozen unless every
    writer is proven stopped, and completion still is not business acceptance
    or publication authorization.

    Preview runs after the Pi writer is proven stopped, so the two identities
    never write the same tree concurrently; concurrent preview is not claimed.
    """
    _require_offline_linux()
    if type(extra_writers) is not tuple:
        try:
            extra_writers = tuple(extra_writers)
        except TypeError:
            raise ControlError('invalid_writer_ids') from None
    for uid in extra_writers:
        if type(uid) is not int or not 10000 <= uid < 2**31 - 1 or uid == 65534:
            raise ControlError('invalid_writer_ids')
    if len(set(extra_writers)) != len(extra_writers):
        raise ControlError('invalid_writer_ids')
    if type(config) is not RuntimeConfig or type(attempt) is not Attempt:
        raise ControlError('invalid_attempt_request')
    if getattr(runtime_settings, 'fixture_mode', None) is not True:
        raise ControlError('production_admission_not_configured')
    if (parsed is None) == (sandbox is None):
        raise ControlError('invalid_parser_configuration')
    pi_uid = allocator.verify(project_id=attempt.project_id, job_id=attempt.job_id,
                              attempt=attempt.attempt, token=attempt.token, role='pi')
    if pi_uid in extra_writers:
        raise ControlError('invalid_writer_ids')
    layout, inventory = stager.stage(attempt, checkpoint_manifest)
    if sandbox is not None:
        parsed = sandbox.parse(layout.inputs, inventory)
    stager.finish_inputs(attempt, layout, parsed)
    execution = run_prepared_fixture(stager, attempt, layout, runtime_settings, Path(scratch),
                                    checkpoint_manifest=checkpoint_manifest,
                                    timeout_seconds=timeout)
    _stop_all((pi_uid, *extra_writers))
    writers = list(extra_writers)
    preview_uid = None
    if preview is not None:
        project = _marked_project(layout.project)
        preview_uid = allocator.verify(project_id=attempt.project_id, job_id=attempt.job_id,
                                       attempt=attempt.attempt, token=attempt.token, role='preview')
        if preview_uid == pi_uid or preview_uid in writers:
            raise ControlError('invalid_writer_ids')
        _stop_all((pi_uid, *writers, preview_uid))
        _handoff(config, layout, project, preview_uid)
        handle = None
        try:
            handle = launch_preview(attempt, layout, allocator, project, spec=preview,
                                    pi_uid=pi_uid)
        finally:
            if handle is not None:
                try:
                    handle.stop()
                except Exception as exc:
                    raise ControlError('writers_not_stopped') from exc
            _stop_all((pi_uid, *writers, preview_uid))
            _safe_tree(Path(layout.project))
            _private_rows(layout.audit)
            _preview_locks(project)
            _reclaim(layout.project)
            _reclaim_private(layout.audit)
            _clear_preview_state(project)
        writers.append(preview_uid)
    proof = combined_stop_proof(attempt, (pi_uid, *writers))
    frozen = freeze_candidate(config, attempt, layout, proof, candidate_id=candidate_id)
    return AttemptOutcome(frozen, proof, layout, execution, preview_uid)
