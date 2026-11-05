"""Exact production scene contracts without reinterpreting frozen compilers."""
from agent_service.contracts import ServiceError

from .motion import COMPILER_TEMPLATES, COMPILER_VERSION, compile_project, resolve_timeline
from .motion_process import compile_process_project, resolve_process_timeline


PROCESS_SCHEMA = 'video.process-plan.v1'
PRODUCTION_PROCESS_COMPILER = 'motion-process-compiler-2'
_CURRENT = object()


def scene_compiler(plan, *, compiler_version=_CURRENT):
    """Return the exact resolver/compiler/version for a new or frozen scene.

    Omitting a version means a new candidate. Passing even ``None`` means an
    explicit frozen version, which must be admitted exactly. Process compiler 1
    remains a historical local experiment and is not a production contract.
    """
    schema = plan.get('schema') if type(plan) is dict else None
    if schema == PROCESS_SCHEMA:
        version = PRODUCTION_PROCESS_COMPILER if compiler_version is _CURRENT else compiler_version
        if version == PRODUCTION_PROCESS_COMPILER:
            return resolve_process_timeline, compile_process_project, version
    elif schema == 'video.motion-plan.v1':
        version = COMPILER_VERSION if compiler_version is _CURRENT else compiler_version
        if type(version) is str and version in COMPILER_TEMPLATES:
            return resolve_timeline, compile_project, version
    raise ServiceError('motion_compiler_schema_mismatch', 422)
