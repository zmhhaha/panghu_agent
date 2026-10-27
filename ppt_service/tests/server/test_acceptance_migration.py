"""Versioned review-table installation on disposable databases; no model calls."""
from __future__ import annotations

import os
from pathlib import Path
import uuid

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ppt_service.server.acceptance.store import AcceptanceEntry, AcceptanceStream
from ppt_service.server.migrate import migrate, REVISION
from ppt_service.server.models import Base, ExecutionSlot, Project


def legacy_schema(engine):
    """A real prior schema with an unrelated owner's durable data."""
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        connection.execute(text('CREATE TABLE api_schema_revision (revision INTEGER PRIMARY KEY)'))
        connection.execute(text('INSERT INTO api_schema_revision(revision) VALUES (1), (2)'))
        connection.execute(ExecutionSlot.__table__.insert().values(id=1, fencing_token=7))
    with Session(engine) as session, session.begin():
        session.add(Project(id='retained-project', issuer='urn:review-migration', subject='retained-owner',
                            title='Retained source project', settings={'fixture': True}, revision=19))


def append_synthetic_entry(engine):
    with Session(engine) as session, session.begin():
        session.add(AcceptanceStream(scope='synthetic-scope', issuer='urn:review-migration', subject='owner',
                                     project_id='synthetic-project', version_id='synthetic-version', revision=1))
        session.flush()
        session.add(AcceptanceEntry(id='synthetic-entry', scope='synthetic-scope', revision=1,
                                   request_key='first', request_sha256='1' * 64, kind='visual_review',
                                   created_at='2026-10-27T00:00:00Z', issuer='urn:review-migration', subject='owner',
                                   binding={'fixture_only': True}, payload={'fixture_only': True}))


def test_fresh_schema_and_repeated_migration_preserve_review_history(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'review-migration.sqlite'))
    try:
        migrate(engine)
        assert {'ppt_acceptance_entries', 'ppt_acceptance_streams'}.issubset(inspect(engine).get_table_names())
        append_synthetic_entry(engine)
        migrate(engine)
        with Session(engine) as session:
            assert session.get(AcceptanceEntry, 'synthetic-entry').payload == {'fixture_only': True}
            assert session.get(AcceptanceStream, 'synthetic-scope').revision == 1
            assert session.scalar(text('SELECT max(revision) FROM api_schema_revision')) == REVISION == 3
            assert session.get(ExecutionSlot, 1).fencing_token == 0
    finally:
        engine.dispose()


def test_schema2_upgrade_preserves_existing_projects_and_execution_slot(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'schema2.sqlite'))
    try:
        legacy_schema(engine)
        migrate(engine)
        with Session(engine) as session:
            project = session.get(Project, 'retained-project')
            assert (project.title, project.revision, project.subject) == ('Retained source project', 19, 'retained-owner')
            assert session.get(ExecutionSlot, 1).fencing_token == 7
            assert session.scalars(select(AcceptanceEntry)).all() == []
            assert session.scalar(text('SELECT max(revision) FROM api_schema_revision')) == 3
    finally:
        engine.dispose()


def test_conflicting_preexisting_review_table_is_preserved_and_rejected(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'conflicting.sqlite'))
    try:
        legacy_schema(engine)
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE ppt_acceptance_entries (unrelated TEXT)'))
            connection.execute(text("INSERT INTO ppt_acceptance_entries VALUES ('preserve-conflict')"))
        with pytest.raises(RuntimeError, match='review tables exist before schema revision 3'):
            migrate(engine)
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT unrelated FROM ppt_acceptance_entries')) == 'preserve-conflict'
            assert connection.scalar(text('SELECT max(revision) FROM api_schema_revision')) == 2
        assert 'ppt_acceptance_streams' not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_runtime_grant_keeps_review_history_append_only():
    grant = (Path(__file__).resolve().parents[2] / 'deploy/k8s/grant-runtime.sql').read_text()
    assert 'REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER\n  ON public.ppt_acceptance_entries' in grant
    assert 'REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER\n  ON public.ppt_acceptance_streams' in grant


@pytest.fixture
def postgres_schema():
    """Only a newly generated schema is ever removed; public data stays intact."""
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL is required for the isolated PostgreSQL migration proof')
    base = create_engine(url)
    schema = 'ppt_acceptance_migration_' + uuid.uuid4().hex
    assert schema.startswith('ppt_acceptance_migration_') and schema.isidentifier()
    with base.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_engine(url, connect_args={'options': '-csearch_path=' + schema})
    try:
        yield engine
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        base.dispose()


def test_postgres_review_entries_reject_update_and_delete_but_stream_revision_advances(postgres_schema):
    engine = postgres_schema
    legacy_schema(engine)
    migrate(engine)
    append_synthetic_entry(engine)
    for statement in (
        "UPDATE ppt_acceptance_entries SET payload='{}' WHERE id='synthetic-entry'",
        "DELETE FROM ppt_acceptance_entries WHERE id='synthetic-entry'",
    ):
        with pytest.raises(DBAPIError, match='immutable record'):
            with engine.begin() as connection:
                connection.execute(text(statement))
    with engine.begin() as connection:
        connection.execute(text("UPDATE ppt_acceptance_streams SET revision=2 WHERE scope='synthetic-scope' AND revision=1"))
    migrate(engine)
    with Session(engine) as session:
        assert session.get(AcceptanceEntry, 'synthetic-entry').payload == {'fixture_only': True}
        assert session.get(AcceptanceStream, 'synthetic-scope').revision == 2
        assert session.get(Project, 'retained-project').revision == 19
