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
from agent_service.deploy import reference_learning_acceptance as admission
from agent_service.profiles import video_reference_learning as protocol
from agent_service import worker_entry
from video_workbench import video_learning_renderer as renderer_module
from test_reference_learning_catalog import prepare
from test_reference_learning_profile import document, result, PNG
import test_animation_method_acceptance as fixtures


def capture(tmp_path, monkeypatch, review=True):
    """Reuse the established signed candidate envelope/pod fixture, with new inputs."""
    old_write = fixtures.write_scenario

    def value(mode):
        context = document('review' if mode else 'create')
        context['constraints'] = dict(width=320,height=180,fps=24,duration_seconds=4,allowed_engines=['canvas'])
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
    assert 'subjective_animation_quality' in report['not_claimed']
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
