"""Past AVES holdings recovered from Wayback Machine captures of the Avantis fund pages.

The fund page embeds the full portfolio of its day as a JavaScript object literal (`etfHoldings:[{name:"…",…}]`,
unquoted keys). A capture may hold another fund's array (the trading-details page of 2023-10 carries AVEM) or be
cut at 1 MB by the archive, so only a closed array of at least MIN_LINES lines preceded by the fund's own
`fundNumber` is kept. (The published weights are no completeness test: cash swings move their sum between 95% and
104% on complete days.) Kept arrays are archived in the Avantis API's JSON shape, so `avantis.parse`
reads them.
"""

import gzip
import hashlib
import json
import logging
import re
import sqlite3
import time
from datetime import date
from pathlib import Path

import httpx

from etf_history import config, db, http
from etf_history.dates import now
from etf_history.refs import Refs
from etf_history.sources import avantis
from etf_history.sources.common import Result, brazilian

log = logging.getLogger(__name__)

SOURCE = "archive"
CDX = "https://web.archive.org/cdx/search/cdx"
CAPTURE = "https://web.archive.org/web/{timestamp}id_/{url}"
PAGES = {
    "AVES": (
        "https://www.avantisinvestors.com/avantis-investments/avantis-emerging-markets-value-etf/",
        "https://www.avantisinvestors.com/avantis-investments/avantis-emerging-markets-value-etf/trading-details/",
    )
}
MIN_LINES = 500  # AVES has held 1,400-2,000 lines
REQUEST_INTERVAL = 3  # seconds between captures; the archive throttles and drops connections


class Rejected(ValueError):
    pass


def captures(client: httpx.Client, fund: str) -> list[tuple[str, str]]:
    """(timestamp, url) of every successful capture of the fund's pages, oldest first."""
    found = []
    for page in PAGES[fund]:
        resp = client.get(CDX, params={"url": page, "fl": "timestamp,original", "filter": "statuscode:200"})
        resp.raise_for_status()
        found += [tuple(line.split()) for line in resp.text.splitlines() if line.strip()]
    return sorted(found)


def _js_array(text: str, start: int) -> str:
    """The JS array literal starting at text[start] ('['), converted to JSON (keys quoted)."""
    out, depth, i, prev = [], 0, start, ""
    while i < len(text):
        c = text[i]
        if c == '"':
            j = i + 1
            while j < len(text) and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            if j >= len(text):
                break
            out.append(text[i : j + 1].replace("\\'", "'"))
            i, prev = j + 1, '"'
            continue
        if c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                out.append(c)
                return "".join(out)
        elif (c.isalpha() or c in "_$") and prev in "{,":
            m = re.compile(r"[\w$]+(?=\s*:)").match(text, i)
            if m:
                out.append(f'"{m.group()}"')
                i, prev = m.end(), '"'
                continue
        out.append(c)
        if not c.isspace():
            prev = c
        i += 1
    raise Rejected("truncated array")


def extract(html: str, fund: str, *, min_lines: int = MIN_LINES) -> tuple[str, list[dict]]:
    """(etfHoldingsAsOfDate, holdings) of the fund's own array in a captured page."""
    number = avantis.FUND_NUMBER[fund]
    reasons = []
    for m in re.finditer(r"etfHoldings:\[", html):
        before = html[: m.start()]
        numbers = re.findall(r'fundNumber[:=]"(\d+)"', before)  # `fundNumber:"808"` or `a.fundNumber="808"`
        if not numbers or numbers[-1] != number:
            reasons.append(f"array of fund {numbers[-1] if numbers else '?'}")
            continue
        own = before[max(before.rfind(f'fundNumber:"{number}"'), before.rfind(f'fundNumber="{number}"')) :]
        dates = re.findall(r'etfHoldingsAsOfDate[:=]"([\d/]+)"', own)
        if not dates:
            reasons.append("no as-of date")
            continue
        try:
            holdings = json.loads(_js_array(html, m.end() - 1))
        except (Rejected, json.JSONDecodeError) as exc:
            reasons.append(str(exc) or "invalid array")
            continue
        if len(holdings) < min_lines:
            reasons.append(f"only {len(holdings)} lines")
            continue
        return dates[-1], holdings
    raise Rejected("; ".join(reasons) or "no holdings array")


def to_api_json(fund: str, as_of: str, holdings: list[dict], timestamp: str) -> bytes:
    """The Avantis API's JSON shape, plus the capture it came from."""
    product = {
        "ticker": fund,
        "fundNumber": avantis.FUND_NUMBER[fund],
        "portfolio": {"etfHoldingsAsOfDate": as_of, "etfHoldings": holdings},
    }
    return json.dumps({"capture": timestamp, "fundData": {"data": {"products": [product]}}}).encode()


def raw_path(raw_dir: Path, fund: str, day: date) -> Path:
    return raw_dir / SOURCE / fund / f"{day:%Y}" / f"{day:%Y%m%d}.json.gz"


def _done(data_dir: Path, fund: str) -> set[str]:
    """Capture timestamps already archived."""
    return {
        json.loads(gzip.decompress(p.read_bytes())).get("capture")
        for p in (data_dir / "raw" / SOURCE / fund).glob("*/*.json.gz")
    }


def ingest_capture(
    conn: sqlite3.Connection,
    client: httpx.Client,
    *,
    fund: str,
    timestamp: str,
    url: str,
    data_dir: Path,
    refs: Refs,
) -> Result:
    html = http.fetch(client, CAPTURE.format(timestamp=timestamp, url=url))
    if html is None:
        return Result("missing")
    as_of_us, holdings = extract(html.decode("utf-8", errors="replace"), fund)
    data = to_api_json(fund, as_of_us, holdings, timestamp)
    as_of, rows = avantis.parse(data, refs, fund=fund)
    if db.snapshot_sha(conn, fund, as_of, SOURCE) is not None:
        return Result("unchanged", as_of)  # another capture of the same day
    path = raw_path(data_dir / "raw", fund, date.fromisoformat(as_of))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(data))
    rows = brazilian(rows, refs)
    db.replace_snapshot(
        conn,
        fund=fund,
        as_of=as_of,
        source=SOURCE,
        fetched_at=now().isoformat(timespec="seconds"),
        file_path=str(path.relative_to(data_dir)),
        sha256=hashlib.sha256(data).hexdigest(),
        rows=rows,
    )
    return Result("loaded", as_of, len(rows))


def backfill(conn: sqlite3.Connection, *, fund: str, settings: config.Settings, refs: Refs) -> dict[str, int]:
    """Load every capture not archived yet. Rejected captures are logged and tried again on the next run."""
    data_dir = settings.data_dir
    counts = {"loaded": 0, "unchanged": 0, "missing": 0, "rejected": 0, "error": 0}
    with http.client(timeout=120) as client:
        done = _done(data_dir, fund)
        todo = [(t, u) for t, u in captures(client, fund) if t not in done]
        log.info("%s: %d capture(s) to check", fund, len(todo))
        for timestamp, url in todo:
            try:
                result = ingest_capture(
                    conn, client, fund=fund, timestamp=timestamp, url=url, data_dir=data_dir, refs=refs
                )
                counts[result.status] += 1
                log.info("%s %s: %s %s %s", fund, timestamp, result.status, result.as_of or "", result.rows or "")
            except Rejected as exc:
                counts["rejected"] += 1
                log.info("%s %s: rejected (%s)", fund, timestamp, exc)
            except Exception as exc:
                counts["error"] += 1
                log.error("%s %s: %s", fund, timestamp, exc)
            time.sleep(REQUEST_INTERVAL)
    log.info("%s: %s", fund, ", ".join(f"{k} {v}" for k, v in counts.items() if v))
    return counts


def reload_raw(conn: sqlite3.Connection, *, fund: str, data_dir: Path, refs: Refs) -> int:
    """Re-parse every archived capture (after a parser or seed change) without downloading."""
    count = 0
    for path in sorted((data_dir / "raw" / SOURCE / fund).glob("*/*.json.gz")):
        data = gzip.decompress(path.read_bytes())
        as_of, rows = avantis.parse(data, refs, fund=fund)
        db.replace_snapshot(
            conn,
            fund=fund,
            as_of=as_of,
            source=SOURCE,
            fetched_at=now().isoformat(timespec="seconds"),
            file_path=str(path.relative_to(data_dir)),
            sha256=hashlib.sha256(data).hexdigest(),
            rows=brazilian(rows, refs),
        )
        count += 1
    return count
