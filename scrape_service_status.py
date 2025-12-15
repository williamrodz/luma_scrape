import asyncio
from playwright.async_api import async_playwright
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List
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

def save_data_to_supabase(data: Dict[str, Any]):
    """
    Converts scraped outage data to a flat one-row dict and inserts into Supabase.
    """
    if not SUPABASE_CONFIGURED:
        print("Supabase credentials not configured; skipping save.")
        return
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    row = {
        "timestamp": data["timestamp"],
        "last_update": data["last_update"]
    }

    for region in data["data"]:
        key_suffix = region["Region"].lower().replace(" ", "_")
        row[f"total_customers_{key_suffix}"] = int(region["Total customers"].replace(",", ""))
        row[f"out_of_service_{key_suffix}"] = int(region["Out of Service"].replace(",", ""))
        row[f"planned_upgrades_{key_suffix}"] = int(region["Planned Upgrades"].replace(",", ""))

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
        cutoff_iso = cutoff_time.isoformat() + 'Z'
        
        # Query the latest row
        response = supabase.table("outage_snapshot").select("timestamp").order("timestamp", desc=True).limit(1).execute()
        
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

async def scrape_luma_outages() -> Dict[str, Any]:
    """
    Scrapes the outage data from the LUMA PR website using Playwright
    """
    async with async_playwright() as p:
        # Launch browser in headless mode
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        try:
            # Navigate to the page
            await page.goto('https://miluma.lumapr.com/outages/status', 
                          wait_until='networkidle')
            
            # Wait for the table container to load
            await page.wait_for_selector('div.w-full.max-w-full.overflow-x-auto', timeout=30000)
            
            # Extract table data
            table_data = await page.evaluate(r'''
                () => {
                    const container = document.querySelector('div.w-full.max-w-full.overflow-x-auto');
                    if (!container) return null;
                    
                    // Get headers from the header row
                    const headerRow = container.querySelector('.grid.grid-cols-8.w-full.text-darkGreen');
                    const headers = [];
                    if (headerRow) {
                        const headerButtons = headerRow.querySelectorAll('button');
                        headerButtons.forEach(button => {
                            const headerText = button.querySelector('div').textContent.trim();
                            headers.push(headerText);
                        });
                    }
                    
                    // Find the positions of the columns we want
                    const totalCustomersIndex = headers.findIndex(h => h === 'Total customers');
                    const outOfServiceIndex = headers.findIndex(h => h === 'Out of Service');
                    const plannedUpgradesIndex = headers.findIndex(h => h === 'Planned Upgrades');
                    
                    // Get all data rows (skip the header row)
                    const rows = [];
                    const dataRows = container.querySelectorAll('.border-t.border-t-darkGray.grid.grid-cols-8');
                    
                    dataRows.forEach(row => {
                        const cells = row.querySelectorAll('div.p-4');
                        if (cells.length >= 8) {
                            const regionName = cells[0].textContent.trim();
                            
                            // Skip the Totals row
                            if (regionName !== 'Totals') {
                                const rowData = {
                                    Region: regionName,
                                    'Total customers': cells[totalCustomersIndex].textContent.trim(),
                                    'Out of Service': cells[outOfServiceIndex].textContent.trim(),
                                    'Planned Upgrades': cells[plannedUpgradesIndex].textContent.trim()
                                };
                                rows.push(rowData);
                            }
                        }
                    });
                    
                    // Find the "Last update:" timestamp
                  let lastUpdate = null;
                  const textElements = document.querySelectorAll('*');
                  for (const element of textElements) {
                      if (element.textContent && element.textContent.includes('Last update:')) {
                          const text = element.textContent;
                          const match = text.match(/Last update:\s*(.+)/);
                          if (match && match[1]) {
                              lastUpdate = match[1].trim();
                              // Trim to include only up to the first AM or PM (case-insensitive)
                              const timeMatch = lastUpdate.match(/.*?(AM|PM)/i);
                              if (timeMatch) {
                                  lastUpdate = timeMatch[0];
                              }
                              break;
                          }
                      }
                  }

                  return {
                      data: rows,
                      timestamp: new Date().toISOString(),
                      last_update: lastUpdate
                  };
                }
            ''')
            
            return table_data
            
        finally:
            await browser.close()

async def main():
    """
    Main function to run the scraper and save results
    """
    print(f"Starting scrape at {datetime.now()}")
    
    try:
        data = await scrape_luma_outages()
        
        if data:
            # Save to JSON file
            filename = f"luma_outages_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            print(f"Successfully scraped {len(data['data'])} regions")
            print("Data preview:")
            for region in data['data']:
                print(f"  {region['Region']}: {region['Total customers']} customers, {region['Out of Service']} out of service, {region['Planned Upgrades']} planned upgrades")
            print(f"Data saved to {filename}")

            if is_newer_last_update(data["last_update"]):
                print("Newer data found, saving to Supabase...")
                # Save to Supabase
                save_data_to_supabase(data)
            else:
                print("No new data to save to Supabase.")
            
            # Also save a latest.json for easy access
            with open('latest.json', 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
        else:
            print("No data found")
            
    except Exception as e:
        print(f"Error during scraping: {str(e)}")
        
        # Check if it's a timeout error and if we have recent data in DB
        from playwright._impl._errors import TimeoutError as PlaywrightTimeoutError
        
        if isinstance(e, PlaywrightTimeoutError):
            print("⏱️ Scraping timed out. Checking database for recent data...")
            if has_recent_data_in_db(minutes=15):
                print("✅ Recent data found in database. Skipping error (site may be temporarily unavailable).")
                return  # Exit successfully without raising
            else:
                print("❌ No recent data in database. This is a real failure.")
                raise  # Re-raise the error if no recent data
        else:
            # For other errors, raise as normal
            raise

if __name__ == "__main__":
    asyncio.run(main())