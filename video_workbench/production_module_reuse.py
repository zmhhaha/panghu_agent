"""Read-only custody checks for an explicit failed-film prefix continuation.

No module, native execution, deadline, source run or package is rewritten here.
The new journal selects original completed versions and authors only its suffix,
or retries one stopped failed whole-film export when every module is complete.
"""
from copy import deepcopy
import hashlib
import json
import math

from agent_service.contracts import digest

SCHEMA = 'video.production-module-prefix-reuse.v1'
MAX_LINEAGE = 16
ERROR = 'production_run_module_reuse_mismatch'


def _require(value, code=ERROR):
    from .production_run_store import require
    require(value, code, 409)


def _eligible(run):
    _require(run.get('renderer_mode') == 'hyperframes-film'
        and run.get('audio_mode') == 'silent' and run.get('continuity_schema') == 'video.film-context.v1'
        and run.get('film', {}).get('schema') == 'video.hyperframes-film-clock.v1'
        and not any(key in run for key in ('resource_plan', 'resource_authoring',
            'resource_plan_registration', 'prepared_resource_references', 'audio_reuse')),
        'production_run_module_reuse_unsupported')
    _require(type(run.get('items')) is list and type(run.get('frozen')) is dict
        and digest(run['frozen']) == run.get('binding_sha256')
        and digest(run['film']) == run.get('film_sha256')
        and run['film']['content_binding_sha256'] == run['binding_sha256']
        and len(run['items']) == len(run['film']['scenes']) == len(run.get('module_targets', [])))
    for item in run['items']:
        from .production_run_store import ProductionRunStore
        ProductionRunStore._require_silent_item(item)
        _require(item.get('motion_operation_id') is None and item.get('motion_version_id') is None)


def _child(db, run, intent, *, policy=None):
    _require(type(intent) is dict and intent.get('phase') == 'module'
        and type(intent.get('index')) is int and 0 <= intent['index'] < len(run['items'])
        and type(intent.get('key')) is str and not intent.get('active'))
    row = db.execute('SELECT document FROM film_module_operations WHERE owner=? AND idem=?',
        (run['owner'], intent['key'])).fetchone()
    _require(row is not None, 'production_run_module_reuse_child_missing')
    child = json.loads(row[0])
    configuration = child.get('configuration')
    configuration_sha256 = run['film_creation_configuration']['configuration_sha256']
    if 'source_runtime_admission_receipt' in run:
        from .film_source_runtime_admission import expected_child_configuration
        configuration_sha256 = expected_child_configuration(run, 'module', intent['index'], policy=policy)
    _require(type(configuration) is dict
        and digest({key: value for key, value in configuration.items() if key != 'sha256'})
            == configuration.get('sha256') == child.get('configuration_sha256')
        and child.get('library_binding') == run.get('library_binding')
        and child.get('request', {}).get('library_binding') == run.get('library_binding'))
    _require(child.get('owner') == run['owner'] and child.get('project_id') == run['project_id']
        and child.get('scene_id') == run['items'][intent['index']]['scene_id']
        and child.get('request_key') == intent['key']
        and child.get('request_sha256') == intent.get('request_sha256')
        and child.get('request_sha256') == digest(child['request'])
        and child.get('film_sha256') == run['film_sha256']
        and child.get('configuration_sha256') == configuration_sha256)
    _require(child.get('status') in {'completed', 'failed', 'needs_input', 'cancelled', 'timed_out'}
        and child.get('stop_proven') is True,
        'production_run_module_reuse_writers_not_stopped')
    return child


def _failed_render(db, source, intent):
    """Read the sole failed export without changing its request or snapshot."""
    from .hyperframes_film_snapshot import decode_film_execution_snapshot
    _require(type(intent) is dict and intent.get('phase') == 'draft'
        and intent.get('index') is None and not intent.get('active')
        and intent.get('key') == 'production-run:' + source['id'] + ':draft'
        and type(intent.get('request')) is dict and set(intent['request']) == {'expected_revision'})
    row = db.execute('SELECT id,owner,project_id,idem,request_hash,document,snapshot '
        'FROM film_render_operations WHERE owner=? AND idem=?',
        (source['owner'], intent['key'])).fetchone()
    _require(row is not None, 'production_run_module_reuse_child_missing')
    child = json.loads(row[5])
    configuration, request = child.get('configuration'), child.get('request')
    receipt = source.get('source_runtime_admission_receipt')
    request_fields = {'expected_revision', 'film', 'film_sha256', 'module_version_ids',
        'module_targets', 'production_run_id', 'timeout_seconds', 'configuration_sha256'}
    if receipt is not None:
        request_fields.add('source_runtime_admission_receipt')
    _require(type(configuration) is dict and type(request) is dict
        and set(request) == request_fields
        and digest({key: value for key, value in configuration.items() if key != 'sha256'})
            == configuration.get('sha256') == child.get('configuration_sha256')
        and child.get('configuration_sha256') == source['film_creation_configuration']['configuration_sha256'])
    _require(tuple(row[:4]) == (child.get('id'), source['owner'], source['project_id'], intent['key'])
        and row[4] == digest(dict(project_id=source['project_id'], request=request))
        and child.get('kind') == 'render' and child.get('id') == source.get('draft_render_id')
        and child.get('owner') == source['owner'] and child.get('project_id') == source['project_id']
        and child.get('request_key') == intent['key']
        and child.get('request_sha256') == intent.get('request_sha256') == digest(request)
        and request.get('expected_revision') == intent['request'].get('expected_revision')
        and request.get('production_run_id') == source['id']
        and request.get('film') == source['film']
        and request.get('film_sha256') == child.get('film_sha256') == source['film_sha256']
        and request.get('configuration_sha256') == child['configuration_sha256']
        and request.get('module_version_ids') == [item['module_version_id'] for item in source['items']]
        and request.get('module_targets') == source['module_targets'])
    if receipt is not None:
        from .film_source_runtime_admission import validate_target_configuration, validate_receipt
        policy = configuration.get('source_runtime_admission')
        validate_target_configuration(policy, configuration)
        validate_receipt(policy, receipt, target_run_id=source['id'],
            target_configuration_sha256=configuration['sha256'], film_sha256=source['film_sha256'])
        _require(request.get('source_runtime_admission_receipt') == receipt)
    _require(child.get('status') == 'failed' and child.get('stop_proven') is True
        and not child.get('cancel_requested') and child.get('finished_at') is not None
        and child.get('result') is None
        and all(type(child.get(key)) in {int, float} and math.isfinite(child[key]) and child[key] > 0
            for key in ('created_at', 'deadline_at', 'finished_at'))
        and child['deadline_at'] == child['created_at'] + request['timeout_seconds']
        and child['finished_at'] >= child['created_at'],
        'production_run_module_reuse_writers_not_stopped')
    snapshot = json.loads(row[6])
    files = decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=child['snapshot_sha256'],
        runtime_fingerprint=configuration['runtime_fingerprint'],
        trusted_host_sha256=configuration['trusted_host_sha256'], image=configuration['image'])
    _require(snapshot['production_run_id'] == source['id'] and snapshot['project_id'] == source['project_id']
        and snapshot['film_sha256'] == source['film_sha256']
        and snapshot['project_revision'] == request['expected_revision']
        and snapshot['timeout_seconds'] == request.get('timeout_seconds')
        and snapshot['clock']['authority'] == 'explicit_silent_intervals'
        and snapshot['recording_sha256'] == {}
        and source.get('render_snapshot_sha256') in (None, snapshot['sha256']))
    return child, snapshot, files


def _source_prefix(runs, db, source):
    from .creative_film_module_contract import predecessor_from_package
    from .hyperframes_film_project import module_identity
    _eligible(source)
    _require(source.get('status') == 'failed' and not source.get('draining')
        and not source.get('blocked_reason') and not source.get('cancel_requested')
        and source.get('finished_at') is not None,
        'production_run_module_reuse_source_not_stopped')
    _require(type(source.get('intents')) is dict and all(
        type(intent) is dict and (name == 'draft' or name == 'module:' + str(intent.get('index')))
        for name, intent in source['intents'].items()))
    module_intents = [intent for name, intent in source['intents'].items() if name != 'draft']
    policy = (runs._film_service().source_runtime_policy()
        if 'source_runtime_admission_receipt' in source else None)
    children = {intent['index']: _child(db, source, intent, policy=policy) for intent in module_intents}
    _require(len(children) == len(module_intents))
    # Orphan or ambiguous admissions cannot be hidden by deleting an intent.
    for row in db.execute('SELECT document FROM film_module_operations WHERE owner=? AND project_id=?',
            (source['owner'], source['project_id'])):
        operation = json.loads(row[0])
        if operation.get('request_key', '').startswith('production-run:' + source['id'] + ':'):
            _require(operation['id'] in {child['id'] for child in children.values()})
    renders = [json.loads(row[0]) for row in db.execute(
        'SELECT document FROM film_render_operations WHERE owner=? AND project_id=?',
        (source['owner'], source['project_id']))]
    renders = [child for child in renders if child.get('request', {}).get('production_run_id') == source['id']
        or child.get('request_key', '').startswith('production-run:' + source['id'] + ':')]
    entries, packages, assets, previous, gap = [], [], {}, None, False
    for index, item in enumerate(source['items']):
        child = children.get(index)
        _require(item.get('module_operation_id') == (child['id'] if child else None))
        if not item.get('module_version_id'):
            gap = True
            _require((child is None or child.get('version_id') is None)
                and all(item.get(key) is None for key in
                    ('module_package_sha256', 'module_input_sha256', 'module_sha256', 'module_engine')))
            continue
        _require(not gap and item.get('status') == item.get('module_status') == 'completed'
            and child is not None and child.get('status') == 'completed'
            and child.get('error') is None and not child.get('cancel_requested')
            and child.get('version_id') == item['module_version_id'])
        material = runs._film_service().module_reuse_material(db, source['owner'], item['module_version_id'])
        version, package, available = (material[key] for key in ('version', 'package', 'assets'))
        document, module = package['input_document'], package['result']['module']
        context = document['film_context']
        delivery = package['skill_delivery']
        binding = source.get('library_binding')
        if binding is not None:
            _require([row.get('library_binding') for row in delivery['retrieval_receipts']
                if row.get('source') == 'authenticated_wikiskill_release'] == [binding])
            if binding.get('use_wikiskill') is True:
                _require(all(row['publication_sha256'] == binding['release_sha256']
                    and row['method_version_id'].startswith('wikiskill:' + binding['release_id'] + ':')
                    for row in delivery['catalog']['skills']))
        _require(all(name in available and hashlib.sha256(available[name]).hexdigest() == checksum
            for name, checksum in document['assets'].items()))
        _require(document['constraints']['runtime_identity']
            == child['configuration']['runtime_capabilities']['runtime_identity']
            and delivery['recipient_owner'] == source['owner']
            and delivery['consumer_id'] == 'video-film-module:' + child['id'])
        _require(version['operation_id'] == child['id'] and version['owner'] == source['owner']
            and version['project_id'] == source['project_id'] and version['scene_id'] == item['scene_id']
            and version['film_sha256'] == source['film_sha256']
            and digest(package) == version['package_sha256'] == item.get('module_package_sha256')
            and child.get('document') == document
            and child.get('input_sha256') == package['input_sha256'] == item.get('module_input_sha256')
            and context['film'] == source['film'] and context['film_sha256'] == source['film_sha256']
            and context['current_scene_index'] == index
            and context['predecessor'] == (None if previous is None else predecessor_from_package(previous))
            and document['target'] == source['module_targets'][index]
            and document['scene']['scene_id'] == item['scene_id']
            and module_identity(module) == item.get('module_sha256')
            and module['engine'] == item.get('module_engine'))
        entries.append(dict(index=index, scene_id=item['scene_id'], operation_id=child['id'],
            version_id=version['id'], package_sha256=digest(package), input_sha256=package['input_sha256'],
            module_sha256=module_identity(module),
            source_sha256=deepcopy(module['files_sha256']),
            operation_receipt_sha256=digest(child), version_receipt_sha256=digest(version)))
        packages.append(package)
        for name, raw in available.items():
            _require(name not in assets or assets[name] == raw)
            assets[name] = raw
        previous = package
    _require(0 < len(entries) <= len(source['items']), 'production_run_module_reuse_prefix_invalid')
    if len(entries) < len(source['items']):
        _require('draft' not in source['intents'] and not renders
            and source.get('draft_render_id') is None and source.get('render_snapshot_sha256') is None,
            'production_run_module_reuse_source_not_stopped')
    else:
        from .film_production_assembly import assemble_film_production
        from .hyperframes_film_browser import HOST_NAMES
        child, snapshot, files = _failed_render(db, source, source['intents'].get('draft'))
        _require(source.get('phase') == 'draft' and len(renders) == 1 and renders[0] == child)
        configuration, request = child['configuration'], child['request']
        # Reassemble only in memory. Equality covers the original scene AND
        # transition sources, boundaries, clock, declared assets and host bytes.
        assembled = assemble_film_production(packages=packages, film=source['film'],
            film_sha256=source['film_sha256'], recordings=[], assets=assets,
            runtime_fingerprint=configuration['runtime_fingerprint'],
            runtime_identity=configuration['runtime_capabilities']['runtime_identity'],
            module_targets=source['module_targets'],
            host_files={name: files['host/' + name] for name in HOST_NAMES},
            trusted_host_sha256=configuration['trusted_host_sha256'], image=configuration['image'],
            project_revision=request['expected_revision'], production_run_id=source['id'],
            timeout_seconds=request['timeout_seconds'],
            source_runtime_admission=configuration.get('source_runtime_admission')
                if request.get('source_runtime_admission_receipt') is not None else None,
            source_runtime_admission_receipt=request.get('source_runtime_admission_receipt'),
            target_configuration_sha256=configuration['sha256'])
        _require(assembled['snapshot'] == snapshot and assembled['manifest'] == child.get('assembly_manifest'))
    if 'module_reuse' not in source:
        _require(source['module_targets'] == runs._module_targets(source['id'], len(source['items'])))
    return entries


def _binding(run, source):
    _require(all(run.get(key) == source.get(key) for key in ('owner', 'project_id',
        'joint_plan_version_id', 'binding_sha256', 'frozen', 'film', 'film_sha256',
        'library_binding', 'resource_plan_source')))
    if 'source_runtime_admission_receipt' not in run:
        _require(run.get('film_creation_configuration') == source.get('film_creation_configuration'))
        return
    receipt = run['source_runtime_admission_receipt']
    before, after = source['film_creation_configuration'], run['film_creation_configuration']
    _require(receipt['source_run_id'] == source['id']
        and receipt['source_configuration_sha256'] == before['configuration_sha256']
        and receipt['target_configuration_sha256'] == after['configuration_sha256']
        and before['configuration_sha256'] != after['configuration_sha256']
        and {key: value for key, value in before.items() if key != 'configuration_sha256'}
            == {key: value for key, value in after.items() if key != 'configuration_sha256'})


def _source_runtime_receipt(runs, db, run, source, *, attaching=False):
    """The operator policy alone may authorize this single configuration edge."""
    present = 'source_runtime_admission_receipt' in run
    same = run.get('film_creation_configuration') == source.get('film_creation_configuration')
    if same and not present:
        # A bridged export is not another alias source. A retry must select
        # the one original A run named by the operator's exact pair.
        _require('source_runtime_admission_receipt' not in source)
        return
    _require(not same and (present or attaching))
    expected = runs._film_service().authorize_source_runtime_reuse(db, run['owner'], source, run['id'])
    _require(expected['target_configuration_sha256']
        == run['film_creation_configuration']['configuration_sha256'])
    if attaching:
        _require(not present)
        run['source_runtime_admission_receipt'] = expected
    else:
        _require(run['source_runtime_admission_receipt'] == expected,
            'film_source_runtime_receipt_mismatch')


def _stopped_children(runs, db, source):
    policy = (runs._film_service().source_runtime_policy()
        if 'source_runtime_admission_receipt' in source else None)
    return {child['id']: digest(child) for intent in source['intents'].values()
        for child in [_failed_render(db, source, intent)[0] if intent.get('phase') == 'draft'
            else _child(db, source, intent, policy=policy)]}


def _budget(run, count):
    return dict(reused_scene_modules=count, new_scene_modules=len(run['items']) - count,
        whole_film_renders=1, new_recordings=0)


def verify(runs, db, run, seen=None):
    """Fail closed before further admissions/export; never called by GET/list."""
    if 'module_reuse' not in run:
        _require('module_reuse_sha256' not in run and 'module_work_budget' not in run
            and 'source_runtime_admission_receipt' not in run)
        return []
    receipt = run['module_reuse']
    seen = set() if seen is None else set(seen)
    _require(run['id'] not in seen and len(seen) < MAX_LINEAGE,
        'production_run_module_reuse_lineage_invalid')
    seen.add(run['id'])
    _eligible(run)
    _require(type(receipt) is dict and set(receipt) == {'schema', 'source_run_id', 'source_run_sha256',
        'prefix_count', 'lineage_run_ids', 'items', 'stopped_child_receipts'} and receipt.get('schema') == SCHEMA
        and digest(receipt) == run.get('module_reuse_sha256'))
    source_id = receipt.get('source_run_id')
    _require(type(source_id) is str and source_id not in seen and len(seen) < MAX_LINEAGE,
        'production_run_module_reuse_lineage_invalid')
    source = runs._read(db, source_id, run['owner'])
    _require(digest(source) == receipt.get('source_run_sha256'))
    chain = [source_id, *verify(runs, db, source, seen)]
    _require(chain == receipt.get('lineage_run_ids') and len(chain) < MAX_LINEAGE
        and len(chain) == len(set(chain)), 'production_run_module_reuse_lineage_invalid')
    _source_runtime_receipt(runs, db, run, source)
    _binding(run, source)
    entries = _source_prefix(runs, db, source)
    _require(type(receipt.get('prefix_count')) is int and receipt['prefix_count'] == len(entries)
        and receipt.get('items') == entries and run['module_targets'] == source['module_targets']
        and receipt.get('stopped_child_receipts') == _stopped_children(runs, db, source)
        and run.get('module_work_budget') == _budget(run, len(entries)))
    for entry in entries:
        index = entry['index']
        _require(run['items'][index] == source['items'][index]
            and run['intents'].get('module:' + str(index)) == source['intents'].get('module:' + str(index)))
    return chain


def attach(runs, db, run, source_id):
    """Select the complete nonempty prefix under the caller's admission lock."""
    _eligible(run)
    source = runs._read(db, source_id, run['owner'])
    _source_runtime_receipt(runs, db, run, source, attaching=True)
    _binding(run, source)
    chain = [source_id, *verify(runs, db, source, {run['id']})]
    _require(len(chain) < MAX_LINEAGE and len(chain) == len(set(chain))
        and run['id'] not in chain, 'production_run_module_reuse_lineage_invalid')
    project = runs.store._project(db, run['owner'], run['project_id'])
    _require(runs._matches(source, project, db), 'production_run_module_reuse_source_stale')
    entries = _source_prefix(runs, db, source)
    run['module_targets'] = deepcopy(source['module_targets'])
    for entry in entries:
        index = entry['index']
        run['items'][index] = deepcopy(source['items'][index])
        name = 'module:' + str(index)
        run['intents'][name] = deepcopy(source['intents'][name])
    receipt = dict(schema=SCHEMA, source_run_id=source_id, source_run_sha256=digest(source),
        prefix_count=len(entries), lineage_run_ids=chain, items=entries,
        stopped_child_receipts=_stopped_children(runs, db, source))
    run.update(module_reuse=receipt, module_reuse_sha256=digest(receipt),
        module_work_budget=_budget(run, len(entries)))
    verify(runs, db, run)
