-- Run only against the NEW isolated PostgreSQL instance as its bootstrap owner.
-- psql -X -v ON_ERROR_STOP=1 -v database=ppt_service -v migrator=ppt_migrator
--      -v runtime=ppt_runtime -f bootstrap-database.sql
-- Roles/database must not already exist. No password is embedded or printed.
CREATE ROLE :"migrator" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT;
CREATE ROLE :"runtime" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT;
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
-- Set each new login's SCRAM password via approved provisioning or psql's
-- interactive \password. Passwords must not appear in -v arguments or Git.
-- Runtime must never be granted migration membership, CREATE or DB ownership.
