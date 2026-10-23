import gzip
from datetime import date
from pathlib import Path

import pytest

from etf_history import db, refs, sources
from etf_history.sources import avantis, common, dimensional

FIXTURES = Path(__file__).parent / "fixtures"
SEEDS = Path(__file__).parents[1] / "seeds"
DATA = (FIXTURES / "aves_20261022.json").read_bytes()


def rows_by_key(refs_=None):
    as_of, rows = avantis.parse(DATA, refs_, fund="AVES")
    return as_of, {r["key"]: r for r in rows}


def test_url_and_registry():
    assert avantis.url("AVES") == "https://www.avantisinvestors.com/fund/808/fundData?type=holdings"
    assert sources.for_fund("AVES") is avantis
    assert sources.for_fund("DFEV") is dimensional
    assert sources.for_fund("DFAE") is dimensional  # any other ticker: Dimensional


def test_parse():
    as_of, rows = rows_by_key()
    assert as_of == "2026-10-22"
    prio = rows["BRPRIOACNOR1"]
    assert (prio["ticker"], prio["source_ticker"], prio["shares"], prio["value_usd"]) == (
        "PRIO3",
        "PRIO3",
        471600,
        6796429.98,
    )
    assert prio["value_local"] is None


def test_weight_is_recomputed_from_values():
    _, rows = rows_by_key()
    total = sum(r["value_usd"] or 0 for r in rows.values())
    assert rows["BRPRIOACNOR1"]["weight"] == pytest.approx(6796429.98 / total)
    assert sum(r["weight"] or 0 for r in rows.values()) == pytest.approx(1)


def test_lines_without_isin_or_value():
    _, rows = rows_by_key()
    assert rows["999TWDZ90"]["name"] == "NEW TAIWAN DOLLAR"  # cash keyed by CUSIP
    aed = rows["999AEDZ91"]
    assert aed["value_usd"] is None and aed["weight"] is None
    assert rows["BRPGMND05OR4"]["kind"] == "right"


def test_brazilian_filter_uses_seeds():
    r = refs.load(SEEDS)
    _, rows = avantis.parse(DATA, r)
    kept = {row["key"] for row in common.brazilian(rows, r)}
    assert {"BRPRIOACNOR1", "BRPGMND05OR4", "US91912E1055", "US71654V4086"} <= kept
    assert "KYG982391099" not in kept  # XP: listed abroad, BDR only in Brazil
    assert "TW0002881000" not in kept


def test_wrong_fund_is_rejected():
    with pytest.raises(ValueError):
        avantis.parse(DATA, fund="AVEM")


def test_reload_raw(tmp_path):
    path = avantis.raw_path(tmp_path / "raw", "AVES", date(2026, 10, 22))
    path.parent.mkdir(parents=True)
    path.write_bytes(gzip.compress(DATA))
    conn = db.connect(tmp_path / "t.db")
    db.init(conn)
    assert avantis.reload_raw(conn, fund="AVES", data_dir=tmp_path, refs=refs.load(SEEDS)) == 1
    assert db.known_days(conn, "AVES") == {"2026-10-22"}
    assert conn.execute("select file_path from snapshot").fetchone()[0] == "raw/daily/AVES/2026/20261022.json.gz"
