"""Suggestions for the owner: "this is late, send a reminder?" - yes or no.

Every suggestion is built from the Phase 2 analysis and has a stable id (for
example ``remind:INV-1094``), so the owner's answer survives a rebuild and a
new upload. When the data changes so that a suggestion no longer applies (the
payment arrived, the lead got a reply), it simply disappears.

Nothing is ever sent. "Yes" records the decision and shows a drafted message
(or the exact change to make) that the owner sends or makes himself.

Order (same rule as Q6, see analysis/owner_priorities.py):
  1 call the client most likely to leave
  2 edit requests that can still be fixed before delivery
  3 money: overdue reminders (longest first), money we owe, unbilled work,
    billing tool corrections
  4 leads and referrals nobody answered (newest first)
  5 delivered videos that lost an edit request (re-do)
  6 identity questions ("are these the same client?")
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote

import pandas as pd

GROUPS = {1: "Keep a client", 2: "Save an edit", 3: "Money", 4: "New business", 5: "Re-do an edit",
          6: "Check a client match"}


@dataclass
class Suggestion:
    id: str
    group: int
    kind: str
    client: str
    question: str                  # one line, ends with "?"
    why: str                       # one line of evidence
    options: list = field(default_factory=lambda: ["Yes", "No"])
    evidence: list = field(default_factory=list)
    amount: float = 0.0
    to: str = ""                   # email address for the draft, if any
    subject: str = ""
    drafts: dict = field(default_factory=dict)   # option -> text shown after that answer
    identity: dict = field(default_factory=dict)  # for identity questions: emails to join
    order: float = 0.0             # sort key inside the group

    def to_dict(self) -> dict:
        return asdict(self)

    def mailto(self, option: str = "Yes") -> str:
        """A link that opens the owner's own email app with the draft. Nothing is sent by the app."""
        if not self.to:
            return ""
        body = self.drafts.get(option, "")
        return f"mailto:{self.to}?subject={quote(self.subject)}&body={quote(body)}"


def _money(x) -> str:
    try:
        return f"{float(x):,.0f}" if float(x).is_integer() else f"{float(x):,.2f}"
    except (TypeError, ValueError):
        return str(x)


def _first(seq, default=""):
    for x in seq if isinstance(seq, (list, tuple)) else []:
        if x:
            return x
    return default


def _short(text: str, n: int = 90) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _hid(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8]


# ----------------------------------------------------------------- builders
def _call(a, out):
    top = a.churn.most_likely
    if not top.get("client"):
        return
    cid = top["client_id"]
    cl = a.clients.clients.set_index("client_id")
    phone = _first(cl.loc[cid, "phones"]) if cid in cl.index else ""
    email = _first(cl.loc[cid, "emails"]) if cid in cl.index else ""
    nr = a.comms.never_replied_leads
    contact = _first(list(nr[(nr.client_id == cid) & ~nr.likely_junk].name))
    if not contact:
        msgs = a.ctx.frames["messages"]
        mine = msgs[(msgs.client_id == cid) & (msgs.direction == "inbound") & (msgs.contact_name != "")]
        contact = mine.sort_values("timestamp").contact_name.iloc[-1] if len(mine) else ""
    sig = a.churn.signals
    intent = sig[(sig.client_id == cid) & (sig.signal == "leave_intent")].sort_values("date")
    if len(intent):
        dates = ", ".join(pd.to_datetime(intent.date).dt.strftime("%d %b"))
        why = f"Asked about ending the contract ({dates}); nobody replied."
    else:
        why = f"Highest risk score ({top['risk_points']} points): " + "; ".join(top["evidence"][-2:])
    who = f"{contact} at {top['client']}" if contact else top["client"]
    out.append(Suggestion(
        id=f"call:{cid}", group=1, kind="call", client=top["client"],
        question=f"Call {who} today?", why=why, evidence=list(top["evidence"]),
        amount=top["revenue_at_stake_90_days"],
        drafts={"Yes": (f"Phone: {phone or 'not in the data'}   Email: {email or 'not in the data'}\n\n"
                        "Talking points:\n" +
                        top["what_to_do_this_week"].replace(". ", ".\n"))}))


def _edits(a, out):
    r = a.comms.requests
    for x in r[r.classification == "delayed_or_unresolved"].sort_values("timestamp").itertuples():
        out.append(Suggestion(
            id=f"edit:{x.message_key}", group=2, kind="save_edit", client=x.client,
            question=f"Add '{x.requested}' to {x.video_id} for {x.client} before it is delivered?",
            why=f"Sent as {x.came_by} on {x.timestamp[:10]}; the board does not have it and {x.video_id} is "
                f"'{x.stage}'.",
            evidence=[f"{x.timestamp} {x.came_by}: \"{x.text}\"", x.why], order=x.timestamp,
            drafts={"Yes": f"Production board, {x.video_id}: add note '{x.requested}' "
                           f"(asked by {x.client}, {x.came_by}, {x.timestamp[:16]})."}))
    for x in r[r.classification == "confirmed_lost"].sort_values("timestamp", ascending=False).itertuples():
        out.append(Suggestion(
            id=f"reopen:{x.message_key}:{x.video_id}", group=5, kind="reopen_edit", client=x.client,
            question=f"Re-do '{x.requested}' on {x.video_id} for {x.client}?",
            why=f"Asked by {x.came_by} on {x.timestamp[:10]}; the video was delivered without it.",
            evidence=[f"{x.timestamp} {x.came_by}: \"{x.text}\"", x.why],
            order=-pd.Timestamp(x.timestamp).value,
            drafts={"Yes": f"Production board, {x.video_id}: re-open and add note '{x.requested}' "
                           f"(asked {x.timestamp[:10]}, missed)."}))


def _money_items(a, out):
    f = a.finance
    inv = a.ctx.frames["invoices"].drop_duplicates("invoice_no").set_index("invoice_no")
    churn_client = a.churn.most_likely.get("client", "")
    for x in f.overdue.itertuples():
        email = inv.loc[x.invoice_no, "email_norm"] if x.invoice_no in inv.index else ""
        partial = x.received and float(x.received) > 0
        why = f"Due {x.due}; {_money(x.balance)} still open" + (f" ({_money(x.received)} paid)" if partial else "")
        if x.status != "confirmed":
            why += f". Check first: {x.basis}"
        if x.client == churn_client:
            why += ". Tip: raise it in your call with them, not by email first"
        body = (f"Hi {x.client} team,\n\nA friendly reminder that invoice {x.invoice_no} "
                f"({_money(x.amount)}) was due on {x.due}"
                + (f". We have received {_money(x.received)}, so {_money(x.balance)} is still open"
                   if partial else f" and is still open on our side")
                + ".\n\nIf you have already paid, please reply with the payment date or reference and we will "
                  "match it.\n\nThank you,\nAxeom")
        out.append(Suggestion(
            id=f"remind:{x.invoice_no}", group=3, kind="remind", client=x.client,
            question=f"{x.invoice_no} from {x.client} is {x.days_late} days late ({_money(x.balance)}). "
                     f"Send a reminder?",
            why=why, amount=float(x.balance), to=email, subject=f"Invoice {x.invoice_no} - reminder",
            evidence=[f"{x.invoice_no}: billed {_money(x.amount)}, received {_money(x.received)}, billing tool says "
                      f"'{x.tool_status}'", x.basis],
            order=-float(x.days_late), drafts={"Yes": body}))
    for x in f.we_owe[f.we_owe.status != "unresolved"].itertuples():
        out.append(Suggestion(
            id=f"owe:{x.client}:{_hid(x.why)}", group=3, kind="we_owe", client=x.client,
            question=f"We owe {x.client} {_money(x.amount)}. Refund it or keep it as credit?",
            why=_short(x.why, 140), amount=float(x.amount), options=["Refund", "Keep as credit", "No"],
            evidence=[x.why], order=-1e9 + -float(x.amount),
            to=_client_email(a, x.client), subject="A correction on your account",
            drafts={"Refund": (f"Hi {x.client} team,\n\nWhile checking our accounts we found that {x.why}. "
                               f"We will refund {_money(x.amount)} to you this week. Sorry for the mix-up.\n\n"
                               "Thank you,\nAxeom"),
                    "Keep as credit": (f"Hi {x.client} team,\n\nWhile checking our accounts we found that {x.why}. "
                                       f"We have added a credit of {_money(x.amount)} to your account for your next "
                                       "invoice. Sorry for the mix-up.\n\nThank you,\nAxeom")}))
    for x in f.never_billed.itertuples():
        what = f"{x.service} {x.booking_or_month}"
        out.append(Suggestion(
            id=f"bill:{x.client_id}:{x.booking_or_month}", group=3, kind="bill", client=x.client,
            question=f"{x.client}: {what} was never billed ({_money(x.amount)}). Create the invoice?",
            why=_short(x.why, 150), amount=float(x.amount), evidence=[x.why], order=-float(x.amount) / 1e6,
            drafts={"Yes": f"New invoice in the billing tool: {x.client} - {what} - {_money(x.amount)}"
                           + (f" (videos {x.videos})" if x.videos else "")}))
    skip = {"paid_twice", "amount_differs_from_booking", "duplicate_invoice"}   # handled above or already fixed
    for x in f.tool_errors[~f.tool_errors.kind.isin(skip)].itertuples():
        unresolved = x.status == "unresolved"
        out.append(Suggestion(
            id=f"tool:{x.invoice_no}:{x.kind}", group=3, kind="fix_tool", client=x.client,
            question=(f"{x.invoice_no} ({x.client}) is {_short(x.truth, 60)}. Look into it?" if unresolved else
                      f"Billing tool shows {x.invoice_no} as '{_short(x.tool_says, 30)}', but it is "
                      f"'{_short(x.truth, 50)}'. Correct it?"),
            why=_short(x.how_we_know, 150), evidence=[x.how_we_know], order=1e9,
            drafts={"Yes": (f"Check {x.invoice_no}: {x.truth}" if unresolved else
                            f"Billing tool, {x.invoice_no}: change '{x.tool_says}' to '{x.truth}'. "
                            f"Reason: {x.how_we_know}")}))


def _client_email(a, client: str) -> str:
    c = a.clients.clients
    hit = c[c.canonical_name == client]
    return _first(hit.emails.iloc[0]) if len(hit) else ""


def _leads(a, out):
    top = a.churn.most_likely.get("client_id", "")
    nr = a.comms.never_replied_leads
    for x in nr[~nr.likely_junk & (nr.client_id != top)].sort_values("created", ascending=False).itertuples():
        first = str(x.name).split()[0] if str(x.name).strip() else "there"
        biz = f" for {x.business}" if str(x.business).strip() else ""
        existing = x.note.startswith("existing client") if x.note else False
        out.append(Suggestion(
            id=f"lead:{x.lead_id}", group=4, kind="reply_lead", client=x.business or x.name,
            question=f"{x.name}{' (' + x.business + ')' if x.business else ''} asked on {x.created[:10]} and "
                     f"never got a reply. Reply now?",
            why=f"Came in through {x.source} ({x.lead_id})" + (f"; {x.note}" if existing else "") + ".",
            evidence=[f"leads.csv {x.lead_id}: first_reply is empty"], to=x.email,
            subject="Your video enquiry - Axeom", order=-pd.Timestamp(x.created).value,
            drafts={"Yes": (f"Hi {first},\n\nThank you for contacting Axeom about videos{biz}, and sorry for our slow "
                            "reply. Could we have a 15-minute call this week to hear what you need? Tell me a time "
                            "that suits you.\n\nThank you,\nAxeom")}))
    mr = a.comms.missed_referrals
    for x in mr[mr.became_lead == ""].itertuples():
        out.append(Suggestion(
            id=f"referral:{x.message_key}", group=4, kind="referral", client=str(x.referred_by),
            question=f"A client referred someone on {x.timestamp[:10]} and nobody called. Call them?",
            why=_short(f"\"{x.statement}\"", 150), evidence=[f"{x.timestamp} {x.referred_by}: {x.statement}", x.check],
            order=-pd.Timestamp(x.timestamp).value,
            drafts={"Yes": f"Referral from {x.referred_by}: {x.statement}"}))
    op = a.comms.opportunities
    for x in op[~op.followed_up].itertuples():
        out.append(Suggestion(
            id=f"upsell:{x.message_key}", group=4, kind="upsell", client=x.client,
            question=f"{x.client} asked about more work on {x.timestamp[:10]}. Send a price?",
            why=_short(f"\"{x.text}\"", 150), evidence=[x.text, x.check], to=_client_email(a, x.client),
            subject="Your question about more videos", order=-pd.Timestamp(x.timestamp).value,
            drafts={"Yes": (f"Hi {x.client} team,\n\nThanks for asking about more work. Here is what that would look "
                            "like and cost: [package and price]. Happy to talk it through on a call.\n\n"
                            "Thank you,\nAxeom")}))


def _identity(a, out):
    """Open identity questions with exactly two groups that each have an email."""
    links = a.clients.record_links
    unc = a.clients.uncertain
    names = dict(zip(a.clients.clients.client_id, a.clients.clients.canonical_name))
    for x in unc[unc.status_after_q1.astype(str).str.startswith("open")].itertuples():
        cands = [c.strip() for c in str(x.candidates).split("|")]
        if all(c.startswith("CL-") for c in cands):
            groups = cands
        else:
            recs = [r.strip().split(":", 1) for r in str(x.records).split("|") if ":" in r]
            groups = []
            for src, rid in recs:
                hit = links[(links.source == src) & (links.record_id == rid)]
                cid = hit.identity_id.iloc[0] if len(hit) else ""
                if cid and cid not in groups:
                    groups.append(cid)
        if len(groups) != 2:
            continue
        mails, labels = [], []
        for g in groups:
            sub = links[(links.client_id == g) | (links.identity_id == g)]
            e = _first(sorted({m for m in sub.email if m}))
            mails.append(e)
            labels.append(names.get(g) or _first(sorted({n for v in sub.raw_names for n in str(v).split(" | ") if n}), g))
        if not all(mails) or mails[0] == mails[1]:
            continue
        out.append(Suggestion(
            id=f"identity:{'|'.join(sorted(mails))}", group=6, kind="identity", client=labels[0],
            question=f"Are '{labels[0]}' ({mails[0]}) and '{labels[1]}' ({mails[1]}) the same client?",
            why=_short(x.detail, 150), options=["Same client", "Different"], evidence=[x.detail],
            identity={"value_type": "email", "value": mails[1], "target_email": mails[0]},
            drafts={"Same client": "Joined. Every record of both is now one client, everywhere in the app.",
                    "Different": "Kept separate. This question will not be asked again."}))


def build_suggestions(a) -> list[Suggestion]:
    out: list[Suggestion] = []
    for step in (_call, _edits, _money_items, _leads, _identity):
        step(a, out)
    seen, unique = set(), []
    for s in out:
        if s.id not in seen:
            seen.add(s.id)
            unique.append(s)
    return sorted(unique, key=lambda s: (s.group, s.order, s.id))


# ----------------------------------------------------------------- answers
ANSWER_COLUMNS = ["id", "answer", "answered_at_utc", "question"]


class DecisionLog:
    """The owner's answers, one row per suggestion (latest answer wins)."""

    def __init__(self, path: str):
        self.path = path

    def frame(self) -> pd.DataFrame:
        if not os.path.exists(self.path):
            return pd.DataFrame(columns=ANSWER_COLUMNS)
        return pd.read_csv(self.path, dtype=str, keep_default_na=False)

    def answers(self) -> dict:
        return dict(zip(self.frame().id, self.frame().answer))

    def answer(self, s: Suggestion, option: str) -> None:
        if option not in s.options and option != "Later":
            raise ValueError(f"'{option}' is not an option for {s.id}")
        d = self.frame()
        d = d[d.id != s.id]
        row = pd.DataFrame([[s.id, option, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), s.question]],
                           columns=ANSWER_COLUMNS)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        pd.concat([d, row], ignore_index=True).to_csv(self.path, index=False)

    def undo(self, suggestion_id: str) -> None:
        d = self.frame()
        d[d.id != suggestion_id].to_csv(self.path, index=False)


def split(suggestions: list[Suggestion], answers: dict) -> tuple[list[Suggestion], list[tuple]]:
    """(waiting for an answer, [(suggestion, answer)] already answered). 'Later' stays waiting, at the end."""
    waiting = [s for s in suggestions if s.id not in answers]
    later = [s for s in suggestions if answers.get(s.id) == "Later"]
    done = [(s, answers[s.id]) for s in suggestions if s.id in answers and answers[s.id] != "Later"]
    return waiting + later, done
