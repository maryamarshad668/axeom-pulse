"""Phase 2: the six answers.

Two kinds of tests:
* on the original Pack 208 data (facts were checked by hand against the CSV
  files, see the comments), and
* on MINI_PACK (conftest.py), a tiny hand-made pack whose right answers are
  known by construction - this checks the rules themselves.
Totals are also re-computed independently from the raw CSV text.
"""
import csv
import json
import os
from datetime import date

import pandas as pd
import pytest

from conftest import ORIGINAL

REF = date(2026, 10, 6)


def raw(name):
    with open(os.path.join(ORIGINAL, name), encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------- Q1 clients
def test_client_dedup_original(analysis):
    c = analysis.clients.clients
    assert c.client_id.is_unique and c.canonical_name.is_unique
    # every email belongs to exactly one client
    emails = [e for lst in c.emails for e in lst]
    assert len(emails) == len(set(emails))
    # rename announced by the client joins two emails into one client
    pecan = c[c.emails.map(lambda e: "trent@pecangym.co" in e)]
    assert len(pecan) == 1 and "trent@pecanathletics.com" in pecan.emails.iloc[0]
    # same contact name, different businesses: kept apart
    hale = c[c.emails.map(lambda e: "hello@haledental.com" in e)]
    assert "alana@kindredmktg.com" not in hale.emails.iloc[0]
    # 30 = identities with own bookings/invoices/videos (32) - Kindred (payer) - Pecan merge (1)
    assert len(c) == 30


def test_payer_is_not_a_client(analysis):
    p = analysis.clients.payers
    assert list(p["name"]) == ["Kindred Creative"]
    assert set(p.pays_for.iloc[0]) == {"Sunset Pediatrics", "Luna Physical Therapy"}
    assert "Kindred Creative" not in set(analysis.clients.clients.canonical_name)
    links = analysis.clients.record_links
    # bookings made under the agency's email for its customer go to the customer
    b144 = links[(links.source == "bookings") & (links.record_id == "B144")].iloc[0]
    assert b144.client == "Sunset Pediatrics" and b144.reassigned_because


def test_mini_pack_clients(mini_analysis):
    c = mini_analysis.clients.clients
    assert set(c.canonical_name) == {"Ann Bakery", "Ray Plumbing"}     # lead-only and payer-only excluded


# ---------------------------------------------------------------- Q2 money
def test_every_payment_used_at_most_once(analysis):
    f = analysis.finance
    per = f.allocations.groupby("payment_id").amount.sum()
    amounts = f.payments.set_index("payment_id").amount_value
    assert (per <= amounts.loc[per.index] + 0.005).all()
    assert abs(f.payments.allocated.sum() + f.payments.unallocated.sum() - f.payments.amount_value.sum()) < 0.01


def test_independent_balance_identity(analysis):
    """invoiced - collected (straight from the raw CSV text) must equal
    open invoice balances - unallocated payments from the reconciliation."""
    inv = raw("invoices.csv")
    pay = raw("payments.csv")
    invoiced = sum(float(r["amount"]) for r in inv if r["status"] != "void")
    collected = sum(float(r["amount"]) for r in pay)
    f = analysis.finance
    # an invoice for a cancelled booking whose payment moved to the rescheduled
    # shoot is not collectable on its own (the rescheduled shoot's invoice is)
    moved = f.invoices[f.invoices.true_status == "billed_cancelled_booking"]
    assert list(moved.invoice_no) == ["INV-1137"]
    collectable = invoiced - moved.amount.sum()
    open_bal = f.invoices.balance.sum()
    assert abs((collectable - collected) - (open_bal - f.payments.unallocated.sum())) < 0.01
    assert f.summary["invoiced_total_excl_void"] == pytest.approx(invoiced)
    assert f.summary["collected_total"] == pytest.approx(collected)


def test_overdue_list_checked_against_raw_files(analysis):
    inv = {r["invoice_no"]: r for r in raw("invoices.csv")}
    pay = raw("payments.csv")
    ov = analysis.finance.overdue
    assert analysis.finance.summary["overdue_total"] == pytest.approx(ov.balance.sum())
    for r in ov.itertuples():
        src = inv[r.invoice_no]
        assert date.fromisoformat(src["due"]) < REF
        assert r.days_late == (REF - date.fromisoformat(src["due"])).days
        refs = [p for p in pay if p["reference"].strip() == r.invoice_no]
        explicit = sum(float(p["amount"]) for p in refs)
        a = analysis.finance.allocations
        other = a[(a.invoice_no == r.invoice_no) & (a.method != "explicit_reference")]
        assert explicit + other.amount.sum() == pytest.approx(r.received)
    # INV-1123's 450 came from P5114 'half now half later' (no invoice number on it)
    a = analysis.finance.allocations
    assert list(a[a.invoice_no == "INV-1123"].payment_id) == ["P5114"]
    # unpaid in the tool, but a payment names it -> must NOT be overdue
    for no in ("INV-1082", "INV-1134"):
        assert no not in set(ov.invoice_no)
    # 'july sessions' 900 covers the three July Calm & Collected invoices
    for no in ("INV-1064", "INV-1071", "INV-1084"):
        assert no not in set(ov.invoice_no)
    # tool says paid but no bank payment can be matched -> inferred, not confirmed
    jr = ov[ov.invoice_no == "INV-1099"].iloc[0]
    assert jr.status == "inferred" and jr.tool_status == "paid"


def test_partial_payment(analysis, mini_analysis):
    f = analysis.finance
    r = f.invoices[f.invoices.invoice_no == "INV-1123"].iloc[0]   # 900, P5114 'half now half later' 450
    assert r.true_status == "partial" and r.balance == 450
    m = mini_analysis.finance.invoices.set_index("invoice_no")
    assert m.loc["INV-2", "true_status"] == "partial" and m.loc["INV-2", "balance"] == 450
    assert m.loc["INV-2", "days_late"] == (REF - date(2026, 6, 27)).days


def test_never_matched_on_amount_alone(mini_analysis):
    """P3 is 900 from an unrelated payer; INV-2 is an open 900 invoice of another client."""
    f = mini_analysis.finance
    assert "P3" not in set(f.allocations.payment_id)
    p3 = f.payments.set_index("payment_id").loc["P3"]
    assert p3.unallocated == 900
    owe = f.we_owe
    assert ((owe.status == "unresolved") & owe.why.str.contains("P3")).any()


def test_unallocated_and_double_payment(analysis):
    f = analysis.finance
    un = f.payments[f.payments.unallocated > 0.005]
    assert list(un.payment_id) == ["P5076"]                      # second payment of INV-1080
    e = f.tool_errors[f.tool_errors.kind == "paid_twice"]
    assert list(e.invoice_no) == ["INV-1080"]
    assert ((f.we_owe.amount == 900) & f.we_owe.why.str.contains("INV-1080")).any()


def test_explicit_references_are_respected(analysis):
    pay = {r["payment_id"]: r["reference"].strip() for r in raw("payments.csv")}
    a = analysis.finance.allocations
    for r in a[a.method == "explicit_reference"].itertuples():
        assert pay[r.payment_id] == r.invoice_no


def test_inferred_matches_stay_within_the_client(analysis):
    f = analysis.finance
    inv_client = f.invoices.set_index("invoice_no").client_id
    pay_client = f.payments.set_index("payment_id").client_id
    pays_for = analysis.clients.pays_for
    for r in f.allocations[f.allocations.method != "explicit_reference"].itertuples():
        pc, ic = pay_client[r.payment_id], inv_client[r.invoice_no]
        assert ic == pc or ic in pays_for.get(pc, set())


def test_completed_but_unbilled(analysis, mini_analysis):
    nb = analysis.finance.never_billed
    inv = raw("invoices.csv")
    for r in nb[nb.booking_or_month.str.startswith("B")].itertuples():
        assert not any(r.booking_id in i["memo"] for i in inv)          # no invoice cites the booking
    # retainer shoots are only unbilled when no 'Monthly retainer <month>' invoice exists
    for r in nb[~nb.booking_or_month.str.startswith("B")].itertuples():
        y, m = map(int, r.booking_or_month.split("-"))
        month = date(y, m, 1).strftime("%B")
        emails = {b["email"] for b in raw("bookings.csv") if b["booking_id"] == r.booking_id}
        assert not any(i["email"] in emails and f"retainer {month}".lower() in i["memo"].lower() for i in inv)
    retainer_clients_billed = {"Juniper Wealth Partners", "Maple Chiropractic"}
    assert not (set(nb.client) & retainer_clients_billed)
    m = mini_analysis.finance.never_billed
    assert list(m.booking_id) == ["B2"]                                 # B4 retainer covered by INV-3


def test_missing_amount_is_not_zero(mini_analysis):
    r = mini_analysis.finance.invoices.set_index("invoice_no").loc["INV-4"]
    assert r.true_status == "unknown_amount" and r.balance == 0
    assert "INV-4" not in set(mini_analysis.finance.overdue.invoice_no)
    q = mini_analysis.ctx.model.quality
    assert ((q.record_id == "INV-4") & (q.issue_type == "missing_amount")).any()


def test_duplicate_records(analysis):
    te = analysis.finance.tool_errors
    dup = te[te.kind == "duplicate_invoice"].iloc[0]
    assert "INV-1065" in dup.tool_says and "INV-1155" in dup.tool_says and "voided" in dup.truth


def test_we_owe_and_tool_errors(analysis):
    owe = analysis.finance.we_owe
    confirmed = owe[owe.status == "confirmed"]
    assert set(confirmed.client) == {"Riverbend Barbershop", "Silverleaf Church"}
    # Stone Bridge asked to MOVE the cancelled shoot ("we already paid") and it
    # happened on B155: no refund is owed, the payment covers the new shoot
    assert "Stone Bridge Realty" not in set(owe.client)
    te = analysis.finance.tool_errors.set_index("invoice_no")
    assert te.loc["INV-1043", "kind"] == "amount_differs_from_booking"      # 25% discount not applied
    assert te.loc["INV-1137", "kind"] == "invoice_for_cancelled_booking"
    assert "B155" in te.loc["INV-1137", "truth"]
    assert te.loc["INV-1145", "kind"] == "status_wrong_unpaid"            # bills the moved shoot again
    a = analysis.finance.allocations
    moved = a[a.method == "moved_with_reschedule"]
    assert list(moved.payment_id) == ["P5122"] and list(moved.invoice_no) == ["INV-1145"]
    assert (moved.confidence == "high").all()                          # client message supports it
    assert te.loc["INV-1060", "status"] == "unresolved"                     # unexplained, not called an error


def test_payables_are_estimates_not_debts(analysis):
    p = analysis.finance.payables
    assert len(p) and not p.recorded_obligation.any()


# ---------------------------------------------------------------- Q3 requests
def test_lost_request_detection(analysis, mini_analysis):
    r = analysis.comms.requests
    assert set(r.classification) <= {"completed", "delayed_or_unresolved", "confirmed_lost", "unmatched"}
    lost = r[r.classification == "confirmed_lost"]
    prod = {v["video_id"]: v for v in raw("production_board.csv")}
    for x in lost.itertuples():
        assert prod[x.video_id]["notes_on_board"].lower() != x.expected_note.lower()
    done = r[r.classification == "completed"]
    for x in done.itertuples():
        assert prod[x.video_id]["notes_on_board"].lower() == x.expected_note.lower()
    assert analysis.comms.worse_since == "2026-07"
    m = mini_analysis.comms.requests.set_index("came_by")
    assert m.loc["email", "classification"] == "completed"
    assert m.loc["voice note", "classification"] == "confirmed_lost"
    assert m.loc["whatsapp", "classification"] == "unmatched"       # no video named: not called lost


def test_unmatched_is_never_counted_as_lost(analysis):
    r = analysis.comms.requests
    assert (r[r.classification == "unmatched"].video_id == "").all()


def test_never_replied_leads(analysis, mini_analysis):
    nr = analysis.comms.never_replied_leads
    blanks = {r["lead_id"] for r in raw("leads.csv") if not r["first_reply"].strip()}
    assert set(nr.lead_id) == blanks
    assert set(nr[nr.likely_junk].lead_id) == {"L012", "L027", "L078", "L097"}
    assert list(mini_analysis.comms.never_replied_leads.query("not likely_junk").lead_id) == ["L3"]


# ---------------------------------------------------------------- Q4 churn
def test_churn_is_explained(analysis):
    ml = analysis.churn.most_likely
    sig = analysis.churn.signals
    mine = sig[(sig.client == ml["client"]) & (sig.points > 0)]
    assert ml["risk_points"] > 0 and len(mine) == len(ml["evidence"])
    assert mine.record_id.str.len().gt(0).all()
    lost = analysis.churn.already_lost
    assert "Maple Chiropractic" in set(lost.client)
    assert ml["client"] not in set(lost.client)


# ---------------------------------------------------------------- Q5 marketing
def test_marketing_conversion(analysis, mini_analysis):
    ch = analysis.marketing.channels.set_index("channel")
    assert (ch.conversion_rate == (ch.converted_clients / ch.unique_prospects).round(3)).all()
    assert ch.converted_clients.sum() == len(analysis.clients.clients) - \
        len(set(analysis.clients.clients.client_id) - set(analysis.marketing.client_value.client_id))
    assert (ch.true_profit == "cannot be calculated").all()
    leads = raw("leads.csv")
    assert ch.leads.sum() == sum(1 for l in leads if l["phone"].strip() or l["business"].strip())
    m = mini_analysis.marketing.channels.set_index("channel")
    assert m.loc["Referral", "conversion_rate"] == 1.0
    assert m.loc["Instagram", "unique_prospects"] == 2 and m.loc["Instagram", "converted_clients"] == 1


# ---------------------------------------------------------------- Q6 + outputs
def test_three_priorities(analysis):
    acts = analysis.priorities.actions
    assert len(analysis.priorities.top3_text) == 3
    assert acts[0]["concerns"] == analysis.churn.most_likely["client"]
    for a in acts[:3]:
        assert a["evidence"] and a["why_first"] and a["benefit"]


def test_evidence_ledger_traces_findings(analysis):
    led = analysis.ledger
    assert set(led.status) <= {"confirmed", "inferred", "unresolved"}
    money = led[led.category.isin(["overdue", "never_billed", "we_owe"])]
    assert (money.record_id != "").all() and (money.source_file != "").all()
    for inv in analysis.finance.overdue.invoice_no:
        assert ((led.category == "overdue") & (led.record_id == inv)).any()


def test_answers_json_matches_template(analysis, tmp_path):
    from axeom.analysis.reports import build_answers, write_all
    tpl_path = os.path.join(os.path.dirname(ORIGINAL), "answers_template.json")
    ans = build_answers(analysis, tpl_path)
    tpl = json.load(open(tpl_path))
    assert list(ans) == list(tpl)
    assert ans["overdue_total"] == pytest.approx(sum(x["amount_owed"] for x in ans["overdue"]))
    assert ans["never_billed_total"] == pytest.approx(sum(x["amount"] for x in ans["never_billed"]))
    assert ans["client_count"] == len(ans["clients"])
    allowed = {"email", "whatsapp", "voice note", "instagram"}
    assert {x["came_by"] for x in ans["lost_requests"]} <= allowed
    assert ans["live_link"] == "" and ans["video_link"] == ""          # do not exist yet
    paths = write_all(analysis, str(tmp_path), tpl_path)
    json.load(open(paths["answers.json"]))                              # valid JSON on disk
    for name in ("evidence_ledger.csv", "financial_reconciliation.csv", "client_identity_report.csv",
                 "lost_requests_report.csv", "churn_analysis.csv", "marketing_analysis.csv"):
        assert len(pd.read_csv(paths[name])) > 0


def test_original_files_untouched(original_dir):
    """The pack files are only read. Their checksums were recorded when the project was created."""
    import hashlib
    manifest = os.path.join(os.path.dirname(original_dir), "original_checksums.sha1")
    for line in open(manifest):
        digest, name = line.split()
        with open(os.path.join(original_dir, name), "rb") as fh:
            assert hashlib.sha1(fh.read()).hexdigest() == digest, name


def test_analysis_updates_after_an_upload(fresh_store):
    """New files flow through to the answers: a new overdue invoice and its client appear."""
    from axeom import build_model
    from axeom.analysis import run_all
    before = run_all(build_model(fresh_store.raw))
    fresh_store.ingest([
        ("bookings_new.csv", b"booking_id,date,client_name,email,service,status,price\n"
                             b"B901,09/01/2026,Brand New Bakery,owner@brandnewbakery.com,Reels Pack,completed,$900\n"),
        ("invoices_new.csv", b"invoice_no,bill_to,email,issued,due,amount,status,memo\n"
                             b"INV-9001,Brand New Bakery,owner@brandnewbakery.com,2026-09-02,2026-09-16,900.00,"
                             b"unpaid,B901 Reels Pack\n")])
    after = run_all(build_model(fresh_store.raw))
    assert len(after.clients.clients) == len(before.clients.clients) + 1
    assert "INV-9001" in set(after.finance.overdue.invoice_no)
    assert after.finance.summary["overdue_total"] == before.finance.summary["overdue_total"] + 900
