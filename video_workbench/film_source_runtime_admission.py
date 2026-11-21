"""Operator-owned admission for one exact completed film across two runtimes.

Original packages keep their authoring identity. This contract authorizes no
authors, aliases or executable bytes; target execution keeps its normal release.
"""
from copy import deepcopy
import json
from pathlib import Path
import re

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles.video_animation_production_contract import validate_runtime_identity
from .animation_runtime_capabilities import validate_runtime_capabilities
from .hyperframes_film_browser import HOST_NAMES
from .hyperframes_film_snapshot import _release

SCHEMA = 'video.film-source-runtime-admission.v1'
RECEIPT_SCHEMA = 'video.film-source-runtime-receipt.v1'
SCOPE = 'exact-completed-twelve-module-film-no-new-authors'
ERROR = 'film_source_runtime_admission_invalid'
MAX_BYTES = 512 * 1024
MODULE_FIELDS = frozenset({'scene_id', 'operation_id', 'version_id', 'package_sha256',
    'input_sha256', 'result_sha256', 'module_sha256'})
SOURCE_FIELDS = frozenset({'owner', 'project_id', 'run_id', 'run_sha256', 'film_sha256',
    'render_id', 'snapshot_sha256', 'configuration', 'runtime_identity',
    'execution_admission_sha256', 'modules'})
TARGET_FIELDS = frozenset({'image', 'runtime_identity', 'runtime_fingerprint',
    'trusted_host_sha256', 'runtime_capabilities', 'execution_admission_sha256'})
EVIDENCE_FIELDS = frozenset({'code_and_license_manifest', 'runtime_comparison',
    'source_inventory', 'capture_36point', 'whole_frame'})
CONFIGURATION_FIELDS = frozenset({'schema', 'profile', 'profile_revision',
    'bundle_manifest_sha256', 'runtime_capabilities', 'runtime_fingerprint',
    'trusted_host_sha256', 'image', 'renderer_mode', 'max_seconds', 'fps', 'sha256'})
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def require(value, code=ERROR):
    if not value:
        raise ServiceError(code, 409)


def exact(left, right):
    return canonical_bytes(left) == canonical_bytes(right)


def checksum(value):
    require(type(value) is str and _SHA.fullmatch(value))


def identity(value):
    return validate_runtime_identity(value)


def _configuration(value):
    require(type(value) is dict and CONFIGURATION_FIELDS <= set(value)
        and set(value) <= CONFIGURATION_FIELDS | {'resource_authoring'}
        and value['schema'] == 'video.film-creation-configuration.v1'
        and value['renderer_mode'] == 'hyperframes-film' and value['fps'] == 24
        and type(value['fps']) is int and value['max_seconds'] == 300)
    require(value['sha256'] == digest({k: v for k, v in value.items() if k != 'sha256'}))
    caps = validate_runtime_capabilities(value['runtime_capabilities'])
    runtime, hosts = _release(value['runtime_fingerprint'], value['image'], value['trusted_host_sha256'])
    runtime_id = caps['runtime_identity']
    require(runtime_id['image_digest'] == runtime['image_digest']
        and runtime_id['engine_runtime_sha256'] == runtime['engine_runtime_sha256']
        and runtime_id['host_sha256'] == digest(hosts))
    if 'resource_authoring' in value:
        from .manim_film_admission import validate_consumer_context
        validate_consumer_context(value['resource_authoring'], runtime=runtime, hosts=hosts, image=value['image'])
    return deepcopy(value)


def validate_policy(value):
    require(type(value) is dict and set(value) == {'schema', 'status', 'scope',
        'source', 'target', 'host_delta', 'evidence', 'sha256'}
        and value['schema'] == SCHEMA and value['status'] == 'admitted' and value['scope'] == SCOPE)
    require(len(canonical_bytes(value)) <= MAX_BYTES
        and value['sha256'] == digest({k: v for k, v in value.items() if k != 'sha256'}))
    source, target = value['source'], value['target']
    require(type(source) is dict and set(source) == SOURCE_FIELDS
        and type(target) is dict and TARGET_FIELDS <= set(target)
        and set(target) <= TARGET_FIELDS | {'resource_authoring'})
    for key in ('owner', 'project_id', 'run_id', 'render_id'):
        require(type(source[key]) is str and 0 < len(source[key]) <= 100)
    for key in ('run_sha256', 'film_sha256', 'snapshot_sha256', 'execution_admission_sha256'):
        checksum(source[key])
    checksum(target['execution_admission_sha256'])
    original = _configuration(source['configuration'])
    a, b = identity(source['runtime_identity']), identity(target['runtime_identity'])
    require(exact(a, original['runtime_capabilities']['runtime_identity'])
        and a['admission_id'] == 'hyperframes-film-execution-v1@' + source['execution_admission_sha256']
        and b['admission_id'] == 'hyperframes-film-execution-v1@' + target['execution_admission_sha256'])
    runtime, hosts = _release(target['runtime_fingerprint'], target['image'], target['trusted_host_sha256'])
    caps = validate_runtime_capabilities(target['runtime_capabilities'])
    require(exact(caps['runtime_identity'], b) and b['image_digest'] == runtime['image_digest']
        and b['host_sha256'] == digest(hosts)
        and a['engine_runtime_sha256'] == b['engine_runtime_sha256'] == runtime['engine_runtime_sha256']
        and a['image_digest'] != b['image_digest'])
    require(exact({k: v for k, v in original['runtime_fingerprint'].items() if k != 'image_digest'},
        {k: v for k, v in runtime.items() if k != 'image_digest'})
        and exact({k: v for k, v in original['runtime_capabilities'].items() if k != 'runtime_identity'},
            {k: v for k, v in caps.items() if k != 'runtime_identity'}))
    has_resources = 'resource_authoring' in original
    require(('resource_authoring' in target) is has_resources)
    if has_resources:
        from .manim_film_admission import validate_consumer_context
        before_context, after_context = original['resource_authoring'], target['resource_authoring']
        validate_consumer_context(after_context, runtime=runtime, hosts=hosts, image=target['image'])
        require(before_context['admission_sha256'] != after_context['admission_sha256']
            and exact({k: v for k, v in before_context.items() if k not in {'formal_release', 'admission_sha256'}},
                {k: v for k, v in after_context.items() if k not in {'formal_release', 'admission_sha256'}}))
    before = original['trusted_host_sha256']
    require(set(before) == set(hosts) == HOST_NAMES
        and {name for name in HOST_NAMES if before[name] != hosts[name]} == {'hyperframes_film_browser.mjs'}
        and exact(value['host_delta'], dict(file='hyperframes_film_browser.mjs',
            source_sha256=before['hyperframes_film_browser.mjs'],
            target_sha256=hosts['hyperframes_film_browser.mjs'],
            launch_flag='--disable-accelerated-2d-canvas')))
    modules = source['modules']
    require(type(modules) is list and len(modules) == 12)
    for row in modules:
        require(type(row) is dict and set(row) == MODULE_FIELDS)
        for key in ('scene_id', 'operation_id', 'version_id'):
            require(type(row[key]) is str and 0 < len(row[key]) <= 100)
        for key in MODULE_FIELDS - {'scene_id', 'operation_id', 'version_id'}:
            checksum(row[key])
    require(len({row['scene_id'] for row in modules}) == len({row['version_id'] for row in modules}) == 12)
    evidence = value['evidence']
    evidence_fields = EVIDENCE_FIELDS | ({'source_consumer_admission', 'target_consumer_admission'} if has_resources else set())
    require(type(evidence) is dict and set(evidence) == evidence_fields)
    for row in evidence.values():
        require(type(row) is dict and set(row) == {'reference', 'sha256'}
            and type(row['reference']) is str and 0 < len(row['reference']) <= 1024)
        checksum(row['sha256'])
    if has_resources:
        require(evidence['source_consumer_admission']['sha256'] == before_context['admission_sha256']
            and evidence['target_consumer_admission']['sha256'] == after_context['admission_sha256'])
    return deepcopy(value)


def validate_target_configuration(policy, configuration, *, execution_admission_sha256=None):
    policy = validate_policy(policy)
    target, source = policy['target'], policy['source']['configuration']
    require(type(configuration) is dict and configuration.get('sha256')
        == digest({k: v for k, v in configuration.items() if k != 'sha256'}))
    expected = {k: deepcopy(v) for k, v in source.items() if k != 'sha256'}
    for key in ('image', 'runtime_fingerprint', 'trusted_host_sha256', 'runtime_capabilities'):
        expected[key] = deepcopy(target[key])
    if 'resource_authoring' in source:
        expected['resource_authoring'] = deepcopy(target['resource_authoring'])
    expected['source_runtime_admission'] = policy
    require(exact({k: v for k, v in configuration.items() if k != 'sha256'}, expected),
        'film_source_runtime_target_configuration_mismatch')
    if execution_admission_sha256 is not None:
        require(execution_admission_sha256 == target['execution_admission_sha256'])
    return policy


def load_policy_file(path):
    target = Path(path)
    require(target.is_absolute() and target.is_file() and not target.is_symlink()
        and target.stat().st_size <= MAX_BYTES)
    def pairs(rows):
        value = {}
        for key, item in rows:
            require(key not in value)
            value[key] = item
        return value
    try:
        value = json.loads(target.read_bytes(), object_pairs_hook=pairs)
    except (ValueError, OSError, UnicodeError):
        raise ServiceError(ERROR, 409) from None
    return validate_policy(value)


def validate_execution_target(policy, *, image, runtime_fingerprint, trusted_host_sha256,
                              runtime_capabilities, execution_admission_sha256, resource_authoring=None):
    policy = validate_policy(policy)
    target = dict(image=image, runtime_fingerprint=runtime_fingerprint,
        trusted_host_sha256=trusted_host_sha256, runtime_capabilities=runtime_capabilities,
        runtime_identity=runtime_capabilities['runtime_identity'],
        execution_admission_sha256=execution_admission_sha256)
    if resource_authoring is not None:
        target['resource_authoring'] = resource_authoring
    require(exact(policy['target'], target),
        'film_source_runtime_target_configuration_mismatch')
    return policy


def validate_source_run(policy, source):
    policy = validate_policy(policy)
    scope = policy['source']
    require(type(source) is dict and source.get('id') == scope['run_id']
        and digest(source) == scope['run_sha256']
        and source.get('owner') == scope['owner'] and source.get('project_id') == scope['project_id']
        and source.get('film_sha256') == scope['film_sha256']
        and source.get('draft_render_id') == scope['render_id']
        and source.get('render_snapshot_sha256') in (None, scope['snapshot_sha256'])
        and source.get('status') == 'failed' and source.get('phase') == 'draft'
        and not any(source.get(k) for k in ('draining', 'blocked_reason', 'cancel_requested'))
        and source.get('audio_mode') == 'silent' and source.get('renderer_mode') == 'hyperframes-film'
        and not any(k in source for k in ('resource_plan', 'resource_authoring',
            'resource_plan_registration', 'prepared_resource_references', 'audio_reuse'))
        and source.get('film_creation_configuration', {}).get('configuration_sha256')
            == scope['configuration']['sha256'], 'film_source_runtime_source_mismatch')
    items, rows = source.get('items'), scope['modules']
    require(type(items) is list and len(items) == 12 and len(source['film']['scenes']) == 12
        and source['film']['total_seconds'] == 300 and digest(source['film']) == scope['film_sha256'])
    for item, row in zip(items, rows):
        require(item.get('module_status') == item.get('status') == 'completed'
            and item.get('scene_id') == row['scene_id'] and item.get('module_operation_id') == row['operation_id']
            and item.get('module_version_id') == row['version_id']
            and item.get('module_package_sha256') == row['package_sha256']
            and item.get('module_input_sha256') == row['input_sha256']
            and item.get('module_sha256') == row['module_sha256'], 'film_source_runtime_source_mismatch')
    return policy


def _receipt_body(policy, target_run_id, target_configuration_sha256):
    scope = policy['source']
    require(type(target_run_id) is str and 0 < len(target_run_id) <= 100
        and target_run_id != scope['run_id'])
    checksum(target_configuration_sha256)
    return dict(schema=RECEIPT_SCHEMA, policy_sha256=policy['sha256'],
        source_run_id=scope['run_id'], source_run_sha256=scope['run_sha256'],
        source_snapshot_sha256=scope['snapshot_sha256'], source_configuration_sha256=scope['configuration']['sha256'],
        source_runtime_identity=deepcopy(policy['source']['runtime_identity']),
        target_runtime_identity=deepcopy(policy['target']['runtime_identity']),
        target_run_id=target_run_id, target_configuration_sha256=target_configuration_sha256,
        owner=scope['owner'], project_id=scope['project_id'], film_sha256=scope['film_sha256'],
        module_package_sha256s=[row['package_sha256'] for row in scope['modules']],
        reused_scene_modules=12, new_scene_modules=0, new_recordings=0)


def make_receipt(policy, source_run, target_run_id, configuration):
    policy = validate_target_configuration(policy, configuration)
    validate_source_run(policy, source_run)
    value = _receipt_body(policy, target_run_id, configuration['sha256'])
    value['sha256'] = digest(value)
    return value


def validate_receipt(policy, receipt, *, target_run_id, target_configuration_sha256, film_sha256=None):
    policy = validate_policy(policy)
    expected = _receipt_body(policy, target_run_id, target_configuration_sha256)
    expected['sha256'] = digest(expected)
    require(exact(receipt, expected) and (film_sha256 is None or film_sha256 == expected['film_sha256']),
        'film_source_runtime_receipt_mismatch')
    return deepcopy(receipt)


def validate_packages(policy, receipt, packages, *, film, module_targets, target_run_id,
                      target_configuration_sha256):
    from .hyperframes_film_project import module_identity
    policy = validate_policy(policy)
    validate_receipt(policy, receipt, target_run_id=target_run_id,
        target_configuration_sha256=target_configuration_sha256, film_sha256=digest(film))
    rows = policy['source']['modules']
    require(type(packages) is list and len(packages) == len(module_targets) == len(film['scenes']) == 12
        and film['project_id'] == policy['source']['project_id'] and film['total_seconds'] == 300)
    for index, (package, row) in enumerate(zip(packages, rows)):
        document, result = package['input_document'], package['result']
        require(digest(package) == row['package_sha256'] and package['input_sha256'] == row['input_sha256']
            and package['result_sha256'] == row['result_sha256']
            and exact(document['constraints']['runtime_identity'], policy['source']['runtime_identity'])
            and document['scene']['scene_id'] == row['scene_id'] == film['scenes'][index]['scene_id']
            and document['film_context']['current_scene_index'] == index
            and exact(document['film_context']['film'], film)
            and exact(document['target'], module_targets[index])
            and document['skill_catalog']['recipient_owner'] == policy['source']['owner']
            and module_identity(result['module']) == row['module_sha256'],
            'film_source_runtime_package_scope_mismatch')
    return deepcopy(policy['source']['runtime_identity'])


def expected_child_configuration(run, phase, index=None, *, policy):
    target = run['film_creation_configuration']['configuration_sha256']
    receipt = run.get('source_runtime_admission_receipt')
    if receipt is None:
        return target
    require(policy is not None)
    validate_receipt(policy, receipt, target_run_id=run['id'],
        target_configuration_sha256=target, film_sha256=run['film_sha256'])
    require(run.get('module_work_budget') == dict(reused_scene_modules=12, new_scene_modules=0,
        whole_film_renders=1, new_recordings=0)
        and run.get('module_reuse', {}).get('source_run_id') == receipt['source_run_id'])
    if phase == 'module':
        require(type(index) is int and 0 <= index < 12)
        return receipt['source_configuration_sha256']
    require(phase == 'draft' and index is None)
    return target
