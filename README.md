# etf-history

Daily holdings history of two emerging-markets value ETFs — **DFEV** (Dimensional, since its inception in April
2022) and **AVES** (Avantis, collected daily from 2026-10-22, with sparser history back to 2021) — with dashboards
to analyze their Brazilian companies (every class and ADR of a company grouped together): share count evolution,
changes between two dates, composition, per-company details with both funds together, and both funds side by side ("Compare funds").

Sources: Dimensional's public daily files (`tools-blob.dimensional.com/etf/YYYYMMDD/DFEV.csv`) and Avantis's
fund data API (`avantisinvestors.com/fund/808/fundData?type=holdings`). Avantis only publishes the latest day, so
AVES history exists only from the day collection started: keep the daily job running.

## Running

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync --extra dashboard             # without the extra: collection only (e.g. on a 32-bit Raspberry Pi)
uv run etf-history init
uv run etf-history backfill-daily     # full history, ~20 min the first time; can be interrupted and resumed
uv run etf-history status
uv run etf-history dashboard          # http://localhost:8501
```

After that, `uv run etf-history catch-up` downloads only the missing days, and `uv run etf-history notify
--dry-run` prints the morning report (what both funds bought or sold in the last 30 days, entries and exits,
collection problems).

AVES history before daily collection (monthly-ish Wayback Machine captures of the Avantis page since 2023-10 and
quarterly SEC N-PORT filings since 2021-11) is loaded once with:

```sh
echo 'ETF_HISTORY_SEC_USER_AGENT="Your Name you@example.com"' >> .env   # SEC requires a contact
uv run etf-history backfill-history
```

## Raspberry Pi

Collection runs from systemd user timers at 09:00 (followed by the Telegram report) and 21:00 (São Paulo time). Use Raspberry Pi OS **Lite**: no
desktop, a third of the packages, and much faster `apt upgrade` on small boards.

Raspberry Pi OS Trixie images set up the first user and SSH with cloud-init (`user-data` on the boot partition);
Raspberry Pi Imager older than 2.0 (e.g. 1.8.x from distro repositories) can neither list current images nor write
that file. Either use a current Imager or write the `.img.xz` with `xzcat … | sudo dd of=/dev/<card>` and edit
`user-data` (a `users:` entry with `ssh_authorized_keys`, plus `timezone:`) before the first boot.

**64-bit Pi (Pi 3 or newer, Pi 2 v1.2, Zero 2 W) on Raspberry Pi OS Lite 64-bit:** collection and dashboard.

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt-get install -y git
git clone <repo> ~/code/etf-history && cd ~/code/etf-history
uv sync --frozen --no-dev --extra dashboard
uv run etf-history init && uv run etf-history backfill-daily
echo 'ETF_HISTORY_SEC_USER_AGENT="Your Name you@example.com"' >> .env   # SEC requires a contact
uv run etf-history backfill-history                                      # AVES history

# user services: collection timer and an always-on dashboard
mkdir -p ~/.config/systemd/user
cp deploy/*.service deploy/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now etf-history-morning.timer etf-history-catchup.timer etf-history-dashboard.service
sudo loginctl enable-linger "$USER"   # keep services running without an open session
```

**32-bit Pi (Pi 1, Pi Zero/Zero W, Pi 2 v1.1) on Raspberry Pi OS Lite 32-bit:** collection only. Streamlit needs
pyarrow, which has no 32-bit ARM wheels, so the dashboard runs on another machine on a copy of the Pi's `data/`.
uv has an ARMv6 build and uses the system Python (3.12+). Rather than running the backfills on the Pi (slow on a
single 700 MHz core), copy `data/` from a machine that already has the history.

```sh
# on the Pi
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt-get install -y git
git clone <repo> ~/code/etf-history && cd ~/code/etf-history
uv sync --frozen --no-dev
mkdir -p ~/.config/systemd/user
cp deploy/etf-history-morning.* deploy/etf-history-catchup.* ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now etf-history-morning.timer etf-history-catchup.timer
sudo loginctl enable-linger "$USER"

# on the machine with the history: seed the Pi with it (only once; afterwards the Pi's data/ is the original)
rsync -a data/ <user>@<pi>:code/etf-history/data/
ssh <user>@<pi> "echo 'ETF_HISTORY_SEC_USER_AGENT=\"Your Name you@example.com\"' >> code/etf-history/.env"

# on the dashboard machine: point it at the Pi's data/; `dashboard` copies it before starting. Use the Pi's
# hostname (raspberrypi.local; Pi OS Lite announces it via mDNS) rather than its DHCP address, which can change.
echo 'ETF_HISTORY_SYNC_FROM="<user>@raspberrypi.local:code/etf-history/data/"' >> .env
uv run etf-history dashboard          # --no-sync to skip the copy
```

Once the Pi collects, don't run `catch-up` on the dashboard machine: the next copy overwrites its database.

Updating the code on the Pi:

```sh
cd ~/code/etf-history && git pull && uv sync --frozen --no-dev    # add --extra dashboard on a 64-bit Pi
systemctl --user daemon-reload    # only if files in deploy/ changed (copy them to ~/.config/systemd/user/ first)
```

Logs of the last downloads (Raspberry Pi OS keeps the journal in memory, so only since the last boot):

```sh
journalctl _SYSTEMD_USER_UNIT=etf-history-morning.service -n 50   # 09:00: catch-up + report
journalctl _SYSTEMD_USER_UNIT=etf-history-catchup.service -n 50   # 21:00
systemctl --user list-timers      # next run
```

## Notifications

The 09:00 run sends a Telegram message when there is something to report: companies every fund bought (+5% or
more, or a new position) or sold over its last 30 days, splits seen in every fund, entries and exits, collection
problems and Brazilian securities missing from `seeds/securities.csv`. Items new since the previous morning are
marked 🆕. To set it up on the collector:

1. In Telegram, talk to @BotFather, `/newbot`, and copy the token.
2. Put it in the collector's `.env` (it is a secret; don't commit it):
   `ETF_HISTORY_TELEGRAM_TOKEN="<token>"`
3. Send any message to your new bot, then run `uv run etf-history telegram-chat-id`: it saves the chat id in
   `.env` and sends a test message.

`uv run etf-history notify --dry-run` prints today's report; `--as-of YYYY-MM-DD` simulates another morning.

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
