# For scraping using BeautifulSoup and requests 
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import pytz
# For publishing to the database using Supabase
from supabase import create_client, Client
from datetime import datetime
import os

# Only try to load .env if running locally
try:
    from dotenv import load_dotenv
    load_dotenv()  # loads .env into os.environ
except ImportError:
    pass  # Skip if dotenv is not installed (like in GitHub Actions)


URL = "https://lumapr.com/system-overview/?lang=en"
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
# print(f"URL: {SUPABASE_URL[0:5]}")  # Print only part of the key for security
# print(f"KEY: {SUPABASE_KEY[0:5]}")  # Print only part of the key for security

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: SUPABASE_URL or SUPABASE_KEY not set")
    exit(1)
else:
    print("Supabase credentials loaded successfully.")
    # Uncomment the next line to see the URL and Key (for debugging purposes only)
    # print(f"URL: {SUPABASE_URL}, Key: {SUPABASE_KEY[:4]}...")  # Print only part of the key for security
    #print(f"URL: {SUPABASE_URL[0:5]}")  # Print only part of the key for security
    #print(f"KEY: {SUPABASE_KEY[0:5]}")  # Print only part of the key for security

    pass

def scrape_luma():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    }
    # Avoid the 403 Forbidden error by the website by using a user-agent header
    response = requests.get(URL, headers=headers)

    if response.status_code != 200:
        raise Exception(f"Failed to fetch page: {response.status_code}")

    soup = BeautifulSoup(response.text, 'html.parser')

   # Define the IDs and their corresponding result keys
    target_ids = {
        "total-Generation": "current_demand",
        "next-Hour-Forecast": "next_hour_demand_forecast",
        "reserve": "current_reserve"
    }

    results = {}

    for div_id, key in target_ids.items():
        div = soup.find("div", id=div_id)
        if div:
            # Current value from data-value
            current = int(div['data-value']) if div.has_attr('data-value') else None
            # Max value from span.max-text
            max_span = div.find("span", class_="max-text")
            max_val = int(max_span.get_text(strip=True)) if max_span else None

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
            results["peak_demand_forecast"] = int(peak_values[0].get_text(strip=True).replace("MW", ""))
            results["peak_reserve_forecast"] = int(peak_values[1].get_text(strip=True).replace("MW", ""))
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

def publish_results_to_db(results):

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Insert your data

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
        print(publishing_response)
        print()

    except Exception as e:
        print(f"An error occurred:\n{e}")
        # Optionally, you could log the error or handle it differently here