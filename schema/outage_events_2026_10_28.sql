-- Grid outage events, maintained by scrape_outage_status.py via outage_events.py.
-- An event opens when island-wide % without service >= 5% or any load shed is reported,
-- and resolves at the first snapshot where neither holds. Backfill: backfill_outage_events.py.
create table public.outage_events (
  id                 bigint generated always as identity primary key,
  started_at         timestamptz not null,          -- first snapshot over threshold
  resolved_at        timestamptz,                   -- null = ongoing
  last_seen_at       timestamptz not null,          -- latest snapshot evaluated while open
  cause              text not null,                 -- 'outage' | 'load_shed' | 'outage+load_shed'
  start_pct          numeric(5,2),
  current_pct        numeric(5,2),
  peak_pct           numeric(5,2),
  peak_customers_out integer,
  peak_load_shed     integer,
  peak_at            timestamptz,
  region_peak_pct    jsonb                          -- {"san_juan": 12.3, ...}
);

-- At most one open event at a time.
create unique index outage_events_one_open on public.outage_events ((true)) where resolved_at is null;
create index outage_events_started_at_idx on public.outage_events (started_at desc);

alter table public.outage_events enable row level security;
create policy "public read" on public.outage_events for select to anon using (true);
