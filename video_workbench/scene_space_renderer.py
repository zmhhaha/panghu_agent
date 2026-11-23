"""Versioned host-owned scene-space rendering, separate from historical hosts.

The authored three-file scene supplies object painters. Frozen plans and the
compositor are independent, checksum-bound host inputs, never executable recipe
references. The existing container isolation and owner-verified stop proof apply.
"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles.video_animation_create import source_sha256
from agent_service.profiles.video_animation_experiment import _validate_source_output
from .scene_space_plan import compile_evaluator, freeze_plan, technical_retry

RUNTIME = Path(__file__).with_name('motion_renderer')/'scene_space_runtime.mjs'
SURFACE_RUNTIME = Path(__file__).with_name('motion_renderer')/'scene_space_surface.mjs'
SURFACE_BUDGET = Path(__file__).with_name('motion_renderer')/'scene_space_surface_budget.mjs'
GEOMETRY_RUNTIME = Path(__file__).with_name('motion_renderer')/'scene_space_geometry.mjs'
GEOMETRY_BUDGET = Path(__file__).with_name('motion_renderer')/'scene_space_geometry_budget.mjs'
HOST = Path(__file__).with_name('scene_space_host.mjs')
SCHEMA = 'video.scene-space-render-binding.v1'
CAPABILITY_SCHEMA = 'video.scene-space-render-capability.v1'
MAX_CONTEXTS = 500
MAX_RASTER_SIDE = 8192
MAX_SIDECAR_BYTES = 16*1024*1024
SURFACE_WORK_BYTES = 2*1024*1024
SURFACE_RETAINED_BYTES = 16*1024*1024
MAX_SURFACE_FIELDS = 64
GEOMETRY_WORK_BYTES = 1024*1024
GEOMETRY_RETAINED_BYTES = 16*1024*1024
MAX_GEOMETRY_HANDLES = 32
MAX_GEOMETRY_VERTICES = 16384
MAX_GEOMETRY_TRIANGLES = 32768
MAX_GEOMETRY_RASTER_SIDE = 1024
MAX_GEOMETRY_PIXELS = 262144
MAX_GEOMETRY_RASTER_WORK = 33554432


def _require(value, code='scene_space_render_invalid', status=422):
    if not value:
        raise ServiceError(code,status)


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _read_file(path,maximum):
    from .animation_creation_renderer import _read
    return _read(path,maximum)


def _runtime_bytes():
    raw=RUNTIME.read_bytes()
    _require(len(raw)<=256*1024,'scene_space_runtime_size_exceeded')
    return raw


def _surface_runtime_bytes():
    raw=SURFACE_RUNTIME.read_bytes()
    _require(len(raw)<=256*1024,'scene_space_surface_runtime_size_exceeded')
    return raw


def _surface_budget_bytes():
    raw=SURFACE_BUDGET.read_bytes()
    _require(len(raw)<=64*1024,'scene_space_surface_budget_size_exceeded')
    return raw


def _geometry_runtime_bytes():
    raw=GEOMETRY_RUNTIME.read_bytes()
    _require(len(raw)<=256*1024,'scene_space_geometry_runtime_size_exceeded')
    return raw


def _geometry_budget_bytes():
    raw=GEOMETRY_BUDGET.read_bytes()
    _require(len(raw)<=64*1024,'scene_space_geometry_budget_size_exceeded')
    return raw


def render_capability(*, admitted_engines=('canvas',)):
    """Only the trusted configured renderer determines actual engine admission."""
    engines=[engine for engine in admitted_engines if engine=='canvas']
    return dict(schema=CAPABILITY_SCHEMA,admitted=bool(engines),engines=engines,
        world_schema='video.scene-world.v1',plan_schema='video.scene-space-plan.v1',
        runtime_sha256=_hash(_runtime_bytes()),host_sha256=_hash(HOST.read_bytes()),
        surface_runtime_sha256=_hash(_surface_runtime_bytes()),surface_budget_sha256=_hash(_surface_budget_bytes()),
        geometry_runtime_sha256=_hash(_geometry_runtime_bytes()),geometry_budget_sha256=_hash(_geometry_budget_bytes()),artwork_api_revision=3,
        artwork_api='window.__sceneSpaceArtwork',source_files=['scene.html','scene.js','scene.css'],
        artwork_extensions=dict(coverage='paintCoverage(ctx,object,world,tools) -> null | {substrate:[r,g,b]}',
            surface='tools.surface',geometry='tools.geometry',coverage_scope='independent_object_coverage_not_pigment_opacity',
            surface_scope='authored_object_local_2_5d_hypothesis_not_observed_3d',
            geometry_scope='authored_shared_xyz_mesh_and_fixed_orthographic_projection_not_observed_3d'),
        clock='absolute_frame',reuse_scope='within_film',cache_policies=['static'],audio_modes=['silent'],
        other_backend_scope='matrix_adapters_not_render_admission',
        limits=dict(width=1280,height=720,fps=24,duration_frames=7200,max_contexts=MAX_CONTEXTS,
            max_raster_side=MAX_RASTER_SIDE,max_sidecar_bytes=MAX_SIDECAR_BYTES,max_font_faces=64,
            max_surface_work_bytes=SURFACE_WORK_BYTES,max_surface_retained_bytes=SURFACE_RETAINED_BYTES,
            max_surface_fields=MAX_SURFACE_FIELDS,max_geometry_work_bytes=GEOMETRY_WORK_BYTES,
            max_geometry_retained_bytes=GEOMETRY_RETAINED_BYTES,max_geometry_handles=MAX_GEOMETRY_HANDLES,
            max_geometry_vertices=MAX_GEOMETRY_VERTICES,max_geometry_triangles=MAX_GEOMETRY_TRIANGLES,
            max_geometry_raster_side=MAX_GEOMETRY_RASTER_SIDE,max_geometry_pixels=MAX_GEOMETRY_PIXELS,
            max_geometry_raster_work=MAX_GEOMETRY_RASTER_WORK))


def _magnification(matrix):
    a,b,c,d=matrix[:4]
    left,right=a*a+b*b,c*c+d*d
    cross=a*c+b*d
    return math.sqrt(max(0,(left+right+math.sqrt((left-right)**2+4*cross*cross))/2))


def prepare_scene_render(plan, *, source_sha256, engine='canvas', frame_range=None, background=None):
    """Freeze artwork scope and resolution before any authored painter runs."""
    _require(engine=='canvas','scene_space_backend_not_admitted')
    _require(type(source_sha256) is str and len(source_sha256)==64
        and all(c in '0123456789abcdef' for c in source_sha256))
    frozen=(technical_retry(plan,plan.get('plan_sha256')) if type(plan) is dict
        and plan.get('schema')=='video.scene-space-frozen-plan.v1' else freeze_plan(plan))
    checked=frozen['plan'];duration=checked['duration_frames']
    _require(all(layer['cache_policy']=='static' for world in checked['worlds'] for layer in world['layers']),
        'scene_space_cache_policy_not_admitted')
    if frame_range is None:
        frame_range=dict(start_frame=0,end_frame=duration)
    _require(type(frame_range) is dict and set(frame_range)=={'start_frame','end_frame'})
    start,end=frame_range['start_frame'],frame_range['end_frame']
    _require(type(start) is int and type(end) is int and 0<=start<end<=duration)
    if background is not None:
        _require(type(background) is list and len(background)==4
            and all(type(n) is int and 0<=n<=255 for n in background))
    evaluator=compile_evaluator(frozen)
    densities={}
    # Whole-plan cache resolution remains stable across execution intervals.
    for frame in range(duration+1):
        state=evaluator.frame(frame)
        for obj in state['objects']:
            key=(state['world_id'],obj['id'])
            densities[key]=max(densities.get(key,0),_magnification(obj['screen_matrix']))
    caches=[]
    for world in checked['worlds']:
        for obj in world['objects']:
            density=densities[(world['id'],obj['id'])]
            width=max(1,math.ceil(obj['bounds']['width']*density))
            height=max(1,math.ceil(obj['bounds']['height']*density))
            _require(width<=MAX_RASTER_SIDE and height<=MAX_RASTER_SIDE,'scene_space_object_raster_exceeded')
            caches.append(dict(world_id=world['id'],object_id=obj['id'],density=density,
                width=width,height=height,pixels=width*height))
    pixels=sum(row['pixels'] for row in caches)
    _require(pixels<=checked['budget']['render']['max_cached_pixels'],'scene_space_object_cache_budget_exceeded')
    # A single temporary coverage canvas is reused for every object. No mask
    # is retained with the static color caches; account for the worst object
    # before executing the optional source callback.
    mask_pixels=max(row['pixels'] for row in caches)
    _require(len(caches)+3<=MAX_CONTEXTS,'scene_space_context_budget_exceeded')
    blur=max((layer['blur'] for world in checked['worlds'] for layer in world['layers']),default=0)
    padding=math.ceil(blur*3)
    viewport=checked['viewport']
    scratch=(viewport['width']+2*padding)*(viewport['height']+2*padding)
    temporary_bytes=(4*(scratch+viewport['width']*viewport['height']+mask_pixels)
        +SURFACE_WORK_BYTES+SURFACE_RETAINED_BYTES+GEOMETRY_WORK_BYTES+GEOMETRY_RETAINED_BYTES)
    _require(temporary_bytes<=checked['budget']['render']['max_temporary_bytes'],
        'scene_space_compositor_temporary_budget_exceeded')
    body=dict(schema=SCHEMA,backend=engine,frozen=frozen,source_sha256=source_sha256,
        runtime_sha256=_hash(_runtime_bytes()),surface_runtime_sha256=_hash(_surface_runtime_bytes()),
        surface_budget_sha256=_hash(_surface_budget_bytes()),
        geometry_runtime_sha256=_hash(_geometry_runtime_bytes()),geometry_budget_sha256=_hash(_geometry_budget_bytes()),
        artwork_api_revision=3,frame_range=deepcopy(frame_range),
        background=deepcopy(background),object_caches=caches,total_cached_pixels=pixels,
        scratch=dict(width=viewport['width']+2*padding,height=viewport['height']+2*padding,
            padding=padding,pixels=scratch),temporary_bytes=temporary_bytes,
        coverage=dict(mode='substrate_under_pigment_then_mask',max_mask_pixels=mask_pixels,
            native_contexts=1,mask_lifetime='one_reused_temporary_canvas_during_cache_initialization'),
        surface_work_bytes=SURFACE_WORK_BYTES,surface_retained_bytes=SURFACE_RETAINED_BYTES,
        surface_max_fields=MAX_SURFACE_FIELDS,native_context_budget=len(caches)+3,
        geometry_work_bytes=GEOMETRY_WORK_BYTES,geometry_retained_bytes=GEOMETRY_RETAINED_BYTES,
        geometry_max_handles=MAX_GEOMETRY_HANDLES,
        evidence_scope='render_inputs_not_visual_acceptance')
    _require(len(canonical_bytes(body))<=MAX_SIDECAR_BYTES,'scene_space_sidecar_size_exceeded')
    return dict(body,sha256=digest(body))


def scene_renderer(owner):
    """Lazily attach a separate operation namespace to a configured renderer."""
    value=getattr(owner,'_scene_space_renderer',None)
    if value is None:
        value=SceneSpaceRenderer(owner.root/'scene-space',runtime_renderer=owner)
        owner._scene_space_renderer=value
    return value


class SceneSpaceRenderer:
    """One durable render claim, isolated host, bound output and stop evidence."""
    def __init__(self, root, *, runtime_renderer):
        from .animation_method_renderer import MethodAnimationRenderer
        self.runtime_renderer=runtime_renderer
        self.owner=MethodAnimationRenderer(root,runtime_renderer.image,
            assets_dir=runtime_renderer.assets_dir,enabled=runtime_renderer.enabled,
            timeout=runtime_renderer.timeout,hyperframes=True,legacy_bridge=True)
        self.root=self.owner.root

    def capabilities(self):
        caps=self.runtime_renderer.capabilities()
        result=render_capability(admitted_engines=caps.get('allowed_engines',[]) if self.owner.enabled else ())
        result['authoring_constraints']=self.authoring_constraints()
        return result

    def authoring_constraints(self):
        from .ink_landscape_library import descriptor,read_assets,runtime_guidance
        from .animation_method_renderer import ENGINE_RUNTIME
        caps=self.runtime_renderer.capabilities()
        library=descriptor();read_assets()
        library_api=runtime_guidance().replace(
            'Use the existing window.__animationExperiment ready/seek interface.',
            'For this scene-space host, register window.__sceneSpaceArtwork.paintObject(ctx, object, world); the host owns seek and transforms.').replace(
            'Each seek must derive state from the supplied absolute seconds and clear/redraw deterministically.',
            'Paint the complete static object once; plan tracks provide local animation under the host clock.')
        library_api+=(' ink.renderCanvas resets its internal context transform and restores it on return. '
            'For direct rendering into an object cache, use object.bounds.x=0 and object.bounds.y=0, '
            'and a zero-origin ink document whose width/height equal object.bounds.width/height; '
            'express world placement through object.transform, which the host applies. '
            'For nonzero local bounds, render into an explicitly allowed detached local Canvas first, '
            'then drawImage it at the declared bounds under the caller context; count that extra native context '
            'and buffer in the existing resource budget. Use clear:false for a transparent cached object; '
            'the ink default clear:true also paints its document paper background.')
        return dict(allowed_engines=self.capabilities_without_guidance()['engines'],
            assets=dict(caps.get('assets',{}),**library['assets']),
            drawing_libraries={'ink-landscape':library},drawing_library_api=library_api,
            runtime_identity=dict(image_digest=self.owner.image_digest,host_sha256=_hash(HOST.read_bytes()),
                surface_runtime_sha256=_hash(_surface_runtime_bytes()),artwork_api_revision=3,
                surface_budget_sha256=_hash(_surface_budget_bytes()),
                geometry_runtime_sha256=_hash(_geometry_runtime_bytes()),geometry_budget_sha256=_hash(_geometry_budget_bytes()),
                engine_runtime_sha256=_hash(ENGINE_RUNTIME.read_bytes()),
                admission_id='scene-space-canvas-v1@'+self.owner.image_digest))

    def capabilities_without_guidance(self):
        return render_capability(admitted_engines=self.runtime_renderer.capabilities().get('allowed_engines',[]) if self.owner.enabled else ())

    def _binding(self, operation_id, source, document, plan, *, deadline_at=None, frame_range=None):
        from .animation_method_renderer import ENGINE_RUNTIME, PROJECT_RUNTIME, HYPERFRAMES_CORE_SHA256
        self.owner._directory(operation_id)
        _require(type(source) is dict and {'engine','strategy','files'}<=set(source)
            and set(source)<={'engine','strategy','files','source_sha256'},'scene_space_source_invalid')
        _require(type(document) is dict and type(document.get('constraints')) is dict,'scene_space_document_invalid')
        _require(deadline_at is None or type(deadline_at) in (int,float)
            and 0<deadline_at<10**12 and math.isfinite(deadline_at))
        payload=_validate_source_output({key:source[key] for key in ('engine','strategy','files')})
        source_hash=source_sha256(payload)
        _require(source.get('source_sha256',source_hash)==source_hash,'scene_space_source_changed',409)
        caps=self.runtime_renderer.capabilities()
        _require(payload['engine'] in self.capabilities()['engines'],'scene_space_backend_not_admitted')
        bounds=document.get('constraints',{})
        _require(not document.get('sound_events'),'scene_space_audio_not_admitted')
        frozen_image=bounds.get('runtime_identity')
        if frozen_image is not None:
            _require(type(frozen_image) is dict and frozen_image.get('image_digest')==self.owner.image_digest,
                'scene_space_frozen_image_changed',409)
        learning_offer=bounds.get('scene_space_learning')
        if learning_offer is not None:
            _require(type(learning_offer) is dict and learning_offer.get('schema')=='video.scene-space-learning-offer.v1'
                and learning_offer.get('runtime')==render_capability(admitted_engines=caps.get('allowed_engines',[])),
                'scene_space_frozen_runtime_changed',409)
        static='duration_seconds' not in bounds
        if static and frame_range is None:
            frame_range=dict(start_frame=0,end_frame=1)
        render=prepare_scene_render(plan,source_sha256=source_hash,engine=payload['engine'],frame_range=frame_range)
        viewport=render['frozen']['plan']['viewport']
        _require(all(bounds.get(key)==viewport[key] for key in ('width','height')),
            'scene_space_viewport_changed',409)
        _require(320<=viewport['width']<=1280 and 180<=viewport['height']<=720,'scene_space_viewport_not_admitted')
        _require(not static or render['frame_range']['end_frame']-render['frame_range']['start_frame']==1
            or render['frozen']['plan']['duration_frames']==1,'scene_space_picture_single_frame_required')
        duration=(render['frame_range']['end_frame']-render['frame_range']['start_frame'])/24
        _require(static or bounds['duration_seconds']==duration,'scene_space_duration_changed',409)
        assets=document.get('assets',bounds.get('assets',{}))
        from .ink_landscape_library import descriptor
        from .learning_drawing_libraries import drawing_libraries
        library=descriptor();offered=dict(caps.get('assets',{}),**library['assets'])
        _require(type(assets) is dict and all(offered.get(name)==sha for name,sha in assets.items()),
            'scene_space_assets_not_admitted',409)
        assets=deepcopy(assets)
        uses_ink=any('ink-landscape.mjs' in content for content in payload['files'].values())
        declared=bounds.get('drawing_libraries')
        selected=drawing_libraries(declared,assets) if declared is not None else {}
        if uses_ink:
            _require(selected=={'ink-landscape':library},'scene_space_drawing_library_not_declared')
        else:
            selected={}
            for name in library['assets']:assets.pop(name,None)
        request=dict(schema='video.scene-space-render-request.v1',engine=payload['engine'],
            constraints=dict(width=viewport['width'],height=viewport['height'],fps=24,
                duration_seconds=duration),
            static=static,source_sha256=source_hash,
            files_sha256={name:_hash(text.encode('utf-8')) for name,text in payload['files'].items()},
            assets_sha256=deepcopy(assets),sidecar_sha256=_hash(canonical_bytes(render)),
            scene_runtime_sha256=render['runtime_sha256'],host_sha256=_hash(HOST.read_bytes()),
            surface_runtime_sha256=render['surface_runtime_sha256'],artwork_api_revision=3,
            surface_budget_sha256=render['surface_budget_sha256'],
            geometry_runtime_sha256=render['geometry_runtime_sha256'],geometry_budget_sha256=render['geometry_budget_sha256'],
            engine_runtime_sha256=_hash(ENGINE_RUNTIME.read_bytes()),
            project_runtime_sha256=_hash(PROJECT_RUNTIME.read_bytes()),
            hyperframes_core_sha256=HYPERFRAMES_CORE_SHA256,image_digest=self.owner.image_digest,
            max_media_bytes=32*1024*1024,max_frame_bytes=512*1024*1024)
        if selected:request['drawing_libraries']=selected
        project_duration=1 if static else duration
        request['project']=dict(schema='video.hyperframes-project.v1',project_id='space-'+digest(dict(
            operation_id=operation_id,source_sha256=source_hash))[:32],width=viewport['width'],height=viewport['height'],
            fps=24,duration_seconds=project_duration,entry='scene.html',scenes=[dict(id='scene-space',
                start_seconds=0,duration_seconds=project_duration,entry='scene.html',
                source_sha256=request['files_sha256']['scene.html'],engine=payload['engine'])])
        request['project_sha256']=digest(request['project'])
        # Compare the actual bytes selected for this exact render, after all
        # helpers and host inputs have been read. An earlier capability check
        # alone cannot pin a deployment that changes during preparation.
        descriptors=[]
        if learning_offer is not None:descriptors.append(learning_offer['runtime'])
        if 'scene_space_capability' in bounds:descriptors.append(bounds['scene_space_capability'])
        for expected in descriptors:
            _require(type(expected) is dict and expected.get('schema')==CAPABILITY_SCHEMA
                and expected.get('admitted') is True and payload['engine'] in expected.get('engines',[])
                and expected.get('host_sha256')==request['host_sha256']
                and expected.get('runtime_sha256')==request['scene_runtime_sha256']
                and expected.get('surface_runtime_sha256')==request['surface_runtime_sha256']
                and expected.get('surface_budget_sha256')==request['surface_budget_sha256']
                and expected.get('geometry_runtime_sha256')==request['geometry_runtime_sha256']
                and expected.get('geometry_budget_sha256')==request['geometry_budget_sha256']
                and expected.get('artwork_api_revision')==request['artwork_api_revision'],
                'scene_space_frozen_runtime_changed',409)
        if frozen_image is not None and 'engine_runtime_sha256' in frozen_image:
            _require(frozen_image['engine_runtime_sha256']==request['engine_runtime_sha256'],
                'scene_space_frozen_engine_runtime_changed',409)
        if frozen_image is not None and 'surface_runtime_sha256' in frozen_image:
            _require(frozen_image['surface_runtime_sha256']==request['surface_runtime_sha256']
                and frozen_image.get('surface_budget_sha256')==request['surface_budget_sha256']
                and frozen_image.get('artwork_api_revision')==request['artwork_api_revision'],
                'scene_space_frozen_surface_runtime_changed',409)
        if frozen_image is not None and 'geometry_runtime_sha256' in frozen_image:
            _require(frozen_image['geometry_runtime_sha256']==request['geometry_runtime_sha256']
                and frozen_image.get('geometry_budget_sha256')==request['geometry_budget_sha256']
                and frozen_image.get('artwork_api_revision')==request['artwork_api_revision'],
                'scene_space_frozen_geometry_runtime_changed',409)
        request['identity']=digest(request)
        return dict(schema='video.scene-space-render-operation.v1',operation_id=operation_id,
            source=payload,original_source=deepcopy(source),source_sha256=source_hash,document=deepcopy(document),input_sha256=digest(document),
            scene_space=render,request=request,request_sha256=digest(request),image=self.owner.image,
            image_digest=self.owner.image_digest,timeout_seconds=self.owner.timeout,
            configured_timeout_seconds=self.owner.timeout,deadline_at=deadline_at)

    def render(self, operation_id, source, document, sound_events=None, *, scene_space,
               deadline_at=None, frame_range=None,expected_request_sha256=None):
        try:
            return self._render_operation(operation_id,source,document,sound_events,scene_space=scene_space,
                deadline_at=deadline_at,frame_range=frame_range,expected_request_sha256=expected_request_sha256)
        except ServiceError as error:
            # The durable renderer claim always precedes container dispatch.
            # Missing or unreadable earlier evidence cannot prove it stopped.
            try:
                absent=self._load(operation_id) is None and not self.owner._directory(operation_id).exists()
            except (OSError,ServiceError):
                absent=False
            if absent:
                error.not_dispatched=True;error.stop_proven=True
            raise

    def _render_operation(self,operation_id,source,document,sound_events=None,*,scene_space,
                          deadline_at=None,frame_range=None,expected_request_sha256=None):
        from .animation_creation_renderer import _write
        from .animation_method_renderer import ENGINE_RUNTIME, PROJECT_RUNTIME, execute_method_render, _TABLE
        _require(self.owner.enabled,'scene_space_renderer_disabled',503)
        _require(not sound_events and not document.get('sound_events'), 'scene_space_audio_not_admitted')
        binding=self._binding(operation_id,source,document,scene_space,
            deadline_at=deadline_at,frame_range=frame_range)
        if expected_request_sha256 is not None:
            if type(expected_request_sha256) is not str or expected_request_sha256!=binding['request_sha256']:
                error=ServiceError('scene_space_render_expected_request_changed',409)
                # This invocation has not dispatched, but an earlier durable
                # operation with the same ID can still be running/uncertain.
                if self._load(operation_id) is None and not self.owner._directory(operation_id).exists():
                    error.not_dispatched=True
                    error.stop_proven=True
                raise error
        with closing(self.owner._connect(create=True)) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT binding FROM '+_TABLE+' WHERE operation_id=?',(operation_id,)).fetchone()
            if row is not None:
                stored=json.loads(row['binding'])
                binding['timeout_seconds']=stored['timeout_seconds']
                _require(stored==binding,'scene_space_operation_conflict',409)
                db.commit()
                return self.lookup(operation_id)
            _require(not self.owner._directory(operation_id).exists(),'scene_space_render_orphan_directory',409)
            if deadline_at is not None:
                _require(deadline_at-time.time()>=30,'scene_space_deadline_insufficient',409)
                binding['timeout_seconds']=min(binding['timeout_seconds'],deadline_at-time.time())
            db.execute('INSERT INTO '+_TABLE+'(operation_id,binding_sha256,binding) VALUES (?,?,?)',
                (operation_id,digest(binding),canonical_bytes(binding)))
            db.commit()
        directory=self.owner._directory(operation_id)
        preparing=True
        created_directory=False
        try:
            directory.mkdir(parents=True,exist_ok=False)
            created_directory=True
            (directory/'input/scene/assets').mkdir(parents=True)
            for name,text in binding['source']['files'].items():
                _write(directory/'input/scene'/name,text.encode('utf-8'))
            for name,sha in binding['request']['assets_sha256'].items():
                from .ink_landscape_library import descriptor,read_assets
                raw=(read_assets()[name] if name in descriptor()['assets']
                    else self.runtime_renderer._read_static_asset(name))
                _require(_hash(raw)==sha,'scene_space_asset_changed',409)
                _write(directory/'input/scene/assets'/name,raw)
            files={'host.mjs':HOST.read_bytes(),'engine_runtime.mjs':ENGINE_RUNTIME.read_bytes(),
                'hyperframes_project.mjs':PROJECT_RUNTIME.read_bytes(),
                'input/scene-space-runtime.mjs':_runtime_bytes(),
                'input/scene_space_surface.mjs':_surface_runtime_bytes(),
                'input/scene_space_surface_budget.mjs':_surface_budget_bytes(),
                'input/scene_space_geometry.mjs':_geometry_runtime_bytes(),
                'input/scene_space_geometry_budget.mjs':_geometry_budget_bytes(),
                'input/scene-space.json':canonical_bytes(binding['scene_space']),
                'input/request.json':canonical_bytes(binding['request']),
                'binding.json':canonical_bytes(binding),'source.json':canonical_bytes(binding['original_source']),
                'render-plan.json':canonical_bytes(dict(directory=str(directory),image=self.owner.image,
                    repository=self.owner.repository,image_digest=self.owner.image_digest,
                    request_sha256=binding['request_sha256'],timeout_seconds=binding['timeout_seconds'],
                    deadline_at=deadline_at))}
            for name,raw in files.items():_write(directory/name,raw)
            preparing=False
            execute_method_render(directory)
        except Exception as error:
            if preparing and created_directory and not (directory/'render-outcome.json').exists():
                _write(directory/'render-outcome.json',canonical_bytes(dict(
                    schema='video.animation-method-render-outcome.v1',request_identity=binding['request']['identity'],
                    request_sha256=binding['request_sha256'],image=binding['image'],status='failed',
                    error=getattr(error,'code',type(error).__name__),actual_image_id=None,container_id=None,
                    cleanup=dict(status='not_created',container_id=None),automatic_retry=False)))
        return self.lookup(operation_id)

    def validate_request(self,operation_id,source,document,*,scene_space,frame_range=None,deadline_at=None):
        """Pure pre-claim validation for durable asynchronous transports."""
        _require(self.owner.enabled,'scene_space_renderer_disabled',503)
        binding=self._binding(operation_id,source,document,scene_space,
            frame_range=frame_range,deadline_at=deadline_at)
        _require(deadline_at is None or deadline_at-time.time()>=30,'scene_space_deadline_insufficient',409)
        return binding

    submit=render

    def _load(self, operation_id):
        from .animation_method_renderer import _TABLE
        self.owner._directory(operation_id)
        try:
            with closing(self.owner._connect()) as db:
                row=db.execute('SELECT binding,binding_sha256 FROM '+_TABLE+' WHERE operation_id=?',(operation_id,)).fetchone()
        except ServiceError as error:
            if error.status==404:return None
            raise
        if row is None:return None
        _require(len(row['binding'])<=32*1024*1024,'scene_space_operation_size_exceeded',409)
        binding=json.loads(row['binding'])
        _require(digest(binding)==row['binding_sha256'] and binding['operation_id']==operation_id,
            'scene_space_operation_changed',409)
        return binding

    def lookup(self, operation_id):
        from .animation_method_renderer import _json
        binding=self._load(operation_id)
        if binding is None:return None
        directory=self.owner._directory(operation_id);request=binding['request']
        if not (directory/'render-outcome.json').exists():
            return dict(status='unknown',technical_passed=False,stop_proven=False)
        outcome,outcome_raw=_json(directory/'render-outcome.json',65536)
        _require(outcome.get('schema')=='video.animation-method-render-outcome.v1'
            and outcome.get('automatic_retry') is False
            and outcome.get('request_sha256')==binding['request_sha256']
            and outcome.get('request_identity')==request['identity'] and outcome.get('image')==binding['image'],
            'scene_space_outcome_changed',409)
        cid=outcome.get('container_id');cleanup=outcome.get('cleanup',{})
        stopped=(cid is None and outcome.get('status')=='failed' and cleanup.get('status')=='not_created' and cleanup.get('container_id') is None
            or type(cid) is str and len(cid)==64 and all(c in '0123456789abcdef' for c in cid) and cleanup.get('container_id')==cid
            and cleanup.get('status')=='removed_and_confirmed_absent')
        result=dict(status=outcome.get('status'),technical_passed=False,stop_proven=stopped,
            schema='video.scene-space-render-verification.v1',operation_id=operation_id,media=None,
            render_request_sha256=binding['request_sha256'],
            input_sha256=binding['input_sha256'],source_sha256=binding['source_sha256'],
            scene_space_sha256=binding['scene_space']['sha256'],
            evidence_scope='static_only' if request['static'] else 'sampled_frames_only',
            motion_observed=False,audio_observed=False,whole_video_watched=False,
            error=None if outcome.get('status')=='rendered' else 'scene_space_render_failed',
            technical=dict(status='failed',evidence_sha256=digest(dict(binding=digest(binding),outcome=_hash(outcome_raw))),
                scope='bound_container_cleanup_not_visual_acceptance'),
            runtime=dict(engine='canvas',image=binding['image']))
        if not stopped or outcome.get('status')!='rendered':
            if stopped and (directory/'work/render-receipt.json').is_file():
                failed,_=_json(directory/'work/render-receipt.json',2*1024*1024)
                if failed.get('request_sha256')==binding['request_sha256'] and failed.get('host_sha256')==request['host_sha256']:
                    measured=failed.get('measured_render_seconds')
                    if type(measured) in (int,float) and math.isfinite(measured) and measured>=0:
                        result.update(measured_render_seconds=measured,
                            measurement_scope='container_host_elapsed_excludes_container_preparation')
            return result
        receipt,raw=_json(directory/'work/render-receipt.json',2*1024*1024)
        actual_image=outcome.get('actual_image_id')
        _require(type(actual_image) is str and actual_image.startswith('sha256:') and len(actual_image)==71
            and all(c in '0123456789abcdef' for c in actual_image[7:]),'scene_space_image_evidence_invalid',409)
        _require(receipt.get('schema')=='video.scene-space-render-receipt.v1'
            and receipt.get('status')=='rendered' and receipt.get('request_sha256')==binding['request_sha256']
            and receipt.get('request_identity')==request['identity']
            and all(receipt.get(key)==request[key] for key in ('host_sha256','scene_runtime_sha256','sidecar_sha256','image_digest')),
            'scene_space_receipt_changed',409)
        if 'surface_runtime_sha256' in request:
            _require(receipt.get('surface_runtime_sha256')==request['surface_runtime_sha256']
                and receipt.get('surface_budget_sha256')==request['surface_budget_sha256']
                and receipt.get('artwork_api_revision')==request['artwork_api_revision'],
                'scene_space_surface_receipt_changed',409)
        if 'geometry_runtime_sha256' in request:
            _require(receipt.get('geometry_runtime_sha256')==request['geometry_runtime_sha256']
                and receipt.get('geometry_budget_sha256')==request['geometry_budget_sha256']
                and request.get('artwork_api_revision')==3,
                'scene_space_geometry_receipt_changed',409)
        for name,key in [('host.mjs','host_sha256'),('input/scene-space-runtime.mjs','scene_runtime_sha256'),
                         ('input/scene-space.json','sidecar_sha256'),('engine_runtime.mjs','engine_runtime_sha256'),
                         ('hyperframes_project.mjs','project_runtime_sha256')]:
            _require(_hash(_read_file(directory/name,MAX_SIDECAR_BYTES))==request[key],'scene_space_runtime_changed',409)
        if 'surface_runtime_sha256' in request:
            _require(_hash(_read_file(directory/'input/scene_space_surface.mjs',256*1024))
                ==request['surface_runtime_sha256'],'scene_space_surface_runtime_changed',409)
            _require(_hash(_read_file(directory/'input/scene_space_surface_budget.mjs',64*1024))
                ==request['surface_budget_sha256'],'scene_space_surface_budget_changed',409)
        if 'geometry_runtime_sha256' in request:
            _require(_hash(_read_file(directory/'input/scene_space_geometry.mjs',256*1024))
                ==request['geometry_runtime_sha256'],'scene_space_geometry_runtime_changed',409)
            _require(_hash(_read_file(directory/'input/scene_space_geometry_budget.mjs',64*1024))
                ==request['geometry_budget_sha256'],'scene_space_geometry_budget_changed',409)
        _require(_read_file(directory/'binding.json',32*1024*1024)==canonical_bytes(binding)
            and _read_file(directory/'input/request.json',256*1024)==canonical_bytes(request),'scene_space_binding_changed',409)
        for name,sha in request['files_sha256'].items():
            _require(_hash(_read_file(directory/'input/scene'/name,512*1024))==sha,'scene_space_source_changed',409)
        for name,sha in request['assets_sha256'].items():
            _require(_hash(_read_file(directory/'input/scene/assets'/name,32*1024*1024))==sha,'scene_space_asset_changed',409)
        name='output.png' if request['static'] else 'output.mp4'
        media=_read_file(directory/'work'/name,request['max_media_bytes'])
        _require(len(media)<=request['max_media_bytes'] and _hash(media)==receipt['output_sha256']
            and len(media)==receipt['output_bytes'],'scene_space_output_changed',409)
        if request['static']:
            from PIL import Image
            with Image.open(io.BytesIO(media)) as image:
                _require(image.format=='PNG' and image.size==(request['constraints']['width'],request['constraints']['height']))
                image.verify()
        else:
            probe=receipt.get('media_probe',{})
            videos=[stream for stream in probe.get('streams',[]) if stream.get('codec_type')=='video']
            audios=[stream for stream in probe.get('streams',[]) if stream.get('codec_type')=='audio']
            try:
                _require(len(videos)==1 and not audios and videos[0]['codec_name']=='h264'
                    and videos[0]['width']==request['constraints']['width'] and videos[0]['height']==request['constraints']['height']
                    and videos[0]['avg_frame_rate']=='24/1' and int(videos[0]['nb_read_frames'])==
                    binding['scene_space']['frame_range']['end_frame']-binding['scene_space']['frame_range']['start_frame']
                    and abs(float(probe['format']['duration'])-request['constraints']['duration_seconds'])<=1/24+.001,
                    'scene_space_media_probe_invalid',409)
            except (KeyError,ValueError,TypeError):
                raise ServiceError('scene_space_media_probe_invalid',409) from None
        checks=receipt.get('checks',{})
        _require(checks.get('repeat_passed') is True and checks.get('reverse_passed') is True
            and checks.get('reload_passed') is True,'scene_space_seek_unproven',409)
        engine=receipt.get('engine_observation',{})
        contexts=engine.get('contexts')
        _require(engine.get('engine')=='canvas' and engine.get('revision')=='animation-engine-runtime-1'
            and type(contexts) is list and len(binding['scene_space']['object_caches'])+2<=len(contexts)<=MAX_CONTEXTS
            and all(type(row) is dict and row.get('kind')=='2d' for row in contexts)
            and any(row.get('connected') is True and row.get('visible') is True
                and row.get('width')==request['constraints']['width'] and row.get('height')==request['constraints']['height']
                for row in contexts),'scene_space_native_context_unproven',409)
        project=receipt.get('project_runtime',{})
        _require(project.get('core_sha256')==request['hyperframes_core_sha256']
            and project.get('project_sha256')==request['project_sha256']
            and type(project.get('core_seek_count')) is int and project['core_seek_count']>=1,
            'scene_space_hyperframes_unproven',409)
        observation=receipt.get('scene_space_observation',{})
        _require(observation.get('plan_sha256')==binding['scene_space']['frozen']['plan_sha256']
            and observation.get('recipes_executed')==0 and observation.get('objects_painted')==
            len(binding['scene_space']['object_caches']),'scene_space_consumption_unproven',409)
        if 'surface_runtime_sha256' in request:
            callbacks=observation.get('coverage_callbacks');covered=observation.get('coverage_objects')
            coverage_contexts=observation.get('coverage_contexts');coverage_pixels=observation.get('coverage_peak_pixels')
            helpers=observation.get('surface_helper_calls')
            usage=observation.get('surface_field_usage')
            count=len(binding['scene_space']['object_caches'])
            expected_compositor=('scene-space-compositor-3' if 'geometry_runtime_sha256' in request else 'scene-space-compositor-2')
            _require(observation.get('runtime_revision')==expected_compositor
                and observation.get('surface_runtime_sha256')==request['surface_runtime_sha256']
                and observation.get('surface_budget_sha256')==request['surface_budget_sha256']
                and observation.get('artwork_api_revision')==request['artwork_api_revision']
                and type(callbacks) is int and callbacks in (0,count)
                and type(covered) is int and 0<=covered<=callbacks
                and type(coverage_contexts) is int and coverage_contexts==(1 if callbacks else 0)
                and type(coverage_pixels) is int and 0<=coverage_pixels<=binding['scene_space']['coverage']['max_mask_pixels']
                and (callbacks>0 or coverage_pixels==0)
                and len(contexts)>=count+2+coverage_contexts
                and type(helpers) is dict and len(helpers)<=32
                and all(type(key) is str and 0<len(key)<=100 and type(value) is int and 0<value<=2**53-1
                    for key,value in helpers.items())
                and type(usage) is dict
                and all(type(usage.get(key)) is int for key in ('fields_created','fields_reserved','numeric_bytes_reserved'))
                and 0<=usage['fields_created']<=usage['fields_reserved']<=binding['scene_space']['surface_max_fields']
                and 0<=usage['numeric_bytes_reserved']<=binding['scene_space']['surface_retained_bytes']
                and usage.get('max_fields')==binding['scene_space']['surface_max_fields']
                and usage.get('max_total_field_bytes')==binding['scene_space']['surface_retained_bytes'],
                'scene_space_coverage_consumption_unproven',409)
        if 'geometry_runtime_sha256' in request:
            helpers=observation.get('geometry_helper_calls');usage=observation.get('geometry_resource_usage')
            _require(observation.get('geometry_runtime_sha256')==request['geometry_runtime_sha256']
                and observation.get('geometry_budget_sha256')==request['geometry_budget_sha256']
                and type(helpers) is dict and len(helpers)<=32
                and all(type(key) is str and 0<len(key)<=100 and type(value) is int and 0<value<=2**53-1
                    for key,value in helpers.items())
                and type(usage) is dict
                and all(type(usage.get(key)) is int and usage[key]>=0
                    for key in ('meshes_created','projections_created','handles_reserved','numeric_bytes_reserved'))
                and 0<=usage['meshes_created']+usage['projections_created']<=usage['handles_reserved']<=binding['scene_space']['geometry_max_handles']
                and 0<=usage['numeric_bytes_reserved']<=binding['scene_space']['geometry_retained_bytes']
                and usage.get('max_handles')==binding['scene_space']['geometry_max_handles']
                and usage.get('max_total_bytes')==binding['scene_space']['geometry_retained_bytes'],
                'scene_space_geometry_consumption_unproven',409)
        if request.get('drawing_libraries'):
            from .learning_drawing_libraries import validate_observation
            validate_observation(receipt.get('drawing_library_observation'),request['drawing_libraries'])
        else:
            _require('drawing_library_observation' not in receipt,'scene_space_drawing_evidence_changed',409)
        measured=receipt.get('measured_render_seconds')
        _require(type(measured) in (int,float) and 0<=measured<=86400 and math.isfinite(measured),
            'scene_space_render_measurement_invalid',409)
        retained=receipt.get('sample_frames')
        _require(type(retained) is list and len(retained)<=3,'scene_space_sample_manifest_invalid',409)
        sample_hashes={row['frame']:row['sha256'] for row in checks.get('samples',[])}
        for sample in retained:
            frame=sample.get('frame')
            _require(type(frame) is int and binding['scene_space']['frame_range']['start_frame']<=frame<
                binding['scene_space']['frame_range']['end_frame'] and sample.get('file_name')==f'sample-{frame:06d}.jpg'
                and sample.get('time_seconds')==(frame-binding['scene_space']['frame_range']['start_frame'])/24,
                'scene_space_sample_manifest_invalid',409)
            sample_raw=_read_file(directory/'work'/sample['file_name'],4*1024*1024)
            _require(sample_raw[:2]==b'\xff\xd8' and sample_raw[-2:]==b'\xff\xd9'
                and len(sample_raw)==sample.get('bytes') and _hash(sample_raw)==sample.get('sha256')==sample_hashes.get(frame),
                'scene_space_sample_changed',409)
        result.update(status='rendered',technical_passed=True,mime='image/png' if request['static'] else 'video/mp4',
            artifact_path=str(directory/'work'/name),receipt_path=str(directory/'work/render-receipt.json'),
            sha256=_hash(media),width=request['constraints']['width'],height=request['constraints']['height'],
            duration_seconds=request['constraints']['duration_seconds'],fps=24,
            evidence_sha256=_hash(raw),runtime=dict(scene_space=observation),
            scene_space=dict(plan_sha256=observation['plan_sha256'],runtime_sha256=request['scene_runtime_sha256']))
        result['picture_path' if request['static'] else 'media_path']=result['artifact_path']
        proof=digest(dict(binding=digest(binding),outcome=_hash(outcome_raw),receipt=_hash(raw)))
        result.update(source_path=str(directory/'source.json'),frame_count=receipt['captured_frames'],
            media=dict(sha256=_hash(media),duration_seconds=request['constraints']['duration_seconds'],audio_sha256=None),
            technical=dict(status='passed',evidence_sha256=proof,scope='isolated_scene_space_media_and_absolute_seek'),
            measured_render_seconds=receipt['measured_render_seconds'],
            measurement_scope='container_host_elapsed_excludes_container_preparation')
        result['runtime'].update(engine='canvas',image=binding['image'],actual_image_id=outcome.get('actual_image_id'),
            admission=dict(status='admitted',id='scene-space-canvas-v1@'+binding['image_digest'],evidence_sha256=proof),
            observation='rendered_frames_not_agent_motion_or_audio_observation')
        if request.get('drawing_libraries'):
            result['runtime'].update(drawing_libraries=deepcopy(request['drawing_libraries']),
                drawing_library_observation=deepcopy(receipt['drawing_library_observation']))
        from .animation_method_renderer import _TABLE
        with closing(self.owner._connect(write=True)) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT proof_sha256,result FROM '+_TABLE+' WHERE operation_id=?',(operation_id,)).fetchone()
            if row['proof_sha256'] is None:
                db.execute('UPDATE '+_TABLE+' SET proof_sha256=?,result=? WHERE operation_id=?',
                    (proof,canonical_bytes(result),operation_id))
            else:_require(row['proof_sha256']==proof and bytes(row['result'])==canonical_bytes(result),'scene_space_proof_changed',409)
            db.commit()
        return result

    recover=lookup

    def media_path(self,operation_id):
        result=self.lookup(operation_id)
        _require(result is not None and result.get('technical_passed'),'scene_space_media_unavailable',404)
        return Path(result['artifact_path'])

    def frames(self,operation_id,max_frames=12):
        from .animation_method_renderer import _json
        result=self.lookup(operation_id)
        _require(result is not None and result.get('technical_passed'),'scene_space_media_unavailable',404)
        directory=self.owner._directory(operation_id)
        receipt,_=_json(directory/'work/render-receipt.json',2*1024*1024)
        _require(type(max_frames) is int and 1<=max_frames<=24)
        rows=receipt.get('sample_frames',[])[:max_frames]
        frames=[]
        for row in rows:
            _require(type(row['file_name']) is str and row['file_name'].startswith('sample-')
                and row['file_name'].endswith('.jpg') and '/' not in row['file_name'] and '\\' not in row['file_name'])
            raw=_read_file(directory/'work'/row['file_name'],4*1024*1024)
            _require(_hash(raw)==row['sha256'],'scene_space_sample_changed',409)
            frames.append(dict(timestamp_seconds=row['time_seconds'],mime='image/jpeg',raw=raw))
        return frames

    def stopping_proof(self, operation_id):
        result=self.lookup(operation_id)
        if result is None:return None
        return dict(operation_id=operation_id,stop_proven=result['stop_proven'],
            evidence_sha256=result.get('evidence_sha256',result.get('technical',{}).get('evidence_sha256')),
            schema='video.scene-space-render-stop.v1')
