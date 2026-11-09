"""Two small Plotly charts. Kept simple so they read on a phone."""
from __future__ import annotations

import plotly.graph_objects as go

ACCENT = "#C2410C"     # lost / late
MUTED = "#94A3B8"      # everything else


def _layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=8, b=8), showlegend=True,
                      legend=dict(orientation="h", y=-0.2, x=0), barmode="overlay",
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(size=13))
    fig.update_yaxes(gridcolor="rgba(148,163,184,0.25)", zeroline=False)
    return fig


def lost_by_month(by_month) -> go.Figure:
    """Requests per month, with the lost ones on top: shows when it got worse."""
    fig = go.Figure()
    fig.add_bar(x=list(by_month["month"]), y=list(by_month["requests"]), name="edit requests", marker_color=MUTED)
    fig.add_bar(x=list(by_month["month"]), y=list(by_month["lost"]), name="lost", marker_color=ACCENT)
    return _layout(fig, 260)


def overdue_by_invoice(overdue) -> go.Figure:
    """One bar per overdue invoice: days late, labelled with the amount."""
    o = overdue.sort_values("days late")
    labels = [f"{c} · {i}" for c, i in zip(o["client"], o["invoice"])]
    fig = go.Figure(go.Bar(x=list(o["days late"]), y=labels, orientation="h", marker_color=ACCENT,
                           text=[f"{v:,.0f}" for v in o["owed"]], textposition="outside", name="days late",
                           hovertemplate="%{y}<br>%{x} days late<extra></extra>"))
    fig = _layout(fig, 60 + 34 * max(len(o), 1))
    fig.update_layout(showlegend=False)
    fig.update_xaxes(title_text="days late")
    return fig
