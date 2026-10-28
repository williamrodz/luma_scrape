"""
Unit tests for outage_events.py and the scraper hook. No network.
Run with: uv run --no-project pytest test_outage_events.py
"""
from unittest.mock import MagicMock

import outage_events as oe
import scrape_outage_status


def snap(ts, pct=0.0, out=0, shed=0, region_pct=None):
    row = {
        "timestamp": ts,
        "totals_pct_without_service": pct,
        "totals_without_service": out,
        "totals_load_shed": shed,
    }
    for r, v in (region_pct or {}).items():
        row[f"pct_without_service_{r}"] = v
    return row


def test_below_threshold_no_event():
    assert oe.next_action(None, snap("t0", pct=4.99)) is None


def test_opens_event_at_threshold():
    kind, fields = oe.next_action(None, snap("t0", pct=5.0, out=75000, region_pct={"ponce": 12.5}))
    assert kind == "insert"
    assert fields["started_at"] == "t0"
    assert fields["cause"] == "outage"
    assert fields["peak_pct"] == 5.0
    assert fields["peak_customers_out"] == 75000
    assert fields["region_peak_pct"]["ponce"] == 12.5
    assert fields["region_peak_pct"]["san_juan"] == 0.0


def test_load_shed_only_opens_event():
    kind, fields = oe.next_action(None, snap("t0", pct=1.0, shed=300))
    assert kind == "insert"
    assert fields["cause"] == "load_shed"


def test_update_tracks_peaks_and_widens_cause():
    _, event = oe.next_action(None, snap("t0", pct=6.0, out=90000, region_pct={"ponce": 20.0, "arecibo": 3.0}))
    kind, changes = oe.next_action(event, snap("t1", pct=8.0, out=120000, shed=50, region_pct={"ponce": 10.0, "arecibo": 9.0}))
    assert kind == "update"
    assert changes["peak_pct"] == 8.0 and changes["peak_at"] == "t1"
    assert changes["peak_customers_out"] == 120000
    assert changes["peak_load_shed"] == 50
    assert changes["cause"] == "outage+load_shed"
    assert changes["region_peak_pct"]["ponce"] == 20.0
    assert changes["region_peak_pct"]["arecibo"] == 9.0
    assert changes["last_seen_at"] == "t1"


def test_update_keeps_peak_when_lower():
    _, event = oe.next_action(None, snap("t0", pct=9.0))
    _, changes = oe.next_action(event, snap("t1", pct=6.0))
    assert "peak_pct" not in changes and "peak_at" not in changes
    assert changes["current_pct"] == 6.0


def test_resolves_below_threshold():
    _, event = oe.next_action(None, snap("t0", pct=9.0))
    kind, fields = oe.next_action(event, snap("t2", pct=2.0))
    assert kind == "resolve"
    assert fields == {"resolved_at": "t2", "current_pct": 2.0}


def test_null_values_treated_as_zero():
    assert oe.next_action(None, {"timestamp": "t0", "totals_pct_without_service": None, "totals_load_shed": None}) is None


def test_apply_to_supabase_inserts_when_none_open():
    sb = MagicMock()
    sb.table.return_value.select.return_value.is_.return_value.limit.return_value.execute.return_value.data = []
    assert oe.apply_to_supabase(sb, snap("t0", pct=7.0)) == "insert"
    sb.table.return_value.insert.assert_called_once()


def test_apply_to_supabase_resolves_open_event():
    sb = MagicMock()
    open_event = {"id": 42, **oe.new_event(snap("t0", pct=7.0))}
    sb.table.return_value.select.return_value.is_.return_value.limit.return_value.execute.return_value.data = [open_event]
    assert oe.apply_to_supabase(sb, snap("t1", pct=1.0)) == "resolve"
    sb.table.return_value.update.return_value.eq.assert_called_once_with("id", 42)


def test_scraper_hook_swallows_errors(monkeypatch):
    monkeypatch.setattr(scrape_outage_status, "create_client", MagicMock(side_effect=RuntimeError("boom")))
    scrape_outage_status.update_outage_events(snap("t0", pct=7.0))  # must not raise


def test_backfill_splits_events_and_closes_at_gaps():
    import backfill_outage_events as bf
    rows = [
        snap("2026-01-01T00:00:00+00:00", pct=1.0),
        snap("2026-01-01T00:05:00+00:00", pct=6.0),   # event 1 opens
        snap("2026-01-01T00:10:00+00:00", pct=8.0),
        snap("2026-01-01T00:15:00+00:00", pct=2.0),   # event 1 resolves
        snap("2026-01-01T01:00:00+00:00", pct=7.0),   # event 2 opens
        snap("2026-01-05T00:00:00+00:00", pct=7.0),   # >2h gap: event 2 closed, event 3 opens
    ]
    events = bf.build_events(rows)
    assert len(events) == 3
    assert events[0]["resolved_at"] == "2026-01-01T00:15:00+00:00" and events[0]["peak_pct"] == 8.0
    assert events[1]["resolved_at"] == "2026-01-01T01:00:00+00:00"  # last snapshot before the gap
    assert "resolved_at" not in events[2]  # still ongoing


def test_pre_2026_02_27_rows_derive_pct_from_counts():
    # Old rows: only per-region counts, all % / totals columns NULL
    row = {"timestamp": "t0", "totals_pct_without_service": None, "totals_load_shed": None}
    for r in oe.REGIONS:
        row[f"out_of_service_{r}"] = 0
        row[f"total_customers_{r}"] = 100000
        row[f"pct_without_service_{r}"] = None
    row["out_of_service_ponce"] = 70000  # 70k of 700k = 10%
    kind, fields = oe.next_action(None, row)
    assert kind == "insert"
    assert fields["peak_pct"] == 10.0
    assert fields["peak_customers_out"] == 70000
    assert fields["region_peak_pct"]["ponce"] == 70.0
    assert fields["cause"] == "outage"


def test_recorded_pct_wins_over_derived():
    row = snap("t0", pct=4.0)
    row["out_of_service_ponce"], row["total_customers_ponce"] = 90000, 100000
    assert oe.next_action(None, row) is None  # uses LUMA's 4.0%, not derived
