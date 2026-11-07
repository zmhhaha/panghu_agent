"""Create a deterministic, offline-only build context for the developer lab.

No build, dependency download, push, cluster access or model call is performed.
Asset digests match the previously accepted local preview environment. Runtime
databases, saved captures, credentials and arbitrary neighboring files never
enter this exact source/asset allowlist.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile


ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = 'video_workbench/Dockerfile.animation-lab'
SOURCE_FILES = frozenset({
    DOCKERFILE, DOCKERFILE + '.dockerignore',
    'video_workbench/requirements-animation-lab.txt',
    'agent_service/__init__.py', 'agent_service/contracts.py',
    'agent_service/diagnostics.py', 'agent_service/engines.py',
    'agent_service/profiles/__init__.py', 'agent_service/profiles/base.py',
    'agent_service/profiles/video_motion_contract.py',
    'agent_service/profiles/video_process_contract.py',
    'video_workbench/__init__.py',
    'video_workbench/animation_lab.py', 'video_workbench/animation_lab_samples.py',
    'video_workbench/animation_lab_store.py', 'video_workbench/animation_lab_triage_store.py',
    'video_workbench/animation_lab_iteration.py',
    'video_workbench/animation_lab_baseline.py','video_workbench/animation_lab_matrix.py','video_workbench/animation_lab_domain_cases.py',
    'video_workbench/animation_lab_scenarios.py','video_workbench/animation_lab_scenarios_review.py',
    'video_workbench/animation_lab_scenarios_charts.py','video_workbench/animation_lab_scenarios_algorithms.py',
    'video_workbench/animation_lab_scenarios_interfaces.py',
    'video_workbench/animation_lab_scenarios_math.py',
    'video_workbench/animation_lab_scenarios_spatial.py',
    'video_workbench/animation_lab_scenarios_spatial_review.py',
    'video_workbench/animation_lab_scenarios_fields.py',
    'video_workbench/animation_lab_candidates.py', 'video_workbench/animation_lab_canvas.py',
    'video_workbench/animation_lab_three.py', 'video_workbench/animation_lab_effects.py',
    'video_workbench/animation_lab_effects_logic.py', 'video_workbench/animation_lab_effects_visual.py',
    'video_workbench/animation_lab_effects_review.py', 'video_workbench/animation_lab_engine_review.py',
    'video_workbench/animation_lab_evaluate.py', 'video_workbench/animation_lab_evaluation_view.py',
    'video_workbench/animation_lab_evidence.py', 'video_workbench/animation_lab_model.py',
    'video_workbench/animation_lab_review.py', 'video_workbench/animation_lab_player.mjs',
    'video_workbench/animation_lab_engines/three.mjs',
    'video_workbench/animation_lab_web/index.html', 'video_workbench/animation_lab_web/app.mjs',
    'video_workbench/animation_lab_web/model.mjs', 'video_workbench/animation_lab_web/style.css',
    'video_workbench/examples/build_process_sample.py',
    'video_workbench/motion.py', 'video_workbench/motion_demo.py',
    'video_workbench/motion_geometry.py', 'video_workbench/motion_process.py',
    'video_workbench/motion_review.py', 'video_workbench/motion_semantic.py',
    'video_workbench/process_continuity.py', 'video_workbench/process_continuity_review.py',
    'video_workbench/process_review.py',
    'video_workbench/motion_renderer/lab_observe.mjs',
    'video_workbench/motion_renderer/engine_observe.mjs',
    'video_workbench/motion_renderer/observe.mjs',
    'video_workbench/motion_renderer/runtime.mjs',
    'video_workbench/motion_renderer/contract.mjs',
})
ASSET_SHA256 = {
    'gsap.min.js': 'c174bfce53a729418d57a8ad8625e7247c793a22fef8e2851e3cfa3de9cd8280',
    'NotoSansCJK-Regular.ttc': 'b76b0433203017ca80401b2ee0dd69350349871c4b19d504c34dbdd80541690a',
    'three.module.min.js': '08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7',
    'three.LICENSE.txt': '4c40a1ef62450b857c3b2aaf294936304cd552d965fbcd9d32d4c5bcf4ba4454',
}
MAX_CONTEXT_BYTES = 32 * 1024 * 1024


def _directory(path):
    path = Path(path).absolute()
    if not path.is_dir() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('animation_lab_build_directory_invalid')
    return path


def _read(root, name, maximum):
    path = root
    for part in Path(name).parts:
        path /= part
        if path.is_symlink():
            raise ValueError('animation_lab_build_symlink_rejected')
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError('animation_lab_build_file_invalid')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if not raw or len(raw) > maximum:
        raise ValueError('animation_lab_build_file_invalid')
    return raw


def context_archive(root, assets_dir, destination):
    """Write a new exact-file TAR; failures never replace an existing context."""
    root, assets_dir = _directory(root), _directory(assets_dir)
    destination = Path(destination).absolute()
    _directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise ValueError('animation_lab_build_context_exists')
    files = {name: _read(root, name, 1024 * 1024) for name in sorted(SOURCE_FILES)}
    for name, digest in ASSET_SHA256.items():
        raw = _read(assets_dir, name, MAX_CONTEXT_BYTES)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('animation_lab_build_asset_digest_mismatch')
        files['build-assets/' + name] = raw
    if sum(map(len, files.values())) > MAX_CONTEXT_BYTES:
        raise ValueError('animation_lab_build_context_too_large')
    measured = [dict(path=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                for name, raw in sorted(files.items())]
    with destination.open('xb') as output:
        with tarfile.open(fileobj=output, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, raw in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(raw), 0o644, 0
                archive.addfile(info, io.BytesIO(raw))
    return dict(schema='video.animation-lab-build-context.v1',
        dockerfile=DOCKERFILE, source_files=len(SOURCE_FILES), assets=len(ASSET_SHA256),
        files=measured,
        source_sha256=hashlib.sha256(json.dumps(measured, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        context_sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
        network_calls=0, builds=0, deployments=0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--assets-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(context_archive(args.root, args.assets_dir, args.output), sort_keys=True))


if __name__ == '__main__':
    main()
