from datetime import date
from pathlib import Path

from etf_history import refs
from etf_history.sources import dimensional

FIXTURES = Path(__file__).parent / "fixtures"
SEEDS = Path(__file__).parents[1] / "seeds"


def rows_by_key(name, refs_=None):
    as_of, rows = dimensional.parse((FIXTURES / name).read_bytes(), refs_)
    return as_of, {r["key"]: r for r in rows}


def test_url():
    assert dimensional.url("DFEV", date(2026, 10, 8)) == "https://tools-blob.dimensional.com/etf/20261008/DFEV.csv"


def test_parse_2022_principal_repeats_shares():
    as_of, rows = rows_by_key("dfev_20220426.csv")
    assert as_of == "2022-04-26"
    petr = rows["BRPETRACNPR6"]
    assert petr["ticker"] == "PETR4"
    assert petr["source_ticker"] == "PETR4 BZ"
    assert petr["country"] == "BR"
    assert petr["shares"] == 26000
    assert petr["value_usd"] == 156515.47
    assert petr["value_local"] is None  # principal == shares in older files
    assert rows["BRSANBCDAM13"]["ticker"] == "SANB11"


def test_parse_2026_principal_is_local_value():
    as_of, rows = rows_by_key("dfev_20261008.csv")
    assert as_of == "2026-10-08"
    petr = rows["BRPETRACNPR6"]
    assert petr["shares"] == 897030
    assert petr["value_local"] == 49767224.4
    assert petr["weight"] == 0.0045690162


def test_lines_without_isin_get_a_key():
    _, rows = rows_by_key("dfev_20261008.csv")
    assert rows["CASH AND CASH EQUIVALENTS"]["isin"] is None
    assert rows["CASH AND CASH EQUIVALENTS"]["country"] is None
    assert rows["INRCGTAXE"]["name"] == "INDIA CAP GAIN TAX"
    assert rows["BRL"]["name"] == "BRAZILIAN REAL"  # FX lines are keyed by their identifier


def test_adr_without_seeds_is_foreign():
    _, rows = rows_by_key("dfev_20261008.csv")
    assert rows["US91912E1055"]["country"] == "US"
    assert rows["US91912E1055"]["kind"] == "foreign"


def test_seeds_classify_adrs_and_local_classes():
    _, rows = rows_by_key("dfev_20261008.csv", refs.load(SEEDS))
    vale = rows["US91912E1055"]
    assert (vale["country"], vale["company"], vale["kind"], vale["share_ratio"]) == ("BR", "VALE", "adr", 1)
    petr = rows["BRPETRACNPR6"]
    assert (petr["company"], petr["kind"], petr["share_ratio"]) == ("PETR", "share", 1)
    assert rows["BRSANBCDAM13"]["kind"] == "unit"
    assert rows["US0594603039"]["company"] == "BBDC"


def test_brazilian_filter_keeps_b3_and_brazilian_adrs():
    r = refs.load(SEEDS)
    _, rows = dimensional.parse((FIXTURES / "dfev_20261008.csv").read_bytes(), r)
    kept = {row["key"] for row in dimensional.brazilian(rows, r)}
    assert kept == {"BRPETRACNPR6", "BRSANBCDAM13", "BREMBJACNOR1", "US0594603039", "US91912E1055"}


def test_repeated_key_is_suffixed():
    data = (FIXTURES / "dfev_20220426.csv").read_bytes()
    cash = data.splitlines()[3]
    assert b"CASH" in cash
    _, rows = dimensional.parse(data + cash + b"\n")
    keys = [r["key"] for r in rows]
    assert "CASH AND CASH EQUIVALENTS" in keys and "CASH AND CASH EQUIVALENTS#2" in keys
