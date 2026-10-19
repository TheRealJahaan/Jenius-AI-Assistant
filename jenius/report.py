"""Turn detector + note-matching results into the CSV deliverable and the
JSON knowledge base consumed by the web dashboard and the assistant."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .config import DEFAULT_SETTINGS, Settings
from .data import Note, Shipment
from .detector import RouteWeek, compute_route_weeks
from .notes import NoteMatch, match_notes, score_note

CSV_FIELDS = (
    "route",
    "week_of",
    "cost_per_tonne_km",
    "vs_own_history",
    "vs_similar_routes",
    "flagged",
    "matched_note_id",
    "reason",
)


def _signed(pct: float) -> str:
    return f"{'+' if pct >= 0 else ''}{pct:.1f}%"


@dataclass(frozen=True)
class Finding:
    """A comparable route-week together with its note match."""

    rw: RouteWeek
    match: NoteMatch
    anomalous: bool
    settings: Settings

    @property
    def vs_own_history(self) -> str:
        assert self.rw.pct_vs_own is not None
        window = self.settings.rolling_window_weeks
        label = f"trailing {min(self.rw.n_hist, window)}-week avg"
        return f"{_signed(self.rw.pct_vs_own)} vs this route's {label}"

    @property
    def vs_similar_routes(self) -> str:
        assert self.rw.pct_vs_peers is not None
        return f"{_signed(self.rw.pct_vs_peers)} vs similar-length routes this week"

    @property
    def drivers(self) -> str:
        """One sentence on whether the rise was broad-based or driven by a few loads."""
        baseline = self.rw.baseline or 0.0
        ships = self.rw.shipments
        above = [s for s in ships if s.cost_per_tonne_km > baseline]
        carriers = sorted({s.transporter for s in above})
        if len(above) == len(ships):
            return (
                f"Broad-based: all {len(ships)} shipments that week "
                f"({len(carriers)} transporter{'s' if len(carriers) != 1 else ''}) "
                f"cost more than the route's baseline of ₹{baseline:.2f}/t-km."
            )
        top = max(ships, key=lambda s: s.cost_per_tonne_km)
        return (
            f"Concentrated: {len(above)} of {len(ships)} shipments were above the "
            f"₹{baseline:.2f}/t-km baseline; the most expensive was {top.shipment_id} "
            f"({top.transporter}, {top.material}) at ₹{top.cost_per_tonne_km:.2f}/t-km."
        )

    def csv_row(self) -> dict[str, str]:
        return {
            "route": self.rw.route,
            "week_of": self.rw.week_of,
            "cost_per_tonne_km": f"{round(self.rw.cptk, 2):.2f}",
            "vs_own_history": self.vs_own_history,
            "vs_similar_routes": self.vs_similar_routes,
            "flagged": self.match.verdict,
            "matched_note_id": self.match.explained_by.note_id if self.match.explained_by else "",
            "reason": self.match.reason,
        }


@dataclass(frozen=True)
class Analysis:
    shipments: list[Shipment]
    notes: list[Note]
    route_weeks: list[RouteWeek]
    findings: list[Finding]
    settings: Settings

    @property
    def anomalies(self) -> list[Finding]:
        return [f for f in self.findings if f.anomalous]


def analyse(shipments: list[Shipment], notes: list[Note], settings: Settings = DEFAULT_SETTINGS) -> Analysis:
    route_weeks = compute_route_weeks(shipments, settings)
    findings = [
        Finding(
            rw=rw,
            match=match_notes(rw.route, rw.week_of, notes, settings),
            anomalous=rw.is_anomalous(settings),
            settings=settings,
        )
        for rw in route_weeks
        if rw.comparable
    ]
    return Analysis(shipments, notes, route_weeks, findings, settings)


def write_csv(findings: list[Finding], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(fd.csv_row() for fd in findings)


def _r(x: float | None, nd: int) -> float | None:
    return None if x is None else round(x, nd)


def knowledge_base(analysis: Analysis) -> dict[str, Any]:
    """Everything the dashboard and the assistant need, as plain JSON.

    Display strings are formatted here, once, so the Python and JavaScript
    assistants never format numbers themselves and always agree.
    """
    s = analysis.settings
    findings_by_key = {(f.rw.route, f.rw.week_of): f for f in analysis.findings}

    route_weeks = []
    for rw in analysis.route_weeks:
        f = findings_by_key.get((rw.route, rw.week_of))
        row: dict[str, Any] = {
            "route": rw.route,
            "week_of": rw.week_of,
            "cptk": round(rw.cptk, 4),
            "cptk_label": f"{round(rw.cptk, 2):.2f}",
            "baseline": _r(rw.baseline, 4),
            "peer_avg": _r(rw.peer_avg, 4),
            "pct_vs_own": _r(rw.pct_vs_own, 2),
            "pct_vs_peers": _r(rw.pct_vs_peers, 2),
            "n_hist": rw.n_hist,
            "n_shipments": len(rw.shipments),
            "anomalous": bool(f and f.anomalous),
        }
        if f:
            csv_row = f.csv_row()
            row.update(
                own_label=_signed(rw.pct_vs_own or 0.0),
                peer_label=_signed(rw.pct_vs_peers or 0.0),
                vs_own_history=csv_row["vs_own_history"],
                vs_similar_routes=csv_row["vs_similar_routes"],
                flagged=csv_row["flagged"],
                matched_note_id=csv_row["matched_note_id"],
                closest_note_id=f.match.closest.note_id if f.match.closest else "",
                reason=csv_row["reason"],
                drivers=f.drivers,
            )
        route_weeks.append(row)

    routes = []
    for route in sorted({rw.route for rw in analysis.route_weeks}):
        rws = [rw for rw in analysis.route_weeks if rw.route == route]
        ships = [sh for rw in rws for sh in rw.shipments]
        avg = sum(sh.cost_per_tonne_km for sh in ships) / len(ships)
        peak = max(rws, key=lambda rw: rw.cptk)
        routes.append(
            {
                "route": route,
                "route_type": rws[0].route_type,
                "n_shipments": len(ships),
                "n_weeks": len(rws),
                "first_week": rws[0].week_of,
                "last_week": rws[-1].week_of,
                "avg_cptk": round(avg, 4),
                "avg_cptk_label": f"{avg:.2f}",
                "peak_week": peak.week_of,
                "peak_cptk_label": f"{peak.cptk:.2f}",
                "n_anomalies": sum(1 for f in analysis.anomalies if f.rw.route == route),
            }
        )

    dates = [sh.shipment_date for sh in analysis.shipments]
    return {
        "meta": {
            "version": __version__,
            "n_shipments": len(analysis.shipments),
            "n_routes": len(routes),
            "n_route_weeks": len(analysis.route_weeks),
            "n_transporters": len({sh.transporter for sh in analysis.shipments}),
            "date_from": min(dates).isoformat(),
            "date_to": max(dates).isoformat(),
            "own_history_threshold_pct": s.own_history_threshold_pct,
            "peer_threshold_pct": s.peer_threshold_pct,
            "rolling_window_weeks": s.rolling_window_weeks,
            "note_window_days": s.note_window_days,
        },
        "routes": routes,
        "route_weeks": route_weeks,
        "notes": [_note_json(n) for n in analysis.notes],
    }


def _note_json(note: Note) -> dict[str, Any]:
    sc = score_note(note)
    return {
        "note_id": note.note_id,
        "date": note.date.isoformat(),
        "applies_to": note.applies_to,
        "note": note.text,
        "signals": {"positive": list(sc.positive), "negative": list(sc.negative), "net": sc.net},
    }


def write_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
