from pathlib import Path

import pytest

from etf_history import refs
from etf_history.sources import avantis, common, nport, wayback

FIXTURES = Path(__file__).parent / "fixtures"
SEEDS = Path(__file__).parents[1] / "seeds"
PAGE = (FIXTURES / "aves_wayback_20250320.html").read_text(encoding="utf-8")
NPORT = (FIXTURES / "aves_nport_20211130.xml").read_bytes()


def test_wayback_extracts_the_funds_own_array():
    as_of, holdings = wayback.extract(PAGE, "AVES", min_lines=1)
    assert as_of == "03/20/2025"
    assert [h["isin"] for h in holdings] == ["BRPRIOACNOR1", "US4655621062", "KR7105560007"]
    assert holdings[2]["name"] == 'KB FINANCIAL GROUP "A" COMMON STOCK'
    assert holdings[2]["sector"] is None


def test_wayback_rejects_other_funds_short_and_truncated_arrays():
    with pytest.raises(wayback.Rejected, match="array of fund 118"):
        wayback.extract(PAGE.split("products:")[0], "AVES", min_lines=1)
    with pytest.raises(wayback.Rejected, match="only 3 lines"):
        wayback.extract(PAGE, "AVES")
    with pytest.raises(wayback.Rejected, match="truncated"):
        wayback.extract(PAGE[: PAGE.index("KB FINANCIAL")], "AVES", min_lines=1)


def test_wayback_capture_parses_as_api_json():
    as_of_us, holdings = wayback.extract(PAGE, "AVES", min_lines=1)
    data = wayback.to_api_json("AVES", as_of_us, holdings, "20250322182023")
    r = refs.load(SEEDS)
    as_of, rows = avantis.parse(data, r, fund="AVES")
    assert as_of == "2025-03-20"
    kept = {row["key"]: row for row in common.brazilian(rows, r)}
    assert set(kept) == {"BRPRIOACNOR1", "US4655621062"}
    assert kept["US4655621062"]["company"] == "ITUB"


def test_nport_parse():
    as_of, rows = nport.parse(NPORT, refs.load(SEEDS), fund="AVES")
    assert as_of == "2021-11-30"
    by = {r["key"]: r for r in rows}
    bmg = by["BRBMGBACNPR0"]
    assert (bmg["ticker"], bmg["shares"], bmg["value_usd"]) == ("BMGB4", 400, 205.63)
    assert bmg["value_local"] == pytest.approx(205.63 * 5.62185)
    assert bmg["weight"] == pytest.approx(0.000006237968)
    pbr = by["US71654V1017"]
    assert (pbr["company"], pbr["kind"], pbr["value_local"]) == ("PETR", "adr", None)
    assert by["US37428A1034"]["company"] == "GETT"


def test_nport_rejects_other_series():
    with pytest.raises(ValueError):
        nport.parse(NPORT.replace(b"S000072996", b"S000066454"), fund="AVES")
