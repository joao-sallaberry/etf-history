import plotly.graph_objects as go
import streamlit as st

from etf_history.dashboard import data

sel = data.selection()

st.title("Share count evolution")
st.caption(data.SHARES_HELP)
df = sel.filter(data.companies(sel.fund))

# Kept in session_state, not the widget: the options follow the period, and a widget whose options change resets.
options = data.company_options(df)
prev = st.session_state.get("evolution_companies")
picked = st.multiselect(
    "Companies",
    options,
    default=options[:5] if prev is None else [p for p in prev if p in options],
    max_selections=data.MAX_SERIES,
    help="Up to 8 per chart. The list starts with the largest on the last day.",
)
st.session_state["evolution_companies"] = picked
base100 = st.toggle("Index to 100 (relative change from each company's first day)")

if not picked:
    st.info("Pick at least one company.")
    st.stop()

colors = data.color_map(picked)
fig = go.Figure()
for name in picked:
    s = df[df["company_label"] == name].sort_values("as_of")
    y = s["shares_eq"] / s["shares_eq"].iloc[0] * 100 if base100 else s["shares_eq"]
    fig.add_scatter(
        x=s["as_of"],
        y=y,
        name=name,
        mode="lines",
        line_shape="hv",
        line=dict(width=2, color=colors[name]),
        hovertemplate="%{y:,.1f}" if base100 else "%{y:,.0f}",
    )
y_title = "Index (first day = 100)" if base100 else "Shares (local equivalents)"
st.plotly_chart(data.style(fig, height=480, y_title=y_title), width="stretch")

st.subheader("Days with buys or sells")
s = df[df["company_label"].isin(picked)].sort_values(["company_label", "as_of"]).copy()
s["before"] = s.groupby("company_label")["shares_eq"].shift()
changes = s[s["before"].notna() & (s["shares_eq"] != s["before"])].copy()
changes["delta"] = changes["shares_eq"] - changes["before"]
changes["pct"] = changes["delta"] / changes["before"] * 100
st.caption(f"{len(changes)} changes in the period. Splits, reverse splits and mergers show up here too.")
st.dataframe(
    changes.sort_values("as_of", ascending=False)[["as_of", "company_label", "before", "shares_eq", "delta", "pct"]],
    hide_index=True,
    width="stretch",
    column_config={
        "as_of": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
        "company_label": "Company",
        "before": st.column_config.NumberColumn("Before", format="localized"),
        "shares_eq": st.column_config.NumberColumn("After", format="localized"),
        "delta": st.column_config.NumberColumn("Change", format="localized"),
        "pct": st.column_config.NumberColumn("Change %", format="%+.2f%%"),
    },
)
