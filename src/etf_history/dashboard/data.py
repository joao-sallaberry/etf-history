"""Queries and shared helpers for the Streamlit pages.

Everything is grouped by company (B3 issuer code, e.g. PETR = PETR3 + PETR4 + PBR + PBR/A). A company's share count
is in local-share equivalents: each security's shares × share_ratio (PBR ADR = 2 PETR3), rights excluded.

A day can have snapshots from several sources; queries keep one per day: daily > archive (Wayback) > nport (SEC).
"""

import sqlite3
from dataclasses import dataclass
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from etf_history import config

# Categorical slots in fixed order (validated palette); color follows the entity, never its rank.
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
OTHER_GRAY = {"light": "#a3a29d", "dark": "#6b6a65"}
INCREASE = {"light": "#2a78d6", "dark": "#3987e5"}  # diverging pair: blue (up) / red (down)
DECREASE = {"light": "#e34948", "dark": "#e66767"}
MAX_SERIES = len(SERIES_LIGHT)

SHARES_HELP = (
    "Local-share equivalents: every class of the company added up, ADRs converted by their ratio "
    "(1 PBR = 2 PETR3, 1 ERJ = 4 EMBJ3), subscription rights excluded."
)


def mode() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except AttributeError:
        return "light"


def series_colors() -> list[str]:
    return SERIES_DARK if mode() == "dark" else SERIES_LIGHT


# Best source of each (fund, day); join holding on it to read one snapshot per day.
BEST_SOURCE = """
    best as (
        select fund, as_of, source from (
            select fund, as_of, source, row_number() over (
                partition by fund, as_of
                order by case source when 'daily' then 0 when 'archive' then 1 else 2 end
            ) as n
            from snapshot where fund = ?
        ) where n = 1
    )"""
SOURCE_LABELS = {"daily": "daily file", "archive": "Wayback capture", "nport": "SEC N-PORT"}


def _connect() -> sqlite3.Connection:
    path = config.load().db_path
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)


@st.cache_data(ttl=600)
def funds() -> list[str]:
    """Funds in the database, in ETF_HISTORY_FUNDS order (the first one is the default), others after."""
    with _connect() as conn:
        found = [r[0] for r in conn.execute("select distinct fund from snapshot order by fund")]
    order = config.load().funds
    return sorted(found, key=lambda f: order.index(f) if f in order else len(order))


@st.cache_data(ttl=600)
def holdings(fund: str) -> pd.DataFrame:
    """Every Brazilian security line, every day (one source per day)."""
    with _connect() as conn:
        df = pd.read_sql_query(
            f"with {BEST_SOURCE} select h.as_of, h.source, isin, ticker, name, company, kind, share_ratio, shares, "
            "value_usd, value_local, weight from holding h join best using (fund, as_of, source)",
            conn,
            params=(fund,),
        )
    df["as_of"] = pd.to_datetime(df["as_of"])
    df["ticker"] = df["ticker"].fillna(df["isin"])
    df["shares_eq"] = (df["shares"] * df["share_ratio"]).where(df["kind"] != "right", 0.0)
    df["company_label"] = df["company"].map(_company_labels(df))
    return df


def _company_labels(df: pd.DataFrame) -> dict[str, str]:
    """'PETR · PETROLEO BRASILEIRO SA', named after the latest local share line (else any line)."""
    ordered = df.assign(local=df["kind"].isin(["share", "unit", "bdr"])).sort_values(["local", "as_of"])
    names = ordered.groupby("company")["name"].last()
    return {code: f"{code} · {name}" for code, name in names.items()}


@st.cache_data(ttl=600)
def companies(fund: str) -> pd.DataFrame:
    """One row per (day, company): local-share equivalents, value, weight in the fund and within Brazil."""
    df = holdings(fund).sort_values("value_usd", ascending=False)
    out = df.groupby(["as_of", "source", "company", "company_label"], as_index=False).agg(
        shares_eq=("shares_eq", "sum"),
        value_usd=("value_usd", "sum"),
        weight=("weight", "sum"),
        securities=("ticker", lambda s: ", ".join(dict.fromkeys(s))),
    )
    out["weight_br"] = out["weight"] / out.groupby("as_of")["weight"].transform("sum")
    return out.sort_values(["as_of", "value_usd"], ascending=[True, False]).reset_index(drop=True)


@st.cache_data(ttl=600)
def fund_totals(fund: str) -> pd.DataFrame:
    with _connect() as conn:
        df = pd.read_sql_query(
            f"with {BEST_SOURCE} select h.as_of, h.source, sum(value_usd) as br_value_usd, sum(weight) as br_weight, "
            "count(distinct company) as br_companies from holding h join best using (fund, as_of, source) "
            "group by h.as_of order by h.as_of",
            conn,
            params=(fund,),
        )
    df["as_of"] = pd.to_datetime(df["as_of"])
    return df


def company_options(df: pd.DataFrame) -> list[str]:
    """Company labels, largest on the last day first, then everything that was ever held."""
    last = df[df["as_of"] == df["as_of"].max()].sort_values("value_usd", ascending=False)
    return list(dict.fromkeys(last["company_label"].tolist() + sorted(df["company_label"].unique())))


@dataclass(frozen=True)
class Selection:
    fund: str
    start: date  # first day with a file inside the picked period
    end: date  # last day with a file inside the picked period

    def filter(self, df: pd.DataFrame) -> pd.DataFrame:
        return df[df["as_of"].dt.date.between(self.start, self.end)]


# Period shortcuts, counted back from the last day with a file; None = all time.
PRESETS = {
    "Last week": dict(weeks=1),
    "Last month": dict(months=1),
    "Last 3 months": dict(months=3),
    "Last year": dict(years=1),
    "All time": None,
}
DEFAULT_PRESET = "Last month"


def _preset_range(name: str, first: date, last: date) -> tuple[date, date]:
    offset = PRESETS[name]
    if offset is None:
        return first, last
    return max(first, (pd.Timestamp(last) - pd.DateOffset(**offset)).date()), last


def sidebar(*, fund_picker: bool = True) -> Selection | None:
    """Fund and period in the sidebar, drawn by app.py before any page so they apply to every page. Without the
    fund picker (pages comparing every fund) the period covers the days of any fund."""
    options = funds()
    if not options:
        st.warning("Empty database. Run `uv run etf-history backfill-daily`.")
        return None
    if fund_picker:
        fund = st.sidebar.selectbox("Fund", options, key="fund")
        days = fund_days(fund)
    else:
        fund = st.session_state.get("fund", options[0])
        days = sorted({d for f in options for d in fund_days(f)})
    first, last = days[0], days[-1]
    ss = st.session_state
    if "period_start" not in ss:  # first run of the session
        ss.preset = DEFAULT_PRESET
        ss.period_start, ss.period_end = _preset_range(DEFAULT_PRESET, first, last)

    def apply_preset():
        if ss.preset:  # clicking the selected pill again deselects it: keep the dates
            ss.period_start, ss.period_end = _preset_range(ss.preset, first, last)

    def clear_preset():
        ss.preset = None

    st.sidebar.pills("Period", list(PRESETS), key="preset", on_change=apply_preset)
    kw = dict(min_value=first, max_value=last, format="YYYY-MM-DD", on_change=clear_preset)
    picked_start = st.sidebar.date_input("Start date", key="period_start", **kw)
    picked_end = st.sidebar.date_input("End date", key="period_end", **kw)
    # Snap to days that have a file (weekends and US holidays have none).
    start = min((d for d in days if d >= picked_start), default=last)
    end = max((d for d in days if d <= picked_end), default=first)
    for picked, day in ((picked_start, start), (picked_end, end)):
        if picked != day:
            st.sidebar.caption(f"No snapshot on {picked:%Y-%m-%d}; using {day:%Y-%m-%d}.")
    if start > end:
        st.sidebar.warning("The start date must be before the end date.")
        return None
    selection = Selection(fund, start, end)
    st.session_state["selection"] = selection
    return selection


def fund_days(fund: str) -> list[date]:
    """Days with a snapshot of the fund, from any source."""
    return sorted(fund_totals(fund)["as_of"].dt.date)


def snap_period(days: list[date], start: date, end: date) -> tuple[date, date] | None:
    """A fund's own days for a period: its first day inside the period (changes start there, so a sparse history
    never credits the period with a trade made before it) and its last day up to `end` (holdings). Without a day
    inside the period both are that last day: a single snapshot, no change. None when the fund has no day up to
    `end`."""
    before_end = [d for d in days if d <= end]
    if not before_end:
        return None
    first = min((d for d in before_end if d >= start), default=before_end[-1])
    return first, before_end[-1]


def source_markers(sources: pd.Series, color: str) -> dict:
    """Markers only on snapshots that are not daily files, so sparse history does not pass for a daily series
    (and on a lone point, which has no line to show)."""
    return dict(size=[0 if s == "daily" and len(sources) > 1 else 7 for s in sources], color=color)


def sources_note(df: pd.DataFrame) -> None:
    """Caption naming the non-daily sources shown (drawn as dots on line charts)."""
    other = [SOURCE_LABELS[s] for s in SOURCE_LABELS if s != "daily" and (df["source"] == s).any()]
    if other:
        st.caption(
            f"Includes sparse history ({', '.join(other)}): dots on line charts mark those snapshots; lines "
            "between them are not daily data."
        )


def selection() -> Selection:
    """The fund and period picked in the sidebar (set by app.py on every run)."""
    sel = st.session_state.get("selection")
    if sel is None:
        st.stop()
    return sel


def style(fig: go.Figure, *, height: int = 420, y_title: str | None = None, y_format: str | None = None) -> go.Figure:
    grid = "rgba(128,128,128,0.18)"
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=24, b=8),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None),
    )
    fig.update_xaxes(showgrid=False, showspikes=True, spikemode="across", spikethickness=1, spikedash="dot")
    fig.update_yaxes(gridcolor=grid, zeroline=False, title=y_title, tickformat=y_format)
    return fig


def color_map(labels: list[str]) -> dict[str, str]:
    """Stable slot per label, in the order given (selection order); callers cap the list at MAX_SERIES."""
    colors = series_colors()
    return {label: colors[i % MAX_SERIES] for i, label in enumerate(labels)}
