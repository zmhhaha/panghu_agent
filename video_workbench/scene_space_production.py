"""Whole-film scene planning over one stopped, source-preserving world author.

The picture author creates new content without reference frames or an inherited
baseline. Its result remains static-only evidence. The separately admitted host
owns camera evaluation and video rendering. Historical film-module v1 inputs,
packages, renderer releases and retry identities are not reinterpreted here.
"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_picture_learning as picture
from .picture_learning_gateway import PictureAuthorGateway,PictureReviewGateway
from .scene_space_learning import scene_method_guidance, validate_scene_method
from .scene_space_plan import default_budget, validate_plan
from .scene_space_preview import preview_frames

SCHEMA = 'video.scene-space-production.v1'
CONFIG_SCHEMA = 'video.scene-space-production-configuration.v1'
RENDERER_MODE = 'scene-space'
ACTIVE = frozenset({'queued','starting','submitting','running','reconciling','cancelling','unknown'})
MAX_JOURNAL_BYTES = 16 * 1024 * 1024
MIN_RENDER_WINDOW_SECONDS = 30
MAX_PREVIEW_RENDER_SECONDS = 60
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z')


def _require(value, code='scene_space_production_invalid', status=422):
    if not value:
        raise ServiceError(code,status)


def _identity(value):
    _require(type(value) is str and _ID.fullmatch(value) is not None)


def _sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


class SceneSpaceWorldAuthorGateway(PictureAuthorGateway):
    """Reuse the admitted static profile with a separate business namespace."""
    business_prefix = 'scene-space-world-author:'
    idempotency_prefix = 'scene-world-'
    task_timeout_seconds = 3600


class SceneSpaceWorldReviewGateway(PictureReviewGateway):
    """Actual bounded preview images, retaining the static-only review profile."""
    business_prefix='scene-space-world-review:'
    idempotency_prefix='scene-world-review-'
    task_timeout_seconds=3600


def _author_guidance(frames, budget):
    return dict(schema='video.scene-space-production-author-offer.v1',
        method=scene_method_guidance('scene_data_only'),
        world_schema='video.scene-world.v1',plan_schema='video.scene-space-plan.v1',
        duration_frames=frames,fps=24,viewport=dict(width=1280,height=720),
        budget=deepcopy(budget),
        world_fields=['schema','id','revision','seed','style','space','layers','objects','layout','relations'],
        object_fields=['id','kind','layer_id','parent_id','bounds','transform','depth','anchors'],
        world_contract=dict(space_fields=['projection','bounds'],projections=['planar','scattered','geometric'],
            rect_fields=['x','y','width','height'],layer_fields=['id','order','parallax'],
            optional_layer_fields=['opacity','blur','cache_policy','mask_region_id'],
            transform_fields=['x','y','scale_x','scale_y','rotation'],rotation_units='degrees',
            depth_fields=['kind','value'],depth_kinds=['ordinal','geometric','unknown'],
            unknown_depth=dict(kind='unknown',value=None),larger_depth_is_nearer=True,
            anchors='Map anchor ID to [local_x,local_y] within object bounds.',
            layout_fields=['grid','points','regions'],grid_fields=['origin','spacing','columns','rows'],
            point_fields=['id','position','depth'],region_kinds=['rect','polygon'],
            relation_kinds=['attachment','support','scale','occlusion'],
            attachment_fields=['id','kind','object_id','anchor','target_object_id','target_anchor','tolerance'],
            support_fields=['id','kind','object_id','anchor','region_id','tolerance'],
            scale_fields=['id','kind','object_id','reference_object_id','metric','min_ratio','max_ratio'],
            occlusion_fields=['id','kind','front_id','back_id']),
        plan_fields=['schema','id','revision','fps','duration_frames','viewport','worlds','shots','budget','provenance'],
        shot_fields=['id','world_id','world_revision','start_frame','end_frame','camera','local_animation','transition'],
        camera_fields=['max_zoom','keyframes'],camera_key_fields=['frame','x','y','zoom','easing'],
        plan_provenance=dict(origin='generated',source_refs=[],evidence_scope='scene_data_only',
            motion_observed=False,audio_observed=False,whole_video_watched=False),
        method_template=dict(schema='video.scene-space-method.v1',world=None,plan=None,
            source=dict(scope='scene_data_only',references=[],frame_ids=[],motion_observed=False,
                audio_observed=False,whole_video_watched=False),hypotheses=[],principles=[],stages=[],limitations=[]),
        artwork=dict(registration='window.__sceneSpaceArtwork',canvas_selector='canvas[data-scene-space-output]',
            callback='paintObject(ctx, object, world)',local_coordinates=True,
            camera_owner='host',source_files=['scene.html','scene.css','scene.js']),
        instructions=[
            'Create the complete worlds and all camera decisions once for the entire film, before painting. Reuse a small appropriate number of stable worlds across shots; storyboard scene boundaries do not require new worlds.',
            'The pre-export static preview budget is at most three images. Required samples include the film start, greatest camera zoom, final frame and the first shot frame of every distinct world. Plan these to overlap within the three-image budget; omitted worlds cannot silently pass review.',
            'Return a complete method.scene_space with a non-null validated plan, source.scope=scene_data_only, empty references and frame_ids, and all observation flags false. This is new authorship, not observed camera or video evidence.',
            'Use the exact host duration_frames, fps, viewport and budget. Plan and world revision start at 1. Keyframes use global absolute integer frames, x/y are world view centres, zoom is finite and bounded; intervals are half-open.',
            'World space.bounds must cover every layer view at all allowed camera positions. Use explicit depth, stable object IDs, parent-local transforms and anchors. Grid and named points assist layout; do not infer depth from image Y.',
            'Choose movement, still cameras and local dynamics from the goal. No compulsory pan, zoom, fog, birds, three-layer layout or black foreground. Keep a fixed principal viewing direction; no orbit or unseen backside reconstruction.',
            'Write each object painter once in the existing three source files. Register window.__sceneSpaceArtwork.paintObject(ctx,object,world) and canvas[data-scene-space-output]. Paint in object-local coordinates; the host owns transforms, layers, masks, cache resolution and absolute-frame camera seeking.',
            'This admitted compositor caches static object painters only: layer cache_policy must be static. Local animation uses the plan absolute-frame transform and opacity tracks, not per-frame painter mutation.',
            'Do not load a fourth source file or a remote scene asset. Do not animate cameras with requestAnimationFrame, wall-clock time or accumulated state. Recipe references are inert identity metadata, never permission to fetch or execute code.',
            'Preserve the full goal, visual style and storyboard intent. No reference or previous reproduction source is supplied. WikiSkill contains optional methods only and never grants runtime capabilities.',
        ])


def author_document(run, *, constraints, budget, knowledge_binding=None, knowledge_snapshot=None):
    """New static authorship; an empty transfer observation is explicitly valid."""
    film=run['film'];frames=round(film['total_seconds']*24)
    _require(digest(film)==run['film_sha256'] and 1<=frames<=7200,
        'scene_space_production_film_changed',409)
    bounds=dict(width=1280,height=720,allowed_engines=['canvas'],
        scene_space_production=_author_guidance(frames,budget))
    # Preserve the full unabridged film separately instead of truncating a long
    # storyboard to fit the historical still profile's 8k goal field.
    bounds['scene_space_production'].update(film_context=deepcopy(film),film_sha256=run['film_sha256'])
    bounds['scene_space_capability']={key:deepcopy(value)
        for key,value in run['scene_space_configuration']['render_capability'].items()
        if key!='authoring_constraints'}
    for key in ('assets','drawing_libraries','drawing_library_api','runtime_identity'):
        if key in constraints:bounds[key]=deepcopy(constraints[key])
    goal=(film['title']+'\nCreate the complete film described by '
        'constraints.scene_space_production.film_context. Preserve its full understanding_goal, '
        'style, script and every storyboard visual_intent; these fields are supplied without truncation.')
    document=dict(schema=picture.INPUT_SCHEMA,mode='create',project_id=run['project_id'],
        experiment_id=run['id'],work_kind='transfer',goal=goal,
        criteria=['Preserve the complete film intent and its style.',
            'Use stable world objects, explicit spatial relations and bounded camera coverage.',
            'Reuse painted worlds across shots; static views and no local dynamics are valid.'],
        constraints=bounds,observation=dict(frames=[]),base_source=None,feedback='',
        prior_review=None,learned_method=None,knowledge_snapshot=deepcopy(knowledge_snapshot))
    if knowledge_binding is not None:document['knowledge_binding']=deepcopy(knowledge_binding)
    if 'assets' in constraints:document['assets']=deepcopy(constraints['assets'])
    return picture.validate_input(document)


def _measured_usage(job):
    """A stopped transport's counts, preserving missing token measurements."""
    usage=job.get('usage')
    if type(usage) is not dict:
        return dict(model_calls=1,model_tokens=None)
    calls=usage.get('request_count')
    calls=calls if type(calls) is int and 1<=calls<=2**53-1 else 1
    values=[usage.get(key) for key in ('input_tokens','output_tokens')]
    # Normalized input_tokens already includes cached input tokens; adding the
    # cache counters again would double-charge those tokens.
    tokens=(sum(values) if usage.get('completeness')=='complete' and type(usage.get('request_count')) is int
        and usage['request_count']>=1
        and all(type(v) is int and v>=0 for v in values) else None)
    return dict(model_calls=calls,model_tokens=tokens)


class SceneSpaceProductionAdapter:
    """Durable new-route coordinator, called by the existing production runner.

    The renderer is the private executor's asynchronous owner-scoped client.
    Its start/get/lookup/cancel calls never borrow the historical film host.
    """
    def __init__(self,root,*,space_store,author,renderer,reviewer=None,library_adapter=None,
                 enabled=False,budget=None,clock=time.time):
        _require(type(enabled) is bool)
        self.root=Path(root).absolute();self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'scene-production.sqlite3'
        self.space_store,self.author,self.renderer=space_store,author,renderer
        self.reviewer=reviewer
        self.library_adapter,self.enabled,self.clock=library_adapter,enabled,clock
        self.budget=deepcopy(budget) if budget is not None else default_budget()
        with closing(self._connect()) as db:
            db.execute('CREATE TABLE IF NOT EXISTS scene_space_productions ('
                'owner TEXT NOT NULL,run_id TEXT NOT NULL,project_id TEXT NOT NULL,'
                'document BLOB NOT NULL,sha256 TEXT NOT NULL,PRIMARY KEY(owner,run_id))')

    def _connect(self):
        _require(not any(path.is_symlink() or getattr(path,'is_junction',lambda:False)()
            for path in (self.path,*self.path.parents)),'scene_space_production_path_invalid')
        db=sqlite3.connect(self.path,timeout=15,isolation_level=None)
        db.row_factory=sqlite3.Row;db.execute('PRAGMA synchronous=FULL')
        return db

    def _read(self,owner,run_id):
        with closing(self._connect()) as db:
            row=db.execute('SELECT * FROM scene_space_productions WHERE owner=? AND run_id=?',
                (owner,run_id)).fetchone()
        _require(row is not None,'not_found',404)
        try:value=json.loads(row['document'])
        except (ValueError,TypeError,UnicodeError):raise ServiceError('scene_space_production_journal_changed',409) from None
        _require(type(value) is dict and canonical_bytes(value)==bytes(row['document']) and digest(value)==row['sha256']
            and value['owner']==owner and value['run_id']==run_id,
            'scene_space_production_journal_changed',409)
        return value

    def _write(self,value,*,insert=False):
        raw=canonical_bytes(value)
        _require(len(raw)<=MAX_JOURNAL_BYTES,'scene_space_production_journal_too_large',413)
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            if insert:
                db.execute('INSERT INTO scene_space_productions VALUES (?,?,?,?,?)',
                    (value['owner'],value['run_id'],value['project_id'],raw,digest(value)))
            else:
                changed=db.execute('UPDATE scene_space_productions SET document=?,sha256=? '
                    'WHERE owner=? AND run_id=?',(raw,digest(value),value['owner'],value['run_id'])).rowcount
                _require(changed==1,'scene_space_production_journal_missing',409)
            db.commit()
        return value

    def freeze_configuration(self,owner,project_id):
        _require(self.enabled and self.author is not None and self.reviewer is not None and self.renderer is not None,
            'scene_space_production_unavailable',503)
        _require(self.budget['model']['limit_calls']>0 and self.budget['model']['limit_tokens']>0
            and self.budget['render']['limit_seconds']>0,'scene_space_production_budget_unavailable',409)
        self.author.preflight(self.clock()+30)
        self.reviewer.preflight(self.clock()+30)
        caps=self.renderer.capabilities(owner)
        # Only the configured private executor can admit this host and painter API.
        _require(type(caps) is dict and caps.get('admitted') is True and 'canvas' in caps.get('engines',[])
            and _sha(caps.get('runtime_sha256')) and _sha(caps.get('host_sha256')),
            'scene_space_production_runtime_unavailable',503)
        value=dict(schema=CONFIG_SCHEMA,available=True,renderer_mode=RENDERER_MODE,
            author_profile=self.author.profile,author_revision=self.author.profile_revision,
            review_profile=self.reviewer.profile,review_revision=self.reviewer.profile_revision,
            render_capability=deepcopy(caps),max_seconds=300,fps=24,audio_modes=['silent'],
            budget=deepcopy(self.budget),preview_min_score=80,max_preview_frames=3,max_pre_export_repairs=0,
            visual_gate_scope='actual_sampled_static_previews_not_whole_video_or_user_acceptance')
        value['configuration_sha256']=digest(value)
        return value

    def _prepare(self,run):
        try:
            row=self._read(run['owner'],run['id'])
            _require(row['film_sha256']==run['film_sha256']
                and row['binding_sha256']==run['binding_sha256']
                and row['configuration']==run['scene_space_configuration'],
                'scene_space_production_frozen_input_changed',409)
            return row
        except ServiceError as error:
            if error.code!='not_found':raise
        configuration=run['scene_space_configuration']
        binding=deepcopy(run.get('library_binding'))
        snapshot=None
        if binding is None:
            enabled=True
            if self.library_adapter is not None:
                binding=self.library_adapter.pin(run['owner'],run['id'],use_wikiskill=enabled)
            else:
                binding=dict(batch_id=run['id'],use_wikiskill=True,release_id=None,
                    release_sha256=None,publisher_owner=None,release_sequence=None)
        if binding.get('use_wikiskill',binding.get('enabled',True)):
            if self.library_adapter is not None:
                snapshot=self.library_adapter.laboratory_snapshot(run['owner'],binding,
                    drawing_libraries=configuration['render_capability'].get('drawing_libraries',{}))
        constraints=configuration['render_capability'].get('authoring_constraints',{})
        document=author_document(run,constraints=constraints,budget=configuration['budget'],
            knowledge_binding=binding,knowledge_snapshot=snapshot)
        row=dict(schema=SCHEMA,owner=run['owner'],run_id=run['id'],project_id=run['project_id'],
            film_sha256=run['film_sha256'],binding_sha256=run['binding_sha256'],
            configuration=deepcopy(configuration),status='author',stop_proven=False,error=None,
            author_document=document,author_input_sha256=digest(document),
            author_operation_id='scene-world-'+run['id'],author_input_refs=None,
            author_submit_attempted=False,author_job_id=None,author_result=None,author_usage=None,
            author_usage_evidence=None,
            source=None,source_sha256=None,plan_sha256=None,render_request=None,
            previews=[],review=None,
            render_request_key='scene-render-'+run['id'],render_id=None,render_result=None,
            render_submit_attempted=False,created_at=self.clock(),deadline_at=self.clock()+3600)
        return self._write(row,insert=True)

    @staticmethod
    def _project(run,row):
        run['scene_space']=dict(status=row['status'],stop_proven=row['stop_proven'],
            author_operation_id=row['author_operation_id'],author_job_id=row['author_job_id'],
            plan_sha256=row['plan_sha256'],source_sha256=row['source_sha256'],
            render_id=row['render_id'],error=row['error'],
            previews=[dict(frame=p['frame'],render_id=p['render_id'],status=p['status'],
                url='/api/production-runs/'+run['id']+'/scene-previews/'+p['render_id']
                    if p['render_id'] and p['status']=='completed' else None) for p in row['previews']],
            visual_review=deepcopy(row['review']['result']['review']) if row.get('review') and row['review'].get('result') else None,
            visual_evidence_scope='sampled_static_previews_only')
        run['phase']='scene_space_'+row['status']
        if row['plan_sha256']:
            for item in run['items']:
                item['world_source_sha256']=row['source_sha256']
                item['scene_space_plan_sha256']=row['plan_sha256']
        if row['status']=='completed':
            run.update(status='completed',phase='completed',error=None,finished_at=run.get('finished_at') or time.time(),
                draft_render_id=row['render_id'],render_snapshot_sha256=row['render_result']['snapshot_sha256'])
            for item in run['items']:item.update(status='completed',error=None)
        elif row['status'] in {'failed','cancelled'}:
            run.update(status=row['status'],error=row['error'],finished_at=run.get('finished_at') or time.time())
            run['draining']=not row['stop_proven']
        else:run.update(status='running',error=row['error'])

    def _author(self,row,*,admit):
        operation=row['author_operation_id'];document=row['author_document']
        if not row['author_submit_attempted']:
            if not admit:return row
            _require(self.freeze_configuration(row['owner'],row['project_id'])==row['configuration'],
                'scene_space_production_configuration_changed',409)
            if row['author_input_refs'] is None:
                row['author_input_refs']=self.author.upload(document,{picture.INPUT_NAME:canonical_bytes(document)})
                self._write(row)
            admit_call=self.author.prepare_submission(operation,document,row['deadline_at'],row['author_input_refs'])
            row['author_submit_attempted']=True;self._write(row)
            try:
                job=admit_call()
            except Exception:
                row['error']='scene_space_world_submission_uncertain'
                return self._write(row)
            row['author_job_id']=job['job_id'];self._write(row)
            return row
        try:
            job=(self.author.poll(row['author_job_id']) if row['author_job_id'] else self.author.lookup(operation))
        except ServiceError as error:
            if error.status==404:
                row['error']='scene_space_world_submission_unconfirmed'
                return self._write(row)
            raise
        row['author_job_id']=job['job_id']
        if job['state'] in ACTIVE:return self._write(row)
        if job.get('stop_proven') is not True:
            row['error']='scene_space_world_stop_unproven'
            return self._write(row)
        row['author_usage']=_measured_usage(job)
        row['author_usage_evidence']=deepcopy(job.get('usage'))
        self._write(row)  # Retain failed/invalid-result costs before interpretation.
        if job['state']!='completed':
            row.update(status='failed',stop_proven=True,error='scene_space_world_'+job['state'])
            return self._write(row)
        result=self.author.collect(job['job_id'],row['author_input_sha256'],document)
        picture.validate_result(result,row['author_input_sha256'],document)
        method=result.get('method')
        _require(type(method) is dict and 'scene_space' in method,
            'scene_space_production_method_required',409)
        spatial=validate_scene_method(method['scene_space'],source_scope='scene_data_only',source_refs=[],frame_ids=[])
        _require(spatial['plan'] is not None,'scene_space_production_plan_required',409)
        plan=deepcopy(spatial['plan'])
        _require(plan['duration_frames']==document['constraints']['scene_space_production']['duration_frames']
            and plan['viewport']==dict(width=1280,height=720) and plan['revision']==1,
            'scene_space_production_clock_changed',409)
        _require(plan['budget']==row['configuration']['budget'],'scene_space_production_budget_changed',409)
        plan=validate_plan(plan)
        saved=self.space_store.freeze(row['owner'],row['run_id'],plan)
        self.space_store.record_usage(row['owner'],row['run_id'],id=operation,
            phase='generation',outcome='completed',**row['author_usage'],render_seconds=0)
        row.update(author_result=result,source=picture.validate_source(result['source'],constraints=document['constraints']),
            source_sha256=result['source']['source_sha256'],plan_sha256=saved['frozen']['plan_sha256'],
            status='preview',error=None)
        return self._write(row)

    def _preview(self,row,*,admit):
        saved=self.space_store.get(row['owner'],row['run_id']);plan=saved['frozen']['plan']
        if not row['previews']:
            samples=preview_frames(plan)
            candidates=[(key['zoom'],min(key['frame'],shot['end_frame']-1))
                for shot in plan['shots'] for key in shot['camera']['keyframes']]
            greatest=max(candidates,key=lambda pair:(pair[0],pair[1]))[1]
            required={samples[0],greatest,samples[-1]}
            seen=set()
            for shot in plan['shots']:
                if shot['world_id'] not in seen:
                    required.add(shot['start_frame']);seen.add(shot['world_id'])
            if len(required)>row['configuration']['max_preview_frames']:
                error=ServiceError('scene_space_preview_coverage_budget_exceeded',409)
                error.details=dict(required_frames=sorted(required),world_ids=sorted(seen),
                    max_preview_frames=row['configuration']['max_preview_frames'],
                    retained_plan_sha256=row['plan_sha256'],retained_source_sha256=row['source_sha256'])
                raise error
            selected=sorted(required)
            row['previews']=[dict(frame=frame,key='scene-preview-'+row['run_id']+'-'+str(frame),
                request=None,attempted=False,render_id=None,result=None,status='pending',artifact_sha256=None)
                for frame in selected]
            self._write(row)
        preview=next((item for item in row['previews'] if item['status']!='completed'),None)
        if preview is None:
            row['status']='review';return self._write(row)
        if preview['request'] is None:
            if not admit:return row
            window=self._render_window(row,preview=True)
            document=deepcopy(row['author_document'])
            document['constraints'].pop('duration_seconds',None)
            preview['reserved_render_seconds']=window['seconds']
            preview['request']=dict(source=deepcopy(row['source']),document=document,scene_space=saved['frozen'],
                frame_range=dict(start_frame=preview['frame'],end_frame=preview['frame']+1),deadline_at=window['deadline_at'])
            self._write(row)
        if preview['render_id'] is None:
            child=self.renderer.lookup(row['owner'],preview['key'])
            if child is None:
                if preview['attempted']:
                    row['error']='scene_space_preview_submission_unconfirmed';return self._write(row)
                if not admit:return row
                _require(preview['request']['deadline_at']-self.clock()>=MIN_RENDER_WINDOW_SECONDS,
                    'scene_space_render_budget_insufficient',409)
                self.space_store.reserve(row['owner'],row['run_id'],preview['key'],phase='preview',
                    render_seconds=preview['reserved_render_seconds'])
                preview['attempted']=True;self._write(row)
                try:child=self.renderer.start(row['owner'],preview['request'],preview['key'])
                except ServiceError as error:
                    if error.status<500:
                        self.space_store.record_usage(row['owner'],row['run_id'],id=preview['key'],phase='preview',
                            outcome='failed',model_calls=0,model_tokens=0,render_seconds=0)
                        row.update(status='failed',stop_proven=True,error=error.code)
                        return self._write(row)
                    row['error']='scene_space_preview_submission_uncertain';return self._write(row)
                except Exception:
                    row['error']='scene_space_preview_submission_uncertain';return self._write(row)
            preview['render_id']=child['id'];self._write(row)
        child=self.renderer.get(row['owner'],preview['render_id'])
        if child['status'] in ACTIVE:return row
        if child.get('stop_proven') is not True:
            row['error']='scene_space_preview_stop_unproven';return self._write(row)
        self.space_store.record_usage(row['owner'],row['run_id'],id=preview['key'],phase='preview',
            outcome='completed' if child['status']=='completed' else 'failed',model_calls=0,model_tokens=0,
            render_seconds=(child.get('usage') or {}).get('render_seconds'))
        _require(child['status']=='completed','scene_space_preview_failed',409)
        result=child.get('result')
        _require(type(result) is dict and result.get('plan_sha256')==row['plan_sha256']
            and result.get('source_sha256')==row['source_sha256'] and result.get('frame_count')==1,
            'scene_space_preview_binding_changed',409)
        raw,mime=self.renderer.video(row['owner'],preview['render_id'])
        _require(type(raw) is bytes and 1<=len(raw)<=8*1024*1024 and mime=='image/png'
            and hashlib.sha256(raw).hexdigest()==result['sha256'],
            'scene_space_preview_media_changed',409)
        from PIL import Image
        try:
            with Image.open(io.BytesIO(raw)) as image:
                _require(image.format=='PNG' and image.size==(1280,720),'scene_space_preview_dimensions_changed',409)
                image.verify()
        except (OSError,ValueError,Image.DecompressionBombError):
            raise ServiceError('scene_space_preview_image_invalid',409) from None
        sha=hashlib.sha256(raw).hexdigest();directory=self.root/'artifacts';directory.mkdir(exist_ok=True)
        path=directory/(sha+'.png')
        _require(not path.is_symlink() and not directory.is_symlink(),'scene_space_production_path_invalid')
        if path.exists():_require(path.read_bytes()==raw,'scene_space_preview_media_changed',409)
        else:path.write_bytes(raw)
        preview.update(status='completed',artifact_sha256=sha,result=deepcopy(result))
        row['error']=None
        if all(item['status']=='completed' for item in row['previews']):row['status']='review'
        return self._write(row)

    def _review_files(self,row):
        document=deepcopy(row['author_document']);document['mode']='review'
        frames=[];files={}
        from PIL import Image
        for item in row['previews']:
            raw=(self.root/'artifacts'/(item['artifact_sha256']+'.png')).read_bytes()
            _require(hashlib.sha256(raw).hexdigest()==item['artifact_sha256'],'scene_space_preview_media_changed',409)
            suffix='png'
            if len(raw)>2*1024*1024:
                with Image.open(io.BytesIO(raw)) as image:
                    converted=io.BytesIO();image.convert('RGB').save(converted,format='JPEG',quality=88)
                    raw=converted.getvalue();suffix='jpg'
            _require(len(raw)<=2*1024*1024,'scene_space_preview_observation_too_large',413)
            name='preview-'+str(item['frame'])+'.'+suffix
            frames.append(dict(file_name=name,sha256=hashlib.sha256(raw).hexdigest(),purpose='output',
                frame_id='preview-'+str(item['frame'])))
            files[name]=raw
        document['observation']=dict(frames=frames)
        document['constraints']['scene_space_static_review']=dict(frames=[item['frame'] for item in row['previews']],
            scope='actual_static_samples_only',instructions=[
                'Judge these actual rendered samples against the full goal, style, object scale, composition, depth and occlusion.',
                'Report defects and uncertainty. Do not claim continuous playback, motion, sound or whole-video observation.',
                'This is independent static quality advice; neither a score nor transport receipt is user artistic acceptance.'])
        document=picture.validate_input(document)
        files[picture.INPUT_NAME]=canonical_bytes(document)
        return document,files

    def _review(self,row,*,admit):
        if row['review'] is None:
            document,_=self._review_files(row)
            row['review']=dict(id='scene-review-'+row['run_id'],document=document,input_sha256=digest(document),
                input_refs=None,attempted=False,job_id=None,result=None,usage=None)
            self._write(row)
        review=row['review'];document=review['document']
        if not review['attempted']:
            if not admit:return row
            self.reviewer.preflight(row['deadline_at'])
            if review['input_refs'] is None:
                rebuilt,files=self._review_files(row)
                _require(rebuilt==document,'scene_space_review_input_changed',409)
                review['input_refs']=self.reviewer.upload(document,files);self._write(row)
            call=self.reviewer.prepare_submission(review['id'],document,row['deadline_at'],review['input_refs'])
            self.space_store.reserve(row['owner'],row['run_id'],review['id'],phase='review',model_calls=1)
            review['attempted']=True;self._write(row)
            try:job=call()
            except Exception:
                row['error']='scene_space_review_submission_uncertain';return self._write(row)
            review['job_id']=job['job_id'];return self._write(row)
        try:job=self.reviewer.poll(review['job_id']) if review['job_id'] else self.reviewer.lookup(review['id'])
        except ServiceError as error:
            if error.status==404:
                row['error']='scene_space_review_submission_unconfirmed';return self._write(row)
            raise
        review['job_id']=job['job_id']
        if job['state'] in ACTIVE:return self._write(row)
        if job.get('stop_proven') is not True:
            row['error']='scene_space_review_stop_unproven';return self._write(row)
        review['usage']=_measured_usage(job)
        self.space_store.record_usage(row['owner'],row['run_id'],id=review['id'],phase='review',
            outcome='completed' if job['state']=='completed' else 'failed',**review['usage'],render_seconds=0)
        _require(job['state']=='completed','scene_space_review_failed',409)
        result=self.reviewer.collect(job['job_id'],review['input_sha256'],document)
        picture.validate_result(result,review['input_sha256'],document)
        receipt=self.reviewer.model_frame_receipt(job['job_id'])
        expected={frame['sha256'] for frame in document['observation']['frames']}
        observed={item.get('sha256') for item in receipt.get('images',[]) if item.get('model_request_completed') is True}
        _require(expected<=observed,'scene_space_review_frame_transport_incomplete',409)
        review['result']=result;review['frame_receipt']=receipt;self._write(row)
        _require(result['review']['score']>=row['configuration']['preview_min_score']
            and not result['review']['issues'],'scene_space_preview_quality_not_met',409)
        row['status']='planned';row['error']=None
        return self._write(row)

    def _render(self,row,*,admit):
        saved=self.space_store.get(row['owner'],row['run_id'])
        _require(saved['frozen']['plan_sha256']==row['plan_sha256'],
            'scene_space_production_plan_changed',409)
        if row['render_request'] is None:
            if not admit:return row
            window=self._render_window(row,preview=False)
            document=deepcopy(row['author_document'])
            document['constraints']['duration_seconds']=saved['frozen']['plan']['duration_frames']/24
            row['reserved_render_seconds']=window['seconds']
            row['render_request']=dict(source=deepcopy(row['source']),document=document,
                scene_space=saved['frozen'],deadline_at=window['deadline_at'])
            self._write(row)
        if row['render_id'] is None:
            try:child=self.renderer.lookup(row['owner'],row['render_request_key'])
            except ServiceError as error:
                if error.status!=404:raise
                child=None
            if child is None:
                if row['render_submit_attempted']:
                    row['error']='scene_space_render_submission_unconfirmed'
                    return self._write(row)
                if not admit:return row
                _require(row['render_request']['deadline_at']-self.clock()>=MIN_RENDER_WINDOW_SECONDS,
                    'scene_space_render_budget_insufficient',409)
                self.space_store.require_export(row['owner'],row['run_id'],reserve_seconds=row['reserved_render_seconds'])
                self.space_store.reserve(row['owner'],row['run_id'],row['render_request_key'],
                    phase='export',render_seconds=row['reserved_render_seconds'])
                row['render_submit_attempted']=True;row['status']='rendering';self._write(row)
                try:child=self.renderer.start(row['owner'],row['render_request'],row['render_request_key'])
                except ServiceError as error:
                    if error.status<500:
                        self.space_store.record_usage(row['owner'],row['run_id'],id=row['render_request_key'],
                            phase='export',outcome='failed',model_calls=0,model_tokens=0,render_seconds=0)
                        row.update(status='failed',stop_proven=True,error=error.code)
                        return self._write(row)
                    row['error']='scene_space_render_submission_uncertain';return self._write(row)
                except Exception:
                    row['error']='scene_space_render_submission_uncertain'
                    return self._write(row)
            row['render_id']=child['id'];row['status']='rendering';self._write(row)
        child=self.renderer.get(row['owner'],row['render_id'])
        if child['status'] in ACTIVE:return row
        if child.get('stop_proven') is not True:
            row['error']='scene_space_render_stop_unproven'
            return self._write(row)
        usage=child.get('usage') or {}
        settled=self.space_store.record_usage(row['owner'],row['run_id'],id=row['render_request_key'],
            phase='export',outcome='completed' if child['status']=='completed' else 'failed',
            model_calls=0,model_tokens=0,render_seconds=usage.get('render_seconds'))
        if settled['usage_status']['status']!='within_budget':
            row.update(status='failed',stop_proven=True,render_result=deepcopy(child.get('result')),
                error='scene_space_usage_unknown' if settled['usage_status']['status']=='usage_unknown'
                    else 'scene_space_budget_exhausted')
            return self._write(row)
        if child['status']!='completed':
            row.update(status='failed',stop_proven=True,error=child.get('error') or 'scene_space_render_failed')
            return self._write(row)
        result=child.get('result')
        _require(type(result) is dict and result.get('plan_sha256')==row['plan_sha256']
            and result.get('source_sha256')==row['source_sha256'] and _sha(result.get('sha256'))
            and _sha(result.get('snapshot_sha256')) and result.get('fps')==24
            and result.get('frame_count')==saved['frozen']['plan']['duration_frames'],
            'scene_space_production_render_binding_changed',409)
        row.update(status='completed',stop_proven=True,render_result=deepcopy(result),error=None)
        return self._write(row)

    def _render_window(self,row,*,preview):
        """Freeze an execution ceiling from resource capacity, never the model clock.

        A reservation covers the entire allowed execution window. Sequential
        settlement releases unused capacity while retaining measured spending;
        a different concurrent reservation cannot borrow this same window.
        """
        record=self.space_store.get(row['owner'],row['run_id'])
        remaining=record['usage_status']['remaining']['render_seconds']
        _require(type(remaining) in (int,float),'scene_space_render_budget_insufficient',409)
        held=sum(item['render_seconds'] for item in record['reservations'] if item['status']=='active')
        now=self.clock()
        seconds=min(remaining-held,row['deadline_at']-now,
            MAX_PREVIEW_RENDER_SECONDS if preview else remaining-held)
        _require(seconds>=MIN_RENDER_WINDOW_SECONDS,'scene_space_render_budget_insufficient',409)
        return dict(seconds=seconds,deadline_at=now+seconds)

    def advance(self,run,*,admit,persist):
        _require(run.get('audio_mode')=='silent','scene_space_production_audio_not_admitted')
        admit=bool(admit and self.enabled)
        row=self._prepare(run)
        self._project(run,row);persist(run)
        if row['status']=='author':row=self._author(row,admit=admit)
        elif row['status']=='preview':row=self._preview(row,admit=admit)
        elif row['status']=='review':row=self._review(row,admit=admit)
        elif row['status'] in {'planned','rendering'}:row=self._render(row,admit=admit)
        self._project(run,row);persist(run)
        return deepcopy(run['scene_space'])

    def cancel(self,run):
        previous={key:run.get(key) for key in ('status','error','finished_at','phase')}
        try:row=self._read(run['owner'],run['id'])
        except ServiceError as error:
            if error.status==404:return True
            raise
        if row['status'] in {'completed','failed','cancelled'}:return row['stop_proven']
        if row['render_submit_attempted'] and row['render_id'] is None:
            try:child=self.renderer.lookup(row['owner'],row['render_request_key'])
            except ServiceError:return False
            if child is None:return False
            row['render_id']=child['id'];self._write(row)
        if row['render_id']:
            child=self.renderer.cancel(row['owner'],row['render_id'])
            if child.get('stop_proven') is not True:return False
            self._settle_cancelled_render(row,child,row['render_request_key'],'export')
        elif row.get('review') and row['review']['attempted']:
            review=row['review']
            try:job=self.reviewer.poll(review['job_id']) if review['job_id'] else self.reviewer.lookup(review['id'])
            except ServiceError:return False
            if job['state'] in ACTIVE:
                self.reviewer.cancel(job['job_id']);return False
            if job.get('stop_proven') is not True:return False
            record=self.space_store.get(row['owner'],row['run_id'])
            if not any(event['id']==review['id'] for event in record['usage']['events']):
                self.space_store.record_usage(row['owner'],row['run_id'],id=review['id'],phase='review',
                    outcome='completed' if job['state']=='completed' else 'cancelled',
                    **_measured_usage(job),render_seconds=0)
        elif any(item['attempted'] and item['status']!='completed' for item in row['previews']):
            for item in row['previews']:
                if item['attempted'] and item['render_id'] is None:
                    try:child=self.renderer.lookup(row['owner'],item['key'])
                    except ServiceError:return False
                    if child is None:return False
                    item['render_id']=child['id'];self._write(row)
                if item['render_id'] and item['status']!='completed':
                    child=self.renderer.cancel(row['owner'],item['render_id'])
                    if child.get('stop_proven') is not True:return False
                    self._settle_cancelled_render(row,child,item['key'],'preview')
        elif row['author_submit_attempted']:
            try:job=self.author.poll(row['author_job_id']) if row['author_job_id'] else self.author.lookup(row['author_operation_id'])
            except ServiceError:return False
            if job['state'] in ACTIVE:
                self.author.cancel(job['job_id']);return False
            if job.get('stop_proven') is not True:return False
            row['author_usage']=_measured_usage(job)
            row['author_usage_evidence']=deepcopy(job.get('usage'))
        row.update(status='cancelled',stop_proven=True,error=None);self._write(row)
        self._project(run,row)
        if not run.get('cancel_requested'):
            run.update(previous)
        return True

    def _settle_cancelled_render(self,row,child,key,phase):
        record=self.space_store.get(row['owner'],row['run_id'])
        if any(event['id']==key for event in record['usage']['events']):return
        self.space_store.record_usage(row['owner'],row['run_id'],id=key,phase=phase,
            outcome='completed' if child['status']=='completed' else 'cancelled',model_calls=0,model_tokens=0,
            render_seconds=(child.get('usage') or {}).get('render_seconds'))

    def verified_source(self,run):
        row=self._read(run['owner'],run['id'])
        _require(row['status']=='completed' and row['stop_proven'] is True
            and row['render_id']==run['draft_render_id'] and row['film_sha256']==run['film_sha256'],
            'scene_space_production_source_not_completed',409)
        child=self.renderer.get(run['owner'],row['render_id'])
        _require(child.get('status')=='completed' and child.get('stop_proven') is True
            and child.get('result')==row['render_result'],'scene_space_production_media_changed',409)
        media=self.renderer.video(run['owner'],row['render_id'])
        raw=media[0] if type(media) is tuple else media
        _require(type(raw) is bytes and len(raw)==row['render_result']['bytes']
            and hashlib.sha256(raw).hexdigest()==row['render_result']['sha256'],
            'scene_space_production_media_changed',409)
        return dict(source=dict(run_id=run['id'],version_id=row['render_id'],sha256=row['render_result']['sha256']),
            snapshot_sha256=row['render_result']['snapshot_sha256'],film_sha256=row['film_sha256'],
            request_sha256=digest(row['render_request']),
            configuration_sha256=row['configuration']['configuration_sha256'])

    def video(self,owner,render_id):
        self._render_owner(owner,render_id)
        return self.renderer.video(owner,render_id)

    def preview(self,owner,run_id,render_id):
        row=self._read(owner,run_id)
        item=next((item for item in row['previews'] if item['render_id']==render_id),None)
        _require(item is not None,'not_found',404)
        _require(item['status']=='completed','scene_space_preview_unavailable',409)
        raw,mime=self.renderer.video(owner,render_id)
        _require(mime=='image/png' and hashlib.sha256(raw).hexdigest()==item['artifact_sha256'],
            'scene_space_preview_media_changed',409)
        return raw,mime

    def _render_owner(self,owner,render_id):
        with closing(self._connect()) as db:
            rows=db.execute('SELECT run_id FROM scene_space_productions WHERE owner=?',(owner,)).fetchall()
        for item in rows:
            row=self._read(owner,item['run_id'])
            if row['render_id']==render_id:return row
        raise ServiceError('not_found',404)

    def cancel_render(self,owner,render_id):
        self._render_owner(owner,render_id)
        return self.renderer.cancel(owner,render_id)

    def renders(self,owner,project_id):
        with closing(self._connect()) as db:
            rows=db.execute('SELECT run_id FROM scene_space_productions WHERE owner=? AND project_id=?',
                (owner,project_id)).fetchall()
        result=[]
        for item in rows:
            row=self._read(owner,item['run_id'])
            if row['render_id']:
                result.append({**(row['render_result'] or {}),'id':row['render_id'],'project_id':project_id,'status':row['status'],
                    'visual_mode':RENDERER_MODE,'quality':'draft','silent':True,'subtitles':False,
                    'created_at':row['created_at'],'source_sha256':row['source_sha256'],'plan_sha256':row['plan_sha256']})
        return result


def configured_scene_space_production(store,*,library_adapter=None,fallback_agent_client=None,env=None):
    """Explicit operator gate, with read/reconcile support for frozen history."""
    import os
    from agent_service.client import AgentClient
    from .config import AgentSettings,ProductionMethodSettings
    from .motion_render_adapter import read_secret
    env=os.environ if env is None else env
    gate=env.get('VIDEO_SCENE_SPACE_PRODUCTION_ENABLED','0')
    if gate not in {'0','1'}:raise ValueError('Invalid VIDEO_SCENE_SPACE_PRODUCTION_ENABLED')
    with store.transaction() as db:
        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        history=any(json.loads(row[0]).get('renderer_mode')==RENDERER_MODE
            for row in db.execute('SELECT document FROM production_runs')) if 'production_runs' in tables else False
    if gate=='0' and not history:return None,None
    from .scene_space_store import SceneSpaceStore
    from .scene_space_render_http import SceneSpaceRenderClient
    method=ProductionMethodSettings.from_env(env)
    url=env.get('VIDEO_SCENE_SPACE_RENDER_URL','')
    token_file=env.get('VIDEO_SCENE_SPACE_RENDER_TOKEN_FILE','')
    if bool(url)!=bool(token_file):
        raise ValueError('Configure the scene renderer origin and token file together')
    renderer=(SceneSpaceRenderClient(url,read_secret(token_file)) if url else
        SceneSpaceRenderClient(method.base_url,method.token) if method is not None else None)
    settings=AgentSettings.from_env({
        'VIDEO_AGENT_URL':env.get('VIDEO_SCENE_SPACE_AUTHOR_URL',''),
        'VIDEO_AGENT_TOKEN':env.get('VIDEO_SCENE_SPACE_AUTHOR_TOKEN',''),
        'VIDEO_AGENT_TOKEN_FILE':env.get('VIDEO_SCENE_SPACE_AUTHOR_TOKEN_FILE',''),
        'VIDEO_AGENT_PROFILE_REVISION':env.get('VIDEO_SCENE_SPACE_AUTHOR_PROFILE_REVISION','1')})
    owned=AgentClient(settings.base_url,settings.token) if settings else None
    client=owned or fallback_agent_client
    author=SceneSpaceWorldAuthorGateway(client,settings.revision if settings else '1') if client else None
    reviewer=SceneSpaceWorldReviewGateway(client,settings.revision if settings else '1') if client else None
    if gate=='1' and (author is None or renderer is None):
        if owned:owned.close()
        raise ValueError('Scene production requires the static world author and private scene renderer')
    root=Path(store.path).with_name('scene-space-production')
    return SceneSpaceProductionAdapter(root,space_store=SceneSpaceStore(root/'space'),
        author=author,reviewer=reviewer,renderer=renderer,library_adapter=library_adapter,enabled=gate=='1'),owned
