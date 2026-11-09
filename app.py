"""AXEOM PULSE - the owner's app (Phase 3).

Phone first. The "Today" screen answers "what do I do first?" in one look:
one thing to do now, two next, and a short list of yes/no questions
("INV-1094 is 42 days late. Send a reminder?"). Nothing is ever sent by the
app: "Yes" records the answer and shows a draft the owner sends himself.

All logic lives in the ``axeom`` package (tested without Streamlit):
  workspace.py    original pack + saved uploads -> engine -> analysis
  suggestions.py  the yes/no questions, drafts and the answer log
  views.py        what each page shows
  charts.py       two small Plotly charts
This file only lays them out.

Run:  streamlit run app.py
"""
from __future__ import annotations

import inspect
import json
import os

import pandas as pd
import streamlit as st

from axeom import views
from axeom.analysis.reports import _whole_numbers, build_answers
from axeom.data_quality import summarize
from axeom.ingestion import reports_table
from axeom.suggestions import GROUPS, DecisionLog, build_suggestions, split
from axeom.workspace import PROJECT, Workspace

st.set_page_config(page_title="Axeom Pulse", page_icon="✅", layout="centered",
                   initial_sidebar_state="collapsed")
st.markdown("""<style>
.block-container {padding-top: 1.5rem; padding-bottom: 3rem; max-width: 760px;}
div.stButton > button, div.stLinkButton > a, div.stDownloadButton > button {min-height: 2.75rem;}
</style>""", unsafe_allow_html=True)

PAGES = ["Today", "Clients", "Money", "Edits", "Leads", "Add files"]
PAGE_OF_KIND = {"churn": "Clients", "edits": "Edits", "money": "Money", "leads": "Leads"}
EMAIL_KINDS = {"remind", "we_owe", "reply_lead", "upsell"}
TEMPLATE = os.path.join(PROJECT, "data", "answers_template.json")
SHOW_FIRST = 5


# ------------------------------------------------------------------ helpers
def _kw(fn, **kw) -> dict:
    """Pass only the keyword arguments this Streamlit version accepts.
    stretch=True becomes width="stretch" (new) or use_container_width=True (older)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    out = {k: v for k, v in kw.items() if k in params}
    if kw.get("stretch"):
        if "width" in params:
            out["width"] = "stretch"
        elif "use_container_width" in params:
            out["use_container_width"] = True
    out.pop("stretch", None)
    return out


def esc(text) -> str:
    """Show text literally in markdown ($ would start maths, * and _ emphasis)."""
    return str(text).replace("\\", "\\\\").replace("$", "\\$").replace("*", "\\*").replace("_", "\\_")


@st.cache_resource
def workspace() -> Workspace:
    return Workspace()


@st.cache_resource(max_entries=4, show_spinner="Reading every file and linking the records...")
def snapshot(fingerprint: str):
    return workspace().build()


@st.cache_resource(max_entries=4)
def suggestions_for(fingerprint: str):
    return build_suggestions(snapshot(fingerprint).analysis)


@st.cache_data(max_entries=4, show_spinner=False)
def exports_for(fingerprint: str) -> dict:
    a = snapshot(fingerprint).analysis
    return {"answers": json.dumps(_whole_numbers(build_answers(a, TEMPLATE)), indent=2, ensure_ascii=False),
            "ledger": a.ledger.drop(columns=["_sources"], errors="ignore").to_csv(index=False)}


def answer_log() -> DecisionLog:
    return DecisionLog(os.path.join(workspace().state_dir, "decisions.csv"))


# ------------------------------------------------------------------ callbacks (run before the next rerun)
def go(page: str, client_label: str | None = None):
    st.session_state.page = page
    if client_label:
        st.session_state.client_label = client_label


def answer(s, option: str):
    answer_log().answer(s, option)
    if s.kind == "identity" and option == "Same client":
        workspace().add_identity_decision(**s.identity, decision="same", note=f"answered in app: {s.question}")
    st.session_state.last = {"s": s, "option": option}


def undo(s):
    answer_log().undo(s.id)
    if s.kind == "identity":
        workspace().remove_identity_decision(s.identity["value"], s.identity["target_email"])
    st.session_state.last = None


def close_last():
    st.session_state.last = None


def undo_upload():
    workspace().undo_last()
    st.session_state.upload_result = None


def reset_all():
    workspace().reset()
    st.session_state.upload_result = None


# ------------------------------------------------------------------ pieces
def show_suggestion(s, where: str):
    with st.container(border=True):
        st.markdown(f"**{esc(s.question)}**")
        st.caption(esc(s.why))
        opts = list(s.options) + ["Later"]
        cols = st.columns(len(opts))
        for i, opt in enumerate(opts):
            cols[i].button(opt, key=f"{where}|{s.id}|{opt}", on_click=answer, args=(s, opt),
                           **_kw(st.button, type="primary" if i == 0 else "secondary", stretch=True))
        if s.evidence:
            with st.expander("Why? Show the records"):
                for e in s.evidence:
                    st.markdown(f"- {esc(e)}")


def show_last():
    last = st.session_state.get("last")
    if not last:
        return
    s, opt = last["s"], last["option"]
    with st.container(border=True):
        st.success(f"Noted: **{esc(opt)}** - {esc(s.question)}")
        text = s.drafts.get(opt)
        if text:
            st.caption("Nothing was sent. Copy this, or open it in your own email app:"
                       if s.kind in EMAIL_KINDS else "Nothing was changed for you. The change to make:")
            st.code(text, **_kw(st.code, language=None, wrap_lines=True))
            if s.to and s.kind in EMAIL_KINDS:
                st.link_button("Open in my email app", s.mailto(opt), **_kw(st.link_button, stretch=True))
        c1, c2 = st.columns(2)
        c1.button("Undo this answer", key="undo_last", on_click=undo, args=(s,), **_kw(st.button, stretch=True))
        c2.button("OK", key="close_last", on_click=close_last, **_kw(st.button, stretch=True))


def show_list(items: list, where: str, first: int = SHOW_FIRST):
    for s in items[:first]:
        show_suggestion(s, where)
    if len(items) > first:
        with st.expander(f"{len(items) - first} more"):
            for s in items[first:]:
                show_suggestion(s, where + "+")


def table(df: pd.DataFrame, title: str, expanded: bool = False, note: str = ""):
    with st.expander(f"{title} ({len(df)})", expanded=expanded):
        if note:
            st.caption(note)
        if len(df):
            st.dataframe(df, hide_index=True)
        else:
            st.write("None.")


# ------------------------------------------------------------------ load
ws = workspace()
try:
    snap = snapshot(ws.fingerprint())
except Exception as exc:                                        # a saved upload breaks the rebuild
    st.error(f"The data could not be rebuilt after the last upload ({type(exc).__name__}: {exc}).")
    st.button("Remove the last upload", on_click=undo_upload, type="primary")
    st.stop()
fp = snap.fingerprint
a = snap.analysis
suggestions = suggestions_for(fp)
waiting, done = split(suggestions, answer_log().answers())

st.session_state.setdefault("page", "Today")
st.session_state.setdefault("last", None)
st.session_state.setdefault("upload_result", None)
st.session_state.setdefault("up_n", 0)

st.markdown("#### Axeom Pulse")
page = st.radio("Page", PAGES, key="page", horizontal=True, label_visibility="collapsed")

# ================================================================== TODAY
if page == "Today":
    t = views.today(a, waiting)
    n_up = len(ws.batches())
    st.caption(f"{t['date']} (the data's 'today')" + (f" · {n_up} upload(s) added" if n_up else ""))
    show_last()
    cards = t["cards"]
    call = next((s for s in waiting if s.kind == "call"), None)
    shown = set()
    if cards:
        st.markdown("##### Do this first")
        c = cards[0]
        with st.container(border=True):
            st.markdown(f"### {esc(c['title'])}")
            st.write(c["line"])
            if c["kind"] == "churn" and call:
                opts = list(call.options) + ["Later"]
                cols = st.columns(len(opts) + 1)
                for i, opt in enumerate(opts):
                    cols[i].button("Yes, I'll call" if opt == "Yes" else opt, key=f"first|{opt}", on_click=answer,
                                   args=(call, opt),
                                   **_kw(st.button, type="primary" if i == 0 else "secondary", stretch=True))
                cols[-1].button("Open", key="first|open", on_click=go, args=("Clients", call.client),
                                **_kw(st.button, stretch=True))
                shown.add(call.id)
            else:
                st.button("Open", key="first|open", on_click=go, args=(PAGE_OF_KIND.get(c["kind"], "Today"),),
                          **_kw(st.button, type="primary", stretch=True))
        if len(cards) > 1:
            st.markdown("##### Then")
            for i, c in enumerate(cards[1:], 2):
                with st.container(border=True):
                    left, right = st.columns([4, 1])
                    left.markdown(f"**{i}. {esc(c['title'])}**")
                    left.caption(esc(c["line"]))
                    right.button("Open", key=f"card{i}", on_click=go, args=(PAGE_OF_KIND.get(c["kind"], "Today"),),
                                 **_kw(st.button, stretch=True))
    rest = [s for s in waiting if s.id not in shown]
    st.markdown(f"##### Waiting for your OK ({len(rest)})")
    if not rest:
        st.write("Nothing waiting. Well done.")
    for s in rest[:SHOW_FIRST]:
        show_suggestion(s, "today")
    if len(rest) > SHOW_FIRST:
        with st.expander(f"See all {len(rest)} by topic"):
            groups = [g for g in GROUPS if any(s.group == g for s in rest)]
            g = st.selectbox("Topic", groups, format_func=lambda x: f"{GROUPS[x]} ({sum(s.group == x for s in rest)})")
            for s in [s for s in rest if s.group == g]:
                show_suggestion(s, "all")
    if done:
        with st.expander(f"Answered ({len(done)})"):
            st.dataframe(pd.DataFrame([{"question": s.question, "answer": ans} for s, ans in done]), hide_index=True)
            pick = st.selectbox("Change an answer", [s.question for s, _ in done], index=None,
                                placeholder="Choose one to undo")
            if pick:
                s = next(s for s, _ in done if s.question == pick)
                st.button("Undo this answer", key="undo_pick", on_click=undo, args=(s,))
    st.caption("Suggestions only. Nothing is sent or changed without your answer.")

# ================================================================== CLIENTS
elif page == "Clients":
    show_last()
    opts = views.client_options(a)
    labels = [o[0] for o in opts]
    ids = dict(opts)
    if st.session_state.get("client_label") not in ids:
        st.session_state.client_label = labels[0] if labels else None
    if not labels:
        st.info("No clients yet. Add files on the 'Add files' page.")
    else:
        label = st.selectbox(f"Client ({len(a.clients.clients)} clients)", labels, key="client_label")
        p = views.client_profile(a, ids[label])
        st.markdown(f"### {esc(p['name'])}")
        if p["risk"]:
            st.caption(esc(p["risk"]))
        mine = [s for s in waiting if s.client == p["name"]]
        if mine:
            st.markdown(f"**Waiting for your OK about {esc(p['name'])} ({len(mine)})**")
            show_list(mine, "client", first=3)
        for k, v in p["facts"].items():
            st.markdown(f"**{k}:** {esc(v)}")
        cols = st.columns(4)
        for col, (k, v) in zip(cols, p["money"].items()):
            col.metric(k, v)
        for name, df in p["tables"].items():
            table(df, name, expanded=False)
        table(p["how_linked"], "How these records were linked", note=(
            "Every record joined to this client, the names as written in each file, and the rule that joined it "
            "(email, phone, announced new email/number, owner decision, name, fuzzy name)."))
    ident = [s for s in waiting if s.kind == "identity"]
    if ident:
        st.markdown(f"##### Check these matches ({len(ident)})")
        st.caption("These look alike but are kept apart until you say they are the same.")
        for s in ident:
            show_suggestion(s, "ident")

# ================================================================== MONEY
elif page == "Money":
    from axeom import charts
    show_last()
    m = views.money(a)
    cols = st.columns(4)
    for col, (k, v) in zip(cols, m["numbers"].items()):
        col.metric(k, v)
    st.caption(m["note"])
    money_items = [s for s in waiting if s.group == 3]
    st.markdown(f"##### Waiting for your OK ({len(money_items)})")
    show_list(money_items, "money")
    if len(m["overdue"]):
        st.markdown("##### Overdue invoices")
        st.plotly_chart(charts.overdue_by_invoice(m["overdue"]), config={"displayModeBar": False})
    table(m["overdue"], "Overdue")
    table(m["never_billed"], "Work done but never billed")
    table(m["we_owe"], "We owe clients")
    table(m["tool_errors"], "Where the billing tool is wrong")
    table(m["not_due"], "Open, not due yet")

# ================================================================== EDITS
elif page == "Edits":
    from axeom import charts
    show_last()
    e = views.edits(a)
    st.markdown("##### Why edit requests get lost")
    for c in e["causes"]:
        st.markdown(f"- {esc(c)}")
    if len(e["by_month"]):
        st.markdown(f"##### Edit requests per month (worse since {e['worse_since'] or '-'})")
        st.plotly_chart(charts.lost_by_month(e["by_month"]), config={"displayModeBar": False})
    save = [s for s in waiting if s.group == 2]
    if save:
        st.markdown(f"##### Fix before delivery ({len(save)})")
        show_list(save, "save")
    redo = [s for s in waiting if s.group == 5]
    st.markdown(f"##### Delivered without the change ({len(redo)})")
    show_list(redo, "redo", first=3)
    table(e["by_channel"], "Lost by how the request was sent")
    table(e["open"], "All open requests")

# ================================================================== LEADS
elif page == "Leads":
    show_last()
    lv = views.leads(a)
    items = [s for s in waiting if s.group == 4]
    st.markdown(f"##### Nobody answered ({len(items)})")
    show_list(items, "lead")
    st.markdown("##### Where good clients come from")
    if lv["best"].get("channel"):
        st.write(lv["best"]["why"] + ".")
    else:
        st.write(lv["best"].get("why", ""))
    if len(lv["channels"]):
        st.dataframe(lv["channels"], hide_index=True)
    table(lv["unanswered"], "Leads never answered", note=f"{lv['junk']} spam or test entries are not listed.")

# ================================================================== ADD FILES
elif page == "Add files":
    st.markdown("##### Add new files")
    st.caption("CSV or TXT files in the same format as the pack: leads, bookings, invoices, payments, production "
               "board, messages, team, costs. Everything is recalculated. A file added twice is not counted twice; "
               "a record with the same id is updated, and every change is kept in the history.")
    files = st.file_uploader("Choose files", type=["csv", "txt"], accept_multiple_files=True,
                             key=f"uploader{st.session_state.up_n}")
    if st.button("Add these files", type="primary", disabled=not files, **_kw(st.button, stretch=True)):
        before = views.key_numbers(a)
        batch, rejected = ws.add_upload(files)
        try:
            new = snapshot(ws.fingerprint())
        except Exception as exc:                                # never keep an upload that breaks the app
            ws.undo_last()
            st.session_state.upload_result = {"error": f"{type(exc).__name__}: {exc}"}
        else:
            reports = (new.batch_reports.get(batch["batch"], []) if batch else []) + rejected
            details = [{"file": r.file_name, "line": x.get("line", ""), "record": x.get("record_id", ""),
                        "problem": x.get("reason", "")} for r in reports for x in r.rejected + r.ambiguous]
            st.session_state.upload_result = {
                "batch": batch["batch"] if batch else None, "table": reports_table(reports),
                "details": pd.DataFrame(details, columns=["file", "line", "record", "problem"]),
                "diff": views.diff_numbers(before, views.key_numbers(new.analysis))}
        st.session_state.up_n += 1
        st.rerun()
    res = st.session_state.upload_result
    if res:
        if res.get("error"):
            st.error(f"These files could not be processed and were not kept ({res['error']}).")
        else:
            if res["batch"]:
                st.success(f"Added as upload {res['batch']}. Everything below is recalculated.")
            else:
                st.warning("None of these files could be used. Nothing was added.")
            st.dataframe(res["table"], hide_index=True)
            if len(res["details"]):
                table(res["details"], "Rows not used, and why", expanded=True)
            d = res["diff"]
            st.markdown("##### What changed")
            ch = d[d.changed]
            st.write("No headline number changed." if not len(ch) else
                     "; ".join(f"{r.what}: {r.before:,.0f} → {r.after:,.0f}" if isinstance(r.after, (int, float))
                               else f"{r.what}: {r.before} → {r.after}" for r in ch.itertuples()))
    batches = ws.batches()
    st.markdown(f"##### Uploads so far ({len(batches)})")
    if batches:
        st.dataframe(pd.DataFrame([{"upload": b["batch"], "added (UTC)": b["saved_at_utc"],
                                    "files": ", ".join(f["name"] for f in b["files"])} for b in batches]),
                     hide_index=True)
        c1, c2 = st.columns(2)
        c1.button("Undo the last upload", on_click=undo_upload, **_kw(st.button, stretch=True))
        sure = c2.checkbox("I want to remove every upload")
        c2.button("Back to the original pack", on_click=reset_all, disabled=not sure, **_kw(st.button, stretch=True))
    else:
        st.write("Only the original pack is loaded.")
    hist = pd.DataFrame(snap.store.history)
    if len(hist):
        hist = hist[hist["batch"].astype(int) > 0]
        table(hist.drop(columns=["old", "new"], errors="ignore"), "Change history (every record added or updated)")
    q = snap.model.quality
    table(summarize(q), "Data problems found in the files", note=(
        "Every problem the engine found (duplicates, bad dates, conflicting records...), counted by type. "
        "Nothing is fixed silently: the analysis works around each one and says how."))
    st.markdown("##### Download")
    ex = exports_for(fp)
    c1, c2, c3 = st.columns(3)
    c1.download_button("answers.json", ex["answers"], file_name="answers.json", mime="application/json",
                       **_kw(st.download_button, stretch=True))
    c2.download_button("Evidence (CSV)", ex["ledger"], file_name="evidence_ledger.csv", mime="text/csv",
                       **_kw(st.download_button, stretch=True))
    c3.download_button("My answers (CSV)", answer_log().frame().to_csv(index=False), file_name="decisions.csv",
                       mime="text/csv", **_kw(st.download_button, stretch=True))
