"""Trusted original-source chart authority; no model, network or Office tools."""
import copy
import hashlib
import os
from uuid import uuid4

import pytest

from agent_service.contracts import canonical_bytes, digest
from ppt_service.control.chart_oracle import task_chart_oracle
from ppt_service.control.execution import ControlError
from ppt_service.control.source_tables import (MAX_WIRE, bind_direction, bind_planning,
    discover_tables, number, validate_direction_binding)

TABLE = '| Year | Temperature (°C) | Measurement |\n| --- | ---: | --- |\n| 2019 | 0.98 | 1e-2 |\n| 2024 | 1.28 | -0.025 |\n'


def original(root, text=TABLE, *, media_type='text/markdown'):
    pid, aid = str(uuid4()), str(uuid4())
    raw = text.encode('utf-8')
    key = f'projects/{pid}/assets/{aid}/original'
    path = root / key
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    asset = {'id': aid, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
             'media_type': media_type, 'storage_key': key}
    body = {'project_id': pid, 'title': 'Public numeric source', 'settings': {'page_count': 3}, 'assets': [asset]}
    return dict(body, hash=digest(body))


def direction(table_id, **overrides):
    spec = dict(table_id=table_id, category_column=0, series_columns=[1, 2], slide=2, type='column')
    spec.update(overrides)
    return {'id': 'A', 'name': 'Source chart', 'page_plan': {'pages': [{}, {}, {}]}, 'native_chart_specs': [spec]}


def bound_case(root):
    snapshot = original(root)
    catalog = discover_tables(root, snapshot)
    selected = bind_direction(direction(catalog['tables'][0]['table_id']), snapshot, catalog)
    return snapshot, catalog, selected


def task(snapshot, selected):
    return {'input_snapshot_sha256': snapshot['hash'],
            'input_snapshot': {key: snapshot[key] for key in ('project_id', 'title', 'settings', 'assets')},
            'confirmations': {'stage2': {'channel': 'website_database', 'input_snapshot_sha256': snapshot['hash'],
                'selection': {'direction_id': 'A'}, 'selected_direction': selected}}}


def test_catalog_reads_all_original_cells_preserves_units_and_is_stable(tmp_path):
    snapshot = original(tmp_path, '# Source\n\n' + TABLE)
    catalog = discover_tables(tmp_path, snapshot)
    assert catalog == discover_tables(tmp_path, snapshot)
    assert len(canonical_bytes(catalog)) <= MAX_WIRE
    table = catalog['tables'][0]
    assert table['line_start'] == 3 and table['source_label'] == '资料表 1'
    assert table['headers'] == ['Year', 'Temperature (°C)', 'Measurement']
    assert table['rows'] == [['2019', '0.98', '1e-2'], ['2024', '1.28', '-0.025']]
    assert table['column_types'] == ['number', 'number', 'number']
    assert table['asset_sha256'] == snapshot['assets'][0]['sha256']
    assert 'storage_key' not in str(catalog) and 'original' not in str(catalog)


def test_same_table_at_different_source_position_has_distinct_identity(tmp_path):
    snapshot = original(tmp_path, TABLE + '\n' + TABLE)
    tables = discover_tables(tmp_path, snapshot)['tables']
    assert len(tables) == 2 and tables[0]['table_id'] != tables[1]['table_id']


@pytest.mark.parametrize('wrapper', ['```md\n{}\n```\n', '~~~~\n{}\n~~~~\n', '{}'])
def test_code_fences_and_indented_code_are_never_numeric_authority(tmp_path, wrapper):
    text = wrapper.format(TABLE) if wrapper != '{}' else ''.join('    ' + row for row in TABLE.splitlines(True))
    assert discover_tables(tmp_path, original(tmp_path, text))['tables'] == []


@pytest.mark.parametrize('text', [
    '| Name | Value |\n| --- | --- |\n| A | 1 | 2 |\n',
    '| Name | Name |\n| --- | --- |\n| A | 1 |\n',
    '| Name | Value |\n| --- | --- |\n| A | =1+1 |\n',
    '| Name | Value |\n| --- | --- |\n| A | 1,000 |\n',
    '| Name | Value |\n| --- | --- |\n| A | 15% |\n',
    '| Name | Value |\n| --- | --- |\n| A | NaN |\n',
    '| Name | Value |\n| --- | --- |\n| A | |\n',
    '| Name | Value |\n| --- | --- |\n' + ''.join(f'| A{n} | {n} |\n' for n in range(41)),
])
def test_unsupported_tables_are_summarized_without_partial_authority(tmp_path, text):
    catalog = discover_tables(tmp_path, original(tmp_path, text))
    assert catalog['tables'] == [] and len(catalog['unsupported']) == 1


@pytest.mark.parametrize('cell', ['=1+1', 'NaN', 'Infinity', '1,000', '3%', '', '1e999999',
    '1.234567890123456', '1e13', '1e-13', '1' * 65])
def test_numeric_limits_reject_ambiguity_formulas_and_unbounded_precision(cell):
    with pytest.raises(ValueError):
        number(cell)


def test_non_markdown_is_not_parsed_or_used_as_chart_authority(tmp_path):
    catalog = discover_tables(tmp_path, original(tmp_path, media_type='application/pdf'))
    assert catalog['tables'] == [] and catalog['unsupported'][0]['reason'] == 'markdown_tables_only'


@pytest.mark.parametrize('changed', ['hash', 'size', 'key', 'bytes'])
def test_exact_snapshot_and_original_identity_are_required(tmp_path, changed):
    snapshot = original(tmp_path)
    if changed == 'hash':
        snapshot['hash'] = 'a' * 64
    elif changed in ('size', 'key'):
        snapshot['assets'][0]['bytes' if changed == 'size' else 'storage_key'] = 7 if changed == 'size' else 'other/original'
        snapshot['hash'] = digest({key: snapshot[key] for key in ('project_id', 'title', 'settings', 'assets')})
    else:
        (tmp_path / snapshot['assets'][0]['storage_key']).write_bytes(b'changed')
    with pytest.raises(ControlError):
        discover_tables(tmp_path, snapshot)


@pytest.mark.skipif(os.name != 'posix', reason='actual no-follow POSIX reader')
@pytest.mark.parametrize('kind', ['symlink', 'parent_symlink', 'hardlink'])
def test_linked_originals_fail_closed(tmp_path, kind):
    snapshot = original(tmp_path)
    path = tmp_path / snapshot['assets'][0]['storage_key']
    if kind == 'parent_symlink':
        parent = path.parent
        replacement = parent.with_name('replacement')
        parent.rename(replacement)
        parent.symlink_to(replacement, target_is_directory=True)
    else:
        other = path.with_name('copy')
        path.rename(other)
        if kind == 'symlink':
            path.symlink_to(other)
        else:
            os.link(other, path)
    with pytest.raises(ControlError):
        discover_tables(tmp_path, snapshot)


def test_expected_charts_are_derived_and_all_actor_authority_is_overwritten(tmp_path):
    snapshot, catalog, selected = bound_case(tmp_path)
    forged = direction(catalog['tables'][0]['table_id'])
    forged['source_chart_binding'] = {'expected_charts': [{'invented': True}]}
    design = {'directions': [forged], 'source_plan': {'source_chart_binding': {'invented': True}},
              'source_table_catalog': {'invented': True}}
    actual = bind_planning(design, tmp_path, snapshot)['directions'][0]
    assert actual == selected
    oracle = validate_direction_binding(actual, snapshot)
    assert oracle == {'source_sha256': [snapshot['assets'][0]['sha256']], 'expected_charts': [
        {'slide': 2, 'type': 'column', 'categories': ['2019', '2024'],
         'series': [{'name': 'Temperature (°C)', 'values': [0.98, 1.28]},
                    {'name': 'Measurement', 'values': [0.01, -0.025]}]}]}
    assert len(canonical_bytes(actual['source_chart_binding'])) <= MAX_WIRE
    assert task_chart_oracle(task(snapshot, actual)) == oracle


@pytest.mark.parametrize('changes', [
    {'table_id': 'not-authorized'}, {'slide': True}, {'slide': 0}, {'slide': 4},
    {'category_column': True}, {'category_column': -1}, {'series_columns': [True]},
    {'series_columns': [1, 1]}, {'series_columns': [0]}, {'series_columns': [3]},
    {'type': 'pie'}, {'type': 'scatter'}, {'values': [1000, 2000]},
])
def test_malformed_ambiguous_and_unauthorized_chart_references_fail(tmp_path, changes):
    snapshot, catalog, _ = bound_case(tmp_path)
    with pytest.raises(ControlError):
        bind_direction(direction(catalog['tables'][0]['table_id'], **changes), snapshot, catalog)


@pytest.mark.parametrize('changed', ['direction', 'snapshot', 'expected', 'spec', 'source', 'digest', 'rows'])
def test_frozen_binding_rejects_corruption_and_mapping_changes(tmp_path, changed):
    snapshot, _, selected = bound_case(tmp_path)
    binding = selected['source_chart_binding']
    if changed == 'direction': selected['id'] = 'B'
    elif changed == 'snapshot': snapshot['hash'] = 'b' * 64
    elif changed == 'expected': binding['expected_charts'][0]['series'][0]['values'][0] = 900
    elif changed == 'spec': selected['native_chart_specs'][0]['slide'] = 1
    elif changed == 'source': binding['charts'][0]['asset_sha256'] = 'a' * 64
    elif changed == 'digest': binding['binding_sha256'] = 'b' * 64
    elif changed == 'rows': binding['charts'][0]['rows'][0][1] = '900'
    if changed not in ('snapshot', 'digest'):
        binding['binding_sha256'] = digest({k: v for k, v in binding.items() if k != 'binding_sha256'})
    with pytest.raises(ControlError):
        validate_direction_binding(selected, snapshot)


def test_generation_revision_and_acceptance_resolve_identical_frozen_values(tmp_path, monkeypatch):
    snapshot, _, selected = bound_case(tmp_path)
    frozen = task(snapshot, selected)
    expected = task_chart_oracle(frozen)
    # Changed originals and deployment files cannot reinterpret this dispatched
    # task. Current-source admission is a separate snapshot/confirmation gate.
    (tmp_path / snapshot['assets'][0]['storage_key']).write_text('new source')
    monkeypatch.setenv('PPT_CHART_ORACLE_FILE', str(tmp_path / 'not-read.json'))
    for phase in ('generate', 'revise'):
        assert task_chart_oracle(dict(frozen, phase=phase)) == expected
    returned = task_chart_oracle(frozen)
    returned['expected_charts'][0]['series'][0]['values'][0] = 999
    assert task_chart_oracle(frozen) == expected


def test_legacy_chart_free_and_unbound_declared_chart_requirements(tmp_path):
    snapshot = original(tmp_path)
    legacy = {'id': 'A'}
    assert validate_direction_binding(legacy, snapshot)['expected_charts'] == []
    legacy['native_chart_specs'] = [{'table_id': 'unknown'}]
    with pytest.raises(ControlError, match='source_chart_binding_required'):
        validate_direction_binding(legacy, snapshot)
    legacy.pop('native_chart_specs')
    frozen = task(snapshot, legacy)
    frozen['input_snapshot']['expected_charts'] = [{'forged': True}]
    assert task_chart_oracle(frozen) == {'expected_charts': [], 'source_sha256': []}


def test_catalog_budget_omits_entire_tables_and_never_truncates_rows(tmp_path):
    large = '| Category | Value |\n| --- | --- |\n' + ''.join('| ' + ('A' * 150) + f'{n} | {n} |\n' for n in range(35))
    snapshot = original(tmp_path, large + '\n' + TABLE)
    catalog = discover_tables(tmp_path, snapshot)
    assert len(canonical_bytes(catalog)) <= MAX_WIRE
    assert all(len(table['rows']) in (2, 35) for table in catalog['tables'])
    assert catalog['tables']


def test_binding_budget_never_silently_drops_series_or_source_rows(tmp_path):
    large = '| Category | Value |\n| --- | --- |\n' + ''.join('| ' + ('A' * 150) + f'{n} | {n} |\n' for n in range(35))
    snapshot = original(tmp_path, large)
    catalog = discover_tables(tmp_path, snapshot)
    assert len(catalog['tables']) == 1
    selected = direction(catalog['tables'][0]['table_id'], series_columns=[1])
    with pytest.raises(ControlError):
        bind_direction(selected, snapshot, catalog)
