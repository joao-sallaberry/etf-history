from pathlib import Path

from etf_history import refs

SEEDS = Path(__file__).parents[1] / "seeds"


def test_kinds_from_isin():
    r = refs.Refs()
    assert r.classify("BRPETRACNOR9")["kind"] == "share"
    assert r.classify("BRSANBCDAM13")["kind"] == "unit"
    assert r.classify("BRBBDCD14OR9")["kind"] == "right"  # subscription right
    assert r.classify("BRABCBACNPRR")["kind"] == "right"  # temporary ISIN, letter check digit
    assert r.classify("BRJBSSBDR002")["kind"] == "bdr"
    assert r.classify("TW0004564000") == {"country": "TW", "company": None, "kind": "foreign", "share_ratio": None}


def test_seeds():
    r = refs.load(SEEDS)
    pbr = r.classify("US71654V4086")
    assert (pbr["company"], pbr["share_ratio"], pbr["kind"]) == ("PETR", 2, "adr")
    assert r.classify("BREMBRACNOR4")["company"] == "EMBJ"  # alias EMBR -> EMBJ
    assert r.classify("US29082A1079")["company"] == "EMBJ"
    assert r.classify("BRJBSSBDR002")["country"] == "NL"
    assert r.is_brazilian("BRJBSSBDR002")
    assert r.is_brazilian("US91912E1055")
    assert not r.is_brazilian("TW0004564000")
    assert not r.is_brazilian(None)
    assert len(r.sha256) == 64
