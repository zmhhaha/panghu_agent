"""Existing formal evaluation consumes shared sampling without changing method."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_animation_review as visual
from video_workbench.production_refinement_driver import ProductionRefinementDriver
from test_film_creation_workflow import OWNER,env
from test_production_refinement_driver import baseline,state
from test_visual_sampling_http import env as sampling_env, fake_decoder
from test_animation_observation import transport_receipt


def driver(env,sampling_env,tmp_path):
    reviewer=SimpleNamespace(profile=visual.REVIEW_PACKED_PROFILE,preflight=lambda deadline:dict(ready=True))
    return ProductionRefinementDriver(env.store,env.workflow,reviewer,tmp_path/'formal',
        sampling_client=sampling_env.client)


def test_formal_driver_defers_to_executor_and_reuses_original_completed_bundle(env,sampling_env,tmp_path,monkeypatch):
    real=driver(env,sampling_env,tmp_path)
    context=real.freeze(OWNER,env.project['id'],baseline(env));current=state(context)
    calls=fake_decoder(monkeypatch,duration=context['film']['total_seconds'])
    with pytest.raises(ServiceError,match='production_refinement_sampling_pending'):
        real._bundle('review-key',context,current)
    assert calls==[]
    sampling_id=real._sampling_id('review-key',context,current)
    assert sampling_env.client.get(OWNER,sampling_id)['state']=='queued'
    sampling_env.service.advance(OWNER,sampling_id)
    document,files=real._bundle('review-key',context,current)
    assert document['schema']==visual.REVIEW_INPUT_SCHEMA
    assert document['source_sha256']==context['film_sha256']
    assert document['media']['sha256']==context['source']['sha256']
    assert document['goal'].endswith(context['film']['understanding_goal'])
    assert document['prompt_revision']=='visual-evidence-r1'
    assert len(document['observation']['frames'])>2
    assert all(name.endswith('.jpg') or name==visual.REVIEW_INPUT_NAME for name in files)
    assert not (real.root/current['id']/'source.mp4').exists()
    assert (real.root/current['id']/'sampling-reference.json').is_file()
    before=len(calls)
    monkeypatch.setattr(real.film,'video',lambda *a:pytest.fail('completed evidence fetched original media again'))
    assert real._bundle('review-key',context,current)==(document,files)
    assert len(calls)==before


def test_missing_sampler_cannot_fallback_to_local_decoder(env,tmp_path,monkeypatch):
    reviewer=SimpleNamespace(profile=visual.REVIEW_PACKED_PROFILE,preflight=lambda deadline:dict(ready=True))
    real=ProductionRefinementDriver(env.store,env.workflow,reviewer,tmp_path/'formal')
    context=real.freeze(OWNER,env.project['id'],baseline(env))
    calls=fake_decoder(monkeypatch)
    with pytest.raises(ServiceError,match='production_refinement_shared_sampling_unavailable'):
        real._bundle('review-key',context,state(context))
    assert not calls


def test_cancelling_pending_review_cancels_its_sampling_without_new_review(env,sampling_env,tmp_path):
    real=driver(env,sampling_env,tmp_path)
    context=real.freeze(OWNER,env.project['id'],baseline(env));current=state(context)
    with pytest.raises(ServiceError,match='production_refinement_sampling_pending'):
        real._bundle('review-key',context,current)
    def no_admission(key):raise ServiceError('film_review_not_found',404)
    real.reviewer.cancel_operation=no_admission
    real.cancel('review','review-key',context,current)
    result=sampling_env.client.get(OWNER,real._sampling_id('review-key',context,current))
    assert result['state']=='cancelled' and result['stop_proven']


@pytest.mark.parametrize('wrong_size',[False,True])
def test_completed_formal_review_checks_actual_retained_model_parts(env,sampling_env,tmp_path,monkeypatch,wrong_size):
    real=driver(env,sampling_env,tmp_path)
    context=real.freeze(OWNER,env.project['id'],baseline(env));current=state(context)
    fake_decoder(monkeypatch,duration=context['film']['total_seconds'])
    with pytest.raises(ServiceError,match='production_refinement_sampling_pending'):
        real._bundle('review-key',context,current)
    sampling_env.service.advance(OWNER,real._sampling_id('review-key',context,current))
    document,files=real._bundle('review-key',context,current)
    control=transport_receipt(document)
    for frame,row in zip(document['observation']['frames'],control['images']):
        row['byte_length']=len(files[frame['file_name']])
    if wrong_size:control['images'][0]['byte_length']+=1
    reviewer=real.reviewer
    reviewer.lookup=lambda key:dict(job_id=control['job_id'])
    reviewer.poll=lambda job:dict(job_id=job,state='completed',stop_proven=True,stop_proof=dict(stopped=True))
    reviewer.collect=lambda *args:dict(issues=[],summary='Only the supplied frames were checked.',
        evidence_scope='visual_only',limitations=['Sampled frames only.'])
    reviewer.model_frame_receipt=lambda job:control
    if wrong_size:
        with pytest.raises(ServiceError,match='animation_model_frame_evidence_incomplete'):
            real.lookup('review','review-key',context,current)
    else:
        receipt=real.lookup('review','review-key',context,current)
        assert receipt['state']=='completed' and receipt['stop_proven']
        assert receipt['output']['observation']['observed_frame_ids']==[
            frame['frame_id'] for frame in document['observation']['frames']]
        assert receipt['output']['audio_reviewed'] is False
