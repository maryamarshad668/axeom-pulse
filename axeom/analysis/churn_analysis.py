"""Question 4 - which client is most likely to leave?

This is NOT a statistical model. It is a transparent checklist: each signal
is a fact found in the data, with a fixed number of points chosen by judgement
and written down below. The total only ranks clients for a human to look at;
every point can be traced to a record.

Signal (points)                                              source
  said they may stop / are reviewing the contract (6)        messages
  complained again about an unfixed edit (2 each, max 6)     messages
  edit request confirmed lost (1 each, max 4)                Q3 analysis
  wrote to us and never got a reply (3 each)                 leads.first_reply blank
  invoice overdue (1, +1 if over 30 days)                    Q2 reconciliation
  cancelled a booking in the last 60 days (1)                bookings
  no booking in the last 45 days and none upcoming (2)       bookings
Counter-evidence (shown, not subtracted): praise messages, upcoming bookings.

A client who said they would pause and has had no booking since is reported
as ALREADY LOST, not as "likely to leave".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from .context import Context

QUESTION = "Q4 churn"
LEAVE_INTENT = re.compile(r"whether we continue|talk about (?:our|the) contract|we'?ll pause|pause after|"
                          r"cancel (?:our|the) (?:contract|retainer)|stop working with|look(?:ing)? (?:at|for) "
                          r"(?:other|another) (?:studio|agency)", re.I)
COMPLAINT = re.compile(r"still not fixed|it'?s not fixed|second time i ask|already told you|"
                       r"still has the same problem|frustrated|did anyone see my message|not happy|i asked for", re.I)
PRAISE = re.compile(r"great|thank|approved|went up|best content|new patients|new cases|loved", re.I)
WEIGHTS = {"leave_intent": 6, "complaint": 2, "lost_request": 1, "no_reply": 3, "overdue": 1, "overdue_30": 1,
           "cancellation": 1, "inactive": 2}
CAPS = {"complaint": 6, "lost_request": 4}


@dataclass
class ChurnResult:
    scores: pd.DataFrame        # one row per client, ranked
    signals: pd.DataFrame       # one row per signal with its record
    most_likely: dict
    already_lost: pd.DataFrame


def analyze_churn(ctx: Context, clients: pd.DataFrame, comm, fin) -> ChurnResult:
    f = ctx.frames
    msgs = f["messages"]
    bk = f["bookings"]
    today = ctx.today
    names = dict(zip(clients.client_id, clients.canonical_name))
    L, src = ctx.ledger, ctx.src
    sig = []

    def add(cid, kind, points, text, source, rid, when=""):
        sig.append({"client_id": cid, "client": names.get(cid, cid), "signal": kind, "points": points,
                    "evidence": text, "source": source, "record_id": rid, "date": when})

    inbound = msgs[msgs.direction == "inbound"]
    for m in inbound.itertuples():
        if m.client_id not in names:
            continue
        if LEAVE_INTENT.search(m.text):
            add(m.client_id, "leave_intent", WEIGHTS["leave_intent"], f"{m.timestamp}: \"{m.text}\"", "messages",
                m.message_key, m.timestamp)
        if COMPLAINT.search(m.text):
            add(m.client_id, "complaint", WEIGHTS["complaint"], f"{m.timestamp}: \"{m.text}\"", "messages",
                m.message_key, m.timestamp)
        if PRAISE.search(m.text) and not COMPLAINT.search(m.text):
            add(m.client_id, "praise", 0, f"{m.timestamp}: \"{m.text}\"", "messages", m.message_key, m.timestamp)
    for r in comm.requests[comm.requests.classification == "confirmed_lost"].itertuples():
        if r.client_id in names:
            add(r.client_id, "lost_request", WEIGHTS["lost_request"],
                f"{r.timestamp}: '{r.requested}' on {r.video_id} never reached the board", "messages",
                r.message_key, r.timestamp)
    for r in comm.never_replied_leads[~comm.never_replied_leads.likely_junk].itertuples():
        if r.client_id in names:
            add(r.client_id, "no_reply", WEIGHTS["no_reply"],
                f"{r.created}: wrote via {r.source} ({r.lead_id}) and never got a reply", "leads", r.lead_id, r.created)
    for r in fin.overdue.itertuples():
        if r.client_id in names:
            add(r.client_id, "overdue", WEIGHTS["overdue"], f"{r.invoice_no}: {r.balance:.2f} {r.days_late} days late",
                "invoices", r.invoice_no, str(r.due))
            if r.days_late > 30:
                add(r.client_id, "overdue_30", WEIGHTS["overdue_30"], f"{r.invoice_no} more than 30 days late",
                    "invoices", r.invoice_no, str(r.due))
    for b in bk[(bk.status_norm == "cancelled") & (bk.date_date >= today - pd.Timedelta(days=60))].itertuples():
        if b.client_id in names:
            add(b.client_id, "cancellation", WEIGHTS["cancellation"], f"{b.booking_id} on {b.date_date.date()} cancelled",
                "bookings", b.booking_id, str(b.date_date.date()))

    # activity
    act = []
    for cid in names:
        mine = bk[bk.client_id == cid]
        done = mine[(mine.status_norm == "completed") & (mine.date_date <= today)]
        upcoming = mine[(mine.status_norm == "upcoming") & (mine.date_date > today)]
        last = done.date_date.max()
        act.append({"client_id": cid, "last_booking": last.date() if pd.notna(last) else None,
                    "days_since_last_booking": int((today - last).days) if pd.notna(last) else None,
                    "upcoming_bookings": ", ".join(upcoming.booking_id)})
        if pd.notna(last) and (today - last).days > 45 and not len(upcoming):
            add(cid, "inactive", WEIGHTS["inactive"], f"last completed booking {last.date()} "
                f"({(today - last).days} days ago), nothing upcoming", "bookings", done.booking_id.iloc[-1],
                str(last.date()))
    act = pd.DataFrame(act, columns=["client_id", "last_booking", "days_since_last_booking",
                                     "upcoming_bookings"]).set_index("client_id")
    signals = pd.DataFrame(sig, columns=["client_id", "client", "signal", "points", "evidence", "source",
                                         "record_id", "date"])

    # capped totals
    rows = []
    inv = fin.invoices
    for cid, name in names.items():
        s = signals[signals.client_id == cid]
        pts = 0
        for kind, grp in s.groupby("signal"):
            pts += min(grp.points.sum(), CAPS.get(kind, 10 ** 6))
        intent = s[s.signal == "leave_intent"]
        a = act.loc[cid]
        lost = bool(len(intent)) and (a.last_booking is None or
                                      pd.Timestamp(a.last_booking) < pd.Timestamp(intent.date.max()[:10])) \
            and not a.upcoming_bookings
        recent = inv[(inv.client_id == cid) & (inv.true_status != "void")]
        last3 = recent[pd.to_datetime(recent.issued) >= today - pd.Timedelta(days=90)].amount.sum()
        rows.append({"client_id": cid, "client": name, "risk_points": int(pts), "already_lost": lost,
                     "signals": "; ".join(f"{k} x{len(g)}" for k, g in s[s.points > 0].groupby("signal")),
                     "praise_messages": int((s.signal == "praise").sum()),
                     "last_booking": a.last_booking, "days_since_last_booking": a.days_since_last_booking,
                     "upcoming_bookings": a.upcoming_bookings, "invoiced_last_90_days": round(last3, 2)})
    scores = pd.DataFrame(rows, columns=["client_id", "client", "risk_points", "already_lost", "signals",
                                         "praise_messages", "last_booking", "days_since_last_booking",
                                         "upcoming_bookings", "invoiced_last_90_days"]).sort_values(["already_lost", "risk_points", "invoiced_last_90_days"],
                                           ascending=[True, False, False]).reset_index(drop=True)
    active = scores[~scores.already_lost.astype(bool)]
    if not len(active):                             # no clients (or all already lost): nothing to rank
        most = {"client_id": "", "client": "", "risk_points": 0, "runner_up": "", "runner_up_points": 0,
                "evidence": [], "what_to_do_this_week": "", "revenue_at_stake_90_days": 0.0,
                "missing": "no active client with bookings, invoices or production work in the loaded files"}
        return ChurnResult(scores=scores, signals=signals, most_likely=most,
                           already_lost=scores[scores.already_lost.astype(bool)])
    top = active.iloc[0]
    tsig = signals[(signals.client_id == top.client_id) & (signals.points > 0)].sort_values("date")
    evidence = [r.evidence for r in tsig.itertuples()]
    actions = _actions(top, tsig, comm, fin)
    most = {"client_id": top.client_id, "client": top.client, "risk_points": int(top.risk_points),
            "runner_up": active.iloc[1].client if len(active) > 1 else "",
            "runner_up_points": int(active.iloc[1].risk_points) if len(active) > 1 else 0,
            "evidence": evidence, "what_to_do_this_week": actions,
            "revenue_at_stake_90_days": float(top.invoiced_last_90_days)}

    srcs = [src(r.source, r.record_id, r.signal) for r in tsig.itertuples()]
    L.add(finding_id=L.new_id("Q4"), question=QUESTION, category="most_likely_to_leave", status="inferred",
          confidence="medium", client=top.client, amount=top.risk_points,
          statement=f"{top.client} is most likely to leave ({top.risk_points} points; next: {most['runner_up']} "
                    f"{most['runner_up_points']})",
          calculation="sum of signal points with caps (see churn_analysis.py docstring); not a statistical model",
          sources=srcs)
    lost_df = scores[scores.already_lost]
    for r in lost_df.itertuples():
        s = signals[(signals.client_id == r.client_id) & (signals.signal == "leave_intent")]
        L.add(finding_id=L.new_id("Q4"), question=QUESTION, category="already_lost", status="inferred",
              confidence="high", client=r.client,
              statement=f"{r.client} said it would pause and has had no booking since {r.last_booking}",
              calculation="leave-intent message dated after the last completed booking, nothing upcoming",
              sources=[src(x.source, x.record_id, "leave intent") for x in s.itertuples()])
    return ChurnResult(scores=scores, signals=signals, most_likely=most, already_lost=lost_df)


def _actions(top, tsig, comm, fin) -> str:
    """Concrete steps built from this client's own open items."""
    steps = []
    nr = comm.never_replied_leads
    mine_nr = nr[(nr.client_id == top.client_id) & ~nr.likely_junk]
    intent = tsig[tsig.signal == "leave_intent"]
    if len(intent) or len(mine_nr):
        who = ", ".join(sorted(set(mine_nr.name))) or "the client"
        since = str(intent.date.min() if len(intent) else mine_nr.created.min())[:10]
        topic = "about the contract" if len(intent) else "and answer the enquiry"
        steps.append(f"Today: call {who} back {topic} (their messages since "
                     f"{since} are unanswered) and book a meeting this week")
    lost = comm.requests[(comm.requests.client_id == top.client_id) &
                         comm.requests.classification.isin(["confirmed_lost", "delayed_or_unresolved"])]
    if len(lost):
        items = "; ".join(f"{r.video_id} {r.requested}" for r in lost.itertuples())
        steps.append(f"Before the meeting, fix and re-deliver their {len(lost)} missed edits ({items})")
    ov = fin.overdue[fin.overdue.client_id == top.client_id]
    if len(ov):
        steps.append("Do not chase " + ", ".join(ov.invoice_no) + " before the call; raise it calmly in the meeting")
    steps.append("Offer one written channel for edit notes, with the owner confirming each note within a day")
    return ". ".join(steps) + "."
