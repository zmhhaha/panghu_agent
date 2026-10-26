"""Synthetic P1 migration fixture, only fixed disposable DB names/socket.

No API server, controller, worker, model, real deck, or production credential.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ppt_service.server.migrate import migrate
from ppt_service.server.models import (Project, Asset, Checkpoint, Plan, Job, JobEvent,
                                      Version, IdempotencyKey, UserQuota, UsageRecord)

DATABASES = {"ppt_p1_source_fixture", "ppt_p1_restore_fixture"}
STAMP = datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc)


def connect(database, role):
    if database not in DATABASES or role not in ("ppt_migrator", "ppt_runtime"):
        raise RuntimeError("only the fixed isolated P1 fixture database/roles are permitted")
    return create_engine(f"postgresql+psycopg://{role}@/{database}?host=/pgsocket")


def seed(root):
    root = Path(root)
    if root != Path("/evidence/source-data") or root.exists():
        raise RuntimeError("synthetic source data requires its fresh fixed evidence path")
    engine = connect("ppt_p1_source_fixture", "ppt_migrator")
    migrate(engine)
    ids = {key: str(uuid.uuid5(uuid.NAMESPACE_URL, "ppt-p1-fixture/" + key))
           for key in ("project", "asset", "checkpoint", "job", "version", "plan", "usage")}
    pid = ids["project"]
    asset_key = f'projects/{pid}/assets/{ids["asset"]}/original'
    checkpoint_key = f'projects/{pid}/checkpoints/{ids["checkpoint"]}'
    version_key = f'projects/{pid}/versions/{ids["version"]}'
    files = {
        asset_key: b"# Synthetic material\nP1 offline migration only.\n",
        checkpoint_key + "/checkpoint.json": b'{"synthetic":true,"production_accepted":false}\n',
        checkpoint_key + "/project/source.md": b"Synthetic retained checkpoint source.\n",
        version_key + "/artifact.txt": b"Synthetic history; this is not a generated deck.\n",
        "controller-state/fixture.json": b'{"fixture_only":true,"execution_enabled":false}\n',
        "material-locks/" + ids["asset"]: b"",
    }
    root.mkdir(mode=0o700)
    for key, content in files.items():
        target = root / key
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(content)
        target.chmod(0o600)
        os.utime(target, ns=(1700000000123456789, 1700000000123456789))
    (root / "empty-retained-directory").mkdir(mode=0o700)
    # The API data identity and root-private controller subtree intentionally differ.
    for target in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if target.parts[len(root.parts)] != "controller-state":
            os.chown(target, 10000, 10000)
    os.chown(root, 10000, 10000)
    common = {"issuer": "https://p1-synthetic.invalid", "subject": "synthetic-alice"}
    with Session(engine) as session, session.begin():
        project = Project(id=pid, title="Synthetic P1 migration", settings={"fixture": True},
                          external_model_ack=False, revision=4, stage="ready", created_at=STAMP, **common)
        session.add(project)
        session.flush()
        session.add(Asset(id=ids["asset"], project_id=pid, storage_key=asset_key,
                          display_name="synthetic.md", sha256=hashlib.sha256(files[asset_key]).hexdigest(),
                          media_type="text/markdown", bytes=len(files[asset_key]), parse_status="ready",
                          parse_attempt=1, parse_token=ids["asset"], parse_scope_uid=ids["job"],
                          parse_lease_until=None, text_summary="Synthetic only"))
        checkpoint = Checkpoint(id=ids["checkpoint"], project_id=pid, storage_key=checkpoint_key,
                                manifest={"fixture_only": True, "accepted": False}, created_at=STAMP, **common)
        session.add(checkpoint)
        session.flush()
        job = Job(id=ids["job"], project_id=pid, kind="generate", stage="generate", status="succeeded",
                  input_snapshot={"fixture_only": True, "hash": "0" * 64}, attempt=1, fencing_token=1,
                  stopped=True, event_cursor=2, event_floor=0, checkpoint_id=ids["checkpoint"], created_at=STAMP, **common)
        session.add(job)
        session.flush()
        session.add(Plan(id=ids["plan"], project_id=pid, revision=1, phase="plan_initial",
                         input_snapshot_hash="0" * 64, outline={"fixture_only": True}, design={"fixture_only": True},
                         checkpoint_id=ids["checkpoint"], confirmation_selection={"design_mode": "free_design"}, confirmed_at=STAMP))
        session.add(Version(id=ids["version"], project_id=pid, job_id=ids["job"], checkpoint_id=ids["checkpoint"],
                            artifact_root=version_key, manifest={"fixture_only": True, "publication_verified": False},
                            status="published", created_at=STAMP, **common))
        session.add_all([JobEvent(job_id=ids["job"], seq=1, type="artifact.ready", payload={"version_id": ids["version"]}, created_at=STAMP),
                         JobEvent(job_id=ids["job"], seq=2, type="job.succeeded", payload={}, created_at=STAMP)])
        session.add(IdempotencyKey(route=f"/projects/{pid}/generations", key="synthetic-idempotency",
                                   request_hash="1" * 64, response={"job_id": ids["job"], "fixture_only": True},
                                   expires_at=STAMP + timedelta(days=365), **common))
        session.add(UserQuota(enabled=True, period="synthetic-period", generation_limit=10, revision_limit=20,
                              generation_count=1, revision_count=2, **common))
        session.add(UsageRecord(id=ids["usage"], project_id=pid, job_id=ids["job"], attempt=1, phase="generate",
                                model="synthetic-no-model", provider="fixture", provider_request_id="synthetic-no-request",
                                input_tokens=7, output_tokens=11, cost_status="unknown", outcome="synthetic", **common))
        project.latest_version_id = ids["version"]
        project.confirmed_plan_revision = 1
    engine.dispose()
    return {"fixture_only": True, "files": len(files), "ids": ids, "model_requests": 0,
            "production_accepted": False, "source_data": str(root)}


def runtime_permissions():
    engine = connect("ppt_p1_restore_fixture", "ppt_runtime")
    table_names = ("assets", "checkpoints", "execution_slots", "idempotency_keys", "job_events", "jobs",
                   "plans", "projects", "usage_records", "user_quotas", "versions")
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT max(revision) FROM public.api_schema_revision"))
        assert revision == 2
        for table in table_names:
            for operation in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert connection.scalar(text("SELECT has_table_privilege(current_user,:table,:operation)"),
                                         {"table": "public." + table, "operation": operation})
        assert not connection.scalar(text("SELECT has_schema_privilege(current_user,'public','CREATE')"))
        assert not connection.scalar(text("SELECT has_database_privilege(current_user,current_database(),'CREATE')"))
    new_id = str(uuid.uuid4())
    with Session(engine) as session, session.begin():
        project = Project(id=new_id, issuer="https://p1-synthetic.invalid", subject="synthetic-runtime",
                          title="runtime insert", settings={}, stage="draft")
        session.add(project)
    with Session(engine) as session, session.begin():
        project = session.get(Project, new_id)
        assert project.title == "runtime insert"
        project.title = "runtime update"
    with Session(engine) as session, session.begin():
        project = session.get(Project, new_id)
        assert project.title == "runtime update"
        session.delete(project)
    blocked = []
    for sql in ("CREATE TABLE public.runtime_forbidden(id int)", "ALTER TABLE public.projects ADD COLUMN forbidden int",
                "INSERT INTO public.api_schema_revision(revision) VALUES(99)",
                "UPDATE public.api_schema_revision SET revision=revision",
                "DELETE FROM public.api_schema_revision WHERE revision=99"):
        try:
            with engine.begin() as connection:
                connection.execute(text(sql))
        except DBAPIError as error:
            assert getattr(error.orig, "sqlstate", None) == "42501", type(error.orig).__name__
            blocked.append(sql.split()[0])
        else:
            raise AssertionError("runtime unexpectedly obtained DDL/schema-write permission")
    engine.dispose()
    return {"runtime_select_insert_update_delete_verified": True, "schema_revision_readable": True,
            "ddl_and_revision_writes_denied": blocked, "model_requests": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "migrate-target", "runtime"))
    args = parser.parse_args()
    if args.action == "seed":
        result = seed("/evidence/source-data")
    elif args.action == "migrate-target":
        engine = connect("ppt_p1_restore_fixture", "ppt_migrator")
        migrate(engine)
        engine.dispose()
        result = {"migrator_schema2_and_triggers_installed": True, "model_requests": 0}
    else:
        result = runtime_permissions()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
