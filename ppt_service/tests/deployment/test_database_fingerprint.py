"""Contracts for migration evidence; synthetic rows only, no database calls."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


HELPER = Path(__file__).resolve().parents[2] / 'deploy/k8s/database_fingerprint.py'
SPEC = importlib.util.spec_from_file_location('ppt_database_fingerprint_contract_tests', HELPER)
fingerprints = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fingerprints)

TABLE = 'reader\'s "notes"'
SEQUENCE = 'ppt_note_id_seq'
SHA256 = '0123456789abcdef' * 4
SOURCE_ACTIVE_INDEX = (
    "CREATE UNIQUE INDEX uq_project_active_job ON public.jobs USING btree (project_id) "
    "WHERE ((status)::text = ANY ((ARRAY['queued'::character varying, 'starting'::character varying, "
    "'running'::character varying, 'collecting'::character varying, 'validating'::character varying, "
    "'cancelling'::character varying])::text[]))"
)
RESTORED_ACTIVE_INDEX = (
    "CREATE UNIQUE INDEX uq_project_active_job ON public.jobs USING btree (project_id) "
    "WHERE ((status)::text = ANY (ARRAY[('queued'::character varying)::text, ('starting'::character varying)::text, "
    "('running'::character varying)::text, ('collecting'::character varying)::text, "
    "('validating'::character varying)::text, ('cancelling'::character varying)::text]))"
)


def catalog():
    return {
        'database': {'name': 'ppt_service', 'owner': 'ppt_migrator', 'acl': []},
        'schemas': [{'name': 'public', 'owner': 'ppt_migrator', 'acl': []}],
        'relations': [
            {'schema': 'public', 'name': TABLE, 'kind': 'r'},
            {'schema': 'public', 'name': SEQUENCE, 'kind': 'S'},
            {'schema': 'public', 'name': 'notes_view', 'kind': 'v'},
        ],
        'columns': [], 'constraints': [], 'indexes': [], 'triggers': [],
        'functions': [], 'sequence_definitions': [], 'default_acl': [],
        'row_security_policies': [],
    }


def data():
    return {
        'tables': {TABLE: {'rows': 2, 'sha256': SHA256}},
        'sequences': {SEQUENCE: {'last_value': 7, 'is_called': True}},
    }


def recording_psql(*responses):
    queries = []

    def run(sql):
        queries.append(sql)
        response = responses[len(queries) - 1]
        return json.dumps(response)

    return run, queries


def test_queries_are_read_only_stable_and_keep_business_content_in_postgres():
    structure, content = catalog(), data()
    psql, queries = recording_psql(structure, content)
    result = fingerprints.fingerprint(psql)

    assert result == {
        'format': 'ppt-postgres-fingerprint-v1', 'schema': 'public',
        'catalog': structure, **content,
    }
    assert len(queries) == 2
    for sql in queries:
        assert sql.startswith('BEGIN READ ONLY;\n') and sql.endswith('\nCOMMIT;\n')
        for setting in (
            'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;',
            "SET LOCAL TIME ZONE 'UTC';", "SET LOCAL datestyle = 'ISO, YMD';",
            "SET LOCAL intervalstyle = 'iso_8601';", 'SET LOCAL extra_float_digits = 3;',
            "SET LOCAL bytea_output = 'hex';", 'SET LOCAL standard_conforming_strings = on;',
            'SET LOCAL search_path = pg_catalog;', 'SET LOCAL row_security = off;',
        ):
            assert setting in sql
        assert 'nextval(' not in sql and 'setval(' not in sql
    schema_sql, rows_sql = queries
    # Compare semantic definitions and role names, independent of OIDs and ACL order.
    for evidence in ('pg_get_triggerdef', 'pg_get_constraintdef', 'pg_get_indexdef',
                     'pg_get_functiondef', 'pg_get_userbyid', 'aclexplode', 'pg_sequence'):
        assert evidence in schema_sql
    assert 'ORDER BY a.grantor COLLATE "C", a.grantee COLLATE "C"' in schema_sql
    assert 'FROM "public"."reader\'s ""notes""" AS row_value' in rows_sql
    assert "SELECT 'reader''s \"notes\"' AS name, count(*) AS rows" in rows_sql
    assert 'to_jsonb(row_value)::text' in rows_sql
    assert 'ORDER BY row_hash COLLATE "C"' in rows_sql
    assert 'string_agg(DISTINCT' not in rows_sql
    assert 'last_value, is_called FROM "public"."ppt_note_id_seq"' in rows_sql
    assert 'FROM "public"."notes_view"' not in rows_sql


@pytest.mark.parametrize('unsupported, error', [
    ('foreign-table', 'foreign_tables_require_separate_fingerprint_review'),
    ('unknown-relation', 'invalid_database_fingerprint_relations'),
    ('aggregate', 'aggregate_functions_require_separate_fingerprint_review'),
])
def test_unsupported_catalog_objects_stop_before_reading_rows(unsupported, error):
    structure = catalog()
    if unsupported == 'foreign-table':
        structure['relations'][0]['kind'] = 'f'
    elif unsupported == 'unknown-relation':
        structure['relations'][0]['kind'] = 'x'
    else:
        structure['functions'] = [{'name': 'custom_aggregate', 'kind': 'a'}]
    psql, queries = recording_psql(structure)
    with pytest.raises(ValueError, match='^' + error + '$'):
        fingerprints.fingerprint(psql)
    assert len(queries) == 1


@pytest.mark.parametrize('invalid_table', [
    {'rows': 2, 'sha256': SHA256[:-1]},
    {'rows': 2, 'sha256': SHA256.upper()},
    {'rows': 2, 'sha256': 'g' * 64},
    {'rows': True, 'sha256': SHA256},
    {'rows': -1, 'sha256': SHA256},
    {'rows': 2, 'sha256': SHA256, 'business_row': 'PRIVATE_SENTINEL'},
])
def test_table_evidence_rejects_invalid_digests_counts_and_extra_business_content(invalid_table):
    content = data()
    content['tables'][TABLE] = invalid_table
    psql, _ = recording_psql(catalog(), content)
    with pytest.raises(ValueError, match='^invalid_database_fingerprint_table$') as failure:
        fingerprints.fingerprint(psql)
    assert 'PRIVATE_SENTINEL' not in str(failure.value)


def test_changed_relation_inventory_is_not_accepted_as_complete_evidence():
    content = data()
    content['tables'].clear()
    psql, _ = recording_psql(catalog(), content)
    with pytest.raises(ValueError, match='^database_fingerprint_inventory_changed$'):
        fingerprints.fingerprint(psql)


def test_malformed_psql_output_never_enters_error_messages():
    def psql(sql):
        return 'PRIVATE_SENTINEL: malformed output'

    with pytest.raises(ValueError, match='^invalid_database_fingerprint_result$') as failure:
        fingerprints.fingerprint(psql)
    assert 'PRIVATE_SENTINEL' not in str(failure.value)


def index_fingerprint(definition, *, relation='jobs', name='uq_project_active_job'):
    structure = catalog()
    structure['indexes'] = [{'relation': relation, 'name': name, 'owner': 'ppt_migrator',
        'valid': True, 'ready': True, 'live': True, 'replica_identity': False,
        'clustered': False, 'definition': definition}]
    psql, _ = recording_psql(structure, data())
    return fingerprints.fingerprint(psql)


def test_exact_original_active_job_index_definition_is_unchanged():
    result = index_fingerprint(SOURCE_ACTIVE_INDEX)
    assert result['catalog']['indexes'] == [{'relation': 'jobs', 'name': 'uq_project_active_job',
        'owner': 'ppt_migrator', 'valid': True, 'ready': True, 'live': True,
        'replica_identity': False, 'clustered': False, 'definition': SOURCE_ACTIVE_INDEX}]


def test_exact_restored_active_job_index_is_canonicalized_to_original_definition():
    assert index_fingerprint(RESTORED_ACTIVE_INDEX) == index_fingerprint(SOURCE_ACTIVE_INDEX)


@pytest.mark.parametrize('definition, context', [
    (RESTORED_ACTIVE_INDEX.replace("'queued'", "'succeeded'"), {}),
    (RESTORED_ACTIVE_INDEX.replace('CREATE UNIQUE INDEX', 'CREATE INDEX'), {}),
    (RESTORED_ACTIVE_INDEX.replace('(project_id)', '(issuer)'), {}),
    (RESTORED_ACTIVE_INDEX.replace('ON public.jobs ', 'ON public.other_jobs '), {}),
    (RESTORED_ACTIVE_INDEX, {'relation': 'other_jobs'}),
    (RESTORED_ACTIVE_INDEX, {'name': 'other_active_index'}),
    (RESTORED_ACTIVE_INDEX + ';', {}),
])
def test_changed_active_job_index_is_never_normalized_or_hidden(definition, context):
    changed = index_fingerprint(definition, **context)
    assert changed != index_fingerprint(SOURCE_ACTIVE_INDEX)
    assert changed['catalog']['indexes'][0]['definition'] == definition
