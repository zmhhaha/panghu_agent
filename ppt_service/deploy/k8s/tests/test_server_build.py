"""Exercise server build orchestration with fake Docker; never build or pull an image."""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

DEPLOY = Path(__file__).resolve().parents[1]
SERVICE = DEPLOY.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD = load('ppt_server_build_test', DEPLOY / 'build_server.py')
MANIFEST = load('ppt_site_manifest_test', DEPLOY / 'write_site_manifest.py')
IMAGE = 'private.example:5000/team/ppt-service-site:test'
REPOSITORY = IMAGE.rsplit(':', 1)[0]
LOCAL_ID = 'sha256:' + '1' * 64
PUBLISHED = REPOSITORY + '@sha256:' + '2' * 64


class FakeDocker:
    def __init__(self, *, fail=None, wrong_repository=False, bad_contract=False):
        self.calls = []
        self.context = b''
        self.pushed = False
        self.fail = fail
        self.wrong_repository = wrong_repository
        self.bad_contract = bad_contract

    def __call__(self, command, **kwargs):
        assert command[0] == 'docker'
        arguments = command[1:]
        self.calls.append(arguments)
        operation = arguments[0]
        if operation == self.fail:
            raise subprocess.CalledProcessError(9, command)
        if operation == 'build':
            self.context = kwargs['stdin'].read()
        elif operation == 'push':
            self.pushed = True
        elif operation == 'image':
            assert arguments[1:4] == ['inspect', '--format', '{{json .}}']
            value = {
                'Id': LOCAL_ID, 'Os': 'linux', 'Architecture': 'arm64',
                'Config': {'User': '0:0' if self.bad_contract else '10000:10000',
                           'Entrypoint': BUILD.ENTRYPOINT,
                           'Env': ['PPT_EXECUTION_ENABLED=false']},
                'RepoDigests': [],
            }
            if self.pushed:
                value['RepoDigests'] = ['other.example/old@sha256:' + '3' * 64]
                if not self.wrong_repository:
                    value['RepoDigests'].append(PUBLISHED)
            return subprocess.CompletedProcess(command, 0, json.dumps(value))
        elif operation != 'run':
            raise AssertionError('unexpected Docker operation: ' + operation)
        return subprocess.CompletedProcess(command, 0)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ppt-server-build-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.service = self.root / 'repository/panghu_agent/ppt_service'
        for relative in ('__init__.py', 'requirements-api.lock',
                         *('server/' + name for name in BUILD.SERVER_FILES),
                         *('web/' + name for name in BUILD.WEB_FILES),
                         'deploy/k8s/Dockerfile.site.server', 'deploy/k8s/verify_image.py',
                         'deploy/k8s/write_site_manifest.py'):
            target = self.service / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((SERVICE / relative).read_bytes())

    def run_build(self, fake, push=True):
        with patch.object(BUILD.subprocess, 'run', fake):
            return BUILD.build(IMAGE, 'linux/arm64', push=push, service=self.service)

    def test_verified_image_is_pushed_then_matching_manifest_is_recorded(self):
        fake = FakeDocker()
        self.assertEqual(self.run_build(fake), PUBLISHED)
        self.assertEqual([call[0] for call in fake.calls if call[0] != 'image'],
                         ['build', 'run', 'push'])
        verify = next(call for call in fake.calls if call[0] == 'run')
        for expected in ('--read-only', '--pull=never', '10000:10000', 'none',
                         'no-new-privileges', LOCAL_ID, '/opt/ppt-service/verify_image.py'):
            self.assertIn(expected, verify)
        self.assertNotIn('--mount', verify)
        self.assertNotIn('-v', verify)
        self.assertEqual((self.service / 'rendered/image.txt').read_text().strip(), PUBLISHED)
        provenance = json.loads((self.service / 'rendered/source.json').read_text())
        self.assertEqual(provenance['local_image_id'], LOCAL_ID)
        self.assertNotEqual(provenance['image'], LOCAL_ID)

    def test_build_failure_stops_before_verification_and_push(self):
        fake = FakeDocker(fail='build')
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_build(fake)
        self.assertEqual([call[0] for call in fake.calls], ['build'])
        self.assertFalse((self.service / 'rendered/image.txt').exists())

    def test_verification_failure_never_pushes_or_leaves_old_receipt(self):
        (self.service / 'rendered').mkdir()
        (self.service / 'rendered/image.txt').write_text('old-digest\n')
        fake = FakeDocker(fail='run')
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_build(fake)
        self.assertNotIn('push', [call[0] for call in fake.calls])
        self.assertFalse((self.service / 'rendered/image.txt').exists())

    def test_invalid_default_runtime_is_not_published(self):
        fake = FakeDocker(bad_contract=True)
        with self.assertRaisesRegex(ValueError, 'runtime contract'):
            self.run_build(fake)
        self.assertNotIn('push', [call[0] for call in fake.calls])

    def test_no_push_verifies_but_leaves_no_deployable_receipt(self):
        (self.service / 'rendered').mkdir()
        (self.service / 'rendered/image.txt').write_text('stale-digest\n')
        fake = FakeDocker()
        self.assertIsNone(self.run_build(fake, push=False))
        self.assertEqual([call[0] for call in fake.calls if call[0] != 'image'], ['build', 'run'])
        self.assertFalse((self.service / 'rendered/image.txt').exists())
        self.assertFalse((self.service / 'rendered/source.json').exists())

    def test_push_failure_leaves_no_digest(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_build(FakeDocker(fail='push'))
        self.assertFalse((self.service / 'rendered/image.txt').exists())

    def test_unrelated_repo_digest_cannot_become_deploy_receipt(self):
        with self.assertRaisesRegex(ValueError, 'matching repository manifest digest'):
            self.run_build(FakeDocker(wrong_repository=True))
        self.assertFalse((self.service / 'rendered/image.txt').exists())

    def test_context_excludes_secrets_runtime_tests_and_repository_files(self):
        for relative in ('.env', '.git/config', 'data/uploads/secret.txt', 'worker/key.py',
                         'control/dev_site.py', 'server/private_token.py', 'tests/test_secret.py',
                         'web/.npmrc', 'web/src/test/secret.ts', 'web/src/local-token.ts',
                         'web/src/acceptance/demo.tsx', 'web/src/acceptance/fixtures.ts',
                         'web/src/acceptance/demo-worker.js',
                         'server/acceptance/generate_contract.py',
                         'server/acceptance/contracts/synthetic.fixture.json',
                         'web/node_modules/secret', 'web/dist/index.html'):
            target = self.service / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'SECRET_SENTINEL')
        fake = FakeDocker()
        self.run_build(fake, push=False)
        self.assertNotIn(b'SECRET_SENTINEL', fake.context)
        with tarfile.open(fileobj=io.BytesIO(fake.context)) as archive:
            names = set(archive.getnames())
            self.assertEqual(archive.extractfile('requirements-api.lock').read(),
                             (SERVICE / 'requirements-api.lock').read_bytes())
            self.assertEqual(archive.extractfile('web/pnpm-lock.yaml').read(),
                             (SERVICE / 'web/pnpm-lock.yaml').read_bytes())
            self.assertTrue(all(item.isfile() for item in archive.getmembers()))
        self.assertFalse(any('/tests/' in name or '/test/' in name or '/worker/' in name
                             or '/control/' in name or '.git' in name for name in names))
        self.assertIn('code/ppt_service/server/review_revisions.py', names)
        self.assertIn('code/ppt_service/server/acceptance/resolver.py', names)
        self.assertIn('web/src/acceptance/AcceptancePanel.tsx', names)

    def test_all_relative_frontend_imports_resolve_inside_allowlist(self):
        inputs = BUILD.context_inputs(self.service)
        for name, content in inputs.items():
            if not name.startswith('web/') or not name.endswith(('.ts', '.tsx')):
                continue
            imports = re.findall(r'(?:from\s+|import\s*)[\'"](\.[^\'"]+)[\'"]',
                                 content.decode('utf-8'))
            for relative in imports:
                target = posixpath.normpath((Path(name).parent / relative).as_posix())
                self.assertTrue(any(target + suffix in inputs for suffix in ('', '.ts', '.tsx', '/index.ts')),
                                name + ' imports missing ' + relative)

    def test_all_relative_backend_imports_resolve_inside_allowlist(self):
        inputs = BUILD.context_inputs(self.service)
        for name, content in inputs.items():
            if not name.startswith('code/') or not name.endswith('.py'):
                continue
            parts = name.removeprefix('code/').removesuffix('.py').split('/')
            for node in ast.walk(ast.parse(content, filename=name)):
                if not isinstance(node, ast.ImportFrom) or not node.level:
                    continue
                base = parts[:-node.level]
                modules = [node.module] if node.module else [item.name for item in node.names]
                for module in modules:
                    target = 'code/' + '/'.join(base + module.split('.'))
                    self.assertTrue(target + '.py' in inputs or target + '/__init__.py' in inputs,
                                    name + ' imports missing ' + target)

    def test_hardlinked_source_is_rejected_before_docker(self):
        source = self.service / 'server/app.py'
        try:
            os.link(source, self.service / 'other-app.py')
        except OSError as exc:
            self.skipTest('hard links unavailable: ' + str(exc))
        fake = FakeDocker()
        with self.assertRaisesRegex(ValueError, 'invalid build source'):
            self.run_build(fake)
        self.assertEqual(fake.calls, [])

    def test_image_requires_explicit_registry_and_tag(self):
        for image in ('ppt-service-site:test', 'private.example/site',
                      'private.example/site@sha256:' + '1' * 64, 'http://private.example/site:t'):
            with self.subTest(image=image), self.assertRaises(ValueError):
                BUILD.image_repository(image)

    def test_multistage_build_uses_locked_frontend_and_api_dependencies(self):
        dockerfile = (DEPLOY / 'Dockerfile.site.server').read_text()
        self.assertIn('pnpm@9.15.9', dockerfile)
        self.assertIn('pnpm install --frozen-lockfile', dockerfile)
        self.assertIn('-r /tmp/requirements-api.lock', dockerfile)
        self.assertNotIn('SITE_DEPENDENCY_IMAGE', dockerfile)
        self.assertNotIn('parse-dev-', dockerfile)
        self.assertEqual(dockerfile.count('\nFROM '), 3)


class ManifestTests(unittest.TestCase):
    def test_compiled_assets_and_exact_installed_sources_are_recorded(self):
        with tempfile.TemporaryDirectory(prefix='ppt-manifest-test-') as directory:
            root = Path(directory)
            (root / 'build-info').mkdir()
            (root / 'ppt_service/server').mkdir(parents=True)
            (root / 'web/dist/assets').mkdir(parents=True)
            source = root / 'ppt_service/server/app.py'
            source.write_bytes(b'print("synthetic")\n')
            sources = {'code/ppt_service/server/app.py': MANIFEST.describe(source)}
            (root / 'build-info/source-inputs.json').write_text(json.dumps(sources))
            (root / 'web/dist/index.html').write_bytes(b'<html>compiled</html>')
            (root / 'web/dist/assets/app.js').write_bytes(b'console.log("compiled")')
            manifest = MANIFEST.generate(root)
            self.assertEqual(set(manifest), {'code/ppt_service/server/app.py',
                                            'web/dist/index.html', 'web/dist/assets/app.js'})
            (root / 'web/dist/assets/private.js.map').write_bytes(b'{}')
            with self.assertRaisesRegex(ValueError, 'unexpected compiled frontend asset'):
                MANIFEST.generate(root)


class ShellEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options = ([Path('C:/Program Files/Git/bin/bash.exe')] if os.name == 'nt' else [])
        found = shutil.which('bash')
        if found:
            options.append(Path(found))
        cls.bash = next((str(path) for path in options if path.exists()), None)
        if cls.bash is None:
            raise unittest.SkipTest('Bash is not installed')

    def test_help_needs_no_docker_python_or_cluster_config(self):
        result = subprocess.run([self.bash, str(SERVICE / 'build.sh'), '--help'],
                                capture_output=True, text=True, check=True)
        self.assertIn('--no-push', result.stdout)
        self.assertIn('rendered/image.txt', result.stdout)
        self.assertNotIn('Building ', result.stdout)

    def test_explicit_registry_wins_over_cluster_config_and_default_tag_is_fresh(self):
        with tempfile.TemporaryDirectory(prefix='ppt-build-shell-') as directory:
            root = Path(directory)
            service = root / 'repository/panghu_agent/ppt_service'
            service.mkdir(parents=True)
            shutil.copyfile(SERVICE / 'build.sh', service / 'build.sh')
            (root / 'repository/cluster_config.sh').write_text('export REGISTRY=cluster-default:5000\n')
            fakebin = root / 'bin'
            fakebin.mkdir()
            fake = fakebin / 'python3'
            fake.write_text('#!/usr/bin/env bash\nif [[ "${1:-}" == -c ]]; then printf "20261023T123456123456Z\\n"; exit; fi\nprintf "%s\\n" "$@" > "$PPT_TEST_LOG"\n')
            fake.chmod(0o755)
            def bash_path(path):
                value = path.as_posix()
                return '/' + value[0].lower() + value[2:] if os.name == 'nt' else value
            env = dict(os.environ, REGISTRY='override.example:6000',
                       PPT_FAKE_BIN=bash_path(fakebin), PPT_SCRIPT=bash_path(service / 'build.sh'),
                       PPT_TEST_LOG=bash_path(root / 'arguments.txt'))
            env.pop('IMAGE', None)
            env.pop('IMAGE_TAG', None)
            subprocess.run([self.bash, '-c', 'PATH="$PPT_FAKE_BIN:$PATH"; export PATH; bash "$PPT_SCRIPT" --no-push'],
                           check=True, capture_output=True, text=True, env=env)
            arguments = (root / 'arguments.txt').read_text().splitlines()
            self.assertIn('override.example:6000/ppt-service-site:20261023T123456123456Z', arguments)
            self.assertIn('--no-push', arguments)
            self.assertIn('linux/arm64', arguments)


if __name__ == '__main__':
    unittest.main()
