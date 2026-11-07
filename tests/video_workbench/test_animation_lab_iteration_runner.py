"""Real local Git checks and synthetic Agent/PR transports; no external jobs."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from video_workbench import animation_lab_iteration_runner as runner


CHECK_IMAGE = 'fixture/test:latest@sha256:' + 'c' * 64


def fixture_container(command, **kwargs):
    """Execute only this module's tiny synthetic fixture on host; no Docker call."""
    assert command[0:2] == ['docker', 'run'] and '--pull=never' in command
    assert command[command.index('--network') + 1] == 'none'
    assert '--read-only' in command and 'no-new-privileges' in command
    image = command.index(CHECK_IMAGE)
    fixture_command = [sys.executable, *command[image + 2:]]
    fixture_command = [part for part in fixture_command if not part.startswith('--basetemp=')]
    return subprocess.run(fixture_command, **kwargs)


def fixture_checks(workspace, request):
    return runner.run_checks(workspace, request, check_image=CHECK_IMAGE, run=fixture_container, timeout=30)


def git(path, *args):
    return runner._git(path, *args)


@pytest.fixture
def setup(tmp_path):
    source = tmp_path / 'src'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    git(source, 'config', 'user.email', 'lab-fixture@example.invalid')
    git(source, 'config', 'user.name', 'Synthetic Lab Fixture')
    git(source, 'config', 'core.autocrlf', 'false')
    git(source, 'remote', 'add', 'origin', 'https://github.com/zmhhaha/panghu_agent.git')
    (source / '.gitignore').write_bytes(b'__pycache__/\n.pytest_cache/\n')
    (source / 'video_workbench').mkdir()
    (source / 'video_workbench/__init__.py').write_text('', encoding='utf-8')
    (source / 'video_workbench/animation_lab_canvas.py').write_bytes(
        b'# Synthetic animation fixture\ndef value():\n    return 1\n')
    test_dir = source / 'tests/video_workbench'
    test_dir.mkdir(parents=True)
    (test_dir / 'test_animation_lab_canvas.py').write_text(
        'from video_workbench.animation_lab_canvas import value\ndef test_value():\n    assert value() == 2\n', encoding='utf-8')
    (test_dir / 'test_animation_lab_engine_integration.py').write_text('def test_contract():\n    assert True\n', encoding='utf-8')
    (test_dir / 'test_animation_lab_domain_cases.py').write_text('def test_domain_contract():\n    assert True\n', encoding='utf-8')
    (test_dir / 'test_animation_lab_matrix_v2.py').write_text('def test_matrix_identity_contract():\n    assert True\n', encoding='utf-8')
    git(source, 'add', '.')
    git(source, 'commit', '-q', '-m', 'Synthetic fixture')
    package = runner._signed(dict(schema=runner.TASK_SCHEMA, module='canvas',
        source_revision=git(source, 'rev-parse', 'HEAD'), base_cursor=None, next_cursor='a' * 64,
        feedback=[dict(key='annotation:' + str(uuid.uuid4()), case_id='canvas-queue', comment='Show the candidate fixture.',
                       binding=dict(semantic_input_sha256='c' * 64))],
        changes=[], scope='developer_feedback', required_checks=[]))
    request = runner.prepare_request(source, package, ['video_workbench/animation_lab_canvas.py:3:3'], base_branch='master')
    workspace = runner.create_workspace(source, tmp_path / 'work', request)
    git(workspace, 'config', 'user.email', 'lab-fixture@example.invalid')
    git(workspace, 'config', 'user.name', 'Synthetic Lab Fixture')
    git(workspace, 'config', 'core.autocrlf', 'false')
    proposal = dict(schema=runner.PROPOSAL_SCHEMA, request_identity=request['identity'],
        task_identity=package['identity'], edits=[dict(source_id=request['sources'][0]['source_id'], replacement='    return 2\n')])
    return source, package, request, workspace, proposal, tmp_path


def test_prepare_creates_separate_pinned_checkout_and_small_window(setup):
    source, package, request, workspace, proposal, _ = setup
    assert git(workspace, 'rev-parse', 'HEAD') == package['source_revision']
    assert git(workspace, 'rev-parse', '--abbrev-ref', 'HEAD').startswith('codex/animation-canvas-')
    assert git(source, 'rev-parse', '--abbrev-ref', 'HEAD') != request['branch']
    assert request['sources'][0]['content'] == '    return 1\n'
    assert len(runner.build_prompt(request)) <= 16000
    assert git(workspace, 'remote', 'get-url', 'origin') == 'https://github.com/zmhhaha/panghu_agent.git'


def test_clean_crlf_source_checkout_binds_commit_blob_and_validates_lf_clone(setup):
    source, package, _, _, _, temp = setup
    name = 'video_workbench/animation_lab_canvas.py'
    path = source / name
    blob = runner._git(source, 'show', package['source_revision'] + ':' + name, raw=True)
    assert b'\r\n' not in blob
    git(source, 'config', 'core.autocrlf', 'true')
    path.write_bytes(blob.replace(b'\n', b'\r\n'))
    git(source, 'add', '--renormalize', '--', name)
    assert path.read_bytes() != blob and git(source, 'status', '--porcelain') == ''
    request = runner.prepare_request(source, package, [name + ':3:3'], base_branch='master')
    assert request['sources'][0]['before_sha256'] == runner._sha(blob)
    assert request['sources'][0]['content'] == '    return 1\n'
    workspace = runner.create_workspace(source, temp / 'crlf-work', request)
    assert (workspace / name).read_bytes() == blob
    proposal = dict(schema=runner.PROPOSAL_SCHEMA, request_identity=request['identity'],
        task_identity=package['identity'], edits=[dict(source_id=request['sources'][0]['source_id'],
                                                    replacement='    return 2\n')])
    result = runner.apply_proposal(workspace, request, proposal)
    assert result['changed_files'] == [name]
    assert path.read_bytes() == blob.replace(b'\n', b'\r\n')


def test_pr_base_branch_must_resolve_to_exact_task_source_commit(setup):
    source, package, _, _, _, _ = setup
    git(source, 'checkout', '-b', 'diverged-base')
    (source / 'other.txt').write_bytes(b'Unrelated history must not enter the PR.\n')
    git(source, 'add', 'other.txt')
    git(source, 'commit', '-q', '-m', 'Different base history')
    git(source, 'checkout', 'master')
    for branch in ('diverged-base', 'does-not-exist'):
        with pytest.raises(ValueError, match='base_revision_mismatch'):
            runner.prepare_request(source, package, ['video_workbench/animation_lab_canvas.py:3:3'], base_branch=branch)


def test_documentation_only_followup_base_is_allowed_but_checkout_stays_at_source(setup):
    source, package, _, _, _, temp = setup
    (source / 'deployment-acceptance.md').write_bytes(b'Deployment evidence recorded after the image build.\n')
    git(source, 'add', 'deployment-acceptance.md')
    git(source, 'commit', '-q', '-m', 'Record deployment acceptance')
    documentation_tip = git(source, 'rev-parse', 'HEAD')
    request = runner.prepare_request(source, package, ['video_workbench/animation_lab_canvas.py:3:3'], base_branch='master')
    assert request['source_revision'] == package['source_revision']
    assert request['base_branch_revision'] == documentation_tip
    workspace = runner.create_workspace(source, temp / 'docs-work', request)
    assert git(workspace, 'rev-parse', 'HEAD') == package['source_revision']
    assert not (workspace / 'deployment-acceptance.md').exists()
    proposal = dict(schema=runner.PROPOSAL_SCHEMA, request_identity=request['identity'], task_identity=package['identity'],
        edits=[dict(source_id=request['sources'][0]['source_id'], replacement='    return 2\n')])
    runner.apply_proposal(workspace, request, proposal)
    git(workspace, 'config', 'user.email', 'lab-fixture@example.invalid')
    git(workspace, 'config', 'user.name', 'Synthetic Lab Fixture')
    git(workspace, 'add', 'video_workbench/animation_lab_canvas.py')
    git(workspace, 'commit', '-q', '-m', 'Synthetic bounded improvement')
    assert git(workspace, 'diff', '--name-only', documentation_tip + '...HEAD') == 'video_workbench/animation_lab_canvas.py'


@pytest.mark.parametrize('name', ['video_workbench/animation_lab_canvas.py', 'tests/extra_test.py', 'requirements.txt'])
def test_code_test_or_dependency_followup_base_is_rejected(setup, name):
    source, package, *_ = setup
    path = source / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(path.read_bytes() + b'\n# New code\n' if path.exists() else b'# New code\n')
    git(source, 'add', name)
    git(source, 'commit', '-q', '-m', 'Code changes after the frozen image source')
    with pytest.raises(ValueError, match='base_revision_mismatch'):
        runner.prepare_request(source, package, ['video_workbench/animation_lab_canvas.py:3:3'], base_branch='master')


@pytest.mark.parametrize('revision', ['unknown', 'master', 'a' * 39, 'A' * 40])
def test_unknown_or_nonimmutable_source_revision_is_rejected(setup, revision):
    _, package, *_ = setup
    package['source_revision'] = revision
    package = runner._signed({k: v for k, v in package.items() if k != 'identity'})
    with pytest.raises(ValueError, match='source_revision_required'):
        runner.validate_package(package)


def test_task_identity_and_path_scope_are_enforced(setup):
    source, package, *_ = setup
    changed = deepcopy(package)
    changed['feedback'][0]['comment'] = 'Changed after export'
    with pytest.raises(ValueError, match='identity_invalid'):
        runner.validate_package(changed)
    for name in ['../secrets', 'tests/video_workbench/test_animation_lab_canvas.py', 'video_workbench/animation_lab.py']:
        with pytest.raises(ValueError, match='out_of_scope'):
            runner.prepare_request(source, package, [name], base_branch='master')


def test_overlapping_source_windows_are_rejected(setup):
    source, package, *_ = setup
    with pytest.raises(ValueError, match='overlap'):
        runner.prepare_request(source, package, ['video_workbench/animation_lab_canvas.py:1:3',
            'video_workbench/animation_lab_canvas.py:3:3'], base_branch='master')


def test_prompt_limit_rejects_instead_of_truncating_feedback(setup):
    source, package, *_ = setup
    package['feedback'][0]['comment'] = '文' * 17000
    package = runner._signed({k: v for k, v in package.items() if k != 'identity'})
    with pytest.raises(ValueError, match='prompt_too_large'):
        runner.prepare_request(source, package, ['video_workbench/animation_lab_canvas.py:3:3'], base_branch='master')


def test_existing_workspace_and_dirty_source_are_not_overwritten(setup):
    source, package, request, workspace, *_ = setup
    with pytest.raises(ValueError, match='new_workspace_required'):
        runner.create_workspace(source, workspace, request)
    (source / 'unrelated.txt').write_text('Keep this user edit', encoding='utf-8')
    with pytest.raises(ValueError, match='repository_dirty'):
        runner.prepare_request(source, package, ['video_workbench/animation_lab_canvas.py:3:3'], base_branch='master')


@pytest.mark.parametrize('mutation', ['request', 'task', 'source', 'command', 'duplicate', 'empty'])
def test_proposal_cannot_change_identity_scope_or_commands(setup, mutation):
    _, _, request, workspace, proposal, _ = setup
    if mutation == 'request':
        proposal['request_identity'] = '0' * 64
    elif mutation == 'task':
        proposal['task_identity'] = '0' * 64
    elif mutation == 'source':
        proposal['edits'][0]['source_id'] = '0' * 64
    elif mutation == 'command':
        proposal['command'] = 'upload credentials'
    elif mutation == 'duplicate':
        proposal['edits'].append(deepcopy(proposal['edits'][0]))
    else:
        proposal['edits'] = []
    before = (workspace / 'video_workbench/animation_lab_canvas.py').read_bytes()
    with pytest.raises(ValueError):
        runner.apply_proposal(workspace, request, proposal)
    assert (workspace / 'video_workbench/animation_lab_canvas.py').read_bytes() == before


def test_apply_changes_only_the_bound_window_and_keeps_source_repository(setup):
    source, _, request, workspace, proposal, _ = setup
    raw = (source / 'video_workbench/animation_lab_canvas.py').read_bytes()
    preview = runner.validate_proposal(workspace, request, proposal)
    assert b'return 2' in preview['video_workbench/animation_lab_canvas.py']
    assert git(workspace, 'status', '--porcelain') == ''
    receipt = runner.apply_proposal(workspace, request, proposal)
    assert receipt['changed_files'] == ['video_workbench/animation_lab_canvas.py']
    assert (source / 'video_workbench/animation_lab_canvas.py').read_bytes() == raw


def test_changed_source_and_noop_proposal_are_rejected(setup):
    _, _, request, workspace, proposal, _ = setup
    proposal['edits'][0]['replacement'] = '    return 1\n'
    with pytest.raises(ValueError, match='no_change'):
        runner.apply_proposal(workspace, request, proposal)
    (workspace / 'video_workbench/animation_lab_canvas.py').write_text('New user edit\n', encoding='utf-8')
    with pytest.raises(ValueError, match='repository_dirty'):
        runner.validate_proposal(workspace, request, proposal)


def test_real_local_module_checks_are_bound_to_exact_candidate_diff(setup):
    _, _, request, workspace, proposal, _ = setup
    runner.apply_proposal(workspace, request, proposal)
    receipt = fixture_checks(workspace, request)
    assert receipt['passed'] is True
    assert '4 passed' in receipt['output_tail']
    assert receipt['diff_sha256'] == runner._sha(runner._git(workspace, 'diff', '--binary', raw=True))
    assert receipt['scope'] == 'module_technical_tests_only'


def test_check_command_is_code_owned_and_secrets_are_not_in_environment(setup, monkeypatch):
    _, _, request, workspace, proposal, _ = setup
    runner.apply_proposal(workspace, request, proposal)
    monkeypatch.setenv('GITHUB_TOKEN', 'do-not-forward')
    monkeypatch.setenv('AGENT_API_KEY', 'do-not-forward')
    seen = {}
    def fake(command, **kwargs):
        seen.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 1, stdout=b'Synthetic failure')
    receipt = runner.run_checks(workspace, request, check_image=CHECK_IMAGE, run=fake)
    assert receipt['passed'] is False and receipt['exit_code'] == 1
    assert 'GITHUB_TOKEN' not in repr(seen['command']) and 'AGENT_API_KEY' not in repr(seen['command'])
    assert 'do-not-forward' not in repr(seen['command'])
    assert '--network' in seen['command'] and '--read-only' in seen['command']
    assert 'tests/video_workbench/test_animation_lab_canvas.py' in seen['command']


def test_mutation_during_checks_is_not_a_pass(setup):
    _, _, request, workspace, proposal, _ = setup
    runner.apply_proposal(workspace, request, proposal)
    def fake(command, **kwargs):
        (workspace / 'video_workbench/animation_lab_canvas.py').write_text('changed while running', encoding='utf-8')
        return subprocess.CompletedProcess(command, 0, stdout=b'Not a valid pass')
    with pytest.raises(ValueError, match='changed_during_checks'):
        runner.run_checks(workspace, request, check_image=CHECK_IMAGE, run=fake)


def test_publish_requires_exact_passed_checks_and_explicit_github_origin(setup):
    _, _, request, workspace, proposal, temp = setup
    runner.apply_proposal(workspace, request, proposal)
    checks = fixture_checks(workspace, request)
    path = workspace / 'video_workbench/animation_lab_canvas.py'
    original = path.read_bytes()
    path.write_text(path.read_text(encoding='utf-8') + '# changed after tests\n', encoding='utf-8')
    with pytest.raises(ValueError, match='matching_passed_checks_required'):
        runner.publish(workspace, request, checks, temp / 'receipt')
    path.write_bytes(original)
    git(workspace, 'remote', 'set-url', 'origin', 'https://example.invalid/wrong.git')
    with pytest.raises(ValueError, match='github_remote_invalid'):
        runner.publish(workspace, request, checks, temp / 'receipt')


def test_explicit_publish_builds_draft_pr_receipt_without_real_network(setup):
    _, _, request, workspace, proposal, temp = setup
    runner.apply_proposal(workspace, request, proposal)
    checks = fixture_checks(workspace, request)
    calls = []
    def transport(command, **kwargs):
        calls.append(command)
        if command[0] == 'gh':
            assert '--draft' in command and '--body-file' in command
            return subprocess.CompletedProcess(command, 0, stdout=b'https://github.com/zmhhaha/panghu_agent/pull/999999\n')
        if 'push' in command:
            return subprocess.CompletedProcess(command, 0, stdout=b'')
        return subprocess.run(command, **kwargs)
    event = runner.publish(workspace, request, checks, temp / 'receipt', run=transport)
    assert event['state'] == 'pr_open' and event['expected_state'] == 'running'
    assert event['receipt']['pr_url'].endswith('/999999')
    assert event['receipt']['checks'][0]['status'] == 'passed'
    assert git(workspace, 'rev-parse', 'HEAD') != request['source_revision']
    assert not any('merge' in command for command in calls)
    assert (temp / 'receipt/publish-claim.json').exists()


class FakeAgent:
    def __init__(self, request, proposal):
        self.request, self.proposal = request, proposal
        self.job_id, self.attempt_id, self.artifact_id = (str(uuid.uuid4()) for _ in range(3))
        self.raw = runner._canonical(dict(schema_version='1', text=json.dumps(proposal)))
        self.manifest = dict(schema_version='agent-artifacts.v1', job_id=self.job_id, attempt_id=self.attempt_id,
            files=[dict(artifact_id=self.artifact_id, name='response.json', sha256=runner._sha(self.raw), size=len(self.raw))],
            total_bytes=len(self.raw), publication_authorized=False)
        self.manifest['sha256'] = runner._sha(self.manifest)
        self.proof = dict(stop_proven=True, attempt_id=self.attempt_id, fence=1,
            request_sha256='a' * 64, result_sha256='b' * 64, resource_ids=['synthetic-local-fixture'], observed_at=1.0)
        self.submissions = []

    def capabilities(self):
        return dict(profiles=[dict(profile=runner.PROFILE, profile_revision='r1', configured=True,
            ready=True, max_timeout_seconds=600)])

    def close(self):
        pass

    def submit(self, document, *, idempotency_key):
        self.submissions.append((document, idempotency_key))
        return dict(job_id=self.job_id, state='queued')

    def lookup_job(self, key):
        assert key == 'animation-lab-' + self.request['identity']
        return dict(job_id=self.job_id, state='completed')

    def get_job(self, job_id):
        assert job_id == self.job_id
        return dict(state='completed', job_id=self.job_id, business_ref='animation-lab:' + self.request['task_identity'],
            publication_authorized=False, stop_proven=True, stop_proof=self.proof,
            pins=dict(profile=runner.PROFILE, profile_revision='r1'),
            attempt=dict(attempt_id=self.attempt_id, fence=1), artifact_manifest_sha256=self.manifest['sha256'])

    def artifact_manifest(self, job_id):
        return self.manifest

    def download_artifact(self, artifact_id):
        assert artifact_id == self.artifact_id
        return self.raw


def test_generic_text_agent_contract_and_verified_candidate_collection(setup):
    _, _, request, _, proposal, _ = setup
    client = FakeAgent(request, proposal)
    gateway = runner.TextProposalGateway(client, 'r1')
    admitted = gateway.submit(request)
    document, key = client.submissions[0]
    assert document['profile'] == 'workspace-task.v1' and set(document['task']) == {'prompt'}
    assert key == 'animation-lab-' + request['identity']
    assert len(document['task']['prompt']) <= 16000
    assert gateway.lookup(request)['job_id'] == admitted['job_id']
    assert gateway.collect(request, admitted['job_id']) == proposal


@pytest.mark.parametrize('mutation', ['unavailable', 'stop', 'manifest', 'content', 'proposal'])
def test_agent_rejects_unavailable_or_unbound_outputs(setup, mutation):
    _, _, request, _, proposal, _ = setup
    client = FakeAgent(request, proposal)
    gateway = runner.TextProposalGateway(client, 'r1')
    if mutation == 'unavailable':
        client.capabilities = lambda: dict(profiles=[])
        with pytest.raises(ValueError, match='profile_unavailable'):
            gateway.submit(request)
        assert client.submissions == []
        return
    if mutation == 'stop':
        client.proof['stop_proven'] = False
    elif mutation == 'manifest':
        client.manifest['attempt_id'] = str(uuid.uuid4())
    elif mutation == 'content':
        client.raw += b' '
    else:
        proposal['request_identity'] = '0' * 64
        client = FakeAgent(request, proposal)
        gateway = runner.TextProposalGateway(client, 'r1')
    with pytest.raises(ValueError):
        gateway.collect(request, client.job_id)


def test_json_duplicate_keys_are_rejected():
    with pytest.raises(ValueError, match='duplicate_json_key'):
        runner._decode_json(b'{"text":"first","text":"second"}')


def agent_arguments(command, request_file, run_dir):
    return [command, '--request', str(request_file), '--run-dir', str(run_dir),
            '--base-url', 'http://unused.invalid', '--token-file', 'not-read-by-fake', '--profile-revision', 'r1']


def test_cli_agent_admission_claim_prevents_second_post_and_keeps_run_identity(setup, monkeypatch):
    _, _, request, _, proposal, temp = setup
    client = FakeAgent(request, proposal)
    monkeypatch.setattr(runner, '_gateway', lambda _: runner.TextProposalGateway(client, 'r1'))
    request_file, run_dir = temp / 'request.json', temp / 'run'
    runner._write_json(request_file, request)
    runner.main(agent_arguments('agent-submit', request_file, run_dir))
    assert len(client.submissions) == 1
    event = runner._read_json(run_dir / 'running-event.json')
    assert event['receipt']['run_id'] == request['identity']
    assert event['receipt']['run_id'] != client.job_id
    with pytest.raises(FileExistsError):
        runner.main(agent_arguments('agent-submit', request_file, run_dir))
    assert len(client.submissions) == 1


def test_lost_agent_admission_response_recovers_by_lookup_then_collects_without_resubmit(setup, monkeypatch):
    _, _, request, _, proposal, temp = setup
    client = FakeAgent(request, proposal)
    admitted_submit = client.submit
    def lost(document, *, idempotency_key):
        admitted_submit(document, idempotency_key=idempotency_key)
        raise RuntimeError('Synthetic admitted response lost')
    client.submit = lost
    monkeypatch.setattr(runner, '_gateway', lambda _: runner.TextProposalGateway(client, 'r1'))
    request_file, run_dir = temp / 'request.json', temp / 'run'
    runner._write_json(request_file, request)
    with pytest.raises(RuntimeError, match='response lost'):
        runner.main(agent_arguments('agent-submit', request_file, run_dir))
    assert (run_dir / 'agent-admission-claim.json').exists()
    assert not (run_dir / 'agent-job.json').exists()
    runner.main(agent_arguments('agent-reconcile', request_file, run_dir))
    runner.main(agent_arguments('agent-collect', request_file, run_dir))
    assert len(client.submissions) == 1
    assert runner._read_json(run_dir / 'proposal.json') == proposal
    assert runner._read_json(run_dir / 'running-event.json')['receipt']['run_id'] == request['identity']


def test_shared_source_changes_expand_only_related_module_checks():
    assert runner.affected_modules(['video_workbench/motion_process.py']) == ['continuity', 'process']
    assert runner.affected_modules(['video_workbench/animation_lab_effects_visual.py']) == ['diagram', 'process']
    assert runner.affected_modules(['video_workbench/motion.py']) == sorted(runner.MODULE_TESTS)
    assert runner.affected_modules(['video_workbench/animation_lab_scenarios.py']) == ['algorithms', 'charts', 'fields', 'interface', 'math', 'spatial']
    assert runner.affected_modules(['video_workbench/animation_lab_domain_cases.py']) == ['canvas', 'continuity', 'diagram', 'process', 'three']


@pytest.mark.parametrize('module,family', [('charts', 'charts'), ('algorithms', 'algorithms'), ('interface', 'interfaces'),
                                        ('math', 'math'), ('spatial', 'spatial'), ('fields', 'fields')])
def test_new_family_modules_are_explicitly_scoped_and_share_common_regression(module, family):
    own = 'video_workbench/animation_lab_scenarios_'+family+'.py'
    assert runner.MODULE_FILES[module] == {own, 'video_workbench/animation_lab_scenarios.py'}
    assert runner.affected_modules([own]) == [module]
    assert 'test_animation_lab_scenarios_'+family+'.py' in runner.MODULE_TESTS[module]
    required = {'test_animation_lab_scenarios_'+family+'.py', 'test_animation_lab_scenarios.py',
                'test_animation_lab_scenarios_review.py', 'test_animation_lab_matrix_v2.py',
                'test_animation_lab_review.py'}
    if module in {'charts', 'algorithms', 'interface'}:
        required.add('test_animation_lab_domain_cases.py')
    assert set(runner.MODULE_TESTS[module]) == required
    assert all(not path.startswith(('tests/', '.github/', 'agent_service/'))
               and path not in {'video_workbench/animation_lab_iteration_runner.py',
                                'video_workbench/animation_lab_model.py'}
               for path in runner.MODULE_FILES[module])


@pytest.mark.parametrize('module,family', [('charts', 'charts'), ('algorithms', 'algorithms'), ('interface', 'interfaces'),
                                        ('math', 'math'), ('spatial', 'spatial'), ('fields', 'fields')])
def test_real_git_new_family_source_windows_cannot_edit_other_modules_or_controls(setup, module, family):
    source, package, _, _, _, temp = setup
    own = 'video_workbench/animation_lab_scenarios_'+family+'.py'
    shared = 'video_workbench/animation_lab_scenarios.py'
    for name in (own, shared):
        (source / name).write_bytes(b'# Synthetic new-family scope fixture\ndef value():\n    return 1\n')
    git(source, 'add', '--', own, shared)
    git(source, 'commit', '-q', '-m', 'Synthetic explicit family sources')
    package = runner._signed({**{k: v for k, v in package.items() if k != 'identity'},
        'module': module, 'source_revision': git(source, 'rev-parse', 'HEAD')})
    request = runner.prepare_request(source, package, [own+':3:3', shared+':3:3'], base_branch='master')
    assert request['module'] == module and request['branch'].startswith('codex/animation-'+module+'-')
    workspace = runner.create_workspace(source, temp / ('scope-'+module), request)
    proposal = dict(schema=runner.PROPOSAL_SCHEMA, request_identity=request['identity'],
        task_identity=package['identity'], edits=[dict(source_id=request['sources'][0]['source_id'], replacement='    return 2\n')])
    assert runner.apply_proposal(workspace, request, proposal)['changed_files'] == [own]
    for forbidden in ['video_workbench/animation_lab_canvas.py', '../secrets',
                      'video_workbench/animation_lab_model.py', 'video_workbench/animation_lab_iteration_runner.py',
                      'tests/video_workbench/test_animation_lab_scenarios.py', '.github/workflows/deploy.yml']:
        with pytest.raises(ValueError, match='out_of_scope'):
            runner.prepare_request(source, package, [forbidden], base_branch='master')


def test_runner_events_roundtrip_real_iteration_store_and_new_retry_attempt(setup):
    from video_workbench.animation_lab_iteration import IterationStore
    from video_workbench.animation_lab_store import AnnotationStore
    from video_workbench.animation_lab_triage_store import TriageStore
    source, package, _, _, _, temp = setup
    database = temp / 'feedback.db'
    annotations = AnnotationStore(database)
    TriageStore(database)
    snapshot = runner._signed(dict(schema='video.animation-lab-case.v1', case_id='canvas-queue',
        module='canvas', title='Synthetic contract fixture', runtime={}, comparison=dict(baseline='baseline', candidate='candidate'),
        variants=[dict(variant_id='candidate', compiler='fixture', artifact_sha256='a' * 64,
                       input_sha256='b' * 64)]))
    annotations.register_snapshot(snapshot)
    annotations.append(dict(case_id='canvas-queue', case_identity=snapshot['identity'], module='canvas',
        variant_id='candidate', kind='issue', start_seconds=1, end_seconds=2, region=None,
        tags=['pacing'], severity='minor', comment='Synthetic contract fixture', preference=None, verdict=None))
    store = IterationStore(database, package['source_revision'], '/dev/animations')
    task = store.prepare('canvas')
    first = runner.prepare_request(source, task['package'], ['video_workbench/animation_lab_canvas.py:3:3'], base_branch='master')
    synced = store.transition(task['task_id'], runner.task_event(first, 'synced', 'prepared'))
    running = store.transition(task['task_id'], runner.task_event(first, 'running', 'synced'))
    assert synced['state'] == 'synced' and running['state'] == 'running'
    failed = store.transition(task['task_id'], runner.task_event(first, 'failed', 'running', receipt=dict(comment='Fixture failure')))
    assert failed['state'] == 'failed'
    retry = runner.prepare_request(source, task['package'], ['video_workbench/animation_lab_canvas.py:3:3'], base_branch='master')
    assert retry['identity'] != first['identity'] and retry['branch'] != first['branch']
    resumed = store.transition(task['task_id'], runner.task_event(retry, 'running', 'failed'))
    assert resumed['state'] == 'running'
    published = store.transition(task['task_id'], runner.task_event(retry, 'pr_open', 'running', receipt=dict(
        commit='f' * 40, pr_url='https://github.com/zmhhaha/panghu_agent/pull/999999',
        checks=[dict(name='synthetic_only', status='passed', evidence_sha256='e' * 64)])))
    assert published['state'] == 'pr_open'


def test_missing_check_runtime_records_failure_without_host_fallback(setup):
    _, _, request, workspace, proposal, _ = setup
    runner.apply_proposal(workspace, request, proposal)
    called = []
    def missing(command, **kwargs):
        called.append(command)
        raise FileNotFoundError('No container runtime')
    checks = runner.run_checks(workspace, request, check_image=CHECK_IMAGE, run=missing)
    assert checks['passed'] is False and checks['error_code'] == 'check_runtime_unavailable'
    assert len(called) == 1 and called[0][0] == 'docker'


def test_timeout_cleans_only_its_named_container_and_cannot_publish(setup):
    _, _, request, workspace, proposal, _ = setup
    runner.apply_proposal(workspace, request, proposal)
    calls = []
    def timeout(command, **kwargs):
        calls.append(command)
        if command[1] == 'run':
            raise subprocess.TimeoutExpired(command, 1, output=b'Synthetic timeout')
        return subprocess.CompletedProcess(command, 0, stdout=b'')
    checks = runner.run_checks(workspace, request, check_image=CHECK_IMAGE, run=timeout)
    assert checks['passed'] is False and checks['error_code'] == 'check_timeout'
    name = calls[0][calls[0].index('--name') + 1]
    assert calls[1] == ['docker', 'rm', '--force', name]


def candidate_fixture(setup, monkeypatch):
    from video_workbench import animation_lab_evaluation_view
    _, _, request, workspace, proposal, temp = setup
    runner.apply_proposal(workspace, request, proposal)
    git(workspace, 'add', 'video_workbench/animation_lab_canvas.py')
    git(workspace, 'commit', '-q', '-m', 'Synthetic published candidate')
    event = runner.task_event(request, 'pr_open', 'running', receipt=dict(
        commit=git(workspace, 'rev-parse', 'HEAD'), pr_url='https://github.com/zmhhaha/panghu_agent/pull/999999'))
    root = temp / 'candidate-r1'
    root.mkdir()
    originals = {name: ('Synthetic frozen fixture: ' + name).encode()
                 for name in ('run.json', 'technical.json', 'review-request.json')}
    for name, raw in originals.items():
        (root / name).write_bytes(raw)
    loaded = dict(run=dict(module='canvas', case_id='canvas-queue', identity='a' * 64,
                           runtime_identity='b' * 64, semantic_input_sha256='c' * 64),
        snapshot=dict(module='canvas', technical=dict(status='passed'), identity='d' * 64),
        bundles=[dict(artifact_sha256='e' * 64), dict(artifact_sha256='f' * 64)])
    # The real loader is exercised by its separate frozen-evidence tests. This
    # fixture isolates the runner's publication association contract only.
    monkeypatch.setattr(animation_lab_evaluation_view, 'load_evaluation', lambda _: loaded)
    return request, workspace, root, event, loaded, originals


def test_bind_candidate_preserves_original_run_and_declares_provenance_scope(setup, monkeypatch):
    request, workspace, root, event, loaded, originals = candidate_fixture(setup, monkeypatch)
    sidecar = runner.bind_candidate(workspace, request, root, event)
    assert sidecar['schema'] == runner.PUBLICATION_SCHEMA
    assert sidecar['identity'] == runner._sha({k: v for k, v in sidecar.items() if k != 'identity'})
    assert sidecar['provenance'] == 'developer_bound_frozen_evaluation_not_independent_build_attestation'
    assert sidecar['artifact_sha256s'] == ['e' * 64, 'f' * 64]
    assert sidecar['commit'] == git(workspace, 'rev-parse', 'HEAD')
    assert all((root / name).read_bytes() == raw for name, raw in originals.items())
    assert runner._read_json(root / 'iteration-candidate.json') == sidecar
    with pytest.raises(FileExistsError):
        runner.bind_candidate(workspace, request, root, event)


@pytest.mark.parametrize('mutation', ['module', 'case', 'technical', 'commit', 'semantics'])
def test_candidate_binding_rejects_wrong_report_or_published_commit(setup, monkeypatch, mutation):
    request, workspace, root, event, loaded, _ = candidate_fixture(setup, monkeypatch)
    if mutation == 'module':
        loaded['run']['module'] = 'three'
    elif mutation == 'case':
        loaded['run']['case_id'] = 'unrelated-case'
    elif mutation == 'technical':
        loaded['snapshot']['technical']['status'] = 'failed'
    elif mutation == 'commit':
        event['receipt']['commit'] = request['source_revision']
    else:
        loaded['run']['semantic_input_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        runner.bind_candidate(workspace, request, root, event)
    assert not (root / 'iteration-candidate.json').exists()
