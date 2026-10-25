"""Fresh-process import checks for the dedicated Pi-only API image layout."""
import pathlib
import subprocess
import sys
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
PROBE = textwrap.dedent(r'''
    import importlib, pathlib, shutil, socket, sys, tempfile
    from unittest.mock import patch

    root = pathlib.Path(sys.argv[1])
    mode = sys.argv[2]
    with tempfile.TemporaryDirectory() as temporary:
        temporary = pathlib.Path(temporary)
        image = temporary / 'image'
        package_path = image / 'scientific_agent'
        package_path.mkdir(parents=True)
        (package_path / '__init__.py').write_text('', encoding='utf-8')
        for name in ('pi_contracts.py', 'pi_client.py', 'pi_projection.py', 'pi_citations.py', 'pi_sections.py', 'pi_report_versions.py'):
            if mode != 'missing-dependency' or name != 'pi_contracts.py':
                shutil.copyfile(root / 'scientific_agent' / name, package_path / name)
        for name in ('app', 'app/api', 'tools'):
            (image / name).mkdir(exist_ok=True)
            (image / name / '__init__.py').write_text('',encoding='utf-8')
        shutil.copyfile(root / 'app/api/scientific_agent.py',image / 'app/api/scientific_agent.py')
        shutil.copyfile(root / 'tools/sqlite_client.py',image / 'tools/sqlite_client.py')
        sys.path.insert(0, str(image))
        package = importlib.import_module('scientific_agent')
        original_paths = list(package.__path__)
        if mode == 'legacy-overlay':
            # The retired whole-package mount cannot silently pull executable
            # code from a second extension location after Pi-only migration.
            for name in ('pi_contracts.py', 'pi_client.py', 'pi_projection.py'):
                (package_path / name).unlink()
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('network on import')):
            if mode in ('legacy-overlay', 'missing-dependency'):
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
                citations = importlib.import_module('scientific_agent.pi_citations')
                sections = importlib.import_module('scientific_agent.pi_sections')
                versions = importlib.import_module('scientific_agent.pi_report_versions')
                assert api.ContractError is client.ContractError is contracts.ContractError
                assert api.PiClient is client.PiClient
                assert api.PiProjection is projection.PiProjection
                for module in (contracts, client, projection, citations, sections, versions):
                    assert pathlib.Path(module.__file__).parent == package_path
                assert 'scientific_agent.crew' not in sys.modules
                assert 'crewai' not in sys.modules
        assert list(package.__path__) == original_paths
''')


class OverlayImportTests(unittest.TestCase):
    def probe(self, mode):
        result = subprocess.run([sys.executable, '-B', '-c', PROBE, str(ROOT), mode],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dedicated_image_preserves_canonical_pi_module_identity_without_crewai(self):
        self.probe('present')

    def test_retired_package_overlay_fails_without_extension_fallback(self):
        self.probe('legacy-overlay')

    def test_incomplete_extension_fails_without_dependency_fallback(self):
        self.probe('missing-dependency')


if __name__ == '__main__':
    unittest.main()
