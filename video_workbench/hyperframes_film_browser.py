"""Freeze a verified project and dedicated browser hosts; no execution claims."""
from __future__ import annotations

import hashlib
import json

from agent_service.contracts import ServiceError, canonical_bytes, safe_relative_path
from .hyperframes_film_project import verify_film_project

SCHEMA = 'video.hyperframes-film-browser-bundle.v1'
HOST_NAMES = frozenset({'hyperframes_film.mjs', 'hyperframes_film_root.mjs',
    'hyperframes_film_realm.mjs', 'hyperframes_film_realm_guard.mjs', 'hyperframes_film_browser.mjs',
    'hyperframes_film_channel.mjs'})
MAX_HOST_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 204 * 1024 * 1024


def _require(value, code='hyperframes_film_browser_invalid'):
    if not value: raise ServiceError(code, 422)


def _sha(raw): return hashlib.sha256(raw).hexdigest()


def compile_film_browser_bundle(project_files, *, expected_project_manifest_sha256,
                                width, height, host_files):
    """Caller freezes the returned bundle SHA in its trusted execution input.

    Host bytes must come from the selected image-owned release, not Agent output.
    The actual core is loaded separately and checked against the pinned project
    runtime SHA; it is never replaced by a fixture or downloaded by this module.
    """
    manifest = verify_film_project(project_files, expected_manifest_sha256=expected_project_manifest_sha256)
    _require(type(width) is int and 320 <= width <= 1920 and type(height) is int and 180 <= height <= 1080)
    _require(type(host_files) is dict and set(host_files) == HOST_NAMES
        and all(type(raw) is bytes and 0 < len(raw) <= 512 * 1024 for raw in host_files.values())
        and sum(map(len, host_files.values())) <= MAX_HOST_BYTES)
    for raw in host_files.values():
        try: raw.decode('utf-8')
        except UnicodeError: raise ServiceError('hyperframes_film_browser_host_not_utf8', 422) from None
    resources = {'project/' + name: raw for name, raw in project_files.items()}
    resources.update({'host/' + name: raw for name, raw in host_files.items()})
    root = dict(schema='video.hyperframes-film-root-config.v1',
        project_manifest_sha256=expected_project_manifest_sha256, film_sha256=manifest['film_sha256'],
        width=width, height=height, root_entry='host/index.html',
        realm_protocol='opaque-sandbox-message-channel-1',
        hyperframes_core_sha256=manifest['runtime_fingerprint']['hyperframes_core_sha256'],
        host_files_sha256={name: _sha(raw) for name, raw in sorted(host_files.items())},
        evidence_scope='verified_bundle_inputs_not_runtime_admission_or_render')
    resources['host/config.json'] = canonical_bytes(root)
    html = ('<!doctype html><html><head><meta charset="utf-8"><title>Film root</title></head>'
        '<body><main id="film-root"></main><script type="module" src="/host/hyperframes_film_root.mjs"></script></body></html>')
    resources['host/index.html'] = html.encode()
    realms = {}
    for module in manifest['modules']:
        namespace = module['namespace']
        config = {key: module[key] for key in ('module_id', 'module_sha256', 'film_sha256', 'engine', 'entry', 'assets')}
        config.update(width=width, height=height)
        # Metadata has already passed the project verifier; escape HTML delimiters
        # even though the protocol uses bounded ASCII module/entry identifiers.
        text = canonical_bytes(config).decode().replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
        entry = 'realms/' + namespace + '/index.html'
        resources[entry] = ('<!doctype html><html><head><meta charset="utf-8"></head><body>'
            '<div id="film-module-root"></div><script id="film-module-config" type="application/json">' + text
            + '</script><script type="module" src="/host/hyperframes_film_realm.mjs"></script></body></html>').encode()
        realms[entry] = module['module_id']
    _require(sum(map(len, resources.values())) <= MAX_BUNDLE_BYTES, 'hyperframes_film_browser_budget_exceeded')
    inventory = {}
    for path, raw in sorted(resources.items()):
        safe_relative_path(path)
        role = ('realm' if path in realms else 'trusted-host' if path.startswith('host/') else
            'recording-not-browser-served' if path.startswith('project/recordings/') else 'project')
        inventory[path] = dict(sha256=_sha(raw), bytes=len(raw), role=role)
    resources['browser-manifest.json'] = canonical_bytes(dict(schema=SCHEMA, root_config=root,
        project_manifest_sha256=expected_project_manifest_sha256, realms=realms, resources=inventory,
        execution_status='not_executed', package_verification='independent_project_recompilation'))
    return resources


def verify_film_browser_bundle(files, *, expected_bundle_sha256):
    _require(type(files) is dict and 'browser-manifest.json' in files
        and all(type(path) is str and type(raw) is bytes for path, raw in files.items()))
    raw = files['browser-manifest.json']
    _require(len(raw) <= 1024 * 1024 and _sha(raw) == expected_bundle_sha256,
        'hyperframes_film_browser_root_digest_mismatch')
    try:
        bundle = json.loads(raw)
        _require(bundle['schema'] == SCHEMA and set(files) == set(bundle['resources']) | {'browser-manifest.json'})
        for path, identity in bundle['resources'].items():
            _require(identity['sha256'] == _sha(files[path]) and identity['bytes'] == len(files[path]))
        root = bundle['root_config']
        expected = compile_film_browser_bundle(
            {path[len('project/'):]: value for path, value in files.items() if path.startswith('project/')},
            expected_project_manifest_sha256=bundle['project_manifest_sha256'],width=root['width'],height=root['height'],
            host_files={name: files['host/' + name] for name in HOST_NAMES})
        _require(files == expected, 'hyperframes_film_browser_not_compiler_output')
        return json.loads(raw)
    except (KeyError, ValueError, TypeError, UnicodeError, RecursionError):
        raise ServiceError('hyperframes_film_browser_invalid', 422) from None
