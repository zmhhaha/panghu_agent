"""Explicit verifier-only reassessment of an immutable, already rendered work.

No Agent, model, shared route or publication is involved. Original records and
reports remain untouched. A separate result is written only when the archived
source produces exactly the original frames and MP4 in the same pinned runtime.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import time

from . import animation_experiment_render as renderer
from .animation_experiments import (
    canonical, decode, digest, read_run, require, signed, _ordinary_file)


SCHEMA = 'video.animation-experiment-reassessment.v1'
VERIFIER_REVISION = 'svg-label-geometry-v2'
MAX_REPORT = 3 * 1024 * 1024
MAX_OBSERVATIONS = 8 * 1024 * 1024


def _write(path, value):
    with Path(path).open('xb') as output:
        output.write(canonical(value))
        output.flush()
        os.fsync(output.fileno())


def _original(root, image):
    row = read_run(root)
    require(row['status'] == 'rendered', 'animation_reassessment_original_not_rendered')
    report_raw = _ordinary_file(root, 'report.json', MAX_REPORT)
    report = decode(report_raw)
    outcome = report.get('renderer', {})
    receipt = outcome.get('receipt', {})
    require(outcome.get('status') == 'rendered' and outcome.get('image') == image
            and receipt.get('decode_verified') is True
            and receipt.get('output_sha256') == row['artifacts']['output.mp4']['sha256']
            and type(outcome.get('actual_image_id')) is str
            and re.fullmatch(r'sha256:[0-9a-f]{64}', outcome['actual_image_id'])
            and type(receipt.get('runtime')) is dict and bool(receipt['runtime']),
            'animation_reassessment_original_runtime_missing_or_changed')
    sources = row.get('code_files')
    require(type(sources) is dict and 'scene.html' in sources and sources.keys() <= renderer.SCENE_FILES
            and all(type(value) is str for value in sources.values()),
            'animation_reassessment_original_sources_invalid')
    hashes = {name: digest(content.encode('utf-8')) for name, content in sources.items()}
    for name, expected in hashes.items():
        raw = _ordinary_file(root, 'bundle/' + name, 512 * 1024)
        require(digest(raw) == expected, 'animation_reassessment_original_source_changed')
    require({path.name for path in (root / 'bundle').iterdir()} == set(sources),
            'animation_reassessment_original_source_changed')
    constraints = row['input_document']['constraints']
    count = int(constraints['duration_seconds'] * constraints['fps'])
    names = ['frame-%06d.jpg' % index for index in range(count)] + ['reverse-initial.jpg']
    frames = receipt.get('frames')
    require(1 <= count <= renderer.MAX_FRAMES and receipt.get('frame_count') == count
            and type(frames) is list and len(frames) == len(names)
            and [item.get('name') for item in frames] == names,
            'animation_reassessment_original_frames_invalid')
    published = {'frames/%06d.jpg' % index for index in range(count)}
    require({name for name in row['artifacts'] if name.startswith('frames/')} == published,
            'animation_reassessment_original_frames_invalid')
    for index, frame in enumerate(frames):
        raw = _ordinary_file(root, 'render/work/frames/' + frame['name'], 4 * 1024 * 1024)
        require(digest(raw) == frame.get('sha256') and len(raw) == frame.get('bytes'),
                'animation_reassessment_original_frame_changed')
        if index < count:
            item = row['artifacts']['frames/%06d.jpg' % index]
            published_raw = _ordinary_file(root, 'frames/%06d.jpg' % index, 4 * 1024 * 1024)
            require(digest(published_raw) == item['sha256'] == frame['sha256']
                    and len(published_raw) == item['size'] == frame['bytes'],
                    'animation_reassessment_original_frame_changed')
    return row, report_raw, outcome, hashes


def reassess(run_directory, output_directory, *, renderer_image, assets_dir=None):
    root, output = Path(run_directory).absolute(), Path(output_directory).absolute()
    require(root.is_dir() and not any(path.is_symlink() for path in (root, *root.parents)),
            'animation_reassessment_original_directory_invalid')
    require(root != output and root not in output.parents and output not in root.parents
            and not any(path.is_symlink() for path in (output, *output.parents)),
            'animation_reassessment_output_directory_invalid')
    # Exclusive output is the replay fence. A failure never rewrites prior
    # evidence or silently repeats a render; an operator must name a new run.
    output.mkdir(parents=True, exist_ok=False)
    row = None
    try:
        row, report_raw, original, source_hashes = _original(root, renderer_image)
        _write(output / 'original-binding.json', dict(run_id=row['id'], run_identity=row['identity'],
            original_report_sha256=digest(report_raw), source_code_sha256=source_hashes,
            media_sha256=row['artifacts']['output.mp4']['sha256'], renderer_image=renderer_image))
        host_sha = digest(renderer.HOST.read_bytes())
        outcome = renderer.render_scene(root / 'bundle', output / 'render', image=renderer_image,
            input_document=row['input_document'], assets_dir=assets_dir)
        receipt = outcome.get('receipt', {})
        require(outcome.get('image') == renderer_image
                and outcome.get('actual_image_id') == original['actual_image_id']
                and receipt.get('runtime') == original['receipt']['runtime']
                and receipt.get('host_sha256') == host_sha == digest(renderer.HOST.read_bytes()),
                'animation_reassessment_runtime_changed')
        request = decode(_ordinary_file(output, 'render/input/request.json', 64 * 1024))
        require(request.get('files_sha256') == source_hashes
                and request.get('assets_sha256') == row['input_document']['assets']
                and request.get('input_sha256') == row['input_sha256'],
                'animation_reassessment_source_or_input_changed')
        original_frames, current_frames = original['receipt']['frames'], receipt.get('frames')
        require(current_frames == original_frames, 'animation_reassessment_frame_hash_mismatch')
        for frame in original_frames:
            raw_frame = _ordinary_file(output, 'render/work/frames/' + frame['name'], 4 * 1024 * 1024)
            require(digest(raw_frame) == frame['sha256'] and len(raw_frame) == frame['bytes'],
                    'animation_reassessment_frame_hash_mismatch')
        raw_media = _ordinary_file(output, 'render/work/output.mp4', renderer.MAX_MEDIA_BYTES)
        require(digest(raw_media) == receipt.get('output_sha256') == row['artifacts']['output.mp4']['sha256'],
                'animation_reassessment_media_hash_mismatch')
        observed_raw = _ordinary_file(output, 'render/work/observations.json', MAX_OBSERVATIONS)
        require(digest(observed_raw) == receipt.get('observations_sha256'),
                'animation_reassessment_observation_hash_mismatch')
        observations = decode(observed_raw)
        require(all(item.get('label_observation', {}).get('verifier_revision') == VERIFIER_REVISION
                    for sample in observations['samples'] for item in sample['objects']),
                'animation_reassessment_label_observer_missing')
        technical = renderer.validate_observations(observations, row['input_document']['constraints'])
        require(technical == outcome.get('technical'), 'animation_reassessment_technical_result_changed')
        # Re-read the originals after the bounded render. No changed record,
        # source, media or original report can be certified by a stale snapshot.
        final_row, final_report, _, final_sources = _original(root, renderer_image)
        require(final_row == row and final_report == report_raw and final_sources == source_hashes,
                'animation_reassessment_original_changed')
        result = signed(dict(schema=SCHEMA, run_id=row['id'], run_identity=row['identity'],
            original_report_sha256=digest(report_raw), media_sha256=row['artifacts']['output.mp4']['sha256'],
            verifier_revision=VERIFIER_REVISION, source_code_sha256=source_hashes,
            renderer_image=renderer_image, observer_host_sha256=host_sha,
            observations_sha256=digest(observed_raw), frame_hashes_match=True, media_hash_matches=True,
            technical=technical, evaluated_at=float(time.time())))
        _write(output / 'reassessment.json', result)
        return result
    except Exception as error:
        _write(output / 'reassessment-failure.json', dict(
            schema='video.animation-experiment-reassessment-failure.v1', status='failed',
            run_id=row['id'] if row else None, run_identity=row['identity'] if row else None,
            verifier_revision=VERIFIER_REVISION, error=getattr(error, 'code', type(error).__name__),
            evaluated_at=float(time.time()), agent_calls=0, original_records_rewritten=False))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-directory', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--renderer-image', required=True, help='same original latest@sha256 reference')
    parser.add_argument('--assets-dir', type=Path)
    args = parser.parse_args(argv)
    result = reassess(args.run_directory, args.output, renderer_image=args.renderer_image,
                      assets_dir=args.assets_dir)
    print(canonical(result).decode('utf-8'))


if __name__ == '__main__':
    main()
