"""Build disabled-role image overlays by applying reviewed, minimal source edits.

The donor must already contain the frozen animation experiment. Shared modules
are patched against an explicit Git baseline, never replaced with local files.
Unknown edits within a patch context fail closed; all other donor bytes survive.
This tool creates no deployment, acceptance, credential, route or model request.
Image push is a separate explicit opt-in; all build targets use ``latest``.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import uuid

from agent_service.deploy import animation_experiment_overlay as prior


PACKAGE = prior.PACKAGE
PROFILES = ('video.animation-observe.v1', 'video.animation-review.v1', 'video.animation-create.v2')
SCOPES = {
    'profiles/__init__.py': {'implementation'},
    'registry.py': {'import:re', 'OutputSchema', 'required_model_capabilities',
                    'TaskContract.reviewed_handler', 'DeploymentRegistry.from_config'},
    'resources.py': {'measured_package'},
    'deploy/acceptance_probe.py': {'PROFILES', 'ProbeAuthority.result'},
    'deploy/candidate_scenario.py': {'load_scenario'},
    'model_proxy.py': {'image_inputs', '_image_inputs', 'image_evidence_for_job', 'ModelProxy.respond'},
    'api.py': {'import:model_proxy:image_evidence_for_job', 'create_app'},
    'client.py': {'AgentClient.model_image_evidence'},
    'worker_entry.py': {'build_pi_runtime_settings'},
}
PATCH_NAMES = tuple(SCOPES)
WORKER_PATCH_NAMES = tuple(name for name in PATCH_NAMES if name not in {'api.py', 'client.py', 'model_proxy.py'})
NEW_NAMES = ('profiles/video_animation_review.py', 'profiles/video_animation_create.py',
    'deploy/animation_review_catalog.py',
    'resources/animation-review-v1/manifest.json', 'resources/animation-review-v1/CONTRACT.md',
    'resources/animation-review-v1/LICENSE', 'resources/animation-review-v1/review-procedure/SKILL.md',
    'resources/animation-create-v2/manifest.json', 'resources/animation-create-v2/CONTRACT.md',
    'resources/animation-create-v2/LICENSE', 'resources/animation-create-v2/creation-procedure/SKILL.md')
REVISION = re.compile(r'[0-9a-f]{40}\Z')


def require(value, code):
    if not value:
        raise RuntimeError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def run(arguments, **kwargs):
    return prior.run(arguments, **kwargs)


def inspect(image):
    return prior.inspect(image)


def donor_sources(image, names=PATCH_NAMES):
    return prior.donor_sources(image, names)


def _nodes(source):
    """Named module statements and individual class members, excluding layout."""
    result = {}

    def visit(rows, prefix=''):
        for node in rows:
            if isinstance(node, ast.ClassDef):
                result[prefix + 'class:' + node.name] = ast.dump(ast.ClassDef(
                    name=node.name, bases=node.bases, keywords=node.keywords, body=[],
                    decorator_list=node.decorator_list, type_params=getattr(node, 'type_params', [])))
                visit(node.body, prefix + node.name + '.')
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result[prefix + node.name] = ast.dump(node)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [target.id for target in targets if isinstance(target, ast.Name)]
                require(names, 'overlay_unnamed_module_assignment')
                result[prefix + ','.join(names)] = ast.dump(node)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                key = 'import:' + (node.module + ':' if isinstance(node, ast.ImportFrom) else '')
                key += ','.join(alias.name + (' as ' + alias.asname if alias.asname else '') for alias in node.names)
                result[prefix + key] = ast.dump(node)
            else:
                # Module/class docstrings and fixed declarations must also
                # remain unchanged. Source comments are not executable code.
                key = prefix + 'statement:' + ast.dump(node)
                result[key] = key
    visit(ast.parse(source).body)
    return result


def _allowed_changes(name, baseline, candidate):
    old, new = _nodes(baseline), _nodes(candidate)
    changed = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
    require(changed <= SCOPES[name], 'overlay_unreviewed_source_scope:' + name)
    return sorted(changed)


def _text(raw):
    return raw.decode('utf-8') if isinstance(raw, bytes) else raw


def _one(rows, code):
    rows = list(rows)
    require(len(rows) == 1, code)
    return rows[0]


def _assignment(source, name):
    return _one((node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Assign)
        and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name), 'overlay_ast_assignment_not_unique:' + name)


def _offset(source, line, column=0):
    # AST columns count UTF-8 bytes, whereas splice offsets index Unicode text.
    lines = source.splitlines(keepends=True)
    prefix = lines[line - 1].encode('utf-8')[:column].decode('utf-8')
    return sum(map(len, lines[:line - 1])) + len(prefix)


def _splice(source, start, end, replacement, edits, target):
    original = source[start:end]
    edits.append(dict(offset=start, before=original, after=replacement, target=target))
    return source[:start] + replacement + source[end:]


def _replace_node(source, node, replacement, edits, target):
    return _splice(source, _offset(source, node.lineno, node.col_offset),
        _offset(source, node.end_lineno, node.end_col_offset), replacement, edits, target)


def _segment(source, node):
    return ast.get_source_segment(source, node)


def _literal_strings(node):
    require(isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
            and node.value.id == 'Literal', 'overlay_ast_literal_shape_changed')
    items = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
    require(all(isinstance(item, ast.Constant) and type(item.value) is str for item in items),
            'overlay_ast_literal_shape_changed')
    values = [item.value for item in items]
    require(len(values) == len(set(values)), 'overlay_ast_literal_shape_changed')
    return set(values)


def _set_members(node):
    """Inspect a literal union; an existing comprehension is an opaque identity."""
    if isinstance(node, ast.Set):
        require(all(isinstance(item, ast.Constant) and type(item.value) is str for item in node.elts),
                'overlay_ast_profile_set_shape_changed')
        return {item.value for item in node.elts}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _set_members(node.left) | _set_members(node.right)
    if isinstance(node, ast.SetComp):
        return {'comprehension:' + ast.dump(node)}
    raise RuntimeError('overlay_ast_profile_set_shape_changed')


def _condition(test):
    name = ast.dump(ast.parse('policy.name', mode='eval').body)
    if (isinstance(test, ast.Compare) and ast.dump(test.left) == name
            and len(test.ops) == len(test.comparators) == 1):
        value = test.comparators[0]
        if isinstance(test.ops[0], ast.Eq) and isinstance(value, ast.Constant) and type(value.value) is str:
            return ('names', frozenset({value.value}))
        if isinstance(test.ops[0], ast.In) and isinstance(value, ast.Set) and all(
                isinstance(item, ast.Constant) and type(item.value) is str for item in value.elts):
            return ('names', frozenset(item.value for item in value.elts))
    if (isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute)
            and ast.dump(test.func.value) == name and test.func.attr == 'startswith'
            and len(test.args) == 1 and not test.keywords and isinstance(test.args[0], ast.Constant)
            and test.args[0].value in {'ppt.', 'research.', 'video.'}):
        return ('prefix', test.args[0].value)
    return None


def _chain(source, function):
    scope = _one((node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)
                  and node.name == function), 'overlay_ast_function_not_unique:' + function)
    node = _one((node for node in scope.body if isinstance(node, ast.If)
                 and _condition(node.test) == ('prefix', 'ppt.')),
                'overlay_ast_profile_chain_not_unique:' + function)
    result = []
    while True:
        result.append(node)
        if len(node.orelse) != 1 or not isinstance(node.orelse[0], ast.If):
            return result
        node = node.orelse[0]


def _branch_text(source, node):
    lines = source.splitlines(keepends=True)
    require(node.col_offset == 4 and lines[node.lineno - 1].startswith('    elif '),
            'overlay_ast_branch_shape_changed')
    start = _offset(source, node.lineno)
    end = sum(map(len, lines[:node.body[-1].end_lineno]))
    return start, end, source[start:end]


def _insert_role_branches(donor, candidate, function, groups, anchor_names, newline, edits):
    """Add only exact new-role predicates before a verified non-shadowed branch."""
    inserted = []
    for names in groups:
        node = _one((node for node in _chain(candidate, function)
            if _condition(node.test) == ('names', frozenset(names))), 'overlay_ast_new_role_branch_required')
        start, end, raw = _branch_text(candidate, node)
        inserted.append(raw.replace('\n', newline))
        candidate = candidate[:start] + candidate[end:]
    branches = _chain(donor, function)
    anchor = _one((node for node in branches if _condition(node.test) == ('names', frozenset(anchor_names))),
                  'overlay_ast_donor_branch_required')
    for node in branches[:branches.index(anchor) + 1]:
        predicate = _condition(node.test)
        require(predicate is not None, 'overlay_ast_prior_predicate_not_reviewed')
        require(not any(profile in predicate[1] if predicate[0] == 'names' else profile.startswith(predicate[1])
                        for profile in PROFILES), 'overlay_ast_prior_predicate_shadows_role')
    start, _, _ = _branch_text(donor, anchor)
    donor = _splice(donor, start, start, ''.join(inserted), edits, function + ':new-role-branches')
    return donor, candidate


def _ast_rebase(name, donor, baseline, candidate, newline):
    """Explicit node edits for the inspected Pi donor's older shared structure.

    This is not fuzzy matching. Existing Literal/set members, quotas, resource
    roots and unrelated statements stay exactly as found. Only declared role
    additions and a structurally identical old suite expression may change.
    All remaining source deltas still require their original exact contexts.
    """
    edits = []
    if name == 'registry.py':
        old = _assignment(baseline, 'OutputSchema').value
        new = _assignment(candidate, 'OutputSchema').value
        required = {'video-animation-observe-output.v1', 'video-animation-review-output.v1',
                    'video-animation-create-output.v2'}
        require(_literal_strings(new) - _literal_strings(old) == required
                and _literal_strings(old) <= _literal_strings(new), 'overlay_ast_schema_delta_not_reviewed')
        actual = _assignment(donor, 'OutputSchema').value
        values = _literal_strings(actual)
        require('video-animation-experiment-output.v1' in values and not values & required,
                'overlay_ast_donor_schema_not_reviewed')
        last = actual.slice.elts[-1] if isinstance(actual.slice, ast.Tuple) else actual.slice
        position = _offset(donor, last.end_lineno, last.end_col_offset)
        donor = _splice(donor, position, position, ', ' + ', '.join(map(repr, sorted(required))),
                        edits, 'OutputSchema:append-new-roles')
        candidate = _replace_node(candidate, new, _segment(baseline, old), [], 'OutputSchema:restore-baseline')
        old = _assignment(baseline, 'suite').value
        new = _assignment(candidate, 'suite').value
        actual = _assignment(donor, 'suite').value
        require(ast.dump(actual) == ast.dump(old), 'overlay_ast_donor_suite_changed')
        donor = _replace_node(donor, actual, _segment(candidate, new).replace('\n', newline), edits,
                              'suite:versioned-contract-expression')
        candidate = _replace_node(candidate, new, _segment(baseline, old), [], 'suite:restore-baseline')
    elif name == 'deploy/acceptance_probe.py':
        old = _assignment(baseline, 'PROFILES').value
        new = _assignment(candidate, 'PROFILES').value
        require(_set_members(new) - _set_members(old) == set(PROFILES)
                and _set_members(old) <= _set_members(new), 'overlay_ast_profile_delta_not_reviewed')
        actual = _assignment(donor, 'PROFILES').value
        members = _set_members(actual)
        require('video.animation-experiment.v1' in members and not members & set(PROFILES),
                'overlay_ast_donor_profiles_not_reviewed')
        donor = _replace_node(donor, actual, _segment(donor, actual) + ' | {' + ', '.join(map(repr, PROFILES)) + '}',
                              edits, 'PROFILES:append-new-roles')
        candidate = _replace_node(candidate, new, _segment(baseline, old), [], 'PROFILES:restore-baseline')
    elif name == 'resources.py':
        donor, candidate = _insert_role_branches(donor, candidate, 'measured_package',
            [PROFILES[:2], (PROFILES[2],)], {'video.animation-experiment.v1'}, newline, edits)
    elif name == 'deploy/candidate_scenario.py':
        donor, candidate = _insert_role_branches(donor, candidate, 'load_scenario',
            [PROFILES], {'video.storyboard.v1', 'video.scene.v1'}, newline, edits)
    else:
        raise RuntimeError('overlay_donor_patch_context_changed:' + name)
    return donor, candidate, edits


def _exact_edits(name, donor, baseline, candidate, newline):
    left, right = baseline.splitlines(keepends=True), candidate.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
    result, edits = donor, []
    for group in matcher.get_grouped_opcodes(3):
        first, last = group[0], group[-1]
        before = ''.join(left[first[1]:last[2]]).replace('\n', newline)
        after = ''.join(right[first[3]:last[4]]).replace('\n', newline)
        require(before and result.count(before) == 1, 'overlay_donor_patch_context_changed:' + name)
        offset = result.index(before)
        result = _splice(result, offset, offset + len(before), after, edits, 'exact-source-context')
    return result, edits


def _patch_text(name, donor, baseline, candidate):
    newline = '\r\n' if '\r\n' in donor else '\n'
    require(newline == '\n' or '\n' not in donor.replace('\r\n', ''), 'overlay_mixed_donor_newlines:' + name)
    baseline = baseline.replace('\r\n', '\n')
    candidate = candidate.replace('\r\n', '\n')
    scopes = _allowed_changes(name, baseline, candidate)
    ast_edits = []
    try:
        result, edits = _exact_edits(name, donor, baseline, candidate, newline)
    except RuntimeError as error:
        if str(error) != 'overlay_donor_patch_context_changed:' + name:
            raise
        rebased, reduced, ast_edits = _ast_rebase(name, donor, baseline, candidate, newline)
        result, exact = _exact_edits(name, rebased, baseline, reduced, newline)
        edits = ast_edits + exact
    restored = result
    for edit in reversed(edits):
        offset, after = edit['offset'], edit['after']
        require(restored[offset:offset + len(after)] == after, 'overlay_patch_inverse_failed:' + name)
        restored = restored[:offset] + edit['before'] + restored[offset + len(after):]
    require(restored == donor, 'overlay_unrelated_donor_bytes_changed:' + name)
    ast.parse(result)
    return result, dict(name=name, donor_sha256=sha(donor.encode()), patched_sha256=sha(result.encode()),
        modified_scopes=scopes, patch_count=len(edits), untouched_bytes_preserved=True,
        reversible_to_exact_donor=True, ast_rebase=[dict(target=edit['target'],
            before_sha256=sha(edit['before'].encode()), after_sha256=sha(edit['after'].encode()))
            for edit in ast_edits])


def patched_sources(donors, baseline_sources, candidate_sources, *, role='control'):
    """Pure minimal patching, with a reversible proof per shared source file."""
    require(role in {'control', 'worker'}, 'overlay_image_role_required')
    names = PATCH_NAMES if role == 'control' else WORKER_PATCH_NAMES
    require(set(donors) == set(baseline_sources) == set(candidate_sources) == set(names),
            'overlay_source_inventory_mismatch')
    require('video.animation-experiment.v1' in _text(donors['profiles/__init__.py']),
            'overlay_existing_experiment_donor_required')
    require(not any(profile in _text(donors['profiles/__init__.py']) for profile in PROFILES),
            'overlay_new_roles_already_present')
    require(all(profile in _text(candidate_sources['profiles/__init__.py']) for profile in PROFILES),
            'overlay_candidate_role_dispatch_missing')
    require('model_image_evidence' in _text(candidate_sources['deploy/acceptance_probe.py'])
            and (role != 'control' or 'image_evidence_for_job' in _text(candidate_sources['model_proxy.py'])),
            'overlay_candidate_image_transport_evidence_missing')
    files, measurements = {}, []
    for name in names:
        value, measurement = _patch_text(name, _text(donors[name]),
            _text(baseline_sources[name]), _text(candidate_sources[name]))
        if measurement['patch_count']:
            files[name] = value.encode('utf-8')
        measurements.append(measurement)
    resulting = {name: files.get(name, _text(donors[name]).encode()).decode() for name in names}
    require(all(profile in resulting['profiles/__init__.py'] for profile in PROFILES)
            and 'required_model_capabilities' in resulting['registry.py']
            and 'model_image_evidence' in resulting['deploy/acceptance_probe.py']
            and 'input_root' in resulting['deploy/candidate_scenario.py'],
            'overlay_resulting_role_support_incomplete')
    if role == 'control':
        require('image_evidence_for_job' in resulting['model_proxy.py']
                and 'image_evidence_for_job' in resulting['api.py']
                and 'model_image_evidence' in resulting['client.py'],
                'overlay_resulting_transport_support_incomplete')
    require(bool(files), 'overlay_no_source_changes')
    return files, measurements


def baseline_sources(repository, revision, *, archive_path=None, archive_sha256=None, names=PATCH_NAMES):
    """Read the explicit reviewed baseline from Git or a measured Git archive."""
    require(type(revision) is str and REVISION.fullmatch(revision), 'overlay_baseline_revision_required')
    if archive_path is None:
        require(archive_sha256 is None, 'overlay_baseline_archive_required')
        return {name: run(['git', '-C', str(repository), 'show', revision + ':agent_service/' + name])
                for name in names}
    raw = Path(archive_path).read_bytes()
    require(type(archive_sha256) is str and sha(raw) == archive_sha256,
            'overlay_baseline_archive_sha256_mismatch')
    result = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        require(archive.pax_headers.get('comment') == revision, 'overlay_baseline_archive_revision_mismatch')
        members = archive.getmembers()
        require(len(members) < 20000 and sum(item.size for item in members) < 256 * 1024 * 1024,
                'overlay_baseline_archive_too_large')
        seen_names = set()
        for item in members:
            path = PurePosixPath(item.name)
            require(not path.is_absolute() and '..' not in path.parts and (item.isfile() or item.isdir())
                    and item.name not in seen_names, 'overlay_baseline_archive_path_invalid')
            seen_names.add(item.name)
            relative = item.name.removeprefix('agent_service/')
            if item.name.startswith('agent_service/') and relative in names:
                require(item.isfile() and item.size <= 512 * 1024, 'overlay_baseline_source_invalid')
                result[relative] = archive.extractfile(item).read()
    require(set(result) == set(names), 'overlay_baseline_inventory_mismatch')
    return result


def image_tree_inventory(image, directory):
    """Hash an image-owned tree in a never-started extraction container."""
    require(directory in {'profiles', 'resources'}, 'overlay_inventory_directory_invalid')
    name = 'animation-review-inventory-' + uuid.uuid4().hex[:16]
    run(['docker', 'create', '--name', name, '--network', 'none', '--read-only',
         '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--entrypoint', '/bin/true', image])
    try:
        raw = run(['docker', 'cp', name + ':' + PACKAGE + directory, '-'])
        require(len(raw) <= 160 * 1024 * 1024, 'overlay_inventory_too_large')
        result, seen, total = {}, set(), 0
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            members = archive.getmembers()
            require(len(members) <= 4096, 'overlay_inventory_too_large')
            for item in members:
                path = PurePosixPath(item.name)
                require(bool(path.parts) and not path.is_absolute() and '..' not in path.parts and path.parts[0] == directory
                    and item.name not in seen and (item.isdir() or item.isfile())
                    and item.uid == 0 and not item.mode & 0o022, 'overlay_inventory_not_image_owned')
                seen.add(item.name)
                if item.isfile():
                    require(item.size <= 16 * 1024 * 1024, 'overlay_inventory_file_too_large')
                    data = archive.extractfile(item).read()
                    total += len(data)
                    require(total <= 128 * 1024 * 1024, 'overlay_inventory_too_large')
                    result[item.name] = dict(sha256=sha(data), bytes=len(data))
        require(bool(result), 'overlay_inventory_empty')
        return result
    finally:
        run(['docker', 'rm', name])


def _new_files(repository):
    files = {}
    for name in NEW_NAMES:
        path = repository / 'agent_service' / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 512 * 1024,
                'overlay_new_source_invalid')
        files[name] = path.read_bytes()
    for revision in ('animation-review-v1', 'animation-create-v2'):
        manifest = json.loads(files['resources/' + revision + '/manifest.json'])
        for row in manifest['local_files']:
            require(sha(files['resources/' + revision + '/' + row['path']]) == row['sha256'],
                    'overlay_new_bundle_content_mismatch')
    return files


def build(args):
    """Build an independent latest candidate; no route or deployment writes."""
    require(prior.IMAGE.fullmatch(args.base_image) and prior.TARGET.fullmatch(args.target_image)
        and REVISION.fullmatch(args.revision) and REVISION.fullmatch(args.baseline_revision),
        'overlay_immutable_donor_latest_target_and_revisions_required')
    builder_revision = getattr(args, 'builder_revision', None) or args.revision
    require(REVISION.fullmatch(builder_revision), 'overlay_builder_revision_required')
    require(not args.output.exists() and not args.output.is_symlink(), 'overlay_output_exists')
    repository = args.repository.resolve()
    role = getattr(args, 'role', 'control')
    require(role in {'control', 'worker'}, 'overlay_image_role_required')
    names = PATCH_NAMES if role == 'control' else WORKER_PATCH_NAMES
    if getattr(args, 'source_archive', None):
        provenance = prior.archive_source(repository, args.source_archive, args.archive_sha256 or '', args.revision)
    else:
        require(not getattr(args, 'archive_sha256', None), 'overlay_source_archive_required')
        require(run(['git', '-C', str(repository), 'rev-parse', 'HEAD']).decode().strip() == args.revision,
                'overlay_source_revision_mismatch')
        require(not run(['git', '-C', str(repository), 'status', '--porcelain', '--', 'agent_service']).strip(),
                'overlay_agent_source_not_clean')
        provenance = dict(source_git_revision=args.revision)
    baseline = baseline_sources(repository, args.baseline_revision,
        archive_path=getattr(args, 'baseline_archive', None),
        archive_sha256=getattr(args, 'baseline_archive_sha256', None), names=names)
    current = {name: (repository / 'agent_service' / name).read_bytes() for name in names}
    donor = inspect(args.base_image)
    original = donor_sources(args.base_image, names)
    files, patches = patched_sources(original, baseline, current, role=role)
    additions = _new_files(repository)
    files.update(additions)
    before = {tree: image_tree_inventory(args.base_image, tree) for tree in ('profiles', 'resources')}
    for name in additions:
        tree = name.split('/', 1)[0]
        require(tree not in before or name not in before[tree], 'overlay_new_file_already_in_donor:' + name)
    dockerfile = (f'FROM {args.base_image}\nCOPY overlay/ {PACKAGE}\n'
        f'LABEL org.opencontainers.image.revision="{args.revision}" '
        f'io.panghu.animation-review.builder-revision="{builder_revision}" '
        f'io.panghu.animation-review.builder-source-sha256="{sha(Path(__file__).read_bytes())}" '
        f'io.panghu.animation-review.baseline="{args.baseline_revision}" '
        f'io.panghu.animation-review.donor="{args.base_image}"\n').encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for name, raw in [('Dockerfile', dockerfile), *[('overlay/' + key, value) for key, value in sorted(files.items())]]:
            item = tarfile.TarInfo(name)
            item.uid = item.gid = 0
            item.mode, item.mtime, item.size = 0o644, 0, len(raw)
            archive.addfile(item, io.BytesIO(raw))
    run(['docker', 'build', '--network', 'none', '--tag', args.target_image, '-'], input=buffer.getvalue())
    built = inspect(args.target_image)
    require(all(donor['Config'].get(key) == built['Config'].get(key) for key in prior.RUNTIME_KEYS),
            'overlay_inherited_runtime_configuration_changed')
    after = {tree: image_tree_inventory(args.target_image, tree) for tree in ('profiles', 'resources')}
    for tree, inventory in before.items():
        for name, identity in inventory.items():
            expected = dict(sha256=sha(files[name]), bytes=len(files[name])) if name in files else identity
            require(after[tree].get(name) == expected, 'overlay_existing_file_changed:' + name)
        expected_names = set(inventory) | {name for name in additions if name.startswith(tree + '/')}
        require(set(after[tree]) == expected_names, 'overlay_built_inventory_mismatch:' + tree)
    measured = donor_sources(args.target_image, tuple(files))
    require(all(measured[name].encode() == raw for name, raw in files.items()),
            'overlay_built_source_bytes_mismatch')
    pushed_image = None
    if getattr(args, 'push', False):
        run(['docker', 'push', args.target_image])
        pushed = inspect(args.target_image)
        prefix = args.target_image.removesuffix(':latest') + '@sha256:'
        digests = [value for value in pushed.get('RepoDigests', []) if value.startswith(prefix)]
        require(len(digests) == 1, 'overlay_pushed_digest_unavailable')
        pushed_image = args.target_image + '@' + digests[0].split('@', 1)[1]
    receipt = dict(schema='animation-review-agent-overlay-build.v1', revision=args.revision,
        baseline_revision=args.baseline_revision, builder_revision=builder_revision,
        builder_source_sha256=sha(Path(__file__).read_bytes()), donor=args.base_image,
        role=role, target=args.target_image, image=pushed_image, pushed=bool(pushed_image),
        donor_image_id=donor['Id'], built_image_id=built['Id'], profiles=list(PROFILES),
        source_patches=patches, new_files={name: sha(raw) for name, raw in additions.items()},
        preserved_donor_trees={tree: dict(original_file_count=len(inventory),
            inventory_sha256=sha(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()),
            original_resources_unchanged=tree == 'resources',
            original_profile_modules_unchanged_except_dispatch=tree == 'profiles',
            exception_files=[name for name in files if name in inventory]) for tree, inventory in before.items()},
        **provenance, model_tasks=0, model_requests=0, deployment_changed=False,
        production_routes_changed=False, acceptance_created=False, credentials_opened=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True, type=Path)
    parser.add_argument('--role', required=True, choices=('control', 'worker'))
    parser.add_argument('--revision', required=True)
    parser.add_argument('--baseline-revision', required=True)
    parser.add_argument('--builder-revision')
    parser.add_argument('--base-image', required=True)
    parser.add_argument('--target-image', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source-archive', type=Path)
    parser.add_argument('--archive-sha256')
    parser.add_argument('--baseline-archive', type=Path)
    parser.add_argument('--baseline-archive-sha256')
    parser.add_argument('--push', action='store_true', help='Explicitly push the measured candidate latest image')
    args = parser.parse_args(argv)
    print(json.dumps(build(args), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
