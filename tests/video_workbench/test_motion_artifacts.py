import io
import json
import os
import tarfile

import pytest

from video_workbench.motion_renderer.experiment import collect_artifacts


def summary(status='pending_validation'):
    return dict(schema='video.motion-experiment.v1', wall_seconds=43, stages={'render': 0, 'observe': 0}, status=status)


def unpack(raw):
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        return {m.name: archive.extractfile(m).read() for m in archive.getmembers()}


def test_collects_jpeg_and_full_dom_observations(tmp_path):
    (tmp_path / 'observations').mkdir()
    (tmp_path / 'observations/a0-middle.jpg').write_bytes(b'JPEG frame evidence')
    (tmp_path / 'observations.json').write_text(json.dumps({'windows': [{'all_elements': ['a', 'b', 'c']}]}))
    (tmp_path / 'output.mp4').write_bytes(b'mp4 evidence pending validation')
    raw, result = collect_artifacts(tmp_path, summary())
    entries = unpack(raw)
    assert result['status'] == 'pending_validation'
    assert entries['observations/a0-middle.jpg'] == b'JPEG frame evidence'
    assert json.loads(entries['observations.json'])['windows'][0]['all_elements'] == ['a', 'b', 'c']


@pytest.mark.parametrize('budget', ['compressed', 'uncompressed'])
def test_budget_failure_emits_bounded_honest_diagnostics_without_partial_video(tmp_path, budget):
    (tmp_path / 'output.mp4').write_bytes(os.urandom(8192))
    (tmp_path / 'render-receipt.json').write_text(json.dumps({'status': 'encoded_pending_validation'}))
    (tmp_path / 'observe-receipt.json').write_text(json.dumps({'status': 'observed_pending_validation'}))
    (tmp_path / 'render.log').write_bytes(b'frame-capture completed\n')
    limits = dict(compressed_limit=2048, uncompressed_limit=32768 if budget == 'compressed' else 4096)
    raw, result = collect_artifacts(tmp_path, summary(), **limits)
    assert len(raw) <= limits['compressed_limit']
    entries = unpack(raw)
    failure = json.loads(entries['experiment.json'])
    assert failure == result
    assert failure['status'] == 'artifact_budget_exceeded'
    assert failure['prior_stage_status'] == 'pending_validation'
    assert failure['artifact_failure']['reason'] == budget + '_limit'
    assert failure['stages'] == {'render': 0, 'observe': 0}
    assert failure['partial_media_included'] is False
    assert not any(name.endswith(('.mp4', '.jpg', '.png')) for name in entries)
    assert json.loads(entries['render-receipt.json'])['status'] == 'encoded_pending_validation'
    assert entries['render.log.tail.txt'] == b'frame-capture completed\n'


def test_even_oversized_diagnostic_tails_leave_a_failure_summary(tmp_path):
    (tmp_path / 'output.mp4').write_bytes(os.urandom(8192))
    (tmp_path / 'render.log').write_bytes(os.urandom(65536))
    raw, result = collect_artifacts(tmp_path, summary(), compressed_limit=2048)
    entries = unpack(raw)
    assert len(raw) <= 2048
    assert list(entries) == ['experiment.json']
    assert result['status'] == 'artifact_budget_exceeded'
    assert result['diagnostic_payload_reduced'] is True


def test_failed_stage_is_not_upgraded_by_a_successful_small_archive(tmp_path):
    raw, result = collect_artifacts(tmp_path, summary('failed'))
    assert result['status'] == json.loads(unpack(raw)['experiment.json'])['status'] == 'failed'
