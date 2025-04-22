import os
import time
import hashlib
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from supabase import create_client, Client

# Supabase setup
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")

def setup_driver():
    """Set up a headless Chrome browser"""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    # For GitHub Actions environment
    if os.environ.get("GITHUB_ACTIONS"):
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def scrape_luma_outages():
    """Scrape outage data from LUMA PR website using Selenium"""
    url = "https://lumapr.com/notable-outages/?lang=en"
    
    try:
        driver = setup_driver()
        driver.get(url)
        
        # Wait for the table to be loaded
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.dataTables_scrollBody table tbody tr"))
        )
        
        # Give a little extra time for all data to render
        time.sleep(3)
        
        # Find all table rows
        rows = driver.find_elements(By.CSS_SELECTOR, "div.dataTables_scrollBody table tbody tr")
        
        outages = []
        for row in rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) >= 7:
                municipality = cells[0].text.strip()
                sector = cells[1].text.strip()
                outage_reported = cells[2].text.strip()
                est_restoration = cells[3].text.strip()
                customers_impacted = cells[4].text.strip()
                category = cells[5].text.strip()
                current_status = cells[6].text.strip()
                
                # Create a unique ID based on municipality, sector and outage time
                unique_id = hashlib.md5(f"{municipality}_{sector}_{outage_reported}".encode()).hexdigest()
                
                # Parse dates
                try:
                    outage_time = datetime.strptime(outage_reported, '%B %d %H:%M\'')
                    # Add current year since it's not in the string
                    outage_time = outage_time.replace(year=datetime.now().year)
                except ValueError:
                    outage_time = None
                
                try:
                    restoration_time = datetime.strptime(est_restoration, '%B %d %H:%M\'')
                    # Add current year since it's not in the string
                    restoration_time = restoration_time.replace(year=datetime.now().year)
                except ValueError:
                    restoration_time = None
                
                outage_data = {
                    "id": unique_id,
                    "municipality": municipality,
                    "sector": sector,
                    "outage_reported_text": outage_reported,
                    "outage_reported_timestamp": outage_time.isoformat() if outage_time else None,
                    "restoration_estimated_text": est_restoration,
                    "restoration_estimated_timestamp": restoration_time.isoformat() if restoration_time else None,
                    "customers_impacted": customers_impacted,
                    "category": category,
                    "current_status": current_status,
                    "scraped_at": datetime.now().isoformat()
                }
                
                outages.append(outage_data)
        
        driver.quit()
        return outages
    
    except Exception as e:
        print(f"Error scraping data: {e}")
        if 'driver' in locals():
            driver.quit()
        return []

def store_outages_in_supabase(outages):
    supabase = create_client(supabase_url, supabase_key)

    if not outages:
        print("No outages to store")
        return
    
    try:
        # Get existing outage IDs to determine which are new
        existing_outages = supabase.table("luma_outages").select("id").execute()
        existing_ids = {item['id'] for item in existing_outages.data}
        
        # Separate new and existing outages
        new_outages = [o for o in outages if o['id'] not in existing_ids]
        update_outages = [o for o in outages if o['id'] in existing_ids]
        
        # Insert new outages
        if new_outages:
            result = supabase.table("luma_outages").insert(new_outages).execute()
            print(f"Inserted {len(new_outages)} new outages")
        
        # Update existing outages (mainly for status changes)
        for outage in update_outages:
            result = supabase.table("luma_outages").update(outage).eq("id", outage['id']).execute()
        
        if update_outages:
            print(f"Updated {len(update_outages)} existing outages")
            
    except Exception as e:
        print(f"Error storing data in Supabase: {e}")

def main():
    print(f"Starting scraper at {datetime.now().isoformat()}")
    outages = scrape_luma_outages()
    print(f"Found {len(outages)} outages")
    #store_outages_in_supabase(outages)
    print(f"Completed at {datetime.now().isoformat()}")

if __name__ == "__main__":
    main()