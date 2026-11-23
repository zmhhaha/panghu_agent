"""Expose the form-field numerical/painting contract in the Python test suite."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_surface_form_geometry_normals_flow_and_local_wash_contract():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node unavailable')
    subprocess.run([node, '--test', str(Path(__file__).with_name('scene-space-surface.test.mjs'))],
                   check=True, timeout=30)
