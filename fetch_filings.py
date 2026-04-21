"""
fetch_filings.py
Fetches activist filings from SEC EDGAR RSS feeds.
Generates briefs ONLY for today's new filings using Claude (no web search, no retries).
Sends daily email digest.
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

HEADERS = {
    "User-Agent": "ActivistTracker/1.0 research tool contact@example.com",
    "Accept-Encoding": "gzip, deflate",
}

EDGAR_RSS     = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form_type}&dateb=&owner=include&count=100&search_text=&output=atom"
FORM_TYPES    = ["SC 13D", "SC 13D/A", "DFAN14A"]
ATOM_NS       = "http://www.w3.org/2005/Atom"
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
# Only called for TODAY's filings. No retries. No web search.

def generate_brief(filing):
    if not ANTHROPIC_API_KEY:
        return None

    prompt = f"""Activist filing on SEC EDGAR:
- Form: {filing['form_type']}
- Filed: {filing['file_date']}
- Entity: {filing['entity_name']}
- URL: {filing['filing_url']}

Based on what you know, identify: who is the activist, what company are they targeting, what are they demanding.

Respond ONLY with JSON:
{{"activist":"fund name","target_company":"company name","target_ticker":"ticker or empty","stake_size":"% or unknown","demand":"one sentence","rationale":"one sentence","watch_for":"one sentence","confidence":"high or medium or low"}}"""

    try:
        resp = requests.post(
            ANTHROPIC_URL,
            headers={
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": ANTHROPIC_API_KEY,
            },
            json={
                "model": "claude-haiku-4-5-20251001",  # cheapest model
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )

        # If rate limited — skip, don't retry
        if resp.status_code == 429:
            print(f"  Rate limited — skipping brief for {filing['entity_name']}")
            return None

        resp.raise_for_status()
        data = resp.json()
        text  = "".join(b.get("text","") for b in data.get("content",[]) if b.get("type")=="text")
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        brief = json.loads(text[start:end])
        print(f"  Brief: {brief.get('activist','?')} → {brief.get('target_company','?')}")
        return brief

    except Exception as e:
        print(f"  Brief error for {filing['entity_name']}: {e}")
        return None


# ── EMAIL ─────────────────────────────────────────────────────────

def build_email_html(all_filings, today_str):
    def form_color(ft):
        if ft == "SC 13D":   return "#f0a500"
        if ft == "SC 13D/A": return "#7a7870"
        return "#e05252"

    def filing_card(f, is_new):
        b = f.get("brief") or {}
        company  = b.get("target_company") or f["entity_name"]
        ticker   = b.get("target_ticker","")
        activist = b.get("activist","—")
        demand   = b.get("demand","")
        rationale= b.get("rationale","")
        watch    = b.get("watch_for","")
        stake    = b.get("stake_size","")
        conf     = b.get("confidence","")
        ft       = f["form_type"]
        fd       = f["file_date"]
        url      = f.get("filing_url","#")
        conf_color = "#3dba6e" if conf=="high" else "#e05252" if conf=="low" else "#f0a500"
        border = "border-left:3px solid #f0a500;" if is_new else ""

        return f"""<div style="border:1px solid #1e232b;{border}border-radius:4px;padding:18px;margin-bottom:14px;background:#111418;">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap;">
    <span style="font-size:17px;font-weight:500;color:#e8e6e0;">{company}</span>
    {"<span style='font-family:monospace;font-size:11px;color:#f0a500;background:#1e232b;padding:2px 8px;border-radius:2px;'>"+ticker+"</span>" if ticker else ""}
    <span style="font-family:monospace;font-size:10px;padding:2px 7px;border-radius:2px;background:#1e232b;color:{form_color(ft)};">{ft}</span>
    <span style="font-size:11px;color:#7a7870;">{fd}</span>
    {"<span style='font-size:10px;color:#f0a500;background:rgba(240,165,0,0.1);padding:2px 8px;border-radius:2px;font-family:monospace;'>NEW TODAY</span>" if is_new else ""}
  </div>
  <div style="margin-bottom:6px;font-size:12px;color:#7a7870;">Activist: <span style="color:#e8e6e0;">{activist}</span>{"  ·  Stake: "+stake if stake and stake!="unknown" else ""}</div>
  {"<div style='margin-bottom:6px;font-size:12px;color:#e8e6e0;line-height:1.6;'><span style=color:#7a7870;>Demand: </span>"+demand+"</div>" if demand else ""}
  {"<div style='margin-bottom:6px;font-size:12px;color:#e8e6e0;line-height:1.6;'><span style=color:#7a7870;>Rationale: </span>"+rationale+"</div>" if rationale else ""}
  {"<div style='margin-bottom:10px;font-size:12px;color:#e8e6e0;line-height:1.6;'><span style=color:#7a7870;>Watch for: </span>"+watch+"</div>" if watch else ""}
  <a href="{url}" style="font-family:monospace;font-size:11px;color:#5b9cf6;text-decoration:none;border:1px solid rgba(91,156,246,0.3);padding:3px 10px;border-radius:2px;">↗ View SEC filing</a>
  {"<span style='font-size:10px;color:"+conf_color+";margin-left:10px;'>"+conf+" confidence</span>" if conf else ""}
</div>"""

    today_filings = [f for f in all_filings if f["file_date"] == today_str]
    older_filings = [f for f in all_filings if f["file_date"] != today_str]

    body = ""
    if today_filings:
        body += f'<div style="font-size:10px;color:#f0a500;letter-spacing:.1em;text-transform:uppercase;margin-bottom:10px;font-family:monospace;">— {len(today_filings)} new filing{"s" if len(today_filings)!=1 else ""} today —</div>'
        for f in today_filings:
            body += filing_card(f, True)

    if older_filings:
        body += '<div style="font-size:10px;color:#7a7870;letter-spacing:.1em;text-transform:uppercase;margin:20px 0 10px;font-family:monospace;">— previous 30 days —</div>'
        for f in older_filings:
            body += filing_card(f, False)

    if not body:
        body = '<div style="color:#7a7870;font-size:13px;">No activist filings in the last 30 days.</div>'

    return f"""<!DOCTYPE html>
<html><body style="background:#0a0c0f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px;max-width:680px;margin:0 auto;">
  <div style="border-bottom:1px solid #1e232b;padding-bottom:14px;margin-bottom:20px;">
    <div style="font-family:monospace;font-size:15px;font-weight:500;color:#f0a500;letter-spacing:.06em;">ACTIVIST // TRACKER</div>
    <div style="font-size:10px;color:#7a7870;letter-spacing:.1em;text-transform:uppercase;margin-top:2px;">{today_str} · {len(all_filings)} filing{"s" if len(all_filings)!=1 else ""} · last 30 days</div>
  </div>
  {body}
  <div style="border-top:1px solid #1e232b;margin-top:20px;padding-top:10px;font-size:10px;color:#4a4840;">
    Powered by SEC EDGAR public data · Briefs by Claude AI (knowledge only — no web search)
  </div>
</body></html>"""


def send_email(all_filings, today_str):
    if not all([GMAIL_USER, GMAIL_APP_PASSWORD, EMAIL_RECIPIENT]):
        print("Email not configured — skipping")
        return

    today_count = len([f for f in all_filings if f["file_date"] == today_str])
    subject = f"Activist Tracker — {today_count} new today, {len(all_filings)} total — {today_str}"
    html = build_email_html(all_filings, today_str)

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

        print(f"Email sent to {EMAIL_RECIPIENT} ({today_count} new, {len(all_filings)} total)")
    except Exception as e:
        print(f"Email error: {e}")


# ── MAIN ──────────────────────────────────────────────────────────

def fetch_all():
    today     = datetime.today()
    today_str = today.strftime("%Y-%m-%d")
    date_from = (today - timedelta(days=30)).strftime("%Y-%m-%d")

    print(f"Fetching filings: {date_from} to {today_str}")

    all_filings = []
    for form_type in FORM_TYPES:
        xml_text = fetch_rss(form_type)
        filings  = parse_rss(xml_text, form_type, date_from, today_str)
        print(f"  {len(filings)} {form_type} filings")
        all_filings.extend(filings)
        time.sleep(0.5)

    all_filings.sort(key=lambda x: x["file_date"], reverse=True)

    # Only generate briefs for TODAY's new filings — saves cost
    today_filings = [f for f in all_filings if f["file_date"] == today_str]
    print(f"\nGenerating briefs for {len(today_filings)} new filings today...")

    for f in today_filings:
        print(f"  {f['entity_name']}")
        f["brief"] = generate_brief(f)
        time.sleep(5)  # 5 second gap between calls

    # Save JSON
    output = {
        "fetched_at": today.strftime("%Y-%m-%d %H:%M UTC"),
        "date_from":  date_from,
        "date_to":    today_str,
        "total":      len(all_filings),
        "filings":    all_filings,
    }
    with open("filings.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved {len(all_filings)} filings to filings.json.")

    # Send email
    print("Sending email...")
    send_email(all_filings, today_str)


if __name__ == "__main__":
    fetch_all()
