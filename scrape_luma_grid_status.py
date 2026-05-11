from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from datetime import datetime, timedelta, timezone
import pytz
import sys
import time
from typing import Any, Dict, Optional
# For publishing to the database using Supabase
from supabase import create_client, Client
from postgrest.exceptions import APIError
import os
import s3_buffer

# Only try to load .env if running locally
try:
    from dotenv import load_dotenv
    load_dotenv()  # loads .env into os.environ
except ImportError:
    pass  # Skip if dotenv is not installed (like in GitHub Actions)


MAX_DATA_AGE_MINUTES = 30

URL = "https://lumapr.com/system-overview/?lang=en"

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_CONFIGURED = bool(SUPABASE_URL and SUPABASE_KEY)

def _safe_parse_int(value: Optional[str]) -> Optional[int]:
    """Parse an integer from a string like "1,234" or "500MW". Returns None if not parseable."""
    if value is None:
        return None
    text = value.strip().replace(",", "").replace("MW", "")
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def has_recent_data_in_db(minutes: int = MAX_DATA_AGE_MINUTES) -> bool:
    """
    Checks if there's data in the database from within the last N minutes.
    Returns True if recent data exists, False otherwise.
    """
    if not SUPABASE_CONFIGURED:
        print("Supabase credentials not configured; cannot check for recent data.")
        return False

    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

        # Calculate the cutoff time (now minus N minutes)
        now = datetime.now(timezone.utc)
        cutoff_time = now - timedelta(minutes=minutes)

        # Query the latest row
        response = supabase.table("luma_scrape_results").select("timestamp").order("timestamp", desc=True).limit(1).execute()

        if response.data and len(response.data) > 0:
            latest_timestamp_str = response.data[0]["timestamp"]
            # Parse the ISO timestamp from the database as timezone-aware UTC
            latest_timestamp = datetime.fromisoformat(latest_timestamp_str.replace('Z', '+00:00'))
            if not latest_timestamp.tzinfo:
                latest_timestamp = latest_timestamp.replace(tzinfo=timezone.utc)

            print(f"Latest data in DB: {latest_timestamp}")
            print(f"Cutoff time (now - {minutes} min): {cutoff_time}")

            if latest_timestamp >= cutoff_time:
                print(f"✅ Found recent data in DB (within last {minutes} minutes)")
                return True
            else:
                age_minutes = (now - latest_timestamp).total_seconds() / 60
                print(f"⚠️ Latest data in DB is older than {minutes} minutes (actual age: {age_minutes:.1f} minutes)")
                return False
        else:
            print("⚠️ No data found in database")
            return False
    except Exception as e:
        print(f"⚠️ Error checking database for recent data: {e}")
        return False  # If we can't check, assume no recent data (safer to fail)


def scrape_luma(timeout_seconds: int = 30) -> Dict[str, Any]:
    """Scrape LUMA system overview metrics using a headless browser."""
    target_ids = {
        "total-Generation": "current_demand",
        "next-Hour-Forecast": "next_hour_demand_forecast",
        "reserve": "current_reserve",
    }

    results: Dict[str, Any] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(URL, timeout=timeout_seconds * 1000, wait_until="domcontentloaded")

            for div_id, key in target_ids.items():
                element = page.query_selector(f"#{div_id}")
                if element:
                    data_value = element.get_attribute("data-value")
                    current = _safe_parse_int(data_value)
                    max_span = element.query_selector("span.max-text")
                    max_val = _safe_parse_int(max_span.inner_text()) if max_span else None
                    results[key] = current
                    results[f"{key}_max"] = max_val
                else:
                    results[key] = None
                    results[f"{key}_max"] = None

            peak_div = page.query_selector("#peak-Forecast")
            if peak_div:
                peak_values = peak_div.query_selector_all("p.peak-text")
                if len(peak_values) >= 2:
                    results["peak_demand_forecast"] = _safe_parse_int(peak_values[0].inner_text())
                    results["peak_reserve_forecast"] = _safe_parse_int(peak_values[1].inner_text())
                else:
                    results["peak_demand_forecast"] = None
                    results["peak_reserve_forecast"] = None
            else:
                results["peak_demand_forecast"] = None
                results["peak_reserve_forecast"] = None
        finally:
            browser.close()

    puerto_rico_tz = pytz.timezone("America/Puerto_Rico")
    results["timestamp"] = datetime.now(puerto_rico_tz).isoformat()
    return results

NUMERIC_FIELDS = [
    "current_demand", "current_demand_max",
    "next_hour_demand_forecast", "next_hour_demand_forecast_max",
    "current_reserve", "current_reserve_max",
    "peak_demand_forecast", "peak_reserve_forecast",
]

def insert_row(row: Dict[str, Any]):
    """Insert a single row into Supabase. Raises on failure."""
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            supabase.table("luma_scrape_results").insert(row).execute()
            return
        except APIError as e:
            code = str(e.code) if hasattr(e, 'code') else ""
            is_transient = code in ("522", "521", "523", "503", "502", "500")
            if is_transient and attempt < max_attempts:
                wait = 2 ** attempt  # 2s, 4s
                print(f"Supabase transient error (code {code}), retrying in {wait}s (attempt {attempt}/{max_attempts})...")
                time.sleep(wait)
            else:
                raise


def publish_results_to_db(results: Dict[str, Any]):
    """Publish results to Supabase if credentials are configured. Buffers to S3 on failure."""
    if not SUPABASE_CONFIGURED:
        print("Supabase credentials not configured; skipping publish.")
        return None

    if all(results.get(f) is None for f in NUMERIC_FIELDS):
        raise ValueError("All numeric fields are None — scraped data is invalid. Rejecting DB insert.")

    try:
        insert_row(results)
    except Exception as e:
        print(f"Supabase insert failed: {e}")
        s3_buffer.write_to_buffer("luma_scrape_results", results)

if __name__ == "__main__":
    s3_buffer.flush_buffer("luma_scrape_results", insert_row)
    # Run the scraper and publish results to the database
    try:
        results = scrape_luma()
        print("Scraping successful. Results:")
        print(results)
        print()
        publishing_response = publish_results_to_db(results)
        if publishing_response is not None:
            print(publishing_response)
        print()

    except PlaywrightTimeoutError as e:
        print(f"⏱️ Browser navigation timed out: {str(e)}")
        print("Checking database for recent data...")
        if has_recent_data_in_db(minutes=MAX_DATA_AGE_MINUTES):
            print("✅ Recent data found in database. Skipping error (site may be temporarily unavailable).")
            sys.exit(0)  # Exit successfully without raising
        else:
            print("❌ No recent data in database. This is a real failure.")
            raise  # Re-raise the error if no recent data
    except APIError as e:
        print(f"Supabase API error after retries:\n{e}")
        print("Checking database for recent data...")
        if has_recent_data_in_db(minutes=MAX_DATA_AGE_MINUTES):
            print("✅ Recent data found. Treating Supabase outage as non-fatal.")
            sys.exit(0)
        else:
            print("❌ No recent data. Supabase outage is a real failure.")
            raise
    except Exception as e:
        print(f"An error occurred:\n{e}")
        raise  # Re-raise all other errors
