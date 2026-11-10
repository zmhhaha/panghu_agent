"""Exact frozen-source v3 transplants; Docker and model calls never execute."""
import ast
from copy import deepcopy
import io
from pathlib import Path
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from agent_service.deploy import animation_review_batch_overlay as overlay


ROOT = Path(overlay.__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def frozen_sources():
    # The builder explicitly promises this repository's frozen Git source, so
    # exercise those exact bytes rather than a second synthetic implementation.
    return {name: subprocess.check_output(['git', '-C', str(ROOT), 'show',
        overlay.BASELINE_REVISION + ':agent_service/' + name]) for name in overlay.PATCH_NAMES}


def source_fixture(frozen_sources, newline='\n'):
    baseline = {name: raw.decode() for name, raw in frozen_sources.items()}
    candidate = {name: (ROOT / 'agent_service' / name).read_text(encoding='utf-8')
                 for name in overlay.PATCH_NAMES}
    donors = deepcopy(baseline)
    # Model the measured Worker's missing process-plan handler. Neither donor
    # extension nor absent unrelated profiles may be replaced from local source.
    for name, function, variable in (
        ('profiles/__init__.py', 'implementation', 'name'),
        ('resources.py', 'measured_package', 'policy.name'),
    ):
        text = donors[name]
        node = next(node for node in ast.walk(ast.parse(text)) if isinstance(node, ast.If)
            and ast.dump(node.test) == ast.dump(ast.parse(variable + " == 'video.process-plan.v1'", mode='eval').body))
        lines = text.splitlines(keepends=True)
        start = sum(map(len, lines[:node.lineno - 1]))
        end = sum(map(len, lines[:node.body[-1].end_lineno]))
        donors[name] = text[:start] + text[end:]
    for name in donors:
        if name != 'profiles/video_animation_review.py':
            donors[name] += '\n\nUNCHANGED_DONOR_EXTENSION = 73\n'
            donors[name] = donors[name].replace('\n', newline)
    return donors, baseline, candidate


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_exact_frozen_review_and_only_v3_shared_edits_preserve_actual_donor(frozen_sources, newline):
    donors, baseline, candidate = source_fixture(frozen_sources, newline)
    files, receipts = overlay.patched_sources(donors, baseline, candidate)
    assert set(files) == set(overlay.PATCH_NAMES)
    assert overlay.prior.sha(frozen_sources['profiles/video_animation_review.py']) == overlay.FROZEN_REVIEW_SOURCE_SHA256
    for name, raw in files.items():
        ast.parse(raw)
        assert overlay.PROFILE in raw.decode() or name == 'registry.py'
        if name != 'profiles/video_animation_review.py':
            assert raw.endswith(('\n\nUNCHANGED_DONOR_EXTENSION = 73\n').replace('\n', newline).encode())
            assert raw.count(b'\r\n') == raw.count(b'\n') if newline == '\r\n' else b'\r\n' not in raw
    assert 'video.process-plan.v1' not in files['profiles/__init__.py'].decode()
    assert 'video.process-plan.v1' not in files['resources.py'].decode()
    assert all(row['untouched_donor_bytes_preserved'] for row in receipts)
    assert [row['name'] for row in receipts if row['frozen_v2_source_verified']] == ['profiles/video_animation_review.py']
    assert files['profiles/video_animation_review.py'] == candidate['profiles/video_animation_review.py'].encode()


@pytest.mark.parametrize('change,code', [
    ('donor_review', 'frozen_review_source_mismatch'),
    ('candidate_frozen_review', 'frozen_review_source_changed'),
    ('candidate_extra_review_function', 'review_extension_not_allowlisted'),
    ('candidate_review_comment', 'existing_review_bytes_changed'),
    ('candidate_registry', 'unrelated_candidate_change'),
    ('candidate_resources', 'unrelated_candidate_change'),
    ('candidate_probe', 'unrelated_candidate_change'),
    ('candidate_scenario', 'unrelated_candidate_change'),
    ('donor_probe_missing_v2', 'donor_probe_changed'),
    ('donor_v2_resource', 'donor_v2_branch_changed'),
    ('already_v3', 'v3_already_present'),
])
def test_source_changes_outside_explicit_v3_delta_are_rejected(frozen_sources, change, code):
    donors, baseline, candidate = source_fixture(frozen_sources)
    if change == 'donor_review':
        donors['profiles/video_animation_review.py'] += '\n# unknown donor change\n'
    elif change == 'candidate_frozen_review':
        candidate['profiles/video_animation_review.py'] = candidate['profiles/video_animation_review.py'].replace(
            'MAX_FRAMES = 24', 'MAX_FRAMES = 12')
    elif change == 'candidate_extra_review_function':
        candidate['profiles/video_animation_review.py'] += '\ndef extra():\n    return True\n'
    elif change == 'candidate_review_comment':
        candidate['profiles/video_animation_review.py'] = candidate['profiles/video_animation_review.py'].replace(
            'MAX_FRAMES = 24', 'MAX_FRAMES = 24  # unrelated comment rewrite')
    elif change in {'candidate_registry', 'candidate_resources', 'candidate_probe', 'candidate_scenario'}:
        name = {'candidate_registry': 'registry.py', 'candidate_resources': 'resources.py',
            'candidate_probe': 'deploy/acceptance_probe.py', 'candidate_scenario': 'deploy/candidate_scenario.py'}[change]
        candidate[name] += '\nUNREVIEWED_CHANGE = True\n'
    elif change == 'donor_probe_missing_v2':
        donors['deploy/acceptance_probe.py'] = donors['deploy/acceptance_probe.py'].replace(overlay.OLD_PROFILE, 'unknown.v2')
    elif change == 'donor_v2_resource':
        donors['resources.py'] = donors['resources.py'].replace('root = SEQUENCE_BUNDLE_ROOT', 'root = CHANGED_ROOT')
    else:
        donors['profiles/__init__.py'] += '\n# ' + overlay.PROFILE + '\n'
    with pytest.raises(RuntimeError, match=code):
        overlay.patched_sources(donors, baseline, candidate)


def build_fixture(tmp_path, monkeypatch, frozen_sources, *, role='worker', changed_runtime=False,
                  changed_old_resource=False, changed_labels=False, changed_tag=None, push=False):
    donors, baseline, candidate = source_fixture(frozen_sources)
    repository = tmp_path / 'source'
    for name, source in candidate.items():
        target = repository / 'agent_service' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.encode())
    for name in overlay.RESOURCE_NAMES:
        target = repository / 'agent_service' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / 'agent_service' / name).read_bytes())
    donor_image = 'registry.internal/current-' + role + ':latest@sha256:' + overlay.DONOR_DIGESTS[role]
    target_image = 'registry.internal/batch-' + role + ':latest'
    before = {
        'profiles': {name: dict(sha256=overlay.prior.sha(raw.encode()), bytes=len(raw.encode()))
            for name, raw in donors.items() if name.startswith('profiles/')},
        'resources': {'resources/animation-review-v2/CONTRACT.md': dict(sha256='d' * 64, bytes=20)},
    }
    before['profiles']['profiles/unrelated.py'] = dict(sha256='e' * 64, bytes=19)
    calls, captured = [], {}

    def run(arguments, **settings):
        calls.append(arguments)
        if arguments[0] == 'git':
            return b'9' * 40 if 'rev-parse' in arguments else b''
        if arguments[:2] == ['docker', 'build']:
            assert arguments[arguments.index('--network') + 1] == 'none'
            assert '--pull=false' in arguments
            with tarfile.open(fileobj=io.BytesIO(settings['input'])) as archive:
                captured['files'] = {member.name.removeprefix('overlay/'): archive.extractfile(member).read()
                    for member in archive.getmembers() if member.name.startswith('overlay/')}
                captured['dockerfile'] = archive.extractfile('Dockerfile').read().decode()
            return b''
        assert arguments == ['docker', 'push', target_image]
        return b''

    def inspect(image):
        original = image == donor_image
        calls.append(['inspect', image])
        target_inspections = sum(call == ['inspect', target_image] for call in calls)
        labels = {'org.opencontainers.image.revision': '9' * 40,
            'io.panghu.animation-review-batch.profile': overlay.PROFILE,
            'io.panghu.animation-review-batch.baseline': overlay.BASELINE_REVISION,
            'io.panghu.animation-review-batch.builder-source-sha256': overlay.prior.sha(Path(overlay.__file__).read_bytes()),
            'io.panghu.animation-review-batch.donor': donor_image}
        if changed_labels:
            labels['io.panghu.animation-review-batch.profile'] = 'wrong-profile'
        changed = not original and ((changed_tag == 'before_push' and target_inspections >= 2)
            or (changed_tag == 'after_push' and target_inspections >= 3))
        return dict(Id='sha256:' + ('a' if original else 'f' if changed else 'b') * 64,
            Config=dict(Env=['UNCHANGED=1'] if original or not changed_runtime else ['CHANGED=1'], Labels=labels),
            RepoDigests=[donor_image.split(':latest@')[0] + '@sha256:' + overlay.DONOR_DIGESTS[role]] if original else
                [target_image.removesuffix(':latest') + '@sha256:' + 'c' * 64])

    def inventory(image, tree):
        result = deepcopy(before[tree])
        if image != donor_image:
            for name, raw in captured['files'].items():
                if name.startswith(tree + '/'):
                    result[name] = dict(sha256=overlay.prior.sha(raw), bytes=len(raw))
            if changed_old_resource and tree == 'resources':
                result['resources/animation-review-v2/CONTRACT.md']['sha256'] = '0' * 64
        return result

    monkeypatch.setattr(overlay.base, 'run', run)
    monkeypatch.setattr(overlay.base, 'inspect', inspect)
    monkeypatch.setattr(overlay.base, 'donor_sources', lambda image, names:
        {name: donors[name] if image == donor_image else captured['files'][name].decode() for name in names})
    monkeypatch.setattr(overlay.prior, 'baseline_sources', lambda *a, **kw: frozen_sources)
    monkeypatch.setattr(overlay.prior, 'image_tree_inventory', inventory)
    args = SimpleNamespace(role=role, repository=repository, revision='9' * 40,
        base_image=donor_image, target_image=target_image, output=tmp_path / 'receipt.json',
        source_archive=None, archive_sha256=None, baseline_archive=None,
        baseline_archive_sha256=None, push=push)
    return args, calls, captured


@pytest.mark.parametrize('role', ['worker', 'control'])
def test_build_only_transplants_v3_and_adds_new_resources_without_deploying(tmp_path, monkeypatch, frozen_sources, role):
    args, calls, captured = build_fixture(tmp_path, monkeypatch, frozen_sources, role=role)
    receipt = overlay.build(args)
    assert receipt['profile'] == overlay.PROFILE and receipt['baseline_revision'] == overlay.BASELINE_REVISION
    assert receipt['image'] is None and receipt['pushed'] is False
    assert receipt['old_resource_files_unchanged'] is True
    assert receipt['unrelated_donor_bytes_preserved'] is True
    assert receipt['model_requests'] == receipt['model_tasks'] == 0
    assert receipt['deployment_changed'] is receipt['production_routes_changed'] is False
    assert receipt['acceptance_created'] is receipt['credentials_opened'] is False
    assert set(captured['files']) == set(overlay.PATCH_NAMES) | set(overlay.RESOURCE_NAMES)
    assert not any(name.startswith('resources/animation-review-v2/') for name in captured['files'])
    assert not any(call[:2] == ['docker', 'push'] for call in calls)
    assert 'FROM ' + args.base_image in captured['dockerfile']
    assert 'io.panghu.animation-review-batch.profile="video.animation-review.v3"' in captured['dockerfile']


def test_push_is_explicit_and_records_the_measured_latest_digest(tmp_path, monkeypatch, frozen_sources):
    args, calls, _ = build_fixture(tmp_path, monkeypatch, frozen_sources, push=True)
    receipt = overlay.build(args)
    assert receipt['image'] == args.target_image + '@sha256:' + 'c' * 64
    assert [call for call in calls if call[:2] == ['docker', 'push']] == [['docker', 'push', args.target_image]]


@pytest.mark.parametrize('when,code', [('before_push', 'measured_latest_identity_changed'),
                                      ('after_push', 'pushed_identity_changed')])
def test_concurrent_latest_retag_cannot_be_recorded_as_the_measured_release(tmp_path, monkeypatch, frozen_sources, when, code):
    args, calls, _ = build_fixture(tmp_path, monkeypatch, frozen_sources, push=True, changed_tag=when)
    with pytest.raises(RuntimeError, match=code):
        overlay.build(args)
    assert not args.output.exists()
    pushes = [call for call in calls if call[:2] == ['docker', 'push']]
    assert len(pushes) == (0 if when == 'before_push' else 1)


@pytest.mark.parametrize('setting,code', [
    ({'changed_runtime': True}, 'inherited_runtime_changed'),
    ({'changed_old_resource': True}, 'unrelated_donor_file_changed'),
    ({'changed_labels': True}, 'release_labels_mismatch'),
])
def test_build_rejects_unrelated_mutation_before_push_or_receipt(tmp_path, monkeypatch, frozen_sources, setting, code):
    args, calls, _ = build_fixture(tmp_path, monkeypatch, frozen_sources, push=True, **setting)
    with pytest.raises(RuntimeError, match=code):
        overlay.build(args)
    assert not args.output.exists()
    assert not any(call[:2] == ['docker', 'push'] for call in calls)


@pytest.mark.parametrize('field,value', [('base_image', 'registry/other:latest@sha256:' + 'f' * 64),
                                      ('target_image', 'registry/batch:v3'), ('role', 'other')])
def test_builder_rejects_unmeasured_donors_and_nonlatest_tags(tmp_path, monkeypatch, frozen_sources, field, value):
    args, calls, _ = build_fixture(tmp_path, monkeypatch, frozen_sources)
    setattr(args, field, value)
    with pytest.raises(RuntimeError, match='fixed_donor_and_latest_target_required'):
        overlay.build(args)
    assert calls == []
