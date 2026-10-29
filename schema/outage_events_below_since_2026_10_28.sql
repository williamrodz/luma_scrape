-- Resolve-side hysteresis: an open event resolves only after staying under threshold for
-- 30 min. below_since is the first under-threshold snapshot while open (null while over
-- threshold); resolved_at is backdated to it. The site shows open events with below_since
-- set as "Recovering".
alter table public.outage_events add column below_since timestamptz;
