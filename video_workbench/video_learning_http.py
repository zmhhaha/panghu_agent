"""Owner-scoped video library over the existing authenticated lab boundary."""
from pathlib import Path
from urllib.parse import quote
import re
import json
from agent_service.contracts import digest

from .animation_creation_http import CreationAPIClient, _require, MAX_ARTIFACT

PREFIX = '/api/video-lab'
MAX_BODY = 36 * 1024 * 1024
MAX_DETAIL = 32 * 1024 * 1024


def identifier(value):
    _require(type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}', value),
        'video_learning_identifier_invalid', 422)
    return quote(value, safe='')


class VideoLabClient(CreationAPIClient):
    def video_catalogue(self, owner):
        from .animation_creation_http import _json
        raw, mime = self._request(owner, 'GET', PREFIX + '/catalog', artifact=True,
            response_limit=MAX_DETAIL)
        _require(mime == 'application/json', 'video_learning_catalogue_invalid', 502)
        return _json(raw)

    def video_upload(self, owner, body):
        return self._request(owner, 'POST', PREFIX + '/references', body)

    def video_reference(self, owner, reference_id):
        return self._request(owner, 'GET', PREFIX + '/references/' + identifier(reference_id) + '/media', artifact=True)

    def video_start(self, owner, body):
        return self._request(owner, 'POST', PREFIX + '/projects', body)

    def video_get(self, owner, project_id):
        from .animation_creation_http import _json
        raw, mime = self._request(owner, 'GET', PREFIX + '/projects/' + identifier(project_id),
            artifact=True, response_limit=MAX_DETAIL)
        _require(mime == 'application/json', 'video_learning_detail_invalid', 502)
        return _json(raw)

    def video_inspect(self, owner, project_id, experiment_id):
        return self._request(owner, 'GET', PREFIX + '/projects/' + identifier(project_id)
            + '/experiments/' + identifier(experiment_id) + '/input', artifact=True,
            response_limit=MAX_DETAIL)

    def video_action(self, owner, project_id, action, body):
        _require(action in {'experiments', 'stop', 'adopt'}, 'video_learning_action_invalid')
        return self._request(owner, 'POST', PREFIX + '/projects/' + identifier(project_id) + '/' + action, body)

    def video_artifact(self, owner, project_id, artifact_id):
        return self._request(owner, 'GET', PREFIX + '/projects/' + identifier(project_id) + '/artifacts/'
            + identifier(artifact_id), artifact=True)

    def video_analyze(self, owner, reference_id, body):
        return self._request(owner, 'POST', PREFIX + '/references/' + identifier(reference_id) + '/analyses', body)

    def video_analysis(self, owner, reference_id, analysis_id):
        return self._request(owner, 'GET', PREFIX + '/references/' + identifier(reference_id)
            + '/analyses/' + identifier(analysis_id))

    def video_analysis_stop(self, owner, reference_id, analysis_id, body):
        return self._request(owner, 'POST', PREFIX + '/references/' + identifier(reference_id)
            + '/analyses/' + identifier(analysis_id) + '/stop', body)

    def video_analysis_frame(self, owner, reference_id, analysis_id, frame_sha256):
        return self._request(owner, 'GET', PREFIX + '/references/' + identifier(reference_id)
            + '/analyses/' + identifier(analysis_id) + '/frames/' + identifier(frame_sha256), artifact=True)


def _artifact(handler, value):
    path, mime = value
    path = Path(path)
    _require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_ARTIFACT,
        'video_learning_artifact_unavailable', 409)
    handler._send(path.read_bytes(), raw=True, mime=mime)


def _send_detail(handler, value):
    raw = json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',', ':')).encode('utf-8')
    _require(len(raw) <= MAX_DETAIL, 'video_learning_detail_too_large', 413)
    handler._send(raw,raw=True,mime='application/json; charset=utf-8')


def route_private(handler, owner, method, path, body=None):
    if path != PREFIX and not path.startswith(PREFIX + '/'):
        return False
    service = getattr(handler.server, 'video_learning_service', None)
    _require(service is not None, 'video_learning_unavailable', 503)
    pieces = path[len(PREFIX):].strip('/').split('/')
    for value in pieces:
        identifier(value)
    if method == 'GET':
        if pieces == ['catalog']:
            handler._send(service.catalogue(owner))
        elif len(pieces) == 3 and pieces[0] == 'references' and pieces[2] == 'media':
            _artifact(handler, service.reference_artifact(owner, pieces[1]))
        elif len(pieces) == 4 and pieces[0] == 'references' and pieces[2] == 'analyses':
            handler._send(service.get_analysis(owner, pieces[1], pieces[3]))
        elif len(pieces) == 6 and pieces[0] == 'references' and pieces[2] == 'analyses' and pieces[4] == 'frames':
            _artifact(handler, service.analysis_reference_artifact(owner, pieces[1], pieces[3], pieces[5]))
        elif len(pieces) == 2 and pieces[0] == 'projects':
            _send_detail(handler, service.get(owner, pieces[1]))
        elif len(pieces) == 5 and pieces[0] == 'projects' and pieces[2] == 'experiments' and pieces[4] == 'input':
            _send_detail(handler, service.inspect(owner, pieces[1], pieces[3]))
        elif len(pieces) == 4 and pieces[0] == 'projects' and pieces[2] == 'artifacts':
            _artifact(handler, service.artifact(owner, pieces[1], pieces[3]))
        else:
            _require(False, 'video_learning_not_found', 404)
        return True
    if method == 'POST':
        if pieces == ['references']:
            handler._send(service.upload_reference(owner, body), 201)
        elif len(pieces) == 3 and pieces[0] == 'references' and pieces[2] == 'analyses':
            handler._send(service.analyze_reference(owner, pieces[1], body), 202)
        elif len(pieces) == 5 and pieces[0] == 'references' and pieces[2] == 'analyses' and pieces[4] == 'stop':
            _require(body == {}, 'video_learning_analysis_stop_invalid', 422)
            handler._send(service.stop_analysis(owner, pieces[1], pieces[3]))
        elif pieces == ['projects']:
            handler._send(service.start(owner, body), 202)
        elif len(pieces) == 3 and pieces[0] == 'projects' and pieces[2] in {'experiments','stop','adopt'}:
            if pieces[2] == 'experiments':
                value = service.experiment(owner, pieces[1], body)
            elif pieces[2] == 'adopt':
                value = service.adopt(owner, pieces[1], body)
            else:
                _require(body == {}, 'video_learning_stop_invalid', 422)
                value = service.stop(owner, pieces[1])
            handler._send(value, 202 if pieces[2] == 'experiments' else 200)
        else:
            _require(False, 'video_learning_not_found', 404)
        pump = getattr(handler.server, 'video_learning_pump', None)
        if pump:
            pump.notify()
        return True
    return False


def route_public(handler, client, owner, method, path, body=None):
    """Called after the lab's existing owner, Origin and CSRF checks."""
    if path != PREFIX and not path.startswith(PREFIX + '/'):
        return False
    pieces = path[len(PREFIX):].strip('/').split('/')
    if method == 'GET' and pieces == ['catalog']:
        value = (client.video_catalogue(owner) if client else
            dict(projects=[], availability=dict(enabled=False, reason='视频学习执行器尚未配置')))
        handler._json(dict(value, csrf_token=handler.server.lab.csrf_token,
            owner_key=digest(dict(namespace='video-learning-library', owner=owner))))
        return True
    _require(client is not None, 'video_learning_unavailable', 503)
    if method == 'GET':
        if len(pieces) == 3 and pieces[0] == 'references' and pieces[2] == 'media':
            raw, mime = client.video_reference(owner, pieces[1])
            handler._send_artifact(raw, mime)
        elif len(pieces) == 4 and pieces[0] == 'references' and pieces[2] == 'analyses':
            handler._json(client.video_analysis(owner, pieces[1], pieces[3]))
        elif len(pieces) == 6 and pieces[0] == 'references' and pieces[2] == 'analyses' and pieces[4] == 'frames':
            raw, mime = client.video_analysis_frame(owner, pieces[1], pieces[3], pieces[5])
            handler._send_artifact(raw, mime)
        elif len(pieces) == 2 and pieces[0] == 'projects':
            handler._json(client.video_get(owner, pieces[1]))
        elif len(pieces) == 5 and pieces[0] == 'projects' and pieces[2] == 'experiments' and pieces[4] == 'input':
            raw, mime = client.video_inspect(owner, pieces[1], pieces[3])
            _require(mime == 'application/json', 'video_learning_input_invalid', 502)
            handler._send(raw, 'application/json; charset=utf-8')
        elif len(pieces) == 4 and pieces[0] == 'projects' and pieces[2] == 'artifacts':
            raw, mime = client.video_artifact(owner, pieces[1], pieces[3])
            if mime == 'application/json':
                handler._send(raw, 'application/json; charset=utf-8', download=True,
                    download_name='video-learning-'+identifier(pieces[3])+'.json')
            else:
                handler._send_artifact(raw, mime)
        else:
            _require(False, 'video_learning_not_found', 404)
        return True
    if method == 'POST':
        if pieces == ['references']:
            handler._json(client.video_upload(owner, body), 201)
        elif len(pieces) == 3 and pieces[0] == 'references' and pieces[2] == 'analyses':
            handler._json(client.video_analyze(owner, pieces[1], body), 202)
        elif len(pieces) == 5 and pieces[0] == 'references' and pieces[2] == 'analyses' and pieces[4] == 'stop':
            handler._json(client.video_analysis_stop(owner, pieces[1], pieces[3], body))
        elif pieces == ['projects']:
            handler._json(client.video_start(owner, body), 202)
        elif len(pieces) == 3 and pieces[0] == 'projects' and pieces[2] in {'experiments','stop','adopt'}:
            handler._json(client.video_action(owner, pieces[1], pieces[2], body),
                202 if pieces[2] == 'experiments' else 200)
        else:
            _require(False, 'video_learning_not_found', 404)
        return True
    return False
