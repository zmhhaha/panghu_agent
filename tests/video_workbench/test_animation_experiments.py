"""Developer experiment records bind opinions and media to the actual frozen work.

Fixtures contain explicitly synthetic media; none is evidence of model or native
browser execution. These checks exercise persistence, tampering and stale pages.
"""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from agent_service.profiles.video_animation_experiment import (
    PROFILE, VideoAnimationExperimentProfile, validate_experiment_input)
from video_workbench import animation_experiments as lab


def save_run(root, name, *, role='development', revision='v1', guidance='', status='rendered',
             experiment_id='identity-tracking', capability_release_id=None):
    directory = Path(root) / name
    directory.mkdir(parents=True)
    goal = lab.definition(experiment_id)
    doc = lab.pilot_input(role, revision, guidance, experiment_id=experiment_id,
                         capability_release_id=capability_release_id)
    validate_experiment_input(doc)
    row = dict(schema=lab.RUN_SCHEMA, id=name, experiment_id=goal['id'],
        experiment_identity=goal['identity'], source='agent', input_role=role,
        input_document=doc, input_sha256=lab.digest(doc), label=name, status=status,
        prompt_text=VideoAnimationExperimentProfile().build_prompt({'input_sha256': lab.digest(doc)}),
        provenance=dict(profile=PROFILE, job_id='synthetic-job-' + name, returned_model=None),
        artifacts={}, code_files={'scene.html': '<script>untrustedSource()</script>'},
        strategy=dict(description='synthetic record fixture', tradeoffs='No actual rendering'))
    if release := doc['constraints'].get('capability_release'):
        row['provenance'].update(capability_release_id=release['id'],
                                 capability_release_sha256=release['sha256'])
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


def test_legacy_definition_and_inputs_keep_pre_extension_frozen_hashes():
    # Captured by executing git HEAD's unmodified source before this extension.
    assert lab.definition()['identity'] == 'e68cb71dd7658b6b3cefe4fc8c5079e81f7407b289623b21a54747ee4496c927'
    assert lab.digest(lab.pilot_input()) == 'cbf58039c63a2fb0d3399241ab80aa3a0e9bbb0ef7b6539fcfaf04823936e5c6'
    assert lab.digest(lab.pilot_input('held_out')) == '5cae20ce730e260311d3d28f6ed64ad646bf94e9ab98da4f54b6139bc274bb8e'


def test_catalogue_has_independent_unexecuted_capability_problems(tmp_path):
    experiments, root = store(tmp_path)
    items = experiments.catalogue()['experiments']
    assert [item['id'] for item in items] == [
        'identity-tracking', 'cohort-statistics', 'parameter-geometry', 'event-delay']
    assert all(item['status'] == 'unexecuted' and item['versions'] == [] for item in items)
    assert not list(root.iterdir())


@pytest.mark.parametrize('experiment_id', ['cohort-statistics', 'parameter-geometry', 'event-delay'])
def test_new_definitions_bind_distinct_frozen_role_inputs_and_return_detached_values(experiment_id):
    goal = lab.definition(experiment_id)
    assert lab.definition(experiment_id, goal['identity']) == goal
    assert goal['version'] == '1'
    development = lab.pilot_input(experiment_id=experiment_id, definition_identity=goal['identity'])
    held_out = lab.pilot_input('held_out', experiment_id=experiment_id, definition_identity=goal['identity'])
    validate_experiment_input(development)
    validate_experiment_input(held_out)
    assert development['constraints']['definition_id'] == experiment_id
    assert development['constraints']['definition_version'] == '1'
    assert development['constraints']['semantic']['kind'] == experiment_id
    assert development['constraints']['semantic']['data'] != held_out['constraints']['semantic']['data']
    goal['constraints']['semantic']['data'].clear()
    development['constraints']['semantic']['data'].clear()
    assert lab.definition(experiment_id)['constraints']['semantic']['data']
    with pytest.raises(lab.ExperimentError, match='definition_changed'):
        lab.definition(experiment_id, '0' * 64)


def test_runs_feedback_and_artifacts_are_isolated_by_capability(tmp_path):
    experiments, root = store(tmp_path)
    for experiment_id in ['identity-tracking', 'cohort-statistics', 'parameter-geometry', 'event-delay']:
        save_run(root, experiment_id + '-one', experiment_id=experiment_id)
        save_run(root, experiment_id + '-two', revision='v2', guidance='A separate visual strategy',
                 experiment_id=experiment_id)
        detail = experiments.detail(experiment_id)
        ids = [experiment_id + '-one', experiment_id + '-two']
        assert [row['id'] for row in detail['versions']] == ids
        result = experiments.annotate(experiment_id, preference(detail, ids))
        assert result['experiment_id'] == experiment_id
        assert [row['id'] for row in experiments.detail(experiment_id)['feedback']] == [result['id']]
    assert len(experiments.feedback()) == 4
    assert len(experiments.export_jsonl().splitlines()) == 4
    assert len(experiments.detail('identity-tracking')['versions']) == 2
    with pytest.raises(lab.ExperimentError, match='artifact_not_found'):
        experiments.artifact('cohort-statistics', 'parameter-geometry-one', 'output.mp4')
    with pytest.raises(lab.ExperimentError, match='version_invalid'):
        detail = experiments.detail('cohort-statistics')
        body = preference(detail, ['cohort-statistics-one', 'cohort-statistics-two'])
        body['version_ids'] = ['cohort-statistics-one', 'event-delay-one']
        experiments.annotate('cohort-statistics', body)


@pytest.mark.parametrize('tamper', ['definition', 'dataset', 'version', 'experiment'])
def test_new_run_reconstruction_rejects_cross_capability_and_input_tampering(tmp_path, tamper):
    experiments, root = store(tmp_path)
    row = save_run(root, 'statistics-reference', experiment_id='cohort-statistics')
    if tamper == 'definition':
        row['experiment_identity'] = lab.definition('event-delay')['identity']
    elif tamper == 'dataset':
        row['input_document']['constraints']['semantic']['data']['threshold'] += 1
    elif tamper == 'version':
        row['input_document']['constraints']['definition_version'] = '2'
    else:
        row['experiment_id'] = 'event-delay'
        row['experiment_identity'] = lab.definition('event-delay')['identity']
    row['input_sha256'] = lab.digest(row['input_document'])
    rewrite(root / 'statistics-reference', row)
    assert experiments.detail('cohort-statistics')['versions'] == []
    assert experiments.detail('event-delay')['versions'] == []
    assert experiments.runs()[1][0]['id'] == 'statistics-reference'


def test_published_v1_run_rebuilds_after_a_new_current_definition_is_registered(tmp_path, monkeypatch):
    from video_workbench import animation_experiment_definitions as registry
    experiments, root = store(tmp_path)
    old = save_run(root, 'statistics-v1', experiment_id='cohort-statistics')
    prior = deepcopy(registry._REGISTRY['cohort-statistics'])
    second = deepcopy(prior['1'])
    second['definition'].update(version='2', goal='A later immutable capability goal')
    for value in second['constraints'].values():
        value['definition_version'] = '2'
        value['semantic']['data']['threshold'] = 7
    second['definition']['constraints'] = deepcopy(second['constraints']['development'])
    monkeypatch.setitem(registry._REGISTRY, 'cohort-statistics', dict(prior, **{'2': second}))
    monkeypatch.setitem(registry.CURRENT_VERSIONS, 'cohort-statistics', '2')
    assert lab.definition('cohort-statistics')['version'] == '2'
    assert lab.definition('cohort-statistics', old['experiment_identity'])['version'] == '1'
    assert lab.read_run(root / 'statistics-v1') == old
    assert experiments.detail('cohort-statistics')['versions'][0]['experiment_identity'] == old['experiment_identity']


def test_same_strategy_release_with_different_local_guidance_keeps_comparison_target(tmp_path):
    from video_workbench.animation_capabilities import DEFAULT_RELEASE_ID
    experiments, root = store(tmp_path)
    before = save_run(root, 'release-before', guidance='Separate the trajectories spatially.',
                      capability_release_id=DEFAULT_RELEASE_ID)
    after = save_run(root, 'release-after', revision='v2', guidance='Separate the movements in time.',
                     capability_release_id=DEFAULT_RELEASE_ID)
    detail = experiments.detail('identity-tracking')
    versions = {item['id']: item for item in detail['versions']}
    assert versions['release-before']['input_identity'] != versions['release-after']['input_identity']
    assert (versions['release-before']['comparison_input_identity'] ==
            versions['release-after']['comparison_input_identity'])
    assert before['input_document']['constraints']['capability_local_guidance'] != after['input_document']['constraints']['capability_local_guidance']
    # Serving the comparison must not normalize away either frozen input.
    assert lab.read_run(root / 'release-before') == before
    assert lab.read_run(root / 'release-after') == after
    assert experiments.annotate('identity-tracking',
        preference(detail, ['release-before', 'release-after']))['preference'] == 'right'


def test_comparison_identity_preserves_strategy_release_and_unbound_context(tmp_path, monkeypatch):
    from video_workbench import animation_capabilities as releases
    experiments, root = store(tmp_path)
    alternate = deepcopy(releases.capability_release())
    alternate.pop('sha256')
    alternate.update(id='test-motion-v2', revision=2)
    alternate['rules'].append('A distinct test strategy with an independently frozen release identity.')
    alternate['sha256'] = releases.digest(alternate)
    monkeypatch.setitem(releases._RELEASES, alternate['id'], alternate)
    for name, release_id in [('original-release', releases.DEFAULT_RELEASE_ID),
                             ('alternate-release', alternate['id']), ('unbound-run', None)]:
        save_run(root, name, guidance='The same local direction.', capability_release_id=release_id)
    versions = {item['id']: item for item in experiments.detail('identity-tracking')['versions']}
    assert len(versions) == 3
    assert len({item['comparison_input_identity'] for item in versions.values()}) == 3
    assert versions['alternate-release']['input_document']['constraints']['capability_release'] == alternate
    assert versions['original-release']['input_document']['constraints']['capability_release']['id'] == releases.DEFAULT_RELEASE_ID
