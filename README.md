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

> **Note:** Between **2026-05-07 20:11 ET** and **2026-05-08 02:01 ET**, a Supabase service outage prevented scraped data from being saved to the database. Both scrapers were running normally during this window, but all write operations failed. No data from this period exists in either table.

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

## Notes

- The `uv.lock` file is committed to ensure reproducible builds.
- `load_shed_*` columns are currently always `0` but will become meaningful if LUMA activates load shedding (`MiLumaOutage.LoadShed.Enable = "true"`).
