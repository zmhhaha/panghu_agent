"""Application-side dependency eligibility before freezing a Skill directory.

No worker/Pi protocol changes: excluded packages never enter the ordinary
immutable catalog/bundle. The versioned context is saved only in new ledger
intents, so an older pending intent or completed delivery retains its meaning.
"""
from copy import deepcopy
import re

from agent_service.contracts import ServiceError, digest, safe_relative_path

SCHEMA = 'video.animation-skill-compatibility.v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _require(value):
    if not value:
        raise ServiceError('animation_skill_dependency_invalid', 422)


def _assets(value, *, normalize=True):
    _require(type(value) is dict and len(value) <= 128)
    normalized = {}
    for path, sha in value.items():
        try:
            safe_relative_path(path)
        except (ValueError, TypeError):
            raise ServiceError('animation_skill_dependency_invalid', 422) from None
        _require(type(sha) is str and _SHA.fullmatch(sha))
        name = path.removeprefix('assets/') if normalize else path
        _require(name not in normalized)
        normalized[name] = sha
    return normalized


def _known_libraries():
    from .animejs_library import descriptor
    return {'animejs': descriptor()}


def _admitted(assets, declarations=None):
    if declarations is not None:
        _require(type(declarations) is dict)
    return {name: spec for name, spec in _known_libraries().items()
        if (declarations is None or declarations.get(name) == spec)
        and all(assets.get(asset) == sha for asset, sha in spec['assets'].items())}


def freeze_compatibility(task):
    _require(type(task) is dict)
    assets = _assets(task.get('assets', {}))
    # Frozen source-based production roles hard-code their earlier assets.
    # Even a misleading metadata field must not give them a new dependency.
    legacy = 'base_source' in task
    return dict(schema=SCHEMA, task_sha256=digest(task), assets=assets,
        source_contract='legacy-source' if legacy else 'asset-bound',
        authoring_libraries={} if legacy else _admitted(assets), resource_authoring={})


def validate_compatibility(value, *, task_sha256=None):
    _require(type(value) is dict and set(value) == {'schema', 'task_sha256', 'assets',
        'source_contract', 'authoring_libraries', 'resource_authoring'} and value['schema'] == SCHEMA
        and type(value['task_sha256']) is str and _SHA.fullmatch(value['task_sha256'])
        and (task_sha256 is None or value['task_sha256'] == task_sha256)
        and type(value['source_contract']) is str and value['source_contract'] in {'legacy-source', 'asset-bound'}
        and type(value['authoring_libraries']) is dict and value['resource_authoring'] == {})
    assets = _assets(value['assets'], normalize=False)
    _require(assets == value['assets'])
    for name, spec in value['authoring_libraries'].items():
        # This is an authenticated, hash-frozen ledger context. Do not compare
        # it to today's pins: recovering an old intent uses its original pins.
        _require(type(name) is str and type(spec) is dict and spec.get('library_id') == name
            and spec.get('schema') == 'video.animation-authoring-library.v1'
            and type(spec.get('assets')) is dict and bool(spec['assets'])
            and all(assets.get(asset) == sha for asset, sha in _assets(spec['assets'], normalize=False).items()))
    _require(value['source_contract'] != 'legacy-source' or not value['authoring_libraries'])
    return deepcopy(value)


def requirements_compatible(requirements, context):
    context = validate_compatibility(context)
    _require(type(requirements) is dict)
    libraries = requirements.get('authoring_libraries', {})
    resources = requirements.get('resource_authoring', {})
    _require(type(libraries) is dict and type(resources) is dict)
    _require(all(type(name) is str and type(spec) is dict for name, spec in libraries.items())
        and all(type(name) is str and type(spec) is dict for name, spec in resources.items()))
    # Manim-generated PNGs are immutable outputs for one task, not a library
    # or evidence that a consumer can run the producer for another explanation.
    if resources:
        return False
    return all(context['authoring_libraries'].get(name) == spec for name, spec in libraries.items())


def method_compatible(method, context):
    _require(type(method) is dict and type(method.get('runtime_requirements', {})) is dict)
    requirements = [method.get('runtime_requirements', {})]
    if 'skill_package' in method:
        _require(type(method['skill_package']) is dict
            and type(method['skill_package'].get('runtime_requirements')) is dict)
        requirements.append(method['skill_package']['runtime_requirements'])
    return all(requirements_compatible(row, context) for row in requirements)


def require_method_compatible(method, *, assets, authoring_libraries):
    _require(type(authoring_libraries) is dict)
    normalized = _assets(assets)
    context = dict(schema=SCHEMA, task_sha256='0' * 64, assets=normalized,
        source_contract='asset-bound', authoring_libraries=_admitted(normalized, authoring_libraries),
        resource_authoring={})
    if not method_compatible(method, context):
        raise ServiceError('animation_method_dependency_not_admitted', 409)


def legacy_method_compatible(method):
    return method_compatible(method, freeze_compatibility({'base_source': None, 'assets': {}}))
