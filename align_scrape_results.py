#!/usr/bin/env python3
"""
align_scrape_results.py

Reads rows from luma_scrape_results and assigns each one to the nearest
5-minute slot boundary, writing the result into prgriddata.

Alignment logic mirrors prgridstatus front end(lines 152-197):
  - Timestamps are truncated to minute precision to remove scrape-timing jitter.
  - Each row is a candidate for its nearest 5-minute UTC boundary and the
    adjacent boundary (as a fallback if the nearest slot is taken).
  - Tolerance: a row is only considered if it falls within ±3 minutes of
    the slot boundary.
  - Greedy assignment: closest match wins; later timestamp breaks ties.
    Each scrape row and each slot are used at most once.

All timestamps in prgriddata are stored in UTC. luma_scrape_results
timestamps are expected to be timezone-aware (TIMESTAMPTZ) and may be
in any timezone — the script normalises to UTC before doing slot arithmetic.

Usage:
    python align_scrape_results.py   # reads SUPABASE_URL and SUPABASE_KEY from .env

Dependencies:
    pip install supabase          # supabase-py ≥ 2.x
    # zoneinfo is in the stdlib since Python 3.9
    # For Python 3.8: pip install backports.zoneinfo
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import holidays as hdays
from dotenv import load_dotenv
from supabase import create_client, Client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PR_TZ        = ZoneInfo("America/Puerto_Rico")   # UTC-4, no DST
INTERVAL     = timedelta(minutes=5)
MAX_DISTANCE = timedelta(minutes=3)

# On subsequent runs we look back this far before the latest known slot so
# that any scrape results that arrived late (e.g. a delayed cron job) still
# get picked up.
LOOKBACK = timedelta(minutes=30)

# Supabase returns at most this many rows per request.
PAGE_SIZE = 1000

# Upsert batch size — keeps each HTTP request small.
BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Slot-alignment helpers  (mirrors page.js logic)
# ---------------------------------------------------------------------------

def _to_utc(ts: datetime) -> datetime:
    """Return ts as a UTC-aware datetime."""
    if ts.tzinfo is None:
        # Assume UTC if no timezone info is present.
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def nearest_slot_utc(ts: datetime) -> datetime:
    """
    Round a timestamp to the nearest 5-minute UTC boundary.

    Mirrors JS:
        const truncatedMs = Math.floor(ts / 60_000) * 60_000;
        const nearestSlot = Math.round(truncatedMs / INTERVAL) * INTERVAL;
    """
    ts_utc = _to_utc(ts)
    # Truncate to whole minute (remove seconds + microseconds)
    ts_min = ts_utc.replace(second=0, microsecond=0)
    # Round to nearest 5-minute boundary
    epoch    = datetime(1970, 1, 1, tzinfo=timezone.utc)
    total_s  = (ts_min - epoch).total_seconds()
    slot_s   = round(total_s / 300) * 300
    return epoch + timedelta(seconds=slot_s)


def adjacent_slot_utc(ts: datetime, nearest: datetime) -> datetime:
    """
    Return the other 5-minute boundary adjacent to nearest.

    Mirrors JS:
        const otherSlot = truncatedMs < nearestSlot
            ? nearestSlot - INTERVAL
            : nearestSlot + INTERVAL;
    """
    ts_min = _to_utc(ts).replace(second=0, microsecond=0)
    return nearest - INTERVAL if ts_min < nearest else nearest + INTERVAL


def slot_distance(ts: datetime, slot_utc: datetime) -> timedelta:
    """Absolute distance between ts (truncated to minute) and a UTC slot."""
    ts_min = _to_utc(ts).replace(second=0, microsecond=0)
    return abs(ts_min - slot_utc)


def to_pr(slot_utc: datetime) -> datetime:
    """Convert a UTC slot boundary to America/Puerto_Rico local time."""
    return slot_utc.astimezone(PR_TZ)


def compute_date_fields(slot_utc: datetime, pr_holidays) -> dict:
    """
    Derive calendar fields from a UTC slot boundary, expressed in PR local time.

    day_of_week follows the SQL/PostgreSQL convention: 0=Sunday … 6=Saturday.
    is_weekend_holiday is True for Saturday, Sunday, or any PR+US federal holiday.
    """
    slot_pr = slot_utc.astimezone(PR_TZ)
    # Python weekday(): Mon=0 … Sun=6  →  SQL DOW: Sun=0 … Sat=6
    dow_sql = (slot_pr.weekday() + 1) % 7
    is_wknd = dow_sql in (0, 6)                  # Sunday=0, Saturday=6
    is_hday = slot_pr.date() in pr_holidays
    return {
        "hour":               slot_pr.hour,
        "minute":             slot_pr.minute,
        "day_of_week":        dow_sql,
        "month":              slot_pr.month,
        "day_of_month":       slot_pr.day,
        "is_weekend_holiday": is_wknd or is_hday,
    }


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def fetch_latest_slot(supabase: Client) -> datetime | None:
    """Return the most recent slot_timestamp in prgriddata, or None."""
    resp = (
        supabase.table("prgriddata")
        .select("slot_timestamp")
        .order("slot_timestamp", desc=True)
        .limit(1)
        .execute()
    )
    if resp.data:
        return datetime.fromisoformat(resp.data[0]["slot_timestamp"])
    return None


def fetch_scrape_rows(supabase: Client, since: datetime) -> list[dict]:
    """
    Page through luma_scrape_results and return all rows with
    timestamp >= since, ordered ascending.
    """
    rows = []
    offset = 0
    since_iso = since.isoformat()

    while True:
        resp = (
            supabase.table("luma_scrape_results")
            .select("*")
            .gte("timestamp", since_iso)
            .order("timestamp", desc=False)
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        page = resp.data or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return rows


# ---------------------------------------------------------------------------
# Core alignment logic
# ---------------------------------------------------------------------------

def compute_assignments(rows: list[dict]) -> dict[datetime, dict]:
    """
    Given a list of raw scrape rows, compute the greedy slot assignment.

    Returns a dict mapping UTC slot datetime → scrape row dict.
    Mirrors the candidate-building and greedy-assignment logic in page.js.
    """
    candidates = []

    for item in rows:
        ts = datetime.fromisoformat(item["timestamp"])

        nearest  = nearest_slot_utc(ts)
        near_d   = slot_distance(ts, nearest)
        if near_d <= MAX_DISTANCE:
            candidates.append({
                "slot":   nearest,
                "item":   item,
                "dist":   near_d,
                "ts_min": _to_utc(ts).replace(second=0, microsecond=0),
            })

        adj   = adjacent_slot_utc(ts, nearest)
        adj_d = slot_distance(ts, adj)
        if adj_d <= MAX_DISTANCE:
            candidates.append({
                "slot":   adj,
                "item":   item,
                "dist":   adj_d,
                "ts_min": _to_utc(ts).replace(second=0, microsecond=0),
            })

    # Sort: closest first; later reading wins ties  (mirrors JS line 188)
    candidates.sort(key=lambda c: (c["dist"], -c["ts_min"].timestamp()))

    assigned_slots: dict[datetime, dict] = {}
    assigned_ids:   set[str]             = set()

    for c in candidates:
        slot    = c["slot"]
        item_id = c["item"]["id"]
        if slot not in assigned_slots and item_id not in assigned_ids:
            assigned_slots[slot] = c["item"]
            assigned_ids.add(item_id)

    return assigned_slots


def build_insert_rows(
    assignments: dict[datetime, dict],
    aligned_at: datetime,
    pr_holidays,
) -> list[dict]:
    """Convert slot-assignment dict to a list of prgriddata insert dicts."""
    insert_rows = []
    for slot_utc, item in assignments.items():
        insert_rows.append({
            "slot_timestamp":                slot_utc.isoformat(),
            "source_scrape_id":              item.get("id"),
            "current_demand":                item.get("current_demand"),
            "current_demand_max":            item.get("current_demand_max"),
            "next_hour_demand_forecast":     item.get("next_hour_demand_forecast"),
            "next_hour_demand_forecast_max": item.get("next_hour_demand_forecast_max"),
            "current_reserve":               item.get("current_reserve"),
            "current_reserve_max":           item.get("current_reserve_max"),
            "peak_demand_forecast":          item.get("peak_demand_forecast"),
            "peak_reserve_forecast":         item.get("peak_reserve_forecast"),
            "aligned_at":                    aligned_at.isoformat(),
            **compute_date_fields(slot_utc, pr_holidays),
        })
    return insert_rows


def backfill_holiday_flag(supabase: Client, pr_holidays) -> None:
    """
    Update existing prgriddata rows where is_weekend_holiday is NULL.
    Fetches slot_timestamp values in pages and updates in batches.
    """
    print("Backfilling is_weekend_holiday for existing rows...")
    offset = 0
    total_updated = 0

    while True:
        resp = (
            supabase.table("prgriddata")
            .select("slot_timestamp")
            .is_("is_weekend_holiday", "null")
            .order("slot_timestamp", desc=False)
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        page = resp.data or []
        if not page:
            break

        for row in page:
            slot_utc = datetime.fromisoformat(row["slot_timestamp"])
            fields   = compute_date_fields(slot_utc, pr_holidays)
            supabase.table("prgriddata").update(
                fields
            ).eq("slot_timestamp", row["slot_timestamp"]).execute()

        total_updated += len(page)
        print(f"  Updated {total_updated} row(s)...")

        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    print(f"Backfill complete. Updated {total_updated} row(s).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_KEY must be set.", file=sys.stderr)
        sys.exit(1)

    supabase: Client = create_client(url, key)

    # Build holiday calendar covering history + next year.
    years      = range(2024, datetime.now(tz=timezone.utc).year + 2)
    pr_holidays = hdays.country_holidays("PR", years=years)

    # ------------------------------------------------------------------
    # Optional backfill: python align_scrape_results.py --backfill
    # ------------------------------------------------------------------
    if "--backfill" in sys.argv:
        backfill_holiday_flag(supabase, pr_holidays)

    # ------------------------------------------------------------------
    # 1. Determine the query window
    # ------------------------------------------------------------------
    latest_slot = fetch_latest_slot(supabase)

    if latest_slot is not None:
        # Re-examine a short window before the latest slot to catch any
        # scrape rows that arrived late.
        query_from = latest_slot - LOOKBACK
        print(f"Latest slot in prgriddata : {latest_slot.isoformat()}")
        print(f"Fetching scrape rows from : {query_from.isoformat()}")
    else:
        # First run — process the entire luma_scrape_results table.
        query_from = datetime(2000, 1, 1, tzinfo=timezone.utc)
        print("No existing rows in prgriddata — processing full history.")

    # ------------------------------------------------------------------
    # 2. Fetch raw scrape rows
    # ------------------------------------------------------------------
    rows = fetch_scrape_rows(supabase, query_from)
    if not rows:
        print("No scrape results found in window. Nothing to do.")
        return
    print(f"Fetched {len(rows)} scrape row(s).")

    # ------------------------------------------------------------------
    # 3. Greedy slot assignment
    # ------------------------------------------------------------------
    assignments = compute_assignments(rows)
    print(f"Computed {len(assignments)} slot assignment(s).")

    if not assignments:
        print("No slots could be filled. Done.")
        return

    # ------------------------------------------------------------------
    # 4. Build insert rows and upsert (ignore slots that already exist)
    # ------------------------------------------------------------------
    now_utc     = datetime.now(tz=timezone.utc)
    insert_rows = build_insert_rows(assignments, aligned_at=now_utc, pr_holidays=pr_holidays)

    inserted = 0
    for i in range(0, len(insert_rows), BATCH_SIZE):
        batch = insert_rows[i : i + BATCH_SIZE]
        # ignore_duplicates=True → INSERT … ON CONFLICT DO NOTHING
        resp = supabase.table("prgriddata").upsert(
            batch,
            ignore_duplicates=True,
        ).execute()
        inserted += len(resp.data)

    skipped = len(insert_rows) - inserted
    print(f"Inserted {inserted} new row(s) into prgriddata ({skipped} existing slot(s) skipped).")


if __name__ == "__main__":
    main()
