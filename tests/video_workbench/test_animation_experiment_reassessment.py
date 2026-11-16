"""Verifier-only custody tests with synthetic bytes, never native media proof.

Real DOM visibility and glyph association are tested separately in Chromium.
Here a render transport double exercises immutable-source/runtime/media gates.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from video_workbench import animation_experiment_reassessment as review
from video_workbench import animation_experiments as lab


IMAGE = 'registry.example/renderer:latest@sha256:' + 'a' * 64
IMAGE_ID = 'sha256:' + 'b' * 64


@pytest.fixture
def archived(tmp_path):
    root = tmp_path / 'identity-development-v1'
    (root / 'bundle').mkdir(parents=True)
    (root / 'frames').mkdir()
    (root / 'render' / 'work' / 'frames').mkdir(parents=True)
    media = b'0000ftypSYNTHETIC-REASSESSMENT-TRANSPORT'
    image = b'\xff\xd8SYNTHETIC-TEST-FRAME\xff\xd9'
    document = lab.pilot_input()
    sources = {'scene.html': '<!doctype html><p>synthetic custody only</p>'}
    (root / 'bundle' / 'scene.html').write_text(sources['scene.html'], encoding='utf-8')
    frames, artifacts = [], {}
    for index in range(193):
        name = 'frame-%06d.jpg' % index if index < 192 else 'reverse-initial.jpg'
        (root / 'render' / 'work' / 'frames' / name).write_bytes(image)
        frames.append(dict(name=name, sha256=lab.digest(image), bytes=len(image)))
        if index < 192:
            published = 'frames/%06d.jpg' % index
            (root / published).write_bytes(image)
            artifacts[published] = dict(size=len(image), sha256=lab.digest(image), mime='image/jpeg')
    report = dict(schema='video.animation-experiment-report.v1', input_sha256=lab.digest(document),
        agent_job_id='synthetic-archived-job', renderer=dict(status='rendered', image=IMAGE,
            actual_image_id=IMAGE_ID, receipt=dict(decode_verified=True, frame_count=192, frames=frames,
                output_sha256=lab.digest(media), runtime={'schema': 'synthetic-frozen-runtime'},
                host_sha256='c' * 64),
            technical=dict(status='failed', issues=['object_identity_not_visible'])))
    report_raw = lab.canonical(report)
    (root / 'report.json').write_bytes(report_raw)
    (root / 'output.mp4').write_bytes(media)
    artifacts.update({'report.json': dict(size=len(report_raw), sha256=lab.digest(report_raw), mime='application/json'),
        'output.mp4': dict(size=len(media), sha256=lab.digest(media), mime='video/mp4')})
    row = lab.signed(dict(schema=lab.RUN_SCHEMA, id=root.name, experiment_id=lab.definition()['id'],
        experiment_identity=lab.definition()['identity'], source='agent', input_role='development',
        input_document=document, input_sha256=lab.digest(document), label='synthetic archive',
        prompt_text='synthetic custody only', status='rendered', provenance={'job_id': 'synthetic-archived-job'},
        artifacts=artifacts, code_files=sources, strategy={}))
    (root / 'experiment-run.json').write_bytes(lab.canonical(row))
    return root


def tree_hashes(root):
    return {str(path.relative_to(root)): lab.digest(path.read_bytes()) for path in root.rglob('*') if path.is_file()}


def install(monkeypatch, archived, change=None):
    row = lab.read_run(archived)
    original = json.loads((archived / 'report.json').read_bytes())['renderer']
    calls = []

    def render(bundle, destination, *, image, input_document, assets_dir):
        calls.append(dict(bundle=bundle, image=image, input_document=input_document, assets_dir=assets_dir))
        if change == 'render_failure':
            raise review.renderer.RenderExecutionError(dict(status='failed', error='synthetic_failure'))
        (destination / 'work' / 'frames').mkdir(parents=True)
        (destination / 'input').mkdir()
        sources = {name: lab.digest(content.encode()) for name, content in row['code_files'].items()}
        request = dict(files_sha256=sources, assets_sha256=input_document['assets'],
                       input_sha256=lab.digest(input_document))
        if change == 'source':
            request['files_sha256']['scene.html'] = '0' * 64
        elif change == 'input':
            request['input_sha256'] = '0' * 64
        (destination / 'input' / 'request.json').write_bytes(lab.canonical(request))
        frames = deepcopy(original['receipt']['frames'])
        for index, frame in enumerate(frames):
            raw = (archived / 'render' / 'work' / 'frames' / frame['name']).read_bytes()
            if change == 'actual_frame' and index == 50:
                raw += b'CHANGED'
            (destination / 'work' / 'frames' / frame['name']).write_bytes(raw)
        if change == 'frame':
            frames[50]['sha256'] = '0' * 64
        elif change == 'reverse':
            frames[-1]['sha256'] = '0' * 64
        samples = []
        for index in range(192):
            time = index / 24
            progress = max(0, min(1, (time - 1) / 5.5))
            objects = []
            for target in input_document['constraints']['objects']:
                x = target['initial'][0] + (target['final'][0] - target['initial'][0]) * progress
                y = target['initial'][1]
                objects.append(dict(id=target['id'], text=target['label'], visible=True,
                    bounds=dict(x=x-target['width']/2, y=y-target['height']/2,
                                width=target['width'], height=target['height']),
                    label_observation={'verifier_revision': review.VERIFIER_REVISION, 'status': 'measured'}))
            samples.append(dict(frame_index=index, time=time, objects=objects, frame_sha256=frames[index]['sha256']))
        if change == 'observer':
            del samples[0]['objects'][0]['label_observation']
        observations = dict(schema='video.animation-experiment-observations.v1', samples=samples,
            reverse_initial_objects=deepcopy(samples[0]['objects']), reverse_initial_sha256=frames[0]['sha256'])
        observed_raw = lab.canonical(observations)
        (destination / 'work' / 'observations.json').write_bytes(observed_raw)
        media = (archived / 'output.mp4').read_bytes() + (b'CHANGED' if change == 'media' else b'')
        (destination / 'work' / 'output.mp4').write_bytes(media)
        receipt = dict(frames=frames, runtime=original['receipt']['runtime'],
            host_sha256=lab.digest(review.renderer.HOST.read_bytes()), output_sha256=lab.digest(media),
            observations_sha256=lab.digest(observed_raw))
        outcome = dict(status='rendered', image=image, actual_image_id=IMAGE_ID, receipt=receipt,
            technical=review.renderer.validate_observations(observations, input_document['constraints']))
        if change == 'runtime':
            receipt['runtime'] = {'schema': 'different runtime'}
        elif change == 'image_id':
            outcome['actual_image_id'] = 'sha256:' + 'd' * 64
        elif change == 'observation_hash':
            receipt['observations_sha256'] = '0' * 64
        elif change == 'technical':
            outcome['technical'] = dict(status='passed', issues=[], aesthetic_approval=True)
        elif change == 'original_mutated':
            (archived / 'report.json').write_bytes((archived / 'report.json').read_bytes() + b'\n')
        return outcome

    monkeypatch.setattr(review.renderer, 'render_scene', render)
    return calls


def test_separate_signed_reassessment_preserves_every_original_byte_and_uses_no_agent(tmp_path, archived, monkeypatch):
    before = tree_hashes(archived)
    original = lab.read_run(archived)
    calls = install(monkeypatch, archived)
    output = tmp_path / 'new-verifier-only-output'
    result = review.reassess(archived, output, renderer_image=IMAGE)
    assert tree_hashes(archived) == before
    assert json.loads((output / 'reassessment.json').read_bytes()) == result
    assert result['schema'] == review.SCHEMA and result['verifier_revision'] == 'svg-label-geometry-v2'
    assert result['identity'] == lab.digest({k: v for k, v in result.items() if k != 'identity'})
    assert result['run_identity'] == original['identity']
    assert result['original_report_sha256'] == original['artifacts']['report.json']['sha256']
    assert result['media_sha256'] == original['artifacts']['output.mp4']['sha256']
    assert result['technical']['status'] == 'passed' and not result['technical']['aesthetic_approval']
    assert result['frame_hashes_match'] is result['media_hash_matches'] is True
    assert result['source_code_sha256'] == {'scene.html': lab.digest(original['code_files']['scene.html'].encode())}
    assert type(result['evaluated_at']) is float
    assert len(calls) == 1 and calls[0]['image'] == IMAGE
    with pytest.raises(FileExistsError):
        review.reassess(archived, output, renderer_image=IMAGE)
    assert len(calls) == 1 and tree_hashes(archived) == before


@pytest.mark.parametrize('change', ['frame', 'actual_frame', 'reverse', 'media', 'runtime', 'image_id',
    'source', 'input', 'observation_hash', 'observer', 'technical', 'original_mutated', 'render_failure'])
def test_drift_or_incomplete_evidence_never_certifies_or_overwrites_original_report(tmp_path, archived, monkeypatch, change):
    before = tree_hashes(archived)
    calls = install(monkeypatch, archived, change)
    output = tmp_path / 'failed-new-verifier'
    with pytest.raises((lab.ExperimentError, review.renderer.RenderExecutionError)):
        review.reassess(archived, output, renderer_image=IMAGE)
    assert len(calls) == 1 and not (output / 'reassessment.json').exists()
    failure = json.loads((output / 'reassessment-failure.json').read_bytes())
    assert failure['status'] == 'failed' and failure['agent_calls'] == 0
    assert failure['original_records_rewritten'] is False
    if change != 'original_mutated':
        assert tree_hashes(archived) == before
    with pytest.raises(FileExistsError):
        review.reassess(archived, output, renderer_image=IMAGE)
    assert len(calls) == 1


def test_original_source_drift_or_different_renderer_refused_before_render(tmp_path, archived, monkeypatch):
    calls = install(monkeypatch, archived)
    with pytest.raises(lab.ExperimentError, match='original_runtime_missing_or_changed'):
        review.reassess(archived, tmp_path / 'wrong-image', renderer_image=IMAGE[:-1] + 'b')
    (archived / 'bundle' / 'scene.html').write_text('changed source', encoding='utf-8')
    with pytest.raises(lab.ExperimentError, match='original_source_changed'):
        review.reassess(archived, tmp_path / 'wrong-source', renderer_image=IMAGE)
    assert calls == []


def test_reassessment_cannot_place_outputs_inside_original_archive(tmp_path, archived, monkeypatch):
    calls = install(monkeypatch, archived)
    before = tree_hashes(archived)
    with pytest.raises(lab.ExperimentError, match='output_directory_invalid'):
        review.reassess(archived, archived / 'new-assessment', renderer_image=IMAGE)
    assert calls == [] and tree_hashes(archived) == before
