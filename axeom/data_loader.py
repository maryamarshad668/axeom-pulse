"""Loading and validating Axeom source files.

Design rules
------------
* Every value is read as text exactly as it appears in the file. Nothing is
  converted here, so the raw value is always available for auditing.
  (Normalised copies are added later by ``normalizer.py``.)
* Every record gets ``_source_file`` and ``_source_line`` so any result can be
  traced back to the exact line of the exact file.
* Problems are returned as messages in a ``LoadResult``. Loading never raises
  for bad data; it only raises for programming errors.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
from dataclasses import dataclass, field

import pandas as pd

from .config import SOURCES


@dataclass
class LoadResult:
    """What came out of reading one file."""
    source: str | None              # dataset key, e.g. "invoices"; None if unknown
    file_name: str
    data: pd.DataFrame | None       # None when the file could not be used
    errors: list[str] = field(default_factory=list)    # file was rejected
    warnings: list[str] = field(default_factory=list)  # file accepted, with notes
    rejected_rows: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.data is not None and not self.errors


# ---------------------------------------------------------------------------
# Text decoding
# ---------------------------------------------------------------------------
def decode_bytes(raw: bytes) -> tuple[str, str]:
    """Turn file bytes into text. Handles a UTF-8 BOM and common Windows
    encodings. Returns (text, encoding_used)."""
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 decodes any byte sequence
        text, enc = raw.decode("utf-8", errors="replace"), "utf-8-replace"
    # Remove a stray BOM that survived (e.g. BOM in the middle after concatenation)
    text = text.replace("﻿", "")
    # Normalise line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, enc


def _read_bytes(path_or_bytes) -> bytes:
    if isinstance(path_or_bytes, (bytes, bytearray)):
        return bytes(path_or_bytes)
    if hasattr(path_or_bytes, "read"):           # Streamlit UploadedFile, BytesIO
        data = path_or_bytes.read()
        return data if isinstance(data, bytes) else data.encode("utf-8")
    with open(path_or_bytes, "rb") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# File type detection
# ---------------------------------------------------------------------------
def _clean_header(name: str) -> str:
    return str(name).replace("﻿", "").strip().lower()


def detect_source(file_name: str, columns: list[str] | None) -> tuple[str | None, str]:
    """Work out which dataset a file belongs to.

    The column header is the source of truth; the file name is only a hint.
    Returns (source_key or None, explanation)."""
    base = os.path.basename(file_name or "").lower()
    if base.endswith(".txt"):
        return "messages", "text file -> messages"
    if columns is None:
        return None, "no header found"
    cols = {_clean_header(c) for c in columns}
    by_header = [k for k, s in SOURCES.items() if s["required"] and set(s["required"]) <= cols]
    by_name = [k for k, s in SOURCES.items()
               if os.path.splitext(s["filename"])[0] in base or k in base]
    if len(by_header) == 1:
        key = by_header[0]
        if by_name and key not in by_name:
            return key, (f"columns match '{key}' although the file name suggests "
                         f"'{by_name[0]}'; using the columns")
        return key, f"columns match '{key}'"
    if len(by_header) > 1:
        both = [k for k in by_header if k in by_name]
        if len(both) == 1:
            return both[0], "columns and file name agree"
        return None, f"columns match several datasets: {by_header}"
    if by_name:
        return None, (f"file name suggests '{by_name[0]}' but required columns are "
                      f"missing: {sorted(set(SOURCES[by_name[0]]['required']) - cols)}")
    return None, "file name and columns do not match any supported dataset"


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------
def load_csv(path_or_bytes, file_name: str | None = None, source: str | None = None) -> LoadResult:
    """Load one CSV file as text-only columns, validate it and tag every row."""
    if file_name is None:
        file_name = getattr(path_or_bytes, "name", None) or (
            path_or_bytes if isinstance(path_or_bytes, str) else "uploaded.csv")
    file_name = os.path.basename(file_name)
    try:
        text, enc = decode_bytes(_read_bytes(path_or_bytes))
    except OSError as exc:
        return LoadResult(source, file_name, None, errors=[f"cannot read file: {exc}"])
    if not text.strip():
        return LoadResult(source, file_name, None, errors=["file is empty"])
    try:
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False,
                         skipinitialspace=False, on_bad_lines="error",
                         skip_blank_lines=False)   # keep line numbers true
    except Exception as exc:  # malformed CSV (wrong number of fields, bad quotes ...)
        return LoadResult(source, file_name, None, errors=[f"not a readable CSV: {exc}"])

    df.columns = [_clean_header(c) for c in df.columns]
    warnings = []
    if enc != "utf-8-sig":
        warnings.append(f"file was not UTF-8; decoded as {enc}")

    detected, why = detect_source(file_name, list(df.columns))
    if source is None:
        source = detected
    if source is None:
        return LoadResult(None, file_name, None, errors=[f"unsupported file: {why}"])
    if detected is not None and detected != source:
        return LoadResult(source, file_name, None,
                          errors=[f"expected a '{source}' file but the columns look like '{detected}'"])
    if "although" in why:
        warnings.append(why)

    missing = [c for c in SOURCES[source]["required"] if c not in df.columns]
    if missing:
        return LoadResult(source, file_name, None,
                          errors=[f"missing required column(s): {', '.join(missing)}"])
    extra = [c for c in df.columns if c not in SOURCES[source]["required"]]
    if extra:
        warnings.append(f"extra column(s) kept but not used: {', '.join(extra)}")

    # Drop rows that are completely blank (common at the end of exported files)
    blank = df.apply(lambda r: all(str(v).strip() == "" for v in r), axis=1)
    if blank.any():
        warnings.append(f"{int(blank.sum())} completely blank row(s) ignored")
    df["_source_file"] = file_name
    df["_source_line"] = range(2, len(df) + 2)       # line 1 is the header
    df = df[~blank].reset_index(drop=True)

    # Rows without the stable identifier cannot be merged safely -> reject them
    rejected = []
    id_col = SOURCES[source]["id_column"]
    if id_col:
        no_id = df[id_col].str.strip() == ""
        for _, row in df[no_id].iterrows():
            rejected.append({"source_file": file_name, "line": int(row["_source_line"]),
                             "reason": f"missing {id_col}", "record": row.to_dict()})
        df = df[~no_id].reset_index(drop=True)
    return LoadResult(source, file_name, df, warnings=warnings, rejected_rows=rejected)


# ---------------------------------------------------------------------------
# messages.txt
# ---------------------------------------------------------------------------
MESSAGE_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s+"
    r"(?P<channel>[A-Z][A-Z ]*?)\s+"
    r"(?P<direction>from|to)\s+"
    r"(?P<who>.+?)"
    r"(?:,\s*(?P<duration>\d{1,2}:\d{2})\.\s*Transcript)?"
    r":\s(?P<text>.*)$"
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{8,}\d")


def split_counterpart(who: str) -> dict:
    """Break the 'from/to' part of a message into email / phone / handle / name."""
    out = {"contact_email": "", "contact_phone": "", "contact_handle": "",
           "contact_name": "", "contact_other": ""}
    rest = who.strip()
    m = re.search(r"\(([^)]*)\)\s*$", rest)
    if m:
        out["contact_name"] = m.group(1).strip()
        rest = rest[:m.start()].strip()
    if EMAIL_RE.fullmatch(rest):
        out["contact_email"] = rest
    elif rest.startswith("@"):
        out["contact_handle"] = rest
    elif PHONE_RE.fullmatch(rest):
        out["contact_phone"] = rest
    else:
        # e.g. "all clients (broadcast)": keep the whole thing, it is not a person
        out["contact_other"] = who.strip()
        out["contact_name"] = ""
    return out


def message_key(ts: str, channel: str, direction: str, who: str, text: str) -> str:
    """Deterministic id for a message that has no id of its own.

    Built from every field after collapsing whitespace and case, so the same
    message uploaded twice (even with different spacing or line endings)
    always gets the same key."""
    norm = lambda s: re.sub(r"\s+", " ", str(s)).strip().lower()
    basis = "|".join(norm(x) for x in (ts, channel, direction, who, text))
    return "M-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def load_messages(path_or_bytes, file_name: str | None = None) -> LoadResult:
    """Parse messages.txt into one row per message."""
    if file_name is None:
        file_name = getattr(path_or_bytes, "name", None) or (
            path_or_bytes if isinstance(path_or_bytes, str) else "messages.txt")
    file_name = os.path.basename(file_name)
    try:
        text, enc = decode_bytes(_read_bytes(path_or_bytes))
    except OSError as exc:
        return LoadResult("messages", file_name, None, errors=[f"cannot read file: {exc}"])
    rows, rejected = [], []
    for line_no, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        m = MESSAGE_RE.match(line.strip())
        if not m:
            rejected.append({"source_file": file_name, "line": line_no,
                             "reason": "line does not follow '[date time] CHANNEL from/to sender: text'",
                             "record": {"raw_line": line}})
            continue
        g = m.groupdict()
        body = g["text"].strip()
        if g["duration"] and len(body) >= 2 and body[0] == body[-1] == '"':
            body = body[1:-1]
        row = {
            "message_key": message_key(g["ts"], g["channel"], g["direction"], g["who"], body),
            "timestamp": g["ts"].strip(),
            "channel": g["channel"].strip(),
            "direction": "inbound" if g["direction"] == "from" else "outbound",
            "counterpart": g["who"].strip(),
            "voice_duration": g["duration"] or "",
            "text": body,
            "raw_line": line,
            "_source_file": file_name,
            "_source_line": line_no,
        }
        row.update(split_counterpart(g["who"]))
        rows.append(row)
    if not rows:
        return LoadResult("messages", file_name, None,
                          errors=["no line in the file looks like a message"], rejected_rows=rejected)
    df = pd.DataFrame(rows).astype({"_source_line": int})
    warnings = [f"{len(rejected)} line(s) could not be read as messages"] if rejected else []
    # Identical message twice in the same file -> keep the first, report the rest
    dup = df.duplicated("message_key", keep="first")
    for _, r in df[dup].iterrows():
        rejected.append({"source_file": file_name, "line": int(r["_source_line"]),
                         "reason": "exact duplicate of an earlier message in the same file",
                         "record": {"raw_line": r["raw_line"]}})
    df = df[~dup].reset_index(drop=True)
    return LoadResult("messages", file_name, df, warnings=warnings, rejected_rows=rejected)


# ---------------------------------------------------------------------------
# Generic entry points
# ---------------------------------------------------------------------------
def load_file(path_or_bytes, file_name: str | None = None) -> LoadResult:
    """Load any supported file, choosing CSV or messages by extension."""
    name = file_name or getattr(path_or_bytes, "name", None) or (
        path_or_bytes if isinstance(path_or_bytes, str) else "")
    if str(name).lower().endswith(".txt"):
        return load_messages(path_or_bytes, file_name=name)
    if str(name).lower().endswith(".csv"):
        return load_csv(path_or_bytes, file_name=name)
    return LoadResult(None, os.path.basename(str(name)), None,
                      errors=["only .csv and .txt files are supported"])


def load_folder(folder: str) -> dict[str, LoadResult]:
    """Load every supported file found in a folder, keyed by dataset."""
    results: dict[str, LoadResult] = {}
    for fname in sorted(os.listdir(folder)):
        if not fname.lower().endswith((".csv", ".txt")):
            continue
        res = load_file(os.path.join(folder, fname))
        key = res.source or fname
        if key in results:
            res.errors.append(f"a second '{key}' file was found in the folder; it was not loaded")
            results[f"{key}:{fname}"] = res
        else:
            results[key] = res
    return results
