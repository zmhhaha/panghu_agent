"""Create an original animation from one immutable, rendered picture version.

The source is a creative starting point, not an animation baseline or a Skill
release. Stable downstream request identities recover a cross-store interruption.
"""
from copy import deepcopy
import re
from uuid import UUID, uuid5

from agent_service.contracts import digest
from agent_service.profiles.video_picture_learning import validate_source
from .picture_learning_store import TERMINAL, identifier, owner_id, require

_NAMESPACE = UUID('3e0f5c31-484d-4214-8cd3-9a2e5e692367')


def validate_origin(value):
    required = {
        'schema', 'project_id', 'experiment_id', 'candidate_id', 'picture_sha256',
        'source_sha256', 'source', 'method', 'verification_scope'}
    require(type(value) is dict and required <= set(value)
        <= required | {'manim_source_context'},
        'picture_animation_origin_invalid')
    require(value['schema'] == 'video.picture-animation-origin.v1'
            and value['verification_scope'] == 'static_only', 'picture_animation_origin_invalid')
    for key in ('project_id', 'experiment_id', 'candidate_id'):
        identifier(value[key])
    source = validate_source(value['source'])
    require(source['source_sha256'] == value['source_sha256']
            and type(value['picture_sha256']) is str and re.fullmatch(r'[0-9a-f]{64}', value['picture_sha256']),
            'picture_animation_origin_changed', 409)
    require(value['method'] is None or type(value['method']) is dict,
            'picture_animation_origin_invalid')
    if 'manim_source_context' in value:
        context = value['manim_source_context']
        fields = {'schema', 'scope', 'descriptor_sha256', 'recipe_input_sha256',
            'recipe_result_sha256', 'request', 'strategy', 'skill_decisions', 'sha256'}
        require(type(context) is dict and set(context) == fields
            and context['schema'] == 'video.manim-method-source-context.v1'
            and context['sha256'] == digest({key: item for key, item in context.items() if key != 'sha256'}),
            'picture_animation_manim_source_changed', 409)
        scope = context['scope']
        require(type(scope) is dict and set(scope) == {'owner', 'effect_id', 'experiment_id', 'operation_id'},
            'picture_animation_manim_source_changed', 409)
        owner_id(scope['owner'])
        for key in ('effect_id', 'experiment_id', 'operation_id'):
            identifier(scope[key])
        for key in ('descriptor_sha256', 'recipe_input_sha256', 'recipe_result_sha256'):
            require(type(context[key]) is str and re.fullmatch(r'[0-9a-f]{64}', context[key]),
                'picture_animation_manim_source_changed', 409)
        from .manim_resource import validate_request
        request = validate_request(context['request'])
        require(request['frame_count'] == 1 and request['global_start_frame'] == 0
            and (context['strategy'] is None or type(context['strategy']) is dict)
            and type(context['skill_decisions']) is list,
            'picture_animation_manim_source_invalid', 409)
    return deepcopy(value)


class PictureAnimationBridge:
    def __init__(self, pictures, animations):
        self.pictures, self.animations = pictures, animations

    def start(self, owner, project_id, body):
        require(type(body) is dict and {'request_id', 'candidate_id', 'goal', 'budget'} <= set(body)
            <= {'request_id', 'candidate_id', 'title', 'goal', 'budget', 'duration_seconds',
                'use_wikiskill', 'capability_selection'}, 'picture_animation_request_invalid')
        identifier(body['request_id'])
        project = self.pictures.store.read('projects', owner, project_id)
        request_kind = 'animation:' + project_id
        prior = self.pictures.store.request(owner, request_kind, body['request_id'], body)
        if prior:
            return self._result(owner, project_id, prior)
        runs = self.pictures.store.list('experiments', owner, project_id=project_id)
        latest = next(run for run in runs if run['id'] == project['latest_experiment_id'])
        require(latest['state'] in TERMINAL, 'picture_animation_experiment_active', 409)
        pair = next(((run, candidate) for run in runs for candidate in run['candidates']
            if candidate['id'] == body['candidate_id']), None)
        require(pair is not None, 'picture_learning_candidate_not_found', 404)
        run, candidate = pair
        require(self.pictures._has_picture(candidate), 'picture_learning_candidate_not_rendered', 409)
        self.pictures.store.read_blob(candidate['picture_sha256'])
        origin = dict(schema='video.picture-animation-origin.v1',
            project_id=project_id, experiment_id=run['id'], candidate_id=candidate['id'],
            picture_sha256=candidate['picture_sha256'], source_sha256=candidate['source_sha256'],
            source=candidate['source'], method=candidate.get('method'), verification_scope='static_only')
        descriptor = candidate.get('resource_descriptor')
        if descriptor is not None:
            require(type(descriptor) is dict and descriptor.get('scope', {}).get('owner') == owner,
                'picture_animation_manim_source_not_owned', 409)
            stage = getattr(self.pictures, 'resource_stage', None)
            require(stage is not None, 'picture_animation_manim_source_unavailable', 409)
            coordinator = stage.coordinator()
            require(coordinator is not None, 'picture_animation_manim_source_unavailable', 409)
            context = coordinator.source_context(owner, descriptor)
            require(context['scope'] == descriptor['scope']
                and context['descriptor_sha256'] == descriptor['sha256'],
                'picture_animation_manim_source_changed', 409)
            origin['manim_source_context'] = dict(context, sha256=digest(context))
        origin = validate_origin(origin)
        downstream = {key: deepcopy(value) for key, value in body.items()
                      if key not in {'request_id', 'candidate_id'}}
        downstream.update(request_id=str(uuid5(_NAMESPACE,
            digest(dict(owner=owner, project_id=project_id, request_id=body['request_id'])))),
            creation_mode='new')
        downstream.setdefault('title', project['title'] + ' · 动画创作')
        if descriptor is not None:
            downstream.setdefault('capability_selection', dict(creation=['manim'], motion=[]))
        # Replaying this exact downstream key returns the existing commission
        # even if the picture-side receipt was interrupted after creation.
        detail = self.animations.start(owner, downstream, _picture_origin=origin,
            _submission_snapshot=None)
        with self.pictures.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.pictures.store.bind_request(owner, request_kind, body['request_id'], body,
                                             detail['id'], db)
        return self._result(owner, project_id, detail['id'])

    def _result(self, owner, project_id, commission_id):
        detail = self.animations.get(owner, commission_id)
        return dict(schema='video.picture-animation-link.v1', project_id=project_id,
                    commission_id=commission_id, effect_id=detail.get('effect_id'),
                    origin=detail.get('picture_origin'),
                    url='./?commission=' + commission_id)

    def links(self, owner, project_id):
        self.pictures.store.read('projects', owner, project_id)
        with self.pictures.store.db() as db:
            rows = list(db.execute('SELECT request_id,entity_id FROM picture_requests '
                'WHERE owner=? AND kind=? ORDER BY rowid', (owner, 'animation:' + project_id)))
        result = []
        for row in rows:
            run = self.animations.commissions.get(owner, row['entity_id'])
            origin = run['frozen'].get('picture_origin')
            require(origin is not None and origin['project_id'] == project_id,
                    'picture_animation_origin_changed', 409)
            result.append(dict(request_id=row['request_id'], commission_id=run['id'],
                candidate_id=origin['candidate_id'], source_sha256=origin['source_sha256'],
                state=run['state'], url='./?commission=' + run['id']))
        return result
