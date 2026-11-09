"""Client identity resolution.

Every record that mentions a party (a lead, a booking, an invoice, a payment,
a video, a message) becomes a *mention*. Mentions are grouped into clients
using this priority:

  1. exact email           (strongest)
  2. exact phone           (only when the two sides do not contradict each other)
  3. confirmed alias       (decisions the owner made in data/match_decisions.csv)
  4. exact normalised name (only when exactly one client uses that name and the
                            contact details do not contradict it)
  5. fuzzy name (RapidFuzz)(auto-link only when clearly the best and >= 92;
                            80-91 or a close runner-up goes to the review list)

Nothing uncertain is merged. Uncertain cases go to ``review`` with the
candidate clients and the records involved, so a human can decide.

Two extra rules come from inspecting the data:
  * A video on the production board that names its shoot booking (shoot_booking)
    inherits that booking's client. A booking ID is a stable identifier and
    is stronger evidence than a free-typed name.
  * A record whose email says "client A" but whose name is how client B
    describes itself (e.g. an agency booking for its customer) keeps the
    email link but is flagged for review.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import pandas as pd
from rapidfuzz import fuzz

from .config import DUPLICATE_CLIENT_SCORE, FUZZY_AUTO_LINK, FUZZY_MARGIN, FUZZY_REVIEW
from .normalizer import name_key

EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
             "aol.com", "live.com", "msn.com", "proton.me", "protonmail.com"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
class UnionFind:
    def __init__(self):
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def domain_stem(email: str) -> str:
    """'hello@haledental.com' -> 'haledental'. Free-mail domains give ''."""
    if "@" not in email:
        return ""
    dom = email.split("@", 1)[1]
    if dom in FREE_MAIL:
        return ""
    return dom.rsplit(".", 1)[0]


def name_score(a: str, b: str) -> float:
    """Similarity of two name keys, 0-100. Uses word-order-insensitive
    comparison and a no-spaces comparison (catches 'Parksidelab' vs
    'Parkside Lab'). Deliberately NOT token_set_ratio: that scores 100
    whenever one name is contained in the other ('hale' vs 'hale dental')."""
    if not a or not b:
        return 0.0
    return max(fuzz.token_sort_ratio(a, b),
               fuzz.ratio(a.replace(" ", ""), b.replace(" ", "")))


# Industry words say what a business does, not who it is. "Vega Tax Services"
# and "Rivera Tax Services" share them but are clearly different businesses.
GENERIC_WORDS = {"and", "auto", "bakery", "barbers", "barbershop", "builders", "care", "chiro",
                 "chiropractic", "coffee", "dental", "dentistry", "family", "firm", "fitness",
                 "group", "gym", "home", "hvac", "insurance", "landscaping", "law", "med",
                 "medspa", "office", "pediatrics", "physical", "plumbing", "realty", "roofing",
                 "roasters", "salon", "services", "spa", "studio", "tax", "therapy", "yoga"}


def distinctive(key: str) -> str:
    return " ".join(w for w in key.split() if w not in GENERIC_WORDS)


def duplicate_score(a: str, b: str) -> float:
    """Similarity used to spot two separate clients that may be one."""
    full = name_score(a, b)
    da, db = distinctive(a), distinctive(b)
    if da and db:
        # both the whole name AND the identifying words must be close:
        # 'Hale Law' vs 'Hale Dental' share a surname but differ as a whole;
        # 'Vega Tax Services' vs 'Rivera Tax Services' are close as a whole
        # only because of the industry words.
        return min(full, name_score(da, db))
    return full


HANDLE_SUFFIXES = ("official", "dallas", "texas", "dfw", "hq", "tx", "co")
ANNOUNCE_CUE = re.compile(r"\bnew (?:e-?mail|email address|address|number|office number|phone(?: number)?)\b"
                          r"|\bfrom now on\b|\bis now\b", re.I)
EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_IN_TEXT = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")


def handle_score(handle: str, target: str) -> float:
    """Similarity of an Instagram handle and a compact business name or
    email domain stem. Handles are often truncated or carry a location
    suffix: '@juniperwealthpartntx' -> 'juniperwealthpartn' starts
    'juniperwealthpartners'."""
    if not handle or not target:
        return 0.0
    core = handle
    changed = True
    while changed:
        changed = False
        for suf in HANDLE_SUFFIXES:
            if core.endswith(suf) and len(core) - len(suf) >= 6:
                core, changed = core[: -len(suf)], True
    score = max(fuzz.ratio(handle, target), fuzz.ratio(core, target))
    short, long_ = sorted((core, target), key=len)
    if len(short) >= 6 and long_.startswith(short) and len(short) >= 0.8 * len(long_):
        score = max(score, 95.0)
    return float(score)


# ---------------------------------------------------------------------------
# Mentions
# ---------------------------------------------------------------------------
def build_mentions(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per record that mentions a party."""
    rows = []

    def add(source, rid, row, names, email="", phone="", handle="", booking_ref="",
            person="", business=""):
        bkey, pkey = name_key(business), name_key(person)
        # Name tiers: 0 = how the party describes itself on a lead form,
        # 1 = business name typed on bookings/invoices/payments/board,
        # 2 = a person's name. A message signature ("Dana") is ignored when the
        # message already carries an email/phone/handle: first names are not identities.
        tiers = {}
        if bkey:
            tiers[bkey] = 0 if source == "leads" else 1
        if pkey and not (source == "messages" and (email or phone or handle)):
            tiers.setdefault(pkey, 2)
        rows.append({
            "mention_id": f"{source}:{rid}",
            "source": source, "record_id": rid,
            "source_file": row.get("_source_file", ""), "source_line": row.get("_source_line", ""),
            "raw_names": " | ".join(n for n in names if str(n).strip()),
            "keys": list(tiers), "tiers": tiers,
            "person_key": pkey, "business_key": bkey,
            "email": email, "phone": phone, "handle": handle, "booking_ref": booking_ref,
        })

    if "leads" in frames:
        for _, r in frames["leads"].iterrows():
            add("leads", r["lead_id"], r, [r["name"], r["business"]],
                email=r["email_norm"] if r["email_valid"] else "", phone=r["phone_norm"],
                person=r["name"], business=r["business"])
    if "bookings" in frames:
        for _, r in frames["bookings"].iterrows():
            add("bookings", r["booking_id"], r, [r["client_name"]],
                email=r["email_norm"] if r["email_valid"] else "", business=r["client_name"])
    if "invoices" in frames:
        for _, r in frames["invoices"].iterrows():
            add("invoices", r["invoice_no"], r, [r["bill_to"]],
                email=r["email_norm"] if r["email_valid"] else "", business=r["bill_to"])
    if "payments" in frames:
        for _, r in frames["payments"].iterrows():
            add("payments", r["payment_id"], r, [r["payer"]],
                email=r["email_norm"] if r["email_valid"] else "", business=r["payer"])
    if "production" in frames:
        for _, r in frames["production"].iterrows():
            add("production", r["video_id"], r, [r["client"]],
                booking_ref=str(r["shoot_booking"]).strip(), business=r["client"])
    if "messages" in frames:
        for _, r in frames["messages"].iterrows():
            if r["contact_other"]:          # broadcasts etc. are not a party
                continue
            add("messages", r["message_key"], r, [r["contact_name"]],
                email=r["email_norm"] if r["email_valid"] else "", phone=r["phone_norm"],
                handle=r["handle_key"], person=r["contact_name"])
    cols = ["mention_id", "source", "record_id", "source_file", "source_line", "raw_names",
            "keys", "tiers", "person_key", "business_key", "email", "phone", "handle", "booking_ref"]
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass
class ResolutionResult:
    clients: pd.DataFrame   # one row per resolved party
    links: pd.DataFrame     # one row per mention -> client, with method + confidence
    review: pd.DataFrame    # things a human must decide

    def client_of(self, source: str, record_id: str) -> str:
        hit = self.links[(self.links.source == source) & (self.links.record_id == record_id)]
        return hit.client_id.iloc[0] if len(hit) else ""

    def records_for(self, client_id: str) -> pd.DataFrame:
        return self.links[self.links.client_id == client_id]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def load_decisions(path: str | None) -> pd.DataFrame:
    """Owner decisions from the review list.

    Columns: value_type (name|email|phone|handle), value, target_email,
    decision (same|different), note.
    'same'      -> anything carrying `value` belongs to the client that owns target_email
    'different' -> never link `value` to that client automatically."""
    cols = ["value_type", "value", "target_email", "decision", "note"]
    if not path:
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return pd.DataFrame(columns=cols)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]


def resolve_clients(frames: dict[str, pd.DataFrame], decisions: pd.DataFrame | None = None) -> ResolutionResult:
    m = build_mentions(frames)
    decisions = decisions if decisions is not None else load_decisions(None)
    uf = UnionFind()
    method: dict[str, tuple[str, float, str]] = {}      # mention -> (method, confidence, note)
    review: list[dict] = []
    for mid in m.mention_id:
        uf.find(mid)

    def add_review(kind, detail, candidates=(), records=(), score=None):
        review.append({"kind": kind, "detail": detail,
                       "candidates": " | ".join(sorted(set(candidates))),
                       "records": " | ".join(records), "score": score})

    # ---- 1. exact email -------------------------------------------------
    by_email = defaultdict(list)
    for _, r in m.iterrows():
        if r.email:
            by_email[r.email].append(r.mention_id)
            method[r.mention_id] = ("email", 100.0, "")
    for ids in by_email.values():
        for x in ids[1:]:
            uf.union(ids[0], x)

    # ---- 1b. contact-change announcements -------------------------------
    # A known contact writing "our new number is 214-555-0121" or
    # "New email is trent@..." is explicit evidence that the new detail
    # belongs to the same client. A referral ("his number is ... call him")
    # has no 'new ... is' cue and is ignored.
    announced = []
    msgs = frames.get("messages")
    if msgs is not None and len(msgs):
        for _, r in msgs.iterrows():
            if r["direction"] != "inbound" or not (r["email_norm"] or r["phone_norm"]):
                continue
            text = str(r["text"])
            if not ANNOUNCE_CUE.search(text):
                continue
            sender = f"messages:{r['message_key']}"
            for e in EMAIL_IN_TEXT.findall(text):
                e = e.lower().rstrip(".")
                if e != r["email_norm"]:
                    announced.append((sender, "email", e, r))
            for p_ in PHONE_IN_TEXT.findall(text):
                digits = re.sub(r"\D", "", p_)[-10:]
                if len(digits) == 10 and digits != r["phone_norm"]:
                    announced.append((sender, "phone", digits, r))
    announced_notes = {}
    for sender, kind, value, r in announced:
        hits = [x for x in m.mention_id if (m.loc[m.mention_id == x, kind].iloc[0] == value)]
        for x in hits:
            uf.union(sender, x)
            announced_notes[x] = (f"{kind} {value} announced by the client in a message on "
                                  f"{r['timestamp']} ({r['_source_file']} line {r['_source_line']})")

    # cluster summary helpers (recomputed when needed; data is small)
    def members():
        g = defaultdict(list)
        for mid in m.mention_id:
            g[uf.find(mid)].append(mid)
        return g

    mrow = m.set_index("mention_id")

    def stems(ids):
        return {domain_stem(mrow.at[i, "email"]) for i in ids if mrow.at[i, "email"]} - {""}

    def business_keys(ids):
        return {mrow.at[i, "business_key"] for i in ids if mrow.at[i, "business_key"]}

    def compatible(ids_a, ids_b) -> tuple[bool, str]:
        """Do two groups of mentions describe the same organisation?"""
        sa, sb = stems(ids_a), stems(ids_b)
        if sa and sb:
            if sa & sb:
                return True, "same email domain"
            return False, f"different email domains ({', '.join(sorted(sa))} vs {', '.join(sorted(sb))})"
        ba, bb = business_keys(ids_a), business_keys(ids_b)
        if not ba or not bb:
            return True, "one side has no business name to contradict"
        best = max(name_score(x, y) for x in ba for y in bb)
        return (best >= FUZZY_REVIEW, f"best business-name similarity {best:.0f}")

    blocked = set()   # (value, target_email) pairs the owner said are different
    for _, d in decisions.iterrows():
        if d.decision.strip().lower() == "different":
            blocked.add((d.value_type.strip().lower(), d.value.strip().lower(), d.target_email.strip().lower()))

    def is_blocked(vtype, value, ids_target):
        emails = {mrow.at[i, "email"] for i in ids_target if mrow.at[i, "email"]}
        return any((vtype, value, e) in blocked for e in emails)

    # ---- 2. exact phone -------------------------------------------------
    by_phone = defaultdict(list)
    for _, r in m.iterrows():
        if r.phone:
            by_phone[r.phone].append(r.mention_id)
    for phone, ids in by_phone.items():
        for x in ids:
            if x not in method:
                method[x] = ("phone", 100.0, "")
        g = members()
        roots = sorted({uf.find(i) for i in ids})
        for other in roots[1:]:
            ra = uf.find(roots[0])
            rb = uf.find(other)
            ok, why = compatible(g[ra], g[rb])
            if ok and not is_blocked("phone", phone, g[rb]):
                small = g[ra] if len(g[ra]) < len(g[rb]) else g[rb]
                uf.union(ra, rb)
                for x in small:      # the smaller group reached the client through the phone
                    method[x] = ("phone", 100.0, f"joined through shared phone {phone} ({why})")
                g = members()
            else:
                add_review("phone_conflict",
                           f"phone {phone} is used by records that look like different clients ({why})",
                           candidates=[mrow.at[i, "raw_names"] for i in g[ra] + g[uf.find(other)]],
                           records=[i for i in ids])

    for x, note in announced_notes.items():
        method[x] = ("announced_alias", 100.0, note)

    # ---- 3. confirmed aliases ------------------------------------------
    email_root = lambda e: uf.find(by_email[e][0]) if e in by_email else None
    for _, d in decisions.iterrows():
        if d.decision.strip().lower() != "same":
            continue
        target = email_root(d.target_email.strip().lower())
        if target is None:
            continue
        vtype, value = d.value_type.strip().lower(), d.value.strip()
        for _, r in m.iterrows():
            hit = ((vtype == "name" and name_key(value) in r["keys"]) or
                   (vtype == "email" and r.email == value.lower()) or
                   (vtype == "phone" and r.phone and r.phone == "".join(ch for ch in value if ch.isdigit())[-10:]) or
                   (vtype == "handle" and r.handle and r.handle == value.lower().lstrip("@")))
            if hit:
                uf.union(target, r.mention_id)
                method[r.mention_id] = ("confirmed_alias", 100.0, d.note)

    # ---- 4a. exact name between groups that both have contact details ---
    def anchored(ids):
        return any(mrow.at[i, "email"] or mrow.at[i, "phone"] for i in ids)

    reviewed_pairs = set()
    g = members()
    key_roots = defaultdict(set)
    for root, ids in g.items():
        if not anchored(ids):
            continue
        for i in ids:
            for k, tier in mrow.at[i, "tiers"].items():
                if tier < 2 or len(k.split()) >= 2:      # skip lone first names
                    key_roots[k].add(root)
    for key, roots in sorted(key_roots.items()):
        roots = sorted({uf.find(r) for r in roots})
        if len(roots) < 2:
            continue
        g = members()
        base = roots[0]
        for other in roots[1:]:
            a, b = uf.find(base), uf.find(other)
            if a == b:
                continue
            ok, why = compatible(g[a], g[b])
            if ok and not is_blocked("name", key, g[b]) and not is_blocked("name", key, g[a]):
                small = g[a] if len(g[a]) < len(g[b]) else g[b]
                uf.union(a, b)
                for i in small:      # the smaller group reached the client through the name
                    if method.get(i, ("",))[0] != "confirmed_alias":
                        method[i] = ("exact_name", 100.0, f"joined on exact name '{key}' ({why})")
                g = members()
            else:
                pair = tuple(sorted((a, b)))
                if pair in reviewed_pairs:
                    continue
                reviewed_pairs.add(pair)
                add_review("same_name_different_contact",
                           f"the name '{key}' is used by two groups with conflicting contact details "
                           f"({why}); not merged",
                           candidates=[mrow.at[i, "raw_names"] for i in g[a] + g[b] if key in mrow.at[i, "keys"]],
                           records=[i for i in g[a] + g[b] if key in mrow.at[i, "keys"]][:20])

    # ---- production rows that cite their booking -----------------------
    g = members()
    booking_root = {mrow.at[i, "record_id"]: uf.find(i) for i in m.mention_id if mrow.at[i, "source"] == "bookings"}
    for _, r in m[(m.source == "production") & (m.booking_ref != "")].iterrows():
        if r.booking_ref in booking_root:
            uf.union(booking_root[r.booking_ref], r.mention_id)
            method[r.mention_id] = ("booking_reference", 100.0, f"shoot_booking {r.booking_ref}")
        else:
            method[r.mention_id] = ("unmatched", 0.0, f"shoot_booking {r.booking_ref} not found")

    # ---- 4b + 5. name-only mentions (no email, phone or booking) --------
    g = members()
    alias_index = defaultdict(set)      # name key -> roots of anchored groups
    tier_index = [defaultdict(set), defaultdict(set), defaultdict(set)]
    compact_index = defaultdict(set)    # compact strings (names and domain stems) for handles
    for root, ids in g.items():
        if not anchored(ids):
            continue
        for i in ids:
            for k, tier in mrow.at[i, "tiers"].items():
                alias_index[k].add(root)
                tier_index[tier][k].add(root)
                compact_index[k.replace(" ", "")].add(root)
            st = domain_stem(mrow.at[i, "email"]) if mrow.at[i, "email"] else ""
            if st:
                compact_index[st].add(root)

    def label(root):
        names = Counter()
        for i in g[root]:
            for n in mrow.at[i, "raw_names"].split(" | "):
                if n:
                    names[n] += 1
        return names.most_common(1)[0][0] if names else root

    pending = [i for i in m.mention_id if i not in method and len(g[uf.find(i)]) == 1]
    for mid in pending:
        r = mrow.loc[mid]
        keys = r["keys"]
        if r.handle:
            cands = {}
            for s_, roots in compact_index.items():
                sc = handle_score(r.handle, s_)
                for root in roots:
                    cands[uf.find(root)] = max(cands.get(uf.find(root), 0), sc)
        elif keys:
            exact = set()
            for tier in range(3):        # self-description beats other spellings beats person names
                exact = {uf.find(x) for k in keys for x in tier_index[tier].get(k, set())}
                if exact:
                    break
            if len(exact) == 1:
                root = exact.pop()
                uf.union(root, mid)
                method[mid] = ("exact_name", 100.0, "")
                continue
            if len(exact) > 1:
                method[mid] = ("unmatched", 0.0, "name used by several clients")
                add_review("ambiguous_name", f"'{r.raw_names}' exactly matches several clients",
                           candidates=[label(x) for x in exact], records=[mid])
                continue
            if len(keys) == 1 and len(keys[0].split()) == 1 and r.source == "messages":
                method[mid] = ("unmatched", 0.0, "first name only")
                tok = keys[0]
                cands = [label(x) for k, roots in alias_index.items() if tok in k.split() for x in roots]
                add_review("first_name_only",
                           f"message signed only '{r.raw_names}' from an unknown number/address; "
                           f"cannot be linked safely", candidates=cands, records=[mid])
                continue
            cands = {}
            for k in keys:
                for ak, roots in alias_index.items():
                    sc = name_score(k, ak)
                    if sc >= FUZZY_REVIEW - 10:
                        for root in roots:
                            cands[uf.find(root)] = max(cands.get(uf.find(root), 0), sc)
        else:
            method[mid] = ("unmatched", 0.0, "no name, email or phone")
            continue
        ranked = sorted(cands.items(), key=lambda t: -t[1])
        best = ranked[0] if ranked else (None, 0)
        second = ranked[1][1] if len(ranked) > 1 else 0
        if best[0] is not None and best[1] >= FUZZY_AUTO_LINK and best[1] - second >= FUZZY_MARGIN:
            uf.union(best[0], mid)
            method[mid] = ("fuzzy", round(best[1], 1), f"runner-up score {second:.0f}")
        elif best[0] is not None and best[1] >= FUZZY_REVIEW:
            method[mid] = ("unmatched", round(best[1], 1), "uncertain fuzzy match")
            add_review("fuzzy_uncertain",
                       f"'{r.raw_names or r.handle}' resembles an existing client but not clearly enough "
                       f"(best {best[1]:.0f}, runner-up {second:.0f})",
                       candidates=[f"{label(x)} ({s:.0f})" for x, s in ranked[:3]], records=[mid],
                       score=round(best[1], 1))
        else:
            method[mid] = ("unmatched", round(best[1], 1) if best[0] else 0.0, "no similar client")

    # ---- final groups -> client ids ------------------------------------
    g = members()
    unresolved = {mid for mid, (meth, _, _) in method.items() if meth == "unmatched"}
    root_to_client = {}
    for root, ids in g.items():
        real = [i for i in ids if i not in unresolved]
        if not real:
            continue
        emails = sorted({mrow.at[i, "email"] for i in real if mrow.at[i, "email"]})
        phones = sorted({mrow.at[i, "phone"] for i in real if mrow.at[i, "phone"]})
        anchor = emails[0] if emails else phones[0] if phones else min(
            k for i in real for k in mrow.at[i, "keys"]) if any(mrow.at[i, "keys"] for i in real) else root
        root_to_client[root] = "CL-" + hashlib.sha1(anchor.encode()).hexdigest()[:8]

    links = []
    for _, r in m.iterrows():
        meth, conf, note = method.get(r.mention_id, ("unmatched", 0.0, ""))
        cid = "" if r.mention_id in unresolved else root_to_client.get(uf.find(r.mention_id), "")
        links.append({"mention_id": r.mention_id, "source": r.source, "record_id": r.record_id,
                      "source_file": r.source_file, "source_line": r.source_line,
                      "raw_names": r.raw_names, "email": r.email, "phone": r.phone, "handle": r.handle,
                      "client_id": cid, "method": meth, "confidence": conf, "note": note,
                      "needs_review": meth == "unmatched"})
    links = pd.DataFrame(links, columns=["mention_id", "source", "record_id", "source_file", "source_line",
                                         "raw_names", "email", "phone", "handle", "client_id", "method",
                                         "confidence", "note", "needs_review"])

    # ---- email/name conflicts (e.g. an agency booking for its customer) --
    lead_keys = defaultdict(set)    # how each client describes itself in leads
    for _, r in links[links.source == "leads"].iterrows():
        if r.client_id:
            lead_keys[r.client_id].update(mrow.at[r.mention_id, "keys"])
    owner_of_key = defaultdict(set)
    for cid, ks in lead_keys.items():
        for k in ks:
            owner_of_key[k].add(cid)
    for idx, r in links.iterrows():
        if not r.client_id or r.source == "leads" or r.method not in ("email", "booking_reference"):
            continue
        for k in mrow.at[r.mention_id, "keys"]:
            others = owner_of_key.get(k, set()) - {r.client_id}
            if others and k not in lead_keys.get(r.client_id, set()):
                links.at[idx, "needs_review"] = True
                links.at[idx, "note"] = (f"name '{r.raw_names}' is how another client describes itself; "
                                         f"kept with the {r.method} match")
                add_review("email_name_conflict",
                           f"{r.mention_id} uses {r.email or 'a booking'} of one client but the name of another",
                           candidates=[r.client_id] + sorted(others), records=[r.mention_id])

    # ---- client table ---------------------------------------------------
    clients = []
    for root, cid in root_to_client.items():
        ids = [i for i in g[root] if i not in unresolved]
        sub = m[m.mention_id.isin(ids)]
        spellings = Counter()
        biz = Counter()
        for _, r in sub.iterrows():
            for n in r.raw_names.split(" | "):
                if n:
                    spellings[n] += 1
            if r.source in ("leads", "invoices", "bookings") and r.business_key:
                for n in r.raw_names.split(" | ")[-1:]:
                    biz[n] += 1
        contacts = sorted({e for e in sub.email if e}) + sorted({p for p in sub.phone if p}) + \
            sorted({"@" + h for h in sub.handle if h})
        display = (biz or spellings).most_common(1)[0][0] if (biz or spellings) else (
            contacts[0] if contacts else cid)
        counts = Counter(sub.source)
        if counts.get("bookings") or counts.get("invoices") or counts.get("production"):
            role = "client"
        elif counts.get("payments"):
            role = "payer_only"
        elif counts.get("leads"):
            role = "lead_only"
        else:
            role = "message_only"
        clients.append({
            "client_id": cid, "display_name": display, "role": role,
            "emails": ", ".join(sorted({e for e in sub.email if e})),
            "phones": ", ".join(sorted({p for p in sub.phone if p})),
            "handles": ", ".join(sorted({h for h in sub.handle if h})),
            "names_used": ", ".join(sorted(spellings)),
            "n_leads": counts.get("leads", 0), "n_bookings": counts.get("bookings", 0),
            "n_invoices": counts.get("invoices", 0), "n_payments": counts.get("payments", 0),
            "n_videos": counts.get("production", 0), "n_messages": counts.get("messages", 0),
            "has_contact_details": bool(sub.email.any() or sub.phone.any()),
        })
    clients = pd.DataFrame(clients).sort_values("display_name").reset_index(drop=True) if clients else \
        pd.DataFrame(columns=["client_id", "display_name"])

    # ---- unknown numbers/addresses signed with a first name only --------
    first_names = defaultdict(set)
    for _, r in m.iterrows():
        if r.person_key and r.source == "leads":
            first_names[r.person_key.split()[0]].add(root_to_client.get(uf.find(r.mention_id)))
    for row in clients.itertuples():
        if row.role != "message_only":
            continue
        for _, r in m[m.mention_id.isin(g[next(k for k, v in root_to_client.items() if v == row.client_id)])].iterrows():
            tok = r.person_key.split()[0] if r.person_key else ""
            if tok and len(r.person_key.split()) == 1 and first_names.get(tok):
                add_review("unknown_contact_first_name",
                           f"{r.phone or r.email or r.handle} is not on file and signs only '{r.raw_names}'; "
                           f"it may belong to an existing client",
                           candidates=sorted(c for c in first_names[tok] if c), records=[r.mention_id])
                break

    # ---- possible duplicate clients (never merged automatically) --------
    names_by_client = defaultdict(set)
    for _, r in links[links.client_id != ""].iterrows():
        names_by_client[r.client_id].add(mrow.at[r.mention_id, "business_key"])
    ids = sorted(names_by_client)
    # pairs already explained by another review item are not repeated
    root_of_client = {c: r for r, c in root_to_client.items()}
    already = set()
    for item in review:
        recs = [x for x in item["records"].split(" | ") if x]
        cl = {root_to_client.get(uf.find(x)) for x in recs if x in mrow.index} - {None}
        cl |= {c for c in item["candidates"].split(" | ") if c.startswith("CL-")}
        for a in cl:
            for b in cl:
                if a < b:
                    already.add((a, b))
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if (a, b) in already:
                continue
            best, pair = 0.0, ("", "")
            for x in names_by_client[a] - {""}:
                for y in names_by_client[b] - {""}:
                    sc = duplicate_score(x, y)
                    if sc > best:
                        best, pair = sc, (x, y)
            if best >= DUPLICATE_CLIENT_SCORE:
                add_review("possible_duplicate_clients",
                           f"'{pair[0]}' and '{pair[1]}' are similar ({best:.0f}) but belong to separate "
                           f"clients; not merged", candidates=[a, b], score=round(best, 1))

    review_df = pd.DataFrame(review, columns=["kind", "detail", "candidates", "records", "score"])
    review_df = review_df.drop_duplicates(subset=["kind", "detail"]).reset_index(drop=True)
    review_df.insert(0, "review_id", [f"R{i + 1:03d}" for i in range(len(review_df))])
    return ResolutionResult(clients=clients, links=links, review=review_df)
