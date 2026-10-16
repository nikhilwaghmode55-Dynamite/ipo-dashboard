"""
fetch_ipos.py — Live IPO Intelligence Dashboard Engine
Unattended cloud mode ("Data will be updated soon"), multi-page Mainboard + SME scraping,
fuzzy cache matching, state retention on network glitches, and IST timezone sync.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

CACHE_FILE = "ipo_cache.json"
DATA_FILE  = "data.json"

# Force Indian Standard Time (IST = UTC + 5:30) everywhere
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():
    return datetime.now(IST)

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

def clean_company_name(raw_name):
    name = raw_name.split("(")[0].strip()
    name = re.sub(r'\b(?:Ltd\.?|Limited|Pvt\.?|Private|IPO)\b', '', name, flags=re.IGNORECASE).strip()
    name = re.sub(r'\s+', ' ', name).strip(" -–.,")
    return name

def canonical_key(name):
    cleaned = clean_company_name(name).lower()
    noise = {
        "services", "service", "business", "businesses", "india",
        "technologies", "technology", "solutions", "system", "systems",
        "transmissions", "manufacturing", "engineering", "imaging", "medical"
    }
    words = [w for w in re.findall(r'[a-z0-9]+', cleaned) if w not in noise]
    if not words:
        words = re.findall(r'[a-z0-9]+', cleaned)
    return "-".join(words[:2]) if len(words) >= 2 else (words[0] if words else cleaned)

def find_in_dict(target_slug, target_name, data_dict):
    if not data_dict:
        return None
    if target_slug in data_dict:
        return data_dict[target_slug]
    target_canon = canonical_key(target_name)
    for k, v in data_dict.items():
        k_canon = canonical_key(k.replace("-", " "))
        if k_canon == target_canon or k.startswith(target_slug) or target_slug.startswith(k):
            return v
    return None

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            try:
                content = f.read().strip()
                return json.loads(content) if content else {}
            except json.JSONDecodeError:
                return {}
    return {}

def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

def load_existing_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                content = f.read().strip()
                if not content:
                    return {}
                data = json.loads(content)
                result = {}
                for item in data.get("upcoming", []):
                    slug = item.get("slug") or item.get("name", "").lower().replace(" ", "-")
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
    current_year = now_ist().year
    if not re.search(r'\b20\d{2}\b', date_str):
        date_str = f"{date_str} {current_year}"
    for fmt in ("%d %B %Y", "%d %b %Y", "%d-%b-%Y", "%d/%m/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None

def extract_month_num(text):
    month_map = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
    }
    text_lower = text.lower()
    for name, num in sorted(month_map.items(), key=lambda x: -len(x[0])):
        if re.search(rf'\b{name}\b', text_lower):
            return num
    return None

def parse_date_range(dates_str):
    if not dates_str or "tba" in dates_str.lower() or "to be announced" in dates_str.lower():
        return None, None

    cleaned = re.sub(r'\b20\d{2}-\d{2}-\d{2}\b', '', dates_str)
    cleaned = re.sub(r'(\d+)(?:st|nd|rd|th)\b', r'\1', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*(?:to|–|—|-)\s*', ' - ', cleaned, flags=re.IGNORECASE).strip()

    if " - " not in cleaned:
        return None, None

    parts = cleaned.split(" - ")
    open_part, close_part = parts[0].strip(), parts[-1].strip()

    end_month = extract_month_num(close_part) or extract_month_num(open_part)
    if not end_month:
        return None, None
    start_month = extract_month_num(open_part) or end_month

    open_no_yr = re.sub(r'\b20\d{2}\b', '', open_part)
    close_no_yr = re.sub(r'\b20\d{2}\b', '', close_part)

    open_nums = [int(x) for x in re.findall(r'\b\d{1,2}\b', open_no_yr) if 1 <= int(x) <= 31]
    close_nums = [int(x) for x in re.findall(r'\b\d{1,2}\b', close_no_yr) if 1 <= int(x) <= 31]

    if not open_nums or not close_nums:
        return None, None

    start_day, end_day = open_nums[0], close_nums[0]

    if not extract_month_num(open_part) and start_day > end_day:
        start_month = 12 if end_month == 1 else end_month - 1

    rev_month_map = {
        1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
        7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"
    }
    current_year = now_ist().year
    start_str = f"{start_day} {rev_month_map[start_month]} {current_year}"
    close_str = f"{end_day} {rev_month_map[end_month]} {current_year}"

    return start_str, close_str

def is_currently_open(open_str, close_str):
    today = now_ist().date()
    open_dt = parse_date_string(open_str)
    close_dt = parse_date_string(close_str)
    if not open_dt or not close_dt:
        return False
    return open_dt.date() <= today <= close_dt.date()

def parse_table_columns(cols, row_text):
    price = None
    issue_size = "TBA"

    cr_match = re.search(r'₹?\s*([\d,.]+)\s*(?:Cr|crore|Crores)', row_text, re.IGNORECASE)
    issue_val_str = None
    if cr_match:
        clean_cr = cr_match.group(1).rstrip('.,')
        issue_size = f"₹{clean_cr} Cr"
        issue_val_str = clean_cr.replace(',', '').split('.')[0]

    all_prices = []
    for idx, col in enumerate(cols):
        if idx in (0, 1):
            continue
        text = col.text.strip()
        text_lower = text.lower()

        if any(kw in text_lower for kw in ["cr", "crore", "issue", "size", "lot", "shares"]):
            continue

        if any(sym in text_lower for sym in ["₹", "rs", "-", "–", "to"]):
            nums = re.findall(r'\d[\d,]*', text)
            for n in nums:
                val = int(n.replace(',', ''))
                if 10 <= val < 5000 and (not issue_val_str or str(val) != issue_val_str):
                    all_prices.append(val)

    if all_prices:
        price = max(all_prices)

    return price, issue_size

def is_github_actions():
    return os.environ.get("GITHUB_ACTIONS") == "true" or "--headless" in sys.argv

def prompt_user_fundamentals(name):
    if is_github_actions():
        print(f"  [Headless Cloud] Auto-publishing {name} with 'Data will be updated soon'")
        return None

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
        if not sec_choice:
            print(f"  [Skipped] Left {name} as 'Data will be updated soon'.")
            return None
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
    except (EOFError, Exception) as e:
        print(f"  [Notice] Input skipped ({e}). Setting 'Data will be updated soon'.")
        return None

def evaluate_decision(enrichment, sector_name):
    if not enrichment:
        return {
            "verdict": "Data will be updated soon", "verdictCls": "verdict-caution",
            "summary": "Awaiting fundamental RHP data entry for comparative analysis.",
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

def extract_row_dates(cols):
    # 1. Check any column for a date range like "14-16 October" or "14 Oct 2026 - 16 Oct 2026"
    for col in cols[1:]:
        open_d, close_d = parse_date_range(col.text.strip())
        if open_d and close_d:
            return open_d, close_d
    # 2. Check adjacent columns for separate Open Date and Close Date (e.g., "October 14, 2026" | "October 16, 2026")
    if len(cols) >= 3:
        d1 = parse_date_string(cols[1].text.strip())
        d2 = parse_date_string(cols[2].text.strip())
        if d1 and d2:
            return d1.strftime("%d %B %Y"), d2.strftime("%d %B %Y")
    return None, None

def scrape_into_merged(session, url, default_cat, headers, merged_ipos):
    response = session.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    tables = soup.find_all('table')

    for table in tables:
        table_heading = ""
        prev_elem = table.find_previous(['h2', 'h3'])
        if prev_elem:
            table_heading = prev_elem.text.lower()

        for row in table.find_all('tr'):
            cols = row.find_all('td')
            if len(cols) < 2:
                continue
            row_text = row.text.strip()
            raw_name = cols[0].text.strip()

            if not raw_name or raw_name.lower() in {"company", "ipo", "ipo name", "company name", "issuer"}:
                continue
            if "sse" in raw_name.lower() or "social stock" in raw_name.lower():
                continue

            open_date, close_date = extract_row_dates(cols)
            if not is_currently_open(open_date, close_date):
                continue

            if "sme" in raw_name.lower() or "sme" in row_text.lower() or "sme" in table_heading or default_cat == "SME IPO":
                ipo_category = "SME IPO"
            else:
                ipo_category = "Mainboard IPO"

            name = clean_company_name(raw_name)
            if len(name) < 3:
                continue

            price, issue_size = parse_table_columns(cols, row_text)
            canon = canonical_key(name)
            slug = name.lower().replace(" ", "-")

            if canon not in merged_ipos:
                merged_ipos[canon] = {
                    "slug": slug,
                    "name": name,
                    "category": ipo_category,
                    "openDate": open_date,
                    "closeDate": close_date,
                    "autoPrice": price,
                    "autoIssue": issue_size
                }
            else:
                existing = merged_ipos[canon]
                existing["openDate"] = open_date
                existing["closeDate"] = close_date
                if len(name) > len(existing["name"]):
                    existing["name"] = name
                    existing["slug"] = slug
                if not existing["autoPrice"] and price:
                    existing["autoPrice"] = price
                if existing["autoIssue"] == "TBA" and issue_size != "TBA":
                    existing["autoIssue"] = issue_size
                if ipo_category == "SME IPO":
                    existing["category"] = "SME IPO"

def fetch_open_ipos():
    print("\n[Scraper] Fetching strictly OPEN IPOs across Mainboard & SME tables (v2.0)...")
    ipowatch_urls = [
        ("https://ipowatch.in/upcoming-ipo-calendar-ipo-list/", None),
        ("https://ipowatch.in/upcoming-ipo-list/", None),
        ("https://ipowatch.in/upcoming-sme-ipo-list/", "SME IPO"),
    ]
    fallback_url = "https://ipo.indiratrade.com/Home"

    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1.5, status_forcelist=[500, 502, 503, 504], raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    merged_ipos = {}
    for url, default_cat in ipowatch_urls:
        try:
            scrape_into_merged(session, url, default_cat, headers, merged_ipos)
        except Exception as err:
            print(f"  [Warning] {url} skipped ({err}).")

    if not merged_ipos:
        try:
            scrape_into_merged(session, fallback_url, None, headers, merged_ipos)
        except Exception as err:
            print(f"  [Warning] Fallback source failed ({err}).")

    results = list(merged_ipos.values())
    print(f"  [Scraper] Found {len(results)} active OPEN IPO(s):")
    for idx, ipo in enumerate(results, 1):
        print(f"    {idx}. {ipo['name']} ({ipo['category']}) | Closes: {ipo['closeDate']} | Cut-off: ₹{ipo['autoPrice']} | Issue: {ipo['autoIssue']}")
    return results

def main():
    print("\n" + "="*50)
    print("  IPO Intelligence Dashboard — Interactive CLI Engine")
    print("="*50)

    cache = load_cache()
    existing_data = load_existing_data()
    open_ipos = fetch_open_ipos()

    # Self-healing state retention: never wipe data.json blank on a temporary network outage
    if not open_ipos and existing_data:
        print("  [Recovery] Retaining still-open IPOs from existing data.json...")
        for slug, item in existing_data.items():
            if is_currently_open(item.get("openDate"), item.get("closeDate")):
                cat = "SME IPO" if "SME" in item.get("sector", "") else "Mainboard IPO"
                open_ipos.append({
                    "slug": slug,
                    "name": item.get("name", slug),
                    "category": cat,
                    "openDate": item.get("openDate"),
                    "closeDate": item.get("closeDate"),
                    "autoPrice": item.get("issuePrice"),
                    "autoIssue": item.get("issueSize", "TBA")
                })

    if not open_ipos:
        print("\nNo OPEN IPOs found for today.")
        save_data({"upcoming": [], "lastUpdated": now_ist().strftime("%d %b %Y, %I:%M %p")})
        return

    upcoming = []
    for ipo in open_ipos:
        slug, name = ipo["slug"], ipo["name"]
        ipo_cat = ipo["category"]
        multiplier = 2 if ipo_cat == "SME IPO" else 1

        enrichment = find_in_dict(slug, name, cache)
        if enrichment and enrichment.get("sector") and enrichment.get("lotSize"):
            print(f"  [Cached] Loaded saved fundamentals for: {name}")
        else:
            enrichment = prompt_user_fundamentals(name)
            if enrichment:
                cache[slug] = enrichment
                save_cache(cache)

        price = ipo["autoPrice"]
        lot = enrichment.get("lotSize") if enrichment else None
        issue_sz = ipo["autoIssue"]
        sector_name = enrichment.get("sector", "Pending Entry") if enrichment else "Pending Entry"

        cached_item = find_in_dict(slug, name, existing_data)
        if cached_item:
            if not price and cached_item.get("issuePrice"):
                price = cached_item["issuePrice"]
            if not lot and cached_item.get("lotSize"):
                lot = cached_item["lotSize"]
            if issue_sz == "TBA" and cached_item.get("issueSize") and cached_item.get("issueSize") != "TBA":
                issue_sz = cached_item["issueSize"]

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
            "lastFetched": now_ist().strftime("%d %b %Y, %I:%M %p"),
        })

    data = {
        "upcoming": upcoming,
        "lastUpdated": now_ist().strftime("%d %b %Y, %I:%M %p")
    }
    save_data(data)
    print(f"\nDone! Processed exactly {len(upcoming)} active OPEN IPO(s).\n")

if __name__ == "__main__":
    main()