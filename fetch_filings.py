"""
fetch_filings.py
1. Fetches activist filings from SEC EDGAR RSS feeds
2. Generates a brief for each filing using Claude API
3. Saves to filings.json for the HTML page
4. Sends a daily email digest

ANTHROPIC_API_KEY, GMAIL_USER, GMAIL_APP_PASSWORD, EMAIL_RECIPIENT
are all stored as GitHub Secrets - never in this file.
"""

import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import time
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── CONFIG ────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": "ActivistTracker/1.0 research tool contact@example.com",
    "Accept-Encoding": "gzip, deflate",
}

EDGAR_RSS    = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form_type}&dateb=&owner=include&count=100&search_text=&output=atom"
FORM_TYPES   = ["SC 13D", "SC 13D/A", "DFAN14A"]
ATOM_NS      = "http://www.w3.org/2005/Atom"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
GMAIL_USER         = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
EMAIL_RECIPIENT    = os.environ.get("EMAIL_RECIPIENT", "")


# ── EDGAR FETCH ───────────────────────────────────────────────────

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

    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        title   = entry.findtext(f"{{{ATOM_NS}}}title", "").strip()
        updated = entry.findtext(f"{{{ATOM_NS}}}updated", "").strip()
        link_el = entry.find(f"{{{ATOM_NS}}}link")
        link    = link_el.get("href", "") if link_el is not None else ""

        file_date = updated[:10] if updated else ""
        if file_date < date_from or file_date > date_to:
            continue

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
            "brief":       None,
        })

    return filings


# ── BRIEF GENERATION ─────────────────────────────────────────────

def call_claude(prompt, retries=3):
    """Call Claude API with retry on rate limit."""
    for attempt in range(retries):
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
            if resp.status_code == 429:
                wait = 60 * (attempt + 1)
                print(f"  Rate limited — waiting {wait}s (attempt {attempt+1}/{retries})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if attempt < retries - 1:
                time.sleep(30)
                continue
            raise
    return None


def generate_brief(filing):
    if not ANTHROPIC_API_KEY:
        return None

    prompt = f"""You are a special situations research analyst. A new activist filing appeared on SEC EDGAR.

Filing:
- Form: {filing['form_type']}
- Filed: {filing['file_date']}
- Name on filing: {filing['entity_name']}
- URL: {filing['filing_url']}

Search the web to find: who is the activist fund, what company they are targeting, stake size, and what they are demanding.

Respond ONLY with JSON (no markdown):
{{
  "activist": "fund name",
  "target_company": "company being targeted",
  "target_ticker": "ticker or empty string",
  "stake_size": "% or $ amount or unknown",
  "demand": "what they want in one sentence",
  "rationale": "why this company in one sentence",
  "watch_for": "next catalyst to watch",
  "confidence": "high or medium or low"
}}"""

    try:
        data = call_claude(prompt)
        if not data:
            return None
        text  = "".join(b.get("text","") for b in data.get("content",[]) if b.get("type")=="text")
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        brief = json.loads(text[start:end])
        print(f"  Brief: {brief.get('activist','?')} → {brief.get('target_company','?')}")
        return brief
    except Exception as e:
        print(f"  Brief error: {e}")
        return None


# ── EMAIL ─────────────────────────────────────────────────────────

def build_email_html(filings, date_str, new_only):
    """Build a clean HTML email digest."""

    def form_color(ft):
        if ft == "SC 13D":   return "#f0a500"
        if ft == "SC 13D/A": return "#7a7870"
        return "#e05252"

    rows = ""
    shown_divider = False
    shown_older_divider = False
    for f in filings:
        is_today = f.get("file_date") == datetime.today().strftime("%Y-%m-%d")
        if is_today and not shown_divider:
            rows += '<div style="font-size:10px;color:#f0a500;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:12px;font-family:monospace;">— Today —</div>'
            shown_divider = True
        if not is_today and not shown_older_divider:
            rows += '<div style="font-size:10px;color:#7a7870;letter-spacing:0.1em;text-transform:uppercase;margin:20px 0 12px;font-family:monospace;">— Previous 30 days —</div>'
            shown_older_divider = True
        b = f.get("brief") or {}
        company  = b.get("target_company") or f["entity_name"]
        ticker   = b.get("target_ticker","")
        activist = b.get("activist","—")
        demand   = b.get("demand","Brief not available")
        rationale= b.get("rationale","")
        watch    = b.get("watch_for","")
        stake    = b.get("stake_size","")
        conf     = b.get("confidence","")
        ft       = f["form_type"]
        fd       = f["file_date"]
        url      = f.get("filing_url","#")

        conf_color = "#3dba6e" if conf=="high" else "#e05252" if conf=="low" else "#f0a500"

        rows += f"""
        <div style="border:1px solid #1e232b;border-radius:4px;padding:20px;margin-bottom:16px;background:#111418;">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap;">
                <span style="font-size:18px;font-weight:500;color:#e8e6e0;">{company}</span>
                {"<span style='font-family:monospace;font-size:12px;color:#f0a500;background:#1e232b;padding:2px 8px;border-radius:2px;'>"+ticker+"</span>" if ticker else ""}
                <span style="font-family:monospace;font-size:11px;padding:2px 8px;border-radius:2px;background:#1e232b;color:{form_color(ft)};">{ft}</span>
                <span style="font-size:11px;color:#7a7870;">{fd}</span>
            </div>
            <div style="margin-bottom:8px;">
                <span style="font-size:11px;color:#7a7870;text-transform:uppercase;letter-spacing:0.08em;">Activist</span>
                <span style="font-size:13px;color:#e8e6e0;margin-left:8px;">{activist}</span>
                {"<span style='font-size:11px;color:#7a7870;margin-left:12px;'>Stake: "+stake+"</span>" if stake and stake != "unknown" else ""}
            </div>
            {"<div style='margin-bottom:8px;'><span style='font-size:11px;color:#7a7870;text-transform:uppercase;letter-spacing:0.08em;'>Demand</span><div style='font-size:13px;color:#e8e6e0;margin-top:4px;line-height:1.6;'>"+demand+"</div></div>" if demand else ""}
            {"<div style='margin-bottom:8px;'><span style='font-size:11px;color:#7a7870;text-transform:uppercase;letter-spacing:0.08em;'>Rationale</span><div style='font-size:13px;color:#e8e6e0;margin-top:4px;line-height:1.6;'>"+rationale+"</div></div>" if rationale else ""}
            {"<div style='margin-bottom:12px;'><span style='font-size:11px;color:#7a7870;text-transform:uppercase;letter-spacing:0.08em;'>Watch for</span><div style='font-size:13px;color:#e8e6e0;margin-top:4px;line-height:1.6;'>"+watch+"</div></div>" if watch else ""}
            <a href="{url}" style="font-family:monospace;font-size:11px;color:#5b9cf6;text-decoration:none;border:1px solid rgba(91,156,246,0.3);padding:4px 12px;border-radius:2px;">↗ View SEC filing</a>
            {"<span style='font-size:10px;color:"+conf_color+";margin-left:12px;'>"+conf+" confidence</span>" if conf else ""}
        </div>"""

    count_label = f"{len(filings)} new filing{'s' if len(filings)!=1 else ''} today" if new_only else f"{len(filings)} filing{'s' if len(filings)!=1 else ''} — last 30 days"

    return f"""
<!DOCTYPE html>
<html>
<body style="background:#0a0c0f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px;max-width:700px;margin:0 auto;">
    <div style="border-bottom:1px solid #1e232b;padding-bottom:16px;margin-bottom:24px;">
        <div style="font-family:monospace;font-size:16px;font-weight:500;color:#f0a500;letter-spacing:0.06em;">ACTIVIST // TRACKER</div>
        <div style="font-size:11px;color:#7a7870;letter-spacing:0.1em;text-transform:uppercase;margin-top:2px;">{date_str} &nbsp;·&nbsp; {count_label}</div>
    </div>
    {rows if rows else '<div style="color:#7a7870;font-size:13px;">No new activist filings today.</div>'}
    <div style="border-top:1px solid #1e232b;margin-top:24px;padding-top:12px;font-size:10px;color:#4a4840;">
        Powered by SEC EDGAR public data · Briefs generated by Claude AI
    </div>
</body>
</html>"""


def send_email(filings, date_str, new_only):
    if not all([GMAIL_USER, GMAIL_APP_PASSWORD, EMAIL_RECIPIENT]):
        print("Email credentials not configured — skipping email")
        return

    today_filings = [f for f in filings if f["file_date"] == date_str] if new_only else filings
    count = len(today_filings)
    subject = f"Activist Tracker — {count} new filing{'s' if count!=1 else ''} today ({date_str})" if new_only else f"Activist Tracker — {date_str}"

    # Sort: today's filings first, then rest by date descending
    sorted_filings = sorted(filings, key=lambda x: (x['file_date'] != date_str, x['file_date']), reverse=False)
    sorted_filings = sorted(sorted_filings, key=lambda x: (0 if x['file_date']==date_str else 1, x['file_date']), reverse=False)
    html = build_email_html(sorted_filings, date_str, new_only)

    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = GMAIL_USER
        msg["To"]      = EMAIL_RECIPIENT
        msg["Subject"] = subject
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)

        print(f"Email sent to {EMAIL_RECIPIENT} — {count} filings")
    except Exception as e:
        print(f"Email error: {e}")


# ── MAIN ──────────────────────────────────────────────────────────

def fetch_all():
    today     = datetime.today()
    date_str  = today.strftime("%Y-%m-%d")
    date_to   = date_str
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
        time.sleep(15)

    # Save JSON for the HTML page
    output = {
        "fetched_at": today.strftime("%Y-%m-%d %H:%M UTC"),
        "date_from":  date_from,
        "date_to":    date_to,
        "total":      len(all_filings),
        "filings":    all_filings,
    }
    with open("filings.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved {len(all_filings)} filings to filings.json.")

    # Send email with today's filings
    print("\nSending email digest...")
    send_email(all_filings, date_str, new_only=False)


if __name__ == "__main__":
    fetch_all()
