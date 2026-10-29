"""
Unit tests for outage_events.py, the scraper hook and the backfill. No network.
Run with: uv run --no-project pytest test_outage_events.py
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import outage_events as oe
import scrape_outage_status

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(minutes):
    return (T0 + timedelta(minutes=minutes)).isoformat()


def snap(minutes, pct=0.0, out=0, shed=0, region_pct=None):
    row = {
        "timestamp": at(minutes),
        "totals_pct_without_service": pct,
        "totals_without_service": out,
        "totals_load_shed": shed,
    }
    for r in oe.REGIONS:
        row[f"pct_without_service_{r}"] = (region_pct or {}).get(r, 0.0)
    return row


def run(rows, event=None):
    """Feed rows through next_action like the backfill does; return (event, action log)."""
    log = []
    for row in rows:
        for kind, fields in oe.next_action(event, row):
            log.append(kind)
            event = fields if kind == "insert" else {**event, **fields}
            if kind == "resolve":
                log.append(event)
                event = None
    return event, log


def test_below_threshold_no_event():
    assert oe.next_action(None, snap(0, pct=4.99)) == []


def test_single_reading_opens_immediately():
    [(kind, fields)] = oe.next_action(None, snap(0, pct=5.0, out=75000, region_pct={"ponce": 12.5}))
    assert kind == "insert"
    assert fields["started_at"] == at(0)
    assert fields["cause"] == "outage"
    assert fields["peak_pct"] == 5.0
    assert fields["peak_customers_out"] == 75000
    assert fields["region_peak_pct"]["ponce"] == 12.5
    assert fields["resolved_at"] is None and fields["below_since"] is None


def test_load_shed_only_opens_event():
    [(kind, fields)] = oe.next_action(None, snap(0, pct=1.0, shed=300))
    assert kind == "insert" and fields["cause"] == "load_shed"


def test_update_tracks_peaks_and_widens_cause():
    event, _ = run([snap(0, pct=6.0, out=90000, region_pct={"ponce": 20.0, "arecibo": 3.0})])
    [(kind, changes)] = oe.next_action(event, snap(5, pct=8.0, out=120000, shed=50, region_pct={"ponce": 10.0, "arecibo": 9.0}))
    assert kind == "update"
    assert changes["peak_pct"] == 8.0 and changes["peak_at"] == at(5)
    assert changes["peak_customers_out"] == 120000
    assert changes["peak_load_shed"] == 50
    assert changes["cause"] == "outage+load_shed"
    assert changes["region_peak_pct"] == {**{r: 0.0 for r in oe.REGIONS}, "ponce": 20.0, "arecibo": 9.0}


def test_update_keeps_peak_when_lower():
    event, _ = run([snap(0, pct=9.0)])
    [(_, changes)] = oe.next_action(event, snap(5, pct=6.0))
    assert "peak_pct" not in changes and changes["current_pct"] == 6.0


def test_dip_under_grace_stays_same_event():
    event, log = run([snap(0, pct=9.0), snap(5, pct=3.0), snap(25, pct=4.0)])
    assert log == ["insert", "update", "update"]
    assert event["below_since"] == at(5) and event["current_pct"] == 4.0
    event, log = run([snap(30, pct=7.0)], event)
    assert log == ["update"]
    assert event["below_since"] is None and event["started_at"] == at(0)


def test_resolves_after_grace_backdated_to_below_since():
    event, log = run([snap(0, pct=9.0), snap(10, pct=2.0), snap(20, pct=1.0), snap(40, pct=1.0)])
    assert event is None
    resolved = log[-1]
    assert resolved["resolved_at"] == at(10)
    assert resolved["peak_pct"] == 9.0


def test_live_gap_closes_event_and_reopens():
    event, _ = run([snap(0, pct=9.0), snap(5, pct=8.0)])
    actions = oe.next_action(event, snap(5 + 180, pct=7.0))
    assert [k for k, _ in actions] == ["resolve", "insert"]
    assert actions[0][1]["resolved_at"] == at(5)
    assert actions[1][1]["started_at"] == at(185)


def test_gap_while_recovering_closes_at_below_since():
    event, _ = run([snap(0, pct=9.0), snap(5, pct=2.0)])
    assert oe.next_action(event, snap(300, pct=1.0)) == [("resolve", {"resolved_at": at(5)})]


def test_null_values_treated_as_zero():
    assert oe.next_action(None, {"timestamp": at(0), "totals_pct_without_service": None, "totals_load_shed": None}) == []


def test_pre_2026_02_27_rows_derive_pct_from_counts():
    row = {"timestamp": at(0), "totals_pct_without_service": None, "totals_load_shed": None}
    for r in oe.REGIONS:
        row[f"out_of_service_{r}"] = 0
        row[f"total_customers_{r}"] = 100000
        row[f"pct_without_service_{r}"] = None
    row["out_of_service_ponce"] = 70000  # 70k of 700k = 10%
    [(kind, fields)] = oe.next_action(None, row)
    assert kind == "insert"
    assert fields["peak_pct"] == 10.0
    assert fields["peak_customers_out"] == 70000
    assert fields["region_peak_pct"]["ponce"] == 70.0


def test_recorded_pct_wins_over_derived():
    row = snap(0, pct=4.0)
    row["out_of_service_ponce"], row["total_customers_ponce"] = 90000, 100000
    assert oe.next_action(None, row) == []


def _mock_supabase(open_rows):
    sb = MagicMock()
    sb.table.return_value.select.return_value.is_.return_value.limit.return_value.execute.return_value.data = open_rows
    return sb


def test_apply_to_supabase_inserts_when_none_open():
    sb = _mock_supabase([])
    assert oe.apply_to_supabase(sb, snap(0, pct=7.0)) == ["insert"]
    sb.table.return_value.insert.assert_called_once()


def test_apply_to_supabase_gap_resolves_then_inserts():
    event, _ = run([snap(0, pct=7.0)])
    sb = _mock_supabase([{"id": 42, **event}])
    assert oe.apply_to_supabase(sb, snap(500, pct=8.0)) == ["resolve", "insert"]
    sb.table.return_value.update.return_value.eq.assert_called_once_with("id", 42)
    sb.table.return_value.insert.assert_called_once()


def test_scraper_hook_swallows_errors(monkeypatch):
    monkeypatch.setattr(scrape_outage_status, "create_client", MagicMock(side_effect=RuntimeError("boom")))
    scrape_outage_status.update_outage_events(snap(0, pct=7.0))  # must not raise


def test_backfill_merges_dips_and_splits_at_gaps():
    import backfill_outage_events as bf
    rows = [
        snap(0, pct=1.0),
        snap(5, pct=6.0),         # event 1 opens
        snap(10, pct=3.0),        # dip...
        snap(15, pct=8.0),        # ...back over within grace: same event
        snap(20, pct=2.0),        # below_since = 20
        snap(55, pct=2.0),        # 35 min below: event 1 resolves at 20
        snap(60, pct=7.0),        # event 2 opens
        snap(60 + 300, pct=7.0),  # >2h gap: event 2 closed at 60, event 3 opens
    ]
    events = bf.build_events(rows)
    assert len(events) == 3
    assert events[0]["started_at"] == at(5) and events[0]["resolved_at"] == at(20) and events[0]["peak_pct"] == 8.0
    assert events[1]["resolved_at"] == at(60)
    assert events[2]["resolved_at"] is None
    assert len({frozenset(e) for e in events}) == 1  # uniform keys for bulk insert
