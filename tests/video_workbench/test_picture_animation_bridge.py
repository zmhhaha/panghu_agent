"""Static-to-animation links use real stores, never confer motion acceptance."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.animation_method_service import AnimationMethodService
from video_workbench.picture_animation_bridge import PictureAnimationBridge, validate_origin
from video_workbench.picture_learning_service import PictureLearningService
from video_workbench.learning_resources import LearningResourceStage
from video_workbench.animation_wikiskill_production import LibrarySkillProductionAdapter
from test_animation_method_service import Gateway as AnimationGateway, Renderer as AnimationRenderer
from test_picture_learning_service import Gateway as PictureGateway, Renderer as PictureRenderer
from test_picture_learning_service import finish, start
from test_manim_method_integration import setup
from test_learning_capabilities import browser_source


@pytest.fixture
def linked(tmp_path):
    author, reviewer = PictureGateway(), PictureGateway(scores=[85, 82])
    pictures = PictureLearningService(tmp_path/'pictures', author_gateway=author,
        review_gateway=reviewer, renderer=PictureRenderer(tmp_path/'picture-renders'))
    animations = AnimationMethodService(tmp_path/'animations', author_gateway=AnimationGateway(),
        review_gateway=AnimationGateway(True), renderer=AnimationRenderer(tmp_path/'animation-renders'))
    bridge = PictureAnimationBridge(pictures, animations)
    pictures.animation_bridge = bridge
    project, _ = start(pictures, max_rounds=1)
    project = finish(pictures, project)
    assert project['state'] == 'completed'
    candidate = next(item for item in project['candidates'] if item['id'] == project['best_candidate_id'])
    body = dict(request_id=uuid4().hex, candidate_id=candidate['id'],
        goal='Animate the learned visual craft for a newly authored scene.',
        budget=dict(max_rounds=1, max_seconds=300), duration_seconds=6, use_wikiskill=False)
    return pictures, animations, bridge, project, candidate, body


def attach_manim_picture(linked, tmp_path):
    pictures, animations, _, project, candidate, _ = linked
    coordinator, recipe, executor, _ = setup(tmp_path/'manim')
    coordinator.skill_adapter = LibrarySkillProductionAdapter(lambda *_:None, lambda *_:None,
        lambda *_:[], tmp_path/'manim-library.sqlite3')
    stage = LearningResourceStage(tmp_path/'picture-resources.sqlite3',
        bindings_path=pictures.store.path, coordinator=lambda:coordinator, output_kind='picture')
    pictures.resource_stage, animations.manim = stage, coordinator
    run = pictures.store.read('experiments', 'developer', project['latest_experiment_id'])
    stored = next(item for item in run['candidates'] if item['id'] == candidate['id'])
    resource_run = dict(run, work_kind='reproduction', round=stored['round'])
    plan = dict(kind='manim', goal='Build the learned still geometry.', guidance='Keep the shared base visible.',
        options=dict(width=320, height=180, frame_count=1))
    for _ in range(20):
        if stage.advance('developer', resource_run, plan) == 'completed':
            break
    context = stage.render_context('developer', resource_run, plan)
    stored['resource_descriptor'] = deepcopy(context['generated_resources'][0])
    stored['source'] = browser_source(dict(constraints=context))
    stored['source_sha256'] = stored['source']['source_sha256']
    stored['artifacts']['source']['sha256'] = pictures.store.blob(canonical_bytes(stored['source']))
    pictures.store.put('experiments', run)
    return coordinator, recipe, executor, context


def test_one_rendered_picture_starts_new_animation_with_exact_source_and_static_scope(linked):
    pictures, animations, bridge, project, candidate, body = linked
    result = bridge.start('developer', project['id'], body)
    run = animations.commissions.get('developer', result['commission_id'])
    origin = run['frozen']['picture_origin']
    assert origin['picture_sha256'] == candidate['picture_sha256']
    assert origin['source_sha256'] == candidate['source_sha256']
    assert origin['verification_scope'] == 'static_only'
    assert run['frozen']['creation_mode'] == 'new'
    assert run['frozen']['use_wikiskill'] is False
    assert 'base_commission_id' not in run['frozen']
    document = animations._document('developer', run, 'plan')
    assert document['base_source'] == origin['source']
    assert document['constraints']['picture_origin']['verification_scope'] == 'static_only'
    assert 'not a previous animation' in document['constraints']['picture_origin']['instruction']
    assert document['observation']['motion_observed'] is False
    assert document['observation']['audio_observed'] is False
    assert 'source' not in result['origin'] and 'method' not in result['origin']
    assert bridge.links('developer', project['id'])[0]['commission_id'] == run['id']
    assert pictures.get('developer', project['id'])['animation_links'][0]['candidate_id'] == candidate['id']
    assert animations.author.submissions == []
    assert pictures.get('developer', project['id'])['adopted_candidate_id'] is None
    assert pictures.get('developer', project['id'])['published'] is False


def test_exact_replay_recovers_downstream_creation_after_picture_receipt_interruption(linked, monkeypatch):
    pictures, animations, bridge, project, _, body = linked
    original = pictures.store.bind_request
    interrupted = [False]

    def fail_once(owner, kind, request_id, document, entity_id, db):
        if kind.startswith('animation:') and not interrupted[0]:
            interrupted[0] = True
            raise ServiceError('synthetic_receipt_interruption', 503)
        return original(owner, kind, request_id, document, entity_id, db)

    monkeypatch.setattr(pictures.store, 'bind_request', fail_once)
    with pytest.raises(ServiceError, match='receipt_interruption'):
        bridge.start('developer', project['id'], body)
    assert len(animations.commissions.list_all('developer')) == 1
    first = animations.commissions.list_all('developer')[0]
    recovered = bridge.start('developer', project['id'], body)
    assert recovered['commission_id'] == first['id']
    assert bridge.start('developer', project['id'], body) == recovered
    assert len(animations.commissions.list_all('developer')) == 1
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        bridge.start('developer', project['id'], dict(body, goal='Changed under the same request id'))


def test_foreign_missing_or_active_picture_cannot_allocate_animation(linked):
    pictures, animations, bridge, project, candidate, body = linked
    with pytest.raises(ServiceError) as error:
        bridge.start('another-developer', project['id'], body)
    assert error.value.status == 404
    with pytest.raises(ServiceError, match='candidate_not_found'):
        bridge.start('developer', project['id'], dict(body, candidate_id=uuid4().hex))
    latest = pictures.store.read('experiments', 'developer', project['latest_experiment_id'])
    latest['state'] = 'running'
    pictures.store.put('experiments', latest)
    with pytest.raises(ServiceError, match='experiment_active'):
        bridge.start('developer', project['id'], body)
    assert animations.commissions.list_all('developer') == []


def test_unrendered_picture_or_forged_source_binding_is_rejected(linked):
    pictures, animations, bridge, project, candidate, body = linked
    run = pictures.store.read('experiments', 'developer', project['latest_experiment_id'])
    stored = next(item for item in run['candidates'] if item['id'] == candidate['id'])
    stored['observed_sha256'] = None
    pictures.store.put('experiments', run)
    with pytest.raises(ServiceError, match='candidate_not_rendered'):
        bridge.start('developer', project['id'], body)
    stored['observed_sha256'] = candidate['picture_sha256']
    stored['source']['files']['scene.js'] = 'tampered after render'
    pictures.store.put('experiments', run)
    with pytest.raises(ServiceError, match='source_changed'):
        bridge.start('developer', project['id'], body)
    assert animations.commissions.list_all('developer') == []


def test_manim_picture_preserves_owned_python_and_defaults_to_new_resource_planning(linked, tmp_path):
    pictures, animations, bridge, project, candidate, body = linked
    coordinator, recipe, executor, context = attach_manim_picture(linked, tmp_path)
    result = bridge.start('developer', project['id'], body)
    animation = animations.commissions.get('developer', result['commission_id'])
    document = animations._document('developer', animation, 'plan')
    descriptor = context['generated_resources'][0]
    seed = document['constraints']['picture_origin']['manim_source_context']
    original = coordinator.source_context('developer', descriptor)
    assert seed == dict(original, sha256=digest(original))
    assert seed['request']['source'] == context['manim_source_context']['request']['source']
    assert animation['frozen']['capability_selection'] == dict(creation=['manim'], motion=[])
    assert animation['frozen']['resource_planning'] == 'after-plan.v1'
    assert animation['frozen']['resource_authoring']['frame_count'] != 1
    assert 'generated_resources' not in document['constraints']
    assert not (set(context['assets']) & set(document['assets']))
    assert 'regenerate resource identities' in document['constraints']['picture_origin']['instruction'].lower()
    assert 'generated_resources' not in animation['frozen']['picture_origin']
    assert recipe.submissions == executor.calls == 1
    changed = deepcopy(animation['frozen']['picture_origin'])
    changed['manim_source_context']['request']['source'] += '\n# forged'
    with pytest.raises(ServiceError, match='manim_source_changed'):
        validate_origin(changed)


def test_manim_picture_respects_explicit_browser_choice_and_rejects_foreign_resource(linked, tmp_path):
    pictures, animations, bridge, project, candidate, body = linked
    _, recipe, executor, context = attach_manim_picture(linked, tmp_path)
    body['capability_selection'] = dict(creation=['html-svg'], motion=[])
    result = bridge.start('developer', project['id'], body)
    animation = animations.commissions.get('developer', result['commission_id'])
    assert animation['frozen']['capability_selection'] == body['capability_selection']
    assert 'resource_authoring' not in animation['frozen']
    assert animation['frozen']['picture_origin']['manim_source_context']['request']['source']
    run = pictures.store.read('experiments', 'developer', project['latest_experiment_id'])
    stored = next(item for item in run['candidates'] if item['id'] == candidate['id'])
    stored['resource_descriptor']['scope']['owner'] = 'another-owner'
    pictures.store.put('experiments', run)
    with pytest.raises(ServiceError, match='manim_source_not_owned'):
        bridge.start('developer', project['id'], dict(body, request_id=uuid4().hex))
    assert recipe.submissions == executor.calls == 1


def test_public_animation_start_cannot_inject_picture_origin_and_store_checks_origin(linked):
    _, animations, bridge, project, _, body = linked
    result = bridge.start('developer', project['id'], body)
    run = animations.commissions.get('developer', result['commission_id'])
    origin = deepcopy(run['frozen']['picture_origin'])
    public = dict(request_id=uuid4().hex, goal='A public new request',
        budget=dict(max_rounds=1, max_seconds=300), picture_origin=origin)
    with pytest.raises(ServiceError, match='request_invalid'):
        animations.start('developer', public)
    changed = deepcopy(origin)
    changed['source_sha256'] = 'f'*64
    with pytest.raises(ServiceError, match='origin_changed'):
        validate_origin(changed)
    changed = deepcopy(run['frozen'])
    changed['picture_origin'] = dict(origin, verification_scope='motion_validated')
    with pytest.raises(ServiceError, match='origin_invalid'):
        animations.commissions.create('developer', changed, uuid4().hex)
