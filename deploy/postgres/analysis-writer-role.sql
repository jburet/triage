-- The role the analysis Job answers with, and nothing more (ADR-0009).
--
-- The Job runs code from a repository under investigation and holds a database
-- URL; the only thing that keeps that from being a way into Triage's data is
-- this grant. One table, in one schema, and no path to the rest.
--
-- ADR-0009 calls this role "insert-only". The code is not: `SqlRepository.
-- save_analysis_result` looks the row up by `job_name` before writing it, so the
-- role needs SELECT and UPDATE on that one table as well. It is still bounded to
-- a single table whose only content is the Job's own answers — but the ADR's
-- wording is wider than the truth, and the day the write becomes a plain INSERT
-- ... ON CONFLICT DO NOTHING, the two grants below should go.
--
-- Run as the owner of the `triage` schema, once, before the first analysis:
--   psql "$TRIAGE_DATABASE_URL" -f deploy/postgres/analysis-writer-role.sql

\set ON_ERROR_STOP on

CREATE ROLE triage_analysis LOGIN PASSWORD :'analysis_password';

-- Nothing by default: no connect on the database beyond what PUBLIC gives, no
-- create anywhere, no visibility of any other schema.
REVOKE ALL ON SCHEMA triage FROM triage_analysis;
REVOKE ALL ON ALL TABLES IN SCHEMA triage FROM triage_analysis;
REVOKE ALL ON SCHEMA public FROM triage_analysis;

GRANT USAGE ON SCHEMA triage TO triage_analysis;
GRANT SELECT, INSERT, UPDATE ON triage.analysis_results TO triage_analysis;

-- A table added later must not become readable by inheritance.
ALTER DEFAULT PRIVILEGES IN SCHEMA triage REVOKE ALL ON TABLES FROM triage_analysis;

-- What this should print: exactly one row, analysis_results, {SELECT,INSERT,UPDATE}.
--   SELECT table_name, array_agg(privilege_type ORDER BY privilege_type)
--     FROM information_schema.table_privileges
--    WHERE grantee = 'triage_analysis'
--    GROUP BY table_name;
