# For scraping using BeautifulSoup and requests 
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import pytz
import sys
from typing import Any, Dict, Optional
# For publishing to the database using Supabase
from supabase import create_client, Client
import os

# Only try to load .env if running locally
try:
    from dotenv import load_dotenv
    load_dotenv()  # loads .env into os.environ
except ImportError:
    pass  # Skip if dotenv is not installed (like in GitHub Actions)


URL = "https://lumapr.com/system-overview/?lang=en"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    )
}

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


def has_recent_data_in_db(minutes: int = 15) -> bool:
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
        cutoff_time = datetime.utcnow() - timedelta(minutes=minutes)
        
        # Query the latest row
        response = supabase.table("luma_scrape_results").select("timestamp").order("timestamp", desc=True).limit(1).execute()
        
        if response.data and len(response.data) > 0:
            latest_timestamp_str = response.data[0]["timestamp"]
            # Parse the ISO timestamp from the database
            latest_timestamp = datetime.fromisoformat(latest_timestamp_str.replace('Z', '+00:00'))
            # Convert to UTC naive for comparison
            if latest_timestamp.tzinfo:
                latest_timestamp = latest_timestamp.replace(tzinfo=None)
            
            print(f"Latest data in DB: {latest_timestamp}")
            print(f"Cutoff time (now - {minutes} min): {cutoff_time}")
            
            if latest_timestamp >= cutoff_time:
                print(f"✅ Found recent data in DB (within last {minutes} minutes)")
                return True
            else:
                print(f"⚠️ Latest data in DB is older than {minutes} minutes")
                return False
        else:
            print("⚠️ No data found in database")
            return False
    except Exception as e:
        print(f"⚠️ Error checking database for recent data: {e}")
        return False  # If we can't check, assume no recent data (safer to fail)


def scrape_luma(timeout_seconds: int = 20) -> Dict[str, Any]:
    """Scrape LUMA system overview metrics and return a structured result dict."""
    with requests.Session() as session:
        response = session.get(URL, headers=DEFAULT_HEADERS, timeout=timeout_seconds)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')

    # Define the IDs and their corresponding result keys
    target_ids = {
        "total-Generation": "current_demand",
        "next-Hour-Forecast": "next_hour_demand_forecast",
        "reserve": "current_reserve",
    }

    results: Dict[str, Any] = {}

    for div_id, key in target_ids.items():
        div = soup.find("div", id=div_id)
        if div:
            # Current value from data-value
            current = _safe_parse_int(div.get("data-value")) if div.has_attr('data-value') else None
            # Max value from span.max-text
            max_span = div.find("span", class_="max-text")
            max_val = _safe_parse_int(max_span.get_text(strip=True)) if max_span else None

            results[key] = current
            results[f"{key}_max"] = max_val
        else:
            results[key] = None
            results[f"{key}_max"] = None

    # Extract peak demand and peak reserve from the "peak-Forecast" section
    peak_div = soup.find("div", id="peak-Forecast")
    if peak_div:
        peak_values = peak_div.find_all("p", class_="peak-text")
        if len(peak_values) >= 2:
            results["peak_demand_forecast"] = _safe_parse_int(peak_values[0].get_text(strip=True))
            results["peak_reserve_forecast"] = _safe_parse_int(peak_values[1].get_text(strip=True))
        else:
            results["peak_demand_forecast"] = None
            results["peak_reserve_forecast"] = None
    else:
        results["peak_demand_forecast"] = None
        results["peak_reserve_forecast"] = None

    # Add timestamp
    puerto_rico_tz = pytz.timezone("America/Puerto_Rico")
    results["timestamp"] = datetime.now(puerto_rico_tz).isoformat()
    return results

def publish_results_to_db(results: Dict[str, Any]):
    """Publish results to Supabase if credentials are configured."""
    if not SUPABASE_CONFIGURED:
        print("Supabase credentials not configured; skipping publish.")
        return None

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    response = supabase.table("luma_scrape_results").insert(results).execute()
    return response

if __name__ == "__main__":
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

    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        print(f"⏱️ Request timed out or connection error: {str(e)}")
        print("Checking database for recent data...")
        if has_recent_data_in_db(minutes=15):
            print("✅ Recent data found in database. Skipping error (site may be temporarily unavailable).")
            sys.exit(0)  # Exit successfully without raising
        else:
            print("❌ No recent data in database. This is a real failure.")
            raise  # Re-raise the error if no recent data
    except Exception as e:
        print(f"An error occurred:\n{e}")
        raise  # Re-raise all other errors