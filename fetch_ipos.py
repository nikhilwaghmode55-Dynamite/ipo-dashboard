"""
fetch_ipos.py — Automated IPO Decision Engine
- Brand-token entity deduplication
- Strictly drops future/closed IPOs outside the 10:00 AM to 5:00 PM active window
- Dynamic SEBI Mainboard lot calculation
"""

import json
import os
import re
import sys
from datetime import datetime, date, timedelta
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
    "Real Estate": {"avgPS": 3.0, "avgCagr": 15.0, "avgListingGain": 22.0, "winRate": "14/16 positive"},
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

NOISE_WORDS = {
    "limited", "ltd", "pvt", "private", "ipo", "sme", "bse", "nse",
    "india", "indian", "global", "international", "group", "holdings",
    "holding", "platforms", "platform", "technologies", "technology",
    "tech", "solutions", "services", "service", "systems", "system",
    "industries", "industrial", "enterprises", "enterprise", "ventures",
    "corporation", "corp", "company", "co", "infocomm", "healthcare",
    "health", "hospitals", "hospital", "medical", "pharma", "pharmaceuticals",
    "lifesciences", "diagnostics", "transmissions", "imaging", "engineering",
    "manufacturing", "infra", "infrastructure", "projects", "logistics",
    "finance", "financial", "capital", "housing", "bank", "energy",
    "power", "solar", "motors", "auto", "retail", "foods", "agro",
    "chemicals", "textiles", "exports", "traders", "trading",
}

def session():
    client = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    client.mount("https://", HTTPAdapter(max_retries=retry))
    return client

def clean(value):
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()

def clean_company_name(raw_name):
    name = str(raw_name).split("(")[0].strip()
    name = re.sub(
        r"\b(?:BSE\s+SME[OUCL]?|NSE\s+SME[OUCL]?|SME[OUCL]?|IPO[OUCL]?|Ltd\.?|Limited|Pvt\.?|Private)\b",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip()
    return re.sub(r"\s+", " ", name).strip(" -–.,")

def key(value):
    return re.sub(r"[^a-z0-9]", "", clean_company_name(value).lower())

def brand_tokens(name):
    words = [w for w in re.findall(r"[a-z0-9]+", clean_company_name(name).lower()) if len(w) >= 3]
    distinctive = {w for w in words if w not in NOISE_WORDS}
    return distinctive if distinctive else set(words)

def is_same_company(name_a, name_b):
    if not name_a or not name_b:
        return False
    ka, kb = key(name_a), key(name_b)
    if ka == kb or ka in kb or kb in ka:
        return True
    ta, tb = brand_tokens(name_a), brand_tokens(name_b)
    return bool(ta and tb and (ta & tb))

def find_matching_key(name, dictionary):
    target_k = key(name)
    if target_k in dictionary:
        return target_k
    for existing_k, val in dictionary.items():
        existing_name = val.get("name", existing_k) if isinstance(val, dict) else existing_k
        if is_same_company(name, existing_name) or is_same_company(name, existing_k):
            return existing_k
    return None

def numbers(value):
    return [
        float(x.replace(",", ""))
        for x in re.findall(r"\d[\d,]*(?:\.\d+)?", str(value))
    ]

def price_band(value):
    year = datetime.now(IST).year
    values = [n for n in numbers(value) if 1 <= n <= 100000 and int(n) != year]
    return max(values) if values else None

def issue_size(value, heading=""):
    raw = clean(value)
    if not raw or raw.lower() in {"tba", "na", "n/a", "-", "—"}:
        return None
    values = numbers(raw)
    if not values:
        return None
    amount = values[0]
    units = (heading + " " + raw).lower()

    if "billion" in units:
        amount *= 100
    elif "million" in units:
        amount /= 10
    elif "lakh" in units:
        amount /= 1000

    return f"₹{amount:,.2f}".rstrip("0").rstrip(".") + " Cr"

def parse_one_date(value, year=None):
    if not value or str(value).strip().upper() == "TBA":
        return None
    text = clean(value)
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    year = int(year or datetime.now(IST).year)

    for fmt in ("%d %B %Y", "%d %b %Y", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass

    for fmt in ("%d %B", "%d %b", "%d-%m", "%d/%m"):
        try:
            parsed = datetime.strptime(f"{text} {year}", f"{fmt} %Y")
            return date(year, parsed.month, parsed.day)
        except ValueError:
            pass

    return None

def parse_dates(value):
    text = clean(value)
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    text = text.replace(" to ", " - ").replace("–", "-").replace("—", "-")
    year = datetime.now(IST).year

    pattern = r"(\d{1,2})\s*([A-Za-z]{3,9})?\s*-\s*(\d{1,2})\s*([A-Za-z]{3,9})\s*(20\d{2})?"
    match = re.search(pattern, text)
    if match:
        d1, m1, d2, m2, yr = match.groups()
        months = {
            name: i
            for i, name in enumerate(
                ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
                1,
            )
        }

        def month_num(val):
            return next(
                (num for prefix, num in months.items() if val and val.lower().startswith(prefix)),
                None,
            )

        end_month = month_num(m2)
        start_month = month_num(m1) or end_month

        if start_month and end_month:
            try:
                y = int(yr or year)
                # Handle year crossover dates correctly
                if start_month == 12 and end_month == 1:
                     return date(y-1, start_month, int(d1)), date(y, end_month, int(d2))
                return date(y, start_month, int(d1)), date(y, end_month, int(d2))
            except ValueError:
                pass

    parts = re.split(r"\s+-\s+", text, maxsplit=1)
    if len(parts) == 2:
        start = parse_one_date(parts[0], year)
        end = parse_one_date(parts[1], year)
        if start and end:
            return start, end

    return None, None

def is_within_active_window(open_dt, close_dt, now):
    if not open_dt or not close_dt:
        return False
    open_cutoff = datetime(open_dt.year, open_dt.month, open_dt.day, 10, 0, 0, tzinfo=IST)
    close_cutoff = datetime(close_dt.year, close_dt.month, close_dt.day, 17, 0, 0, tzinfo=IST)
    return open_cutoff <= now < close_cutoff

def category_for(name, row_text, url):
    if "sme" in f"{name} {row_text} {url}".lower():
        return "SME IPO"
    return "Mainboard IPO"

def extract_tables(soup, url):
    records = []
    current_year = datetime.now(IST).year

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers, header_row = None, None
        for row in rows[:3]:
            labels = [
                clean(cell.get_text(" ", strip=True)).lower()
                for cell in row.find_all(["th", "td"], recursive=False)
            ]
            if any(
                term in label
                for label in labels
                for term in (
                    "company", "ipo name", "ipo date", "opening date", "open",
                    "closing date", "close date", "close", "issue size",
                    "ipo size", "price band", "price", "lot size", "bidding dates",
                )
            ):
                headers, header_row = labels, row
                break

        if headers is None:
            continue

        for row in rows:
            if row is header_row:
                continue
            values = [
                clean(cell.get_text(" ", strip=True))
                for cell in row.find_all(["td", "th"], recursive=False)
            ]
            if len(values) < 2:
                continue

            mapped = {headers[i]: value for i, value in enumerate(values) if i < len(headers)}
            raw_name = values[0]
            if raw_name.lower() in {"company", "company name", "ipo name", "ipo", "company ipo", "issuer"}:
                continue
            if "sse" in raw_name.lower() or "social stock" in raw_name.lower() or "apply" in raw_name.lower():
                continue

            name = clean_company_name(raw_name)
            if len(name) < 3:
                continue

            row_text = " ".join(values)
            start = end = None

            for heading, value in mapped.items():
                if any(t in heading for t in ("ipo date", "bidding dates", "date")):
                    start, end = parse_dates(value)
                    if start and end:
                        break

            if not start or not end:
                open_col = next((v for h, v in mapped.items() if "open" in h), None)
                close_col = next((v for h, v in mapped.items() if "close" in h or "closing" in h), None)
                if open_col and close_col:
                    start = parse_one_date(open_col, current_year)
                    end = parse_one_date(close_col, current_year)

            if not start or not end:
                for value in values:
                    start, end = parse_dates(value)
                    if start and end:
                        break

            price = None
            for heading, value in mapped.items():
                if any(t in heading for t in ("price", "band", "range")):
                    price = price_band(value)
                    if price is not None:
                        break

            if price is None:
                for value in values:
                    if re.search(r"(?:₹|Rs\.?)\s*[\d,]+\s*(?:to|-|–)\s*(?:₹|Rs\.?)?\s*[\d,]+", value, re.I):
                        price = price_band(value)
                        if price is not None:
                            break

            size = None
            for heading, value in mapped.items():
                if any(t in heading for t in ("size", "amount", "cr")):
                    if not any(t in heading for t in ("lot", "share", "price")):
                        size = issue_size(value, heading)
                        if size:
                            break

            if size is None:
                for value in values:
                    size = issue_size(value, "")
                    if size:
                        break

            lot = None
            for heading, value in mapped.items():
                if any(t in heading for t in ("lot", "share", "qty", "quantity")):
                    if value.lower() in {"tba", "na", "n/a", "-", "—", ""}:
                        continue
                    vals = numbers(value)
                    if vals and vals[0].is_integer() and 1 <= vals[0] <= 100000:
                        if int(vals[0]) != current_year:
                            lot = int(vals[0])
                            break

            if lot is None:
                for value in values:
                    low_val = value.lower()
                    if not any(sym in low_val for sym in ["₹", "rs", "-", "–", "to", "%", "cr", "crore", "."]):
                        nums = re.findall(r"\b\d{1,5}\b", value.replace(",", ""))
                        for n in nums:
                            v = int(n)
                            if 1 <= v <= 15000 and v != current_year:
                                if price and v == int(price):
                                    continue
                                lot = v
                                break
                        if lot:
                            break

            records.append({
                "name": name,
                "category": category_for(raw_name, row_text, url),
                "open": start,
                "close": end,
                "price": price,
                "lot": lot,
                "issueSize": size,
                "source": url,
            })

    return records

def scrape(now):
    client = session()
    merged, errors = {}, []
    for url in SOURCES:
        try:
            response = client.get(url, headers=HEADERS, timeout=25)
            response.raise_for_status()
            records = extract_tables(BeautifulSoup(response.text, "html.parser"), url)
            for item in records:
                # Strictly enforce date existence and active window logic
                open_dt, close_dt = item.get("open"), item.get("close")
                if not open_dt or not close_dt:
                    continue
                if not is_within_active_window(open_dt, close_dt, now):
                    continue

                match_k = find_matching_key(item["name"], merged)
                if not match_k:
                    k = key(item["name"])
                    if k:
                        merged[k] = item
                    continue

                old = merged[match_k]
                for field in ("price", "lot", "issueSize", "open", "close"):
                    if old.get(field) is None and item.get(field) is not None:
                        old[field] = item[field]
                if item["category"] == "SME IPO":
                    old["category"] = "SME IPO"
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")

    opened = [item for item in merged.values() if item.get("price") is not None]
    return opened, errors

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default

def save_json(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)

def is_github_actions():
    return os.environ.get("GITHUB_ACTIONS") == "true" or "--headless" in sys.argv

def prompt_user_fundamentals(name):
    if is_github_actions():
        return None
    print("\n" + "=" * 55 + f"\n [CLI Input Needed] Configure fundamentals for: {name}\n" + "=" * 55)
    sectors = list(SECTOR_BENCHMARKS.keys())
    for i in range(0, len(sectors), 2):
        item1 = f"{i+1:2d}. {sectors[i]:<26}"
        item2 = f"{i+2:2d}. {sectors[i+1]}" if i + 1 < len(sectors) else ""
        print(f"  {item1}   {item2}")

    try:
        sector = None
        while not sector:
            sec_choice = input(f"\nEnter sector choice (1-{len(sectors)}) or press Enter to skip: ").strip()
            if not sec_choice:
                return None
            if sec_choice.isdigit() and 1 <= int(sec_choice) <= len(sectors):
                sector = sectors[int(sec_choice) - 1]
            else:
                print(f"  [Error] Please enter a whole number between 1 and {len(sectors)}.")

        ps_ratio = None
        while ps_ratio is None:
            val = input("Current FY P/S Ratio (e.g., 4.5): ").strip()
            try:
                ps_ratio = float(val)
            except ValueError:
                print("  [Error] Please enter a valid decimal number.")

        cagr = None
        while cagr is None:
            val = input("3-Year Revenue CAGR % (e.g., 20): ").strip()
            try:
                cagr = float(val)
            except ValueError:
                print("  [Error] Please enter a valid decimal number.")

        print("\nSelect Use of Proceeds:")
        print("  1. Capex / Business Expansion / Growth / R&D")
        print("  2. Working Capital")
        print("  3. Debt Repayment / OFS (Offer for Sale)")
        proceeds = None
        while not proceeds:
            proc_choice = input("Enter option (1-3): ").strip()
            if proc_choice in ["1", "2", "3"]:
                proceeds = {
                    "1": "Capex and Business Expansion",
                    "2": "Working Capital",
                    "3": "Debt Repayment / OFS",
                }[proc_choice]
            else:
                print("  [Error] Please enter 1, 2, or 3.")

        promoter = None
        while promoter is None:
            val = input("Post-IPO Promoter Holding % (e.g., 65): ").strip()
            try:
                promoter = float(val)
            except ValueError:
                print("  [Error] Please enter a valid decimal number.")

        return {
            "sector": sector,
            "psRatio": ps_ratio,
            "cagr3Yr": cagr,
            "proceedsUse": proceeds,
            "promoterPct": promoter,
        }
    except (EOFError, KeyboardInterrupt):
        return None

def evaluate_decision(enrichment, sector_name):
    if not enrichment:
        return {
            "verdict": "Data will be updated soon",
            "verdictCls": "verdict-caution",
            "summary": "Awaiting fundamental RHP data entry for comparative analysis.",
            "checks": [{"icon": "ℹ", "cls": "check-warn", "text": "Fundamental metrics pending entry"}],
            "score": 0,
        }

    bench = SECTOR_BENCHMARKS.get(sector_name, {"avgPS": 3.5, "avgCagr": 15.0})
    ps, cagr, promoter = enrichment["psRatio"], enrichment["cagr3Yr"], enrichment["promoterPct"]
    proceeds = enrichment["proceedsUse"]
    score, checks = 0, []

    if ps <= bench["avgPS"]:
        checks.append({"icon": "✓", "cls": "check-pass", "text": f"Attractive P/S valuation ({ps}x vs Sector Average {bench['avgPS']}x)"})
        score += 2
    else:
        checks.append({"icon": "✗", "cls": "check-fail", "text": f"High P/S valuation ({ps}x vs Sector Average {bench['avgPS']}x)"})

    if cagr >= bench["avgCagr"]:
        checks.append({"icon": "✓", "cls": "check-pass", "text": f"Robust growth ({cagr}% vs Sector Average {bench['avgCagr']}%)"})
        score += 2
    else:
        checks.append({"icon": "✗", "cls": "check-fail", "text": f"Slow revenue growth ({cagr}% vs Sector Average {bench['avgCagr']}%)"})

    if promoter >= 50:
        checks.append({"icon": "✓", "cls": "check-pass", "text": f"High promoter confidence ({promoter}% holding)"})
        score += 1
    else:
        checks.append({"icon": "✗", "cls": "check-fail", "text": f"Low promoter holding ({promoter}%)"})

    if any(k in proceeds.lower() for k in ["capex", "growth", "working capital", "expansion"]):
        checks.append({"icon": "✓", "cls": "check-pass", "text": f"Major chunk of issue allocated to {proceeds}"})
        score += 1
    else:
        checks.append({"icon": "✗", "cls": "check-fail", "text": f"Major chunk of issue goes to {proceeds}"})

    if score >= 3:
        return {
            "verdict": "Apply",
            "verdictCls": "verdict-apply",
            "summary": f"Strong fundamentals outperforming historical benchmarks in {sector_name}.",
            "checks": checks,
            "score": score,
        }
    return {
        "verdict": "Avoid",
        "verdictCls": "verdict-avoid",
        "summary": f"Subpar valuation or growth metrics compared to peers in {sector_name}.",
        "checks": checks,
        "score": score,
    }

def display_date(value):
    return value.strftime("%d %B %Y") if value else "TBA"

def main():
    now = datetime.now(IST)
    print(f"\nIPO scraper started: {now:%d %b %Y %I:%M %p} IST")

    items, errors = scrape(now)
    previous = load_json(DATA_FILE, {})
    cache = load_json(CACHE_FILE, {})

    old_items = {
        key(item.get("name", "")): item
        for item in previous.get("upcoming", [])
        if item.get("name")
    }

    for k, old in old_items.items():
        if any(is_same_company(old.get("name"), x["name"]) for x in items):
            continue
        open_dt = parse_one_date(old.get("openDate"), now.year)
        close_dt = parse_one_date(old.get("closeDate"), now.year)
        if is_within_active_window(open_dt, close_dt, now):
            print(f"  [Retention] Retaining active open IPO from cache: {old.get('name')}")
            items.append({
                "name": old.get("name"),
                "category": old.get("category") or ("SME IPO" if "SME" in old.get("sector", "") else "Mainboard IPO"),
                "open": open_dt,
                "close": close_dt,
                "price": old.get("issuePrice"),
                "lot": old.get("lotSize"),
                "issueSize": old.get("issueSize", "TBA"),
            })

    output = []
    for item in items:
        match_old_k = find_matching_key(item["name"], old_items)
        old = old_items.get(match_old_k, {}) if match_old_k else {}

        match_cache_k = find_matching_key(item["name"], cache)
        enrichment = cache.get(match_cache_k) if match_cache_k else None

        if enrichment and enrichment.get("sector"):
            print(f"  [Cached] Loaded saved fundamentals for: {item['name']}")
        else:
            enrichment = prompt_user_fundamentals(item["name"])
            if enrichment:
                cache[key(item["name"])] = enrichment
                save_json(CACHE_FILE, cache)

        price = item.get("price") or old.get("issuePrice")
        lot = item.get("lot") or old.get("lotSize")
        size = item.get("issueSize") or old.get("issueSize") or "TBA"

        is_sme = item["category"] == "SME IPO"
        if not lot and not is_sme and price and price > 0:
            lot = max(1, int(15000 // price))

        lots_required = 1
        investment = round(price * lot * lots_required) if price and lot else None

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
            "openDate": display_date(item.get("open")),
            "closeDate": display_date(item.get("close")),
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

    output.sort(
        key=lambda item: (
            parse_one_date(item.get("closeDate", ""), now.year) or date.max,
            item.get("name", "").lower(),
        )
    )

    data = {
        "upcoming": output,
        "lastUpdated": now.strftime("%d %b %Y, %I:%M %p"),
        "scrapeWarnings": errors,
    }
    save_json(DATA_FILE, data)

    print(f"\n[OK] {len(output)} currently open IPO(s) written to data.json")
    for item in output:
        print(
            f"{item['name']} | {item['category']} | Price: {item['issuePrice']} | "
            f"Lot: {item['lotSize']} | Investment: {item['minAmount']} | "
            f"Issue size: {item['issueSize']} | Verdict: {item['verdict']}"
        )

    if errors:
        print(f"\n[WARN] {len(errors)} source(s) failed.")

if __name__ == "__main__":
    main()