-- Migration: add missing per-region and island-wide totals columns to outage_snapshot
-- All new columns are nullable so existing rows are unaffected.

ALTER TABLE outage_snapshot

  -- Arecibo
  ADD COLUMN with_service_arecibo                INTEGER,
  ADD COLUMN load_shed_arecibo                   INTEGER,
  ADD COLUMN pct_without_service_arecibo         NUMERIC(5,2),
  ADD COLUMN pct_with_service_arecibo            NUMERIC(5,2),

  -- Bayamon
  ADD COLUMN with_service_bayamon                INTEGER,
  ADD COLUMN load_shed_bayamon                   INTEGER,
  ADD COLUMN pct_without_service_bayamon         NUMERIC(5,2),
  ADD COLUMN pct_with_service_bayamon            NUMERIC(5,2),

  -- Carolina
  ADD COLUMN with_service_carolina               INTEGER,
  ADD COLUMN load_shed_carolina                  INTEGER,
  ADD COLUMN pct_without_service_carolina        NUMERIC(5,2),
  ADD COLUMN pct_with_service_carolina           NUMERIC(5,2),

  -- Caguas
  ADD COLUMN with_service_caguas                 INTEGER,
  ADD COLUMN load_shed_caguas                    INTEGER,
  ADD COLUMN pct_without_service_caguas          NUMERIC(5,2),
  ADD COLUMN pct_with_service_caguas             NUMERIC(5,2),

  -- Mayaguez
  ADD COLUMN with_service_mayaguez               INTEGER,
  ADD COLUMN load_shed_mayaguez                  INTEGER,
  ADD COLUMN pct_without_service_mayaguez        NUMERIC(5,2),
  ADD COLUMN pct_with_service_mayaguez           NUMERIC(5,2),

  -- Ponce
  ADD COLUMN with_service_ponce                  INTEGER,
  ADD COLUMN load_shed_ponce                     INTEGER,
  ADD COLUMN pct_without_service_ponce           NUMERIC(5,2),
  ADD COLUMN pct_with_service_ponce              NUMERIC(5,2),

  -- San Juan
  ADD COLUMN with_service_san_juan               INTEGER,
  ADD COLUMN load_shed_san_juan                  INTEGER,
  ADD COLUMN pct_without_service_san_juan        NUMERIC(5,2),
  ADD COLUMN pct_with_service_san_juan           NUMERIC(5,2),

  -- Island-wide totals
  ADD COLUMN totals_total_clients                INTEGER,
  ADD COLUMN totals_without_service              INTEGER,
  ADD COLUMN totals_with_service                 INTEGER,
  ADD COLUMN totals_planned                      INTEGER,
  ADD COLUMN totals_load_shed                    INTEGER,
  ADD COLUMN totals_pct_without_service          NUMERIC(5,2),
  ADD COLUMN totals_pct_with_service             NUMERIC(5,2);
