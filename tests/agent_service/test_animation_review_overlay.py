"""Minimal donor edits and byte preservation; no Docker/model calls execute."""
import ast
from copy import deepcopy
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from agent_service.deploy import animation_review_overlay as overlay


def sources(newline='\n'):
    baseline = {
        'profiles/__init__.py': "def implementation(name):\n    if name == 'video.animation-experiment.v1':\n        return Experiment()\n    raise ValueError('unknown')\n",
        'registry.py': "OutputSchema = Literal['video-animation-experiment-output.v1']\n\n\ndef old_contract_guard(handler):\n    return True\n",
        'resources.py': "def measured_package(config, policy, control_root):\n    if policy.name == 'video.animation-experiment.v1':\n        return original_resources\n    return other_resources\n",
        'deploy/acceptance_probe.py': "PROFILES = {'video.animation-experiment.v1'}\n\n\nclass ProbeAuthority:\n    def result(self, document):\n        return {'state': 'completed'}\n",
        'deploy/candidate_scenario.py': "def load_scenario(path, source_dir, parent_dir, policy):\n    if policy.name == 'video.animation-experiment.v1':\n        validate_original(source_dir)\n    return source_dir\n",
        'model_proxy.py': "def old_proxy(value):\n    return value\n",
        'api.py': "from .storage import execution_fields\n\n\ndef create_app(settings, store, registry):\n    return original_app\n",
        'client.py': "class AgentClient:\n    def get_job(self, job_id):\n        return original_job\n",
        'worker_entry.py': "def build_pi_runtime_settings(config):\n    return original_runtime_settings\n",
    }
    candidate = deepcopy(baseline)
    candidate['profiles/__init__.py'] = candidate['profiles/__init__.py'].replace(
        "    raise ValueError('unknown')", ''.join(
            f"    if name == '{profile}':\n        return NewProfile()\n" for profile in overlay.PROFILES)
        + "    raise ValueError('unknown')")
    candidate['registry.py'] = candidate['registry.py'].replace(
        "'video-animation-experiment-output.v1']", "'video-animation-experiment-output.v1', "
        "'video-animation-observe-output.v1', 'video-animation-review-output.v1', "
        "'video-animation-create-output.v2', 'video-animation-review-output.v2']")
    candidate['registry.py'] += "\n\ndef required_model_capabilities(handler):\n    return handler.required_model_capabilities\n"
    candidate['resources.py'] = candidate['resources.py'].replace('    return other_resources',
        "    if policy.name in {'video.animation-observe.v1', 'video.animation-review.v1'}:\n"
        "        return review_resources\n"
        "    if policy.name == 'video.animation-create.v2':\n        return create_resources\n"
        "    if policy.name == 'video.animation-review.v2':\n        return sequence_resources\n"
        "    return other_resources")
    candidate['deploy/acceptance_probe.py'] = candidate['deploy/acceptance_probe.py'].replace(
        "PROFILES = {'video.animation-experiment.v1'}", "PROFILES = {'video.animation-experiment.v1', "
        + ', '.join(map(repr, overlay.PROFILES)) + ' }')
    candidate['deploy/acceptance_probe.py'] = candidate['deploy/acceptance_probe.py'].replace(
        "return {'state': 'completed'}", "return {'state': 'completed', 'model_image_evidence': measured_images()}")
    candidate['deploy/candidate_scenario.py'] = candidate['deploy/candidate_scenario.py'].replace(
        '    return source_dir', "    if policy.name in {" + ', '.join(map(repr, overlay.PROFILES))
        + "}:\n        implementation(policy.name).validate_inputs(None, task, input_root=source_dir)\n    return source_dir")
    candidate['model_proxy.py'] += "\n\ndef image_evidence_for_job(store, job):\n    return actual_transport_evidence\n"
    candidate['api.py'] = candidate['api.py'].replace('from .storage import execution_fields',
        'from .storage import execution_fields\nfrom .model_proxy import image_evidence_for_job').replace(
            '    return original_app', '    app.register_image_evidence_route()\n    return original_app')
    candidate['client.py'] += "\n    def model_image_evidence(self, job_id):\n        return self._request('GET', job_id)\n"
    return ({name: value.replace('\n', newline) for name, value in baseline.items()},
            {name: value.replace('\n', newline) for name, value in candidate.items()})


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_patch_preserves_unknown_donor_changes_outside_exact_edit_contexts(newline):
    baseline, candidate = sources()
    donors = {name: value.replace('\n', newline) for name, value in baseline.items()}
    extension = (newline * 5 + 'def unrelated_live_extension():' + newline + '    return 73' + newline)
    donors['registry.py'] += extension
    patched, receipts = overlay.patched_sources(donors, baseline, candidate)
    assert 'worker_entry.py' not in patched
    assert patched['registry.py'].decode().endswith(extension)
    assert all(row['untouched_bytes_preserved'] and row['reversible_to_exact_donor'] for row in receipts)
    assert all(row['donor_sha256'] == overlay.sha(donors[row['name']].encode()) for row in receipts)
    assert all(row['modified_scopes'] for row in receipts if row['patch_count'])
    for raw in patched.values():
        if newline == '\r\n':
            assert raw.count(b'\n') == raw.count(b'\r\n')


def test_unknown_shared_changes_inside_patch_context_are_rejected():
    baseline, candidate, donors = older_pi_sources()
    donors['registry.py'] = donors['registry.py'].replace(
        'from typing import Literal', 'from typing import Literal, Any')
    with pytest.raises(RuntimeError, match='overlay_donor_patch_context_changed:registry.py'):
        overlay.patched_sources(donors, baseline, candidate)


def test_unreviewed_local_shared_scope_and_missing_transport_evidence_are_rejected():
    baseline, candidate = sources()
    changed = deepcopy(candidate)
    changed['model_proxy.py'] += '\nunreviewed_global = 1\n'
    with pytest.raises(RuntimeError, match='overlay_unreviewed_source_scope:model_proxy.py'):
        overlay.patched_sources(baseline, baseline, changed)
    changed = deepcopy(candidate)
    changed['deploy/acceptance_probe.py'] = baseline['deploy/acceptance_probe.py']
    with pytest.raises(RuntimeError, match='overlay_candidate_image_transport_evidence_missing'):
        overlay.patched_sources(baseline, baseline, changed)


def test_old_experiment_is_required_and_new_role_donors_are_never_repatched():
    baseline, candidate = sources()
    donors = deepcopy(baseline)
    donors['profiles/__init__.py'] = 'def implementation(name):\n    raise ValueError(name)\n'
    with pytest.raises(RuntimeError, match='overlay_existing_experiment_donor_required'):
        overlay.patched_sources(donors, baseline, candidate)
    with pytest.raises(RuntimeError, match='overlay_new_roles_already_present'):
        overlay.patched_sources(candidate, baseline, candidate)


def test_worker_overlay_does_not_patch_control_api_client_or_proxy():
    baseline, candidate = sources()
    baseline = {name: baseline[name] for name in overlay.WORKER_PATCH_NAMES}
    candidate = {name: candidate[name] for name in overlay.WORKER_PATCH_NAMES}
    patched, _ = overlay.patched_sources(baseline, baseline, candidate, role='worker')
    assert not {'api.py', 'client.py', 'model_proxy.py'} & set(patched)


def test_a_wrong_baseline_cannot_silently_omit_role_runtime_support():
    baseline, candidate = sources()
    with pytest.raises(RuntimeError, match='overlay_resulting_role_support_incomplete'):
        overlay.patched_sources(baseline, candidate, candidate)


def older_pi_sources(newline='\n'):
    """The inspected donor's older structures, without Git/image dependencies."""
    baseline, candidate = sources()
    baseline['registry.py'] = '''from typing import Literal


EXISTING_GUARD = True
CONTRACT_RECORD_LIMIT = 200


OutputSchema = Literal['video-motion-plan-output.v1', 'video-process-plan-output.v1',
                       'video-animation-experiment-output.v1',
                       ]


class DeploymentRegistry:
    def from_config(self, c, routes):
        suite = ('workspace-contract.v1' if c.name == 'workspace-task.v1'
                 else c.name.replace('.v1', '-contract.v1'))
        if type(routes) not in (list, tuple) or len(routes) > CONTRACT_RECORD_LIMIT:
            raise ValueError('Invalid bounded routes')
        return suite
'''
    candidate['registry.py'] = baseline['registry.py'].replace('from typing import Literal',
        'import re\n\n\ndef required_model_capabilities(handler):\n'
        '    return handler.required_model_capabilities\n\n\nfrom typing import Literal').replace(
        "'video-animation-experiment-output.v1',",
        "'video-animation-experiment-output.v1', 'video-animation-observe-output.v1', "
        "'video-animation-review-output.v1', 'video-animation-create-output.v2', "
        "'video-animation-review-output.v2',").replace(
        "c.name.replace('.v1', '-contract.v1')", "re.sub(r'\\.v([0-9]+)$', r'-contract.v\\1', c.name)")
    baseline['resources.py'] = '''def measured_package(config, policy, control_root):
    if policy.name.startswith('ppt.'):
        return old_ppt_resources
    elif policy.name == 'research.generate.v1':
        return old_research_resources
    elif policy.name == 'video.animation-experiment.v1':
        return frozen_experiment_resources
    elif policy.name == 'video.motion-plan.v1':
        return motion_resources
    elif policy.name in {'video.storyboard.v1', 'video.scene.v1'}:
        return old_video_resources
    else:
        raise ValueError('unknown')
'''
    new_resource_branches = '''    elif policy.name in {'video.animation-observe.v1', 'video.animation-review.v1'}:
        return review_resources
    elif policy.name == 'video.animation-create.v2':
        return create_resources
    elif policy.name == 'video.animation-review.v2':
        return sequence_resources
'''
    candidate['resources.py'] = baseline['resources.py'].replace(
        "    elif policy.name == 'video.motion-plan.v1':", new_resource_branches +
        "    elif policy.name == 'video.motion-plan.v1':")
    baseline['deploy/acceptance_probe.py'] = '''PROFILES = {'ppt.' + stage + '.v1' for stage in ('generate', 'revise')} | {
    'video.storyboard.v1', 'video.scene.v1', 'video.process-plan.v1', 'video.animation-experiment.v1'}


EXISTING_PROBE_SETTING = True


class ProbeAuthority:
    def result(self, document):
        return {'state': 'completed'}
'''
    candidate['deploy/acceptance_probe.py'] = baseline['deploy/acceptance_probe.py'].replace(
        "'video.animation-experiment.v1'}", "'video.animation-experiment.v1', " +
        ', '.join(map(repr, overlay.PROFILES)) + '}').replace(
        "return {'state': 'completed'}", "return {'state': 'completed', 'model_image_evidence': measured_images()}")
    baseline['deploy/candidate_scenario.py'] = '''def load_scenario(path, source_dir, parent_dir, policy):
    if policy.name.startswith('ppt.'):
        validate_old('ppt')
    elif policy.name == 'video.animation-experiment.v1':
        validate_old('experiment')
    elif policy.name == 'video.motion-plan.v1':
        validate_old('motion')
    elif policy.name in {'video.storyboard.v1', 'video.scene.v1'}:
        validate_old('video')
    elif policy.name == 'research.generate.v1':
        validate_old('research')
    elif workflow:
        validate_old('workflow')
    else:
        validate_old('research revision')
    return source_dir
'''
    role_branch = "    elif policy.name in {" + ', '.join(map(repr, overlay.PROFILES)) + "}:\n"
    role_branch += "        implementation(policy.name).validate_inputs(None, task, input_root=source_dir)\n"
    candidate['deploy/candidate_scenario.py'] = baseline['deploy/candidate_scenario.py'].replace(
        "    elif policy.name in {'video.storyboard.v1', 'video.scene.v1'}:", role_branch +
        "    elif policy.name in {'video.storyboard.v1', 'video.scene.v1'}:")
    donors = deepcopy(baseline)
    donors['registry.py'] = donors['registry.py'].replace("'video-process-plan-output.v1',", '').replace(
        'len(routes) > CONTRACT_RECORD_LIMIT', 'len(routes) > 100')
    donors['resources.py'] = donors['resources.py'].replace(
        "    elif policy.name == 'video.motion-plan.v1':\n        return motion_resources\n", '').replace(
        "policy.name in {'video.storyboard.v1', 'video.scene.v1'}", "policy.name.startswith('video.')")
    donors['deploy/acceptance_probe.py'] = donors['deploy/acceptance_probe.py'].replace(
        "'video.process-plan.v1', 'video.animation-experiment.v1'}", "} | {'video.animation-experiment.v1'}")
    donors['deploy/candidate_scenario.py'] = donors['deploy/candidate_scenario.py'].replace(
        "    elif policy.name == 'video.animation-experiment.v1':\n        validate_old('experiment')\n", '').replace(
        "    elif policy.name == 'video.motion-plan.v1':\n        validate_old('motion')\n", '')
    donors = {name: value.replace('\n', newline) for name, value in donors.items()}
    return baseline, candidate, donors


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_inspected_older_pi_shapes_keep_quotas_members_and_original_video_routing(newline):
    baseline, candidate, donors = older_pi_sources(newline)
    patched, receipts = overlay.patched_sources(donors, baseline, candidate)
    measurements = {row['name']: row for row in receipts}
    expected_rebased = {'registry.py', 'resources.py', 'deploy/acceptance_probe.py', 'deploy/candidate_scenario.py'}
    assert {name for name, row in measurements.items() if row['ast_rebase']} == expected_rebased
    assert all(row['untouched_bytes_preserved'] and row['reversible_to_exact_donor'] for row in receipts)
    assert all(row['donor_sha256'] == overlay.sha(donors[row['name']].encode()) for row in receipts)
    if newline == '\r\n':
        assert all(raw.count(b'\n') == raw.count(b'\r\n') for raw in patched.values())

    registry = {}
    exec(patched['registry.py'], registry)
    schema = set(registry['OutputSchema'].__args__)
    expected_schemas = {'video-animation-observe-output.v1', 'video-animation-review-output.v1',
                        'video-animation-create-output.v2', 'video-animation-review-output.v2'}
    assert schema == {'video-motion-plan-output.v1', 'video-animation-experiment-output.v1'} | expected_schemas
    config = registry['DeploymentRegistry']()
    assert config.from_config(SimpleNamespace(name='video-animation-create.v2'), [None] * 100) == 'video-animation-create-contract.v2'
    with pytest.raises(ValueError, match='Invalid bounded routes'):
        config.from_config(SimpleNamespace(name='video-animation-create.v2'), [None] * 101)

    old_values = ('old_ppt_resources', 'old_research_resources', 'frozen_experiment_resources', 'old_video_resources')
    resources = {name: name for name in (*old_values, 'review_resources', 'create_resources', 'sequence_resources')}
    exec(patched['resources.py'], resources)
    for profile, expected in (
            ('video.animation-observe.v1', 'review_resources'), ('video.animation-review.v1', 'review_resources'),
            ('video.animation-review.v2', 'sequence_resources'),
            ('video.animation-create.v2', 'create_resources'), ('video.animation-experiment.v1', 'frozen_experiment_resources'),
            ('video.motion-plan.v1', 'old_video_resources'), ('video.storyboard.v1', 'old_video_resources')):
        assert resources['measured_package'](None, SimpleNamespace(name=profile), None) == expected
    assert 'motion_resources' not in patched['resources.py'].decode()

    probe, original_probe = {}, {}
    exec(patched['deploy/acceptance_probe.py'], probe)
    exec(donors['deploy/acceptance_probe.py'], original_probe)
    assert probe['PROFILES'] == original_probe['PROFILES'] | set(overlay.PROFILES)
    assert 'video.process-plan.v1' not in probe['PROFILES']

    calls = []
    handler = SimpleNamespace(validate_inputs=lambda *args, **kw: calls.append(('new', args, kw)))
    scenario = dict(implementation=lambda name: handler, task='projected-task', validate_old=calls.append, workflow=False)
    exec(patched['deploy/candidate_scenario.py'], scenario)
    for profile in overlay.PROFILES:
        assert scenario['load_scenario'](None, 'projected-files', None, SimpleNamespace(name=profile)) == 'projected-files'
        assert calls[-1] == ('new', (None, 'projected-task'), {'input_root': 'projected-files'})
    for profile, expected in (('ppt.generate.v1', 'ppt'), ('video.scene.v1', 'video'),
                              ('research.generate.v1', 'research')):
        scenario['load_scenario'](None, 'projected-files', None, SimpleNamespace(name=profile))
        assert calls[-1] == expected
    assert "validate_old('motion')" not in patched['deploy/candidate_scenario.py'].decode()


@pytest.mark.parametrize('kind,code', [
    ('suite', 'overlay_ast_donor_suite_changed'),
    ('shadow', 'overlay_ast_prior_predicate_shadows_role'),
    ('unknown-predicate', 'overlay_ast_prior_predicate_not_reviewed'),
    ('remaining-context', 'overlay_donor_patch_context_changed:registry.py'),
])
def test_older_pi_rebase_rejects_unknown_suite_shadowing_or_remaining_context(kind, code):
    baseline, candidate, donors = older_pi_sources()
    if kind == 'suite':
        donors['registry.py'] = donors['registry.py'].replace("c.name.replace('.v1', '-contract.v1')", 'unreviewed_suite(c)')
    elif kind == 'remaining-context':
        donors['registry.py'] = donors['registry.py'].replace('from typing import Literal', 'from typing import Literal, Any')
    else:
        predicate = "policy.name.startswith('video.')" if kind == 'shadow' else 'unreviewed(policy)'
        donors['resources.py'] = donors['resources.py'].replace(
            "    elif policy.name == 'video.animation-experiment.v1':",
            '    elif ' + predicate + ":\n        return unrelated_resources\n" +
            "    elif policy.name == 'video.animation-experiment.v1':")
    with pytest.raises(RuntimeError, match=code):
        overlay.patched_sources(donors, baseline, candidate)


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_ast_splice_uses_utf8_columns_and_restores_original_unicode_bytes(newline):
    source = "中文 = '值'; OutputSchema = Literal['video-animation-experiment-output.v1']\n".replace('\n', newline)
    node = overlay._assignment(source, 'OutputSchema').value
    edits = []
    result = overlay._replace_node(source, node, "Literal['new-schema']", edits, 'test')
    assert result == "中文 = '值'; OutputSchema = Literal['new-schema']\n".replace('\n', newline)
    edit = edits[0]
    restored = result[:edit['offset']] + edit['before'] + result[edit['offset'] + len(edit['after']):]
    assert restored.encode() == source.encode()
    ast.parse(result)


def archive_bytes(files, revision):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT,
                      pax_headers={'comment': revision}) as archive:
        for name, raw in files.items():
            raw = raw.encode() if isinstance(raw, str) else raw
            item = tarfile.TarInfo(name)
            item.mode, item.uid, item.size = 0o644, 0, len(raw)
            archive.addfile(item, io.BytesIO(raw))
    return buffer.getvalue()


def test_baseline_archive_is_pinned_to_revision_hash_and_exact_required_inventory(tmp_path):
    baseline, _ = sources()
    revision = 'a' * 40
    raw = archive_bytes({'agent_service/' + name: value for name, value in baseline.items()}, revision)
    path = tmp_path / 'baseline.tar'
    path.write_bytes(raw)
    actual = overlay.baseline_sources(tmp_path, revision, archive_path=path, archive_sha256=overlay.sha(raw))
    assert actual == {name: value.encode() for name, value in baseline.items()}
    with pytest.raises(RuntimeError, match='overlay_baseline_archive_sha256_mismatch'):
        overlay.baseline_sources(tmp_path, revision, archive_path=path, archive_sha256='f' * 64)
    with pytest.raises(RuntimeError, match='overlay_baseline_archive_revision_mismatch'):
        overlay.baseline_sources(tmp_path, 'b' * 40, archive_path=path, archive_sha256=overlay.sha(raw))


def build_fixture(tmp_path, monkeypatch, *, tamper_resource=False, runtime_changed=False, push=False):
    baseline, candidate = sources()
    repository = tmp_path / 'source'
    real_root = Path(overlay.__file__).resolve().parents[2]
    for name, value in candidate.items():
        path = repository / 'agent_service' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value.encode())
    for name in overlay.NEW_NAMES:
        path = repository / 'agent_service' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((real_root / 'agent_service' / name).read_bytes())
    donor_image = 'registry.internal/old-control@sha256:' + 'a' * 64
    target = 'registry.internal/review-control:latest'
    calls, captured = [], {}
    before = {
        'profiles': {'profiles/__init__.py': dict(sha256=overlay.sha(baseline['profiles/__init__.py'].encode()),
            bytes=len(baseline['profiles/__init__.py'].encode())),
            'profiles/video_animation_experiment.py': dict(sha256='d' * 64, bytes=51)},
        'resources': {'resources/animation-experiment-v1/manifest.json': dict(sha256='e' * 64, bytes=23),
                      'resources/video-workbench-v1/LICENSE': dict(sha256='f' * 64, bytes=12)},
    }

    def run(arguments, **settings):
        calls.append(arguments)
        if arguments[:2] == ['git', '-C']:
            return ('9' * 40).encode() if 'rev-parse' in arguments else b''
        if arguments[:2] == ['docker', 'build']:
            assert arguments[arguments.index('--network') + 1] == 'none'
            with tarfile.open(fileobj=io.BytesIO(settings['input'])) as archive:
                captured['files'] = {item.name.removeprefix('overlay/'): archive.extractfile(item).read()
                    for item in archive.getmembers() if item.name.startswith('overlay/')}
                captured['dockerfile'] = archive.extractfile('Dockerfile').read().decode()
            return b''
        assert arguments == ['docker', 'push', target]
        return b''

    def inventory(image, tree):
        result = deepcopy(before[tree])
        if image == donor_image:
            return result
        for name, raw in captured['files'].items():
            if name.startswith(tree + '/'):
                result[name] = dict(sha256=overlay.sha(raw), bytes=len(raw))
        if tamper_resource and tree == 'resources':
            result['resources/animation-experiment-v1/manifest.json']['sha256'] = '0' * 64
        return result

    def inspect(image):
        return dict(Id='sha256:' + ('a' if image == donor_image else 'b') * 64,
            Config=dict(Env=['UNCHANGED=1'] if image == donor_image or not runtime_changed else ['MUTATED=1']),
            RepoDigests=[target.removesuffix(':latest') + '@sha256:' + 'c' * 64])

    monkeypatch.setattr(overlay, 'run', run)
    monkeypatch.setattr(overlay, 'inspect', inspect)
    monkeypatch.setattr(overlay, 'baseline_sources', lambda *a, **kw: baseline)
    monkeypatch.setattr(overlay, 'donor_sources', lambda image, names=overlay.PATCH_NAMES:
        baseline if image == donor_image else {name: captured['files'][name].decode() for name in names})
    monkeypatch.setattr(overlay, 'image_tree_inventory', inventory)
    arguments = SimpleNamespace(repository=repository, revision='9' * 40, baseline_revision='8' * 40,
        builder_revision='7' * 40, base_image=donor_image, target_image=target, output=tmp_path / 'receipt.json',
        source_archive=None, archive_sha256=None, baseline_archive=None, baseline_archive_sha256=None, push=push)
    return arguments, calls, captured


def test_build_preserves_old_resources_and_runtime_and_never_pushes_implicitly(tmp_path, monkeypatch):
    args, calls, captured = build_fixture(tmp_path, monkeypatch)
    receipt = overlay.build(args)
    assert receipt['image'] is None and receipt['pushed'] is False
    assert receipt['revision'] == '9' * 40 and receipt['baseline_revision'] == '8' * 40
    assert receipt['builder_revision'] == '7' * 40
    assert not any(call[:2] == ['docker', 'push'] for call in calls)
    assert set(captured['files']) == set(overlay.NEW_NAMES) | {
        name for name, detail in ((row['name'], row) for row in receipt['source_patches']) if detail['patch_count']}
    assert 'profiles/video_animation_experiment.py' not in captured['files']
    assert 'resources/animation-experiment-v1/manifest.json' not in captured['files']
    assert receipt['preserved_donor_trees']['resources']['original_resources_unchanged'] is True
    assert receipt['model_requests'] == receipt['model_tasks'] == 0
    assert receipt['deployment_changed'] is receipt['production_routes_changed'] is False
    assert 'COPY overlay/ ' in captured['dockerfile'] and 'FROM ' + args.base_image in captured['dockerfile']
    assert receipt['profiles'] == list(overlay.PROFILES)
    assert receipt['profiles'][-1] == 'video.animation-review.v2'
    from agent_service.profiles import video_animation_review as review
    manifest = json.loads(captured['files']['resources/animation-review-v2/manifest.json'])
    assert overlay.sha(captured['files']['resources/animation-review-v2/manifest.json']) == review.SEQUENCE_BUNDLE_MANIFEST_SHA256
    assert manifest['bundle_revision'] == review.SEQUENCE_BUNDLE_REVISION
    for row in manifest['local_files']:
        name = 'resources/animation-review-v2/' + row['path']
        assert row['sha256'] == overlay.sha(captured['files'][name]) == receipt['new_files'][name]


def test_overlay_inventory_adds_explicit_sequence_resources_without_replacing_old_experiment():
    root = Path(overlay.__file__).resolve().parents[2]
    files = overlay._new_files(root)
    assert set(files) == set(overlay.NEW_NAMES)
    sequence = {name for name in files if name.startswith('resources/animation-review-v2/')}
    assert sequence == {'resources/animation-review-v2/' + name for name in
                        ('manifest.json', 'CONTRACT.md', 'LICENSE', 'review-procedure/SKILL.md')}
    assert not any(name.startswith('resources/animation-experiment-v1/') for name in files)
    assert 'profiles/video_animation_experiment.py' not in files


@pytest.mark.parametrize('part', ['output_schema', 'resource_branch', 'candidate_branch'])
def test_old_donor_rebase_cannot_silently_omit_the_sequence_role(part):
    baseline, candidate, donors = older_pi_sources()
    if part == 'output_schema':
        candidate['registry.py'] = candidate['registry.py'].replace("'video-animation-review-output.v2',", '')
        code = 'overlay_ast_schema_delta_not_reviewed'
    elif part == 'resource_branch':
        candidate['resources.py'] = candidate['resources.py'].replace(
            "    elif policy.name == 'video.animation-review.v2':\n        return sequence_resources\n", '')
        code = 'overlay_ast_new_role_branch_required'
    else:
        candidate['deploy/candidate_scenario.py'] = candidate['deploy/candidate_scenario.py'].replace(
            ", 'video.animation-review.v2'", '')
        code = 'overlay_ast_new_role_branch_required'
    with pytest.raises(RuntimeError, match=code):
        overlay.patched_sources(donors, baseline, candidate)


def test_push_requires_explicit_flag_and_returns_latest_with_immutable_digest(tmp_path, monkeypatch):
    args, calls, _ = build_fixture(tmp_path, monkeypatch, push=True)
    receipt = overlay.build(args)
    assert receipt['image'] == args.target_image + '@sha256:' + 'c' * 64
    assert receipt['pushed'] is True
    assert [args for args in calls if args[:2] == ['docker', 'push']] == [['docker', 'push', args.target_image]]


@pytest.mark.parametrize('setting,code', [
    ({'tamper_resource': True}, 'overlay_existing_file_changed'),
    ({'runtime_changed': True}, 'overlay_inherited_runtime_configuration_changed')])
def test_changed_old_resources_or_runtime_fail_before_push_and_receipt(tmp_path, monkeypatch, setting, code):
    args, calls, _ = build_fixture(tmp_path, monkeypatch, push=True, **setting)
    with pytest.raises(RuntimeError, match=code):
        overlay.build(args)
    assert not args.output.exists()
    assert not any(call[:2] == ['docker', 'push'] for call in calls)


def test_image_inventory_reads_never_started_container_and_always_removes_it(monkeypatch):
    raw = archive_bytes({'resources/old/manifest.json': b'unchanged'}, 'a' * 40)
    calls = []

    def run(arguments, **settings):
        calls.append(arguments)
        return raw if arguments[:2] == ['docker', 'cp'] else b''

    monkeypatch.setattr(overlay, 'run', run)
    measured = overlay.image_tree_inventory('registry/internal@sha256:' + 'a' * 64, 'resources')
    assert measured == {'resources/old/manifest.json': dict(sha256=overlay.sha(b'unchanged'), bytes=9)}
    assert calls[0][:2] == ['docker', 'create'] and '--read-only' in calls[0]
    assert calls[0][calls[0].index('--network') + 1] == 'none'
    assert calls[-1][:2] == ['docker', 'rm']
    assert not any(call[:2] in (['docker', 'start'], ['docker', 'run']) for call in calls)
