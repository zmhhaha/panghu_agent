-- Internal template for deploy_site.py, run only against a new dedicated DB.
-- The helper supplies escaped passwords through SQL stdin, never psql -v/argv.
-- Roles/database must not already exist. No credentials belong in this file.
SET standard_conforming_strings = on;
CREATE ROLE :"migrator" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT PASSWORD :'migrator_password';
CREATE ROLE :"runtime" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT PASSWORD :'runtime_password';
CREATE DATABASE :"database" OWNER :"migrator";
REVOKE ALL ON DATABASE :"database" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database" TO :"runtime";
\connect :database
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO :"migrator";
GRANT USAGE ON SCHEMA public TO :"runtime";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"runtime";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"runtime";
-- This completion receipt must remain last: a failed/interrupted setup cannot
-- be mistaken for a reusable complete database merely because its roles exist.
COMMENT ON DATABASE :"database" IS 'ppt-service-bootstrap-v1';
