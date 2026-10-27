#!/usr/bin/env python3
"""Read-only PostgreSQL fingerprints through psql; credentials stay in files.

This tool never dumps/restores, changes a schema, discovers production settings,
or claims that a database state proves OS/container termination.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile

from snapshot import Refused, atomic_json, encoded, open_parent, read_json

SCHEMA = "ppt-service.p1.database.v1"
TABLES = ("api_schema_revision", "assets", "checkpoints", "execution_slots", "idempotency_keys",
          "job_events", "jobs", "plans", "ppt_acceptance_entries", "ppt_acceptance_streams",
          "projects", "usage_records", "user_quotas", "versions")
INDEX_CAST_RULE = "ppt-jobs-active-index-cast-v1"
# PostgreSQL 16 pg_dump/restore reparses this one partial-index predicate by
# distributing the text[] cast over its varchar elements. These are the two
# complete DDL strings observed in the isolated rehearsal, not a SQL rewrite.
ACTIVE_INDEX_ARRAY_CAST = (
    "CREATE UNIQUE INDEX uq_project_active_job ON public.jobs USING btree (project_id) "
    "WHERE ((status)::text = ANY ((ARRAY['queued'::character varying, 'starting'::character varying, "
    "'running'::character varying, 'collecting'::character varying, 'validating'::character varying, "
    "'cancelling'::character varying])::text[]))"
)
ACTIVE_INDEX_ELEMENT_CAST = (
    "CREATE UNIQUE INDEX uq_project_active_job ON public.jobs USING btree (project_id) "
    "WHERE ((status)::text = ANY (ARRAY[('queued'::character varying)::text, ('starting'::character varying)::text, "
    "('running'::character varying)::text, ('collecting'::character varying)::text, "
    "('validating'::character varying)::text, ('cancelling'::character varying)::text]))"
)


def comparison_copy(report):
    """Keep raw captures intact; recognize only two complete known definitions."""
    result = copy.deepcopy(report)
    if result.get("database_schema") != "public":
        return result
    for definition in result.get("definitions", []):
        for variant in (ACTIVE_INDEX_ARRAY_CAST, ACTIVE_INDEX_ELEMENT_CAST):
            if definition == {"tablename": "jobs", "indexname": "uq_project_active_job", "indexdef": variant}:
                definition["indexdef"] = ACTIVE_INDEX_ARRAY_CAST
                break
    return result


def compare_manifests(expected, observed):
    """Return the rule used for a proven known-only difference, else reject."""
    if expected.get("schema") != SCHEMA or observed.get("schema") != SCHEMA:
        raise Refused("unsupported database manifest")
    if expected == observed:
        return []
    if comparison_copy(expected) == comparison_copy(observed):
        return [INDEX_CAST_RULE]
    raise Refused("database differs: rows, usage/quota, idempotency, schema definitions or quiescence")


def identifier(value):
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value or ""):
        raise Refused("schema/service names must be simple lowercase PostgreSQL identifiers")
    return value


def credential_file(path):
    parent, name = open_parent(path)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077
                    or info.st_uid not in (0, os.geteuid())):
                raise Refused("credential files must be private regular files, owner root/current UID, mode 0600 or 0400")
        finally:
            os.close(fd)
    finally:
        os.close(parent)
    return path


def query(schema, empty=False):
    schema = identifier(schema)
    prefix = f'"{schema}".'
    sql = ["BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;",
           "SET LOCAL TIME ZONE 'UTC'; SET LOCAL datestyle = 'ISO, YMD'; SET LOCAL extra_float_digits = 3; SET LOCAL search_path=pg_catalog;",
           "SELECT json_build_object('kind','tables','value',COALESCE(json_agg(tablename ORDER BY tablename),'[]'::json)) "
           f"FROM pg_tables WHERE schemaname='{schema}';"]
    if not empty:
        sql += [
            "SELECT json_build_object('kind','definition','value',to_jsonb(x)) FROM ("
            "SELECT table_name,column_name,ordinal_position,is_nullable,data_type,udt_name,column_default,is_identity,identity_generation "
            f"FROM information_schema.columns WHERE table_schema='{schema}' ORDER BY table_name,ordinal_position) x;",
            "SELECT json_build_object('kind','definition','value',to_jsonb(x)) FROM ("
            "SELECT c.relname AS table_name,k.conname,pg_get_constraintdef(k.oid,true) AS definition "
            "FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
            f"WHERE n.nspname='{schema}' ORDER BY c.relname,k.conname) x;",
            "SELECT json_build_object('kind','definition','value',to_jsonb(x)) FROM ("
            "SELECT tablename,indexname,indexdef FROM pg_indexes "
            f"WHERE schemaname='{schema}' ORDER BY tablename,indexname) x;",
            "SELECT json_build_object('kind','definition','value',to_jsonb(x)) FROM ("
            "SELECT c.relname AS table_name,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid,true) AS definition "
            "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
            f"WHERE n.nspname='{schema}' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname) x;",
            "SELECT json_build_object('kind','definition','value',to_jsonb(x)) FROM ("
            "SELECT p.proname,pg_get_function_identity_arguments(p.oid) AS arguments,pg_get_functiondef(p.oid) AS definition "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            f"WHERE n.nspname='{schema}' AND p.prokind='f' ORDER BY p.proname,arguments) x;",
            "SELECT json_build_object('kind','definition','value',to_jsonb(x)) FROM ("
            "SELECT sequencename,data_type::text,start_value,min_value,max_value,increment_by,cycle,cache_size,last_value "
            f"FROM pg_sequences WHERE schemaname='{schema}' ORDER BY sequencename) x;",
        ]
        for name in TABLES:
            sql += [f"SELECT json_build_object('kind','table','table','{name}');",
                    f"SELECT json_build_object('kind','row','table','{name}','value',to_jsonb(t)) "
                    f'FROM {prefix}"{name}" t ORDER BY to_jsonb(t)::text COLLATE "C";']
        sql += [
            "SELECT json_build_object('kind','quiescence','value',json_build_object("
            f"'nonterminal_jobs',(SELECT count(*) FROM {prefix}jobs WHERE status NOT IN ('succeeded','failed','cancelled','interrupted')),"
            f"'unproven_attached_jobs',(SELECT count(*) FROM {prefix}jobs WHERE (job_uid IS NOT NULL OR run_id IS NOT NULL) AND NOT stopped),"
            f"'active_projects',(SELECT count(*) FROM {prefix}projects WHERE active_job_id IS NOT NULL),"
            f"'slots',(SELECT count(*) FROM {prefix}execution_slots),"
            f"'idle_slot',(SELECT count(*) FROM {prefix}execution_slots WHERE id=1 AND job_id IS NULL),"
            f"'parser_writers_or_leases',(SELECT count(*) FROM {prefix}assets WHERE parse_status IN ('parsing','stopping') OR parse_lease_until IS NOT NULL)"
            "));",
        ]
    sql += ["SELECT json_build_object('kind','done');", "COMMIT;"]
    return "\n".join(sql)


def fingerprint(schema, service, service_file, passfile, psql="psql", empty=False):
    identifier(service)
    schema = identifier(schema)
    service_file, passfile = credential_file(service_file), credential_file(passfile)
    # Do not inherit DATABASE_URL, PG*, shell startup, proxy or application env.
    env = {key: value for key, value in os.environ.items() if key in ("PATH", "SYSTEMROOT", "TMPDIR")}
    env |= {"PGSERVICEFILE": service_file, "PGPASSFILE": passfile,
            "PGAPPNAME": "ppt-p1-offline-inventory", "LC_ALL": "C", "PGCONNECT_TIMEOUT": "10"}
    tables, definitions, digests, counts, state, done = None, [], {}, {}, None, False
    temporary = None
    process = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="ppt-p1-readonly-", suffix=".sql", delete=False) as script:
            script.write(query(schema, empty))
            temporary = script.name
        process = subprocess.Popen([psql, "-X", "-q", "-A", "-t", "--no-password", "--set", "ON_ERROR_STOP=1",
                                    "--dbname", "service=" + service, "--file", temporary],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=env, text=True, encoding="utf-8")
        for line in process.stdout:
            if not line.strip():
                continue
            item = json.loads(line)
            kind = item.get("kind")
            if kind == "tables":
                tables = item["value"]
                if (tables if empty else sorted(tables)) != ([] if empty else sorted(TABLES)):
                    raise Refused("schema must be empty" if empty else "schema is missing/has unexpected tables; refusing an incomplete inventory")
            elif kind == "definition":
                definitions.append(item["value"])
            elif kind == "table":
                name = item["table"]
                if name not in TABLES or name in digests:
                    raise Refused("unexpected or duplicate table marker")
                digests[name], counts[name] = hashlib.sha256(), 0
            elif kind == "row":
                name = item["table"]
                if name not in digests:
                    raise Refused("row appeared before its table marker")
                digests[name].update(encoded(item["value"]))
                counts[name] += 1
            elif kind == "quiescence":
                state = item["value"]
            elif kind == "done":
                done = True
            else:
                raise Refused("unexpected psql result")
        if process.wait() != 0 or not done or tables is None:
            raise Refused("read-only psql query failed (credentials and server error text withheld)")
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdout is not None:
                process.stdout.close()
        if temporary is not None:
            os.unlink(temporary)
    if empty:
        return {"schema": SCHEMA, "database_schema": schema, "empty_schema": True}
    if set(digests) != set(TABLES) or state is None:
        raise Refused("incomplete database inventory")
    required = {"nonterminal_jobs": 0, "unproven_attached_jobs": 0, "active_projects": 0,
                "slots": 1, "idle_slot": 1, "parser_writers_or_leases": 0}
    if state != required:
        raise Refused("database is not quiescent: " + json.dumps(state, sort_keys=True))
    return {"schema": SCHEMA, "database_schema": schema, "quiescence": state,
            "definitions": definitions,
            "tables": {name: {"rows": counts[name], "sha256": digests[name].hexdigest()} for name in sorted(digests)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "compare", "check-empty"))
    parser.add_argument("--schema", default="public")
    parser.add_argument("--service", required=True)
    parser.add_argument("--service-file", required=True)
    parser.add_argument("--passfile", required=True)
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--output")
    parser.add_argument("--expected")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    try:
        if args.action == "capture" and not args.output:
            raise Refused("capture requires a fresh --output path")
        if args.action == "compare" and (not args.expected or not args.expected_sha256):
            raise Refused("compare requires --expected and independently retained --expected-sha256")
        report = fingerprint(args.schema, args.service, args.service_file, args.passfile,
                             args.psql, args.action == "check-empty")
        comparison_rules = []
        if args.action == "compare":
            expected = read_json(args.expected, args.expected_sha256)
            comparison_rules = compare_manifests(expected, report)
        result = {"action": args.action, "verified": True, "read_only": True,
                  "writers_stopped_proven": False,
                  "tables": len(report.get("tables", {}))}
        if args.action == "compare":
            result["comparison_rules"] = comparison_rules
        if args.output:
            result["manifest_sha256"] = atomic_json(args.output, report)
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, Refused) as error:
        print("migration refused: " + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
