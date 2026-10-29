"""Offline native Docker smoke for an already-built workbench image.

Usage: python tests/video_workbench/container_smoke.py --image REGISTRY/IMAGE:latest
The image must already exist on the selected Docker daemon. This script never
pulls, publishes ports, enables container networking, submits an Agent job, or
reads deployment credentials. Its only credential is a newly created fixture.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from uuid import uuid4


LABEL = 'video-workbench.panghuer.top/smoke-run'
SHA256 = re.compile(r'sha256:[0-9a-f]{64}\Z')
PART = re.compile(r'[a-z0-9]+(?:[._-]+[a-z0-9]+)*\Z')
IMAGE_INFO = '{"id":{{json .Id}},"user":{{json .Config.User}}}'
CONTAINER_INFO = (
    '{"image_id":{{json .Image}},"user":{{json .Config.User}},'
    '"readonly_root":{{json .HostConfig.ReadonlyRootfs}},'
    '"network":{{json .HostConfig.NetworkMode}},'
    '"cap_drop":{{json .HostConfig.CapDrop}},'
    '"security_options":{{json .HostConfig.SecurityOpt}},'
    '"published_ports":{{json .HostConfig.PortBindings}},'
    '"publish_all_ports":{{json .HostConfig.PublishAllPorts}}}'
)

# Only loopback inside the container is reachable. The outer script never
# exposes HTTP response bodies, session tokens or request stdin in its report.
REQUEST = '''import json,sys,urllib.request,urllib.error
q=json.load(sys.stdin)
data=json.dumps(q['body']).encode() if 'body' in q else None
headers={'Host':'localhost',**q.get('headers',{})}
if data is not None: headers['Content-Type']='application/json'
r=urllib.request.Request('http://127.0.0.1:8089'+q['path'],data=data,headers=headers,method=q.get('method','GET'))
try: response=urllib.request.urlopen(r,timeout=3)
except urllib.error.HTTPError as e: response=e
with response: print(json.dumps({'status':response.status,'body':json.loads(response.read(1048577))}))
'''


class SmokeError(RuntimeError):
    """Fixed error categories; Docker stderr and HTTP bodies stay private."""


def require(condition, code):
    if not condition:
        raise SmokeError(code)


def image_reference(value):
    """Accept ordinary repositories with latest and an optional exact digest."""
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise argparse.ArgumentTypeError('An ordinary latest image reference is required')
    base, separator, digest = value.partition('@')
    if (not base.endswith(':latest') or separator and not SHA256.fullmatch(digest)
            or '..' in base or '//' in base):
        raise argparse.ArgumentTypeError('Use an image tagged latest, optionally followed by @sha256:HASH')
    parts = base[:-len(':latest')].split('/')
    host = parts[0]
    if len(parts) > 1 and ':' in host:
        address, _, port = host.rpartition(':')
        valid_host = (re.fullmatch(r'[a-z0-9][a-z0-9.-]*', address)
                      and port.isascii() and port.isdigit() and 1 <= int(port) <= 65535)
        valid_body = all(PART.fullmatch(part) for part in parts[1:])
    else:
        valid_host = True
        valid_body = all(PART.fullmatch(part) for part in parts)
    if not valid_host or not valid_body:
        raise argparse.ArgumentTypeError('An ordinary latest image repository is required')
    return value


def docker(*arguments, timeout=30, **options):
    return subprocess.run(['docker', *arguments], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, **options)


class ContainerSmoke:
    def __init__(self, image, runner=docker):
        self.image = image_reference(image)
        self.runner = runner
        self.run_id = uuid4().hex
        self.name = 'video-workbench-smoke-' + self.run_id
        self.volume = self.name + '-data'
        self.volume_attempted = self.container_attempted = False

    def command(self, *arguments, timeout=30, **options):
        result = self.runner(*arguments, timeout=timeout, check=False, **options)
        require(result.returncode == 0, 'docker_command_failed')
        return result.stdout

    def json(self, *arguments, **options):
        raw = self.command(*arguments, **options)
        require(type(raw) is bytes and 0 < len(raw) <= 1024 * 1024, 'invalid_docker_response')
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise SmokeError('invalid_docker_response') from None

    def request(self, path, **fields):
        return self.json('exec', '-i', self.name, 'python', '-c', REQUEST, timeout=8,
                         input=json.dumps(dict(path=path, **fields)).encode())

    def wait_ready(self):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if self.request('/readyz').get('status') == 200:
                    return
            except (SmokeError, OSError, subprocess.SubprocessError, AttributeError):
                pass
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        raise SmokeError('container_not_ready')

    def _remove_owned(self, kind, name):
        """Never delete a name without verifying this invocation's label."""
        label_path = '.Config.Labels' if kind == 'container' else '.Labels'
        template = '{{json (index ' + label_path + ' "' + LABEL + '")}}'
        try:
            inspected = self.runner(kind, 'inspect', '--format', template, name, timeout=15, check=False)
            if inspected.returncode:
                # A failed inspect alone does not prove absence: the daemon may
                # be unavailable. Confirm absence with a successful exact-name
                # comparison; do not interpret or print localized stderr.
                listed = self.runner(kind, 'ls', *(['-a'] if kind == 'container' else []),
                                     '--filter', 'name=' + name, '--format',
                                     '{{.Names}}' if kind == 'container' else '{{.Name}}',
                                     timeout=15, check=False)
                return listed.returncode == 0 and name not in listed.stdout.decode('utf-8').splitlines()
            if len(inspected.stdout) > 256 or json.loads(inspected.stdout) != self.run_id:
                return False
            removed = self.runner(kind, 'rm', *(['-f', '-v'] if kind == 'container' else []),
                                  name, timeout=30, check=False)
            return removed.returncode == 0
        except (ValueError, TypeError, AttributeError, UnicodeError, OSError, subprocess.SubprocessError):
            return False

    def cleanup(self):
        # Container first, then its named volume. No prune, wildcard, caller
        # supplied names, pre-existing volume, deployment or production service.
        container = not self.container_attempted or self._remove_owned('container', self.name)
        volume = not self.volume_attempted or self._remove_owned('volume', self.volume)
        return dict(container_removed=container, volume_removed=volume)

    def run(self):
        report = dict(status='failed', image=self.image, model_calls=0)
        stage = 'inspect_local_image'
        try:
            image = self.json('image', 'inspect', '--format', IMAGE_INFO, self.image)
            require(type(image) is dict and type(image.get('id')) is str
                    and SHA256.fullmatch(image['id']) and image.get('user') == '10001:10001',
                    'image_identity_or_user_invalid')
            report['image_id'] = image['id']
            with tempfile.TemporaryDirectory(prefix='video-offline-smoke-') as temporary:
                token = Path(temporary) / 'token'
                require(token.is_absolute() and ',' not in str(token)
                        and not any(ord(char) < 32 for char in str(token)), 'invalid_fixture_mount_path')
                # A fixture created by this process, never an operator token.
                token.write_text('offline-fixture-only-' + 'x' * 32, encoding='utf-8')
                token.chmod(0o444)
                try:
                    stage = 'create_volume'
                    self.volume_attempted = True
                    self.command('volume', 'create', '--label', LABEL + '=' + self.run_id, self.volume)
                    stage = 'verify_owned_volume'
                    require(self.json('volume', 'inspect', '--format',
                                      '{{json (index .Labels "' + LABEL + '")}}', self.volume) == self.run_id,
                            'volume_ownership_unverified')
                    stage = 'start_isolated_container'
                    self.container_attempted = True
                    self.command('run', '-d', '--pull', 'never', '--name', self.name,
                                 '--label', LABEL + '=' + self.run_id,
                                 '--network', 'none', '--read-only', '--user', '10001:10001',
                                 '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                                 '--tmpfs', '/tmp:rw,nosuid,nodev,size=33554432',
                                 '--mount', f'type=volume,source={self.volume},target=/data',
                                 '--mount', f'type=bind,source={token},target=/run/offline-token,readonly',
                                 '-e', 'VIDEO_AGENT_URL=http://offline-agent.invalid',
                                 '-e', 'VIDEO_AGENT_TOKEN=',
                                 '-e', 'VIDEO_AGENT_TOKEN_FILE=/run/offline-token',
                                 '-e', 'VIDEO_AGENT_PROFILE_REVISION=1',
                                 '-e', 'VIDEO_WORKBENCH_DB=/data/workbench.db',
                                 '-e', 'VIDEO_WORKBENCH_HOSTS=localhost,127.0.0.1,[::1]', self.image)
                    stage = 'verify_runtime_isolation'
                    inspection = self.json('container', 'inspect', '--format', CONTAINER_INFO, self.name)
                    require(inspection.get('image_id') == image['id']
                            and inspection.get('user') == '10001:10001'
                            and inspection.get('readonly_root') is True
                            and inspection.get('network') == 'none'
                            and inspection.get('cap_drop') == ['ALL']
                            and any(option in {'no-new-privileges', 'no-new-privileges:true'}
                                    for option in inspection.get('security_options', []))
                            and not inspection.get('published_ports')
                            and inspection.get('publish_all_ports') is False, 'runtime_isolation_invalid')
                    uid = self.json('exec', self.name, 'python', '-c',
                                    'import os,json;print(json.dumps(dict(uid=os.getuid(),gid=os.getgid())))')
                    require(uid == dict(uid=10001, gid=10001), 'runtime_uid_invalid')
                    stage = 'check_probes_host_and_identity'
                    self.wait_ready()
                    require(self.request('/healthz').get('status') == 200, 'health_probe_failed')
                    require(self.request('/healthz', headers={'Host': 'unexpected.invalid'}).get('status') == 400,
                            'host_validation_failed')
                    require(self.request('/api/session').get('status') == 401, 'identity_validation_failed')
                    owner = {'X-Forwarded-User': 'offline-container-smoke'}
                    session = self.request('/api/session', headers=owner)
                    require(session.get('status') == 200 and session['body']['agent_configured'] is True,
                            'synthetic_file_credential_failed')
                    csrf = session['body']['csrf_token']
                    require(type(csrf) is str and re.fullmatch('[0-9a-f]{64}', csrf), 'csrf_response_invalid')
                    stage = 'create_project_and_scene'
                    fields = dict(title='容器重启校验', script='离线持久化验证。', style='简洁')
                    require(self.request('/api/projects', method='POST', headers=owner, body=fields).get('status') == 403,
                            'csrf_validation_failed')
                    headers = dict(owner, **{'X-CSRF-Token': csrf})
                    project = self.request('/api/projects', method='POST', headers=headers, body=fields)
                    require(project.get('status') == 200, 'project_creation_failed')
                    scene = dict(title='离线镜头', narration='验证重启后保留镜头。',
                                 visual_description='深色背景和简单图形。', duration_seconds=8, format='svg')
                    created = self.request('/api/projects/' + project['body']['id'] + '/scenes',
                                           method='POST', headers=headers, body=scene)
                    require(created.get('status') == 200 and len(created['body']['scenes']) == 1,
                            'scene_creation_failed')
                    before = created['body']
                    stage = 'restart_and_check_persistence'
                    self.command('restart', self.name)
                    self.wait_ready()
                    after = self.request('/api/projects/' + before['id'], headers=owner)
                    require(after.get('status') == 200 and after.get('body') == before,
                            'database_restart_persistence_failed')
                    restarted_session = self.request('/api/session', headers=owner)
                    require(restarted_session.get('status') == 200
                            and restarted_session['body']['csrf_token'] == csrf, 'csrf_restart_persistence_failed')
                    report.update(status='passed', nonroot=True, readonly_root=True, network='none',
                                  published_ports=False, probes=True, host_validation=True,
                                  identity_and_csrf=True, synthetic_file_credential=True,
                                  database_and_csrf_survive_restart=True, scene_survives_restart=True)
                finally:
                    # Remove bind users before deleting the synthetic token.
                    cleanup = self.cleanup()
                    report['cleanup'] = cleanup
                    report['cleanup_complete'] = all(cleanup.values())
                    if not report['cleanup_complete']:
                        report.update(status='failed', error='owned_resource_cleanup_failed')
        except Exception:
            # Never include exception strings, Docker stderr, HTTP bodies or
            # CSRF in console output, including on an unexpected failure.
            report.update(status='failed', error=report.get('error', stage))
        report.setdefault('cleanup', dict(container_removed=True, volume_removed=True))
        report.setdefault('cleanup_complete', True)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, type=image_reference,
                        help='已存在于当前 Docker 主机的 :latest 或 :latest@sha256:HASH 镜像')
    args = parser.parse_args(argv)
    report = ContainerSmoke(args.image).run()
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'passed' and report['cleanup_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
