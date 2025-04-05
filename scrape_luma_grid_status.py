# fo
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import pytz
# For publishing to the database
from supabase import create_client, Client
from datetime import datetime
from dotenv import load_dotenv
import os

URL = "https://lumapr.com/system-overview/?lang=en"

def get_svg_value(soup, div_id):
    div = soup.find("div", id=div_id)
    if div:
        text = div.find("text", class_="value-text")
        if text:
            return text.text.strip()
    return None

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
    # Set up
    load_dotenv()  # loads .env into os.environ

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    supabase: Client = create_client(url, key)

    # Insert your data

    response = supabase.table("luma_scrape_results").insert(results).execute()
    return response

if __name__ == "__main__":
    # Run the scraper and publish results to the database
    try:
        results = scrape_luma()
        print("Scraping successful. Results:")
        print(results)
        publishing_response = publish_results_to_db(results)
        print(publishing_response)

    except Exception as e:
        print(f"An error occurred: {e}")
        # Optionally, you could log the error or handle it differently here