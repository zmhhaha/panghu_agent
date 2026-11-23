"""Versioned spatial hypotheses carried beside strictly preserved source files.

Authors describe a method and its uncertainty. Host layout/render receipts are
separate evidence; this data cannot certify artistic quality, motion or audio.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.scene_space_contract import WORLD_SCHEMA, validate_world, world_sha256
from .scene_space_plan import PLAN_SCHEMA, default_budget, default_provenance, validate_plan

METHOD_SCHEMA = 'video.scene-space-method.v1'
MAX_METHOD_BYTES = 1024 * 1024
MAX_ROWS = 32
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_SCOPES = {'scene_data_only', 'static_image', 'sampled_frames_only'}
_STAGES = {'observe', 'spatial_hypothesis', 'reproduce', 'compare', 'temporal', 'transfer'}
_STATUSES = {'proposed', 'inferred', 'pending', 'failed', 'excluded'}


def _require(ok, code='scene_space_method_invalid', status=422):
    if not ok:
        raise ServiceError(code, status)


def _fields(value, keys):
    _require(type(value) is dict and set(value) == set(keys))


def _id(value):
    _require(type(value) is str and _ID.fullmatch(value) is not None)
    return value


def _text(value):
    _require(type(value) is str and bool(value.strip()) and len(value) <= 4000
        and not any((ord(c) < 32 and c not in '\n\r\t') or ord(c) == 127
                    or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def _texts(value):
    _require(type(value) is list and len(value) <= MAX_ROWS)
    for item in value:
        _text(item)


def _ids(value):
    _require(type(value) is list and len(value) <= 128)
    for item in value:
        _id(item)
    _require(len(set(value)) == len(value))


def _references(value):
    _require(type(value) is list and len(value) <= 128)
    seen = set()
    for row in value:
        _fields(row, ('id', 'sha256'))
        _id(row['id'])
        _require(type(row['sha256']) is str and _SHA.fullmatch(row['sha256']) is not None)
        _require(row['id'] not in seen)
        seen.add(row['id'])
    return {(row['id'], row['sha256']) for row in value}


def _scope(value):
    # The picture service's old observation name remains unchanged outside
    # this new carrier. Its explicit spatial-source label is static_image.
    value = 'static_image' if value == 'static_only' else value
    _require(type(value) is str and value in _SCOPES)
    return value


def validate_scene_method(value, *, source_scope=None, source_refs=None, frame_ids=None):
    """Normalize inert hypotheses and optionally bind actual supplied sources.

    With no source context this performs structural validation only. It does
    not upgrade saved metadata into observed evidence. Callers accepting an
    author result must supply the frozen scope, references and frame IDs.
    """
    _fields(value, ('schema', 'world', 'plan', 'source', 'hypotheses',
                    'principles', 'stages', 'limitations'))
    try:
        _require(len(canonical_bytes(value)) <= MAX_METHOD_BYTES,
            'scene_space_method_too_large', 413)
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise ServiceError('scene_space_method_invalid', 422) from None
    _require(value['schema'] == METHOD_SCHEMA)
    result = deepcopy(value)
    source = result['source']
    _fields(source, ('scope', 'references', 'frame_ids', 'motion_observed',
                     'audio_observed', 'whole_video_watched'))
    source['scope'] = _scope(source['scope'])
    references = _references(source['references'])
    _ids(source['frame_ids'])
    _require({row['id'] for row in source['references']} == set(source['frame_ids']),
        'scene_space_method_frame_source_unbound', 409)
    _require(all(source[key] is False for key in
        ('motion_observed', 'audio_observed', 'whole_video_watched')),
        'scene_space_method_observation_not_admitted')
    if source_scope is not None:
        _require(source['scope'] == _scope(source_scope), 'scene_space_method_source_scope_changed', 409)
    if source_refs is not None:
        _require(references <= _references(source_refs), 'scene_space_method_source_changed', 409)
    if frame_ids is not None:
        _ids(frame_ids)
        _require(set(source['frame_ids']) <= set(frame_ids), 'scene_space_method_frame_not_supplied', 409)
    if source['scope'] == 'scene_data_only':
        _require(not references and not source['frame_ids'], 'scene_space_method_source_scope_changed', 409)
    if result['world'] is not None:
        result['world'] = validate_world(result['world'])
    if result['plan'] is not None:
        result['plan'] = validate_plan(result['plan'])
        if result['world'] is not None:
            matching = [world for world in result['plan']['worlds'] if world['id'] == result['world']['id']]
            _require(len(matching) == 1 and world_sha256(matching[0]) == world_sha256(result['world']),
                'scene_space_method_world_plan_changed', 409)
    _require(type(result['hypotheses']) is list and len(result['hypotheses']) <= MAX_ROWS)
    seen = set()
    for row in result['hypotheses']:
        _fields(row, ('id', 'claim', 'confidence', 'evidence_frame_ids', 'alternatives', 'unknowns'))
        _id(row['id']); _require(row['id'] not in seen); seen.add(row['id'])
        _text(row['claim'])
        confidence = row['confidence']
        _require(type(confidence) in (int, float) and 0 <= confidence <= 1 and math.isfinite(confidence))
        _ids(row['evidence_frame_ids'])
        _require(set(row['evidence_frame_ids']) <= set(source['frame_ids']),
            'scene_space_method_evidence_frame_changed', 409)
        _texts(row['alternatives']); _texts(row['unknowns'])
    _texts(result['principles']); _texts(result['limitations'])
    _require(type(result['stages']) is list and len(result['stages']) <= len(_STAGES))
    seen = set()
    for row in result['stages']:
        _fields(row, ('stage', 'status', 'notes', 'evidence_frame_ids', 'limitations'))
        _require(type(row['stage']) is str and row['stage'] in _STAGES and row['stage'] not in seen)
        seen.add(row['stage'])
        _require(type(row['status']) is str and row['status'] in _STATUSES,
            'scene_space_method_unearned_verification')
        _text(row['notes']); _ids(row['evidence_frame_ids']); _texts(row['limitations'])
        _require(set(row['evidence_frame_ids']) <= set(source['frame_ids']),
            'scene_space_method_evidence_frame_changed', 409)
    return result


def transferable_scene_method(method):
    """Keep old method semantics; remove this carrier's reference-specific data.

    Legacy methods without a versioned spatial carrier are returned unchanged.
    This is used only for explicitly admitted v1 spatial methods, never to
    reinterpret an old arbitrary method.scene_space field automatically.
    """
    if (type(method) is not dict or type(method.get('scene_space')) is not dict
            or method['scene_space'].get('schema') != METHOD_SCHEMA):
        return deepcopy(method)
    checked = validate_scene_method(method['scene_space'])
    result = deepcopy(method)
    result['scene_space'] = dict(schema=METHOD_SCHEMA, world=None, plan=None,
        source=dict(scope='scene_data_only', references=[], frame_ids=[], motion_observed=False,
            audio_observed=False, whole_video_watched=False), hypotheses=[],
        principles=deepcopy(checked['principles']), limitations=deepcopy(checked['limitations']),
        stages=[dict(stage='transfer', status='pending',
            notes='Apply the extracted principles to independently planned new content.',
            evidence_frame_ids=[], limitations=['Reproduction does not establish successful transfer.'])])
    return result


def _scene_structure_example():
    """An inert syntax example, deliberately without artwork or paint recipes."""
    world = dict(schema=WORLD_SCHEMA, id='example-world', revision=1, seed=1,
        style='unspecified', space=dict(projection='planar',
            bounds=dict(x=0, y=0, width=64, height=64)),
        layers=[dict(id='example-layer', order=0, parallax=1, cache_policy='static')],
        objects=[dict(id='example-object', kind='placeholder', layer_id='example-layer',
            parent_id=None, bounds=dict(x=0, y=0, width=16, height=16),
            transform=dict(x=24, y=24, scale_x=1, scale_y=1, rotation=0),
            depth=dict(kind='unknown', value=None), anchors=dict(center=[8, 8]))],
        layout=dict(grid=dict(origin=[0, 0], spacing=[32, 32], columns=3, rows=3),
            points=[dict(id='example-center', position=[32, 32])], regions=[]),
        relations=[])
    plan = dict(schema=PLAN_SCHEMA, id='example-plan', revision=1, fps=24,
        duration_frames=1, viewport=dict(width=64, height=64), worlds=[deepcopy(world)],
        shots=[dict(id='example-shot', world_id=world['id'], world_revision=1,
            start_frame=0, end_frame=1, camera=dict(max_zoom=1,
                keyframes=[dict(frame=0, x=32, y=32, zoom=1, easing='linear')]),
            local_animation=[], transition='cut')],
        budget=default_budget(), provenance=default_provenance())
    return dict(purpose='Syntax only: a 64x64 generic placeholder, not a composition, painting recipe or task output. '
        'Replace its viewport, geometry, objects, layers, IDs and budget for the actual task; never copy it as the artwork.',
        world=world, plan=plan)


def scene_method_guidance(scope):
    """A data-only author offer, kept inside existing constraints semantics."""
    scope = _scope(scope)
    return dict(schema='video.scene-space-learning-offer.v1', method_schema=METHOD_SCHEMA,
        result_field='method.scene_space', source_scope=scope,
        world_schema=WORLD_SCHEMA, plan_schema=PLAN_SCHEMA,
        required_fields=['schema', 'world', 'plan', 'source', 'hypotheses', 'principles', 'stages', 'limitations'],
        source_fields=['scope', 'references', 'frame_ids', 'motion_observed', 'audio_observed', 'whole_video_watched'],
        hypothesis_fields=['id', 'claim', 'confidence', 'evidence_frame_ids', 'alternatives', 'unknowns'],
        stage_fields=['stage', 'status', 'notes', 'evidence_frame_ids', 'limitations'],
        stages=sorted(_STAGES), statuses=sorted(_STATUSES),
        method_types=dict(world='null or a complete world object',plan='null or a complete plan object',
            source='object with exactly the listed source_fields; references is an array of {id,sha256}, '
                'frame_ids an array of those same IDs; copy only supplied IDs/digests, at most 128; all observation flags false',
            hypotheses='array of at most 32 objects: id, claim:string, confidence:finite number 0..1, '
                'evidence_frame_ids:string array, alternatives:string array, unknowns:string array',
            principles='array of at most 32 nonempty strings, each at most 4000 characters',
            stages='array with unique admitted stage names; status from statuses, notes is a string, '
                'evidence_frame_ids references supplied frames, limitations is a string array; authors cannot mark verified/completed',
            limitations='array of at most 32 nonempty strings, each at most 4000 characters',
            size='the whole scene-space method is at most 1048576 canonical JSON bytes'),
        world_contract=dict(required=['schema','id','revision','seed','style','space','layers',
            'objects','layout','relations'],
            field_types=dict(schema='literal video.scene-world.v1',
                id='ASCII ID matching [A-Za-z0-9][A-Za-z0-9_.:-]{0,79}; not constructor, prototype or __proto__',
                revision='integer 1..1000000',seed='integer 0..4294967295',
                style='nonempty single-line string, at most 120 characters; never an object, palette or style dictionary',
                space='object',layers='array of 1..32 layer objects',objects='array of 1..256 object objects',
                layout='object with exactly grid, points and regions',relations='array, at most 1024 relations'),
            scalar_rules='Numbers must be finite JSON numbers, not booleans or numeric strings. '
                'IDs are strings, points are [x,y] arrays, and rectangles are {x,y,width,height} objects. '
                'Only the listed required/optional fields are allowed; no arbitrary appearance or metadata fields.',
            space=dict(projection='planar | scattered | geometric',bounds='rect{x,y,width,height}'),
            rect='x/y and right/bottom within [-1000000,1000000], width/height positive (at least 0.000001)',
            layer=dict(required=['id','order','parallax'],optional=['opacity','blur','cache_policy','mask_region_id'],
                types=dict(order='unique integer -4096..4096; later layers have greater order',
                    parallax='number 0..2',opacity='number 0..1, default 1',blur='number 0..128, default 0',
                    cache_policy='static for the currently admitted host',mask_region_id='null or an existing region ID')),
            object=dict(required=['id','kind','layer_id','parent_id','bounds','transform','depth','anchors'],
                identity='id and kind are ASCII IDs; layer_id references a declared layer; parent_id is null or another object ID',
                transform=['x','y','scale_x','scale_y','rotation'],
                transform_types='x/y are parent-local translations; scale_x/scale_y are numbers 0.0001..1000; rotation is degrees -360..360',
                depth=dict(kind='ordinal | geometric | unknown',value='number; null for unknown'),
                anchors='map of at most 32 ASCII anchor IDs to local [x,y] arrays inside object.bounds',
                optional=['order','allow_overflow','overflow_reason','recipe_ref'],
                optional_types=dict(order='integer -4096..4096, default 0',allow_overflow='boolean, default false',
                    overflow_reason='null when allow_overflow=false; nonempty string when true',
                    recipe_ref='omit, null, or exactly {id:ASCII_ID,sha256:64_lowercase_hex_characters}; an inert identity reference only, never seed/params/drawing instructions')),
            layout=dict(grid='null or {origin:[x,y],spacing:[dx,dy],columns,rows}',
                grid_limits='spacing positive; columns/rows integers 1..128, product <=4096; first/last grid points inside space.bounds',
                points='array of {id,position:[x,y],depth?:{kind,value}}, at most 4096; positions inside space.bounds',
                regions='array of {id,kind:"rect",bounds:{x,y,width,height}} or {id,kind:"polygon",points:[[x,y],...]}; '
                    'at most 128 regions, polygon 3..256 distinct vertices, nonzero area, no self intersection; all inside space.bounds',
                total_points='at most 16384 grid points, named points, region vertices and anchors combined'),
            relations=dict(
                attachment=['id','kind','object_id','anchor','target_object_id','target_anchor'],
                support=['id','kind','object_id','anchor','region_id'],
                scale=['id','kind','object_id','reference_object_id','metric','min_ratio','max_ratio'],
                occlusion=['id','kind','front_id','back_id'],
                rules='IDs/anchors/regions must exist. Attachment/support may add tolerance from 0 to 5% of the smaller world side; '
                    'scale metric is width|height|area, 0.000001<=min_ratio<=max_ratio<=1000000. '
                    'World-space attachments, support and scale must already hold. Parent/attachment/occlusion graphs cannot cycle. '
                    'Occluding front objects need a strictly greater (layer.order,object.order) tuple, '
                    'and no smaller depth value when both objects have the same non-unknown depth kind. '
                    'Depth describes distance but does not sort drawing order; give overlapping same-layer objects explicit different order values.')),
        plan_contract=dict(required=['schema','id','revision','fps','duration_frames','viewport','worlds',
            'shots','budget','provenance'],fps=24,
            worlds='Array of 1..16 complete video.scene-world.v1 objects BY VALUE, not {world_id,world_revision} references. '
                'If method.scene_space.world is also present, the matching plan world must contain the same complete world data.',
            dimensions='viewport is exactly {width:integer 1..1280,height:integer 1..720}; '
                'it must match the actual task dimensions. duration_frames is an integer 1..7200.',
            identity='id is an ASCII ID (at most 100 characters); revision is an integer 1..1000000',
            camera='fixed principal direction; x/y are world-space view centre; zoom in [.25,8]',
            shots=dict(required=['id','world_id','world_revision','start_frame','end_frame','camera','local_animation'],
                count='array of 1..16 shots; every world must be used by a shot',
                interval='ordered, gap-free half-open integer start_frame/end_frame partitions from 0 to duration_frames',
                camera='{max_zoom,keyframes:[{frame,x,y,zoom,easing}]}',
                camera_keys='1..16 keys in strictly increasing absolute integer frames; first at shot.start_frame, '
                    'and if multiple keys, last at shot.end_frame; easing linear|smoothstep; each zoom <= max_zoom',
                local_animation='[{id,target_id,property:x|y|rotation|scale|opacity,keyframes:[{frame,value,easing}]}]',
                local_keys='at most 64 tracks, unique target/property pairs; target_id is an existing object; '
                    '1..32 keys with the same absolute-frame boundary rules; scale values .01..16, opacity 0..1',
                transition='optional cut | continuous'),
            provenance=dict(origin='generated',source_refs=[],evidence_scope='scene_data_only',
                motion_observed=False,audio_observed=False,whole_video_watched=False),
            budget_rules='Use exactly model/render/revisions and the demonstrated nested keys. Use the host-supplied task budget when offered; '
                'example limits do not authorize larger work. All limits/used counts must be nonnegative and used values cannot exceed limits; '
                'keep unknown measured tokens null rather than inventing actual usage.',
            budget=dict(model=dict(limit_calls=16,used_calls=0,limit_tokens=1000000,used_tokens=0),
                render=dict(limit_seconds=3600,used_seconds=0,max_cached_pixels=16777216,max_temporary_bytes=134217728),
                revisions=dict(limit=4,used=0))),
        structural_example=_scene_structure_example(),
        artwork_extensions=dict(api_revision=3,
            paint_object='paintObject(ctx, object, world, tools); the optional fourth argument is supplied by the trusted host',
            paint_coverage='Optional paintCoverage(maskCtx, object, world, tools). Draw object-local coverage in mask alpha, then return exactly {substrate:[r,g,b]} with integer RGB 0..255. Return null/undefined/false to keep ordinary pigment-alpha composition for this object.',
            coverage_semantics='For a solid object, the host places opaque substrate behind its pigment and applies the independent silhouette coverage once. Pale ink and paper highlights can occlude distant scenery. Pixels outside the silhouette remain untouched; local mist may soften selected edges.',
            coverage_lifetime='Both callbacks run once per object in each page instance. Static colour caches are reused across frames. One temporary coverage surface is reused; its peak size and native context are admitted before painting.',
            surface_tools=dict(namespace='tools.surface',
                create='createSurfaceField({bounds,outline,ridges,valleys,grid,baseHeight?,boundaryFade?})',
                bounds='object-local {x,y,width,height}; outline is a bounded simple polygon of [x,y] points',
                features='ridges/valleys are arrays of {id,points:[[x,y,z],...],width?}; author-chosen z is local relief, positive valley z means depression; features stay inside the outline',
                grid='bounded integer columns/rows, at most 128 each and 16384 nodes; this is an internal surface sampling grid, separate from world.layout composition anchors',
                sample='sampleSurface(field,x,y,{light?}) returns coverage, height, normal:[nx,ny,nz], flow:[dx,dy], shade; larger shade is more back-facing to the chosen light',
                wash='paintSurfaceWash(ctx,field,{paper:[r,g,b],ink:[r,g,b],light:[x,y,z],inkRange:[min,max]}); retains the caller transform and clip; no new Canvas',
                scope='Generated local 2.5D form hypothesis for painting, never observed calibrated 3D or hidden geometry; light and tone ranges are style choices, not mandatory photorealistic shading'),
            geometry_tools=dict(namespace='tools.geometry',requires_artwork_api_revision=3,
                create_mesh='createMesh({vertices:[[x,y,z],...],triangles:[[a,b,c],...]}); finite XYZ coordinates and nondegenerate indexed triangles with consistent winding',
                create_terrain='createTerrainMesh({bounds:{x,z,width,depth},grid:{columns,rows},heights:[row-major y,...]}); one connected XZ height surface, not an observed volume reconstruction',
                project='projectMesh(mesh,{width,height,camera:{position:[x,y,z],target:[x,y,z],up?:[x,y,z],viewHeight,near?,far?}}); fixed orthographic depth buffer; every vertex must lie strictly between near and far',
                sample='sampleProjection(projection,x,y,{bounds?}) returns covered/coverage, depth (smaller camera distance is nearer, unlike world ordinal ordering), position XYZ, mesh-space normal, projected downslope flow:[dx,dy], flow_valid and triangle; uncovered values are null except coverage=0, flow=[0,0], flow_valid=false',
                project_point='projectPoint(projection,[x,y,z],{bounds?,depthTolerance?}) returns x,y,depth,in_view,visible,triangle; visibility is a geometric test against the same depth buffer, not source-image observation',
                coverage='paintCoverage(ctx,projection,{bounds?}) paints alpha scanlines while retaining caller transform, clip and alpha; bounds is an object-local {x,y,width,height} mapping, not an arbitrary camera matrix',
                limits=dict(vertices=16384,triangles=32768,projection_side=1024,projection_pixels=262144,
                    raster_bbox_pixel_work=33554432,page_handles=32,page_numeric_bytes=16777216,work_numeric_bytes=1048576),
                scope='Authored shared XYZ mesh plus fixed-view projection for brush construction, not observed/calibrated reconstruction, automatic ink shading, a new pixel backend or orbit-camera admission'),
            data_location='Keep outlines, ridges, valleys, shared XYZ mesh/projection data and per-object drawing parameters in scene.js keyed by world/object ID. Do not add them to strict world/plan fields, source.strategy or recipe_ref.'),
        instructions=[
            'The currently admitted Canvas compositor accepts only static layer cache_policy. Paint each object once; express supported local changes with absolute-frame transform/opacity tracks. Other cache policies remain descriptive metadata until separately admitted.',
            'When useful, record spatial, layer, scale, anchor and occlusion hypotheses in method.scene_space; source files retain their exact contract.',
            'Use explicit declared depth; image Y does not establish distance. Grid and named points assist object layout, not every brush stroke.',
            'Copy source references and frame IDs only from the host offer. Confidence describes a hypothesis, not verified geometry or artistic acceptance.',
            'One image or sampled frames do not establish actual camera movement, three-dimensional reconstruction, continuous motion, audio or full-video observation. Keep all three observation flags false.',
            'world and plan may be null for a partial learning stage. A plan must use the common world/plan schema and match the actual output viewport and frame count.',
            'Use the exact field types. world.style is one short string. Omit recipe_ref unless you have a real id+sha256 identity; keep your own per-object drawing parameters in scene.js keyed by object ID. plan.worlds embeds complete world objects, while shots reference their world_id and world_revision.',
            'The structural_example is a validated syntax illustration with one generic placeholder and no painting recipe. Build the actual composition from the task, using the offered dimensions/frame count and budget, rather than rendering or inheriting that example.',
            'A world-only hypothesis uses ordinary source rendering and separate layout diagnostics. A non-null plan selects the admitted scene-space compositor and requires its artwork API.',
            'For the admitted Canvas compositor, scene.html must provide canvas[data-scene-space-output] styled to fill the viewport, and scene.js must register window.__sceneSpaceArtwork={paintObject(ctx,object,world,tools){...},paintCoverage(maskCtx,object,world,tools){...}}; paintCoverage is optional. Paint only each object in its local bounds; the host applies parent/layer/camera transforms, opacity and masking. Use the declared seed for reproducible artwork. Do not define a nonconfigurable window.__animationExperiment; the host owns the frame clock.',
            'Separate solid silhouette coverage from pigment opacity. A pale mountain should still cover the distant ridge through its solid body. Use the optional coverage callback with the intended substrate colour; do not fill an entire object bounding rectangle, punch holes in the final canvas or add a compulsory white halo. Fog and deliberately translucent objects may retain ordinary alpha.',
            'An outer object mask does not recover occlusion between several peaks painted inside that object. Give independently occluding parts separate world objects, or composite their opaque substrate and local coverage in the correct order inside the painter before returning the group colour.',
            'Layer and parent opacity remain intentional transparency and are applied by the host. Express pale solid surfaces with pigment tone and wash strength rather than fading the entire solid layer and unintentionally revealing scenery behind it.',
            'For internal volume, plan a varied contour, branching ridges/valleys and distinct facing planes before dense brush detail. tools.surface can derive bounded local relief, normals, light-facing values and brush-flow directions. Make brush paths follow those forms; a composition grid or repeated vertical lines alone do not supply rock volume. Avoid copying one pointed cone for every mountain.',
            'Only use tools.geometry when the task frozen capability offers artwork API3 and its geometry helper identities. A world depth label or space.projection=geometric alone does not supply a 3D mesh or change the host camera. Do not silently rebind an older frozen task to a new helper.',
            'For a shared mountain body, author one connected base, main ridge, branches, saddles, shoulders and front feet before detailing peaks. tools.geometry can project the same XYZ triangle mesh and expose visible surfaces and directions; independent peak depth buffers cannot resolve occlusion between those peaks. mesh.connected_components diagnoses connectivity of referenced vertices, not a closed manifold or artistic quality.',
            'Construct and project a shared mesh once per page/world group, retain its handles for painting and coverage, and reuse the static artwork cache under the host clock. The fixed-view helper camera does not authorize per-frame orbit, a new world/plan camera schema, hidden-side reconstruction or another pixel backend.',
            'Use projected coverage, visible surface position, face normal and downslope direction to organize local brush paths and ink groups. Normals follow mesh triangle winding; a horizontal face or degenerate projected downslope has flow_valid=false and flow=[0,0], so choose an authored alternative instead of normalizing a zero direction. Geometry values do not mandate a smooth gray underpainting or establish ink quality.',
            'If a visible sun, lamp or specified light direction is part of the picture, relate it to the camera, local transforms and the depicted lit/shadowed planes throughout the selected view or seek range. Do not silently illuminate the front from an unrelated helper light. Intentional nonphysical lighting is a style choice to explain, not an accidental mismatch.',
            'For a brush-painted ink goal, organize the large silhouette and connected ink masses before texture. Use a hierarchy of broad deposits, medium form strokes and sparse fine accents, with varied pressure, contact, ink load and dry/wet edges. A smooth normal-to-grey underpainting with uniformly scattered thin lines does not establish brushwork. Surface fields may guide direction and grouping without painting a continuous shaded wash.',
            'Judge actual artwork at both thumbnail and close-up scales: the thumbnail should retain coherent mass, dominant/subordinate forms and gaps; the close-up should show purposeful stroke bodies and local dry/wet variation. Merely widening the same contact template can create repeated ribbons or fringes. A drawing-library call, valid depth plan or technically rendered image is not evidence that the intended painting style succeeded.',
            'ink.renderCanvas resets its internal Canvas transform. For direct object painting use bounds.x=bounds.y=0, match the InkScene document width/height to object.bounds, and use clear:false; express world placement in object.transform. For nonzero local bounds, render into an admitted local Canvas and drawImage it back into bounds, within the context and pixel budgets.',
            'Use the pinned ink API types: stroke smoothing is linear or catmull-rom, never a numeric smoothing amount. Check actual library usage and output; a valid spatial description alone does not prove the painter executed.',
            'For a still use exactly one task frame. For video use exactly duration_seconds*24 frames. A static camera may have one key at shot start; interpolated tracks begin at start_frame and end at the exclusive end_frame. Use absolute task frames and linear or smoothstep easing.',
            'Camera movement, local animation, fixed-camera animation and complete stillness are distinct valid choices. Preserve the reference objective and label ambiguous motion as a hypothesis.',
            'Do not claim completed or verified stages. Host layout/render/comparison receipts are separate; report pending, failed, excluded and unknown parts honestly.',
            'Changed-content transfer receives general principles only. Create a new world and layout; do not carry reference assets, geometry or a reproduction baseline.',
        ])
