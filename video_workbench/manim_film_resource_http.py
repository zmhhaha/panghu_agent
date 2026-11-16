"""Private bounded transport for formal-film generated Manim resources."""
from urllib.parse import quote

from agent_service.contracts import ServiceError, canonical_bytes
from .animation_creation_http import CreationAPIClient, CreationTransportError, _require, _json
from .manim_generated_assets import MAX_ENVELOPE_BYTES
from .manim_film_resource_service import validate_request

PREFIX = '/api/film-manim'


class ManimFilmResourceClient(CreationAPIClient):
    def _request(self, *args, **kwargs):
        try:
            return super()._request(*args, **kwargs)
        except CreationTransportError as error:
            raise ServiceError(error.code, error.status) from None

    def capabilities(self, owner):
        return self._request(owner, 'GET', PREFIX + '/capabilities')

    def start(self, owner, request):
        validate_request(request)
        return self._request(owner, 'POST', PREFIX + '/operations', request)

    def get(self, owner, operation_id):
        return self._request(owner, 'GET', PREFIX + '/operations/' + quote(operation_id, safe=''))

    def cancel(self, owner, operation_id):
        return self._request(owner, 'POST', PREFIX + '/operations/' + quote(operation_id, safe='') + '/cancel', {})

    def cancel_reservation(self, owner, request):
        validate_request(request)
        return self._request(owner, 'POST', PREFIX + '/operations/'
            + quote(request['operation_id'], safe='') + '/cancel-reservation', request)

    def envelope(self, owner, operation_id):
        raw, mime = self._request(owner, 'GET', PREFIX + '/operations/'
            + quote(operation_id, safe='') + '/envelope', artifact=True,
            response_limit=MAX_ENVELOPE_BYTES)
        try:
            _require(mime == 'application/json', 'film_manim_envelope_content_type_invalid', 502)
            return _json(raw)
        except CreationTransportError as error:
            raise ServiceError(error.code, error.status) from None
        except (ValueError, UnicodeError):
            raise ServiceError('film_manim_envelope_invalid', 502) from None


def route_private(handler, owner, method, path, body=None):
    if not (path == PREFIX or path.startswith(PREFIX + '/')):
        return False
    service = getattr(handler.server, 'film_resource_service', None)
    pieces = path[len(PREFIX):].strip('/').split('/')
    if method == 'GET' and pieces == ['capabilities'] and service is None:
        handler._send(dict(schema='video.manim-film-resource-capabilities.v1', enabled=False,
            status='not_configured', resource_authoring={}))
        return True
    _require(service is not None, 'film_manim_not_configured', 503)
    if method == 'GET':
        if pieces == ['capabilities']:
            handler._send(service.capabilities(owner))
            return True
        if len(pieces) == 2 and pieces[0] == 'operations':
            handler._send(service.get(owner, pieces[1]))
            return True
        if len(pieces) == 3 and pieces[0] == 'operations' and pieces[2] == 'envelope':
            value = service.envelope(owner, pieces[1])
            raw = canonical_bytes(value)
            _require(len(raw) <= MAX_ENVELOPE_BYTES, 'film_manim_envelope_too_large', 413)
            handler._send(raw, raw=True, mime='application/json')
            return True
    elif method == 'POST':
        if pieces == ['operations']:
            handler._send(service.start(owner, body), 202)
            if handler.server.film_resource_pump:
                handler.server.film_resource_pump.notify()
            return True
        if len(pieces) == 3 and pieces[0] == 'operations' and pieces[2] == 'cancel':
            _require(body == {}, 'film_manim_cancel_request_invalid')
            handler._send(service.cancel(owner, pieces[1]))
            if handler.server.film_resource_pump:
                handler.server.film_resource_pump.notify()
            return True
        if len(pieces) == 3 and pieces[0] == 'operations' and pieces[2] == 'cancel-reservation':
            _require(type(body) is dict and body.get('operation_id') == pieces[1],
                'film_manim_cancel_request_invalid')
            handler._send(service.cancel_reservation(owner, body))
            if handler.server.film_resource_pump:
                handler.server.film_resource_pump.notify()
            return True
    raise CreationTransportError('film_manim_not_found', 404)
