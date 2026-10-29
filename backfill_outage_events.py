"""
One-time backfill of `outage_events` from historical `outage_snapshot` rows.

Replays every snapshot in order through the same logic the scraper uses
(outage_events.next_action), including the 30-min resolve grace and closing an
event at the last snapshot before a data gap longer than 2 h (e.g. the Aug–Oct
2026 runner outage).

Usage (needs the service_role key in .env):
  uv run --no-project python backfill_outage_events.py --dry-run
  uv run --no-project python backfill_outage_events.py [--force]
"""
import os
import sys

from supabase import create_client

import outage_events as oe

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BATCH_SIZE = 1000

COLUMNS = ",".join(
    ["timestamp", "totals_pct_without_service", "totals_without_service", "totals_load_shed"]
    + [f"{col}_{r}" for r in oe.REGIONS for col in ("pct_without_service", "out_of_service", "total_customers")]
)


def fetch_snapshots(supabase):
    rows, offset = [], 0
    while True:
        batch = (
            supabase.table("outage_snapshot")
            .select(COLUMNS)
            .order("timestamp")
            .range(offset, offset + BATCH_SIZE - 1)
            .execute()
            .data
        )
        rows.extend(batch)
        print(f"  fetched {len(rows)} snapshots...")
        if len(batch) < BATCH_SIZE:
            return rows
        offset += BATCH_SIZE


def build_events(rows):
    """Return a list of event dicts (the last one may be open)."""
    events, current = [], None
    for row in rows:
        for kind, fields in oe.next_action(current, row):
            if kind == "insert":
                current = fields
            else:
                current = {**current, **fields}
                if kind == "resolve":
                    events.append(current)
                    current = None
    if current:
        events.append(current)
    return events


def main():
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    existing = supabase.table(oe.TABLE).select("id", count="exact").limit(1).execute().count or 0
    if existing and not force and not dry:
        print(f"outage_events already has {existing} rows; pass --force to delete and rebuild.")
        sys.exit(1)

    events = build_events(fetch_snapshots(supabase))
    print(f"\n{len(events)} events:")
    for e in events:
        end = e["resolved_at"] or "ONGOING"
        print(f"  {e['started_at'][:16]} → {str(end)[:16]}  peak {e['peak_pct']}%  "
              f"out {e['peak_customers_out']:,}  shed {e['peak_load_shed']:,}  ({e['cause']})")

    if dry:
        print("\n--dry-run: nothing written.")
        return
    if existing:
        supabase.table(oe.TABLE).delete().gte("id", 0).execute()
        print(f"deleted {existing} existing rows")
    for i in range(0, len(events), 500):
        supabase.table(oe.TABLE).insert(events[i:i + 500]).execute()
    print(f"inserted {len(events)} events")


if __name__ == "__main__":
    main()
