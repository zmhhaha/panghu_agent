"""Donor branch ordering and fail-closed pre-model image checks, without Docker."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_service.deploy import animation_experiment_overlay as overlay


RESOURCES = '''def measured_package(config, policy, control_root):
    if policy.name.startswith('ppt.'):
        root = PPT_ROOT
        mandatory = {'skills/ppt-master/SKILL.md', 'LICENSE'}
    elif policy.name.startswith('research.'):
        root = RESEARCH_ROOT
        mandatory = {'research-procedure/SKILL.md', 'LICENSE'}
    elif policy.name == 'video.motion-plan.v1':
        root = MOTION_ROOT
        mandatory = {'motion-procedure/SKILL.md', 'LICENSE'}
    elif policy.name == 'video.explanation-plan.v1':
        root = EXPLANATION_ROOT
        mandatory = {'explanation-procedure/SKILL.md', 'LICENSE'}
    elif policy.name == 'video.process-plan.v1':
        root = PROCESS_ROOT
        mandatory = {'process-procedure/SKILL.md', 'LICENSE'}
    elif policy.name.startswith('video.'):
        root = VIDEO_ROOT
        mandatory = {'video-procedure/SKILL.md', 'CONTRACT.md', 'LICENSE'}
    else:
        raise ServiceError('package_profile_unknown', 503)
    if not mandatory <= {r.name for r in bundle.resources}:
        raise ServiceError('package_resources_incomplete', 503)
'''


def donor(newline='\n'):
    return {name: value.replace('\n', newline) for name, value in {
        'profiles/__init__.py': "def implementation(name):\n    raise ValueError('No reviewed profile implementation')\n",
        'registry.py': "OutputSchema = Literal['text-output.v1']\n",
        'resources.py': RESOURCES,
        'deploy/acceptance_probe.py': "PROFILES = {'video.storyboard.v1'}\n"}.items()}


def branch(source, name):
    node = ast.parse(source).body[0].body[0]
    while True:
        if eval(compile(ast.Expression(node.test), '<reviewed-test-branch>', 'eval'),
                {'policy': SimpleNamespace(name=name)}):
            return ast.unparse(node.test), node.body
        assert len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If)
        node = node.orelse[0]


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_specific_animation_package_precedes_generic_video_and_preserves_old_routes(newline):
    original = donor(newline)
    patched = overlay.patched_sources(original)
    code = patched['resources.py'].decode()
    selected, body = branch(code, overlay.PROFILE)
    assert selected == "policy.name == 'video.animation-experiment.v1'"
    mandatory = next(row.value for row in body if isinstance(row, ast.Assign)
                     and row.targets[0].id == 'mandatory')
    resources = {'CONTRACT.md', 'LICENSE', 'animation-procedure/SKILL.md'}
    assert eval(compile(ast.Expression(mandatory), '<inventory>', 'eval'), {'BUNDLE_FILES': resources}) == {
        'manifest.json', *resources}
    for old in ('ppt.plan_initial.v1', 'research.generate.v1', 'video.motion-plan.v1',
                'video.explanation-plan.v1', 'video.process-plan.v1', 'video.storyboard.v1',
                'video.scene.v1', 'video.future.v1'):
        before, after = branch(original['resources.py'], old), branch(code, old)
        assert before[0] == after[0]
        assert [ast.dump(row) for row in before[1]] == [ast.dump(row) for row in after[1]]
    lines = code.splitlines(keepends=True)
    selected_node = next(row for row in ast.walk(ast.parse(code)) if isinstance(row, ast.If)
                         and ast.unparse(row.test) == selected)
    # Removing only the inserted branch restores every byte of the donor.
    removed = ''.join(lines[:selected_node.lineno - 1] + lines[selected_node.body[-1].end_lineno:])
    assert removed == original['resources.py']
    assert all(ast.parse(value.decode()) for value in patched.values())
    if newline == '\r\n':
        assert code.count('\n') == code.count('\r\n')


def test_the_failed_late_branch_insertion_is_detected_by_the_regression():
    faulty = overlay.once(RESOURCES, "    else:\n        raise ServiceError('package_profile_unknown', 503)",
        "    elif policy.name == 'video.animation-experiment.v1':\n        mandatory = {'animation-procedure/SKILL.md'}\n"
        "    else:\n        raise ServiceError('package_profile_unknown', 503)")
    assert branch(faulty, overlay.PROFILE)[0] == "policy.name.startswith('video.')"


def test_unrecognized_donor_cannot_silently_fall_back_to_another_patch_anchor():
    original = donor()
    original['resources.py'] = original['resources.py'].replace("policy.name.startswith('video.')", "policy.name == 'video.scene.v1'")
    with pytest.raises(RuntimeError, match='donor_patch_anchor_changed'):
        overlay.patched_sources(original)


def test_already_patched_donor_is_refused():
    once = {name: raw.decode() for name, raw in overlay.patched_sources(donor()).items()}
    with pytest.raises(RuntimeError, match='donor_already_has_profile'):
        overlay.patched_sources(once)


def preflight_fixture(tmp_path, monkeypatch, *, failure=None, network='none'):
    source = Path(overlay.__file__).resolve().parents[2]
    profile_sha = overlay.sha((source / 'agent_service/profiles/video_animation_experiment.py').read_bytes())
    manifest_sha = overlay.sha((source / 'agent_service/resources/animation-experiment-v1/manifest.json').read_bytes())
    images = {'control': 'registry.internal/control:latest@sha256:' + 'a' * 64,
              'worker': 'registry.internal/worker:latest@sha256:' + 'b' * 64}
    receipts = {role: dict(schema='animation-agent-overlay-build.v1', image=image,
        new_files={'profiles/video_animation_experiment.py': profile_sha,
                   'resources/animation-experiment-v1/manifest.json': manifest_sha}) for role, image in images.items()}
    host = tmp_path / 'host.json'
    host.write_text(json.dumps(dict(profile=dict(name=overlay.PROFILE, enabled=False, image_digest='sha256:' + 'b' * 64))))
    monkeypatch.setattr(overlay, 'inspect', lambda image: dict(Id='sha256:' + 'f' * 64, RepoDigests=[image]))
    monkeypatch.setattr(overlay, 'donor_sources', lambda *args: {
        'deploy/acceptance_probe.py': 'old image probe\n', 'deploy/candidate_scenario.py': 'old image scenario\n'})
    calls, state = [], {}
    def run(arguments, **settings):
        calls.append(arguments)
        if arguments[:3] == ['docker', 'container', 'create']:
            assert arguments[arguments.index('--network') + 1] == 'none'
            assert arguments[arguments.index('--entrypoint') + 1] == 'python'
            assert arguments[arguments.index('--user') + 1] == '0:0'
            assert '--read-only' in arguments and '--cap-drop' in arguments
            assert all('docker.sock' not in value and 'secrets.json' not in value for value in arguments)
            state['name'] = arguments[arguments.index('--name') + 1]
            state['hashes'] = arguments[-3:]
            return ('c' * 64).encode()
        if arguments[:3] == ['docker', 'container', 'inspect']:
            return json.dumps([dict(Id='c'*64, Name='/' + state['name'], Image='sha256:'+'f'*64,
                HostConfig=dict(NetworkMode=network, ReadonlyRootfs=True, Privileged=False, CapDrop=['ALL'],
                                CapAdd=None, SecurityOpt=['no-new-privileges']), Config=dict(User='0:0'),
                State=dict(Running=False, ExitCode=0, OOMKilled=False))]).encode()
        if arguments[:3] == ['docker', 'container', 'start']:
            if failure:
                raise RuntimeError(failure)
            return json.dumps(dict(schema='animation-package-container-preflight.v1', package_measured=True,
                profile_source_sha256=state['hashes'][0], harness_sha256=state['hashes'][1],
                scenario_harness_sha256=state['hashes'][2], model_requests=0, agent_started=False)).encode()
        if arguments[:3] in (['docker', 'container', 'rm'], ['docker', 'container', 'ls']):
            return b''
        raise AssertionError(arguments)
    monkeypatch.setattr(overlay, 'run', run)
    return dict(control_receipt=receipts['control'], worker_receipt=receipts['worker'],
                host_config=host, source_root=source), calls


def test_preflight_measures_both_real_boundaries_and_exact_projected_harnesses(tmp_path, monkeypatch):
    arguments, calls = preflight_fixture(tmp_path, monkeypatch)
    receipt = overlay.preflight_images(**arguments)
    assert receipt['passed'] is True and receipt['agent_tasks'] == receipt['model_requests'] == 0
    assert len(receipt['results']) == 2 and receipt['containers_removed'] is True
    assert all(row['executed_harness_source'] == 'exact_readonly_candidate_projection' for row in receipt['results'])
    assert len([row for row in calls if row[:3] == ['docker', 'container', 'start']]) == 2


def test_package_failure_stops_before_any_candidate_and_cleans_its_container(tmp_path, monkeypatch):
    arguments, calls = preflight_fixture(tmp_path, monkeypatch, failure='package_resources_incomplete')
    with pytest.raises(RuntimeError, match='package_resources_incomplete'):
        overlay.preflight_images(**arguments)
    assert len([row for row in calls if row[:3] == ['docker', 'container', 'create']]) == 1
    assert any(row[:3] == ['docker', 'container', 'rm'] for row in calls)


def test_actual_network_boundary_mismatch_rejects_before_start(tmp_path, monkeypatch):
    arguments, calls = preflight_fixture(tmp_path, monkeypatch, network='bridge')
    with pytest.raises(RuntimeError, match='preflight_actual_container_boundary_invalid'):
        overlay.preflight_images(**arguments)
    assert not any(row[:3] == ['docker', 'container', 'start'] for row in calls)
