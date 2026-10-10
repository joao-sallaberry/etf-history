import plotly.graph_objects as go
import streamlit as st

from etf_history.dashboard import data

st.set_page_config(page_title="ETF history", page_icon="📈", layout="wide")


def overview():
    sel = data.selection()
    fund = sel.fund
    df = sel.filter(data.companies(fund))
    totals = sel.filter(data.fund_totals(fund)).set_index("as_of")
    days = totals.index
    first_day, last_day = days.min(), days.max()

    st.title(f"{fund} · Brazil")
    st.caption(
        f"{len(days)} days from {first_day:%Y-%m-%d} to {last_day:%Y-%m-%d}. Companies group every class, unit, "
        "ADR and BDR of the same B3 issuer (e.g. PETR = PETR3 + PETR4 + PBR + PBR/A)."
    )
    c1, c2, c3 = st.columns(3)
    n = int(totals.loc[last_day, "br_companies"])
    since = f"since {first_day:%Y-%m-%d}"
    c1.metric("Brazilian companies", n, f"{n - int(totals.loc[first_day, 'br_companies']):+d} {since}")
    weight, weight0 = totals.loc[last_day, "br_weight"], totals.loc[first_day, "br_weight"]
    c2.metric("Brazil weight in the fund", f"{weight:.2%}", f"{(weight - weight0) * 100:+.2f} pp {since}")
    value, value0 = totals.loc[last_day, "br_value_usd"], totals.loc[first_day, "br_value_usd"]
    c3.metric("Value in Brazilian companies", f"US$ {value / 1e6:,.1f}M", f"{value / value0 - 1:+.1%} {since}")

    color = data.series_colors()[0]
    left, right = st.columns(2)
    with left:
        st.subheader("Brazil weight in the fund")
        fig = go.Figure(
            go.Scatter(
                x=totals.index,
                y=totals["br_weight"],
                mode="lines",
                line=dict(width=2, color=color),
                name="Brazil weight",
                hovertemplate="%{y:.2%}<extra></extra>",
            )
        )
        st.plotly_chart(data.style(fig, height=300, y_format=".1%"), width="stretch")
    with right:
        st.subheader("Number of Brazilian companies")
        fig = go.Figure(
            go.Scatter(
                x=totals.index,
                y=totals["br_companies"],
                mode="lines",
                line_shape="hv",
                line=dict(width=2, color=color),
                name="Companies",
                hovertemplate="%{y}<extra></extra>",
            )
        )
        st.plotly_chart(data.style(fig, height=300), width="stretch")

    st.subheader(f"Brazilian companies on {last_day:%Y-%m-%d}")
    table = df[df["as_of"] == last_day][
        ["company_label", "securities", "shares_eq", "value_usd", "weight", "weight_br"]
    ].copy()
    table["weight"] *= 100
    table["weight_br"] *= 100
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={
            "company_label": "Company",
            "securities": "Securities",
            "shares_eq": st.column_config.NumberColumn("Shares (local eq.)", format="localized", help=data.SHARES_HELP),
            "value_usd": st.column_config.NumberColumn("Value (US$)", format="localized"),
            "weight": st.column_config.NumberColumn("Fund weight %", format="%.3f"),
            "weight_br": st.column_config.ProgressColumn(
                "Brazil weight %", format="%.2f", min_value=0, max_value=float(table["weight_br"].max())
            ),
        },
    )


pages = [
    st.Page(overview, title="Overview", icon=":material/dashboard:", default=True),
    st.Page("views/evolution.py", title="Share count evolution", icon=":material/show_chart:"),
    st.Page("views/changes.py", title="Changes between dates", icon=":material/compare_arrows:"),
    st.Page("views/composition.py", title="Composition", icon=":material/stacked_line_chart:"),
    st.Page("views/company.py", title="Company", icon=":material/search:"),
]
page = st.navigation(pages)  # menu first, then the global fund/period controls below it
if data.sidebar():
    page.run()
