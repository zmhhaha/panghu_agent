"""Read-only, content-private fingerprints for a quiesced PostgreSQL database.

``fingerprint(psql)`` takes a callable bound to the database and admin user.
It makes two quiet, unaligned, tuples-only psql calls. Writers and schema
changes must already be stopped: the two calls use separate read-only
transactions. Business rows never leave PostgreSQL; only counts and hashes do.
No pg_dump output, physical identifiers, server version, size or role secrets
participate in the result.
"""
from __future__ import annotations

import json
import re
from typing import Callable


FORMAT = 'ppt-postgres-fingerprint-v1'
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')
_CATALOG_ARRAYS = {'schemas', 'relations', 'columns', 'constraints', 'indexes',
                   'triggers', 'functions', 'sequence_definitions', 'default_acl',
                   'row_security_policies'}


def quote_ident(value: str) -> str:
    """Quote one catalog-supplied identifier, including embedded quotes."""
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError('invalid_fingerprint_identifier')
    return '"' + value.replace('"', '""') + '"'


def _literal(value: str) -> str:
    if not isinstance(value, str) or '\x00' in value:
        raise ValueError('invalid_fingerprint_literal')
    # Every transaction explicitly enables standard_conforming_strings.
    return "'" + value.replace("'", "''") + "'"


def _transaction(sql: str) -> str:
    return """BEGIN READ ONLY;
SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;
SET LOCAL TIME ZONE 'UTC';
SET LOCAL datestyle = 'ISO, YMD';
SET LOCAL intervalstyle = 'iso_8601';
SET LOCAL extra_float_digits = 3;
SET LOCAL bytea_output = 'hex';
SET LOCAL standard_conforming_strings = on;
SET LOCAL search_path = pg_catalog;
SET LOCAL row_security = off;
""" + sql + '\nCOMMIT;\n'


def _acl(expression: str) -> str:
    """Normalize ACL entries rather than comparing array insertion order."""
    return """COALESCE((SELECT jsonb_agg(to_jsonb(a)
        ORDER BY a.grantor COLLATE "C", a.grantee COLLATE "C",
                 a.privilege COLLATE "C", a.grantable)
      FROM (SELECT pg_get_userbyid(x.grantor) AS grantor,
                   CASE WHEN x.grantee=0 THEN 'PUBLIC'
                        ELSE pg_get_userbyid(x.grantee) END AS grantee,
                   x.privilege_type AS privilege, x.is_grantable AS grantable
            FROM aclexplode(CASE WHEN cardinality(""" + expression + """)>0
                THEN """ + expression + """ ELSE NULL::aclitem[] END) x) a), '[]'::jsonb)"""


def _records(query: str, order: str) -> str:
    return "COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY " + order + ") FROM (" + query + ") x), '[]'::jsonb)"


def _catalog_sql() -> str:
    database = """SELECT d.datname AS name, pg_get_userbyid(d.datdba) AS owner,
        """ + _acl("COALESCE(d.datacl, acldefault('d', d.datdba))") + """ AS acl
        FROM pg_database d WHERE d.datname=current_database()"""
    schema = """SELECT n.nspname AS name, pg_get_userbyid(n.nspowner) AS owner,
        """ + _acl("COALESCE(n.nspacl, acldefault('n', n.nspowner))") + """ AS acl
        FROM pg_namespace n WHERE n.nspname='public'"""
    relations = """SELECT n.nspname AS schema, c.relname AS name, c.relkind AS kind,
        pg_get_userbyid(c.relowner) AS owner, c.relpersistence AS persistence,
        c.relreplident AS replica_identity, c.relrowsecurity AS row_security,
        c.relforcerowsecurity AS force_row_security,
        COALESCE((SELECT jsonb_agg(o ORDER BY o COLLATE "C") FROM unnest(c.reloptions) o), '[]'::jsonb) AS options,
        """ + _acl("COALESCE(c.relacl, acldefault(CASE WHEN c.relkind='S' THEN 'S'::\"char\" ELSE 'r'::\"char\" END, c.relowner))") + """ AS acl,
        CASE WHEN c.relkind IN ('v','m') THEN pg_get_viewdef(c.oid, false) END AS view_definition,
        CASE WHEN c.relkind='p' THEN pg_get_partkeydef(c.oid) END AS partition_key,
        CASE WHEN c.relispartition THEN pg_get_expr(c.relpartbound,c.oid,false) END AS partition_bound,
        COALESCE((SELECT jsonb_agg(jsonb_build_object('schema',pn.nspname,'name',pc.relname)
                        ORDER BY i.inhseqno)
                  FROM pg_inherits i JOIN pg_class pc ON pc.oid=i.inhparent
                  JOIN pg_namespace pn ON pn.oid=pc.relnamespace WHERE i.inhrelid=c.oid), '[]'::jsonb) AS parents
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f')"""
    columns = """SELECT c.relname AS relation, a.attnum AS position, a.attname AS name,
        format_type(a.atttypid,a.atttypmod) AS type, a.attnotnull AS not_null,
        a.attidentity AS identity, a.attgenerated AS generated,
        a.attstorage AS storage, a.attcompression AS compression,
        pg_get_expr(d.adbin,d.adrelid,false) AS default_expression,
        CASE WHEN a.attcollation<>0 THEN jsonb_build_object('schema',cn.nspname,'name',co.collname) END AS collation,
        """ + _acl("COALESCE(a.attacl, '{}'::aclitem[])") + """ AS acl
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
        LEFT JOIN pg_collation co ON co.oid=a.attcollation
        LEFT JOIN pg_namespace cn ON cn.oid=co.collnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
          AND a.attnum>0 AND NOT a.attisdropped"""
    constraints = """SELECT c.relname AS relation, t.typname AS domain, k.conname AS name,
        k.contype AS type, k.condeferrable AS deferrable, k.condeferred AS deferred,
        k.convalidated AS validated, k.connoinherit AS no_inherit,
        pg_get_constraintdef(k.oid,false) AS definition
        FROM pg_constraint k JOIN pg_namespace n ON n.oid=k.connamespace
        LEFT JOIN pg_class c ON c.oid=k.conrelid LEFT JOIN pg_type t ON t.oid=k.contypid
        WHERE n.nspname='public'"""
    indexes = """SELECT t.relname AS relation, c.relname AS name,
        pg_get_userbyid(c.relowner) AS owner, i.indisvalid AS valid,
        i.indisready AS ready, i.indislive AS live,
        i.indisreplident AS replica_identity, i.indisclustered AS clustered,
        pg_get_indexdef(i.indexrelid,0,false) AS definition
        FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace JOIN pg_class c ON c.oid=i.indexrelid
        WHERE n.nspname='public'"""
    triggers = """SELECT c.relname AS relation, t.tgname AS name, t.tgenabled AS enabled,
        pg_get_triggerdef(t.oid,false) AS definition
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND NOT t.tgisinternal"""
    functions = """SELECT p.proname AS name, p.prokind AS kind,
        pg_get_function_identity_arguments(p.oid) AS arguments,
        pg_get_userbyid(p.proowner) AS owner,
        """ + _acl("COALESCE(p.proacl, acldefault('f', p.proowner))") + """ AS acl,
        CASE WHEN p.prokind<>'a' THEN pg_get_functiondef(p.oid) END AS definition
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'"""
    sequences = """SELECT c.relname AS name, format_type(s.seqtypid,NULL) AS type,
        s.seqstart AS start, s.seqincrement AS increment, s.seqmax AS max,
        s.seqmin AS min, s.seqcache AS cache, s.seqcycle AS cycle,
        COALESCE((SELECT jsonb_agg(jsonb_build_object('schema',tn.nspname,
                     'table',t.relname,'column',a.attname,'dependency',d.deptype)
                     ORDER BY tn.nspname COLLATE "C",t.relname COLLATE "C",a.attnum,d.deptype)
                  FROM pg_depend d JOIN pg_class t ON t.oid=d.refobjid
                  JOIN pg_namespace tn ON tn.oid=t.relnamespace
                  JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid
                  WHERE d.classid='pg_class'::regclass AND d.objid=c.oid
                    AND d.refclassid='pg_class'::regclass AND d.deptype IN ('a','i')), '[]'::jsonb) AS owned_by
        FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'"""
    defaults = """SELECT pg_get_userbyid(d.defaclrole) AS owner,
        n.nspname AS schema, d.defaclobjtype AS type,
        """ + _acl('d.defaclacl') + """ AS acl
        FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
        WHERE d.defaclnamespace=0 OR n.nspname='public'"""
    policies = """SELECT c.relname AS relation, p.polname AS name,
        p.polcmd AS command, p.polpermissive AS permissive,
        COALESCE((SELECT jsonb_agg(role_name ORDER BY role_name COLLATE "C") FROM
            (SELECT CASE WHEN r=0 THEN 'PUBLIC' ELSE pg_get_userbyid(r) END AS role_name
             FROM unnest(p.polroles) r) roles), '[]'::jsonb) AS roles,
        pg_get_expr(p.polqual,p.polrelid,false) AS using_expression,
        pg_get_expr(p.polwithcheck,p.polrelid,false) AS check_expression
        FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'"""
    entries = [
        ('database', '(SELECT to_jsonb(x) FROM (' + database + ') x)'),
        ('schemas', _records(schema, 'x.name COLLATE "C"')),
        ('relations', _records(relations, 'x.name COLLATE "C", x.kind')),
        ('columns', _records(columns, 'x.relation COLLATE "C", x.position')),
        ('constraints', _records(constraints, 'x.relation COLLATE "C", x.domain COLLATE "C", x.name COLLATE "C"')),
        ('indexes', _records(indexes, 'x.relation COLLATE "C", x.name COLLATE "C"')),
        ('triggers', _records(triggers, 'x.relation COLLATE "C", x.name COLLATE "C"')),
        ('functions', _records(functions, 'x.name COLLATE "C", x.arguments COLLATE "C", x.kind')),
        ('sequence_definitions', _records(sequences, 'x.name COLLATE "C"')),
        ('default_acl', _records(defaults, 'x.owner COLLATE "C", x.schema COLLATE "C", x.type')),
        ('row_security_policies', _records(policies, 'x.relation COLLATE "C", x.name COLLATE "C"')),
    ]
    return 'SELECT jsonb_build_object(\n' + ',\n'.join(_literal(key) + ', ' + query for key, query in entries) + '\n);'


def _data_sql(relations: list[dict]) -> str:
    tables, sequences = [], []
    for relation in relations:
        name, kind = relation['name'], relation['kind']
        qualified = quote_ident('public') + '.' + quote_ident(name)
        if kind in ('r', 'p', 'm'):
            # Match SELECT count(*) FROM public.table semantics, including
            # inherited/partitioned rows even when a child is in another schema.
            # A public child also has its own entry; overlap is intentional.
            tables.append("""SELECT """ + _literal(name) + """ AS name, count(*) AS rows,
                encode(sha256(convert_to(COALESCE(string_agg(row_hash,''
                    ORDER BY row_hash COLLATE "C"),''),'UTF8')),'hex') AS sha256
                FROM (SELECT encode(sha256(convert_to(to_jsonb(row_value)::text,'UTF8')),'hex') AS row_hash
                      FROM """ + qualified + ' AS row_value) row_hashes')
        elif kind == 'S':
            sequences.append('SELECT ' + _literal(name) + ' AS name, last_value, is_called FROM ' + qualified)
        elif kind == 'f':
            # The database's stopped writers cannot freeze a foreign server.
            raise ValueError('foreign_tables_require_separate_fingerprint_review')
    table_query = ' UNION ALL '.join(tables) if tables else 'SELECT NULL::text AS name, 0::bigint AS rows, NULL::text AS sha256 WHERE false'
    sequence_query = ' UNION ALL '.join(sequences) if sequences else 'SELECT NULL::text AS name, 0::bigint AS last_value, false AS is_called WHERE false'
    return """SELECT jsonb_build_object(
        'tables', COALESCE((SELECT jsonb_object_agg(name,
            jsonb_build_object('rows',rows,'sha256',sha256) ORDER BY name COLLATE "C")
            FROM (""" + table_query + """) t), '{}'::jsonb),
        'sequences', COALESCE((SELECT jsonb_object_agg(name,
            jsonb_build_object('last_value',last_value,'is_called',is_called) ORDER BY name COLLATE "C")
            FROM (""" + sequence_query + """) s), '{}'::jsonb));"""


def _read(psql: Callable[[str], str], sql: str) -> dict:
    try:
        result = json.loads(psql(_transaction(sql)))
    except (TypeError, ValueError):
        # Neither malformed psql output nor business data belongs in errors.
        raise ValueError('invalid_database_fingerprint_result') from None
    if not isinstance(result, dict):
        raise ValueError('invalid_database_fingerprint_result')
    return result


def fingerprint(psql: Callable[[str], str]) -> dict:
    """Return complete public data/structure/ACL evidence without any writes.

    The caller must stop writers, bind psql to the intended DB/admin and use
    ON_ERROR_STOP. Sequence reads do not call nextval/setval. Per-table hashes
    sort fixed-width SHA256 row digests, preserving duplicates and row counts.
    An unsupported foreign table/aggregate fails closed instead of silently
    yielding an incomplete migration comparison.
    """
    catalog = _read(psql, _catalog_sql())
    if (set(catalog) != _CATALOG_ARRAYS | {'database'}
            or any(not isinstance(catalog[key], list) for key in _CATALOG_ARRAYS)
            or any(not isinstance(row, dict) for key in _CATALOG_ARRAYS for row in catalog[key])
            or [row.get('name') for row in catalog['schemas']] != ['public']
            or not isinstance(catalog.get('database'), dict)):
        raise ValueError('incomplete_database_fingerprint_catalog')
    if any(row.get('kind') == 'a' for row in catalog.get('functions', [])):
        raise ValueError('aggregate_functions_require_separate_fingerprint_review')
    relations = catalog['relations']
    if any(not isinstance(row, dict) or row.get('schema') != 'public'
           or not isinstance(row.get('name'), str) or not row['name'] or '\x00' in row['name']
           or row.get('kind') not in ('r', 'p', 'm', 'v', 'S', 'f') for row in relations):
        raise ValueError('invalid_database_fingerprint_relations')
    data = _read(psql, _data_sql(relations))
    tables, sequences = data.get('tables'), data.get('sequences')
    if set(data) != {'tables', 'sequences'} or not isinstance(tables, dict) or not isinstance(sequences, dict):
        raise ValueError('incomplete_database_fingerprint_data')
    if (set(tables) != {row['name'] for row in relations if row['kind'] in ('r','p','m')}
            or set(sequences) != {row['name'] for row in relations if row['kind'] == 'S'}):
        raise ValueError('database_fingerprint_inventory_changed')
    for value in tables.values():
        if (not isinstance(value, dict) or set(value) != {'rows', 'sha256'}
                or type(value['rows']) is not int or value['rows'] < 0
                or not isinstance(value['sha256'], str) or not _SHA256.fullmatch(value['sha256'])):
            raise ValueError('invalid_database_fingerprint_table')
    for value in sequences.values():
        if (not isinstance(value, dict) or set(value) != {'last_value', 'is_called'}
                or type(value['last_value']) is not int or type(value['is_called']) is not bool):
            raise ValueError('invalid_database_fingerprint_sequence')
    return {'format': FORMAT, 'schema': 'public', 'catalog': catalog,
            'tables': tables, 'sequences': sequences}
