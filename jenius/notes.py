"""Match anomalous route-weeks against free-text context notes.

A note is a *candidate* for a route-week when it applies to that route (or to
"All Routes") and is dated within ``note_window_days`` of the week. A candidate
*explains* a cost rise when its weighted net signal is positive: phrases that
describe a cost driver (flooding, surcharge, diesel...) score up, phrases that
describe normality or resolution ("returned to normal", "no rate change") score
down. Net scoring rather than a hard veto lets a flooding note with a trailing
"roads reopened" postscript still count, while a pure resolution note never does.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from .config import DEFAULT_SETTINGS, Settings
from .data import Note

POSITIVE_SIGNALS: tuple[tuple[str, int], ...] = (
    ("flood", 4),
    ("flooding", 4),
    ("disrupted", 3),
    ("surcharge", 4),
    ("forcing longer detours", 4),
    ("higher trip costs", 4),
    ("detour", 2),
    ("diesel", 3),
    ("pushing up", 3),
    ("festival", 3),
    ("limited truck", 2),
    ("maintenance", 1),
    ("delayed", 1),
    ("demand", 1),
    ("rose", 1),
)

NEGATIVE_SIGNALS: tuple[tuple[str, int], ...] = (
    ("returned to normal", 4),
    ("passable again by", 3),
    ("no significant disruption", 4),
    ("no major disruption", 4),
    ("remained stable", 4),
    ("remained normal", 4),
    ("no rate change", 4),
    ("not significantly affected", 4),
    ("without a rate change", 4),
    ("absorbed by transporters without", 4),
    ("costs were not significantly affected", 4),
    ("improved road", 2),
)


@dataclass(frozen=True)
class NoteScore:
    note: Note
    positive: tuple[str, ...]
    negative: tuple[str, ...]
    net: int

    @property
    def explains_cost_rise(self) -> bool:
        return self.net > 0


@dataclass(frozen=True)
class NoteMatch:
    """Outcome of matching one route-week against all notes."""

    explained_by: Note | None
    closest: Note | None
    candidates: tuple[NoteScore, ...] = field(default_factory=tuple)

    @property
    def verdict(self) -> str:
        return "No (justified)" if self.explained_by else "Yes"

    @property
    def reason(self) -> str:
        if self.explained_by:
            n = self.explained_by
            return (
                f"Matches note {n.note_id} dated {n.date.isoformat()}: {n.text} "
                "The cost rise has a clear explanation."
            )
        if self.closest:
            n = self.closest
            return (
                f'The closest note ({n.note_id}, {n.date.isoformat()}) mentions: "{n.text}" -- '
                "it does not describe a reason for a cost rise on this route. "
                "No genuine justification found; flagged for review."
            )
        return (
            "No matching note found for this route or date range. "
            "Cost rise looks unexplained and worth a human review."
        )


def _normalise_route(label: str) -> str:
    # "Mumbai – Pune", "Mumbai-Pune" and "mumbai pune" all compare equal.
    return re.sub(r"\s*[-–—]\s*|\s+", "-", label.strip()).lower()


def note_applies(note: Note, route: str, week_of: str, settings: Settings = DEFAULT_SETTINGS) -> bool:
    week = dt.date.fromisoformat(week_of)
    if abs((note.date - week).days) > settings.note_window_days:
        return False
    if note.applies_to == "All Routes":
        return True
    return _normalise_route(note.applies_to) == _normalise_route(route)


def score_note(note: Note) -> NoteScore:
    text = note.text.lower()
    pos = tuple(p for p, _ in POSITIVE_SIGNALS if p in text)
    neg = tuple(p for p, _ in NEGATIVE_SIGNALS if p in text)
    net = sum(w for p, w in POSITIVE_SIGNALS if p in pos) - sum(w for p, w in NEGATIVE_SIGNALS if p in neg)
    return NoteScore(note=note, positive=pos, negative=neg, net=net)


def match_notes(
    route: str, week_of: str, notes: list[Note], settings: Settings = DEFAULT_SETTINGS
) -> NoteMatch:
    """Pick the closest-in-date note that explains a rise, else the closest candidate."""
    week = dt.date.fromisoformat(week_of)

    def distance(n: Note) -> int:
        return abs((n.date - week).days)

    scored = [score_note(n) for n in notes if note_applies(n, route, week_of, settings)]
    if not scored:
        return NoteMatch(explained_by=None, closest=None)
    explaining = sorted((s.note for s in scored if s.explains_cost_rise), key=distance)
    closest = min((s.note for s in scored), key=distance)
    return NoteMatch(
        explained_by=explaining[0] if explaining else None,
        closest=closest,
        candidates=tuple(scored),
    )
