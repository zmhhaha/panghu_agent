"""Synthetic custody mutations exercise real admission checks, not live approval."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.deploy import picture_learning_acceptance as admission
from agent_service.profiles import video_picture_learning as protocol
from agent_service import worker_entry
from video_workbench import picture_learning_renderer as renderer_module
from test_picture_learning_catalog import prepare
from test_picture_learning_profile import document, result, PNG
import test_animation_method_acceptance as fixtures


def capture(tmp_path, monkeypatch, review=True):
    """Reuse the established signed candidate envelope/pod fixture, with new inputs."""
    old_write = fixtures.write_scenario

    def value(mode):
        context = document('review' if mode else 'create')
        context['constraints'] = dict(width=320,height=180,allowed_engines=['canvas'])
        for frame in context['observation']['frames']:
            if frame['purpose'] == 'output':
                frame['file_name'] = 'frame-one.png'
        return context

    def projected(directory, profile, task, files):
        context = json.loads(files[protocol.INPUT_NAME])
        for frame in context['observation']['frames']:
            files[frame['file_name']] = PNG
        return old_write(directory,profile,task,files)

    monkeypatch.setattr(fixtures, 'method', protocol)
    monkeypatch.setattr(fixtures, 'prepare', prepare)
    monkeypatch.setattr(fixtures, 'document', value)
    monkeypatch.setattr(fixtures, 'output', result)
    monkeypatch.setattr(fixtures, 'write_scenario', projected)
    directory,root,context,artifact = fixtures.capture(tmp_path,review=review)
    request = fixtures.read(root/'candidate-request.json')['envelope']
    change = dict(allowed_tools=['read_inputs','skill_search','skill_read','write'])
    fixtures.edit(root,'candidate-result.json',lambda value:value['observations']['capability'].update(change))
    fixtures.edit(root,'candidate-evidence.json',lambda value:value['observations']['capability'].update(change))
    record = fixtures.read(root/'candidate-evidence.json')
    record['model_image_evidence'] = dict(schema='agent-model-image-evidence.v1',
        evidence_scope='completed_upstream_transport',job_id=request['job_id'],attempt_id=request['attempt_id'],
        request_sha256=record['request_sha256'],images=[dict(sha256=hashlib.sha256(PNG).hexdigest(),
            model_request_completed=True,model_request_sha256='f'*64,media_type='image/png',
            byte_length=len(PNG),input_item_index=1,part_index=0)])
    fixtures.save(root/'candidate-evidence.json',record)
    return directory,root,context,artifact


def test_real_custody_verifier_accepts_new_review_protocol_without_artistic_approval(tmp_path,monkeypatch):
    directory,root,_,_ = capture(tmp_path,monkeypatch)
    _,report = admission.assess_candidate(directory)
    assert report['technical_host_admission_only'] is True
    assert report['model_frame_transport']['evidence_scope'] == 'completed_upstream_transport'
    assert report['model_frame_transport']['model_understanding_verified'] is False
    assert 'subjective_picture_quality' in report['not_claimed']
    before = {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir()}
    monkeypatch.setattr(worker_entry,'_read_operator_json',lambda path,**kwargs:fixtures.read(path))
    summary = admission.review_candidate(directory,tmp_path/'admitted')
    assert summary['accepted_host'] is True and summary['enabled'] is False
    assert summary['deployment_performed'] is False and summary['model_requests'] == 0
    assert {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir()} == before


@pytest.mark.parametrize('review',[False,True])
def test_missing_or_wrong_original_image_transport_cannot_admit_either_role(tmp_path,monkeypatch,review):
    directory,root,_,_ = capture(tmp_path,monkeypatch,review)
    fixtures.edit(root,'candidate-evidence.json',lambda value:value.pop('model_image_evidence'))
    with pytest.raises(ServiceError,match='image_evidence_missing'):
        admission.assess_candidate(directory)


def still_capture(tmp_path,monkeypatch):
    directory,root,context,artifact = capture(tmp_path,monkeypatch,False)
    runtime = SimpleNamespace(image=fixtures.RENDER_IMAGE,assets_dir=None,enabled=True,timeout=60,
        capabilities=lambda:dict(allowed_engines=['canvas'],assets={},runtime_identity=dict(
            image_digest='sha256:'+'a'*64,host_sha256='b'*64,engine_runtime_sha256='c'*64)))
    renderer = renderer_module.PictureLearningRenderer(tmp_path/'renders',runtime_renderer=runtime)
    def synthetic(root):
        request = fixtures.read(root/'input/request.json')
        plan = fixtures.read(root/'render-plan.json')
        work=root/'work'; work.mkdir()
        out=io.BytesIO(); Image.new('RGB',(320,180),'white').save(out,format='PNG'); png=out.getvalue()
        checksum=hashlib.sha256(png).hexdigest()
        fixtures.save(work/'output.png',png)
        receipt=dict(schema='video.picture-learning-render-receipt.v1',status='rendered',evidence_scope='static_only',
            request_identity=request['identity'],request_sha256=digest(request),
            **{key:request[key] for key in ('host_sha256','engine_runtime_sha256','project_runtime_sha256','engine','image_digest')},
            output_sha256=checksum,output_bytes=len(png),width=320,height=180,
            checks=dict(repeat_sha256=checksum,reload_sha256=checksum,seek_seconds=0),
            project_runtime=dict(core_sha256=renderer_module.HYPERFRAMES_CORE_SHA256,
                project_sha256=request['project_sha256'],core_seek_count=1,actual_seconds=0),
            engine_observation=dict(engine='canvas',contexts=[]))
        fixtures.save(work/'render-receipt.json',receipt)
        start = dict(schema='video.animation-method-render-start.v1',plan=plan,owner_token='a'*32,
            container_name='animation-method-synthetic')
        outcome=dict(schema='video.animation-method-render-outcome.v1',request_identity=request['identity'],
            request_sha256=digest(request),image=fixtures.RENDER_IMAGE,container_id='c'*64,
            cleanup=dict(status='removed_and_confirmed_absent',container_id='c'*64),
            status='rendered',automatic_retry=False,error=None,actual_image_id='sha256:'+'b'*64,owner_token=start['owner_token'],
            container_name=start['container_name'],execution_timeout_seconds=60)
        fixtures.save(root/'render-start.json',start)
        fixtures.save(root/'render-outcome.json',outcome)
        return outcome

    monkeypatch.setattr(renderer_module,'execute_method_render',synthetic)
    renderer.render('synthetic-admission',artifact['source'],context)
    return directory,renderer._directory('synthetic-admission'),context,artifact


def test_author_requires_real_bound_still_capture_and_direct_reference_transport(tmp_path,monkeypatch):
    directory,render,context,artifact = still_capture(tmp_path,monkeypatch)
    proof = admission.verify_renderer_capture(render,context,artifact,renderer_image=fixtures.RENDER_IMAGE)
    assert proof['evidence_scope'] == 'static_only' and proof['container_absent_verified'] is True
    assert proof['subjective_quality_approved'] is False
    _,report = admission.assess_candidate(directory,renderer_dir=render,renderer_image=fixtures.RENDER_IMAGE)
    assert report['renderer_proof']['picture_sha256'] == proof['picture_sha256']
    assert report['model_frame_transport']['observed_frame_ids'] == ['reference.png']


@pytest.mark.parametrize('changed',['input/scene/scene.js','host.mjs','work/output.png','work/render-receipt.json','render-outcome.json'])
def test_modified_source_image_runtime_or_stop_capture_is_rejected(tmp_path,monkeypatch,changed):
    _,render,context,artifact = still_capture(tmp_path,monkeypatch)
    path = render/changed
    if changed == 'render-outcome.json':
        fixtures.edit(render,changed,lambda value:value['cleanup'].update(status='unconfirmed'))
    else:
        path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises((ServiceError,ValueError)):
        admission.verify_renderer_capture(render,context,artifact,renderer_image=fixtures.RENDER_IMAGE)
