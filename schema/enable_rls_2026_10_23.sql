-- Enable RLS on tables flagged by the Supabase Security Advisor ("RLS Disabled in Public").
-- No policies are added: scrapers, backup and greenlight use the service_role key, which
-- bypasses RLS, while anon/public access is denied.
alter table public.outage_snapshot  enable row level security;
alter table public.outage_by_region enable row level security;
