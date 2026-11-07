"""Finite old-engine domain expansion; compilation checks, no browser claims."""
import json

import pytest

from video_workbench.animation_lab_domain_cases import DOMAIN_CASES,inputs_for
from video_workbench.animation_lab_samples import compile_sample
from video_workbench.animation_lab_candidates import semantic_identity
from video_workbench.animation_lab_matrix import requirement_suite


@pytest.mark.parametrize('identity',list(DOMAIN_CASES))
def test_domain_requirements_compile_genuine_engines_and_preserve_ab_inputs(identity):
    a,b=(compile_sample(identity,variant) for variant in ('baseline','candidate'))
    assert a['artifact_sha256']!=b['artifact_sha256']
    assert a['input_sha256']==b['input_sha256'] and semantic_identity(a)==semantic_identity(b)
    assert a['inputs']['requirement']['domain']==DOMAIN_CASES[identity]['domain']
    assert b['tts_calls']==b['agent_calls']==0


def test_domain_variation_changes_more_than_titles_and_preserves_explicit_boundaries():
    plans={key:inputs_for(key)[0] for key in DOMAIN_CASES}
    assert {plans[k]['plan']['packet_count'] for k in plans if DOMAIN_CASES[k]['module']=='canvas'}=={4,8,12}
    assert {len(plans[k]['plan']['layers']) for k in plans if DOMAIN_CASES[k]['module']=='three'}=={3,4,5}
    processes=[plans[k]['plan'] for k in plans if DOMAIN_CASES[k]['module']=='process']
    assert len({len(plan['actions']) for plan in processes})==2
    assert {len(plans[k]['scenes']) for k in plans if DOMAIN_CASES[k]['module']=='continuity'}=={2,3}
    assert all(row['requirement']['boundaries'] for row in DOMAIN_CASES.values())


def test_three_scene_course_continues_actual_object_across_both_boundaries():
    bundle=compile_sample('domain-continuity-course','candidate')
    assert len(bundle['clips'])==3
    first,second,third=[clip['timeline'] for clip in bundle['clips']]
    assert all(clip['compiler']=='motion-process-continuity-compiler-1' for clip in (first,second,third))
    # Review returns the same assignment to revision; its declared final
    # location and narration must agree rather than claiming archival.
    scenes=bundle['inputs']['inputs']['scenes']
    assert scenes[1]['motion']['plan']['relations'][0]['target']=='worker'
    assert next(x for x in scenes[1]['motion']['plan']['elements'] if x['kind']=='token')['slot']=='token_queue'
    assert scenes[2]['motion']['plan']['relations'][0]['source']=='worker'
    assert scenes[2]['motion']['plan']['relations'][0]['target']=='queue'


def test_four_and_five_layer_labels_use_compact_layout_without_changing_three_layer_fixture():
    compact=compile_sample('domain-three-factory','candidate')['clips'][0]['files']['index.html']
    original=compile_sample('three-layers','candidate')['clips'][0]['files']['index.html']
    assert b'#keys{top:168px}' in compact and b'#keys{top:168px}' not in original
