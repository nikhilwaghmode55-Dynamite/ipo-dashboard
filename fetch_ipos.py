
"""
IPO Dashboard Engine
Scraping + fundamentals cache + transparent benchmark-based assessment.

Benchmark schema in ipo_cache.json:
{
  "version": 1,
  "benchmarks": {
    "Industrials": {
      "psRatioMax": 5.0,
      "cagrMin": 10.0,
      "promoterMin": 25.0,
      "preferredProceedsUse": ["Capex / Business Expansion / Growth / R&D"]
    }
  },
  "ipos": {}
}

Only add benchmark values after verifying their source, peer group and date.
"""

import json
import re
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

LOT_FALLBACKS = {
    "tonboimaging": 21,
    "zetwerk": 99,
    "sahajanandmedical": 43,
    "sahajanandmedicaltechnologies": 43,
}

ALIASES = {
    "bonfigliolitransmissions": [
        "bonfiglioli", "bonfigliolitransmissions"
    ],
    "tonboimaging": ["tonbo", "tonboimaging"],
    "zetwerk": ["zetwerk"],
    "sahajanandmedical": [
        "sahajanand", "sahajanandmedical",
        "sahajanandmedicaltechnologies"
    ],
    "shellzindia": ["shellz", "shellzindia"],
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


def key(value):
    return re.sub(r"[^a-z0-9]", "", clean(value).lower())


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
    elif "crore" not in units and not re.search(r"\bcr\b", units):
        return None

    return f"₹{amount:,.2f}".rstrip("0").rstrip(".") + " Cr"


def parse_one_date(value, year=None):
    text = clean(value)
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    year = int(year or datetime.now(IST).year)

    for fmt in (
        "%d %B %Y", "%d %b %Y", "%d-%m-%Y",
        "%d/%m/%Y", "%d-%b-%Y", "%Y-%m-%d",
    ):
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

    pattern = (
        r"(\d{1,2})\s*([A-Za-z]{3,9})?\s*-\s*"
        r"(\d{1,2})\s*([A-Za-z]{3,9})\s*(20\d{2})?"
    )
    match = re.search(pattern, text)

    if match:
        d1, m1, d2, m2, yr = match.groups()
        months = {
            name: i for i, name in enumerate(
                ["jan", "feb", "mar", "apr", "may", "jun",
                 "jul", "aug", "sep", "oct", "nov", "dec"], 1
            )
        }

        def month_num(value):
            if not value:
                return None
            return next(
                (num for prefix, num in months.items()
                 if value.lower().startswith(prefix)),
                None,
            )

        end_month = month_num(m2)
        start_month = month_num(m1) or end_month

        if start_month and end_month:
            try:
                y = int(yr or year)
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


def category_for(name, row_text, url):
    if "sme" in f"{name} {row_text} {url}".lower():
        return "SME IPO"
    return "Mainboard IPO"


def extract_tables(soup, url):
    records = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers = None
        header_row = None

        for row in rows[:3]:
            labels = [
                clean(cell.get_text(" ", strip=True)).lower()
                for cell in row.find_all(["th", "td"], recursive=False)
            ]
            if any(
                term in label
                for label in labels
                for term in (
                    "company", "ipo name", "ipo date", "opening date",
                    "closing date", "close date", "issue size",
                    "ipo size", "price band", "lot size", "bidding dates",
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

            mapped = {
                headers[i]: value
                for i, value in enumerate(values)
                if i < len(headers)
            }
            name = values[0]

            if name.lower() in {
                "company", "company name", "ipo name", "ipo", "company ipo"
            }:
                continue

            row_text = " ".join(values)
            start = end = None

            for heading, value in mapped.items():
                if any(t in heading for t in (
                    "ipo date", "bidding dates", "open date",
                    "opening date", "date"
                )):
                    start, end = parse_dates(value)
                    if start and end:
                        break

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
                    if re.search(
                        r"(?:₹|Rs\.?)\s*[\d,]+\s*(?:to|-|–)\s*"
                        r"(?:₹|Rs\.?)?\s*[\d,]+",
                        value, re.I
                    ):
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

            lot = None
            lot_terms = (
                "lot size", "market lot", "minimum lot", "lot",
                "minimum bid", "bid lot", "shares per lot",
                "minimum application", "quantity", "qty",
            )
            for heading, value in mapped.items():
                if not any(t in heading for t in lot_terms):
                    continue
                if value.lower() in {"tba", "na", "n/a", "-", "—", ""}:
                    continue
                vals = numbers(value)
                if vals and vals[0].is_integer() and 1 <= vals[0] <= 100000:
                    lot = int(vals[0])
                    break

            records.append({
                "name": name,
                "category": category_for(name, row_text, url),
                "open": start,
                "close": end,
                "price": price,
                "lot": lot,
                "issueSize": size,
                "source": url,
            })

    return records


def scrape():
    client = session()
    merged, errors = {}, []

    for url in SOURCES:
        try:
            response = client.get(url, headers=HEADERS, timeout=25)
            response.raise_for_status()
            records = extract_tables(
                BeautifulSoup(response.text, "html.parser"), url
            )

            for item in records:
                k = key(item["name"])
                if not k:
                    continue

                if k not in merged:
                    merged[k] = item
                    continue

                old = merged[k]
                for field in ("price", "lot", "issueSize", "open", "close"):
                    if old.get(field) is None and item.get(field) is not None:
                        old[field] = item[field]

                if item["category"] == "SME IPO":
                    old["category"] = "SME IPO"

        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")

    today = datetime.now(IST).date()
    opened = [
        item for item in merged.values()
        if item.get("open") and item.get("close")
        and item["open"] <= today <= item["close"]
    ]
    return opened, errors, sum(1 for _ in merged)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def load_cache():
    cache = load_json(CACHE_FILE, {})
    if not isinstance(cache, dict):
        cache = {}
    cache.setdefault("version", 1)
    cache.setdefault("benchmarks", {})
    cache.setdefault("ipos", {})
    return cache


def fundamentals_for(name, cache):
    """Support both full normalized keys and older short aliases."""
    normalized = key(name)
    candidates = [normalized]

    for canonical, aliases in ALIASES.items():
        if normalized == canonical or normalized in aliases:
            candidates = [canonical] + aliases + [normalized]
            break

    # Entries may be stored directly at the root (legacy format).
    for candidate in candidates:
        value = cache.get(candidate)
        if isinstance(value, dict) and any(
            field in value for field in (
                "psRatio", "cagr3Yr", "promoterPct", "proceedsUse", "sector"
            )
        ):
            return value

    # Preferred nested format: cache["ipos"][key].
    ipos = cache.get("ipos", {})
    if isinstance(ipos, dict):
        normalized_ipos = {key(k): v for k, v in ipos.items()}
        for candidate in candidates:
            value = normalized_ipos.get(key(candidate))
            if isinstance(value, dict):
                return value

    return {}


def assess(fundamentals, cache):
    """
    Calculate a score only when all four required inputs and the
    corresponding sector benchmark thresholds are present.
    No default benchmark values are invented.
    """
    required = ("sector", "psRatio", "cagr3Yr", "promoterPct", "proceedsUse")
    missing = [field for field in required if fundamentals.get(field) in (None, "")]

    if missing:
        return {
            "score": None,
            "verdict": "DATA PENDING",
            "verdictCls": "verdict-caution",
            "summary": "Missing fundamentals: " + ", ".join(missing) + ".",
            "historicalGain": "Benchmark comparison pending.",
            "checks": [{
                "icon": "⏳",
                "cls": "check-warn",
                "text": f"Missing required field: {field}",
            } for field in missing],
        }

    sector = fundamentals["sector"]
    benchmarks = cache.get("benchmarks", {})
    benchmark = benchmarks.get(sector, {}) if isinstance(benchmarks, dict) else {}

    benchmark_fields = (
        "psRatioMax", "cagrMin", "promoterMin", "preferredProceedsUse"
    )
    missing_benchmarks = [
        field for field in benchmark_fields
        if benchmark.get(field) is None
    ]

    checks = [
        {
            "icon": "ℹ️",
            "cls": "check-warn",
            "text": f"Sector: {sector}",
        },
        {
            "icon": "ℹ️",
            "cls": "check-warn",
            "text": f"P/S ratio entered: {fundamentals['psRatio']} (not independently verified)",
        },
        {
            "icon": "ℹ️",
            "cls": "check-warn",
            "text": f"3-year revenue CAGR entered: {fundamentals['cagr3Yr']}% (not independently verified)",
        },
        {
            "icon": "ℹ️",
            "cls": "check-warn",
            "text": f"Post-IPO promoter holding entered: {fundamentals['promoterPct']}% (not independently verified)",
        },
        {
            "icon": "ℹ️",
            "cls": "check-warn",
            "text": f"Use of proceeds entered: {fundamentals['proceedsUse']}",
        },
    ]

    if missing_benchmarks:
        checks.append({
            "icon": "⏳",
            "cls": "check-warn",
            "text": "Sector benchmarks missing: " + ", ".join(missing_benchmarks),
        })
        return {
            "score": None,
            "verdict": "DATA PENDING",
            "verdictCls": "verdict-caution",
            "summary": (
                "Fundamentals are entered, but a defensible sector comparison "
                "cannot be calculated until verified benchmarks are configured."
            ),
            "historicalGain": "No verified sector benchmark configured.",
            "checks": checks,
        }

    preferred = benchmark["preferredProceedsUse"]
    if not isinstance(preferred, list):
        preferred = [preferred]

    results = [
        ("P/S within configured ceiling", float(fundamentals["psRatio"]) <= float(benchmark["psRatioMax"])),
        ("Revenue CAGR meets configured minimum", float(fundamentals["cagr3Yr"]) >= float(benchmark["cagrMin"])),
        ("Promoter holding meets configured minimum", float(fundamentals["promoterPct"]) >= float(benchmark["promoterMin"])),
        ("Proceeds use matches configured preference", fundamentals["proceedsUse"] in preferred),
    ]

    for label, passed in results:
        checks.append({
            "icon": "✓" if passed else "!",
            "cls": "check-pass" if passed else "check-fail",
            "text": label + (": Pass" if passed else ": Does not meet threshold"),
        })

    score = round(sum(passed for _, passed in results) / len(results) * 100)
    verdict = "MEETS CONFIGURED CHECKS" if score >= 75 else "REVIEW REQUIRED"

    return {
        "score": score,
        "verdict": verdict,
        "verdictCls": "verdict-apply" if score >= 75 else "verdict-caution",
        "summary": (
            f"{sum(passed for _, passed in results)} of {len(results)} configured "
            f"checks passed. This is a screening result, not an investment recommendation."
        ),
        "historicalGain": "Sector thresholds configured; not a historical listing-return prediction.",
        "checks": checks,
    }


def display_date(value):
    return value.strftime("%d %B %Y") if value else "TBA"


def main():
    now = datetime.now(IST)
    print(f"\nIPO scraper started: {now:%d %b %Y %I:%M %p} IST")

    items, errors, total_rows = scrape()
    previous = load_json(DATA_FILE, {})
    cache = load_cache()

    old_items = {
        key(item.get("name", "")): item
        for item in previous.get("upcoming", [])
        if item.get("name")
    }

    if not items and (total_rows == 0 or len(errors) == len(SOURCES)):
        print("[WARN] No usable scrape results. Keeping existing data.json.")
        for error in errors:
            print(" -", error)
        return

    output, scraped_keys = [], set()

    for item in items:
        k = key(item["name"])
        scraped_keys.add(k)
        old = old_items.get(k, {})
        fundamentals = fundamentals_for(item["name"], cache)
        assessment = assess(fundamentals, cache)

        price = item.get("price") or old.get("issuePrice")
        lot = item.get("lot") or LOT_FALLBACKS.get(k) or old.get("lotSize")
        size = item.get("issueSize") or old.get("issueSize") or "TBA"

        is_sme = item["category"] == "SME IPO"
        lots_required = 2 if is_sme else 1
        investment = round(price * lot * lots_required) if price and lot else None

        sector = fundamentals.get("sector") or old.get(
            "sector", f"Pending Entry ({item['category']})"
        )

        output.append({
            "slug": re.sub(r"[^a-z0-9]+", "-", item["name"].lower()).strip("-"),
            "name": item["name"],
            "symbol": old.get("symbol", ""),
            "sector": sector,
            "category": item["category"],
            "status": "Open",
            "openDate": display_date(item.get("open")),
            "closeDate": display_date(item.get("close")),
            "issuePrice": price,
            "lotSize": lot,
            "minAmount": investment,
            "issueSize": size,
            "fundamentals": fundamentals,
            "historicalGain": assessment["historicalGain"],
            "summary": assessment["summary"],
            "verdict": assessment["verdict"],
            "verdictCls": assessment["verdictCls"],
            "checks": assessment["checks"],
            "score": assessment["score"],
            "lastFetched": now.strftime("%d %b %Y, %I:%M %p"),
        })

    today = now.date()
    for k, old in old_items.items():
        if k in scraped_keys:
            continue
        close_date = old.get("closeDate")
        close_dt = parse_one_date(close_date, now.year) if close_date else None

        if close_dt and close_dt >= today:
            print(f"  [Retention] Keeping previous entry: {old.get('name')}")
            output.append(old)

    if not output and previous.get("upcoming"):
        print("[WARN] No open IPOs returned. Keeping previous dashboard data.")
        return

    output.sort(key=lambda item: (
        parse_one_date(item.get("closeDate", ""), now.year) or date.max,
        item.get("name", "").lower(),
    ))

    data = {
        "upcoming": output,
        "lastUpdated": now.strftime("%d %b %Y, %I:%M %p"),
        "scrapeWarnings": errors,
    }

    temp = DATA_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temp.replace(DATA_FILE)

    print(f"\n[OK] {len(output)} IPO(s) written to data.json")
    for item in output:
        print(
            f"{item['name']} | {item['category']} | "
            f"Price: {item['issuePrice']} | Lot: {item['lotSize']} | "
            f"Investment: {item['minAmount']} | Score: {item['score']} | "
            f"Verdict: {item['verdict']}"
        )

    if errors:
        print(f"\n[WARN] {len(errors)} source(s) failed.")
        for error in errors:
            print(" -", error)


if __name__ == "__main__":
    main()
