"""
fetch_filings.py
1. Fetches activist filings from SEC EDGAR RSS feeds
2. For each filing, calls Claude API to generate a 200-word brief
3. Saves everything to filings.json for the HTML page to read

Runs inside GitHub Actions every weekday morning.
ANTHROPIC_API_KEY is stored as a GitHub Secret - never in the code.
"""

import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import time
import os

# SEC requires a descriptive User-Agent
HEADERS = {
    "User-Agent": "ActivistTracker/1.0 research tool contact@example.com",
    "Accept-Encoding": "gzip, deflate",
}

EDGAR_RSS = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form_type}&dateb=&owner=include&count=100&search_text=&output=atom"
FORM_TYPES = ["SC 13D", "SC 13D/A", "DFAN14A"]
ATOM_NS = "http://www.w3.org/2005/Atom"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def fetch_rss(form_type):
    url = EDGAR_RSS.format(form_type=requests.utils.quote(form_type))
    print(f"  Fetching {form_type}...")
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
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

        # Clean company name - remove trailing CIK like (0001234567)
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
            "brief":       None,  # will be filled in below
        })

    return filings


def generate_brief(filing):
    """Call Claude API to generate a structured brief for a filing."""
    if not ANTHROPIC_API_KEY:
        print("  No API key — skipping brief generation")
        return None

    prompt = f"""You are a special situations research analyst. A new activist filing has appeared on SEC EDGAR.

Filing details:
- Form type: {filing['form_type']}
- Filed: {filing['file_date']}
- Name on filing: {filing['entity_name']}
- Filing URL: {filing['filing_url']}

Your job: figure out exactly what this filing is about.

Search the web to find:
1. Who is the activist investor (fund name, not just a person's name)
2. What company are they targeting
3. What stake size they have disclosed
4. What they are demanding

Then respond ONLY with a JSON object (no markdown, no explanation):
{{
  "activist": "Name of the activist fund or investor",
  "target_company": "Name of the company being targeted",
  "target_ticker": "Stock ticker if public, else empty string",
  "stake_size": "Percentage or dollar amount, else unknown",
  "demand": "What the activist is demanding in one sentence",
  "rationale": "Why they are targeting this company in one sentence",
  "watch_for": "What to watch for as the next catalyst",
  "confidence": "high or medium or low based on how much info you found"
}}"""

    try:
        resp = requests.post(
            ANTHROPIC_URL,
            headers={
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": ANTHROPIC_API_KEY,
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        # Extract text blocks from response
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

        # Find JSON object in response
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start == -1 or end == 0:
            print(f"  No JSON in response for {filing['entity_name']}")
            return None

        brief = json.loads(text[start:end])
        print(f"  Brief generated for {filing['entity_name']} — {brief.get('activist','?')} targeting {brief.get('target_company','?')}")
        return brief

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            print(f"  Rate limited — waiting 60s then retrying...")
            time.sleep(60)
            try:
                resp = requests.post(
                    ANTHROPIC_URL,
                    headers={
                        "Content-Type": "application/json",
                        "anthropic-version": "2023-06-01",
                        "x-api-key": ANTHROPIC_API_KEY,
                    },
                    json={
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 1000,
                        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
                start = text.find("{")
                end   = text.rfind("}") + 1
                if start == -1 or end == 0:
                    return None
                return json.loads(text[start:end])
            except Exception as e2:
                print(f"  Retry failed: {e2}")
                return None
        print(f"  Brief error for {filing['entity_name']}: {e}")
        return None
    except Exception as e:
        print(f"  Brief error for {filing['entity_name']}: {e}")
        return None


def fetch_all():
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

    print(f"\nGenerating briefs for {len(all_filings)} filings...")
    for i, f in enumerate(all_filings):
        print(f"  [{i+1}/{len(all_filings)}] {f['entity_name']}")
        f["brief"] = generate_brief(f)
        time.sleep(15)  # wait 15s between calls to respect rate limits

    output = {
        "fetched_at": today.strftime("%Y-%m-%d %H:%M UTC"),
        "date_from":  date_from,
        "date_to":    date_to,
        "total":      len(all_filings),
        "filings":    all_filings,
    }

    with open("filings.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nDone. {len(all_filings)} filings with briefs saved to filings.json.")


if __name__ == "__main__":
    fetch_all()
