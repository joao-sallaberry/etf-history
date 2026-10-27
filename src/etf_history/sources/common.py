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


def unseeded(rows: list[dict], refs: Refs) -> list[dict]:
    """Lines that look Brazilian but are neither stored nor in seeds/securities.csv: named like a stored line of the
    same file, or with `source_country` BRAZIL (Avantis). A new ADR of a Brazilian company shows up here."""
    names = {r["name"] for r in rows if r["name"] and refs.is_brazilian(r["isin"])}
    return [
        r
        for r in rows
        if r["isin"]
        and not refs.is_brazilian(r["isin"])
        and r["isin"] not in refs.securities
        and (r["name"] in names or r.get("source_country") == "BRAZIL")
    ]


def brazilian(rows: list[dict], refs: Refs) -> list[dict]:
    """Rows kept in the database; warns about `unseeded` lines."""
    for r in unseeded(rows, refs):
        log.warning(
            "possible Brazilian security not in seeds/securities.csv: %s %s %s", r["isin"], r["ticker"], r["name"]
        )
    return [r for r in rows if refs.is_brazilian(r["isin"])]
