"""Capability shape fixtures do not grant real renderer admission."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_runtime_capabilities import validate_runtime_capabilities
from test_animation_method_service import runtime_capabilities


def test_capability_snapshot_is_copied_and_binds_fixed_three_asset_and_runtime_identity():
    value = runtime_capabilities(['html-svg', 'canvas', 'three'])
    frozen = validate_runtime_capabilities(value)
    value['allowed_engines'].clear()
    assert frozen['allowed_engines'] == ['html-svg', 'canvas', 'three']
    for changed in ('module', 'host', 'image', 'duplicate', 'unknown', 'schema'):
        bad = deepcopy(frozen)
        if changed == 'module': bad['assets']['three.module.min.js'] = 'f' * 64
        elif changed == 'host': bad['runtime_identity']['host_sha256'] = 'bad'
        elif changed == 'image': bad['runtime_identity']['image_digest'] = 'latest'
        elif changed == 'duplicate': bad['allowed_engines'].append('canvas')
        elif changed == 'unknown': bad['assets']['remote.js'] = 'a' * 64
        else: bad['schema'] = 'untrusted'
        with pytest.raises(ServiceError):
            validate_runtime_capabilities(bad)
