"""Normalising names, emails, phones, dates, money and statuses.

Golden rule: the original column is never changed. Every normalised value is
written to a NEW column next to it, together with a status column that says
how the value was obtained. Missing values stay missing (never become 0).
"""
from __future__ import annotations

import re
import unicodedata

import pandas as pd

from .config import SOURCES, STATUS_VALUES

# ---------------------------------------------------------------------------
# Emails and phones
# ---------------------------------------------------------------------------
_EMAIL_OK = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$")


def normalize_email(value) -> str:
    """Lower-case and trim. Returns '' for blanks. Invalid emails are returned
    as-is (lower-cased) so they can be reported; use is_valid_email() to check."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip().lower()


def is_valid_email(value: str) -> bool:
    return bool(_EMAIL_OK.match(value or ""))


def normalize_phone(value) -> str:
    """Reduce a phone number to its 10 US digits ('' if it is not one).
    '+1 972-555-0186', '(972) 555-0186' and '972.555.0186' all become '9725550186'."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------
_TITLES = {"dr", "mr", "mrs", "ms", "miss", "pastor", "rev"}
_TRAILING = {"llc", "pllc", "inc", "ltd", "corp", "corporation", "company", "co",
             "dds", "md", "dc", "pc", "lp", "llp"}


def name_key(value) -> str:
    """A comparison key for a client / business / person name.

    'Jared's Roofing LLC' -> 'jareds roofing'
    'Dr. Alana Hale'      -> 'alana hale'
    'Calm & Collected'    -> 'calm and collected'
    'The Parkside Lab'    -> 'parkside lab'
    Only used for matching; the original spelling is always kept."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = unicodedata.normalize("NFKD", str(value))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"['’`]", "", s)            # apostrophes: jared's -> jareds
    s = re.sub(r"[^a-z0-9]+", " ", s)            # punctuation -> space
    words = s.split()
    while words and words[0] in _TITLES | {"the"}:
        words = words[1:]
    while words and words[-1] in _TRAILING:
        words = words[:-1]
    return " ".join(words)


def handle_key(value) -> str:
    """Instagram handle -> comparable text: '@juniperwealthpartntx' -> 'juniperwealthpartntx'."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------
def parse_money(value) -> tuple[float | None, str]:
    """'$1,500' -> (1500.0, 'ok'); '' -> (None, 'blank'); 'abc' -> (None, 'invalid').
    Parentheses mean negative: '(300)' -> (-300.0, 'ok'). Never turns a blank into 0."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None, "blank"
    s = str(value).strip()
    if s == "":
        return None, "blank"
    neg = s.startswith("(") and s.endswith(")")
    s2 = s.strip("()").replace("$", "").replace(",", "").replace("USD", "").strip()
    if s2.startswith("-"):
        neg, s2 = True, s2[1:].strip()
    if not re.fullmatch(r"\d+(\.\d{1,2})?", s2):
        return None, "invalid"
    amount = round(float(s2), 2)
    return (-amount if neg else amount), "ok"


def parse_rate(value) -> tuple[float | None, str, str]:
    """'$35/hour' -> (35.0, 'hour', 'ok'). Used for team pay and costs."""
    s = str(value or "").strip()
    if not s:
        return None, "", "blank"
    m = re.fullmatch(r"\$?\s*([\d,]+(?:\.\d+)?)\s*/\s*(hour|hr|day|month)", s, re.I)
    if not m:
        return None, "", "invalid"
    unit = m.group(2).lower().replace("hr", "hour")
    return float(m.group(1).replace(",", "")), unit, "ok"


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
# Status values written to <col>_date_status:
#   ok                          - only one possible reading
#   resolved_by_file_evidence   - looked ambiguous (e.g. 05/04/2026) but other
#                                 rows in the same file prove the day/month order
#   assumed_source_convention   - the file gave no evidence; the order seen in
#                                 the original pack for this file type was used
#   ambiguous                   - could be read two ways and nothing decides it
#   invalid                     - not a real date
#   blank                       - empty
_SLASH = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")
_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*$")


def _make(y, m, d, hh=0, mm=0, ss=0):
    try:
        return pd.Timestamp(year=int(y), month=int(m), day=int(d),
                            hour=int(hh or 0), minute=int(mm or 0), second=int(ss or 0))
    except ValueError:
        return None


def detect_day_month_order(values) -> str | None:
    """Look at a whole column of d/m/yyyy-style dates and decide the order.
    Returns 'DMY', 'MDY', 'CONFLICT' (both kinds of proof found) or None."""
    dmy = mdy = False
    for v in values:
        m = _SLASH.match(str(v))
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 and b <= 12:
            dmy = True
        elif b > 12 and a <= 12:
            mdy = True
    if dmy and mdy:
        return "CONFLICT"
    return "DMY" if dmy else "MDY" if mdy else None


def parse_date_column(values, convention: str | None = None) -> pd.DataFrame:
    """Parse a whole column of dates. Returns a frame with columns
    'value' (Timestamp or NaT), 'status' and 'note' (one row per input value).

    ISO dates (2026-04-01, 2026-04-01 19:05) are unambiguous. Slash dates are
    resolved with evidence from the same column; see the status list above."""
    values = list(values)
    order = detect_day_month_order(values)
    out = []
    for v in values:
        s = "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
        if s == "":
            out.append((pd.NaT, "blank", ""))
            continue
        m = _ISO.match(s)
        if m:
            ts = _make(*m.groups())
            out.append((ts, "ok", "") if ts is not None else (pd.NaT, "invalid", "no such date"))
            continue
        m = _SLASH.match(s)
        if not m:
            out.append((pd.NaT, "invalid", "unrecognised date format"))
            continue
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if a > 12 or b > 12:                       # only one reading is possible
            ts = _make(y, b, a) if a > 12 else _make(y, a, b)
            note = "day first" if a > 12 else "month first"
            if order == "CONFLICT":
                note += "; file mixes day-first and month-first dates"
            out.append((ts, "ok", note) if ts is not None else (pd.NaT, "invalid", "no such date"))
            continue
        if a == b:                                 # 05/05/2026 reads the same both ways
            ts = _make(y, a, b)
            out.append((ts, "ok", "day and month equal") if ts is not None else (pd.NaT, "invalid", ""))
            continue
        if order in ("DMY", "MDY"):
            ts = _make(y, b, a) if order == "DMY" else _make(y, a, b)
            out.append((ts, "resolved_by_file_evidence", f"file is {order}"))
        elif order == "CONFLICT":
            out.append((pd.NaT, "ambiguous", "file mixes day-first and month-first dates"))
        elif convention in ("DMY", "MDY"):
            ts = _make(y, b, a) if convention == "DMY" else _make(y, a, b)
            out.append((ts, "assumed_source_convention",
                        f"no evidence in this file; used {convention} as in the original pack"))
        else:
            out.append((pd.NaT, "ambiguous", "could be day-first or month-first"))
    return pd.DataFrame(out, columns=["value", "status", "note"])


# ---------------------------------------------------------------------------
# Whole-dataset normalisation
# ---------------------------------------------------------------------------
NAME_COLUMNS = {
    "leads": ["name", "business"],
    "bookings": ["client_name"],
    "invoices": ["bill_to"],
    "payments": ["payer"],
    "production": ["client"],
    "messages": ["contact_name"],
}
EMAIL_COLUMNS = {"leads": "email", "bookings": "email", "invoices": "email",
                 "payments": "payer_email", "messages": "contact_email"}
PHONE_COLUMNS = {"leads": "phone", "messages": "contact_phone"}


def normalize_frame(source: str, df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ``df`` with normalised helper columns added."""
    out = df.copy()
    spec = SOURCES[source]

    for col in NAME_COLUMNS.get(source, []):
        out[f"{col}_key"] = out[col].map(name_key)
    if source in EMAIL_COLUMNS:
        col = EMAIL_COLUMNS[source]
        out["email_norm"] = out[col].map(normalize_email)
        out["email_valid"] = out["email_norm"].map(lambda e: e == "" or is_valid_email(e))
    if source in PHONE_COLUMNS:
        out["phone_norm"] = out[PHONE_COLUMNS[source]].map(normalize_phone)
    if source == "messages":
        out["handle_key"] = out["contact_handle"].map(handle_key)
        ts = parse_date_column(out["timestamp"])
        out["timestamp_date"] = ts["value"].values
        out["timestamp_date_status"] = ts["status"].values

    for col, style in spec["date_columns"].items():
        conv = spec.get("date_convention", {}).get(col)
        # Day/month order is decided per original FILE: a newly uploaded file
        # may follow a different convention from the pack it is merged into.
        parts = []
        groups = out.groupby("_source_file", sort=False) if "_source_file" in out.columns else [("", out)]
        for _, grp in groups:
            parsed = parse_date_column(grp[col], convention=conv)
            parsed.index = grp.index
            parts.append(parsed)
        parsed = pd.concat(parts).reindex(out.index) if parts else pd.DataFrame(
            columns=["value", "status", "note"], index=out.index)
        out[f"{col}_date"] = pd.to_datetime(parsed["value"])
        out[f"{col}_date_status"] = parsed["status"]
        out[f"{col}_date_note"] = parsed["note"]

    for col in spec["money_columns"]:
        parsed = out[col].map(parse_money)
        out[f"{col}_value"] = parsed.map(lambda t: t[0]).astype("float64")
        out[f"{col}_value_status"] = parsed.map(lambda t: t[1])

    if "status" in out.columns or source == "production":
        col = "stage" if source == "production" else "status"
        out["status_norm"] = out[col].map(lambda v: re.sub(r"\s+", " ", str(v)).strip().lower())
        known = STATUS_VALUES.get(source)
        if known:
            out["status_known"] = out["status_norm"].isin(known)

    if source == "production":
        out["revisions_value"] = pd.to_numeric(out["revisions"].str.strip(), errors="coerce")
        out["editor_hours_value"] = pd.to_numeric(out["editor_hours"].str.strip(), errors="coerce")
    if source == "team":
        rates = out["pay"].map(parse_rate)
        out["pay_rate"] = rates.map(lambda t: t[0]).astype("float64")
        out["pay_unit"] = rates.map(lambda t: t[1])
        out["pay_status"] = rates.map(lambda t: t[2])
    return out
