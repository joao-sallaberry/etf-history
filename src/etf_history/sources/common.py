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
    """Rows kept in the database. Warns about non-BR lines named like a Brazilian company in the same file
    (probably an ADR missing from seeds/securities.csv)."""
    kept = [r for r in rows if refs.is_brazilian(r["isin"])]
    names = {r["name"] for r in kept if r["name"]}
    for r in rows:
        if r["isin"] and not refs.is_brazilian(r["isin"]) and r["name"] in names:
            log.warning(
                "possible Brazilian ADR not in seeds/securities.csv: %s %s %s", r["isin"], r["ticker"], r["name"]
            )
    return kept
