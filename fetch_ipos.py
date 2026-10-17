
"""
fetch_ipos.py — IPO Dashboard Engine
Complete replacement file.

Features:
- Scrapes the configured IPO sources.
- Extracts IPO dates, price band, lot size and issue size from labelled columns.
- Uses explicit lot-size fallbacks for IPOs missing lot data in scraped tables.
- Retains existing dashboard-only fields from data.json.
- Preserves still-open entries if a source temporarily omits them.
- Avoids overwriting valid dashboard data after a failed/empty scrape.
- Removes the yearless strptime deprecation warning.
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

# Fallbacks for the IPOs whose lot size was missing in your scrape.
# Re-verify against the official prospectus/exchange before relying on them.
VERIFIED_LOTS = {
    "tonboimaging": 21,
    "zetwerk": 99,
    "sahajanandmedical": 43,
    "sahajanandmedicaltechnologies": 43,
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
    return re.sub(
        r"\s+", " ", str(value).replace("\xa0", " ")
    ).strip()


def key(value):
    return re.sub(r"[^a-z0-9]", "", clean(value).lower())


def numeric_values(text):
    return [
        float(x.replace(",", ""))
        for x in re.findall(r"\d[\d,]*(?:\.\d+)?", str(text))
    ]


def price_band(text):
    current_year = datetime.now(IST).year
    nums = [
        n for n in numeric_values(text)
        if 1 <= n <= 100000 and int(n) != current_year
    ]
    return max(nums) if nums else None


def issue_size(text, heading=""):
    """Convert an explicitly labelled issue size to crore."""
    raw = clean(text)

    if not raw or raw.lower() in {
        "tba", "na", "n/a", "-", "—"
    }:
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
        return None

    formatted = f"{amount:,.2f}".rstrip("0").rstrip(".")
    return f"₹{formatted} Cr"


def parse_one_date(text, year=None):
    """Parse dates without relying on yearless strptime formats."""
    text = clean(text)
    text = re.sub(
        r"(\d+)(st|nd|rd|th)\b",
        r"\1",
        text,
        flags=re.I,
    )
    year = int(year or datetime.now(IST).year)

    # Full dates containing a year.
    formats_with_year = (
        "%d %B %Y",
        "%d %b %Y",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d-%b-%Y",
        "%Y-%m-%d",
    )

    for fmt in formats_with_year:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass

    # Parse month/day separately, then explicitly apply the year.
    for fmt in ("%d %B", "%d %b", "%d-%m", "%d/%m"):
        try:
            parsed = datetime.strptime(text, fmt)
            return date(year, parsed.month, parsed.day)
        except ValueError:
            pass

    return None


def parse_dates(text):
    """Parse ranges such as 15-19 October or 15-19 October 2026."""
    text = clean(text)
    text = re.sub(
        r"(\d+)(st|nd|rd|th)\b",
        r"\1",
        text,
        flags=re.I,
    )
    text = (
        text.replace(" to ", " - ")
        .replace("–", "-")
        .replace("—", "-")
    )
    year = datetime.now(IST).year

    pattern = (
        r"(\d{1,2})\s*([A-Za-z]{3,9})?\s*-\s*"
        r"(\d{1,2})\s*([A-Za-z]{3,9})\s*(20\d{2})?"
    )
    match = re.search(pattern, text)

    if match:
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
                (
                    number
                    for prefix, number in month_names.items()
                    if value.lower().startswith(prefix)
                ),
                None,
            )

        end_month = month_number(m2)
        start_month = month_number(m1) or end_month

        if start_month and end_month:
            try:
                y = int(yr or year)
                return (
                    date(y, start_month, int(d1)),
                    date(y, end_month, int(d2)),
                )
            except ValueError:
                pass

    # Fallback for two complete dates separated by a hyphen.
    parts = re.split(r"\s+-\s+", text, maxsplit=1)

    if len(parts) == 2:
        start = parse_one_date(parts[0], year)
        end = parse_one_date(parts[1], year)

        if start and end:
            return start, end

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
            cells = row.find_all(
                ["th", "td"], recursive=False
            )
            labels = [
                clean(c.get_text(" ", strip=True)).lower()
                for c in cells
            ]

            if any(
                term in label
                for label in labels
                for term in (
                    "company", "ipo name", "ipo date",
                    "opening date", "closing date",
                    "close date", "issue size", "ipo size",
                    "price band", "lot size", "bidding dates",
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

            cells = row.find_all(
                ["td", "th"], recursive=False
            )
            values = [
                clean(c.get_text(" ", strip=True))
                for c in cells
            ]

            if len(values) < 2:
                continue

            mapped = {}
            for i, value in enumerate(values):
                heading = headers[i] if i < len(headers) else ""
                mapped[heading] = value

            name = values[0]

            if name.lower() in {
                "company", "company name", "ipo name",
                "ipo", "company ipo",
            }:
                continue

            row_text = " ".join(values)
            low = row_text.lower()

            # Dates: prefer date-labelled columns.
            start_date = end_date = None

            for heading, value in mapped.items():
                if any(
                    term in heading
                    for term in (
                        "ipo date", "bidding dates",
                        "open date", "opening date", "date",
                    )
                ):
                    start_date, end_date = parse_dates(value)

                    if start_date and end_date:
                        break

            if not start_date or not end_date:
                for value in values:
                    start_date, end_date = parse_dates(value)

                    if start_date and end_date:
                        break

            # Price: prefer price-labelled columns.
            price = None

            for heading, value in mapped.items():
                if any(
                    term in heading
                    for term in ("price", "band", "range")
                ):
                    price = price_band(value)

                    if price is not None:
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

                        if price is not None:
                            break

            # Issue size: only use a clearly labelled column.
            size = None

            for heading, value in mapped.items():
                if any(
                    term in heading
                    for term in ("size", "amount", "cr")
                ):
                    if not any(
                        term in heading
                        for term in ("lot", "share", "price")
                    ):
                        size = issue_size(value, heading)

                        if size:
                            break

            # Lot size: labelled columns only. Do not guess from arbitrary
            # numeric cells, which may contain prices or dates.
            lot = None

            lot_heading_terms = (
                "lot size",
                "market lot",
                "minimum lot",
                "lot",
                "minimum bid",
                "bid lot",
                "shares per lot",
                "minimum application",
                "quantity",
                "qty",
            )

            for heading, value in mapped.items():
                if not any(
                    term in heading
                    for term in lot_heading_terms
                ):
                    continue

                if value.lower() in {
                    "tba", "na", "n/a", "-", "—", ""
                }:
                    continue

                nums = numeric_values(value)

                if nums:
                    candidate = nums[0]

                    if (
                        candidate.is_integer()
                        and 1 <= candidate <= 100000
                    ):
                        lot = int(candidate)
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
        item.get("open") is not None
        and item.get("close") is not None
        and item["open"] <= today <= item["close"]
    )


def scrape():
    s = session()
    merged = {}
    errors = []
    total_rows = 0

    for url in SOURCES:
        try:
            response = s.get(
                url,
                headers=HEADERS,
                timeout=25,
            )
            response.raise_for_status()

            soup = BeautifulSoup(
                response.text,
                "html.parser",
            )
            found = extract_tables(soup, url)
            total_rows += len(found)

            for item in found:
                k = key(item["name"])

                if not k:
                    continue

                if k not in merged:
                    merged[k] = item
                    continue

                old = merged[k]

                # Fill missing fields from other sources.
                for field in (
                    "price", "lot", "issueSize", "open", "close"
                ):
                    if (
                        old.get(field) is None
                        and item.get(field) is not None
                    ):
                        old[field] = item[field]

                if item["category"] == "SME IPO":
                    old["category"] = "SME IPO"

        except Exception as exc:
            errors.append(
                f"{url}: {type(exc).__name__}: {exc}"
            )

    today = datetime.now(IST).date()
    open_items = [
        item for item in merged.values()
        if currently_open(item, today)
    ]

    return open_items, errors, total_rows


def display_date(value):
    return value.strftime("%d %B %Y") if value else "TBA"


def main():
    now = datetime.now(IST)

    print(
        f"\nIPO scraper started: "
        f"{now:%d %b %Y %I:%M %p} IST"
    )

    try:
        items, errors, total_rows = scrape()
    except Exception as exc:
        print(
            f"[ERROR] Scrape failed: "
            f"{type(exc).__name__}: {exc}"
        )
        items, errors, total_rows = [], [str(exc)], 0

    # Load previous dashboard data, if available.
    try:
        previous = json.loads(
            DATA_FILE.read_text(encoding="utf-8-sig")
        )
    except (OSError, json.JSONDecodeError):
        previous = {}

    old_items = {
        key(item.get("name", "")): item
        for item in previous.get("upcoming", [])
        if item.get("name")
    }

    # Do not destroy valid data if all sources fail or no tables parse.
    if not items and (
        total_rows == 0 or len(errors) == len(SOURCES)
    ):
        print(
            "[WARN] No usable scrape results. "
            "Keeping existing data.json unchanged."
        )

        for error in errors:
            print(f"  - {error}")

        return

    output = []
    scraped_keys = set()

    for item in items:
        k = key(item["name"])
        scraped_keys.add(k)
        old = old_items.get(k, {})

        price = item.get("price") or old.get("issuePrice")

        # Priority: scraped lot -> explicit fallback -> previous data.
        lot = (
            item.get("lot")
            or VERIFIED_LOTS.get(k)
            or old.get("lotSize")
        )

        size = (
            item.get("issueSize")
            or old.get("issueSize")
            or "TBA"
        )

        is_sme = item["category"] == "SME IPO"
        lots_required = 2 if is_sme else 1

        investment = (
            round(price * lot * lots_required)
            if price and lot
            else None
        )

        output.append({
            "slug": re.sub(
                r"[^a-z0-9]+",
                "-",
                item["name"].lower(),
            ).strip("-"),
            "name": item["name"],
            "symbol": old.get("symbol", ""),
            "sector": old.get(
                "sector",
                f"Pending Entry ({item['category']})",
            ),
            "category": item["category"],
            "status": "Open",
            "openDate": display_date(item.get("open")),
            "closeDate": display_date(item.get("close")),
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
                "Live issue details scraped automatically; "
                "fundamentals require separate verification.",
            ),
            "verdict": old.get(
                "verdict",
                "Data will be updated soon",
            ),
            "verdictCls": old.get(
                "verdictCls",
                "verdict-caution",
            ),
            "checks": old.get("checks", []),
            "score": old.get("score"),
            "lastFetched": now.strftime(
                "%d %b %Y, %I:%M %p"
            ),
        })

    # Retain previously listed entries if a source temporarily omits them.
    # This is a fallback, not confirmation that the IPO is still open.
    today = now.date()

    for k, old_item in old_items.items():
        if k in scraped_keys:
            continue

        close_str = old_item.get("closeDate")

        if not close_str or close_str == "TBA":
            continue

        close_dt = parse_one_date(close_str, now.year)

        if not close_dt:
            continue

        if close_dt == today and now.hour >= 17:
            continue

        if close_dt >= today:
            print(
                f"  [Retention] Keeping previously listed IPO: "
                f"{old_item.get('name')}"
            )
            output.append(old_item)

    if not output and previous.get("upcoming"):
        print(
            "[WARN] Scrape produced no open IPOs; "
            "keeping previous dashboard data."
        )
        return

    # Sort by parsed closing date, not alphabetically formatted date text.
    output.sort(
        key=lambda item: (
            parse_one_date(
                item.get("closeDate", ""),
                now.year,
            ) or date.max,
            item.get("name", "").lower(),
        )
    )

    data = {
        "upcoming": output,
        "lastUpdated": now.strftime(
            "%d %b %Y, %I:%M %p"
        ),
        "scrapeWarnings": errors,
    }

    temp_file = DATA_FILE.with_suffix(".tmp")

    temp_file.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    temp_file.replace(DATA_FILE)

    print(
        f"\n[OK] {len(output)} currently open IPO(s) "
        f"written to data.json"
    )

    for item in output:
        print(
            f"{item.get('name')} | {item.get('category')} | "
            f"Price: {item.get('issuePrice')} | "
            f"Lot: {item.get('lotSize')} | "
            f"Investment: {item.get('minAmount')} | "
            f"Issue size: {item.get('issueSize')} | "
            f"Closes: {item.get('closeDate')}"
        )

    if errors:
        print(
            f"\n[WARN] {len(errors)} source(s) failed. "
            "Review logs above."
        )


if __name__ == "__main__":
    main()
