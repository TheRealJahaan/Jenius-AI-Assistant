"""Paths and tunable parameters.

Every path resolves relative to the repository, so the project runs the same on
any machine. Set ``JENIUS_DATA_DIR`` to point the pipeline at another dataset.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("JENIUS_DATA_DIR", REPO_ROOT / "data"))
OUT_DIR = Path(os.environ.get("JENIUS_OUT_DIR", REPO_ROOT / "out"))
WEB_DIR = REPO_ROOT / "web"

SHIPMENTS_FILE = DATA_DIR / "shipment_records.csv"
NOTES_FILE = DATA_DIR / "context_notes.csv"


@dataclass(frozen=True)
class Settings:
    """Detection parameters. Defaults follow the case-study specification."""

    own_history_threshold_pct: float = 15.0
    """Flag only if the week is at least this much above the route's own baseline."""

    peer_threshold_pct: float = 15.0
    """...and at least this much above similar routes (same ``route_type``) that week."""

    rolling_window_weeks: int = 8
    """Trailing window for the own-history baseline, using strictly earlier weeks."""

    note_window_days: int = 60
    """A context note can explain a week only if dated within this many days of it."""


DEFAULT_SETTINGS = Settings()
