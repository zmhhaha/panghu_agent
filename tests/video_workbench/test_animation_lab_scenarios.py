"""Requirement/compilation contracts. No model, browser or production dispatch."""
from copy import deepcopy
import json

import pytest

from video_workbench.animation_lab_samples import catalog,compile_sample
from video_workbench.animation_lab_scenarios import SCENARIOS,example_scenario_plan,validate_scenario_plan,recipe_for
from video_workbench.animation_lab_matrix import coverage,requirement_suite,prepare_suite
from video_workbench.animation_lab_candidates import semantic_identity
from video_workbench.motion import sha256


def test_active_cross_domain_corpus_covers_eleven_modules_and_39_requirements():
    rows=catalog(); corpus=coverage(rows)
    assert len(rows)==39 and corpus['requirements']==39
    assert all(row['is_matrix_case'] for row in rows)
    assert len(corpus['modules'])==11
    assert all(row['requirements']>=3 and row['domains']>=3 for row in corpus['modules'])
    assert len(corpus['domains'])>=8
    for module in ('charts','algorithms','interface','math','spatial','fields'):
        selected=[row for row in rows if row['module']==module]
        assert len(selected)==4 and len({row['domain'] for row in selected})==4
        assert len({sha256(row['data']) for row in selected})==4


@pytest.mark.parametrize('identity',list(SCENARIOS))
def test_new_families_freeze_real_briefs_and_identical_semantics(identity):
    a,b=(compile_sample(identity,variant) for variant in ('baseline','candidate'))
    assert a['input_sha256']==b['input_sha256']
    assert a['artifact_sha256']!=b['artifact_sha256']
    assert a['duration_seconds']==b['duration_seconds']
    assert semantic_identity(a)==semantic_identity(b)
    assert a['inputs']['requirement']['requirement']['goal']
    for bundle in (a,b):
        clip=bundle['clips'][0]
        assert len(clip['checkpoints'])==len(clip['timeline']['actions'])*3+2
        manifest=json.loads(clip['files']['manifest.json'])
        assert manifest['production_admitted'] is False
        assert all(sha256(clip['files'][name])==digest for name,digest in manifest['files'].items())


def test_suite_seed_binding_selection_and_lightweight_export_are_reproducible():
    rows=catalog()
    a=requirement_suite(rows,seed=29);b=requirement_suite(rows,seed=29)
    assert a==b and len(a['cases'])==39
    assert a['schema']=='video.animation-lab-requirement-suite.v2'
    assert all(row['sample_identity'] for row in a['cases'])
    assert a['identity']==sha256({k:v for k,v in a.items() if k!='identity'})
    changed=requirement_suite(rows,seed=30)
    assert changed['identity']!=a['identity']
    assert {r['case_id'] for r in changed['cases']}=={r['case_id'] for r in a['cases']}
    assert all(r['status']=='not_run' for r in a['cases'])
    assert a['execution_status']=='prepared_not_executed'
    assert len(json.dumps(a).encode())<160000 and 'base64' not in json.dumps(a)
    selected=requirement_suite(rows,module='charts',domain='energy',engine='svg')
    assert [r['case_id'] for r in selected['cases']]==['chart-energy-balance']
    assert requirement_suite(rows,module='charts',engine='html')['case_count']==0


@pytest.mark.parametrize('options',[dict(seed=True),dict(seed=-1),dict(seed=2**32),dict(module='../x'),dict(domain='missing'),dict(engine='')])
def test_invalid_suite_input_is_not_silently_expanded(options):
    with pytest.raises(ValueError):requirement_suite(catalog(),**options)


def test_registry_and_mutable_returns_cannot_inject_code_or_relabel_requirements():
    identity=next(iter(SCENARIOS));plan=example_scenario_plan(identity)
    plan['narrative']='</script><script>fetch("/private")</script>'
    with pytest.raises(ValueError):validate_scenario_plan(plan)
    fresh=example_scenario_plan(identity)
    with pytest.raises(ValueError):recipe_for(fresh,'other')
    bundle=compile_sample(identity,'candidate')
    bundle['inputs']['requirement']['requirement']['goal']='tampered'
    assert compile_sample(identity,'candidate')['inputs']['requirement']['requirement']['goal']!='tampered'


def test_independent_suite_preparation_freezes_two_real_variants_without_dispatch(tmp_path):
    runtime=dict(schema='video.motion-runtime.v1',hyperframes='0.8.149',
        hyperframes_source='synthetic_contract_fixture',node='synthetic',browser='synthetic',
        ffmpeg='synthetic',gsap='3.14.2',gsap_sha256='a'*64,
        font_sha256='b'*64,npm_lock_sha256='c'*64,dpkg_sha256='d'*64,
        image_digest='sha256:'+'e'*64)
    root=tmp_path/'suite'
    receipt=prepare_suite(root,runtime,module='interface',domain='education')
    assert receipt['status']=='prepared_not_executed' and receipt['captures']==2
    frozen=json.loads((root/'suite.json').read_bytes())
    run=json.loads((root/'interface-library-search'/'run.json').read_bytes())
    assert frozen['case_count']==1 and run['limits']['captures']==2
    assert run['agent_calls']==run['tts_calls']==run['production_writes']==0
    assert not (root/'interface-library-search'/'technical.json').exists()
    with pytest.raises(ValueError,match='destination_exists'):prepare_suite(root,runtime,module='interface')
