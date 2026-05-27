-- Optional: a dedicated read-only role for the dashboard API (defense in depth).
-- The API also enforces read-only at the session level, so this is not strictly
-- required, but it limits the blast radius of the always-on, externally reachable
-- endpoint to SELECT-only.
--
-- Requires DB admin privileges (e.g. run as the `postgres` superuser):
--   sudo -u postgres psql -d <dbname> -f sql/readonly_role.sql
-- Then point the API at it:  DASH_DATABASE_URL=postgresql://dash_ro:<pw>@host:5432/<dbname>
--
-- Replace CHANGE_ME and <dbname> before running.

CREATE ROLE dash_ro LOGIN PASSWORD 'CHANGE_ME';

GRANT CONNECT ON DATABASE "<dbname>" TO dash_ro;
GRANT USAGE  ON SCHEMA public TO dash_ro;
GRANT SELECT  ON ALL TABLES IN SCHEMA public TO dash_ro;

-- Cover tables created later (e.g. future dash_* / plateau_* tables).
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO dash_ro;
