"""Offline candidate wiring only. No production dispatch or stop-proof authority."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
from pi_worker.runtime import PiRuntime, RuntimeSettings, RuntimeExecutionError
from ppt_service.control.pi_staging import checked_directory
from .pi_context import PreparedPptContext
from .pi_profile import PptProfile


@dataclass(frozen=True)
class FixtureExecution:
    workspace: Path
    context: PreparedPptContext
    # Runtime completion is not candidate acceptance or a combined StopProof.
    event_types: tuple[str, ...]


def run_prepared_fixture(stager, attempt, layout, settings: RuntimeSettings,
                         workspace: Path, *, checkpoint_manifest=None,
                         timeout_seconds=60, cancel=None):
    """Recheck real staged inputs and run an internally constructed fixture envelope.

    All arguments are trusted controller/test objects, never a user JSON route.
    Endpoint/command selection belongs to the caller's synthetic test harness.
    This function does not establish mounts, UID isolation, preview or publication.
    """
    if type(settings) is not RuntimeSettings or settings.fixture_mode is not True:
        raise RuntimeExecutionError('runtime_not_ready', 'Only explicit offline fixtures are supported by this entry')
    if type(timeout_seconds) is not int or not 60 <= timeout_seconds <= 3600:
        raise RuntimeExecutionError('invalid_ppt_job', 'Invalid bounded fixture timeout')
    context = PreparedPptContext.from_stager(stager, attempt, layout,
                                            checkpoint_manifest=checkpoint_manifest)
    workspace = Path(workspace).absolute()
    # Domain state must never be inside the runtime chown/HOME traversal.
    domain = Path(context.receipt['stage_root'])
    if workspace == domain or workspace in domain.parents or domain in workspace.parents:
        raise RuntimeExecutionError('unsafe_workspace', 'Runtime scratch must be separate from the staged domain tree')
    checked_directory(workspace.parent)
    profile = PptProfile(runtime_revision=context.runtime_revision,
                         skill_bundle_revision=context.skill_bundle_revision,
                         prepared_context=context)
    created = datetime.now(timezone.utc)
    receipt = context.receipt
    wire = {'schema_version': 1, 'domain': 'ppt', 'engine': 'pi', 'profile': 'ppt-master',
            'phase': receipt['phase'], 'job_id': receipt['job_id'],
            'input_snapshot_sha256': receipt['input_snapshot_sha256'],
            'runtime_revision': context.runtime_revision,
            'skill_bundle_revision': context.skill_bundle_revision,
            'timeout_seconds': timeout_seconds, 'created_at': created.isoformat(),
            'execution_deadline_at': (created + timedelta(seconds=timeout_seconds)).isoformat(),
            'task': context.task, 'checkpoint': context.checkpoint}
    # Validate the complete original envelope before the generic runtime strips
    # internal dispatch keys. No caller-supplied attempt/_runtime_uid is accepted.
    profile.validate(wire)
    events = []
    runtime = PiRuntime(settings, profile=profile)
    runtime.execute(wire, workspace, events.append, cancel if cancel is not None else threading.Event())
    return FixtureExecution(workspace, context, tuple(event['type'] for event in events))
