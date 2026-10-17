"""
fetch_ipos.py — Master Engine (ChatGPT Core + Recovered CLI & Retention)
Flawless Issue Size parsing, Interactive CLI, and Aggressive State Retention to prevent blank websites.
"""

import json
import re
import sys
import os
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data.json"
CACHE_FILE = ROOT / "ipo_cache.json"
IST = ZoneInfo("Asia/Kolkata")

SECTOR_BENCHMARKS = {
    "Information Technology": {"avgPS": 5.2, "avgCagr": 22.0, "avgListingGain": 24.5, "winRate": "28/32 positive"},
    "Financials": {"avgPS": 3.8, "avgCagr": 18.0, "avgListingGain": 29.4, "winRate": "42/50 positive"},
    "Health Care": {"avgPS": 4.5, "avgCagr": 20.0, "avgListingGain": 21.0, "winRate": "15/18 positive"},
    "Consumer Discretionary": {"avgPS": 4.0, "avgCagr": 16.0, "avgListingGain": 16.5, "winRate": "18/24 positive"},
    "Consumer Staples": {"avgPS": 3.5, "avgCagr": 12.0, "avgListingGain": 12.0, "winRate": "11/15 positive"},
    "Communication Services": {"avgPS": 4.2, "avgCagr": 15.0, "avgListingGain": 19.0, "winRate": "8/10 positive"},
    "Industrials": {"avgPS": 2.8, "avgCagr": 14.0, "avgListingGain": 18.2, "winRate": "41/48 positive"},
    "Energy": {"avgPS": 2.2, "avgCagr": 10.0, "avgListingGain": 14.0, "winRate": "9/11 positive"},
    "Utilities": {"avgPS": 2.0, "avgCagr": 8.0, "avgListingGain": 10.5, "winRate": "6/8 positive"},
    "Materials": {"avgPS": 2.5, "avgCagr": 13.0, "avgListingGain": 17.0, "winRate": "22/28 positive"},
    "Real Estate": {"avgPS": 3.0, "avgCagr": 15.0, "avgListingGain": 22.0, "winRate": "14/16 positive"}
}

SOURCES = [
    "https://ipowatch.in/upcoming-ipo-list/",
    "https://ipowatch.in/upcoming-ipo-this-week/",
    "https://ipowatch.in/mainboard-ipo-2026/",
    "https://ipowatch.in/upcoming-sme-ipo-list/",
    "https://www.ipoinfo.ai/ipo-calendar",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

def session():
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s

def clean(value):
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()

def key(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())

def numeric_values(text):
    return [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]

def price_band(text):
    nums = [n for n in numeric_values(text) if 1 <= n <= 100000]
    return max(nums) if nums else None

def issue_size(text, heading=""):
    raw = clean(text)
    if not raw or raw.lower() in {"tba", "na", "n/a", "-", "—"}:
        return None
    nums = numeric_values(raw)
    if not nums:
        return None
    amount = nums[0]
    unit_text = (heading + " " + raw).lower()
    if "billion" in unit_text: amount *= 100
    elif "million" in unit_text: amount /= 10
    elif "lakh" in unit_text: amount /= 1000
    elif "crore" in unit_text or re.search(r"\bcr\b", unit_text): pass
    else: return None
    return f"₹{amount:,.2f}".rstrip("0").rstrip(".") + " Cr"

def parse_one_date(text, year=None):
    text = clean(text)
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    year = year or datetime.now(IST).year
    formats = ["%d %B %Y", "%d %b %Y", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%Y-%m-%d", "%d %B", "%d %b"]
    for fmt in formats:
        candidate = text if "%Y" in fmt else f"{text} {year}"
        try: return datetime.strptime(candidate, fmt).date()
        except ValueError: continue
    return None

def parse_dates(text):
    text = clean(text)
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    text = text.replace(" to ", " - ").replace("–", "-").replace("—", "-")
    year = datetime.now(IST).year
    patterns = [r"(\d{1,2})\s*([A-Za-z]{3,9})?\s*-\s*(\d{1,2})\s*([A-Za-z]{3,9})\s*(20\d{2})?"]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match: continue
        d1, m1, d2, m2, yr = match.groups()
        month_names = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
        def month_number(value):
            if not value: return None
            return next((v for k, v in month_names.items() if value.lower().startswith(k)), None)
        m_end = month_number(m2)
        m_start = month_number(m1) or m_end
        if m_start and m_end:
            try:
                y = int(yr or year)
                return (date(y, m_start, int(d1)), date(y, m_end, int(d2)))
            except ValueError: pass
    parts = re.split(r"\s+-\s+", text, maxsplit=1)
    if len(parts) == 2:
        start = parse_one_date(parts[0], year)
        end = parse_one_date(parts[1], year)
        if start and end: return start, end
    return None, None

def get_category(name, row_text, url):
    text = f"{name} {row_text} {url}".lower()
    if "sme" in text or "sme-ipo" in url.lower(): return "SME IPO"
    return "Mainboard IPO"

def extract_tables(soup, url):
    records = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2: continue
        header_row, headers = None, []
        for row in rows[:3]:
            cells = row.find_all(["th", "td"], recursive=False)
            labels = [clean(c.get_text(" ", strip=True)).lower() for c in cells]
            if any(term in label for label in labels for term in ("company", "ipo name", "ipo date", "opening date", "closing date", "close date", "issue size", "ipo size", "price band", "lot size", "bidding dates")):
                header_row, headers = row, labels
                break
        if not header_row: continue
        for row in rows:
            if row is header_row: continue
            cells = row.find_all(["td", "th"], recursive=False)
            if not cells: continue
            values = [clean(c.get_text(" ", strip=True)) for c in cells]
            if not values or len(values) < 2: continue
            mapped = {headers[i] if i < len(headers) else "": value for i, value in enumerate(values)}
            name = values[0]
            if name.lower() in {"company", "company name", "ipo name", "ipo", "company ipo"}: continue
            row_text, low = " ".join(values), " ".join(values).lower()
            start_date = end_date = None
            for heading, value in mapped.items():
                if any(x in heading for x in ("ipo date", "bidding dates", "open date", "opening date", "date")):
                    start_date, end_date = parse_dates(value)
                    if start_date and end_date: break
            if not start_date or not end_date:
                for value in values:
                    a, b = parse_dates(value)
                    if a and b: start_date, end_date = a, b; break
            price = None
            for heading, value in mapped.items():
                if "price band" in heading or "price range" in heading:
                    price = price_band(value)
                    if price: break
            if price is None:
                for value in values:
                    if re.search(r"(?:₹|Rs\.?)\s*[\d,]+\s*(?:to|-|–)\s*(?:₹|Rs\.?)?\s*[\d,]+", value, re.I):
                        price = price_band(value)
                        if price: break
            size = None
            for heading, value in mapped.items():
                if "issue size" in heading or "ipo size" in heading:
                    size = issue_size(value, heading)
                    if size: break
            lot = None
            for heading, value in mapped.items():
                if "lot size" in heading or "market lot" in heading:
                    nums = numeric_values(value)
                    if nums and nums[0].is_integer() and 1 <= nums[0] <= 100000:
                        lot = int(nums[0])
                        break
            records.append({"name": name, "category": get_category(name, low, url), "open": start_date, "close": end_date, "price": price, "lot": lot, "issueSize": size, "source": url})
    return records

def currently_open(item, today):
    return item["open"] is not None and item["close"] is not None and item["open"] <= today <= item["close"]

def scrape():
    s = session()
    merged, errors = {}, []
    for url in SOURCES:
        try:
            response = s.get(url, headers=HEADERS, timeout=25)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            found = extract_tables(soup, url)
            for item in found:
                k = key(item["name"])
                if not k: continue
                if k not in merged: merged[k] = item; continue
                old = merged[k]
                for field in ("price", "lot", "issueSize", "open", "close"):
                    if old.get(field) is None and item.get(field) is not None: old[field] = item[field]
                if item["category"] == "SME IPO": old["category"] = "SME IPO"
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
    today = datetime.now(IST).date()
    return [x for x in merged.values() if currently_open(x, today)], errors

def display_date(value):
    return value.strftime("%d %B %Y") if value else "TBA"

def load_cache():
    if CACHE_FILE.exists():
        try: return json.loads(CACHE_FILE.read_text(encoding="utf-8")) or {}
        except: return {}
    return {}

def save_cache(cache):
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")

def is_github_actions():
    return os.environ.get("GITHUB_ACTIONS") == "true" or "--headless" in sys.argv

def prompt_user_fundamentals(name):
    if is_github_actions(): return None
    print(f"\n" + "="*55 + f"\n [CLI Input Needed] Configure fundamentals for: {name}\n" + "="*55)
    sectors = list(SECTOR_BENCHMARKS.keys())
    for i in range(0, len(sectors), 2):
        item1 = f"{i+1:2d}. {sectors[i]:<26}"
        item2 = f"{i+2:2d}. {sectors[i+1]}" if i+1 < len(sectors) else ""
        print(f"  {item1}   {item2}")
    
    try:
        sector = None
        while not sector:
            sec_choice = input(f"\nEnter sector choice (1-{len(sectors)}) or press Enter to skip: ").strip()
            if not sec_choice: return None
            if sec_choice.isdigit() and 1 <= int(sec_choice) <= len(sectors): sector = sectors[int(sec_choice) - 1]
            else: print(f"  [Error] Please enter a whole number between 1 and {len(sectors)}.")
        
        ps_ratio = None
        while ps_ratio is None:
            val = input("Current FY P/S Ratio (e.g., 4.5): ").strip()
            try: ps_ratio = float(val)
            except ValueError: print("  [Error] Please enter a valid decimal number.")
            
        cagr = None
        while cagr is None:
            val = input("3-Year Revenue CAGR % (e.g., 20): ").strip()
            try: cagr = float(val)
            except ValueError: print("  [Error] Please enter a valid decimal number.")
        
        print("\nSelect Use of Proceeds:")
        print("  1. Capex / Business Expansion / Growth / R&D")
        print("  2. Working Capital")
        print("  3. Debt Repayment / OFS (Offer for Sale)")
        proceeds = None
        while not proceeds:
            proc_choice = input("Enter option (1-3): ").strip()
            if proc_choice in ["1", "2", "3"]: proceeds = {"1": "Capex and Business Expansion", "2": "Working Capital", "3": "Debt Repayment / OFS"}[proc_choice]
            else: print("  [Error] Please enter 1, 2, or 3.")

        promoter = None
        while promoter is None:
            val = input("Post-IPO Promoter Holding % (e.g., 65): ").strip()
            try: promoter = float(val)
            except ValueError: print("  [Error] Please enter a valid decimal number.")
        
        return {"sector": sector, "psRatio": ps_ratio, "cagr3Yr": cagr, "proceedsUse": proceeds, "promoterPct": promoter}
    except (EOFError, KeyboardInterrupt): return None

def evaluate_decision(enrichment, sector_name):
    if not enrichment: return {"verdict": "Data will be updated soon", "verdictCls": "verdict-caution", "summary": "Awaiting fundamental RHP data entry for comparative analysis.", "checks": [{"icon": "ℹ", "cls": "check-warn", "text": "Fundamental metrics pending entry"}]}

    bench = SECTOR_BENCHMARKS.get(sector_name, {"avgPS": 3.5, "avgCagr": 15.0})
    ps, cagr, promoter = enrichment["psRatio"], enrichment["cagr3Yr"], enrichment["promoterPct"]
    proceeds = enrichment["proceedsUse"]
    score, checks = 0, []

    if ps <= bench["avgPS"]: checks.append({"icon": "✓", "cls": "check-pass", "text": f"Attractive P/S valuation ({ps}x vs Sector Average {bench['avgPS']}x)"}); score += 2
    else: checks.append({"icon": "✗", "cls": "check-fail", "text": f"High P/S valuation ({ps}x vs Sector Average {bench['avgPS']}x)"}); score -= 1

    if cagr >= bench["avgCagr"]: checks.append({"icon": "✓", "cls": "check-pass", "text": f"Robust growth ({cagr}% vs Sector Average {bench['avgCagr']}%)"}); score += 2
    else: checks.append({"icon": "✗", "cls": "check-fail", "text": f"Slow revenue growth ({cagr}% vs Sector Average {bench['avgCagr']}%)"}); score -= 1

    if promoter >= 50: checks.append({"icon": "✓", "cls": "check-pass", "text": f"High promoter confidence ({promoter}% holding)"}); score += 1
    else: checks.append({"icon": "✗", "cls": "check-fail", "text": f"Low promoter holding ({promoter}%)"}); score -= 1

    if any(k in proceeds.lower() for k in ["capex", "growth", "working capital", "expansion"]): checks.append({"icon": "✓", "cls": "check-pass", "text": f"Major chunk of issue allocated to {proceeds}"}); score += 1
    else: checks.append({"icon": "✗", "cls": "check-fail", "text": f"Major chunk of issue goes to {proceeds}"}); score -= 1

    if score >= 3: return {"verdict": "Apply", "verdictCls": "verdict-apply", "summary": f"Strong fundamentals outperforming historical benchmarks in {sector_name}.", "checks": checks}
    else: return {"verdict": "Avoid", "verdictCls": "verdict-avoid", "summary": f"Subpar valuation or growth metrics compared to peers in {sector_name}.", "checks": checks}

def main():
    now = datetime.now(IST)
    today = now.date()
    print(f"\nIPO scraper started: {now:%d %b %Y %I:%M %p} IST")

    cache = load_cache()

    try:
        items, errors = scrape()
    except Exception as exc:
        print(f"[ERROR] {exc}")
        items = []

    try:
        previous = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {}

    old_items = {key(x.get("name", "")): x for x in previous.get("upcoming", [])}
    output = []
    scraped_keys = set()

    for item in items:
        k = key(item["name"])
        scraped_keys.add(k)
        old = old_items.get(k, {})

        price = item["price"] or old.get("issuePrice")
        lot = item["lot"] or old.get("lotSize")
        size = item["issueSize"] or old.get("issueSize") or "TBA"

        is_sme = item["category"] == "SME IPO"
        multiplier = 2 if is_sme else 1
        investment = round(price * lot * multiplier) if price and lot else None

        enrichment = cache.get(k)
        if not enrichment:
            enrichment = prompt_user_fundamentals(item["name"])
            if enrichment:
                cache[k] = enrichment
                save_cache(cache)
                
        sector_name = enrichment.get("sector", "Pending Entry") if enrichment else "Pending Entry"
        decision = evaluate_decision(enrichment, sector_name)
        
        if enrichment:
            bench = SECTOR_BENCHMARKS.get(sector_name, {"avgListingGain": 20.0, "winRate": "40/50 positive"})
            historical_gain = f"{bench['avgListingGain']}% avg listing gain in last 1 yr ({bench['winRate']})"
        else:
            historical_gain = "Fundamental data pending entry for sector benchmarking"

        output.append({
            "slug": re.sub(r"[^a-z0-9]+", "-", item["name"].lower()).strip("-"),
            "name": item["name"],
            "symbol": old.get("symbol", ""),
            "sector": f"{sector_name} ({item['category']})",
            "category": item["category"],
            "status": "Open",
            "openDate": display_date(item["open"]),
            "closeDate": display_date(item["close"]),
            "issuePrice": price,
            "lotSize": lot,
            "minAmount": investment,
            "issueSize": size,
            "historicalGain": historical_gain,
            "summary": decision["summary"],
            "verdict": decision["verdict"],
            "verdictCls": decision["verdictCls"],
            "checks": decision["checks"],
            "score": 1,
            "lastFetched": now.strftime("%d %b %Y, %I:%M %p"),
        })

    # =======================================================================================
    # AGGRESSIVE STATE RETENTION (The Fix for the Blank Website)
    # Even if Cloudflare blocks the script, we recover and retain IPOs from the local cache.
    # =======================================================================================
    for k, old_item in old_items.items():
        if k not in scraped_keys:
            close_str = old_item.get("closeDate")
            if close_str and close_str != "TBA":
                close_dt = parse_one_date(close_str, now.year)
                if close_dt:
                    # 5 PM Market Close Expiry
                    if close_dt == today and now.hour >= 17:
                        continue
                    if close_dt >= today:
                        print(f"  [Retention] Recovering missing open IPO: {old_item.get('name')}")
                        output.append(old_item)

    if not output:
        print("\nNo OPEN IPOs found for today.")

    data = {
        "upcoming": sorted(output, key=lambda x: x.get("closeDate", "TBA")),
        "lastUpdated": now.strftime("%d %b %Y, %I:%M %p"),
        "scrapeWarnings": errors if 'errors' in locals() else [],
    }

    temp_file = DATA_FILE.with_suffix(".tmp")
    temp_file.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp_file.replace(DATA_FILE)

    print(f"\n[OK] {len(output)} currently open IPO(s) written to data.json")
    for x in output:
        print(f"{x['name']} | {x['category']} | Price: {x['issuePrice']} | Lot: {x['lotSize']} | Investment: {x['minAmount']} | Issue size: {x['issueSize']} | Closes: {x['closeDate']}")

if __name__ == "__main__":
    main()