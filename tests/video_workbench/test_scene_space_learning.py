"""Spatial methods are bound to supplied frames and do not inherit old worlds."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError
from agent_service.scene_space_contract import validate_world, world_sha256
from video_workbench.scene_space_learning import (
    METHOD_SCHEMA, scene_method_guidance, transferable_scene_method, validate_scene_method,
)
from video_workbench.scene_space_plan import automatic_plan, default_budget, validate_plan


WORLD_PATH = Path(__file__).resolve().parents[1] / 'fixtures/scene_space/world.json'


def carrier(*, scope='static_image', refs=None, frames=None, world=True, plan=False,
            viewport=None, duration_frames=1):
    value = json.loads(WORLD_PATH.read_text(encoding='utf-8')) if world else None
    references = refs or [dict(id='reference-1', sha256='a'*64)] if scope != 'scene_data_only' else []
    frame_ids = frames or ['reference-1'] if scope != 'scene_data_only' else []
    return dict(schema=METHOD_SCHEMA, world=value,
        plan=automatic_plan([value], id='learned-plan', viewport=viewport or dict(width=320,height=180),
            duration_frames=duration_frames, budget=default_budget(),
            intents=[dict(world_id=value['id'], weight=1, view='hold', focus=dict(x=700,y=400))]) if plan else None,
        source=dict(scope=scope,references=references,frame_ids=frame_ids,
            motion_observed=False,audio_observed=False,whole_video_watched=False),
        hypotheses=[dict(id='overlap',claim='Declared foreground ordering may explain the visible overlap.',
            confidence=.7,evidence_frame_ids=frame_ids,alternatives=['Flat illustration with overlapping shapes.'],
            unknowns=['Exact distance and hidden geometry are not observed.'])],
        principles=['Use stable object anchors and validate explicit overlap order before rendering.'],
        stages=[dict(stage='spatial_hypothesis',status='inferred',notes='A bounded layout hypothesis.',
            evidence_frame_ids=frame_ids,limitations=['Geometric validity is separate from visual quality.'])],
        limitations=['No continuous motion or audio has been observed.'])


def test_method_is_normalized_without_mutation_and_bound_to_actual_sources():
    value=carrier(plan=True)
    original=deepcopy(value)
    checked=validate_scene_method(value,source_scope='static_only',
        source_refs=value['source']['references'],frame_ids=['reference-1'])
    assert value==original
    assert world_sha256(checked['world'])==world_sha256(checked['plan']['worlds'][0])
    assert checked['source']['motion_observed'] is False
    assert checked['stages'][0]['status']=='inferred'


@pytest.mark.parametrize('key',['motion_observed','audio_observed','whole_video_watched'])
def test_authors_cannot_promote_static_or_sampled_data_to_playback_evidence(key):
    value=carrier(scope='sampled_frames_only')
    value['source'][key]=True
    with pytest.raises(ServiceError,match='observation_not_admitted'):
        validate_scene_method(value)


def test_unsupplied_frames_sources_and_verified_stages_are_rejected():
    value=carrier()
    with pytest.raises(ServiceError,match='source_changed'):
        validate_scene_method(value,source_refs=[dict(id='reference-1',sha256='b'*64)])
    with pytest.raises(ServiceError,match='frame_not_supplied'):
        validate_scene_method(value,frame_ids=['other'])
    unbound=deepcopy(value)
    unbound['source']['references']=[]
    with pytest.raises(ServiceError,match='frame_source_unbound'):
        validate_scene_method(unbound)
    value['hypotheses'][0]['evidence_frame_ids']=['invented']
    with pytest.raises(ServiceError,match='evidence_frame_changed'):
        validate_scene_method(value)
    value=carrier()
    value['stages'][0]['status']='verified'
    with pytest.raises(ServiceError,match='unearned_verification'):
        validate_scene_method(value)


def test_world_and_plan_cannot_disagree():
    value=carrier(plan=True)
    value['world']['seed']+=1
    with pytest.raises(ServiceError,match='world_plan_changed'):
        validate_scene_method(value)


def test_transfer_receives_principles_without_reference_layout_or_evidence():
    method=dict(technique='Keep the old generic technique semantics.',scene_space=carrier(plan=True))
    original=deepcopy(method)
    transfer=transferable_scene_method(method)
    scene=validate_scene_method(transfer['scene_space'],source_scope='scene_data_only',source_refs=[],frame_ids=[])
    assert method==original
    assert transfer['technique']==method['technique']
    assert scene['world'] is scene['plan'] is None
    assert scene['source']['references']==scene['source']['frame_ids']==scene['hypotheses']==[]
    assert scene['principles']==method['scene_space']['principles']
    assert scene['stages'][0]['status']=='pending'
    legacy=dict(technique='Original method without the new carrier')
    assert transferable_scene_method(legacy)==legacy
    unknown=dict(scene_space=dict(schema='unrelated-legacy-schema',data='Retain original meaning.'))
    assert transferable_scene_method(unknown)==unknown


@pytest.mark.parametrize('value',[False,{},[],10**400,float('inf')])
def test_malformed_confidence_has_a_service_error(value):
    method=carrier()
    method['hypotheses'][0]['confidence']=value
    with pytest.raises(ServiceError):
        validate_scene_method(method)


def test_guidance_preserves_source_and_evidence_boundaries():
    offer=scene_method_guidance('sampled_frames_only')
    assert offer['result_field']=='method.scene_space'
    assert 'verified' not in offer['statuses']
    assert offer['source_scope']=='sampled_frames_only'
    assert any('source files' in row for row in offer['instructions'])
    assert any('Changed-content transfer' in row for row in offer['instructions'])


def test_guidance_structural_example_passes_the_real_world_plan_and_method_validators():
    example=scene_method_guidance('static_image')['structural_example']
    world=validate_world(example['world'])
    plan=validate_plan(example['plan'])
    assert world_sha256(world)==world_sha256(plan['worlds'][0])
    method=carrier()
    method.update(world=example['world'],plan=example['plan'])
    checked=validate_scene_method(method,source_scope='static_image',
        source_refs=method['source']['references'],frame_ids=method['source']['frame_ids'])
    assert checked['plan']['duration_frames']==1
    assert checked['world']['style']=='unspecified'
    assert all(row['recipe_ref'] is None for row in checked['world']['objects'])
    # It is inert syntax: it supplies neither a source painter nor reusable art.
    assert set(example)=={'purpose','world','plan'}
    assert {row['kind'] for row in checked['world']['objects']}=={'placeholder'}
    example['world']['style']='changed only in this returned offer'
    assert scene_method_guidance('static_image')['structural_example']['world']['style']=='unspecified'


@pytest.mark.parametrize('mistake,code',[
    ('style_dictionary','scene_world_text_invalid'),
    ('recipe_parameters','scene_world_fields_invalid'),
    ('world_reference_wrapper','scene_world_fields_invalid'),
])
def test_author_guidance_example_does_not_relax_the_rejected_structures(mistake,code):
    example=scene_method_guidance('static_image')['structural_example']
    if mistake=='style_dictionary':
        example['world']['style']={'medium':'ink'}
        with pytest.raises(ServiceError,match=code):validate_world(example['world'])
    elif mistake=='recipe_parameters':
        example['world']['objects'][0]['recipe_ref']={'seed':1,'params':{}}
        with pytest.raises(ServiceError,match=code):validate_world(example['world'])
    else:
        example['plan']['worlds']=[dict(world_id=example['world']['id'],world_revision=1)]
        with pytest.raises(ServiceError,match=code):validate_plan(example['plan'])


def test_occlusion_requires_explicit_drawing_order_as_well_as_consistent_depth():
    world=scene_method_guidance('static_image')['structural_example']['world']
    back=world['objects'][0]
    back['depth']=dict(kind='ordinal',value=0)
    front=deepcopy(back);front.update(id='front-object',depth=dict(kind='ordinal',value=1))
    world['objects'].append(front)
    world['relations']=[dict(id='front-over-back',kind='occlusion',front_id=front['id'],back_id=back['id'])]
    with pytest.raises(ServiceError,match='scene_world_occlusion_order_invalid'):validate_world(world)
    front['order']=1
    assert validate_world(world)['objects'][1]['order']==1
    front['depth']['value']=-1
    with pytest.raises(ServiceError,match='scene_world_occlusion_depth_invalid'):validate_world(world)
