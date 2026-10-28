"""
Grid outage event tracking for the `outage_events` table.

An event opens when an outage_snapshot row crosses the grid-outage threshold
(same rule as the `grid_outage` alert in ops/check_alerts.py) and resolves at
the first snapshot that no longer does. At most one event is open at a time.

The functions here are pure (no I/O) so the scraper and the backfill script
share exactly the same logic; `apply_to_supabase` does the writes.
"""
from typing import Any, Dict, Optional, Tuple

GRID_PCT_THRESHOLD = 5.0
TABLE = "outage_events"
REGIONS = ["arecibo", "bayamon", "carolina", "caguas", "mayaguez", "ponce", "san_juan"]


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _pct(out, total) -> float:
    return round(out / total * 100, 2) if total else 0.0


def normalize(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fill the % and totals columns from the per-region counts when they are NULL.
    Rows before the 2026-02-27 schema change only have out_of_service_* and
    total_customers_* (load shed wasn't captured then, so it stays 0).
    """
    row = dict(row)
    for r in REGIONS:
        if row.get(f"pct_without_service_{r}") is None:
            row[f"pct_without_service_{r}"] = _pct(_num(row.get(f"out_of_service_{r}")), _num(row.get(f"total_customers_{r}")))
    if row.get("totals_pct_without_service") is None:
        out = sum(_num(row.get(f"out_of_service_{r}")) for r in REGIONS)
        total = sum(_num(row.get(f"total_customers_{r}")) for r in REGIONS)
        row["totals_pct_without_service"] = _pct(out, total)
        if row.get("totals_without_service") is None:
            row["totals_without_service"] = int(out)
    return row


def row_cause(row: Dict[str, Any]) -> Optional[str]:
    """Return 'outage', 'load_shed', 'outage+load_shed', or None if below threshold."""
    over_pct = _num(row.get("totals_pct_without_service")) >= GRID_PCT_THRESHOLD
    shedding = _num(row.get("totals_load_shed")) > 0
    if over_pct and shedding:
        return "outage+load_shed"
    if over_pct:
        return "outage"
    if shedding:
        return "load_shed"
    return None


def is_grid_outage(row: Dict[str, Any]) -> bool:
    return row_cause(row) is not None


def _merge_cause(a: str, b: Optional[str]) -> str:
    parts = set(a.split("+")) | (set(b.split("+")) if b else set())
    return "+".join(p for p in ("outage", "load_shed") if p in parts)


def _region_pcts(row: Dict[str, Any]) -> Dict[str, float]:
    return {r: _num(row.get(f"pct_without_service_{r}")) for r in REGIONS}


def new_event(row: Dict[str, Any]) -> Dict[str, Any]:
    pct = _num(row.get("totals_pct_without_service"))
    return {
        "started_at": row["timestamp"],
        "last_seen_at": row["timestamp"],
        "cause": row_cause(row),
        "start_pct": pct,
        "current_pct": pct,
        "peak_pct": pct,
        "peak_customers_out": int(_num(row.get("totals_without_service"))),
        "peak_load_shed": int(_num(row.get("totals_load_shed"))),
        "peak_at": row["timestamp"],
        "region_peak_pct": _region_pcts(row),
    }


def extend_event(event: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    """Fields to update on an open event that is still over threshold."""
    pct = _num(row.get("totals_pct_without_service"))
    changes = {
        "last_seen_at": row["timestamp"],
        "current_pct": pct,
        "cause": _merge_cause(event["cause"], row_cause(row)),
        "peak_customers_out": max(int(_num(event.get("peak_customers_out"))), int(_num(row.get("totals_without_service")))),
        "peak_load_shed": max(int(_num(event.get("peak_load_shed"))), int(_num(row.get("totals_load_shed")))),
    }
    if pct > _num(event.get("peak_pct")):
        changes["peak_pct"] = pct
        changes["peak_at"] = row["timestamp"]
    old_regions = event.get("region_peak_pct") or {}
    changes["region_peak_pct"] = {
        r: max(_num(old_regions.get(r)), v) for r, v in _region_pcts(row).items()
    }
    return changes


def next_action(open_event: Optional[Dict[str, Any]], row: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Decide what a new snapshot does to the event log.
    Returns ('insert', fields), ('update', fields), ('resolve', fields) or None.
    """
    row = normalize(row)
    over = is_grid_outage(row)
    if open_event is None:
        return ("insert", new_event(row)) if over else None
    if over:
        return ("update", extend_event(open_event, row))
    return ("resolve", {
        "resolved_at": row["timestamp"],
        "current_pct": _num(row.get("totals_pct_without_service")),
    })


def apply_to_supabase(supabase, row: Dict[str, Any]) -> Optional[str]:
    """Fetch the open event (if any), apply `next_action`, return the action name."""
    resp = supabase.table(TABLE).select("*").is_("resolved_at", "null").limit(1).execute()
    open_event = resp.data[0] if resp.data else None
    action = next_action(open_event, row)
    if action is None:
        return None
    kind, fields = action
    if kind == "insert":
        supabase.table(TABLE).insert(fields).execute()
    else:
        supabase.table(TABLE).update(fields).eq("id", open_event["id"]).execute()
    return kind
