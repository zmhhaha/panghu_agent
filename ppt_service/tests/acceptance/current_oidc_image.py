#!/usr/bin/env python3
"""Exercise installed site/proxy images with a fully isolated synthetic issuer.

Reads only the public deployment and ConfigMap. No production Secret, session,
database, file volume or model request is used. Synthetic OIDC exchanges and
SQLite run inside one no-network, rootless, disposable container.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid


LABEL = 'ppt-service.panghuer.top/oidc-acceptance'


def command(args, *, timeout=60, check=True):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('command_failed:' + args[0])
    return result


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    path.chmod(0o600)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('linux_server_root_required')
    os.umask(0o077)
    root = Path(__file__).resolve().parents[3]
    commit = command(['git', '-C', str(root), 'rev-parse', 'HEAD']).stdout.decode().strip()
    sources = [Path(__file__).resolve(), root / 'ppt_service/tests/oidc/synthetic_oidc.py',
               root / 'ppt_service/server/auth.py']
    for source in sources:
        frozen = command(['git', '-C', str(root), 'show', commit + ':' + source.relative_to(root).as_posix()]).stdout
        if frozen != source.read_bytes():
            raise RuntimeError('source_not_committed')
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    deployment = json.loads(command(['kubectl', '-n', 'ppt-service', 'get', 'deployment', 'ppt-site', '-o', 'json']).stdout)
    config = json.loads(command(['kubectl', '-n', 'ppt-service', 'get', 'configmap', 'ppt-oidc', '-o', 'json']).stdout)
    images = {c['name']: c['image'] for c in deployment['spec']['template']['spec']['containers']}
    if set(images) != {'site', 'oauth2-proxy'} or not all(re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', image) for image in images.values()):
        raise RuntimeError('expected_immutable_production_images')
    template = config['data']['oauth2-proxy.cfg']
    if any(line.strip().startswith(('client_secret', 'cookie_secret')) for line in template.splitlines()):
        raise RuntimeError('public_template_must_not_contain_secrets')
    inputs, evidence, binary = (args.output / name for name in ('input', 'evidence', 'bin'))
    for path in (inputs, evidence, binary):
        path.mkdir(mode=0o700)
    os.chown(evidence, 10000, 10000)
    shutil.copyfile(sources[1], inputs / 'synthetic_oidc.py')
    (inputs / 'oauth2-proxy.cfg').write_text(template)
    run_id = 'ppt-oidc-current-' + uuid.uuid4().hex
    names = [run_id + '-extract', run_id + '-test']
    owned = []
    receipt = dict(passed=False, commit=commit, images=images,
        config_uid=config['metadata']['uid'], config_resource_version=config['metadata']['resourceVersion'],
        config_sha256=hashlib.sha256(template.encode()).hexdigest(),
        source_sha256={p.relative_to(root).as_posix(): sha(p) for p in sources},
        network='none', user_credentials_used=False, production_database_accessed=False,
        model_requests=0, scope='current production images and public settings; synthetic issuer identities only')
    try:
        inspected = {name: json.loads(command(['docker', 'image', 'inspect', image]).stdout)[0]
                     for name, image in images.items()}
        if any(value['Os'] != 'linux' or value['Architecture'] != 'arm64' for value in inspected.values()):
            raise RuntimeError('expected_cached_linux_arm64_images')
        receipt['image_ids'] = {name: value['Id'] for name, value in inspected.items()}
        owned.append(names[0])
        command(['docker', 'create', '--name', names[0], '--label', LABEL + '=' + run_id,
                 '--network', 'none', images['oauth2-proxy']])
        command(['docker', 'cp', names[0] + ':/bin/oauth2-proxy', str(binary / 'oauth2-proxy')])
        (binary / 'oauth2-proxy').chmod(0o555)
        save(inputs / 'source.json', dict(proxy_image_id=inspected['oauth2-proxy']['Id'],
            proxy_binary_sha256=sha(binary / 'oauth2-proxy'),
            runtime_source_sha256={'ppt_service/server/auth.py': sha(sources[2])}))
        for path in inputs.iterdir():
            path.chmod(0o444)
        inputs.chmod(0o555)
        binary.chmod(0o555)
        owned.append(names[1])
        result = command(['docker', 'run', '--pull=never', '--name', names[1], '--label', LABEL + '=' + run_id,
            '--network', 'none', '--read-only', '--user', '10000:10000', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--memory=1g', '--cpus=1', '--pids-limit=128',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m,uid=10000,gid=10000,mode=700',
            '-v', str(inputs) + ':/tests:ro', '-v', str(evidence) + ':/evidence:rw',
            '-v', str(binary) + ':/probe-bin:ro', '--entrypoint', '/usr/local/bin/python',
            images['site'], '/tests/synthetic_oidc.py', '--template', '/tests/oauth2-proxy.cfg',
            '--binary', '/probe-bin/oauth2-proxy', '--output', '/evidence/summary.json'], timeout=180, check=False)
        (args.output / 'checks.private.log').write_bytes(result.stdout + result.stderr)
        receipt['exit_code'] = result.returncode
        result_path = evidence / 'summary.json'
        if result_path.is_file():
            summary = json.loads(result_path.read_text())
            receipt.update(protocol_passed=summary.get('passed') is True,
                           checks_passed=summary.get('checks_passed'), checks_total=summary.get('checks_total'),
                           summary_sha256=sha(result_path))
        receipt['passed'] = result.returncode == 0 and receipt.get('protocol_passed') is True
    except Exception as error:
        receipt['error_type'] = type(error).__name__
        receipt['diagnostic'] = str(error) if isinstance(error, RuntimeError) else 'inspect_private_evidence'
    finally:
        cleanup = []
        for name in reversed(owned):
            inspect = command(['docker', 'inspect', name], check=False)
            if inspect.returncode == 0:
                value = json.loads(inspect.stdout)[0]
                if value['Config']['Labels'].get(LABEL) != run_id:
                    cleanup.append('ownership_changed:' + name)
                    continue
                if command(['docker', 'rm', '-f', name], check=False).returncode:
                    cleanup.append('remove_failed:' + name)
            if command(['docker', 'inspect', name], check=False).returncode == 0:
                cleanup.append('resource_remained:' + name)
        receipt.update(cleanup_errors=cleanup, owned_resources_removed=not cleanup,
                       passed=receipt['passed'] and not cleanup, finished_at=time.time())
        save(args.output / 'receipt.json', receipt)
    print(json.dumps({key: receipt.get(key) for key in (
        'passed', 'checks_passed', 'checks_total', 'owned_resources_removed', 'diagnostic')}), flush=True)
    return 0 if receipt['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
