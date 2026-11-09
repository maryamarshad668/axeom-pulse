"""One function that turns raw records into everything the app needs.

raw records (text, as uploaded)
    -> normalised frames      (normalizer.py)
    -> client identities      (entity_resolution.py)
    -> data quality report    (data_quality.py)

It is called after the original files are loaded and again after every
successful upload, so dependent results are always recalculated from the
current data (nothing is patched by hand).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .data_quality import build_quality_report
from .entity_resolution import ResolutionResult, load_decisions, resolve_clients
from .normalizer import normalize_frame


@dataclass
class Model:
    frames: dict[str, pd.DataFrame]   # normalised data, one frame per dataset
    resolution: ResolutionResult
    quality: pd.DataFrame


def build_model(raw: dict[str, pd.DataFrame], decisions_path: str | None = None) -> Model:
    frames = {src: normalize_frame(src, df) for src, df in raw.items() if df is not None and len(df.columns)}
    resolution = resolve_clients(frames, load_decisions(decisions_path))
    quality = build_quality_report(frames, resolution)
    return Model(frames=frames, resolution=resolution, quality=quality)
