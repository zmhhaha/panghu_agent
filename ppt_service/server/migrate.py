"""Versioned transactional schema installation: python -m ppt_service.server.migrate.
API startup never implicitly migrates production databases.
"""
import os
from sqlalchemy import create_engine,text,select,inspect
from .models import Base,ExecutionSlot
from .acceptance.store import AcceptanceBase
from sqlalchemy.orm import Session
REVISION=3

def migrate(engine):
    with engine.begin() as c:
        if engine.dialect.name=='postgresql':c.execute(text('SELECT pg_advisory_xact_lock(73114000)'))
        c.execute(text('CREATE TABLE IF NOT EXISTS api_schema_revision (revision INTEGER PRIMARY KEY)'))
        current=c.scalar(text('SELECT max(revision) FROM api_schema_revision')) or 0
        if current>REVISION:raise RuntimeError('database schema newer than application')
        if current<1:
            Base.metadata.create_all(c)
            c.execute(ExecutionSlot.__table__.insert().values(id=1,fencing_token=0))
            c.execute(text('INSERT INTO api_schema_revision (revision) VALUES (1)'))
        if current<2:
            existing={column['name'] for column in inspect(c).get_columns('assets')}
            timestamp='TIMESTAMP WITH TIME ZONE' if engine.dialect.name=='postgresql' else 'DATETIME'
            additions={'parse_attempt':'INTEGER NOT NULL DEFAULT 0','parse_token':'VARCHAR(36)','parse_scope_uid':'VARCHAR(36)','parse_lease_until':timestamp,'parse_error':'VARCHAR(60)','text_summary':'VARCHAR(2000)'}
            for name,kind in additions.items():
                if name not in existing:c.execute(text(f'ALTER TABLE assets ADD COLUMN {name} {kind}'))
            c.execute(text('CREATE INDEX IF NOT EXISTS ix_assets_parse_queue ON assets (parse_status, parse_lease_until)'))
            c.execute(text('INSERT INTO api_schema_revision (revision) VALUES (2)'))
        if current<3:
            # These tables were previously an opt-in isolated module. Never
            # adopt an unknown partial installation or synthetic review rows.
            existing=set(inspect(c).get_table_names())
            if existing.intersection(AcceptanceBase.metadata.tables):
                raise RuntimeError('review tables exist before schema revision 3; preserve and inspect the conflicting installation')
            AcceptanceBase.metadata.create_all(c)
            c.execute(text('INSERT INTO api_schema_revision (revision) VALUES (3)'))
        if engine.dialect.name=='postgresql':
            # Baselines and immutable input snapshots cannot silently change after publication.
            c.execute(text("""CREATE OR REPLACE FUNCTION ppt_immutable_record() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable record'; END $$"""))
            for table in ('checkpoints','versions'):
                c.execute(text(f'DROP TRIGGER IF EXISTS ppt_immutable ON {table}'))
                c.execute(text(f'CREATE TRIGGER ppt_immutable BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION ppt_immutable_record()'))
            # Corrections and revocations append new entries. Deleting a project
            # currently revokes access without physically purging its history.
            c.execute(text('DROP TRIGGER IF EXISTS ppt_acceptance_append_only ON ppt_acceptance_entries'))
            c.execute(text('CREATE TRIGGER ppt_acceptance_append_only BEFORE UPDATE OR DELETE ON ppt_acceptance_entries FOR EACH ROW EXECUTE FUNCTION ppt_immutable_record()'))
            c.execute(text("""CREATE OR REPLACE FUNCTION ppt_immutable_job_input() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.input_snapshot::jsonb IS DISTINCT FROM OLD.input_snapshot::jsonb THEN RAISE EXCEPTION 'immutable job input'; END IF; RETURN NEW; END $$"""))
            c.execute(text('DROP TRIGGER IF EXISTS ppt_immutable_input ON jobs'))
            c.execute(text('CREATE TRIGGER ppt_immutable_input BEFORE UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION ppt_immutable_job_input()'))
            c.execute(text("""CREATE OR REPLACE FUNCTION ppt_immutable_plan() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF (to_jsonb(NEW)-'confirmed_at'-'confirmation_selection') IS DISTINCT FROM (to_jsonb(OLD)-'confirmed_at'-'confirmation_selection') OR (OLD.confirmed_at IS NOT NULL AND NEW.confirmation_selection::jsonb IS DISTINCT FROM OLD.confirmation_selection::jsonb) THEN RAISE EXCEPTION 'immutable plan'; END IF; RETURN NEW; END $$"""))
            c.execute(text('DROP TRIGGER IF EXISTS ppt_immutable_plan ON plans'))
            c.execute(text('CREATE TRIGGER ppt_immutable_plan BEFORE UPDATE ON plans FOR EACH ROW EXECUTE FUNCTION ppt_immutable_plan()'))
if __name__=='__main__':
    url=os.environ['DATABASE_URL']
    if not url.startswith('postgresql'):raise RuntimeError('PostgreSQL required')
    migrate(create_engine(url))
