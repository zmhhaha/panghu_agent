"""Actual production-run dispatch, with stopped author/render transport fixtures.

Synthetic render bytes test custody and orchestration, not audiovisual quality.
Real scene compositor pixels are covered by its independent runtime tests.
"""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError,digest
from agent_service.profiles import video_picture_learning as protocol
from agent_service.profiles.video_animation_create import source_sha256
from video_workbench.production_run_store import ProductionRunStore
from video_workbench.scene_space_production import SceneSpaceProductionAdapter,author_document
from video_workbench.scene_space_plan import automatic_plan,default_budget
from video_workbench.scene_space_store import SceneSpaceStore
from video_workbench.production_sequence_store import ProductionSequenceStore
from video_workbench.app import create_app
from fastapi.testclient import TestClient
from test_production_runs import env as legacy_env,OWNER,children
from test_picture_learning_service import Gateway


class WorldAuthor(Gateway):
    profile=protocol.AUTHOR_PROFILE
    profile_revision='1'

    def __init__(self):
        super().__init__();self.invalid_motion=False;self.world_count=1

    def preflight(self,deadline):return dict(ready=True)

    def collect(self,job_id,input_sha256,document):
        world=json.loads((Path(__file__).resolve().parents[1]/'fixtures/scene_space/world.json').read_text(encoding='utf-8'))
        offer=document['constraints']['scene_space_production']
        worlds=[dict(deepcopy(world),id=world['id']+'-'+str(index)) for index in range(self.world_count)]
        world=worlds[0]
        intents=([dict(world_id=world['id'],weight=1,view='hold',focus=dict(x=700,y=400)) for world in worlds]
            if len(worlds)>1 else [dict(world_id=world['id'],weight=1,view='hold',focus=dict(x=700,y=400)),
                dict(world_id=world['id'],weight=1,view='push',focus=dict(x=700,y=400),zoom=1,end_zoom=1.1),
                dict(world_id=world['id'],weight=1,view='hold',focus=dict(x=700,y=400),zoom=1.1)])
        plan=automatic_plan(worlds,id='world-film-'+document['experiment_id'],
            viewport=offer['viewport'],duration_frames=offer['duration_frames'],budget=offer['budget'],
            intents=intents)
        source=dict(engine='canvas',strategy=dict(description='One world painter',tradeoffs='Synthetic transport fixture'),
            files={'scene.html':'<canvas data-scene-space-output></canvas><script src="/scene.js"></script>',
                'scene.css':'canvas{display:block}',
                'scene.js':'window.__sceneSpaceArtwork={paintObject(ctx,obj,world){ctx.fillRect(0,0,20,20)}};'})
        source['source_sha256']=source_sha256(source)
        if document['constraints'].get('scene_space_same_content_repair'):
            source['files']['scene.js']+='\n// Same-content source correction.'
            source['source_sha256']=source_sha256(source)
        spatial=dict(schema='video.scene-space-method.v1',world=world,plan=plan,
            source=dict(scope='scene_data_only',references=[],frame_ids=[],motion_observed=self.invalid_motion,
                audio_observed=False,whole_video_watched=False),hypotheses=[],principles=['Reuse stable world objects.'],
            stages=[],limitations=['Synthetic author transport fixture; no pixel quality claim.'])
        return dict(schema=protocol.RESULT_SCHEMA,input_sha256=input_sha256,mode='create',source=source,
            method=dict(scene_space=spatial),review=None,transfer_task=None,evidence_scope='static_only')

    def prepare_submission(self,*args,**kwargs):
        submit=super().prepare_submission(*args,**kwargs)
        def measured():
            try:job=submit()
            except ServiceError:
                for job in self.jobs.values():
                    job['usage']=dict(request_count=1,input_tokens=600,output_tokens=400,completeness='complete')
                raise
            self._job(job['job_id'])['usage']=dict(request_count=1,input_tokens=600,output_tokens=400,completeness='complete')
            return deepcopy(self._job(job['job_id']))
        return measured


class SceneRenderer:
    def __init__(self):
        self.jobs={};self.submissions=[];self.lose_submit=False;self.raw=b'synthetic media custody fixture';self.artifacts={}
        self.runtime_sha256='a'*64

    def capabilities(self,owner):
        return dict(admitted=True,engines=['canvas'],runtime_sha256=self.runtime_sha256,host_sha256='b'*64,
            authoring_constraints={})

    def start(self,owner,request,key):
        expected={key:value for key,value in self.capabilities(owner).items() if key!='authoring_constraints'}
        if request['document']['constraints']['scene_space_capability']!=expected:
            raise ServiceError('scene_space_renderer_changed',409)
        assert (owner,key) not in self.jobs
        self.submissions.append(deepcopy(request))
        frozen=request['scene_space']
        raw=self.raw
        static='duration_seconds' not in request['document']['constraints']
        if static:
            from PIL import Image
            output=io.BytesIO();Image.new('RGB',(1280,720),(240,235,220)).save(output,format='PNG')
            raw=output.getvalue()
        result=dict(sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),fps=24,
            frame_count=(request['frame_range']['end_frame']-request['frame_range']['start_frame']
                if 'frame_range' in request else frozen['plan']['duration_frames']),plan_sha256=frozen['plan_sha256'],
            source_sha256=request['source']['source_sha256'],snapshot_sha256=digest(request))
        job=dict(id='render-'+str(len(self.jobs)),status='completed',stop_proven=True,result=result,
            usage=dict(render_seconds=3),error=None)
        self.jobs[(owner,key)]=job
        self.artifacts[job['id']]=(raw,'image/png' if static else 'video/mp4')
        if self.lose_submit:raise ServiceError('lost_response',503)
        return deepcopy(job)

    def lookup(self,owner,key):return deepcopy(self.jobs.get((owner,key)))
    def get(self,owner,identity):
        for (saved_owner,_),row in self.jobs.items():
            if saved_owner==owner and row['id']==identity:return deepcopy(row)
        raise ServiceError('not_found',404)
    def cancel(self,owner,identity):return self.get(owner,identity)
    def video(self,owner,identity):self.get(owner,identity);return self.artifacts[identity]
    def frames(self,owner,identity,max_frames=3):
        row=self.get(owner,identity)
        from PIL import Image
        output=io.BytesIO();Image.new('RGB',(1280,720),(240,235,220)).save(output,format='JPEG')
        count=row['result']['frame_count']
        return [dict(timestamp_seconds=frame/24,mime='image/jpeg',raw=output.getvalue())
            for frame in sorted({0,count//2,count-1})][:max_frames]


class WorldReview(Gateway):
    profile=protocol.REVIEW_PROFILE
    profile_revision='1'
    def __init__(self):super().__init__(scores=[90])
    def preflight(self,deadline):return dict(ready=True)
    def prepare_submission(self,*args,**kwargs):
        submit=super().prepare_submission(*args,**kwargs)
        def measured():
            job=submit()
            self._job(job['job_id'])['usage']=dict(request_count=1,input_tokens=500,output_tokens=300,completeness='complete')
            return deepcopy(self._job(job['job_id']))
        return measured


@pytest.fixture
def scene_env(legacy_env,tmp_path):
    env=legacy_env;env.author=WorldAuthor();env.renderer=SceneRenderer();env.reviewer=WorldReview()
    env.spaces=SceneSpaceStore(tmp_path/'spaces')
    env.adapter=SceneSpaceProductionAdapter(tmp_path/'scene-production',space_store=env.spaces,
        author=env.author,reviewer=env.reviewer,renderer=env.renderer,enabled=True)
    env.current_scene_configuration=env.adapter.freeze_configuration
    def legacy_configuration(owner,project_id):
        value=env.current_scene_configuration(owner,project_id)
        for key in ('pre_export_gate_revision','max_preview_clip_frames','model_job_reserve_calls','model_job_reserve_tokens'):
            value.pop(key,None)
        value.update(max_pre_export_repairs=0,
            visual_gate_scope='actual_sampled_static_previews_not_whole_video_or_user_acceptance')
        value.pop('configuration_sha256');value['configuration_sha256']=digest(value)
        return value
    # These original regressions exercise recovery of the frozen v1 policy.
    env.adapter.freeze_configuration=legacy_configuration
    env.sequence=ProductionSequenceStore(tmp_path/'sequences.sqlite3')
    env.runs=ProductionRunStore(env.store,env.joint,env.motion,env.production,
        scene_space_adapter=env.adapter,sequence_store=env.sequence)
    return env


@pytest.fixture
def gated_scene_env(scene_env):
    env=scene_env;env.adapter.freeze_configuration=env.current_scene_configuration
    return env


def begin(env,key='scene-space-production-test'):
    project=env.store.project(OWNER,env.project['id'])
    return env.runs.start(OWNER,project['id'],dict(expected_revision=project['revision'],
        joint_plan_version_id='adopted-plan-1',audio_mode='silent'),key)


def finish(env):
    for _ in range(64):
        env.runs.tick()
        with env.store.transaction() as db:
            runs=[json.loads(row[0]) for row in db.execute('SELECT document FROM production_runs')]
        if runs and all(run['status'] in {'completed','failed','cancelled'} and not run.get('draining') for run in runs):
            break


def test_formal_run_authors_one_world_once_for_all_automatic_shots_and_registers_source(scene_env):
    env=scene_env;run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id'])
    assert public['status']=='completed' and public['renderer_mode']=='scene-space'
    assert public['progress']==dict(done=3,total=3)
    assert len(env.author.submissions)==len(env.reviewer.submissions)==1
    assert len(env.renderer.submissions)==4
    request=env.renderer.submissions[-1];frozen=request['scene_space']
    assert len(frozen['plan']['worlds'])==1 and len(frozen['plan']['shots'])==3
    assert request['document']['constraints']['duration_seconds']==16
    assert len(request['source']['files'])==3 and not request['document']['observation']['frames']
    assert all(item['world_source_sha256']==request['source']['source_sha256'] for item in public['items'])
    assert not children(env,'audio_operations') and not children(env,'motion_operations')
    usage=env.spaces.get(OWNER,run['id'])['usage_status']['used']
    assert usage['model_calls']==2 and usage['model_tokens']==1800 and usage['render_seconds']==12
    assert public['sequence_registration']['status']=='registered'
    assert public['sequence_registration']['source']['version_id']==public['draft_render_id']
    assert begin(env)['id']==run['id']


def test_author_preserves_long_film_script_and_style_without_truncating_to_goal_field(scene_env):
    env=scene_env;public=begin(env)
    with env.store.transaction() as db:run=env.runs._read(db,public['id'])
    run['film']['script']='Long original script. '*1000
    run['film']['style']='Keep the exact author style and spatial emphasis.'
    run['film']['scenes'][0]['visual_intent']='Detailed original visual intent. '*400
    run['film_sha256']=digest(run['film'])
    document=author_document(run,constraints={},budget=default_budget())
    assert len(document['goal'])<8000
    assert document['constraints']['scene_space_production']['film_context']==run['film']
    assert document['constraints']['scene_space_production']['film_sha256']==run['film_sha256']
    assert protocol.validate_input(document)==document


def test_five_minute_scene_plan_uses_six_execution_intervals_without_six_authors(scene_env):
    env=scene_env
    with env.store.transaction() as db:
        project=env.store._project(db,OWNER,env.project['id'])
        template=deepcopy(project['scenes'][0])
        project['scenes']=[dict(deepcopy(template),id='scene-'+str(i),title='Passage '+str(i),duration_seconds=25)
            for i in range(12)]
        env.store._save(db,project)
        db.execute('UPDATE joint_plan_heads SET applied_binding_sha256=? WHERE project_id=?',
            (env.joint._binding(project),project['id']))
    run=begin(env);finish(env)
    assert env.runs.get(OWNER,run['id'])['status']=='completed'
    saved=env.spaces.get(OWNER,run['id'])['frozen']
    assert saved['plan']['duration_frames']==7200 and len(saved['intervals'])==6
    assert len(saved['plan']['worlds'])==1 and len(env.author.submissions)==1
    assert len(env.renderer.submissions)==4


def test_restart_and_lost_author_submission_reconcile_same_paid_job(scene_env,tmp_path):
    env=scene_env;env.author.lose_submit=True
    run=begin(env);env.runs.tick()
    assert len(env.author.submissions)==1
    recovered=SceneSpaceProductionAdapter(tmp_path/'scene-production',space_store=env.spaces,
        author=env.author,reviewer=env.reviewer,renderer=env.renderer,enabled=True)
    env.runs=ProductionRunStore(env.store,env.joint,env.motion,env.production,
        scene_space_adapter=recovered,sequence_store=env.sequence)
    finish(env)
    assert env.runs.get(OWNER,run['id'])['status']=='completed'
    assert len(env.author.submissions)==1 and env.author.lookups


def test_lost_render_submission_does_not_spawn_second_export(scene_env):
    env=scene_env;env.renderer.lose_submit=True
    run=begin(env);finish(env)
    assert env.runs.get(OWNER,run['id'])['status']=='completed'
    assert len(env.renderer.submissions)==4


def test_static_world_author_cannot_claim_observed_video_motion(scene_env):
    env=scene_env;env.author.invalid_motion=True
    run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id'])
    assert public['status']=='failed'
    assert public['error']=='scene_space_method_observation_not_admitted'
    assert not env.renderer.submissions


def test_unknown_actual_model_tokens_block_formal_export(scene_env):
    env=scene_env
    prepare=env.author.prepare_submission
    def missing_usage(*args,**kwargs):
        submit=prepare(*args,**kwargs)
        def call():
            job=submit();env.author._job(job['job_id']).pop('usage',None);return job
        return call
    env.author.prepare_submission=missing_usage
    run=begin(env);finish(env)
    assert env.runs.get(OWNER,run['id'])['status']=='failed'
    assert len(env.renderer.submissions)==3
    assert all('frame_range' in request for request in env.renderer.submissions)
    assert not env.reviewer.submissions
    assert env.spaces.get(OWNER,run['id'])['usage_status']['used']['model_tokens'] is None


def test_disabled_knowledge_never_retrieves_snapshot_and_reference_is_empty(scene_env):
    env=scene_env
    class Library:
        def laboratory_snapshot(self,*args,**kwargs):pytest.fail('Disabled knowledge was retrieved')
    env.adapter.library_adapter=Library()
    with env.store.transaction() as db:
        project=env.store._project(db,OWNER,env.project['id'])
        project['joint_plan']['library_binding']=dict(use_wikiskill=False,release_id=None)
        env.store._save(db,project)
        db.execute('UPDATE joint_plan_heads SET applied_binding_sha256=? WHERE project_id=?',
            (env.joint._binding(project),project['id']))
    begin(env);finish(env)
    document=env.author.uploads[0][0]
    assert document['knowledge_snapshot'] is None and document['knowledge_binding']['use_wikiskill'] is False
    assert document['base_source'] is None and document['learned_method'] is None
    assert document['observation']==dict(frames=[])


def test_owner_cannot_read_another_productions_render(scene_env):
    env=scene_env;run=begin(env);finish(env)
    render=env.runs.get(OWNER,run['id'])['draft_render_id']
    with pytest.raises(ServiceError,match='not_found'):env.adapter.video('another-owner',render)


def test_preview_quality_failure_preserves_images_and_never_exports_whole_film(scene_env):
    env=scene_env;env.reviewer.scores=[65]
    run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id'])
    assert public['status']=='failed' and public['error']=='scene_space_preview_quality_not_met'
    assert len(env.renderer.submissions)==3
    assert all('frame_range' in request for request in env.renderer.submissions)
    assert len(list((env.adapter.root/'artifacts').glob('*.png')))>=1
    assert env.spaces.get(OWNER,run['id'])['usage_status']['used']['model_calls']==2
    assert public['scene_space']['visual_review']['score']==65


def test_independent_review_requires_host_transport_evidence_of_actual_pngs(scene_env):
    env=scene_env;env.reviewer.receipt_missing=True
    run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id'])
    assert public['status']=='failed' and public['error']=='scene_space_review_frame_transport_incomplete'
    assert len(env.renderer.submissions)==3


def test_closing_gate_reconciles_paid_author_without_admitting_new_previews(scene_env):
    env=scene_env;run=begin(env);env.runs.tick()
    assert len(env.author.submissions)==1
    env.adapter.enabled=False
    for _ in range(4):env.runs.tick()
    assert len(env.author.submissions)==1 and not env.renderer.submissions
    assert env.runs.get(OWNER,run['id'])['scene_space']['plan_sha256']
    assert env.runs.get(OWNER,run['id'])['status']=='running'
    assert begin(env)['id']==run['id']


def test_cancel_after_lost_export_response_does_not_claim_stop_before_remote_confirmation(scene_env):
    env=scene_env;original=env.renderer.start
    def lose_active_export(owner,request,key):
        child=original(owner,request,key)
        if 'frame_range' not in request:
            env.renderer.jobs[(owner,key)].update(status='running',stop_proven=False)
            raise ServiceError('lost_export_response',503)
        return child
    env.renderer.start=lose_active_export
    run=begin(env)
    for _ in range(12):
        env.runs.tick()
        if len(env.renderer.submissions)==4:break
    assert len(env.renderer.submissions)==4
    env.runs.cancel(OWNER,run['id']);env.runs.tick()
    pending=env.runs.get(OWNER,run['id'])
    assert pending['draining'] is True and pending['status']!='cancelled'
    assert pending['blocked_reason']=='production_run_cancellation_pending'
    key=next(key for key in env.renderer.jobs if key[1].startswith('scene-render-'))
    env.renderer.jobs[key].update(status='cancelled',stop_proven=True)
    env.runs.tick()
    stopped=env.runs.get(OWNER,run['id'])
    assert stopped['status']=='cancelled' and stopped['draining'] is False
    assert len(env.renderer.submissions)==4 and len(env.author.submissions)==1


def test_render_overrun_cannot_be_reported_as_completed_formal_work(scene_env):
    env=scene_env;original=env.renderer.start
    def measured(owner,request,key):
        child=original(owner,request,key)
        if 'frame_range' not in request:
            env.renderer.jobs[(owner,key)]['usage']['render_seconds']=4000
        return child
    env.renderer.start=measured
    run=begin(env);finish(env)
    assert env.runs.get(OWNER,run['id'])['status']=='failed'
    assert env.runs.get(OWNER,run['id'])['error']=='scene_space_budget_exhausted'
    assert env.spaces.get(OWNER,run['id'])['usage_status']['status']=='over_budget'


def test_budget_deadlines_and_reservations_bound_each_preview_and_full_export_before_launch(scene_env):
    env=scene_env;env.adapter.budget=default_budget(render_seconds=60)
    env.adapter.clock=lambda:1_000_000
    original=env.renderer.start;windows=[]
    def observe(owner,request,key):
        task=env.spaces.get(owner,request['document']['experiment_id'])
        remaining=task['usage_status']['remaining']['render_seconds']
        deadline=request['deadline_at']
        reservation=next(item for item in task['reservations'] if item['id']==key)
        assert 30<=deadline-env.adapter.clock()<=remaining
        assert reservation['render_seconds']==deadline-env.adapter.clock()
        windows.append(reservation['render_seconds'])
        return original(owner,request,key)
    env.renderer.start=observe
    run=begin(env);finish(env)
    assert env.runs.get(OWNER,run['id'])['status']=='completed'
    assert windows==[60,57,54,51]
    # The model author's host task still has its independent one-hour clock.
    assert env.adapter._read(OWNER,run['id'])['deadline_at']==1_003_600


def test_budget_below_minimum_render_launch_window_stops_without_starting_a_container(scene_env):
    env=scene_env;env.adapter.budget=default_budget(render_seconds=29)
    run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id'])
    assert public['status']=='failed' and public['error']=='scene_space_render_budget_insufficient'
    assert not env.renderer.submissions
    assert env.spaces.get(OWNER,run['id'])['usage_status']['used']['render_seconds']==0


def test_insufficient_remaining_window_preserves_completed_pngs_and_refuses_full_export(scene_env):
    env=scene_env;env.adapter.budget=default_budget(render_seconds=38)
    env.adapter.clock=lambda:1_000_000
    run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id'])
    assert public['status']=='failed' and public['error']=='scene_space_render_budget_insufficient'
    assert len(env.renderer.submissions)==3 and all('frame_range' in item for item in env.renderer.submissions)
    assert all(item['status']=='completed' for item in public['scene_space']['previews'])
    assert env.spaces.get(OWNER,run['id'])['usage_status']['remaining']['render_seconds']==29


def test_renderer_release_change_between_preview_and_export_cannot_rebind_the_same_film(scene_env):
    env=scene_env;run=begin(env)
    for _ in range(16):
        env.runs.tick()
        if len(env.reviewer.submissions)==1 and env.adapter._read(OWNER,run['id'])['status']=='planned':break
    assert len(env.renderer.submissions)==3
    first=env.renderer.submissions[0]['document']['constraints']['scene_space_capability']
    env.renderer.runtime_sha256='d'*64
    finish(env)
    public=env.runs.get(OWNER,run['id'])
    assert public['status']=='failed' and public['error']=='scene_space_renderer_changed'
    assert len(env.renderer.submissions)==3
    row=env.adapter._read(OWNER,run['id'])
    assert row['render_request']['document']['constraints']['scene_space_capability']==first


def test_every_rendered_world_receives_a_preview_within_the_fixed_sampling_budget(scene_env):
    env=scene_env;env.author.world_count=2
    run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id'])
    assert public['status']=='completed'
    plan=env.spaces.get(OWNER,run['id'])['frozen']['plan']
    frames={item['frame'] for item in public['scene_space']['previews']}
    assert all(any(shot['start_frame'] in frames for shot in plan['shots'] if shot['world_id']==world['id'])
        for world in plan['worlds'])


def test_new_gate_reviews_real_short_clip_samples_before_whole_export(gated_scene_env):
    env=gated_scene_env;run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id']);row=env.adapter._read(OWNER,run['id'])
    assert public['status']=='completed'
    assert len(env.renderer.submissions)==5 and len(row['previews'])==2 and len(row['clips'])==2
    assert all(2<=c['end_frame']-c['start_frame']<=48 and len(c['samples'])==2 for c in row['clips'])
    assert len(env.reviewer.uploads[0][0]['observation']['frames'])==6
    assert public['scene_space']['continuous_playback_reviewed'] is False
    assert public['scene_space']['layout_gate']['visibility_scope']=='bounds_only'
    assert public['scene_space']['temporal_preview_scope']=='bounded_clips_rendered_static_samples_reviewed'
    for clip in row['clips']:
        raw,mime=env.adapter.preview(OWNER,run['id'],clip['render_id'])
        assert mime=='video/mp4' and hashlib.sha256(raw).hexdigest()==clip['result']['sha256']
    assert env.spaces.get(OWNER,run['id'])['usage_status']['used']['render_seconds']==15


def test_one_bounded_source_repair_preserves_plan_goal_knowledge_and_original_previews(gated_scene_env):
    env=gated_scene_env;env.reviewer.scores=[65,90]
    run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id']);row=env.adapter._read(OWNER,run['id'])
    assert public['status']=='completed' and row['attempt']==1
    assert len(env.author.submissions)==len(env.reviewer.submissions)==2
    assert len(env.renderer.submissions)==9
    original=row['attempts'][0];repair=row['repair']['document']
    assert original['source_sha256']!=row['source_sha256'] and original['plan_sha256']==row['plan_sha256']
    assert repair['goal']==row['author_document']['goal']
    assert repair['knowledge_snapshot']==row['author_document']['knowledge_snapshot']
    assert repair['base_source'] is None and not any(f['purpose']=='reference' for f in repair['observation']['frames'])
    assert repair['constraints']['scene_space_same_content_repair']['source']==original['source']
    assert public['scene_space']['best_preview_attempt']==1
    old_preview=original['previews'][0]
    assert env.adapter.preview(OWNER,run['id'],old_preview['render_id'])[1]=='image/png'
    with pytest.raises(ServiceError,match='not_found'):
        env.adapter.preview('another-owner',run['id'],old_preview['render_id'])
    usage=env.spaces.get(OWNER,run['id'])['usage_status']['used']
    assert usage['model_calls']==4 and usage['model_tokens']==3600 and usage['render_seconds']==27


def test_repair_stops_after_one_attempt_and_retains_the_better_previous_preview(gated_scene_env):
    env=gated_scene_env;env.reviewer.scores=[65,60]
    run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id'])
    assert public['status']=='failed' and public['error']=='scene_space_preview_quality_not_met'
    assert len(env.author.submissions)==2 and len(env.reviewer.submissions)==2
    assert len(env.renderer.submissions)==8 and all('frame_range' in r for r in env.renderer.submissions)
    assert public['scene_space']['best_preview_attempt']==0
    assert len(public['scene_space']['retained_attempts'][0]['previews'])==2


def test_failed_repair_is_charged_once_and_drain_does_not_rewrite_failure_as_cancelled(gated_scene_env):
    env=gated_scene_env;env.reviewer.scores=[65]
    prepare=env.author.prepare_submission
    def fail_repair(operation,document,*args):
        submit=prepare(operation,document,*args)
        def call():
            job=submit()
            if 'scene_space_same_content_repair' in document['constraints']:
                env.author._job(job['job_id']).update(state='failed',stop_proven=True)
            return job
        return call
    env.author.prepare_submission=fail_repair
    run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id']);record=env.spaces.get(OWNER,run['id'])
    assert public['status']=='failed' and public['error']=='scene_space_repair_failed' and not public['draining']
    events=[event for event in record['usage']['events'] if event['phase']=='repair']
    assert len(events)==1 and events[0]['outcome']=='failed' and events[0]['model_tokens']==1000
    assert not any(r['status']=='active' for r in record['reservations'])


def test_clip_unknown_cost_retains_stopped_clip_samples_and_blocks_later_work(gated_scene_env):
    env=gated_scene_env;original=env.renderer.start
    def missing_usage(owner,request,key):
        child=original(owner,request,key)
        if key.startswith('scene-clip-'):env.renderer.jobs[(owner,key)]['usage']['render_seconds']=None
        return child
    env.renderer.start=missing_usage
    run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id']);row=env.adapter._read(OWNER,run['id'])
    assert public['status']=='failed' and not env.reviewer.submissions
    assert len(env.renderer.submissions)==3 and row['clips'][0]['status']=='completed'
    assert len(row['clips'][0]['samples'])==2
    assert env.spaces.get(OWNER,run['id'])['usage_status']['status']=='usage_unknown'


def test_lost_repair_response_is_reconciled_after_restart_without_second_paid_job(gated_scene_env,tmp_path):
    env=gated_scene_env;env.reviewer.scores=[65,90];run=begin(env)
    for _ in range(20):
        env.runs.tick()
        if env.adapter._read(OWNER,run['id'])['status']=='repair':break
    env.author.lose_submit=True;env.runs.tick()
    assert len(env.author.submissions)==2
    recovered=SceneSpaceProductionAdapter(tmp_path/'scene-production',space_store=env.spaces,
        author=env.author,reviewer=env.reviewer,renderer=env.renderer,enabled=True)
    env.adapter=recovered
    env.runs=ProductionRunStore(env.store,env.joint,env.motion,env.production,
        scene_space_adapter=recovered,sequence_store=env.sequence)
    finish(env)
    assert env.runs.get(OWNER,run['id'])['status']=='completed'
    assert len(env.author.submissions)==2 and any(key.startswith('scene-repair-') for key in env.author.lookups)


def test_first_author_admission_requires_its_real_request_allowance(gated_scene_env):
    env=gated_scene_env;env.adapter.budget=default_budget(model_calls=7)
    run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id'])
    assert public['status']=='failed' and public['error']=='scene_space_model_budget_unavailable'
    assert not env.author.submissions and not env.renderer.submissions


def test_uncovered_worlds_cannot_silently_pass_when_preview_budget_is_three(scene_env):
    env=scene_env;env.author.world_count=4
    run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id'])
    assert public['status']=='failed' and public['error']=='scene_space_preview_coverage_budget_exceeded'
    assert not env.renderer.submissions and not env.reviewer.submissions
    saved=env.spaces.get(OWNER,run['id'])['frozen']
    assert len(saved['plan']['worlds'])==4
    assert env.adapter._read(OWNER,run['id'])['source']['source_sha256']


def test_existing_production_http_route_dispatches_and_serves_new_scene_video(scene_env,monkeypatch):
    env=scene_env
    monkeypatch.setenv('SERVICE_AUTH_ISSUER','https://issuer.test')
    app=create_app(db_path=env.store.path,local_mode=False,background=False,
        production_runs_enabled=True,joint_plan_enabled=True,scene_space_adapter=env.adapter,
        trusted_hosts={'testserver'})
    project=app.state.store.project(OWNER,env.project['id'])
    with TestClient(app,base_url='https://testserver',headers={'Origin':'https://testserver'}) as client:
        client.headers['x-forwarded-user']=OWNER
        session=client.get('/api/session').json()
        assert session['scene_space_production'] is True
        client.headers['x-csrf-token']=session['csrf_token']
        response=client.post('/api/projects/'+project['id']+'/production-runs',
            json=dict(expected_revision=project['revision'],joint_plan_version_id='adopted-plan-1',audio_mode='silent'),
            headers={'Idempotency-Key':'scene-route-api-test'})
        assert response.status_code==202
        identity=response.json()['id']
        for _ in range(16):app.state.production_run_store.tick()
        complete=client.get('/api/production-runs/'+identity).json()
        assert complete['status']=='completed'
        assert all(item['candidate_current'] for item in complete['items'])
        render=complete['draft_render_id']
        assert client.get('/api/renders/'+render+'/video').content==env.renderer.raw
        preview=complete['scene_space']['previews'][0]['url']
        response=client.get(preview)
        assert response.status_code==200 and response.headers['content-type']=='image/png'
        rows=client.get('/api/projects/'+project['id']+'/renders').json()['renders']
        assert any(row['id']==render and row['status']=='completed' for row in rows)
        client.headers['x-forwarded-user']='another-owner'
        assert client.get('/api/renders/'+render+'/video').status_code==404
        assert client.get(preview).status_code==404
