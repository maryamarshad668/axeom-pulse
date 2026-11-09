"""Question 1 - who are our real clients?

Builds on the identities from entity_resolution and adds two business rules:

1. Payers who are not clients. An identity whose invoices reference bookings
   of *other* clients, and who has no bookings of its own (every booking under
   its email carries another client's name), pays for others: an agency.
   Its flagged bookings (and the videos shot at them) are given to the client
   they were made for. The payer keeps its invoices and payments.
2. A real client is an identity with at least one own booking, invoice or
   production video once payers are separated. Leads that never bought,
   spam and message-only contacts are not clients.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import pandas as pd

from ..normalizer import name_key
from .context import ID_COL, Context

QUESTION = "Q1 clients"


@dataclass
class ClientResult:
    clients: pd.DataFrame          # one row per real client (canonical)
    payers: pd.DataFrame           # payers who are not clients
    record_links: pd.DataFrame     # every record -> final client, with how
    uncertain: pd.DataFrame        # identity questions still open
    pays_for: dict                 # payer client_id -> set of client_ids


def _fmt_phone(p: str) -> str:
    return f"{p[:3]}-{p[3:6]}-{p[6:]}" if len(p) == 10 else p


def _payer_relations(ctx: Context) -> dict:
    """payer client_id -> set of client_ids it pays for (evidence-based)."""
    f = ctx.frames
    rel = defaultdict(set)
    if "invoices" in f and "bookings" in f:
        bclient = dict(zip(f["bookings"]["booking_id"], f["bookings"]["client_id"]))
        for _, inv in f["invoices"].iterrows():
            for b in re.findall(r"\b(B\d{3,})\b", str(inv["memo"])):
                other = bclient.get(b, "")
                if other and inv["client_id"] and other != inv["client_id"]:
                    rel[inv["client_id"]].add(other)
    # records booked under one client's email but in another client's own name
    review = ctx.model.resolution.review
    for _, r in review[review.kind == "email_name_conflict"].iterrows():
        cands = [c for c in r.candidates.split(" | ") if c.startswith("CL-")]
        if len(cands) >= 2:
            rel[cands[0]].update(cands[1:])
    return rel


def _canonical_name(sub_links: pd.DataFrame, frames: dict) -> str:
    """Prefer how the client names its business on its most recent lead form;
    fall back to the most frequent business spelling on bookings / invoices.
    A 'business' field that just repeats the person's name is ignored."""
    leads = frames.get("leads")
    if leads is not None:
        ids = sub_links[sub_links.source == "leads"].record_id
        ls = leads[leads.lead_id.isin(ids)].sort_values("created_date", ascending=False)
        good = [l["business"] for _, l in ls.iterrows()
                if name_key(l["business"]) and
                not set(name_key(l["business"]).split()) <= set(name_key(l["name"]).split())]
        if good:
            # the most recent self-description wins (a rename is newer), unless it
            # only adds a location / suffix to an older one ('Apex Med Spa - Frisco')
            best = good[0]
            for older in good[1:]:
                a, b = name_key(best).replace(" ", ""), name_key(older).replace(" ", "")
                if a.startswith(b) and a != b:
                    best = older
            return best
    person = set()
    if leads is not None:
        for n in leads[leads.lead_id.isin(sub_links[sub_links.source == "leads"].record_id)]["name"]:
            person |= set(name_key(n).split())
    counts = Counter()
    for _, r in sub_links[sub_links.source.isin(["bookings", "invoices"])].iterrows():
        n = r.raw_names
        k = name_key(n)
        if k and not set(k.split()) <= person:
            counts[n] += 1
    if counts:
        best = max(counts.items(), key=lambda t: (t[1], len(t[0])))
        return best[0]
    names = Counter(n for x in sub_links.raw_names for n in x.split(" | ") if n)
    return names.most_common(1)[0][0] if names else ""


def analyze_clients(ctx: Context) -> ClientResult:
    res = ctx.model.resolution
    links = res.links.copy()
    ident = res.clients.set_index("client_id")
    rel = _payer_relations(ctx)

    # identities with no own bookings: every booking under them is flagged for another client
    payer_ids = set()
    for payer, others in rel.items():
        own = links[(links.client_id == payer) & (links.source == "bookings")]
        if len(own) == 0 or own.needs_review.all():
            payer_ids.add(payer)

    # give flagged bookings (and their videos) to the client they were made for
    links["final_client_id"] = links["client_id"]
    links["reassigned_because"] = ""
    for payer in payer_ids:
        for idx, r in links[(links.client_id == payer) & links.needs_review &
                            (links.source == "bookings")].iterrows():
            target = [c for c in rel[payer]
                      if name_key(r.raw_names) in set(name_key(n) for n in
                                                      ident.loc[c, "names_used"].split(", "))]
            if len(target) == 1:
                links.at[idx, "final_client_id"] = target[0]
                links.at[idx, "reassigned_because"] = (
                    f"booked under payer {ident.loc[payer, 'display_name']}'s email for "
                    f"'{r.raw_names}', a client this payer pays for")
        if "production" in ctx.frames:
            prod = ctx.frames["production"]
            moved = dict(zip(links[links.source == "bookings"].record_id,
                             links[links.source == "bookings"].final_client_id))
            for idx, r in links[(links.client_id == payer) & (links.source == "production")].iterrows():
                b = prod.loc[prod.video_id == r.record_id, "shoot_booking"]
                if len(b) and moved.get(b.iloc[0], payer) != payer:
                    links.at[idx, "final_client_id"] = moved[b.iloc[0]]
                    links.at[idx, "reassigned_because"] = f"video of booking {b.iloc[0]} (reassigned)"

    # real clients
    rows = []
    for cid in sorted(set(links.final_client_id) - {""}):
        sub = links[links.final_client_id == cid]
        has_work = sub.source.isin(["bookings", "invoices", "production"]).any()
        if cid in payer_ids or not has_work:
            continue
        own_names = sorted({n for x in sub.raw_names for n in x.split(" | ") if n})
        canonical = _canonical_name(sub, ctx.frames)
        counts = sub.source.value_counts()
        rows.append({
            "client_id": cid, "canonical_name": canonical,
            "other_names": [n for n in own_names if n != canonical],
            "emails": sorted({e for e in sub[sub.reassigned_because == ""].email if e}),
            "phones": sorted({_fmt_phone(p) for p in sub.phone if p}),
            "instagram": sorted({"@" + h for h in sub.handle if h}),
            **{f"n_{s}": int(counts.get(s, 0)) for s in ID_COL},
            "lead_ids": sorted(sub[sub.source == "leads"].record_id),
            "booking_ids": sorted(sub[sub.source == "bookings"].record_id),
            "invoice_ids": sorted(sub[sub.source == "invoices"].record_id),
            "payment_ids": sorted(sub[sub.source == "payments"].record_id),
            "video_ids": sorted(sub[sub.source == "production"].record_id),
            "link_methods": dict(Counter(sub.method)),
        })
    clients = pd.DataFrame(rows, columns=["client_id", "canonical_name", "other_names", "emails", "phones",
                                          "instagram", *[f"n_{s}" for s in ID_COL], "lead_ids", "booking_ids",
                                          "invoice_ids", "payment_ids", "video_ids", "link_methods"]
                           ).sort_values("canonical_name").reset_index(drop=True)
    name_of = dict(zip(clients.client_id, clients.canonical_name))

    payers = []
    for p in sorted(payer_ids):
        payers.append({"client_id": p, "name": _canonical_name(links[links.client_id == p], ctx.frames)
                       or ident.loc[p, "display_name"],
                       "pays_for": sorted(name_of.get(c, ident.loc[c, "display_name"]) for c in rel[p]),
                       "pays_for_ids": sorted(rel[p])})
    payers = pd.DataFrame(payers, columns=["client_id", "name", "pays_for", "pays_for_ids"])

    # open identity questions that still matter after the rules above
    review = res.review.copy()
    resolved_note = []
    for _, r in review.iterrows():
        cands = [c for c in r.candidates.split(" | ") if c.startswith("CL-")]
        recs = [x for x in r.records.split(" | ") if x]
        cl = {links.loc[links.mention_id == x, "final_client_id"].iloc[0] for x in recs
              if (links.mention_id == x).any()} | set(cands)
        cl.discard("")
        involved = set(cands)
        for x in recs:
            if (links.mention_id == x).any():
                involved.add(links.loc[links.mention_id == x, "client_id"].iloc[0])
        if r.kind == "email_name_conflict" and involved & payer_ids:
            resolved_note.append("resolved: payer booking reassigned to the client it was for")
        elif r.kind == "same_name_different_contact" and involved & payer_ids:
            resolved_note.append("resolved: one side is a payer (agency), the other its contact or customer")
        elif r.kind in ("phone_conflict", "same_name_different_contact") and len(cl) == 1:
            resolved_note.append("resolved: joined by later evidence (announced email change)")
        else:
            resolved_note.append("open")
    review["status_after_q1"] = resolved_note

    # evidence ledger
    L, src = ctx.ledger, ctx.src
    fid = L.new_id("Q1")
    L.add(finding_id=fid, question=QUESTION, category="client_count", status="inferred", confidence="high",
          amount=len(clients),
          statement=f"{len(clients)} real clients (identities with own bookings, invoices or videos; "
                    f"payers for others excluded)",
          calculation="count of resolved identities with >=1 own booking/invoice/video, minus payer-only identities",
          sources=[("client_identity_report.csv", "", c, "client") for c in clients.client_id])
    for _, c in clients.iterrows():
        sub = links[links.final_client_id == c.client_id]
        f2 = L.new_id("Q1")
        decisions = "; ".join(sorted({f"{m}" for m in sub.method}))
        L.add(finding_id=f2, question=QUESTION, category="client_profile", status="inferred",
              confidence="high" if set(sub.method) <= {"email", "booking_reference", "phone",
                                                         "announced_alias", "exact_name"} else "medium",
              client=c.canonical_name,
              statement=f"{c.canonical_name}: {len(sub)} records under {len(c.other_names) + 1} names",
              calculation="records joined by: " + decisions, matching_decision=decisions,
              sources=[(r.source_file, r.source_line, r.record_id, r.source) for _, r in sub.iterrows()])
    for _, p in payers.iterrows():
        f3 = L.new_id("Q1")
        sub = links[links.client_id == p.client_id]
        L.add(finding_id=f3, question=QUESTION, category="payer_not_client", status="inferred", confidence="high",
              client=p["name"],
              statement=f"{p['name']} pays for {', '.join(p.pays_for)} and has no bookings of its own",
              calculation="invoice memos of this payer cite bookings of other clients; its own bookings carry "
                          "the other clients' names",
              sources=[(r.source_file, r.source_line, r.record_id, r.source) for _, r in sub.iterrows()])
    for _, r in review[review.status_after_q1 == "open"].iterrows():
        L.add(finding_id=L.new_id("Q1"), question=QUESTION, category="uncertain_identity", status="unresolved",
              confidence="", statement=r.detail, matching_decision="not merged; needs owner decision",
              sources=[("", "", r.review_id, "review item")])

    record_links = links.rename(columns={"client_id": "identity_id", "final_client_id": "client_id"})
    record_links["client"] = record_links.client_id.map(name_of).fillna(
        record_links.client_id.map(dict(zip(payers.client_id, payers["name"]))).fillna(""))
    return ClientResult(clients=clients, payers=payers, record_links=record_links,
                        uncertain=review, pays_for={p: rel[p] for p in payer_ids})
