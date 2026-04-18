-- Editable client panel setup for NocoDB.
-- Exposes only 4 real business tables from public schema:
-- users, payments, voting_members, applications.

BEGIN;

DO $block$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'uavdi_client_panel') THEN
        CREATE ROLE uavdi_client_panel LOGIN PASSWORD 'UavdiClient2026!';
    ELSE
        ALTER ROLE uavdi_client_panel WITH LOGIN PASSWORD 'UavdiClient2026!';
    END IF;
END
$block$;

GRANT CONNECT ON DATABASE bot TO uavdi_client_panel;

-- Reset all previous privileges.
REVOKE ALL ON SCHEMA public FROM uavdi_client_panel;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM uavdi_client_panel;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM uavdi_client_panel;

-- Allow only required editable business tables.
GRANT USAGE ON SCHEMA public TO uavdi_client_panel;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
    public.users,
    public.payments,
    public.voting_members,
    public.applications
TO uavdi_client_panel;

-- Sequences needed for inserts (applications/payments).
GRANT USAGE, SELECT, UPDATE ON SEQUENCE
    public.applications_id_seq,
    public.payments_id_seq
TO uavdi_client_panel;

COMMIT;
