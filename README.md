# etf-history

Daily holdings history of two emerging-markets value ETFs — **DFEV** (Dimensional, since its inception in April
2022) and **AVES** (Avantis, collected daily from 2026-10-22, with sparser history back to 2021) — with dashboards
to analyze their Brazilian companies (every class and ADR of a company grouped together): share count evolution,
changes between two dates, composition, per-company details, and both funds side by side ("Compare funds").

Sources: Dimensional's public daily files (`tools-blob.dimensional.com/etf/YYYYMMDD/DFEV.csv`) and Avantis's
fund data API (`avantisinvestors.com/fund/808/fundData?type=holdings`). Avantis only publishes the latest day, so
AVES history exists only from the day collection started: keep the daily job running.

## Running

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run etf-history init
uv run etf-history backfill-daily     # full history, ~20 min the first time; can be interrupted and resumed
uv run etf-history status
uv run etf-history dashboard          # http://localhost:8501
```

After that, `uv run etf-history catch-up` downloads only the missing days.

AVES history before daily collection (monthly-ish Wayback Machine captures of the Avantis page since 2023-10 and
quarterly SEC N-PORT filings since 2021-11) is loaded once with:

```sh
echo 'ETF_HISTORY_SEC_USER_AGENT="Your Name you@example.com"' >> .env   # SEC requires a contact
uv run etf-history backfill-history
```

## Raspberry Pi

Meant for Raspberry Pi OS 64-bit (aarch64); uv installs Python 3.12+ if the system one is older.

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone <repo> ~/code/etf-history && cd ~/code/etf-history
uv sync --frozen
uv run etf-history init && uv run etf-history backfill-daily

# user services: download at 09:00 and 21:00 (São Paulo time) and an always-on dashboard
mkdir -p ~/.config/systemd/user
cp deploy/*.service deploy/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now etf-history-catchup.timer etf-history-dashboard.service
sudo loginctl enable-linger "$USER"   # keep services running without an open session

journalctl --user -u etf-history-catchup -n 50   # log of the last download
```

The units assume the repository at `~/code/etf-history` and uv at `~/.local/bin/uv`; adjust `WorkingDirectory`
and `ExecStart` otherwise. To move the downloaded history to another machine, copy the `data/` folder (database
+ raw files).

## Data

- SQLite database at `data/etf.db` (~20 MB), Brazilian holdings only: `snapshot` (one row per downloaded day),
  `holding` (one row per position), `missing_day` (holidays without a file), `meta`.
- Original files with every holding, compressed, in `data/raw/daily/<FUND>/YYYY/` (DFEV ~135 MB; AVES ~100 KB
  per day), plus AVES history in `data/raw/archive/` and `data/raw/nport/` (~5 MB).
- `seeds/securities.csv` (ADRs of Brazilian companies and their share ratios) and `seeds/company_aliases.csv`
  (companies whose B3 code changed). A new Brazilian ADR in the fund must be added to `securities.csv`.
- Only companies with shares on B3 are stored (plus their ADRs); BDRs of issuers listed abroad (JBS NV, XP…) are
  not.
- Details and data pitfalls: `CLAUDE.md`.

## License

The code is licensed under the [GNU AGPL v3.0 or later](LICENSE). If you modify the project and offer it as a
network service (e.g. a public dashboard), you must make the source code of your modifications available to its
users.

The license covers the code, not the data. Holdings data belongs to Dimensional Fund Advisors and Avantis
Investors (American Century) and is subject to their terms of use. The files in `tests/fixtures/` are small excerpts used only for testing.
