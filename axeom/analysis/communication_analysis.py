"""Question 3 - why do client requests get lost?

Approach
--------
1. Find edit requests in messages.txt. Requests follow recognisable phrasings,
   each with a matching note the editors use on production_board.csv
   ("make the captions bigger" -> "Captions bigger").
2. Find which video a request is about: the client (from identity resolution)
   + a video title quoted in the message (longest title wins). "The one we shot
   last week" is tried against that client's shoots in the 10 days before.
   Titles repeat across shoots, so the most recent shoot on or before the
   message is used.
3. Classify every request:
     completed              the board carries the matching note and the video
                            was delivered, with no complaint afterwards
     delayed_or_unresolved  the board carries the note, but the client later
                            says it is still not fixed, or the video is still open
     confirmed_lost         the video is identified and its board row does NOT
                            carry the requested change: it never became a task
     unmatched              the video cannot be identified with certainty
                            (never called lost just because it cannot be matched)
4. Follow-up complaints ("still not fixed", "did anyone see my message") are
   evidence attached to the request they refer to.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .context import Context

QUESTION = "Q3 lost requests"

# (phrase in the message, note expected on the board, short label)
REQUEST_RULES = [
    (r"blur the face", "Blur client face", "blur a face"),
    (r"name is spelled wrong", "Fix name spelling", "fix name spelling"),
    (r"captions bigger", "Captions bigger", "bigger captions"),
    (r"cut the first 3 seconds", "Cut first 3 sec", "cut first 3 seconds"),
    (r"stumble on my words", "Cut stumble", "cut a stumble"),
    (r"walk-ins welcome'? at the end", "Add walk-ins welcome", "add 'walk-ins welcome'"),
    (r"talk about prices", "Remove prices part", "remove the prices part"),
    (r"calmer song", "Calmer music", "calmer music"),
    (r"music on .*too loud", "Music lower", "lower the music"),
    (r"other shot, the one outside", "Use outside shot", "use the outside shot"),
    (r"phone number at the end", "Phone number end card", "phone number end card"),
    (r"brand colou?r ([a-z][a-z ]*?)(?:,|\.| not\b|$)", "Brand color {0}", "brand color"),
    # a standing instruction for every future video, e.g. "please say Juniper
    # Wealth Partners, the full name, not just Juniper WP"
    (r"the full name|our full name", "Fix name spelling", "use the full business name (all videos)"),
]
STANDING = {"use the full business name (all videos)"}
FOLLOW_UP = re.compile(r"still not fixed|it'?s not fixed|second time i ask|still (?:is )?wrong|"
                       r"still has the same problem|already told you|i asked for", re.I)
LAST_WEEK = re.compile(r"(?:shot|filmed) last week", re.I)
QUOTED = re.compile(r"(?:(?<=\s)|^)['\"][^'\"]+['\"](?=[\s.,!?:]|$)")
LAST_WEEK_DAYS = 21     # clients say "last week" about shoots 7-15 days earlier in this data


def estimate_video_dates(prod: pd.DataFrame) -> pd.Series:
    """Date of each video: its shoot date, or - for cards without a shoot
    booking - interpolated from neighbouring cards. Video ids are issued in
    date order (checked: no card is more than 3 days out of order), so a card
    between two cards of the 11 May shoot belongs to that shoot."""
    num = prod.video_id.str.extract(r"(\d+)")[0].astype(float)
    known = prod.shoot_date.notna() & num.notna()
    est = prod.shoot_date.copy()
    if known.sum() >= 2:
        epoch = pd.Timestamp("1970-01-01")
        x = num[known].to_numpy()
        y = (prod.shoot_date[known] - epoch).dt.days.to_numpy(dtype=float)   # unit-safe (pandas 2 and 3)
        order = np.argsort(x, kind="stable")
        days = np.round(np.interp(num.fillna(0).to_numpy(), x[order], y[order]))
        filled = pd.Series(epoch + pd.to_timedelta(days, unit="D"), index=prod.index)
        est = est.where(est.notna(), filled)
    return est
OUT_COLUMNS = ["video_id", "classification", "confidence", "why", "board_note", "stage", "editor", "revisions",
               "approved_on", "shoot_booking", "followups", "followup_dates"]
CHANNEL_LABEL = {"EMAIL": "email", "WHATSAPP": "whatsapp", "VOICE NOTE": "voice note",
                 "INSTAGRAM DM": "instagram", "WEBSITE FORM": "website form"}


@dataclass
class CommResult:
    requests: pd.DataFrame       # one row per request with its classification
    followups: pd.DataFrame
    by_month: pd.DataFrame
    by_channel: pd.DataFrame
    root_causes: list            # (cause, evidence) tuples
    worse_since: str             # YYYY-MM
    never_replied_leads: pd.DataFrame
    missed_referrals: pd.DataFrame
    opportunities: pd.DataFrame     # clients asking what more would cost


def _match_rule(text):
    t = text.lower()
    for pat, note, label in REQUEST_RULES:
        m = re.search(pat, t)
        if m:
            return note.format(*m.groups()) if "{0}" in note else note, label
    return None, None


def _find_video(msg, vids, expected_note=None):
    """Return (candidate videos frame, how).

    * The request wording and anything in quotes are removed first, so
      "add the line 'walk-ins welcome'" is never read as the title
      'Walk-ins welcome'.
    * Titles repeat across shoots: among videos made on or before the message,
      a card that already carries the requested note wins (within 60 days),
      otherwise the most recent one.
    * "The one we shot last week": that client's most recent shoot in the
      previous 21 days; all of its cards are candidates."""
    t = msg.text.lower()
    clean = QUOTED.sub(" ", t)
    hits = vids.loc[[ti.lower() in clean for ti in vids.title]] if len(vids) else vids
    if len(hits):
        longest = hits.title.str.len().max()           # 'meet the advisor' beats 'advisor'
        hits = hits[hits.title.str.len() == longest]
        before = hits[hits.est_date <= msg.ts.normalize() + pd.Timedelta(days=1)]
        hits = before if len(before) else hits
        hits = hits.sort_values("est_date", ascending=False)
        if expected_note:
            noted = hits[(hits.notes_on_board.str.strip().str.lower() == expected_note.lower()) &
                         (hits.est_date >= msg.ts.normalize() - pd.Timedelta(days=60))]
            if len(noted):
                return noted.iloc[:1], "title"
        return hits.iloc[:1], "title"
    if LAST_WEEK.search(t):
        md = msg.ts.normalize()
        shoots = vids[vids.shoot_date.notna() & (vids.shoot_date <= md) &
                      (vids.shoot_date >= md - pd.Timedelta(days=LAST_WEEK_DAYS))]
        if len(shoots):
            sd = shoots.shoot_date.max()
            return vids[(vids.est_date - sd).abs() <= pd.Timedelta(days=2)], "shot last week"
        return vids.iloc[0:0], "shot last week (no shoot in the previous 21 days)"
    return vids.iloc[0:0], "no video named"


def analyze_requests(ctx: Context, names: dict) -> CommResult:
    f = ctx.frames
    msgs = f["messages"].copy()
    msgs["ts"] = msgs["timestamp_date"]
    prod = f["production"].copy()
    bk = f["bookings"].set_index("booking_id")
    prod["shoot_date"] = (pd.to_datetime(prod.shoot_booking.map(bk.date_date.to_dict()))
                          if len(prod) and len(bk) else pd.Series(pd.NaT, index=prod.index, dtype="datetime64[ns]"))
    prod["est_date"] = estimate_video_dates(prod)
    # clients' videos: use the identity of the video (booking-inherited where possible)
    inbound = msgs[(msgs.direction == "inbound")].sort_values("ts")
    L, src = ctx.ledger, ctx.src

    reqs, fups = [], []
    for m in inbound.itertuples():
        note, label = _match_rule(m.text)
        is_fu = bool(FOLLOW_UP.search(m.text))
        if is_fu and label in STANDING:
            note, label = None, None          # a complaint about a standing instruction, not a new one
        if not note and not is_fu:
            continue
        vids = prod[prod.client_id == m.client_id] if m.client_id else prod.iloc[0:0]
        if label in STANDING:       # applies to every video made after the message
            cand = vids[vids.est_date >= m.ts.normalize()].sort_values("est_date")
            how = "standing instruction (videos made afterwards)"
        else:
            cand, how = _find_video(m, vids, note)
        base = {"message_key": m.message_key, "timestamp": m.timestamp, "month": m.timestamp[:7],
                "came_by": CHANNEL_LABEL.get(m.channel, m.channel.lower()), "client_id": m.client_id,
                "client": names.get(m.client_id, m.counterpart), "text": m.text, "video_match": how,
                "candidates": ", ".join(cand.video_id) if len(cand) else ""}
        if is_fu and not note:
            fups.append({**base, "video_id": cand.video_id.iloc[0] if len(cand) == 1 else ""})
            continue
        reqs.append({**base, "requested": label, "expected_note": note})
    req = pd.DataFrame(reqs, columns=["message_key", "timestamp", "month", "came_by", "client_id", "client", "text",
                                      "video_match", "candidates", "requested", "expected_note"])
    fu = pd.DataFrame(fups, columns=["message_key", "timestamp", "month", "came_by", "client_id", "client", "text",
                                     "video_match", "candidates", "video_id"])
    pv = prod.set_index("video_id")

    out = []
    for r in req.itertuples():
        cands = [c for c in r.candidates.split(", ") if c]
        with_note = [c for c in cands if pv.loc[c, "notes_on_board"].strip().lower() == r.expected_note.lower()]
        vid = with_note[0] if with_note else (cands[0] if len(cands) == 1 else "")
        how_note = ""
        client_fu = fu[(fu.client_id == r.client_id) & (fu.timestamp > r.timestamp)]
        if not vid and cands:
            # a later complaint naming one of the candidate videos tells which one it was
            named = client_fu[client_fu.video_id.isin(cands)]
            if len(named):
                vid = named.video_id.iloc[0]
                how_note = f"; video identified by the client's follow-up on {named.timestamp.iloc[0][:10]}"
        if r.requested in STANDING:
            later_fu = client_fu[client_fu.text.str.contains(r"name|again", case=False)]
            vid = cands[0] if cands else ""
        else:
            later_fu = fu[(fu.video_id == vid) & (fu.timestamp > r.timestamp)] if vid else fu.iloc[0:0]
        row = {**r._asdict(), "video_id": vid}
        row["video_match"] = r.video_match + how_note
        row.pop("Index", None)
        if r.requested in STANDING and vid:
            affected = [c for c in cands if pv.loc[c, "notes_on_board"].strip().lower() != r.expected_note.lower()]
            v = pv.loc[vid]
            row.update(board_note=v.notes_on_board, stage=v.stage, editor=v.editor, revisions=v.revisions,
                       approved_on=v.client_approved_on, shoot_booking=v.shoot_booking,
                       followups=", ".join(later_fu.message_key), followup_dates=", ".join(later_fu.timestamp))
            if len(later_fu):
                row.update(classification="confirmed_lost", confidence="high",
                           why=f"standing instruction sent by {r.came_by}; none of the {len(affected)} videos made "
                               f"afterwards ({', '.join(affected[:6])}) carries it on the board; the client "
                               f"complained on {later_fu.timestamp.iloc[0][:10]}")
            else:
                row.update(classification="delayed_or_unresolved", confidence="medium",
                           why=f"standing instruction; not on the board for {len(affected)} later videos")
            out.append(row)
            continue
        if not vid:
            row.update(classification="unmatched", confidence="",
                       why=("no video title in the message" + (" and no shoot in the previous 21 days"
                                                                if "no shoot" in r.video_match else "")
                            if not cands else
                            f"the message names no video; none of the {len(cands)} videos from that shoot "
                            f"({', '.join(cands)}) carries the change, so it was probably never recorded - "
                            f"but which video it was cannot be proven"))
        else:
            v = pv.loc[vid]
            row.update(board_note=v.notes_on_board, stage=v.stage, editor=v.editor,
                       revisions=v.revisions, approved_on=v.client_approved_on, shoot_booking=v.shoot_booking,
                       followups=", ".join(later_fu.message_key), followup_dates=", ".join(later_fu.timestamp))
            if with_note:
                if len(later_fu):
                    row.update(classification="delayed_or_unresolved", confidence="high",
                               why=f"on the board as '{v.notes_on_board}', but the client complained again "
                                   f"on {later_fu.timestamp.iloc[0][:10]}")
                elif v.status_norm != "delivered":
                    days = (ctx.today - pd.Timestamp(r.timestamp).normalize()).days
                    row.update(classification="delayed_or_unresolved", confidence="medium",
                               why=f"on the board, video still '{v.stage}' {days} days after the request")
                else:
                    row.update(classification="completed", confidence="high",
                               why=f"on the board as '{v.notes_on_board}' and the video was delivered")
            else:
                reasons = []
                if r.came_by == "voice note":
                    reasons.append("sent as a voice note; nothing was written onto the board")
                elif r.came_by == "instagram":
                    reasons.append("sent by Instagram DM; never copied onto the board")
                if r.video_match == "shot last week":
                    reasons.append("the message named no video ('the one we shot last week')")
                if v.notes_on_board.strip():
                    reasons.append(f"the board's single note field already held '{v.notes_on_board}', "
                                   f"so this second request was not recorded")
                if not reasons:
                    reasons.append(f"sent by {r.came_by} but never added to the board")
                if v.status_norm == "delivered":
                    reasons.append("the video was delivered without the change")
                if len(later_fu):
                    reasons.append(f"the client chased it on {later_fu.timestamp.iloc[0][:10]}")
                if v.status_norm != "delivered" and not len(later_fu):
                    # the video is still being worked on: the change may yet be made
                    row.update(classification="delayed_or_unresolved", confidence="medium",
                               why="NOT on the board yet and the video is still '" + v.stage + "' - at risk: "
                                   + "; ".join(reasons))
                else:
                    row.update(classification="confirmed_lost", confidence="high", why="; ".join(reasons))
        out.append(row)
    req = pd.DataFrame(out, columns=list(req.columns) + [c for c in OUT_COLUMNS if c not in req.columns]) \
        if not out else pd.DataFrame(out)
    for c in OUT_COLUMNS:
        if c not in req.columns:
            req[c] = ""

    # aggregates
    req["is_lost"] = req.classification == "confirmed_lost"
    CLASSES = ["completed", "delayed_or_unresolved", "confirmed_lost", "unmatched"]
    by_month = (req.groupby(["month", "classification"]).size().unstack(fill_value=0).reindex(columns=CLASSES, fill_value=0)
                if len(req) else pd.DataFrame(columns=CLASSES, dtype=int))
    by_month["requests"] = by_month.sum(axis=1)
    by_month["lost_share"] = (by_month.confirmed_lost / by_month.requests).round(2)
    by_channel = (req.groupby(["came_by", "classification"]).size().unstack(fill_value=0).reindex(columns=CLASSES, fill_value=0)
                  if len(req) else pd.DataFrame(columns=CLASSES, dtype=int))
    by_channel["requests"] = by_channel.sum(axis=1)
    by_channel["lost_share"] = (by_channel.confirmed_lost / by_channel.requests).round(2)

    # when did it get worse: first month whose lost share is at least double the
    # share of all earlier months combined, and stays at or above it afterwards
    # (months with fewer than MIN_SAMPLE requests are too small to judge, e.g.
    #  the first days of the reference month, and are skipped)
    MIN_SAMPLE = 5
    worse = ""
    judged = by_month[by_month.requests >= MIN_SAMPLE]
    months = list(judged.index)
    for i, mo in enumerate(months[1:], start=1):
        prev = judged.iloc[:i]
        base = prev.confirmed_lost.sum() / max(prev.requests.sum(), 1)
        bar = max(2 * base, 0.3)
        if (judged.iloc[i:].lost_share >= bar).all():
            worse = mo
            break

    # voice-note channel: when Axeom invited it
    invite = msgs[(msgs.direction == "outbound") & msgs.text.str.contains("voice note", case=False)]
    first_voice = msgs[msgs.channel == "VOICE NOTE"].timestamp.min()
    lost = req[req.is_lost]
    causes = []
    vn = by_channel.loc["voice note"] if "voice note" in by_channel.index else None
    if vn is not None and vn.requests:
        vreq = req[req.came_by == "voice note"]
        missing = int(((vreq.classification == "confirmed_lost") |
                       vreq.why.str.startswith("NOT on the board")).sum())
        txt = (f"Voice notes never reach the production board: {missing} of {int(vn.requests)} voice-note "
               f"requests are missing from it ({int(vn.confirmed_lost)} on videos already delivered)")
        if len(invite):
            txt += (f". Axeom itself told clients on {invite.timestamp.iloc[0][:10]} to 'just send edit notes as a "
                    f"voice note'")
        causes.append(("voice notes are not written down", txt))
    slot = lost[lost.why.str.contains("single note field")]
    if len(slot):
        causes.append(("one note per video", f"The board has one 'notes_on_board' field per video; {len(slot)} lost "
                       f"requests were second requests on a video that already had a note"))
    vague = req[req.video_match == "shot last week"]
    if len(vague):
        causes.append(("requests that name no video", f"{len(vague)} requests only say 'the one we shot last week'; "
                       f"{int((vague.classification != 'completed').sum())} of them did not end as completed"))
    ig = by_channel.loc["instagram"] if "instagram" in by_channel.index else None
    if ig is not None and ig.requests:
        causes.append(("Instagram DMs are not watched", f"{int(ig.confirmed_lost)} of {int(ig.requests)} Instagram "
                       f"requests lost; {int(ig.unmatched)} could not be tied to a client/video"))
    other = lost[~lost.came_by.isin(["voice note", "instagram"]) & ~lost.why.str.contains("single note field")]
    if len(other):
        causes.append(("written requests also slip", f"{len(other)} email/WhatsApp requests never made it to the "
                       f"board either: there is no single intake step"))

    # ledger
    for r in req.itertuples():
        srcs = [src("messages", r.message_key, f"request ({r.came_by})")]
        if r.video_id:
            srcs.append(src("production", r.video_id, "video on the board"))
        if isinstance(r.followups, str) and r.followups:
            srcs += [src("messages", k, "follow-up complaint") for k in r.followups.split(", ")]
        L.add(finding_id=L.new_id("Q3-REQ"), question=QUESTION, category=r.classification,
              status=("confirmed" if r.classification in ("confirmed_lost", "completed") and r.confidence == "high"
                      else "inferred" if r.classification != "unmatched" else "unresolved"),
              confidence=r.confidence, client=r.client,
              statement=f"{r.timestamp} {r.came_by}: '{r.requested}' on {r.video_id or '?'} -> {r.classification}",
              calculation=r.why, matching_decision=f"video found by {r.video_match}", sources=srcs)
    L.add(finding_id=L.new_id("Q3-TREND"), question=QUESTION, category="worse_since", status="inferred",
          confidence="medium", statement=f"lost requests got worse from {worse}",
          calculation="monthly share of requests classified confirmed_lost; first month at least double the "
                      "earlier rate (and >= 30%) and staying there in every later month with >= 5 requests: " +
                      "; ".join(f"{m}: {int(r.confirmed_lost)}/{int(r.requests)}" for m, r in by_month.iterrows()),
          sources=[src("messages", k, "lost request") for k in lost.message_key])

    leads = _never_replied(ctx, names)
    refs = _missed_referrals(ctx)
    for r in leads.itertuples():
        L.add(finding_id=L.new_id("Q3-LEAD"), question=QUESTION, category="never_replied_lead", status="confirmed",
              confidence="high", client=r.business or r.name,
              statement=f"{r.lead_id} ({r.source}, {r.created}) has no first reply; status {r.status}",
              calculation="first_reply is blank" + (f"; {r.note}" if r.note else ""),
              sources=[src("leads", r.lead_id, "lead")])
    for r in refs.itertuples():
        L.add(finding_id=L.new_id("Q3-REFERRAL"), question=QUESTION, category="referral", status=r.status,
              confidence="high", client=r.referred_by, statement=r.statement, calculation=r.check,
              sources=[src("messages", r.message_key, "referral message")])
    opps = _opportunities(ctx, names)
    for r in opps.itertuples():
        L.add(finding_id=L.new_id("Q3-OPPORTUNITY"), question=QUESTION, category="sales_opportunity",
              status="confirmed", confidence="high", client=r.client,
              statement=f"{r.timestamp} {r.client} asked: '{r.text}'", calculation=r.check,
              sources=[src("messages", r.message_key, "question about price / more work")])
    return CommResult(requests=req, followups=fu, by_month=by_month, by_channel=by_channel, root_causes=causes,
                      worse_since=worse, never_replied_leads=leads, missed_referrals=refs, opportunities=opps)


def _opportunities(ctx: Context, names: dict) -> pd.DataFrame:
    """Existing clients asking for more work or a price, and whether anything followed."""
    msgs = ctx.frames["messages"]
    bk = ctx.frames["bookings"]
    pat = re.compile(r"what would that cost|how much would that be|every month\?|do you have a package", re.I)
    rows = []
    for m in msgs[(msgs.direction == "inbound") & msgs.text.str.contains(pat)].itertuples():
        later = bk[(bk.client_id == m.client_id) & (bk.date_date > m.timestamp_date)]
        services_before = set(bk[(bk.client_id == m.client_id) & (bk.date_date <= m.timestamp_date)].service)
        new = later[~later.service.isin(services_before)]
        rows.append({"message_key": m.message_key, "timestamp": m.timestamp, "client_id": m.client_id,
                     "client": names.get(m.client_id, m.counterpart), "text": m.text,
                     "check": (f"{len(later)} later booking(s), none for a new service" if len(later) and not len(new)
                               else f"later booking(s) for a new service: {', '.join(new.booking_id)}" if len(new)
                               else "no later booking at all"),
                     "followed_up": bool(len(new))})
    return pd.DataFrame(rows, columns=["message_key", "timestamp", "client_id", "client", "text", "check",
                                       "followed_up"])


def _never_replied(ctx: Context, names: dict) -> pd.DataFrame:
    leads = ctx.frames["leads"].copy()
    nr = leads[leads.first_reply.str.strip() == ""].copy()
    junk = (nr.phone_norm == "") & (nr.business.str.strip() == "")
    nr["likely_junk"] = junk
    nr["note"] = ""
    nr.loc[junk, "note"] = "no phone and no business: looks like spam or a test entry"
    existing = nr.client_id.map(lambda c: c in names)
    if (existing & ~junk).any():
        nr.loc[existing & ~junk, "note"] = "existing client: " + nr.loc[existing & ~junk, "client_id"].map(names)
    return nr[["lead_id", "created", "name", "business", "email", "source", "status", "client_id", "likely_junk",
               "note"]].reset_index(drop=True)


def _missed_referrals(ctx: Context) -> pd.DataFrame:
    """Messages that pass on a new prospect; checks whether a lead appeared afterwards."""
    msgs = ctx.frames["messages"]
    leads = ctx.frames["leads"]
    rows = []
    pat = re.compile(r"\b(?:friend|cousin|brother|sister|colleague|neighbou?r)\b.*\b(?:wants|needs)\b.*video", re.I)
    for m in msgs[(msgs.direction == "inbound") & msgs.text.str.contains(pat)].itertuples():
        phone = re.search(r"(\d{3}[-.\s]\d{3}[-.\s]\d{4})", m.text)
        names_in = re.findall(r"(?:Dr\.?\s)?[A-Z][a-z]+(?:\s[A-Z][a-z]+)+", m.text)
        later = leads[leads.created_date >= m.timestamp_date.normalize()]
        found = later.iloc[0:0]
        if phone:
            p = re.sub(r"\D", "", phone.group(1))
            found = later[later.phone_norm == p]
        if not len(found):
            for n in names_in:
                k = n.replace("Dr. ", "").replace("Dr ", "").lower()
                found = later[(later.name.str.lower() == k) | later.business.str.lower().str.contains(k, regex=False)]
                if len(found):
                    break
        status = "confirmed"
        rows.append({"message_key": m.message_key, "timestamp": m.timestamp, "referred_by": m.counterpart,
                     "statement": m.text, "became_lead": ", ".join(found.lead_id) if len(found) else "",
                     "status": status,
                     "check": (f"lead {', '.join(found.lead_id)} created afterwards" if len(found) else
                               "no lead with this phone or name was ever created: the referral was dropped")})
    return pd.DataFrame(rows, columns=["message_key", "timestamp", "referred_by", "statement", "became_lead",
                                       "status", "check"])
