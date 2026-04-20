"""
fetch_filings.py
Runs inside GitHub Actions every morning.
Fetches the last 30 days of activist filings from SEC EDGAR
and saves them to filings.json for the HTML page to read.
"""

import requests
import json
from datetime import datetime, timedelta

# SEC requires a descriptive User-Agent — this is their published policy
HEADERS = {
    "User-Agent": "ActivistTracker/1.0 research tool github.com/ssanghvi-maker/activist-tracker"
}

EDGAR_URL = "https://efts.sec.gov/LATEST/search-index"

# Filing types to track
# SC 13D   = new activist stake (>5% with intent to influence)
# SC 13D/A = amendment — demand letters, updated stakes, escalations
# DFAN14A  = proxy solicitation — board letters, public campaigns
FORMS = "SC 13D,SC 13D/A,DFAN14A"

def fetch_filings():
    today = datetime.today()
    thirty_days_ago = today - timedelta(days=30)

    date_to   = today.strftime("%Y-%m-%d")
    date_from = thirty_days_ago.strftime("%Y-%m-%d")

    all_filings = []
    page = 0
    page_size = 40

    print(f"Fetching filings from {date_from} to {date_to}...")

    while True:
        params = {
            "q": "",
            "forms": FORMS,
            "dateRange": "custom",
            "startdt": date_from,
            "enddt": date_to,
            "from": page * page_size,
        }

        try:
            resp = requests.get(EDGAR_URL, params=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"Error fetching page {page}: {e}")
            break

        hits = data.get("hits", {}).get("hits", [])
        total = data.get("hits", {}).get("total", {}).get("value", 0)

        if not hits:
            break

        for h in hits:
            s = h.get("_source", {})
            accession_no = s.get("accession_no", "")
            display_names = s.get("display_names", [])

            all_filings.append({
                "file_date":     s.get("file_date", ""),
                "form_type":     s.get("form_type", ""),
                "entity_name":   s.get("entity_name", ""),
                "filer_names":   [d.get("name", "") for d in display_names],
                "accession_no":  accession_no,
                "filing_url":    build_filing_url(accession_no, display_names),
            })

        print(f"  Page {page+1}: got {len(hits)} filings (total: {total})")

        # Stop if we have all results
        if (page + 1) * page_size >= total:
            break

        # SEC rate limit — be polite
        import time
        time.sleep(0.2)
        page += 1

    # Save to filings.json
    output = {
        "fetched_at": today.strftime("%Y-%m-%d %H:%M UTC"),
        "date_from":  date_from,
        "date_to":    date_to,
        "total":      len(all_filings),
        "filings":    all_filings,
    }

    with open("filings.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nDone. Saved {len(all_filings)} filings to filings.json.")


def build_filing_url(accession_no, display_names):
    """Build direct link to filing index page on EDGAR."""
    try:
        cik = display_names[0].get("id", "").lstrip("0")
        acc_nodash = accession_no.replace("-", "")
        if cik and acc_nodash:
            return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{accession_no}-index.htm"
    except Exception:
        pass
    return "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=SC+13D&dateb=&owner=include&count=40"


if __name__ == "__main__":
    fetch_filings()
