"""
fetch_filings.py
Fetches activist filings from SEC EDGAR using their official RSS feeds.
Runs inside GitHub Actions every weekday morning.
Saves results to filings.json for the HTML page to read.
"""

import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import time

# SEC requires a descriptive User-Agent
HEADERS = {
    "User-Agent": "ActivistTracker/1.0 research tool contact@example.com",
    "Accept-Encoding": "gzip, deflate",
}

# EDGAR RSS feed — returns most recent filings by form type
EDGAR_RSS = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form_type}&dateb=&owner=include&count=100&search_text=&output=atom"

FORM_TYPES = ["SC 13D", "SC 13D/A", "DFAN14A"]

ATOM_NS = "http://www.w3.org/2005/Atom"


def fetch_rss(form_type):
    url = EDGAR_RSS.format(form_type=requests.utils.quote(form_type))
    print(f"  Fetching {form_type} from: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        print(f"  HTTP {r.status_code}, content length: {len(r.text)}")
        return r.text
    except Exception as e:
        print(f"  Error: {e}")
        return None


def parse_rss(xml_text, form_type, date_from, date_to):
    if not xml_text:
        return []

    filings = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  XML parse error: {e}")
        print(f"  First 500 chars: {xml_text[:500]}")
        return []

    entries = root.findall(f"{{{ATOM_NS}}}entry")
    print(f"  Total entries in feed: {len(entries)}")

    for entry in entries:
        title   = entry.findtext(f"{{{ATOM_NS}}}title", "").strip()
        updated = entry.findtext(f"{{{ATOM_NS}}}updated", "").strip()
        link_el = entry.find(f"{{{ATOM_NS}}}link")
        link    = link_el.get("href", "") if link_el is not None else ""

        file_date = updated[:10] if updated else ""

        if file_date < date_from or file_date > date_to:
            continue

        # Title format: "SC 13D - COMPANY NAME (CIK) (ACCESSION)"
        company_name = title
        if " - " in title:
            company_name = title.split(" - ", 1)[1]
            if "(" in company_name:
                company_name = company_name[:company_name.rfind("(")].strip()

        filings.append({
            "file_date":   file_date,
            "form_type":   form_type,
            "entity_name": company_name,
            "filer_names": [],
            "filing_url":  link,
        })

    return filings


def fetch_all_filings():
    today     = datetime.today()
    date_to   = today.strftime("%Y-%m-%d")
    date_from = (today - timedelta(days=30)).strftime("%Y-%m-%d")

    print(f"Fetching activist filings: {date_from} to {date_to}")

    all_filings = []

    for form_type in FORM_TYPES:
        xml_text = fetch_rss(form_type)
        filings  = parse_rss(xml_text, form_type, date_from, date_to)
        print(f"  {len(filings)} {form_type} filings in range")
        all_filings.extend(filings)
        time.sleep(0.5)

    all_filings.sort(key=lambda x: x["file_date"], reverse=True)

    output = {
        "fetched_at": today.strftime("%Y-%m-%d %H:%M UTC"),
        "date_from":  date_from,
        "date_to":    date_to,
        "total":      len(all_filings),
        "filings":    all_filings,
    }

    with open("filings.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nDone. {len(all_filings)} filings saved to filings.json.")


if __name__ == "__main__":
    fetch_all_filings()
