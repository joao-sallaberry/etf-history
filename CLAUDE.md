# etf-history

Daily holdings history of emerging-markets value ETFs — **DFEV** (Dimensional Emerging Markets Value ETF) and
**AVES** (Avantis Emerging Markets Value ETF) — with Streamlit dashboards of their Brazilian companies. Only
Brazilian holdings go into the database; the raw files keep everything. Collection and storage in Python (uv),
SQLite in a single file. Collection runs on a Raspberry Pi 1 Model B (`raspberrypi.local`, DHCP, user `joao`, 32-bit
ARMv6, 512 MB) with a systemd user timer; the dashboard runs elsewhere on a copy of `data/`.

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
  sources/__init__.py   fund → source module registry (for_fund; unlisted tickers are Dimensional)
  sources/common.py     Result, brazilian() filter shared by every source
  sources/dimensional.py  URL, daily CSV parser, ingest_day, reload_raw
  sources/avantis.py    Avantis JSON API: parser, ingest_latest (latest day only), reload_raw
  sources/wayback.py    AVES history from Wayback captures of the fund page (source 'archive')
  sources/nport.py      AVES quarter-end history from SEC N-PORT filings (source 'nport')
  dashboard/
    app.py              Streamlit: navigation, global fund/period sidebar, overview page
    data.py             queries (10 min cache, one source per day), company aggregation, palette, chart styling
    views/              pages: evolution, changes, composition, company and compare (these two: every fund)
seeds/
  securities.csv        ADRs of Brazilian companies, foreign BDRs and known non-stored issuers (XP…): country,
                        company, share_ratio (+ evidence)
  company_aliases.csv   issuer codes that changed (EMBR → EMBJ, ARZZ → AZZA, RRRP → BRAV, NTCO → NATU)
tests/                  parsers, refs, tickers, database load; fixtures/ with real excerpts (DFEV 2022/2026, AVES 2026)
deploy/                 systemd user units: catch-up twice a day and the dashboard
data/                   (git-ignored) etf.db, raw/daily/<FUND>/<YYYY>/<YYYYMMDD>.csv.gz (.json.gz for AVES),
                        raw/archive/AVES/<YYYY>/<YYYYMMDD>.json.gz, raw/nport/AVES/<YYYY>/<YYYYMMDD>_<accession>.xml.gz
```

## Commands

```sh
uv sync --extra dashboard                     # pandas/plotly/streamlit are the `dashboard` extra; plain `uv sync`
                                              # installs collection only (32-bit ARM: no pyarrow wheels)
uv run etf-history init                       # create the database
uv run etf-history backfill-daily             # from yesterday back to the fund's inception; resumable (AVES: latest day)
uv run etf-history backfill-daily --since 2025-01-01 --until 2025-06-30
uv run etf-history fetch-daily [--day YYYY-MM-DD]   # one day, or the missing days of the last week (no --day for AVES)
uv run etf-history catch-up                   # daily job: every missing day
uv run etf-history backfill-history           # AVES past: Wayback captures + N-PORT filings; resumable (~3 min)
uv run etf-history status [--gaps]            # days per fund, date range, missing days
uv run etf-history seed                       # re-parse raw files if seeds/ changed (download commands do it too)
uv run etf-history reload                     # re-parse every raw file in data/raw (after a parser change, ~30 s)
uv run etf-history dashboard [--port 8501]    # Streamlit on 0.0.0.0; first copies data/ from ETF_HISTORY_SYNC_FROM

uv run pytest
uv run ruff check src tests && uv run ruff format src tests
```

## Environment variables

| Variable | Default | Use |
|---|---|---|
| `ETF_HISTORY_DATA_DIR` | `<repo>/data` | database and raw files |
| `ETF_HISTORY_DB` | `$ETF_HISTORY_DATA_DIR/etf.db` | SQLite file |
| `ETF_HISTORY_SEEDS_DIR` | `<repo>/seeds` | seeds |
| `ETF_HISTORY_FUNDS` | `DFEV,AVES` | funds to collect, comma-separated |
| `ETF_HISTORY_SEC_USER_AGENT` | (empty) | `"Name email"` contact SEC requires; N-PORT download is skipped (error) without it |
| `ETF_HISTORY_SYNC_FROM` | (empty) | rsync source of the collector's `data/` (`joao@raspberrypi.local:code/etf-history/data/`; the hostname, since the Pi's DHCP address can change); `dashboard` copies it first (`--no-sync` skips; a failed copy only warns) |

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

## Source: Avantis JSON API (AVES)

- URL: `https://www.avantisinvestors.com/fund/<fundNumber>/fundData?type=holdings` (AVES = 808, `FUND_NUMBER` in
  `sources/avantis.py`), no authentication. Returns **only the latest day** (`portfolio.etfHoldingsAsOfDate`,
  usually the previous business day); no date parameter exists (tried `asOfDate`, `date`, `holdingsDate`). A day
  not fetched while it is the latest one is lost, so `catch-up` must keep running. No `missing_day` rows.
- Raw file: the whole JSON response, gzipped (~100 KB/day). `as_of` comes from `etfHoldingsAsOfDate`; a response
  for another fund (`ticker` ≠ fund) is rejected.
- Lines (`portfolio.etfHoldings`): `name, ticker, securityType, cusip, isin, sedol, shareQuantity, contractCount,
  baseMarketValue, weight, coupon, maturityDate, sector, country`. `ticker` is already B3-style (PRIO3).
  Currencies have no ISIN/SEDOL (keyed by CUSIP, e.g. `999TWDZ90`); some lines have empty value and weight.
- `weight` is published rounded to 0.01% ("0.45%"), so the stored `weight` is `baseMarketValue / Σ
  baseMarketValue` of the file (the published weights add to ~99.8%, against net assets). No local-currency value
  (`value_local` NULL).
- Inception 2021-09-28. History before daily collection comes from the two sources below; `snapshot.source` tells
  them apart. Several sources may hold the same day.

## Source: Wayback Machine captures of the AVES page (`archive`)

- The fund page `avantis-investments/avantis-emerging-markets-value-etf/` (and its `trading-details/`) embeds the
  full array of its day as a JS object literal (`etfHoldings:[{name:"…",…}]`, unquoted keys). The API itself was
  never captured. 38 captures (2023-01 → 2026-07) give **28 days from 2023-10-03 to 2026-07-29**, ~1 a month.
- Rejected (logged, retried on the next run): captures before 2023-10 (no array), the trading-details captures
  of 2023-10/2024-08/2025-02/2025-05 (the array is **AVEM**'s, fund 118 — the array must follow `fundNumber`
  808), and arrays cut at 1 MB by the archive (unclosed). Not a completeness test: the sum of published weights,
  which swings between 95% and 104% on complete days (US$ cash, money market, negative cash on flows).
- Kept arrays are stored in the API's JSON shape plus `"capture": <timestamp>`, so `avantis.parse` reads them and
  done captures are skipped. Two captures of the same day keep the first.

## Source: SEC N-PORT (`nport`)

- AVES = series `S000072996` of American Century ETF Trust (CIK 1710607). Filing list: EDGAR browse-edgar atom
  feed by series id; document: `Archives/edgar/data/1710607/<accession>/primary_doc.xml`. 19 NPORT-P filings,
  **quarter-ends from 2021-11-30 to 2026-05-31** (fiscal quarters end Feb/May/Aug/Nov), published ~60 days later.
- SEC blocks requests without a contact in the User-Agent ("Undeclared Automated Tool"):
  `ETF_HISTORY_SEC_USER_AGENT` in `.env`.
- `as_of` = `repPdDate`; per position: `identifiers/isin`, `identifiers/ticker` (B3-style), `balance` (shares when
  `units` = NS), `valUSD`, `pctVal` (% of net assets → `weight`), `invCountry` (feeds the unseeded-security
  warning), `currencyConditional/@exchangeRt` (→ `value_local` = valUSD × rate; NULL for US$ positions).

## Data pitfalls

- **`principal` changed meaning:** older files repeat `shares` there; newer ones put the local-currency value
  (BRL for B3 stocks, US$ for ADRs). `value_local` is only stored when `principal != shares`, so it is NULL in the
  older period. Clean switch: 2024-03-22 is the last old-format day, 2024-03-25 the first new one (afterwards only
  a few subscription rights such as MOVI1/HYPE1 still have `principal == shares`).
- **Lines without ISIN:** cash (`CASH AND CASH EQUIVALENTS`), currencies (`BRAZILIAN REAL`, key `BRL` from
  `identifier`), Indian capital gains tax accrual (`INRCGTAXE`), rights. The row key (`holding.key`) is ISIN →
  SEDOL → identifier → description, suffixed `#2` if repeated in the same file.
- **What is stored ("Brazilian"):** companies with shares on B3 — every `BR…` ISIN **except BDRs** (`BDR` at
  chars 6:9: JBS NV, Carrefour SA, Éxito are listed abroad and only have a receipt on B3) plus the ADRs listed in
  `seeds/securities.csv` with country `BR` (PBR, VALE, TIMB…). Issuers listed abroad with only a BDR in Brazil
  (XP, PAGS, STNE, INTR, AURA, Sigma) are not stored even though Avantis marks them `BRAZIL`; they are in the seed
  with their foreign country so they don't trigger warnings. A **new ADR of a Brazilian company is dropped until
  it is added to the seed**; ingest logs `possible Brazilian security not in seeds/securities.csv` for an unseeded
  line named like a stored line of the same file or with Avantis `country = BRAZIL`. After editing a seed, any
  download command (or `etf-history seed`) notices the hash change and re-parses the raw files.
- **Company = B3 issuer code** (ISIN chars 2:6: PETR, BBDC), so classes (PETR3/PETR4), units (SANB11) and
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

- DFEV: 1,118 days from 2022-04-26 to 2026-10-08; 84k Brazilian rows (3.16M lines in the raw files); `data/etf.db`
  ~20 MB, `data/raw` ~135 MB. Re-parsing every raw file takes ~30 s.
- Full backfill took ~16 min (~1 day/s with the 0.25 s pause).
- The 45 weekdays without a file are all NYSE holidays (including 2025-01-09, the national day of mourning for
  Jimmy Carter). No real gaps, so N-PORT is not needed for DFEV.
- AVES (2026-10-23): 19 N-PORT quarter-ends (2021-11-30 → 2026-05-31), 28 Wayback days (2023-10-03 →
  2026-07-29), daily from 2026-10-22; 62–82 Brazilian lines per day. Unseeded Brazilian ADRs found through
  Avantis/N-PORT country (ITUB, ASAI, SID, BAK, CBD, GET, TIMB) are now in the seed (DFEV never held them).
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
- One snapshot per day in the dashboard: `data.BEST_SOURCE` picks daily > archive > nport; `holdings`,
  `companies` and `fund_totals` carry a `source` column. Line charts put dots only on non-daily points
  (`data.source_markers`) and pages call `data.sources_note(df)`, so sparse AVES history never passes for a daily
  series.
- "Compare funds" (`views/compare.py`) and "Company" (`views/company.py`) show every fund: `app.py` calls
  `data.sidebar(fund_picker=False)` there, so the period covers any fund's days. On "Compare funds" each fund is
  read on its own snapshots (`data.snap_period`: last day up to the period end; for changes, last day up to the
  start, else its first day), and the column headers say which dates. "Company" draws one line per fund over the
  period (shares and value as change since each fund's first day in it, actual number on hover) and one "shares by security" chart per fund (ticker colors shared; Avantis writes `PBR.A`, Dimensional
  `PBR/A`). Fund colors are palette slots in `ETF_HISTORY_FUNDS` order (also the fund picker order; first =
  default).
- Dashboard fund and period are global: `data.sidebar()` draws them in `app.py` below the navigation menu, before
  the page runs; pages call `data.selection()` and filter with `sel.filter(df)`. Default period: last month;
  shortcuts (`PRESETS`: last week/month/3 months/year, all time) count back from the last day with a file and
  fill both dates; editing a date by hand clears the shortcut. The period snaps to days with a
  file (start → next file day, end → previous one). "Changes between dates" compares the period's first and last
  day. Don't add per-page date pickers.
- Page widgets whose options depend on the period (company pickers) reset when the options change, so the pick is
  kept in a plain `st.session_state` entry (`company`, `evolution_companies`) and passed back as `index`/`default`.
- **Dependencies:** collection code (`cli`, `db`, `sources/`…) must only use httpx, typer and the standard
  library, so it keeps running on the Pi 1; pandas/plotly/streamlit are imported only under `dashboard/`.
- **Raspberry Pi 1** (Raspberry Pi OS Lite 32-bit Trixie 2026-10-06, set up by a cloud-init `user-data` on the
  boot partition: user `joao`, SSH by key only, passwordless sudo, TZ America/Sao_Paulo): repo at
  `~/code/etf-history`, `uv sync --frozen --no-dev`, only `etf-history-catchup.timer` enabled, linger on.
  Python startup alone takes ~15 s there. The journal is volatile (Pi OS default, spares the SD card): read logs
  with `journalctl _SYSTEMD_USER_UNIT=etf-history-catchup.service` (`journalctl --user` finds nothing). The Pi is
  now the collector: its `data/` is the source of truth; don't run `catch-up` elsewhere and copy over it. The
  dashboard machine sets `ETF_HISTORY_SYNC_FROM`, so `etf-history dashboard` pulls the Pi's `data/` (rsync, no
  `--delete`, `*-shm` excluded) before starting. Reading the Pi's SQLite over the network (sshfs/NFS) is not an
  option: WAL readers need the writer's shared memory.
- Streamlit only hot-reloads the page scripts, not the `etf_history` modules they import (`data.py`, `db.py`…):
  restart the dashboard after changing them (`systemctl --user restart etf-history-dashboard` where it runs as a service),
  otherwise it fails with stale-module errors such as `has no attribute 'sidebar'`.
- SEC N-PORT is out of scope: only if gaps show up that Dimensional does not cover. Starting point:
  `~/code/random-scripts/finance/dfev_holdings.py`.
- Git: branch `main`; commit only when asked.
- License: AGPL-3.0-or-later for the code (`LICENSE`); holdings data follows Dimensional's terms (see README).
