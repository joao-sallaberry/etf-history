import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from etf_history.dashboard import data

sel = data.selection()
funds = data.funds()
colors = data.color_map(funds)  # one slot per fund, same color on every chart of the page

st.title(f"{' vs '.join(funds)} · Brazil")
if len(funds) < 2:
    st.info("Only one fund in the database.")
    st.stop()

# Each fund on its own days: its last snapshot up to the period end (and up to the start, for changes).
periods = {f: data.snap_period(data.fund_days(f), sel.start, sel.end) for f in funds}
missing = [f for f, p in periods.items() if p is None]
if missing:
    st.info(f"No snapshot of {', '.join(missing)} up to {sel.end:%Y-%m-%d}.")
    st.stop()
st.caption(
    "Each fund is read on its own last snapshot up to the period end: "
    + ", ".join(f"{f} on {p[1]:%Y-%m-%d}" for f, p in periods.items())
    + ". Companies group every class, unit and ADR of the same B3 issuer."
)

companies = {f: data.companies(f) for f in funds}
last = {f: df[df["as_of"].dt.date == periods[f][1]].set_index("company") for f, df in companies.items()}
totals = {f: data.fund_totals(f).set_index("as_of") for f in funds}
held = {f: set(last[f].index) for f in funds}
both = set.intersection(*held.values())

every = "both" if len(funds) == 2 else "all"
cols = st.columns(4)
cols[0].metric(f"Brazil weight · {' / '.join(funds)}", " / ".join(f"{last[f]['weight'].sum():.2%}" for f in funds))
cols[1].metric(f"Brazilian companies · {' / '.join(funds)}", " / ".join(str(len(held[f])) for f in funds))
cols[2].metric(f"Held by {every}", len(both))
cols[2].caption(" · ".join(f"{len(held[f] - both)} only in {f}" for f in funds))
overlap = sum(min(last[f]["weight_br"].get(c, 0) for f in funds) for c in both)
cols[3].metric(
    "Overlap by weight",
    f"{overlap:.0%}",
    help="Σ over companies of the smallest weight within Brazil among the funds: 100% = identical Brazilian books.",
)

st.subheader("Brazil weight in each fund")
fig = go.Figure()
shown = []
for f in funds:
    t = totals[f][(totals[f].index.date >= sel.start) & (totals[f].index.date <= sel.end)]
    shown.append(t)
    fig.add_scatter(
        x=t.index,
        y=t["br_weight"],
        name=f,
        mode="lines+markers",
        line=dict(width=2, color=colors[f]),
        marker=data.source_markers(t["source"], colors[f]),
        hovertemplate="%{y:.2%}",
    )
st.plotly_chart(data.style(fig, height=320, y_format=".1%"), width="stretch")
data.sources_note(pd.concat(shown))

st.subheader("Weight within Brazil by company")
st.caption("Each company's share of the fund's Brazilian holdings (each fund's Brazilian book adds up to 100%).")
labels = {}
for f in funds:  # the first fund's naming wins (Avantis names carry "COMMON STOCK" suffixes)
    for company, label in last[f]["company_label"].items():
        labels.setdefault(company, label)
table = pd.DataFrame({"company_label": pd.Series(labels)})
for f in funds:
    table[f] = last[f]["weight_br"].reindex(table.index) * 100
groups = {"All": table.index, f"In {every} ({len(both)})": list(both)}
groups |= {f"Only {f} ({len(held[f] - both)})": list(held[f] - both) for f in funds}
group = st.pills("Show", list(groups), default="All", key="compare_group") or "All"
table = table.loc[list(groups[group])]
if len(funds) == 2:
    a, b = funds
    table["diff"] = table[b].fillna(0) - table[a].fillna(0)
table = table.sort_values(funds, ascending=False, na_position="last")
top = float(table[funds].max().max() or 1)
st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={
        "company_label": "Company",
        **{
            f: st.column_config.ProgressColumn(f"{f} % of Brazil", format="%.2f", min_value=0, max_value=top)
            for f in funds
        },
        "diff": st.column_config.NumberColumn(f"{funds[-1]} − {funds[0]} (pp)", format="%+.2f"),
    },
)

# Who bought / sold: each fund between its own snapshots nearest the period start and end.
st.subheader("Who bought, who sold")
changes = {}
for f in funds:
    day_a, day_b = periods[f]
    df = companies[f].set_index("company")
    a = df[df["as_of"].dt.date == day_a]["shares_eq"]
    b = df[df["as_of"].dt.date == day_b]["shares_eq"]
    changes[f] = (day_a, day_b, a, b)
    if day_a == day_b:
        st.caption(f"{f}: a single snapshot ({day_a:%Y-%m-%d}) up to the period end; no change to show.")


def describe(a: pd.Series, b: pd.Series, company: str) -> tuple[str, int]:
    """Text of a change and its direction (+1 bought, -1 sold, 0 unchanged/not held)."""
    before, after = a.get(company), b.get(company)
    if before is None and after is None:
        return "—", 0
    if before is None:
        return "new", 1
    if after is None:
        return "exited", -1
    if after == before:
        return "=", 0
    pct = (after / before - 1) * 100 if before else float("inf")
    return f"{pct:+.1f}%", 1 if after > before else -1


rows = []
for company in sorted(set().union(*(set(c[2].index) | set(c[3].index) for c in changes.values()))):
    row, directions = {"company_label": labels.get(company, company)}, []
    for f, (day_a, day_b, a, b) in changes.items():
        text, direction = describe(a, b, company) if day_a != day_b else ("—", 0)
        row[f] = text
        directions.append(direction)
    if not any(directions):
        continue
    if all(d > 0 for d in directions):
        row["agreement"] = f"{every} bought"
    elif all(d < 0 for d in directions):
        row["agreement"] = f"{every} sold"
    elif any(d > 0 for d in directions) and any(d < 0 for d in directions):
        row["agreement"] = "opposite"
    else:
        row["agreement"] = "one fund only"
    rows.append(row)
st.caption(
    "Change in local-share equivalents. Splits, reverse splits and mergers show up too. "
    "Uses each fund's own snapshots nearest the period start and end."
)
if rows:
    st.dataframe(
        pd.DataFrame(rows).sort_values(["agreement", "company_label"]),
        hide_index=True,
        width="stretch",
        column_config={
            "company_label": "Company",
            **{f: f"{f} ({c[0]:%Y-%m-%d} → {c[1]:%Y-%m-%d})" for f, c in changes.items()},
            "agreement": "Agreement",
        },
    )
else:
    st.info("No changes in the period.")
