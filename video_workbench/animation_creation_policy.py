"""Explicit authoring choices; effect membership never implies inheritance."""
from copy import deepcopy

from agent_service.contracts import ServiceError, canonical_bytes


REQUEST_FIELDS = frozenset({'creation_mode', 'effect_id', 'use_wikiskill', 'problem_summary'})
MODES = frozenset({'new', 'continue', 'fresh'})


def require(value, code='animation_creation_policy_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def normalize(data):
    value = deepcopy(data)
    mode = value.setdefault('creation_mode', 'continue' if value.get('base_commission_id') else 'new')
    require(type(mode) is str and mode in MODES)
    enabled = value.setdefault('use_wikiskill', True)
    require(type(enabled) is bool, 'animation_creation_knowledge_choice_invalid')
    if mode == 'fresh':
        require(type(value.get('effect_id')) is str and bool(value['effect_id']),
                'animation_creation_effect_required')
        require(not any(value.get(key) for key in (
            'base_commission_id', 'base_candidate_id', 'method_versions',
            'baseline_demonstration_id', 'structural_changes', 'content_changes'))
            and value.get('role', 'reference') == 'reference', 'animation_creation_fresh_inheritance_forbidden')
    elif mode == 'new':
        require(not value.get('base_commission_id') and not value.get('effect_id'),
                'animation_creation_mode_conflict')
    else:
        require(bool(value.get('base_commission_id')), 'animation_creation_baseline_required')
    problems = value.get('problem_summary', [])
    require(type(problems) is list and len(problems) <= 16, 'animation_creation_problem_summary_invalid')
    for problem in problems:
        require(type(problem) is dict and {'text', 'source'} <= set(problem)
            and set(problem) <= {'text', 'source', 'source_id', 'observation'}
            and problem['source'] in ('developer', 'observation', 'hypothesis'),
            'animation_creation_problem_summary_invalid')
        for key, text in problem.items():
            require(type(text) is str and bool(text.strip()) and len(text) <= (4000 if key == 'text' else 2000)
                and not any(0xD800 <= ord(char) <= 0xDFFF for char in text),
                'animation_creation_problem_summary_invalid')
    require(len(canonical_bytes(problems)) <= 24 * 1024, 'animation_creation_problem_summary_too_large', 413)
    return value


def authoring_constraints(frozen):
    """Only the current brief and scoped observations, never old artifacts."""
    policy = dict(creation_mode=frozen.get('creation_mode', 'new'),
        use_wikiskill=frozen.get('use_wikiskill', True),
        problem_summary=deepcopy(frozen.get('problem_summary', [])))
    policy['instruction'] = (
        'Author for this current goal and explicit constraints. Record goal-specific observable criteria '
        'before creation; if none were supplied, propose them and continue without waiting for human '
        'confirmation. A problem marked hypothesis is unverified, not a mandatory fact or fixed solution. '
        'A fresh rebuild retains only effect ownership, not the old composition, source, dedicated '
        'methods, recipes or collaboration responsibilities. New work from this same experiment may '
        'be refined within its budget. Internal best selection is not default-work adoption or publication. '
        'When animation knowledge is disabled, use only current task materials and platform runtime '
        'contracts; do not recover animation-library guidance through other tools or dependencies.')
    return policy
