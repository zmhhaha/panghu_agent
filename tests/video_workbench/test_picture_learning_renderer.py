"""PNG custody/claims; synthetic receipts do not claim actual browser quality."""
from copy import deepcopy
import io
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles.video_animation_create import source_sha256
from video_workbench import picture_learning_renderer as module

IMAGE = 'registry.example/renderer:latest@sha256:'+'a'*64


def source():
    value = dict(engine='html-svg', strategy=dict(description='Deterministic reference still',tradeoffs='Static only'),
        files={'scene.html':'<html><body><script>window.__animationExperiment={seek(){}};</script></body></html>'})
    return dict(value,source_sha256=source_sha256(value))


def document():
    return dict(schema='video.picture-learning-input.v1',mode='create',project_id='project',
        experiment_id='experiment',work_kind='reproduction',goal='Study ink contour and negative space',criteria=[],
        constraints=dict(width=320,height=180,allowed_engines=['html-svg']),
        observation=dict(frames=[dict(file_name='reference.png',sha256='a'*64,purpose='reference')]),
        base_source=None,feedback='',knowledge_snapshot=None,prior_review=None,learned_method=None,assets={})


@pytest.fixture
def renderer(tmp_path):
    runtime = SimpleNamespace(image=IMAGE, assets_dir=None,enabled=True,timeout=60,
        capabilities=lambda:dict(allowed_engines=['html-svg'],assets={},runtime_identity=dict(
            image_digest='sha256:'+'a'*64,host_sha256='b'*64,engine_runtime_sha256='c'*64)))
    return module.PictureLearningRenderer(tmp_path/'renderer',runtime_renderer=runtime)


class SyntheticExecution:
    def __init__(self, *, fail=False, unknown=False):
        self.calls=0; self.fail=fail; self.unknown=unknown

    def __call__(self, root):
        self.calls+=1
        if self.unknown:
            raise TimeoutError('lost response')
        request=json.loads((root/'input/request.json').read_bytes())
        outcome=dict(schema='video.animation-method-render-outcome.v1',request_identity=request['identity'],
            request_sha256=digest(request),image=IMAGE,container_id='c'*64,
            cleanup=dict(status='removed_and_confirmed_absent',container_id='c'*64),
            status='failed' if self.fail else 'rendered',automatic_retry=False)
        (root/'render-outcome.json').write_bytes(canonical_bytes(outcome))
        if self.fail:
            return outcome
        work=root/'work'; work.mkdir()
        out=io.BytesIO(); Image.new('RGB',(320,180),'white').save(out,format='PNG'); png=out.getvalue()
        (work/'output.png').write_bytes(png)
        sha=module.digest_bytes(png)
        receipt=dict(schema='video.picture-learning-render-receipt.v1',status='rendered',evidence_scope='static_only',
            request_identity=request['identity'],request_sha256=digest(request),
            **{key:request[key] for key in ('host_sha256','engine_runtime_sha256','project_runtime_sha256','engine','image_digest')},
            output_sha256=sha,output_bytes=len(png),width=320,height=180,
            checks=dict(repeat_sha256=sha,reload_sha256=sha,seek_seconds=0),
            project_runtime=dict(core_sha256=module.HYPERFRAMES_CORE_SHA256,
                project_sha256=request['project_sha256'],core_seek_count=1,actual_seconds=0),
            engine_observation=dict(engine='html-svg',contexts=[]))
        (work/'render-receipt.json').write_bytes(canonical_bytes(receipt))
        return outcome


def test_png_is_verified_and_same_claim_is_never_rerendered(renderer,monkeypatch):
    execute=SyntheticExecution(); monkeypatch.setattr(module,'execute_method_render',execute)
    result=renderer.render('same-operation',source(),document())
    assert result['status']=='rendered' and result['technical_passed'] and result['stop_proven']
    assert result['mime']=='image/png' and result['evidence_scope']=='static_only'
    assert renderer.render('same-operation',source(),document())==result
    assert renderer.recover('same-operation')==result and execute.calls==1
    assert json.loads(open(result['source_path']).read())==source()


def test_unknown_claim_does_not_authorize_a_second_container(renderer,monkeypatch):
    execute=SyntheticExecution(unknown=True); monkeypatch.setattr(module,'execute_method_render',execute)
    result=renderer.render('uncertain',source(),document())
    assert result==dict(status='unknown',technical_passed=False,stop_proven=False)
    assert renderer.render('uncertain',source(),document())==result
    assert renderer.recover('uncertain')==result and execute.calls==1


def test_failed_render_preserves_proven_stop_without_media(renderer,monkeypatch):
    execute=SyntheticExecution(fail=True); monkeypatch.setattr(module,'execute_method_render',execute)
    result=renderer.render('failure',source(),document())
    assert result['status']=='failed' and result['stop_proven'] and not result['technical_passed']
    assert 'picture_path' not in result and execute.calls==1


@pytest.mark.parametrize('changed',['input/scene/scene.html','host.mjs','work/output.png','work/render-receipt.json'])
def test_post_completion_artifact_tampering_is_rejected(renderer,monkeypatch,changed):
    monkeypatch.setattr(module,'execute_method_render',SyntheticExecution())
    renderer.render('sealed',source(),document())
    path=renderer._directory('sealed')/changed
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises((ServiceError,ValueError)):
        renderer.lookup('sealed')


def test_idempotency_key_cannot_be_rebound_to_a_different_picture(renderer,monkeypatch):
    monkeypatch.setattr(module,'execute_method_render',SyntheticExecution())
    renderer.render('identity',source(),document())
    changed=deepcopy(document()); changed['goal']='A different target'
    with pytest.raises(ServiceError,match='picture_render_operation_conflict'):
        renderer.render('identity',source(),changed)


def test_static_capabilities_exclude_unconsumed_animation_library_assets(renderer):
    renderer.runtime_renderer.capabilities=lambda: dict(allowed_engines=['html-svg'],
        assets={'gsap.min.js':module.ASSET_SHA256['gsap.min.js'],'anime_timeline.mjs':'a'*64},
        authoring_libraries={'animejs':{'version':'4.5.0'}}, runtime_identity={})
    capabilities=renderer.capabilities()
    from video_workbench.ink_landscape_library import descriptor
    assert set(capabilities['assets'])=={'gsap.min.js'} | set(descriptor()['assets'])
    assert capabilities['drawing_libraries']=={'ink-landscape':descriptor()}
    assert 'anime_timeline.mjs' not in capabilities['assets']
    assert 'authoring_libraries' not in capabilities
    assert capabilities['evidence_scope']=='static_only'
