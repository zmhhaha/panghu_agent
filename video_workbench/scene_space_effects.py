"""Compatible effect-author carrier and immutable spatial-method retention."""
from copy import deepcopy

from agent_service.contracts import ServiceError, digest
from .scene_space_learning import METHOD_SCHEMA, scene_method_guidance, validate_scene_method
from .scene_space_plan import default_budget


def effect_scene_offer(run):
    offer=scene_method_guidance('scene_data_only')
    offer['result_field']='methods[].runtime_requirements.scene_space'
    offer['instructions']=[line.replace('method.scene_space',offer['result_field']) for line in offer['instructions']]
    offer['render_capability']=deepcopy(run['frozen']['constraints']['scene_space_capability'])
    budget=run.get('budget',run['frozen'].get('budget'))
    offer['plan_budget']=default_budget(model_calls=budget['creator']+budget['review'],
        render_seconds=budget['seconds'],revisions=budget['render'])
    offer['instructions']+=[
        'The spatial carrier belongs in methods[].runtime_requirements.scene_space, not in source.strategy or an extra source file.',
        'All methods describing the same painted candidate must use the same spatial carrier. The host retains it separately from portable method bodies.',
        'For an executable Canvas plan register window.__sceneSpaceArtwork.paintObject(ctx,object,world) and canvas[data-scene-space-output]. The host owns layer composition, absolute camera seeking and cache resolution.',
        'The plan viewport and duration must equal the current constraints. A world-only hypothesis can be retained without selecting the new compositor.',
        'Keep reusable method guidance and skill_package independent of reference layouts, scene instances and source assets. Extract general principles instead.',
    ]
    return offer


def effect_scene_metadata(result,run,document=None):
    """Interpret new data only under a newly frozen capability marker."""
    if not run['frozen'].get('constraints',{}).get('scene_space_capability'):return None
    carriers=[row.get('runtime_requirements',{}).get('scene_space') for row in result.get('methods',[])
        if row.get('runtime_requirements',{}).get('scene_space') is not None]
    if not carriers:return None
    checked=[]
    frames=(document or {}).get('observation',{}).get('frames',[])
    references=[dict(id=row['frame_id'],sha256=row['sha256']) for row in frames if row.get('frame_id')]
    for value in carriers:
        source=value.get('source') if type(value) is dict else None
        scope=source.get('scope') if type(source) is dict else None
        checked.append(validate_scene_method(value,source_scope='scene_data_only' if scope=='scene_data_only' else 'sampled_frames_only',
            source_refs=[] if scope=='scene_data_only' else references,
            frame_ids=[] if scope=='scene_data_only' else [row['id'] for row in references]))
    if len({digest(value) for value in checked})!=1:
        raise ServiceError('animation_method_scene_plan_ambiguous',422)
    metadata=checked[0];plan=metadata['plan']
    if plan is not None and (plan['viewport']!={'width':1280,'height':720}
        or plan['duration_frames']!=round(run['frozen'].get('duration_seconds',18)*24)
        or result['source']['engine']!='canvas'):
        raise ServiceError('animation_method_scene_plan_clock_changed',422)
    if plan is not None:
        limits=effect_scene_offer(run)['plan_budget']
        for section,keys in [('model',('limit_calls','limit_tokens')),('render',
                ('limit_seconds','max_cached_pixels','max_temporary_bytes')),('revisions',('limit',))]:
            if any(plan['budget'][section][key]>limits[section][key] for key in keys):
                raise ServiceError('animation_method_scene_host_budget_exceeded',422)
    return metadata


def portable_method(proposal,*,enabled):
    """Extract the admitted carrier; preserve all authored portable content."""
    body=deepcopy(proposal)
    if enabled and type(body.get('runtime_requirements',{}).get('scene_space')) is dict:
        if body['runtime_requirements']['scene_space'].get('schema')!=METHOD_SCHEMA:
            raise ServiceError('animation_method_scene_carrier_invalid',422)
        body['runtime_requirements'].pop('scene_space')
    return body
