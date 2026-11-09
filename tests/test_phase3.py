"""Phase 3 building blocks: workspace (uploads), suggestions (yes/no) and views."""
import ast
import os

import pandas as pd
import pytest

from axeom import views
from axeom.suggestions import DecisionLog, build_suggestions, split
from axeom.workspace import Workspace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGINAL = os.path.join(ROOT, "data", "original")


@pytest.fixture(scope="module")
def sugg(analysis):
    return build_suggestions(analysis)


# ----------------------------------------------------------------- suggestions
def test_suggestion_ids_are_unique_and_stable(analysis, sugg):
    ids = [s.id for s in sugg]
    assert len(ids) == len(set(ids))
    assert ids == [s.id for s in build_suggestions(analysis)]          # same data -> same ids, same order


def test_first_suggestion_is_the_call_then_saveable_edits(sugg):
    assert sugg[0].kind == "call" and sugg[0].client == "Juniper Wealth Partners"
    assert [s.kind for s in sugg[1:3]] == ["save_edit", "save_edit"]


def test_every_overdue_invoice_gets_a_reminder_longest_first(analysis, sugg):
    rem = [s for s in sugg if s.kind == "remind"]
    assert {s.id.split(":")[1] for s in rem} == set(analysis.finance.overdue.invoice_no)
    late = analysis.finance.overdue.set_index("invoice_no").days_late
    assert [late[s.id.split(":")[1]] for s in rem] == sorted(late, reverse=True)
    assert abs(sum(s.amount for s in rem) - analysis.finance.summary["overdue_total"]) < 0.01


def test_reminder_to_the_client_at_risk_says_call_first(sugg):
    juniper = next(s for s in sugg if s.id == "remind:INV-1119")
    assert "call" in juniper.why.lower()


def test_disputed_invoice_says_check_first(sugg):
    s = next(s for s in sugg if s.id == "remind:INV-1099")             # tool says paid, bank says no
    assert "check first" in s.why.lower()


def test_money_we_owe_offers_refund_or_credit(sugg):
    owe = [s for s in sugg if s.kind == "we_owe"]
    assert sorted(s.amount for s in owe) == [75, 900]
    assert all(s.options == ["Refund", "Keep as credit", "No"] for s in owe)
    assert all(set(s.drafts) == {"Refund", "Keep as credit"} for s in owe)


def test_unbilled_work_and_tool_fixes(analysis, sugg):
    assert abs(sum(s.amount for s in sugg if s.kind == "bill") - analysis.finance.summary["never_billed_total"]) < 0.01
    tool = {s.id.split(":")[1] for s in sugg if s.kind == "fix_tool"}
    assert "INV-1155" not in tool                       # the duplicate is already voided: nothing to do
    assert "INV-1080" not in tool                       # handled by the refund question
    assert {"INV-1099", "INV-1145", "INV-1060"} <= tool


def test_leads_exclude_spam_and_the_client_already_being_called(analysis, sugg):
    leads = {s.id.split(":")[1] for s in sugg if s.kind == "reply_lead"}
    nr = analysis.comms.never_replied_leads
    assert not leads & set(nr[nr.likely_junk].lead_id)
    assert "L098" not in leads                          # Juniper's lead: covered by the call
    assert len(leads) == 15


def test_identity_questions_only_for_open_items(sugg):
    q = [s for s in sugg if s.kind == "identity"]
    assert len(q) == 2
    assert all(s.options == ["Same client", "Different"] and s.identity["value_type"] == "email" for s in q)
    assert not any("kindred" in s.id for s in q)        # payer questions were resolved in Phase 2


def test_mailto_opens_the_owners_mail_app_with_the_draft(sugg):
    s = next(s for s in sugg if s.kind == "remind")
    url = s.mailto()
    assert url.startswith(f"mailto:{s.to}?subject=") and "%0A" in url and " " not in url


def test_nothing_in_phase3_can_send_anything():
    """No mail, HTTP or messaging library is imported by the app or its modules."""
    banned = {"smtplib", "requests", "urllib.request", "http.client", "twilio", "socket", "email.mime"}
    for path in ["app.py", "axeom/suggestions.py", "axeom/workspace.py", "axeom/views.py", "axeom/charts.py"]:
        tree = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8").read())
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
                {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not names & banned, (path, names & banned)


def test_decision_log_answer_undo_and_later(tmp_path, sugg):
    log = DecisionLog(str(tmp_path / "d.csv"))
    a, b = sugg[3], sugg[4]
    log.answer(a, a.options[0])
    log.answer(b, "Later")
    log.answer(a, a.options[-1])                         # a new answer replaces the old one
    assert log.answers() == {a.id: a.options[-1], b.id: "Later"}
    waiting, done = split(sugg, log.answers())
    assert waiting[-1].id == b.id                        # "Later" goes to the end of the list
    assert [s.id for s, _ in done] == [a.id]
    log.undo(a.id)
    assert a.id not in log.answers()
    with pytest.raises(ValueError):
        log.answer(a, "Maybe")


# ----------------------------------------------------------------- workspace
PAY = (b"payment_id,date,payer,payer_email,amount,method,reference\n"
       b"P9001,05/10/2026,Stonebridge RG,nolan@stonebridgerg.com,1500.00,ACH,INV-1094\n")


def test_workspace_saves_checks_and_undoes_uploads(tmp_path):
    ws = Workspace(state_dir=str(tmp_path))
    fp0 = ws.fingerprint()
    batch, rejected = ws.add_upload([("new pay?.csv", PAY), ("readme.csv", b"a,b\n1,2\n")])
    assert batch["batch"] == 1 and [f["name"] for f in batch["files"]] == ["new pay_.csv"]   # safe file name
    assert len(rejected) == 1 and not rejected[0].accepted
    assert ws.fingerprint() != fp0
    snap = ws.build()
    assert snap.analysis.finance.summary["overdue_total"] == 8300
    assert snap.batch_reports[1][0].inserted == 1
    ws.undo_last()
    assert ws.batches() == [] and ws.fingerprint() == fp0


def test_later_upload_corrects_an_earlier_one(tmp_path):
    ws = Workspace(state_dir=str(tmp_path))
    ws.add_upload([("p.csv", PAY)])
    ws.add_upload([("p_fix.csv", PAY.replace(b"1500.00", b"1000.00"))])         # same id, corrected amount
    snap = ws.build()
    pay = snap.store.raw["payments"]
    assert (pay.payment_id == "P9001").sum() == 1 and pay[pay.payment_id == "P9001"].amount.iloc[0] == "1000.00"
    assert snap.analysis.finance.summary["overdue_total"] == 8800
    assert snap.batch_reports[2][0].updated == 1


def test_identity_answer_is_applied_on_rebuild_and_reset_clears_it(tmp_path):
    ws = Workspace(state_dir=str(tmp_path))
    ws.add_identity_decision("email", "nolan@stonebridgerg.com", "nadia@stonebridgerealty.com", "same", "test")
    assert len(ws.build().analysis.clients.clients) == 29
    ws.reset()
    assert ws.identity_decisions().empty and len(ws.build().analysis.clients.clients) == 30
    with pytest.raises(ValueError):
        ws.add_identity_decision("email", "a@b.com", "c@d.com", "maybe")


def test_original_files_are_never_touched(tmp_path):
    before = {f: os.path.getmtime(os.path.join(ORIGINAL, f)) for f in os.listdir(ORIGINAL)}
    ws = Workspace(state_dir=str(tmp_path))
    ws.add_upload([("p.csv", PAY)])
    ws.build()
    ws.reset()
    assert before == {f: os.path.getmtime(os.path.join(ORIGINAL, f)) for f in os.listdir(ORIGINAL)}


# ----------------------------------------------------------------- views
def test_key_numbers_match_the_analysis(analysis):
    k = views.key_numbers(analysis)
    s = analysis.finance.summary
    assert (k["Clients"], k["Overdue (owed to us)"], k["Never billed"], k["We owe clients"]) == \
        (30, s["overdue_total"], s["never_billed_total"], s["we_owe_total"])
    assert (k["Edit requests lost"], k["Edit requests at risk"], k["Leads never answered"]) == (39, 2, 16)


def test_today_has_three_cards_in_priority_order(analysis, sugg):
    t = views.today(analysis, sugg)
    assert [c["kind"] for c in t["cards"]] == ["churn", "edits", "money"]
    assert t["date"] == "Tuesday 06 October 2026"
    assert t["waiting"] == len(sugg)


def test_client_profile_connects_every_file(analysis):
    cid = analysis.clients.clients.set_index("canonical_name").loc["Juniper Wealth Partners", "client_id"]
    p = views.client_profile(analysis, cid)
    assert {k: len(v) for k, v in p["tables"].items() if k != "Edit requests"} == {
        "Invoices": 7, "Payments": 5, "Bookings": 7, "Videos": 24, "Messages": 19, "Leads": 2}
    assert p["money"]["Overdue"] == "2,500"
    names = p["facts"]["Also written as"].split(", ")
    assert len(names) == len({n.casefold() for n in names})        # no repeated spellings
    assert len(p["how_linked"]) == len(analysis.clients.record_links.query("client_id == @cid"))


def test_payer_and_paid_for_profiles(analysis):
    payer = analysis.clients.payers.iloc[0]
    p = views.client_profile(analysis, payer.client_id)
    assert p["is_payer"] and "Luna Physical Therapy" in p["facts"]["Pays for"]
    luna = analysis.clients.clients.set_index("canonical_name").loc["Luna Physical Therapy", "client_id"]
    assert views.client_profile(analysis, luna)["facts"]["Billed through"] == payer["name"]


def test_diff_numbers_marks_changes():
    d = views.diff_numbers({"a": 1, "b": 2}, {"a": 1, "b": 3})
    assert list(d[d.changed].what) == ["b"]


def test_charts_build(analysis):
    from axeom import charts
    assert charts.lost_by_month(views.edits(analysis)["by_month"]).to_json()
    assert charts.overdue_by_invoice(views.money(analysis)["overdue"]).to_json()


def test_streamlit_calls_match_streamlit_source():
    """Runs only where the Streamlit source is available (STREAMLIT_SRC=<path to streamlit package>)."""
    src = os.environ.get("STREAMLIT_SRC")
    if not src:
        pytest.skip("set STREAMLIT_SRC to the streamlit package folder to run this check")
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tests", "tools"))
    from check_streamlit_api import check
    assert check(os.path.join(ROOT, "app.py"), src) == []
