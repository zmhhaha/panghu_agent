"""Read-only custody checks for an explicit failed-film prefix continuation.

No module, native execution, deadline, source run or package is rewritten here.
The new journal selects original completed versions and authors only its suffix.
"""
from copy import deepcopy
import hashlib
import json

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


def _child(db, run, intent):
    _require(type(intent) is dict and intent.get('phase') == 'module'
        and type(intent.get('index')) is int and 0 <= intent['index'] < len(run['items'])
        and type(intent.get('key')) is str and not intent.get('active'))
    row = db.execute('SELECT document FROM film_module_operations WHERE owner=? AND idem=?',
        (run['owner'], intent['key'])).fetchone()
    _require(row is not None, 'production_run_module_reuse_child_missing')
    child = json.loads(row[0])
    configuration = child.get('configuration')
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
        and child.get('configuration_sha256') == run['film_creation_configuration']['configuration_sha256'])
    _require(child.get('status') in {'completed', 'failed', 'needs_input', 'cancelled', 'timed_out'}
        and child.get('stop_proven') is True,
        'production_run_module_reuse_writers_not_stopped')
    return child


def _source_prefix(runs, db, source):
    from .creative_film_module_contract import predecessor_from_package
    from .hyperframes_film_project import module_identity
    _eligible(source)
    _require(source.get('status') == 'failed' and not source.get('draining')
        and not source.get('blocked_reason') and not source.get('cancel_requested')
        and source.get('draft_render_id') is None and source.get('render_snapshot_sha256') is None
        and source.get('finished_at') is not None,
        'production_run_module_reuse_source_not_stopped')
    _require(type(source.get('intents')) is dict and all(
        type(intent) is dict and name == 'module:' + str(intent.get('index'))
        for name, intent in source['intents'].items()))
    children = {intent['index']: _child(db, source, intent) for intent in source['intents'].values()}
    _require(len(children) == len(source['intents']))
    # Orphan or ambiguous admissions cannot be hidden by deleting an intent.
    for row in db.execute('SELECT document FROM film_module_operations WHERE owner=? AND project_id=?',
            (source['owner'], source['project_id'])):
        operation = json.loads(row[0])
        if operation.get('request_key', '').startswith('production-run:' + source['id'] + ':'):
            _require(operation['id'] in {child['id'] for child in children.values()})
    for row in db.execute('SELECT document FROM film_render_operations WHERE owner=? AND project_id=?',
            (source['owner'], source['project_id'])):
        _require(json.loads(row[0]).get('request', {}).get('production_run_id') != source['id'])
    entries, previous, gap = [], None, False
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
        previous = package
    _require(0 < len(entries) < len(source['items']), 'production_run_module_reuse_prefix_invalid')
    if 'module_reuse' not in source:
        _require(source['module_targets'] == runs._module_targets(source['id'], len(source['items'])))
    return entries


def _binding(run, source):
    _require(all(run.get(key) == source.get(key) for key in ('owner', 'project_id',
        'joint_plan_version_id', 'binding_sha256', 'frozen', 'film', 'film_sha256',
        'library_binding', 'film_creation_configuration', 'resource_plan_source')))


def _stopped_children(db, source):
    return {child['id']: digest(child) for intent in source['intents'].values()
        for child in [_child(db, source, intent)]}


def _budget(run, count):
    return dict(reused_scene_modules=count, new_scene_modules=len(run['items']) - count,
        whole_film_renders=1, new_recordings=0)


def verify(runs, db, run, seen=None):
    """Fail closed before further admissions/export; never called by GET/list."""
    if 'module_reuse' not in run:
        _require('module_reuse_sha256' not in run and 'module_work_budget' not in run)
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
    _binding(run, source)
    entries = _source_prefix(runs, db, source)
    _require(type(receipt.get('prefix_count')) is int and receipt['prefix_count'] == len(entries)
        and receipt.get('items') == entries and run['module_targets'] == source['module_targets']
        and receipt.get('stopped_child_receipts') == _stopped_children(db, source)
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
        stopped_child_receipts=_stopped_children(db, source))
    run.update(module_reuse=receipt, module_reuse_sha256=digest(receipt),
        module_work_budget=_budget(run, len(entries)))
    verify(runs, db, run)
