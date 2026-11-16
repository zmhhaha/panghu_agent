"""Narrow private transport; no raw Agent job or artifact identifiers accepted."""
from urllib.parse import unquote

from .animation_creation_http import CreationTransportError, _require
from .film_review_proxy import identity


PREFIX = '/api/film-reviews'


def route_private(handler, owner, method, path, body=None):
    if not path.startswith(PREFIX + '/'):
        return False
    service = getattr(handler.server, 'film_review_service', None)
    _require(service is not None, 'film_review_not_configured', 503)
    pieces = path[len(PREFIX):].strip('/').split('/')
    if method == 'GET' and pieces == ['capabilities']:
        handler._send(service.capabilities())
        return True
    if method == 'POST' and pieces == ['operations']:
        handler._send(service.start(owner, body), 202)
        if handler.server.film_review_pump:
            handler.server.film_review_pump.notify()
        return True
    if method == 'POST' and len(pieces) == 3 and pieces[0] == 'operations':
        operation = identity(unquote(pieces[1]))
        _require(pieces[2] in {'lookup', 'result', 'cancel'}, 'film_review_not_found', 404)
        handler._send(getattr(service, pieces[2])(owner, operation, body))
        if pieces[2] == 'cancel' and handler.server.film_review_pump:
            handler.server.film_review_pump.notify()
        return True
    raise CreationTransportError('film_review_not_found', 404)
