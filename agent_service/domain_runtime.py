"""Neutral domain prompts and workspace preparation for fixed-image Pi hosts."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path

from .contracts import ServiceError
from .mounts import ReadOnlyInputs
from .profiles import implementation
from .runtime.profiles import CommonExecution, ExecutionProfile, ProfileError


class DomainExecutionProfile(ExecutionProfile):
    production_ready = True  # Constructed only after host acceptance.
    load_skills = True

    def __init__(self, policy, bundle_revision, *, extensions=()):
        self.policy, self.bundle_revision = policy, bundle_revision
        self.impl = implementation(policy.name)
        self.allowed_tools = tuple(t for t in self.impl.required_tools if t in {'read', 'write', 'bash'})
        # Pi's read tool natively accepts images; no invented `image` tool.
        # Research embeds its checksum-verified, tool-rendered procedure in the
        # prompt. Loading the raw CLI-oriented skill again would contradict it,
        # and a revision must not acquire a new search workflow.
        self.load_skills = not policy.name.startswith('research.')
        self.extension_paths = tuple(Path(p) for p in extensions)
        self.protected_paths = ('inputs', 'base', '.preview-home', '.preview-tmp')
        self._context, self._mounts = None, None
        self._preview = None
        self.shared_project = policy.name.startswith('ppt.')
        self.agent_uid = self.preview_uid = self.control_root = None

    @contextmanager
    def execution_context(self, context):
        if self._context is not None:
            raise ServiceError('runtime_busy', 409)
        self._context = context
        try:
            yield
        finally:
            try:
                if self._mounts is not None:
                    self._mounts.close()
            finally:
                self._context, self._mounts = None, None

    def make_wire(self, request):
        if request.profile != self.policy.name:
            raise ProfileError('invalid_native_request', 'Wrong domain contract')
        self.impl.validate_task(request.payload)
        return dict(schema_version='1', task_id=request.execution_id,
            runtime_revision=self.policy.runtime_revision, skill_bundle_revision=self.bundle_revision,
            timeout_seconds=request.timeout_seconds, execution_deadline_at=request.deadline_at.isoformat(),
            task=request.payload)

    def validate(self, wire):
        if (type(wire) is not dict or set(wire) != {'schema_version', 'task_id', 'runtime_revision',
                'skill_bundle_revision', 'timeout_seconds', 'execution_deadline_at', 'task'}
                or wire['schema_version'] != '1' or wire['runtime_revision'] != self.policy.runtime_revision
                or wire['skill_bundle_revision'] != self.bundle_revision
                or type(wire['timeout_seconds']) is not int or not 1 <= wire['timeout_seconds'] <= self.policy.max_timeout_seconds):
            raise ProfileError('invalid_native_request', 'Invalid fixed domain request')
        self.impl.validate_task(wire['task'])
        try:
            deadline = datetime.fromisoformat(wire['execution_deadline_at'].replace('Z', '+00:00'))
            if deadline.tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ProfileError('invalid_native_request', 'Aware deadline required') from None
        return CommonExecution(wire['task_id'], self.policy.runtime_revision, self.bundle_revision,
            wire['timeout_seconds'], wire['execution_deadline_at'])

    def prepare_workspace(self, wire, workspace):
        self.validate(wire)
        if self._context is None:
            raise ServiceError('input_staging_not_ready', 503)
        self._mounts = ReadOnlyInputs(workspace, self._context)
        self._mounts.prepare()
        # All writable output is fresh; an engine session/HOME is never restored.
        (workspace / 'result').mkdir(mode=0o700)
        if self.policy.name.startswith('ppt.'):
            (workspace / 'project').mkdir(mode=0o700)
            project = self.impl.prepare_project(workspace, wire['task'])
            if self.policy.name in {'ppt.generate.v1', 'ppt.revise.v1'}:
                if project is None:
                    raise ServiceError('ppt_prepared_checkpoint_required', 422)
                from .preview import TrustedPreview
                self._preview = TrustedPreview(project, workspace, self.control_root,
                    self.agent_uid, self.preview_uid,
                    datetime.fromisoformat(wire['execution_deadline_at'].replace('Z', '+00:00')).timestamp())
                self._preview.start()

    def build_prompt(self, wire):
        self.validate(wire)
        text = self.impl.build_prompt(wire['task'])
        if self.policy.name.startswith('ppt.'):
            text += ('\nFixed tool environment: /opt/hermes/.venv/bin/python, '
                     '/opt/ppt-master/skills/ppt-master/; use those interpreter/script paths, '
                     'inspect actual --help and never install dependencies or fetch code. '
                     'Exclude ephemeral live_preview/lock.json and .live_preview.lock from the manifest; '
                     'the host removes its own stopped-preview locks before freezing files.\n')
        if self._preview is not None:
            text += self._preview.instructions()
        return text

    def after_stopped(self, workspace):
        if self._preview is not None:
            self._preview.cleanup_after_stop()

    def execution_provenance(self, started, finished):
        return dict(engine=self.policy.engine, profile=self.policy.name,
            execution_started_at=started, execution_finished_at=finished,
            execution_interval_basis='execution_envelope')
