"""Question 6 - the owner's first three actions on Monday morning.

Candidate actions are built from the other answers, each with the money or
clients involved. They are ranked by a written rule, not by a hidden score:

  1. an explicit client deadline this week beats everything (a client who
     says "we need to talk about the contract this week")
  2. then the problem that is still creating new damage every week
     (requests being lost now), because every day of delay adds to it
  3. then money that can be recovered by acting (overdue + never billed),
     together with money we owe, so the client relationship stays clean
Leads and referrals come next; they are listed as a 4th item.

Nothing is sent automatically: every action is a suggestion for the owner.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .context import Context

QUESTION = "Q6 priorities"


@dataclass
class PriorityResult:
    actions: list          # dicts, ranked
    top3_text: list


def owner_priorities(ctx: Context, clients, fin, comm, churn, mkt) -> PriorityResult:
    L, src = ctx.ledger, ctx.src
    acts = []
    top = churn.most_likely
    monthly = round(top["revenue_at_stake_90_days"] / 3, 2)
    deadline = any("this week" in e.lower() for e in top["evidence"])
    acts.append({
        "kind": "churn",
        "rank_rule": 1 if deadline else 2,
        "what": f"Call {top['client']} today and agree a meeting this week; fix their missed edits first",
        "concerns": top["client"],
        "evidence": top["evidence"],
        "why_first": ("the client set a deadline ('this week') to decide whether to continue, nobody has replied, "
                      f"and it is the highest-risk client ({top['risk_points']} points vs {top['runner_up_points']})"),
        "benefit": f"keeps about {monthly:,.0f} a month of revenue (last 90 days invoiced {top['revenue_at_stake_90_days']:,.0f})",
        "detail": top["what_to_do_this_week"],
    })
    req = comm.requests
    lost = req[req.classification == "confirmed_lost"]
    pending = req[req.classification == "delayed_or_unresolved"]
    hurt = sorted(set(lost.client))
    vn = comm.by_channel.loc["voice note"] if "voice note" in comm.by_channel.index else None
    acts.append({
        "kind": "edits",
        "rank_rule": 2,
        "what": ("Stop losing edit requests: from today every voice note, WhatsApp, DM and email edit request is "
                 "written onto the production board the same day (one person owns it), and the "
                 f"{len(lost)} lost requests on delivered videos are re-opened"),
        "concerns": f"{len(hurt)} clients with lost requests, starting with " + ", ".join(hurt[:4]),
        "evidence": [c[1] for c in comm.root_causes] +
                    [f"lost requests rose from {comm.worse_since}: " +
                     ", ".join(f"{m} {int(r.confirmed_lost)}/{int(r.requests)}" for m, r in comm.by_month.iterrows())],
        "why_first": "it is still happening every week and it is the reason clients complain and leave "
                     "(one client already paused citing missed notes)",
        "benefit": (f"protects all {len(clients)} clients; {len(pending)} recent request(s) can still be saved before "
                    f"delivery" + (f"; voice notes alone are {int(vn.requests)} of {len(req)} requests" if vn is not None else "")),
        "detail": "Re-open list: " + "; ".join(f"{r.client} {r.video_id} {r.requested}" for r in lost.itertuples()),
    })
    s = fin.summary
    ov = fin.overdue
    acts.append({
        "kind": "money",
        "rank_rule": 3,
        "what": (f"Money: approve reminders for {len(ov)} overdue invoices ({s['overdue_total']:,.0f}), bill the "
                 f"{len(fin.never_billed)} jobs never invoiced ({s['never_billed_total']:,.0f}), correct the billing tool, "
                 f"and settle the {s['we_owe_total']:,.0f} we owe clients"),
        "concerns": ", ".join(ov.client.head(4)) + (" and others" if len(ov) > 4 else ""),
        "evidence": [f"{r.invoice_no} {r.client} {r.balance:,.0f} {r.days_late} days late" for r in ov.itertuples()] +
                    [f"never billed: {r.client} {r.booking_or_month} {r.amount:,.0f}" for r in fin.never_billed.itertuples()] +
                    [f"we owe {r.client} {r.amount:,.0f}: {r.why}" for r in fin.we_owe[fin.we_owe.status != 'unresolved'].itertuples()],
        "why_first": "cash that is already earned; the longest overdue is "
                     f"{int(ov.days_late.max()) if len(ov) else 0} days late; paying back what we owe first "
                     "avoids chasing a client who has a credit with us",
        "benefit": f"up to {s['overdue_total'] + s['never_billed_total']:,.0f} collected or billed",
        "detail": "Nothing is sent automatically: each reminder is drafted for the owner to approve.",
    })
    nr = comm.never_replied_leads
    real_nr = nr[~nr.likely_junk]
    acts.append({
        "kind": "leads",
        "rank_rule": 4,
        "what": (f"Reply to the {len(real_nr)} leads that never got an answer and call the "
                 f"{int((comm.missed_referrals.became_lead == '').sum())} dropped referral(s); follow up the "
                 f"{int((~comm.opportunities.followed_up).sum())} clients who asked about more work"),
        "concerns": ", ".join(real_nr.lead_id),
        "evidence": [f"{r.lead_id} {r.source} {r.created[:10]}" for r in real_nr.itertuples()],
        "why_first": f"best channel is {mkt.best['channel']}; referrals and existing clients are being ignored",
        "benefit": "new revenue at almost no cost",
        "detail": "",
    })
    if not top.get("client"):                       # no client to rank (e.g. only a few files loaded)
        acts = [a for a in acts if a["kind"] != "churn"]
    acts = sorted(acts, key=lambda a: a["rank_rule"])
    top3 = [f"{i}. {a['what']}. Why: {a['why_first']}. Benefit: {a['benefit']}." for i, a in enumerate(acts[:3], 1)]
    for i, a in enumerate(acts, 1):
        L.add(finding_id=L.new_id("Q6"), question=QUESTION, category=f"priority_{i}", status="inferred",
              confidence="medium", client=a["concerns"], statement=a["what"],
              calculation=f"ranked by rule {a['rank_rule']} (see owner_priorities.py); " + a["why_first"],
              sources=[("", "", e[:120], "evidence") for e in a["evidence"][:12]])
    return PriorityResult(actions=acts, top3_text=top3)
