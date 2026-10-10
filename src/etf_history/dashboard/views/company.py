import plotly.graph_objects as go
import streamlit as st

from etf_history.dashboard import data

sel = data.selection()

comp = sel.filter(data.companies(sel.fund))
label = st.selectbox("Company", data.company_options(comp))
c = comp[comp["company_label"] == label].sort_values("as_of")
h = sel.filter(data.holdings(sel.fund))
h = h[h["company_label"] == label]

st.title(label)
first, last = c.iloc[0], c.iloc[-1]
st.caption(f"Held from {first['as_of']:%Y-%m-%d} to {last['as_of']:%Y-%m-%d} · securities: {last['securities']}")
c1, c2, c3 = st.columns(3)
c1.metric(
    "Shares (local eq.)",
    f"{last['shares_eq']:,.0f}",
    f"{(last['shares_eq'] / first['shares_eq'] - 1):+.1%} in the period" if first["shares_eq"] else None,
    help=data.SHARES_HELP,
)
c2.metric("Value", f"US$ {last['value_usd'] / 1e6:,.2f}M")
c3.metric("Weight within Brazil", f"{last['weight_br']:.2%}")

# Share count stacked by security, so the contribution of each class/ADR is visible.
st.subheader("Shares by security (local equivalents)")
by_sec = h[h["kind"] != "right"].pivot_table(index="as_of", columns="ticker", values="shares_eq", aggfunc="sum")
by_sec = by_sec.reindex(c["as_of"]).fillna(0)
order = by_sec.iloc[-1].sort_values(ascending=False).index.tolist()
if len(order) > data.MAX_SERIES:  # fold the smallest into "Other" (8 slots max)
    keep = order[: data.MAX_SERIES - 1]
    by_sec["Other"] = by_sec.drop(columns=keep).sum(axis=1)
    order = [*keep, "Other"]
colors = data.color_map([o for o in order if o != "Other"])
colors["Other"] = data.OTHER_GRAY[data.mode()]
fig = go.Figure()
for ticker in order:
    fig.add_scatter(
        x=by_sec.index,
        y=by_sec[ticker],
        name=ticker,
        mode="lines",
        line_shape="hv",
        stackgroup="one",
        line=dict(width=0.5, color=colors[ticker]),
        fillcolor=colors[ticker],
        hovertemplate="%{y:,.0f}",
    )
st.plotly_chart(data.style(fig, height=380), width="stretch")

color = data.series_colors()[0]
for title, col, fmt in [("Value (US$)", "value_usd", ",.0f"), ("Weight within Brazil", "weight_br", ".2%")]:
    st.subheader(title)
    fig = go.Figure(
        go.Scatter(
            x=c["as_of"],
            y=c[col],
            mode="lines",
            line=dict(width=2, color=color),
            name=title,
            hovertemplate=f"%{{y:{fmt}}}<extra></extra>",
        )
    )
    st.plotly_chart(data.style(fig, height=260, y_format=".1%" if col == "weight_br" else None), width="stretch")

with st.expander("All securities, all dates"):
    st.dataframe(
        h.sort_values(["as_of", "value_usd"], ascending=[False, False])[
            [
                "as_of",
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
