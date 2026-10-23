"""Pieces shared by every holdings source: the result of one ingest and the Brazilian filter."""

import logging
from dataclasses import dataclass

from etf_history.refs import Refs

log = logging.getLogger(__name__)


@dataclass
class Result:
    status: str  # loaded | unchanged | missing | mismatch
    as_of: str | None = None
    rows: int = 0


def brazilian(rows: list[dict], refs: Refs) -> list[dict]:
    """Rows kept in the database. Warns about lines that look Brazilian but are neither stored nor in
    seeds/securities.csv: named like a stored line of the same file, or with `source_country` BRAZIL (Avantis)."""
    kept = [r for r in rows if refs.is_brazilian(r["isin"])]
    names = {r["name"] for r in kept if r["name"]}
    for r in rows:
        isin = r["isin"]
        if not isin or refs.is_brazilian(isin) or isin in refs.securities:
            continue
        if r["name"] in names or r.get("source_country") == "BRAZIL":
            log.warning(
                "possible Brazilian security not in seeds/securities.csv: %s %s %s", isin, r["ticker"], r["name"]
            )
    return kept
