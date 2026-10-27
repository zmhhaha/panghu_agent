"""Trusted phase budgets shared by admission and isolated execution backends.

The database deadline is assigned once when a job is claimed. These limits
never renew that deadline and do not authorize retries or phase transitions.
"""
from types import MappingProxyType


PHASE_TIMEOUT_SECONDS = MappingProxyType({
    'plan_initial': 600,
    'plan_options': 1200,
    'prepare': 1800,
    'generate': 1800,
    'revise': 1800,
})
POD_STOP_ALLOWANCE_SECONDS = 120


def phase_timeout_seconds(phase: str) -> int:
    try:
        return PHASE_TIMEOUT_SECONDS[phase]
    except (KeyError, TypeError):
        raise ValueError('Unsupported execution phase') from None


def phase_pod_timeout_seconds(phase: str) -> int:
    return phase_timeout_seconds(phase) + POD_STOP_ALLOWANCE_SECONDS
