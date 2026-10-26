"""Fixed-image, single-attempt capsule host. No caller-selected executable.

The only runnable native host here is the bounded Pi text profile. Hermes stays
explicitly unavailable until its own isolated host has passed acceptance. A
dedicated Responses proxy grant and an operator-owned acceptance record are
required; this process never substitutes a personal or upstream API key.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from typing import Literal
from urllib.parse import urlsplit
import ipaddress
import uuid

from pydantic import Field, SecretStr, field_validator, model_validator

from .contracts import ProfilePolicy, ServiceError, WireModel, validate_workspace_task
from .engine_profiles import WorkspaceTaskProfile
from .engines import NativeOutcome, PiDriver
from .linux_stop import AGENT_UID, LinuxUidStopController, private_kernel_scope
from .runtime import PiRuntime, RuntimeSettings
from .worker_api import WorkerHost, create_worker_app


CONTROL_ROOT = Path('/run/agent-control')
WORKSPACE_ROOT = Path('/work')
NODE = '/usr/local/bin/node'
PI_CLI = '/opt/pi/node_modules/@mariozechner/pi-coding-agent/dist/cli.js'
PROFILE_BUNDLE_REVISION = 'workspace-text-v1'
RESOURCE_ROOT = Path(__file__).with_name('resources') / PROFILE_BUNDLE_REVISION


class WorkerConfigError(RuntimeError):
    code = 'invalid_worker_configuration'

    def __init__(self):
        super().__init__(self.code)


class HostProfile(WireModel):
    name: Literal['workspace-task.v1'] = 'workspace-task.v1'
    revision: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    runtime_revision: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    image_digest: str = Field(pattern=r'^sha256:[0-9a-f]{64}$')
    enabled: bool = False
    max_timeout_seconds: int = Field(default=600, ge=1, le=3600)


class ProxyModel(WireModel):
    provider: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,80}$')
    model: str = Field(pattern=r'^[A-Za-z0-9_./:-]{1,200}$')
    api: Literal['openai-responses'] = 'openai-responses'
    proxy_base_url: str

    @field_validator('proxy_base_url')
    @classmethod
    def scoped_proxy_url(cls, value):
        try:
            parsed = urlsplit(value)
            host = parsed.hostname or ''
            try:
                address = ipaddress.ip_address(host)
                private = address.version == 4 and any(address in ipaddress.ip_network(cidr)
                    for cidr in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
            except ValueError:
                private = bool(re.fullmatch(r'[a-z0-9.-]+\.svc\.cluster\.local', host))
            if (parsed.scheme != 'http' or not private or parsed.port != 8080
                    or parsed.path != '/v1' or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or any(ord(c) <= 32 for c in value)):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('A dedicated private task proxy is required') from None
        return value


class HostConfig(WireModel):
    schema_version: Literal[1] = 1
    engine: Literal['pi', 'hermes']
    profile: HostProfile
    model: ProxyModel | None = None


class HostSecrets(WireModel):
    worker_token: SecretStr
    receipt_key_hex: SecretStr
    model_proxy_token: SecretStr | None = None
    model_proxy_attempt_id: str | None = None
    model_proxy_request_sha256: str | None = None
    model_proxy_expires_at: float | None = None

    @model_validator(mode='after')
    def secrets_contract(self):
        token = self.worker_token.get_secret_value()
        receipt = self.receipt_key_hex.get_secret_value()
        if (not 32 <= len(token) <= 256 or any(ord(c) < 33 or ord(c) > 126 for c in token)
                or not re.fullmatch(r'[0-9a-f]{64,128}', receipt) or len(receipt) % 2):
            raise ValueError('Invalid dedicated host credential')
        fields = (self.model_proxy_token, self.model_proxy_attempt_id,
                  self.model_proxy_request_sha256, self.model_proxy_expires_at)
        if any(x is not None for x in fields):
            if (any(x is None for x in fields)
                    or not 32 <= len(self.model_proxy_token.get_secret_value()) <= 256
                    or any(ord(c) < 33 or ord(c) > 126 for c in self.model_proxy_token.get_secret_value())
                    or not _canonical_uuid(self.model_proxy_attempt_id)
                    or not re.fullmatch(r'[0-9a-f]{64}', self.model_proxy_request_sha256)
                    or not math.isfinite(self.model_proxy_expires_at)):
                raise ValueError('Incomplete scoped proxy grant')
        return self


def _canonical_uuid(value):
    try:
        return type(value) is str and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


class CapsuleScope(WireModel):
    attempt_id: str
    pod_uid: str
    request_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    # This default exists only for direct offline fixture construction. A real
    # capsule must receive an explicitly reserved UID via from_env below.
    agent_uid: int = Field(default=AGENT_UID, strict=True, ge=1, le=2**31 - 1)

    @field_validator('attempt_id', 'pod_uid')
    @classmethod
    def scope_uuid(cls, value):
        if not _canonical_uuid(value):
            raise ValueError('Canonical scope UUID required')
        return value

    @classmethod
    def from_env(cls, config: HostConfig, env=None):
        env = os.environ if env is None else env
        expected = {'AGENT_ENGINE': config.engine, 'AGENT_PROFILE_NAME': config.profile.name,
                    'AGENT_PROFILE_REVISION': config.profile.revision,
                    'AGENT_RUNTIME_REVISION': config.profile.runtime_revision,
                    'AGENT_RUNTIME_IMAGE_DIGEST': config.profile.image_digest}
        if any(env.get(k) != v for k, v in expected.items()):
            raise WorkerConfigError()
        runtime_uid = env.get('AGENT_RUNTIME_UID')
        if (type(runtime_uid) is not str or not re.fullmatch(r'[1-9][0-9]{0,9}', runtime_uid)
                or not 65536 <= int(runtime_uid) <= 2**31 - 1):
            raise WorkerConfigError()
        return cls(attempt_id=env.get('AGENT_ATTEMPT_ID'), pod_uid=env.get('AGENT_POD_UID'),
                   request_sha256=env.get('AGENT_REQUEST_SHA256'), agent_uid=int(runtime_uid))


def _read_operator_json(path: Path, *, private=False, max_bytes=65536):
    """Read a root-owned fixed mount, including Kubernetes's ..data links.

    Projected volume links may resolve only inside their root-owned mount; the
    final file is opened without following a link and checked against lstat.
    """
    path = Path(os.path.abspath(path))
    parent = path.parent.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if parent not in resolved.parents:
        raise WorkerConfigError()
    for component in (parent, *parent.parents):
        info = component.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise WorkerConfigError()
    before = resolved.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != 0
            or before.st_mode & (0o077 if private else 0o022) or not 0 < before.st_size <= max_bytes):
        raise WorkerConfigError()
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(resolved, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not os.path.samestat(before, opened):
            raise WorkerConfigError()
        data = stream.read(max_bytes + 1)
    if len(data) != before.st_size or len(data) > max_bytes:
        raise WorkerConfigError()
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise WorkerConfigError()
            result[k] = v
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(WorkerConfigError()))


def accepted_host(config: HostConfig, path=Path('/etc/agent-worker/acceptance.json')):
    """Operator-attested candidate evidence is separate from enablement.

    Absence, stale pins or missing model/proxy/isolation evidence cannot be
    turned into readiness by setting profile.enabled. This host produces no
    acceptance records and never accepts one in a Job or Worker request.
    """
    try:
        value = _read_operator_json(path)
        required = {'schema_version', 'engine', 'profile', 'profile_revision', 'runtime_revision',
                    'image_digest', 'model_api', 'checks', 'evidence_sha256'}
        if set(value) != required:
            return False
        expected = dict(schema_version=1, engine='pi', profile=config.profile.name,
                        profile_revision=config.profile.revision,
                        runtime_revision=config.profile.runtime_revision,
                        image_digest=config.profile.image_digest, model_api='openai-responses')
        if any(type(value.get(k)) is not type(v) or value[k] != v for k, v in expected.items()):
            return False
        checks = value['checks']
        if (type(checks) is not dict
                or set(checks) != {'capability', 'model_round_trip', 'isolation', 'model_proxy_lease'}
                or any(type(item) is not bool or item is not True for item in checks.values())):
            return False
        expected_hash = value['evidence_sha256']
        evidence_path = path.with_name('acceptance-evidence.json')
        evidence = _read_operator_json(evidence_path)
        raw = evidence_path.resolve(strict=True).read_bytes()
        return (isinstance(evidence, dict) and bool(evidence)
                and type(expected_hash) is str and re.fullmatch(r'[0-9a-f]{64}', expected_hash) is not None
                and expected_hash != '0' * 64 and hashlib.sha256(raw).hexdigest() == expected_hash)
    except (OSError, ValueError, TypeError, WorkerConfigError):
        return False


class _UnavailableDriver:
    ready = False

    def __init__(self, engine):
        self.engine = engine

    def execute(self, *args, **kwargs):
        return NativeOutcome('quarantined', error_code='engine_host_unavailable')


class _AcceptedWorkspaceProfile(WorkspaceTaskProfile):
    # Only constructed below after the independent operator acceptance check.
    production_ready = True


def build_pi_driver(config: HostConfig, secrets: HostSecrets, scope: CapsuleScope,
                    stop_controller, *, acceptance=False, now=time.time):
    grant_ok = (secrets.model_proxy_token is not None
                and secrets.model_proxy_attempt_id == scope.attempt_id
                and secrets.model_proxy_request_sha256 == scope.request_sha256
                and now() < secrets.model_proxy_expires_at <= now() + 600)
    if (config.engine != 'pi' or not config.profile.enabled or config.model is None
            or not acceptance or not grant_ok):
        return _UnavailableDriver(config.engine)
    resource = RESOURCE_ROOT / 'CONTRACT.md'
    manifest = RESOURCE_ROOT / 'manifest.json'
    # Fixed image-owned resources, never caller-selected paths or content.
    expected_manifest = json.loads(manifest.read_text(encoding='utf-8'))
    if (expected_manifest.get('bundle_revision') != PROFILE_BUNDLE_REVISION
            or len(expected_manifest.get('local_files', [])) != 1
            or expected_manifest['local_files'][0]['path'] != 'CONTRACT.md'):
        raise WorkerConfigError()
    resource_hash = expected_manifest['local_files'][0]['sha256']
    profile = _AcceptedWorkspaceProfile(runtime_revision=config.profile.runtime_revision,
                                         skill_bundle_revision=PROFILE_BUNDLE_REVISION)
    settings = RuntimeSettings(command=(NODE, PI_CLI), node_command=(NODE,),
        provider=config.model.provider, model=config.model.model,
        model_base_url=config.model.proxy_base_url, model_api=config.model.api,
        model_env={'AGENT_MODEL_API_KEY': secrets.model_proxy_token.get_secret_value()},
        runtime_revision=config.profile.runtime_revision, skill_bundle_revision=PROFILE_BUNDLE_REVISION,
        image_digest=config.profile.image_digest, skill_paths=(resource,), skill_hashes=(resource_hash,),
        bundle_manifest=manifest, bundle_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        capability_verified=True, model_verified=True, isolation_verified=True,
        control_dir=CONTROL_ROOT, run_uid_start=scope.agent_uid, run_gid=scope.agent_uid,
        # Projected Secret directories are traversable; the protected file,
        # rather than its Kubernetes-created directory, is the sentinel.
        denied_paths=(Path('/run/agent-secrets/worker.json'),), compact_events=True)
    runtime = PiRuntime(settings, profile=profile)
    def wire(request):
        if (request.execution_id != scope.attempt_id or request.deadline_at.timestamp() > secrets.model_proxy_expires_at
                or now() >= secrets.model_proxy_expires_at or stop_controller.consumed):
            raise ServiceError('model_proxy_grant_invalid', 503)
        return {**profile.make_wire(request), '_runtime_uid': scope.agent_uid}
    return PiDriver(runtime, stop_controller, wire_builder=wire, enabled=True)


class ScopedWorkerHost(WorkerHost):
    def __init__(self, *args, scope, stop_controller, proxy_expires_at=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope, self.stop_controller = scope, stop_controller
        self.proxy_expires_at = proxy_expires_at

    def initialize(self):
        self.stop_controller.initialize()
        if self.stop_controller._uid_processes():
            # Unexpected writers at HTTP startup are not a fresh capsule. Stop
            # them and leave the scope consumed; never resume a paid execution.
            self.stop_controller.stop(self.scope.attempt_id)
        super().initialize()

    def capabilities(self):
        value = super().capabilities()
        if (self.stop_controller.consumed or self.proxy_expires_at is None
                or self.clock() >= self.proxy_expires_at):
            value['ready'] = False
        return value

    def start(self, envelope):
        if (envelope.attempt_id != self.scope.attempt_id
                or envelope.request_sha256 != self.scope.request_sha256):
            raise ServiceError('execution_scope_mismatch', 409)
        if self._state is None and (self.proxy_expires_at is None or envelope.deadline_at > self.proxy_expires_at):
            raise ServiceError('model_proxy_grant_invalid', 503)
        return super().start(envelope)

    def close(self):
        try:
            super().close()
        finally:
            self.stop_controller.stop(self.scope.attempt_id)


def assemble_app(config: HostConfig, secrets: HostSecrets, scope: CapsuleScope):
    private_kernel_scope()
    # Kubernetes emptyDir mounts replace the image directory's permissions.
    # Establish the private root-owned control boundary before either journal
    # opens; the Agent never receives write access to this mount.
    CONTROL_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    control_info = CONTROL_ROOT.lstat()
    if (not stat.S_ISDIR(control_info.st_mode) or control_info.st_uid != 0
            or CONTROL_ROOT.is_symlink()):
        raise WorkerConfigError()
    CONTROL_ROOT.chmod(0o700)
    if not WORKSPACE_ROOT.exists():
        WORKSPACE_ROOT.mkdir(mode=0o711)
    info = WORKSPACE_ROOT.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or WORKSPACE_ROOT.is_symlink()):
        raise WorkerConfigError()
    # The child may traverse its own attempt but cannot list or replace siblings.
    WORKSPACE_ROOT.chmod(0o711)
    stopper = LinuxUidStopController(CONTROL_ROOT, scope.attempt_id, scope.pod_uid, scope.request_sha256,
                                    agent_uid=scope.agent_uid)
    driver = build_pi_driver(config, secrets, scope, stopper, acceptance=accepted_host(config))
    policy = ProfilePolicy(config.profile.name, config.profile.revision, config.engine,
        config.profile.runtime_revision, config.profile.image_digest,
        enabled=config.profile.enabled, max_timeout_seconds=config.profile.max_timeout_seconds,
        task_validator=validate_workspace_task)
    host = ScopedWorkerHost(policy, driver, CONTROL_ROOT, WORKSPACE_ROOT,
        bytes.fromhex(secrets.receipt_key_hex.get_secret_value()), scope=scope, stop_controller=stopper,
        proxy_expires_at=secrets.model_proxy_expires_at)
    return create_worker_app(host, secrets.worker_token.get_secret_value())


def main(argv=None):
    parser = argparse.ArgumentParser(description='Fixed-image single-attempt Agent capsule host')
    parser.add_argument('--config', type=Path, default=Path('/etc/agent-worker/config.json'))
    parser.add_argument('--secrets', type=Path, default=Path('/run/agent-secrets/worker.json'))
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    try:
        config = HostConfig.model_validate(_read_operator_json(args.config))
        secrets = HostSecrets.model_validate(_read_operator_json(args.secrets, private=True))
        scope = CapsuleScope.from_env(config)
        private_kernel_scope()
        if args.check:
            print(json.dumps({'status': 'configuration_valid', 'engine': config.engine,
                              'host_accepted': accepted_host(config)}))
            return 0
        import uvicorn
        from .__main__ import _logging_configuration
        app = assemble_app(config, secrets, scope)
        uvicorn.run(app, host='0.0.0.0', port=8090, access_log=False, proxy_headers=False,
                    server_header=False, log_config=_logging_configuration())
        return 0
    except Exception:
        # Pydantic and subprocess exceptions can contain the credential input.
        print(json.dumps({'status': 'error', 'code': 'worker_host_unavailable'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
