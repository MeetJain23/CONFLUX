"""
Diagnostic: hit candidate NSE PIT JSON endpoints, dump first response
to figure out actual URL + shape.

Not committed; gitignored under scripts/test_*.py style.
"""

import json
import logging
from datetime import date, timedelta

from ingestion.nse_session import NSESession, NSESessionError

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Candidate endpoints — NSE's URL patterns are inconsistent
CANDIDATES = [
    "https://www.nseindia.com/api/corporates-pit?index=equities",
    "https://www.nseindia.com/api/corporates-pit?index=equities&from_date=01-04-2026&to_date=27-06-2026",
    "https://www.nseindia.com/api/corporates-insidertrading?index=equities",
    "https://www.nseindia.com/api/corporates-corporateActions?index=equities&category=insider",
]


def main():
    to_d = date.today()
    from_d = to_d - timedelta(days=30)
    from_str = from_d.strftime("%d-%m-%Y")
    to_str = to_d.strftime("%d-%m-%Y")

    with NSESession() as nse:
        for base_url in CANDIDATES:
            url = base_url
            if "from_date" not in url:
                sep = "&" if "?" in url else "?"
                url = f"{base_url}{sep}from_date={from_str}&to_date={to_str}"

            print(f"\n{'='*80}\nTrying: {url}\n{'='*80}")
            try:
                data = nse.fetch_json(url)
            except NSESessionError as e:
                print(f"FAILED: {e}")
                continue

            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("data", data.get("rows", []))
            else:
                print(f"Unexpected response type: {type(data).__name__}")
                print(f"Raw: {repr(data)[:500]}")
                continue

            print(f"Returned {len(items)} items")
            if items:
                first = items[0]
                print(f"\nFirst item keys: {list(first.keys())}")
                print(f"\nFirst item (full):")
                print(json.dumps(first, indent=2, default=str))
                if len(items) > 1:
                    print(f"\nSecond item keys (sanity-check schema consistency): "
                          f"{list(items[1].keys())}")
                # Found a working endpoint — stop here
                print(f"\n*** WORKING ENDPOINT: {url} ***")
                break
            else:
                print("0 items — endpoint exists but returned empty. "
                      "Could be wrong category or genuinely empty window.")


if __name__ == "__main__":
    main()