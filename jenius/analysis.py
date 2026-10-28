"""The anomaly engine: deterministic, no LLM.

Ported from the case-study solution (solution.py) and kept byte-for-byte compatible with its
output, so the eval harness and every past result still hold.

  - Cost per tonne-km (CPTK) = freight_cost_inr / (quantity_tonnes x distance_km)
  - vs own history    -> trailing 8-week average of the route's weekly mean CPTK, using only weeks
                          strictly before the current one (fewer if that's all there is)
  - vs similar routes -> mean weekly CPTK of every OTHER route with the same route_type that week
  - A route-week is an anomaly only when BOTH are at least 15% higher.
  - A context note justifies it when it applies to the route (or "All Routes"), is within 60 days,
    and describes a cost rise rather than a return to normal.
"""
import datetime as dt
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

OWN_HISTORY_THRESHOLD = 15.0
SIMILAR_ROUTE_THRESHOLD = 15.0
ROLLING_WINDOW_WEEKS = 8
NOTE_WINDOW_DAYS = 60

OUTPUT_FIELDS = [
    "route", "week_of", "cost_per_tonne_km",
    "vs_own_history", "vs_similar_routes",
    "flagged", "matched_note_id", "reason",
]

POSITIVE_SIGNALS = [
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
]
NEGATIVE_SIGNALS = [
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
]


@dataclass(frozen=True)
class ShipmentRow:
    route: str
    route_type: str
    quantity_tonnes: float
    distance_km: float
    freight_cost_inr: float
    shipment_date: dt.date


@dataclass(frozen=True)
class NoteRow:
    note_id: str
    date: dt.date
    applies_to: str
    note: str


@dataclass(frozen=True)
class AnomalyResult:
    route: str
    week_of: dt.date
    cost_per_tonne_km: float  # rounded to 2 places, as reported
    pct_vs_own: float
    pct_vs_similar: float
    history_weeks: int
    vs_own_history: str
    vs_similar_routes: str
    needs_review: bool
    matched_note_id: str
    reason: str

    @property
    def flagged(self) -> str:
        return "Yes" if self.needs_review else "No (justified)"

    def as_output_row(self) -> dict[str, str]:
        return {
            "route": self.route,
            "week_of": self.week_of.isoformat(),
            "cost_per_tonne_km": f"{self.cost_per_tonne_km:.2f}",
            "vs_own_history": self.vs_own_history,
            "vs_similar_routes": self.vs_similar_routes,
            "flagged": self.flagged,
            "matched_note_id": self.matched_note_id,
            "reason": self.reason,
        }


def week_start(day: dt.date) -> dt.date:
    """The Monday of the ISO week a date falls in."""
    return day - dt.timedelta(days=day.weekday())


def cost_per_tonne_km(quantity_tonnes: float, distance_km: float, freight_cost_inr: float) -> float:
    if quantity_tonnes == 0 or distance_km == 0:
        return 0.0
    return freight_cost_inr / (quantity_tonnes * distance_km)


def _normalise_route(label: str) -> str:
    return label.strip().replace("–", "-").replace(" ", "-").lower()


def note_applies(note: NoteRow, route: str, week: dt.date, window_days: int = NOTE_WINDOW_DAYS) -> bool:
    """True if a context note could be relevant to this route and week."""
    if abs((note.date - week).days) > window_days:
        return False
    applies_to = note.applies_to.strip()
    if applies_to.lower() == "all routes":
        return True
    return _normalise_route(applies_to) == _normalise_route(route)


def note_explains_cost_rise(text: str) -> bool:
    """Net-score heuristic: does the note mainly describe a genuine cost increase?

    "flooding ... detours ... higher trip costs ... passable again by Mar 9" scores positive (the
    postscript doesn't cancel the flood), while "returned to normal after flood repairs" and
    "remained stable, no major disruptions" score negative.
    """
    lowered = text.lower()
    positive = sum(w for phrase, w in POSITIVE_SIGNALS if phrase in lowered)
    negative = sum(w for phrase, w in NEGATIVE_SIGNALS if phrase in lowered)
    return positive - negative > 0


def weekly_route_costs(shipments: Iterable[ShipmentRow]) -> dict[tuple[str, dt.date], tuple[float, int]]:
    """(route, Monday) -> (mean CPTK, shipment count), in first-seen order."""
    buckets: dict[tuple[str, dt.date], list[float]] = defaultdict(list)
    for s in shipments:
        buckets[(s.route, week_start(s.shipment_date))].append(
            cost_per_tonne_km(s.quantity_tonnes, s.distance_km, s.freight_cost_inr)
        )
    return {key: (sum(vals) / len(vals), len(vals)) for key, vals in buckets.items()}


def detect(
    shipments: list[ShipmentRow],
    notes: list[NoteRow],
    own_threshold: float = OWN_HISTORY_THRESHOLD,
    similar_threshold: float = SIMILAR_ROUTE_THRESHOLD,
    window_weeks: int = ROLLING_WINDOW_WEEKS,
    note_window_days: int = NOTE_WINDOW_DAYS,
) -> list[AnomalyResult]:
    """Find anomalous route-weeks and explain each one from the context notes.

    Shipments must be in their original order: the averages are summed in that order, which is
    what keeps the output byte-identical to the original solution.
    """
    weekly = {key: mean for key, (mean, _count) in weekly_route_costs(shipments).items()}

    weeks_by_route: dict[str, list[dt.date]] = defaultdict(list)
    for route, week in weekly:
        weeks_by_route[route].append(week)
    for route in weeks_by_route:
        weeks_by_route[route].sort()

    route_type = {s.route: s.route_type for s in shipments}

    # Each (week, route type) with its routes' averages, in the same order the original scanned them,
    # so peer averages add up identically (float sums depend on order) without rescanning every week.
    peers_by_week: dict[tuple[dt.date, str], list[tuple[str, float]]] = defaultdict(list)
    for (r, w), avg in weekly.items():
        peers_by_week[(w, route_type.get(r, ""))].append((r, avg))

    def rolling_baseline(route: str, current: dt.date) -> tuple[float | None, int]:
        prior = sorted((w for w in weeks_by_route[route] if w < current), reverse=True)[:window_weeks]
        if not prior:
            return None, 0
        vals = [weekly[(route, w)] for w in prior]
        return sum(vals) / len(vals), len(prior)

    def peer_avg(route: str, week: dt.date) -> float | None:
        peers = [avg for r, avg in peers_by_week[(week, route_type.get(route, ""))] if r != route]
        return sum(peers) / len(peers) if peers else None

    found = []
    for (route, week), avg in weekly.items():
        hist_avg, n_hist = rolling_baseline(route, week)
        if hist_avg is None:
            continue
        pct_own = (avg - hist_avg) / hist_avg * 100.0
        p_avg = peer_avg(route, week)
        if p_avg is None or p_avg == 0:
            continue
        pct_sim = (avg - p_avg) / p_avg * 100.0
        if pct_own >= own_threshold and pct_sim >= similar_threshold:
            found.append((route, week, round(avg, 2), pct_own, pct_sim, n_hist))

    found.sort(key=lambda a: (a[0], a[1]))
    return [_explain(*a, notes=notes, window_weeks=window_weeks, note_window_days=note_window_days) for a in found]


def _explain(route, week, cptk, pct_own, pct_sim, n_hist, *, notes, window_weeks, note_window_days) -> AnomalyResult:
    hist_label = f"trailing {n_hist}-week avg" if n_hist < window_weeks else f"trailing {window_weeks}-week avg"
    own_str = f"{'+' if pct_own >= 0 else ''}{pct_own:.1f}% vs this route's {hist_label}"
    sim_str = f"{'+' if pct_sim >= 0 else ''}{pct_sim:.1f}% vs similar-length routes this week"

    def closeness(n: NoteRow) -> int:
        return abs((n.date - week).days)

    candidates = [n for n in notes if note_applies(n, route, week, note_window_days)]
    explaining = sorted((n for n in candidates if note_explains_cost_rise(n.note)), key=closeness)
    if explaining:
        best = explaining[0]
        return AnomalyResult(
            route, week, cptk, pct_own, pct_sim, n_hist, own_str, sim_str,
            needs_review=False,
            matched_note_id=best.note_id,
            reason=(
                f"Matches note {best.note_id} dated {best.date.isoformat()}: "
                f"{best.note.strip()} The cost rise has a clear explanation."
            ),
        )
    if candidates:
        closest = sorted(candidates, key=closeness)[0]
        reason = (
            f"The closest note ({closest.note_id}, {closest.date.isoformat()}) "
            f"mentions: \"{closest.note.strip()}\" -- "
            f"it does not describe a reason for a cost rise on this route. "
            f"No genuine justification found; flagged for review."
        )
    else:
        reason = (
            "No matching note found for this route or date range. "
            "Cost rise looks unexplained and worth a human review."
        )
    return AnomalyResult(route, week, cptk, pct_own, pct_sim, n_hist, own_str, sim_str,
                         needs_review=True, matched_note_id="", reason=reason)
