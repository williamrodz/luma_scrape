"""
Manual test for s3_buffer.py.
Run with: uv run --no-project python test_s3_buffer.py
Requires AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUFFER_BUCKET in .env or environment.
"""
from dotenv import load_dotenv
load_dotenv()

import s3_buffer
from datetime import datetime, timezone

PASS = "✅"
FAIL = "❌"
results = []


def check(label: str, condition: bool):
    status = PASS if condition else FAIL
    print(f"  {status} {label}")
    results.append(condition)


# ── 1. Config check ───────────────────────────────────────────────────────────
print("\n── 1. Config ────────────────────────────────────────────────────────────")
check("S3_CONFIGURED is True", s3_buffer.S3_CONFIGURED)
check("BUFFER_BUCKET is set", bool(s3_buffer.BUFFER_BUCKET))
if not s3_buffer.S3_CONFIGURED:
    print("\n❌ S3 not configured — check your .env. Aborting.")
    raise SystemExit(1)


# ── 2. Write dummy rows ───────────────────────────────────────────────────────
print("\n── 2. Write to buffer ───────────────────────────────────────────────────")

dummy_grid_row = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "current_demand": 1234,
    "current_demand_max": 2000,
    "next_hour_demand_forecast": 1300,
    "next_hour_demand_forecast_max": 2000,
    "current_reserve": 766,
    "current_reserve_max": 900,
    "peak_demand_forecast": 1800,
    "peak_reserve_forecast": 200,
    "_test": True,
}

dummy_outage_row = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "last_update": "05/11/2026 12:00 PM",
    "total_customers_san_juan": 500000,
    "out_of_service_san_juan": 1000,
    "_test": True,
}

ok1 = s3_buffer.write_to_buffer("luma_scrape_results", dummy_grid_row)
check("write dummy luma_scrape_results row", ok1)

ok2 = s3_buffer.write_to_buffer("outage_snapshot", dummy_outage_row)
check("write dummy outage_snapshot row", ok2)


# ── 3. Verify files exist in S3 ───────────────────────────────────────────────
print("\n── 3. Verify files exist in S3 ──────────────────────────────────────────")

def list_keys(prefix: str) -> list[str]:
    s3 = s3_buffer._client()
    paginator = s3.get_paginator("list_objects_v2")
    return [
        obj["Key"]
        for page in paginator.paginate(Bucket=s3_buffer.BUFFER_BUCKET, Prefix=prefix)
        for obj in page.get("Contents", [])
    ]

grid_keys = list_keys("luma_scrape_results/")
outage_keys = list_keys("outage_snapshot/")
print(f"  luma_scrape_results/ keys: {grid_keys}")
print(f"  outage_snapshot/ keys:     {outage_keys}")
check("at least 1 luma_scrape_results file in S3", len(grid_keys) >= 1)
check("at least 1 outage_snapshot file in S3", len(outage_keys) >= 1)


# ── 4. Flush with mock insert ─────────────────────────────────────────────────
print("\n── 4. Flush buffer (mock insert) ────────────────────────────────────────")

flushed_grid = []
flushed_outage = []

def mock_insert_grid(row):
    flushed_grid.append(row)
    print(f"    mock insert → luma_scrape_results: demand={row.get('current_demand')}")

def mock_insert_outage(row):
    flushed_outage.append(row)
    print(f"    mock insert → outage_snapshot: last_update={row.get('last_update')}")

s3_buffer.flush_buffer("luma_scrape_results", mock_insert_grid)
s3_buffer.flush_buffer("outage_snapshot", mock_insert_outage)

check("luma_scrape_results row was flushed", len(flushed_grid) >= 1)
check("outage_snapshot row was flushed", len(flushed_outage) >= 1)
check("flushed grid row has _test flag", flushed_grid[0].get("_test") is True if flushed_grid else False)


# ── 5. Verify S3 is clean after flush ─────────────────────────────────────────
print("\n── 5. Verify S3 is clean after flush ────────────────────────────────────")
remaining_grid = list_keys("luma_scrape_results/")
remaining_outage = list_keys("outage_snapshot/")
print(f"  luma_scrape_results/ remaining: {remaining_grid}")
print(f"  outage_snapshot/ remaining:     {remaining_outage}")
check("no luma_scrape_results files left in S3", len(remaining_grid) == 0)
check("no outage_snapshot files left in S3", len(remaining_outage) == 0)


# ── 6. Summary ────────────────────────────────────────────────────────────────
print("\n── Summary ──────────────────────────────────────────────────────────────")
passed = sum(results)
total = len(results)
print(f"  {passed}/{total} checks passed\n")
if passed < total:
    raise SystemExit(1)
