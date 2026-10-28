-- RLS was enabled on outage_snapshot on 2026-10-23 (enable_rls_2026_10_23.sql) with no
-- policies, which made the table read as empty to the prgridstatus.com site (anon key) and
-- left /outage-map stuck on its loading spinner. Grant public read-only access; writes stay
-- service_role only. outage_by_region stays locked (not used by the site).
create policy "public read" on public.outage_snapshot for select to anon using (true);
