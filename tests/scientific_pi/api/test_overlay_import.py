"""Fresh-process import checks for the existing whole-package ConfigMap mount."""
import pathlib
import subprocess
import sys
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
PROBE = textwrap.dedent(r'''
    import importlib, os, pathlib, shutil, socket, sys, tempfile
    from unittest.mock import patch

    root = pathlib.Path(sys.argv[1])
    mode = sys.argv[2]
    fixed = '/opt/scientific-pi-extension'
    with tempfile.TemporaryDirectory() as temporary:
        temporary = pathlib.Path(temporary)
        overlay = temporary / 'overlay' / 'scientific_agent'
        overlay.mkdir(parents=True)
        (overlay / '__init__.py').write_text('', encoding='utf-8')
        (overlay / 'crew.py').write_text('ORIGIN = "mounted-crew"', encoding='utf-8')
        extension = temporary / 'image-extension'
        extension.mkdir()
        for name in ('pi_contracts.py', 'pi_client.py', 'pi_projection.py'):
            if mode != 'missing-dependency' or name != 'pi_contracts.py':
                shutil.copyfile(root / 'scientific_agent' / name, extension / name)
        # An overlapping image module must never take precedence over CrewAI.
        (extension / 'crew.py').write_text('ORIGIN = "wrong-image-crew"', encoding='utf-8')
        sys.path.insert(0, str(overlay.parent))
        package = importlib.import_module('scientific_agent')
        original_paths = list(package.__path__)
        appended = []
        class MountedPaths(list):
            def append(self, value):
                # Map only the literal image path to a temporary host directory;
                # this keeps the probe portable without creating /opt on the host.
                assert value == fixed, value
                appended.append(value)
                super().append(str(extension))
        package.__path__ = MountedPaths(original_paths)
        original_isdir = os.path.isdir
        def image_isdir(path):
            if path == fixed:
                return mode != 'absent'
            return original_isdir(path)
        sys.path.insert(0, str(root))
        with patch('os.path.isdir', side_effect=image_isdir), patch.object(
                socket.socket, 'connect', side_effect=AssertionError('network on import')):
            if mode in ('absent', 'missing-dependency'):
                try:
                    importlib.import_module('app.api.scientific_agent')
                except ModuleNotFoundError as error:
                    assert error.name == 'scientific_agent.pi_contracts', error
                else:
                    raise AssertionError('missing Pi dependency was hidden')
            else:
                api = importlib.import_module('app.api.scientific_agent')
                contracts = importlib.import_module('scientific_agent.pi_contracts')
                client = importlib.import_module('scientific_agent.pi_client')
                projection = importlib.import_module('scientific_agent.pi_projection')
                assert api.ContractError is client.ContractError is contracts.ContractError
                assert api.PiClient is client.PiClient
                assert api.PiProjection is projection.PiProjection
                for module in (contracts, client, projection):
                    assert pathlib.Path(module.__file__).parent == extension
        crew = importlib.import_module('scientific_agent.crew')
        assert crew.ORIGIN == 'mounted-crew'
        assert list(package.__path__)[:len(original_paths)] == original_paths
        assert appended == ([] if mode == 'absent' else [fixed])
''')


class OverlayImportTests(unittest.TestCase):
    def probe(self, mode):
        result = subprocess.run([sys.executable, '-B', '-c', PROBE, str(ROOT), mode],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_overlay_preserves_crew_and_canonical_pi_module_identity(self):
        self.probe('present')

    def test_absent_extension_does_not_mask_missing_modules(self):
        self.probe('absent')

    def test_incomplete_extension_fails_without_dependency_fallback(self):
        self.probe('missing-dependency')


if __name__ == '__main__':
    unittest.main()
