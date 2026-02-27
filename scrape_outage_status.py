import requests
import json
from datetime import datetime, timedelta, timezone
import sys
from typing import Dict, Any
from supabase import create_client, Client

import os

# Only try to load .env if running locally
# try:
#     from dotenv import load_dotenv
#     load_dotenv()  # loads .env into os.environ
# except ImportError:
#     pass  # Skip if dotenv is not installed (like in GitHub Actions)


# Supabase credentials from environment
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_CONFIGURED = bool(SUPABASE_URL and SUPABASE_KEY)

MAX_DATA_AGE_MINUTES = 30

API_URL = "https://api.miluma.lumapr.com/miluma-outage-api/outage/regionsWithoutService"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Origin": "https://miluma.lumapr.com",
    "Referer": "https://miluma.lumapr.com/",
}


def fetch_outage_data(timeout_seconds: int = 20) -> Dict[str, Any]:
    """Fetch outage data directly from the LUMA API."""
    response = requests.get(API_URL, headers=DEFAULT_HEADERS, timeout=timeout_seconds)
    response.raise_for_status()
    return response.json()


def save_data_to_supabase(data: Dict[str, Any]):
    """
    Converts API outage data to a flat one-row dict and inserts into Supabase.
    """
    if not SUPABASE_CONFIGURED:
        print("Supabase credentials not configured; skipping save.")
        return
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "last_update": data["timestamp"],
    }

    for region in data["regions"]:
        key_suffix = region["name"].lower().replace(" ", "_")
        row[f"total_customers_{key_suffix}"]        = region["totalClients"]
        row[f"out_of_service_{key_suffix}"]         = region["totalClientsWithoutService"]
        row[f"planned_upgrades_{key_suffix}"]       = region["totalClientsAffectedByPlannedOutage"]
        row[f"with_service_{key_suffix}"]           = region["totalClientsWithService"]
        row[f"load_shed_{key_suffix}"]              = region["totalClientsAffectedByLoadShed"]
        row[f"pct_without_service_{key_suffix}"]    = region["percentageClientsWithoutService"]
        row[f"pct_with_service_{key_suffix}"]       = region["percentageClientsWithService"]

    totals = data["totals"]
    row["totals_total_clients"]       = totals["totalClients"]
    row["totals_without_service"]     = totals["totalClientsWithoutService"]
    row["totals_with_service"]        = totals["totalClientsWithService"]
    row["totals_planned"]             = totals["totalClientsAffectedByPlannedOutage"]
    row["totals_load_shed"]           = totals["totalClientsAffectedByLoadShed"]
    row["totals_pct_without_service"] = totals["totalPercentageWithoutService"]
    row["totals_pct_with_service"]    = totals["totalPercentageWithService"]

    response = supabase.table("outage_snapshot").insert(row).execute()

    if response:
        print("✅ Supabase insert successful.")
    else:
        print("❌ Supabase insert error:", response["error"])


def is_newer_last_update(scraped_last_update: str) -> bool:
    """
    Compares the new `last_update` value to the most recent one in Supabase.
    Returns True if new data is newer, False otherwise.
    """
    if not SUPABASE_CONFIGURED:
        return True
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Parse the new timestamp
    new_time = datetime.strptime(scraped_last_update, "%m/%d/%Y %I:%M %p")

    # Query the latest row
    response = supabase.table("outage_snapshot").select("last_update").order("timestamp", desc=True).limit(1).execute()

    if response.data and len(response.data) > 0:
        latest = response.data[0]["last_update"]
        try:
            latest_time = datetime.strptime(latest, "%m/%d/%Y %I:%M %p")
            print(f"Latest timestamp in DB: {latest_time}")
            print(f"New timestamp: {new_time}")
            if new_time > latest_time:
                print("🆕 Newer data found.")
                return True
            else:
                print("🔄 No new data found.")
                return False
        except Exception as e:
            print("⚠️ Error parsing last_update from DB:", e)
            return True  # if in doubt, insert
    else:
        return True  # table is empty


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
        response = supabase.table("outage_snapshot").select("timestamp").order("timestamp", desc=True).limit(1).execute()

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


def main():
    print(f"Starting scrape at {datetime.now()}")

    try:
        data = fetch_outage_data()

        print(f"Successfully fetched {len(data['regions'])} regions")
        print("Data preview:")
        for region in data["regions"]:
            print(f"  {region['name']}: {region['totalClients']} customers, "
                  f"{region['totalClientsWithoutService']} out of service ({region['percentageClientsWithoutService']}%), "
                  f"{region['totalClientsWithService']} with service ({region['percentageClientsWithService']}%), "
                  f"{region['totalClientsAffectedByPlannedOutage']} planned, "
                  f"{region['totalClientsAffectedByLoadShed']} load shed")
        totals = data["totals"]
        print(f"  TOTAL: {totals['totalClients']} customers, "
              f"{totals['totalClientsWithoutService']} out of service ({totals['totalPercentageWithoutService']}%), "
              f"{totals['totalClientsWithService']} with service ({totals['totalPercentageWithService']}%), "
              f"{totals['totalClientsAffectedByPlannedOutage']} planned, "
              f"{totals['totalClientsAffectedByLoadShed']} load shed")
        print(f"Last update: {data['timestamp']}")

        # Save to latest.json for easy access
        with open('latest.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print("Data saved to latest.json")

        if is_newer_last_update(data["timestamp"]):
            print("Newer data found, saving to Supabase...")
            save_data_to_supabase(data)
        else:
            print("No new data to save to Supabase.")

    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        print(f"⏱️ Request timed out or connection error: {str(e)}")
        print("Checking database for recent data...")
        if has_recent_data_in_db(minutes=MAX_DATA_AGE_MINUTES):
            print("✅ Recent data found in database. Skipping error (site may be temporarily unavailable).")
            sys.exit(0)
        else:
            print("❌ No recent data in database. This is a real failure.")
            raise
    except Exception as e:
        print(f"An error occurred:\n{e}")
        raise


if __name__ == "__main__":
    main()
