"""Project a creation/review host onto its exact existing execution binding.

A worker image only needs to parse its selected contract. The shared catalog
may contain historical contracts implemented by other images. This pure helper
selects raw records before constructing a registry; it never changes authority,
quota, acceptance, runtime/image identity, model settings or UID reservations.
"""
from __future__ import annotations

from copy import deepcopy

from agent_service.worker_entry import CATALOG_KEYS, HostConfig, WorkerConfigError, host_policy


PROFILES = frozenset({'video.animation-create.v2', 'video.animation-review.v2',
                      'video.animation-review.v3', 'video.animation-review.v4',
                      'video.animation-method-author.v1', 'video.animation-method-review.v1',
                      'video.animation-production.v1', 'video.animation-production.v2',
                      'video.joint-plan.v1'})


class AnimationWorkerHostError(ValueError):
    """The raw host has no unique, valid selected animation combination."""


def _require(condition, code):
    if not condition:
        raise AnimationWorkerHostError(code)


def _one(rows, fields, identity):
    _require(type(rows) is list and all(type(row) is dict for row in rows),
             'animation_worker_host_catalog_invalid')
    matches = [row for row in rows if tuple(row.get(field) for field in fields) == identity]
    _require(len(matches) == 1, 'animation_worker_host_reference_not_unique')
    return matches[0]


def _resource_identity(binding, key):
    reference = binding.get(key)
    _require(type(reference) is dict and set(reference) == {'name', 'revision'}
             and all(type(reference[field]) is str for field in reference),
             'animation_worker_host_reference_invalid')
    return reference['name'], reference['revision']


def project_animation_worker_host(document):
    """Return a deep-copied HostConfig document with exactly five selected rows.

    The binding selector and the route must agree. Every referenced identity
    must occur exactly once, including a conflicting duplicate route. Only the
    resulting closure is validated with the existing strict worker contracts;
    unsupported unrelated historical handlers are intentionally never imported.
    Acceptance evidence files remain separate, byte-for-byte operator inputs.
    """
    _require(type(document) is dict and type(document.get('execution_catalog')) is dict,
             'animation_worker_host_document_invalid')
    catalog = document['execution_catalog']
    _require(set(catalog) == CATALOG_KEYS, 'animation_worker_host_catalog_invalid')
    selector = document.get('binding_id'), document.get('binding_revision')
    _require(all(type(value) is str and value for value in selector),
             'animation_worker_host_selector_required')
    binding = _one(catalog['execution_bindings'], ('id', 'revision'), selector)
    profile = binding.get('profile'), binding.get('profile_revision')
    _require(type(profile[0]) is str and profile[0] in PROFILES and type(profile[1]) is str,
             'animation_worker_host_profile_invalid')
    route = _one(catalog['routes'], ('profile', 'profile_revision'), profile)
    _require((route.get('binding_id'), route.get('binding_revision')) == selector,
             'animation_worker_host_route_mismatch')
    selected = dict(
        task_contracts=[_one(catalog['task_contracts'], ('name', 'revision'), profile)],
        capability_bundles=[_one(catalog['capability_bundles'], ('name', 'revision'),
                                _resource_identity(binding, 'bundle'))],
        tool_environments=[_one(catalog['tool_environments'], ('name', 'revision'),
                               _resource_identity(binding, 'tool_environment'))],
        execution_bindings=[binding], routes=[route])
    result = deepcopy(document)
    result['execution_catalog'] = deepcopy(selected)
    try:
        config = HostConfig.model_validate(result)
        policy = host_policy(config)
        _require(config.engine == 'pi' and config.model is not None,
                 'animation_worker_host_model_invalid')
        model = binding['model_policy']
        _require((config.model.provider, config.model.model, config.model.api)
                 == (model['provider'], model['model'], model['api_mode']),
                 'animation_worker_host_model_mismatch')
        _require(policy.name == profile[0] and policy.execution_binding is not None,
                 'animation_worker_host_selected_invalid')
    except AnimationWorkerHostError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError, WorkerConfigError):
        raise AnimationWorkerHostError('animation_worker_host_selected_invalid') from None
    return result
