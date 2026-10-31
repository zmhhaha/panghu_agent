"""Neutral domain prompts and workspace preparation for fixed-image Pi hosts."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import os
from pathlib import Path
import shlex
import stat
import tempfile

from .contracts import ServiceError, canonical_bytes, digest
from .mounts import ReadOnlyInputs
from .profiles import implementation
from .runtime.profiles import CommonExecution, ExecutionProfile, ProfileError


class DomainExecutionProfile(ExecutionProfile):
    production_ready = True  # Constructed only after host acceptance.
    load_skills = True

    def __init__(self, policy, bundle_revision, *, extensions=()):
        self.policy, self.bundle_revision = policy, bundle_revision
        self.impl = implementation(policy.name)
        # Pi 0.99.2 applies --tools to registered extension tools as well as
        # builtins. Select precisely the domain contract's supported tools;
        # merely loading the academic extension does not activate its tools.
        self.allowed_tools = tuple(t for t in self.impl.required_tools
            if t in {'read', 'write', 'bash', 'academic_search', 'academic_verify'})
        # Pi's read tool natively accepts images; no invented `image` tool.
        # Research and video embed their checksum-verified procedures in the
        # prompt. Only PPT uses Pi's separate skill loading; other domains must
        # not acquire an additional CLI-oriented or search workflow.
        self.load_skills = policy.name.startswith('ppt.')
        self.extension_paths = tuple(Path(p) for p in extensions)
        self.protected_paths = ('inputs', 'base', 'context-view', '.preview-home', '.preview-tmp')
        self._context, self._mounts, self._context_view = None, None, None
        self._preview = None
        self._handoff_inputs = self._handoff_workspace = self._handoff_task_digest = None
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
            self.release_inputs()

    def release_inputs(self):
        """Shared by contextual Pi/Hermes release, after proven writer stop."""
        if self._mounts is not None:
            self._mounts.close()
            self._mounts = None
        # Never remove backing files before the kernel confirms that their view
        # was unmounted. A failed release retains references and remains busy.
        if self._context_view is not None:
            self._context_view.cleanup()
            self._context_view = None
        if self._handoff_inputs is not None:
            # Only these host-created entries are removed, never a recursive
            # walk into an Agent-owned workspace. Retain the reference if any
            # cleanup fails, just as for a failed unmount above.
            (self._handoff_inputs / 'task.json').unlink(missing_ok=True)
            self._handoff_inputs.rmdir()
            self._handoff_inputs = self._handoff_workspace = self._handoff_task_digest = None
        self._context = None

    def _export_handoff_task(self, workspace, task):
        """Export the actual task outside the tree later chowned to the Agent.

        Production /work is owned by the controller and mode 0711. A separate
        host-owned sibling makes the read-only file resistant to directory-entry
        replacement by the workspace owner. It is only advisory input: the
        collector never consumes it instead of its original envelope task.
        """
        if self._handoff_inputs is not None:
            raise ServiceError('runtime_busy', 409)
        workspace = Path(workspace).absolute()
        parent = workspace.parent
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400
                or info.st_uid != os.geteuid() or info.st_mode & 0o022
                or not info.st_mode & 0o001
                or self.agent_uid == os.geteuid()):
            raise ServiceError('ppt_handoff_export_unavailable', 503)
        directory = Path(tempfile.mkdtemp(prefix='.ppt-handoff-', dir=parent))
        self._handoff_inputs = directory
        path = directory / 'task.json'
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(canonical_bytes(task))
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o444)
        directory.chmod(0o711)
        self._handoff_workspace, self._handoff_task_digest = workspace, digest(task)

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
        validate_inputs = getattr(self.impl, 'validate_inputs', None)
        if validate_inputs is not None:
            validate_inputs(workspace, wire['task'])
        prepare_context_view = getattr(self.impl, 'prepare_context_view', None)
        if prepare_context_view is not None:
            if self.control_root is None:
                raise ServiceError('input_staging_not_ready', 503)
            self._context_view = prepare_context_view(workspace, wire['task'],
                Path(self.control_root) / 'research-context-views', wire['task_id'])
            self._mounts.mount_context_view(self._context_view)
        # All writable output is fresh; an engine session/HOME is never restored.
        (workspace / 'result').mkdir(mode=0o700)
        if self.policy.name.startswith('ppt.'):
            (workspace / 'project').mkdir(mode=0o700)
            project = self.impl.prepare_project(workspace, wire['task'])
            self._export_handoff_task(workspace, wire['task'])
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
            if (self._handoff_inputs is None or self._handoff_workspace is None
                    or self._handoff_task_digest != digest(wire['task'])):
                raise ServiceError('ppt_handoff_export_unavailable', 503)
            text += ('\nFixed tool environment: /opt/hermes/.venv/bin/python, '
                     '/opt/ppt-master/skills/ppt-master/; use those interpreter/script paths, '
                     'inspect actual --help and never install dependencies or fetch code. '
                     'The separate handoff preflight below is an explicitly supplied host tool and '
                     'uses its own fixed /opt/agent-service/.venv/bin/python. '
                     'Exclude ephemeral live_preview/lock.json and .live_preview.lock from the manifest; '
                     'the host removes its own stopped-preview locks before freezing files.\n')
            command = shlex.join(['/opt/agent-service/.venv/bin/python', '-I', '-B',
                '/opt/agent-service/agent_service/ppt_handoff.py',
                '--workspace', str(self._handoff_workspace),
                '--task-file', str(self._handoff_inputs / 'task.json')])
            text += ('\nBefore ending this execution, finish all project/report writes, write the actual '
                'result/manifest.json, then run this exact image-owned read-only handoff preflight:\n'
                + command + '\n'
                'Use the supplied host-exported task file; never reconstruct, replace or edit it. '
                'The command checks actual files, measured hashes, project_root, literal PPTX role, '
                'report references, the complete roster and the approved specification binding. '
                'If it reports a contract error, correct only the actual authorized project/manifest '
                'and rerun the same command until it returns exit 0. It never writes or repairs files. '
                'Do not fabricate a successful command result. If a real blocker prevents a valid '
                'handoff, provide the truthful needs_input handoff under the same contract. '
                'This check is advisory contract validation only: it does not perform visual review, '
                'prove writer stop, create a checkpoint or candidate, pass business acceptance or '
                'authorize publication. The host independently re-reads and collects after physical '
                'writer stop; all later gates remain required.\n')
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
