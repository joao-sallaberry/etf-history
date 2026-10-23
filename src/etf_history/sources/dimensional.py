"""Daily holdings files published by Dimensional: https://tools-blob.dimensional.com/etf/YYYYMMDD/<FUND>.csv

Columns: date, etf_ticker, ticker, description, weight, market_value, identifier, isin, sedol, shares,
coupon_rate, maturity_date, principal. Weekends, holidays and the current day return 404.
"""

import csv
import gzip
import hashlib
import io
import logging
import sqlite3
from datetime import date
from pathlib import Path

import httpx

from etf_history import db, http
from etf_history.dates import now
from etf_history.refs import Refs
from etf_history.sources.common import Result, brazilian
from etf_history.tickers import br_ticker

log = logging.getLogger(__name__)

SOURCE = "daily"
URL = "https://tools-blob.dimensional.com/etf/{day:%Y%m%d}/{fund}.csv"
FIRST_DAY = {"DFEV": date(2022, 4, 26)}  # first file available (fund inception)
DEFAULT_FIRST_DAY = date(2020, 1, 1)


def url(fund: str, day: date) -> str:
    return URL.format(day=day, fund=fund)


def _float(value: str | None) -> float | None:
    value = (value or "").strip()
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def parse(data: bytes, refs: Refs | None = None) -> tuple[str, list[dict]]:
    """Return (as_of, rows) with every line of the file. as_of is the most common `date` in the file."""
    refs = refs or Refs()
    rows, dates, seen = [], {}, {}
    for r in csv.DictReader(io.StringIO(_decode(data))):
        dates[r["date"]] = dates.get(r["date"], 0) + 1
        isin = (r.get("isin") or "").strip()
        sedol = (r.get("sedol") or "").strip()
        name = (r.get("description") or "").strip()
        source_ticker = (r.get("ticker") or "").strip()

        key = isin or sedol or (r.get("identifier") or "").strip() or name
        if key in seen:  # cash/FX lines can repeat a description
            seen[key] += 1
            key = f"{key}#{seen[key]}"
        else:
            seen[key] = 1

        ticker = source_ticker.split()[0] if source_ticker else ""
        if isin.startswith("BR") and not ticker:
            ticker = br_ticker(isin)

        shares = _float(r.get("shares"))
        principal = _float(r.get("principal"))
        cls = refs.classify(isin) if isin else {"country": None, "company": None, "kind": None, "share_ratio": None}
        rows.append(
            {
                "key": key,
                "isin": isin or None,
                "ticker": ticker or None,
                "source_ticker": source_ticker or None,
                "name": name or None,
                **cls,
                "sedol": sedol or None,
                "shares": shares,
                "value_usd": _float(r.get("market_value")),
                # Older files repeat `shares` in `principal`; newer ones put the local-currency value there.
                "value_local": principal if principal is not None and principal != shares else None,
                "weight": _float(r.get("weight")),
            }
        )
    if not dates:
        raise ValueError("empty holdings file")
    as_of = max(dates, key=dates.get)
    return as_of, rows


def raw_path(raw_dir: Path, fund: str, day: date) -> Path:
    return raw_dir / "daily" / fund / f"{day:%Y}" / f"{day:%Y%m%d}.csv.gz"


def ingest_day(
    conn: sqlite3.Connection,
    client: httpx.Client,
    *,
    fund: str,
    day: date,
    data_dir: Path,
    refs: Refs,
    record_missing: bool,
) -> Result:
    """Download, archive and load one day. 404 -> 'missing' (recorded only when `record_missing`)."""
    checked_at = now().isoformat(timespec="seconds")
    data = http.fetch(client, url(fund, day))
    if data is None:
        if record_missing:
            db.mark_missing(conn, fund, day.isoformat(), "404", checked_at)
        return Result("missing")

    as_of, rows = parse(data, refs)
    if as_of != day.isoformat():
        # A file served for another date (stale copy). Do not load it under the wrong day.
        log.warning("%s %s: file is dated %s", fund, day, as_of)
        db.mark_missing(conn, fund, day.isoformat(), f"date:{as_of}", checked_at)
        return Result("mismatch", as_of)

    sha = hashlib.sha256(data).hexdigest()
    if db.snapshot_sha(conn, fund, as_of, SOURCE) == sha:
        return Result("unchanged", as_of)

    path = raw_path(data_dir / "raw", fund, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(data))
    rows = brazilian(rows, refs)
    db.replace_snapshot(
        conn,
        fund=fund,
        as_of=as_of,
        source=SOURCE,
        fetched_at=checked_at,
        file_path=str(path.relative_to(data_dir)),
        sha256=sha,
        rows=rows,
    )
    return Result("loaded", as_of, len(rows))


def reload_raw(conn: sqlite3.Connection, *, fund: str, data_dir: Path, refs: Refs) -> int:
    """Re-parse every archived raw file (after a parser or seed change) without downloading."""
    count = 0
    for path in sorted((data_dir / "raw" / "daily" / fund).glob("*/*.csv.gz")):
        data = gzip.decompress(path.read_bytes())
        as_of, rows = parse(data, refs)
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
