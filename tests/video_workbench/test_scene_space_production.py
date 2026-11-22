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
        if 'frame_range' in request:
            from PIL import Image
            output=io.BytesIO();Image.new('RGB',(1280,720),(240,235,220)).save(output,format='PNG')
            raw=output.getvalue()
        result=dict(sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),fps=24,
            frame_count=1 if 'frame_range' in request else frozen['plan']['duration_frames'],plan_sha256=frozen['plan_sha256'],
            source_sha256=request['source']['source_sha256'],snapshot_sha256=digest(request))
        job=dict(id='render-'+str(len(self.jobs)),status='completed',stop_proven=True,result=result,
            usage=dict(render_seconds=3),error=None)
        self.jobs[(owner,key)]=job
        self.artifacts[job['id']]=(raw,'image/png' if 'frame_range' in request else 'video/mp4')
        if self.lose_submit:raise ServiceError('lost_response',503)
        return deepcopy(job)

    def lookup(self,owner,key):return deepcopy(self.jobs.get((owner,key)))
    def get(self,owner,identity):
        for (saved_owner,_),row in self.jobs.items():
            if saved_owner==owner and row['id']==identity:return deepcopy(row)
        raise ServiceError('not_found',404)
    def cancel(self,owner,identity):return self.get(owner,identity)
    def video(self,owner,identity):self.get(owner,identity);return self.artifacts[identity]


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
    env.sequence=ProductionSequenceStore(tmp_path/'sequences.sqlite3')
    env.runs=ProductionRunStore(env.store,env.joint,env.motion,env.production,
        scene_space_adapter=env.adapter,sequence_store=env.sequence)
    return env


def begin(env,key='scene-space-production-test'):
    project=env.store.project(OWNER,env.project['id'])
    return env.runs.start(OWNER,project['id'],dict(expected_revision=project['revision'],
        joint_plan_version_id='adopted-plan-1',audio_mode='silent'),key)


def finish(env):
    for _ in range(24):env.runs.tick()


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


def test_uncovered_worlds_cannot_silently_pass_when_preview_budget_is_three(scene_env):
    env=scene_env;env.author.world_count=4
    run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id'])
    assert public['status']=='failed' and public['error']=='scene_space_preview_coverage_budget_exceeded'
    assert not env.renderer.submissions and not env.reviewer.submissions
    saved=env.spaces.get(OWNER,run['id'])['frozen']
    assert len(saved['plan']['worlds'])==4
    assert env.adapter._read(OWNER,run['id'])['source']['source_sha256']


def test_existing_production_http_route_dispatches_and_serves_new_scene_video(scene_env):
    env=scene_env
    app=create_app(db_path=env.store.path,local_mode=False,background=False,
        production_runs_enabled=True,joint_plan_enabled=True,scene_space_adapter=env.adapter,
        trusted_hosts={'testserver'})
    project=app.state.store.project(OWNER,env.project['id'])
    with TestClient(app) as client:
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
