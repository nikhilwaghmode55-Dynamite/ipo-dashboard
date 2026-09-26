"""
fetch_ipos.py — Live IPO Intelligence Dashboard Engine
Strictly filters for currently OPEN IPOs only, discarding archives and closed issues.
"""

import requests
import json
import os
import re
from datetime import datetime
from bs4 import BeautifulSoup

CACHE_FILE = "ipo_cache.json"
DATA_FILE  = "data.json"

# 11 Sector Benchmarks for Analytical Decision Engine
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
    print(f"  [Success] Cleaned dashboard data ({len(data.get('upcoming', []))} open IPOs) written to data.json")

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

def parse_date_range(dates_str):
    dates_str = dates_str.strip()
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December", 
              "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    found_month = ""
    for m in months:
        if m.lower() in dates_str.lower():
            found_month = m
            break
            
    open_str, close_str = dates_str, "TBA"
    if " to " in dates_str.lower():
        parts = dates_str.lower().split(" to ")
        open_str, close_str = parts[0].strip(), parts[1].strip()
    elif "-" in dates_str:
        parts = dates_str.split("-")
        open_str, close_str = parts[0].strip(), parts[1].strip()

    if found_month and not any(m.lower() in open_str.lower() for m in months):
        open_str = f"{open_str} {found_month}"
    if found_month and not any(m.lower() in close_str.lower() for m in months):
        close_str = f"{close_str} {found_month}"
        
    return open_str, close_str

def is_currently_open(open_str, close_str):
    """Strictly checks if today falls between open and close dates. Drops closed or future listings."""
    today = datetime.now().date()
    open_dt = parse_date_string(open_str)
    close_dt = parse_date_string(close_str)
    
    if not open_dt or not close_dt:
        return False
        
    # Strict active window: today must be >= open date and <= close date
    return open_dt.date() <= today <= close_dt.date()

def parse_table_columns(cols, row_text):
    price, lot_size, issue_size = None, None, "TBA"
    for col in cols:
        text = col.text.strip()
        if "cr" in text.lower():
            cr_match = re.search(r'₹?\s*([\d,.]+)', text)
            if cr_match: issue_size = f"₹{cr_match.group(1)} Cr"
        elif "₹" in text and "cr" not in text.lower():
            p_match = re.search(r'₹\s*([\d,]+)', text)
            if p_match:
                val = int(p_match.group(1).replace(',', ''))
                if val < 25000: price = val
        elif text.isdigit():
            val = int(text)
            if 25 <= val <= 10000 and val not in [2025, 2026]: lot_size = val

    if not price:
        clean_row = re.sub(r'₹?\s*[\d,.]+\s*(?:Cr|crore)', '', row_text, flags=re.IGNORECASE)
        prices = re.findall(r'₹\s*([\d,]+)', clean_row)
        if prices:
            clean_p = [int(p.replace(',', '')) for p in prices if int(p.replace(',', '')) < 25000]
            if clean_p: price = max(clean_p)

    if not lot_size:
        lot_match = re.search(r'(\d+)\s*(?:shares|lot)', row_text, re.IGNORECASE)
        if lot_match: lot_size = int(lot_match.group(1))

    return price, lot_size, issue_size

def prompt_user_fundamentals(name):
    print(f"\n[CLI Input] Configure fundamentals for active IPO: {name}")
    print("Select Sector from the 11 Predefined Sectors:")
    sectors = list(SECTOR_BENCHMARKS.keys())
    for idx, sec in enumerate(sectors, 1): print(f"  {idx}. {sec}")
    
    try:
        sec_choice = input(f"Enter choice (1-{len(sectors)}) or press Enter to skip: ").strip()
        if not sec_choice: return None
        sector = sectors[int(sec_choice) - 1]
        
        ps_ratio = float(input("Current FY P/S Ratio: ").strip())
        cagr = float(input("3-Year Revenue CAGR (%): ").strip())
        proceeds = input("Use of Proceeds (Capex, Debt Repayment, OFS, etc.): ").strip()
        promoter = float(input("Post-IPO Promoter Holding (%): ").strip())
        
        return {"sector": sector, "psRatio": ps_ratio, "cagr3Yr": cagr, "proceedsUse": proceeds, "promoterPct": promoter}
    except Exception:
        print("  [Notice] Skipped or invalid input. Setting status to 'Data will be updated soon'.")
        return None

def evaluate_decision(enrichment, sector_name):
    if not enrichment:
        return {
            "verdict": "Data will be updated soon", "verdictCls": "verdict-caution",
            "summary": f"Awaiting fundamental RHP data entry for comparative analysis against {sector_name}.",
            "checks": [{"icon": "ℹ", "cls": "check-warn", "text": "Fundamental metrics pending entry"}]
        }

    bench = SECTOR_BENCHMARKS.get(sector_name, {"avgPS": 3.5, "avgCagr": 15.0})
    ps, cagr, promoter = enrichment["psRatio"], enrichment["cagr3Yr"], enrichment["promoterPct"]
    proceeds = enrichment["proceedsUse"].lower()
    score, checks = 0, []

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
        return {"verdict": "Apply", "verdictCls": "verdict-apply", "summary": f"Strong fundamentals outperforming historical benchmarks in {sector_name}.", "checks": checks}
    else:
        return {"verdict": "Avoid", "verdictCls": "verdict-avoid", "summary": f"Subpar valuation or growth metrics compared to peers in {sector_name}.", "checks": checks}

def fetch_open_ipos():
    print("\n[Scraper] Fetching strictly OPEN IPOs...")
    url = "https://ipowatch.in/upcoming-ipo-calendar-ipo-list/"
    headers = {'User-Agent': 'Mozilla/5.0'}
    open_ipos = []
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        tables = soup.find_all('table')
        
        for table in tables:
            table_heading = ""
            prev_elem = table.find_previous(['h2', 'h3', 'h4', 'strong', 'caption'])
            if prev_elem: table_heading = prev_elem.text.lower()
                
            # Skip closed or archive tables entirely
            if any(kw in table_heading for kw in ["closed", "archive", "past", "completed", "listing"]):
                continue
                
            ipo_category = "SME IPO" if "sme" in table_heading else "Mainboard IPO"
                
            for row in table.find_all('tr'):
                cols = row.find_all('td')
                row_text = row.text.strip()
                
                if len(cols) >= 2:
                    name_text = cols[0].text.strip()
                    dates_raw = cols[1].text.strip() if len(cols) > 1 else ""
                    if "Company" in name_text or not name_text: continue
                        
                    open_date, close_date = parse_date_range(dates_raw)

                    # STRICT OPEN WINDOW VALIDATION
                    if not is_currently_open(open_date, close_date):
                        continue

                    name = name_text.split("(")[0].strip()
                    if len(name) < 3: continue

                    price, lot_size, issue_size = parse_table_columns(cols, row_text)

                    open_ipos.append({
                        "slug": name.lower().replace(" ", "-"),
                        "name": name,
                        "category": ipo_category,
                        "openDate": open_date,
                        "closeDate": close_date,
                        "autoPrice": price,
                        "autoLot": lot_size,
                        "autoIssue": issue_size
                    })
    except Exception as err:
        print(f"  Error fetching calendar: {err}")
    return open_ipos

def main():
    print("\n" + "="*50)
    print("  IPO Intelligence Dashboard — Strictly Filtered Engine")
    print("="*50)

    cache = load_cache()
    existing_data = load_existing_data()
    open_ipos = fetch_open_ipos()

    if not open_ipos:
        print("\nNo OPEN IPOs found for today.")
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
            enrichment = prompt_user_fundamentals(name)
            if enrichment:
                cache[slug] = enrichment
                save_cache(cache)

        price = ipo["autoPrice"]
        lot = ipo["autoLot"]
        listing = "TBA"
        issue_sz = ipo["autoIssue"]
        sector_name = enrichment.get("sector", "Financials") if enrichment else "Financials"

        if slug in existing_data:
            cached_item = existing_data[slug]
            if cached_item.get("issuePrice"): price = cached_item["issuePrice"]
            if cached_item.get("lotSize"): lot = cached_item["lotSize"]
            if cached_item.get("listingDate"): listing = cached_item["listingDate"]
            if cached_item.get("issueSize"): issue_sz = cached_item["issueSize"]
            if cached_item.get("sector"):
                sector_str = cached_item["sector"]
                sector_name = sector_str.split(" (")[0]

        min_amt = (price * lot * multiplier) if (price and lot) else None

        decision = evaluate_decision(enrichment, sector_name)
        bench = SECTOR_BENCHMARKS.get(sector_name, {"avgListingGain": 20.0, "winRate": "3/4 positive"})

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
            "lastFetched": datetime.now().strftime("%d %b %Y, %I:%M %p"),
        })

    data = {
        "upcoming": upcoming,
        "lastUpdated": datetime.now().strftime("%d %b %Y, %I:%M %p")
    }
    save_data(data)
    print(f"\nDone! Processed exactly {len(upcoming)} active OPEN IPO(s).\n")

if __name__ == "__main__":
    main()