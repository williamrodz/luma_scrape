# outage_snapshot Schema Changes — 2026-02-27

## Background

The `outage_snapshot` table stores periodic snapshots of LUMA PR outage data scraped from the MiLUMA API (`GET /miluma-outage-api/outage/regionsWithoutService`). The API returns 7 fields per region plus an island-wide `totals` object. Previously, only 3 of those 7 per-region fields were being captured and the `totals` object was dropped entirely.

This migration adds the missing fields.

---

## What Was Captured Before

3 columns per region × 7 regions = **21 region columns**, plus 2 metadata columns = **23 columns total**.

| Column pattern | API field |
|---|---|
| `total_customers_{region}` | `region.totalClients` |
| `out_of_service_{region}` | `region.totalClientsWithoutService` |
| `planned_upgrades_{region}` | `region.totalClientsAffectedByPlannedOutage` |

---

## What Is Captured Now

4 additional columns per region + 7 island-wide totals columns = **35 new columns**, bringing the total to **58 columns**.

### Per-region additions (4 new fields × 7 regions = 28 new columns)

| Column pattern | Type | API field |
|---|---|---|
| `with_service_{region}` | `INTEGER` | `region.totalClientsWithService` |
| `load_shed_{region}` | `INTEGER` | `region.totalClientsAffectedByLoadShed` |
| `pct_without_service_{region}` | `NUMERIC(5,2)` | `region.percentageClientsWithoutService` |
| `pct_with_service_{region}` | `NUMERIC(5,2)` | `region.percentageClientsWithService` |

### Island-wide totals (7 new columns)

| Column | Type | API field |
|---|---|---|
| `totals_total_clients` | `INTEGER` | `totals.totalClients` |
| `totals_without_service` | `INTEGER` | `totals.totalClientsWithoutService` |
| `totals_with_service` | `INTEGER` | `totals.totalClientsWithService` |
| `totals_planned` | `INTEGER` | `totals.totalClientsAffectedByPlannedOutage` |
| `totals_load_shed` | `INTEGER` | `totals.totalClientsAffectedByLoadShed` |
| `totals_pct_without_service` | `NUMERIC(5,2)` | `totals.totalPercentageWithoutService` |
| `totals_pct_with_service` | `NUMERIC(5,2)` | `totals.totalPercentageWithService` |

---

## Full API → Column Mapping Reference

The `{region}` suffix is the region name lowercased with spaces replaced by underscores:
`arecibo`, `bayamon`, `carolina`, `caguas`, `mayaguez`, `ponce`, `san_juan`.

### Metadata

| Column | Source |
|---|---|
| `timestamp` | Scrape time (UTC, set by scraper) |
| `last_update` | `data.timestamp` — e.g. `"02/27/2026 11:40 AM"` (set by LUMA) |

### Per-region columns (shown for one region; pattern repeats for all 7)

| Column | Type | API field | New? |
|---|---|---|---|
| `total_customers_{region}` | `INTEGER` | `region.totalClients` | — |
| `out_of_service_{region}` | `INTEGER` | `region.totalClientsWithoutService` | — |
| `planned_upgrades_{region}` | `INTEGER` | `region.totalClientsAffectedByPlannedOutage` | — |
| `with_service_{region}` | `INTEGER` | `region.totalClientsWithService` | ✅ |
| `load_shed_{region}` | `INTEGER` | `region.totalClientsAffectedByLoadShed` | ✅ |
| `pct_without_service_{region}` | `NUMERIC(5,2)` | `region.percentageClientsWithoutService` | ✅ |
| `pct_with_service_{region}` | `NUMERIC(5,2)` | `region.percentageClientsWithService` | ✅ |

### Island-wide totals columns

| Column | Type | API field | New? |
|---|---|---|---|
| `totals_total_clients` | `INTEGER` | `totals.totalClients` | ✅ |
| `totals_without_service` | `INTEGER` | `totals.totalClientsWithoutService` | ✅ |
| `totals_with_service` | `INTEGER` | `totals.totalClientsWithService` | ✅ |
| `totals_planned` | `INTEGER` | `totals.totalClientsAffectedByPlannedOutage` | ✅ |
| `totals_load_shed` | `INTEGER` | `totals.totalClientsAffectedByLoadShed` | ✅ |
| `totals_pct_without_service` | `NUMERIC(5,2)` | `totals.totalPercentageWithoutService` | ✅ |
| `totals_pct_with_service` | `NUMERIC(5,2)` | `totals.totalPercentageWithService` | ✅ |

---

## Notes

- All new columns are **nullable** — existing rows are unaffected and backfilled with `NULL`.
- The `load_shed_*` columns are currently always `0` but are captured for completeness; the LUMA app config flag `MiLumaOutage.LoadShed.Enable` controls when load shedding is active.
- Derived fields (e.g. unplanned outages = `out_of_service - planned_upgrades - load_shed`) are intentionally excluded and left to downstream consumers to compute.
- The migration SQL is in `schema/outage_snapshot_migration.sql`.

---

## MiLUMA API Observations (2026-02-27)

### Context

During investigation of a scraper breakage (Playwright timing out on the DOM), the MiLUMA website was found to have transitioned from server-rendered HTML to a **React SPA** backed by a set of dedicated JSON REST APIs on `api.miluma.lumapr.com`. The scraper was rewritten to call the API directly, eliminating the Playwright dependency entirely.

### API Architecture

The frontend JS bundle (`/assets/index-BwRXc_Az.js`) revealed **11 distinct backend API services**, all hosted under `https://api.miluma.lumapr.com/`:

| Service | Base path | Auth required |
|---|---|---|
| `miluma-outage-api` | `/miluma-outage-api` | Partial — one endpoint is open |
| `miluma-report-outage-api` | `/miluma-report-outage-api` | Yes |
| `miluma-api` | `/miluma-api` | Yes |
| `miluma-app-config-api` | `/miluma-app-config-api` | No |
| `miluma-notification-api` | `/miluma-notification-api` | Yes |
| `miluma-payment-api` | `/miluma-payment-api` | Yes |
| `miluma-servicecall-api` | `/miluma-servicecall-api` | Yes |
| `miluma-certification-api` | `/miluma-certification-api` | Yes |
| `miluma-bill-api` | `/miluma-bill-api` | Yes |
| `miluma-bill-objection-api` | `/miluma-bill-objection-api` | Yes |
| `miluma-email-api` | `/miluma-email-api` | Yes |

No OpenAPI/Swagger docs are exposed publicly.

### Publicly Accessible Endpoints

**`GET /miluma-outage-api/outage/regionsWithoutService`** — the endpoint currently scraped. Returns live outage data for all 7 LUMA regions plus island-wide totals. No authentication required. Sample response shape:

```json
{
  "regions": [
    {
      "name": "Arecibo",
      "totalClients": 177369,
      "totalClientsWithoutService": 224,
      "totalClientsWithService": 177145,
      "totalClientsAffectedByPlannedOutage": 0,
      "totalClientsAffectedByLoadShed": 0,
      "percentageClientsWithoutService": 0.13,
      "percentageClientsWithService": 99.87
    }
  ],
  "totals": {
    "totalClients": 1468223,
    "totalClientsWithoutService": 307,
    "totalClientsWithService": 1465953,
    "totalClientsAffectedByPlannedOutage": 1716,
    "totalClientsAffectedByLoadShed": 0,
    "totalPercentageWithoutService": 0.82,
    "totalPercentageWithService": 99.18
  },
  "timestamp": "02/27/2026 11:40 AM"
}
```

**`POST /miluma-outage-api/outage/municipality/towns`** — POST-only endpoint that accepts a municipality name and returns per-town outage breakdowns. Provides finer granularity than the region-level data above. Not yet scraped.

**`GET /miluma-app-config-api/v2/configs`** — returns all application feature flags. Notable flags relevant to scraping:

| Flag | Current value | Significance |
|---|---|---|
| `MiLumaOutage.LoadShed.Enable` | `"false"` | When `"true"`, a load shed column appears in the region data |
| `MiLumaOutage.ServiceStatus.Enable` | `"true"` | Controls whether the outage status page is live |
| `maintenanceMode` | `"false"` | Site-wide maintenance flag |
| `enableStorms` | `"yes"` | Storm mode — may affect data structure |

**`GET /miluma-app-config-api/getGeneralPurposeBanners`** — returns active site banners. Currently empty (`[]`); likely populated during major outage events or storms.

### Potential Future Data Sources

- **`POST /miluma-outage-api/outage/municipality/towns`** — town-level granularity, the most immediately useful undiscovered endpoint.
- **`GET /miluma-app-config-api/getGeneralPurposeBanners`** — could serve as an early-warning signal for major events if polled alongside the outage data.
