"""Current-schema addition to the fixed disposable P1 dump/restore rehearsal.

Seed only after the existing pg_fixture.py seed and before inventory/dump.
Verify-target only after the independent restore comparison. No arbitrary URL,
schema migration, file changes, production configuration or model transport.
All seeded decisions are pending/revoked synthetic history, never human passes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import uuid

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ppt_service.server.acceptance.store import AcceptanceEntry, AcceptanceStream, _digest, _scope
from ppt_service.server.auth import Identity
from ppt_service.server.models import AgentOutbox, ExecutionSlot, Job, Project, Version
from ppt_service.server.repository import ACTIVE

TABLES = {"api_schema_revision", "assets", "checkpoints", "execution_slots", "idempotency_keys",
          "job_events", "jobs", "plans", "ppt_acceptance_entries", "ppt_acceptance_streams",
          "ppt_agent_outbox", "projects", "usage_records", "user_quotas", "versions"}
STAMP = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
OWNER = Identity("https://p1-synthetic.invalid", "synthetic-alice")


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def identifier(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "ppt-p1-fixture/" + name))


def connect(database, role="ppt_migrator"):
    require(database in ("ppt_p1_source_fixture", "ppt_p1_restore_fixture"), "fixed_fixture_database_required")
    require(role in ("ppt_migrator", "ppt_runtime"), "fixed_fixture_role_required")
    return create_engine(f"postgresql+psycopg://{role}@/{database}?host=/pgsocket")


def expected():
    pid, jid, vid = (identifier(name) for name in ("project", "job", "version"))
    scope = _scope(OWNER, pid, vid)
    binding = {"project_id": pid, "version_id": vid, "source_revision": 4,
               "manifest_sha256": "1" * 64, "input_snapshot_sha256": "0" * 64,
               "pptx_sha256": "2" * 64, "source_fingerprint_sha256": "3" * 64,
               "evidence_id": identifier("backup-evidence"), "evidence_sha256": "4" * 64,
               "runtime": {"engine": "synthetic-no-execution", "runtime_revision": "fixture-only",
                           "profile": "backup-fixture", "checkpoint_format": "fixture-only"}}
    payload = {"pages": [{"number": 1, "preview_sha256": "5" * 64, "verdict": "pending", "issues": []}],
               "note": "Synthetic backup fixture; no human review or publication."}
    visual_id, revoke_id = identifier("backup-visual"), identifier("backup-revoke")
    records = [dict(id=visual_id, scope=scope, revision=1, request_key="backup-fixture-visual",
                    request_sha256=_digest(payload), kind="visual_review", created_at=STAMP.isoformat(),
                    issuer=OWNER.issuer, subject=OWNER.sub, binding=binding, payload=payload),
               dict(id=revoke_id, scope=scope, revision=2, request_key="backup-fixture-revoke",
                    request_sha256=_digest({"record_id": visual_id}), kind="revoke", created_at=STAMP.isoformat(),
                    issuer=OWNER.issuer, subject=OWNER.sub, binding=binding,
                    payload={"record_id": visual_id, "reason": "Synthetic revocation history retained for restore."})]
    outbox = dict(job_id=jid, attempt=1, fencing_token=1,
                  business_ref="fixture-only:backup:" + jid, idempotency_key="fixture-only:backup:" + jid,
                  request={"fixture_only": True, "no_execution": True, "execution_deadline_at": STAMP.timestamp()},
                  inputs=[], submission_started=True, remote_job_id=identifier("backup-remote"),
                  remote_pins={"fixture_only": True}, state="mapped", event_cursor=7,
                  checkpoint_ref={"checkpoint_id": identifier("backup-checkpoint"), "format": "fixture-only", "sha256": "6" * 64},
                  result={"fixture_only": True, "status": "no-execution"}, acked=True)
    stream = dict(scope=scope, issuer=OWNER.issuer, subject=OWNER.sub, project_id=pid, version_id=vid, revision=2)
    return stream, records, outbox


def quiescent(session):
    slot = session.get(ExecutionSlot, 1)
    require(slot is not None and slot.job_id is None, "fixture_slot_not_idle")
    require(session.scalar(select(Job.id).where(Job.status.in_(ACTIVE)).limit(1)) is None, "fixture_active_job")
    project, version, job = (session.get(model, identifier(name)) for model, name in
                             ((Project, "project"), (Version, "version"), (Job, "job")))
    require(project is not None and project.settings.get("fixture") is True, "p1_synthetic_fixture_required")
    require((project.issuer, project.subject) == (OWNER.issuer, OWNER.sub), "p1_synthetic_owner_required")
    require(version is not None and version.manifest.get("fixture_only") is True, "p1_synthetic_version_required")
    require(job is not None and job.stopped is True and job.input_snapshot.get("fixture_only") is True,
            "p1_synthetic_stopped_job_required")


def project_row(row, names):
    return {name: getattr(row, name) for name in names}


def check_current(engine):
    require(set(inspect(engine).get_table_names(schema="public")) == TABLES, "schema4_exact_table_set_required")
    with engine.connect() as connection:
        require(connection.scalar(text("SELECT max(revision) FROM public.api_schema_revision")) == 4,
                "schema4_revision_required")


def check_rows(engine):
    stream, records, outbox = expected()
    with Session(engine) as session:
        quiescent(session)
        saved_stream = session.get(AcceptanceStream, stream["scope"])
        require(saved_stream is not None and project_row(saved_stream, stream) == stream, "restored_review_stream_mismatch")
        for record in records:
            row = session.get(AcceptanceEntry, record["id"])
            require(row is not None and project_row(row, record) == record, "restored_review_history_mismatch")
        row = session.get(AgentOutbox, outbox["job_id"])
        require(row is not None and project_row(row, outbox) == outbox, "restored_agent_outbox_mismatch")
    digest = hashlib.sha256(json.dumps([stream, records, outbox], sort_keys=True).encode()).hexdigest()
    return digest


def seed(engine):
    check_current(engine)
    stream, records, outbox = expected()
    with Session(engine) as session, session.begin():
        quiescent(session)
        require(session.get(AcceptanceStream, stream["scope"]) is None and
                session.get(AgentOutbox, outbox["job_id"]) is None, "fresh_current_fixture_required")
        session.add(AcceptanceStream(**stream))
        session.flush()
        session.add_all([AcceptanceEntry(**record) for record in records])
        session.add(AgentOutbox(**outbox))
    return check_rows(engine)


def verify_immutability(engine):
    """Run no-op forbidden writes in rolled-back transactions on the new target."""
    stream, records, _ = expected()
    blocked = 0
    for sql in ("UPDATE public.ppt_acceptance_entries SET payload=payload WHERE id=:id",
                "DELETE FROM public.ppt_acceptance_entries WHERE id=:id"):
        try:
            with engine.begin() as connection:
                connection.execute(text(sql), {"id": records[0]["id"]})
                # Even a broken trigger must never commit this probe's deletion.
                raise RuntimeError("restored_review_append_only_trigger_missing")
        except DBAPIError as error:
            require(getattr(error.orig, "sqlstate", None) == "P0001", "unexpected_review_trigger_failure")
            blocked += 1
    return blocked


def verify_runtime_permissions():
    engine = connect("ppt_p1_restore_fixture", "ppt_runtime")
    try:
        with engine.connect() as connection:
            allowed = {
                "ppt_acceptance_entries": {"SELECT", "INSERT"},
                "ppt_acceptance_streams": {"SELECT", "INSERT", "UPDATE"},
                "ppt_agent_outbox": {"SELECT", "INSERT", "UPDATE", "DELETE"},
                "api_schema_revision": {"SELECT"},
            }
            for table, operations in allowed.items():
                for operation in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                    actual = connection.scalar(text("SELECT has_table_privilege(current_user,:table,:operation)"),
                                               {"table": "public." + table, "operation": operation})
                    require(bool(actual) == (operation in operations), "restored_runtime_permission_mismatch")
            require(connection.scalar(text("SELECT max(revision) FROM public.api_schema_revision")) == 4,
                    "restored_runtime_schema_revision_unreadable")
            require(not connection.scalar(text("SELECT has_schema_privilege(current_user,'public','CREATE')"))
                    and not connection.scalar(text("SELECT has_database_privilege(current_user,current_database(),'CREATE')")),
                    "restored_runtime_ddl_permission")
            require(not connection.scalar(text(
                "SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user")), "restored_runtime_elevated_role")
        # Roll back even if a forbidden statement unexpectedly succeeds. These
        # probes run only against the independently restored disposable fixture.
        for sql in ("CREATE TABLE public.current_backup_forbidden(id int)",
                    "ALTER TABLE public.projects ADD COLUMN current_backup_forbidden int",
                    "INSERT INTO public.api_schema_revision(revision) VALUES(99)",
                    "UPDATE public.api_schema_revision SET revision=revision",
                    "DELETE FROM public.api_schema_revision WHERE revision=99"):
            try:
                with engine.begin() as connection:
                    connection.execute(text(sql))
                    raise RuntimeError("restored_runtime_forbidden_write_succeeded")
            except DBAPIError as error:
                require(getattr(error.orig, "sqlstate", None) == "42501", "unexpected_runtime_denial_failure")
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "verify-source", "verify-target"))
    args = parser.parse_args()
    target = args.action == "verify-target"
    engine = connect("ppt_p1_restore_fixture" if target else "ppt_p1_source_fixture")
    try:
        check_current(engine)
        fingerprint = seed(engine) if args.action == "seed" else check_rows(engine)
        blocked = verify_immutability(engine) if target else None
        if target:
            verify_runtime_permissions()
            require(check_rows(engine) == fingerprint, "target_probe_changed_retained_history")
        print(json.dumps({"fixture_only": True, "model_requests": 0, "schema_revision": 4,
                          "tables": len(TABLES), "review_records": 2, "agent_outbox_rows": 1,
                          "current_records_sha256": fingerprint, "review_mutations_denied": blocked,
                          "runtime_permissions_verified": target, "action": args.action}, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
