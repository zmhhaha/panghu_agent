"""Frozen adapter provenance with synthetic observations/JPEG headers only.

No browser, renderer, real model transport or TTS is invoked by these tests.
The synthetic images are dimension-bearing sentinels, never visual evidence.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from video_workbench import animation_lab_evidence as evidence
from video_workbench.animation_lab_evaluate import collect, load_run, model_review, prepare
from video_workbench.animation_lab_review import REVIEW_SCHEMA
from video_workbench.motion import canonical, sha256
from test_animation_lab_scenarios_review import fixture as synthetic_scenario_observations


RUNTIME = dict(schema='video.motion-runtime.v1', hyperframes='0.8.149',
    hyperframes_source='synthetic-frozen-adapter-test', node='synthetic', browser='synthetic',
    ffmpeg='synthetic', gsap='3.14.2', gsap_sha256='a'*64, font_sha256='b'*64,
    npm_lock_sha256='c'*64, dpkg_sha256='d'*64, image_digest='sha256:'+'e'*64)
ADAPTER_BINDINGS = {'lab_observe.mjs': 'capture_adapter_sha256', 'observe.mjs': 'observer_sha256',
                    'runtime.mjs': 'runtime_collector_sha256', 'engine_observe.mjs': 'engine_observer_sha256'}


def synthetic_jpeg(time):
    # A bounded SOF0 header suffices for the existing dimension reader. This
    # intentionally is not a screenshot or a claim of visual render quality.
    return (b'\xff\xd8\xff\xc0\x00\x11\x08'+(720).to_bytes(2, 'big')
            +(1280).to_bytes(2, 'big')+b'\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00'
            +str(time).encode('ascii')+b'\xff\xd9')


def write_synthetic_capture(root, bundle, *, coordinator_outcome):
    clip = bundle['clips'][0]
    directory = root/'inputs'/bundle['variant_id']/clip['id']
    request_raw = (directory/'capture-request.json').read_bytes()
    request = json.loads(request_raw)
    (directory/'observations').mkdir()
    timeline = clip['timeline']

    # Reuse the explicitly synthetic cross-domain DOM contract fixture. These
    # observations are independent of installed capture adapters and are never
    # described as rendered evidence. The frozen bundle supplies the timeline.
    _, _, observations = synthetic_scenario_observations(bundle['case_id'], bundle['variant_id'])
    observations['timeline_sha256'] = sha256(timeline)

    def bind_sample(sample, name):
        raw = synthetic_jpeg(sample['time'])
        (directory/'observations'/f'{name}.jpg').write_bytes(raw)
        sample['frame_sha256'] = sha256(raw)

    for window in observations['windows']:
        for name in ('before', 'middle', 'after'):
            bind_sample(window[name], f"a{window['ordinal']}-{name}")
    for key, name in [('initial', 'seek-first'), ('last', 'seek-last'),
                      ('repeated_initial', 'seek-repeat')]:
        bind_sample(observations[key], name)
    observations['reverse_seek'] = dict(
        first_sha256=observations['initial']['frame_sha256'],
        last_sha256=observations['last']['frame_sha256'],
        repeated_first_sha256=observations['repeated_initial']['frame_sha256'],
        repeat_state_equal=True)
    (directory/'observations.json').write_bytes(canonical(observations))
    frames = [dict(name=path.name, sha256=sha256(path.read_bytes()), bytes=path.stat().st_size)
              for path in sorted((directory/'observations').iterdir())]
    receipt = dict(schema='video.animation-lab-capture-receipt.v1', capture_identity=request['capture_identity'],
        request_sha256=sha256(request_raw), runtime=deepcopy(RUNTIME), image_digest_source='coordinator_pinned_not_self_measured',
        observations_sha256=sha256(canonical(observations)), frames=frames, frame_count=len(frames),
        agent_calls=0, tts_calls=0, production_writes=0, output='sampled_browser_observations_not_mp4',
        engine_assets=request['engine_assets'], **{key: request[key] for key in ADAPTER_BINDINGS.values()
                                                  if key != 'engine_observer_sha256'})
    (directory/'capture-receipt.json').write_bytes(canonical(receipt))
    if coordinator_outcome:
        adapters = {name: sha256((root/'overlay'/name).read_bytes())
                    for name in [*ADAPTER_BINDINGS, 'contract.mjs']}
        outcome = dict(schema='video.animation-lab-execution-outcome.v1', status='captured',
            request_sha256=sha256(request_raw), capture_identity=request['capture_identity'], adapters=adapters)
        (directory/'capture-outcome.json').write_bytes(canonical(outcome))
    return directory


def frozen_run(tmp_path, *, coordinator_outcome=False):
    root = tmp_path/'frozen-run'
    prepare(root, deepcopy(RUNTIME), case_id='chart-compute-backlog')
    _, bundles = load_run(root)
    directories = [write_synthetic_capture(root, bundle, coordinator_outcome=coordinator_outcome)
                   for bundle in bundles]
    return root, bundles, directories


def upgrade_live_sources(tmp_path, monkeypatch, *, change_contract=False):
    live = tmp_path/'current-package'
    live.mkdir()
    for name in [*ADAPTER_BINDINGS, 'contract.mjs']:
        raw = (evidence._HERE/name).read_bytes()
        if name != 'contract.mjs' or change_contract:
            raw += b'\n// upgraded installed source, not part of old frozen run\n'
        (live/name).write_bytes(raw)
    monkeypatch.setattr(evidence, '_HERE', live)
    return live


class SyntheticModel:
    calls = 0

    def __init__(self, request):
        self.request = request

    def review(self, _messages):
        self.calls += 1
        opinion = dict(schema=REVIEW_SCHEMA, request_identity=self.request['identity'], preference='inconclusive',
            comparison_reason='Synthetic provenance contract only; no aesthetic judgement',
            comparison_frame_ids=[row['id'] for row in self.request['public']['frames']], issues=[],
            suggestions=[dict(issue_id=None, kind='no_change', parameters={}, reason='Synthetic test')],
            limitations=['Dimension-bearing sentinel bytes, not rendered media'])
        return canonical(opinion).decode('utf-8'), dict(post_attempts=1, automatic_retries=0)


@pytest.mark.parametrize('coordinator_outcome', [False, True])
def test_upgraded_current_adapters_do_not_strand_frozen_collect_or_first_model_review(tmp_path, monkeypatch, coordinator_outcome):
    root, bundles, directories = frozen_run(tmp_path, coordinator_outcome=coordinator_outcome)
    original = [evidence.review_capture(bundle, 'scene-1', directory, RUNTIME)
                for bundle,directory in zip(bundles,directories)]
    old_request = json.loads((directories[0]/'capture-request.json').read_bytes())
    upgrade_live_sources(tmp_path, monkeypatch, change_contract=coordinator_outcome)
    assert evidence.capture_request(bundles[0], 'scene-1', RUNTIME)['capture_identity'] != old_request['capture_identity']
    assert [evidence.review_capture(bundle, 'scene-1', directory, RUNTIME)
            for bundle,directory in zip(bundles,directories)] == original
    technical, request = collect(root)
    assert technical['status'] == 'passed'
    model = SyntheticModel(request)
    review = model_review(root, model=model)
    assert model.calls == 1 and review['status'] == 'suggestion_only'
    assert not review['production_adopted']


@pytest.mark.parametrize('name', [*ADAPTER_BINDINGS, 'contract.mjs'])
@pytest.mark.parametrize('coordinator_outcome', [False, True])
def test_changed_frozen_overlay_bytes_still_fail_closed(tmp_path, name, coordinator_outcome):
    root, bundles, directories = frozen_run(tmp_path, coordinator_outcome=coordinator_outcome)
    (root/'overlay'/name).write_bytes(b'changed frozen source')
    with pytest.raises(ValueError, match='animation_lab_capture_frozen_'):
        evidence.review_capture(bundles[0], 'scene-1', directories[0], RUNTIME)


@pytest.mark.parametrize('target,key', [
    ('capture-request.json', 'capture_adapter_sha256'),
    ('capture-request.json', 'capture_identity'),
    ('capture-receipt.json', 'request_sha256'),
    ('capture-receipt.json', 'capture_identity'),
    ('capture-receipt.json', 'capture_adapter_sha256'),
    ('capture-receipt.json', 'observer_sha256'),
    ('capture-receipt.json', 'runtime_collector_sha256'),
])
def test_request_and_receipt_cannot_relabel_the_frozen_measurements(tmp_path, target, key):
    _, bundles, directories = frozen_run(tmp_path)
    path = directories[0]/target
    value = json.loads(path.read_bytes())
    value[key] = 'f'*64
    path.write_bytes(canonical(value))
    with pytest.raises(ValueError, match='animation_lab_capture_'):
        evidence.review_capture(bundles[0], 'scene-1', directories[0], RUNTIME)


@pytest.mark.parametrize('key', ['capture_identity', 'request_sha256', 'adapters', 'status'])
def test_coordinator_outcome_must_bind_the_same_request_and_actual_overlay(tmp_path, key):
    _, bundles, directories = frozen_run(tmp_path, coordinator_outcome=True)
    path = directories[0]/'capture-outcome.json'
    value = json.loads(path.read_bytes())
    value[key] = {'contract.mjs': 'f'*64} if key == 'adapters' else 'failed' if key == 'status' else 'f'*64
    path.write_bytes(canonical(value))
    with pytest.raises(ValueError, match='frozen_outcome_mismatch'):
        evidence.review_capture(bundles[0], 'scene-1', directories[0], RUNTIME)


def test_rehashed_request_and_matching_receipt_cannot_bypass_the_frozen_run_entry(tmp_path):
    root, bundles, directories = frozen_run(tmp_path)
    path = directories[0]/'capture-request.json'
    value = json.loads(path.read_bytes())
    changed = (root/'overlay'/'lab_observe.mjs')
    changed.write_bytes(b'replaced adapter together with self-consistent asserted hashes')
    value['capture_adapter_sha256'] = sha256(changed.read_bytes())
    value['capture_identity'] = sha256({key:row for key,row in value.items() if key != 'capture_identity'})
    path.write_bytes(canonical(value))
    receipt_path = directories[0]/'capture-receipt.json'
    receipt = json.loads(receipt_path.read_bytes())
    receipt.update(capture_adapter_sha256=value['capture_adapter_sha256'],
                   capture_identity=value['capture_identity'], request_sha256=sha256(path.read_bytes()))
    receipt_path.write_bytes(canonical(receipt))
    with pytest.raises(ValueError, match='frozen_adapter_mismatch'):
        evidence.review_capture(bundles[0], 'scene-1', directories[0], RUNTIME)


def test_missing_frozen_overlay_never_falls_back_to_current_matching_code(tmp_path):
    root, bundles, directories = frozen_run(tmp_path)
    (root/'overlay').rename(root/'moved-overlay')
    with pytest.raises(ValueError, match='frozen_overlay_invalid'):
        evidence.review_capture(bundles[0], 'scene-1', directories[0], RUNTIME)


def test_legacy_contract_without_coordinator_measurement_cannot_accept_unknown_old_bytes(tmp_path, monkeypatch):
    _, bundles, directories = frozen_run(tmp_path)
    upgrade_live_sources(tmp_path, monkeypatch, change_contract=True)
    with pytest.raises(ValueError, match='frozen_contract_mismatch'):
        evidence.review_capture(bundles[0], 'scene-1', directories[0], RUNTIME)
