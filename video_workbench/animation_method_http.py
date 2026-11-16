"""Methods-first laboratory API over the existing private executor boundary."""
from __future__ import annotations

from pathlib import Path
import json
import re
from urllib.parse import quote, unquote

from .animation_creation_http import CreationAPIClient, CreationTransportError, _require, _json, MAX_ARTIFACT, MAX_JSON

PREFIX = '/api/method-lab'
MAX_WIKISKILL_JSON = 18 * 1024 * 1024
MAX_PROMPT_INSPECTION_JSON = 6 * 1024 * 1024


def _identifier(value, *, encoded=False):
    _require(type(value) is str, 'animation_method_identifier_invalid')
    value = unquote(value) if encoded else value
    _require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}', value) is not None,
             'animation_method_identifier_invalid')
    return value


def _knowledge_revision(value):
    _require(type(value) is str and re.fullmatch(r'[1-9][0-9]{0,6}', value) is not None
             and int(value) <= 1000000, 'animation_knowledge_revision_invalid')
    return int(value)


def _send_bounded(handler, value):
    # Keep Wiki bodies out of the catalogue and fail before emitting a partial
    # response if an adapter unexpectedly exceeds the established transport.
    _require(len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode()) <= MAX_JSON,
             'animation_method_response_too_large', 413)
    handler._send(value)


def _send_wikiskill(handler, value, status=200):
    # Whole generated packages have their own finite transport budget. UTF-8
    # avoids doubling Chinese text or tripling emoji through ASCII escaping.
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    _require(len(raw) <= MAX_WIKISKILL_JSON, 'animation_wikiskill_response_too_large', 413)
    handler._send(raw, status, raw=True, mime='application/json; charset=utf-8')


def prompt_inspection_bytes(value):
    """Keep frozen input content exact and out of ordinary detail polling."""
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, UnicodeError):
        raise CreationTransportError('animation_method_prompt_inspection_invalid', 502) from None
    _require(len(raw) <= MAX_PROMPT_INSPECTION_JSON,
             'animation_method_prompt_inspection_too_large', 413)
    return raw


def _send_prompt_inspection(handler, value):
    handler._send(prompt_inspection_bytes(value), raw=True, mime='application/json; charset=utf-8')


def _wikiskill(service):
    coordinator = getattr(service, 'wikiskill', None)
    _require(coordinator is not None, 'animation_wikiskill_unavailable', 503)
    return coordinator


class MethodLabClient(CreationAPIClient):
    def _prompt_inspection_read(self, owner, path):
        raw, mime = self._request(owner, 'GET', path, artifact=True,
                                  response_limit=MAX_PROMPT_INSPECTION_JSON)
        _require(mime == 'application/json', 'animation_method_prompt_inspection_invalid', 502)
        try:
            value = _json(raw)
        except (ValueError, TypeError, UnicodeError):
            raise CreationTransportError('animation_method_prompt_inspection_invalid', 502) from None
        _require(type(value) is dict, 'animation_method_prompt_inspection_invalid', 502)
        return value

    def prompt_inspection(self, owner, commission_id):
        return self._prompt_inspection_read(owner, PREFIX + '/commissions/' +
            quote(_identifier(commission_id), safe='') + '/prompt-inspection')

    def operation_input(self, owner, commission_id, operation_id):
        return self._prompt_inspection_read(owner, PREFIX + '/commissions/' +
            quote(_identifier(commission_id), safe='') + '/operations/' +
            quote(_identifier(operation_id), safe='') + '/input')

    def _wikiskill_read(self, owner, suffix):
        raw, mime = self._request(owner, 'GET', PREFIX + '/knowledge/library' + suffix,
                                  artifact=True, response_limit=MAX_WIKISKILL_JSON)
        _require(mime == 'application/json', 'animation_wikiskill_response_invalid', 502)
        return _json(raw)

    def wikiskill_catalogue(self, owner):
        return self._wikiskill_read(owner, '')

    def wikiskill_active(self, owner):
        return self._wikiskill_read(owner, '/active')

    def wikiskill_release(self, owner, release_id):
        return self._wikiskill_read(owner, '/releases/' + quote(_identifier(release_id), safe=''))

    def wikiskill_job(self, owner, job_id):
        return self._wikiskill_read(owner, '/jobs/' + quote(_identifier(job_id), safe=''))

    def wikiskill_request(self, owner, request_id):
        return self._wikiskill_read(owner, '/requests/' + quote(_identifier(request_id), safe=''))

    def wikiskill_activation_request(self, owner, request_id):
        return self._wikiskill_read(owner, '/activation-requests/' + quote(_identifier(request_id), safe=''))

    def wikiskill_publish(self, owner, body):
        return self._request(owner, 'POST', PREFIX + '/knowledge/library/jobs', body)

    def wikiskill_activate(self, owner, body):
        return self._request(owner, 'POST', PREFIX + '/knowledge/library/active', body)
    def published_catalog(self, owner):
        return self._request(owner, 'GET', PREFIX + '/methods/published')

    def catalogue(self, owner):
        return self._request(owner, 'GET', PREFIX + '/catalog')

    def trash_catalogue(self, owner):
        return self._request(owner, 'GET', PREFIX + '/trash')

    def trash_commission(self, owner, commission_id, body):
        return self._request(owner, 'POST', PREFIX + '/commissions/' + quote(_identifier(commission_id), safe='') + '/trash', body)

    def trash_effect(self, owner, effect_id, body):
        return self._request(owner, 'POST', PREFIX + '/effects/' + quote(_identifier(effect_id), safe='') + '/trash', body)

    def restore_trash(self, owner, entry_id, body):
        return self._request(owner, 'POST', PREFIX + '/trash/' + quote(_identifier(entry_id), safe='') + '/restore', body)

    def get(self, owner, commission_id):
        return self._request(owner, 'GET', PREFIX + '/commissions/' + quote(commission_id, safe=''))

    def start(self, owner, body):
        return self._request(owner, 'POST', PREFIX + '/commissions', body)

    def action(self, owner, commission_id, action, body):
        _require(action in {'cancel', 'feedback', 'withdraw_feedback', 'publish', 'transfer'})
        return self._request(owner, 'POST', PREFIX + '/commissions/' +
                             quote(commission_id, safe='') + '/' + action, body)

    def artifact(self, owner, commission_id, candidate_id, kind):
        _require(kind in {'media', 'source'})
        return self._request(owner, 'GET', PREFIX + '/commissions/' +
            quote(commission_id, safe='') + '/candidates/' + quote(candidate_id, safe='') +
            '/' + kind, artifact=True)

    def published_methods(self, owner, body):
        return self._request(owner, 'POST', PREFIX + '/methods/read', body)

    def knowledge_catalogue(self, owner):
        return self._request(owner, 'GET', PREFIX + '/knowledge')

    def knowledge_page(self, owner, page_id):
        return self._request(owner, 'GET', PREFIX + '/knowledge/pages/' + quote(_identifier(page_id), safe=''))

    def knowledge_experience(self, owner, experience_id):
        return self._request(owner, 'GET', PREFIX + '/knowledge/experiences/' + quote(_identifier(experience_id), safe=''))

    def knowledge_page_revision(self, owner, page_id, revision):
        _require(type(revision) is int, 'animation_knowledge_revision_invalid')
        revision = _knowledge_revision(str(revision))
        return self._request(owner, 'GET', PREFIX + '/knowledge/pages/' + quote(_identifier(page_id), safe='')
                             + '/revisions/' + str(revision))

    def revise_knowledge_page(self, owner, page_id, body):
        raise CreationTransportError('animation_knowledge_manual_edit_retired', 410)

    def publication_readiness(self, owner, method_version_id):
        return self._request(owner, 'GET', PREFIX + '/methods/' + quote(_identifier(method_version_id), safe='') + '/readiness')

    def method_version(self, owner, method_version_id):
        return self._request(owner, 'GET', PREFIX + '/methods/' + quote(_identifier(method_version_id), safe=''))

    def accept_validation(self, owner, validation_id, body):
        return self._request(owner, 'POST', PREFIX + '/validations/' + quote(_identifier(validation_id), safe='') + '/accept', body)


def route_private(handler, owner, method, path, body=None):
    """Return False outside this namespace; otherwise emit one response."""
    if not (path == PREFIX or path.startswith(PREFIX + '/')):
        return False
    service = getattr(handler.server, 'method_service', None)
    if service is None and method == 'GET' and path == PREFIX + '/catalog':
        handler._send(dict(effects=[], commissions=[], availability=dict(enabled=False,
            reason='新制作角色尚未配置，等待运行验证与启用')))
        return True
    _require(service is not None, 'animation_method_service_unavailable', 503)
    pieces = path[len(PREFIX):].strip('/').split('/')
    if method == 'GET':
        if pieces == ['knowledge', 'library']:
            _send_wikiskill(handler, _wikiskill(service).catalogue(owner))
            return True
        if pieces == ['knowledge', 'library', 'active']:
            _send_wikiskill(handler, _wikiskill(service).active(owner))
            return True
        if len(pieces) == 4 and pieces[:2] == ['knowledge', 'library']:
            actions = {'jobs': 'get', 'requests': 'recover', 'releases': 'get_release',
                       'activation-requests': 'recover_activation'}
            if pieces[2] in actions:
                value = getattr(_wikiskill(service), actions[pieces[2]])(owner, _identifier(pieces[3], encoded=True))
                _send_wikiskill(handler, value)
                return True
        if pieces == ['trash']:
            _send_bounded(handler, service.trash_catalogue(owner))
            return True
        if pieces == ['knowledge']:
            _send_bounded(handler, service.knowledge_catalogue(owner))
            return True
        if len(pieces) == 3 and pieces[:2] == ['knowledge', 'pages']:
            _send_bounded(handler, service.knowledge_page(owner, _identifier(pieces[2], encoded=True)))
            return True
        if len(pieces) == 5 and pieces[:2] == ['knowledge', 'pages'] and pieces[3] == 'revisions':
            _send_bounded(handler, service.knowledge_page_revision(owner,
                _identifier(pieces[2], encoded=True), _knowledge_revision(pieces[4])))
            return True
        if len(pieces) == 3 and pieces[:2] == ['knowledge', 'experiences']:
            _send_bounded(handler, service.knowledge_experience(owner, _identifier(pieces[2], encoded=True)))
            return True
        if len(pieces) == 3 and pieces[0] == 'methods' and pieces[2] == 'readiness':
            _send_bounded(handler, service.publication_readiness(owner, _identifier(pieces[1], encoded=True)))
            return True
        if pieces == ['methods', 'published']:
            catalogue = getattr(service, 'published_catalogue', None)
            value = catalogue(owner) if callable(catalogue) else dict(methods=service.methods.list_methods(owner, published_only=True))
            _send_bounded(handler, value)
            return True
        if len(pieces) == 2 and pieces[0] == 'methods':
            _send_bounded(handler, service.get_method_version(owner, _identifier(pieces[1], encoded=True)))
            return True
        if pieces == ['catalog']:
            handler._send(service.catalogue(owner))
            return True
        if len(pieces) == 2 and pieces[0] == 'commissions':
            handler._send(service.get(owner, pieces[1]))
            return True
        if len(pieces) == 3 and pieces[0] == 'commissions' and pieces[2] == 'prompt-inspection':
            _send_prompt_inspection(handler,
                service.prompt_inspection(owner, _identifier(pieces[1], encoded=True)))
            return True
        if (len(pieces) == 5 and pieces[0] == 'commissions' and pieces[2] == 'operations'
                and pieces[4] == 'input'):
            _send_prompt_inspection(handler, service.operation_input(owner,
                _identifier(pieces[1], encoded=True), _identifier(pieces[3], encoded=True)))
            return True
        if len(pieces) == 5 and pieces[0] == 'commissions' and pieces[2] == 'candidates':
            _require(pieces[4] in {'media', 'source'})
            raw, mime = service.artifact(owner, pieces[1], pieces[3], pieces[4])
            _require(type(raw) is bytes and len(raw) <= MAX_ARTIFACT,
                     'animation_method_artifact_too_large', 413)
            handler._send(raw, raw=True, mime=mime)
            return True
    else:
        if pieces == ['knowledge', 'library', 'jobs']:
            _send_wikiskill(handler, _wikiskill(service).start(owner, body), 202)
            if getattr(handler.server, 'wikiskill_pump', None):
                handler.server.wikiskill_pump.notify()
            return True
        if pieces == ['knowledge', 'library', 'active']:
            _send_wikiskill(handler, _wikiskill(service).activate(owner, body))
            return True
        if len(pieces) == 3 and pieces[2] == 'trash' and pieces[0] in {'commissions', 'effects'}:
            action = service.trash_commission if pieces[0] == 'commissions' else service.trash_effect
            _send_bounded(handler, action(owner, _identifier(pieces[1], encoded=True), body))
            return True
        if len(pieces) == 3 and pieces[0] == 'trash' and pieces[2] == 'restore':
            _send_bounded(handler, service.restore_trash(owner, _identifier(pieces[1], encoded=True), body))
            return True
        if len(pieces) == 3 and pieces[:2] == ['knowledge', 'pages']:
            raise CreationTransportError('animation_knowledge_manual_edit_retired', 410)
        if len(pieces) == 3 and pieces[0] == 'commissions' and pieces[2] == 'publish':
            raise CreationTransportError('animation_method_publication_retired', 410)
        if len(pieces) == 3 and pieces[0] == 'validations' and pieces[2] == 'accept':
            _send_bounded(handler, service.accept_validation(owner, _identifier(pieces[1], encoded=True), body))
            return True
        if pieces == ['commissions']:
            handler._send(service.start(owner, body), 202)
            if handler.server.method_pump:
                handler.server.method_pump.notify()
            return True
        if pieces == ['methods', 'read']:
            handler._send(service.read_methods(owner, body))
            return True
        if len(pieces) == 3 and pieces[0] == 'commissions' and pieces[2] == 'separate-effect':
            # Explicit library identity correction; never starts Agent work or
            # rewrites the creation baseline, selected candidate or media.
            _send_bounded(handler, service.effect_identity.separate(
                owner, _identifier(pieces[1], encoded=True), body))
            return True
        if len(pieces) == 3 and pieces[0] == 'commissions' and pieces[2] in {
                'cancel', 'feedback', 'withdraw_feedback', 'publish', 'transfer'}:
            handler._send(getattr(service, pieces[2])(owner, pieces[1], body))
            # Developer opinions are metadata, never a reason to schedule Agent
            # work. Only execution-affecting actions wake the coordinator.
            if pieces[2] in {'cancel', 'transfer'} and handler.server.method_pump:
                handler.server.method_pump.notify()
            return True
    raise CreationTransportError('animation_method_not_found', 404)
