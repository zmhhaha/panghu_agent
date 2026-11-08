"""Build and measure the new profile on inspected immutable Agent donors.

Administrator tool. Does not open running containers, credentials, config,
databases or model routes. Extraction containers are never started; preflight
containers run only fixed administrator measurement code without network.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import uuid


PROFILE = 'video.animation-experiment.v1'
OUTPUT_SCHEMA = 'video-animation-experiment-output.v1'
IMAGE = re.compile(r'[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}\Z')
TARGET = re.compile(r'[a-z0-9][a-z0-9._:/-]*:latest\Z')
PACKAGE = '/opt/agent-service/agent_service/'
PATCH_NAMES = ('profiles/__init__.py', 'registry.py', 'resources.py', 'deploy/acceptance_probe.py')
NEW_NAMES = ('profiles/video_animation_experiment.py',
    'resources/animation-experiment-v1/manifest.json',
    'resources/animation-experiment-v1/CONTRACT.md',
    'resources/animation-experiment-v1/LICENSE',
    'resources/animation-experiment-v1/animation-procedure/SKILL.md')
RUNTIME_KEYS = ('Env', 'Entrypoint', 'Cmd', 'User', 'WorkingDir', 'Volumes',
                'ExposedPorts', 'Healthcheck', 'StopSignal', 'Shell')


def require(value, code):
    if not value:
        raise RuntimeError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def run(arguments, *, input=None, timeout=600):
    result = subprocess.run(arguments, input=input, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, timeout=timeout)
    require(result.returncode == 0, 'overlay_command_failed:' + arguments[0] + ':' + arguments[1])
    return result.stdout


def assignment(source, name):
    nodes = [node for node in ast.parse(source).body if isinstance(node, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
    require(len(nodes) == 1, 'donor_assignment_not_unique:' + name)
    node = nodes[0]
    lines = source.splitlines(keepends=True)
    start = sum(map(len, lines[:node.lineno - 1])) + node.col_offset
    end = sum(map(len, lines[:node.end_lineno - 1])) + node.end_col_offset
    return start, end


def once(source, before, after):
    if '\r\n' in source:
        before, after = before.replace('\n', '\r\n'), after.replace('\n', '\r\n')
    require(source.count(before) == 1, 'donor_patch_anchor_changed')
    return source.replace(before, after, 1)


def resource_condition(test):
    """Recognize only the reviewed package profile tests, without executing one."""
    name = ast.dump(ast.parse('policy.name', mode='eval').body)
    if (isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute)
        and ast.dump(test.func.value) == name and test.func.attr == 'startswith'
        and len(test.args) == 1 and not test.keywords and isinstance(test.args[0], ast.Constant)
        and test.args[0].value in {'ppt.', 'research.', 'video.'}):
        return ('prefix', test.args[0].value)
    if (isinstance(test, ast.Compare) and ast.dump(test.left) == name
        and len(test.ops) == len(test.comparators) == 1):
        value = test.comparators[0]
        if (isinstance(test.ops[0], ast.Eq) and isinstance(value, ast.Constant)
            and value.value in {'video.motion-plan.v1', 'video.explanation-plan.v1', 'video.process-plan.v1'}):
            return ('profile', value.value)
        if isinstance(test.ops[0], ast.In) and isinstance(value, ast.Set):
            if all(isinstance(item, ast.Constant) and type(item.value) is str for item in value.elts):
                if {item.value for item in value.elts} == {'video.storyboard.v1', 'video.scene.v1'}:
                    return ('legacy-video',)
    return None


def patched_resources(source):
    """Insert before either inspected terminal video branch; preserve other bytes.

    The real control donor has an explicit legacy-profile set, while the Pi
    donor has a generic video prefix. Both known chains are reviewed here;
    unfamiliar conditions or terminal branches fail closed.
    """
    functions = [row for row in ast.parse(source).body
                 if isinstance(row, ast.FunctionDef) and row.name == 'measured_package']
    require(len(functions) == 1 and PROFILE not in source, 'donor_resources_shape_not_reviewed')
    roots = [row for row in functions[0].body if isinstance(row, ast.If)
             and resource_condition(row.test) == ('prefix', 'ppt.')]
    require(len(roots) == 1, 'donor_resources_shape_not_reviewed')
    branches, node = [], roots[0]
    while True:
        branches.append(node)
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            node = node.orelse[0]
        else:
            break
    keys = [resource_condition(row.test) for row in branches]
    require(None not in keys and len(keys) == len(set(keys))
        and keys[:2] == [('prefix', 'ppt.'), ('prefix', 'research.')]
        and keys[-1] in {('prefix', 'video.'), ('legacy-video',)}
        and not any(key in {('prefix', 'video.'), ('legacy-video',)} for key in keys[:-1])
        and len(node.orelse) == 1 and isinstance(node.orelse[0], ast.Raise),
        'donor_resources_shape_not_reviewed')
    lines = source.splitlines(keepends=True)
    require(node.col_offset == 4 and lines[node.lineno - 1].startswith('    elif '),
            'donor_resources_shape_not_reviewed')
    newline = '\r\n' if '\r\n' in source else '\n'
    insertion = (
        "    elif policy.name == 'video.animation-experiment.v1':\n"
        "        from .profiles.video_animation_experiment import BUNDLE_ROOT, BUNDLE_FILES, verified_bundle\n"
        "        root = BUNDLE_ROOT\n"
        "        mandatory = {'manifest.json', *BUNDLE_FILES}\n"
        "        verified_bundle()\n").replace('\n', newline)
    offset = sum(map(len, lines[:node.lineno - 1]))
    return source[:offset] + insertion + source[offset:]


def patched_sources(sources):
    require(PROFILE not in sources['profiles/__init__.py'], 'donor_already_has_profile')
    init = once(sources['profiles/__init__.py'],
        "    raise ValueError('No reviewed profile implementation')",
        "    if name == 'video.animation-experiment.v1':\n"
        "        from .video_animation_experiment import VideoAnimationExperimentProfile\n"
        "        return VideoAnimationExperimentProfile()\n"
        "    raise ValueError('No reviewed profile implementation')")
    registry = sources['registry.py']
    start, end = assignment(registry, 'OutputSchema')
    value = registry[start:end]
    require(value.endswith(']') and OUTPUT_SCHEMA not in value, 'donor_schema_already_registered')
    registry = registry[:start] + value[:-1] + ", '" + OUTPUT_SCHEMA + "']" + registry[end:]
    resources = patched_resources(sources['resources.py'])
    probe = sources['deploy/acceptance_probe.py']
    start, end = assignment(probe, 'PROFILES')
    value = probe[start:end]
    require(PROFILE not in value, 'donor_probe_already_registered')
    probe = probe[:start] + value + " | {'video.animation-experiment.v1'}" + probe[end:]
    result = dict(zip(PATCH_NAMES, (init, registry, resources, probe), strict=True))
    for value in result.values():
        ast.parse(value)
    return {name: value.encode() for name, value in result.items()}


def inspect(image):
    rows = json.loads(run(['docker', 'image', 'inspect', image]))
    require(len(rows) == 1 and not rows[0]['Config'].get('OnBuild'), 'donor_unexpected_configuration')
    return rows[0]


def donor_sources(image, names=PATCH_NAMES):
    name = 'animation-donor-read-' + uuid.uuid4().hex[:16]
    run(['docker', 'create', '--name', name, '--network', 'none', '--read-only',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--entrypoint', '/bin/true', image])
    try:
        result = {}
        for relative in names:
            raw = run(['docker', 'cp', name + ':' + PACKAGE + relative, '-'])
            require(len(raw) <= 1024 * 1024, 'donor_source_too_large')
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                members = archive.getmembers()
                require(len(members) == 1 and members[0].isfile()
                    and members[0].uid == 0 and not members[0].mode & 0o022
                    and members[0].size <= 512 * 1024, 'donor_source_not_image_owned')
                result[relative] = archive.extractfile(members[0]).read().decode('utf-8')
        return result
    finally:
        run(['docker', 'rm', name])


def archive_source(repository, archive_path, expected_sha256, revision):
    raw = archive_path.read_bytes()
    require(re.fullmatch(r'[0-9a-f]{64}', expected_sha256) and sha(raw) == expected_sha256,
            'source_archive_sha256_mismatch')
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        require(archive.pax_headers.get('comment') == revision, 'archive_git_revision_mismatch')
        members = archive.getmembers()
        require(len(members) < 20000 and sum(item.size for item in members) < 256 * 1024 * 1024,
                'source_archive_exceeds_limit')
        files = {}
        for item in members:
            path = Path(item.name)
            require(not path.is_absolute() and '..' not in path.parts and str(path) != '.',
                    'source_archive_path_invalid')
            require(item.isfile() or item.isdir(), 'source_archive_special_file')
            if item.isfile():
                require(item.name not in files, 'source_archive_duplicate_file')
                files[item.name] = archive.extractfile(item).read()
    if not repository.exists():
        repository.mkdir(mode=0o700)
        for name, content in files.items():
            destination = repository / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('xb') as stream:
                stream.write(content)
            destination.chmod(0o644)
    require(repository.is_dir() and not repository.is_symlink(), 'source_root_invalid')
    actual = {str(path.relative_to(repository)).replace('\\', '/') for path in repository.rglob('*') if not path.is_dir()}
    require(actual == set(files), 'source_archive_file_set_mismatch')
    for name, content in files.items():
        path = repository / name
        require(path.is_file() and not path.is_symlink() and path.read_bytes() == content,
                'source_archive_file_content_mismatch')
    file_manifest = json.dumps({name: sha(content) for name, content in sorted(files.items())},
                               separators=(',', ':'), sort_keys=True).encode()
    return dict(source_archive_sha256=expected_sha256,
                source_files_manifest_sha256=sha(file_manifest), source_file_count=len(files))


def build(args):
    """Build a minimal independent overlay; never alter a running service."""
    require(IMAGE.fullmatch(args.base_image) and TARGET.fullmatch(args.target_image)
        and re.fullmatch(r'[0-9a-f]{40}', args.revision), 'immutable_donor_latest_target_and_revision_required')
    builder_revision = getattr(args, 'builder_revision', None) or args.revision
    require(re.fullmatch(r'[0-9a-f]{40}', builder_revision), 'builder_revision_required')
    builder_source_sha256 = sha(Path(__file__).read_bytes())
    require(not args.output.exists() and not args.output.is_symlink(), 'output_exists')
    repository = args.repository.resolve()
    if args.source_archive:
        provenance = archive_source(repository, args.source_archive, args.archive_sha256 or '', args.revision)
    else:
        require(not args.archive_sha256, 'source_archive_required')
        actual = run(['git', '-C', str(repository), 'rev-parse', 'HEAD']).decode().strip()
        require(actual == args.revision, 'source_revision_mismatch')
        require(not run(['git', '-C', str(repository), 'status', '--porcelain', '--', 'agent_service']).strip(),
                 'agent_source_not_clean')
        provenance = dict(source_git_revision=args.revision)
    before = inspect(args.base_image)
    original = donor_sources(args.base_image)
    files = patched_sources(original)
    for name in NEW_NAMES:
        path = repository / 'agent_service' / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 512 * 1024,
                 'new_source_invalid')
        files[name] = path.read_bytes()
    manifest_name = 'resources/animation-experiment-v1/manifest.json'
    manifest = json.loads(files[manifest_name])
    for row in manifest['local_files']:
        require(sha(files['resources/animation-experiment-v1/' + row['path']]) == row['sha256'],
                 'new_bundle_content_mismatch')
    dockerfile = (f'FROM {args.base_image}\nCOPY overlay/ {PACKAGE}\n'
        f'LABEL org.opencontainers.image.revision="{args.revision}" '
        f'io.panghu.animation-experiment.profile="{PROFILE}" '
        f'io.panghu.animation-experiment.builder-revision="{builder_revision}" '
        f'io.panghu.animation-experiment.builder-source-sha256="{builder_source_sha256}" '
        f'io.panghu.animation-experiment.donor="{args.base_image}"\n').encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for name, raw in [('Dockerfile', dockerfile), *[('overlay/' + key, value) for key, value in sorted(files.items())]]:
            item = tarfile.TarInfo(name)
            item.uid = item.gid = 0
            item.mode, item.mtime, item.size = 0o644, 0, len(raw)
            archive.addfile(item, io.BytesIO(raw))
    context = buffer.getvalue()
    run(['docker', 'build', '--network', 'none', '--tag', args.target_image, '-'], input=context)
    after = inspect(args.target_image)
    require(all(before['Config'].get(key) == after['Config'].get(key) for key in RUNTIME_KEYS),
             'inherited_runtime_configuration_changed')
    run(['docker', 'push', args.target_image])
    pushed = inspect(args.target_image)
    repository_name = args.target_image.removesuffix(':latest')
    matches = [value for value in pushed.get('RepoDigests', []) if value.startswith(repository_name + '@sha256:')]
    require(len(matches) == 1, 'pushed_digest_unavailable')
    receipt = dict(schema='animation-agent-overlay-build.v1', revision=args.revision,
        builder_revision=builder_revision, builder_source_sha256=builder_source_sha256,
        donor=args.base_image, image=args.target_image + '@' + matches[0].split('@')[1],
        donor_image_id=before['Id'], built_image_id=after['Id'],
        context_sha256=sha(context), runtime_configuration_preserved=True,
        patched_files={key: dict(before_sha256=sha(original[key].encode()), after_sha256=sha(files[key]))
                       for key in PATCH_NAMES},
        new_files={key: sha(files[key]) for key in NEW_NAMES},
        **provenance,
        model_tasks=0, routes_changed=False, credentials_opened=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return receipt


PREFLIGHT_CODE = r'''
import hashlib,importlib.util,json,os,sys
from pathlib import Path
from agent_service.worker_entry import HostConfig,host_policy
from agent_service.resources import measured_package
from agent_service.profiles.video_animation_experiment import BUNDLE_ROOT,BUNDLE_FILES,verified_bundle
from agent_service.deploy.candidate_scenario import module_sha256
raw=Path('/preflight/host.json').read_bytes()
config=HostConfig.model_validate(json.loads(raw)); policy=host_policy(config)
assert policy.name=='video.animation-experiment.v1' and not policy.enabled
assert hashlib.sha256(Path('/opt/agent-service/agent_service/profiles/video_animation_experiment.py').read_bytes()).hexdigest()==sys.argv[1]
status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
assert os.geteuid()==0 and status['NoNewPrivs'].strip()=='1' and int(status['CapEff'].strip(),16)==0
spec=importlib.util.spec_from_file_location('measured_candidate_probe','/probe/acceptance_probe.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
assert probe.harness_sha256()==sys.argv[2] and module_sha256()==sys.argv[3]
probe.candidate_config(config.model_dump(mode='json'))
root,manifest,checksum,skills,hashes=measured_package(config,policy,Path('/run/agent-control'))
assert root==BUNDLE_ROOT and {row['path'] for row in json.loads(manifest.read_bytes())['local_files']}=={'manifest.json',*BUNDLE_FILES}
assert len(skills)==len(hashes)==1 and skills[0]==BUNDLE_ROOT/'animation-procedure/SKILL.md'
verified_bundle()
print(json.dumps(dict(schema='animation-package-container-preflight.v1',profile=policy.name,package_root=str(root),
 package_manifest_sha256=checksum,profile_source_sha256=sys.argv[1],harness_sha256=probe.harness_sha256(),
 scenario_harness_sha256=module_sha256(),package_measured=True,model_requests=0,agent_started=False),sort_keys=True))
'''


def preflight_images(*, control_receipt, worker_receipt, host_config, source_root):
    """Measure both actual images without network, credentials or model calls.

    Project the exact candidate harness files at the same destinations used by
    render_probe. The generated experiment's code is never executed here.
    """
    source_root, host_config = Path(source_root).resolve(strict=True), Path(host_config).resolve(strict=True)
    host_raw = host_config.read_bytes()
    host = json.loads(host_raw)
    require(host['profile']['name'] == PROFILE and host['profile']['enabled'] is False,
            'disabled_animation_host_required')
    package = source_root / 'agent_service'
    profile_sha = sha((package / 'profiles/video_animation_experiment.py').read_bytes())
    manifest_sha = sha((package / 'resources/animation-experiment-v1/manifest.json').read_bytes())
    probe_path, scenario_path = package / 'deploy/acceptance_probe.py', package / 'deploy/candidate_scenario.py'
    probe_sha = sha(probe_path.read_text(encoding='utf-8').encode())
    scenario_sha = sha(scenario_path.read_text(encoding='utf-8').encode())
    results = []
    for role, receipt in (('control', control_receipt), ('worker', worker_receipt)):
        require(receipt.get('schema') == 'animation-agent-overlay-build.v1'
            and receipt.get('new_files', {}).get('profiles/video_animation_experiment.py') == profile_sha
            and receipt['new_files'].get('resources/animation-experiment-v1/manifest.json') == manifest_sha
            and IMAGE.fullmatch(receipt.get('image', '')), 'preflight_build_provenance_mismatch')
        if role == 'worker':
            require(receipt['image'].rsplit('@', 1)[1] == host['profile']['image_digest'], 'preflight_worker_pin_mismatch')
        image = receipt['image']
        inspected = inspect(image)
        require(any(value.endswith('@' + image.rsplit('@', 1)[1]) for value in inspected.get('RepoDigests', [])),
                'preflight_actual_image_mismatch')
        image_harness = donor_sources(image, ('deploy/acceptance_probe.py', 'deploy/candidate_scenario.py'))
        name, identity = 'animation-package-preflight-' + uuid.uuid4().hex[:16], None
        arguments = ['docker', 'container', 'create', '--name', name, '--pull', 'never',
            '--network', 'none', '--read-only', '--user', '0:0', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--memory', '256m', '--pids-limit', '64',
            '--tmpfs', '/run/agent-control:rw,nosuid,nodev,noexec,size=4m,mode=700',
            '--env', 'PYTHONDONTWRITEBYTECODE=1', '--env', 'PYTHONPATH=/opt/agent-service',
            '--mount', f'type=bind,src={host_config},dst=/preflight/host.json,readonly',
            '--mount', f'type=bind,src={probe_path},dst=/probe/acceptance_probe.py,readonly',
            '--mount', f'type=bind,src={scenario_path},dst=/opt/agent-service/agent_service/deploy/candidate_scenario.py,readonly',
            '--entrypoint', 'python', image, '-B', '-c', PREFLIGHT_CODE,
            profile_sha, probe_sha, scenario_sha]
        try:
            identity = run(arguments).decode().strip()
            require(re.fullmatch('[0-9a-f]{64}', identity), 'preflight_container_identity_invalid')
            actual = json.loads(run(['docker', 'container', 'inspect', identity]))[0]
            configuration = actual['HostConfig']
            require(actual['Id'] == identity and actual['Name'] == '/' + name
                and actual['Image'] == inspected['Id'] and configuration['NetworkMode'] == 'none'
                and configuration['ReadonlyRootfs'] is True and configuration.get('Privileged', False) is False
                and configuration['CapDrop'] == ['ALL'] and not configuration.get('CapAdd')
                and any(value.startswith('no-new-privileges') for value in configuration['SecurityOpt'])
                and actual['Config']['User'] == '0:0', 'preflight_actual_container_boundary_invalid')
            output = json.loads(run(['docker', 'container', 'start', '--attach', identity], timeout=90))
            require(output.get('schema') == 'animation-package-container-preflight.v1'
                and output.get('package_measured') is True and output.get('profile_source_sha256') == profile_sha
                and output.get('harness_sha256') == probe_sha and output.get('scenario_harness_sha256') == scenario_sha
                and output.get('model_requests') == 0 and output.get('agent_started') is False,
                'preflight_container_measurement_failed')
            final = json.loads(run(['docker', 'container', 'inspect', identity]))[0]
            require(final['Id'] == identity and final['State']['Running'] is False
                and final['State']['ExitCode'] == 0 and final['State']['OOMKilled'] is False,
                'preflight_container_exit_invalid')
            results.append(dict(role=role, image=image, actual_image_id=inspected['Id'], measurement=output,
                image_harness_sha256=sha(image_harness['deploy/acceptance_probe.py'].replace('\r\n', '\n').encode()),
                image_scenario_harness_sha256=sha(image_harness['deploy/candidate_scenario.py'].replace('\r\n', '\n').encode()),
                executed_harness_source='exact_readonly_candidate_projection'))
        finally:
            if identity is not None:
                run(['docker', 'container', 'rm', '--force', identity])
                require(not run(['docker', 'container', 'ls', '-a', '--filter', 'id=' + identity,
                                 '--format', '{{.ID}}']).strip(), 'preflight_container_cleanup_unconfirmed')
    return dict(schema='animation-package-image-preflight.v1', host_config_sha256=sha(host_raw),
        profile_source_sha256=profile_sha, bundle_manifest_sha256=manifest_sha,
        harness_sha256=probe_sha, scenario_harness_sha256=scenario_sha, results=results,
        passed=True, model_requests=0, agent_tasks=0, containers_removed=True,
        production_routes_changed=False, credentials_opened=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build_parser = commands.add_parser('build', help='Build/push independent latest overlays only')
    build_parser.add_argument('--repository', required=True, type=Path)
    build_parser.add_argument('--base-image', required=True)
    build_parser.add_argument('--target-image', required=True)
    build_parser.add_argument('--revision', required=True)
    build_parser.add_argument('--builder-revision', help='Separate builder source revision; defaults to core revision')
    build_parser.add_argument('--output', required=True, type=Path)
    build_parser.add_argument('--source-archive', type=Path)
    build_parser.add_argument('--archive-sha256')
    preflight = commands.add_parser('preflight', help='Measure real images offline before any model request')
    preflight.add_argument('--control-receipt', required=True, type=Path)
    preflight.add_argument('--worker-receipt', required=True, type=Path)
    preflight.add_argument('--host-config', required=True, type=Path)
    preflight.add_argument('--source-root', required=True, type=Path)
    preflight.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'build':
        receipt = build(args)
    else:
        require(not args.output.exists(), 'preflight_output_exists')
        receipt = preflight_images(control_receipt=json.loads(args.control_receipt.read_bytes()),
            worker_receipt=json.loads(args.worker_receipt.read_bytes()), host_config=args.host_config,
            source_root=args.source_root)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    print(json.dumps(receipt, ensure_ascii=True))


if __name__ == '__main__':
    main()
