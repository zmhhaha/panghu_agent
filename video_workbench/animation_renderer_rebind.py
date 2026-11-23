"""Explicit replay of stopped Agent source on one newly frozen renderer.

This is not new authorship or a relaxed identity check. The original creator
documents and source remain immutable; only a separately recorded render input
is derived under the current admitted runtime.
"""
from copy import deepcopy

from agent_service.contracts import ServiceError, digest
from agent_service.profiles.video_animation_create import source_sha256
from .animation_runtime_capabilities import drawing_context_budget, validate_runtime_capabilities


POLICY = 'current-admitted'
SCHEMA = 'video.animation-renderer-rebind.v1'


def require(condition, code='animation_method_renderer_rebind_not_available'):
    if not condition:
        raise ServiceError(code, 409)


def freeze_binding(run, capabilities):
    """Admit only the first failed ordinary render, never a creative restart."""
    frozen = run['frozen']
    require(run['state'] == 'failed' and not run.get('candidates') and not run.get('reviews')
        and all(op['status'] in {'completed', 'failed', 'cancelled', 'released'} for op in run['operations'])
        and not any(frozen.get(key) for key in ('collaboration', 'resource_authoring', 'resource_planning'))
        and frozen.get('role', 'reference') == 'reference')
    creators = [op for op in run['operations'] if op['stage'] == 'creator'
        and op['input'].get('mode') == 'create' and op['status'] == 'completed']
    renders = [op for op in run['operations'] if op['stage'] == 'render']
    require(len(creators) == 1 and len(renders) == 1)
    author, render = creators[0], renders[0]
    require(render['input'].get('author_operation_id') == author['id']
        and (render['status'] == 'failed' or (render['status'] == 'completed'
            and (render.get('result') or {}).get('technical', {}).get('status') == 'failed')))
    document, result = author['input'], author['result']
    require(author['input_sha256'] == digest(document) and author['result_sha256'] == digest(result),
        'animation_method_renderer_rebind_source_changed')
    source, bounds = result['source'], document['constraints']
    require(source is not None and not bounds.get('generated_resources')
        and not any(row.get('runtime_requirements', {}).get('resource_authoring')
            or row.get('skill_package', {}).get('runtime_requirements', {}).get('resource_authoring')
            for row in result['methods']))
    capabilities = validate_runtime_capabilities(capabilities)
    require(source['engine'] in capabilities['allowed_engines'],
        'animation_method_renderer_rebind_engine_not_admitted')
    require(all(capabilities['assets'].get(name) == checksum for name, checksum in document['assets'].items()),
        'animation_method_renderer_rebind_assets_changed')
    libraries = bounds.get('authoring_libraries', {})
    require(all(capabilities.get('authoring_libraries', {}).get(name) == descriptor
        for name, descriptor in libraries.items()), 'animation_method_renderer_rebind_libraries_changed')
    require(type(bounds.get('runtime_identity')) is dict,
        'animation_method_renderer_rebind_original_runtime_missing')
    return dict(schema=SCHEMA, policy=POLICY, source_commission_id=run['id'],
        source_frozen_sha256=run['frozen_sha256'], author_operation_id=author['id'],
        author_input_sha256=author['input_sha256'], author_result_sha256=author['result_sha256'],
        original_agent_job_id=author['job_id'], source_sha256=source_sha256(source),
        source_document_sha256=digest(source), engine=source['engine'],
        original_runtime_identity=deepcopy(bounds['runtime_identity']),
        runtime_identity=deepcopy(capabilities['runtime_identity']),
        assets_sha256=deepcopy(document['assets']), authoring_libraries=deepcopy(libraries),
        native_drawing_context_budget=drawing_context_budget(),
        scope='historical_agent_source_rendered_on_explicitly_rebound_runtime_not_new_authorship')


def render_input(run, author):
    """Keep authored bytes exact and bind the derived render document separately."""
    result = author['result']
    value = dict(author_operation_id=author['id'], source=deepcopy(result['source']),
        document=deepcopy(author['input']), sound_events=deepcopy(result['sound_events']))
    binding = run['frozen'].get('render_context_binding')
    if binding is None:
        return value
    require(binding.get('schema') == SCHEMA and binding.get('policy') == POLICY
        and author['input_sha256'] == binding['author_input_sha256'] == digest(author['input'])
        and author['result_sha256'] == binding['author_result_sha256'] == digest(result)
        and author['job_id'] == binding['original_agent_job_id']
        and source_sha256(result['source']) == binding['source_sha256']
        and digest(result['source']) == binding['source_document_sha256']
        and author['input']['assets'] == binding['assets_sha256']
        and author['input']['constraints'].get('authoring_libraries', {}) == binding['authoring_libraries'],
        'animation_method_renderer_rebind_source_changed')
    value['document']['constraints'].update(runtime_identity=deepcopy(binding['runtime_identity']),
        native_drawing_context_budget=deepcopy(binding['native_drawing_context_budget']))
    value['renderer_rebind_receipt'] = dict(schema='video.animation-renderer-rebind-receipt.v1',
        binding_sha256=digest(binding), source_commission_id=binding['source_commission_id'],
        original_author_operation_id=binding['author_operation_id'],
        original_agent_job_id=binding['original_agent_job_id'],
        author_input_sha256=author['input_sha256'], author_result_sha256=author['result_sha256'],
        render_input_sha256=digest(value['document']), source_sha256=binding['source_sha256'],
        original_runtime_identity=deepcopy(binding['original_runtime_identity']),
        runtime_identity=deepcopy(binding['runtime_identity']))
    return value


def review_constraints(binding):
    return dict(runtime_identity=deepcopy(binding['runtime_identity']),
        native_drawing_context_budget=deepcopy(binding['native_drawing_context_budget']),
        renderer_rebind_provenance=dict(binding_sha256=digest(binding),
            author_input_sha256=binding['author_input_sha256'], source_sha256=binding['source_sha256'],
            original_runtime_identity=deepcopy(binding['original_runtime_identity']),
            scope=binding['scope'],
            instruction='Review the supplied newly rendered media. The source was authored under the original '
                'runtime; no new creator call occurred and this replay does not prove new-runtime authorship.'))
