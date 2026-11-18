"""The author preflight agrees with the unchanged Python release authority.

These are deterministic output-shape fixtures, not media, learned knowledge or
published releases. The test deliberately calls the real Python collector
functions instead of copying their validation into a second Python checker.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_service import animation_wikiskill_contract as C
from agent_service.animation_skills_contract import build_skill_package
from agent_service.animation_wikiskill_compact import COMPACT_RESULT_SCHEMA, expand_compact_result
from agent_service.contracts import ServiceError, digest


CHECKER = Path(__file__).resolve().parents[2] / 'agent_service' / 'compilation_output_preflight.mjs'


def context():
    snapshot = C.make_snapshot('offline-checker-parity', [dict(record_id=f'fact-{i}', kind='fixture',
        content=dict(observed_fact=f'Original bounded observation {i}', motion_verified=False))
        for i in range(4)], captured_at=1000., snapshot_id='3b2c2a7a-e266-4a47-b79a-157a43965c95')
    return C.build_input(snapshot)


def package(identity='causal-guide', *, kind='foundation'):
    return dict(skill_id=identity, version=1, kind=kind,
        files={'SKILL.md': f'---\nname: {identity}\ndescription: Bounded causal drawing evidence.\n---\n'
            'Preserve object identity. Sampled images do not verify continuous motion.'},
        runtime_requirements={'engines': ['canvas']})


def compact(document):
    return dict(schema=COMPACT_RESULT_SCHEMA, input_sha256=digest(document),
        snapshot_sha256=document['snapshot']['sha256'], evidence_scope=C.EVIDENCE_SCOPE,
        summary='Synthetic preflight parity fixture, not newly learned or published knowledge.',
        packages=[package()],
        index=[dict(skill_id='causal-guide', parent_skill_id=None, source_record_ranges=[[0, 1]])],
        exclusions=[dict(record_ranges=[[1, 4]], reason='Unneeded bounded structural history.')],
        limitations=[C.COMPILATION_LIMITATION])


def authoritative_verdict(value, document, input_sha256=None):
    try:
        C.validate_result(C.normalize_agent_result(expand_compact_result(value, document)),
                          input_sha256 or digest(document), document)
        return True, None
    except (ServiceError, TypeError, ValueError, KeyError, OverflowError, RecursionError) as error:
        return False, str(error)


def node_verdict(value, document, input_sha256=None):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node unavailable')
    assert CHECKER.is_file(), 'The v4 output preflight must be part of the source build.'
    source = '''
import fs from 'node:fs';
const {inspectCompilationOutput}=await import(process.argv[1]);
const p=JSON.parse(fs.readFileSync(0,'utf8'));
const before=JSON.stringify(p);
const result=inspectCompilationOutput(p.value,p.document,p.inputSha256);
process.stdout.write(JSON.stringify({result,mutated:JSON.stringify(p)!==before}));
'''
    run = subprocess.run([node, '--input-type=module', '-e', source, CHECKER.as_uri()],
        input=json.dumps(dict(value=value, document=document, inputSha256=input_sha256 or digest(document)),
                         ensure_ascii=True, allow_nan=False),
        capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert run.returncode == 0, run.stderr
    reply = json.loads(run.stdout)
    assert reply['mutated'] is False, 'Preflight must inspect, not repair authored outputs.'
    result = reply['result']
    assert type(result.get('valid')) is bool
    assert type(result.get('errors')) is list
    assert type(result.get('errors_truncated')) is bool
    assert type(result.get('counts')) is dict
    if result['valid']:
        assert result['errors'] == []
    else:
        assert result['errors'] and all(type(row) is dict and {'path', 'code'} <= set(row)
                                       for row in result['errors'])
    return result


def case(name, mutate, expected):
    return pytest.param(mutate, expected, id=name)


def add_child(value, identity='response-guide', *, parent='causal-guide', index=1):
    value['packages'].append(package(identity, kind='expression'))
    value['index'].append(dict(skill_id=identity, parent_skill_id=parent, source_record_ranges=[[index, index + 1]]))
    value['exclusions'][0]['record_ranges'] = [[index + 1, 4]]


def many_packages(value, count):
    value['packages'] = [package('fixture-' + str(i), kind='foundation' if i == 0 else 'expression')
                         for i in range(count)]
    value['index'] = [dict(skill_id=row['skill_id'], parent_skill_id=None if i == 0 else 'fixture-0',
                         source_record_ranges=[[0, 1]]) for i, row in enumerate(value['packages'])]


def normalize(value):
    value['packages'] = [build_skill_package(**row) for row in value['packages']]


VALID = [
    case('draft-package', lambda v: None, True),
    case('normalized-package', normalize, True),
    case('all-records-used-no-exclusions', lambda v: (v['index'][0].update(source_record_ranges=[[0, 4]]), v.update(exclusions=[])), True),
    case('adjacent-ranges-still-need-disjoint-exclusions', lambda v: v['index'][0].update(source_record_ranges=[[0, 1], [1, 2]]), False),
    case('disjoint-ranges-full-exact-coverage', lambda v: (v['index'][0].update(source_record_ranges=[[0, 1], [2, 3]]),
        v['exclusions'][0].update(record_ranges=[[1, 2], [3, 4]])), True),
    case('two-level-hierarchy', add_child, True),
    case('three-level-hierarchy', lambda v: (add_child(v), add_child(v, 'settling-guide', parent='response-guide', index=2)), True),
    case('same-source-can-support-two-skills', lambda v: (add_child(v),
        v['index'][1].update(source_record_ranges=[[0, 1], [1, 2]])), True),
    case('crlf-frontmatter', lambda v: v['packages'][0]['files'].update({'SKILL.md': v['packages'][0]['files']['SKILL.md'].replace('\n', '\r\n')}), True),
    case('json-quoted-metadata', lambda v: v['packages'][0]['files'].update({'SKILL.md':
        '---\nname: "causal-guide"\ndescription: "Keep \\"bounded\\" evidence."\n---\nActual bounded guidance.'}), True),
    case('yaml-single-quoted-description', lambda v: v['packages'][0]['files'].update({'SKILL.md':
        "---\nname: causal-guide\ndescription: 'Keep the author''s bounded observations.'\n---\nActual guidance."}), True),
    case('self-contained-local-links', lambda v: v['packages'][0]['files'].update({
        'SKILL.md': v['packages'][0]['files']['SKILL.md'] + '\n[Details](references/info.md#bound) [Self](#bound)',
        'references/info.md': 'Actual self-contained detail. [Nested](nested/more.md)',
        'references/nested/more.md': '[Parent](../info.md)'}), True),
    case('svg-namespace-is-inert', lambda v: v['packages'][0]['files'].update({'assets/shape.svg':
        '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0L1 1"/></svg>'}), True),
    case('portable-runtime-extras', lambda v: v['packages'][0]['runtime_requirements'].update({
        'safe': {'maximum': 9007199254740991, 'enabled': True, 'optional': None, 'modes': ['still', 'clip']}}), True),
    case('multiple-admitted-drawing-engines', lambda v: v['packages'][0]['runtime_requirements'].update(engines=['html-svg', 'canvas', 'three']), True),
    case('thirty-two-package-files-boundary', lambda v: v['packages'][0]['files'].update({f'references/f{i}.md': 'Bounded detail.' for i in range(31)}), True),
]


INVALID = [
    case('wrong-compact-schema', lambda v: v.update(schema=C.RESULT_SCHEMA), False),
    case('missing-summary', lambda v: v.pop('summary'), False),
    case('extra-top-level-key', lambda v: v.update(quality='unverified'), False),
    case('wrong-input-sha', lambda v: v.update(input_sha256='0' * 64), False),
    case('wrong-snapshot-sha', lambda v: v.update(snapshot_sha256='0' * 64), False),
    case('original-failure-scope-field', lambda v: (v.pop('evidence_scope'), v.update(scope=C.EVIDENCE_SCOPE)), False),
    case('original-failure-invented-library-metadata', lambda v: (v.pop('summary'),
        v.update(record_count=4, root_skill_id='causal-guide', feedback_lifecycle={}, coverage={}, validation={})), False),
    case('wrong-evidence-scope', lambda v: v.update(evidence_scope='frozen_records_only'), False),
    case('summary-empty', lambda v: v.update(summary='  '), False),
    case('summary-over-limit', lambda v: v.update(summary='s' * 16001), False),
    case('index-source-ids-not-ranges', lambda v: (v['index'][0].pop('source_record_ranges'), v['index'][0].update(source_record_ids=['fact-0'])), False),
    case('index-source-range-singular-key', lambda v: (v['index'][0].pop('source_record_ranges'), v['index'][0].update(source_record_range=[0, 1])), False),
    case('original-failure-index-is-display-navigation-not-evidence-map', lambda v: v.update(index=[
        dict(skill_id='causal-guide', kind='foundation', parent_skill_id=None,
             path='causal-guide/SKILL.md', use_when='Use for bounded causal drawing.')]), False),
    case('index-extra-key', lambda v: v['index'][0].update(observation='actual'), False),
    case('index-missing-parent-key', lambda v: v['index'][0].pop('parent_skill_id'), False),
    case('index-empty-ranges', lambda v: v['index'][0].update(source_record_ranges=[]), False),
    case('index-empty-half-open-range', lambda v: v['index'][0].update(source_record_ranges=[[0, 0]]), False),
    case('index-out-of-bounds', lambda v: v['index'][0].update(source_record_ranges=[[0, 5]]), False),
    case('range-bool-is-not-integer', lambda v: v['index'][0].update(source_record_ranges=[[False, 1]]), False),
    case('range-fraction-not-integer', lambda v: v['index'][0].update(source_record_ranges=[[0, 1.5]]), False),
    case('range-unsorted', lambda v: v['index'][0].update(source_record_ranges=[[2, 3], [0, 1]]), False),
    case('range-overlap', lambda v: v['index'][0].update(source_record_ranges=[[0, 2], [1, 3]]), False),
    case('excluded-source-ranges-wrong-key', lambda v: (v['exclusions'][0].pop('record_ranges'), v['exclusions'][0].update(source_record_ranges=[[1, 4]])), False),
    case('original-failure-exclusion-singular-range', lambda v: (v['exclusions'][0].pop('record_ranges'),
        v['exclusions'][0].update(record_range=[1, 4])), False),
    case('exclusion-extra-key', lambda v: v['exclusions'][0].update(scope='history'), False),
    case('exclusion-no-reason', lambda v: v['exclusions'][0].pop('reason'), False),
    case('exclusion-empty-reason', lambda v: v['exclusions'][0].update(reason=' '), False),
    case('exclusion-reason-over-limit', lambda v: v['exclusions'][0].update(reason='r' * 4001), False),
    case('duplicate-exclusion-record', lambda v: v['exclusions'].append(deepcopy(v['exclusions'][0])), False),
    case('used-record-is-also-excluded', lambda v: v['exclusions'][0].update(record_ranges=[[0, 4]]), False),
    case('uncovered-record', lambda v: v['exclusions'][0].update(record_ranges=[[2, 4]]), False),
    case('duplicate-skill-id', lambda v: (add_child(v), v['packages'][1].update(skill_id='causal-guide')), False),
    case('duplicate-frontmatter-name', lambda v: (add_child(v), v['packages'][1]['files'].update({'SKILL.md':v['packages'][0]['files']['SKILL.md']})), False),
    case('index-unknown-skill', lambda v: v['index'][0].update(skill_id='not-authored'), False),
    case('unknown-parent', lambda v: v['index'][0].update(parent_skill_id='missing'), False),
    case('self-parent', lambda v: v['index'][0].update(parent_skill_id='causal-guide'), False),
    case('two-roots', lambda v: (add_child(v), v['index'][1].update(parent_skill_id=None)), False),
    case('parent-cycle-no-root', lambda v: (add_child(v), v['index'][0].update(parent_skill_id='response-guide')), False),
    case('parent-cycle-with-separate-foundation-root', lambda v: (add_child(v),
        add_child(v, 'settling-guide', parent='response-guide', index=2),
        v['index'][1].update(parent_skill_id='settling-guide')), False),
    case('root-must-be-foundation', lambda v: v['packages'][0].update(kind='expression'), False),
    case('missing-index-entry', lambda v: v.update(index=[]), False),
    case('no-packages', lambda v: v.update(packages=[]), False),
    case('package-extra-field', lambda v: v['packages'][0].update(accepted=True), False),
    case('original-failure-package-metadata-without-version', lambda v: (v['packages'][0].pop('version'),
        v['packages'][0].update(name='causal-guide', parent_skill_id=None, description='Bounded causal drawing evidence.',
            evidence_record_ranges=[[0, 1]], evidence_notes='Actual bounded record.')), False),
    case('package-kind-unsupported', lambda v: v['packages'][0].update(kind='template'), False),
    case('package-version-not-first', lambda v: v['packages'][0].update(version=2), False),
    case('package-version-bool', lambda v: v['packages'][0].update(version=True), False),
    case('skill-id-empty', lambda v: v['packages'][0].update(skill_id=''), False),
    case('frontmatter-missing', lambda v: v['packages'][0]['files'].update({'SKILL.md':'Actual guide without metadata.'}), False),
    case('frontmatter-duplicate-name', lambda v: v['packages'][0]['files'].update({'SKILL.md':
        '---\nname: causal-guide\nname: duplicate\ndescription: Bounded detail.\n---\nActual guide.'}), False),
    case('frontmatter-unknown-key', lambda v: v['packages'][0]['files'].update({'SKILL.md':
        '---\nname: causal-guide\ndescription: Bounded detail.\nlicense: MIT\n---\nActual guide.'}), False),
    case('frontmatter-missing-body', lambda v: v['packages'][0]['files'].update({'SKILL.md':
        '---\nname: causal-guide\ndescription: Bounded detail.\n---\n  '}), False),
    case('frontmatter-name-not-kebab', lambda v: v['packages'][0]['files'].update({'SKILL.md':
        '---\nname: Bad_Name\ndescription: Bounded detail.\n---\nActual guide.'}), False),
    case('frontmatter-description-too-long', lambda v: v['packages'][0]['files'].update({'SKILL.md':
        '---\nname: causal-guide\ndescription: ' + 'x' * 1201 + '\n---\nActual guide.'}), False),
    case('missing-skill-file', lambda v: v['packages'][0].update(files={'references/a.md':'Actual guide.'}), False),
    case('file-outside-admitted-directories', lambda v: v['packages'][0]['files'].update({'secret.md':'No.'}), False),
    case('path-traversal', lambda v: v['packages'][0]['files'].update({'references/../other.md':'No.'}), False),
    case('windows-device-path', lambda v: v['packages'][0]['files'].update({'references/CON.md':'No.'}), False),
    case('case-colliding-file', lambda v: v['packages'][0]['files'].update({'references/A.md':'A', 'references/a.md':'B'}), False),
    case('file-shadows-directory', lambda v: v['packages'][0]['files'].update({'references/a':'A', 'references/a/b.md':'B'}), False),
    case('missing-local-link-target', lambda v: v['packages'][0]['files'].update({'references/more.md':'[Missing](absent.md)'}), False),
    case('local-link-escapes-package', lambda v: v['packages'][0]['files'].update({'references/more.md':'[Outside](../../SKILL.md)'}), False),
    case('original-failure-cross-package-navigation-link', lambda v: v['packages'][0]['files'].update({
        'SKILL.md':v['packages'][0]['files']['SKILL.md'] + '\n[Related](../causal-comparisons/SKILL.md)'}), False),
    case('absolute-local-link', lambda v: v['packages'][0]['files'].update({'references/more.md':'[Outside](/SKILL.md)'}), False),
    case('query-local-link', lambda v: v['packages'][0]['files'].update({'references/more.md':'[Query](../SKILL.md?x=1)'}), False),
    case('external-url-in-prose', lambda v: v['packages'][0]['files'].update({'references/more.md':'Learn at https://example.org/style'}), False),
    case('namespace-prefix-is-not-a-whitelist', lambda v: v['packages'][0]['files'].update({'references/more.md':'http://www.w3.org/2000/svg/remote.png'}), False),
    case('unsupported-runtime-engine', lambda v: v['packages'][0]['runtime_requirements'].update(engines=['manim']), False),
    case('runtime-engine-duplicates', lambda v: v['packages'][0]['runtime_requirements'].update(engines=['canvas', 'canvas']), False),
    case('runtime-fraction-is-not-portable', lambda v: v['packages'][0]['runtime_requirements'].update(fraction=0.5), False),
    case('runtime-int-over-safe-range', lambda v: v['packages'][0]['runtime_requirements'].update(maximum=9007199254740992), False),
    case('runtime-external-dependency', lambda v: v['packages'][0]['runtime_requirements'].update(asset='https://example.org/asset'), False),
    case('runtime-oversize', lambda v: v['packages'][0]['runtime_requirements'].update(note='x' * 8192), False),
    case('file-utf8-byte-limit', lambda v: v['packages'][0]['files'].update({'references/unicode.md':'墨' * 65536}), False),
    case('file-ascii-limit', lambda v: v['packages'][0]['files'].update({'references/large.md':'x' * (128 * 1024 + 1)}), False),
    case('package-file-count-limit', lambda v: v['packages'][0]['files'].update({f'references/f{i}.md':'x' for i in range(32)}), False),
    case('package-byte-limit', lambda v: v['packages'][0]['files'].update({f'references/f{i}.md':'x' * (128 * 1024) for i in range(8)}), False),
    case('missing-compilation-limitation', lambda v: v.update(limitations=['Unverified.']), False),
    case('limitation-count-limit', lambda v: v.update(limitations=[C.COMPILATION_LIMITATION] + ['Bounded.'] * 128), False),
    case('catalog-package-count-limit', lambda v: many_packages(v, 257), False),
    case('bundle-file-count-limit', lambda v: (many_packages(v, 33),
        [row['files'].update({f'references/f{i}.md':'Bounded detail.' for i in range(30)}) for row in v['packages']]), False),
    case('normalized-file-hash-tampering', lambda v: (normalize(v), v['packages'][0]['files_sha256'].update({'SKILL.md':'0' * 64})), False),
    case('normalized-package-hash-tampering', lambda v: (normalize(v), v['packages'][0].update(sha256='0' * 64)), False),
    case('normalized-metadata-mismatch', lambda v: (normalize(v), v['packages'][0].update(description='Altered metadata.')), False),
]


@pytest.mark.parametrize('mutate,expected', VALID + INVALID)
def test_javascript_preflight_matches_existing_python_release_authority(mutate, expected):
    document = context()
    value = compact(document)
    mutate(value)
    authoritative, error = authoritative_verdict(value, document)
    assert authoritative is expected, f'Fixture expectation differs from real authority: {error}'
    before = deepcopy(value)
    report = node_verdict(value, document)
    assert value == before
    assert report['valid'] is authoritative, dict(python=error, javascript=report)


def test_valid_adjacent_ranges_do_not_imply_an_overlap():
    document = context()
    value = compact(document)
    value['index'][0]['source_record_ranges'] = [[0, 1], [1, 2]]
    value['exclusions'][0]['record_ranges'] = [[2, 4]]
    assert authoritative_verdict(value, document)[0] is True
    assert node_verdict(value, document)['valid'] is True


def test_checker_refuses_wrong_caller_input_binding_even_with_self_consistent_output():
    document = context()
    value = compact(document)
    assert authoritative_verdict(value, document, '0' * 64)[0] is False
    assert node_verdict(value, document, '0' * 64)['valid'] is False


def test_preflight_diagnostics_are_bounded_and_do_not_echo_authored_filenames_or_prose():
    document = context()
    value = compact(document)
    marker = 'AUTHOR-CONTENT-DO-NOT-ECHO'
    value['packages'][0]['files'].update({
        f'references/{marker}-{i}?.md': f'[Unsupported](/outside-{marker}-{i}.md)' for i in range(50)})
    assert authoritative_verdict(value, document)[0] is False
    report = node_verdict(value, document)
    assert report['valid'] is False and report['errors_truncated'] is True
    assert 1 <= len(report['errors']) <= 64
    assert marker not in json.dumps(report)
