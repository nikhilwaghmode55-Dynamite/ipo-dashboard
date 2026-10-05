"""
fetch_ipos.py — Live IPO Intelligence Dashboard Engine
Features robust cross-month date parsing, horizontal CLI layout, and persistent caching.
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
                result = {}
                for item in data.get("upcoming", []):
                    slug = item.get("slug")
                    if not slug and item.get("name"):
                        slug = item["name"].lower().replace(" ", "-")
                    if slug:
                        result[slug] = item
                return result
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
    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12
    }
    rev_month_map = {1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June", 
                     7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"}
    
    found_month_name = ""
    for m in months:
        if m.lower() in dates_str.lower():
            found_month_name = m
            break
            
    open_part, close_part = dates_str, "TBA"
    if " to " in dates_str.lower():
        parts = dates_str.lower().split(" to ")
        open_part, close_part = parts[0].strip(), parts[1].strip()
    elif "-" in dates_str:
        parts = dates_str.split("-")
        open_part, close_part = parts[0].strip(), parts[1].strip()

    open_nums = re.findall(r'\d+', open_part)
    close_nums = re.findall(r'\d+', close_part)
    
    start_day = int(open_nums[0]) if open_nums else 1
    end_day = int(close_nums[0]) if close_nums else 1

    end_month_num = 9  # default fallback
    if found_month_name:
        for k, v in month_map.items():
            if k in found_month_name.lower():
                end_month_num = v
                break
            
    start_month_num = end_month_num
    if start_day > end_day:
        start_month_num = end_month_num - 1
        if start_month_num < 1:
            start_month_num = 12

    current_year = datetime.now().year
    start_str = f"{start_day} {rev_month_map[start_month_num]} {current_year}"
    close_str = f"{end_day} {rev_month_map[end_month_num]} {current_year}"
    
    return start_str, close_str

def is_currently_open(open_str, close_str):
    today = datetime.now().date()
    open_dt = parse_date_string(open_str)
    close_dt = parse_date_string(close_str)
    
    if not open_dt or not close_dt:
        return False
        
    return open_dt.date() <= today <= close_dt.date()

def parse_table_columns(cols, row_text):
    price, issue_size = None, "TBA"
    
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

    if not price:
        clean_row = re.sub(r'₹?\s*[\d,.]+\s*(?:Cr|crore)', '', row_text, flags=re.IGNORECASE)
        prices = re.findall(r'₹\s*([\d,]+)', clean_row)
        if prices:
            clean_p = [int(p.replace(',', '')) for p in prices if int(p.replace(',', '')) < 25000]
            if clean_p: price = max(clean_p)

    return price, issue_size

def prompt_user_fundamentals(name):
    print(f"\n" + "="*55)
    print(f" [CLI Input Needed] Configure details for: {name}")
    print("="*55)
    print("Select Sector (Displayed Horizontally):")
    sectors = list(SECTOR_BENCHMARKS.keys())
    
    for i in range(0, len(sectors), 2):
        item1 = f"{i+1:2d}. {sectors[i]:<26}"
        item2 = f"{i+2:2d}. {sectors[i+1]}" if i+1 < len(sectors) else ""
        print(f"  {item1}   {item2}")
    
    try:
        sec_choice = input(f"\nEnter sector choice (1-{len(sectors)}) or press Enter to skip: ").strip()
        if not sec_choice: return None
        sector = sectors[int(sec_choice) - 1]
        
        ps_ratio = float(input("Current FY P/S Ratio (e.g., 4.5): ").strip())
        cagr = float(input("3-Year Revenue CAGR % (e.g., 20): ").strip())
        
        print("\nSelect Use of Proceeds:")
        print("  1. Capex / Business Expansion / Growth / R&D (Positive)")
        print("  2. Working Capital (Neutral/Positive)")
        print("  3. Debt Repayment / OFS (Offer for Sale) (Negative)")
        proc_choice = input("Enter option (1-3): ").strip()
        proceeds_map = {
            "1": "Capex and Business Expansion",
            "2": "Working Capital",
            "3": "Debt Repayment / OFS"
        }
        proceeds = proceeds_map.get(proc_choice, "Debt Repayment / OFS")

        promoter = float(input("Post-IPO Promoter Holding % (e.g., 65): ").strip())
        lot_size = int(input("Exact Broker Lot Size (e.g., 50, 500, 1000): ").strip())
        
        return {
            "sector": sector, 
            "psRatio": ps_ratio, 
            "cagr3Yr": cagr, 
            "proceedsUse": proceeds, 
            "promoterPct": promoter,
            "lotSize": lot_size
        }
    except Exception:
        print("  [Notice] Invalid or skipped input. Setting defaults.")
        return None

def evaluate_decision(enrichment, sector_name):
    if not enrichment:
        return {
            "verdict": "Data will be updated soon", "verdictCls": "verdict-caution",
            "summary": f"Awaiting fundamental RHP data entry for comparative analysis.",
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

    if any(k in proceeds for k in ["capex", "growth", "working capital", "expansion"]):
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

                    if not is_currently_open(open_date, close_date):
                        continue

                    name = name_text.split("(")[0].strip()
                    if len(name) < 3: continue

                    price, issue_size = parse_table_columns(cols, row_text)

                    open_ipos.append({
                        "slug": name.lower().replace(" ", "-"),
                        "name": name,
                        "category": ipo_category,
                        "openDate": open_date,
                        "closeDate": close_date,
                        "autoPrice": price,
                        "autoIssue": issue_size
                    })
    except Exception as err:
        print(f"  Error fetching calendar: {err}")
    return open_ipos

def main():
    print("\n" + "="*50)
    print("  IPO Intelligence Dashboard — Interactive CLI Engine")
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
        lot = enrichment.get("lotSize") if enrichment else None
        issue_sz = ipo["autoIssue"]
        
        if enrichment:
            sector_name = enrichment.get("sector", "Pending Entry")
        else:
            sector_name = "Pending Entry"

        if slug in existing_data:
            cached_item = existing_data[slug]
            if cached_item.get("issuePrice"): price = cached_item["issuePrice"]
            if cached_item.get("lotSize"): lot = cached_item["lotSize"]
            if cached_item.get("issueSize"): issue_sz = cached_item["issueSize"]
            if cached_item.get("sector") and "Pending" not in cached_item["sector"]:
                sector_str = cached_item["sector"]
                sector_name = sector_str.split(" (")[0]

        min_amt = (price * lot * multiplier) if (price and lot is not None) else None

        decision = evaluate_decision(enrichment, sector_name)
        
        if enrichment:
            bench = SECTOR_BENCHMARKS.get(sector_name, {"avgListingGain": 20.0, "winRate": "3/4 positive"})
            historical_gain = f"{bench['avgListingGain']}% avg listing gain in last 1 yr ({bench['winRate']})"
        else:
            historical_gain = "Fundamental data pending entry for sector benchmarking"

        upcoming.append({
            "slug": slug,
            "name": name,
            "symbol": name[:5].upper(),
            "sector": f"{sector_name} ({ipo_cat})",
            "status": "Open",
            "openDate": ipo["openDate"],
            "closeDate": ipo["closeDate"],
            "issuePrice": price,
            "lotSize": lot,
            "minAmount": min_amt,
            "issueSize": issue_sz,
            "historicalGain": historical_gain,
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