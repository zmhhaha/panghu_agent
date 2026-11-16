"""Add only review-v3 support to the two measured review-v2 Agent images.

Shared modules keep their actual donor bytes except for explicit v3 AST edits.
The existing review implementation must equal the frozen source exactly. Old
resource trees, other profiles, runtime settings and acceptance are untouched.
This builder does not deploy, open credentials or make Agent/model requests.
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import difflib
import io
import json
from pathlib import Path
import tarfile

from agent_service.deploy import animation_experiment_overlay as base
from agent_service.deploy import animation_review_overlay as prior
from agent_service.profiles import video_animation_review as review


BASELINE_REVISION = '41ce26c7beee6b93739dfb62d14d79cc110ba6f1'
FROZEN_REVIEW_SOURCE_SHA256 = 'c035fb0a293f86753060779da70e34d2d0a1b28bcff6522a8b6f1ac07e969832'
DONOR_DIGESTS = {
    'worker': '38731ff5003aa80d4a98acd9cc77caa1dbe004cac18185509b3d09d51aeabb40',
    'control': 'e17b9578e508ac6699f3be66f52b8fba60baf8bc85bd445696c057076d91cc1a',
}
PROFILE = review.REVIEW_BATCH_PROFILE
OLD_PROFILE = review.REVIEW_SEQUENCE_PROFILE
SCHEMA = 'video-animation-review-output.v3'
PATCH_NAMES = ('profiles/__init__.py', 'registry.py', 'resources.py',
    'deploy/acceptance_probe.py', 'deploy/candidate_scenario.py', 'profiles/video_animation_review.py')
RESOURCE_NAMES = tuple('resources/' + review.BATCH_BUNDLE_REVISION + '/' + name for name in
    ('manifest.json', 'CONTRACT.md', 'LICENSE', 'review-procedure/SKILL.md'))


def require(value, code):
    if not value:
        raise RuntimeError(code)


def _one(rows):
    rows = list(rows)
    require(len(rows) == 1, 'batch_overlay_source_shape_changed')
    return rows[0]


def _function(source, name):
    return _one(node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == name)


def _profile_branch(source, function, profile, *, variable='name'):
    target = ast.dump(ast.parse(variable + ' == ' + repr(profile), mode='eval').body)
    return _one(node for node in ast.walk(_function(source, function))
        if isinstance(node, ast.If) and ast.dump(node.test) == target)


def _branch_node(node):
    result = deepcopy(node)
    result.orelse = []
    return result


def _branch_block(source, node):
    lines = source.splitlines(keepends=True)
    require(node.col_offset == 4, 'batch_overlay_source_shape_changed')
    start = sum(map(len, lines[:node.lineno - 1]))
    end = sum(map(len, lines[:node.body[-1].end_lineno]))
    return start, end, source[start:end]


def _insert_branch(donor, baseline, candidate, function, *, resource=False):
    variable = 'policy.name' if resource else 'name'
    new = _profile_branch(candidate, function, PROFILE, variable=variable)
    old = _profile_branch(baseline, function, OLD_PROFILE, variable=variable)
    actual = _profile_branch(donor, function, OLD_PROFILE, variable=variable)
    require(ast.dump(_branch_node(actual)) == ast.dump(_branch_node(old)),
            'batch_overlay_donor_v2_branch_changed')
    start, end, insertion = _branch_block(candidate, new)
    reduced = candidate[:start] + candidate[end:]
    require(ast.dump(ast.parse(reduced)) == ast.dump(ast.parse(baseline)),
            'batch_overlay_unrelated_candidate_change')
    if resource:
        branches = prior._chain(donor, function)
        anchor = _one(node for node in branches if ast.dump(node.test) == ast.dump(actual.test))
        for node in branches[:branches.index(anchor) + 1]:
            condition = prior._condition(node.test)
            require(condition is not None and not (PROFILE in condition[1] if condition[0] == 'names'
                    else PROFILE.startswith(condition[1])), 'batch_overlay_v3_shadowed')
    _, offset, _ = _branch_block(donor, actual)
    return prior._splice(donor, offset, offset, insertion, [], function + ':v3')


def _append_schema(donor, baseline, candidate):
    old = prior._assignment(baseline, 'OutputSchema').value
    new = prior._assignment(candidate, 'OutputSchema').value
    require(prior._literal_strings(new) == prior._literal_strings(old) | {SCHEMA}
            and SCHEMA not in prior._literal_strings(old), 'batch_overlay_schema_delta_changed')
    old_nodes, new_nodes = prior._nodes(baseline), prior._nodes(candidate)
    require(set(old_nodes) == set(new_nodes)
            and {key for key in new_nodes if new_nodes[key] != old_nodes[key]} == {'OutputSchema'},
            'batch_overlay_unrelated_candidate_change')
    actual = prior._assignment(donor, 'OutputSchema').value
    values = prior._literal_strings(actual)
    require('video-animation-review-output.v2' in values and SCHEMA not in values,
            'batch_overlay_donor_schema_changed')
    last = actual.slice.elts[-1] if isinstance(actual.slice, ast.Tuple) else actual.slice
    offset = prior._offset(donor, last.end_lineno, last.end_col_offset)
    return prior._splice(donor, offset, offset, ', ' + repr(SCHEMA), [], 'OutputSchema:v3')


def _append_probe(donor, baseline, candidate):
    old = prior._assignment(baseline, 'PROFILES').value
    new = prior._assignment(candidate, 'PROFILES').value
    require(prior._set_members(new) == prior._set_members(old) | {PROFILE}
            and PROFILE not in prior._set_members(old), 'batch_overlay_probe_delta_changed')
    restored = prior._replace_node(candidate, new, prior._segment(baseline, old), [], 'restore')
    require(ast.dump(ast.parse(restored)) == ast.dump(ast.parse(baseline)),
            'batch_overlay_unrelated_candidate_change')
    actual = prior._assignment(donor, 'PROFILES').value
    require(OLD_PROFILE in prior._set_members(actual) and PROFILE not in prior._set_members(actual),
            'batch_overlay_donor_probe_changed')
    return prior._replace_node(donor, actual,
        prior._segment(donor, actual) + ' | {' + repr(PROFILE) + '}', [], 'PROFILES:v3')


def _scenario_set(source):
    function = _function(source, 'load_scenario')
    return _one(node for node in ast.walk(function) if isinstance(node, ast.Set)
        and all(isinstance(item, ast.Constant) and type(item.value) is str for item in node.elts)
        and OLD_PROFILE in {item.value for item in node.elts})


def _append_scenario(donor, baseline, candidate):
    old, new, actual = map(_scenario_set, (baseline, candidate, donor))
    values = lambda node: {item.value for item in node.elts}
    require(values(new) == values(old) | {PROFILE} and PROFILE not in values(old),
            'batch_overlay_scenario_delta_changed')
    restored = prior._replace_node(candidate, new, prior._segment(baseline, old), [], 'restore')
    require(ast.dump(ast.parse(restored)) == ast.dump(ast.parse(baseline)),
            'batch_overlay_unrelated_candidate_change')
    require(values(actual) == values(old), 'batch_overlay_donor_scenario_changed')
    # The existing projected-input validator must be the measured v2 branch.
    def branch(source):
        target = ast.dump(_scenario_set(source))
        return _one(node for node in ast.walk(_function(source, 'load_scenario'))
            if isinstance(node, ast.If) and any(ast.dump(child) == target for child in ast.walk(node.test)))
    require(ast.dump(_branch_node(branch(donor))) == ast.dump(_branch_node(branch(baseline))),
            'batch_overlay_donor_v2_branch_changed')
    last = actual.elts[-1]
    offset = prior._offset(donor, last.end_lineno, last.end_col_offset)
    return prior._splice(donor, offset, offset, ', ' + repr(PROFILE), [], 'scenario:v3')


def _review_extension(donor, baseline, candidate):
    require(donor == baseline, 'batch_overlay_frozen_review_source_mismatch')
    def nodes(source):
        result = {}
        for node in ast.parse(source).body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                key = ('class' if isinstance(node, ast.ClassDef) else 'function', node.name)
            elif (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                key = ('assign', node.targets[0].id)
            else:
                key = ('statement', ast.dump(node))
            require(key not in result, 'batch_overlay_duplicate_review_statement')
            result[key] = ast.dump(node)
        return result
    old, new = nodes(baseline), nodes(candidate)
    added = new.keys() - old.keys()
    require(all(new.get(key) == value for key, value in old.items()),
            'batch_overlay_frozen_review_source_changed')
    expected = {('assign', name) for name in ('REVIEW_BATCH_PROFILE', 'BATCH_BUNDLE_REVISION',
        'BATCH_BUNDLE_ROOT', 'BATCH_BUNDLE_FILES', 'BATCH_BUNDLE_MANIFEST_SHA256')}
    expected |= {('function', 'verified_batch_bundle'), ('class', 'VideoAnimationReviewBatchProfile')}
    require(added == expected, 'batch_overlay_review_extension_not_allowlisted')
    expected_class = '''class VideoAnimationReviewBatchProfile(VideoAnimationReviewSequenceProfile):
    """New execution identity for grouped reads; visual evidence gates are unchanged."""
    name = REVIEW_BATCH_PROFILE
    task_schema = 'video-animation-review.v3'
    output_schema = 'video-animation-review-output.v3'

    def _bundle(self):
        return verified_batch_bundle()
'''
    require(new[('class', 'VideoAnimationReviewBatchProfile')] == ast.dump(ast.parse(expected_class).body[0]),
            'batch_overlay_review_class_changed')
    require(all(operation in {'equal', 'insert'} for operation, *_ in
        difflib.SequenceMatcher(a=baseline.splitlines(keepends=True),
            b=candidate.splitlines(keepends=True), autojunk=False).get_opcodes()),
        'batch_overlay_existing_review_bytes_changed')
    return candidate


def patched_sources(donors, baseline_sources, candidate_sources):
    """Return six minimally patched files and a reversible donor-byte receipt."""
    require(set(donors) == set(baseline_sources) == set(candidate_sources) == set(PATCH_NAMES),
            'batch_overlay_source_inventory_mismatch')
    patches = {
        'profiles/__init__.py': lambda d, b, c: _insert_branch(d, b, c, 'implementation'),
        'resources.py': lambda d, b, c: _insert_branch(d, b, c, 'measured_package', resource=True),
        'registry.py': _append_schema, 'deploy/acceptance_probe.py': _append_probe,
        'deploy/candidate_scenario.py': _append_scenario,
        'profiles/video_animation_review.py': _review_extension,
    }
    files, receipts = {}, []
    for name in PATCH_NAMES:
        donor, baseline, candidate = map(prior._text,
            (donors[name], baseline_sources[name], candidate_sources[name]))
        require(PROFILE not in donor, 'batch_overlay_v3_already_present')
        newline = '\r\n' if '\r\n' in donor else '\n'
        require(newline == '\n' or '\n' not in donor.replace('\r\n', ''),
                'batch_overlay_mixed_donor_newlines')
        # The frozen review module is compared as exact bytes, before normalizing
        # shared-module splice coordinates for the donor's newline convention.
        if name == 'profiles/video_animation_review.py':
            result = patches[name](donor, baseline, candidate)
        else:
            result = patches[name](donor.replace('\r\n', '\n'),
                baseline.replace('\r\n', '\n'), candidate.replace('\r\n', '\n')).replace('\n', newline)
        ast.parse(result)
        # Reuse the audited exact-context patcher to prove every changed byte
        # belongs to the selected edit; applying its inverse restores the donor.
        _, receipt = prior._patch_text(name, donor, donor, result) if name in prior.SCOPES else (None, None)
        files[name] = result.encode('utf-8')
        receipts.append(dict(name=name, donor_sha256=prior.sha(donor.encode()),
            baseline_sha256=prior.sha(baseline.encode()), patched_sha256=prior.sha(files[name]),
            untouched_donor_bytes_preserved=True,
            frozen_v2_source_verified=name == 'profiles/video_animation_review.py',
            exact_patch=receipt))
    return files, receipts


def _resources(repository):
    files = {}
    for name in RESOURCE_NAMES:
        path = repository / 'agent_service' / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 128 * 1024,
                'batch_overlay_resource_file_invalid')
        files[name] = path.read_bytes()
    manifest = json.loads(files[RESOURCE_NAMES[0]])
    require(prior.sha(files[RESOURCE_NAMES[0]]) == review.BATCH_BUNDLE_MANIFEST_SHA256
            and manifest['bundle_revision'] == review.BATCH_BUNDLE_REVISION,
            'batch_overlay_bundle_manifest_mismatch')
    for row in manifest['local_files']:
        name = 'resources/' + review.BATCH_BUNDLE_REVISION + '/' + row['path']
        require(name in files and prior.sha(files[name]) == row['sha256'],
                'batch_overlay_bundle_content_mismatch')
    return files


def build(args):
    role = args.role
    require(role in DONOR_DIGESTS and base.IMAGE.fullmatch(args.base_image)
            and args.base_image.rsplit('@sha256:', 1)[1] == DONOR_DIGESTS[role]
            and base.TARGET.fullmatch(args.target_image) and prior.REVISION.fullmatch(args.revision),
            'batch_overlay_fixed_donor_and_latest_target_required')
    require(not args.output.exists() and not args.output.is_symlink(), 'batch_overlay_output_exists')
    repository = args.repository.resolve()
    if getattr(args, 'source_archive', None):
        provenance = base.archive_source(repository, args.source_archive, args.archive_sha256 or '', args.revision)
    else:
        require(not getattr(args, 'archive_sha256', None), 'batch_overlay_source_archive_required')
        require(base.run(['git', '-C', str(repository), 'rev-parse', 'HEAD']).decode().strip() == args.revision,
                'batch_overlay_source_revision_mismatch')
        require(not base.run(['git', '-C', str(repository), 'status', '--porcelain', '--', 'agent_service']).strip(),
                'batch_overlay_agent_source_not_clean')
        provenance = dict(source_git_revision=args.revision)
    baseline = prior.baseline_sources(repository, BASELINE_REVISION,
        archive_path=getattr(args, 'baseline_archive', None),
        archive_sha256=getattr(args, 'baseline_archive_sha256', None), names=PATCH_NAMES)
    require(prior.sha(baseline['profiles/video_animation_review.py']) == FROZEN_REVIEW_SOURCE_SHA256,
            'batch_overlay_frozen_baseline_source_mismatch')
    candidate = {name: (repository / 'agent_service' / name).read_bytes() for name in PATCH_NAMES}
    donor = base.inspect(args.base_image)
    require(any(value.endswith('@sha256:' + DONOR_DIGESTS[role]) for value in donor.get('RepoDigests', [])),
            'batch_overlay_donor_digest_not_measured')
    original = base.donor_sources(args.base_image, PATCH_NAMES)
    files, receipts = patched_sources(original, baseline, candidate)
    additions = _resources(repository)
    before = {tree: prior.image_tree_inventory(args.base_image, tree) for tree in ('profiles', 'resources')}
    require(not set(additions) & set(before['resources']), 'batch_overlay_v3_resources_already_present')
    files.update(additions)
    builder_sha = prior.sha(Path(__file__).read_bytes())
    dockerfile = (f'FROM {args.base_image}\nCOPY overlay/ {base.PACKAGE}\n'
        f'LABEL org.opencontainers.image.revision="{args.revision}" '
        f'io.panghu.animation-review-batch.profile="{PROFILE}" '
        f'io.panghu.animation-review-batch.baseline="{BASELINE_REVISION}" '
        f'io.panghu.animation-review-batch.builder-source-sha256="{builder_sha}" '
        f'io.panghu.animation-review-batch.donor="{args.base_image}"\n').encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for name, raw in [('Dockerfile', dockerfile), *[('overlay/' + key, raw) for key, raw in sorted(files.items())]]:
            item = tarfile.TarInfo(name)
            item.uid = item.gid = item.mtime = 0
            item.mode, item.size = 0o644, len(raw)
            archive.addfile(item, io.BytesIO(raw))
    base.run(['docker', 'build', '--network', 'none', '--pull=false', '--tag', args.target_image, '-'], input=buffer.getvalue())
    built = base.inspect(args.target_image)
    require(all(donor['Config'].get(key) == built['Config'].get(key) for key in base.RUNTIME_KEYS),
            'batch_overlay_inherited_runtime_changed')
    expected_labels = {'org.opencontainers.image.revision': args.revision,
        'io.panghu.animation-review-batch.profile': PROFILE,
        'io.panghu.animation-review-batch.baseline': BASELINE_REVISION,
        'io.panghu.animation-review-batch.builder-source-sha256': builder_sha,
        'io.panghu.animation-review-batch.donor': args.base_image}
    require(all(built['Config'].get('Labels', {}).get(key) == value for key, value in expected_labels.items()),
            'batch_overlay_release_labels_mismatch')
    after = {tree: prior.image_tree_inventory(args.target_image, tree) for tree in ('profiles', 'resources')}
    for tree, inventory in before.items():
        for name, identity in inventory.items():
            expected = dict(sha256=prior.sha(files[name]), bytes=len(files[name])) if name in files else identity
            require(after[tree].get(name) == expected, 'batch_overlay_unrelated_donor_file_changed')
        require(set(after[tree]) == set(inventory) | {name for name in additions if name.startswith(tree + '/')},
                'batch_overlay_built_inventory_mismatch')
    measured = base.donor_sources(args.target_image, tuple(files))
    require(all(prior._text(measured[name]).encode() == raw for name, raw in files.items()),
            'batch_overlay_built_source_mismatch')
    image = None
    if getattr(args, 'push', False):
        require(base.inspect(args.target_image)['Id'] == built['Id'],
                'batch_overlay_measured_latest_identity_changed')
        base.run(['docker', 'push', args.target_image])
        pushed = base.inspect(args.target_image)
        require(pushed['Id'] == built['Id'], 'batch_overlay_pushed_identity_changed')
        prefix = args.target_image.removesuffix(':latest') + '@sha256:'
        identities = [value for value in pushed.get('RepoDigests', []) if value.startswith(prefix)]
        require(len(identities) == 1, 'batch_overlay_pushed_digest_unavailable')
        image = args.target_image + '@' + identities[0].split('@', 1)[1]
    result = dict(schema='animation-review-batch-overlay-build.v1', role=role, profile=PROFILE,
        revision=args.revision, baseline_revision=BASELINE_REVISION, donor=args.base_image,
        donor_image_id=donor['Id'], built_image_id=built['Id'], target=args.target_image,
        image=image, pushed=image is not None, builder_source_sha256=builder_sha,
        source_patches=receipts, new_files={name: prior.sha(raw) for name, raw in additions.items()},
        old_resource_files_unchanged=True, unrelated_donor_bytes_preserved=True,
        inherited_runtime_unchanged=True, model_tasks=0, model_requests=0,
        deployment_changed=False, production_routes_changed=False, acceptance_created=False,
        credentials_opened=False, **provenance)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write('\n')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True, type=Path)
    parser.add_argument('--role', required=True, choices=tuple(DONOR_DIGESTS))
    parser.add_argument('--revision', required=True)
    parser.add_argument('--base-image', required=True)
    parser.add_argument('--target-image', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source-archive', type=Path)
    parser.add_argument('--archive-sha256')
    parser.add_argument('--baseline-archive', type=Path)
    parser.add_argument('--baseline-archive-sha256')
    parser.add_argument('--push', action='store_true')
    args = parser.parse_args(argv)
    print(json.dumps(build(args), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
