"""Fixed drawing assets, source custody and truthful invocation evidence."""
from copy import deepcopy
import base64
import hashlib
import json
import shutil
import subprocess

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_reference_learning as video_protocol
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
        # Exercise an explicit compatible dependency, independent of availability.
        value.candidate_engines = ('canvas',)
        document['constraints']['allowed_engines'] = ['canvas']
        source['engine'] = 'canvas'
        source['files']['scene.html'] = source['files']['scene.html'].replace(
            '</body>', '<script type="module" src="./scene.js"></script></body>')
        source['files']['scene.js'] = "import * as ink from './assets/ink-landscape.mjs';"
        source['source_sha256'] = video_protocol.source_sha256(source)
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


def _three_binding(tmp_path):
    runtime = shared.MethodAnimationRenderer(tmp_path / 'existing', IMAGE,
        enabled=True, timeout=60, hyperframes=True, legacy_bridge=True)
    value = VideoLearningRenderer(tmp_path / 'video', runtime_renderer=runtime)
    value.candidate_engines = ('three',)
    document = bind_library(video_document())
    document['constraints']['allowed_engines'] = ['three']
    document['assets']['three.module.min.js'] = shared.ASSET_SHA256['three.module.min.js']
    source = deepcopy(video_source())
    source['engine'] = 'three'
    source['files']['scene.js'] = "import * as THREE from './assets/three.module.min.js';"
    source['source_sha256'] = video_protocol.source_sha256(source)
    return value, document, source


def _native_canvas_binding(tmp_path):
    runtime = shared.MethodAnimationRenderer(tmp_path / 'existing', IMAGE,
        enabled=True, timeout=60, hyperframes=True, legacy_bridge=True)
    value = VideoLearningRenderer(tmp_path / 'video', runtime_renderer=runtime)
    value.candidate_engines = ('canvas',)
    document = bind_library(video_document())
    document['constraints']['allowed_engines'] = ['canvas']
    document['assets']['NotoSansCJK-Regular.ttc'] = shared.ASSET_SHA256['NotoSansCJK-Regular.ttc']
    source = deepcopy(video_source())
    source['engine'] = 'canvas'
    source['files'] = {
        'scene.html': '<html><body><canvas width="320" height="180"></canvas>'
            '<script src="./scene.js"></script></body></html>',
        'scene.js': "const ctx=document.querySelector('canvas').getContext('2d');"
            "window.__animationExperiment={seek(t){ctx.clearRect(0,0,320,180);"
            "ctx.fillRect(t*10,20,30,30);}};",
    }
    source['source_sha256'] = video_protocol.source_sha256(source)
    return value, document, source


def _actual_video_host_validation(request, resources):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for the actual host validator regression')
    script = """
const {validateDrawingLibraries} = await import(process.argv[1]);
let raw='';for await(const chunk of process.stdin)raw+=chunk;
const payload=JSON.parse(raw),resources=new Map(Object.entries(payload.resources)
  .map(([name,value])=>[name,Buffer.from(value,'base64')]));
try { process.stdout.write(JSON.stringify({accepted:true,
  libraries:validateDrawingLibraries(payload.request,resources)})); }
catch(error) { process.stdout.write(JSON.stringify({accepted:false,error:String(error)})); }
"""
    payload = dict(request=request, resources={name: base64.b64encode(raw).decode('ascii')
        for name, raw in resources.items()})
    result = subprocess.run([node, '--input-type=module', '-e', script, VIDEO_HOST.resolve().as_uri()],
        input=canonical_bytes(payload), capture_output=True, timeout=15, check=True)
    return json.loads(result.stdout)


def test_native_canvas_omits_unused_library_and_passes_the_actual_unchanged_host(tmp_path):
    value, document, source = _native_canvas_binding(tmp_path)
    original_document, original_source = deepcopy(document), deepcopy(source)
    host_sha256 = hashlib.sha256(VIDEO_HOST.read_bytes()).hexdigest()
    binding = value._snapshot('native-canvas', source, document, [])
    request = binding['request']

    # Reproduce the real pre-browser failure when an unused helper is attached.
    old_request = deepcopy(request)
    old_request['drawing_libraries'] = document['constraints']['drawing_libraries']
    old_request['assets_sha256'].update(descriptor()['assets'])
    assert _actual_video_host_validation(old_request,
        {'/assets/'+name: raw for name, raw in read_assets().items()}) == dict(
            accepted=False, error='Error: learning_drawing_library_invalid')
    assert _actual_video_host_validation(request, {}) == dict(accepted=True, libraries=None)
    assert 'drawing_libraries' not in request
    assert not (descriptor()['assets'].keys() & request['assets_sha256'].keys())
    assert request['assets_sha256'] == {
        name: checksum for name, checksum in original_document['assets'].items()
        if name not in descriptor()['assets']}
    assert document == original_document and source == original_source
    assert binding['document'] == document and binding['input_sha256'] == digest(document)
    assert binding['source']['files'] == source['files']
    assert binding['source_sha256'] == binding['learning_source_sha256'] == source['source_sha256']
    assert request['host_sha256'] == host_sha256 == hashlib.sha256(VIDEO_HOST.read_bytes()).hexdigest()


@pytest.mark.parametrize('asset', ['ink-landscape.mjs', 'ink-landscape.LICENSE.txt'])
def test_canvas_explicit_drawing_dependency_is_never_silently_removed(tmp_path, asset):
    value, document, source = _native_canvas_binding(tmp_path)
    source['files']['scene.js'] += f"\nimport './assets/{asset}';"
    source['source_sha256'] = video_protocol.source_sha256(source)
    original_document, original_source = deepcopy(document), deepcopy(source)
    binding = value._snapshot('explicit-canvas', source, document, [])
    assert binding['request']['drawing_libraries'] == document['constraints']['drawing_libraries']
    assert all(binding['request']['assets_sha256'][name] == checksum
               for name, checksum in descriptor()['assets'].items())
    assert document == original_document and source == original_source
    assert binding['document'] == original_document
    assert binding['learning_source_sha256'] == original_source['source_sha256']


@pytest.mark.parametrize('mutation', ['revision', 'module', 'license'])
def test_unused_canvas_library_is_validated_before_dependency_selection(tmp_path, mutation):
    value, document, source = _native_canvas_binding(tmp_path)
    if mutation == 'revision':
        document['constraints']['drawing_libraries']['ink-landscape']['source_revision'] = '0' * 40
    else:
        name = 'ink-landscape.mjs' if mutation == 'module' else 'ink-landscape.LICENSE.txt'
        document['assets'][name] = '0' * 64
    with pytest.raises(ServiceError, match='ink_landscape_library_not_admitted'):
        value._snapshot('changed-unused-library', source, document, [])
    assert not value.root.exists()


def test_changed_vendor_module_bytes_are_rejected_before_native_canvas_execution(tmp_path, monkeypatch):
    from video_workbench import ink_landscape_library as ink
    value, document, source = _native_canvas_binding(tmp_path)
    vendor = tmp_path / 'vendor'
    shutil.copytree(ink.ROOT, vendor)
    (vendor / 'ink-landscape.mjs').write_bytes(b'changed module bytes')
    monkeypatch.setattr(ink, 'ROOT', vendor)

    def unexpected_execution(*_):
        pytest.fail('changed offered vendor bytes must fail before execution')
    monkeypatch.setattr(shared, 'execute_method_render', unexpected_execution)
    with pytest.raises(ServiceError, match='ink_landscape_library_bytes_invalid'):
        value.render('changed-vendor-module', source, document)
    assert not value.root.exists()


def test_three_request_omits_incompatible_library_and_passes_the_actual_unchanged_host(tmp_path):
    value, document, source = _three_binding(tmp_path)
    original_document, original_source = deepcopy(document), deepcopy(source)
    host_sha256 = hashlib.sha256(VIDEO_HOST.read_bytes()).hexdigest()
    binding = value._snapshot('three', source, document, [])
    request = binding['request']

    # Reconstruct the failed request's unconditional library attachment. The
    # real host rejects it before scene execution, rather than a mocked check.
    old_request = deepcopy(request)
    old_request['drawing_libraries'] = document['constraints']['drawing_libraries']
    old_request['assets_sha256'].update(descriptor()['assets'])
    failure = _actual_video_host_validation(old_request,
        {'/assets/'+name: raw for name, raw in read_assets().items()})
    assert failure == dict(accepted=False, error='Error: learning_drawing_library_invalid')

    assert _actual_video_host_validation(request, {}) == dict(accepted=True, libraries=None)
    assert 'drawing_libraries' not in request
    assert not (descriptor()['assets'].keys() & request['assets_sha256'].keys())
    assert request['assets_sha256']['three.module.min.js'] == shared.ASSET_SHA256['three.module.min.js']
    assert document == original_document and source == original_source
    assert binding['document'] == document and binding['input_sha256'] == digest(document)
    assert binding['source']['files'] == source['files']
    assert binding['source_sha256'] == binding['learning_source_sha256'] == source['source_sha256']
    assert request['host_sha256'] == host_sha256 == hashlib.sha256(VIDEO_HOST.read_bytes()).hexdigest()


@pytest.mark.parametrize('asset', ['ink-landscape.mjs', 'ink-landscape.LICENSE.txt'])
def test_three_cannot_depend_on_a_canvas_only_library(tmp_path, asset):
    value, document, source = _three_binding(tmp_path)
    source['files']['scene.js'] += f"\nimport './assets/{asset}';"
    source['source_sha256'] = video_protocol.source_sha256(source)
    with pytest.raises(ServiceError, match='learning_drawing_library_engine_not_admitted'):
        value._snapshot('unsupported-combination', source, document, [])
    assert not value.root.exists()


def test_three_receipt_cannot_claim_a_library_absent_from_its_render_request(tmp_path, monkeypatch):
    value, document, source = _three_binding(tmp_path)
    binding = value._snapshot('three', source, document, [])
    receipt = {}
    monkeypatch.setattr(shared.MethodAnimationRenderer, '_outputs', lambda *_: (receipt, b'{}', None))
    assert value._outputs(tmp_path, binding, {}) == (receipt, b'{}', None)
    receipt['drawing_library_observation'] = observation()
    with pytest.raises(ServiceError, match='learning_drawing_library_evidence_invalid'):
        value._outputs(tmp_path, binding, {})
