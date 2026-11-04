"""Passive source-table wire context; never a human or production verdict."""
import copy
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError
from agent_service.deploy.acceptance_probe import fixture
from agent_service.profiles.ppt import CHECKPOINT_SCHEMA, PPT_MASTER_REVISION, PPTProfile


def task_with_catalog():
    task, _ = fixture('ppt.plan_initial.v1')
    task.update(phase='plan_options', schema_version='ppt-plan_options.v1')
    task['confirmations'] = {'stage1': {'channel': 'website_database',
        'plan_id': str(uuid4()), 'plan_revision': 1, 'plan_sha256': 'b' * 64,
        'input_snapshot_sha256': task['input_snapshot_sha256'],
        'confirmed_at': '2026-11-05T00:00:00+08:00',
        'selection': {'design_mode': 'free_design'}, 'outline': {}, 'design': {}}}
    task['checkpoint'] = {'schema_version': CHECKPOINT_SCHEMA,
        'ppt_master_revision': PPT_MASTER_REVISION, 'project_id': task['project_id'],
        'input_snapshot_sha256': task['input_snapshot_sha256'], 'phase': 'plan_initial',
        'project_root': 'deck', 'total_bytes': 1, 'file_count': 1, 'sha256': 'c' * 64,
        'files_sha256': 'd' * 64, 'approved_specs': {}}
    source = task['input_snapshot']['assets'][0]
    task['source_table_catalog'] = {
        'schema_version': 1, 'input_snapshot_sha256': task['input_snapshot_sha256'],
        'tables': [{'table_id': 'md:source:1', 'asset_id': source['id'],
            'asset_sha256': source['sha256'], 'source_label': '资料表 1',
            'line_start': 2, 'headers': ['年度', '数值（单位）'],
            'rows': [['2023', '1.17'], ['2024', '1.28']],
            'column_types': ['number', 'number']}], 'unsupported': []}
    return task


def test_legacy_task_remains_compatible_and_source_context_is_passive():
    task = task_with_catalog()
    profile = PPTProfile('plan_options')
    profile.validate_task(task)
    prompt = profile.build_prompt(task)
    assert 'zero-based' in prompt and 'source_chart_binding' in prompt
    assert 'before the existing direction confirmation' in prompt
    task.pop('source_table_catalog')
    profile.validate_task(task)


@pytest.mark.parametrize('changed', [
    'foreign_asset', 'foreign_hash', 'foreign_snapshot', 'duplicate_table',
    'extra_authority', 'rows_truncated', 'boolean_line', 'unknown_column_type',
    'table_path', 'excessive_bytes', 'duplicate_header', 'unknown_unsupported_asset',
])
def test_source_context_rejects_unbound_or_ambiguous_catalog(changed):
    task = task_with_catalog()
    catalog = task['source_table_catalog']
    table = catalog['tables'][0]
    if changed == 'foreign_asset': table['asset_id'] = '00000000-0000-0000-0000-000000000000'
    elif changed == 'foreign_hash': table['asset_sha256'] = 'f' * 64
    elif changed == 'foreign_snapshot': catalog['input_snapshot_sha256'] = 'f' * 64
    elif changed == 'duplicate_table': catalog['tables'].append(copy.deepcopy(table))
    elif changed == 'extra_authority': catalog['confirmed'] = True
    elif changed == 'rows_truncated': table['rows'][0].pop()
    elif changed == 'boolean_line': table['line_start'] = True
    elif changed == 'unknown_column_type': table['column_types'][1] = 'formula'
    elif changed == 'table_path': table['table_id'] = '../../private'
    elif changed == 'excessive_bytes': table['rows'] = [['x' * 500, 'x' * 500] for _ in range(40)]
    elif changed == 'duplicate_header': table['headers'][1] = table['headers'][0]
    elif changed == 'unknown_unsupported_asset':
        catalog['unsupported'].append({'asset_id': 'foreign', 'reason': 'unsupported_format'})
    with pytest.raises(ServiceError):
        PPTProfile('plan_options').validate_task(task)


def test_catalog_is_not_accepted_as_another_stage_override():
    source = task_with_catalog()['source_table_catalog']
    task, _ = fixture('ppt.plan_initial.v1')
    task['source_table_catalog'] = source
    with pytest.raises(ServiceError, match='ppt_source_table_catalog_phase_invalid'):
        PPTProfile('plan_initial').validate_task(task)
