"""Host-estimated, bounded material work; not a wall-time or visual guarantee.

A policy is a frozen request for the actual host wrapper. Its digest is content
identity, not authority: admission must compare host-owned source, plan, runtime,
configuration, usage and budget inputs. Actual execution enforces the per-page
material noise limit; existing production accounting retains cumulative job usage.
"""
from copy import deepcopy
import re

from agent_service.contracts import ServiceError, digest
from agent_service.spatial_rendering_contract import validate_rendering_intent


POLICY_SCHEMA = 'video.scene-appearance-policy.v1'
MAX_MATERIAL_NOISE_EVALUATIONS = 64 * 1024 * 1024
ESTIMATE_SCOPE = 'host_estimated_per_page_cache_work_not_walltime_or_author_cost'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z')
_IDENTITIES = ('source_sha256', 'plan_sha256', 'surface_runtime_sha256',
               'configuration_sha256', 'usage_sha256')
_FIELDS = frozenset({'schema', 'intent', *_IDENTITIES, 'usage_lineage',
    'requested_parameters', 'actual_parameters', 'requested_capability', 'actual_capability',
    'estimated_material_samples', 'material_noise_limit', 'estimated_requested_noise_evaluations',
    'estimated_selected_noise_evaluations', 'estimate_scope', 'reason', 'scope', 'expected_loss', 'sha256'})


def _require(condition, code='scene_appearance_policy_invalid', status=422):
    if not condition:
        raise ServiceError(code, status)


def _known_usage(usage_known):
    _require(type(usage_known) is bool)
    _require(usage_known, 'scene_appearance_usage_unknown', 409)


def select_appearance_policy(intent, *, source_sha256, plan_sha256, surface_runtime_sha256,
        configuration_sha256, usage_lineage, usage_sha256, estimated_material_samples,
        material_noise_limit, usage_known=True):
    """Select only host-enforced texture detail within an existing known budget.

    Samples are a host estimate for this page's cached paint work, not a bound.
    Full uses three noise frequency bands per material sample; coarse uses one
    band and zero material normal perturbation. Neither profile changes shape,
    silhouette coverage, light interpretation, camera, output size or clocks.
    The wrapper must count real calls; an underestimated request must stop.
    """
    _known_usage(usage_known)
    checked = validate_rendering_intent(intent)
    _require(checked['engine'] == 'canvas', 'scene_appearance_engine_not_supported')
    identities = dict(source_sha256=source_sha256, plan_sha256=plan_sha256,
        surface_runtime_sha256=surface_runtime_sha256, configuration_sha256=configuration_sha256,
        usage_sha256=usage_sha256)
    _require(all(type(value) is str and _SHA.fullmatch(value) is not None
                 for value in identities.values()))
    _require(type(usage_lineage) is str and _ID.fullmatch(usage_lineage) is not None)
    _require(type(estimated_material_samples) is int
        and 0 <= estimated_material_samples <= MAX_MATERIAL_NOISE_EVALUATIONS)
    _require(type(material_noise_limit) is int
        and 1 <= material_noise_limit <= MAX_MATERIAL_NOISE_EVALUATIONS)
    requested = estimated_material_samples * 3
    quality = 'full'
    if requested > material_noise_limit:
        _require('texture' in checked['optional_features'],
            'scene_appearance_material_budget_exceeded', 409)
        _require(checked['quality']['optional_degradation'] == 'declared',
            'scene_appearance_optional_downgrade_forbidden', 409)
        _require(estimated_material_samples <= material_noise_limit,
            'scene_appearance_material_budget_exceeded', 409)
        quality = 'coarse'
    full = quality == 'full'
    result = dict(schema=POLICY_SCHEMA, intent=checked, **identities,
        usage_lineage=usage_lineage, requested_capability='texture', actual_capability='texture',
        requested_parameters=dict(material_quality='full', noise_bands=3, normal_strength='authored'),
        actual_parameters=dict(material_quality=quality, noise_bands=3 if full else 1,
            normal_strength='authored' if full else 0),
        estimated_material_samples=estimated_material_samples, material_noise_limit=material_noise_limit,
        estimated_requested_noise_evaluations=requested,
        estimated_selected_noise_evaluations=estimated_material_samples * (3 if full else 1),
        estimate_scope=ESTIMATE_SCOPE,
        reason='within_material_noise_budget' if full else 'optional_texture_budget_reduction',
        scope='per_page_cached_material_sampling',
        expected_loss=('none_from_material_profile_reduction' if full else
            'medium_and_high_frequency_material_detail_and_material_normal_perturbation_removed'))
    result['sha256'] = digest(result)
    return result


def validate_appearance_policy(value, *, intent=None, source_sha256=None, plan_sha256=None,
        surface_runtime_sha256=None, configuration_sha256=None, usage_lineage=None,
        usage_sha256=None, estimated_material_samples=None, material_noise_limit=None,
        usage_known=True):
    """Check exact decisions and, when provided, independent host expectations.

    Omitting expected values performs internal validation only, not admission.
    Never take an untrusted policy's quote or usage-known claim as host evidence.
    """
    _known_usage(usage_known)
    _require(type(value) is dict and set(value) == _FIELDS and value.get('schema') == POLICY_SCHEMA)
    _require(type(value['sha256']) is str and _SHA.fullmatch(value['sha256']) is not None
        and value['sha256'] == digest({k: v for k, v in value.items() if k != 'sha256'}),
        'scene_appearance_policy_identity_changed', 409)
    expected = dict(intent=intent, source_sha256=source_sha256, plan_sha256=plan_sha256,
        surface_runtime_sha256=surface_runtime_sha256, configuration_sha256=configuration_sha256,
        usage_lineage=usage_lineage, usage_sha256=usage_sha256,
        estimated_material_samples=estimated_material_samples, material_noise_limit=material_noise_limit)
    for key, bound in expected.items():
        if bound is not None:
            _require(value[key] == bound, 'scene_appearance_policy_binding_mismatch', 409)
    recomputed = select_appearance_policy(value['intent'],
        **{key: value[key] for key in _IDENTITIES}, usage_lineage=value['usage_lineage'],
        estimated_material_samples=value['estimated_material_samples'],
        material_noise_limit=value['material_noise_limit'], usage_known=usage_known)
    _require(value == recomputed, 'scene_appearance_policy_decision_changed', 409)
    return deepcopy(value)
