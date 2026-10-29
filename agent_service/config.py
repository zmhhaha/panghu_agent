"""Trusted deployment configuration, separate from jobs and provider credentials.

Loading/checking configuration reads two bounded ordinary JSON files. It does
not open a database, contact a worker, select a Python module, or run an Agent.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import ipaddress
import math
import os
from pathlib import Path
import re
import stat
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.engine import make_url

from .api import ApiSettings
from .contracts import Principal, ProfileRegistry, ServiceError
from .transport import WorkerEndpoint


class ConfigurationError(ServiceError):
    def __init__(self, code='configuration_invalid'):
        super().__init__(code, 503)


def _read_json(path, *, secret=False):
    import json
    limit = 131072 if secret else 4 * 1024 * 1024
    failure = 'secrets_file_unavailable' if secret else 'configuration_file_unavailable'
    try:
        filename = Path(path).absolute()
        for component in (*reversed(filename.parents), filename):
            observed = component.lstat()
            if stat.S_ISLNK(observed.st_mode) or getattr(observed, 'st_file_attributes', 0) & 0x400:
                raise ValueError()
            if component != filename and not stat.S_ISDIR(observed.st_mode):
                raise ValueError()
        if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1 or observed.st_size > limit:
            raise ValueError()
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
        with os.fdopen(os.open(filename, flags), 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (not os.path.samestat(observed, opened) or not stat.S_ISREG(opened.st_mode)
                    or opened.st_nlink != 1 or opened.st_size > limit):
                raise ValueError()
            content = stream.read(limit + 1)
            if len(content) > limit or len(content) != opened.st_size:
                raise ValueError()
    except (OSError, ValueError, TypeError):
        raise ConfigurationError(failure) from None

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def constant(_):
        raise ValueError()

    try:
        value = json.loads(content.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise ConfigurationError('secrets_invalid' if secret else 'configuration_invalid') from None


class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class _Caller(_Model):
    tenant: str = Field(min_length=1, max_length=128)
    caller: str = Field(min_length=1, max_length=128)
    profiles: list[str] = Field(max_length=100)
    token_ref: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,63}$')

    @field_validator('tenant', 'caller')
    @classmethod
    def ordinary_identity(cls, value):
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Invalid identity')
        return value

    @field_validator('profiles')
    @classmethod
    def registered_contract_names(cls, values):
        if (len(set(values)) != len(values)
                or any(not re.fullmatch(r'[a-z][a-z0-9_.-]{0,79}', value) for value in values)):
            raise ValueError('Invalid or duplicate task contract name')
        return values


class _Worker(_Model):
    engine: Literal['pi', 'hermes']
    base_url: str = Field(min_length=1, max_length=300)
    token_ref: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,63}$')
    receipt_key_ref: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,63}$')

    @field_validator('base_url')
    @classmethod
    def fixed_origin(cls, value):
        parsed = urlsplit(value)
        if (any(ord(c) < 33 or ord(c) == 127 for c in value) or '\\' in value
                or parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {'', '/'} or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError('Invalid fixed worker origin')
        return value


class _KubernetesWorker(_Model):
    pool_id: str = Field(pattern=r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
    engine: Literal['pi', 'hermes']
    binding_id: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_.-]{0,79}$')
    binding_revision: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    namespace: str = Field(pattern=r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
    image: str = Field(min_length=1, max_length=400)
    host_config_map: str = Field(min_length=1, max_length=253)
    pod_cidrs: list[str] = Field(min_length=1, max_length=8)
    enabled: bool = False
    port: int = Field(default=8090, ge=8090, le=8090)
    uid_start: int | None = Field(default=None, ge=65536, le=2147483647)
    uid_count: int = Field(default=65536, ge=1, le=1000000)
    read_only_mounts: bool = False
    mount_apparmor_profile: str = Field(default='agent-capsule-inputs', pattern=r'^[a-zA-Z0-9_.-]{1,80}$')
    node_selector: dict[str, str] = Field(default_factory=dict)
    token_ref: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,63}$')
    receipt_key_ref: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,63}$')

    @field_validator('node_selector')
    @classmethod
    def fixed_nodes(cls, values):
        from .kubernetes_provider import validate_node_selector
        validate_node_selector(tuple(values.items()))
        return values

    @field_validator('image')
    @classmethod
    def pinned_image(cls, value):
        if not re.fullmatch(
                r'(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?/)?'
                r'[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*'
                r'(?::latest)?@sha256:[0-9a-f]{64}', value):
            raise ValueError('A fixed Worker repository and digest are required')
        registry = value.rsplit('@', 1)[0].removesuffix(':latest').split('/', 1)[0]
        if ':' in registry:
            if not 1 <= int(registry.rsplit(':', 1)[1]) <= 65535:
                raise ValueError('Invalid registry port')
        return value

    @field_validator('host_config_map')
    @classmethod
    def config_map_name(cls, value):
        if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
               for part in value.split('.')):
            raise ValueError('A fixed ConfigMap name is required')
        return value

    @field_validator('pod_cidrs')
    @classmethod
    def private_pod_networks(cls, values):
        seen = set()
        forbidden = tuple(ipaddress.ip_network(value) for value in (
            '0.0.0.0/8', '127.0.0.0/8', '169.254.0.0/16', '224.0.0.0/4', '240.0.0.0/4',
            '::/128', '::1/128', 'fe80::/10', 'ff00::/8', '::ffff:0:0/96'))
        for value in values:
            network = ipaddress.ip_network(value, strict=True)
            if (network.prefixlen == 0 or not network.is_private or network in seen
                    or any(network.version == blocked.version and network.overlaps(blocked)
                           for blocked in forbidden)):
                raise ValueError('A bounded private Pod network is required')
            seen.add(network)
        return values

    @model_validator(mode='after')
    def reserved_uid_range(self):
        if (self.enabled and self.uid_start is None
                or self.uid_start is not None and self.uid_start + self.uid_count - 1 > 2147483647):
            raise ValueError('An enabled pool requires an explicitly reserved bounded UID range')
        return self


class _Api(_Model):
    host: str = Field(default='127.0.0.1', pattern=r'^[A-Za-z0-9_.:-]{1,253}$')
    port: int = Field(default=8080, ge=1, le=65535)
    max_request_bytes: int = Field(default=4 * 1024 * 1024, ge=1024, le=4 * 1024 * 1024)
    max_input_bytes: int = Field(default=64 * 1024 * 1024, ge=1, le=64 * 1024 * 1024)


class _Dispatch(_Model):
    poll_seconds: float = Field(default=1.0, gt=0, le=5)
    lease_seconds: int = Field(default=60, ge=15, le=300)

    @field_validator('poll_seconds')
    @classmethod
    def finite_poll(cls, value):
        if not math.isfinite(value):
            raise ValueError('Invalid poll interval')
        return value


class _Store(_Model):
    max_events: int = Field(default=1000, ge=32, le=10000)
    max_queue: int = Field(default=1000, ge=1, le=10000)


class _ModelProxy(_Model):
    upstream_base_url: str = Field(min_length=1, max_length=300)
    upstream_token_ref: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,63}$')
    signing_key_ref: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,63}$')
    max_calls_per_attempt: int = Field(default=128, ge=1, le=1000)
    max_output_tokens: int = Field(default=32768, ge=256, le=131072)


class _Public(_Model):
    schema_version: Literal[2]
    callers: list[_Caller] = Field(min_length=1, max_length=100)
    task_contracts: list[dict] = Field(default_factory=list, max_length=100)
    capability_bundles: list[dict] = Field(default_factory=list, max_length=100)
    tool_environments: list[dict] = Field(default_factory=list, max_length=100)
    execution_bindings: list[dict] = Field(default_factory=list, max_length=100)
    routes: list[dict] = Field(default_factory=list, max_length=100)
    workers: list[_Worker] = Field(default_factory=list, max_length=2)
    kubernetes_workers: list[_KubernetesWorker] = Field(default_factory=list, max_length=100)
    api: _Api = Field(default_factory=_Api)
    dispatcher: _Dispatch = Field(default_factory=_Dispatch)
    store: _Store = Field(default_factory=_Store)
    model_proxy: _ModelProxy | None = None

    @field_validator('schema_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('Invalid configuration version')
        return value

    @model_validator(mode='after')
    def unique_routes(self):
        engines = [w.engine for w in self.workers]
        kube_engines = {w.engine for w in self.kubernetes_workers}
        pools = [w.pool_id for w in self.kubernetes_workers]
        caller_keys = [c.token_ref for c in self.callers]
        if (len(set(engines)) != len(engines) or set(engines) & kube_engines
                or len(set(caller_keys)) != len(caller_keys) or len(set(pools)) != len(pools)):
            raise ValueError('Duplicate configured identity or route')
        return self


class _Secrets(_Model):
    database_url: str = Field(min_length=1, max_length=4096, repr=False)
    tokens: dict[str, str] = Field(repr=False)
    receipt_keys: dict[str, str] = Field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class KubernetesWorkerCredentials:
    pool_id: str
    token: str = field(repr=False)
    receipt_key: bytes = field(repr=False)


@dataclass(frozen=True)
class ServiceConfiguration:
    database_url: str = field(repr=False)
    api_settings: ApiSettings = field(repr=False)
    registry: ProfileRegistry = field(repr=False)
    worker_endpoints: tuple[WorkerEndpoint, ...] = field(repr=False)
    deployment_registry: object = field(default=None, repr=False)
    kubernetes_pools: tuple = field(default=(), repr=False)
    kubernetes_credentials: tuple[KubernetesWorkerCredentials, ...] = field(default=(), repr=False)
    model_proxy_settings: object = field(default=None, repr=False)
    api_host: str = '127.0.0.1'
    api_port: int = 8080
    poll_seconds: float = 1.0
    lease_seconds: int = 60
    max_events: int = 1000
    max_queue: int = 1000
    allow_sqlite: bool = False
    database_kind: str = 'postgresql'

    def build_store(self):
        from .store import Store
        try:
            return Store(self.database_url, allow_sqlite=self.allow_sqlite,
                         max_blob_bytes=self.api_settings.max_input_bytes,
                         max_events=self.max_events, max_queue=self.max_queue)
        except Exception:
            raise ConfigurationError('database_configuration_unavailable') from None

    def build_adapters(self, store=None):
        from .transport import RemoteWorkerAdapter
        adapters = {}
        try:
            for endpoint in self.worker_endpoints:
                adapters[endpoint.engine] = RemoteWorkerAdapter(endpoint)
            if self.kubernetes_pools:
                if store is None:
                    raise ValueError('A persistent store is required for Worker provisioning')
                from .kubernetes_client import KubernetesClient
                from .kubernetes_provider import KubernetesWorkerAdapter
                credentials = {value.pool_id: value for value in self.kubernetes_credentials}
                routed = {}
                for pool in self.kubernetes_pools:
                    private = credentials[pool.pool_id]
                    kube = KubernetesClient.from_service_account()
                    try:
                        from .model_proxy import ModelGrantIssuer
                        issuer = (ModelGrantIssuer(self.model_proxy_settings.signing_key)
                                  if self.model_proxy_settings is not None else None)
                        pins = pool.policy.execution_binding
                        model_policy = (self.deployment_registry.binding(pins.binding_id,
                                        pins.binding_revision).model_policy if pins else None)
                        adapter = KubernetesWorkerAdapter(store, pool, kube, private.token,
                            private.receipt_key, model_grant_issuer=issuer, model_policy=model_policy)
                    except Exception:
                        kube.close()
                        raise
                    routed.setdefault(pool.policy.engine, []).append((pool.policy, adapter))
                from .routing import RoutedWorkerAdapter
                for engine, entries in routed.items():
                    adapters[engine] = RoutedWorkerAdapter(entries)
        except Exception:
            for adapter in adapters.values():
                try:
                    adapter.close()
                except Exception:
                    pass
            raise ConfigurationError('worker_configuration_unavailable') from None
        return adapters

    def summary(self):
        engines = [endpoint.engine for endpoint in self.worker_endpoints]
        engines += [pool.policy.engine for pool in self.kubernetes_pools]
        mode = ('mixed' if self.worker_endpoints and self.kubernetes_pools else
                'kubernetes_per_attempt' if self.kubernetes_pools else
                'external_single_capsule' if self.worker_endpoints else 'unconfigured')
        return {'status': 'configuration_valid', 'schema_version': 2,
                'database_kind': self.database_kind,
                'caller_count': len(self.api_settings.principals),
                'worker_engines': engines, 'worker_provisioning': mode, 'model_requests': 0,
                'catalog': self.deployment_registry.summary() if self.deployment_registry is not None else None}


def load_configuration(config_path, secrets_path, *, allow_sqlite=False) -> ServiceConfiguration:
    """Parse trusted files only. No environment, module or job selects a backend."""
    if type(allow_sqlite) is not bool:
        raise ConfigurationError()
    public_wire = _read_json(config_path)
    secret_wire = _read_json(secrets_path, secret=True)
    if not {'database_url', 'tokens'} <= set(secret_wire):
        raise ConfigurationError('required_secret_missing')
    try:
        public = _Public.model_validate(public_wire)
    except (ValueError, TypeError, RecursionError):
        raise ConfigurationError() from None
    try:
        from .registry import DeploymentRegistry
        deployment_registry = DeploymentRegistry.from_config(
            public.task_contracts, public.capability_bundles, public.tool_environments,
            public.execution_bindings, public.routes)
        registry = deployment_registry.profiles()
        registered_names = {contract['name'] for contract in public.task_contracts}
        if any(not set(caller.profiles) <= registered_names for caller in public.callers):
            raise ValueError('Caller scope names an unregistered task contract')
        for pool in public.kubernetes_workers:
            if (pool.binding_id is None) != (pool.binding_revision is None):
                raise ValueError('Both fixed binding identity fields are required')
            matches = ([deployment_registry.binding_policy(pool.binding_id, pool.binding_revision)]
                       if pool.binding_id is not None else
                       [policy for policy in registry.policies() if policy.engine == pool.engine])
            if (len(matches) != 1 or matches[0].engine != pool.engine
                    or matches[0].image_digest != pool.image.rsplit('@', 1)[1]):
                raise ValueError('A Worker pool requires exactly one matching pinned routed profile')
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ConfigurationError() from None
    try:
        secrets = _Secrets.model_validate(secret_wire)
        if len(secrets.tokens) > 300 or len(secrets.receipt_keys) > 100:
            raise ValueError()
        for reference, token in secrets.tokens.items():
            if (not re.fullmatch(r'[a-z][a-z0-9_.-]{0,63}', reference)
                    or not 32 <= len(token) <= 512 or any(not 33 <= ord(c) <= 126 for c in token)):
                raise ValueError()
        if len(set(secrets.tokens.values())) != len(secrets.tokens):
            raise ValueError()
        decoded = {}
        for reference, encoded in secrets.receipt_keys.items():
            if not re.fullmatch(r'[a-z][a-z0-9_.-]{0,63}', reference) or len(encoded) > 1400:
                raise ValueError()
            raw = base64.b64decode(encoded, validate=True)
            if not 32 <= len(raw) <= 1024:
                raise ValueError()
            decoded[reference] = raw
    except (ValueError, TypeError, RecursionError):
        raise ConfigurationError('secrets_invalid') from None
    try:
        url = make_url(secrets.database_url)
        dialect = url.get_backend_name()
        if dialect == 'sqlite':
            if not allow_sqlite:
                raise ConfigurationError('postgresql_required')
            if url.drivername != 'sqlite' or url.database in {None, '', ':memory:'} or url.query:
                raise ValueError()
        elif dialect != 'postgresql' or url.drivername not in {'postgresql', 'postgresql+psycopg'} or not url.database:
            raise ValueError()
        # Pin the supported installed driver rather than SQLAlchemy's legacy
        # implicit psycopg2 default. Parsing never attempts a DB connection.
        if url.drivername == 'postgresql':
            database_url = url.set(drivername='postgresql+psycopg').render_as_string(hide_password=False)
        else:
            database_url = secrets.database_url
    except ConfigurationError:
        raise
    except Exception:
        raise ConfigurationError('database_configuration_invalid') from None
    try:
        principals = {}
        for caller in public.callers:
            principals[secrets.tokens[caller.token_ref]] = Principal(caller.tenant, caller.caller,
                                                                  frozenset(caller.profiles))
        endpoints = tuple(WorkerEndpoint(worker.base_url, secrets.tokens[worker.token_ref],
                                        decoded[worker.receipt_key_ref], worker.engine)
                          for worker in public.workers)
    except KeyError:
        raise ConfigurationError('required_secret_missing') from None
    except (ValueError, TypeError):
        raise ConfigurationError('worker_configuration_invalid') from None
    try:
        api_settings = ApiSettings(principals, public.api.max_request_bytes, public.api.max_input_bytes)
    except (ValueError, TypeError):
        raise ConfigurationError() from None
    proxy_settings = None
    if public.model_proxy is not None:
        try:
            from .model_proxy import ModelProxySettings
            proxy_settings = ModelProxySettings(
                signing_key=decoded[public.model_proxy.signing_key_ref],
                upstream_base_url=public.model_proxy.upstream_base_url,
                upstream_token=secrets.tokens[public.model_proxy.upstream_token_ref],
                max_calls_per_attempt=public.model_proxy.max_calls_per_attempt,
                max_output_tokens=public.model_proxy.max_output_tokens)
        except (KeyError, ValueError, TypeError):
            raise ConfigurationError('model_proxy_configuration_invalid') from None
    pools, kube_credentials = (), ()
    if public.kubernetes_workers:
        try:
            from .kubernetes_provider import KubernetesPool
            pools = tuple(KubernetesPool(pool_id=worker.pool_id,
                policy=(deployment_registry.binding_policy(worker.binding_id, worker.binding_revision)
                        if worker.binding_id is not None else
                        next(policy for policy in registry.policies() if policy.engine == worker.engine)),
                namespace=worker.namespace, image=worker.image, host_config_map=worker.host_config_map,
                pod_cidrs=tuple(worker.pod_cidrs), enabled=worker.enabled, port=worker.port,
                uid_start=worker.uid_start, uid_count=worker.uid_count,
                read_only_mounts=worker.read_only_mounts,
                mount_apparmor_profile=worker.mount_apparmor_profile,
                node_selector=tuple(sorted(worker.node_selector.items())))
                for worker in public.kubernetes_workers)
            kube_credentials = tuple(KubernetesWorkerCredentials(worker.pool_id,
                secrets.tokens[worker.token_ref], decoded[worker.receipt_key_ref])
                for worker in public.kubernetes_workers)
            if any(pool.enabled and (proxy_settings is None or pool.policy.execution_binding is None
                    or pool.policy.supports_inputs and not pool.read_only_mounts) for pool in pools):
                raise ValueError('Enabled domain capsules require model grants and read-only input mounts')
        except KeyError:
            raise ConfigurationError('required_secret_missing') from None
        except (ValueError, TypeError, StopIteration):
            raise ConfigurationError('worker_configuration_invalid') from None
    return ServiceConfiguration(database_url=database_url, api_settings=api_settings, registry=registry,
        worker_endpoints=endpoints, deployment_registry=deployment_registry,
        kubernetes_pools=pools, kubernetes_credentials=kube_credentials,
        model_proxy_settings=proxy_settings,
        api_host=public.api.host, api_port=public.api.port,
        poll_seconds=public.dispatcher.poll_seconds, lease_seconds=public.dispatcher.lease_seconds,
        max_events=public.store.max_events, max_queue=public.store.max_queue,
        allow_sqlite=allow_sqlite, database_kind='sqlite-development' if dialect == 'sqlite' else 'postgresql')


load_config = load_configuration
