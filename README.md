# ⚡ LUMA Energy Scraper

A pair of scheduled scrapers that collect electricity grid and outage data from LUMA PR and store it in Supabase for analysis.

---

## Scripts

### `scrape_luma_grid_status.py`
Scrapes the [LUMA System Overview](https://lumapr.com/system-overview/?lang=en) page using Playwright (headless Chromium). Captures island-wide generation, demand, and reserve metrics and stores them in the `luma_scrape_results` table.

> **Note:** This scraper was originally `requests` + BeautifulSoup-based. Between **2026-04-20 20:50 UTC** and **2026-04-27 16:15 UTC**, bot detection on the LUMA server caused the scraper to receive stripped HTML, resulting in all numeric fields being written as `NULL`. It was rewritten on 2026-04-27 to use a headless Chromium browser via Playwright, which bypasses the bot detection and retrieves fully-rendered HTML.

### `scrape_outage_status.py`
Fetches per-region outage data directly from the MiLUMA JSON API (`GET https://api.miluma.lumapr.com/miluma-outage-api/outage/regionsWithoutService`). Stores results in the `outage_snapshot` table. No browser required — the API is publicly accessible without authentication.

> **Note:** This scraper was originally Playwright-based (scraping the DOM of `miluma.lumapr.com`). Around 2026-02-17 the site transitioned to a React SPA backed by a dedicated REST API, which caused the DOM scraper to time out. It was rewritten on 2026-02-27 to call the API directly.

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/your-username/luma-scraper.git
cd luma-scraper
```

### 2. Install uv (if not already installed)

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or via Homebrew
brew install uv
```

### 3. Sync dependencies

```bash
uv sync --no-install-project
```

### 4. Configure credentials (local runs only)

Create a `.env` file in the repo root (already gitignored):

```
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_KEY=your-anon-or-service-role-key
```

Credentials are found in your Supabase project under **Project Settings → API**. On GitHub Actions they are supplied via repository secrets.

### 5. Run

```bash
uv run --no-project python scrape_luma_grid_status.py
uv run --no-project python scrape_outage_status.py
```

---

## outage_snapshot Schema

The `outage_snapshot` table stores one row per scrape. Columns follow a consistent naming pattern across the 7 LUMA regions: `arecibo`, `bayamon`, `carolina`, `caguas`, `mayaguez`, `ponce`, `san_juan`.

### Metadata columns

| Column | Type | Description |
|---|---|---|
| `timestamp` | `TIMESTAMPTZ` | When the row was scraped (UTC) |
| `last_update` | `TEXT` | Timestamp reported by LUMA, e.g. `"02/27/2026 11:40 AM"` |

### Per-region columns (× 7 regions)

| Column pattern | Type | API field |
|---|---|---|
| `total_customers_{region}` | `INTEGER` | `region.totalClients` |
| `out_of_service_{region}` | `INTEGER` | `region.totalClientsWithoutService` |
| `with_service_{region}` | `INTEGER` | `region.totalClientsWithService` |
| `planned_upgrades_{region}` | `INTEGER` | `region.totalClientsAffectedByPlannedOutage` |
| `load_shed_{region}` | `INTEGER` | `region.totalClientsAffectedByLoadShed` |
| `pct_without_service_{region}` | `NUMERIC(5,2)` | `region.percentageClientsWithoutService` |
| `pct_with_service_{region}` | `NUMERIC(5,2)` | `region.percentageClientsWithService` |

### Island-wide totals columns

| Column | Type | API field |
|---|---|---|
| `totals_total_clients` | `INTEGER` | `totals.totalClients` |
| `totals_without_service` | `INTEGER` | `totals.totalClientsWithoutService` |
| `totals_with_service` | `INTEGER` | `totals.totalClientsWithService` |
| `totals_planned` | `INTEGER` | `totals.totalClientsAffectedByPlannedOutage` |
| `totals_load_shed` | `INTEGER` | `totals.totalClientsAffectedByLoadShed` |
| `totals_pct_without_service` | `NUMERIC(5,2)` | `totals.totalPercentageWithoutService` |
| `totals_pct_with_service` | `NUMERIC(5,2)` | `totals.totalPercentageWithService` |

> Derived fields (e.g. unplanned outages = `out_of_service - planned_upgrades - load_shed`) are intentionally omitted and left to downstream consumers.

For the full schema change history see [`schema/OUTAGE_SNAPSHOT_CHANGES_2026_02_27.md`](schema/OUTAGE_SNAPSHOT_CHANGES_2026_02_27.md). Migration SQL is in [`schema/outage_snapshot_migration.sql`](schema/outage_snapshot_migration.sql).

---

## outage_events Schema

`outage_events` holds one row per **grid outage event**, shown on prgridstatus.com/outage-map. An event opens at the first `outage_snapshot` row with island-wide `totals_pct_without_service >= 5` or any `totals_load_shed > 0`.
- **Resolving:** it resolves only after staying under threshold for **30 minutes**, so a brief dip doesn't split one outage into two events. `resolved_at` is backdated to the first snapshot under threshold, which is when service actually recovered.
- **Data gaps:** if snapshots stop for more than 2 h while an event is open, the event is closed at the last point seen.
- **Emails:** the `grid_outage` email alert (`ops/check_alerts.py`) follows this table, so its emails match the website. `scrape_outage_status.py` maintains it after each successful insert, using [`outage_events.py`](outage_events.py), and a failure there never fails the scrape. At most one event is open at a time.

| Column | Type | Description |
|---|---|---|
| `started_at` | `TIMESTAMPTZ` | First snapshot over threshold |
| `resolved_at` | `TIMESTAMPTZ` | First snapshot back under threshold; `NULL` = ongoing |
| `last_seen_at` | `TIMESTAMPTZ` | Latest snapshot evaluated while open |
| `below_since` | `TIMESTAMPTZ` | First under-threshold snapshot while open ("Recovering"); `NULL` while over threshold |
| `cause` | `TEXT` | `outage`, `load_shed`, or `outage+load_shed` (union over the event) |
| `start_pct` / `current_pct` / `peak_pct` | `NUMERIC(5,2)` | Island-wide % without service |
| `peak_customers_out` / `peak_load_shed` | `INTEGER` | Max customers out / load shed during the event |
| `peak_at` | `TIMESTAMPTZ` | When `peak_pct` was reached |
| `region_peak_pct` | `JSONB` | Max % without service per region, e.g. `{"ponce": 21.4, ...}` |

DDL: [`schema/outage_events_2026_10_28.sql`](schema/outage_events_2026_10_28.sql) plus [`schema/outage_events_below_since_2026_10_28.sql`](schema/outage_events_below_since_2026_10_28.sql). Rebuild from history with `backfill_outage_events.py` (`--dry-run` first; `--force` replaces existing rows). The backfill uses the same logic as the scraper.

Both `outage_snapshot` and `outage_events` have an anon `SELECT` policy so the website can read them ([`schema/outage_snapshot_public_read_2026_10_28.sql`](schema/outage_snapshot_public_read_2026_10_28.sql)). Writes remain service_role only.

---

## MiLUMA API

The outage data is served by a public REST API discovered by inspecting the MiLUMA frontend JS bundle. The full API surface has 11 backend services; only two are publicly accessible without authentication:

| Endpoint | Method | Description |
|---|---|---|
| `/miluma-outage-api/outage/regionsWithoutService` | `GET` | Per-region outage counts and percentages + island-wide totals. Currently scraped. |
| `/miluma-outage-api/outage/municipality/towns` | `POST` | Per-town breakdown within a municipality. Not yet scraped. |
| `/miluma-app-config-api/v2/configs` | `GET` | App feature flags, including `MiLumaOutage.LoadShed.Enable` and `maintenanceMode`. |
| `/miluma-app-config-api/getGeneralPurposeBanners` | `GET` | Active site banners; likely populated during storms or major events. |

All endpoints are under `https://api.miluma.lumapr.com/`.

---

## GitHub Actions

Both workflows run every 5 minutes on a self-hosted runner (`ai-workstation`). They use only shell `run:` steps — no `uses:` action dependencies — so they have zero reliance on GitHub's action CDN (`codeload.github.com`). `uv` and Python 3.12 must be pre-installed on the runner machine.

> **Incident 2026-05-26 08:25–08:45 ET:** Both workflows failed during "Setup job" because the runner could not download `actions/setup-python@v5` from `codeload.github.com`. The scraper never ran, causing a ~20-minute data gap. The S3 buffer did not activate because it only catches Supabase failures during a successful scrape run — it cannot compensate for a workflow that never starts. **Root cause:** transient network connectivity issue between the runner and GitHub's CDN. **Fix:** removed all `uses:` action steps (`actions/checkout@v4`, `actions/setup-python@v5`, `astral-sh/setup-uv@v5`) from both workflows and replaced `checkout` with a `git fetch + reset --hard` shell step, eliminating the dependency on `codeload.github.com` entirely.

> **Incident 2026-08-06 → 2026-10-23 (two gaps, ~75 days of missing data):** The self-hosted runner went offline around 2026-08-06, so no scrapes ran until 2026-09-27 (51 days). The biweekly backup jobs also never got a runner, so no bot commits landed after 2026-08-01, and GitHub disabled every scheduled workflow on 2026-09-30 under its 60-day inactivity rule for public repos (23 more days). The local cron dispatcher kept failing with `422 Cannot trigger a 'workflow_dispatch' on a disabled workflow`, and nothing alerted. **Fix:** re-enabled the workflows; the cron dispatcher now re-enables them daily through the API (no dummy commits); added local and GitHub-hosted email alerts (see [Monitoring](#monitoring)); enabled RLS on `outage_snapshot` and `outage_by_region` (`schema/enable_rls_2026_10_23.sql`). Data for the gaps cannot be backfilled — the MiLUMA API only returns the current state.

---

## Monitoring

Scrapes are triggered and monitored from two places, so an outage is caught whether the workstation is up or down.

**On the workstation (crontab).** Reference copies live in [`ops/`](ops/); the live files are in the home directory.

| Cron | Script | Purpose |
|---|---|---|
| `*/5 * * * *` | `~/trigger-luma-scrape.sh` | Dispatches both scrape workflows. Once a day it also calls the GitHub "enable workflow" API for all workflows, which resets the 60-day inactivity timer. If a dispatch is refused because a workflow is disabled, it re-enables it and retries. Needs a PAT with `actions:write` in `~/.github_pat`. |
| `2-59/5 * * * *` | `~/luma-alerts/check_alerts.py` | Emails an alert via [Resend](https://resend.com) when either table is >30 min stale, Supabase is unreachable, or a grid outage starts (≥5% of customers without service, or any load shed). One email when an alert starts and one when it clears; state in `~/luma-alerts/state.json`, config in `~/luma-alerts/.env` (see `ops/.env.example`). `--test` sends a test email; `--dry-run` sends nothing. |

**On GitHub-hosted runners.** `.github/workflows/external-health-check.yml` runs `external_health_check.py` on `ubuntu-latest` every 30 minutes and alerts if either table is >45 min stale. It does not depend on the workstation. An open issue labelled `outage-alert` marks an ongoing incident: it is opened (with an email) when data goes stale and closed (with a "resolved" email) when data is fresh again. Each run re-enables the workflow so it can't be disabled for inactivity. Test the alert path with:

```bash
gh workflow run external-health-check.yml -R williamrodz/luma_scrape -f test_alert=true   # opens a test issue + email
gh workflow run external-health-check.yml -R williamrodz/luma_scrape                      # closes it + "resolved" email
```

Secrets used: `SUPABASE_URL`, `SUPABASE_KEY` (service_role), `RESEND_API_KEY` (send-only), `ALERT_TO`.

**Email domain.** Alerts are sent from `alerts@prgridstatus.com`. The Resend DNS records (DKIM TXT `resend._domainkey`, CNAMEs `rsend` and `send`, DMARC TXT `_dmarc`) live in the Route 53 zone for `prgridstatus.com`.

---

## Notes

- The `uv.lock` file is committed to ensure reproducible builds.
- `load_shed_*` columns are currently always `0` but will become meaningful if LUMA activates load shedding (`MiLumaOutage.LoadShed.Enable = "true"`).
