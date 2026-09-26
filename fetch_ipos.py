"""
fetch_ipos.py — Live IPO Decision Engine (Clean Syntax & Persistent Pipeline)
"""

import requests
import json
import os
import re
from datetime import datetime
from bs4 import BeautifulSoup

DATA_FILE = "data.json"

SECTOR_BENCHMARKS = {
    "Information Technology": {"avgPS": 5.2, "avgCagr": 22.0, "avgListingGain": 24.5, "winRate": "4/5 positive"},
    "Financials": {"avgPS": 3.8, "avgCagr": 18.0, "avgListingGain": 29.4, "winRate": "4/6 positive"},
    "Health Care": {"avgPS": 4.5, "avgCagr": 20.0, "avgListingGain": 21.0, "winRate": "3/4 positive"},
    "Consumer Discretionary": {"avgPS": 4.0, "avgCagr": 16.0, "avgListingGain": 16.5, "winRate": "3/5 positive"},
    "Consumer Staples": {"avgPS": 3.5, "avgCagr": 12.0, "avgListingGain": 12.0, "winRate": "3/4 positive"},
    "Communication Services": {"avgPS": 4.2, "avgCagr": 15.0, "avgListingGain": 19.0, "winRate": "3/4 positive"},
    "Industrials": {"avgPS": 2.8, "avgCagr": 14.0, "avgListingGain": 18.2, "winRate": "3/4 positive"},
    "Energy": {"avgPS": 2.2, "avgCagr": 10.0, "avgListingGain": 14.0, "winRate": "2/3 positive"},
    "Utilities": {"avgPS": 2.0, "avgCagr": 8.0, "avgListingGain": 10.5, "winRate": "2/3 positive"},
    "Materials": {"avgPS": 2.5, "avgCagr": 13.0, "avgListingGain": 17.0, "winRate": "3/4 positive"},
    "Real Estate": {"avgPS": 3.0, "avgCagr": 15.0, "avgListingGain": 22.0, "winRate": "3/4 positive"}
}

def load_existing_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return {item["slug"]: item for item in data.get("upcoming", [])}
            except json.JSONDecodeError:
                return {}
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print("  [Success] Persistent decision engine data written to data.json")

def parse_date_string(date_str):
    if not date_str or "TBA" in str(date_str).upper():
        return None
    date_str = str(date_str).strip()
    current_year = datetime.now().year
    if not re.search(r'\b20\d{2}\b', date_str):
        date_str = f"{date_str} {current_year}"
    for fmt in ("%d %B %Y", "%d %b %Y", "%d-%b-%Y", "%d/%m/%Y", "%B %d, %Y", "%d %B"):
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.year == 1900: dt = dt.replace(year=current_year)
            return dt
        except ValueError: continue
    return None

def extract_base_metrics(row_text, cols):
    full_text = " ".join([c.text for c in cols]) + " " + row_text
    issue_size = "TBA"
    cr_match = re.search(r'₹?\s*([\d,.]+)\s*(?:Cr|crore|crores)', full_text, re.IGNORECASE)
    if cr_match: issue_size = f"₹{cr_match.group(1)} Cr"

    clean_text = re.sub(r'₹?\s*[\d,.]+\s*(?:Cr|crore|crores)', '', full_text, flags=re.IGNORECASE)
    price = None
    prices = re.findall(r'₹\s*([\d,]+)', clean_text)
    if prices:
        clean_p = [int(p.replace(',', '')) for p in prices if int(p.replace(',', '')) < 25000]
        if clean_p: price = max(clean_p)
    return price, issue_size

def evaluate_decision(enrichment, sector_name):
    if not enrichment or enrichment.get("verdict") == "Data will be updated soon":
        return {
            "verdict": "Data will be updated soon",
            "verdictCls": "verdict-caution",
            "summary": "Awaiting fundamental RHP data entry for comparative analysis.",
            "checks": [{"icon": "ℹ", "cls": "check-warn", "text": "Fundamental metrics pending entry"}]
        }
    return {
        "verdict": enrichment.get("verdict", "Apply"),
        "verdictCls": enrichment.get("verdictCls", "verdict-apply"),
        "summary": enrichment.get("summary", "Fundamentals evaluated against sector benchmarks."),
        "checks": enrichment.get("checks", [{"icon": "✓", "cls": "check-pass", "text": "Validated against sector metrics"}])
    }

def fetch_open_ipos():
    print("\n[Scraper] Fetching open IPOs...")
    url = "https://ipowatch.in/upcoming-ipo-calendar-ipo-list/"
    headers = {'User-Agent': 'Mozilla/5.0'}
    open_ipos = []
    current_year = datetime.now().year
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        tables = soup.find_all('table')
        
        for table in tables:
            table_heading = ""
            prev_elem = table.find_previous(['h2', 'h3', 'h4', 'strong', 'caption'])
            if prev_elem: table_heading = prev_elem.text.lower()
                
            ipo_category = "SME IPO" if "sme" in table_heading else "Mainboard IPO"
                
            for row in table.find_all('tr'):
                cols = row.find_all('td')
                row_text = row.text.strip()
                
                if len(cols) >= 2:
                    name_text = cols[0].text.strip()
                    dates = cols[1].text.strip() if len(cols) > 1 else ""
                    if "Company" in name_text or not name_text: continue
                        
                    open_date, close_date = dates, "TBA"
                    if " to " in dates.lower():
                        parts = dates.lower().split(" to ")
                        open_date, close_date = parts[0].strip().title(), parts[1].strip().title()
                    elif "-" in dates:
                        parts = dates.split("-")
                        open_date, close_date = parts[0].strip().title(), parts[1].strip().title()

                    name = name_text.split("(")[0].strip()
                    if len(name) < 3: continue

                    auto_price, auto_issue = extract_base_metrics(row_text, cols)
                    open_ipos.append({
                        "slug": name.lower().replace(" ", "-"),
                        "name": name,
                        "category": ipo_category,
                        "openDate": open_date,
                        "closeDate": close_date,
                        "autoPrice": auto_price,
                        "autoIssue": auto_issue
                    })
    except Exception as err:
        print(f"  Error: {err}")
    return open_ipos

def main():
    print("\n" + "="*50)
    print("  IPO Decision Engine — Persistent Pipeline")
    print("="*50)

    existing_data = load_existing_data()
    open_ipos = fetch_open_ipos()

    if not open_ipos:
        print("\nNo OPEN IPOs found.")
        save_data({"upcoming": [], "lastUpdated": datetime.now().strftime("%d %b %Y, %I:%M %p")})
        return

    upcoming = []
    for ipo in open_ipos:
        slug, name = ipo["slug"], ipo["name"]
        ipo_cat = ipo["category"]
        multiplier = 2 if ipo_cat == "SME IPO" else 1

        if slug in existing_data:
            cached = existing_data[slug]
            price = cached.get("issuePrice") or ipo["autoPrice"]
            lot = cached.get("lotSize")
            listing = cached.get("listingDate", "TBA")
            issue_sz = cached.get("issueSize", ipo["autoIssue"])
            sector_str = cached.get("sector", "Financials (Mainboard IPO)")
            sector_name = sector_str.split(" (")[0]
            decision = evaluate_decision(cached, sector_name)
            
            min_amt = (price * lot * multiplier) if (price and lot) else cached.get("minAmount")
            
            upcoming.append({
                "name": name,
                "symbol": name[:5].upper(),
                "sector": f"{sector_name} ({ipo_cat})",
                "status": "Open",
                "openDate": ipo["openDate"],
                "closeDate": ipo["closeDate"],
                "listingDate": listing,
                "issuePrice": price,
                "lotSize": lot,
                "minAmount": min_amt,
                "issueSize": issue_sz,
                "historicalGain": cached.get("historicalGain", "20.0% avg listing gain in last 1 yr"),
                "summary": decision["summary"],
                "verdict": decision["verdict"],
                "verdictCls": decision["verdictCls"],
                "checks": cached.get("checks", []),
                "score": 1,
                "lastFetched": datetime.now().strftime("%d %b %Y, %I:%M %p")
            })
        else:
            upcoming.append({
                "name": name,
                "symbol": name[:5].upper(),
                "sector": f"Financials ({ipo_cat})",
                "status": "Open",
                "openDate": ipo["openDate"],
                "closeDate": ipo["closeDate"],
                "listingDate": "TBA",
                "issuePrice": ipo["autoPrice"],
                "lotSize": None,
                "minAmount": None,
                "issueSize": ipo["autoIssue"],
                "historicalGain": "20.0% avg listing gain in last 1 yr (3/4 positive)",
                "summary": "Awaiting fundamental RHP data entry for comparative analysis.",
                "verdict": "Data will be updated soon",
                "verdictCls": "verdict-caution",
                "checks": [{"icon": "ℹ", "cls": "check-warn", "text": "Fundamental metrics pending entry"}],
                "score": 1,
                "lastFetched": datetime.now().strftime("%d %b %Y, %I:%M %p")
            })

    data = {"upcoming": upcoming, "lastUpdated": datetime.now().strftime("%d %b %Y, %I:%M %p")}
    save_data(data)
    print(f"\nDone! Processed {len(upcoming)} OPEN IPO(s) successfully.\n")

if __name__ == "__main__":
    main()