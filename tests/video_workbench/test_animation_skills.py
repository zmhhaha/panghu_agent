"""Immutable knowledge, frozen discovery and real local Pi-tool byte delivery.

Publications below are explicit fixtures, never real developer approval.
Node tests execute the actual read/search extension without a model or network.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.animation_skills import (
    SkillCatalogTools, build_skill_package, validate_skill_package,
    freeze_skill_catalog, validate_skill_catalog, bundle_files,
)


ROOT = Path(__file__).resolve().parents[2]
EXTENSION = ROOT / 'agent_service' / 'skill_tools_extension.mjs'
RUNTIME = dict(runtime='hyperframes-core', version='0.8.149',
               project_schema='video.hyperframes-project.v1', engines=['html-svg', 'canvas', 'three'])


def package(index=1, *, version=1, kind='expression', **changes):
    body = dict(skill_id='skill-' + str(index), version=version, kind=kind,
        files={'SKILL.md': '---\nname: keep-object-identity\n'
            'description: Keep object identity legible during grouping, filtering and state changes.\n'
            '---\n\nKeep objects recognizable while their relations change.\n'
            'Read [comparison checks](references/checks.md) when comparing candidates.\n',
            'references/checks.md': 'Compare the transition itself, not only its endpoints.\n'},
        runtime_requirements=deepcopy(RUNTIME))
    body.update(changes)
    return build_skill_package(**body)


def publication(value, *, status='published'):
    return dict(schema='video.animation-method.v2', id='method-' + value['sha256'][:20],
        content_sha256='c' * 64, status=status, skill_package=deepcopy(value),
        publication=dict(schema='video.animation-method-publication.v2',
            method_version_id='method-' + value['sha256'][:20], method_content_sha256='c' * 64,
            skill_package_sha256=value['sha256'], reference_validation_ids=['fixture-reference'],
            transfer_validation_ids=['fixture-structural-transfer']))


def freeze(packages, *, owner='video-user', consumer='fixture-production-1', task='a' * 64):
    return freeze_skill_catalog([publication(p) for p in packages], recipient_owner=owner,
        consumer_id=consumer, task_sha256=task, allowed_engines=['html-svg', 'canvas', 'three'])


def stage(workspace, snapshot, packages):
    for file in bundle_files(snapshot, packages):
        path = workspace / 'inputs' / file.name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(file.content())


def node(workspace, requests):
    binary = shutil.which('node')
    if not binary:
        pytest.skip('Node is required to exercise the real Pi extension')
    script = r"""
      import fs from 'node:fs';
      const mod = await import(process.argv[1]);
      const registered = new Map();
      mod.default({ registerTool: t => registered.set(t.name, t) });
      const requests = JSON.parse(fs.readFileSync(0, 'utf8'));
      const output = [];
      for (const row of requests) {
        try {
          if (row.tool === 'registered') {
            output.push({ ok: true, result: [...registered.keys()] });
          } else {
            const controller = new AbortController();
            if (row.cancel) controller.abort();
            const tool = registered.get(row.tool);
            const result = tool ? await tool.execute('fixture-native-call', row.params, controller.signal)
              : await mod[row.tool](row.params, controller.signal);
            output.push({ ok: true, result });
          }
        } catch (error) { output.push({ ok: false, error: error.message }); }
      }
      process.stdout.write(JSON.stringify(output));
    """
    completed = subprocess.run([binary, '--input-type=module', '-e', script, EXTENSION.as_uri()],
        cwd=workspace, input=json.dumps(requests, ensure_ascii=False), text=True, encoding='utf-8',
        capture_output=True, timeout=20, check=True)
    return json.loads(completed.stdout)


def test_package_binds_original_utf8_bytes_every_attachment_and_version():
    original = package()
    assert validate_skill_package(original) == original
    assert original['files_sha256']['SKILL.md'] == hashlib.sha256(original['files']['SKILL.md'].encode()).hexdigest()
    changed = deepcopy(original)
    changed['files']['references/checks.md'] += 'Another rule.'
    with pytest.raises(ServiceError, match='animation_skill_package_changed'):
        validate_skill_package(changed)
    revised = package(version=2)
    assert original['sha256'] != revised['sha256']
    original['files']['SKILL.md'] = 'changed by caller'
    assert 'Keep objects' in revised['files']['SKILL.md']


def test_local_svg_and_xml_namespace_identifiers_are_not_remote_dependencies():
    original = package()
    files = dict(original['files'], **{'assets/diagram.svg':
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink"><path d="M0 0L1 1"/></svg>'})
    value = package(files=files)
    assert value['files']['assets/diagram.svg'] == files['assets/diagram.svg']


@pytest.mark.parametrize('remote', [
    'http://www.w3.org/2000/svg/remote.js',
    'http://www.w3.org/2000/svg?fetch=asset',
    'https://www.w3.org/2000/svg',
    'http://www.w3.org/1999/xlink#remote',
    'http://www.w3.org/2000/svg.evil.test',
])
def test_namespace_exceptions_do_not_allow_url_prefix_or_protocol_variants(remote):
    original = package()
    files = dict(original['files'], **{'assets/diagram.svg': '<svg xmlns="' + remote + '"/>'})
    with pytest.raises(ServiceError, match='animation_skill_external_dependency'):
        package(files=files)


@pytest.mark.parametrize('change,code', [
    ({'files': {'SKILL.md': 'no frontmatter'}}, 'frontmatter_invalid'),
    ({'files': {'SKILL.md': '---\nname: x\nname: y\ndescription: z\n---\nbody'}}, 'frontmatter_invalid'),
    ({'files': {'SKILL.md': '---\nname: x\ndescription: z\n---\n[missing](references/no.md)'}}, 'reference_missing'),
    ({'files': {'SKILL.md': '---\nname: x\ndescription: z\n---\nFollow https://www.bilibili.com/video/example'}}, 'external_dependency'),
    ({'files': {'SKILL.md': '---\nname: x\ndescription: z\n---\nbody', '../escape.md': 'x'}}, 'path_invalid'),
    ({'files': {'SKILL.md': '---\nname: x\ndescription: z\n---\nbody', 'assets/a': 'a', 'assets/A': 'b'}}, 'path_collision'),
    ({'runtime_requirements': {'engines': ['html-svg'], 'remote': 'https://example.org/asset.js'}}, 'external_dependency'),
    ({'runtime_requirements': {'engines': ['html-svg'], 'float': 1.0}}, 'runtime_invalid'),
])
def test_packages_reject_ambiguous_metadata_escape_links_and_online_dependencies(change, code):
    with pytest.raises(ServiceError, match='animation_skill_' + code):
        package(**change)


def test_snapshot_only_discovers_published_compatible_packages_without_host_shortlist():
    values = [package(i) for i in range(7)]
    rows = [publication(p) for p in values]
    rows.append(publication(package(99), status='candidate'))
    rows.append(dict(schema='video.animation-method.v1', status='published', id='legacy'))
    snapshot = freeze_skill_catalog(rows, recipient_owner='user', consumer_id='production',
        task_sha256='a' * 64, allowed_engines=['canvas'])
    assert len(snapshot['skills']) == 7
    assert all('files' not in row and 'files_sha256' not in row for row in snapshot['skills'])
    assert 'Keep objects recognizable' not in canonical_bytes(snapshot).decode()
    tools = SkillCatalogTools(snapshot, values)
    first = tools.search('object identity', limit=4)
    assert len(first['skills']) == 4 and first['next_offset'] == 4 and first['total'] == 7
    assert len(tools.search('object identity', offset=4)['skills']) == 3
    assert tools.search('not-present')['skills'] == []


def test_invalid_publication_is_not_silently_treated_as_no_match():
    row = publication(package())
    row['publication']['skill_package_sha256'] = '0' * 64
    with pytest.raises(ServiceError, match='animation_skill_not_published'):
        freeze_skill_catalog([row], recipient_owner='user', consumer_id='production',
            task_sha256='a' * 64, allowed_engines=['html-svg'])


def test_new_publications_do_not_change_old_snapshot_or_allow_off_snapshot_reads():
    first, newer = package(), package(version=2)
    snapshot = freeze([first])
    saved = canonical_bytes(snapshot)
    latest = freeze([first, newer], consumer='next-production')
    assert len(latest['skills']) == 2 and canonical_bytes(snapshot) == saved
    tools = SkillCatalogTools(snapshot, [first])
    with pytest.raises(ServiceError, match='animation_skill_not_in_snapshot'):
        tools.read(newer['sha256'])
    changed = deepcopy(snapshot)
    changed['consumer_id'] = 'different-consumer'
    with pytest.raises(ServiceError, match='animation_skill_catalog_changed'):
        validate_skill_catalog(changed)


def test_receipt_binds_exact_file_task_snapshot_owner_and_does_not_claim_application():
    value = package()
    tools = SkillCatalogTools(freeze([value]), [value])
    returned = tools.read(value['sha256'], 'references/checks.md')
    assert returned['text'] == value['files']['references/checks.md']
    assert tools.validate_read_receipt(returned['receipt']) == returned['receipt']
    assert returned['receipt']['task_sha256'] == 'a' * 64
    assert returned['receipt']['evidence_scope'] == 'returned_exact_bytes_not_visual_application_or_quality'
    altered = deepcopy(returned['receipt'])
    altered['file_sha256'] = '0' * 64
    with pytest.raises(ServiceError, match='animation_skill_read_receipt_changed'):
        tools.validate_read_receipt(altered)
    other = SkillCatalogTools(freeze([value], owner='another-user'), [value])
    with pytest.raises(ServiceError, match='animation_skill_read_receipt_changed'):
        other.validate_read_receipt(returned['receipt'])


def test_empty_catalog_supports_free_creation_without_inventing_a_read(tmp_path):
    snapshot = freeze([])
    assert SkillCatalogTools(snapshot, []).search()['skills'] == []
    stage(tmp_path, snapshot, [])
    result = node(tmp_path, [dict(tool='searchSkills', params={})])[0]
    assert result['ok'] and result['result']['details']['receipt']['total'] == 0


def test_actual_pi_tools_match_python_search_and_precise_lazy_reads(tmp_path):
    values = [package(i) for i in range(6)] + [package(8, kind='foundation')]
    snapshot = freeze(values)
    stage(tmp_path, snapshot, values)
    tools = SkillCatalogTools(snapshot, values)
    result = node(tmp_path, [dict(tool='registered'),
        dict(tool='skill_search', params=dict(query='object identity', kind='expression', limit=5)),
        dict(tool='skill_search', params=dict(query='object identity', offset=5, limit=5)),
        dict(tool='skill_read', params=dict(package_sha256=values[5]['sha256'])),
        dict(tool='skill_read', params=dict(package_sha256=values[5]['sha256'], path='references/checks.md'))])
    assert all(row['ok'] for row in result), result
    assert result[0]['result'] == ['skill_search', 'skill_read']
    assert result[1]['result']['details']['receipt'] == tools.search('object identity', kind='expression', limit=5)
    assert result[2]['result']['details']['receipt'] == tools.search('object identity', offset=5, limit=5)
    assert tools.validate_tool_result('skill_search',
        dict(query='object identity', kind='expression', limit=5), result[1]['result']) == result[1]['result']
    for index, filename in ((3, 'SKILL.md'), (4, 'references/checks.md')):
        expected = tools.read(values[5]['sha256'], filename)
        assert result[index]['result']['content'] == [dict(type='text', text=expected['text'])]
        assert result[index]['result']['details']['receipt'] == expected['receipt']
        assert tools.validate_tool_result('skill_read',
            dict(package_sha256=values[5]['sha256'], path=filename), result[index]['result']) == result[index]['result']


def test_native_result_reconstruction_rejects_receipt_or_returned_text_substitution():
    value = package()
    tools = SkillCatalogTools(freeze([value]), [value])
    params = dict(package_sha256=value['sha256'])
    result = tools.tool_result('skill_read', params)
    result['content'][0]['text'] += '\nInjected text is not the frozen skill.'
    with pytest.raises(ServiceError, match='animation_skill_tool_result_changed'):
        tools.validate_tool_result('skill_read', params, result)
    result = tools.tool_result('skill_read', params)
    result['details']['receipt']['task_sha256'] = 'b' * 64
    with pytest.raises(ServiceError, match='animation_skill_tool_result_changed'):
        tools.validate_tool_result('skill_read', params, result)
    with pytest.raises(ServiceError, match='animation_skill_tool_invalid'):
        tools.tool_result('skill_read', dict(params, root='/arbitrary-root'))
    with pytest.raises(ServiceError, match='animation_skill_tool_invalid'):
        tools.tool_result('write', params)


def test_real_tool_and_host_match_unicode_and_empty_search_content(tmp_path):
    original = package()
    files = dict(original['files'])
    files['SKILL.md'] = files['SKILL.md'].replace(
        'description: Keep object identity legible during grouping, filtering and state changes.',
        'description: 群体筛选保持对象身份；Straße 与 STRASSE，İ 与 emoji 🧭。')
    value = package(files=files)
    snapshot = freeze([value])
    stage(tmp_path, snapshot, [value])
    tools = SkillCatalogTools(snapshot, [value])
    queries = ['群体筛选', 'STRASSE', 'Straße', 'İ', '']
    actual = node(tmp_path, [dict(tool='searchSkills', params=dict(query=q)) for q in queries])
    for query, result in zip(queries, actual):
        assert result['ok'], result
        assert result['result'] == tools.tool_result('skill_search', dict(query=query))


def test_real_tool_does_not_fall_back_to_historical_base_root(tmp_path):
    value = package()
    stage(tmp_path, freeze([value]), [value])
    (tmp_path / 'inputs').rename(tmp_path / 'base')
    assert not node(tmp_path, [dict(tool='searchSkills', params={})])[0]['ok']


@pytest.mark.parametrize('params', [
    {'package_sha256': '0' * 64},
    {'package_sha256': 'select', 'path': 'SKILL.md'},
    {'package_sha256': 'known', 'path': '../catalog.json'},
    {'package_sha256': 'known', 'path': '/etc/passwd'},
    {'package_sha256': 'known', 'path': 'https://example.org'},
    {'package_sha256': 'known', 'root': '/elsewhere'},
])
def test_real_tool_rejects_undeclared_packages_paths_or_tool_authority(tmp_path, params):
    value = package()
    stage(tmp_path, freeze([value]), [value])
    if params.get('package_sha256') == 'known':
        params = dict(params, package_sha256=value['sha256'])
    assert not node(tmp_path, [dict(tool='readSkill', params=params)])[0]['ok']


def test_actual_tool_rejects_tampered_attachment_even_when_reading_entrypoint(tmp_path):
    value = package()
    stage(tmp_path, freeze([value]), [value])
    file = tmp_path / 'inputs' / 'animation-skills' / 'packages' / value['sha256'] / 'references' / 'checks.md'
    file.write_text('different attachment', encoding='utf-8')
    result = node(tmp_path, [dict(tool='readSkill', params=dict(package_sha256=value['sha256']))])[0]
    assert not result['ok']


def test_real_tool_stops_before_read_when_cancelled(tmp_path):
    result = node(tmp_path, [dict(tool='searchSkills', params={}, cancel=True)])[0]
    assert not result['ok'] and result['error'] == 'animation_skill_tool_cancelled'


def test_actual_tool_rejects_hardlinked_staged_files(tmp_path):
    value = package()
    stage(tmp_path, freeze([value]), [value])
    original = tmp_path / 'inputs' / 'animation-skills' / 'catalog.json'
    try:
        (tmp_path / 'hardlink.json').hardlink_to(original)
    except OSError:
        pytest.skip('Filesystem does not support hardlinks')
    assert not node(tmp_path, [dict(tool='searchSkills', params={})])[0]['ok']
