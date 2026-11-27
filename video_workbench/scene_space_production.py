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
from agent_service.profiles import video_picture_review_v2 as classified_picture
from .picture_learning_gateway import PictureAuthorGateway,PictureReviewGateway,ClassifiedPictureReviewGateway
from .scene_review_reliability import GATE_SCHEMA,EVIDENCE_SCHEMA,default_review_policy,retain_png,image_provenance
from .scene_space_learning import scene_method_guidance, validate_scene_method
from .scene_space_plan import default_budget, validate_plan
from .scene_space_preview import preview_frames, production_preview_plan
from .scene_space_budget import validate_reservation

SCHEMA = 'video.scene-space-production.v1'
CONFIG_SCHEMA = 'video.scene-space-production-configuration.v1'
RENDERER_MODE = 'scene-space'
ACTIVE = frozenset({'queued','starting','submitting','running','reconciling','cancelling','unknown'})
MAX_JOURNAL_BYTES = 16 * 1024 * 1024
MIN_RENDER_WINDOW_SECONDS = 30
MAX_PREVIEW_RENDER_SECONDS = 60
MAX_PREVIEW_MEDIA_BYTES = 128 * 1024 * 1024
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


class SceneSpaceClassifiedReviewGateway(ClassifiedPictureReviewGateway):
    business_prefix='scene-space-classified-review:'
    idempotency_prefix='scene-classified-review-'
    task_timeout_seconds=3600


def _author_guidance(frames, budget, *, runtime_capabilities=None):
    return dict(schema='video.scene-space-production-author-offer.v1',
        method=scene_method_guidance('scene_data_only',runtime_capabilities=runtime_capabilities),
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
            callback='paintObject(ctx, object, world, tools)',
            optional_coverage='paintCoverage(maskCtx, object, world, tools) -> {substrate:[r,g,b]} or null',
            surface_tools='tools.surface',geometry_tools='tools.geometry (frozen artwork API3 only)',local_coordinates=True,
            camera_owner='host',source_files=['scene.html','scene.css','scene.js']),
        instructions=[
            'Create the complete worlds and all camera decisions once for the entire film, before painting. Reuse a small appropriate number of stable worlds across shots; storyboard scene boundaries do not require new worlds.',
            'The pre-export static preview budget is at most three images. Required samples include the film start, greatest camera zoom, final frame and the first shot frame of every distinct world. Plan these to overlap within the three-image budget; omitted worlds cannot silently pass review.',
            'Return a complete method.scene_space with a non-null validated plan, source.scope=scene_data_only, empty references and frame_ids, and all observation flags false. This is new authorship, not observed camera or video evidence.',
            'Use the exact host duration_frames, fps, viewport and budget. Plan and world revision start at 1. Keyframes use global absolute integer frames, x/y are world view centres, zoom is finite and bounded; intervals are half-open.',
            'World space.bounds must cover every layer view at all allowed camera positions. Use explicit depth, stable object IDs, parent-local transforms and anchors. Grid and named points assist layout; do not infer depth from image Y.',
            'Choose movement, still cameras and local dynamics from the goal. No compulsory pan, zoom, fog, birds, three-layer layout or black foreground. Keep a fixed principal viewing direction; no orbit or unseen backside reconstruction.',
            'Write each object painter once in the existing three source files. Register window.__sceneSpaceArtwork.paintObject(ctx,object,world,tools) and canvas[data-scene-space-output]. Optional paintCoverage separates solid silhouette from ink transparency; tools.surface supplies bounded local form fields. Paint in object-local coordinates; the host owns transforms, layers, masks, cache resolution and absolute-frame camera seeking.',
            'When the frozen capability offers tools.geometry, shared XYZ triangle meshes and fixed orthographic projection can plan connected bases, ridges, shoulders and front feet before brushwork. Keep this authored geometry in scene.js, project the same body for inter-part depth visibility, and reuse its handles. This helper adds neither an orbit camera nor a new pixel backend or source-observed reconstruction.',
            'Keep visible light sources and depicted light/shadow orientation coherent with the camera. Match form rendering to the requested medium: for ink brushwork, build the mass through organized ink deposits and varied brush contacts; continuous surface shading is optional and cannot substitute for the painting style.',
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
        scene_space_production=_author_guidance(frames,budget,
            runtime_capabilities=run['scene_space_configuration']['render_capability']))
    intent=(run.get('frozen',{}).get('joint_plan') or {}).get('rendering_intent')
    if intent is not None:
        bounds['scene_space_production']['rendering_intent']=deepcopy(intent)
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
    def __init__(self,root,*,space_store,author,renderer,reviewer=None,structured_reviewer=None,library_adapter=None,
                 enabled=False,budget=None,clock=time.time,material_noise_limit=64*1024*1024):
        _require(type(enabled) is bool)
        _require(type(material_noise_limit) is int and 1<=material_noise_limit<=64*1024*1024,
            'scene_appearance_policy_invalid')
        self.material_noise_limit=material_noise_limit
        self.root=Path(root).absolute();self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'scene-production.sqlite3'
        self.space_store,self.author,self.renderer=space_store,author,renderer
        self.reviewer=reviewer
        self.structured_reviewer=structured_reviewer
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
        caps=self.renderer.capabilities(owner)
        # Only the configured private executor can admit this host and painter API.
        _require(type(caps) is dict and caps.get('admitted') is True and 'canvas' in caps.get('engines',[])
            and _sha(caps.get('runtime_sha256')) and _sha(caps.get('host_sha256')),
            'scene_space_production_runtime_unavailable',503)
        selected=self.reviewer
        lossless=caps.get('sample_capture')
        if lossless is not None:
            from .scene_space_capture import validate_sample_capture
            validate_sample_capture(lossless)
            _require(self.structured_reviewer is not None,'scene_space_classified_review_unavailable',503)
            selected=self.structured_reviewer
            _require(selected.profile==classified_picture.PROFILE,
                'scene_space_classified_review_unavailable',503)
        selected.preflight(self.clock()+30)
        value=dict(schema=CONFIG_SCHEMA,available=True,renderer_mode=RENDERER_MODE,
            author_profile=self.author.profile,author_revision=self.author.profile_revision,
            review_profile=selected.profile,review_revision=selected.profile_revision,
            render_capability=deepcopy(caps),max_seconds=300,fps=24,audio_modes=['silent'],
            budget=deepcopy(self.budget),preview_min_score=80,max_preview_frames=3,max_pre_export_repairs=1,
            pre_export_gate_revision=2,max_preview_clip_frames=48,
            plan_repair_revision=1,
            model_job_reserve_calls=8,model_job_reserve_tokens=262144,
            visual_gate_scope='actual_static_and_clip_samples_not_continuous_playback_or_user_acceptance')
        if lossless is not None:
            policy=classified_picture.validate_review_policy(default_review_policy())
            value.update(review_gate_schema=GATE_SCHEMA,review_policy=policy,sample_capture=deepcopy(lossless))
        if caps.get('appearance',{}).get('quality_policy_schema')=='video.scene-appearance-policy.v1':
            value['material_noise_limit']=self.material_noise_limit
        value['configuration_sha256']=digest(value)
        return value

    @staticmethod
    def _classified(row):
        configuration=row['configuration'];schema=configuration.get('review_gate_schema')
        if schema is None:
            _require(configuration.get('review_profile')!=classified_picture.PROFILE,
                'scene_space_review_protocol_changed',409)
            return False
        _require(schema==GATE_SCHEMA and configuration.get('review_profile')==classified_picture.PROFILE,
            'scene_space_review_protocol_changed',409)
        classified_picture.validate_review_policy(configuration.get('review_policy'))
        from .scene_space_capture import validate_sample_capture
        validate_sample_capture(configuration.get('sample_capture'))
        return True

    def _review_gateway(self,row):
        gateway=self.structured_reviewer if self._classified(row) else self.reviewer
        configuration=row['configuration']
        _require(gateway is not None and gateway.profile==configuration['review_profile']
            and gateway.profile_revision==configuration['review_revision'],
            'scene_space_frozen_review_unavailable',503)
        return gateway

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
        if run.get('scene_source_import'):
            return self._prepare_source_import(run)
        if run.get('scene_review_continuation'):
            return self._prepare_review_continuation(run)
        if run.get('scene_source_recovery'):
            return self._prepare_source_recovery(run)
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
            previews=[],clips=[],review=None,repair=None,attempts=[],attempt=0,gate=None,
            render_request_key='scene-render-'+run['id'],render_id=None,render_result=None,
            render_submit_attempted=False,created_at=self.clock(),deadline_at=self.clock()+3600)
        return self._write(row,insert=True)

    def source_import_configuration(self,owner,project_id):
        from .scene_source_import import LIMITS
        configuration=self.freeze_configuration(owner,project_id)
        _require(self._classified({'configuration':configuration}),
            'scene_source_import_classified_review_required',503)
        configuration.update(max_pre_export_repairs=0,source_import_limits=deepcopy(LIMITS),
            model_job_reserve_calls=8,model_job_reserve_tokens=LIMITS['admission_reserve_tokens'])
        configuration['configuration_sha256']=digest({k:v for k,v in configuration.items()
            if k!='configuration_sha256'})
        return configuration

    def source_import_snapshot(self,run,request):
        from .scene_source_import import task_document, parent_ledgers
        row=self._read(run['owner'],run['id'])
        _require(row['status'] in {'failed','cancelled'} and row['stop_proven'] is True
            and row.get('author_submit_attempted') is True and row.get('author_result') is not None
            and row.get('source') is not None and row.get('plan_sha256') is not None
            and not row.get('source_import') and row.get('render_id') is None
            and not row.get('render_submit_attempted') and row.get('review') is not None
            and not row.get('repair') and not row.get('attempts') and row.get('attempt',0)==0,
            'scene_source_import_parent_unavailable',409)
        task=task_document(self.space_store.get(run['owner'],run['id']))
        _require(digest(row)==request['expected_scene_sha256'] and digest(task)==request['expected_task_sha256']
            and digest(parent_ledgers(task))==request['expected_parent_usage_sha256']
            and row['author_job_id']==request['expected_author_job_id']
            and row['author_input_sha256']==request['expected_author_input_sha256']==digest(row['author_document'])
            and digest(row['author_result'])==request['expected_author_result_sha256']
            and row['source_sha256']==request['expected_source_sha256']
            and row['source']==picture.validate_source(row['author_result']['source'],constraints=row['author_document']['constraints'])
            and row['plan_sha256']==request['expected_plan_sha256']==task['frozen']['plan_sha256']
            and row['configuration']['configuration_sha256']==request['expected_parent_configuration_sha256']
            and row['configuration']==run['scene_space_configuration']
            and row['film_sha256']==run['film_sha256'] and row['binding_sha256']==run['binding_sha256']
            and not any(r['status']=='active' for r in task['reservations'])
            and self.clock()<request['execution_deadline_at']<=self.clock()+3600,
            'scene_source_import_parent_changed',409)
        return row,task

    def prepare_source_import(self,run,request,*,lineage_job_ids):
        from .scene_source_import import SCHEMA as IMPORT_SCHEMA, LIMITS, MODEL_LIMITS, validate_grant
        row,task=self.source_import_snapshot(run,request)
        configuration=self.source_import_configuration(run['owner'],run['project_id'])
        _require(configuration['configuration_sha256']==request['expected_configuration_sha256'],
            'scene_source_import_configuration_changed',409)
        self.structured_reviewer.preflight_model_limits(request['execution_deadline_at'],MODEL_LIMITS)
        job=self.author.poll(row['author_job_id']);attempt=job.get('attempt') or {};proof=job.get('stop_proof') or {}
        pins=job.get('pins') or {};evidence=job.get('usage_evidence')
        _require(job.get('job_id')==row['author_job_id'] and job.get('state')=='completed'
            and job.get('stop_proven') is True and proof.get('stop_proven') is True
            and proof.get('attempt_id')==attempt.get('attempt_id') and type(attempt.get('fence')) is int
            and attempt['fence']>=1 and proof.get('fence')==attempt['fence']
            and _sha(proof.get('request_sha256')) and _sha(proof.get('result_sha256'))
            and pins.get('profile')==row['configuration']['author_profile']
            and pins.get('profile_revision')==row['configuration']['author_revision'],
            'scene_source_import_author_unproven',409)
        _require(type(evidence) is dict and evidence.get('schema')=='agent-model-usage-evidence.v1'
            and evidence.get('sha256')==request['expected_usage_evidence_sha256']==digest(
                {k:v for k,v in evidence.items() if k!='sha256'})
            and evidence.get('job_id')==job['job_id'] and evidence.get('attempt_id')==attempt['attempt_id']
            and evidence.get('fence')==attempt['fence'] and evidence.get('state')=='completed'
            and _sha(evidence.get('execution_binding_sha256'))
            and evidence.get('execution_binding_sha256')==(pins.get('execution_binding') or {}).get('sha256')
            and evidence.get('stop_proven') is True and evidence.get('authoritative') is True
            and evidence.get('all_attempts') is True and evidence.get('completeness')=='complete'
            and type(evidence.get('request_count')) is int and evidence['request_count']>=1
            and all(type(evidence.get(k)) is int and evidence[k]>=0
                for k in ('input_tokens','output_tokens','total_tokens'))
            and evidence['total_tokens']==evidence['input_tokens']+evidence['output_tokens'],
            'scene_source_import_author_usage_unproven',409)
        result=self.author.collect(job['job_id'],row['author_input_sha256'],row['author_document'])
        picture.validate_result(result,row['author_input_sha256'],row['author_document'])
        _require(result==row['author_result'] and validate_scene_method(result['method']['scene_space'],
            source_scope='scene_data_only',source_refs=[],frame_ids=[])['plan']==task['frozen']['plan'],
            'scene_source_import_author_artifact_changed',409)
        old=self._review_gateway(row).poll(row['review']['job_id'])
        _require(old.get('job_id')==row['review']['job_id'] and old.get('state') not in ACTIVE
            and old.get('stop_proven') is True,'scene_source_import_parent_review_not_stopped',409)
        for item in row['previews']+row.get('clips',[]):
            if not item.get('attempted'):continue
            _require(item.get('render_id') is not None,'scene_source_import_parent_render_uncertain',409)
            child=self.renderer.get(run['owner'],item['render_id'])
            _require(child.get('status') not in ACTIVE and child.get('stop_proven') is True,
                'scene_source_import_parent_render_not_stopped',409)
        previous=row.get('review_continuation') or row.get('source_recovery') or {}
        original=previous.get('original_run_id',previous.get('source_run_id',run['id']))
        value=dict(schema=IMPORT_SCHEMA,parent_run_id=run['id'],original_run_id=original,
            parent_run_sha256=digest(run),parent_scene_sha256=digest(row),parent_task_sha256=digest(task),
            parent_usage_sha256=request['expected_parent_usage_sha256'],
            parent_configuration_sha256=row['configuration']['configuration_sha256'],
            configuration_sha256=configuration['configuration_sha256'],author_job_id=job['job_id'],
            author_input_sha256=row['author_input_sha256'],author_result_sha256=digest(result),
            author_usage_evidence_sha256=evidence['sha256'],source_sha256=row['source_sha256'],
            plan_sha256=row['plan_sha256'],film_sha256=row['film_sha256'],
            authorization_sha256=request['authorization_sha256'],lineage_job_ids=sorted(lineage_job_ids),
            issued_at=self.clock(),execution_deadline_at=request['execution_deadline_at'],
            limits=deepcopy(LIMITS),model_limits=deepcopy(MODEL_LIMITS),maximum_imports=1,
            accept_parent_cumulative_usage_unknown=True,accept_original_token_cap_unproven=True,
            allow_export_with_parent_unknown=True,cumulative_cost_status='unknown',new_unknown_usage_policy='stop')
        grant=validate_grant(dict(value,sha256=digest(value)))
        return grant,configuration

    def _prepare_source_import(self,run):
        from .scene_source_import import validate_grant
        grant=validate_grant(run['scene_source_import']);source=self._read(run['owner'],grant['parent_run_id'])
        _require(digest(source)==grant['parent_scene_sha256'] and run['film_sha256']==source['film_sha256']
            and run['binding_sha256']==source['binding_sha256']
            and run['scene_space_configuration']['configuration_sha256']==grant['configuration_sha256']
            and digest({k:v for k,v in run['scene_space_configuration'].items() if k!='configuration_sha256'})
                ==grant['configuration_sha256'] and self._classified({'configuration':run['scene_space_configuration']}),
            'scene_source_import_parent_changed',409)
        self.space_store.import_source(run['owner'],run['id'],grant=grant)
        row=deepcopy(source);row.pop('review_continuation',None);row.pop('source_recovery',None)
        row.update(run_id=run['id'],configuration=deepcopy(run['scene_space_configuration']),
            source_import=deepcopy(grant),status='preview',stop_proven=False,error=None,
            previews=[],clips=[],review=None,repair=None,attempts=[],attempt=0,gate=None,
            render_request=None,render_request_key='scene-render-'+run['id'],render_id=None,render_result=None,
            render_submit_attempted=False,created_at=self.clock(),deadline_at=grant['execution_deadline_at'])
        return self._write(row,insert=True)

    def source_recovery_snapshot(self,run,request):
        """Pure local CAS checks; no reconciliation or writes to the old scene."""
        row=self._read(run['owner'],run['id'])
        _require(row['status'] in {'failed','cancelled'} and row['stop_proven'] is True
            and row['author_submit_attempted'] is True and row['author_job_id']==request['expected_author_job_id']
            and row['author_result'] is not None and row['source'] is not None and row['plan_sha256'] is not None
            and not row.get('attempts') and row.get('attempt',0)==0 and not row.get('review')
            and not row.get('repair') and row['render_id'] is None and not row['render_submit_attempted']
            and all(not p['attempted'] and p['render_id'] is None for p in row['previews']+row.get('clips',[])),
            'scene_space_source_recovery_unavailable',409)
        record=self.space_store.get(run['owner'],run['id'])
        task={key:value for key,value in record.items() if key!='usage_status'}
        _require(digest(row)==request['expected_scene_sha256']
            and row['author_input_sha256']==request['expected_author_input_sha256']==digest(row['author_document'])
            and digest(row['author_result'])==request['expected_author_result_sha256']
            and row['source_sha256']==request['expected_source_sha256']
            and row['source']==picture.validate_source(row['author_result']['source'],constraints=row['author_document']['constraints'])
            and row['plan_sha256']==request['expected_plan_sha256']==record['frozen']['plan_sha256']
            and row['configuration']['configuration_sha256']==request['expected_configuration_sha256']
            and row['configuration']==run['scene_space_configuration']
            and row['film_sha256']==run['film_sha256'] and row['binding_sha256']==run['binding_sha256']
            and record['usage']['sha256']==request['expected_usage_sha256']
            and not record['usage']['history']
            and not any(r['status']=='active' for r in record['reservations'])
            and self.clock()<request['execution_deadline_at']<=row['deadline_at'],
            'scene_space_source_recovery_changed',409)
        plan=validate_scene_method(row['author_result']['method']['scene_space'],
            source_scope='scene_data_only',source_refs=[],frame_ids=[])['plan']
        _require(plan==record['frozen']['plan'],'scene_space_source_recovery_plan_changed',409)
        return row,task

    def prepare_source_recovery(self,run,request):
        row,task=self.source_recovery_snapshot(run,request)
        # Actual authenticated GET and artifact collection, never caller usage,
        # upload, submit, or a fresh author operation.
        job=self.author.poll(row['author_job_id'])
        attempt,proof,pins=job.get('attempt'),job.get('stop_proof'),job.get('pins')
        evidence=job.get('usage_evidence')
        _require(job.get('job_id')==row['author_job_id'] and job.get('state')=='completed'
            and job.get('stop_proven') is True and type(attempt) is dict and type(proof) is dict
            and type(pins) is dict and pins.get('profile')==self.author.profile
            and pins.get('profile_revision')==self.author.profile_revision
            and proof.get('stop_proven') is True and proof.get('attempt_id')==attempt.get('attempt_id')
            and type(attempt.get('fence')) is int and attempt['fence']>=1 and proof.get('fence')==attempt['fence']
            and _sha(proof.get('request_sha256')) and _sha(proof.get('result_sha256')),
            'scene_space_source_recovery_author_unproven',409)
        _require(type(evidence) is dict and evidence.get('schema')=='agent-model-usage-evidence.v1'
            and evidence.get('sha256')==request['expected_usage_evidence_sha256']==digest(
                {k:v for k,v in evidence.items() if k!='sha256'})
            and evidence.get('job_id')==job['job_id'] and evidence.get('attempt_id')==attempt['attempt_id']
            and evidence.get('fence')==attempt['fence'] and evidence.get('state')=='completed'
            and _sha(evidence.get('execution_binding_sha256'))
            and evidence['execution_binding_sha256']==(pins.get('execution_binding') or {}).get('sha256')
            and evidence.get('stop_proven') is True and evidence.get('authoritative') is True
            and evidence.get('all_attempts') is True and evidence.get('completeness')=='complete'
            and type(evidence.get('request_count')) is int and evidence['request_count']>=1
            and all(type(evidence.get(key)) is int and evidence[key]>=0 for key in ('input_tokens','output_tokens','total_tokens'))
            and evidence['total_tokens']==evidence['input_tokens']+evidence['output_tokens'],
            'scene_space_source_recovery_usage_unproven',409)
        result=self.author.collect(job['job_id'],row['author_input_sha256'],row['author_document'])
        picture.validate_result(result,row['author_input_sha256'],row['author_document'])
        _require(result==row['author_result'],'scene_space_source_recovery_artifact_changed',409)
        measured=dict(model_calls=evidence['request_count'],model_tokens=evidence['total_tokens'])
        usage=deepcopy(task['usage']);author=[e for e in usage['events'] if e['id']==row['author_operation_id']]
        _require(len(author)==1 and author[0]['phase']=='generation' and author[0]['outcome']=='completed'
            and author[0]['model_tokens'] is None and measured['model_calls']>=author[0]['model_calls'],
            'scene_space_source_recovery_usage_invalid',409)
        author[0].update(measured)
        usage['sha256']=digest({k:v for k,v in usage.items() if k!='sha256'})
        from .scene_space_budget import usage_status
        _require(usage_status(usage)['status']=='within_budget','scene_space_source_recovery_budget_unavailable',409)
        return dict(schema='video.scene-source-recovery.v1',source_run_id=run['id'],
            source_run_sha256=digest(run),source_scene_sha256=digest(row),source_task_sha256=digest(task),
            source_usage_sha256=task['usage']['sha256'],source_author_event_sha256=digest(
                next(e for e in task['usage']['events'] if e['id']==row['author_operation_id'])),
            author_job_id=job['job_id'],author_operation_id=row['author_operation_id'],
            author_input_sha256=row['author_input_sha256'],author_result_sha256=digest(result),
            source_sha256=row['source_sha256'],plan_sha256=row['plan_sha256'],
            configuration_sha256=row['configuration']['configuration_sha256'],film_sha256=row['film_sha256'],
            usage_evidence=deepcopy(evidence),measured_author_usage=measured,
            original_deadline_at=row['deadline_at'],execution_deadline_at=request['execution_deadline_at'],
            maximum_recoveries=1,extra_author_jobs=0)

    def _prepare_source_recovery(self,run):
        recovery=run['scene_source_recovery']
        source=self._read(run['owner'],recovery['source_run_id'])
        _require(digest(source)==recovery['source_scene_sha256']
            and run['film_sha256']==source['film_sha256']
            and run['binding_sha256']==source['binding_sha256']
            and run['scene_space_configuration']==source['configuration'],
            'scene_space_source_recovery_changed',409)
        saved=self.space_store.recover_source(run['owner'],run['id'],recovery['source_run_id'],
            source_task_sha256=recovery['source_task_sha256'],author_operation_id=source['author_operation_id'],
            measured_usage=recovery['measured_author_usage'],recovery=recovery)
        _require(saved['frozen']['plan_sha256']==recovery['plan_sha256'],
            'scene_space_source_recovery_plan_changed',409)
        row=deepcopy(source)
        row.update(run_id=run['id'],status='preview',stop_proven=False,error=None,previews=[],clips=[],
            review=None,repair=None,attempts=[],attempt=0,gate=None,render_request=None,
            render_request_key='scene-render-'+run['id'],render_id=None,render_result=None,
            render_submit_attempted=False,created_at=self.clock(),deadline_at=recovery['execution_deadline_at'],
            author_usage=deepcopy(recovery['measured_author_usage']),
            author_usage_evidence=deepcopy(recovery['usage_evidence']),source_recovery=deepcopy(recovery))
        return self._write(row,insert=True)

    def review_continuation_snapshot(self,run,request):
        """Verify one stopped source-recovery parent; no old state is repaired."""
        from .scene_space_budget import usage_status
        row=self._read(run['owner'],run['id'])
        _require(not self._classified(row),'scene_review_continuation_protocol_unsupported',409)
        parent_recovery=run.get('scene_source_recovery')
        _require(type(parent_recovery) is dict and not run.get('scene_review_continuation')
            and row['status']=='cancelled' and row['stop_proven'] is True
            and row.get('source_recovery')==parent_recovery
            and not row.get('attempts') and row.get('attempt',0)==0 and not row.get('repair')
            and row.get('review') and row['review']['attempted'] is True
            and row['review']['job_id']==request['expected_review_job_id']
            and row['review']['input_sha256']==request['expected_review_input_sha256']
            and row['review'].get('result') is None
            and row['render_id'] is None and row['render_submit_attempted'] is False,
            'scene_review_continuation_unavailable',409)
        record=self.space_store.get(run['owner'],run['id'])
        task={k:v for k,v in record.items() if k not in {'usage_status','continuation_usage_status'}}
        _require(digest(row)==request['expected_scene_sha256']
            and digest(task)==request['expected_task_sha256']
            and task['usage']['sha256']==request['expected_usage_sha256']
            and not task.get('review_continuation') and not task['usage']['history']
            and not any(r['status']=='active' for r in task['reservations'])
            and row['author_job_id']==request['expected_author_job_id']
            and row['author_input_sha256']==request['expected_author_input_sha256']==digest(row['author_document'])
            and digest(row['author_result'])==request['expected_author_result_sha256']
            and row['source_sha256']==request['expected_source_sha256']
            and row['source']==picture.validate_source(row['author_result']['source'],constraints=row['author_document']['constraints'])
            and row['plan_sha256']==request['expected_plan_sha256']==task['frozen']['plan_sha256']
            and row['configuration']['configuration_sha256']==request['expected_configuration_sha256']
            and row['configuration']==run['scene_space_configuration']
            and row['film_sha256']==run['film_sha256'] and row['binding_sha256']==run['binding_sha256']
            and self.clock()<request['execution_deadline_at']<=self.clock()+3600,
            'scene_review_continuation_changed',409)
        unknown=[event for event in task['usage']['events']
            if event['model_tokens'] is None or event['render_seconds'] is None]
        _require(len(unknown)==1 and unknown[0]['id']==row['review']['id']
            and unknown[0]['phase']=='review' and unknown[0]['outcome']=='cancelled'
            and unknown[0]['model_tokens'] is None and unknown[0]['render_seconds']==0
            and task['usage']['baseline']['model_tokens'] is not None
            and usage_status(task['usage'])['status']=='usage_unknown',
            'scene_review_continuation_legacy_usage_invalid',409)
        _require(row['previews'] and row.get('clips')
            and all(item['attempted'] and item['status']=='completed' and item['render_id']
                for item in row['previews']+row['clips']),
            'scene_review_continuation_preview_unavailable',409)
        return row,task

    def prepare_review_continuation(self,run,request,*,lineage_job_ids):
        from .scene_review_continuation import SCHEMA as GRANT_SCHEMA, LIMITS, MODEL_LIMITS
        row,task=self.review_continuation_snapshot(run,request)
        # The configured control service must advertise the actual per-job
        # enforcement protocol before any upload or paid submission is possible.
        self.reviewer.preflight_model_limits(request['execution_deadline_at'],MODEL_LIMITS)
        job=self.author.poll(row['author_job_id'])
        attempt,proof,pins=job.get('attempt'),job.get('stop_proof'),job.get('pins')
        evidence=job.get('usage_evidence')
        _require(job.get('job_id')==row['author_job_id'] and job.get('state')=='completed'
            and job.get('stop_proven') is True and type(attempt) is dict and type(proof) is dict
            and type(pins) is dict and pins.get('profile')==row['configuration']['author_profile']
            and pins.get('profile_revision')==row['configuration']['author_revision']
            and proof.get('stop_proven') is True and proof.get('attempt_id')==attempt.get('attempt_id')
            and proof.get('fence')==attempt.get('fence') and type(attempt.get('fence')) is int
            and _sha(proof.get('request_sha256')) and _sha(proof.get('result_sha256')),
            'scene_review_continuation_author_unproven',409)
        _require(type(evidence) is dict and evidence.get('schema')=='agent-model-usage-evidence.v1'
            and evidence.get('sha256')==request['expected_usage_evidence_sha256']==digest(
                {k:v for k,v in evidence.items() if k!='sha256'})
            and evidence.get('job_id')==job['job_id'] and evidence.get('attempt_id')==attempt['attempt_id']
            and evidence.get('fence')==attempt['fence'] and evidence.get('state')=='completed'
            and evidence.get('execution_binding_sha256')==(pins.get('execution_binding') or {}).get('sha256')
            and _sha(evidence.get('execution_binding_sha256'))
            and evidence.get('stop_proven') is True and evidence.get('authoritative') is True
            and evidence.get('all_attempts') is True and evidence.get('completeness')=='complete'
            and type(evidence.get('request_count')) is int and evidence['request_count']>=1
            and all(type(evidence.get(k)) is int and evidence[k]>=0
                for k in ('input_tokens','output_tokens','total_tokens'))
            and evidence['total_tokens']==evidence['input_tokens']+evidence['output_tokens'],
            'scene_review_continuation_author_usage_unproven',409)
        author_events=[event for event in task['usage']['events'] if event['id']==row['author_operation_id']]
        _require(len(author_events)==1 and author_events[0]['phase']=='generation'
            and author_events[0]['outcome']=='completed'
            and author_events[0]['model_calls']==evidence['request_count']
            and author_events[0]['model_tokens']==evidence['total_tokens'],
            'scene_review_continuation_author_usage_changed',409)
        result=self.author.collect(job['job_id'],row['author_input_sha256'],row['author_document'])
        picture.validate_result(result,row['author_input_sha256'],row['author_document'])
        _require(result==row['author_result'],'scene_review_continuation_author_artifact_changed',409)
        _require(validate_scene_method(result['method']['scene_space'],source_scope='scene_data_only',
            source_refs=[],frame_ids=[])['plan']==task['frozen']['plan'],
            'scene_review_continuation_plan_changed',409)
        old=self.reviewer.poll(row['review']['job_id']);old_evidence=old.get('usage_evidence')
        old_attempt,old_proof=old.get('attempt'),old.get('stop_proof')
        old_pins=old.get('pins') or {}
        _require(old.get('job_id')==row['review']['job_id'] and old.get('state')=='cancelled'
            and old.get('stop_proven') is True and type(old_attempt) is dict and type(old_proof) is dict
            and old_proof.get('stop_proven') is True
            and type(old_attempt.get('fence')) is int and old_attempt['fence']>=1
            and type(old_attempt.get('attempt_id')) is str and old_attempt['attempt_id']
            and old_proof.get('attempt_id')==old_attempt.get('attempt_id')
            and old_proof.get('fence')==old_attempt.get('fence')
            and _sha(old_proof.get('request_sha256')) and _sha(old_proof.get('result_sha256'))
            and old_pins.get('profile')==row['configuration']['review_profile']
            and old_pins.get('profile_revision')==row['configuration']['review_revision']
            and type(old_evidence) is dict and old_evidence.get('schema')=='agent-model-usage-evidence.v1'
            and old_evidence.get('sha256')==request['expected_review_usage_evidence_sha256']==digest(
                {k:v for k,v in old_evidence.items() if k!='sha256'})
            and old_evidence.get('job_id')==old['job_id']
            and old_evidence.get('attempt_id')==old_attempt.get('attempt_id')
            and old_evidence.get('fence')==old_attempt.get('fence')
            and old_evidence.get('state')=='cancelled'
            and old_evidence.get('execution_binding_sha256')==(old_pins.get('execution_binding') or {}).get('sha256')
            and _sha(old_evidence.get('execution_binding_sha256'))
            and old_evidence.get('stop_proven') is True and old_evidence.get('authoritative') is True
            and old_evidence.get('all_attempts') is True and old_evidence.get('completeness')=='partial',
            'scene_review_continuation_old_review_unproven',409)
        # Reuse only actual completed previews of this exact source/plan. Old
        # request deadlines remain historical; no old renderer is resubmitted.
        for item in row['previews']+row['clips']:
            child=self.renderer.get(run['owner'],item['render_id'])
            _require(child.get('status')=='completed' and child.get('stop_proven') is True
                and child.get('result')==item['result']
                and child['result'].get('source_sha256')==row['source_sha256']
                and child['result'].get('plan_sha256')==row['plan_sha256'],
                'scene_review_continuation_preview_changed',409)
            self.preview(run['owner'],run['id'],item['render_id'])
        self._review_files(row)  # Hash-check the actual local sampled files too.
        value=dict(schema=GRANT_SCHEMA,parent_run_id=run['id'],original_run_id=run['scene_source_recovery']['source_run_id'],
            parent_run_sha256=digest(run),parent_scene_sha256=digest(row),parent_task_sha256=digest(task),
            legacy_usage_sha256=task['usage']['sha256'],legacy_unknown_event_id=row['review']['id'],
            legacy_review_job_id=old['job_id'],legacy_review_usage_evidence_sha256=old_evidence['sha256'],
            author_job_id=job['job_id'],author_input_sha256=row['author_input_sha256'],
            author_result_sha256=digest(result),author_usage_evidence_sha256=evidence['sha256'],
            source_sha256=row['source_sha256'],plan_sha256=row['plan_sha256'],
            configuration_sha256=row['configuration']['configuration_sha256'],film_sha256=row['film_sha256'],
            original_deadline_at=row['deadline_at'],execution_deadline_at=request['execution_deadline_at'],
            issued_at=self.clock(),limits=deepcopy(LIMITS),model_limits=deepcopy(MODEL_LIMITS),
            lineage_job_ids=sorted(lineage_job_ids),maximum_continuations=1,
            accept_legacy_usage_unknown=True,accept_original_token_cap_unproven=True,
            allow_export_with_legacy_unknown=True,cumulative_cost_status='unknown',
            original_total_token_cap_proven=False,new_unknown_usage_policy='stop')
        return dict(value,sha256=digest(value))

    def _prepare_review_continuation(self,run):
        from .scene_review_continuation import validate_grant
        grant=validate_grant(run['scene_review_continuation'])
        source=self._read(run['owner'],grant['parent_run_id'])
        _require(digest(source)==grant['parent_scene_sha256']
            and source['source_sha256']==grant['source_sha256'] and source['plan_sha256']==grant['plan_sha256']
            and run['film_sha256']==source['film_sha256'] and run['binding_sha256']==source['binding_sha256']
            and run['scene_space_configuration']==source['configuration'],
            'scene_review_continuation_parent_changed',409)
        self.space_store.continue_review(run['owner'],run['id'],grant=grant)
        row=deepcopy(source)
        row.update(run_id=run['id'],status='review',stop_proven=False,error=None,review=None,repair=None,
            attempts=[],attempt=0,render_request=None,render_request_key='scene-render-'+run['id'],
            render_id=None,render_result=None,render_submit_attempted=False,
            created_at=self.clock(),deadline_at=grant['execution_deadline_at'],
            review_continuation=deepcopy(grant))
        row.pop('source_recovery',None)
        return self._write(row,insert=True)

    def _project(self,run,row):
        reviews=[(a['review']['score'],a['attempt']) for a in row.get('attempts',[]) if a.get('review')]
        if row.get('review') and row['review'].get('result'):
            reviews.append((row['review']['result']['review']['score'],row.get('attempt',0)))
        run['scene_space']=dict(status=row['status'],stop_proven=row['stop_proven'],
            author_operation_id=row['author_operation_id'],author_job_id=row['author_job_id'],
            plan_sha256=row['plan_sha256'],source_sha256=row['source_sha256'],
            render_id=row['render_id'],error=row['error'],
            previews=[dict(frame=p['frame'],render_id=p['render_id'],status=p['status'],
                url='/api/production-runs/'+run['id']+'/scene-previews/'+p['render_id']
                    if p['render_id'] and p['status']=='completed' else None) for p in row['previews']],
            visual_review=deepcopy(row['review']['result']['review']) if row.get('review') and row['review'].get('result') else None,
            preview_clips=[dict(start_frame=p['start_frame'],end_frame=p['end_frame'],
                render_id=p['render_id'],status=p['status'],
                url='/api/production-runs/'+run['id']+'/scene-previews/'+p['render_id']
                    if p['render_id'] and p['status']=='completed' else None) for p in row.get('clips',[])],
            preview_attempt=row.get('attempt',0),repair_limit=row['configuration'].get('max_pre_export_repairs',0),
            retained_attempts=[dict(attempt=a['attempt'],source_sha256=a['source_sha256'],
                review_score=(a.get('review') or {}).get('score'),
                previews=[dict(frame=p['frame'],url='/api/production-runs/'+run['id']+
                    '/scene-previews/'+p['render_id']) for p in a['previews'] if p['status']=='completed'])
                for a in row.get('attempts',[])],
            layout_gate=deepcopy((row.get('gate') or {}).get('layout_report')),
            best_preview_attempt=max(reviews,key=lambda item:(item[0],-item[1]))[1] if reviews else None,
            best_preview_scope='highest_sample_review_score_not_user_acceptance',
            initial_author_usage=deepcopy(row.get('author_usage')),
            visual_evidence_scope='sampled_static_previews_only',
            temporal_preview_scope='bounded_clips_rendered_static_samples_reviewed'
                if row.get('clips') and all(p['status']=='completed' for p in row['clips']) else 'not_completed',
            continuous_playback_reviewed=False,audio_reviewed=False)
        if row.get('review_continuation'):
            saved=self.space_store.get(row['owner'],row['run_id'])
            run['scene_space'].update(repair_limit=0,accounting=dict(
                cumulative=deepcopy(saved['usage_status']),
                continuation=deepcopy(saved['continuation_usage_status']),
                legacy_usage_sha256=row['review_continuation']['legacy_usage_sha256'],
                grant_sha256=row['review_continuation']['sha256'],
                original_total_token_cap_proven=False))
        if row.get('source_import'):
            saved=self.space_store.get(row['owner'],row['run_id'])
            run['scene_space'].update(repair_limit=0,accounting=dict(
                cumulative=deepcopy(saved['usage_status']),
                source_import_execution=deepcopy(saved['source_import_execution_status']),
                parent_usage_sha256=row['source_import']['parent_usage_sha256'],
                grant_sha256=row['source_import']['sha256'],original_total_token_cap_proven=False))
        if self._classified(row):
            run['scene_space'].update(review_profile=row['configuration']['review_profile'],
                review_gate_schema=GATE_SCHEMA,
                review_decision=deepcopy((row.get('review') or {}).get('decision')))
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
            current=self.freeze_configuration(row['owner'],row['project_id'])
            _require(all(current[key]==row['configuration'][key] for key in (
                'author_profile','author_revision','review_profile','review_revision','render_capability')),
                'scene_space_production_configuration_changed',409)
            if row.get('author_model_reservation') is None:
                budget=row['configuration']['budget']['model']
                calls=row['configuration'].get('model_job_reserve_calls',1)
                remaining=None if budget['used_tokens'] is None else budget['limit_tokens']-budget['used_tokens']
                _require(budget['limit_calls']-budget['used_calls']>=calls and remaining is not None and remaining>0,
                    'scene_space_model_budget_unavailable',409)
                row['author_model_reservation']=dict(model_calls=calls,
                    model_tokens=min(remaining,row['configuration'].get('model_job_reserve_tokens',262144)),
                    scope='admission_hold_not_upstream_total_token_cap')
                validate_reservation(id=operation,phase='generation',
                    **{k:v for k,v in row['author_model_reservation'].items() if k!='scope'})
                self._write(row)
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
        intent=document['constraints']['scene_space_production'].get('rendering_intent')
        if intent is not None:
            from .spatial_rendering_route import validate_scene_intent_plan
            validate_scene_intent_plan(intent,plan)
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
            if row['configuration'].get('pre_export_gate_revision',1)>=2:
                row['gate']=production_preview_plan(saved['frozen'],
                    max_static_frames=row['configuration']['max_preview_frames'],
                    max_clip_frames=row['configuration']['max_preview_clip_frames'])
                row['clips']=[dict(window,frame=window['start_frame'],
                    key='scene-clip-'+row['run_id']+'-'+str(row.get('attempt',0))+'-'+str(index),
                    request=None,attempted=False,render_id=None,result=None,status='pending',samples=[])
                    for index,window in enumerate(row['gate']['clips'])]
            samples=preview_frames(plan)
            candidates=[(key['zoom'],min(key['frame'],shot['end_frame']-1))
                for shot in plan['shots'] for key in shot['camera']['keyframes']]
            greatest=max(candidates,key=lambda pair:(pair[0],pair[1]))[1]
            required={samples[0],greatest,samples[-1]}
            seen=set()
            for shot in plan['shots']:
                if shot['world_id'] not in seen:
                    required.add(shot['start_frame']);seen.add(shot['world_id'])
            if row.get('gate'):required=set(row['gate']['static_frames'])
            if len(required)>row['configuration']['max_preview_frames']:
                error=ServiceError('scene_space_preview_coverage_budget_exceeded',409)
                error.details=dict(required_frames=sorted(required),world_ids=sorted(seen),
                    max_preview_frames=row['configuration']['max_preview_frames'],
                    retained_plan_sha256=row['plan_sha256'],retained_source_sha256=row['source_sha256'])
                raise error
            selected=sorted(required)
            row['previews']=[dict(frame=frame,key='scene-preview-'+row['run_id']+'-'+str(frame)
                +('-r'+str(row['attempt']) if row.get('attempt') else ''),
                request=None,attempted=False,render_id=None,result=None,status='pending',artifact_sha256=None)
                for frame in selected]
            self._write(row)
        preview=next((item for item in row['previews'] if item['status']!='completed'),None)
        if preview is None:
            row['status']='clip_preview' if row.get('clips') else 'review';return self._write(row)
        if preview['request'] is None:
            if not admit:return row
            window=self._render_window(row,preview=True)
            document=self._render_document(row,saved)
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
        if all(item['status']=='completed' for item in row['previews']):
            row['status']='clip_preview' if row.get('clips') else 'review'
        return self._write(row)

    def _store_sample(self,raw,mime):
        """Keep verified review pixels locally, independent of later attempts."""
        _require(type(raw) is bytes and len(raw)<=4*1024*1024 and mime in {'image/png','image/jpeg'},
            'scene_space_preview_image_invalid',409)
        from PIL import Image
        try:
            with Image.open(io.BytesIO(raw)) as image:
                _require(image.size==(1280,720) and image.format==('PNG' if mime=='image/png' else 'JPEG'),
                    'scene_space_preview_dimensions_changed',409)
                image.load();output=io.BytesIO();image.convert('RGB').save(output,format='PNG')
                data=output.getvalue()
        except (OSError,ValueError,Image.DecompressionBombError):
            raise ServiceError('scene_space_preview_image_invalid',409) from None
        sha=hashlib.sha256(data).hexdigest();directory=self.root/'artifacts';directory.mkdir(exist_ok=True)
        path=directory/(sha+'.png')
        _require(not path.is_symlink() and not directory.is_symlink(),'scene_space_production_path_invalid')
        if path.exists():_require(path.read_bytes()==data,'scene_space_preview_media_changed',409)
        else:path.write_bytes(data)
        return sha

    def _store_lossless_sample(self,sample,*,frame_range):
        from .scene_space_capture import validate_frame_capture
        capture=sample.get('capture')
        validate_frame_capture(capture,frame_range=frame_range,raw=sample['raw'],
            timestamp_seconds=sample['timestamp_seconds'])
        sha=retain_png(sample['raw'],sample['mime'],expected_sha256=capture['sha256'])
        directory=self.root/'artifacts';directory.mkdir(exist_ok=True);path=directory/(sha+'.png')
        _require(not path.is_symlink() and not directory.is_symlink(),'scene_space_production_path_invalid')
        if path.exists():_require(path.read_bytes()==sample['raw'],'scene_space_preview_media_changed',409)
        else:path.write_bytes(sample['raw'])
        return dict(frame=capture['absolute_frame'],artifact_sha256=sha,capture=deepcopy(capture))

    def _clip_preview(self,row,*,admit):
        saved=self.space_store.get(row['owner'],row['run_id'])
        clip=next((item for item in row['clips'] if item['status']!='completed'),None)
        if clip is None:
            row['status']='review';return self._write(row)
        if not clip['attempted'] and clip['render_id'] is None:
            from .scene_review_continuation import execution_status
            status=execution_status(saved) if row.get('source_import') else saved['usage_status']
            _require(status['status']=='within_budget','scene_space_budget_unavailable',409)
        if clip['request'] is None:
            if not admit:return row
            window=self._render_window(row,preview=True)
            document=self._render_document(row,saved)
            document['constraints']['duration_seconds']=(clip['end_frame']-clip['start_frame'])/24
            if self._classified(row):
                document['constraints']['sample_capture']=deepcopy(row['configuration']['sample_capture'])
            clip['reserved_render_seconds']=window['seconds']
            clip['request']=dict(source=deepcopy(row['source']),document=document,scene_space=saved['frozen'],
                frame_range={key:clip[key] for key in ('start_frame','end_frame')},deadline_at=window['deadline_at'])
            self._write(row)
        if clip['render_id'] is None:
            child=self.renderer.lookup(row['owner'],clip['key'])
            if child is None:
                if clip['attempted']:
                    row['error']='scene_space_preview_submission_unconfirmed';return self._write(row)
                if not admit:return row
                _require(clip['request']['deadline_at']-self.clock()>=MIN_RENDER_WINDOW_SECONDS,
                    'scene_space_render_budget_insufficient',409)
                self.space_store.reserve(row['owner'],row['run_id'],clip['key'],phase='preview',
                    render_seconds=clip['reserved_render_seconds'])
                clip['attempted']=True;self._write(row)
                try:child=self.renderer.start(row['owner'],clip['request'],clip['key'])
                except ServiceError as error:
                    if error.status<500:
                        self.space_store.record_usage(row['owner'],row['run_id'],id=clip['key'],phase='preview',
                            outcome='failed',model_calls=0,model_tokens=0,render_seconds=0)
                        row.update(status='failed',stop_proven=True,error=error.code)
                        return self._write(row)
                    row['error']='scene_space_preview_submission_uncertain';return self._write(row)
                except Exception:
                    row['error']='scene_space_preview_submission_uncertain';return self._write(row)
            clip['render_id']=child['id'];self._write(row)
        child=self.renderer.get(row['owner'],clip['render_id'])
        if child['status'] in ACTIVE:return row
        if child.get('stop_proven') is not True:
            row['error']='scene_space_preview_stop_unproven';return self._write(row)
        self.space_store.record_usage(row['owner'],row['run_id'],id=clip['key'],phase='preview',
            outcome='completed' if child['status']=='completed' else 'failed',model_calls=0,model_tokens=0,
            render_seconds=(child.get('usage') or {}).get('render_seconds'))
        _require(child['status']=='completed','scene_space_preview_failed',409)
        result=child.get('result')
        _require(type(result) is dict and result.get('plan_sha256')==row['plan_sha256']
            and result.get('source_sha256')==row['source_sha256'] and result.get('fps')==24
            and result.get('frame_count')==clip['end_frame']-clip['start_frame'],
            'scene_space_preview_binding_changed',409)
        if self._classified(row):
            _require(result.get('sample_capture')==row['configuration']['sample_capture']
                and result.get('sample_frame_range')=={key:clip[key] for key in ('start_frame','end_frame')},
                'scene_space_preview_capture_changed',409)
        raw,mime=self.renderer.video(row['owner'],clip['render_id'])
        _require(type(raw) is bytes and 1<=len(raw)<=MAX_PREVIEW_MEDIA_BYTES and mime=='video/mp4'
            and len(raw)==result.get('bytes') and hashlib.sha256(raw).hexdigest()==result['sha256'],
            'scene_space_preview_media_changed',409)
        samples=self.renderer.frames(row['owner'],clip['render_id'],max_frames=3)
        count=clip['end_frame']-clip['start_frame']
        _require(type(samples) is list and min(2,count)<=len(samples)<=3,'scene_space_preview_samples_missing',409)
        selected=[samples[0],samples[-1]] if count>1 else [samples[0]]
        expected_times=[0,(count-1)/24] if count>1 else [0]
        _require([sample['timestamp_seconds'] for sample in selected]==expected_times,
            'scene_space_preview_sample_clock_changed',409)
        if self._classified(row):
            clip['samples']=[self._store_lossless_sample(sample,
                frame_range={key:clip[key] for key in ('start_frame','end_frame')}) for sample in selected]
        else:
            clip['samples']=[dict(frame=clip['start_frame']+round(sample['timestamp_seconds']*24),
                artifact_sha256=self._store_sample(sample['raw'],sample['mime'])) for sample in selected]
        clip.update(status='completed',result=deepcopy(result));row['error']=None
        if all(item['status']=='completed' for item in row['clips']):row['status']='review'
        return self._write(row)

    def _review_files(self,row):
        document=deepcopy(row['author_document']);document['mode']='review'
        classified=self._classified(row)
        if row.get('review_continuation'):
            grant=row['review_continuation']
            document['constraints']['scene_review_continuation']=dict(schema=grant['schema'],
                grant_sha256=grant['sha256'],parent_run_id=grant['parent_run_id'],
                limits=deepcopy(grant['limits']),cumulative_cost_status='unknown',
                scope='fresh_static_review_of_unchanged_author_and_retained_completed_previews')
        if row.get('source_import'):
            grant=row['source_import']
            document['constraints']['scene_source_import']=dict(schema=grant['schema'],grant_sha256=grant['sha256'],
                parent_run_id=grant['parent_run_id'],configuration_sha256=grant['configuration_sha256'],
                source_sha256=grant['source_sha256'],plan_sha256=grant['plan_sha256'],
                limits=deepcopy(grant['limits']),cumulative_cost_status='unknown',
                scope='fresh_lossless_samples_and_new_review_of_the_exact_imported_source_and_plan')
        frames=[];files={};provenance=[]
        from PIL import Image
        samples=[dict(item,identity='preview-'+str(item['frame']),capture_kind='static_preview')
            for item in row['previews']]
        samples.extend(dict(sample,identity='clip-'+str(index)+'-'+str(sample['frame']))
            for index,clip in enumerate(row.get('clips',[])) for sample in clip.get('samples',[]))
        for item in samples:
            raw=(self.root/'artifacts'/(item['artifact_sha256']+'.png')).read_bytes()
            _require(hashlib.sha256(raw).hexdigest()==item['artifact_sha256'],'scene_space_preview_media_changed',409)
            suffix='png'
            if classified:
                render_id=item.get('render_id')
                frame_range=dict(start_frame=item['frame'],end_frame=item['frame']+1)
                if item.get('capture_kind')!='static_preview':
                    index=int(item['identity'].split('-')[1]);clip=row['clips'][index]
                    render_id=clip['render_id']
                    frame_range={key:clip[key] for key in ('start_frame','end_frame')}
                provenance.append(image_provenance(item,raw,source_sha256=row['source_sha256'],
                    plan_sha256=row['plan_sha256'],kind=item.get('capture_kind','clip_keyframe'),
                    render_id=render_id,frame_range=frame_range))
            if not classified and len(raw)>2*1024*1024:
                with Image.open(io.BytesIO(raw)) as image:
                    converted=io.BytesIO();image.convert('RGB').save(converted,format='JPEG',quality=88)
                    raw=converted.getvalue();suffix='jpg'
            _require(len(raw)<=2*1024*1024,'scene_space_preview_observation_too_large',413)
            name=item['identity']+'.'+suffix
            frames.append(dict(file_name=name,sha256=hashlib.sha256(raw).hexdigest(),purpose='output',
                frame_id=item['identity']))
            files[name]=raw
        document['observation']=dict(frames=frames)
        document['constraints']['scene_space_static_review']=dict(frames=[item['frame'] for item in row['previews']],
            clip_samples=[dict(start_frame=c['start_frame'],end_frame=c['end_frame'],
                frames=[s['frame'] for s in c.get('samples',[])]) for c in row.get('clips',[])],
            scope='actual_static_samples_only',instructions=[
                'Judge these actual rendered samples against the full goal, style, object scale, composition, depth and occlusion.',
                'Report defects and uncertainty. Do not claim continuous playback, motion, sound or whole-video observation.',
                'This is independent static quality advice; neither a score nor transport receipt is user artistic acceptance.'])
        if classified:
            document['constraints']['review_policy']=deepcopy(row['configuration']['review_policy'])
            document['constraints']['review_evidence']=dict(schema=EVIDENCE_SCHEMA,
                frames=provenance,scope='actual_lossless_static_samples_not_continuous_playback')
            document=classified_picture.validate_input(document)
        else:document=picture.validate_input(document)
        files[picture.INPUT_NAME]=canonical_bytes(document)
        return document,files

    def _review(self,row,*,admit):
        gateway=self._review_gateway(row)
        grant=row.get('source_import') or row.get('review_continuation')
        if row['review'] is None:
            document,_=self._review_files(row)
            row['review']=dict(id='scene-review-'+row['run_id']
                +('-r'+str(row['attempt']) if row.get('attempt') else ''),document=document,input_sha256=digest(document),
                input_refs=None,attempted=False,job_id=None,result=None,usage=None)
            self._write(row)
        review=row['review'];document=review['document']
        if not review['attempted']:
            if not admit:return row
            if grant:
                gateway.preflight_model_limits(row['deadline_at'],grant['model_limits'])
            else:
                gateway.preflight(row['deadline_at'])
            if review['input_refs'] is None:
                rebuilt,files=self._review_files(row)
                _require(rebuilt==document,'scene_space_review_input_changed',409)
                review['input_refs']=gateway.upload(document,files);self._write(row)
            limits=({'model_limits':grant['model_limits']} if grant else {})
            call=gateway.prepare_submission(review['id'],document,row['deadline_at'],review['input_refs'],**limits)
            self._reserve_model(row,review['id'],phase='review')
            review['attempted']=True;self._write(row)
            try:job=call()
            except Exception:
                row['error']='scene_space_review_submission_uncertain';return self._write(row)
            review['job_id']=job['job_id'];return self._write(row)
        try:job=gateway.poll(review['job_id']) if review['job_id'] else gateway.lookup(review['id'])
        except ServiceError as error:
            if error.status==404:
                row['error']='scene_space_review_submission_unconfirmed';return self._write(row)
            raise
        review['job_id']=job['job_id']
        if grant:
            _require(job.get('model_limits')==grant['model_limits']
                and job['job_id'] not in grant['lineage_job_ids']
                and len(set(grant['lineage_job_ids'])|{job['job_id']})<=grant['limits']['maximum_lineage_distinct_jobs'],
                'scene_review_continuation_job_limits_unproven',409)
        if job['state'] in ACTIVE:return self._write(row)
        if job.get('stop_proven') is not True:
            row['error']='scene_space_review_stop_unproven';return self._write(row)
        verified=True
        if grant:
            review['usage'],verified=self._continuation_review_usage(job)
        else:
            review['usage']=_measured_usage(job)
        settled=self.space_store.record_usage(row['owner'],row['run_id'],id=review['id'],phase='review',
            outcome='completed' if job['state']=='completed' else 'failed',**review['usage'],render_seconds=0)
        if grant:
            from .scene_review_continuation import execution_status
            _require(verified and execution_status(settled)['status']=='within_budget',
                'scene_review_continuation_new_usage_unavailable',409)
        _require(job['state']=='completed','scene_space_review_failed',409)
        result=gateway.collect(job['job_id'],review['input_sha256'],document)
        protocol=classified_picture if self._classified(row) else picture
        protocol.validate_result(result,review['input_sha256'],document)
        receipt=gateway.model_frame_receipt(job['job_id'])
        expected={frame['sha256'] for frame in document['observation']['frames']}
        observed={item.get('sha256') for item in receipt.get('images',[]) if item.get('model_request_completed') is True}
        _require(expected<=observed,'scene_space_review_frame_transport_incomplete',409)
        review['result']=result;review['frame_receipt']=receipt;self._write(row)
        if self._classified(row):
            decision=classified_picture.quality_gate(result['review'],row['configuration']['review_policy'],
                document['observation']['frames'],row['configuration']['preview_min_score'])
            review['decision']=deepcopy(decision);self._write(row)
            passed=decision['passed']
        else:
            passed=(result['review']['score']>=row['configuration']['preview_min_score'] and not result['review']['issues'])
        if not passed and not grant \
                and row.get('attempt',0)<row['configuration'].get('max_pre_export_repairs',0) \
                and not result['review']['stop_recommended']:
            row['status']='repair';row['error']=None
            return self._write(row)
        _require(passed,'scene_space_preview_quality_not_met',409)
        row['status']='planned';row['error']=None
        return self._write(row)

    @staticmethod
    def _continuation_review_usage(job):
        from .scene_review_continuation import MODEL_LIMITS
        measured=_measured_usage(job);evidence=job.get('usage_evidence')
        attempt=job.get('attempt') or {}
        valid=(type(evidence) is dict and evidence.get('schema')=='agent-model-usage-evidence.v1'
            and evidence.get('sha256')==digest({k:v for k,v in evidence.items() if k!='sha256'})
            and evidence.get('job_id')==job.get('job_id') and evidence.get('attempt_id')==attempt.get('attempt_id')
            and evidence.get('fence')==attempt.get('fence')
            and evidence.get('authoritative') is True and evidence.get('all_attempts') is True
            and evidence.get('stop_proven') is True and evidence.get('completeness')=='complete'
            and evidence.get('model_limits_verified') is True and evidence.get('model_limits')==MODEL_LIMITS
            and job.get('model_limits')==MODEL_LIMITS
            and type(evidence.get('request_count')) is int and 1<=evidence['request_count']<=8
            and all(type(evidence.get(k)) is int and 0<=evidence[k]<=2**53-1
                for k in ('input_tokens','output_tokens','total_tokens'))
            and evidence['total_tokens']==evidence['input_tokens']+evidence['output_tokens'])
        if valid:
            return dict(model_calls=evidence['request_count'],model_tokens=evidence['total_tokens']),True
        measured['model_tokens']=None
        return measured,False

    def _reserve_model(self,row,operation,*,phase):
        from .scene_review_continuation import execution_status
        status=execution_status(self.space_store.get(row['owner'],row['run_id']))
        remaining=status['remaining']['model_tokens']
        _require(status['status']=='within_budget' and remaining is not None and remaining>0,
            'scene_space_model_budget_unavailable',409)
        self.space_store.reserve(row['owner'],row['run_id'],operation,phase=phase,
            model_calls=row['configuration'].get('model_job_reserve_calls',1),
            model_tokens=min(remaining,row['configuration'].get('model_job_reserve_tokens',262144)))

    def _repair(self,row,*,admit):
        """One same-plan source correction; never a changed-content experiment.

        The admitted still author receives actual output pixels and the exact
        source in a production-only constraint. Historical transfer inputs keep
        base_source=None: this is not a learning transfer or inherited reference.
        """
        if row.get('repair') is None:
            document,files=self._review_files(row);document['mode']='create'
            plan=self.space_store.get(row['owner'],row['run_id'])['frozen']['plan']
            document['constraints']['scene_space_same_content_repair']=dict(
                schema='video.scene-space-source-repair.v1',plan_sha256=row['plan_sha256'],
                source_sha256=row['source_sha256'],source=deepcopy(row['source']),plan=deepcopy(plan),
                review=deepcopy(row['review']['result']['review']),
                instructions=[
                    'Repair only the reported painting defects in this exact production candidate. Preserve the full original goal, style, worlds, object identities, layout, timing, cameras and local animation.',
                    'Return complete editable source and the unchanged method.scene_space plan. Do not create new content, a transfer experiment, a second world or a new camera plan.',
                    'The supplied output images are sampled static evidence only. Do not claim motion, audio, artistic acceptance or user approval.'])
            if row['configuration'].get('plan_repair_revision',0)>=1:
                offer=document['constraints']['scene_space_same_content_repair']
                offer.update(schema='video.scene-space-structural-repair.v1',allow_plan_revision=True,
                    instructions=[
                        'Correct the earliest faulty layer identified in the review: authored geometry, layout, lighting, camera or painting. Preserve the film goal, style, world IDs/seeds and object IDs/kinds. No changed-content experiment.',
                        'If only source changes, return the identical plan. For spatial or camera edits increment plan.revision and budget.revisions.used exactly once; increment each changed world.revision, update its shot world_revision, and retain fps, viewport, duration, provenance, budget caps and used totals.',
                        'Return the complete editable source and method.scene_space plan. The host validates geometry/coverage, records revision lineage, preserves actual consumption, invalidates previous previews and rechecks the replacement.',
                        'Samples remain static evidence. Do not claim continuous motion/audio observation or user artistic acceptance.'])
            document['prior_review']=deepcopy(row['review']['result']['review'])
            document['feedback']='Correct only the defects in constraints.scene_space_same_content_repair.review.'
            document=picture.validate_input(document)
            row['repair']=dict(id='scene-repair-'+row['run_id']+'-'+str(row.get('attempt',0)+1),
                document=document,input_sha256=digest(document),input_refs=None,attempted=False,
                job_id=None,result=None,usage=None,usage_evidence=None)
            self._write(row)
        repair=row['repair'];document=repair['document']
        if not repair['attempted']:
            if not admit:return row
            if repair['input_refs'] is None:
                _,files=self._review_files(row);files[picture.INPUT_NAME]=canonical_bytes(document)
                repair['input_refs']=self.author.upload(document,files);self._write(row)
            call=self.author.prepare_submission(repair['id'],document,row['deadline_at'],repair['input_refs'])
            self._reserve_model(row,repair['id'],phase='repair')
            repair['attempted']=True;self._write(row)
            try:job=call()
            except Exception:
                row['error']='scene_space_repair_submission_uncertain';return self._write(row)
            repair['job_id']=job['job_id'];return self._write(row)
        try:job=self.author.poll(repair['job_id']) if repair['job_id'] else self.author.lookup(repair['id'])
        except ServiceError as error:
            if error.status==404:
                row['error']='scene_space_repair_submission_unconfirmed';return self._write(row)
            raise
        repair['job_id']=job['job_id']
        if job['state'] in ACTIVE:return self._write(row)
        if job.get('stop_proven') is not True:
            row['error']='scene_space_repair_stop_unproven';return self._write(row)
        repair['usage']=_measured_usage(job);repair['usage_evidence']=deepcopy(job.get('usage'))
        self._write(row)
        self.space_store.record_usage(row['owner'],row['run_id'],id=repair['id'],phase='repair',
            outcome='completed' if job['state']=='completed' else 'failed',**repair['usage'],render_seconds=0)
        _require(job['state']=='completed','scene_space_repair_failed',409)
        result=self.author.collect(job['job_id'],repair['input_sha256'],document)
        picture.validate_result(result,repair['input_sha256'],document)
        spatial=validate_scene_method((result.get('method') or {}).get('scene_space'),
            source_scope='scene_data_only',source_refs=[],frame_ids=[])
        source=picture.validate_source(result['source'],constraints=document['constraints'])
        prior_plan=document['constraints']['scene_space_same_content_repair']['plan']
        intent=document['constraints']['scene_space_production'].get('rendering_intent')
        if intent is not None:
            from .spatial_rendering_route import validate_scene_intent_plan
            validate_scene_intent_plan(intent,spatial['plan'])
        change=None
        if row['configuration'].get('plan_repair_revision',0)>=1:
            from .spatial_plan_repair import validate_same_content_plan_repair
            replacement=validate_same_content_plan_repair(prior_plan,spatial['plan'],
                reason='Automatic correction of reviewed defects in the same production scene')
            if replacement['change'] is not None:
                current=self.space_store.get(row['owner'],row['run_id'])
                target_sha=replacement['frozen']['plan_sha256']
                if current['frozen']['plan_sha256']!=target_sha:
                    try:
                        changed=self.space_store.revise(row['owner'],row['run_id'],replacement['plan'],
                            reason=replacement['change']['reason'],expected_plan_sha256=digest(prior_plan))
                    except ServiceError as error:
                        if error.code!='scene_space_plan_revision_conflict':raise
                        changed=self.space_store.get(row['owner'],row['run_id'])
                    _require(changed['frozen']['plan_sha256']==target_sha,'scene_space_repair_plan_changed',409)
                change=replacement['change']
        else:
            _require(spatial['plan']==prior_plan,'scene_space_repair_plan_changed',409)
        _require(source['files']!=row['source']['files'] or change is not None,
            'scene_space_repair_unchanged',409)
        row.setdefault('attempts',[]).append(dict(attempt=row.get('attempt',0),source=deepcopy(row['source']),
            source_sha256=row['source_sha256'],plan_sha256=row['plan_sha256'],
            previews=deepcopy(row['previews']),clips=deepcopy(row.get('clips',[])),
            review=deepcopy(row['review']['result']['review']),review_evidence=deepcopy(row['review']),
            gate=deepcopy(row.get('gate')),plan=deepcopy(prior_plan),plan_change=deepcopy(change)))
        repair['result']=deepcopy(result)
        row.update(source=source,source_sha256=source['source_sha256'],attempt=row.get('attempt',0)+1,
            plan_sha256=digest(spatial['plan']),previews=[],clips=[],review=None,gate=None,status='preview',error=None)
        return self._write(row)

    def _render(self,row,*,admit):
        saved=self.space_store.get(row['owner'],row['run_id'])
        _require(saved['frozen']['plan_sha256']==row['plan_sha256'],
            'scene_space_production_plan_changed',409)
        if row['render_request'] is None:
            if not admit:return row
            window=self._render_window(row,preview=False)
            document=self._render_document(row,saved,export=True)
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
        from .scene_review_continuation import execution_status
        settled_status=execution_status(settled)
        if settled_status['status']!='within_budget':
            row.update(status='failed',stop_proven=True,render_result=deepcopy(child.get('result')),
                error='scene_space_usage_unknown' if settled_status['status']=='usage_unknown'
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

    def _render_document(self,row,saved,*,export=False):
        document=deepcopy(row['author_document'])
        if row.get('source_import'):
            grant=row['source_import'];caps=row['configuration']['render_capability']
            document['constraints']['scene_space_capability']={key:deepcopy(value)
                for key,value in caps.items() if key!='authoring_constraints'}
            constraints=caps.get('authoring_constraints',{})
            for key in ('assets','drawing_libraries','drawing_library_api','runtime_identity'):
                if key in constraints:document['constraints'][key]=deepcopy(constraints[key])
            document['constraints']['scene_source_import']=dict(schema=grant['schema'],
                grant_sha256=grant['sha256'],parent_run_id=grant['parent_run_id'],
                source_sha256=grant['source_sha256'],plan_sha256=grant['plan_sha256'],
                configuration_sha256=grant['configuration_sha256'],parent_usage_sha256=grant['parent_usage_sha256'],
                cumulative_cost_status='unknown',budget_scope='new_source_import_execution_only')
        if export and self._classified(row):
            review=row.get('review') or {}
            reviewed,_=self._review_files(row)
            _require(reviewed==review.get('document') and digest(reviewed)==review.get('input_sha256'),
                'scene_space_review_export_binding_changed',409)
            result=classified_picture.validate_result(review.get('result'),review['input_sha256'],reviewed)
            decision=classified_picture.quality_gate(result['review'],row['configuration']['review_policy'],
                reviewed['observation']['frames'],row['configuration']['preview_min_score'])
            _require(decision['passed'] and decision==review.get('decision'),
                'scene_space_review_export_binding_changed',409)
            receipt=review.get('frame_receipt') or {}
            observed={item.get('sha256') for item in receipt.get('images',[])
                if item.get('model_request_completed') is True}
            _require({f['sha256'] for f in reviewed['observation']['frames']}<=observed,
                'scene_space_review_frame_transport_incomplete',409)
            document['constraints']['scene_review_binding']=dict(schema='video.scene-review-export-binding.v1',
                profile=row['configuration']['review_profile'],profile_revision=row['configuration']['review_revision'],
                gate_schema=GATE_SCHEMA,source_sha256=row['source_sha256'],plan_sha256=row['plan_sha256'],
                review_job_id=review['job_id'],input_sha256=review['input_sha256'],
                policy_sha256=digest(row['configuration']['review_policy']),result_sha256=digest(result),
                decision_sha256=digest(decision),
                evidence_sha256=digest(reviewed['constraints']['review_evidence']),
                frame_receipt_sha256=digest(receipt))
        intent=document['constraints']['scene_space_production'].get('rendering_intent')
        limit=row['configuration'].get('material_noise_limit')
        if intent is not None and limit is not None:
            from .scene_review_continuation import execution_status, execution_usage
            status=execution_status(saved);ledger=execution_usage(saved)
            _require(status['status']=='within_budget'
                and status['remaining']['model_tokens'] is not None,
                'scene_appearance_usage_unknown',409)
            document['constraints']['scene_space_quality']=dict(intent=deepcopy(intent),
                configuration_sha256=row['configuration']['configuration_sha256'],
                usage_lineage=ledger['lineage_id'],usage_sha256=digest(ledger),
                material_noise_limit=limit,usage_known=True)
            if row.get('review_continuation'):
                grant=row['review_continuation']
                document['constraints']['scene_review_continuation']=dict(schema=grant['schema'],
                    grant_sha256=grant['sha256'],legacy_usage_sha256=grant['legacy_usage_sha256'],
                    budget_scope='explicit_new_grant_only',cumulative_cost_status='unknown',
                    original_total_token_cap_proven=False)
        return document

    def _render_window(self,row,*,preview):
        """Freeze an execution ceiling from resource capacity, never the model clock.

        A reservation covers the entire allowed execution window. Sequential
        settlement releases unused capacity while retaining measured spending;
        a different concurrent reservation cannot borrow this same window.
        """
        record=self.space_store.get(row['owner'],row['run_id'])
        from .scene_review_continuation import execution_status
        remaining=execution_status(record)['remaining']['render_seconds']
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
        if (row.get('review_continuation') or row.get('source_import')) and row['status'] not in {'completed','failed','cancelled'}:
            _require(self.clock()<row['deadline_at'],'scene_review_continuation_deadline_expired',409)
        self._project(run,row);persist(run)
        if row['status']=='author':row=self._author(row,admit=admit)
        elif row['status']=='preview':row=self._preview(row,admit=admit)
        elif row['status']=='clip_preview':row=self._clip_preview(row,admit=admit)
        elif row['status']=='review':row=self._review(row,admit=admit)
        elif row['status']=='repair':row=self._repair(row,admit=admit)
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
        # A crash after a durable hold but before the dispatch intent leaves a
        # known zero-cost, undispatched reservation. Release it on cancellation;
        # attempted/uncertain operations must instead reconcile their original ID.
        operations=[(p['key'],p['attempted']) for p in row['previews']+row.get('clips',[])]
        operations.append((row['render_request_key'],row['render_submit_attempted']))
        operations.extend((p['id'],p['attempted']) for p in (row.get('review'),row.get('repair')) if p)
        if row['plan_sha256']:
            record=self.space_store.get(row['owner'],row['run_id'])
            undispatched={key for key,attempted in operations if not attempted}
            for reservation in record['reservations']:
                if reservation['id'] in undispatched and reservation['status']=='active':
                    self.space_store.record_usage(row['owner'],row['run_id'],id=reservation['id'],
                        phase=reservation['phase'],outcome='cancelled',model_calls=0,model_tokens=0,render_seconds=0)
        if row['render_submit_attempted'] and row['render_id'] is None:
            try:child=self.renderer.lookup(row['owner'],row['render_request_key'])
            except ServiceError:return False
            if child is None:return False
            row['render_id']=child['id'];self._write(row)
        if row['render_id']:
            child=self.renderer.cancel(row['owner'],row['render_id'])
            if child.get('stop_proven') is not True:return False
            self._settle_cancelled_render(row,child,row['render_request_key'],'export')
        elif row.get('repair') and row['repair']['attempted'] and row['repair'].get('result') is None:
            repair=row['repair']
            try:job=self.author.poll(repair['job_id']) if repair['job_id'] else self.author.lookup(repair['id'])
            except ServiceError:return False
            if job['state'] in ACTIVE:
                self.author.cancel(job['job_id']);return False
            if job.get('stop_proven') is not True:return False
            record=self.space_store.get(row['owner'],row['run_id'])
            if not any(event['id']==repair['id'] for event in record['usage']['events']):
                self.space_store.record_usage(row['owner'],row['run_id'],id=repair['id'],phase='repair',
                    outcome='completed' if job['state']=='completed' else 'cancelled',
                    **_measured_usage(job),render_seconds=0)
        elif row.get('review') and row['review']['attempted'] and row['review'].get('result') is None:
            review=row['review']
            try:
                gateway=self._review_gateway(row)
                job=gateway.poll(review['job_id']) if review['job_id'] else gateway.lookup(review['id'])
            except ServiceError:return False
            if job['state'] in ACTIVE:
                gateway.cancel(job['job_id']);return False
            if job.get('stop_proven') is not True:return False
            record=self.space_store.get(row['owner'],row['run_id'])
            spending=record.get('continuation_usage',record['usage'])
            if not any(event['id']==review['id'] for event in spending['events']):
                measurement=(self._continuation_review_usage(job)[0]
                    if row.get('review_continuation') or row.get('source_import') else _measured_usage(job))
                self.space_store.record_usage(row['owner'],row['run_id'],id=review['id'],phase='review',
                    outcome='completed' if job['state']=='completed' else 'cancelled',
                    **measurement,render_seconds=0)
        elif any(item['attempted'] and item['status']!='completed'
                for item in row['previews']+row.get('clips',[])):
            for item in row['previews']+row.get('clips',[]):
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
        spending=record.get('continuation_usage',record['usage'])
        if any(event['id']==key for event in spending['events']):return
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
        candidates=row['previews']+row.get('clips',[])
        for attempt in row.get('attempts',[]):candidates+=attempt['previews']+attempt.get('clips',[])
        item=next((item for item in candidates if item['render_id']==render_id),None)
        _require(item is not None,'not_found',404)
        _require(item['status']=='completed','scene_space_preview_unavailable',409)
        raw,mime=self.renderer.video(owner,render_id)
        clip='start_frame' in item
        _require(mime==('video/mp4' if clip else 'image/png')
            and hashlib.sha256(raw).hexdigest()==(item['result']['sha256'] if clip else item['artifact_sha256']),
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


class _OwnedSceneClients:
    def __init__(self,*clients):self.clients=[client for client in clients if client is not None]
    def close(self):
        for client in self.clients:client.close()


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
    review_settings=AgentSettings.from_env({
        'VIDEO_AGENT_URL':env.get('VIDEO_SCENE_SPACE_REVIEW_V2_URL',''),
        'VIDEO_AGENT_TOKEN':env.get('VIDEO_SCENE_SPACE_REVIEW_V2_TOKEN',''),
        'VIDEO_AGENT_TOKEN_FILE':env.get('VIDEO_SCENE_SPACE_REVIEW_V2_TOKEN_FILE',''),
        'VIDEO_AGENT_PROFILE_REVISION':env.get('VIDEO_SCENE_SPACE_REVIEW_V2_PROFILE_REVISION','1')})
    owned=AgentClient(settings.base_url,settings.token) if settings else None
    owned_review=AgentClient(review_settings.base_url,review_settings.token) if review_settings else None
    client=owned or fallback_agent_client
    author=SceneSpaceWorldAuthorGateway(client,settings.revision if settings else '1') if client else None
    reviewer=SceneSpaceWorldReviewGateway(client,settings.revision if settings else '1') if client else None
    structured=SceneSpaceClassifiedReviewGateway(owned_review,review_settings.revision) if owned_review else None
    if gate=='1' and (author is None or renderer is None):
        if owned:owned.close()
        if owned_review:owned_review.close()
        raise ValueError('Scene production requires the static world author and private scene renderer')
    root=Path(store.path).with_name('scene-space-production')
    return SceneSpaceProductionAdapter(root,space_store=SceneSpaceStore(root/'space'),
        author=author,reviewer=reviewer,structured_reviewer=structured,renderer=renderer,
        library_adapter=library_adapter,enabled=gate=='1'),(
            _OwnedSceneClients(owned,owned_review) if owned_review else owned)
