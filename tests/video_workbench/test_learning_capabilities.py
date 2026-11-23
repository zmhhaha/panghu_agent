"""Observed resource planning through real learning services and shared ledgers.

Gateway/render peers are synthetic; this establishes scheduling, custody and
stop/retry behavior, never genuine Cairo admission or artistic similarity.
"""
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_picture_learning as picture_protocol
from agent_service.profiles import video_reference_learning as video_protocol
from agent_service.profiles.video_animation_create import source_sha256
from video_workbench.learning_resources import LearningResourceStage
from video_workbench.picture_learning_service import PictureLearningService
from video_workbench.video_learning_service import VideoLearningService
from video_workbench.animation_wikiskill_production import LibrarySkillProductionAdapter
from test_manim_method_integration import setup
from test_picture_learning_service import Gateway as PictureGateway, Renderer as PictureRenderer
from test_picture_learning_service import finish as finish_picture, start as start_picture
from test_video_learning_service import Gateway as VideoGateway, Renderer as VideoRenderer, real_mp4
from test_video_learning_service import finish as finish_video, start as start_video


def browser_source(document):
    generated = document['constraints'].get('generated_resources')
    if generated:
        manifest = json.dumps(generated[0]['manifest'])
        placement = document['constraints']['resource_placement']['global_start_frame']
        script = ("import {prepareManimMethodFrames} from '/assets/manim_method_frames.mjs';\n"
            f"const layer=prepareManimMethodFrames(document.querySelector('#stage'),{manifest},"
            f"{{placementStartFrame:{placement}}});\n"
            "window.__animationExperiment={ready:layer.ready,seek(seconds){layer.seekFrame(Math.round(seconds*24));}};")
    else:
        script = 'window.__animationExperiment={seek(seconds){}};'
    source = dict(engine='html-svg', strategy=dict(description='An authored geometry carrier', tradeoffs='Synthetic test'),
        files={'scene.html':'<html><body><div id="stage"></div><script type="module" src="/scene.js"></script></body></html>',
               'scene.js':script})
    source['source_sha256'] = source_sha256(source)
    return source


class ResourceAuthor:
    offer_resource = True

    def collect(self, job_id, checksum, document):
        if document['mode'] != 'create':
            return super().collect(job_id, checksum, document)
        result = dict(schema=self.protocol.RESULT_SCHEMA, input_sha256=checksum, mode='create',
            source=browser_source(document), review=None, transfer_task=None,
            method=dict(technique='Maintain vertex correspondence before adding labels.',
                evidence_scope='static_only' if self.protocol is picture_protocol else 'sampled_frames_only'))
        if self.protocol is video_protocol:
            result.update(sound_events=[], evidence_scope='sampled_frames_only')
        if self.offer_resource and not document['constraints'].get('generated_resources'):
            result['method']['resource_plan'] = dict(kind='manim',
                goal='Build the geometry described by the reference-observing author.',
                guidance='Two corresponding triangles share a fixed base; the height stays visible.',
                options=dict(width=320, height=180, frame_count=1 if self.protocol is picture_protocol else 4))
        self.protocol.validate_result(result, checksum, document)
        return result


class PictureResourceAuthor(ResourceAuthor, PictureGateway):
    protocol = picture_protocol


class VideoResourceAuthor(ResourceAuthor, VideoGateway):
    protocol = video_protocol


class ReadyPictureRenderer(PictureRenderer):
    def render(self, operation_id, source, document, **kwargs):
        assert document['constraints'].get('generated_resources'), 'draft must not render before the resource'
        assert document['constraints']['manim_source_context']['request']['source']
        assert 'prepareManimMethodFrames' in source['files']['scene.js']
        return super().render(operation_id, source, document, **kwargs)


class ReadyVideoRenderer(VideoRenderer):
    def render(self, operation_id, source, document, **kwargs):
        assert document['constraints'].get('generated_resources'), 'draft must not render before the resource'
        assert document['constraints']['manim_source_context']['request']['source']
        return super().render(operation_id, source, document, **kwargs)


def resources(service, tmp_path, kind):
    coordinator, recipe, executor, _ = setup(tmp_path/'producer')
    coordinator.skill_adapter = LibrarySkillProductionAdapter(
        lambda *_:pytest.fail('Disabled learning must not choose the active library'),
        lambda *_:pytest.fail('Disabled learning must not read a published release'),
        lambda *_:[], tmp_path/'producer-library.sqlite3')
    stage = LearningResourceStage(tmp_path/'learning-resources.sqlite3',
        bindings_path=service.store.path, coordinator=lambda:coordinator, output_kind=kind)
    service.resource_stage = stage
    return coordinator, recipe, executor, stage


@pytest.fixture
def picture(tmp_path):
    author, reviewer = PictureResourceAuthor(), PictureGateway(scores=[85, 82])
    renderer = ReadyPictureRenderer(tmp_path/'renders')
    service = PictureLearningService(tmp_path/'learning', author_gateway=author,
        review_gateway=reviewer, renderer=renderer)
    coordinator, recipe, executor, stage = resources(service, tmp_path, 'picture')
    return service, author, reviewer, renderer, coordinator, recipe, executor, stage


def test_still_observation_precedes_resource_then_composition_review_and_new_transfer_resource(picture):
    service, author, reviewer, renderer, coordinator, recipe, executor, stage = picture
    assert 'manim' in service.catalogue('developer')['capabilities']['creation']
    project, _ = start_picture(service, max_rounds=1,
        capability_selection=dict(creation=['manim'], motion=[]))
    assert recipe.submissions == executor.calls == 0
    result = finish_picture(service, project, limit=100)
    assert result['state'] == 'completed', result['experiments'][0].get('error')
    assert len(renderer.calls) == 2 and recipe.submissions == executor.calls == 2
    assert len(author.submissions) == 4 and len(reviewer.submissions) == 2
    first, first_compose, transfer, transfer_compose = [row[0] for row in author.uploads]
    assert first['observation']['frames'][0]['purpose'] == 'reference'
    assert 'generated_resources' not in first['constraints']
    assert first_compose['constraints']['learning_strategy_task']['phase'] == 'compose'
    assert first_compose['constraints']['generated_resources'][0]['manifest']['frame_count'] == 1
    assert first_compose['base_source'] == browser_source(first)
    assert transfer['work_kind'] == 'transfer' and transfer['base_source'] is None
    assert transfer['observation']['frames'] == []
    first_descriptor = first_compose['constraints']['generated_resources'][0]
    transfer_descriptor = transfer_compose['constraints']['generated_resources'][0]
    assert first_descriptor['scope'] != transfer_descriptor['scope']
    assert first_descriptor['manifest']['resource_id'] != transfer_descriptor['manifest']['resource_id']
    assert all(item['resource_descriptor']['manifest']['frame_count'] == 1 for item in result['candidates'])
    assert result['adopted_candidate_id'] is None and result['published'] is False
    count = (recipe.submissions, executor.calls, len(author.submissions), len(renderer.calls))
    service.catalogue('developer'); service.get('developer', project['id'])
    assert count == (recipe.submissions, executor.calls, len(author.submissions), len(renderer.calls))


def test_manim_planning_keeps_strict_source_contract_in_frozen_reproduction_and_transfer(picture):
    """The real profile rejects the null-source proposal seen in live transfer.

    A resource plan remains an addition to a valid author-written carrier. No
    validator relaxation or host-authored replacement source is involved.
    """
    service, author, _, renderer, _, recipe, executor, _ = picture
    project, _ = start_picture(service, max_rounds=1,
        capability_selection=dict(creation=['manim'], motion=[]))
    completed = finish_picture(service, project, limit=100)
    assert completed['state'] == 'completed'
    for index, work_kind in ((0, 'reproduction'), (2, 'transfer')):
        document = author.uploads[index][0]
        stored = service.store.read('operations', 'developer', author.submissions[index])
        assert stored['input'] == document and stored['input_sha256'] == digest(document)
        assert document['work_kind'] == work_kind
        assert 'generated_resources' not in document['constraints']
        instructions = document['constraints']['learning_resource_offer']['instructions']
        assert any('source must still be a non-null valid source object' in row for row in instructions)
        assert any('method.resource_plan supplements source; it never replaces source' in row
            for row in instructions)
        assert any('without claiming that the resource has been generated or consumed' in row
            for row in instructions)
        if work_kind == 'transfer':
            assert document['base_source'] is None and document['observation']['frames'] == []
        planned = author.collect('offline-validator-only', digest(document), document)
        assert planned['method']['resource_plan']['kind'] == 'manim'
        assert planned['source']['files']['scene.js'] == 'window.__animationExperiment={seek(seconds){}};'
        assert picture_protocol.validate_result(planned, digest(document), document) == planned
        missing_source = deepcopy(planned)
        missing_source['source'] = None
        with pytest.raises(ServiceError, match='picture_learning_protocol_invalid'):
            picture_protocol.validate_result(missing_source, digest(document), document)
    assert len(renderer.calls) == recipe.submissions == executor.calls == 2


def test_manim_only_cannot_render_a_browser_substitute_without_a_resource_plan(picture):
    service, author, _, renderer, _, recipe, executor, _ = picture
    author.offer_resource = False
    project, _ = start_picture(service, max_rounds=1,
        capability_selection=dict(creation=['manim'], motion=[]))
    result = finish_picture(service, project)
    assert result['state'] == 'failed'
    assert result['experiments'][0]['error']['code'] == 'picture_learning_manim_resource_required'
    assert recipe.submissions == executor.calls == len(renderer.calls) == 0


def test_new_round_keeps_the_best_reviewed_resource_source_after_regression(picture):
    service, _, reviewer, _, _, recipe, executor, _ = picture
    reviewer.scores = [70, 65]
    project, _ = start_picture(service, max_rounds=3,
        capability_selection=dict(creation=['manim'], motion=[]))
    for _ in range(100):
        run = service.store.read('experiments', 'developer', project['latest_experiment_id'])
        if run['round'] == 3:
            break
        service.advance('developer', run['id'])
    else:
        pytest.fail('Third round was not reached')
    first, second = run['candidates']
    assert first['review']['score'] > second['review']['score']
    assert first['resource_descriptor'] != second['resource_descriptor']
    assert run['best_candidate_id'] == first['id']
    assert run['resource_baseline_descriptor'] == first['resource_descriptor']
    assert run['resource_prior_review'] == first['review']
    assert recipe.submissions == executor.calls == 2


def test_stop_resource_phase_before_producer_claim_stops_without_phantom_render(picture):
    service, author, _, renderer, _, recipe, executor, _ = picture
    project, _ = start_picture(service, max_rounds=1,
        capability_selection=dict(creation=['manim'], motion=[]))
    for _ in range(10):
        detail = service.get('developer', project['id'])
        if detail['stage'] == 'learning_resources':
            break
        service.advance('developer', project['latest_experiment_id'])
    assert detail['stage'] == 'learning_resources'
    assert len(author.submissions) == 1 and recipe.submissions == executor.calls == 0
    service.stop('developer', project['id'])
    stopped = service.advance('developer', project['latest_experiment_id'])
    assert stopped['state'] == 'cancelled'
    assert stopped['experiments'][0]['stop_proven'] is True
    assert recipe.submissions == executor.calls == len(renderer.calls) == 0


def test_failed_render_retry_adopts_resource_and_does_not_pay_for_new_recipe(picture):
    service, author, _, renderer, _, recipe, executor, _ = picture
    renderer.fail = True
    project, _ = start_picture(service, max_rounds=1,
        capability_selection=dict(creation=['manim'], motion=[]))
    failed = finish_picture(service, project, limit=100)
    assert failed['state'] == 'failed' and failed['stage'] == 'render'
    assert recipe.submissions == executor.calls == 1
    created = len(author.submissions)
    renderer.fail = False
    retry = service.next_experiment('developer', project['id'], dict(request_id=uuid4().hex,
        mode='retry'))
    completed = finish_picture(service, retry, limit=100)
    assert completed['state'] == 'completed', completed['experiments'][-1].get('error')
    # Only the changed-content transfer creates another recipe/composition.
    assert recipe.submissions == executor.calls == 2
    assert len(author.submissions) == created + 2
    reproduction = [item for item in completed['candidates'] if item['work_kind'] == 'reproduction']
    assert len(reproduction) == 2
    assert reproduction[0]['resource_descriptor'] == reproduction[1]['resource_descriptor']


def test_video_service_creates_short_local_resource_and_independent_transfer(tmp_path, real_mp4):
    author, reviewer = VideoResourceAuthor(), VideoGateway()
    renderer = ReadyVideoRenderer(tmp_path/'renders', real_mp4)
    service = VideoLearningService(tmp_path/'learning', author_gateway=author,
        review_gateway=reviewer, renderer=renderer)
    _, recipe, executor, _ = resources(service, tmp_path, 'video')
    project, _ = start_video(service, real_mp4,
        capability_selection=dict(creation=['manim'], motion=[]))
    result = finish_video(service, project, limit=100)
    assert result['state'] == 'completed', result['experiments'][0].get('error')
    assert len(renderer.calls) == 2 and recipe.submissions == executor.calls == 2
    assert len(author.submissions) == 4
    reproduced, transferred = result['candidates']
    assert reproduced['resource_descriptor']['manifest']['frame_count'] == 4
    assert reproduced['resource_descriptor']['scope'] != transferred['resource_descriptor']['scope']
    assert result['continuous_motion_verified'] is result['audio_verified'] is False
