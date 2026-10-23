"""Quarter-end holdings from SEC Form N-PORT (NPORT-P), filed ~60 days after each fiscal quarter.

Each filing's primary_doc.xml lists every position (`invstOrSec`): name, identifiers (isin, ticker, SEDOL), balance
(shares when units = NS), valUSD, pctVal (% of net assets), invCountry and, for non-US$ positions, the exchange
rate. SEC requires a User-Agent with a contact (`ETF_HISTORY_SEC_USER_AGENT`).
"""

import gzip
import hashlib
import logging
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import httpx

from etf_history import config, db, http
from etf_history.dates import now
from etf_history.refs import Refs
from etf_history.sources.common import Result, brazilian
from etf_history.tickers import br_ticker

log = logging.getLogger(__name__)

SOURCE = "nport"
SERIES = {"AVES": ("1710607", "S000072996")}  # (trust CIK, series id)
FILINGS = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={series}&type=NPORT-P&dateb=&owner=include"
    "&count=100&output=atom"
)
DOC = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/primary_doc.xml"
REQUEST_INTERVAL = 0.5  # SEC allows 10 requests/s


def filings(client: httpx.Client, fund: str) -> list[str]:
    """Accession numbers of the fund's NPORT-P filings (amendments excluded), newest first."""
    resp = client.get(FILINGS.format(series=SERIES[fund][1]))
    resp.raise_for_status()
    found = []
    for entry in re.findall(r"<entry>.*?</entry>", resp.text, re.S):
        kind = re.search(r"<filing-type>([^<]+)</filing-type>", entry)
        accession = re.search(r"<accession-number>([\d-]+)</accession-number>", entry)
        if kind and accession and kind.group(1) == "NPORT-P":
            found.append(accession.group(1))
    return found


def _text(el: ET.Element, path: str) -> str:
    found = el.find(path)
    return (found.text or "").strip() if found is not None else ""


def _float(value: str) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def parse(data: bytes, refs: Refs | None = None, *, fund: str | None = None) -> tuple[str, list[dict]]:
    """Return (as_of, rows): the report date and every position."""
    refs = refs or Refs()
    root = ET.fromstring(data)
    series = _text(root, ".//{*}genInfo/{*}seriesId")
    if fund and series != SERIES[fund][1]:
        raise ValueError(f"expected series {SERIES[fund][1]}, got {series}")
    as_of = _text(root, ".//{*}genInfo/{*}repPdDate")
    rows, seen = [], {}
    for sec in root.iter("{http://www.sec.gov/edgar/nport}invstOrSec"):
        ids = sec.find("{*}identifiers")
        isin = ids.find("{*}isin").get("value", "") if ids is not None and ids.find("{*}isin") is not None else ""
        ticker_el = ids.find("{*}ticker") if ids is not None else None
        source_ticker = ticker_el.get("value", "").strip() if ticker_el is not None else ""
        sedol = ""
        for other in ids.iterfind("{*}other") if ids is not None else ():
            if other.get("otherDesc") == "SEDOL":
                sedol = other.get("value", "")
        name = _text(sec, "{*}name")
        cusip = _text(sec, "{*}cusip")
        key = isin or sedol or (cusip if cusip != "N/A" else "") or name
        if key in seen:
            seen[key] += 1
            key = f"{key}#{seen[key]}"
        else:
            seen[key] = 1

        ticker = source_ticker.split()[0] if source_ticker else ""
        if isin.startswith("BR") and not ticker:
            ticker = br_ticker(isin)
        value = _float(_text(sec, "{*}valUSD"))
        rate = sec.find("{*}currencyConditional")
        pct = _float(_text(sec, "{*}pctVal"))
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
                "shares": _float(_text(sec, "{*}balance")) if _text(sec, "{*}units") == "NS" else None,
                "value_usd": value,
                "value_local": value * float(rate.get("exchangeRt"))
                if value is not None and rate is not None
                else None,
                "weight": pct / 100 if pct is not None else None,
                "source_country": "BRAZIL" if _text(sec, "{*}invCountry") == "BR" else None,
            }
        )
    if not as_of or not rows:
        raise ValueError("no report date or positions")
    return as_of, rows


def raw_path(raw_dir: Path, fund: str, day: date, accession: str) -> Path:
    return raw_dir / SOURCE / fund / f"{day:%Y}" / f"{day:%Y%m%d}_{accession}.xml.gz"


def _done(data_dir: Path, fund: str) -> set[str]:
    return {p.name.split("_", 1)[1].removesuffix(".xml.gz") for p in (data_dir / "raw" / SOURCE / fund).glob("*/*")}


def ingest_filing(
    conn: sqlite3.Connection, client: httpx.Client, *, fund: str, accession: str, data_dir: Path, refs: Refs
) -> Result:
    data = http.fetch(client, DOC.format(cik=SERIES[fund][0], accession=accession.replace("-", "")))
    if data is None:
        return Result("missing")
    as_of, rows = parse(data, refs, fund=fund)
    path = raw_path(data_dir / "raw", fund, date.fromisoformat(as_of), accession)
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
    """Load every filing not archived yet."""
    if not settings.sec_user_agent:
        log.error("%s: set ETF_HISTORY_SEC_USER_AGENT (e.g. 'Name email@example.com') to download N-PORT", fund)
        return {"error": 1}
    counts = {"loaded": 0, "missing": 0, "error": 0}
    with http.client(timeout=120, user_agent=settings.sec_user_agent) as client:
        done = _done(settings.data_dir, fund)
        todo = [a for a in filings(client, fund) if a not in done]
        log.info("%s: %d N-PORT filing(s) to load", fund, len(todo))
        for accession in todo:
            try:
                result = ingest_filing(
                    conn, client, fund=fund, accession=accession, data_dir=settings.data_dir, refs=refs
                )
                counts[result.status] += 1
                log.info("%s %s: %s %s %s", fund, accession, result.status, result.as_of or "", result.rows or "")
            except Exception as exc:
                counts["error"] += 1
                log.error("%s %s: %s", fund, accession, exc)
            time.sleep(REQUEST_INTERVAL)
    log.info("%s: %s", fund, ", ".join(f"{k} {v}" for k, v in counts.items() if v))
    return counts


def reload_raw(conn: sqlite3.Connection, *, fund: str, data_dir: Path, refs: Refs) -> int:
    """Re-parse every archived filing (after a parser or seed change) without downloading."""
    count = 0
    for path in sorted((data_dir / "raw" / SOURCE / fund).glob("*/*.xml.gz")):
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
