"""Fixed-recipe compiler and explicitly synthetic observation boundary checks."""
from copy import deepcopy
import json

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_lab_effects import (EFFECT_IDS, compile_effect_project,
    example_effect_plan, validate_effect_plan)
from video_workbench.animation_lab_effects_review import verify_effect_observations
from video_workbench.animation_lab_samples import compile_sample
from video_workbench.motion import sha256


@pytest.mark.parametrize('effect', EFFECT_IDS)
def test_effect_compilation_is_deterministic_and_two_presentations_share_inputs(effect):
    a, b = [compile_sample(effect, variant) for variant in ('baseline', 'candidate')]
    assert a['inputs'] == b['inputs']
    assert a['artifact_sha256'] != b['artifact_sha256']
    assert a['duration_seconds'] == b['duration_seconds'] == 18
    assert a['clips'][0]['files']['audio.wav'] == b['clips'][0]['files']['audio.wav']
    for sample in (a, b):
        files = compile_effect_project(**sample['inputs']['inputs'], variant=sample['variant_id'])
        assert all(sample['clips'][0]['files'][key] == value for key, value in files.items())
        page = files['index.html'].decode()
        assert '<svg id="effect-scene"' in page
        assert 'getBoundingClientRect' in page and 'effectRender(lastTime)' in page
        assert '__timelines' in page and 'document.fonts.ready' in page
        assert all(token not in page for token in ('requestAnimationFrame(', 'setInterval(', 'Date.now(', 'Math.random('))
        timeline = json.loads(files['timeline.json'])
        assert [row['ordinal'] for row in timeline['actions']] == list(range(len(timeline['actions'])))
        assert all(row['target'] in timeline['object_ids'] for row in timeline['actions'])
        assert json.loads(files['manifest.json'])['developer_only'] is True


@pytest.mark.parametrize('change', [dict(effect='arbitrary-code'), dict(labels={'injected':'<script>'}),
    dict(title='changed content'), dict(extra='unknown'), dict(schema='video.motion-plan.v1')])
def test_recipe_does_not_admit_changed_content_or_arbitrary_inputs(change):
    with pytest.raises(ValueError, match='effect_recipe_invalid'):
        validate_effect_plan(dict(example_effect_plan('condition-branch'), **change))


def _fixture(effect='condition-branch'):
    # Synthetic contract data only: never presented as browser/visual evidence.
    bundle = compile_sample(effect, 'candidate')
    timeline = deepcopy(bundle['clips'][0]['timeline'])
    plan = deepcopy(bundle['inputs']['inputs']['plan'])

    def sample(t):
        return dict(time=t, actual_time=t, frame_sha256=sha256(dict(synthetic_time=t)),
            engine_state=dict(engine='svg-effects', context='svg', width=1280, height=720,
                time=t, effect=plan['effect'], deterministic=True,
                objects=[dict(id=identity, tag='g', opacity=1, text='',
                    bounds=dict(x=100+t,y=150,width=40,height=40), attributes={
                        'transform':f'translate({t} 0)', 'd':f'M0 0L{t+1} {t+2}', 'stroke-dashoffset':str(t)})
                    for identity in timeline['object_ids']],
                text_boxes=[dict(text=text,opacity=1,font_size=22,bounds=dict(x=64,y=y,width=120,height=24))
                            for text,y in [('synthetic title',40),('synthetic takeaway',650)]],
                semantic=dict(source='synthetic fixture')))

    initial, last = sample(0), sample(18-1/24)
    observations = dict(schema='video.animation-lab-engine-observations.v1', engine='svg-effects',
        seek_protocol='hyperframes-core-renderSeek-exact', render_fps=24, rendering='svg_dom', page_errors=[],
        timeline_sha256=sha256(timeline), initial=initial, last=last, repeated_initial=deepcopy(initial),
        windows=[dict(ordinal=a['ordinal'], before=sample(max(0,a['start']-1/24)),
            middle=sample(a['start']+a['duration']/2), after=sample(min(18-1/24,a['start']+a['duration']+1/24)))
            for a in timeline['actions']],
        reverse_seek=dict(first_sha256=initial['frame_sha256'], last_sha256=last['frame_sha256'],
            repeated_first_sha256=initial['frame_sha256'], repeat_state_equal=True))
    return plan,timeline,observations


def test_synthetic_valid_contract_remains_technical_and_not_aesthetic_approval():
    result = verify_effect_observations(*_fixture())
    assert result['technical_status'] == 'passed'
    assert result['visual_comprehension'] == 'pending_developer_review'
    assert result['production_admitted'] is False


def test_static_target_cannot_pass_due_to_unrelated_semantic_or_screenshot_changes():
    plan,timeline,obs = _fixture()
    action = timeline['actions'][0]
    before = next(row for row in obs['windows'][0]['before']['engine_state']['objects'] if row['id']==action['target'])
    rows = obs['windows'][0]['after']['engine_state']['objects']
    rows[rows.index(next(row for row in rows if row['id']==action['target']))] = deepcopy(before)
    with pytest.raises(ServiceError, match='target_change_missing'):
        verify_effect_observations(plan,timeline,obs)


@pytest.mark.parametrize('failure', ['missing_object','wrong_clock','clipped_text','small_text','fake_reset','missing_window'])
def test_incomplete_or_invalid_saved_measurements_fail(failure):
    plan,timeline,obs = _fixture()
    middle = obs['windows'][0]['middle']
    if failure=='missing_object':
        middle['engine_state']['objects'].pop()
    elif failure=='wrong_clock':
        middle['actual_time'] += .5
    elif failure=='clipped_text':
        middle['engine_state']['text_boxes'][0]['bounds']['x'] = 1250
    elif failure=='small_text':
        middle['engine_state']['text_boxes'][0]['font_size'] = 8
    elif failure=='fake_reset':
        obs['repeated_initial']['engine_state']['semantic']['source'] = 'different'
    else:
        obs['windows'].pop()
    with pytest.raises(ServiceError):
        verify_effect_observations(plan,timeline,obs)


def test_rehashed_timeline_cannot_invent_a_different_recipe_or_skip_actions():
    plan,timeline,obs = _fixture()
    timeline['actions'].pop()
    obs['timeline_sha256'] = sha256(timeline)
    with pytest.raises(ServiceError, match='timeline_mismatch'):
        verify_effect_observations(plan,timeline,obs)


@pytest.mark.parametrize('ordinal,key', [(0,'stroke-dashoffset'),(1,'d')])
def test_path_action_requires_actual_draw_or_morph_attributes(ordinal,key):
    plan,timeline,obs = _fixture('svg-morph')
    target = timeline['actions'][ordinal]['target']
    window = obs['windows'][ordinal]
    before = next(row for row in window['before']['engine_state']['objects'] if row['id']==target)
    after = next(row for row in window['after']['engine_state']['objects'] if row['id']==target)
    after['attributes'][key] = before['attributes'][key]
    with pytest.raises(ServiceError, match='path_change_missing'):
        verify_effect_observations(plan,timeline,obs)
