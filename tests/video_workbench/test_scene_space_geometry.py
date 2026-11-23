"""Run the bounded shared-mesh/projection contract with the normal test suite."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_shared_mesh_visibility_flow_coverage_and_resource_limits():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node unavailable')
    subprocess.run([node, '--test', str(Path(__file__).with_name('scene-space-geometry.test.mjs'))],
                   check=True, timeout=60)
