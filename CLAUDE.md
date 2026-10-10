# etf-history

Daily holdings history of Dimensional ETFs (for now only **DFEV**, Dimensional Emerging Markets Value ETF), with
Streamlit dashboards of its Brazilian companies. Only Brazilian holdings go into the database; the raw files keep
everything. Collection and storage in Python (uv), SQLite in a single
file. Will run on a Raspberry Pi (daily job + dashboard always on in the local network).

## Keep docs up to date

Every change in behavior updates the docs **in the same change**: commands, environment variables, structure and
data pitfalls here; install/run steps in `README.md`; tables/columns in the comments of `src/etf_history/schema.sql`.

## Structure

```
src/etf_history/
  cli.py                Typer commands (`etf-history ...`)
  config.py             repo .env + environment variables → Settings
  schema.sql            SQLite schema (create if not exists; applied by every command)
  db.py                 connection (WAL, autocommit), replace_snapshot (one day = one transaction), missing_day, meta
  refs.py               seeds → Refs: is_brazilian(isin), classify(isin) → country/company/kind/share_ratio
  http.py               httpx with retry; 404 → None
  dates.py              today() in São Paulo time, weekdays_backwards
  tickers.py            br_ticker(isin): BRPETRACNPR6 → PETR4
  sources/dimensional.py  URL, daily CSV parser, brazilian() filter, ingest_day, reload_raw
  dashboard/
    app.py              Streamlit: navigation, global fund/period sidebar, overview page
    data.py             queries (10 min cache), company aggregation, palette, chart styling
    views/              pages: evolution, changes, composition, company
seeds/
  securities.csv        ADRs of Brazilian companies and foreign BDRs: country, company, share_ratio (+ evidence)
  company_aliases.csv   issuer codes that changed (EMBR → EMBJ, ARZZ → AZZA, RRRP → BRAV, NTCO → NATU)
tests/                  parser, refs, tickers, database load; fixtures/ with real excerpts from 2022 and 2026
deploy/                 systemd user units: catch-up twice a day and the dashboard
data/                   (git-ignored) etf.db, raw/daily/<FUND>/<YYYY>/<YYYYMMDD>.csv.gz, logs
```

## Commands

```sh
uv run etf-history init                       # create the database
uv run etf-history backfill-daily             # from yesterday back to the fund's inception; resumable
uv run etf-history backfill-daily --since 2025-01-01 --until 2025-06-30
uv run etf-history fetch-daily [--day YYYY-MM-DD]   # one day, or the missing days of the last week
uv run etf-history catch-up                   # daily job: every missing day
uv run etf-history status [--gaps]            # days per fund, date range, missing days
uv run etf-history seed                       # re-parse raw files if seeds/ changed (download commands do it too)
uv run etf-history reload                     # re-parse every raw file in data/raw (after a parser change, ~30 s)
uv run etf-history dashboard [--port 8501]    # Streamlit on 0.0.0.0

uv run pytest
uv run ruff check src tests && uv run ruff format src tests
```

## Environment variables

| Variable | Default | Use |
|---|---|---|
| `ETF_HISTORY_DATA_DIR` | `<repo>/data` | database and raw files |
| `ETF_HISTORY_DB` | `$ETF_HISTORY_DATA_DIR/etf.db` | SQLite file |
| `ETF_HISTORY_SEEDS_DIR` | `<repo>/seeds` | seeds |
| `ETF_HISTORY_FUNDS` | `DFEV` | funds to collect, comma-separated |

## Source: Dimensional daily CSV

- URL: `https://tools-blob.dimensional.com/etf/YYYYMMDD/<FUND>.csv`, no authentication. The whole history is
  there: DFEV since **2022-04-26** (`FIRST_DAY` in `sources/dimensional.py`).
- Days without a file (404): weekends (never requested), **US holidays** and the most recent days. A day's file
  shows up one or two days later (on Saturday 2026-10-10, 10-08 was there and 10-09 was not yet). A 404 older
  than 5 days goes to `missing_day` and is not requested again; a recent 404 is not recorded.
- If the `date` inside the file does not match the URL date, the file is not loaded and the day goes to
  `missing_day` with `reason = 'date:<date in the file>'`.
- Columns: `date, etf_ticker, ticker, description, weight, market_value, identifier, isin, sedol, shares,
  coupon_rate, maturity_date, principal`. `shares` = actual share count; `market_value` = US$; `weight` = fraction
  of the fund's net assets. The old notebook (`random-scripts/finance/dfev.ipynb`) divided `shares` by 100 for
  readability only; the database stores the real value.

## Data pitfalls

- **`principal` changed meaning:** older files repeat `shares` there; newer ones put the local-currency value
  (BRL for B3 stocks, US$ for ADRs). `value_local` is only stored when `principal != shares`, so it is NULL in the
  older period. Clean switch: 2024-03-22 is the last old-format day, 2024-03-25 the first new one (afterwards only
  a few subscription rights such as MOVI1/HYPE1 still have `principal == shares`).
- **Lines without ISIN:** cash (`CASH AND CASH EQUIVALENTS`), currencies (`BRAZILIAN REAL`, key `BRL` from
  `identifier`), Indian capital gains tax accrual (`INRCGTAXE`), rights. The row key (`holding.key`) is ISIN →
  SEDOL → identifier → description, suffixed `#2` if repeated in the same file.
- **What is stored ("Brazilian"):** every `BR…` ISIN (B3-listed, including foreign BDRs such as JBS NV) plus the
  ADRs listed in `seeds/securities.csv` with country `BR`. The CSV has no country column, so a **new ADR of a
  Brazilian company is dropped until it is added to the seed**; ingest logs `possible Brazilian ADR not in
  seeds/securities.csv` when a non-BR line has the same name as a Brazilian line of the same file. After editing a
  seed, any download command (or `etf-history seed`) notices the hash change and re-parses the raw files.
- **Company = B3 issuer code** (ISIN chars 2:6: PETR, BBDC), so classes (PETR3/PETR4), units (SANB11), BDRs and
  rights of one issuer group together; ADRs get their company from the seed. Names are not usable: they vary
  across lines ("BANCO ABC BRASIL SA - RTS").
- **Codes change:** EMBR3 → EMBJ3 (new ISIN), Arezzo → Azzas, 3R → Brava, NTCO3 → NATU3. `company_aliases.csv`
  maps the old code to the current one. Mergers still change the share count (e.g. NATU ×47.6 in 2026-07..10).
- **Share counts per company are local-share equivalents:** `shares × share_ratio`, rights (`kind = 'right'`)
  excluded. Ratios in `securities.csv` were checked against prices (median ADR price / local price over every day
  both were held: PBR 2.00, ERJ 4.00, BBD 1.00…); CSAN/SUZ/UGP use the published ratio because their local share
  was never held. Common and preferred are added 1:1. A unit (SANB11) counts as one share.
- **Kind** from the ISIN: `D`/`R` at char 6 or a letter check digit (temporary ISIN) = right/receipt; `BDR` =
  BDR; `CD` = unit; otherwise share; non-BR ISIN from the seed = adr.
- **Splits/reverse splits and reorganizations** change the share count without trading (e.g. NATU3 ×47.6 between
  2026-07-10 and 2026-10-08). No adjustment yet; they show up as changes on the "Days with buys or sells" table.

## Loaded history (2026-10-10)

- 1,118 days from 2022-04-26 to 2026-10-08; 84k Brazilian rows (3.16M lines in the raw files); `data/etf.db`
  ~20 MB, `data/raw` ~135 MB. Re-parsing every raw file takes ~30 s.
- Full backfill took ~16 min (~1 day/s with the 0.25 s pause).
- The 45 weekdays without a file are all NYSE holidays (including 2025-01-09, the national day of mourning for
  Jimmy Carter). No real gaps, so N-PORT is not needed.
- 79 Brazilian securities from 68 companies on 2026-10-08; no unseeded Brazilian ADR warnings.

## Conventions

- **Language: everything in English** — code, comments, docs and dashboard UI. (The author chats in Portuguese.)
- Market dates in São Paulo time (`dates.today()`), timestamps in UTC (`dates.now()`); ruff (`DTZ`) flags
  `date.today()`/`datetime.now()` without a timezone.
- Idempotent loads: one day = one transaction replacing every row of that `(fund, as_of, source)`; same sha256 as
  stored = nothing to do. The raw file is always kept in `data/raw` before parsing; after a parser change run
  `etf-history reload` instead of downloading again.
- One failing day does not stop the backfill (logged and skipped; exit code 1 at the end if anything failed).
- Dashboards: fixed 8-color categorical palette in `dashboard/data.py` (color follows the company in selection
  order, at most 8 series per chart; the rest folds into "Other"); increases/decreases in blue/red; one y axis
  per chart; hover on every chart. Dates shown as YYYY-MM-DD.
- Dashboard fund and period are global: `data.sidebar()` draws them in `app.py` below the navigation menu, before
  the page runs; pages call `data.selection()` and filter with `sel.filter(df)`. Default period: last month;
  shortcuts (`PRESETS`: last week/month/3 months/year, all time) count back from the last day with a file and
  fill both dates; editing a date by hand clears the shortcut. The period snaps to days with a
  file (start → next file day, end → previous one). "Changes between dates" compares the period's first and last
  day. Don't add per-page date pickers.
- Streamlit only hot-reloads the page scripts, not the `etf_history` modules they import (`data.py`, `db.py`…):
  restart the dashboard after changing them (`systemctl --user restart etf-history-dashboard` on the Pi),
  otherwise it fails with stale-module errors such as `has no attribute 'sidebar'`.
- SEC N-PORT is out of scope: only if gaps show up that Dimensional does not cover. Starting point:
  `~/code/random-scripts/finance/dfev_holdings.py`.
- Git: branch `main`; commit only when asked.
- License: AGPL-3.0-or-later for the code (`LICENSE`); holdings data follows Dimensional's terms (see README).
