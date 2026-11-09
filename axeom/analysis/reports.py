"""Write the Phase 2 outputs: CSV reports, answers.json and analysis_report.md."""
from __future__ import annotations

import copy
import json
import re
import os

import pandas as pd

from ..config import REFERENCE_DATE
from . import Analysis


def _lst(x):
    return "; ".join(map(str, x)) if isinstance(x, (list, tuple, set)) else x


# ---------------------------------------------------------------------------
# CSV files
# ---------------------------------------------------------------------------
def client_identity_report(a: Analysis) -> pd.DataFrame:
    c = a.clients.clients.copy()
    c.insert(0, "type", "client")
    for col in ["other_names", "emails", "phones", "instagram", "lead_ids", "booking_ids", "invoice_ids",
                "payment_ids", "video_ids"]:
        c[col] = c[col].map(_lst)
    c["link_methods"] = c["link_methods"].map(lambda d: "; ".join(f"{k}={v}" for k, v in d.items()))
    p = a.clients.payers.copy()
    p = pd.DataFrame({"type": "payer_not_client", "client_id": p.client_id, "canonical_name": p["name"],
                      "other_names": p.pays_for.map(lambda x: "pays for: " + ", ".join(x))})
    u = a.clients.uncertain
    u = pd.DataFrame({"type": "identity_question_" + u.status_after_q1.astype(str).str.split(":").str[0],
                      "client_id": u.review_id, "canonical_name": u.kind, "other_names": u.candidates,
                      "emails": u.detail})
    return pd.concat([c, p, u], ignore_index=True)


def financial_reconciliation(a: Analysis) -> pd.DataFrame:
    f = a.finance
    rows = []
    for r in f.invoices.itertuples():
        section = ("overdue" if r.balance > 0 and r.days_late > 0 else
                   "receivable_not_yet_due" if r.balance > 0 else "invoice_settled_or_void")
        rows.append({"section": section, "invoice_no": r.invoice_no, "payment_ids": r.payments, "booking_id": "",
                     "client": r.client, "amount": r.amount, "received": r.received, "balance": r.balance,
                     "days_late": r.days_late, "tool_status": r.tool_status, "true_status": r.true_status,
                     "status": "confirmed" if r.allocation_evidence in ("confirmed", "none") else "inferred",
                     "confidence": "", "method": r.allocation_evidence,
                     "explanation": f"issued {r.issued}, due {r.due}; memo '{r.memo}'"})
    for r in f.allocations.itertuples():
        rows.append({"section": "payment_allocation", "invoice_no": r.invoice_no, "payment_ids": r.payment_id,
                     "amount": r.amount, "status": r.status, "confidence": r.confidence, "method": r.method,
                     "explanation": r.evidence})
    for r in f.payments[f.payments.unallocated > 0.005].itertuples():
        rows.append({"section": "unallocated_payment", "payment_ids": r.payment_id, "client": r.client,
                     "amount": r.unallocated, "status": "unresolved", "method": "",
                     "explanation": f"payment {r.amount_value:.2f} ref '{r.reference}': {r.unallocated:.2f} not "
                                    f"matched to any open invoice"})
    for _, r in f.we_owe.iterrows():
        ids = [s[2] for s in r["_sources"]]
        rows.append({"section": "we_owe", "client": r.client, "amount": r.amount, "status": r.status,
                     "confidence": r.confidence, "explanation": r.why,
                     "invoice_no": ", ".join(i for i in ids if i.startswith("INV")),
                     "payment_ids": ", ".join(i for i in ids if i.startswith("P"))})
    for r in f.never_billed.itertuples():
        rows.append({"section": "never_billed", "booking_id": r.booking_id, "client": r.client, "amount": r.amount,
                     "status": r.status, "confidence": r.confidence, "method": r.booking_or_month,
                     "explanation": r.why})
    for r in f.tool_errors.itertuples():
        rows.append({"section": "billing_tool_" + r.kind, "invoice_no": r.invoice_no, "client": r.client,
                     "amount": r.amount, "tool_status": r.tool_says, "true_status": r.truth, "status": r.status,
                     "confidence": r.confidence, "explanation": r.how_we_know})
    for r in f.payables.itertuples():
        rows.append({"section": "cost_incurred_estimate", "client": "", "amount": r.estimated_cost,
                     "status": "estimate (not a recorded obligation)", "method": r.basis, "explanation": r.note,
                     "invoice_no": r.item})
    for k, v in f.summary.items():
        rows.append({"section": "summary", "invoice_no": k, "amount": v})
    return pd.DataFrame(rows)


def lost_requests_report(a: Analysis) -> pd.DataFrame:
    cols = ["timestamp", "came_by", "client", "requested", "video_id", "classification", "confidence", "why",
            "board_note", "stage", "editor", "approved_on", "shoot_booking", "video_match", "candidates",
            "followups", "followup_dates", "expected_note", "text", "message_key"]
    r = a.comms.requests
    return r[[c for c in cols if c in r.columns]].sort_values("timestamp")


def write_all(a: Analysis, out_dir: str, template_path: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    paths = {}

    def save(df, name):
        p = os.path.join(out_dir, name)
        df.to_csv(p, index=False)
        paths[name] = p

    save(a.ledger, "evidence_ledger.csv")
    save(financial_reconciliation(a), "financial_reconciliation.csv")
    save(client_identity_report(a), "client_identity_report.csv")
    save(a.clients.record_links.drop(columns=["needs_review"], errors="ignore"), "client_record_links.csv")
    save(lost_requests_report(a), "lost_requests_report.csv")
    save(a.churn.scores, "churn_analysis.csv")
    save(a.churn.signals, "churn_signals.csv")
    save(a.marketing.channels, "marketing_analysis.csv")
    save(a.marketing.client_value, "marketing_client_value.csv")
    answers = build_answers(a, template_path)
    p = os.path.join(out_dir, "answers.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(_whole_numbers(answers), fh, indent=2, ensure_ascii=False)
    paths["answers.json"] = p
    p = os.path.join(out_dir, "analysis_report.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(analysis_markdown(a))
    paths["analysis_report.md"] = p
    return paths


# ---------------------------------------------------------------------------
# answers.json: fill the original template, keep its structure exactly
# ---------------------------------------------------------------------------
CAME_BY = {"email": "email", "whatsapp": "whatsapp", "voice note": "voice note", "instagram": "instagram"}


def _whole_numbers(x):
    """Write 1500.0 as 1500 (the template uses integers); keep cents when present."""
    if isinstance(x, dict):
        return {k: _whole_numbers(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_whole_numbers(v) for v in x]
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def build_answers(a: Analysis, template_path: str) -> dict:
    with open(template_path, encoding="utf-8") as fh:
        tpl = json.load(fh)
    ans = copy.deepcopy(tpl)
    f, cm, ch, mk = a.finance, a.comms, a.churn, a.marketing
    ans["pack_id"] = "208"
    # left empty on purpose: unknown here, or the links do not exist yet
    for k in ("your_name", "your_email", "live_link", "video_link", "code_or_files_link"):
        ans[k] = ""

    cl = a.clients.clients
    ans["client_count"] = int(len(cl))
    ans["clients"] = [{"name": r.canonical_name, "other_names_used_in_the_data": list(r.other_names),
                       "emails": list(r.emails), "phones": list(r.phones)} for r in cl.itertuples()]
    ans["payers_who_are_not_clients"] = [{"name": r.name, "pays_for": list(r.pays_for)}
                                         for r in a.clients.payers.itertuples()]
    ans["overdue"] = [{"client": r.client, "invoice": r.invoice_no, "amount_owed": float(r.balance),
                       "days_late": int(r.days_late)} for r in f.overdue.itertuples()]
    ans["overdue_total"] = float(f.summary["overdue_total"])
    ans["we_owe"] = [{"client": r.client, "amount": float(r.amount), "why": r.why}
                     for r in f.we_owe[f.we_owe.status != "unresolved"].itertuples()]
    ans["never_billed"] = [{"client": r.client, "booking_id_or_month": r.booking_or_month, "amount": float(r.amount)}
                           for r in f.never_billed.itertuples() if pd.notna(r.amount)]
    ans["never_billed_total"] = float(f.summary["never_billed_total"])
    te = f.tool_errors
    keep = te[(te.status != "unresolved") &
              ~((te.kind == "duplicate_invoice") & te.truth.str.contains("voided"))]
    ans["billing_tool_is_wrong"] = [{"invoice": r.invoice_no, "tool_says": str(r.tool_says), "truth": str(r.truth),
                                     "how_you_know": r.how_we_know} for r in keep.itertuples()]
    lost = cm.requests[cm.requests.classification == "confirmed_lost"].sort_values("timestamp")
    ans["lost_requests"] = [{"video_id": r.video_id, "request_date": r.timestamp[:10],
                             "came_by": CAME_BY.get(r.came_by, r.came_by), "why_it_was_lost": r.why}
                            for r in lost.itertuples()]
    ans["lost_requests_root_causes"] = [c[1] for c in cm.root_causes]
    ans["lost_requests_started_getting_worse_in"] = cm.worse_since
    nr = cm.never_replied_leads
    ans["never_replied_leads"] = list(nr[~nr.likely_junk].lead_id)
    ml = ch.most_likely
    ans["most_likely_to_leave"] = {"client": ml["client"], "evidence": ml["evidence"],
                                   "what_to_do_this_week": ml["what_to_do_this_week"]}
    ans["best_marketing_channel"] = {"channel": mk.best["channel"], "why": mk.best["why"]}
    ans["what_this_data_cannot_tell_us"] = cannot_tell(a)
    ans["monday_top_3"] = a.priorities.top3_text[:3]
    # structure check: same keys, same order, same nested keys as the template
    assert list(ans.keys()) == list(tpl.keys())
    for k, v in tpl.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            for item in ans[k]:
                assert list(item.keys()) == list(v[0].keys()), k
        if isinstance(v, dict):
            assert list(ans[k].keys()) == list(v.keys()), k
    return ans


def cannot_tell(a: Analysis) -> list:
    """Limits of the data, each built from the analysis itself (nothing typed in by hand)."""
    out = list(a.marketing.limitations[:2])
    msgs = a.ctx.frames.get("messages")
    if msgs is not None:
        n_out = int((msgs.direction == "outbound").sum())
        out.append(f"Whether anyone replied to client messages: messages.txt holds only {n_out} outbound "
                   f"message(s) out of {len(msgs)}, so response times to clients cannot be measured.")
    if len(a.finance.payables):
        out.append("What Axeom owes its team or suppliers: there are no payroll, bill or bank-out records, only pay "
                   "rates and hours (an incurred-cost estimate is given, not a debt).")
    alloc = a.finance.allocations
    inferred = alloc[alloc.status != "confirmed"]
    if len(inferred):
        out.append(f"Which invoice each of the {inferred.payment_id.nunique()} payments without an invoice number was "
                   f"meant for: they are matched by client, reference text, amount and date, which is strong but not "
                   f"proof.")
    te = a.finance.tool_errors
    for r in te[te.status == "unresolved"].itertuples():
        out.append(f"Why {r.invoice_no} ({r.client}) is {r.tool_says} when {r.truth.replace('unclear: ', '')}: "
                   f"{r.how_we_know}.")
    rv = a.ctx.model.resolution.review
    same = rv[rv.kind == "same_name_different_contact"]
    if len(same):
        explained = {n.lower() for r in a.clients.payers.itertuples() for n in r.pays_for}   # a payer booking for a client
        names = sorted({re.search(r"'([^']+)'", d).group(1).title() for d in same.detail if re.search(r"'([^']+)'", d)}
                       - {n.title() for n in explained})
        out.append("Whether records sharing a name but not contact details are one account (kept separate): "
                   + ", ".join(names) + ".")
    um = a.comms.requests[a.comms.requests.classification == "unmatched"]
    if len(um):
        out.append(f"Which video {len(um)} request(s) were about: they say only 'the one we shot last week' and no "
                   f"card from that shoot carries the change.")
    links = a.ctx.model.resolution.links
    lost_msgs = links[(links.source == "messages") & (links.client_id == "")]
    if len(lost_msgs):
        out.append(f"Who sent {len(lost_msgs)} message(s) that cannot be tied to any client "
                   f"({', '.join(sorted(set(lost_msgs.handle.replace('', None).dropna().map(lambda h: '@' + h)) | set(lost_msgs.email.replace('', None).dropna())))}).")
    return out


# ---------------------------------------------------------------------------
# analysis_report.md (generated, so every number matches the code)
# ---------------------------------------------------------------------------
def _table(df: pd.DataFrame, cols: list, fmt: dict | None = None) -> str:
    fmt = fmt or {}
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    body = ""
    for _, r in df.iterrows():
        vals = []
        for c in cols:
            v = r[c]
            v = fmt[c](v) if c in fmt and pd.notna(v) else ("" if (not isinstance(v, (list, dict)) and pd.isna(v)) else v)
            vals.append(str(v).replace("|", "/").replace("\n", " "))
        body += "| " + " | ".join(vals) + " |\n"
    return head + body


def analysis_markdown(a: Analysis) -> str:
    f, cm, ch, mk, cl = a.finance, a.comms, a.churn, a.marketing, a.clients
    s = f.summary
    money = lambda v: f"{v:,.2f}"
    led = a.ledger
    st = led.drop_duplicates("finding_id").status.value_counts().to_dict()
    md = [f"# Axeom Pulse - Phase 2 analysis (Pack 208)\n",
          f"Reference date **{REFERENCE_DATE.isoformat()}**. Generated by `python run_analysis.py`; every number "
          f"below comes from the code and is traced in `evidence_ledger.csv` ({led.finding_id.nunique()} findings, "
          f"{len(led)} source references: {st.get('confirmed', 0)} confirmed, {st.get('inferred', 0)} inferred, "
          f"{st.get('unresolved', 0)} unresolved).\n",
          "**Confirmed** = stated directly by the data or two sources agree. **Inferred** = strong but indirect "
          "evidence (method given). **Unresolved** = needs a human.\n"]

    md.append(f"## Q1. Real clients: {len(cl.clients)}\n")
    md.append("A real client has at least one booking, invoice or video of its own. Leads that never bought, "
              "spam, and payers who only pay for others are not clients. Identity rules: email > phone > confirmed "
              "or announced alias > exact name > fuzzy name (Phase 1), plus two Phase 2 rules: a client's own message "
              "announcing a new email joins old and new (Pecan Gym -> Pecan Athletics), and a payer's bookings made "
              "in another client's name are given to that client.\n")
    t = cl.clients.copy()
    t["names"] = t.other_names.map(len)
    t["emails"] = t.emails.map(", ".join)
    md.append(_table(t, ["canonical_name", "emails", "names", "n_bookings", "n_invoices", "n_payments",
                         "n_production", "n_messages"]))
    md.append("\nPayers who are not clients: " + "; ".join(f"**{r.name}** pays for {', '.join(r.pays_for)}"
                                                          for r in cl.payers.itertuples()) + ".\n")
    open_q = cl.uncertain[cl.uncertain.status_after_q1 == "open"]
    md.append("Still uncertain (kept separate, for the owner): " +
              "; ".join(f"{r.review_id} {r.detail}" for r in open_q.itertuples()) + "\n")

    md.append("## Q2. Money\n")
    md.append(_table(pd.DataFrame([{"figure": k.replace('_', ' '), "amount": v} for k, v in s.items()]),
                     ["figure", "amount"], {"amount": money}))
    md.append("\nPayment matching (each payment used once, never on amount alone): " +
              ", ".join(f"{k} {v}" for k, v in f.allocations.method.value_counts().items()) + ".\n")
    md.append(f"\n### Overdue on {REFERENCE_DATE.isoformat()}: {money(s['overdue_total'])} "
              f"({money(s['overdue_confirmed_total'])} confirmed by both the billing tool and the bank file)\n")
    md.append(_table(f.overdue, ["invoice_no", "client", "amount", "received", "balance", "due", "days_late",
                                 "tool_status", "status", "basis"], {"amount": money, "received": money, "balance": money}))
    md.append(f"\nNot yet due: {money(s['receivable_not_yet_due'])} on {len(f.receivable_not_due)} invoices.\n")
    md.append(f"\n### We owe clients: {money(s['we_owe_total'])}\n")
    md.append(_table(f.we_owe, ["client", "amount", "status", "why"], {"amount": money}))
    md.append(f"\n### Never billed: {money(s['never_billed_total'])}\n")
    md.append(_table(f.never_billed, ["client", "booking_or_month", "service", "amount", "videos", "confidence", "why"],
                     {"amount": money}))
    md.append("\nRetainer Shoot bookings at price 0 are NOT counted when a 'Monthly retainer <month>' invoice exists "
              "for that client and month.\n")
    md.append("\n### Billing tool errors and suspicious records\n")
    md.append(_table(f.tool_errors, ["invoice_no", "client", "kind", "tool_says", "truth", "status", "confidence"]))
    md.append("\n### Costs incurred (estimates, not recorded debts)\n")
    md.append(_table(f.payables, ["item", "basis", "estimated_cost"], {"estimated_cost": money}))
    md.append("\nNo payroll, supplier bill or payment-out record exists, so nothing here can be called an "
              "outstanding obligation.\n")

    md.append("## Q3. Lost requests\n")
    md.append("Edit requests were found in messages.txt, tied to a video (client + quoted title, or the last "
              "week's shoot) and checked against `notes_on_board`.\n")
    md.append(_table(cm.by_month.reset_index(), ["month", "completed", "delayed_or_unresolved", "confirmed_lost",
                                                 "unmatched", "requests", "lost_share"]))
    md.append("\n" + _table(cm.by_channel.reset_index(), ["came_by", "completed", "delayed_or_unresolved",
                                                          "confirmed_lost", "unmatched", "requests", "lost_share"]))
    md.append(f"\n**Got worse from {cm.worse_since}.** Root causes:\n")
    md += [f"- {c[1]}\n" for c in cm.root_causes]
    md.append(f"\nUnmatched requests ({int((cm.requests.classification == 'unmatched').sum())}) are NOT counted as "
              "lost. Leads never replied to (spam excluded): " +
              ", ".join(cm.never_replied_leads[~cm.never_replied_leads.likely_junk].lead_id) + ".\n")
    if len(cm.missed_referrals):
        md.append("\nReferrals: " + "; ".join(f"{r.timestamp} {r.referred_by}: {r.check}"
                                             for r in cm.missed_referrals.itertuples()) + ".\n")
    if len(cm.opportunities):
        md.append("\nClients asking about more work: " + "; ".join(f"{r.timestamp} {r.client}: '{r.text}' -> {r.check}"
                                                                  for r in cm.opportunities.itertuples()) + ".\n")

    md.append("## Q4. Most likely to leave\n")
    m = ch.most_likely
    md.append(f"**{m['client']}** ({m['risk_points']} points; next {m['runner_up']} {m['runner_up_points']}). "
              "A transparent checklist, not a predictive model (weights in `churn_analysis.py`).\n")
    md += [f"- {e}\n" for e in m["evidence"]]
    md.append(f"\nThis week: {m['what_to_do_this_week']}\n")
    if len(ch.already_lost):
        md.append("\nAlready lost: " + ", ".join(f"{r.client} (last booking {r.last_booking})"
                                                for r in ch.already_lost.itertuples()) + ".\n")
    md.append("\n" + _table(ch.scores.head(8), ["client", "risk_points", "signals", "praise_messages", "last_booking",
                                                "upcoming_bookings", "invoiced_last_90_days"]))

    md.append("## Q5. Marketing channels\n")
    md.append(_table(mk.channels, ["channel", "unique_prospects", "converted_clients", "conversion_rate", "invoiced",
                                   "collected", "est_delivery_cost", "est_margin_after_delivery",
                                   "est_margin_per_prospect"],
                     {"invoiced": money, "collected": money, "est_delivery_cost": money,
                      "est_margin_after_delivery": money, "conversion_rate": lambda v: f"{v:.0%}"}))
    md.append(f"\n**{mk.best['channel']}**: {mk.best['why']}.\n\nLimits:\n")
    md += [f"- {x}\n" for x in mk.limitations]

    md.append("## Q6. Monday's first three actions\n")
    for i, act in enumerate(a.priorities.actions[:3], 1):
        md.append(f"**{i}. {act['what']}.**\n\n- Concerns: {act['concerns']}\n- Why now: {act['why_first']}\n"
                  f"- Benefit: {act['benefit']}\n- Evidence: " + "; ".join(act["evidence"][:6]) + "\n")
        if act["detail"]:
            md.append(f"- Detail: {act['detail']}\n")
    md.append("\nNothing is sent automatically; every reminder or message is a suggestion for the owner.\n")
    md.append("## What this data cannot tell us\n")
    md += [f"- {x}\n" for x in cannot_tell(a)]
    return "\n".join(md)
