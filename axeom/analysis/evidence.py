"""Evidence ledger: every reported figure points back to source records.

One ledger row = one source record supporting one finding. A finding with
three supporting records has three rows sharing the same finding_id.

status
  confirmed  - stated directly by the data, or two independent sources agree
               (e.g. invoice says unpaid AND no bank payment exists)
  inferred   - a conclusion drawn from evidence that is strong but indirect
               (e.g. payment matched to an invoice by client + amount + date)
  unresolved - the data conflicts or is insufficient; a human must decide
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

LEDGER_COLUMNS = ["finding_id", "question", "category", "status", "confidence", "client", "amount",
                  "statement", "calculation", "matching_decision", "source_file", "source_line",
                  "record_id", "record_role"]


@dataclass
class Ledger:
    rows: list[dict] = field(default_factory=list)
    _counter: dict = field(default_factory=dict)

    def new_id(self, prefix: str) -> str:
        n = self._counter.get(prefix, 0) + 1
        self._counter[prefix] = n
        return f"{prefix}-{n:03d}"

    def add(self, *, finding_id, question, category, status, confidence, statement,
            calculation="", client="", amount=None, matching_decision="", sources=()):
        """sources: iterable of (source_file, source_line, record_id, role)."""
        sources = list(sources) or [("", "", "", "")]
        for src in sources:
            f, line, rid, role = (list(src) + ["", "", "", ""])[:4]
            self.rows.append({
                "finding_id": finding_id, "question": question, "category": category,
                "status": status, "confidence": confidence, "client": client,
                "amount": None if amount is None else round(float(amount), 2),
                "statement": statement, "calculation": calculation,
                "matching_decision": matching_decision,
                "source_file": f, "source_line": line, "record_id": rid, "record_role": role})
        return finding_id

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=LEDGER_COLUMNS)


class SourceIndex:
    """Find the file and line of any record by (dataset, record id)."""

    ID = {"leads": "lead_id", "bookings": "booking_id", "invoices": "invoice_no", "payments": "payment_id",
          "production": "video_id", "messages": "message_key", "team": "name", "costs": "item"}

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self._idx = {}
        for src, df in frames.items():
            col = self.ID.get(src)
            if col and col in df.columns:
                for rid, f, line in zip(df[col], df["_source_file"], df["_source_line"]):
                    self._idx[(src, str(rid))] = (f, int(line))

    def __call__(self, source: str, record_id, role: str = "") -> tuple:
        f, line = self._idx.get((source, str(record_id)), ("", ""))
        return (f, line, str(record_id), role)
