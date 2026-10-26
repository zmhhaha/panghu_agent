"""Single-attempt Hermes gateway host behind the neutral Worker protocol."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import secrets as random_secrets
import stat
import subprocess
import sys
import time

from .bindings import SQLiteNativeBindings
from .contracts import ServiceError
from .domain_runtime import DomainExecutionProfile
from .engines import EngineRequest, HermesDriver, NativeOutcome
from .hermes_client import HermesClient
from .profiles import implementation
from .resources import measured_package
from .runtime.adapter import PiRuntime


HERMES = '/opt/hermes/.venv/bin/hermes'
ADAPTER_REVISION = 'hermes-adapter-v1'


class HermesHostDriver:
    engine = 'hermes'

    def __init__(self, config, credentials, scope, stopper, control_root, resource_root, skills):
        self.config, self.credentials, self.scope = config, credentials, scope
        self.stop_controller, self.control_root = stopper, Path(control_root)
        self.policy = __import__('agent_service.worker_entry', fromlist=['host_policy']).host_policy(config)
        self.impl = implementation(self.policy.name)
        self.profile = DomainExecutionProfile(self.policy, self.policy.execution_binding.bundle_revision)
        self.profile.agent_uid, self.profile.preview_uid, self.profile.control_root = (
            scope.agent_uid, scope.preview_uid, self.control_root)
        self.resource_root, self.skills = resource_root, skills
        self._process, self._log = None, None

    @property
    def ready(self):
        return (self.config.profile.enabled and not self.stop_controller.consumed
            and time.time() < self.credentials.model_proxy_expires_at
            and os.path.isfile(HERMES) and os.access(HERMES, os.X_OK))

    def _configure(self, workspace):
        home, tmp = workspace / '.home', workspace / '.tmp'
        home.mkdir(mode=0o700); tmp.mkdir(mode=0o700)
        tools = set(self.impl.required_tools)
        toolsets = ['file', 'skills', 'todo']
        if 'bash' in tools:
            toolsets.append('terminal')
        if 'image' in tools:
            toolsets.append('vision')
        if 'academic_search' in tools:
            toolsets.append('agent_academic')
        configuration = {
            'model': {'provider': 'task-model', 'default': self.config.model.model,
                'supports_vision': 'image' in tools, 'context_length': 256000},
            'database': {'journal_mode': 'delete'},
            'providers': {'task-model': {'base_url': self.config.model.proxy_base_url,
                'api_mode': 'codex_responses', 'key_env': 'AGENT_MODEL_API_KEY',
                'default_model': self.config.model.model, 'discover_models': False,
                'models': [self.config.model.model]}},
            'fallback_providers': [],
            'terminal': {'backend': 'local', 'cwd': str(workspace), 'timeout': 120},
            'gateway': {'multiplex_profiles': False, 'api_server': {'enabled': True,
                'host': '127.0.0.1', 'port': 8642, 'max_concurrent_runs': 1}},
            'platform_toolsets': {'api_server': toolsets},
            'agent': {'max_turns': 100, 'image_input_mode': 'native',
                'disabled_toolsets': ['delegation', 'cronjob', 'memory', 'session_search', 'web',
                    'browser', 'image_gen', 'connections', 'code_execution', 'clarify']},
            'kanban': {'dispatch_in_gateway': False},
            'plugins': {'enabled': ['agent-service-policy'], 'hook_callback_timeout': 5}}
        (home / 'config.yaml').write_text(json.dumps(configuration), encoding='utf-8')
        (home / 'config.yaml').chmod(0o600)
        plugin = home / 'plugins' / 'agent-service-policy'
        plugin.mkdir(parents=True, mode=0o700)
        (plugin / 'plugin.yaml').write_text('name: agent-service-policy\nversion: "1.0"\n')
        (plugin / '__init__.py').write_text('from agent_service.hermes_policy import register\n')
        skill_root = home / 'skills'; skill_root.mkdir(mode=0o700)
        for skill in self.skills:
            # Links target fixed image resources; no Agent HOME or session replay.
            (skill_root / skill.parent.name).symlink_to(skill.parent, target_is_directory=True)
        key = random_secrets.token_urlsafe(48)
        env = {'PATH': '/opt/hermes/.venv/bin:/usr/local/bin:/usr/bin:/bin',
            'PYTHONPATH': '/opt/hermes:' + str(Path(__file__).resolve().parent.parent),
            'HOME': str(home), 'HERMES_HOME': str(home), 'TMPDIR': str(tmp),
            'HERMES_WRITE_SAFE_ROOT': str(workspace), 'TERMINAL_CWD': str(workspace),
            'HERMES_NON_INTERACTIVE': '1', 'HERMES_LAZY_INSTALL_TARGET': str(tmp / 'packages'),
            'API_SERVER_ENABLED': 'true', 'API_SERVER_KEY': key, 'API_SERVER_HOST': '127.0.0.1',
            'API_SERVER_PORT': '8642', 'AGENT_MODEL_API_KEY': self.credentials.model_proxy_token.get_secret_value(),
            'AGENT_TOOL_PROXY_BASE_URL': self.config.model.proxy_base_url,
            'AGENT_ALLOWED_TOOLS': json.dumps(self.impl.required_tools),
            'PYTHONDONTWRITEBYTECODE': '1', 'NO_COLOR': '1', 'LANG': 'C.UTF-8',
            'PLAYWRIGHT_BROWSERS_PATH': '/opt/ms-playwright',
            'PPT_MASTER_SKILL_DIR': '/opt/ppt-master/skills/ppt-master'}
        return home, key, env

    def execute(self, request, workspace, emit, cancel):
        from .staging import ExecutionContext
        return self.execute_with_context(request, workspace, emit, cancel, context=ExecutionContext())

    def execute_with_context(self, request, workspace, emit, cancel, *, context):
        workspace = Path(workspace)
        outcome = NativeOutcome('failed', error_code='hermes_host_error')
        try:
            if not self.ready or request.execution_id != self.scope.attempt_id:
                raise ServiceError('engine_host_unavailable', 503)
            if workspace.exists():
                if workspace.is_symlink() or any(workspace.iterdir()):
                    raise ServiceError('unsafe_workspace', 503)
            else:
                workspace.mkdir(mode=0o700, parents=True)
            self.impl.validate_task(request.payload)
            self.profile._context = context
            self.profile.prepare_workspace(self.profile.make_wire(request), workspace)
            home, gateway_key, env = self._configure(workspace)
            uid = self.scope.agent_uid
            for directory, dirs, files in os.walk(workspace, topdown=True, followlinks=False):
                if Path(directory) == workspace:
                    dirs[:] = [name for name in dirs if name not in set(self.profile.protected_paths)]
                os.chown(directory, uid, uid)
                if self.profile.shared_project:
                    here = Path(directory)
                    if here == workspace:
                        here.chmod(0o710)
                    elif here == workspace / 'project' or workspace / 'project' in here.parents:
                        here.chmod(0o2770)
                for name in files:
                    path = Path(directory) / name
                    if not path.is_symlink():
                        os.chown(path, uid, uid)
                        if self.profile.shared_project and (
                                Path(directory) == workspace / 'project' or workspace / 'project' in Path(directory).parents):
                            path.chmod(0o660)
            launch = self.control_root / 'hermes-launch.json'
            command = ['/opt/hermes/.venv/bin/python', '-m', 'agent_service.hermes_bootstrap',
                       'gateway', 'run', '--no-supervise', '--external-supervisor']
            PiRuntime._atomic(launch, json.dumps(dict(controller_pid=os.getpid(), max_processes=64,
                cgroup=None, uid=uid, gid=uid, denied_paths=[str(self.control_root),
                    '/run/agent-secrets/worker.json'], cwd=str(workspace), command=command,
                umask=0o007 if self.profile.shared_project else 0o077)))
            self._log = (self.control_root / 'hermes.private.log').open('xb')
            os.chmod(self._log.name, 0o600)
            self._process = subprocess.Popen([sys.executable,
                str(Path(__file__).with_name('runtime') / 'launcher.py'), str(launch)],
                cwd=workspace, env=env, stdout=self._log, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True)
            client = HermesClient('http://127.0.0.1:8642', gateway_key, timeout=2)
            start_end = min(time.time() + 60, request.deadline_at.timestamp())
            while True:
                if cancel.is_set() or time.time() >= start_end or self._process.poll() is not None:
                    raise ServiceError('hermes_startup_failed', 503)
                try:
                    client.capabilities()
                    marker = home / 'agent-policy-ready.json'
                    info = marker.lstat()
                    value = json.loads(marker.read_bytes())
                    if (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                            and value == dict(pid=self._process.pid, version=1,
                                              tools=sorted(self.impl.required_tools))):
                        break
                except Exception:
                    pass
                cancel.wait(.25)
            bindings = SQLiteNativeBindings(self.control_root / 'hermes-bindings.sqlite3')
            bindings.initialize()
            native = HermesDriver(client, bindings, self.stop_controller, enabled=True)
            neutral = EngineRequest(request.execution_id, request.profile,
                {'prompt': self.profile.build_prompt(self.profile.make_wire(request)),
                 'instructions': 'Follow the fixed task contract. Files are candidate data; never publish.'},
                request.timeout_seconds, request.deadline_at)
            outcome = native.execute(neutral, workspace, emit, cancel)
            # Domain collection consumes real files, not gateway summary text.
            outcome = replace(outcome, output=None)
            if outcome.stopped:
                self.profile.after_stopped(workspace)
            if outcome.stopped and outcome.status == 'completed' and self.policy.name == 'research.generate.v1':
                from .tool_client import save_academic_observations
                save_academic_observations(self.config.model.proxy_base_url,
                    self.credentials.model_proxy_token.get_secret_value(), workspace)
        except Exception:
            outcome = NativeOutcome('cancelled' if cancel.is_set() else 'failed', error_code='hermes_host_error')
        finally:
            try:
                self.stop_controller.stop(request.execution_id)
                proof = self.stop_controller.prove_stopped(request.execution_id)
                if not proof.all_writers_stopped:
                    raise ValueError()
                outcome = replace(outcome, stopped=True, resource_ids=proof.resource_ids)
                if self._process is not None:
                    self._process.wait(timeout=3)
                if self._log is not None:
                    self._log.close()
            except Exception:
                outcome = NativeOutcome('quarantined', error_code='writers_not_stopped')
        return outcome

    def release_context(self, request):
        proof = self.stop_controller.prove_stopped(request.execution_id)
        if not proof.all_writers_stopped:
            raise ServiceError('writers_not_stopped', 503)
        if self.profile._mounts is not None:
            self.profile._mounts.close()
            self.profile._mounts = None
        self.profile._context = None


def build_hermes_driver(config, credentials, scope, stopper, *, acceptance=False):
    from .worker_entry import (CONTROL_ROOT, _UnavailableDriver, _host_registry, host_policy)
    policy = host_policy(config)
    if (not acceptance or not config.profile.enabled or config.model is None
            or policy.execution_binding is None or credentials.model_proxy_token is None
            or credentials.model_proxy_attempt_id != scope.attempt_id
            or credentials.model_proxy_request_sha256 != scope.request_sha256
            or not time.time() < credentials.model_proxy_expires_at <= time.time() + 3600):
        return _UnavailableDriver('hermes')
    binding = _host_registry(config).binding(policy.execution_binding.binding_id,
        policy.execution_binding.binding_revision)
    if (binding.engine != 'hermes' or binding.adapter_revision != ADAPTER_REVISION
            or binding.model_policy.model != config.model.model
            or binding.model_policy.provider != config.model.provider):
        raise ServiceError('execution_pins_mismatch', 503)
    root, _manifest, _hash, skills, _hashes = measured_package(config, policy, CONTROL_ROOT)
    return HermesHostDriver(config, credentials, scope, stopper, CONTROL_ROOT, root, skills)
