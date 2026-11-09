"""Runs app.py itself (against tests/fake_streamlit.py) and taps through it.

Covers what the challenge checks on the live link: the first screen says
what to do first, every page renders, answers are recorded and nothing is
sent, new files update everything, uploads can be undone, and an identity
answer re-links the clients.
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from fake_streamlit import FakeStreamlit, Upload  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(__file__))
APP = os.path.join(ROOT, "app.py")


@pytest.fixture
def st(tmp_path, monkeypatch):
    fake = FakeStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    monkeypatch.setenv("AXEOM_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(ROOT)
    return fake


def _page(st, name):
    st.session_state["page"] = name
    return st.run(APP)


def _number(st, label):
    for kind, t in st.shown:
        if kind == "metric" and str(t).startswith(label + ":"):
            return t.split(": ", 1)[1]
    raise AssertionError(f"metric {label} not shown")


def test_today_says_what_to_do_first(st):
    st.run(APP)
    shown = [t for k, t in st.shown if k == "markdown"]
    first = shown.index("##### Do this first")
    assert "Juniper Wealth Partners" in shown[first + 1]           # the card right under "Do this first"
    assert any(str(t).startswith("##### Waiting for your OK") for t in shown)
    assert "Suggestions only. Nothing is sent" in st.text()
    assert "first|Yes" in st.buttons                                # answer the call right on the card


@pytest.mark.parametrize("page", ["Today", "Clients", "Money", "Edits", "Leads", "Add files"])
def test_every_page_renders(st, page):
    _page(st, page)
    assert not [t for k, t in st.shown if k == "error"]
    if page in ("Money", "Edits"):
        assert any(k == "chart" for k, _ in st.shown)


def test_yes_records_the_answer_and_only_drafts_the_email(st):
    st.run(APP)
    key = next(k for k in st.buttons if k.startswith("today|remind:") and k.endswith("|Yes"))
    inv = key.split("remind:")[1].split("|")[0]
    st.tap(key, APP)
    log = pd.read_csv(os.path.join(os.environ["AXEOM_STATE_DIR"], "decisions.csv"), dtype=str)
    assert list(log.id) == [f"remind:{inv}"] and list(log.answer) == ["Yes"]
    drafts = [t for k, t in st.shown if k == "code"]
    assert drafts and inv in drafts[0]                              # the draft is shown to copy
    assert any(k == "link_button" and "mailto:" in t for k, t in st.shown)   # opens HIS mail app
    assert f"today|remind:{inv}|Yes" not in st.buttons              # no longer waiting
    st.tap("undo_last", APP)                                        # and can be undone
    assert f"today|remind:{inv}|Yes" in st.buttons or any(
        f"remind:{inv}" in k for k in st.buttons)


def test_upload_updates_everything_and_can_be_undone(st):
    _page(st, "Money")
    assert _number(st, "Overdue") == "9,800"
    pay = (b"payment_id,date,payer,payer_email,amount,method,reference\n"
           b"P9001,05/10/2026,Stonebridge RG,nolan@stonebridgerg.com,1500.00,ACH,INV-1094\n")
    st.uploads = [Upload("payments_new.csv", pay)]
    _page(st, "Add files")
    st.tap("Add these files", APP)
    res = st.session_state["upload_result"]
    assert res["batch"] == 1 and int(res["table"].inserted.sum()) == 1
    diff = res["diff"].set_index("what")
    assert diff.loc["Overdue (owed to us)", "before"] == 9800 and diff.loc["Overdue (owed to us)", "after"] == 8300
    _page(st, "Money")
    assert _number(st, "Overdue") == "8,300"
    assert not any("remind:INV-1094" in k for k in st.buttons)    # the reminder disappeared
    # the same file again is not counted twice
    _page(st, "Add files")
    st.tap("Add these files", APP)
    assert st.session_state["upload_result"]["diff"].changed.sum() == 0
    # undo both uploads
    _page(st, "Add files")
    st.tap("Undo the last upload", APP)
    st.tap("Undo the last upload", APP)
    _page(st, "Money")
    assert _number(st, "Overdue") == "9,800"


def test_a_file_the_engine_cannot_read_is_not_kept(st):
    st.uploads = [Upload("notes.csv", b"foo,bar\n1,2\n")]
    _page(st, "Add files")
    st.tap("Add these files", APP)
    res = st.session_state["upload_result"]
    assert res["batch"] is None
    assert any(k == "warning" for k, _ in st.shown)
    assert not os.listdir(os.path.join(os.environ["AXEOM_STATE_DIR"], "uploads")) or \
        not os.path.exists(os.path.join(os.environ["AXEOM_STATE_DIR"], "uploads", "manifest.json"))


def test_identity_answer_relinks_clients(st):
    _page(st, "Clients")
    before = len([k for k in st.buttons])
    key = next(k for k in st.buttons if k.startswith("ident|identity:") and "stonebridge" in k
               and k.endswith("|Same client"))
    st.tap(key, APP)
    labels = st.session_state.get("client_label")
    assert labels is not None
    caption = [t for k, t in st.shown if k == "markdown" and "Check these matches" in str(t)]
    assert not caption or "(1)" in caption[0]                      # one question left (Sofia Khan)
    from axeom.workspace import Workspace
    snap = Workspace().build()
    assert len(snap.analysis.clients.clients) == 29                 # the two Stone Bridge clients are one now
    st.tap("undo_last", APP)
    assert len(Workspace().build().analysis.clients.clients) == 30
    assert before
