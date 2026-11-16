"""Real package measurement with offline image-ownership metadata only.

The copied bundle bytes, registry, policy, verifier and measurement are real.
Synthetic POSIX ownership is not evidence of container or model readiness.
"""
import hashlib
import json
from pathlib import Path

from agent_service import resources
from agent_service.profiles import video_animation_review as review
from agent_service.worker_entry import HostConfig, host_policy
from test_animation_review_catalog import prepare


def test_complete_input_review_measures_its_real_registered_package(monkeypatch, tmp_path):
    _, hosts, _ = prepare(include_input_review=True)
    config = HostConfig.model_validate(hosts[review.REVIEW_PACKED_PROFILE])
    policy = host_policy(config)
    copied = tmp_path / review.PACKED_BUNDLE_REVISION
    copied.mkdir()
    names = {'manifest.json', *review.PACKED_BUNDLE_FILES}
    for name in names:
        target = copied / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((review.PACKED_BUNDLE_ROOT / name).read_bytes())

    class ImageOwnedStat:
        # Only this copied fixture's owner/write bits stand in for image
        # metadata on Windows and nonroot Linux. All other stat data is real.
        def __init__(self, observed):
            self.observed = observed
            self.st_uid = 0
            self.st_mode = observed.st_mode & ~0o022

        def __getattr__(self, name):
            return getattr(self.observed, name)

    class ImageOwnedPath(type(Path())):
        def lstat(self, *args, **kwargs):
            observed = super().lstat(*args, **kwargs)
            try:
                self.relative_to(copied)
            except ValueError:
                return observed
            return ImageOwnedStat(observed)

    bundle_root = ImageOwnedPath(copied)
    monkeypatch.setattr(review, 'PACKED_BUNDLE_ROOT', bundle_root)
    control = tmp_path / 'control'
    control.mkdir()

    measured = resources.measured_package(config, policy, control)
    root, manifest, checksum, skills, skill_hashes = measured
    assert root == bundle_root
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == checksum
    package = json.loads(manifest.read_bytes())
    assert package['bundle_revision'] == review.PACKED_BUNDLE_REVISION
    assert {row['path'] for row in package['local_files']} == names
    for row in package['local_files']:
        assert row['sha256'] == hashlib.sha256((root / row['path']).read_bytes()).hexdigest()
    assert skills == (root / 'review-procedure/SKILL.md',)
    assert skill_hashes == (hashlib.sha256(skills[0].read_bytes()).hexdigest(),)
    assert resources.measured_package(config, policy, control) == measured
