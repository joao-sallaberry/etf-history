import plotly.graph_objects as go
import streamlit as st

from etf_history.dashboard import data

sel = data.selection()

st.title("Composition of the Brazilian holdings")
df = sel.filter(data.companies(sel.fund))
measure = st.radio("Measure", ["Weight within Brazil", "Weight in the fund"], horizontal=True)
ycol = "weight_br" if measure == "Weight within Brazil" else "weight"

# Top 7 by weight on the last day keep their own color; the rest fold into "Other" (8 slots max).
last = df[df["as_of"] == df["as_of"].max()].sort_values(ycol, ascending=False)
top = last["company_label"].head(data.MAX_SERIES - 1).tolist()
df = df.assign(group=df["company_label"].where(df["company_label"].isin(top), "Other"))
area = df.groupby(["as_of", "group"], as_index=False)[ycol].sum()

colors = data.color_map(top)
colors["Other"] = data.OTHER_GRAY[data.mode()]
fig = go.Figure()
for name in [*top, "Other"]:
    s = area[area["group"] == name]
    fig.add_scatter(
        x=s["as_of"],
        y=s[ycol],
        name=name,
        mode="lines",
        stackgroup="one",
        line=dict(width=0.5, color=colors[name]),
        fillcolor=colors[name],
        hovertemplate="%{y:.2%}",
    )
st.plotly_chart(data.style(fig, height=500, y_format=".0%"), width="stretch")
st.caption("The 7 largest on the last day have their own color; the rest are summed into Other.")
