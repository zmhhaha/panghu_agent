"""Library task delivery / custody tests; synthetic admission is not production proof."""
from copy import deepcopy
import json
import hashlib

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_animation_skill_method as skill_protocol
from video_workbench.animejs_library import ASSETS, descriptor, read_assets, enable_in_capabilities
from video_workbench.animation_method_renderer import MethodAnimationRenderer, PROJECT_RUNTIME, HYPERFRAMES_CORE_SHA256
from video_workbench.animation_method_service import AnimationMethodService
from test_animation_method_renderer import IMAGE, SOURCE, document
from test_animation_method_service import Gateway, Renderer, start, runtime_capabilities, CRITERIA


def candidate(tmp_path, **kwargs):
    assets = tmp_path/'assets'; assets.mkdir(exist_ok=True)
    for name, raw in read_assets().items(): (assets/name).write_bytes(raw)
    return MethodAnimationRenderer(tmp_path/'renderer', IMAGE, assets_dir=assets,
        hyperframes=True, legacy_bridge=True, **kwargs)


def test_library_candidate_not_advertised_and_opt_in_request_binds_v4(tmp_path):
    renderer = candidate(tmp_path, candidate_authoring_libraries=('animejs',))
    assert renderer.capabilities()['schema'].endswith('.v1')
    assert not set(ASSETS)&set(renderer.capabilities()['assets'])
    context = document()
    context['assets'].update(ASSETS)
    context['constraints']['authoring_libraries']={'animejs':descriptor()}
    binding=renderer._snapshot('anime-probe',SOURCE,context,[])
    assert binding['request']['schema']=='video.animation-method-render-request.v4'
    assert binding['request']['authoring_libraries']=={'animejs':descriptor()}
    assert binding['request']['assets_sha256']==ASSETS
    assert binding['request']['identity']==digest({k:v for k,v in binding['request'].items() if k!='identity'})
    renderer.candidate_authoring_libraries=()
    with pytest.raises(ServiceError,match='library_not_admitted'):
        renderer._snapshot('not-candidate',SOURCE,context,[])


def test_library_release_requires_real_measured_operation_not_only_asset_presence(tmp_path,monkeypatch):
    renderer=candidate(tmp_path)
    base=renderer.capabilities()
    path=tmp_path/'library-admission.json'
    value=dict(schema='video.animation-authoring-library-admission.v1',runtime_identity=base['runtime_identity'],
        authoring_libraries={'animejs':descriptor()}, evidence=dict(operation_id='probe',evidence_sha256='a'*64))
    path.write_text(json.dumps(value))
    renderer.authoring_library_admission_file=path
    project_sha=hashlib.sha256(PROJECT_RUNTIME.read_bytes()).hexdigest()
    measured=dict(schema='video.animation-method-render-request.v4',host_sha256=base['runtime_identity']['host_sha256'],
        engine_runtime_sha256=base['runtime_identity']['engine_runtime_sha256'],project_runtime_sha256=project_sha,
        hyperframes_core_sha256=HYPERFRAMES_CORE_SHA256)
    monkeypatch.setattr(renderer,'_row',lambda operation_id,**kwargs:({},dict(request=deepcopy(measured),image=IMAGE)))
    result=dict(technical_passed=True,runtime=dict(image=IMAGE,authoring_libraries={'animejs':descriptor()}),
        technical=dict(evidence_sha256='a'*64))
    result['runtime'].update(project_runtime='hyperframes-core-project-1', project_runtime_sha256=project_sha,
        hyperframes_core_sha256=HYPERFRAMES_CORE_SHA256)
    monkeypatch.setattr(renderer,'lookup',lambda operation_id:deepcopy(result))
    assert renderer.capabilities()['schema'].endswith('.v2')
    result['runtime']['authoring_libraries']={}
    with pytest.raises(ServiceError,match='admission_unproven'):renderer.capabilities()
    result['runtime']['authoring_libraries']={'animejs':descriptor()}
    result['runtime']['image']=IMAGE.replace('a'*64,'b'*64)
    with pytest.raises(ServiceError,match='admission_unproven'):renderer.capabilities()
    result['runtime']['image']=IMAGE
    for field in ['host_sha256','engine_runtime_sha256','project_runtime_sha256','hyperframes_core_sha256']:
        original=measured[field];measured[field]='f'*64
        with pytest.raises(ServiceError,match='admission_unproven'):renderer.capabilities()
        measured[field]=original


def test_library_metadata_and_self_contained_guidance_reach_real_method_input(tmp_path):
    class SkillGateway(Gateway): protocol=skill_protocol
    capabilities=enable_in_capabilities(runtime_capabilities(['html-svg','canvas','three']))
    service=AnimationMethodService(tmp_path/'lab',author_gateway=SkillGateway(),review_gateway=SkillGateway(True),
        renderer=Renderer(tmp_path/'renders'),runtime_capabilities=capabilities)
    started=start(service,criteria=CRITERIA)
    run=service.commissions.get('developer',started['id'])
    for mode in ['plan','create']:
        context=service._document('developer',run,mode)
        validated=skill_protocol.validate_input(context)
        assert validated['constraints']['authoring_libraries']=={'animejs':descriptor()}
        assert 'timeline.renderAt' in validated['constraints']['authoring_library_guidance']
        assert 'window.__animationExperiment.seek(seconds)' in validated['constraints']['authoring_library_guidance']
        assert set(ASSETS)<=set(validated['assets'])
    # Caller-supplied settings cannot broaden these trusted task fields.
    with pytest.raises(ServiceError):
        start(service,constraints={'authoring_libraries':{'remote':'untrusted'}})
