"""What each screen shows, as plain data (no Streamlit here, so it is testable).

app.py only lays these out. Every number comes from the Phase 2 analysis.
"""
from __future__ import annotations

import pandas as pd

from .config import REFERENCE_DATE


def _uniq(names) -> list:
    """Drop repeats that differ only in spacing or capital letters (keeps the first spelling)."""
    seen, out = set(), []
    parts = [p for n in names for p in str(n).split(",")]      # "Simone Ramirez, Juniper Wealth Partners"
    for n in parts:
        k = " ".join(str(n).split()).casefold()
        if k and k not in seen:
            seen.add(k)
            out.append(" ".join(str(n).split()))
    return out


def _m(x) -> str:
    x = float(x or 0)
    return f"{x:,.0f}" if x.is_integer() else f"{x:,.2f}"


# ----------------------------------------------------------------- headline numbers
def key_numbers(a) -> dict:
    f, r = a.finance, a.comms.requests
    nr = a.comms.never_replied_leads
    return {
        "Clients": len(a.clients.clients),
        "Overdue (owed to us)": float(f.summary["overdue_total"]),
        "Overdue invoices": len(f.overdue),
        "Never billed": float(f.summary["never_billed_total"]),
        "We owe clients": float(f.summary["we_owe_total"]),
        "Edit requests lost": int((r.classification == "confirmed_lost").sum()),
        "Edit requests at risk": int((r.classification == "delayed_or_unresolved").sum()),
        "Leads never answered": int((~nr.likely_junk).sum()),
    }


def diff_numbers(before: dict, after: dict) -> pd.DataFrame:
    """Rows for every headline number, marking the ones an upload changed."""
    rows = []
    for k in after:
        b, c = before.get(k), after[k]
        rows.append({"what": k, "before": b, "after": c, "changed": b != c})
    return pd.DataFrame(rows, columns=["what", "before", "after", "changed"])


# ----------------------------------------------------------------- today
def today(a, waiting: list) -> dict:
    """The 10-second screen: one thing to do first, two next, and a count of open questions."""
    f, r = a.finance, a.comms.requests
    lost = int((r.classification == "confirmed_lost").sum())
    at_risk = int((r.classification == "delayed_or_unresolved").sum())
    nr = a.comms.never_replied_leads
    open_by_group = {}
    for s in waiting:
        open_by_group[s.group] = open_by_group.get(s.group, 0) + 1
    call = next((s for s in waiting if s.kind == "call"), None)
    cards = []
    for act in a.priorities.actions:
        k = act.get("kind")
        if k == "churn":
            top = a.churn.most_likely
            cards.append({"kind": k, "group": 1, "title": f"Call {top['client']} today",
                          "line": call.why if call else act["why_first"], "detail": act["detail"]})
        elif k == "edits":
            since = a.comms.worse_since or "recently"
            line = f"{lost} requests never reached the board (worse since {since})"
            if at_risk:
                line += f"; {at_risk} can still be fixed before delivery"
            cards.append({"kind": k, "group": 2 if at_risk else 5, "title": "Stop losing edit requests",
                          "line": line, "detail": act["what"]})
        elif k == "money":
            s = f.summary
            cards.append({"kind": k, "group": 3,
                          "title": f"Collect {_m(s['overdue_total'])} overdue ({len(f.overdue)} invoices)",
                          "line": f"Also {_m(s['never_billed_total'])} never billed and {_m(s['we_owe_total'])} "
                                  f"we owe clients", "detail": act["what"]})
        elif k == "leads":
            cards.append({"kind": k, "group": 4, "title": f"Answer {int((~nr.likely_junk).sum())} leads",
                          "line": "They asked about videos and nobody replied", "detail": act["what"]})
    return {"date": f"{REFERENCE_DATE:%A %d %B %Y}", "cards": cards[:3], "rest": cards[3:],
            "waiting": len(waiting), "open_by_group": open_by_group}


# ----------------------------------------------------------------- clients
def client_options(a) -> list[tuple[str, str]]:
    """(label, client_id) for the picker: clients first, then payers."""
    c = a.clients.clients.sort_values("canonical_name")
    opts = [(n, cid) for n, cid in zip(c.canonical_name, c.client_id)]
    p = a.clients.payers
    opts += [(f"{n} (pays for others)", cid) for n, cid in zip(p["name"], p.client_id)]
    return opts


def _ids(a, cid: str, source: str) -> list:
    rl = a.clients.record_links
    return list(dict.fromkeys(rl[(rl.client_id == cid) & (rl.source == source)].record_id))


def _pick(df: pd.DataFrame, id_col: str, ids: list, cols: dict) -> pd.DataFrame:
    sub = df[df[id_col].isin(ids)]
    out = sub[[c for c in cols if c in sub.columns]].rename(columns=cols)
    return out.reset_index(drop=True)


def client_profile(a, cid: str) -> dict:
    f = a.ctx.frames
    c = a.clients.clients.set_index("client_id")
    p = a.clients.payers.set_index("client_id")
    is_payer = cid in p.index and cid not in c.index
    if cid in c.index:
        row = c.loc[cid]
        name = row.canonical_name
        facts = {"Also written as": ", ".join(n for n in _uniq(row.other_names) if n.casefold() != name.casefold()), "Emails": ", ".join(row.emails),
                 "Phones": ", ".join(row.phones), "Instagram": ", ".join(row.instagram)}
    else:
        name = p.loc[cid, "name"] if cid in p.index else cid
        facts = {}
    if cid in p.index:
        facts["Pays for"] = ", ".join(p.loc[cid, "pays_for"])
    paid_by = [p.loc[x, "name"] for x in p.index if cid in p.loc[x, "pays_for_ids"]]
    if paid_by:
        facts["Billed through"] = ", ".join(paid_by)
    facts = {k: v for k, v in facts.items() if v}

    inv = a.finance.invoices
    inv_ids = _ids(a, cid, "invoices")
    mine = inv[inv.invoice_no.isin(inv_ids) & (inv.true_status != "void")]
    money = {"Invoiced": _m(mine.amount.sum()), "Received": _m(mine.received.sum()),
             "Open": _m(mine.balance.sum()),
             "Overdue": _m(a.finance.overdue[a.finance.overdue.invoice_no.isin(inv_ids)].balance.sum())}

    tables = {
        "Invoices": _pick(inv, "invoice_no", inv_ids, {
            "invoice_no": "invoice", "issued": "issued", "due": "due", "amount": "amount", "received": "received",
            "balance": "open", "true_status": "real status", "tool_status": "billing tool says",
            "days_late": "days late", "payments": "paid by"}),
        "Payments": _pick(a.finance.payments.assign(date=a.finance.payments.date_date.dt.date), "payment_id",
                          _ids(a, cid, "payments"), {
                              "payment_id": "payment", "date": "date", "payer": "payer (as written)",
                              "amount_value": "amount", "reference": "reference", "invoices": "pays invoices"}),
        "Bookings": _pick(f["bookings"], "booking_id", _ids(a, cid, "bookings"), {
            "booking_id": "booking", "date": "date", "client_name": "name (as written)", "service": "service",
            "status": "status", "price": "price"}),
        "Videos": _pick(f["production"], "video_id", _ids(a, cid, "production"), {
            "video_id": "video", "title": "title", "stage": "stage", "notes_on_board": "note on board",
            "editor": "editor", "revisions": "revisions", "shoot_booking": "booking"}),
        "Edit requests": _requests_for(a, cid),
        "Messages": _pick(f["messages"], "message_key", _ids(a, cid, "messages"), {
            "timestamp": "when", "channel": "channel", "direction": "direction", "text": "message"}),
        "Leads": _pick(f["leads"], "lead_id", _ids(a, cid, "leads"), {
            "lead_id": "lead", "created": "created", "name": "name", "business": "business", "source": "source",
            "first_reply": "first reply", "status": "status"}),
    }
    if len(tables["Messages"]):
        tables["Messages"] = tables["Messages"].sort_values("when", ascending=False).reset_index(drop=True)
    rl = a.clients.record_links
    how = rl[rl.client_id == cid][["source", "record_id", "raw_names", "email", "phone", "handle", "method",
                                   "confidence", "source_file", "source_line", "note"]].rename(
        columns={"raw_names": "names as written", "source_file": "file", "source_line": "line"})
    sc = a.churn.scores
    risk = sc[sc.client_id == cid]
    risk_line = ""
    if len(risk):
        x = risk.iloc[0]
        risk_line = ("Already lost: said it would pause and has not booked since" if x.already_lost else
                     f"Risk points {int(x.risk_points)} ({x.signals})" if x.risk_points else "No risk signals")
    return {"name": name, "is_payer": is_payer, "facts": facts, "money": money, "risk": risk_line,
            "tables": tables, "how_linked": how.reset_index(drop=True)}


def _requests_for(a, cid: str) -> pd.DataFrame:
    r = a.comms.requests
    sub = r[r.client_id == cid]
    return sub[["timestamp", "came_by", "requested", "video_id", "classification", "why"]].rename(
        columns={"timestamp": "when", "came_by": "sent as", "video_id": "video", "classification": "result"}
    ).sort_values("when", ascending=False).reset_index(drop=True)


# ----------------------------------------------------------------- money
def money(a) -> dict:
    f = a.finance
    s = f.summary
    ov = f.overdue[["invoice_no", "client", "balance", "days_late", "due", "tool_status", "status", "basis"]].rename(
        columns={"invoice_no": "invoice", "balance": "owed", "days_late": "days late",
                 "tool_status": "billing tool says", "status": "evidence"})
    nb = f.never_billed[["client", "booking_or_month", "service", "amount", "videos", "why"]].rename(
        columns={"booking_or_month": "booking / month"})
    owe = f.we_owe[["client", "amount", "why", "status"]].rename(columns={"status": "evidence"})
    te = f.tool_errors[["invoice_no", "client", "tool_says", "truth", "how_we_know", "status"]].rename(
        columns={"invoice_no": "invoice", "tool_says": "billing tool says", "truth": "real", "how_we_know": "how we know",
                 "status": "evidence"})
    nd = f.receivable_not_due[["invoice_no", "client", "balance", "due"]].rename(
        columns={"invoice_no": "invoice", "balance": "open"}) if hasattr(f, "receivable_not_due") else pd.DataFrame()
    return {"numbers": {"Overdue": _m(s["overdue_total"]), "Never billed": _m(s["never_billed_total"]),
                        "We owe clients": _m(s["we_owe_total"]), "Not due yet": _m(s["receivable_not_yet_due"])},
            "overdue": ov.reset_index(drop=True), "never_billed": nb.reset_index(drop=True),
            "we_owe": owe.reset_index(drop=True), "tool_errors": te.reset_index(drop=True),
            "not_due": nd.reset_index(drop=True),
            "note": (f"Overdue = past due on {REFERENCE_DATE:%d %b %Y} and not covered by a bank payment. "
                     f"{_m(s['overdue_confirmed_total'])} of it is confirmed by both the billing tool and the bank "
                     f"file; the rest is where they disagree.")}


# ----------------------------------------------------------------- edits and leads
def edits(a) -> dict:
    c = a.comms
    r = c.requests
    open_ = r[r.classification.isin(["delayed_or_unresolved", "confirmed_lost"])].copy()
    open_["order"] = open_.classification.map({"delayed_or_unresolved": 0, "confirmed_lost": 1})
    open_ = open_.sort_values(["order", "timestamp"], ascending=[True, False])
    tbl = open_[["timestamp", "client", "came_by", "requested", "video_id", "classification", "why"]].rename(
        columns={"timestamp": "when", "came_by": "sent as", "video_id": "video", "classification": "result"})
    bm = c.by_month.reset_index()
    by_month = pd.DataFrame({"month": bm["month"].astype(str),
                             "lost": bm.get("confirmed_lost", 0), "requests": bm.get("requests", 0)})
    ch = c.by_channel.reset_index()
    ch = ch.rename(columns={ch.columns[0]: "sent as"})
    return {"causes": [x[1] for x in c.root_causes], "worse_since": c.worse_since,
            "by_month": by_month, "by_channel": ch[[x for x in ["sent as", "requests", "confirmed_lost", "lost_share"]
                                                  if x in ch.columns]],
            "open": tbl.reset_index(drop=True)}


def leads(a) -> dict:
    nr = a.comms.never_replied_leads
    real = nr[~nr.likely_junk].sort_values("created", ascending=False)
    ch = a.marketing.channels
    cols = [x for x in ["channel", "leads", "converted_clients", "conversion_rate", "invoiced", "collected",
                        "est_margin_after_delivery"] if x in ch.columns]
    return {"unanswered": real[["lead_id", "created", "name", "business", "source", "note"]].reset_index(drop=True),
            "junk": int(nr.likely_junk.sum()),
            "channels": ch[cols].rename(columns={"converted_clients": "clients", "conversion_rate": "conversion",
                                                 "est_margin_after_delivery": "est. margin after delivery"}),
            "best": a.marketing.best}
