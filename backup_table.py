import os
import csv
from datetime import datetime
from pathlib import Path
from supabase import create_client

try:
    from dotenv import load_dotenv
    load_dotenv()  # loads .env into os.environ
except ImportError:
    pass  # Skip if dotenv is not installed (like in GitHub Actions)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
TABLE = "luma_scrape_results"
BATCH_SIZE = 1000


def fetch_all_rows(supabase):
    rows = []
    offset = 0
    while True:
        response = (
            supabase.table(TABLE)
            .select("*")
            .order("timestamp")
            .range(offset, offset + BATCH_SIZE - 1)
            .execute()
        )
        batch = response.data
        rows.extend(batch)
        print(f"  Fetched {len(rows)} rows so far...")
        if len(batch) < BATCH_SIZE:
            break
        offset += BATCH_SIZE
    return rows


def main():
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    print(f"Fetching all rows from {TABLE}...")
    rows = fetch_all_rows(supabase)
    print(f"Total rows fetched: {len(rows)}")

    if not rows:
        print("No rows found, skipping backup.")
        return

    Path("backups").mkdir(exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    output_path = Path("backups") / f"{TABLE}_{date_str}.csv"

    fieldnames = list(rows[0].keys())
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Backup saved to {output_path}")


if __name__ == "__main__":
    main()
