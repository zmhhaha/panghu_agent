"""Admission identity tests; fixtures are synthetic, never live render evidence."""
from copy import deepcopy
import json

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from video_workbench import manim_method_admission as admission
from video_workbench.manim_generated_assets import freeze_generated_assets
from video_workbench.manim_method_resources import method_resource
from test_manim_resource import compiled, IMAGE


RESOURCE_IMAGE = 'registry.example/manim:latest@' + IMAGE
METHOD_IMAGE = 'registry.example/renderer:latest@sha256:' + 'b' * 64
SCOPE = dict(owner='casdoor/person@example.com', effect_id='effect',
    experiment_id='experiment', operation_id='resource-operation')
METHOD_OPERATION = 'method-operation'


class ResourceExecutor:
    image = RESOURCE_IMAGE
    def __init__(self):
        request, files, _ = compiled()
        envelope = freeze_generated_assets(files, request, **SCOPE, runtime_image_digest=IMAGE,
            stop_evidence=StopEvidence(SCOPE['operation_id'], True, ('original-stopped-resource',)))
        self.result = dict(status='completed', scope=SCOPE, image=self.image,
            stop_proven=True, generated_assets=envelope, plan_sha256='c' * 64)
    def lookup(self, **scope):
        assert scope == SCOPE
        return deepcopy(self.result)


class MethodRenderer:
    image = METHOD_IMAGE
    def __init__(self, resource):
        descriptor, _ = method_resource(resource.result['generated_assets'],
            expected_sha256=resource.result['generated_assets']['sha256'], expected_scope=SCOPE, image_digest=IMAGE)
        project = admission.sha(admission.PROJECT_RUNTIME.read_bytes())
        core = admission.HYPERFRAMES_CORE_SHA256
        self.identity = dict(image_digest=METHOD_IMAGE.rsplit('@', 1)[1],
            host_sha256='d' * 64, engine_runtime_sha256='e' * 64,
            admission_id='fixture-not-real-runtime')
        request = dict(schema='video.animation-method-render-request.v5',
            image_digest=self.identity['image_digest'], host_sha256=self.identity['host_sha256'],
            engine_runtime_sha256=self.identity['engine_runtime_sha256'],
            project_runtime_sha256=project, hyperframes_core_sha256=core,
            generated_resources=[descriptor])
        self.binding = dict(image=self.image, request=request, request_sha256=digest(request))
        self.result = dict(operation_id=METHOD_OPERATION, technical_passed=True,
            technical=dict(status='passed', evidence_sha256='f' * 64), runtime=dict(image=self.image,
                generated_resources=[descriptor], generated_resource_observation=dict(helpers_loaded=True),
                project_runtime='hyperframes-core-project-1', project_runtime_sha256=project,
                hyperframes_core_sha256=core, admission=dict(status='admitted', evidence_sha256='f' * 64)))
    def lookup(self, operation):
        assert operation == METHOD_OPERATION
        return deepcopy(self.result)
    def _row(self, operation, *, historical):
        assert operation == METHOD_OPERATION and historical is True
        return {}, deepcopy(self.binding)
    def capabilities(self):
        return dict(runtime_identity=deepcopy(self.identity))


def fixtures():
    resource = ResourceExecutor()
    renderer = MethodRenderer(resource)
    return resource, renderer


def build(resource, renderer):
    return admission.build_admission(resource_executor=resource, method_renderer=renderer,
        resource_scope=SCOPE, method_operation_id=METHOD_OPERATION)


def test_admission_binds_stopped_original_assets_and_actual_project_runtime(tmp_path):
    resource, renderer = fixtures()
    value = build(resource, renderer)
    assert value['method_request_sha256'] == digest(renderer.binding['request'])
    assert value['method_project_runtime_sha256'] == admission.sha(admission.PROJECT_RUNTIME.read_bytes())
    assert value['hyperframes_core_sha256'] == admission.HYPERFRAMES_CORE_SHA256
    assert value['resource_envelope_sha256'] == resource.result['generated_assets']['sha256']
    assert value['method_evidence_sha256'] == renderer.result['technical']['evidence_sha256']
    path = tmp_path / 'admission.json'
    path.write_bytes(canonical_bytes(value))
    assert admission.validate_admission(path, resource_executor=resource, method_renderer=renderer) == value


@pytest.mark.parametrize('field', ['host_sha256', 'engine_runtime_sha256',
    'project_runtime_sha256', 'hyperframes_core_sha256', 'image_digest'])
def test_same_image_historical_host_engine_project_or_core_cannot_admit_current_runtime(field):
    resource, renderer = fixtures()
    renderer.binding['request'][field] = '1' * 64
    renderer.binding['request_sha256'] = digest(renderer.binding['request'])
    with pytest.raises(ServiceError, match='runtime_changed'):
        build(resource, renderer)


@pytest.mark.parametrize('field', ['project_runtime', 'project_runtime_sha256',
    'hyperframes_core_sha256', 'image'])
def test_declared_request_does_not_replace_actual_historical_runtime_proof(field):
    resource, renderer = fixtures()
    renderer.result['runtime'][field] = 'other-runtime'
    with pytest.raises(ServiceError, match='runtime_changed'):
        build(resource, renderer)


@pytest.mark.parametrize('field,value', [('stop_proven', False), ('status', 'running'),
    ('scope', dict(SCOPE, owner='someone-else')), ('image', METHOD_IMAGE), ('plan_sha256', 'invalid')])
def test_generation_must_have_exact_scope_pin_plan_and_stopped_result(field, value):
    resource, renderer = fixtures()
    resource.result[field] = value
    with pytest.raises(ServiceError, match='generation_not_proven'):
        build(resource, renderer)


@pytest.mark.parametrize('change', ['operation', 'technical', 'descriptor', 'helper', 'request-descriptor', 'request-sha', 'evidence'])
def test_wrong_method_operation_resource_or_evidence_cannot_be_admitted(change):
    resource, renderer = fixtures()
    if change == 'operation': renderer.result['operation_id'] = 'other-method'
    elif change == 'technical': renderer.result['technical_passed'] = False
    elif change == 'descriptor': renderer.result['runtime']['generated_resources'][0]['scope']['owner'] = 'someone-else'
    elif change == 'helper': renderer.result['runtime']['generated_resource_observation']['helpers_loaded'] = False
    elif change == 'request-descriptor':
        renderer.binding['request']['generated_resources'] = []
        renderer.binding['request_sha256'] = digest(renderer.binding['request'])
    elif change == 'request-sha': renderer.binding['request_sha256'] = '1' * 64
    elif change == 'evidence': renderer.result['runtime']['admission']['evidence_sha256'] = '1' * 64
    with pytest.raises(ServiceError):
        build(resource, renderer)


def test_existing_admission_is_rechecked_against_current_runtime_and_result(tmp_path):
    resource, renderer = fixtures()
    value = build(resource, renderer)
    path = tmp_path / 'admission.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    renderer.identity['host_sha256'] = '2' * 64
    with pytest.raises(ServiceError, match='runtime_changed'):
        admission.validate_admission(path, resource_executor=resource, method_renderer=renderer)


@pytest.mark.parametrize('raw', [b'', b'{}', b'{', b'x' * (64 * 1024 + 1)],
    ids=['empty', 'missing-fields', 'invalid-json', 'oversized'])
def test_malformed_or_oversized_admission_is_rejected(tmp_path, raw):
    resource, renderer = fixtures()
    path = tmp_path / 'invalid.json'
    path.write_bytes(raw)
    with pytest.raises(ServiceError, match='file_invalid'):
        admission.validate_admission(path, resource_executor=resource, method_renderer=renderer)
