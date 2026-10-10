import sqlite3
from importlib.resources import files
from pathlib import Path

HOLDING_COLUMNS = (
    "key",
    "isin",
    "ticker",
    "source_ticker",
    "name",
    "country",
    "company",
    "kind",
    "share_ratio",
    "sedol",
    "shares",
    "value_usd",
    "value_local",
    "weight",
)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: autocommit; transactions are explicit (`with transaction(conn)`).
    conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode = wal")  # dashboard reads while the job writes
    conn.execute("pragma foreign_keys = on")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(files("etf_history").joinpath("schema.sql").read_text(encoding="utf-8"))


class transaction:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("begin immediate")
        return self.conn

    def __exit__(self, exc_type, *_):
        self.conn.execute("rollback" if exc_type else "commit")


def known_days(conn: sqlite3.Connection, fund: str) -> set[str]:
    """Days already loaded or known to have no file."""
    rows = conn.execute(
        "select as_of from snapshot where fund = ? union select day from missing_day where fund = ?", (fund, fund)
    )
    return {r[0] for r in rows}


def snapshot_sha(conn: sqlite3.Connection, fund: str, as_of: str, source: str) -> str | None:
    row = conn.execute(
        "select sha256 from snapshot where fund = ? and as_of = ? and source = ?", (fund, as_of, source)
    ).fetchone()
    return row[0] if row else None


def replace_snapshot(
    conn: sqlite3.Connection,
    *,
    fund: str,
    as_of: str,
    source: str,
    fetched_at: str,
    file_path: str,
    sha256: str,
    rows: list[dict],
) -> None:
    """Replace all holdings of one (fund, as_of, source) in a single transaction."""
    with transaction(conn):
        conn.execute("delete from holding where fund = ? and as_of = ? and source = ?", (fund, as_of, source))
        conn.executemany(
            f"insert into holding (fund, as_of, source, {', '.join(HOLDING_COLUMNS)}) "
            f"values (?, ?, ?, {', '.join('?' * len(HOLDING_COLUMNS))})",
            [(fund, as_of, source, *(r[c] for c in HOLDING_COLUMNS)) for r in rows],
        )
        conn.execute(
            "insert or replace into snapshot (fund, as_of, source, fetched_at, file_path, sha256, row_count) "
            "values (?, ?, ?, ?, ?, ?, ?)",
            (fund, as_of, source, fetched_at, file_path, sha256, len(rows)),
        )
        conn.execute("delete from missing_day where fund = ? and day = ?", (fund, as_of))


def mark_missing(conn: sqlite3.Connection, fund: str, day: str, reason: str, checked_at: str) -> None:
    conn.execute(
        "insert or replace into missing_day (fund, day, reason, checked_at) values (?, ?, ?, ?)",
        (fund, day, reason, checked_at),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("select value from meta where key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("insert or replace into meta (key, value) values (?, ?)", (key, value))
