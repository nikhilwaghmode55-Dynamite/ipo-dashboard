"""
fetch_ipos.py — Live IPO Decision Engine & Precision CLI Input
Run this script locally: python fetch_ipos.py
"""

import requests
import json
import os
import re
from datetime import datetime
from bs4 import BeautifulSoup

CACHE_FILE = "ipo_cache.json"
DATA_FILE  = "data.json"

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

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            try: return json.load(f)
            except json.JSONDecodeError: return {}
    return {}

def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print("  [Success] Precision data written to data.json")

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

def is_currently_open(open_str, close_str):
    today = datetime.now().date()
    open_dt = parse_date_string(open_str)
    close_dt = parse_date_string(close_str)
    if "nse" in str(open_str).lower(): return False
    if open_dt and close_dt:
        return open_dt.date() <= today <= close_dt.date()
    elif open_dt:
        return 0 <= (today - open_dt.date()).days <= 7
    return False

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

def prompt_user_data(name, auto_price, auto_issue):
    """Interactive CLI prompt for accurate financial metrics and fundamentals."""
    print(f"\n" + "="*50)
    print(f" [Precision Entry] Configure IPO: {name}")
    print(f"="*50)
    
    try:
        price_input = input(f"Cut-off Price [Detected: {auto_price if auto_price else 'None'}]: ").strip()
        cut_off_price = float(price_input) if price_input else auto_price

        lot_size = int(input("Exact Lot Size (e.g., 468, 107, 49, 4000): ").strip())
        listing_date = input("Listing Date (e.g., 05 Oct 2026 or TBA): ").strip() or "TBA"

        print("Select Sector:")
        sectors = list(SECTOR_BENCHMARKS.keys())
        for idx, sec in enumerate(sectors, 1):
            print(f"  {idx}. {sec}")
        sec_choice = int(input(f"Enter choice (1-{len(sectors)}): ").strip())
        sector = sectors[sec_choice - 1]

        ps_ratio = float(input("Current FY P/S Ratio: ").strip())
        cagr = float(input("3-Year Revenue CAGR (%): ").strip())
        proceeds = input("Use of Proceeds (Capex, Debt Repayment, OFS, etc.): ").strip()
        promoter = float(input("Post-IPO Promoter Holding (%): ").strip())

        return {
            "sector": sector,
            "cutOffPrice": cut_off_price,
            "lotSize": lot_size,
            "listingDate": listing_date,
            "issueSize": auto_issue,
            "psRatio": ps_ratio,
            "cagr3Yr": cagr,
            "proceedsUse": proceeds,
            "promoterPct": promoter
        }
    except Exception as e:
        print(f"  [Error] Invalid input encountered: {e}. Skipping configuration.")
        return None

def evaluate_decision(enrichment, sector_name):
    if not enrichment:
        return {
            "verdict": "Data will be updated soon",
            "verdictCls": "verdict-caution",
            "summary": "Awaiting fundamental RHP data entry for comparative analysis.",
            "checks": [{"icon": "ℹ", "cls": "check-warn", "text": "Fundamental metrics pending entry"}]
        }

    bench = SECTOR_BENCHMARKS.get(sector_name, {"avgPS": 3.5, "avgCagr": 15.0})
    ps = enrichment["psRatio"]
    cagr = enrichment["cagr3Yr"]
    promoter = enrichment["promoterPct"]
    proceeds = enrichment["proceedsUse"].lower()

    score = 0
    checks = []

    if ps <= bench["avgPS"]:
        checks.append({"icon": "✓", "cls": "check-pass", "text": f"Attractive P/S valuation ({ps}x vs sector avg {bench['avgPS']}x)"})
        score += 2
    else:
        checks.append({"icon": "✗", "cls": "check-fail", "text": f"High P/S valuation ({ps}x vs sector avg {bench['avgPS']}x)"})
        score -= 1

    if cagr >= bench["avgCagr"]:
        checks.append({"icon": "✓", "cls": "check-pass", "text": f"Robust growth ({cagr}% vs sector avg {bench['avgCagr']}%)"})
        score += 2
    else:
        checks.append({"icon": "✗", "cls": "check-fail", "text": f"Slow revenue growth ({cagr}%)"})
        score -= 1

    if promoter >= 50:
        checks.append({"icon": "✓", "cls": "check-pass", "text": f"High promoter confidence ({promoter}% holding)"})
        score += 1
    else:
        checks.append({"icon": "✗", "cls": "check-fail", "text": f"Low promoter holding ({promoter}%)"})
        score -= 1

    if any(k in proceeds for k in ["capex", "growth", "working capital", "r&d"]):
        checks.append({"icon": "✓", "cls": "check-pass", "text": f"Proceeds deployed for business expansion ({proceeds})"})
        score += 1
    else:
        checks.append({"icon": "✗", "cls": "check-fail", "text": f"Proceeds for debt repayment / OFS ({proceeds})"})
        score -= 1

    if score >= 3:
        verdict, cls = "Apply", "verdict-apply"
        summary = f"Strong fundamentals outperforming historical benchmarks in {sector_name}."
    else:
        verdict, cls = "Avoid", "verdict-avoid"
        summary = f"Subpar valuation or growth metrics compared to established peers in {sector_name}."

    return {"verdict": verdict, "verdictCls": cls, "summary": summary, "checks": checks}

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
                        open_date, close_date = parts[0].strip(), parts[1].strip()

                    if not re.search(r'\b20\d{2}\b', open_date): open_date = f"{open_date} {current_year}"
                    if close_date != "TBA" and not re.search(r'\b20\d{2}\b', close_date): close_date = f"{close_date} {current_year}"

                    if not is_currently_open(open_date, close_date): continue

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
    print("  IPO Decision Engine — Precision CLI Configuration")
    print("="*50)

    cache = load_cache()
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

        if slug in cache:
            enrichment = cache[slug]
        else:
            enrichment = prompt_user_data(name, ipo["autoPrice"], ipo["autoIssue"])
            if enrichment:
                cache[slug] = enrichment
                save_cache(cache)

        if enrichment:
            price = enrichment.get("cutOffPrice")
            lot = enrichment.get("lotSize")
            listing = enrichment.get("listingDate", "TBA")
            issue_sz = enrichment.get("issueSize", "TBA")
            sector_name = enrichment.get("sector", "Financials")
            min_amt = (price * lot * multiplier) if (price and lot) else None
        else:
            price, lot, listing, issue_sz, sector_name, min_amt = None, None, "TBA", ipo["autoIssue"], "Financials", None

        bench = SECTOR_BENCHMARKS.get(sector_name, {"avgListingGain": 20.0, "winRate": "3/4 positive"})
        decision = evaluate_decision(enrichment, sector_name)

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
            "historicalGain": f"{bench['avgListingGain']}% avg listing gain in last 1 yr ({bench['winRate']})",
            "summary": decision["summary"],
            "verdict": decision["verdict"],
            "verdictCls": decision["verdictCls"],
            "checks": decision["checks"],
            "score": 1,
            "lastFetched": datetime.now().strftime("%d %b %Y, %I:%M %p")
        })

    data = {"upcoming": upcoming, "lastUpdated": datetime.now().strftime("%d %b %Y, %I:%M %p")}
    save_data(data)
    print(f"\nDone! Processed {len(upcoming)} OPEN IPO(s).\n")

if __name__ == "__main__":
    main()