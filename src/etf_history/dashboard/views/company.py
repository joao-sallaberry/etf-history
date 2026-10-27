import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from etf_history.dashboard import data

sel = data.selection()
funds = data.funds()
fund_colors = data.color_map(funds)  # one slot per fund, as on "Compare funds"

comp = {f: sel.filter(data.companies(f)) for f in funds}
hold = {f: sel.filter(data.holdings(f)) for f in funds}
# The first fund's naming wins (Avantis names carry "COMMON STOCK" suffixes).
labels = {}
for f in funds:
    for code, label in zip(comp[f]["company"], comp[f]["company_label"], strict=True):
        labels.setdefault(code, label)
# Options: largest on each fund's last day first (funds in order), then everything held in the period.
last_days = [df[df["as_of"] == df["as_of"].max()].sort_values("value_usd", ascending=False) for df in comp.values()]
options = list(dict.fromkeys([c for df in last_days for c in df["company"]] + sorted(labels, key=labels.get)))
if not options:
    st.info("No Brazilian company in the period.")
    st.stop()
# Kept in session_state, not the widget: the options follow the period, and a widget whose options change resets.
prev = st.session_state.get("company")
code = st.selectbox(
    "Company", options, index=options.index(prev) if prev in options else 0, format_func=lambda c: labels[c]
)
st.session_state["company"] = code
c = {f: comp[f][comp[f]["company"] == code].sort_values("as_of") for f in funds}
h = {f: hold[f][hold[f]["company"] == code] for f in funds}
held = [f for f in funds if not c[f].empty]

st.title(labels[code])
st.caption(
    " · ".join(
        f"{f}: held from {c[f]['as_of'].iloc[0]:%Y-%m-%d} to {c[f]['as_of'].iloc[-1]:%Y-%m-%d} "
        f"({c[f]['securities'].iloc[-1]})"
        if f in held
        else f"{f}: not held in the period"
        for f in funds
    )
)
for f in held:
    first, last = c[f].iloc[0], c[f].iloc[-1]
    c1, c2, c3 = st.columns(3)
    c1.metric(
        f"{f} · Shares (local eq.) on {last['as_of']:%Y-%m-%d}",
        f"{last['shares_eq']:,.0f}",
        f"{(last['shares_eq'] / first['shares_eq'] - 1):+.1%} in the period"
        if first["shares_eq"] and last["shares_eq"] != first["shares_eq"]
        else None,
        help=data.SHARES_HELP,
    )
    c2.metric(f"{f} · Value", f"US$ {last['value_usd'] / 1e6:,.2f}M")
    c3.metric(f"{f} · Weight within Brazil", f"{last['weight_br']:.2%}")
data.sources_note(pd.concat(c.values()))

# Shares and value as change since each fund's first day in the period, so funds of very different sizes share
# one axis; the hover also shows the actual number.
for title, col, fmt, relative, shape in [
    ("Shares (local equivalents) · change in the period", "shares_eq", ",.0f", True, "hv"),
    ("Value (US$) · change in the period", "value_usd", ",.0f", True, "linear"),
    ("Weight within Brazil", "weight_br", ".2%", False, "linear"),
]:
    st.subheader(title)
    fig = go.Figure()
    for f in held:
        y = c[f][col]
        if relative:
            base = y.iloc[0] or float("nan")  # a first day with only rights has no share count to compare against
            y = y / base - 1
        fig.add_scatter(
            x=c[f]["as_of"],
            y=y,
            customdata=c[f][col],
            name=f,
            mode="lines+markers",
            line_shape=shape,
            line=dict(width=2, color=fund_colors[f]),
            marker=data.source_markers(c[f]["source"], fund_colors[f]),
            hovertemplate=f"%{{y:+.1%}} (%{{customdata:{fmt}}})" if relative else f"%{{y:{fmt}}}",
        )
    st.plotly_chart(data.style(fig, height=280, y_format="+.0%" if relative else ".1%"), width="stretch")
    if relative:
        st.caption(
            "Change since each fund's first day in the period: "
            + ", ".join(f"{f} on {c[f]['as_of'].iloc[0]:%Y-%m-%d}" for f in held)
            + "."
        )

# Share count stacked by security, one chart per fund, so the contribution of each class/ADR is visible.
st.subheader("Shares by security (local equivalents)")
by_sec = {}
for f in held:
    shares = h[f][h[f]["kind"] != "right"]
    pivot = shares.pivot_table(index="as_of", columns="ticker", values="shares_eq", aggfunc="sum")
    by_sec[f] = pivot.reindex(c[f]["as_of"]).fillna(0)
# Same color for a ticker in every fund's chart: largest on the last days first.
tickers = (
    pd.concat([s.iloc[-1] for s in by_sec.values()]).groupby(level=0).sum().sort_values(ascending=False).index.tolist()
)
keep = tickers if len(tickers) <= data.MAX_SERIES else tickers[: data.MAX_SERIES - 1]  # rest folds into "Other"
colors = data.color_map(keep)
colors["Other"] = data.OTHER_GRAY[data.mode()]
for col, f in zip(st.columns(len(held)), held, strict=True):
    s = by_sec[f]
    if other := [t for t in s.columns if t not in keep]:
        s = s.drop(columns=other).assign(Other=s[other].sum(axis=1))
    fig = go.Figure()
    for ticker in [t for t in [*keep, "Other"] if t in s.columns]:
        fig.add_scatter(
            x=s.index,
            y=s[ticker],
            name=ticker,
            mode="lines",
            line_shape="hv",
            stackgroup="one",
            line=dict(width=0.5, color=colors[ticker]),
            fillcolor=colors[ticker],
            hovertemplate="%{y:,.0f}",
        )
    col.markdown(f"**{f}**")
    col.plotly_chart(data.style(fig, height=340), width="stretch")

with st.expander("All securities, all dates"):
    st.dataframe(
        pd.concat(h[f].assign(fund=f) for f in funds).sort_values(
            ["as_of", "fund", "value_usd"], ascending=[False, True, False]
        )[
            [
                "as_of",
                "fund",
                "source",
                "ticker",
                "isin",
                "kind",
                "share_ratio",
                "shares",
                "shares_eq",
                "value_usd",
                "value_local",
                "weight",
            ]
        ],
        hide_index=True,
        width="stretch",
        column_config={
            "as_of": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
            "fund": "Fund",
            "source": "Source",
            "ticker": "Ticker",
            "isin": "ISIN",
            "kind": "Kind",
            "share_ratio": st.column_config.NumberColumn("Ratio", help="Local shares per unit (PBR ADR = 2)"),
            "shares": st.column_config.NumberColumn("Shares", format="localized"),
            "shares_eq": st.column_config.NumberColumn("Local eq.", format="localized"),
            "value_usd": st.column_config.NumberColumn("Value (US$)", format="localized"),
            "value_local": st.column_config.NumberColumn("Local value", format="localized"),
            "weight": st.column_config.NumberColumn("Fund weight", format="percent"),
        },
    )
