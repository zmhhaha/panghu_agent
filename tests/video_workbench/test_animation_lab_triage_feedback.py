"""Developer dispositions become data only for the exact source artifact."""
import json

import pytest

from video_workbench.animation_lab_candidates import effective_triage_feedback
from video_workbench.animation_lab_review import build_review_request
from video_workbench.animation_lab_samples import compile_sample
from video_workbench.animation_lab_triage_store import TriageStore
from video_workbench.motion import sha256


def source(bundle):
    row = dict(schema='video.animation-lab-triage-snapshot.v1', run_identity='a'*64,
        evidence_identity='b'*64, review_identity='c'*64,
        variants=[dict(id=bundle['variant_id'], artifact_sha256=bundle['artifact_sha256'])],
        findings=[dict(id='model-layout', variant_id=bundle['variant_id'], category='layout',
            description='Synthetic opinion, not an actual model finding.')])
    return dict(row, identity=sha256(row))


@pytest.mark.parametrize('decision,expected', [('needs_fix', 1), ('dismiss', 1), ('defer', 0)])
def test_only_current_decisions_on_exact_visual_are_guidance(tmp_path, decision, expected):
    bundle = compile_sample('diagram-connections', 'candidate')
    store = TriageStore(tmp_path / 'lab.db')
    snapshot = source(bundle)
    store.register_report(snapshot)
    old = store.append(snapshot['identity'], 'model-layout', 'needs_fix', 'earlier')
    latest = store.append(snapshot['identity'], 'model-layout', decision, 'current')
    feedback = effective_triage_feedback(store, [bundle])
    assert len(feedback) == expected
    if feedback:
        assert feedback[0]['id'] == latest['id'] and feedback[0]['id'] != old['id']
        assert feedback[0]['source_case_identity'] == snapshot['run_identity']
        assert feedback[0]['source_report_identity'] == snapshot['identity']
        assert 'current' in feedback[0]['comment']
    other = compile_sample('diagram-long-label', 'candidate')
    assert effective_triage_feedback(store, [other]) == []


def test_prior_source_is_auditable_and_blind_model_input_does_not_claim_approval(tmp_path):
    bundle = compile_sample('diagram-connections', 'candidate')
    alternative = compile_sample('diagram-connections', 'baseline')
    store = TriageStore(tmp_path / 'lab.db')
    snapshot = source(bundle)
    store.register_report(snapshot)
    event = store.append(snapshot['identity'], 'model-layout', 'dismiss', 'Prefer the original spacing.')
    feedback = effective_triage_feedback(store, [bundle, alternative])
    request = build_review_request(case_identity='d'*64, module='diagram',
        runtime_identity='e'*64, evidence_identity='f'*64,
        variants=[dict(id=row['variant_id'], artifact_sha256=row['artifact_sha256'],
                       duration_seconds=row['duration_seconds']) for row in (bundle, alternative)],
        frames=[dict(id='frame-'+row['variant_id'], variant_id=row['variant_id'], clip_id='scene-1',
            time_seconds=0, sha256=str(index)*64, mime_type='image/jpeg', width=1280, height=720)
            for index, row in enumerate((bundle, alternative), 1)], artifact_feedback=feedback)
    public = json.dumps(request['public'])
    assert event['id'] not in public and snapshot['identity'] not in public
    assert '误报或不需修改' in request['public']['developer_feedback'][0]['comment']
    provenance = request['private']['feedback_sources'][0]
    assert provenance['source_report_identity'] == snapshot['identity']
    assert provenance['source_finding_id'] == 'model-layout'
    assert provenance['source_case_identity'] == snapshot['run_identity']
    assert request['public']['developer_feedback'][0]['verdict'] is None


def test_export_preserves_superseded_notes_without_guiding_new_reviews(tmp_path):
    bundle = compile_sample('diagram-connections', 'candidate')
    store = TriageStore(tmp_path / 'lab.db')
    snapshot = source(bundle)
    store.register_report(snapshot)
    store.append(snapshot['identity'], 'model-layout', 'needs_fix', 'old instruction-like data')
    store.append(snapshot['identity'], 'model-layout', 'defer', 'not yet decided')
    assert effective_triage_feedback(store, [bundle]) == []
    records = [json.loads(row) for row in store.export_jsonl().splitlines()]
    decisions = [row for row in records if row['record_type'] == 'evaluation_decision']
    assert len(decisions) == 2 and decisions[0]['superseded'] and decisions[1]['effective']
