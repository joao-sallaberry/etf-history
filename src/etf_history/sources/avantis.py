"""Holdings published by Avantis: https://www.avantisinvestors.com/fund/<fundNumber>/fundData?type=holdings

JSON with the full portfolio of the latest day only (`portfolio.etfHoldingsAsOfDate`); there is no date
parameter, so a day not fetched while it is the latest one is lost. Each line of `portfolio.etfHoldings` has
name, ticker, securityType, cusip, isin, sedol, shareQuantity, baseMarketValue (US$), weight ("1.47%", rounded
to two decimals), sector and country.
"""

import gzip
import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

import httpx

from etf_history import db, http
from etf_history.dates import now
from etf_history.refs import Refs
from etf_history.sources.common import Result, brazilian
from etf_history.tickers import br_ticker

SOURCE = "daily"
URL = "https://www.avantisinvestors.com/fund/{number}/fundData?type=holdings"
FUND_NUMBER = {"AVES": "808"}


def url(fund: str) -> str:
    return URL.format(number=FUND_NUMBER[fund])


def _float(value: str | None) -> float | None:
    value = (value or "").strip().rstrip("%")
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _str(value) -> str:
    return (value or "").strip()


def parse(data: bytes, refs: Refs | None = None, *, fund: str | None = None) -> tuple[str, list[dict]]:
    """Return (as_of, rows) with every line. `weight` is recomputed as baseMarketValue / Σ baseMarketValue, since
    the published one is rounded to 0.01%."""
    refs = refs or Refs()
    product = json.loads(data)["fundData"]["data"]["products"][0]
    if fund and product.get("ticker") != fund:
        raise ValueError(f"expected {fund}, got {product.get('ticker')}")
    portfolio = product["portfolio"]
    month, day, year = portfolio["etfHoldingsAsOfDate"].split("/")
    as_of = date(int(year), int(month), int(day)).isoformat()
    lines = portfolio["etfHoldings"]
    if not lines:
        raise ValueError("empty holdings")
    total = sum(_float(h.get("baseMarketValue")) or 0 for h in lines)

    rows, seen = [], {}
    for h in lines:
        isin, sedol, name = _str(h.get("isin")), _str(h.get("sedol")), _str(h.get("name"))
        source_ticker = _str(h.get("ticker"))
        key = isin or sedol or _str(h.get("cusip")) or name
        if key in seen:
            seen[key] += 1
            key = f"{key}#{seen[key]}"
        else:
            seen[key] = 1

        ticker = source_ticker.split()[0] if source_ticker else ""
        if isin.startswith("BR") and not ticker:
            ticker = br_ticker(isin)

        value = _float(h.get("baseMarketValue"))
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
                "shares": _float(h.get("shareQuantity")),
                "value_usd": value,
                "value_local": None,
                "weight": value / total if value is not None and total else None,
            }
        )
    return as_of, rows


def raw_path(raw_dir: Path, fund: str, day: date) -> Path:
    return raw_dir / "daily" / fund / f"{day:%Y}" / f"{day:%Y%m%d}.json.gz"


def ingest_latest(conn: sqlite3.Connection, client: httpx.Client, *, fund: str, data_dir: Path, refs: Refs) -> Result:
    """Download, archive and load the latest day."""
    fetched_at = now().isoformat(timespec="seconds")
    data = http.fetch(client, url(fund))
    if data is None:
        return Result("missing")
    as_of, rows = parse(data, refs, fund=fund)
    sha = hashlib.sha256(data).hexdigest()
    if db.snapshot_sha(conn, fund, as_of, SOURCE) == sha:
        return Result("unchanged", as_of)
    path = raw_path(data_dir / "raw", fund, date.fromisoformat(as_of))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(data))
    rows = brazilian(rows, refs)
    db.replace_snapshot(
        conn,
        fund=fund,
        as_of=as_of,
        source=SOURCE,
        fetched_at=fetched_at,
        file_path=str(path.relative_to(data_dir)),
        sha256=sha,
        rows=rows,
    )
    return Result("loaded", as_of, len(rows))


def reload_raw(conn: sqlite3.Connection, *, fund: str, data_dir: Path, refs: Refs) -> int:
    """Re-parse every archived raw file (after a parser or seed change) without downloading."""
    count = 0
    for path in sorted((data_dir / "raw" / "daily" / fund).glob("*/*.json.gz")):
        data = gzip.decompress(path.read_bytes())
        as_of, rows = parse(data, refs, fund=fund)
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
