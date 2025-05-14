import asyncio
from playwright.async_api import async_playwright
import json
from datetime import datetime
import os

async def scrape_luma_outages():
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
            table_data = await page.evaluate('''
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
            
            # Also save a latest.json for easy access
            with open('latest.json', 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
        else:
            print("No data found")
            
    except Exception as e:
        print(f"Error during scraping: {str(e)}")
        raise

if __name__ == "__main__":
    asyncio.run(main())