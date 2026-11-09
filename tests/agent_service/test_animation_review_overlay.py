"""Minimal donor edits and byte preservation; no Docker/model calls execute."""
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
        "'video-animation-experiment-output.v1']", "'video-animation-experiment-output.v1', 'video-animation-review-output.v1']")
    candidate['registry.py'] += "\n\ndef required_model_capabilities(handler):\n    return handler.required_model_capabilities\n"
    candidate['resources.py'] = candidate['resources.py'].replace('    return other_resources',
        "    if policy.name == 'video.animation-review.v1':\n        return new_resources\n    return other_resources")
    candidate['deploy/acceptance_probe.py'] = candidate['deploy/acceptance_probe.py'].replace(
        "PROFILES = {'video.animation-experiment.v1'}", "PROFILES = {'video.animation-experiment.v1', 'video.animation-review.v1'}")
    candidate['deploy/acceptance_probe.py'] = candidate['deploy/acceptance_probe.py'].replace(
        "return {'state': 'completed'}", "return {'state': 'completed', 'model_image_evidence': measured_images()}")
    candidate['deploy/candidate_scenario.py'] = candidate['deploy/candidate_scenario.py'].replace(
        '    return source_dir', "    if policy.name == 'video.animation-review.v1':\n        implementation(policy.name).validate_inputs(None, task, input_root=source_dir)\n    return source_dir")
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
    baseline, candidate = sources()
    donors = deepcopy(baseline)
    donors['registry.py'] = donors['registry.py'].replace('return True', 'return tightened_gate')
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
