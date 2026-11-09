"""Question 2 - money: who owes us, what we owe, unbilled work, billing tool errors.

Evidence levels
---------------
Payment -> invoice allocation, strongest first:
  explicit_reference  payment.reference is the invoice number          (confirmed)
  reference_month     free-text reference names a month ("Kindred Jul
                      invoices", "july sessions") -> that payer's
                      invoices issued in that month, oldest first       (inferred, high)
  client_amount_date  no reference: same client (or a client the payer
                      pays for) AND remaining balance equals the payment
                      AND invoice issued on/before the payment date.
                      Prefers invoices the billing tool already marks
                      paid, then the closest issue date                 (inferred, medium;
                                                                         low if the tool disagrees)
  partial_text        the only open invoice of that client before the
                      payment, when the payment is smaller and its
                      reference says it is a part payment             (inferred, medium)
An equal amount on its own is NEVER enough: the client must match too.
Every payment is allocated at most once, so nothing is double-counted.

Booking -> invoice (for "never billed"):
  memo_booking_id     memo contains the booking id                       (confirmed)
  memo_service_date   memo names the service and the booking's M/D       (confirmed)
  retainer_month      "Monthly retainer <Month>" covers that client's
                      Retainer Shoot bookings in that month              (confirmed)
  amount_date         same client, invoice amount = booking price,
                      issued 7 days before .. 14 days after the shoot    (inferred, medium)
  moved_with_reschedule  an invoice paid for a booking that was then
                      cancelled: if the client's same shoot happened
                      within 30 days, the payment moves to it            (inferred, high with a message)
"""
from __future__ import annotations

import calendar
import re
from collections import defaultdict
from dataclasses import dataclass

import pandas as pd

from .context import Context

QUESTION = "Q2 money"
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
PARTIAL_WORDS = re.compile(r"\b(half|part|partial|deposit|installment|instalment)\b", re.I)
RETAINER_MEMO = re.compile(r"retainer\s+([A-Za-z]+)", re.I)


@dataclass
class FinanceResult:
    allocations: pd.DataFrame     # payment -> invoice, amount, method, confidence
    invoices: pd.DataFrame        # one row per invoice with true status and balance
    payments: pd.DataFrame        # one row per payment with allocated / unallocated amount
    booking_links: pd.DataFrame   # booking -> invoice
    overdue: pd.DataFrame
    receivable_not_due: pd.DataFrame
    we_owe: pd.DataFrame
    never_billed: pd.DataFrame
    tool_errors: pd.DataFrame     # confirmed errors and suspicious records
    payables: pd.DataFrame        # incurred cost estimates (not recorded obligations)
    summary: dict


def _month_of(text: str):
    for w in re.findall(r"[A-Za-z]+", text or ""):
        if w.lower() in MONTHS:
            return MONTHS[w.lower()]
    return None


def analyze_finances(ctx: Context, names: dict, pays_for: dict) -> FinanceResult:
    f = ctx.frames
    inv = f["invoices"].copy()
    pay = f["payments"].copy()
    bk = f["bookings"].copy()
    today = ctx.today
    L, src = ctx.ledger, ctx.src

    def who(cid):
        return names.get(cid, cid)

    def accounts(cid):
        """identities whose invoices this payer may settle: itself + those it pays for"""
        return {cid} | set(pays_for.get(cid, set()))

    inv["tool_status"] = inv["status_norm"]
    open_bal = {r.invoice_no: (0.0 if r.tool_status == "void" or pd.isna(r.amount_value) else r.amount_value)
                for r in inv.itertuples()}
    inv_by_no = inv.set_index("invoice_no")
    allocs = []
    pay_left = dict(zip(pay.payment_id, pay.amount_value.fillna(0.0)))
    pay = pay.sort_values(["date_date", "payment_id"])

    def allocate(pid, ino, amount, method, status, conf, why):
        allocs.append({"payment_id": pid, "invoice_no": ino, "amount": round(amount, 2), "method": method,
                       "status": status, "confidence": conf, "evidence": why})
        open_bal[ino] = round(open_bal[ino] - amount, 2)
        pay_left[pid] = round(pay_left[pid] - amount, 2)

    # ---- pass 1: explicit invoice numbers --------------------------------
    for p in pay.itertuples():
        ref = str(p.reference).strip().upper()
        if not re.fullmatch(r"INV-\d+", ref) or ref not in open_bal:
            continue
        if inv_by_no.loc[ref, "tool_status"] == "void":
            continue   # paying a void invoice is reported as an error below
        amt = min(pay_left[p.payment_id], max(open_bal[ref], 0.0))
        if amt > 0:
            allocate(p.payment_id, ref, amt, "explicit_reference", "confirmed", "high",
                     f"payment reference is {ref}")

    # ---- pass 2: free-text reference naming a month ----------------------
    for p in pay.itertuples():
        ref = str(p.reference).strip()
        if not ref or re.fullmatch(r"INV-\d+", ref.upper()) or pay_left[p.payment_id] <= 0:
            continue
        month = _month_of(ref)
        if month is None:
            continue
        year = p.date_date.year if month <= p.date_date.month else p.date_date.year - 1
        cand = inv[inv.client_id.isin(accounts(p.client_id)) & (inv.tool_status != "void") &
                   (inv.issued_date.dt.month == month) & (inv.issued_date.dt.year == year)]
        cand = cand.loc[[open_bal[i] > 0 for i in cand.invoice_no] if len(cand) else cand.index].sort_values("issued_date")
        total = sum(open_bal[i] for i in cand.invoice_no)
        exact = abs(total - pay_left[p.payment_id]) < 0.01
        for i in cand.invoice_no:
            if pay_left[p.payment_id] <= 0:
                break
            amt = min(pay_left[p.payment_id], open_bal[i])
            allocate(p.payment_id, i, amt, "reference_month", "inferred", "high" if exact else "medium",
                     f"reference '{ref}' names {calendar.month_name[month]} {year}; open invoices of this "
                     f"payer in that month total {total:.2f}" + ("" if exact else " (not an exact match)"))

    # ---- pass 3: same client + same remaining amount + date --------------
    for p in pay.itertuples():
        left = pay_left[p.payment_id]
        if left <= 0 or not p.client_id or pd.isna(p.date_date):
            continue
        cand = inv[inv.client_id.isin(accounts(p.client_id)) & (inv.tool_status != "void") &
                   (inv.issued_date <= p.date_date)]
        cand = cand.loc[[abs(open_bal[i] - left) < 0.01 for i in cand.invoice_no] if len(cand) else cand.index]
        if not len(cand):
            continue
        cand = cand.assign(tool_paid=(cand.tool_status == "paid").astype(int),
                           gap=(p.date_date - cand.issued_date).dt.days)
        best = cand.sort_values(["tool_paid", "gap"], ascending=[False, True]).iloc[0]
        agrees = best.tool_status == "paid"
        n_alt = len(cand) - 1
        allocate(p.payment_id, best.invoice_no, left, "client_amount_date", "inferred",
                 "medium" if agrees else "low",
                 f"no invoice number; same client, open balance {left:.2f} equals the payment, issued "
                 f"{int(best.gap)} days before it; billing tool status '{best.tool_status}'"
                 + (f"; {n_alt} other open invoice(s) of the same amount existed" if n_alt else ""))

    # ---- pass 4: part payments ----------------------------------------------
    for p in pay.itertuples():
        left = pay_left[p.payment_id]
        if left <= 0 or not p.client_id or not PARTIAL_WORDS.search(str(p.reference)):
            continue
        cand = inv[inv.client_id.isin(accounts(p.client_id)) & (inv.tool_status != "void") &
                   (inv.issued_date <= p.date_date)]
        cand = cand.loc[[open_bal[i] > left for i in cand.invoice_no] if len(cand) else cand.index]
        if len(cand) == 1:
            i = cand.invoice_no.iloc[0]
            allocate(p.payment_id, i, left, "partial_text", "inferred", "medium",
                     f"reference '{p.reference}' says part payment; {i} is the only larger open invoice "
                     f"of this client before the payment")

    alloc = pd.DataFrame(allocs, columns=["payment_id", "invoice_no", "amount", "method", "status",
                                          "confidence", "evidence"])

    # ---- booking -> invoice ------------------------------------------------
    services = sorted(set(bk.service), key=len, reverse=True)
    svc_re = re.compile("(" + "|".join(re.escape(s) for s in services) + r")\s+(\d{1,2})/(\d{1,2})", re.I) \
        if services else None
    blinks = []
    used_inv = set()
    bk_by_id = bk.set_index("booking_id")
    for r in inv[inv.tool_status != "void"].itertuples():
        for b in re.findall(r"\b(B\d+)\b", str(r.memo)):
            if b in bk_by_id.index:
                blinks.append({"booking_id": b, "invoice_no": r.invoice_no, "method": "memo_booking_id",
                               "status": "confirmed"})
                used_inv.add(r.invoice_no)
    linked_b = {x["booking_id"] for x in blinks}
    for r in inv[(inv.tool_status != "void") & ~inv.invoice_no.isin(used_inv)].itertuples():
        m = svc_re.search(str(r.memo)) if svc_re else None
        if not m:
            continue
        cand = bk[bk.client_id.isin(accounts(r.client_id)) & (bk.service.str.lower() == m.group(1).lower()) &
                  (bk.date_date.dt.month == int(m.group(2))) & (bk.date_date.dt.day == int(m.group(3))) &
                  ~bk.booking_id.isin(linked_b)]
        if len(cand) == 1:
            b = cand.booking_id.iloc[0]
            blinks.append({"booking_id": b, "invoice_no": r.invoice_no, "method": "memo_service_date",
                           "status": "confirmed"})
            used_inv.add(r.invoice_no)
            linked_b.add(b)
    # proposed: same client, same amount, close date (closest pairs first)
    pairs = []
    rest_inv = inv[(inv.tool_status != "void") & ~inv.invoice_no.isin(used_inv) &
                   ~inv.memo.str.contains("retainer", case=False)]
    rest_bk = bk[(bk.status_norm != "cancelled") & ~bk.booking_id.isin(linked_b) & (bk.price_value > 0)]
    for r in rest_inv.itertuples():
        cand = rest_bk[rest_bk.client_id.isin(accounts(r.client_id)) &
                       ((rest_bk.price_value - r.amount_value).abs() < 0.01)]
        for b in cand.itertuples():
            gap = (r.issued_date - b.date_date).days
            if -7 <= gap <= 14:
                pairs.append((abs(gap), r.invoice_no, b.booking_id, gap))
    for _, ino, b, gap in sorted(pairs):
        if ino in used_inv or b in linked_b:
            continue
        blinks.append({"booking_id": b, "invoice_no": ino, "method": "amount_date", "status": "inferred",
                       "note": f"invoice issued {gap:+d} days from the shoot"})
        used_inv.add(ino)
        linked_b.add(b)
    blinks = pd.DataFrame(blinks, columns=["booking_id", "invoice_no", "method", "status", "note"]).fillna("")

    # retainer months covered
    retainer_cover = defaultdict(set)   # client_id -> {(year, month)}
    retainer_amounts = defaultdict(list)
    for r in inv[inv.tool_status != "void"].itertuples():
        m = RETAINER_MEMO.search(str(r.memo))
        if m and m.group(1).lower() in MONTHS:
            mo = MONTHS[m.group(1).lower()]
            retainer_cover[r.client_id].add((r.issued_date.year, mo))
            retainer_amounts[r.client_id].append(r.amount_value)

    # ---- a cancelled shoot that was moved: the payment moves with it ------
    # "We have to cancel today's shoot ... Can we move it? We already paid."
    # If an invoice bills a cancelled booking and the same client has a later
    # completed booking of the same service and price within 30 days, the
    # money already paid belongs to the rescheduled shoot. It is moved onto the
    # invoice that bills the rescheduled shoot (if there is one), so nothing is
    # counted twice and no refund is invented.
    moved = {}
    msgs = f.get("messages")
    for l in blinks[blinks.invoice_no != ""].itertuples():
        b = bk_by_id.loc[l.booking_id]
        paid_rows = alloc[alloc.invoice_no == l.invoice_no]
        if b.status_norm != "cancelled" or paid_rows.empty:
            continue
        later = bk[(bk.client_id == b.client_id) & (bk.service == b.service) & (bk.status_norm == "completed")
                   & ((bk.price_value - b.price_value).abs() < 0.01) & (bk.date_date > b.date_date)
                   & ((bk.date_date - b.date_date).dt.days <= 30)].sort_values("date_date")
        if not len(later):
            continue
        new_b = later.iloc[0]
        tgt = blinks[(blinks.booking_id == new_b.booking_id) & (blinks.invoice_no != "")].invoice_no
        tgt = tgt.iloc[0] if len(tgt) else ""
        note = ""
        if msgs is not None and len(msgs):
            near = msgs[(msgs.client_id == b.client_id) & msgs.timestamp_date.notna()
                        & ((msgs.timestamp_date - b.date_date).abs() <= pd.Timedelta(days=2))
                        & msgs.text.str.contains(r"cancel", case=False)
                        & msgs.text.str.contains(r"\bmove|reschedul|another day|new date", case=False)]
            if len(near):
                note = near.iloc[0].message_key
        conf = "high" if note else "medium"
        moved[l.invoice_no] = {"cancelled": l.booking_id, "new_booking": new_b.booking_id, "target": tgt,
                               "message": note, "paid": round(paid_rows.amount.sum(), 2),
                               "payments": ", ".join(paid_rows.payment_id), "confidence": conf}
        linked_b.add(new_b.booking_id)
        if tgt and open_bal.get(tgt, 0) > 0:
            for idx in paid_rows.index:
                amt = alloc.at[idx, "amount"]
                open_bal[l.invoice_no] = round(open_bal[l.invoice_no] + amt, 2)
                open_bal[tgt] = round(open_bal[tgt] - amt, 2)
                alloc.loc[idx, ["invoice_no", "method", "status", "confidence", "evidence"]] = [
                    tgt, "moved_with_reschedule", "inferred", conf,
                    f"paid for {l.booking_id} via {l.invoice_no}; {l.booking_id} was cancelled and the shoot moved "
                    f"to {new_b.booking_id} ({new_b.date_date.date()}), which {tgt} bills"
                    + (" (client message: cancel and move, already paid)" if note else "")]
        if not tgt:
            blinks = pd.concat([blinks, pd.DataFrame([{
                "booking_id": new_b.booking_id, "invoice_no": l.invoice_no, "method": "prepaid_before_cancellation",
                "status": "inferred", "note": f"rescheduled from cancelled {l.booking_id}; already paid"}])],
                ignore_index=True)

    # ---- invoice state ----------------------------------------------------
    rows = []
    for r in inv.itertuples():
        a = alloc[alloc.invoice_no == r.invoice_no]
        received = round(a.amount.sum(), 2)
        amount = r.amount_value
        if r.tool_status == "void":
            true = "void"
        elif r.invoice_no in moved:
            true = "billed_cancelled_booking"
        elif pd.isna(amount):
            true = "unknown_amount"
        elif received >= amount - 0.005:
            true = "paid"
        elif received > 0:
            true = "partial"
        else:
            true = "unpaid"
        bal = 0.0 if true in ("void", "paid", "billed_cancelled_booking") or pd.isna(amount) \
            else round(amount - received, 2)
        late = int((today - r.due_date).days) if bal > 0 and r.due_date < today else 0
        weakest = "none" if not len(a) else ("confirmed" if (a.status == "confirmed").all() else "inferred")
        rows.append({"invoice_no": r.invoice_no, "client_id": r.client_id, "client": who(r.client_id),
                     "bill_to": r.bill_to, "issued": r.issued_date.date(), "due": r.due_date.date(),
                     "amount": amount, "tool_status": r.tool_status, "received": received,
                     "payments": ", ".join(a.payment_id), "allocation_evidence": weakest,
                     "true_status": true, "balance": bal, "days_late": late, "memo": r.memo,
                     "source_line": r._source_line if hasattr(r, "_source_line") else ""})
    inv_state = pd.DataFrame(rows, columns=["invoice_no", "client_id", "client", "bill_to", "issued", "due",
                                            "amount", "tool_status", "received", "payments",
                                            "allocation_evidence", "true_status", "balance", "days_late", "memo",
                                            "source_line"])

    # ---- payment state ----------------------------------------------------
    pay_state = pay[["payment_id", "date_date", "payer", "payer_email", "amount_value", "reference",
                     "client_id"]].copy()
    pay_state["client"] = pay_state.client_id.map(who)
    pay_state["allocated"] = pay_state.payment_id.map(alloc.groupby("payment_id").amount.sum()).fillna(0.0)
    pay_state["unallocated"] = (pay_state.amount_value - pay_state.allocated).round(2)
    pay_state["invoices"] = pay_state.payment_id.map(alloc.groupby("payment_id").invoice_no.apply(", ".join)).fillna("")

    # ---- receivables --------------------------------------------------------
    open_inv = inv_state[(inv_state.balance > 0)]
    overdue = open_inv[open_inv.days_late > 0].copy()
    not_due = open_inv[open_inv.days_late == 0].copy()

    def overdue_conf(r):
        if r.tool_status in ("unpaid", "partial"):
            return "confirmed", "high", "billing tool and bank file agree: no (or only part) payment"
        return ("inferred", "medium",
                f"billing tool says '{r.tool_status}', but no bank payment could be matched to it")
    if len(overdue):
        overdue[["status", "confidence", "basis"]] = overdue.apply(lambda r: pd.Series(overdue_conf(r)), axis=1)
    else:                       # nothing overdue (e.g. a small upload) must not crash
        overdue = overdue.assign(status=pd.Series(dtype=str), confidence=pd.Series(dtype=str),
                                 basis=pd.Series(dtype=str))
    overdue = overdue.sort_values("days_late", ascending=False)

    for _, r in overdue.iterrows():
        a = alloc[alloc.invoice_no == r.invoice_no]
        L.add(finding_id=L.new_id("Q2-OVERDUE"), question=QUESTION, category="overdue", status=r.status,
              confidence=r.confidence, client=r.client, amount=r.balance,
              statement=f"{r.invoice_no} {r.balance:.2f} owed, {r.days_late} days late (due {r.due})",
              calculation=f"amount {r.amount:.2f} - matched payments {r.received:.2f}; days late = "
                          f"{today.date()} - {r.due}. {r.basis}",
              matching_decision="; ".join(a.method + " " + a.payment_id) or "no payment matched",
              sources=[src("invoices", r.invoice_no, "invoice")] +
                      [src("payments", x, "partial payment") for x in a.payment_id])

    # ---- never billed ---------------------------------------------------------
    nb = []
    done = bk[(bk.status_norm == "completed") & (bk.date_date <= today)]
    prod = f.get("production")
    vids = prod.groupby("shoot_booking").video_id.apply(list).to_dict() if prod is not None else {}
    for b in done.itertuples():
        if b.booking_id in linked_b:
            continue
        delivered = vids.get(b.booking_id, [])
        if "retainer" in b.service.lower() or (b.price_value == 0):
            key = (b.date_date.year, b.date_date.month)
            if key in retainer_cover.get(b.client_id, set()):
                blinks = pd.concat([blinks, pd.DataFrame([{"booking_id": b.booking_id, "invoice_no": "",
                                                            "method": "retainer_month", "status": "confirmed",
                                                            "note": f"covered by {calendar.month_name[key[1]]} retainer invoice"}])],
                                   ignore_index=True)
                continue
            amounts = retainer_amounts.get(b.client_id, [])
            usual = pd.Series(amounts).mode().iloc[0] if amounts else None
            nb.append({"client_id": b.client_id, "client": who(b.client_id), "booking_or_month":
                       f"{key[0]}-{key[1]:02d}", "booking_id": b.booking_id, "service": b.service,
                       "amount": usual, "videos": ", ".join(delivered),
                       "status": "inferred", "confidence": "medium" if usual is not None else "low",
                       "why": (f"Retainer Shoot {b.booking_id} on {b.date_date.date()} was completed"
                               f"{' and ' + str(len(delivered)) + ' video(s) made' if delivered else ''}, but there "
                               f"is no 'Monthly retainer {calendar.month_name[key[1]]}' invoice. Amount = this "
                               f"client's usual retainer invoice" if usual is not None else
                               "; no retainer invoice exists to estimate the amount")})
            continue
        nb.append({"client_id": b.client_id, "client": who(b.client_id), "booking_or_month": b.booking_id,
                   "booking_id": b.booking_id, "service": b.service, "amount": b.price_value,
                   "videos": ", ".join(delivered), "status": "inferred", "confidence": "medium",
                   "why": f"completed {b.service} on {b.date_date.date()}"
                          f"{' with ' + str(len(delivered)) + ' video(s) delivered' if delivered else ''}; no invoice "
                          f"references it, names it in the memo, or matches its client, price and date"})
    never_billed = pd.DataFrame(nb, columns=["client_id", "client", "booking_or_month", "booking_id", "service",
                                             "amount", "videos", "status", "confidence", "why"])
    for _, r in never_billed.iterrows():
        L.add(finding_id=L.new_id("Q2-UNBILLED"), question=QUESTION, category="never_billed", status=r.status,
              confidence=r.confidence, client=r.client, amount=r.amount,
              statement=f"{r.client}: {r.booking_or_month} ({r.service}) never billed", calculation=r.why,
              sources=[src("bookings", r.booking_id, "completed booking")] +
                      [src("production", v, "video from this shoot") for v in r.videos.split(", ") if v])

    # ---- billing tool errors and suspicious records --------------------------
    errs = []

    def err(ino, tool, truth, how, status, conf, kind, sources, amount=None):
        errs.append({"invoice_no": ino, "kind": kind, "tool_says": tool, "truth": truth, "how_we_know": how,
                     "status": status, "confidence": conf, "amount": amount, "_sources": sources,
                     "client": who(inv_by_no.loc[ino, "client_id"]) if ino in inv_by_no.index else ""})

    for _, r in inv_state.iterrows():
        a = alloc[alloc.invoice_no == r.invoice_no]
        psrc = [src("payments", x, "payment") for x in a.payment_id]
        if r.tool_status in ("unpaid", "partial") and r.true_status == "paid":
            st = "confirmed" if r.allocation_evidence == "confirmed" else "inferred"
            err(r.invoice_no, r.tool_status, "paid",
                f"bank payment(s) {r.payments} ({r.received:.2f}) " +
                ("reference this invoice" if st == "confirmed" else
                 "cover it: " + "; ".join(sorted(set(a.evidence)))),
                st, "high" if st == "confirmed" or set(a.method) == {"reference_month"} else "medium",
                "status_wrong_unpaid", [src("invoices", r.invoice_no, "invoice")] + psrc, r.amount)
        if r.tool_status == "paid" and r.true_status in ("unpaid", "partial"):
            err(r.invoice_no, "paid", f"{r.true_status} ({r.balance:.2f} not received)",
                "no bank payment references it, and every unreferenced payment from this client is "
                "already matched to another invoice", "inferred", "medium", "status_wrong_paid",
                [src("invoices", r.invoice_no, "invoice")], r.balance)
    # payments beyond an invoice's amount
    for p in pay_state[pay_state.unallocated > 0.005].itertuples():
        ref = str(p.reference).strip().upper()
        if re.fullmatch(r"INV-\d+", ref) and ref in inv_by_no.index:
            others = alloc[alloc.invoice_no == ref].payment_id.tolist()
            err(ref, f"{inv_by_no.loc[ref, 'tool_status']} ({inv_by_no.loc[ref, 'amount_value']:.2f})",
                f"paid {inv_by_no.loc[ref, 'amount_value'] + p.unallocated:.2f}: overpaid by {p.unallocated:.2f}",
                f"{p.payment_id} also references {ref}, already settled by {', '.join(others)}",
                "confirmed", "high", "paid_twice",
                [src("invoices", ref, "invoice"), src("payments", p.payment_id, "extra payment")] +
                [src("payments", x, "first payment") for x in others], p.unallocated)
    # duplicate invoices (same email, date, amount, memo)
    keys = inv.assign(k=inv.email_norm + "|" + inv.issued + "|" + inv.amount.astype(str) + "|" + inv.memo)
    for _, grp in keys[keys.memo != ""].groupby("k"):
        if len(grp) < 2:
            continue
        nos = list(grp.invoice_no)
        void = grp[grp.tool_status == "void"].invoice_no.tolist()
        live = [n for n in nos if n not in void]
        err(nos[-1], f"{len(nos)} invoices: " + ", ".join(f"{n} ({inv_by_no.loc[n, 'tool_status']})" for n in nos),
            "one job billed twice; the copy was voided" if void and len(live) == 1 else "possible double billing",
            "same email, issue date, amount and memo", "confirmed", "high",
            "duplicate_invoice", [src("invoices", n, "invoice") for n in nos],
            0.0 if void and len(live) == 1 else inv_by_no.loc[nos[-1], "amount_value"])
    # invoice amount differs from the booking it bills
    bl = blinks[blinks.invoice_no != ""].merge(bk[["booking_id", "price_value", "status_norm", "date_date",
                                                  "client_id"]], on="booking_id")
    for r in bl.itertuples():
        amt = inv_by_no.loc[r.invoice_no, "amount_value"]
        if pd.notna(r.price_value) and pd.notna(amt) and abs(amt - r.price_value) > 0.005 and r.price_value > 0:
            paid = inv_state.loc[inv_state.invoice_no == r.invoice_no, "received"].iloc[0]
            err(r.invoice_no, f"{amt:.2f}", f"{r.price_value:.2f} (booking {r.booking_id} price)",
                f"the invoice is linked to {r.booking_id} by {r.method}; booking price {r.price_value:.2f}"
                + (f"; client paid {paid:.2f}" if paid else ""),
                r.status, "high" if r.status == "confirmed" else "medium", "amount_differs_from_booking",
                [src("invoices", r.invoice_no, "invoice"), src("bookings", r.booking_id, "booking")],
                round(amt - r.price_value, 2))
        if r.status_norm == "cancelled":
            mv = moved.get(r.invoice_no)
            if mv:
                truth = (f"bills cancelled booking {r.booking_id}; the {mv['paid']:.2f} paid ({mv['payments']}) "
                         f"belongs to the rescheduled shoot {mv['new_booking']}"
                         + (f", which {mv['target']} bills again" if mv["target"] else ""))
                extra = [src("bookings", mv["new_booking"], "rescheduled booking")] + \
                    ([src("invoices", mv["target"], "invoice for the rescheduled shoot")] if mv["target"] else []) + \
                    ([src("messages", mv["message"], "client asks to move the shoot")] if mv["message"] else [])
                paid = 0.0
            else:
                paid = inv_state.loc[inv_state.invoice_no == r.invoice_no, "received"].iloc[0]
                truth = f"bills cancelled booking {r.booking_id}" + (f"; {paid:.2f} was paid" if paid else "")
                extra = []
            err(r.invoice_no, inv_by_no.loc[r.invoice_no, "tool_status"], truth,
                f"memo cites {r.booking_id}, whose status is cancelled", "confirmed", "high",
                "invoice_for_cancelled_booking",
                [src("invoices", r.invoice_no, "invoice"), src("bookings", r.booking_id, "cancelled booking")]
                + [src("payments", x, "payment") for x in alloc[alloc.invoice_no == r.invoice_no].payment_id]
                + [src("payments", x.strip(), "payment") for x in (mv or {}).get("payments", "").split(",") if x.strip()]
                + extra, paid)
    # retainer invoice with an unusual amount (review, not an error)
    for cid, amounts in retainer_amounts.items():
        usual = pd.Series(amounts).mode().iloc[0]
        rr = inv[(inv.client_id == cid) & inv.memo.str.contains("retainer", case=False) &
                 ((inv.amount_value - usual).abs() > 0.005)]
        for r in rr.itertuples():
            err(r.invoice_no, f"{r.amount_value:.2f}", f"unclear: this client's other retainer invoices are {usual:.2f}",
                "no message or memo explains the different amount", "unresolved", "low",
                "retainer_amount_differs", [src("invoices", r.invoice_no, "invoice")], round(r.amount_value - usual, 2))
    tool_errors = pd.DataFrame(errs, columns=["invoice_no", "client", "kind", "tool_says", "truth", "how_we_know",
                                              "status", "confidence", "amount", "_sources"])
    for _, e in tool_errors.iterrows():
        L.add(finding_id=L.new_id("Q2-TOOL"), question=QUESTION, category="billing_tool_" + e.kind,
              status=e.status, confidence=e.confidence, client=e.client, amount=e.amount,
              statement=f"{e.invoice_no}: tool says {e.tool_says}; truth: {e.truth}", calculation=e.how_we_know,
              sources=e._sources)

    # ---- money Axeom owes ----------------------------------------------------------
    owe = []
    for _, e in tool_errors[tool_errors.kind == "paid_twice"].iterrows():
        owe.append({"client": e.client, "amount": e.amount, "why": f"{e.invoice_no} was paid twice ({e.how_we_know})",
                    "status": "confirmed", "confidence": "high", "_sources": e._sources})
    for _, e in tool_errors[(tool_errors.kind == "invoice_for_cancelled_booking") & (tool_errors.amount > 0)].iterrows():
        owe.append({"client": e.client, "amount": e.amount,
                    "why": f"paid {e.invoice_no} for a shoot that was then cancelled and not rebooked within 30 "
                           f"days ({e.truth}); refund or credit",
                    "status": "inferred", "confidence": "medium", "_sources": e._sources})
    for _, e in tool_errors[(tool_errors.kind == "amount_differs_from_booking") & (tool_errors.amount > 0)].iterrows():
        paid = inv_state.loc[inv_state.invoice_no == e.invoice_no, "received"].iloc[0]
        if paid > 0:
            owe.append({"client": e.client, "amount": min(e.amount, paid),
                        "why": f"{e.invoice_no} charged {e.tool_says} but the booking price was {e.truth}; the "
                               f"client paid the higher amount",
                        "status": e.status, "confidence": e.confidence, "_sources": e._sources})
    for p in pay_state[(pay_state.unallocated > 0.005)].itertuples():
        ref = str(p.reference).strip().upper()
        if re.fullmatch(r"INV-\d+", ref) and ref in inv_by_no.index:
            continue     # already reported as paid twice
        owe.append({"client": who(p.client_id) or p.payer, "amount": p.unallocated,
                    "why": f"payment {p.payment_id} ({p.amount_value:.2f}, ref '{p.reference}') could not be matched "
                           f"to an open invoice; unallocated credit until explained",
                    "status": "unresolved", "confidence": "low", "_sources": [src("payments", p.payment_id, "payment")]})
    we_owe = pd.DataFrame(owe, columns=["client", "amount", "why", "status", "confidence", "_sources"])
    for _, o in we_owe.iterrows():
        L.add(finding_id=L.new_id("Q2-WEOWE"), question=QUESTION, category="we_owe", status=o.status,
              confidence=o.confidence, client=o.client, amount=o.amount, statement=o.why,
              calculation="see sources", sources=o._sources)

    # ---- payables: incurred cost estimates ---------------------------------------
    payables = _payables(ctx)

    summary = {
        "overdue_total": round(overdue.balance.sum(), 2),
        "overdue_confirmed_total": round(overdue[overdue.status == "confirmed"].balance.sum(), 2),
        "receivable_not_yet_due": round(not_due.balance.sum(), 2),
        "never_billed_total": round(never_billed.amount.fillna(0).sum(), 2),
        "we_owe_total": round(we_owe[we_owe.status != "unresolved"].amount.sum(), 2),
        "unallocated_payments_total": round(pay_state.unallocated.clip(lower=0).sum(), 2),
        "invoiced_total_excl_void": round(inv[inv.tool_status != "void"].amount_value.sum(), 2),
        "collected_total": round(pay.amount_value.sum(), 2),
    }
    L.add(finding_id=L.new_id("Q2-TOTAL"), question=QUESTION, category="overdue_total",
          status="inferred" if (overdue.status != "confirmed").any() else "confirmed", confidence="high",
          amount=summary["overdue_total"],
          statement=f"overdue total {summary['overdue_total']:.2f} across {len(overdue)} invoices "
                    f"({summary['overdue_confirmed_total']:.2f} confirmed by both sources)",
          calculation="sum of open balances (amount - matched payments) of invoices due before "
                      f"{today.date()}; each payment counted once",
          sources=[src("invoices", i, "overdue invoice") for i in overdue.invoice_no])
    L.add(finding_id=L.new_id("Q2-TOTAL"), question=QUESTION, category="never_billed_total", status="inferred",
          confidence="medium", amount=summary["never_billed_total"],
          statement=f"never billed total {summary['never_billed_total']:.2f} ({len(never_billed)} items)",
          calculation="sum of booking prices (or the client's usual retainer for a retainer month)",
          sources=[src("bookings", b, "unbilled booking") for b in never_billed.booking_id])
    return FinanceResult(allocations=alloc, invoices=inv_state, payments=pay_state, booking_links=blinks,
                         overdue=overdue, receivable_not_due=not_due, we_owe=we_owe, never_billed=never_billed,
                         tool_errors=tool_errors, payables=payables, summary=summary)


def _payables(ctx: Context) -> pd.DataFrame:
    """Costs Axeom has incurred, estimated from team rates, editor hours and studio hours.
    There are no payroll, bill or payment-out records, so NONE of this can be shown
    to be outstanding: it is an estimate of cost incurred, not a recorded obligation."""
    from ..normalizer import parse_rate
    f = ctx.frames
    rows = []
    team = f.get("team")
    prod = f.get("production")
    if team is not None and prod is not None:
        rate = dict(zip(team.name, team.pay_rate))
        unit = dict(zip(team.name, team.pay_unit))
        joined = dict(zip(team.name, team.joined))
        for ed, grp in prod.groupby("editor"):
            hours = grp.editor_hours_value.sum()
            r = rate.get(ed)
            rows.append({"item": f"editor time: {ed}", "basis": f"{hours:.1f} editor_hours on {len(grp)} videos "
                         f"x ${r:.0f}/{unit.get(ed, '')}" if r == r and r is not None else f"{hours:.1f} hours; no rate",
                         "estimated_cost": round(hours * r, 2) if r == r and r is not None else None,
                         "recorded_obligation": False,
                         "note": f"{ed} joined {joined.get(ed, '?')}; no record of what was already paid"})
    costs = f.get("costs")
    bk = f.get("bookings")
    if costs is not None and bk is not None:
        studio = costs[costs.item.str.lower().str.contains("studio")]
        if len(studio):
            val, u, st = parse_rate(studio.cost.iloc[0])
            note = studio.note.iloc[0]
            hours_rule = {m.group(1).strip().lower(): float(m.group(2))
                          for m in re.finditer(r"([A-Za-z ]+?)\s+(?:uses\s+)?(\d+)(?:\s*hours)?(?:,|$)", note)}
            done = bk[(bk.status_norm == "completed") & (bk.date_date <= ctx.today)]
            hrs = 0.0
            unknown = 0
            for s in done.service:
                m = re.search(r"(\d+)\s*hr", s.lower())
                h = float(m.group(1)) if m else hours_rule.get(s.lower())
                if h is None:
                    unknown += 1
                else:
                    hrs += h
            rows.append({"item": "studio time", "basis": f"{hrs:.0f} studio hours on {len(done)} completed bookings x "
                         f"${val:.0f}/{u} (hours per service from costs.csv)" + (f"; {unknown} bookings without "
                                                                                 f"an hours rule" if unknown else ""),
                         "estimated_cost": round(hrs * val, 2) if st == "ok" else None,
                         "recorded_obligation": False,
                         "note": "own studio cost per hour; not a bill from a supplier"})
    return pd.DataFrame(rows, columns=["item", "basis", "estimated_cost", "recorded_obligation", "note"])
