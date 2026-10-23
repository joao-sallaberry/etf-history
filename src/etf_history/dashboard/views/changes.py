import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from etf_history.dashboard import data

sel = data.selection()

st.title("Changes between two dates")
df = data.companies(sel.fund).rename(columns={"shares_eq": "shares"})
st.caption(data.SHARES_HELP)
day_a, day_b = sel.start, sel.end
st.markdown(f"From **{day_a:%Y-%m-%d}** to **{day_b:%Y-%m-%d}** (the period in the sidebar).")
if day_a == day_b:
    st.info("Pick a period longer than one day in the sidebar.")
    st.stop()

cols = ["company", "company_label", "shares", "value_usd", "weight_br"]
a = df[df["as_of"].dt.date == day_a][cols]
b = df[df["as_of"].dt.date == day_b][cols]
m = pd.merge(b, a, on=["company", "company_label"], how="outer", suffixes=("_b", "_a"), indicator=True)

both = m[m["_merge"] == "both"].copy()
both["ratio"] = both["shares_b"] / both["shares_a"]
both["pct"] = (both["ratio"] - 1) * 100
changed = both[both["shares_b"] != both["shares_a"]].sort_values("pct", ascending=False)

st.subheader(f"Position changed ({len(changed)} of {len(both)} companies held on both days)")
if not changed[(changed["shares_a"] > 0) & (changed["shares_b"] > 0)].empty:
    mode = data.mode()
    # Log2 of the share ratio: doubling and halving sit at the same distance from zero, so one outlier
    # (e.g. a corporate reorganization) does not flatten every other bar. Top 15 each way.
    # A company held only through rights on one day has 0 shares there: no finite ratio, table only.
    finite = changed[(changed["shares_a"] > 0) & (changed["shares_b"] > 0)].copy()
    finite["log2"] = np.log2(finite["ratio"])
    bars = pd.concat([finite.nsmallest(15, "log2"), finite.nlargest(15, "log2")]).drop_duplicates("company")
    bars = bars.sort_values("log2")
    span = int(np.ceil(max(1.0, bars["log2"].abs().max())))
    ticks = list(range(-span, span + 1))
    fig = go.Figure(
        go.Bar(
            x=bars["log2"],
            y=bars["company_label"],
            orientation="h",
            customdata=bars[["pct", "shares_a", "shares_b"]],
            marker=dict(
                color=[data.INCREASE[mode] if v > 0 else data.DECREASE[mode] for v in bars["log2"]], cornerradius=4
            ),
            hovertemplate="%{y}<br>%{customdata[0]:+.2f}% (%{customdata[1]:,.0f} → %{customdata[2]:,.0f})"
            "<extra></extra>",
        )
    )
    fig = data.style(fig, height=max(240, 24 * len(bars)))
    fig.update_layout(hovermode="closest", bargap=0.3)
    fig.update_xaxes(
        title="Change in share count (×2 / ÷2 scale)",
        tickvals=ticks,
        ticktext=[f"×{2**t}" if t > 0 else ("=" if t == 0 else f"÷{2**-t}") for t in ticks],
        range=[-span - 0.1, span + 0.1],
        showgrid=True,
        gridcolor="rgba(128,128,128,0.18)",
        zeroline=True,
        zerolinecolor="rgba(128,128,128,0.6)",
        showspikes=False,
    )
    if len(changed) > len(bars):
        st.caption(f"Chart shows the 15 largest increases and decreases; the table below has all {len(changed)}.")
    st.plotly_chart(fig, width="stretch")
st.dataframe(
    changed[["company_label", "shares_a", "shares_b", "ratio", "pct", "weight_br_a", "weight_br_b"]],
    hide_index=True,
    width="stretch",
    column_config={
        "company_label": "Company",
        "shares_a": st.column_config.NumberColumn(f"Shares on {day_a:%Y-%m-%d}", format="localized"),
        "shares_b": st.column_config.NumberColumn(f"Shares on {day_b:%Y-%m-%d}", format="localized"),
        "ratio": st.column_config.NumberColumn("Ratio", format="%.2f"),
        "pct": st.column_config.NumberColumn("Change %", format="%+.2f%%"),
        "weight_br_a": st.column_config.NumberColumn("Brazil weight before", format="percent"),
        "weight_br_b": st.column_config.NumberColumn("Brazil weight after", format="percent"),
    },
)

left, right = st.columns(2)
with left:
    new = m[m["_merge"] == "left_only"].sort_values("value_usd_b", ascending=False)
    st.subheader(f"Added ({len(new)})")
    st.dataframe(
        new[["company_label", "shares_b", "value_usd_b"]],
        hide_index=True,
        width="stretch",
        column_config={
            "company_label": "Company",
            "shares_b": st.column_config.NumberColumn("Shares", format="localized"),
            "value_usd_b": st.column_config.NumberColumn("Value (US$)", format="localized"),
        },
    )
with right:
    gone = m[m["_merge"] == "right_only"].sort_values("value_usd_a", ascending=False)
    st.subheader(f"Removed ({len(gone)})")
    st.dataframe(
        gone[["company_label", "shares_a", "value_usd_a"]],
        hide_index=True,
        width="stretch",
        column_config={
            "company_label": "Company",
            "shares_a": st.column_config.NumberColumn("Shares", format="localized"),
            "value_usd_a": st.column_config.NumberColumn("Value (US$)", format="localized"),
        },
    )
