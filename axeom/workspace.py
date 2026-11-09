"""The app's working data: the original pack plus every upload, kept on disk.

Uploads are never merged into the original files. Each "Add new files" batch
is saved as-is in ``<state>/uploads/<batch>/`` with a manifest, and the whole
system is rebuilt by loading the original pack and replaying the batches in
order through the Phase 1 engine (``DataStore.ingest``). That gives:

* the same result whoever opens the app (one source of truth),
* undo of the last batch and a full reset, with nothing lost,
* an audit trail: which file, when, what it inserted / updated / skipped.

The owner's answers to identity questions ("are these the same client?") are
written to ``<state>/identity_decisions.csv`` in the Phase 1
``match_decisions.csv`` format and applied on every rebuild.

The state folder defaults to ``data/state`` and can be moved with the
environment variable ``AXEOM_STATE_DIR``. On free hosting the disk is reset
when the app restarts; the original pack is always there.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from .analysis import Analysis, run_all
from .data_loader import load_file
from .entity_resolution import load_decisions
from .ingestion import DataStore, FileReport
from .pipeline import Model, build_model

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECISION_COLUMNS = ["value_type", "value", "target_email", "decision", "note"]


@dataclass
class Snapshot:
    """Everything the app shows, built from the original pack + uploads."""
    store: DataStore
    model: Model
    analysis: Analysis
    batch_reports: dict = field(default_factory=dict)   # batch number -> list[FileReport]
    fingerprint: str = ""


def _safe_name(name: str) -> str:
    base = os.path.basename(str(name)).strip() or "file"
    return re.sub(r"[^A-Za-z0-9._ -]", "_", base)[:120]


class Workspace:
    def __init__(self, state_dir: str | None = None, original_dir: str | None = None,
                 base_decisions: str | None = None):
        self.state_dir = state_dir or os.environ.get("AXEOM_STATE_DIR") or os.path.join(PROJECT, "data", "state")
        self.original_dir = original_dir or os.path.join(PROJECT, "data", "original")
        self.base_decisions = base_decisions if base_decisions is not None else \
            os.path.join(PROJECT, "data", "match_decisions.csv")
        self.uploads_dir = os.path.join(self.state_dir, "uploads")
        self.manifest_path = os.path.join(self.uploads_dir, "manifest.json")
        self.identity_path = os.path.join(self.state_dir, "identity_decisions.csv")
        self.merged_decisions_path = os.path.join(self.state_dir, "_match_decisions_in_use.csv")
        os.makedirs(self.uploads_dir, exist_ok=True)

    # ------------------------------------------------------------ uploads
    def batches(self) -> list[dict]:
        if not os.path.exists(self.manifest_path):
            return []
        with open(self.manifest_path, encoding="utf-8") as fh:
            return json.load(fh)

    def _write_manifest(self, batches: list[dict]) -> None:
        tmp = self.manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(batches, fh, indent=2)
        os.replace(tmp, self.manifest_path)

    def add_upload(self, files) -> tuple[dict | None, list[FileReport]]:
        """Check and save one batch of files.

        ``files`` is a list of ``(name, bytes)`` or objects with ``.name`` and
        ``.read()`` / ``.getvalue()``. Files the engine cannot use at all
        (unknown type, missing columns) are not saved; their report says why.
        Returns ``(batch or None, reports_for_rejected_files)``.
        """
        rejected, keep = [], []
        for f in files:
            if isinstance(f, tuple):
                name, data = f
            else:
                name = f.name
                data = f.getvalue() if hasattr(f, "getvalue") else f.read()
            data = bytes(data)
            res = load_file(data, file_name=_safe_name(name))
            if not res.ok:
                rejected.append(FileReport(_safe_name(name), res.source, accepted=False,
                                           warnings=list(res.warnings), errors=list(res.errors)))
                continue
            keep.append((_safe_name(name), data))
        if not keep:
            return None, rejected
        batches = self.batches()
        number = max([b["batch"] for b in batches] or [0]) + 1
        folder = os.path.join(self.uploads_dir, f"{number:04d}")
        os.makedirs(folder, exist_ok=True)
        entries = []
        for i, (name, data) in enumerate(keep):
            stored = f"{i:02d}__{name}"
            with open(os.path.join(folder, stored), "wb") as fh:
                fh.write(data)
            entries.append({"name": name, "stored": stored, "bytes": len(data),
                            "sha1": hashlib.sha1(data).hexdigest()})
        batch = {"batch": number, "saved_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                 "files": entries}
        self._write_manifest(batches + [batch])
        return batch, rejected

    def undo_last(self) -> dict | None:
        batches = self.batches()
        if not batches:
            return None
        last = batches[-1]
        shutil.rmtree(os.path.join(self.uploads_dir, f"{last['batch']:04d}"), ignore_errors=True)
        self._write_manifest(batches[:-1])
        return last

    def reset(self) -> None:
        """Back to the original pack: removes every upload and identity answer."""
        shutil.rmtree(self.uploads_dir, ignore_errors=True)
        os.makedirs(self.uploads_dir, exist_ok=True)
        for p in (self.identity_path, self.merged_decisions_path):
            if os.path.exists(p):
                os.remove(p)

    # ------------------------------------------------------------ identity answers
    def identity_decisions(self) -> pd.DataFrame:
        if not os.path.exists(self.identity_path):
            return pd.DataFrame(columns=DECISION_COLUMNS)
        return pd.read_csv(self.identity_path, dtype=str, keep_default_na=False)

    def add_identity_decision(self, value_type: str, value: str, target_email: str, decision: str,
                              note: str = "") -> None:
        if decision not in ("same", "different"):
            raise ValueError("decision must be 'same' or 'different'")
        d = self.identity_decisions()
        d = d[~((d.value_type == value_type) & (d.value == value) & (d.target_email == target_email))]
        row = pd.DataFrame([[value_type, value, target_email, decision, note]], columns=DECISION_COLUMNS)
        os.makedirs(self.state_dir, exist_ok=True)
        pd.concat([d, row], ignore_index=True).to_csv(self.identity_path, index=False)

    def remove_identity_decision(self, value: str, target_email: str) -> None:
        d = self.identity_decisions()
        d = d[~((d.value == value) & (d.target_email == target_email))]
        d.to_csv(self.identity_path, index=False)

    def _decisions_file(self) -> str:
        """Phase 1 decisions + the owner's in-app answers, as one file for the engine."""
        frames = [load_decisions(self.base_decisions if os.path.exists(self.base_decisions) else None),
                  self.identity_decisions()]
        merged = pd.concat([f[DECISION_COLUMNS] for f in frames if len(f)], ignore_index=True) \
            if any(len(f) for f in frames) else pd.DataFrame(columns=DECISION_COLUMNS)
        os.makedirs(self.state_dir, exist_ok=True)
        merged.to_csv(self.merged_decisions_path, index=False)
        return self.merged_decisions_path

    # ------------------------------------------------------------ rebuild
    def fingerprint(self) -> str:
        """Changes whenever anything that affects the results changes."""
        h = hashlib.sha1()
        h.update(json.dumps(self.batches(), sort_keys=True).encode())
        for p in (self.identity_path, self.base_decisions):
            if os.path.exists(p):
                with open(p, "rb") as fh:
                    h.update(fh.read())
        for name in sorted(os.listdir(self.original_dir)):
            st = os.stat(os.path.join(self.original_dir, name))
            h.update(f"{name}:{st.st_size}".encode())
        return h.hexdigest()[:16]

    def build(self) -> Snapshot:
        store, _ = DataStore.from_folder(self.original_dir)
        batch_reports = {}
        for b in self.batches():
            folder = os.path.join(self.uploads_dir, f"{b['batch']:04d}")
            files = []
            for e in b["files"]:
                path = os.path.join(folder, e["stored"])
                if os.path.exists(path):
                    with open(path, "rb") as fh:
                        files.append((e["name"], fh.read()))
            batch_reports[b["batch"]] = store.ingest(files)
        model = build_model(store.raw, self._decisions_file())
        return Snapshot(store=store, model=model, analysis=run_all(model), batch_reports=batch_reports,
                        fingerprint=self.fingerprint())
