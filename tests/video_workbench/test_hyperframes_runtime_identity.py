"""Identity validation is distinct from actual core rendering or admission."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError
from video_workbench.motion_snapshot import _runtime
from test_creative_engine_renderer import RUNTIME_V2


CORE_SHA = '675e02ee21f31540e3a6abaec061f86cea92a0178ae67a390700acb4f801dbbc'


def test_core_and_project_host_are_required_under_a_new_runtime_schema():
    runtime = dict(RUNTIME_V2, schema='video.motion-runtime.v3',
        hyperframes_core_sha256=CORE_SHA, hyperframes_project_host_sha256='b' * 64)
    assert _runtime(runtime) == runtime
    assert _runtime(RUNTIME_V2) == RUNTIME_V2
    for field in ('hyperframes_core_sha256', 'hyperframes_project_host_sha256'):
        changed = deepcopy(runtime)
        changed.pop(field)
        with pytest.raises(ServiceError, match='fingerprint_missing'):
            _runtime(changed)
    changed = dict(runtime, schema='video.motion-runtime.v2')
    with pytest.raises(ServiceError, match='fingerprint_missing'):
        _runtime(changed)


@pytest.mark.parametrize('field,value', [
    ('hyperframes_core_sha256', 'a' * 64),
    ('hyperframes_project_host_sha256', 'unknown'),
    ('hyperframes', '0.8.150'),
    ('engine_runtime_sha256', 'unknown'),
])
def test_installed_core_version_and_host_hashes_cannot_be_substituted(field, value):
    runtime = dict(RUNTIME_V2, schema='video.motion-runtime.v3',
        hyperframes_core_sha256=CORE_SHA, hyperframes_project_host_sha256='b' * 64)
    runtime[field] = value
    with pytest.raises(ServiceError):
        _runtime(runtime)
