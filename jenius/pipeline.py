"""One-call entry points shared by the CLI, the server and the tests."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from . import config
from .config import DEFAULT_SETTINGS, Settings
from .data import load_notes, load_shipments
from .report import Analysis, analyse, knowledge_base


def run(
    shipments_file: Path | None = None,
    notes_file: Path | None = None,
    settings: Settings = DEFAULT_SETTINGS,
) -> Analysis:
    return analyse(
        load_shipments(shipments_file or config.SHIPMENTS_FILE),
        load_notes(notes_file or config.NOTES_FILE),
        settings,
    )


@lru_cache(maxsize=1)
def default_knowledge_base() -> dict[str, Any]:
    return knowledge_base(run())
