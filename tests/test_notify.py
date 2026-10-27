import json
from datetime import date, timedelta

import pytest

from etf_history import config, db, notify
from etf_history.dates import now
from etf_history.refs import Refs

DAY = date(2026, 10, 26)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init(c)
    return c


def put(conn, fund, day, shares, source="daily"):
    """One snapshot: {company: shares} as local shares (ratio 1)."""
    rows = [
        {c: None for c in db.HOLDING_COLUMNS}
        | {"key": f"BR{company}ACNOR0", "company": company, "kind": "share", "share_ratio": 1, "shares": n}
        | {"name": f"{company} SA", "isin": f"BR{company}ACNOR0"}
        for company, n in shares.items()
    ]
    db.replace_snapshot(
        conn, fund=fund, as_of=day.isoformat(), source=source, fetched_at="t", file_path="x", sha256="x", rows=rows
    )


def settings(tmp_path):
    return config.Settings(
        db_path=tmp_path / "t.db",
        data_dir=tmp_path,
        seeds_dir=tmp_path,
        funds=("DFEV", "AVES"),
        sec_user_agent="",
        sync_from="",
    )


def test_both_bought_sold_and_threshold(conn):
    start = DAY - timedelta(days=20)
    put(conn, "DFEV", start, {"PETR": 100, "VALE": 100, "SMTO": 100, "ITUB": 100})
    put(conn, "DFEV", DAY, {"PETR": 110, "VALE": 90, "SMTO": 103, "ITUB": 120, "TIMS": 5})
    put(conn, "AVES", start, {"PETR": 50, "VALE": 50, "SMTO": 50})
    put(conn, "AVES", DAY, {"PETR": 60, "VALE": 40, "SMTO": 60, "TIMS": 7})
    r = notify.trades(conn, ["DFEV", "AVES"], DAY)
    assert set(r.bought) == {("bought", "PETR"), ("bought", "TIMS")}  # SMTO +3% in DFEV: below the 5% threshold
    assert set(r.sold) == {("sold", "VALE")}
    assert "DFEV +10.0%, AVES +20.0%" in r.bought[("bought", "PETR")]
    assert "DFEV new, AVES new" in r.bought[("bought", "TIMS")]
    # ITUB: only DFEV holds it → not joint; entries listed per fund
    assert ("entry", "DFEV", "TIMS") in r.moves and ("entry", "AVES", "TIMS") in r.moves


def test_same_factor_in_every_fund_is_a_corporate_event(conn):
    start = DAY - timedelta(days=10)
    put(conn, "DFEV", start, {"NATU": 100, "PETR": 100})
    put(conn, "DFEV", DAY, {"NATU": 4760, "PETR": 300})
    put(conn, "AVES", start, {"NATU": 10, "PETR": 100})
    put(conn, "AVES", DAY, {"NATU": 476, "PETR": 160})
    r = notify.trades(conn, ["DFEV", "AVES"], DAY)
    assert set(r.events) == {("event", "NATU")}
    assert set(r.bought) == {("bought", "PETR")}  # ×3 and ×1.6: different factors, real buys


def test_fund_with_one_day_in_the_window_is_left_out(conn):
    put(conn, "DFEV", DAY - timedelta(days=10), {"PETR": 100})
    put(conn, "DFEV", DAY, {"PETR": 200})
    put(conn, "AVES", DAY - timedelta(days=60), {"PETR": 10}, source="archive")  # outside the window
    put(conn, "AVES", DAY, {"PETR": 20})
    r = notify.trades(conn, ["DFEV", "AVES"], DAY)
    assert r.sparse == ["AVES"]
    assert not r.bought  # one fund compared: no joint section


def test_window_uses_the_first_day_inside_it():
    days = [date(2026, 6, 15), date(2026, 7, 29), date(2026, 10, 22), date(2026, 10, 26)]
    assert notify.window(days, DAY) == (date(2026, 10, 22), DAY)
    assert notify.window(days, date(2026, 8, 10)) is None  # only 07-29 in the 30 days ending there


def test_new_items_against_the_previous_morning(conn, tmp_path):
    put(conn, "DFEV", DAY - timedelta(days=10), {"PETR": 100, "VALE": 100})
    put(conn, "AVES", DAY - timedelta(days=10), {"PETR": 100, "VALE": 100})
    put(conn, "DFEV", DAY - timedelta(days=1), {"PETR": 120, "VALE": 100})
    put(conn, "AVES", DAY - timedelta(days=1), {"PETR": 120, "VALE": 100})
    put(conn, "DFEV", DAY, {"PETR": 120, "VALE": 120})
    put(conn, "AVES", DAY, {"PETR": 120, "VALE": 120})
    r = notify.build(conn, settings(tmp_path), Refs(), DAY)
    assert list(r.bought.values())[0].startswith("🆕 VALE")  # new first
    assert not list(r.bought.values())[1].startswith("🆕")  # PETR was already there yesterday
    text = notify.render(r)
    assert "Both bought" in text and "🆕 VALE" in text


def test_health(conn):
    put(conn, "DFEV", DAY - timedelta(days=1), {"PETR": 1})
    put(conn, "AVES", DAY - timedelta(days=10), {"PETR": 1})
    db.set_meta(conn, "catchup_started", now().isoformat(timespec="seconds"))
    problems = notify.health(conn, ["DFEV", "AVES"], DAY, check_catchup=True)
    assert problems[0].startswith("AVES: last day 2026-10-16, 5 weekdays")
    assert "did not finish" in problems[1]
    finished = {"at": now().isoformat(timespec="seconds"), "errors": 1, "failed": ["AVES"]}
    db.set_meta(conn, "catchup_finished", json.dumps(finished))
    assert "failed for AVES" in notify.health(conn, ["DFEV"], DAY, check_catchup=True)[0]
    finished["errors"], finished["failed"] = 0, []
    db.set_meta(conn, "catchup_finished", json.dumps(finished))
    assert notify.health(conn, ["DFEV"], DAY, check_catchup=True) == []
