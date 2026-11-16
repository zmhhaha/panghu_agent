"""Immutable v1 pools and additive v2 pools resolve independent resource roots."""
import shutil

import pytest

from agent_service import resources
from agent_service.contracts import ServiceError
from agent_service.profiles import video_motion


def test_motion_bundle_root_selects_exact_legacy_and_current_revision():
    assert resources.motion_package_root('video-motion-v1') == resources.MOTION_ROOT
    assert resources.motion_package_root('video-motion-v2') == video_motion.BUNDLE_ROOT
    assert resources.MOTION_ROOT != video_motion.BUNDLE_ROOT


@pytest.mark.parametrize('revision', ['../video-motion-v1', 'video-motion-v3', '', None])
def test_motion_bundle_root_rejects_unknown_or_path_revisions(revision):
    with pytest.raises(ServiceError) as error:
        resources.motion_package_root(revision)
    assert error.value.code == 'package_bundle_revision_unsupported'


@pytest.mark.parametrize('relative', ['manifest.json', 'motion-procedure/SKILL.md', 'PLAN.schema.json'])
def test_legacy_motion_resources_remain_independently_hash_checked(tmp_path, monkeypatch, relative):
    root = tmp_path / 'video-motion-v1'
    shutil.copytree(resources.MOTION_ROOT, root)
    path = root / relative
    path.write_bytes(path.read_bytes() + b'\nchanged')
    monkeypatch.setattr(resources, 'MOTION_ROOT', root)
    with pytest.raises(ServiceError) as error:
        resources.motion_package_root('video-motion-v1')
    assert error.value.code == 'motion_bundle_integrity'


def test_new_motion_resources_cannot_borrow_legacy_manifest(tmp_path, monkeypatch):
    root = tmp_path / 'video-motion-v2'
    shutil.copytree(video_motion.BUNDLE_ROOT, root)
    (root / 'manifest.json').write_bytes((resources.MOTION_ROOT / 'manifest.json').read_bytes())
    monkeypatch.setattr(video_motion, 'BUNDLE_ROOT', root)
    with pytest.raises(ServiceError) as error:
        resources.motion_package_root('video-motion-v2')
    assert error.value.code == 'motion_bundle_integrity'
