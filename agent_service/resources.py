"""Measure declared package resources under fixed image-owned roots only."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from .contracts import ServiceError, canonical_bytes


PPT_COMMIT = 'c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7'
PPT_ROOT = Path('/opt/ppt-master')
RESEARCH_ROOT = Path(__file__).resolve().parent.parent / 'scientific_skills'


def measured_package(config, policy, control_root):
    from .worker_entry import _host_registry
    registry = _host_registry(config)
    pins = policy.execution_binding
    if pins is None:
        raise ServiceError('execution_binding_required', 503)
    bundle = registry.bundle(pins.bundle_name, pins.bundle_revision)
    if policy.name.startswith('ppt.'):
        root = PPT_ROOT
        mandatory = {'skills/ppt-master/SKILL.md', 'LICENSE'}
        commit = Path('/opt/ppt-service/build-info/ppt-master.commit')
        if commit.read_text().strip() != PPT_COMMIT:
            raise ServiceError('package_provenance_mismatch', 503)
    elif policy.name.startswith('research.'):
        root = RESEARCH_ROOT
        mandatory = {'research-procedure/SKILL.md', 'task-template.md', 'LICENSE'}
        from .profiles.research import verified_bundle
        verified_bundle()
    else:
        raise ServiceError('package_profile_unknown', 503)
    if bundle is None or not mandatory <= {r.name for r in bundle.resources}:
        raise ServiceError('package_resources_incomplete', 503)
    total = 0
    for item in bundle.resources:
        path = root.joinpath(*item.name.split('/'))
        for part in (root, *path.relative_to(root).parents):
            observed = (root / part if not part.is_absolute() else part).lstat()
            if stat.S_ISLNK(observed.st_mode) or observed.st_uid != 0 or observed.st_mode & 0o022:
                raise ServiceError('unsafe_package_resource', 503)
        observed = path.lstat()
        if (not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1
                or observed.st_uid != 0 or observed.st_mode & 0o022 or observed.st_size > 16 * 1024 * 1024):
            raise ServiceError('unsafe_package_resource', 503)
        raw = path.read_bytes()
        total += len(raw)
        if total > 128 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != item.sha256:
            raise ServiceError('package_digest_mismatch', 503)
    raw = canonical_bytes(dict(bundle_revision=bundle.revision,
        local_files=[dict(path=r.name, sha256=r.sha256) for r in bundle.resources]))
    manifest = Path(control_root) / ('package-' + hashlib.sha256(raw).hexdigest() + '.json')
    if not manifest.exists():
        fd = os.open(manifest, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o400)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    elif manifest.read_bytes() != raw:
        raise ServiceError('package_manifest_conflict', 503)
    skills = tuple(root / r.name for r in bundle.resources if r.name.endswith('/SKILL.md'))
    skill_hashes = tuple(r.sha256 for r in bundle.resources if r.name.endswith('/SKILL.md'))
    return root, manifest, hashlib.sha256(raw).hexdigest(), skills, skill_hashes
