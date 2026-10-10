from pathlib import Path

from etf_history import db
from etf_history.sources import dimensional

FIXTURES = Path(__file__).parent / "fixtures"


def load(conn, name, sha="x"):
    as_of, rows = dimensional.parse((FIXTURES / name).read_bytes())
    db.replace_snapshot(
        conn, fund="DFEV", as_of=as_of, source="daily", fetched_at="t", file_path=name, sha256=sha, rows=rows
    )
    return as_of, rows


def test_replace_snapshot_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init(conn)
    as_of, rows = load(conn, "dfev_20261008.csv")
    load(conn, "dfev_20261008.csv")
    assert conn.execute("select count(*) from holding").fetchone()[0] == len(rows)
    assert conn.execute("select count(*) from snapshot").fetchone()[0] == 1
    assert db.known_days(conn, "DFEV") == {as_of}
    assert db.snapshot_sha(conn, "DFEV", as_of, "daily") == "x"


def test_meta_roundtrip(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init(conn)
    assert db.get_meta(conn, "seeds_sha256") is None
    db.set_meta(conn, "seeds_sha256", "abc")
    assert db.get_meta(conn, "seeds_sha256") == "abc"


def test_mark_missing_counts_as_known(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init(conn)
    db.mark_missing(conn, "DFEV", "2026-09-07", "404", "t")
    assert db.known_days(conn, "DFEV") == {"2026-09-07"}
