"""
fetch_ipos.py — Automated IPO Decision Engine
- Parses both Day-First ('2-5 Nov', '30-3 Nov') and Month-First ('Nov 2-5, 2026') dates
- Prevents combined 'Open & Close Date' columns from corrupting close_dt
- Fetches ONLY currently OPEN IPOs (drops Closed after 5:00 PM IST and future Upcoming)
- Blocks Face Value (Rs 2, Rs 4, Rs 10) from corrupting Mainboard Lot Sizes
- Applies 2-lot minimum for SME IPOs and 1-lot for Mainboard IPOs
"""

import json
import os
import re
import sys
from datetime import datetime, date, time
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
    "engineers", "manufacturing", "infra", "infrastructure", "projects",
    "logistics", "finance", "financial", "capital", "housing", "bank",
    "energy", "power", "solar", "motors", "auto", "retail", "foods", "agro",
    "chemicals", "textiles", "exports", "traders", "trading", "polymers",
    "environmental", "alternatives", "jewels", "builders", "developers",
}

MONTH_MAP = {
    name: i
    for i, name in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        1,
    )
}


def month_num(val):
    if not val:
        return None
    low = val.strip().lower()
    return next((num for prefix, num in MONTH_MAP.items() if low.startswith(prefix)), None)


def session():
    client = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    client.mount("https://", HTTPAdapter(max_retries=retry))
    return client


def clean(value):
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def clean_company_name(raw_name):
    name = str(raw_name).split("(")[0].strip()
    name = re.sub(r"\b(?:BSE\s+SME[OUCL]?|NSE\s+SME[OUCL]?|SME[OUCL]?|IPO[OUCL]?|Ltd\.?|Limited|Pvt\.?|Private)\b", "", name, flags=re.I).strip()
    return re.sub(r"\s+", " ", name).strip(" -–.,")


def key(value):
    name = clean_company_name(value).lower()
    words = [w for w in re.findall(r"[a-z0-9]+", name)]
    filtered = [w for w in words if w not in NOISE_WORDS] or words
    return "".join(filtered)


def is_same_company(name_a, name_b):
    if not name_a or not name_b:
        return False
    ka, kb = key(name_a), key(name_b)
    return bool(ka and kb and (ka == kb or ka in kb or kb in ka))


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
    return [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", str(value))]


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
    if not value or str(value).strip().upper() in ["TBA", "NA", "-", "—", ""]:
        return None
    text = clean(value).replace(",", "").replace(".", "").strip()
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    year = int(year or datetime.now(IST).year)
    for fmt in (
        "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y",
        "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    for fmt in ("%d %B", "%d %b", "%B %d", "%b %d", "%d-%m", "%d/%m", "%d-%b"):
        try:
            parsed = datetime.strptime(f"{text} {year}", f"{fmt} %Y")
            return date(year, parsed.month, parsed.day)
        except ValueError:
            pass
    return None


def build_date_pair(d1, m1, d2, m2, yr, default_year):
    end_month = month_num(m2) or month_num(m1)
    start_month = month_num(m1)
    if not end_month:
        return None, None
    try:
        y_end = int(yr or default_year)
        y_start = y_end
        if not start_month:
            if int(d1) > int(d2):
                if end_month == 1:
                    start_month = 12
                    y_start = y_end - 1
                else:
                    start_month = end_month - 1
            else:
                start_month = end_month
        elif start_month == 12 and end_month == 1:
            y_start = y_end - 1
        return date(y_start, start_month, int(d1)), date(y_end, end_month, int(d2))
    except ValueError:
        return None, None


def parse_dates(value):
    if not value:
        return None, None
    text = clean(value).replace(",", " ").replace(".", "").strip()
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    text = re.sub(r"\s+(?:to|till|until)\s+", " - ", text, flags=re.I)
    text = text.replace("–", "-").replace("—", "-")
    year = datetime.now(IST).year

    # Pattern 1: Day-First e.g. "2-5 November 2026", "30 Oct - 3 Nov 2026", "30-3 November"
    p_day_first = r"\b(\d{1,2})\s*([A-Za-z]{3,9})?\s*-\s*(\d{1,2})\s*([A-Za-z]{3,9})\s*(20\d{2})?\b"
    m = re.search(p_day_first, text)
    if m:
        d1, m1, d2, m2, yr = m.groups()
        s, e = build_date_pair(d1, m1, d2, m2, yr, year)
        if s and e:
            return s, e

    # Pattern 2: Month-First e.g. "November 2-5 2026", "Oct 30 - Nov 3 2026"
    p_month_first = r"\b([A-Za-z]{3,9})\s*(\d{1,2})\s*-\s*([A-Za-z]{3,9})?\s*(\d{1,2})\s*(20\d{2})?\b"
    m2_match = re.search(p_month_first, text)
    if m2_match:
        m1, d1, m2, d2, yr = m2_match.groups()
        s, e = build_date_pair(d1, m1, d2, m2 or m1, yr, year)
        if s and e:
            return s, e

    parts = re.split(r"\s*-\s*", text, maxsplit=1)
    if len(parts) == 2:
        start = parse_one_date(parts[0], year)
        end = parse_one_date(parts[1], year)
        if start and end:
            if start > end:
                m_prev = end.month - 1 or 12
                y_prev = end.year - 1 if end.month == 1 else end.year
                try:
                    start = date(y_prev, m_prev, start.day)
                except ValueError:
                    pass
            return start, end
    return None, None


def category_for(name, row_text, url):
    return "SME IPO" if "sme" in f"{name} {row_text} {url}".lower() else "Mainboard IPO"


def extract_tables(soup, url):
    records = []
    current_year = datetime.now(IST).year
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        headers, header_row = None, None
        for row in rows[:3]:
            labels = [clean(c.get_text(" ", strip=True)).lower() for c in row.find_all(["th", "td"], recursive=False)]
            if any(t in l for l in labels for t in ("company", "ipo name", "ipo date", "opening", "open", "closing", "close", "size", "price", "lot", "bidding")):
                headers, header_row = labels, row
                break
        if headers is None:
            continue

        for row in rows:
            if row is header_row:
                continue
            values = [clean(c.get_text(" ", strip=True)) for c in row.find_all(["td", "th"], recursive=False)]
            if len(values) < 2:
                continue
            mapped = {headers[i]: value for i, value in enumerate(values) if i < len(headers)}
            raw_name = values[0]
            if raw_name.lower() in {"company", "company name", "ipo name", "ipo", "company ipo", "issuer"}:
                continue
            if any(b in raw_name.lower() for b in ("anchor", "sse", "social stock", "apply", "reit", "invit")):
                continue
            name = clean_company_name(raw_name)
            if len(name) < 3:
                continue
            row_text = " ".join(values)

            # 1. Try combined range parsing on any date-like column first (including 'Open & Close Date')
            start = end = None
            for heading, value in mapped.items():
                if "anchor" in heading.lower():
                    continue
                if any(t in heading for t in ("date", "bidding", "subscription", "open", "close")):
                    s, e = parse_dates(value)
                    if s and e:
                        start, end = s, e
                        break

            # 2. If not a combined range, check separate Open and Close columns strictly
            if not start or not end:
                open_col = next(
                    (v for h, v in mapped.items() if "open" in h and "close" not in h and "closing" not in h and "anchor" not in h.lower()),
                    None,
                )
                close_col = next(
                    (v for h, v in mapped.items() if ("close" in h or "closing" in h) and "open" not in h and "anchor" not in h.lower()),
                    None,
                )
                if open_col and not start:
                    start = parse_one_date(open_col, current_year)
                if close_col and not end:
                    end = parse_one_date(close_col, current_year)
                if start and end and start > end:
                    m_prev = end.month - 1 or 12
                    y_prev = end.year - 1 if end.month == 1 else end.year
                    try:
                        start = date(y_prev, m_prev, start.day)
                    except ValueError:
                        pass

            # 3. Last-resort scan across all cells in the row for a date range
            if not start or not end:
                for value in values[1:]:
                    s, e = parse_dates(value)
                    if s and e:
                        start, end = s, e
                        break

            price = None
            for heading, value in mapped.items():
                if any(t in heading for t in ("price", "band", "range")) and "face" not in heading:
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
                if any(t in heading for t in ("size", "amount", "cr")) and not any(t in heading for t in ("lot", "share", "price", "face")):
                    size = issue_size(value, heading)
                    if size:
                        break
            if size is None:
                for value in values:
                    if "cr" in value.lower() or "crore" in value.lower():
                        size = issue_size(value, "")
                        if size:
                            break

            lot = None
            for heading, value in mapped.items():
                if any(t in heading for t in ("face", "fv", "price", "size", "amount", "date")):
                    continue
                if any(t in heading for t in ("lot", "qty", "quantity", "shares")):
                    if value.lower() in {"tba", "na", "n/a", "-", "—", ""}:
                        continue
                    for v in numbers(value):
                        if v.is_integer() and 11 <= int(v) <= 100000 and int(v) != current_year:
                            if price and int(v) == int(price):
                                continue
                            lot = int(v)
                            break
                    if lot:
                        break
            if lot is None:
                for value in values:
                    low_val = value.lower()
                    if any(w in low_val for w in ("shares", "lot")) or not any(sym in low_val for sym in ["₹", "rs", "-", "–", "to", "%", "cr", "crore", "."]):
                        for n in re.findall(r"\b\d{2,5}\b", value.replace(",", "")):
                            v = int(n)
                            if 11 <= v <= 15000 and v != current_year and (not price or v != int(price)):
                                lot = v
                                break
                        if lot:
                            break

            records.append({"name": name, "category": category_for(raw_name, row_text, url), "open": start, "close": end, "price": price, "lot": lot, "issueSize": size, "source": url})
    return records


def is_currently_open(open_dt, close_dt, now):
    """
    Strictly returns True ONLY for IPOs currently open for bidding right now:
    - Excludes Closed IPOs (close_dt < today, or close_dt == today after 17:00 IST)
    - Excludes Upcoming IPOs (open_dt > today)
    """
    today = now.date()
    if not close_dt or close_dt < today:
        return False
    if close_dt == today and now.time() >= time(17, 0):
        return False
    if open_dt:
        if open_dt > close_dt:
            m = close_dt.month - 1 or 12
            y = close_dt.year - 1 if close_dt.month == 1 else close_dt.year
            try:
                open_dt = date(y, m, open_dt.day)
            except ValueError:
                return 0 <= (close_dt - today).days <= 4
        return open_dt <= today <= close_dt
    return 0 <= (close_dt - today).days <= 4


def scrape(now):
    client = session()
    merged, errors = {}, []
    for url in SOURCES:
        try:
            response = client.get(url, headers=HEADERS, timeout=25)
            response.raise_for_status()
            for item in extract_tables(BeautifulSoup(response.text, "html.parser"), url):
                match_k = find_matching_key(item["name"], merged)
                if not match_k:
                    k = key(item["name"])
                    if k:
                        merged[k] = item
                    continue
                old = merged[match_k]
                for field in ("price", "lot", "issueSize"):
                    if old.get(field) is None and item.get(field) is not None:
                        old[field] = item[field]
                # If the new record has a valid (open, close) pair where open < close, prefer it
                if item.get("open") and item.get("close") and item["open"] <= item["close"]:
                    if not old.get("open") or not old.get("close") or old["open"] >= old["close"] or item["close"] > old["close"]:
                        old["open"] = item["open"]
                        old["close"] = item["close"]
                else:
                    if item.get("open") and not old.get("open"):
                        old["open"] = item["open"]
                    if item.get("close") and (not old.get("close") or item["close"] > old["close"]):
                        old["close"] = item["close"]
                if item["category"] == "SME IPO":
                    old["category"] = "SME IPO"
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")

    open_ipos = [item for item in merged.values() if is_currently_open(item.get("open"), item.get("close"), now)]
    return open_ipos, errors


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
        print(f"  {i+1:2d}. {sectors[i]:<26}   {f'{i+2:2d}. {sectors[i+1]}' if i+1 < len(sectors) else ''}")
    try:
        sector = None
        while not sector:
            sec_choice = input(f"\nEnter sector choice (1-{len(sectors)}) or Enter to skip: ").strip()
            if not sec_choice:
                return None
            if sec_choice.isdigit() and 1 <= int(sec_choice) <= len(sectors):
                sector = sectors[int(sec_choice) - 1]
        ps_ratio = float(input("Current FY P/S Ratio (e.g., 4.5): ").strip())
        cagr = float(input("3-Year Revenue CAGR % (e.g., 20): ").strip())
        print("\nSelect Use of Proceeds:\n  1. Capex / Growth\n  2. Working Capital\n  3. Debt Repayment / OFS")
        proceeds = {"1": "Capex and Business Expansion", "2": "Working Capital", "3": "Debt Repayment / OFS"}[input("Enter option (1-3): ").strip()]
        promoter = float(input("Post-IPO Promoter Holding % (e.g., 65): ").strip())
        return {"sector": sector, "psRatio": ps_ratio, "cagr3Yr": cagr, "proceedsUse": proceeds, "promoterPct": promoter}
    except (EOFError, KeyboardInterrupt, ValueError, KeyError):
        return None


def evaluate_decision(enrichment, sector_name):
    if not enrichment:
        return {"verdict": "Data will be updated soon", "verdictCls": "verdict-caution", "summary": "Awaiting fundamental RHP data entry.", "checks": [{"icon": "ℹ", "cls": "check-warn", "text": "Fundamental metrics pending"}], "score": 0}
    bench = SECTOR_BENCHMARKS.get(sector_name, {"avgPS": 3.5, "avgCagr": 15.0})
    ps, cagr, promoter, proceeds = enrichment["psRatio"], enrichment["cagr3Yr"], enrichment["promoterPct"], enrichment["proceedsUse"]
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
        return {"verdict": "Apply", "verdictCls": "verdict-apply", "summary": f"Strong fundamentals in {sector_name}.", "checks": checks, "score": score}
    return {"verdict": "Avoid", "verdictCls": "verdict-avoid", "summary": f"Subpar metrics in {sector_name}.", "checks": checks, "score": score}


def display_date(value):
    return value.strftime("%d %B %Y") if value else "TBA"


def main():
    now = datetime.now(IST)
    print(f"\nIPO scraper started: {now:%d %b %Y %I:%M %p} IST")
    items, errors = scrape(now)
    previous, cache = load_json(DATA_FILE, {}), load_json(CACHE_FILE, {})
    old_items = {key(item.get("name", "")): item for item in previous.get("upcoming", []) if item.get("name")}

    output = []
    for item in items:
        match_old_k = find_matching_key(item["name"], old_items)
        match_cache_k = find_matching_key(item["name"], cache)
        old = old_items.get(match_old_k, {}) if match_old_k else {}
        enrichment = cache.get(match_cache_k) if match_cache_k else None

        if enrichment and enrichment.get("sector"):
            print(f"  [Cached] Loaded fundamentals for: {item['name']} (Open)")
        else:
            enrichment = prompt_user_fundamentals(item["name"])
            if enrichment:
                cache[key(item["name"])] = enrichment
                save_json(CACHE_FILE, cache)

        price = item.get("price") or old.get("issuePrice")
        is_sme = item["category"] == "SME IPO"

        old_lot = old.get("lotSize")
        if old_lot is not None and old_lot < 11:
            old_lot = None
        lot = item.get("lot") or old_lot
        if not lot and not is_sme and price and price > 0:
            lot = max(1, int(15000 // price))

        investment = round(price * lot * (2 if is_sme else 1)) if price and lot else None
        size = item.get("issueSize") or old.get("issueSize") or "TBA"
        sector_name = enrichment.get("sector", "Pending Entry") if enrichment else "Pending Entry"
        decision = evaluate_decision(enrichment, sector_name)
        historical_gain = f"{SECTOR_BENCHMARKS[sector_name]['avgListingGain']}% avg listing gain in last 1 yr" if enrichment and sector_name in SECTOR_BENCHMARKS else "Data pending"

        print(f"  -> {item['name']} | Open ({display_date(item.get('open'))} to {display_date(item.get('close'))}) | {item['category']} | Price: {price} | Lot: {lot} | Inv: {investment}")

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

    output.sort(key=lambda x: (parse_one_date(x.get("closeDate", ""), now.year) or date.max, x.get("name", "").lower()))
    save_json(DATA_FILE, {"upcoming": output, "lastUpdated": now.strftime("%d %b %Y, %I:%M %p"), "scrapeWarnings": errors})
    print(f"\n[OK] {len(output)} currently OPEN IPO(s) written to data.json")


if __name__ == "__main__":
    main()