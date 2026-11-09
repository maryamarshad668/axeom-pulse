"""Question 5 - which marketing channel brings the most profitable clients?

What the data allows
--------------------
* leads.csv gives each lead's source. A client is attributed to the source
  of its EARLIEST lead (first touch). Duplicate leads of the same identity
  count once; spam/test leads (no phone, no business) are excluded.
* Revenue per client: invoiced (not void) and collected (bank payments
  matched to its invoices in the reconciliation). A payer's invoices count
  for the payer's own lead source.
* Delivery cost per client CAN be estimated: studio hours per service
  (costs.csv) x studio rate, plus editor_hours on its videos x editor rate
  (team.csv). It is an estimate (shared studio, hours include revisions).
* Marketing spend per channel is NOT in the data (no ad spend, no referral
  fees, no time spent on Instagram). So true channel profitability cannot
  be calculated. The comparison below is revenue and an estimated gross
  margin after delivery cost, NOT profit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from ..normalizer import parse_rate
from .context import Context

QUESTION = "Q5 marketing"


@dataclass
class MarketingResult:
    channels: pd.DataFrame
    client_value: pd.DataFrame
    best: dict
    limitations: list


def _studio_hours(ctx: Context):
    costs = ctx.frames.get("costs")
    if costs is None:
        return None, {}
    studio = costs[costs.item.str.lower().str.contains("studio")]
    if not len(studio):
        return None, {}
    rate, unit, st = parse_rate(studio.cost.iloc[0])
    note = studio.note.iloc[0]
    rule = {m.group(1).strip().lower(): float(m.group(2))
            for m in re.finditer(r"([A-Za-z ]+?)\s+(?:uses\s+)?(\d+)(?:\s*hours)?(?:,|$)", note)}
    return (rate if st == "ok" else None), rule


def analyze_marketing(ctx: Context, clients: pd.DataFrame, payers: pd.DataFrame, record_links: pd.DataFrame,
                      fin) -> MarketingResult:
    f = ctx.frames
    leads = f["leads"].copy()
    leads["junk"] = (leads.phone_norm == "") & (leads.business.str.strip() == "")
    real = leads[~leads.junk].sort_values("created_date")
    first = real.groupby("client_id").head(1)                      # first touch per identity
    names = dict(zip(clients.client_id, clients.canonical_name))
    payer_ids = set(payers.client_id)

    rate, hours_rule = _studio_hours(ctx)
    team = f.get("team")
    ed_rate = dict(zip(team.name, team.pay_rate)) if team is not None else {}
    bk = f["bookings"]
    prod = f["production"]
    rl = record_links

    # final client of each booking / video (after payer reassignment)
    bk_client = dict(zip(rl[rl.source == "bookings"].record_id, rl[rl.source == "bookings"].client_id))
    vid_client = dict(zip(rl[rl.source == "production"].record_id, rl[rl.source == "production"].client_id))
    inv = fin.invoices[fin.invoices.true_status != "void"]
    alloc = fin.allocations.merge(fin.invoices[["invoice_no", "client_id"]], on="invoice_no")

    rows = []
    for _, l in first.iterrows():
        cid = l.client_id
        is_client = cid in names
        is_payer = cid in payer_ids
        done = bk[(bk.booking_id.map(bk_client) == cid) & (bk.status_norm == "completed") &
                  (bk.date_date <= ctx.today)]
        studio_h = 0.0
        for s in done.service:
            m = re.search(r"(\d+)\s*hr", s.lower())
            studio_h += float(m.group(1)) if m else hours_rule.get(s.lower(), 0.0)
        vids = prod[prod.video_id.map(vid_client) == cid]
        editor_cost = float((vids.editor_hours_value * vids.editor.map(ed_rate)).sum())
        invoiced = float(inv[inv.client_id == cid].amount.sum())
        collected = float(alloc[alloc.client_id == cid].amount.sum())
        studio_cost = studio_h * rate if rate else None
        cost = (studio_cost or 0.0) + editor_cost
        rows.append({"client_id": cid, "client": names.get(cid, l.business or l.name),
                     "is_client": is_client, "is_payer_only": is_payer, "first_lead": l.lead_id,
                     "source": l.source, "lead_status": l.status, "lead_created": l.created,
                     "invoiced": round(invoiced, 2), "collected": round(collected, 2),
                     "studio_hours": studio_h, "est_studio_cost": round(studio_cost, 2) if studio_cost is not None else None,
                     "editor_hours": round(float(vids.editor_hours_value.sum()), 1),
                     "est_editor_cost": round(editor_cost, 2),
                     "est_delivery_cost": round(cost, 2),
                     "est_margin_after_delivery": round(invoiced - cost, 2)})
    cv = pd.DataFrame(rows, columns=["client_id", "client", "is_client", "is_payer_only", "first_lead", "source",
                                     "lead_status", "lead_created", "invoiced", "collected", "studio_hours",
                                     "est_studio_cost", "editor_hours", "est_editor_cost", "est_delivery_cost",
                                     "est_margin_after_delivery"])
    # clients reached by an agency count for the agency's channel only through the agency's invoices;
    # their own delivery cost is added to the payer so revenue and cost stay together
    for p in payers.itertuples():
        served = cv[cv.client_id.isin(p.pays_for_ids)]
        if len(served):
            idx = cv.index[cv.client_id == p.client_id]
            if len(idx):
                i = idx[0]
                for col in ("studio_hours", "est_studio_cost", "editor_hours", "est_editor_cost", "est_delivery_cost"):
                    cv.at[i, col] = round((cv.at[i, col] or 0) + served[col].fillna(0).sum(), 2)
                cv.at[i, "est_margin_after_delivery"] = round(cv.at[i, "invoiced"] - cv.at[i, "est_delivery_cost"], 2)
                cv.loc[cv.client_id.isin(p.pays_for_ids), ["est_studio_cost", "est_editor_cost", "est_delivery_cost",
                                                            "est_margin_after_delivery"]] = 0.0
                cv.loc[cv.client_id.isin(p.pays_for_ids), "note"] = f"billed via {p.name}; value counted there"
    if "note" not in cv.columns:
        cv["note"] = ""
    cv["note"] = cv["note"].fillna("")

    # clients without any lead record (channel unknown)
    unattributed = [c for c in names if c not in set(cv.client_id)]

    if not len(cv):                                 # no leads (or no client linked to a lead)
        cols = ["channel", "leads", "unique_prospects", "converted_clients", "paying_accounts", "invoiced",
                "collected", "est_delivery_cost", "est_margin_after_delivery", "conversion_rate",
                "invoiced_per_converted_client", "est_margin_per_converted_client", "est_margin_per_prospect",
                "marketing_spend", "true_profit"]
        why = "cannot be answered: no lead records with a source were loaded"
        return MarketingResult(channels=pd.DataFrame(columns=cols), client_value=cv,
                               best={"channel": "", "why": why, "best_per_prospect": ""},
                               limitations=[why.capitalize() + "."])
    g = cv.groupby("source")
    ch = pd.DataFrame({
        "leads": real.groupby("source").size(),
        "unique_prospects": g.size(),
        "converted_clients": g.apply(lambda d: int(d.is_client.sum())),
        "paying_accounts": g.apply(lambda d: int(((d.is_client) | (d.is_payer_only)).sum())),
        "invoiced": g.invoiced.sum(), "collected": g.collected.sum(),
        "est_delivery_cost": g.est_delivery_cost.sum(), "est_margin_after_delivery": g.est_margin_after_delivery.sum(),
    }).fillna(0)
    ch["conversion_rate"] = (ch.converted_clients / ch.unique_prospects).round(3)
    ch["invoiced_per_converted_client"] = (ch.invoiced / ch.converted_clients.astype(float).replace(0.0, float("nan"))).round(2)
    ch["est_margin_per_converted_client"] = (ch.est_margin_after_delivery / ch.converted_clients.astype(float).replace(0.0, float("nan"))).round(2)
    ch["est_margin_per_prospect"] = (ch.est_margin_after_delivery / ch.unique_prospects).round(2)
    ch["marketing_spend"] = "not in data"
    ch["true_profit"] = "cannot be calculated"
    ch = ch.sort_values("est_margin_after_delivery", ascending=False)
    ch.index.name = "channel"

    best_total = ch.index[0]
    best_rate = ch.sort_values("est_margin_per_prospect", ascending=False).index[0]
    b = ch.loc[best_total]
    why = (f"{best_total} brought {int(b.converted_clients)} clients from {int(b.unique_prospects)} prospects "
           f"({b.conversion_rate:.0%}), {b.invoiced:,.0f} invoiced and {b.collected:,.0f} collected; estimated "
           f"margin after studio and editor time {b.est_margin_after_delivery:,.0f}, the highest of all channels")
    if best_rate == best_total:
        why += f", and also the most per prospect ({b.est_margin_per_prospect:,.0f})"
    why += (". Ad spend and referral costs are not in the data, so this is the best channel by margin after "
            "delivery cost, not proven profit")
    best = {"channel": best_total, "why": why, "best_per_prospect": best_rate}
    limitations = [
        "No marketing spend per channel (ad budgets, referral rewards, time on Instagram), so channel profit cannot be calculated.",
        "Delivery cost is an estimate: studio hours per service from costs.csv and editor hours x hourly rate; owner time, equipment and overheads are not in the data.",
        "Each client is credited to the source of its first lead only; later touches (messages, referrals between clients) are ignored.",
        "About six months of data (Mar-Oct 2026): too short to compare client lifetime value between channels.",
        "Lead 'status' is typed by hand and does not always match what happened (e.g. 'Won' leads of the same client twice).",
    ]
    if unattributed:
        limitations.append(f"{len(unattributed)} client(s) have no lead record, so their channel is unknown: "
                           + ", ".join(names[c] for c in unattributed))

    L, src = ctx.ledger, ctx.src
    for c, r in ch.iterrows():
        members = cv[cv.source == c]
        L.add(finding_id=L.new_id("Q5"), question=QUESTION, category="channel", status="inferred",
              confidence="medium", client=c, amount=r.est_margin_after_delivery,
              statement=f"{c}: {int(r.unique_prospects)} prospects, {int(r.converted_clients)} clients, invoiced "
                        f"{r.invoiced:.2f}, collected {r.collected:.2f}, est. margin {r.est_margin_after_delivery:.2f}",
              calculation="first-touch attribution; invoiced excl. void; collected = payments matched to invoices; "
                          "margin = invoiced - (studio hours x rate + editor hours x rate)",
              sources=[src("leads", x, "first lead") for x in members.first_lead])
    L.add(finding_id=L.new_id("Q5"), question=QUESTION, category="best_channel", status="inferred",
          confidence="medium", client=best_total, statement=why,
          calculation="highest estimated margin after delivery cost; spend unknown",
          sources=[src("leads", x, "first lead") for x in cv[cv.source == best_total].first_lead])
    return MarketingResult(channels=ch.reset_index(), client_value=cv, best=best, limitations=limitations)
