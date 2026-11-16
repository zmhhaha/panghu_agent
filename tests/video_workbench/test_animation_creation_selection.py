"""Explicit laboratory choices constrain work; fixture media proves no quality."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_creation_selection import (
    freeze_constraints, manim_request, motion_tools, normalize_selection,
    validate_rendered_selection, validate_source_selection,
)
from video_workbench.animation_motion_evidence import GSAP_SHA256, motion_orchestration
from video_workbench.animation_method_service import AnimationMethodService
from video_workbench.animejs_library import descriptor
from test_animation_method_service import Gateway, Renderer, start, finish, runtime_capabilities
from test_animation_motion_evidence import source, NATIVE, gsap_fixture
from test_animation_authoring_tools import manim_runtime, runtime as anime_runtime


@pytest.mark.parametrize('bad', [None, [], {'engine': 'canvas'}, {'creation': 'canvas'},
    {'creation': ['webgpu']}, {'motion': ['gsap', 'gsap']}, {'motion': ['anime']},
    {'creation': [True]}, {'runtime_identity': {}}, {'assets': {}}])
def test_selection_accepts_only_the_two_bounded_public_axes(bad):
    with pytest.raises(ServiceError, match='selection_invalid'):
        normalize_selection(bad)


def test_selection_is_canonical_and_empty_groups_remain_agent_choice():
    assert normalize_selection({'creation': ['three', 'canvas'], 'motion': ['animejs', 'gsap']}) == {
        'creation': ['canvas', 'three'], 'motion': ['gsap', 'animejs']}
    assert normalize_selection({'creation': [], 'motion': []}) == {'creation': [], 'motion': []}


def test_selection_only_narrows_trusted_engines_and_manim_uses_an_auxiliary_carrier():
    original = dict(allowed_engines=['html-svg', 'canvas', 'three'], runtime_identity={'fixed': True})
    narrowed = freeze_constraints({'creation': ['three', 'canvas']}, original, {})
    assert narrowed['allowed_engines'] == ['canvas', 'three']
    assert narrowed['runtime_identity'] == original['runtime_identity']
    assert original['allowed_engines'] == ['html-svg', 'canvas', 'three']
    assert freeze_constraints({'creation': ['manim']}, original, {})['allowed_engines'] == ['html-svg']
    with pytest.raises(ServiceError, match='engine_not_admitted'):
        freeze_constraints({'creation': ['three']}, {'allowed_engines': ['html-svg']}, {})


def test_motion_availability_uses_real_gsap_asset_without_an_authoring_library_entry():
    assert motion_tools({}) == ['native-js']
    assert motion_tools({'assets': {'gsap.min.js': GSAP_SHA256}}) == ['native-js', 'gsap']
    assert motion_tools({'assets': {'gsap.min.js': 'f' * 64}}) == ['native-js']
    assert motion_tools({'authoring_libraries': {'animejs': descriptor()}}) == ['native-js', 'animejs']
    frozen = freeze_constraints({'motion': ['gsap']}, {'allowed_engines': ['html-svg']},
                                {'gsap.min.js': GSAP_SHA256})
    assert frozen['capability_selection']['motion'] == ['gsap']
    with pytest.raises(ServiceError, match='motion_not_admitted'):
        freeze_constraints({'motion': ['animejs']}, {'allowed_engines': ['html-svg']}, {})


def test_manim_selection_prepares_bounded_resources_without_accepting_browser_source():
    assert manim_request({'creation': ['manim']}, None, 1) == dict(kind='manim', width=640,
        height=360, frame_count=24, global_start_frame=0, seed=17)
    assert manim_request({'creation': []}, None, 4) is None
    with pytest.raises(ServiceError, match='resource_conflict'):
        manim_request({'creation': ['canvas']}, {'kind': 'manim'}, 4)


def test_motion_selection_requires_substantive_source_and_rejects_unselected_libraries():
    native = source()
    validate_source_selection({'creation': ['canvas'], 'motion': ['native-js']}, native)
    with pytest.raises(ServiceError, match='source_engine_mismatch'):
        validate_source_selection({'creation': ['three']}, native)
    with pytest.raises(ServiceError, match='motion_not_used'):
        validate_source_selection({'motion': ['gsap']}, source('', before='<script src="/assets/gsap.min.js"></script>'))
    candidate, _ = gsap_fixture()
    validate_source_selection({'motion': ['gsap']}, candidate['source'])
    with pytest.raises(ServiceError, match='unselected_motion_library'):
        validate_source_selection({'motion': ['native-js']}, candidate['source'])
    # One permitted tool is enough: multi-selection does not require ornamental use of all.
    validate_source_selection({'motion': ['native-js', 'gsap']}, native)


def test_render_requires_exact_observation_and_never_turns_selection_into_a_tag():
    candidate, demos = gsap_fixture()
    outcome = dict(media=candidate['media'], runtime=demos['candidate'][0]['runtime'])
    before = deepcopy(outcome)
    validate_rendered_selection({'motion': ['gsap']}, candidate['source'], outcome, candidate['operation_id'])
    assert outcome == before
    outcome['runtime'] = {}
    with pytest.raises(ServiceError, match='motion_not_observed'):
        validate_rendered_selection({'motion': ['gsap']}, candidate['source'], outcome, candidate['operation_id'])


def test_anime_uses_local_frame_bridge_and_actual_rendered_library_inventory():
    src = source('''import {createFrameTimeline} from '/assets/anime_timeline.mjs';
        const timeline=createFrameTimeline({fps:24});timeline.add(target,{x:[0,10]});
        window.__animationExperiment={seek(seconds){timeline.renderAt(Math.round(seconds*24));}};''')
    outcome = dict(media={'sha256': 'a' * 64}, runtime=anime_runtime())
    validate_rendered_selection({'motion': ['animejs']}, src, outcome, 'test-render')
    with pytest.raises(ServiceError, match='unselected_motion_library'):
        validate_source_selection({'motion': ['native-js']}, src)


MANIM_CARRIER = '''import {prepareManimMethodFrames} from '/assets/manim_method_frames.mjs';
const layer=prepareManimMethodFrames(container,manifest,{placementStartFrame:0});
window.__animationExperiment={ready:layer.ready,seek(seconds){layer.seekFrame(Math.round(seconds*24));}};'''


def test_manim_native_carrier_needs_actual_resource_and_time_driven_frame_consumption():
    src = source(MANIM_CARRIER, engine='html-svg')
    selection = {'creation': ['manim'], 'motion': ['native-js']}
    validate_source_selection(selection, src)
    outcome = dict(media={'sha256': 'a' * 64}, runtime=manim_runtime())
    validate_rendered_selection(selection, src, outcome, 'manim-render')
    before = deepcopy(outcome)
    assert motion_orchestration(dict(source=src, media=outcome['media'], operation_id='manim-render'),
        dict(source=src, runtime=outcome['runtime']))[0]['id'] == 'native-js'
    assert outcome == before
    with pytest.raises(ServiceError, match='motion_not_observed'):
        validate_rendered_selection(selection, src, dict(media=outcome['media'], runtime={}), 'manim-render')
    for bad in (MANIM_CARRIER.replace('seconds*24', '0'),
                MANIM_CARRIER.replace('prepareManimMethodFrames(container,manifest,{placementStartFrame:0})', '{}')):
        with pytest.raises(ServiceError, match='motion_not_used'):
            validate_source_selection(selection, source(bad, engine='html-svg'))
    # Mixed drawing choices may choose Canvas without consuming the prepared resource.
    validate_rendered_selection({'creation': ['canvas', 'manim'], 'motion': ['native-js']}, source(),
        dict(media={'sha256': 'a' * 64}, runtime={}), 'canvas-render')


def make_service(tmp_path):
    renderer = Renderer(tmp_path / 'renders')
    renderer.capabilities = lambda: runtime_capabilities(['html-svg', 'canvas', 'three'])
    return AnimationMethodService(tmp_path / 'lab', author_gateway=Gateway(), review_gateway=Gateway(True),
                                  renderer=renderer)


def test_no_selection_keeps_existing_frozen_request_and_internal_fields_cannot_be_injected(tmp_path):
    service = make_service(tmp_path)
    row = start(service)
    frozen = service.commissions.get('developer', row['id'])['frozen']
    assert 'capability_selection' not in frozen and 'capability_selection' not in frozen['constraints']
    for name, value in [('manim_iteration', {}), ('constraints', {}), ('assets', {}), ('resource_round', 0)]:
        with pytest.raises(ServiceError):
            start(service, **{name: value})


def test_selection_is_frozen_exposed_in_detail_and_same_key_change_is_a_conflict(tmp_path):
    service = make_service(tmp_path)
    body = dict(request_id=str(uuid4()), goal='Render an explicit drawing choice', duration_seconds=4,
        budget=dict(max_rounds=1, max_seconds=300), capability_selection={'creation': ['three', 'canvas']})
    row = service.start('developer', body)
    assert row['capability_selection'] == {'creation': ['canvas', 'three']}
    frozen = service.commissions.get('developer', row['id'])['frozen']
    assert frozen['constraints']['allowed_engines'] == ['canvas', 'three']
    service.renderer.capabilities = lambda: runtime_capabilities(['html-svg'])
    assert service.start('developer', body)['id'] == row['id']
    assert service.commissions.get('developer', row['id'])['frozen'] == frozen
    with pytest.raises(ServiceError, match='conflict'):
        service.start('developer', dict(body, capability_selection={'creation': ['canvas']}))


def test_retry_and_continuation_inherit_selection_without_turning_it_into_evidence(tmp_path):
    service = make_service(tmp_path)
    first = start(service, capability_selection={'creation': ['html-svg']})
    first = finish(service, first['id'])
    assert first['state'] == 'completed'
    continued = start(service, base_commission_id=first['id'])
    assert continued['capability_selection'] == first['capability_selection']
    failed = start(service, capability_selection={'creation': ['canvas', 'html-svg']})
    service.commissions.cancel('developer', failed['id'])
    retried = start(service, retry_of_commission_id=failed['id'],
                    capability_selection={'creation': ['html-svg', 'canvas']})
    assert retried['capability_selection'] == {'creation': ['html-svg', 'canvas']}
    assert all('motion_orchestration' not in row for row in retried['candidates'])
    changed_retry = start(service, retry_of_commission_id=failed['id'],
                          capability_selection={'creation': [], 'motion': []})
    assert changed_retry['capability_selection'] == {'creation': [], 'motion': []}
    assert service.commissions.get('developer', changed_retry['id'])['frozen']['constraints']['allowed_engines'] == [
        'html-svg', 'canvas', 'three']


def test_transfer_accepts_explicit_axes_and_defaults_to_the_source_selection(tmp_path):
    service = make_service(tmp_path)
    first = finish(service, start(service, capability_selection={'creation': ['html-svg']})['id'])
    body = dict(request_id=str(uuid4()), goal='Explain a parallel fork and merge',
        structural_changes={'relations': dict(before='One direct route', after='A fork and merge')},
        budget=dict(max_rounds=1, max_seconds=300))
    inherited = service.transfer('developer', first['id'], body)
    assert inherited['capability_selection'] == {'creation': ['html-svg']}
    automatic = service.transfer('developer', first['id'], dict(body, request_id=str(uuid4()),
        capability_selection={'creation': [], 'motion': []}))
    assert automatic['capability_selection'] == {'creation': [], 'motion': []}


def test_mismatched_requested_engine_fails_before_a_candidate_is_added(tmp_path):
    service = make_service(tmp_path)
    row = finish(service, start(service, capability_selection={'creation': ['canvas']})['id'])
    assert row['state'] == 'failed' and row['candidates'] == []
    assert len(service.renderer.calls) == 0
