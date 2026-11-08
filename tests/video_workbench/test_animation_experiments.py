"""Developer experiment records bind opinions and media to the actual frozen work.

Fixtures contain explicitly synthetic media; none is evidence of model or native
browser execution. These checks exercise persistence, tampering and stale pages.
"""
import json
from pathlib import Path

import pytest

from agent_service.profiles.video_animation_experiment import (
    PROFILE, VideoAnimationExperimentProfile, validate_experiment_input)
from video_workbench import animation_experiments as lab


def save_run(root, name, *, role='development', revision='v1', guidance='', status='rendered'):
    directory = Path(root) / name
    directory.mkdir(parents=True)
    doc = lab.pilot_input(role, revision, guidance)
    validate_experiment_input(doc)
    row = dict(schema=lab.RUN_SCHEMA, id=name, experiment_id=lab.definition()['id'],
        experiment_identity=lab.definition()['identity'], source='agent', input_role=role,
        input_document=doc, input_sha256=lab.digest(doc), label=name, status=status,
        prompt_text=VideoAnimationExperimentProfile().build_prompt({'input_sha256': lab.digest(doc)}),
        provenance=dict(profile=PROFILE, job_id='synthetic-job-' + name, returned_model=None),
        artifacts={}, code_files={'scene.html': '<script>untrustedSource()</script>'},
        strategy=dict(description='synthetic record fixture', tradeoffs='No actual rendering'))
    if status == 'rendered':
        report = dict(schema='video.animation-experiment-report.v1', input_sha256=row['input_sha256'],
            agent_job_id=row['provenance']['job_id'],
            renderer=dict(technical=dict(status='passed', scope='synthetic test fixture only')))
        files = {'output.mp4': b'0000ftypSYNTHETIC-UNIT-TEST-NOT-ENCODED-VIDEO',
                 'report.json': lab.canonical(report)}
        for name, raw in files.items():
            (directory / name).write_bytes(raw)
            row['artifacts'][name] = dict(size=len(raw), sha256=lab.digest(raw),
                mime='video/mp4' if name.endswith('.mp4') else 'application/json')
    return rewrite(directory, row)


def rewrite(directory, row):
    row = lab.signed({key: value for key, value in row.items() if key != 'identity'})
    (directory / 'experiment-run.json').write_bytes(lab.canonical(row))
    return row


def store(tmp_path):
    root = tmp_path / 'runs'
    root.mkdir()
    return lab.ExperimentStore(tmp_path / 'feedback.db', root), root


def preference(detail, ids, comment=''):
    versions = {row['id']: row for row in detail['versions']}
    return dict(experiment_identity=detail['identity'], kind='preference', version_ids=ids,
        version_identities={identity: versions[identity]['identity'] for identity in ids},
        comment=comment, start_seconds=1, end_seconds=6.5, preference='right')


def test_definitions_without_execution_have_no_versions_or_playable_result(tmp_path):
    experiments, root = store(tmp_path)
    before = list(root.iterdir())
    detail = experiments.catalogue()['experiments'][0]
    assert detail['source'] == 'definition' and detail['status'] == 'unexecuted'
    assert detail['versions'] == detail['comparisons'] == detail['feedback'] == []
    assert list(root.iterdir()) == before
    assert all('narration' not in item for item in detail['constraints']['objects'])


def test_generated_sources_remain_inspection_text_without_playable_media(tmp_path):
    experiments, root = store(tmp_path)
    save_run(root, 'unrendered', status='generated')
    detail = experiments.detail('identity-tracking')
    version = detail['versions'][0]
    assert version['status'] == 'generated' and version['media_url'] is None
    assert version['aesthetic_status'] == 'unreviewed'
    assert version['code_files']['scene.html'] == '<script>untrustedSource()</script>'
    with pytest.raises(lab.ExperimentError, match='artifact_not_found'):
        experiments.artifact('identity-tracking', 'unrendered', 'scene.html')
    row = json.loads((root / 'unrendered' / 'experiment-run.json').read_bytes())
    row['status'] = 'rendered'
    rewrite(root / 'unrendered', row)
    current = experiments.detail('identity-tracking')
    assert current['versions'] == [] and current['rejected_runs'][0]['id'] == 'unrendered'


def test_guidance_and_exact_input_are_available_with_the_actual_prompt(tmp_path):
    experiments, root = store(tmp_path)
    guidance = 'Before crossing, separate the objects into distinct motion lanes.'
    save_run(root, 'revision-two', revision='v2', guidance=guidance)
    version = experiments.detail('identity-tracking')['versions'][0]
    assert guidance in version['prompt_text']
    assert version['input_document']['guidance'] == guidance
    assert version['input_identity'] == lab.digest(version['input_document'])
    assert version['provenance']['returned_model'] is None


@pytest.mark.parametrize('tamper', ['unsigned_metadata', 'input', 'media', 'report_input', 'report_job'])
def test_record_input_and_resource_binding_reject_tampering(tmp_path, tamper):
    experiments, root = store(tmp_path)
    row = save_run(root, 'reference')
    directory = root / 'reference'
    if tamper == 'unsigned_metadata':
        row['label'] = 'silently replaced work'
        (directory / 'experiment-run.json').write_bytes(lab.canonical(row))
    elif tamper == 'input':
        row['input_document']['constraints']['objects'][0]['initial'][0] += 40
        row['input_sha256'] = lab.digest(row['input_document'])
        rewrite(directory, row)
    elif tamper == 'media':
        (directory / 'output.mp4').write_bytes(b'changed-after-publication')
    else:
        report = json.loads((directory / 'report.json').read_bytes())
        report['input_sha256' if tamper == 'report_input' else 'agent_job_id'] = 'other-run'
        raw = lab.canonical(report)
        (directory / 'report.json').write_bytes(raw)
        row['artifacts']['report.json'].update(size=len(raw), sha256=lab.digest(raw))
        rewrite(directory, row)
    current = experiments.detail('identity-tracking')
    assert current['versions'] == []
    assert current['rejected_runs'][0]['id'] == 'reference'


def test_bad_publication_is_quarantined_without_hiding_valid_work(tmp_path):
    experiments, root = store(tmp_path)
    save_run(root, 'valid')
    invalid = root / 'invalid'
    invalid.mkdir()
    (invalid / 'experiment-run.json').write_text('{not json', encoding='utf-8')
    current = experiments.detail('identity-tracking')
    assert [version['id'] for version in current['versions']] == ['valid']
    assert [entry['id'] for entry in current['rejected_runs']] == ['invalid']


@pytest.mark.parametrize('name', ['../secret.json', '/report.json', 'scene.html',
    'frames/../../secret.jpg', 'report.json?token=x', 'C:\\secret.json'])
def test_only_declared_media_and_report_paths_are_served(tmp_path, name):
    experiments, root = store(tmp_path)
    save_run(root, 'reference')
    with pytest.raises(lab.ExperimentError):
        experiments.artifact('identity-tracking', 'reference', name)


def test_preferences_allow_empty_comment_and_persist_exact_version_identities(tmp_path):
    experiments, root = store(tmp_path)
    save_run(root, 'reference')
    save_run(root, 'candidate', revision='v2', guidance='Try a different temporal arrangement.')
    detail = experiments.detail('identity-tracking')
    body = preference(detail, ['reference', 'candidate'])
    result = experiments.annotate('identity-tracking', body, actor='developer-test')
    reopened = lab.ExperimentStore(experiments.path, root)
    assert reopened.feedback()[0] == result
    assert result['comment'] == '' and result['version_identities'] == body['version_identities']
    exported = [json.loads(line) for line in reopened.export_jsonl().splitlines()]
    assert exported[0]['document']['id'] == result['id']


def test_stale_browser_version_identity_is_rejected_without_writing_feedback(tmp_path):
    experiments, root = store(tmp_path)
    save_run(root, 'reference')
    row = save_run(root, 'candidate', revision='v2', guidance='Direction one')
    body = preference(experiments.detail('identity-tracking'), ['reference', 'candidate'])
    row['input_document']['guidance'] = 'Direction two, producing a different frozen work'
    row['input_sha256'] = lab.digest(row['input_document'])
    report = json.loads((root / 'candidate' / 'report.json').read_bytes())
    report['input_sha256'] = row['input_sha256']
    raw = lab.canonical(report)
    (root / 'candidate' / 'report.json').write_bytes(raw)
    row['artifacts']['report.json'].update(size=len(raw), sha256=lab.digest(raw))
    rewrite(root / 'candidate', row)
    with pytest.raises(lab.ExperimentError, match='version_changed'):
        experiments.annotate('identity-tracking', body)
    assert experiments.feedback() == []


def test_comparison_cannot_mix_development_and_held_out_inputs(tmp_path):
    experiments, root = store(tmp_path)
    save_run(root, 'reference')
    save_run(root, 'other-input', role='held_out', revision='v2', guidance='Candidate')
    body = preference(experiments.detail('identity-tracking'), ['reference', 'other-input'])
    with pytest.raises(lab.ExperimentError, match='comparison_invalid'):
        experiments.annotate('identity-tracking', body)
    assert experiments.feedback() == []


@pytest.mark.parametrize('start,end', [(True, 1), (0, float('nan')), (0, float('inf')), (-1, 1), (2, 1), (1, 9)])
def test_feedback_rejects_invalid_or_out_of_range_time_without_partial_rows(tmp_path, start, end):
    experiments, root = store(tmp_path)
    save_run(root, 'reference')
    save_run(root, 'candidate', revision='v2', guidance='Candidate')
    body = preference(experiments.detail('identity-tracking'), ['reference', 'candidate'])
    body.update(start_seconds=start, end_seconds=end)
    with pytest.raises(lab.ExperimentError, match='feedback_time_invalid'):
        experiments.annotate('identity-tracking', body)
    assert experiments.feedback() == []
