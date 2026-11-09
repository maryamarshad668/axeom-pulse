"""Adding new files to the existing data ("Add new files" button).

Rules
-----
* A file is identified by its columns (the file name is only a hint).
* Records are merged by their stable id (lead_id, booking_id, invoice_no,
  payment_id, video_id; team: name; costs: item). Messages have no id, so a
  deterministic ``message_key`` is built from timestamp, channel, direction,
  sender and text (see data_loader.message_key).
* For each incoming record:
    - id not seen before                  -> inserted
    - id seen, identical content          -> skipped as a duplicate
    - id seen, different content          -> treated as a correction: updated,
                                             and the old version is kept in history
    - same id twice in one file, with
      different content                   -> ambiguous: neither copy is applied
    - no id, or a money field that is
      text rather than a number           -> rejected
* Every change is logged with the file it came from, so it can be audited.
* Nothing is blindly appended: uploading the same file twice changes nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

import pandas as pd

from .config import SOURCES
from .data_loader import load_file, load_folder
from .normalizer import parse_money


def _norm_id(v) -> str:
    return str(v).strip().upper()


def _content_hash(row: pd.Series, columns: list[str]) -> str:
    """Fingerprint of a record's business content (surrounding spaces ignored)."""
    basis = json.dumps([str(row.get(c, "")).strip() for c in columns])
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _content_columns(source: str, df: pd.DataFrame) -> list[str]:
    if source == "messages":
        return ["message_key"]
    return [c for c in df.columns if not c.startswith("_")]


@dataclass
class FileReport:
    file_name: str
    source: str | None
    accepted: bool
    inserted: int = 0
    updated: int = 0
    duplicates: int = 0
    rejected: list[dict] = field(default_factory=list)   # {line, record_id, reason}
    ambiguous: list[dict] = field(default_factory=list)  # {record_id, lines, reason}
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"file": self.file_name, "dataset": self.source or "-", "accepted": self.accepted,
                "inserted": self.inserted, "updated": self.updated, "duplicates": self.duplicates,
                "rejected": len(self.rejected), "ambiguous": len(self.ambiguous),
                "notes": "; ".join(self.errors + self.warnings)}


class DataStore:
    """Holds the current version of every dataset plus an audit history."""

    def __init__(self):
        self.raw: dict[str, pd.DataFrame] = {}
        self.history: list[dict] = []      # one entry per insert / update / skip / reject
        self.batch = 0

    # ---------------------------------------------------------------- creation
    @classmethod
    def from_folder(cls, folder: str) -> tuple["DataStore", list[FileReport]]:
        store = cls()
        reports = []
        for key, res in load_folder(folder).items():
            reports.append(store._merge(res))
        return store, reports

    # ---------------------------------------------------------------- uploads
    def ingest(self, files) -> list[FileReport]:
        """files: list of (file_name, bytes) or objects with .name and .read()."""
        reports = []
        self.batch += 1
        for f in files:
            if isinstance(f, tuple):
                name, data = f
            else:
                name, data = f.name, f.read()
            reports.append(self._merge(load_file(bytes(data), file_name=name)))
        return reports

    # ---------------------------------------------------------------- merging
    def _log(self, action, source, record_id, file_name, line="", detail="", old=None, new=None):
        self.history.append({"batch": self.batch, "action": action, "dataset": source,
                             "record_id": record_id, "file": file_name, "line": line,
                             "detail": detail,
                             "old": json.dumps(old, default=str) if old is not None else "",
                             "new": json.dumps(new, default=str) if new is not None else ""})

    def _merge(self, res) -> FileReport:
        rep = FileReport(res.file_name, res.source, accepted=res.ok,
                         warnings=list(res.warnings), errors=list(res.errors))
        for rj in res.rejected_rows:
            rid = (rj.get("record") or {}).get(SOURCES.get(res.source or "", {}).get("id_column") or "", "")
            rep.rejected.append({"line": rj["line"], "record_id": rid, "reason": rj["reason"]})
            self._log("rejected", res.source, rid, res.file_name, rj["line"], rj["reason"])
        if not res.ok:
            self._log("file_rejected", res.source, "", res.file_name, "", "; ".join(res.errors))
            return rep

        source = res.source
        incoming = res.data.copy()
        id_col = "message_key" if source == "messages" else SOURCES[source]["id_column"]
        cols = _content_columns(source, incoming)

        # reject rows whose money fields hold text (they would silently distort totals)
        keep = pd.Series(True, index=incoming.index)
        for mc in SOURCES[source]["money_columns"]:
            for idx, v in incoming[mc].items():
                if parse_money(v)[1] == "invalid":
                    keep[idx] = False
                    r = incoming.loc[idx]
                    rep.rejected.append({"line": int(r["_source_line"]), "record_id": r[id_col],
                                         "reason": f"{mc} '{v}' is not a number"})
                    self._log("rejected", source, r[id_col], res.file_name, int(r["_source_line"]),
                              f"{mc} '{v}' is not a number")
        incoming = incoming[keep]

        incoming["_id"] = incoming[id_col].map(_norm_id)
        incoming["_hash"] = incoming.apply(lambda r: _content_hash(r, cols), axis=1)

        # same id more than once inside this file
        apply_rows = []
        for rid, grp in incoming.groupby("_id", sort=False):
            if grp["_hash"].nunique() > 1:
                lines = ", ".join(str(x) for x in grp["_source_line"])
                rep.ambiguous.append({"record_id": grp[id_col].iloc[0], "lines": lines,
                                      "reason": "same id appears with different content in one file; not applied"})
                self._log("ambiguous", source, grp[id_col].iloc[0], res.file_name, lines,
                          "same id with different content in one file")
                continue
            if len(grp) > 1:
                rep.duplicates += len(grp) - 1
            apply_rows.append(grp.iloc[0])

        current = self.raw.get(source)
        if current is None or current.empty:
            current = pd.DataFrame(columns=list(incoming.columns))
        else:
            current = current.copy()
            current["_id"] = current[id_col].map(_norm_id)
            current["_hash"] = current.apply(lambda r: _content_hash(r, cols), axis=1)
        pos = {rid: i for i, rid in enumerate(current["_id"])}

        new_rows, updates = [], {}
        for r in apply_rows:
            rid = r["_id"]
            if rid in pos:
                old = current.iloc[pos[rid]]
                if old["_hash"] == r["_hash"]:
                    rep.duplicates += 1
                    self._log("duplicate_skipped", source, r[id_col], res.file_name, int(r["_source_line"]),
                              f"identical to the version from {old['_source_file']}")
                    continue
                changed = {c: (str(old.get(c, "")), str(r.get(c, ""))) for c in cols
                           if str(old.get(c, "")).strip() != str(r.get(c, "")).strip()}
                updates[pos[rid]] = r
                rep.updated += 1
                self._log("updated", source, r[id_col], res.file_name, int(r["_source_line"]),
                          "changed: " + ", ".join(changed), old={c: v[0] for c, v in changed.items()},
                          new={c: v[1] for c, v in changed.items()})
            else:
                new_rows.append(r)
                pos[rid] = len(current) + len(new_rows) - 1
                rep.inserted += 1
                self._log("inserted", source, r[id_col], res.file_name, int(r["_source_line"]))

        for i, r in updates.items():
            for c in current.columns:
                if c in r.index:
                    current.iat[i, current.columns.get_loc(c)] = r[c]
        if new_rows:
            current = pd.concat([current, pd.DataFrame(new_rows)], ignore_index=True)
        # make sure every incoming column exists (extra columns are kept)
        self.raw[source] = current.drop(columns=["_id", "_hash"], errors="ignore").reset_index(drop=True)
        return rep

    # ---------------------------------------------------------------- saving
    def save(self, folder: str) -> None:
        """Write the current data and the audit history (CSV, UTF-8)."""
        os.makedirs(folder, exist_ok=True)
        for source, df in self.raw.items():
            df.to_csv(os.path.join(folder, f"{source}.store.csv"), index=False)
        pd.DataFrame(self.history).to_csv(os.path.join(folder, "history.csv"), index=False)

    @classmethod
    def load(cls, folder: str) -> "DataStore":
        store = cls()
        for source in SOURCES:
            path = os.path.join(folder, f"{source}.store.csv")
            if os.path.exists(path):
                df = pd.read_csv(path, dtype=str, keep_default_na=False)
                if "_source_line" in df.columns:
                    df["_source_line"] = pd.to_numeric(df["_source_line"], errors="coerce").fillna(0).astype(int)
                store.raw[source] = df
        hist = os.path.join(folder, "history.csv")
        if os.path.exists(hist):
            store.history = pd.read_csv(hist, dtype=str, keep_default_na=False).to_dict("records")
            store.batch = max([int(h["batch"]) for h in store.history] or [0])
        return store


def reports_table(reports: list[FileReport]) -> pd.DataFrame:
    return pd.DataFrame([r.summary() for r in reports])
