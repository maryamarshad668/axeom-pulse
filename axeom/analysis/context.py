"""Shared starting point for every analysis: the model plus client ids on each record."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..config import REFERENCE_DATE
from ..pipeline import Model
from .evidence import Ledger, SourceIndex

ID_COL = {"leads": "lead_id", "bookings": "booking_id", "invoices": "invoice_no", "payments": "payment_id",
          "production": "video_id", "messages": "message_key"}


@dataclass
class Context:
    model: Model
    frames: dict[str, pd.DataFrame]   # normalised frames with a 'client_id' column added
    ledger: Ledger
    src: SourceIndex
    today: pd.Timestamp

    @property
    def identities(self) -> pd.DataFrame:
        return self.model.resolution.clients


MESSAGE_COLUMNS = ["message_key", "timestamp", "channel", "direction", "counterpart", "voice_duration", "text",
                   "raw_line", "contact_email", "contact_phone", "contact_handle", "contact_name", "contact_other"]


def _empty_frame(source: str) -> pd.DataFrame:
    """A correctly shaped, empty, normalised table for a dataset that was not
    loaded, so every analysis still runs on a partial set of files."""
    from ..config import SOURCES
    from ..normalizer import normalize_frame
    cols = MESSAGE_COLUMNS if source == "messages" else SOURCES[source]["required"]
    df = pd.DataFrame({c: pd.Series(dtype=str) for c in cols})
    df["_source_file"] = pd.Series(dtype=str)
    df["_source_line"] = pd.Series(dtype=int)
    return normalize_frame(source, df)


def make_context(model: Model) -> Context:
    from ..config import SOURCES
    frames = {}
    links = model.resolution.links
    all_frames = dict(model.frames)
    for source in SOURCES:
        if source not in all_frames:
            all_frames[source] = _empty_frame(source)
    for source, df in all_frames.items():
        df = df.copy()
        if source in ID_COL:
            m = links[links.source == source].set_index("record_id")["client_id"] if len(links) else pd.Series(dtype=str)
            df["client_id"] = df[ID_COL[source]].map(m).fillna("") if len(df) else pd.Series(dtype=str)
        frames[source] = df
    return Context(model=model, frames=frames, ledger=Ledger(), src=SourceIndex(model.frames),
                   today=pd.Timestamp(REFERENCE_DATE))
