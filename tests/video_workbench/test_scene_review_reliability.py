"""New visual contracts use synthetic custody fixtures, never art acceptance."""
from copy import deepcopy
import hashlib
import io
from random import Random

import pytest

from agent_service.contracts import ServiceError,digest
from agent_service.profiles import video_picture_review_v2 as v2
from video_workbench.scene_space_capture import SAMPLE_CAPTURE_POLICY,FRAME_CAPTURE_SCHEMA
from video_workbench.scene_space_production import SceneSpaceProductionAdapter
from video_workbench.scene_review_reliability import GATE_SCHEMA
from test_scene_space_production import (gated_scene_env,scene_env,legacy_env,begin,finish,
    SceneRenderer,WorldReview,OWNER)


class LosslessRenderer(SceneRenderer):
    def capabilities(self,owner):
        return dict(super().capabilities(owner),sample_capture=deepcopy(SAMPLE_CAPTURE_POLICY))

    def start(self,owner,request,key):
        row=super().start(owner,request,key)
        if request['document']['constraints'].get('sample_capture'):
            saved=self.jobs[(owner,key)]
            saved['result'].update(sample_capture=deepcopy(SAMPLE_CAPTURE_POLICY),
                sample_frame_range=deepcopy(request['frame_range']))
            row=deepcopy(saved)
        return row

    def frames(self,owner,identity,max_frames=3):
        row=self.get(owner,identity);frame_range=row['result']['sample_frame_range']
        output=io.BytesIO()
        from PIL import Image
        Image.new('RGB',(1280,720),(240,235,220)).save(output,format='PNG')
        raw=output.getvalue();count=row['result']['frame_count']
        return [dict(timestamp_seconds=frame/24,mime='image/png',raw=raw,
            capture=dict(schema=FRAME_CAPTURE_SCHEMA,absolute_frame=frame_range['start_frame']+frame,
                relative_frame=frame,format='png',quality=None,sha256=hashlib.sha256(raw).hexdigest(),
                bytes=len(raw))) for frame in sorted({0,count//2,count-1})][:max_frames]


class ClassifiedReview(WorldReview):
    profile=v2.PROFILE
    def __init__(self):
        super().__init__();self.finding='advisory';self.score=90;self.recommend_stop=False

    def collect(self,job_id,input_sha256,document):
        frames=document['observation']['frames'];ids=[frames[0]['frame_id']]
        criteria=document['constraints']['review_policy']['criteria']
        assessments=[dict(criterion_id=c['id'],status='satisfied',evidence_frame_ids=ids,
            reason='Synthetic fixture establishes this required criterion.')
            for c in criteria if c['requirement']=='required']
        findings=[]
        if self.finding:
            criterion=next(c for c in criteria if c['requirement']==
                ('optional' if self.finding=='advisory' else 'required'))
            uncertain=self.finding=='unresolved'
            findings=[dict(id='finding-1',criterion_id=criterion['id'],
                severity='advisory' if self.finding=='advisory' else 'blocking',
                certainty='uncertain' if uncertain else 'observed',evidence_frame_ids=ids,
                observation='One retained visual finding.',recommendation='A bounded appropriate follow-up.')]
            if self.finding!='advisory':
                assessments[0]['status']='unresolved' if uncertain else 'violation'
        review=dict(score=self.score,summary='Synthetic classified review.',assessments=assessments,
            findings=findings,limitations=['No continuous motion or user acceptance.'],
            stop_recommended=self.recommend_stop)
        return dict(schema=v2.RESULT_SCHEMA,input_sha256=input_sha256,mode='review',
            review=review,evidence_scope='static_only')


@pytest.fixture
def reliable_env(gated_scene_env):
    env=gated_scene_env;env.renderer=LosslessRenderer();env.classified=ClassifiedReview()
    env.adapter.renderer=env.renderer;env.adapter.structured_reviewer=env.classified
    original=env.adapter.freeze_configuration
    def configuration(owner,project):
        # Historical explicit classified contracts retain their original gate;
        # the availability of PNG capture no longer chooses this evaluator.
        value=env.adapter._classified_configuration(original(owner,project));value['max_pre_export_repairs']=0
        value.pop('configuration_sha256');value['configuration_sha256']=digest(value)
        return value
    env.adapter.freeze_configuration=configuration
    return env


def test_advisory_is_retained_without_repair_or_legacy_reviewer(reliable_env):
    env=reliable_env;run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id']);row=env.adapter._read(OWNER,run['id'])
    assert public['status']=='completed' and row['repair'] is None
    assert row['configuration']['review_gate_schema']==GATE_SCHEMA
    assert row['review']['decision']['passed']
    assert row['review']['decision']['advisory_finding_ids']==['finding-1']
    assert len(env.author.submissions)==len(env.classified.submissions)==1
    assert not env.reviewer.submissions
    binding=env.renderer.submissions[-1]['document']['constraints']['scene_review_binding']
    assert binding['result_sha256']==digest(row['review']['result'])
    assert binding['decision_sha256']==digest(row['review']['decision'])
    assert binding['frame_receipt_sha256']==digest(row['review']['frame_receipt'])
    assert binding['source_sha256']==row['source_sha256'] and binding['plan_sha256']==row['plan_sha256']
    document,files=env.classified.uploads[0]
    for frame,record in zip(document['observation']['frames'],document['constraints']['review_evidence']['frames']):
        raw=files[frame['file_name']]
        assert raw.startswith(b'\x89PNG') and hashlib.sha256(raw).hexdigest()==record['capture']['sha256']
        assert record['retained_sha256']==record['model_input_sha256']==frame['sha256']
        assert record['conversion']=='identity_original_png'
    assert all('sample_capture' not in request['document']['constraints']
        for request in env.renderer.submissions if 'duration_seconds' not in request['document']['constraints'])


@pytest.mark.parametrize('finding,score,stop',[
    ('violation',90,False),('unresolved',90,False),('advisory',79,False),(None,90,True)])
def test_required_uncertainty_low_score_or_stop_never_exports(reliable_env,finding,score,stop):
    env=reliable_env;env.classified.finding=finding;env.classified.score=score;env.classified.recommend_stop=stop
    run=begin(env);finish(env)
    row=env.adapter._read(OWNER,run['id'])
    assert env.runs.get(OWNER,run['id'])['status']=='failed'
    assert row['render_id'] is None and not row['review']['decision']['passed']
    assert len(env.author.submissions)==len(env.classified.submissions)==1


def test_lossless_capture_keeps_original_reviewer_and_gate(gated_scene_env):
    env=gated_scene_env;env.adapter.renderer=LosslessRenderer()
    run=begin(env);finish(env)
    row=env.adapter._read(OWNER,run['id'])
    assert env.runs.get(OWNER,run['id'])['status']=='completed'
    assert row['configuration']['review_profile']==env.reviewer.profile
    assert row['configuration']['sample_capture']==SAMPLE_CAPTURE_POLICY
    assert 'review_gate_schema' not in row['configuration']
    assert 'review_policy' not in row['review']['document']['constraints']
    assert 'review_canonicalization_revision' not in row['configuration']
    assert len(env.reviewer.submissions)==1
    document,files=env.reviewer.uploads[0]
    assert len(document['observation']['frames'])==sum(len(c['samples']) for c in row['clips'])+len(row['previews'])
    assert all(files[f['file_name']].startswith(b'\x89PNG') for f in document['observation']['frames'])


def test_frozen_gateway_dispatch_is_independent_of_today_capture_capability(reliable_env):
    env=reliable_env;run=begin(env);finish(env);row=env.adapter._read(OWNER,run['id'])
    env.renderer.capabilities=lambda owner:(_ for _ in ()).throw(AssertionError('today capability read'))
    assert env.adapter._review_gateway(row) is env.classified
    env.adapter.structured_reviewer=None
    with pytest.raises(ServiceError,match='scene_space_frozen_review_unavailable'):
        env.adapter._review_gateway(row)


def test_new_gateway_does_not_change_legacy_issue_or_stop_semantics(gated_scene_env):
    env=gated_scene_env;env.adapter.structured_reviewer=ClassifiedReview()
    run=begin(env);finish(env)
    row=env.adapter._read(OWNER,run['id'])
    assert env.runs.get(OWNER,run['id'])['status']=='completed'
    assert 'review_gate_schema' not in row['configuration'] and row['review']['result']['review']['stop_recommended']
    assert len(env.reviewer.submissions)==1 and not env.adapter.structured_reviewer.submissions


def test_lossless_transport_over_limit_preserves_png_and_rejects_before_upload(reliable_env):
    env=reliable_env;run=begin(env);finish(env);row=env.adapter._read(OWNER,run['id'])
    from PIL import Image
    output=io.BytesIO();Image.frombytes('RGB',(1280,720),Random(0).randbytes(1280*720*3)).save(output,format='PNG')
    raw=output.getvalue();assert 2*1024*1024<len(raw)<4*1024*1024
    sha=hashlib.sha256(raw).hexdigest()
    (env.adapter.root/'artifacts'/(sha+'.png')).write_bytes(raw)
    row['previews'][0]['artifact_sha256']=sha
    before=len(env.classified.uploads)
    with pytest.raises(ServiceError,match='scene_space_preview_observation_too_large'):
        env.adapter._review_files(row)
    assert len(env.classified.uploads)==before
    assert (env.adapter.root/'artifacts'/(sha+'.png')).read_bytes()==raw


@pytest.mark.parametrize('changes',[dict(schema='other-contract'),dict(relative_frame=999),dict(extra=True)])
def test_retained_capture_is_revalidated_when_rebuilding_review(reliable_env,changes):
    env=reliable_env;run=begin(env);finish(env);row=env.adapter._read(OWNER,run['id'])
    row['clips'][0]['samples'][0]['capture'].update(changes)
    with pytest.raises(ServiceError,match='scene_space_frame_capture_invalid'):
        env.adapter._review_files(row)


@pytest.mark.parametrize('part',['result','decision','frame_receipt'])
def test_export_rechecks_the_bound_review_and_transport(reliable_env,part):
    env=reliable_env;run=begin(env);finish(env);row=env.adapter._read(OWNER,run['id'])
    if part=='result':row['review']['result']['review']['score']=79
    elif part=='decision':row['review']['decision']['advisory_finding_ids']=[]
    else:row['review']['frame_receipt']['images']=[]
    with pytest.raises(ServiceError):
        env.adapter._render_document(row,env.spaces.get(OWNER,run['id']),export=True)
