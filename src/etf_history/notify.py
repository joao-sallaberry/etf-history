"""Morning report: what every fund bought or sold over the last 30 days, entries/exits, collection health and
unseeded Brazilian securities. Stateless: the report is rebuilt each morning, and items not in the report of the
previous day are marked new.

Collection code: standard library only (runs on the Pi 1).
"""

import gzip
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from etf_history import config, db, sources
from etf_history.dates import now, today, weekdays_backwards
from etf_history.refs import Refs
from etf_history.sources import avantis, common, nport

WINDOW_DAYS = 30
MIN_CHANGE = 0.05  # a buy/sell is a share count change of at least 5% (or an entry/exit)
EVENT_FACTOR = (0.67, 1.5)  # every fund's share count × the same factor outside this range: split/reorganization
EVENT_TOLERANCE = 0.05  # "same factor": the funds' factors within 5% of each other
STALE_WEEKDAYS = 3  # a fund with more weekdays than this since its last day is reported as stale
CATCHUP_MAX_AGE = timedelta(hours=26)  # no finished catch-up for this long: the timer is not running


@dataclass(frozen=True)
class Window:
    fund: str
    first: date
    last: date


@dataclass
class Report:
    as_of: date
    windows: list[Window] = field(default_factory=list)
    sparse: list[str] = field(default_factory=list)  # funds with fewer than 2 days in the window
    # Sections: key → text line. Keys identify an item across days (for the "new" mark).
    bought: dict[tuple, str] = field(default_factory=dict)
    sold: dict[tuple, str] = field(default_factory=dict)
    events: dict[tuple, str] = field(default_factory=dict)
    entries: dict[tuple, str] = field(default_factory=dict)  # any fund
    exits: dict[tuple, str] = field(default_factory=dict)
    health: list[str] = field(default_factory=list)
    seeds: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.bought or self.sold or self.events or self.entries or self.exits or self.health or self.seeds)


def _days(conn: sqlite3.Connection, fund: str) -> list[date]:
    rows = conn.execute(f"with {db.BEST_SOURCE} select as_of from best order by as_of", (fund,))
    return [date.fromisoformat(r[0]) for r in rows]


def _shares(conn: sqlite3.Connection, fund: str, day: date) -> dict[str, float]:
    """Local-share equivalents per company on one day (rights excluded), from the day's best source."""
    rows = conn.execute(
        f"with {db.BEST_SOURCE} select company, sum(case when kind = 'right' then 0 else shares * share_ratio end) "
        "from holding h join best using (fund, as_of, source) where h.as_of = ? group by company",
        (fund, day.isoformat()),
    )
    return {company: shares or 0.0 for company, shares in rows if company}


def _names(conn: sqlite3.Connection, fund: str, day: date) -> dict[str, str]:
    """Company → name of its local share line (else any line) on one day."""
    rows = conn.execute(
        "select company, name, kind in ('share', 'unit') as local from holding where fund = ? and as_of = ? "
        "order by local",
        (fund, day.isoformat()),
    )
    return {company: name for company, name, _ in rows}


def window(days: list[date], as_of: date) -> tuple[date, date] | None:
    """First and last day of a fund in the WINDOW_DAYS ending at its last day up to `as_of`; None with fewer than
    two days there."""
    upto = [d for d in days if d <= as_of]
    if not upto:
        return None
    last = upto[-1]
    first = min(d for d in upto if d >= last - timedelta(days=WINDOW_DAYS))
    return (first, last) if first < last else None


def change(before: float, after: float) -> str | None:
    """'entry' | 'exit' | 'up' | 'down' | None (below MIN_CHANGE or not held)."""
    if before <= 0 and after <= 0:
        return None
    if before <= 0:
        return "entry"
    if after <= 0:
        return "exit"
    ratio = after / before - 1
    if ratio >= MIN_CHANGE:
        return "up"
    if ratio <= -MIN_CHANGE:
        return "down"
    return None


def _describe(before: float, after: float) -> str:
    kind = change(before, after)
    if kind == "entry":
        return "new"
    if kind == "exit":
        return "exited"
    return f"{after / before - 1:+.1%}" if before > 0 else "—"


def _event(pairs: list[tuple[float, float]]) -> bool:
    """Every fund's share count changed by about the same factor, far from 1: split or reorganization."""
    if any(b <= 0 or a <= 0 for b, a in pairs):
        return False
    factors = [a / b for b, a in pairs]
    lo, hi = EVENT_FACTOR
    same = max(factors) / min(factors) <= 1 + EVENT_TOLERANCE
    return same and (all(f > hi for f in factors) or all(f < lo for f in factors))


def trades(conn: sqlite3.Connection, funds: list[str], as_of: date) -> Report:
    """The joint buy/sell, corporate event and entry/exit sections for the morning of `as_of`."""
    report = Report(as_of)
    before, after, names = {}, {}, {}
    for fund in funds:
        span = window(_days(conn, fund), as_of)
        if span is None:
            report.sparse.append(fund)
            continue
        first, last = span
        report.windows.append(Window(fund, first, last))
        before[fund], after[fund] = _shares(conn, fund, first), _shares(conn, fund, last)
        names = _names(conn, fund, first) | _names(conn, fund, last) | names  # first fund's naming wins
    compared = [w.fund for w in report.windows]
    for company in sorted({c for f in compared for c in (*before[f], *after[f])}):
        pairs = {f: (before[f].get(company, 0.0), after[f].get(company, 0.0)) for f in compared}
        label = f"{company} · {names.get(company, '')}".rstrip(" ·")
        detail = ", ".join(f"{f} {_describe(*p)}" for f, p in pairs.items() if p != (0.0, 0.0))
        kinds = {f: change(*p) for f, p in pairs.items()}
        for f, kind in kinds.items():
            if kind in ("entry", "exit"):
                (report.entries if kind == "entry" else report.exits)[(kind, f, company)] = f"{label} ({f})"
        if len(compared) < 2:
            continue
        if _event(list(pairs.values())):
            report.events[("event", company)] = f"{label}: {detail}"
        elif all(k in ("entry", "up") for k in kinds.values()):
            report.bought[("bought", company)] = f"{label}: {detail}"
        elif all(k in ("exit", "down") for k in kinds.values()):
            report.sold[("sold", company)] = f"{label}: {detail}"
    return report


def health(conn: sqlite3.Connection, funds: list[str], as_of: date, *, check_catchup: bool) -> list[str]:
    """Collection problems: a failed or missing catch-up run, funds with no new day for a while."""
    problems = []
    for fund in funds:
        days = [d for d in _days(conn, fund) if d <= as_of]
        if not days:
            problems.append(f"{fund}: no snapshot at all")
            continue
        lag = sum(1 for d in weekdays_backwards(as_of - timedelta(days=1), days[-1]) if d > days[-1])
        if lag > STALE_WEEKDAYS:
            problems.append(f"{fund}: last day {days[-1]:%Y-%m-%d}, {lag} weekdays without a new one")
    if check_catchup:
        started, finished = db.get_meta(conn, "catchup_started"), db.get_meta(conn, "catchup_finished")
        result = json.loads(finished) if finished else None
        if started and (result is None or result["at"] < started):
            problems.append(f"catch-up started at {started} and did not finish")
        elif result is None or now() - datetime.fromisoformat(result["at"]) > CATCHUP_MAX_AGE:
            problems.append(f"no finished catch-up since {result['at'] if result else 'ever'} (timer stopped?)")
        elif result["errors"]:
            problems.append(f"last catch-up ({result['at']}) failed for {', '.join(result['failed'])}; see the journal")
    return problems


def _parser(fund: str, source: str):
    if source == "nport":
        return lambda data, refs: nport.parse(data, refs, fund=fund)
    if source == "archive" or sources.for_fund(fund) is avantis:  # Wayback captures are stored in the API shape
        return lambda data, refs: avantis.parse(data, refs, fund=fund)
    return lambda data, refs: sources.dimensional.parse(data, refs)


def unseeded(conn: sqlite3.Connection, funds: list[str], data_dir: Path, refs: Refs, as_of: date) -> list[str]:
    """Lines of each fund's last file that look Brazilian but are not in seeds/securities.csv (dropped)."""
    lines = []
    for fund in funds:
        row = conn.execute(
            f"with {db.BEST_SOURCE} select s.source, s.file_path from snapshot s join best using (fund, as_of, source) "
            "where s.as_of <= ? order by s.as_of desc limit 1",
            (fund, as_of.isoformat()),
        ).fetchone()
        if row is None or not (data_dir / row["file_path"]).exists():
            continue
        _, rows = _parser(fund, row["source"])(gzip.decompress((data_dir / row["file_path"]).read_bytes()), refs)
        lines += [
            f"{fund}: {r['isin']} {r['ticker'] or ''} {r['name'] or ''}".rstrip() for r in common.unseeded(rows, refs)
        ]
    return lines


def build(conn: sqlite3.Connection, settings: config.Settings, refs: Refs, as_of: date | None = None) -> Report:
    """The report for the morning of `as_of` (default today); `new` items are computed against the day before."""
    simulated = as_of is not None
    as_of = as_of or today()
    funds = list(settings.funds)
    report = trades(conn, funds, as_of)
    previous = trades(conn, funds, as_of - timedelta(days=1))
    report.health = health(conn, funds, as_of, check_catchup=not simulated)
    report.seeds = unseeded(conn, funds, settings.data_dir, refs, as_of)
    for section, old in [
        (report.bought, previous.bought),
        (report.sold, previous.sold),
        (report.events, previous.events),
        (report.entries, previous.entries),
        (report.exits, previous.exits),
    ]:
        new = {k: f"🆕 {v}" for k, v in section.items() if k not in old}
        rest = {k: v for k, v in section.items() if k in old}
        section.clear()
        section.update(new | rest)
    return report


def render(report: Report) -> str:
    """Plain text (no Telegram markup to escape)."""
    funds = " · ".join(f"{w.fund} {w.first:%Y-%m-%d} → {w.last:%Y-%m-%d}" for w in report.windows)
    parts = [f"etf-history · {report.as_of:%Y-%m-%d}", f"Last {WINDOW_DAYS} days: {funds or 'no data'}"]
    if report.sparse:
        parts.append(f"Not enough data in the window: {', '.join(report.sparse)}")
    every = "Both" if len(report.windows) == 2 else "All funds"
    # Green/red: buys and entries/sells and exits.
    for title, items in [
        (f"🟢 {every} bought", list(report.bought.values())),
        (f"🔴 {every} sold", list(report.sold.values())),
        ("Possible split or reorganization (same factor in every fund)", list(report.events.values())),
        ("🟢 Entered", list(report.entries.values())),
        ("🔴 Exited", list(report.exits.values())),
        ("Collection", report.health),
        ("Not in seeds/securities.csv (dropped)", report.seeds),
    ]:
        if items:
            parts.append(f"\n{title} ({len(items)})\n" + "\n".join(f"• {i}" for i in items))
    return "\n".join(parts)
