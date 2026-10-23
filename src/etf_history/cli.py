import logging
import sqlite3
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import typer

from etf_history import config, db, http, refs, sources
from etf_history.dates import today, weekdays_backwards
from etf_history.sources import dimensional

app = typer.Typer(no_args_is_help=True, add_completion=False)
log = logging.getLogger("etf_history")

# A 404 older than this is a holiday, not a file that is still to be published.
MISSING_AFTER_DAYS = 5
REQUEST_INTERVAL = 0.25  # seconds between downloads (be polite to the blob store)

FundOpt = Annotated[list[str] | None, typer.Option("--fund", "-f", help="Fund ticker (repeatable); default: config")]


def _setup() -> tuple[config.Settings, sqlite3.Connection]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = config.load()
    conn = db.connect(settings.db_path)
    db.init(conn)
    return settings, conn


def _funds(settings: config.Settings, funds: list[str] | None) -> list[str]:
    return [f.upper() for f in funds] if funds else list(settings.funds)


def _refs(conn: sqlite3.Connection, settings: config.Settings) -> refs.Refs:
    """Load seeds; if they changed since the stored rows were built, re-parse the raw files."""
    current = refs.load(settings.seeds_dir)
    if db.get_meta(conn, "seeds_sha256") != current.sha256:
        for fund in conn.execute("select distinct fund from snapshot").fetchall():
            log.info("%s: seeds changed, reloading raw files", fund[0])
            n = sources.for_fund(fund[0]).reload_raw(conn, fund=fund[0], data_dir=settings.data_dir, refs=current)
            log.info("%s: %d files reloaded", fund[0], n)
        db.set_meta(conn, "seeds_sha256", current.sha256)
    return current


def _backfill(conn, settings: config.Settings, refs_: refs.Refs, fund: str, since: date, until: date) -> dict[str, int]:
    """Walk weekdays from `until` back to `since`, downloading the days not yet known."""
    known = db.known_days(conn, fund)
    days = [d for d in weekdays_backwards(until, since) if d.isoformat() not in known]
    counts = {"loaded": 0, "unchanged": 0, "missing": 0, "mismatch": 0, "error": 0}
    log.info("%s: %d day(s) to check between %s and %s", fund, len(days), since, until)
    cutoff = today() - timedelta(days=MISSING_AFTER_DAYS)
    with http.client() as client:
        for i, day in enumerate(days, 1):
            try:
                result = dimensional.ingest_day(
                    conn,
                    client,
                    fund=fund,
                    day=day,
                    data_dir=settings.data_dir,
                    refs=refs_,
                    record_missing=day < cutoff,
                )
                counts[result.status] += 1
                if result.status == "loaded":
                    log.info("%s %s: %d rows (%d/%d)", fund, day, result.rows, i, len(days))
                elif result.status == "missing":
                    log.info("%s %s: no file", fund, day)
            except Exception as exc:  # one bad day must not stop the backfill
                counts["error"] += 1
                log.error("%s %s: %s", fund, day, exc)
            time.sleep(REQUEST_INTERVAL)
    log.info("%s: %s", fund, ", ".join(f"{k} {v}" for k, v in counts.items() if v))
    return counts


def _latest(conn, settings: config.Settings, refs_: refs.Refs, fund: str) -> dict[str, int]:
    """Sources without history (Avantis): load the day currently published."""
    try:
        with http.client() as client:
            result = sources.avantis.ingest_latest(conn, client, fund=fund, data_dir=settings.data_dir, refs=refs_)
    except Exception as exc:
        log.error("%s latest: %s", fund, exc)
        return {"error": 1}
    log.info("%s %s: %s %s", fund, result.as_of or "latest", result.status, result.rows or "")
    return {result.status: 1, "error": 0}


def _collect(conn, settings: config.Settings, refs_: refs.Refs, fund: str, since: date, until: date) -> dict[str, int]:
    if sources.for_fund(fund) is sources.avantis:
        return _latest(conn, settings, refs_, fund)
    return _backfill(conn, settings, refs_, fund, since, until)


@app.command()
def init():
    """Create the database."""
    settings, conn = _setup()
    r = _refs(conn, settings)
    typer.echo(f"Database: {settings.db_path} ({len(r.securities)} securities, {len(r.aliases)} aliases in seeds)")


@app.command()
def seed():
    """Re-parse the raw files if seeds/*.csv changed (also done by every download command)."""
    settings, conn = _setup()
    r = _refs(conn, settings)
    typer.echo(f"{len(r.securities)} securities, {len(r.aliases)} aliases; rows match the current seeds")


@app.command("backfill-daily")
def backfill_daily(
    fund: FundOpt = None,
    since: Annotated[str | None, typer.Option(help="Oldest day (YYYY-MM-DD); default: fund inception")] = None,
    until: Annotated[str | None, typer.Option(help="Newest day (YYYY-MM-DD); default: yesterday")] = None,
):
    """Download daily holdings from newest to oldest, skipping days already loaded. Resumable.

    Avantis funds have no history to download: only the latest day is loaded."""
    settings, conn = _setup()
    r = _refs(conn, settings)
    errors = 0
    for f in _funds(settings, fund):
        start = date.fromisoformat(since) if since else dimensional.FIRST_DAY.get(f, dimensional.DEFAULT_FIRST_DAY)
        end = date.fromisoformat(until) if until else today() - timedelta(days=1)
        errors += _collect(conn, settings, r, f, start, end)["error"]
    raise typer.Exit(1 if errors else 0)


@app.command("fetch-daily")
def fetch_daily(
    fund: FundOpt = None,
    day: Annotated[
        str | None, typer.Option(help="Single day (YYYY-MM-DD); default: missing days of the last week")
    ] = None,
):
    """Download one day, or the missing days of the last week."""
    settings, conn = _setup()
    r = _refs(conn, settings)
    errors = 0
    for f in _funds(settings, fund):
        if day and sources.for_fund(f) is sources.avantis:
            typer.echo(f"{f}: the source only publishes the latest day; run without --day")
            errors += 1
        elif day:
            d = date.fromisoformat(day)
            with http.client() as client:
                result = dimensional.ingest_day(
                    conn, client, fund=f, day=d, data_dir=settings.data_dir, refs=r, record_missing=False
                )
            typer.echo(f"{f} {d}: {result.status} {result.rows or ''}".rstrip())
        else:
            errors += _collect(conn, settings, r, f, today() - timedelta(days=7), today())["error"]
    raise typer.Exit(1 if errors else 0)


@app.command("catch-up")
def catch_up(fund: FundOpt = None):
    """Daily job: reload seeds and download every day still missing since inception. Idempotent."""
    settings, conn = _setup()
    r = _refs(conn, settings)
    errors = 0
    for f in _funds(settings, fund):
        start = dimensional.FIRST_DAY.get(f, dimensional.DEFAULT_FIRST_DAY)
        errors += _collect(conn, settings, r, f, start, today())["error"]
    raise typer.Exit(1 if errors else 0)


@app.command()
def reload(fund: FundOpt = None):
    """Re-parse archived raw files into the database (after a parser change), without downloading."""
    settings, conn = _setup()
    r = refs.load(settings.seeds_dir)
    for f in _funds(settings, fund):
        n = sources.for_fund(f).reload_raw(conn, fund=f, data_dir=settings.data_dir, refs=r)
        typer.echo(f"{f}: {n} files reloaded")
    db.set_meta(conn, "seeds_sha256", r.sha256)


@app.command()
def status(gaps: Annotated[bool, typer.Option(help="List weekdays with no file")] = False):
    """Snapshots per fund, date range, Brazilian holdings and gaps."""
    settings, conn = _setup()
    typer.echo(f"Database: {settings.db_path}")
    for row in conn.execute(
        "select fund, source, count(*) n, min(as_of) first, max(as_of) last, sum(row_count) rows "
        "from snapshot group by fund, source order by fund, source"
    ):
        br, companies = conn.execute(
            "select count(*), count(distinct company) from holding where fund = ? and as_of = ?",
            (row["fund"], row["last"]),
        ).fetchone()
        missing = conn.execute(
            "select reason, count(*) from missing_day where fund = ? group by reason", (row["fund"],)
        ).fetchall()
        typer.echo(
            f"{row['fund']} [{row['source']}]: {row['n']} days, {row['first']} → {row['last']}, "
            f"{row['rows']:,} rows; {br} Brazilian holdings ({companies} companies) on {row['last']}; "
            f"missing: {', '.join(f'{r[0]} {r[1]}' for r in missing) or 'none'}"
        )
        if gaps:
            for r in conn.execute("select day, reason from missing_day where fund = ? order by day", (row["fund"],)):
                typer.echo(f"  {r['day']} ({date.fromisoformat(r['day']):%a})  {r['reason']}")


@app.command()
def dashboard(
    port: Annotated[int, typer.Option(help="HTTP port")] = 8501,
    address: Annotated[str, typer.Option(help="Bind address")] = "0.0.0.0",
):
    """Start the Streamlit dashboard."""
    app_path = Path(__file__).parent / "dashboard" / "app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(app_path)]
    cmd += ["--server.port", str(port), "--server.address", address, "--browser.gatherUsageStats", "false"]
    cmd += ["--server.headless", "true"]  # otherwise the first run blocks on an e-mail prompt (bad for systemd)
    cmd += ["--client.toolbarMode", "viewer", "--theme.primaryColor", "#2a78d6"]
    raise typer.Exit(subprocess.call(cmd))


if __name__ == "__main__":
    app()
