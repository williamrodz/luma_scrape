#!/usr/bin/env python3
"""
Outage alerts for the LUMA scrapers, sent by email through Resend.

Runs from cron every 5 minutes. Each alert emails once when it starts and once
when it clears; state is kept in state.json next to this script.

Alerts:
  - scraper_outage_snapshot / scraper_grid: no new rows for STALE_MINUTES
  - supabase_unreachable: the freshness queries failed
  - grid_outage: island-wide % without service >= GRID_PCT_THRESHOLD, or any load shed

Usage:
  check_alerts.py           # normal run
  check_alerts.py --test    # send a test email and exit
  check_alerts.py --dry-run # evaluate and print, send nothing, don't save state
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "state.json"

STALE_MINUTES = 30
GRID_PCT_THRESHOLD = 5.0


def load_env():
    env = {}
    for line in (HERE / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"')
    return env


ENV = load_env()


def supabase_get(path):
    req = urllib.request.Request(
        f"{ENV['SUPABASE_URL']}/rest/v1/{path}",
        headers={"apikey": ENV["SUPABASE_KEY"], "Authorization": f"Bearer {ENV['SUPABASE_KEY']}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def send_email(subject, body):
    payload = {
        "from": ENV.get("ALERT_FROM", "PR Grid Status Alerts <alerts@prgridstatus.com>"),
        "to": [ENV["ALERT_TO"]],
        "subject": subject,
        "text": body,
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {ENV['RESEND_API_KEY']}",
            "Content-Type": "application/json",
            "User-Agent": "luma-alerts/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"email sent: {subject} -> {json.load(r).get('id')}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Resend error {e.code}: {e.read().decode()}") from None


def parse_ts(s):
    ts = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def age_minutes(ts, now):
    return (now - ts).total_seconds() / 60


def evaluate(now):
    """Return {alert_key: (active, detail)}."""
    alerts = {}
    try:
        snap = supabase_get("outage_snapshot?select=*&order=timestamp.desc&limit=1")[0]
        grid = supabase_get("luma_scrape_results?select=timestamp&order=timestamp.desc&limit=1")[0]
    except Exception as e:
        alerts["supabase_unreachable"] = (True, f"Supabase query failed: {e}")
        return alerts
    alerts["supabase_unreachable"] = (False, "Supabase reachable again.")

    for key, table, ts in [
        ("scraper_outage_snapshot", "outage_snapshot", parse_ts(snap["timestamp"])),
        ("scraper_grid", "luma_scrape_results", parse_ts(grid["timestamp"])),
    ]:
        age = age_minutes(ts, now)
        alerts[key] = (
            age > STALE_MINUTES,
            f"Newest {table} row: {ts:%Y-%m-%d %H:%M} UTC ({age:.0f} min ago).",
        )

    pct = snap.get("totals_pct_without_service") or 0
    shed = snap.get("totals_load_shed") or 0
    alerts["grid_outage"] = (
        pct >= GRID_PCT_THRESHOLD or shed > 0,
        f"LUMA update {snap['last_update']}: {snap['totals_without_service']:,} of "
        f"{snap['totals_total_clients']:,} customers without service ({pct}%), "
        f"load shed: {shed:,}.",
    )
    return alerts


TITLES = {
    "supabase_unreachable": "Supabase unreachable",
    "scraper_outage_snapshot": "Outage scraper stopped (outage_snapshot)",
    "scraper_grid": "Grid scraper stopped (luma_scrape_results)",
    "grid_outage": f"Grid outage: ≥{GRID_PCT_THRESHOLD:g}% without service or load shedding",
}

HINTS = {
    "scraper_outage_snapshot": "Check: gh workflow list --all -R williamrodz/luma_scrape, "
    "~/trigger-luma-scrape.log, and the self-hosted runner service.",
    "scraper_grid": "Check: gh workflow list --all -R williamrodz/luma_scrape, "
    "~/trigger-luma-scrape.log, and the self-hosted runner service.",
    "supabase_unreachable": "Check whether the Supabase project is paused.",
}


def main():
    if "--test" in sys.argv:
        send_email("[luma alerts] Test email", "Resend is configured correctly for luma outage alerts.")
        return
    dry = "--dry-run" in sys.argv

    now = datetime.now(timezone.utc)
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    for key, (active, detail) in evaluate(now).items():
        was_active = state.get(key, {}).get("active", False)
        print(f"{now:%Y-%m-%d %H:%M} {key}: {'ALERT' if active else 'ok'} - {detail}")
        if active == was_active:
            continue
        if active:
            subject = f"🔴 {TITLES[key]}"
            body = f"{detail}\n\n{HINTS.get(key, '')}".strip()
        else:
            since = state[key].get("since", "?")
            subject = f"✅ Resolved: {TITLES[key]}"
            body = f"{detail}\n\nAlert started {since}."
        if dry:
            print(f"  would send: {subject}")
            continue
        send_email(subject, body)
        state[key] = {"active": active, "since": f"{now:%Y-%m-%d %H:%M} UTC"}

    if not dry:
        STATE_FILE.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
