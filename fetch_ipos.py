
import json
import re
import sys
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data.json"
IST = ZoneInfo("Asia/Kolkata")

SOURCES = [
    "https://ipowatch.in/upcoming-ipo-list/",
    "https://ipowatch.in/upcoming-ipo-this-week/",
    "https://ipowatch.in/mainboard-ipo-2026/",
    "https://ipowatch.in/sme-ipo-2026/",
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
    return [
        float(x.replace(",", ""))
        for x in re.findall(r"\d[\d,]*(?:\.\d+)?", text)
    ]


def price_band(text):
    nums = [n for n in numeric_values(text) if 1 <= n <= 100000]
    return max(nums) if nums else None


def issue_size(text, heading=""):
    """Return issue size in crore only when its unit is identifiable."""
    raw = clean(text)
    if not raw or raw.lower() in {"tba", "na", "n/a", "-", "—"}:
        return None

    nums = numeric_values(raw)
    if not nums:
        return None

    amount = nums[0]
    unit_text = (heading + " " + raw).lower()

    if "billion" in unit_text:
        amount *= 100
    elif "million" in unit_text:
        amount /= 10
    elif "lakh" in unit_text:
        amount /= 1000
    elif "crore" in unit_text or re.search(r"\bcr\b", unit_text):
        pass
    else:
        # Never assume a bare number is already in crore.
        return None

    return f"₹{amount:,.2f}".rstrip("0").rstrip(".") + " Cr"


def parse_one_date(text, year=None):
    text = clean(text)
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    year = year or datetime.now(IST).year

    formats = [
        "%d %B %Y", "%d %b %Y",
        "%d-%m-%Y", "%d/%m/%Y",
        "%d-%b-%Y", "%Y-%m-%d",
        "%d %B", "%d %b",
    ]

    for fmt in formats:
        candidate = text if "%Y" in fmt else f"{text} {year}"
        try:
            result = datetime.strptime(candidate, fmt).date()
            return result
        except ValueError:
            continue

    return None


def parse_dates(text):
    """Parse date ranges such as 15-19 October or 15-19 October 2026."""
    text = clean(text)
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    text = text.replace(" to ", " - ").replace("–", "-").replace("—", "-")
    year = datetime.now(IST).year

    patterns = [
        r"(\d{1,2})\s*([A-Za-z]{3,9})?\s*-\s*"
        r"(\d{1,2})\s*([A-Za-z]{3,9})\s*(20\d{2})?",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue

        d1, m1, d2, m2, yr = match.groups()
        month_names = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4,
            "may": 5, "jun": 6, "jul": 7, "aug": 8,
            "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }

        def month_number(value):
            if not value:
                return None
            return next(
                (v for k, v in month_names.items()
                 if value.lower().startswith(k)),
                None,
            )

        m_end = month_number(m2)
        m_start = month_number(m1) or m_end

        if m_start and m_end:
            try:
                y = int(yr or year)
                return (
                    date(y, m_start, int(d1)),
                    date(y, m_end, int(d2)),
                )
            except ValueError:
                pass

    # Fallback for two full dates separated by a hyphen.
    parts = re.split(r"\s+-\s+", text, maxsplit=1)
    if len(parts) == 2:
        start = parse_one_date(parts[0], year)
        end = parse_one_date(parts[1], year)
        if start and end:
            return start, end

    # A single date is not sufficient to infer an opening and closing date.
    return None, None


def get_category(name, row_text, url):
    text = f"{name} {row_text} {url}".lower()
    if "sme" in text or "sme-ipo" in url.lower():
        return "SME IPO"
    return "Mainboard IPO"


def extract_tables(soup, url):
    records = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        header_row = None
        headers = []

        for row in rows[:3]:
            cells = row.find_all(["th", "td"], recursive=False)
            labels = [clean(c.get_text(" ", strip=True)).lower()
                      for c in cells]

            if any(
                term in label
                for label in labels
                for term in (
                    "company", "ipo name", "ipo date",
                    "opening date", "closing date", "close date",
                    "issue size", "ipo size", "price band",
                    "lot size", "bidding dates",
                )
            ):
                header_row = row
                headers = labels
                break

        if not header_row:
            continue

        for row in rows:
            if row is header_row:
                continue

            cells = row.find_all(["td", "th"], recursive=False)
            if not cells:
                continue

            values = [clean(c.get_text(" ", strip=True)) for c in cells]
            if not values or len(values) < 2:
                continue

            # Match each value to its heading.
            mapped = {}
            for i, value in enumerate(values):
                heading = headers[i] if i < len(headers) else ""
                mapped[heading] = value

            name = values[0]
            if name.lower() in {
                "company", "company name", "ipo name", "ipo",
                "company ipo",
            }:
                continue

            row_text = " ".join(values)
            low = row_text.lower()

            # Find the date range, preferring date-labelled cells.
            start_date = end_date = None
            for heading, value in mapped.items():
                if any(x in heading for x in (
                    "ipo date", "bidding dates", "open date",
                    "opening date", "date",
                )):
                    start_date, end_date = parse_dates(value)
                    if start_date and end_date:
                        break

            if not start_date or not end_date:
                for value in values:
                    a, b = parse_dates(value)
                    if a and b:
                        start_date, end_date = a, b
                        break

            # Find price band from the correctly labelled column.
            price = None
            for heading, value in mapped.items():
                if "price band" in heading or "price range" in heading:
                    price = price_band(value)
                    if price:
                        break

            if price is None:
                for value in values:
                    if re.search(
                        r"(?:₹|Rs\.?)\s*[\d,]+\s*(?:to|-|–)\s*"
                        r"(?:₹|Rs\.?)?\s*[\d,]+",
                        value,
                        re.I,
                    ):
                        price = price_band(value)
                        if price:
                            break

            # Extract issue size only from a clearly labelled column.
            size = None
            for heading, value in mapped.items():
                if "issue size" in heading or "ipo size" in heading:
                    size = issue_size(value, heading)
                    if size:
                        break

            # Lot size is generally available on detailed IPO calendar pages.
            lot = None
            for heading, value in mapped.items():
                if "lot size" in heading or "market lot" in heading:
                    nums = numeric_values(value)
                    if nums and nums[0].is_integer() and 1 <= nums[0] <= 100000:
                        lot = int(nums[0])
                        break

            records.append({
                "name": name,
                "category": get_category(name, low, url),
                "open": start_date,
                "close": end_date,
                "price": price,
                "lot": lot,
                "issueSize": size,
                "source": url,
            })

    return records


def currently_open(item, today):
    return (
        item["open"] is not None
        and item["close"] is not None
        and item["open"] <= today <= item["close"]
    )


def scrape():
    s = session()
    merged = {}
    errors = []

    for url in SOURCES:
        try:
            response = s.get(url, headers=HEADERS, timeout=25)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            found = extract_tables(soup, url)
            print(f"[SOURCE] {url}: extracted {len(found)} table rows")

            for item in found:
                k = key(item["name"])
                if not k:
                    continue

                if k not in merged:
                    merged[k] = item
                    continue

                old = merged[k]
                # Merge non-empty fields from multiple sources.
                for field in ("price", "lot", "issueSize", "open", "close"):
                    if old.get(field) is None and item.get(field) is not None:
                        old[field] = item[field]

                if item["category"] == "SME IPO":
                    old["category"] = "SME IPO"

        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
            print(f"[WARN] Could not scrape {url}: {exc}")

    if not merged and errors:
        raise RuntimeError("All IPO sources failed. Existing data.json was preserved.")

    today = datetime.now(IST).date()
    return [x for x in merged.values() if currently_open(x, today)], errors


def display_date(value):
    return value.strftime("%d %B %Y") if value else "TBA"


def main():
    now = datetime.now(IST)
    print(f"\nIPO scraper started: {now:%d %b %Y %I:%M %p} IST")

    try:
        items, errors = scrape()
    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    # Load old data only to retain optional fields not supplied by the scraper.
    try:
        previous = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {}

    old_items = {
        key(x.get("name", "")): x
        for x in previous.get("upcoming", [])
    }

    output = []

    for item in items:
        old = old_items.get(key(item["name"]), {})

        price = item["price"] or old.get("issuePrice")
        lot = item["lot"] or old.get("lotSize")
        size = item["issueSize"] or old.get("issueSize") or "TBA"

        is_sme = item["category"] == "SME IPO"
        multiplier = 2 if is_sme else 1
        investment = round(price * lot * multiplier) if price and lot else None

        output.append({
            "slug": re.sub(r"[^a-z0-9]+", "-", item["name"].lower()).strip("-"),
            "name": item["name"],
            "symbol": old.get("symbol", ""),
            "sector": f"Pending Entry ({item['category']})",
            "category": item["category"],
            "status": "Open",
            "openDate": display_date(item["open"]),
            "closeDate": display_date(item["close"]),
            "issuePrice": price,
            "lotSize": lot,
            "minAmount": investment,
            "issueSize": size,
            "historicalGain": old.get(
                "historicalGain",
                "Fundamental data pending entry for sector benchmarking",
            ),
            "summary": old.get(
                "summary",
                "Live issue details scraped automatically; fundamentals require separate verification.",
            ),
            "verdict": old.get("verdict", "Data will be updated soon"),
            "verdictCls": old.get("verdictCls", "verdict-caution"),
            "checks": old.get("checks", []),
            "score": old.get("score"),
            "lastFetched": now.strftime("%d %b %Y, %I:%M %p"),
        })

    data = {
        "upcoming": sorted(
            output,
            key=lambda x: x["closeDate"],
        ),
        "lastUpdated": now.strftime("%d %b %Y, %I:%M %p"),
        "scrapeWarnings": errors,
    }

    temp_file = DATA_FILE.with_suffix(".tmp")
    temp_file.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temp_file.replace(DATA_FILE)

    print(f"\n[OK] {len(output)} currently open IPO(s) written to data.json")
    for x in output:
        print(
            f"{x['name']} | {x['category']} | "
            f"Price: {x['issuePrice']} | Lot: {x['lotSize']} | "
            f"Investment: {x['minAmount']} | "
            f"Issue size: {x['issueSize']} | Closes: {x['closeDate']}"
        )

    if errors:
        print(f"\n[WARN] {len(errors)} source(s) failed. Review logs above.")


if __name__ == "__main__":
    main()
