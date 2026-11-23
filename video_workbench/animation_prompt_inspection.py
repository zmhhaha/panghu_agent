"""Read-only views of submitted requirements and real frozen Agent inputs.

The application input document is inspectable evidence. It is not a recovered
system prompt, a model transcript, or proof that a reserved task was delivered.
"""
from copy import deepcopy
import re

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_method as method_protocol
from agent_service.profiles import video_animation_skill_method as skill_protocol
from agent_service.profiles import video_animation_collaboration_contract as collaboration_protocol


SCHEMA = 'video.animation-prompt-inspection.v1'
INPUT_SCHEMA = 'video.animation-operation-input.v1'
SNAPSHOT_SCHEMA = 'video.animation-submission-snapshot.v1'
REQUEST_FIELDS = frozenset({
    'request_id', 'title', 'goal', 'reference', 'duration_seconds', 'budget',
    'base_commission_id', 'base_candidate_id', 'feedback', 'criteria', 'method_versions',
    'role', 'baseline_demonstration_id', 'structural_changes', 'retry_of_commission_id',
    'research_record_ids', 'content_changes', 'resource_authoring', 'collaboration', 'capability_selection',
    'creation_mode', 'effect_id', 'use_wikiskill', 'problem_summary', 'renderer_rebind',
})
PROTOCOLS = {row.INPUT_SCHEMA: row for row in (method_protocol, skill_protocol, collaboration_protocol)}
LIMITATIONS = [
    'This view contains frozen application JSON, not reconstructed system prompts or a model conversation.',
    'Reserved inputs may not have been delivered to an Agent; execution status is reported separately.',
    'Image inputs are represented by their frozen file references and hashes, not image bytes.',
    'Scope is the main method-lab creator/reviewer and collaboration tasks. Manim recipe and other '
    'resource-producer ledgers, worker credentials and harness configuration are not included.',
]
_SECRET_KEYS = frozenset({
    'authorization', 'proxyauthorization', 'cookie', 'setcookie', 'password', 'clientsecret',
    'accesstoken', 'refreshtoken', 'apikey', 'claimtoken', 'sshprivatekey', 'connectionstring',
})
_PATH_KEYS = frozenset({'path', 'filename', 'workspace', 'workspacepath', 'storagepath',
    'root', 'rootpath', 'cwd', 'outputpath', 'artifactpath', 'bundlepath', 'resourceroot', 'localpath'})


def require(condition, code='animation_prompt_inspection_input_unavailable', status=409):
    if not condition:
        raise ServiceError(code, status)


def _safe(value):
    """Never export execution envelopes or host-local path fields by accident.

    Validate and refuse an unsafe document rather than silently redacting it
    and labelling a modified projection as the original hash-bound input.
    Source, Skill prose and requirements remain inert authored text.
    """
    if type(value) is dict:
        for key, child in value.items():
            normalized = re.sub(r'[-_]', '', key).lower()
            require(normalized not in _SECRET_KEYS, 'animation_prompt_inspection_private_data')
            if normalized in _PATH_KEYS and type(child) is str:
                require(not re.match(r'^(?:[A-Za-z]:[\\/]|\\\\|/(?:tmp|home|root|var|srv|app|workspace|mnt|opt)(?:/|$))', child),
                        'animation_prompt_inspection_private_data')
            _safe(child)
    elif type(value) is list:
        for child in value:
            _safe(child)


def submission_snapshot(document, *, action='create'):
    require(action in {'create', 'transfer'} and type(document) is dict
            and {'request_id', 'budget'} <= set(document)
            and ('goal' in document or 'retry_of_commission_id' in document)
            and set(document) <= REQUEST_FIELDS, 'animation_prompt_inspection_submission_invalid', 422)
    return dict(schema=SNAPSHOT_SCHEMA, action=action, document=deepcopy(document))


def validate_submission_snapshot(value, *, request_id):
    require(type(value) is dict and set(value) == {'schema', 'action', 'document'}
            and value['schema'] == SNAPSHOT_SCHEMA, 'animation_prompt_inspection_submission_invalid', 422)
    checked = submission_snapshot(value['document'], action=value['action'])
    require(checked == value and checked['document']['request_id'] == request_id,
            'animation_prompt_inspection_submission_invalid', 422)
    return checked


def effective_request(run):
    document = {key: deepcopy(value) for key, value in run['frozen'].items() if key in REQUEST_FIELDS}
    document['request_id'] = run['request_id']
    _safe(document)
    return dict(document=document, sha256=digest(document))


def submission_view(run):
    snapshot = run.get('submission_snapshot')
    if snapshot is None:
        restored = effective_request(run)
        return dict(origin='reconstructed_frozen_fields', is_original=False, action='unknown',
            **restored, limitations=[
                'The original submission was not captured for this historical task. These are public '
                'fields from its frozen normalized request; omitted fields and original form choices cannot be recovered.'])
    snapshot = validate_submission_snapshot(snapshot, request_id=run['request_id'])
    require(run.get('submission_snapshot_sha256') == digest(snapshot),
            'animation_prompt_inspection_submission_changed')
    _safe(snapshot['document'])
    return dict(origin='captured_request', is_original=True, action=snapshot['action'],
                document=deepcopy(snapshot['document']), sha256=digest(snapshot['document']), limitations=[])


def validate_operation_input(operation):
    require(operation.get('stage') in {'creator', 'review'}, 'animation_prompt_inspection_operation_not_found', 404)
    document = operation.get('input')
    require(type(document) is dict and operation.get('input_sha256') == digest(document),
            'animation_method_operation_frozen_input_changed')
    protocol = PROTOCOLS.get(document.get('schema'))
    require(protocol is not None)
    try:
        protocol.validate_input(document)
    except ServiceError:
        raise ServiceError('animation_prompt_inspection_input_unavailable', 409) from None
    _safe(document)
    return deepcopy(document)


def _associations(run):
    creates = [row for row in run['operations'] if row['stage'] == 'creator'
               and row['input'].get('mode') == 'create']
    creation_rounds = {row['id']: index + 1 for index, row in enumerate(creates)}
    candidate_authors = {row['id']: row.get('provenance', {}).get('author_operation_id')
                         for row in run.get('candidates', [])}
    # Historic candidates can retain the author link only on their render op.
    render_authors = {row['id']: row['input'].get('author_operation_id') for row in run['operations']
                      if row['stage'] == 'render'}
    for candidate in run.get('candidates', []):
        if not candidate_authors[candidate['id']]:
            candidate_authors[candidate['id']] = render_authors.get(candidate.get('operation_id'))
    return creation_rounds, candidate_authors


def operation_row(run, operation):
    document = operation['input']
    collaborative = document.get('schema') == collaboration_protocol.INPUT_SCHEMA
    phase = document.get('phase')
    mode = document.get('mode') if not collaborative else phase
    creation_rounds, authors = _associations(run)
    author_id, round_number, basis = None, None, 'unresolved'
    candidate_ids = []
    if mode == 'plan':
        round_number, basis = 0, 'plan'
    elif collaborative and phase in {'component', 'integrate'}:
        round_number, basis = 1, 'single_collaboration_round'
        author_id = operation['id'] if phase == 'integrate' else None
    elif mode == 'create':
        author_id = operation['id']
        round_number, basis = creation_rounds.get(author_id), 'creation_order'
    elif mode == 'review':
        comparison = document.get('comparison') or {}
        candidate_ids = [value for value in (comparison.get('candidate_id'),
                         comparison.get('baseline_candidate_id')) if value is not None]
        author_id = authors.get(comparison.get('candidate_id'))
        round_number = creation_rounds.get(author_id)
        if round_number is not None:
            basis = 'candidate_author'
        elif run['frozen'].get('collaboration') and author_id is not None:
            author = next((row for row in run['operations'] if row['id'] == author_id), None)
            if author is not None and author['input'].get('phase') == 'integrate':
                round_number, basis = 1, 'single_collaboration_round'
    if not candidate_ids and author_id is not None:
        candidate_ids = [identity for identity, author in authors.items() if author == author_id]
    available = document.get('schema') in PROTOCOLS
    return dict(operation_id=operation['id'], stage=operation['stage'], mode=mode, phase=phase,
        scope='collaboration' if collaborative else 'method', round=round_number, round_basis=basis,
        component_id=document.get('component_id'), status=operation['status'],
        input_schema=document.get('schema'), input_sha256=operation['input_sha256'],
        input_bytes=len(canonical_bytes(document)), candidate_ids=candidate_ids,
        author_operation_id=author_id, available=available,
        input_url=('/api/method-lab/commissions/' + run['id'] + '/operations/' + operation['id'] + '/input')
                  if available else None)


def inspection_view(run):
    rows = []
    for operation in run['operations']:
        if operation['stage'] not in {'creator', 'review'}:
            continue
        row = operation_row(run, operation)
        if row['available']:
            try:
                require(operation['input_sha256'] == digest(operation['input']),
                        'animation_method_operation_frozen_input_changed')
                _safe(operation['input'])
            except ServiceError as error:
                row.update(available=False, input_url=None, unavailable_reason=error.code)
        else:
            row['unavailable_reason'] = 'unsupported_input_contract'
        rows.append(row)
    return dict(schema=SCHEMA, scope='method_lab_agent_inputs', commission_id=run['id'],
        request_id=run['request_id'], submission=submission_view(run), effective_request=effective_request(run),
        adopted_capabilities=deepcopy(run.get('adopted_capabilities')),
        operations=rows,
        renderer_rebind=deepcopy(run['frozen'].get('render_context_binding')),
        render_replays=[dict(operation_id=op['id'], status=op['status'],
            input_sha256=op['input_sha256'], receipt=deepcopy(op['input']['renderer_rebind_receipt']))
            for op in run['operations'] if op['stage'] == 'render' and op['input'].get('renderer_rebind_receipt')],
        limitations=deepcopy(LIMITATIONS))


def operation_input_view(run, operation):
    require(operation.get('commission_id') == run['id'], 'animation_prompt_inspection_operation_not_found', 404)
    document = validate_operation_input(operation)
    row = operation_row(run, operation)
    return dict(schema=INPUT_SCHEMA, commission_id=run['id'], **row,
                input=document, limitations=deepcopy(LIMITATIONS))
