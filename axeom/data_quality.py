"""Data quality report.

Every issue points at a source file, a line and a record id, so it can be
checked by opening the original file. The report describes problems; it never
"fixes" them silently.

Columns of the report
---------------------
issue_type  e.g. invalid_date, missing_reference, conflicting_record ...
severity    high   = money or client identity may be wrong
            medium = a record cannot be trusted or connected as it stands
            low    = worth knowing, not urgent
source_file, line, record_id, field, raw_value, detail
"""
from __future__ import annotations

import re
from collections import defaultdict

import pandas as pd

from .config import REFERENCE_DATE, SOURCES
from .entity_resolution import ResolutionResult, domain_stem

COLUMNS = ["issue_type", "severity", "source_file", "line", "record_id", "field", "raw_value", "detail"]
_REF = pd.Timestamp(REFERENCE_DATE)
_INV_RE = re.compile(r"^INV-\d+$", re.I)
_BOOKING_IN_MEMO = re.compile(r"\b(B\d{3,})\b")


class _Issues:
    def __init__(self):
        self.rows = []

    def add(self, issue_type, severity, row, record_id, field="", raw_value="", detail=""):
        self.rows.append({
            "issue_type": issue_type, "severity": severity,
            "source_file": row.get("_source_file", "") if row is not None else "",
            "line": row.get("_source_line", "") if row is not None else "",
            "record_id": record_id, "field": field, "raw_value": raw_value, "detail": detail})

    def frame(self):
        df = pd.DataFrame(self.rows, columns=COLUMNS)
        order = {"high": 0, "medium": 1, "low": 2}
        return df.sort_values(by=["severity", "issue_type", "source_file", "record_id"],
                              key=lambda s: s.map(order) if s.name == "severity" else s.astype(str),
                              kind="stable").reset_index(drop=True)


def _id(source, row):
    col = SOURCES[source]["id_column"]
    return row["message_key"] if source == "messages" else row[col]


# ---------------------------------------------------------------------------
# Checks on single files
# ---------------------------------------------------------------------------
def _field_checks(source, df, iss):
    spec = SOURCES[source]
    for col in spec["date_columns"]:
        for _, r in df.iterrows():
            st = r[f"{col}_date_status"]
            if st == "invalid":
                iss.add("invalid_date", "medium", r, _id(source, r), col, r[col], "not a real date")
            elif st == "ambiguous":
                iss.add("ambiguous_date", "medium", r, _id(source, r), col, r[col], r[f"{col}_date_note"])
            elif st == "assumed_source_convention":
                iss.add("assumed_date_format", "low", r, _id(source, r), col, r[col], r[f"{col}_date_note"])
    for col in spec["money_columns"]:
        for _, r in df.iterrows():
            st = r[f"{col}_value_status"]
            if st == "invalid":
                iss.add("invalid_amount", "high", r, _id(source, r), col, r[col], "not a number")
            elif st == "blank":
                iss.add("missing_amount", "medium", r, _id(source, r), col, "",
                        "amount is blank; treated as unknown, not as 0")
            elif r[f"{col}_value"] < 0:
                iss.add("suspicious_amount", "medium", r, _id(source, r), col, r[col], "negative amount")
    if "email_valid" in df.columns:
        email_col = "payer_email" if source == "payments" else "contact_email" if source == "messages" else "email"
        for _, r in df[~df["email_valid"]].iterrows():
            iss.add("invalid_email", "medium", r, _id(source, r), email_col, r[email_col], "not a valid email address")
        if source in ("payments", "bookings", "invoices"):
            for _, r in df[df["email_norm"] == ""].iterrows():
                iss.add("missing_identifier", "medium", r, _id(source, r), email_col, "",
                        "no email; the record can only be linked by name")
    if source == "leads":
        for _, r in df.iterrows():
            if str(r["phone"]).strip() and not r["phone_norm"]:
                iss.add("invalid_phone", "low", r, r["lead_id"], "phone", r["phone"], "not a 10-digit US number")
    if "status_known" in df.columns:
        col = "stage" if source == "production" else "status"
        for _, r in df[~df["status_known"]].iterrows():
            iss.add("unknown_status", "medium", r, _id(source, r), col, r[col], "status value not recognised")


def _date_logic(frames, iss):
    """Dates that cannot be right relative to the reference date or to each other."""
    after = {"leads": ["created", "first_reply"], "invoices": ["issued"], "payments": ["date"],
             "production": ["client_approved_on"]}
    for source, cols in after.items():
        if source not in frames:
            continue
        df = frames[source]
        for col in cols:
            for _, r in df[df[f"{col}_date"] > _REF + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)].iterrows():
                iss.add("date_after_reference_date", "medium", r, _id(source, r), col, r[col],
                        f"later than the reference date {REFERENCE_DATE.isoformat()}")
    if "messages" in frames:
        df = frames["messages"]
        for _, r in df[df["timestamp_date"] > _REF + pd.Timedelta(days=1)].iterrows():
            iss.add("date_after_reference_date", "medium", r, r["message_key"], "timestamp", r["timestamp"],
                    f"later than the reference date {REFERENCE_DATE.isoformat()}")
    if "leads" in frames:
        df = frames["leads"]
        bad = df[(df["first_reply_date"].notna()) &
                 (df["first_reply_date"] < df["created_date"].dt.normalize())]
        for _, r in bad.iterrows():
            iss.add("invalid_date", "medium", r, r["lead_id"], "first_reply", r["first_reply"],
                    "first reply is before the lead was created")
    if "invoices" in frames:
        df = frames["invoices"]
        for _, r in df[df["due_date"] < df["issued_date"]].iterrows():
            iss.add("invalid_date", "medium", r, r["invoice_no"], "due", r["due"], "due date before issue date")
    if "bookings" in frames:
        df = frames["bookings"]
        for _, r in df.iterrows():
            d, st = r["date_date"], r["status_norm"]
            if pd.isna(d):
                continue
            if st == "upcoming" and d < _REF:
                iss.add("conflicting_record", "medium", r, r["booking_id"], "status", r["status"],
                        f"marked upcoming but the date {d.date()} is before the reference date")
            if st == "completed" and d > _REF:
                iss.add("conflicting_record", "medium", r, r["booking_id"], "status", r["status"],
                        f"marked completed but the date {d.date()} is after the reference date")


def _duplicates(frames, iss):
    """Same id twice, and different ids that describe the same thing."""
    for source, df in frames.items():
        col = SOURCES[source]["id_column"] if source != "messages" else "message_key"
        if not col:
            continue
        ids = df[col].astype(str).str.strip().str.upper()
        for _, r in df[ids.duplicated(keep=False)].iterrows():
            iss.add("duplicate_record", "high", r, r[col], col, r[col], "the same id appears more than once")

    def same(source, cols, detail, severity="medium", where=None):
        if source not in frames:
            return
        df = frames[source] if where is None else frames[source][where(frames[source])]
        df = df[df[cols].astype(str).apply(lambda c: c.str.strip() != "").all(axis=1)]
        for _, grp in df.groupby(cols):
            if len(grp) < 2:
                continue
            ids = ", ".join(str(_id(source, r)) for _, r in grp.iterrows())
            for _, r in grp.iterrows():
                iss.add("possible_duplicate_record", severity, r, _id(source, r), "+".join(cols), "",
                        f"{detail}: {ids}")

    same("leads", ["email_norm"], "several leads with the same email", "low")
    same("bookings", ["email_norm", "date_date", "service"], "same client, date and service")
    same("invoices", ["email_norm", "issued", "amount_value", "memo"], "same email, issue date, amount and memo")
    same("payments", ["email_norm", "date_date", "amount_value"], "same payer, date and amount")
    same("production", ["client", "title", "shoot_booking"], "same client, title and shoot booking")


# ---------------------------------------------------------------------------
# Checks across files (money and references)
# ---------------------------------------------------------------------------
def _references(frames, iss):
    inv = frames.get("invoices")
    pay = frames.get("payments")
    bk = frames.get("bookings")
    prod = frames.get("production")
    inv_by_no = {str(r["invoice_no"]).strip().upper(): r for _, r in inv.iterrows()} if inv is not None else {}
    bk_by_id = {str(r["booking_id"]).strip().upper(): r for _, r in bk.iterrows()} if bk is not None else {}

    paid_by = defaultdict(list)
    if pay is not None:
        all_inv_amounts = set(inv["amount_value"].dropna()) if inv is not None else set()
        for _, p in pay.iterrows():
            ref = str(p["reference"]).strip()
            if not ref:
                iss.add("payment_without_reference", "medium", p, p["payment_id"], "reference", "",
                        "no invoice number; the payment cannot be tied to an invoice without a human check")
            elif _INV_RE.match(ref):
                if ref.upper() not in inv_by_no:
                    iss.add("missing_reference", "high", p, p["payment_id"], "reference", ref,
                            "refers to an invoice that does not exist")
                else:
                    paid_by[ref.upper()].append(p)
            else:
                iss.add("unstructured_reference", "medium", p, p["payment_id"], "reference", ref,
                        "free-text reference; it may cover several invoices and needs a human check")
            if inv is not None and pd.notna(p["amount_value"]) and p["amount_value"] not in all_inv_amounts:
                iss.add("suspicious_amount", "low", p, p["payment_id"], "amount", p["amount"],
                        "does not equal any single invoice amount (several invoices at once, or a part payment?)")

    if inv is not None:
        for no, r in inv_by_no.items():
            pays = paid_by.get(no, [])
            st = r["status_norm"]
            total = sum(p["amount_value"] for p in pays if pd.notna(p["amount_value"]))
            ids = ", ".join(p["payment_id"] for p in pays)
            if len(pays) > 1:
                iss.add("conflicting_record", "high", r, r["invoice_no"], "invoice_no", r["invoice_no"],
                        f"referenced by {len(pays)} payments ({ids}) totalling {total:.2f} "
                        f"against an invoice of {r['amount_value']:.2f}")
            if pays and st in ("unpaid", "partial", "void"):
                iss.add("conflicting_record", "high", r, r["invoice_no"], "status", r["status"],
                        f"billing tool says '{r['status']}' but payment(s) {ids} reference this invoice "
                        f"({total:.2f} received)")
            if len(pays) == 1 and pd.notna(r["amount_value"]) and abs(total - r["amount_value"]) > 0.005:
                iss.add("conflicting_record", "medium", r, r["invoice_no"], "amount", r["amount"],
                        f"payment {ids} is {total:.2f}, invoice is {r['amount_value']:.2f}")
            if st == "paid" and not pays:
                iss.add("unverified_status", "low", r, r["invoice_no"], "status", r["status"],
                        "marked paid but no payment names this invoice; payment not confirmed by the bank file")
            for b in _BOOKING_IN_MEMO.findall(str(r["memo"])):
                if bk is not None and b.upper() not in bk_by_id:
                    iss.add("missing_reference", "medium", r, r["invoice_no"], "memo", r["memo"],
                            f"memo mentions booking {b}, which does not exist")
                elif bk is not None and bk_by_id[b.upper()]["status_norm"] == "cancelled":
                    iss.add("conflicting_record", "medium", r, r["invoice_no"], "memo", r["memo"],
                            f"invoice for booking {b}, which is cancelled")

    if prod is not None:
        for _, r in prod.iterrows():
            b = str(r["shoot_booking"]).strip()
            if b and bk is not None and b.upper() not in bk_by_id:
                iss.add("missing_reference", "medium", r, r["video_id"], "shoot_booking", b,
                        "refers to a booking that does not exist")
            elif b and bk is not None and bk_by_id[b.upper()]["status_norm"] == "cancelled":
                iss.add("conflicting_record", "medium", r, r["video_id"], "shoot_booking", b,
                        "video belongs to a cancelled booking")
            stage = r["status_norm"]
            if stage == "delivered" and pd.isna(r["client_approved_on_date"]):
                iss.add("missing_value", "low", r, r["video_id"], "client_approved_on", "",
                        "delivered but no client approval date")
            if stage != "delivered" and pd.notna(r["client_approved_on_date"]):
                iss.add("conflicting_record", "medium", r, r["video_id"], "stage", r["stage"],
                        "client approval date present but the video is not delivered")
            if pd.isna(r["revisions_value"]) or pd.isna(r["editor_hours_value"]):
                iss.add("invalid_number", "low", r, r["video_id"], "revisions/editor_hours",
                        f"{r['revisions']}/{r['editor_hours']}", "not a number")

    if bk is not None:
        # prices that differ from the usual price of the same service in this data
        usual = bk.groupby("service")["price_value"].agg(lambda s: s.mode().iloc[0] if len(s.mode()) else None)
        for _, r in bk.iterrows():
            v, u = r["price_value"], usual.get(r["service"])
            if pd.isna(v) or u is None:
                continue
            if v == 0:
                iss.add("suspicious_amount", "low", r, r["booking_id"], "price", r["price"],
                        "price is 0 (may be covered by a retainer; needs evidence)")
            elif v != u:
                iss.add("suspicious_amount", "low", r, r["booking_id"], "price", r["price"],
                        f"usual price for {r['service']} in this data is {u:.2f}")


def _email_typos(frames, iss):
    """An email that appears once and looks like a typo of a common one
    (hello@haledental.con vs hello@haledental.com)."""
    counts = defaultdict(int)
    where = defaultdict(list)
    for source in ("leads", "bookings", "invoices", "payments", "messages"):
        if source not in frames or "email_norm" not in frames[source]:
            continue
        for _, r in frames[source].iterrows():
            e = r["email_norm"]
            if e:
                counts[e] += 1
                where[e].append((source, r))
    by_local_stem = defaultdict(list)
    for e in counts:
        if "@" in e:
            by_local_stem[(e.split("@")[0], domain_stem(e) or e.split("@")[1])].append(e)
    for variants in by_local_stem.values():
        if len(variants) < 2:
            continue
        main = max(variants, key=lambda e: counts[e])
        for e in variants:
            if e == main:
                continue
            for source, r in where[e]:
                iss.add("suspicious_email", "medium", r, _id(source, r), "email", e,
                        f"probably a typo of {main} (used {counts[main]} times)")


def _resolution_issues(res: ResolutionResult, iss):
    for _, l in res.links.iterrows():
        row = {"_source_file": l.source_file, "_source_line": l.source_line}
        if not l.client_id:
            iss.add("unmatched_client", "medium" if l.source != "messages" else "low", row, l.record_id,
                    "", l.raw_names or l.email or l.phone or l.handle, l.note or "not linked to any client")
        elif l.needs_review:
            iss.add("identity_conflict", "high", row, l.record_id, "", l.raw_names, l.note)
    for _, r in res.review.iterrows():
        kind = "possible_client_duplicate" if r.kind in (
            "possible_duplicate_clients", "same_name_different_contact", "phone_conflict") else "identity_review"
        iss.add(kind, "high" if kind == "possible_client_duplicate" else "medium", None, r.review_id,
                r.kind, r.candidates, r.detail)


# ---------------------------------------------------------------------------
def build_quality_report(frames: dict[str, pd.DataFrame], resolution: ResolutionResult | None = None) -> pd.DataFrame:
    """Run every check and return one table of issues."""
    iss = _Issues()
    for source, df in frames.items():
        _field_checks(source, df, iss)
    _date_logic(frames, iss)
    _duplicates(frames, iss)
    _references(frames, iss)
    _email_typos(frames, iss)
    if resolution is not None:
        _resolution_issues(resolution, iss)
    return iss.frame()


def summarize(report: pd.DataFrame) -> pd.DataFrame:
    """Counts per issue type and severity, for a quick overview."""
    if report.empty:
        return pd.DataFrame(columns=["issue_type", "severity", "count"])
    out = report.groupby(["severity", "issue_type"]).size().rename("count").reset_index()
    out["_order"] = out["severity"].map({"high": 0, "medium": 1, "low": 2})
    return out.sort_values(["_order", "count"], ascending=[True, False]).drop(columns="_order") \
        .reset_index(drop=True)
