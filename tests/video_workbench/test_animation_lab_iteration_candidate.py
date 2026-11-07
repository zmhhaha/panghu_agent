"""Use a real frozen-report reader over explicit synthetic capture fixtures."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_animation_lab_evaluation_view import evaluation_factory
from video_workbench.animation_lab import AnimationLab, LabError
from video_workbench.animation_lab_evaluation_view import load_evaluation
from video_workbench.motion import canonical, sha256


def candidate(evaluation_factory, *, technical_issue=False):
    fixture = evaluation_factory(technical_issue=technical_issue, model='not_run')
    loaded = load_evaluation(fixture['root'])
    run, report = loaded['run'], loaded['snapshot']
    lab = AnimationLab.__new__(AnimationLab)
    lab.evaluations_dir = fixture['root'].parent
    lab.iteration_store = SimpleNamespace(base_path='/dev/animations')
    package = dict(identity='a'*64, source_revision='b'*40, module=run['module'],
        feedback=[dict(key='annotation:test', case_id=run['case_id'], binding={})])
    receipt = dict(commit='c'*40, run_slug=fixture['root'].name, report_identity=report['identity'])
    publication = dict(schema='video.animation-lab-candidate-publication.v1',
        task_identity=package['identity'], module=package['module'], source_revision=package['source_revision'],
        commit=receipt['commit'], run_slug=receipt['run_slug'], run_identity=run['identity'],
        report_identity=report['identity'], artifact_sha256s=[row['artifact_sha256'] for row in loaded['bundles']],
        runtime_identity=run['runtime_identity'], semantic_input_sha256=run['semantic_input_sha256'],
        provenance='developer_bound_frozen_evaluation_not_independent_build_attestation')
    publication['identity'] = sha256(publication)
    (fixture['root']/'iteration-candidate.json').write_bytes(canonical(publication))
    return lab, package, receipt, publication, fixture


def test_verified_candidate_is_the_actual_frozen_case_not_a_query_parameter(evaluation_factory):
    lab, package, receipt, publication, fixture = candidate(evaluation_factory)
    proof = lab.verify_iteration_candidate(package, receipt)
    run = fixture['run']
    assert proof['candidate_url'] == '/dev/animations/#evaluation-'+fixture['root'].name+'-'+run['identity'][:16]
    assert proof['publication_identity'] == publication['identity']
    assert proof['verification_scope'] == 'frozen_report_and_developer_publication_binding'
    assert proof['covered_feedback_keys'] == ['annotation:test'] and proof['uncovered_feedback_keys'] == []


@pytest.mark.parametrize('field,value', [('commit', 'd'*40), ('task_identity', 'e'*64),
                                      ('report_identity', 'f'*64)])
def test_valid_selfhash_cannot_relabel_the_task_commit_or_report(evaluation_factory, field, value):
    lab, package, receipt, publication, fixture = candidate(evaluation_factory)
    altered = dict(publication, **{field:value})
    altered['identity'] = sha256({k:v for k,v in altered.items() if k != 'identity'})
    (fixture['root']/'iteration-candidate.json').write_bytes(canonical(altered))
    with pytest.raises(LabError, match='candidate_binding_mismatch'):
        lab.verify_iteration_candidate(package, receipt)


def test_missing_publication_and_failed_technical_report_cannot_be_ready(evaluation_factory):
    lab, package, receipt, _, fixture = candidate(evaluation_factory, technical_issue=True)
    with pytest.raises(LabError, match='candidate_binding_mismatch'):
        lab.verify_iteration_candidate(package, receipt)
    (fixture['root']/'iteration-candidate.json').unlink()
    with pytest.raises(LabError, match='candidate_publication_invalid'):
        lab.verify_iteration_candidate(package, receipt)


def test_case_coverage_and_semantic_binding_are_explicit(evaluation_factory):
    lab, package, receipt, _, _ = candidate(evaluation_factory)
    other = dict(key='annotation:other', case_id='diagram-long-label', binding={})
    package['feedback'].append(other)
    proof = lab.verify_iteration_candidate(package, receipt)
    assert proof['uncovered_feedback_keys'] == ['annotation:other']
    package['feedback'][0]['binding'] = dict(semantic_input_sha256='f'*64)
    with pytest.raises(LabError, match='candidate_semantics_changed'):
        lab.verify_iteration_candidate(package, receipt)
