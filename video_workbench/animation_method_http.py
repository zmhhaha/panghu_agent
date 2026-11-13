"""Methods-first laboratory API over the existing private executor boundary."""
from __future__ import annotations

from pathlib import Path
import json
import re
from urllib.parse import quote, unquote

from .animation_creation_http import CreationAPIClient, CreationTransportError, _require, MAX_ARTIFACT, MAX_JSON

PREFIX = '/api/method-lab'


def _identifier(value, *, encoded=False):
    _require(type(value) is str, 'animation_method_identifier_invalid')
    value = unquote(value) if encoded else value
    _require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}', value) is not None,
             'animation_method_identifier_invalid')
    return value


def _send_bounded(handler, value):
    # Keep Wiki bodies out of the catalogue and fail before emitting a partial
    # response if an adapter unexpectedly exceeds the established transport.
    _require(len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode()) <= MAX_JSON,
             'animation_method_response_too_large', 413)
    handler._send(value)


class MethodLabClient(CreationAPIClient):
    def published_catalog(self, owner):
        return self._request(owner, 'GET', PREFIX + '/methods/published')

    def catalogue(self, owner):
        return self._request(owner, 'GET', PREFIX + '/catalog')

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

    def revise_knowledge_page(self, owner, page_id, body):
        return self._request(owner, 'POST', PREFIX + '/knowledge/pages/' + quote(_identifier(page_id), safe=''), body)

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
        if pieces == ['knowledge']:
            _send_bounded(handler, service.knowledge_catalogue(owner))
            return True
        if len(pieces) == 3 and pieces[:2] == ['knowledge', 'pages']:
            _send_bounded(handler, service.knowledge_page(owner, _identifier(pieces[2], encoded=True)))
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
        if len(pieces) == 5 and pieces[0] == 'commissions' and pieces[2] == 'candidates':
            _require(pieces[4] in {'media', 'source'})
            raw, mime = service.artifact(owner, pieces[1], pieces[3], pieces[4])
            _require(type(raw) is bytes and len(raw) <= MAX_ARTIFACT,
                     'animation_method_artifact_too_large', 413)
            handler._send(raw, raw=True, mime=mime)
            return True
    else:
        if len(pieces) == 3 and pieces[:2] == ['knowledge', 'pages']:
            _send_bounded(handler, service.revise_knowledge_page(owner, _identifier(pieces[2], encoded=True), body))
            return True
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
        if len(pieces) == 3 and pieces[0] == 'commissions' and pieces[2] in {
                'cancel', 'feedback', 'withdraw_feedback', 'publish', 'transfer'}:
            handler._send(getattr(service, pieces[2])(owner, pieces[1], body))
            # Developer opinions are metadata, never a reason to schedule Agent
            # work. Only execution-affecting actions wake the coordinator.
            if pieces[2] in {'cancel', 'transfer'} and handler.server.method_pump:
                handler.server.method_pump.notify()
            return True
    raise CreationTransportError('animation_method_not_found', 404)
