"""Fixed drawing assets, source custody and truthful invocation evidence."""
from copy import deepcopy
import json

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench import animation_method_renderer as shared
from video_workbench import picture_learning_renderer as picture
from video_workbench.video_learning_renderer import VideoLearningRenderer, HOST as VIDEO_HOST
from video_workbench.ink_landscape_library import descriptor, read_assets
from video_workbench.learning_drawing_libraries import validate_observation
from test_picture_learning_renderer import document as picture_document, source as picture_source, SyntheticExecution as PictureExecution
from test_video_learning_renderer import document as video_document, source as video_source, VideoExecution
from test_animation_method_renderer import IMAGE, SOURCE, document as method_document


def observation(*, loaded=False):
    return dict(schema='video.learning-drawing-library-observation.v1',
        scope='loaded_scripts_not_api_calls_or_visual_application',
        drawing_libraries={'ink-landscape': descriptor()},
        loaded_scripts=['/assets/ink-landscape.mjs'] if loaded else [],
        loaded_libraries=['ink-landscape'] if loaded else [],
        api_execution=dict(schema='video.learning-drawing-api-execution.v1',
            scope='v8_executed_functions_not_visual_application',
            module_sha256=descriptor()['assets']['ink-landscape.mjs'], functions=[]))


@pytest.fixture(params=['picture', 'video'])
def configured(request, tmp_path, monkeypatch):
    runtime = shared.MethodAnimationRenderer(tmp_path / 'existing', IMAGE,
        enabled=True, timeout=60, hyperframes=True, legacy_bridge=True)
    if request.param == 'picture':
        value = picture.PictureLearningRenderer(tmp_path / 'picture', runtime_renderer=runtime)
        execute, document, source = PictureExecution(), picture_document(), picture_source()
        target = picture
    else:
        value = VideoLearningRenderer(tmp_path / 'video', runtime_renderer=runtime)
        execute, document, source = VideoExecution(), video_document(), video_source()
        target = shared

    def capture(root):
        result = execute(root)
        path = root / 'work/render-receipt.json'
        receipt = json.loads(path.read_bytes())
        receipt['drawing_library_observation'] = observation()
        path.write_bytes(canonical_bytes(receipt))
        return result

    monkeypatch.setattr(target, 'execute_method_render', capture)
    return value, document, source, execute


def bind_library(document):
    document['constraints']['drawing_libraries'] = {'ink-landscape': descriptor()}
    document['assets'].update(descriptor()['assets'])
    return document


def test_learning_capabilities_offer_fixed_local_drawing_without_changing_formal_runtime(configured):
    value, _, _, _ = configured
    caps = value.capabilities()
    assert caps['drawing_libraries'] == {'ink-landscape': descriptor()}
    assert all(caps['assets'][name] == sha for name, sha in descriptor()['assets'].items())
    assert 'drawing_libraries' not in value.runtime_renderer.capabilities()
    assert caps['runtime_identity']['host_sha256'] != value.runtime_renderer.capabilities()['runtime_identity']['host_sha256']


def test_exact_assets_and_library_identity_are_bound_while_original_source_stays_unchanged(configured):
    value, document, source, execute = configured
    bind_library(document)
    original = deepcopy(source)
    result = value.render('with-drawing', source, document)
    assert result['technical_passed'] and execute.calls == 1
    root = value._directory('with-drawing')
    binding = json.loads((root / 'binding.json').read_bytes())
    assert binding['source']['files'] == original['files'] and binding['input_sha256'] == digest(document)
    assert binding['source_sha256'] == original['source_sha256']
    assert binding['request']['drawing_libraries'] == document['constraints']['drawing_libraries']
    assert binding['request']['identity'] == digest({k: v for k, v in binding['request'].items() if k != 'identity'})
    assert {name: (root / 'input/scene/assets' / name).read_bytes() for name in read_assets()} == read_assets()
    measured = result['runtime']['drawing_library_observation']
    assert measured == observation()  # Availability does not imply loading or invocation.
    assert value.lookup('with-drawing') == result


def test_undeclared_library_assets_cannot_be_added_to_historical_frozen_task(configured):
    value, document, source, execute = configured
    document['assets'].update(descriptor()['assets'])
    with pytest.raises(ServiceError):
        value.render('undeclared', source, document)
    assert execute.calls == 0


@pytest.mark.parametrize('mutation', ['revision', 'module', 'license'])
def test_changed_library_or_any_asset_digest_is_rejected_before_execution(configured, mutation):
    value, document, source, execute = configured
    bind_library(document)
    if mutation == 'revision':
        document['constraints']['drawing_libraries']['ink-landscape']['source_revision'] = '0' * 40
    else:
        name = 'ink-landscape.mjs' if mutation == 'module' else 'ink-landscape.LICENSE.txt'
        document['assets'][name] = '0' * 64
    with pytest.raises(ServiceError):
        value.render('changed', source, document)
    assert execute.calls == 0


def test_formal_method_renderer_keeps_original_host_and_drawing_asset_boundary(tmp_path):
    value = shared.MethodAnimationRenderer(tmp_path, IMAGE, enabled=True, hyperframes=True)
    assert value._host_path() == shared.HOST and value._host_path() != VIDEO_HOST
    document = bind_library(method_document())
    with pytest.raises(ServiceError, match='asset_not_admitted'):
        value._snapshot('formal', SOURCE, document, [])


def test_executed_function_claim_requires_loaded_module_and_exact_coverage_source():
    value = observation()
    value['api_execution']['functions'] = [dict(function_name='renderCanvas',
        start_offset=1, end_offset=9, call_count=1)]
    with pytest.raises(ServiceError, match='evidence_invalid'):
        validate_observation(value, {'ink-landscape': descriptor()})
    value.update(loaded_scripts=['/assets/ink-landscape.mjs'], loaded_libraries=['ink-landscape'])
    assert validate_observation(value, {'ink-landscape': descriptor()}) == value
    value['api_execution']['module_sha256'] = '0' * 64
    with pytest.raises(ServiceError, match='evidence_invalid'):
        validate_observation(value, {'ink-landscape': descriptor()})
