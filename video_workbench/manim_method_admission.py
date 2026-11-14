"""Live Manim availability requires both stopped generation and v5 render proof."""
import json
from pathlib import Path
import re

from agent_service.contracts import digest
from agent_service.profiles.video_animation_manim_recipe import PROFILE
from .manim_resource import require, sha
from .manim_method_resources import helper_assets, method_resource
from .animation_method_renderer import PROJECT_RUNTIME, HYPERFRAMES_CORE_SHA256

SCHEMA = 'video.manim-method-admission.v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def build_admission(*, resource_executor, method_renderer, resource_scope, method_operation_id):
    resource = resource_executor.lookup(**resource_scope)
    require(type(resource) is dict and resource.get('status') == 'completed'
        and resource.get('stop_proven') is True and resource.get('scope') == resource_scope
        and resource.get('image') == resource_executor.image
        and type(resource.get('plan_sha256')) is str and _SHA.fullmatch(resource['plan_sha256']),
        'manim_method_generation_not_proven', 409)
    envelope = resource['generated_assets']
    descriptor, _ = method_resource(envelope, expected_sha256=envelope['sha256'],
        expected_scope=resource_scope, image_digest=resource_executor.image.rsplit('@', 1)[1])
    result = method_renderer.lookup(method_operation_id)
    require(type(result) is dict and result.get('technical_passed') is True
        and result.get('operation_id') == method_operation_id
        and result.get('technical', {}).get('status') == 'passed'
        and result.get('runtime', {}).get('generated_resources') == [descriptor]
        and result['runtime'].get('generated_resource_observation', {}).get('helpers_loaded') is True,
        'manim_method_whole_project_not_proven', 409)
    _, binding = method_renderer._row(method_operation_id, historical=True)
    request = binding['request']
    identity = method_renderer.capabilities()['runtime_identity']
    project_runtime_sha256 = sha(PROJECT_RUNTIME.read_bytes())
    require(request['schema'] == 'video.animation-method-render-request.v5'
        and binding['image'] == method_renderer.image
        and result['runtime'].get('image') == method_renderer.image
        and request['image_digest'] == identity['image_digest']
        and binding['request_sha256'] == digest(request)
        and request['host_sha256'] == identity['host_sha256']
        and request['engine_runtime_sha256'] == identity['engine_runtime_sha256']
        and request.get('project_runtime_sha256') == project_runtime_sha256
        and request.get('hyperframes_core_sha256') == HYPERFRAMES_CORE_SHA256
        and result['runtime'].get('project_runtime') == 'hyperframes-core-project-1'
        and result['runtime'].get('project_runtime_sha256') == project_runtime_sha256
        and result['runtime'].get('hyperframes_core_sha256') == HYPERFRAMES_CORE_SHA256
        and request['generated_resources'] == [descriptor], 'manim_method_runtime_changed', 409)
    evidence_sha256 = result['technical'].get('evidence_sha256')
    require(type(evidence_sha256) is str and _SHA.fullmatch(evidence_sha256)
        and result['runtime'].get('admission', {}).get('status') == 'admitted'
        and result['runtime']['admission'].get('evidence_sha256') == evidence_sha256,
        'manim_method_evidence_binding_changed', 409)
    return dict(schema=SCHEMA, recipe_profile=PROFILE, resource_image=resource_executor.image,
        method_runtime_identity=identity, helpers={name: sha(raw) for name, raw in helper_assets().items()},
        method_project_runtime_sha256=project_runtime_sha256, hyperframes_core_sha256=HYPERFRAMES_CORE_SHA256,
        resource_scope=resource_scope, resource_plan_sha256=resource['plan_sha256'],
        resource_envelope_sha256=envelope['sha256'], method_operation_id=method_operation_id,
        method_request_sha256=binding['request_sha256'], method_evidence_sha256=evidence_sha256,
        scope='technical_admission_not_visual_acceptance_or_skill_publication')


def validate_admission(path, *, resource_executor, method_renderer):
    path = Path(path).absolute()
    require(path.is_file() and not any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
        for p in (path, *path.parents)) and path.stat().st_nlink == 1 and 0 < path.stat().st_size <= 64 * 1024,
        'manim_method_admission_file_invalid', 503)
    try:
        with path.open('rb') as stream:
            raw = stream.read(64 * 1024 + 1)
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        require(False, 'manim_method_admission_file_invalid', 503)
    require(len(raw) <= 64 * 1024 and type(value) is dict and value.get('schema') == SCHEMA
        and type(value.get('resource_scope')) is dict and type(value.get('method_operation_id')) is str,
        'manim_method_admission_file_invalid', 503)
    current = build_admission(resource_executor=resource_executor, method_renderer=method_renderer,
        resource_scope=value.get('resource_scope', {}), method_operation_id=value.get('method_operation_id'))
    require(value == current, 'manim_method_admission_binding_changed', 503)
    return current
