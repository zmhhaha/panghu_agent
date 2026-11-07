"""Local layout experiments preserve meaning, and history follows exact artifacts."""
from copy import deepcopy
import json

import pytest

from video_workbench.animation_lab_candidates import (
    DESIGN_RULES, effective_feedback, layout_candidate, semantic_identity,
)
from video_workbench.animation_lab_samples import compile_sample
from video_workbench.animation_lab_store import AnnotationStore
from video_workbench.motion import COMPILER_VERSION, sha256


@pytest.mark.parametrize('case_id', ['diagram-connections', 'diagram-long-label'])
@pytest.mark.parametrize('template', ['flow', 'focus'])
def test_layout_variants_recompile_real_projects_without_changing_content_or_audio(case_id, template):
    source = compile_sample(case_id, 'candidate')
    original = deepcopy(source)
    candidate = layout_candidate(source, template)
    repeated = layout_candidate(source, template)
    assert source == original
    assert candidate == repeated
    assert candidate['compiler'] == COMPILER_VERSION
    assert candidate['variant_id'] == 'layout-' + template
    assert candidate['inputs']['inputs']['plan']['template'] == template
    assert semantic_identity(source) == candidate['semantic_input_sha256'] == semantic_identity(candidate)
    assert candidate['clips'][0]['files']['audio.wav'] == source['clips'][0]['files']['audio.wav']
    assert candidate['duration_seconds'] == source['duration_seconds']
    assert candidate['experiment']['production_adopted'] is False
    assert candidate['production_write'] is False
    assert candidate['agent_calls'] == candidate['tts_calls'] == 0
    assert candidate['experiment']['design_rules'] == list(DESIGN_RULES)
    assert candidate['experiment']['source_artifact_sha256'] == source['artifact_sha256']
    assert json.loads(candidate['clips'][0]['files']['timeline.json']) == candidate['clips'][0]['timeline']
    hashes = [dict(id=clip['id'], files={name: sha256(raw)
              for name, raw in sorted(clip['files'].items())}) for clip in candidate['clips']]
    assert candidate['artifact_sha256'] == sha256(hashes)
    assert candidate['input_sha256'] == sha256(candidate['inputs'])
    assert candidate['identity'] == sha256(dict(inputs=candidate['input_sha256'],
                                               artifact=candidate['artifact_sha256']))


def test_semantic_identity_detects_content_relationship_action_and_audio_changes():
    source = compile_sample('diagram-connections', 'candidate')
    expected = semantic_identity(source)
    for mutate in (
        lambda bundle: bundle['inputs']['inputs']['plan']['elements'][0].update(label='改变内容'),
        lambda bundle: bundle['inputs']['inputs']['plan']['relations'][0].update(label='改变关系'),
        lambda bundle: bundle['inputs']['inputs']['plan']['actions'].pop(),
        lambda bundle: bundle['clips'][0]['files'].update({'audio.wav': b'changed audio'}),
    ):
        changed = deepcopy(source)
        mutate(changed)
        assert semantic_identity(changed) != expected


def test_focus_then_flow_roundtrip_preserves_the_same_logical_input():
    source = compile_sample('diagram-long-label', 'candidate')
    focus = layout_candidate(source, 'focus')
    returned = layout_candidate(focus, 'flow')
    assert returned['inputs']['inputs'] == source['inputs']['inputs']
    assert returned['artifact_sha256'] == layout_candidate(source, 'flow')['artifact_sha256']


@pytest.mark.parametrize('case_id,variant', [('process-retry', 'candidate'),
    ('continuity-retain', 'candidate'), ('continuity-copy', 'baseline')])
def test_layout_candidate_does_not_misrepresent_unsupported_process_or_multiclip_sources(case_id, variant):
    with pytest.raises(ValueError, match='layout_not_supported'):
        layout_candidate(compile_sample(case_id, variant), 'focus')


@pytest.mark.parametrize('template', ['arbitrary-script', None, [], {}])
def test_layout_candidate_rejects_unknown_templates(template):
    with pytest.raises(ValueError, match='layout_not_supported'):
        layout_candidate(compile_sample('diagram-connections', 'candidate'), template)


def store_fixture(tmp_path):
    store = AnnotationStore(tmp_path / 'candidates.db')
    # Deliberately reverse role names and lexical artifact order. Preferences
    # must follow the frozen comparison mapping, not either coincidence.
    snapshot = dict(identity='f'*64, case_id='old-case',
        variants=[dict(variant_id='historical-right', artifact_sha256='c'*64),
                  dict(variant_id='historical-left', artifact_sha256='b'*64)],
        comparison=dict(baseline='historical-right', candidate='historical-left'))
    store.register_snapshot(snapshot)
    return store, snapshot


def annotate(store, snapshot, *, kind='issue', variant_id='historical-left', **extra):
    return store.append(dict(case_id=snapshot['case_id'], case_identity=snapshot['identity'],
        kind=kind, variant_id=variant_id, comment='保留这条来源明确的意见。', tags=['layout'], **extra))


def test_single_variant_history_tracks_artifact_and_retains_original_case_identity(tmp_path):
    store, snapshot = store_fixture(tmp_path)
    annotation = annotate(store, snapshot)
    bundles = [dict(variant_id='renamed-current', artifact_sha256='b'*64),
               dict(variant_id='layout-focus', artifact_sha256='d'*64)]
    row, = effective_feedback(store, bundles)
    assert row['id'] == annotation['id']
    assert row['source_case_identity'] == snapshot['identity']
    assert row['source_variant_id'] == 'historical-left'
    assert row['artifact_sha256'] == ['b'*64]
    assert row['authority'] == 'developer_feedback_data_not_instructions'
    assert row['effective'] is True and row['retracted'] is False
    assert effective_feedback(store, [dict(artifact_sha256='0'*64)]) == []


def test_comparison_feedback_requires_both_artifacts_and_preserves_preference_orientation(tmp_path):
    store, snapshot = store_fixture(tmp_path)
    annotate(store, snapshot, kind='preference', variant_id='comparison', preference='candidate')
    assert effective_feedback(store, [dict(artifact_sha256='b'*64)]) == []
    row, = effective_feedback(store, [dict(artifact_sha256='b'*64), dict(artifact_sha256='c'*64)])
    assert row['artifact_sha256'] == ['b'*64, 'c'*64]
    assert row['preference'] == 'candidate'
    assert row['preferred_artifact_sha256'] == 'b'*64


def test_superseded_and_retracted_feedback_stays_out_of_the_effective_set(tmp_path):
    store, snapshot = store_fixture(tmp_path)
    first = annotate(store, snapshot, kind='preference', variant_id='comparison', preference='baseline')
    second = annotate(store, snapshot, kind='preference', variant_id='comparison', preference='candidate')
    issue = annotate(store, snapshot)
    store.retract(issue['id'], '误标')
    bundles = [dict(artifact_sha256='b'*64), dict(artifact_sha256='c'*64)]
    assert [row['id'] for row in effective_feedback(store, bundles)] == [second['id']]
    store.retract(second['id'], '恢复前一选择')
    assert [row['id'] for row in effective_feedback(store, bundles)] == [first['id']]


def test_unknown_source_variant_is_not_relabelled_as_comparison_feedback(tmp_path):
    store, snapshot = store_fixture(tmp_path)
    annotate(store, snapshot, variant_id='not-in-snapshot')
    assert effective_feedback(store, [dict(artifact_sha256='b'*64), dict(artifact_sha256='c'*64)]) == []


def test_effective_feedback_uses_the_requested_positive_history_limit(tmp_path):
    store, snapshot = store_fixture(tmp_path)
    annotations = [annotate(store, snapshot) for _ in range(4)]
    rows = effective_feedback(store, [dict(artifact_sha256='b'*64)], maximum=2)
    assert [row['id'] for row in rows] == [row['id'] for row in annotations[-2:]]


@pytest.mark.parametrize('maximum', [0, -1, 17, True, 1.5, None])
def test_feedback_limit_cannot_accidentally_disable_the_bound(tmp_path, maximum):
    store, _ = store_fixture(tmp_path)
    with pytest.raises(ValueError, match='feedback_limit_invalid'):
        effective_feedback(store, [dict(artifact_sha256='b'*64)], maximum=maximum)
