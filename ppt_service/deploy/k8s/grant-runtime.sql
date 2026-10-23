-- Run as database owner/migrator in the new target AFTER restore/migration.
-- psql -X -v ON_ERROR_STOP=1 -v runtime=ppt_runtime -f grant-runtime.sql
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"runtime";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"runtime";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"runtime";
-- The application reads schema readiness; it must not rewrite migration state.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON public.api_schema_revision FROM :"runtime";
