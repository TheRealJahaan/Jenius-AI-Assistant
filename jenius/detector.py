"""Weekly cost-per-tonne-km metrics and the two comparison baselines.

For every (route, week) the detector computes:

* ``cptk``: mean cost per tonne-km of that week's shipments.
* ``baseline``: mean of the route's previous ``rolling_window_weeks`` weekly
  values, using only weeks strictly before this one.
* ``peer_avg``: mean weekly value that same week across the *other* routes with
  the same ``route_type`` (the route is excluded from its own peer group).

A route-week is anomalous when it beats both baselines by the configured
thresholds. Everything here is pure arithmetic on sorted data, so the output is
byte-for-byte reproducible.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .config import DEFAULT_SETTINGS, Settings
from .data import Shipment


@dataclass(frozen=True)
class RouteWeek:
    route: str
    route_type: str
    week_of: str
    cptk: float
    shipments: tuple[Shipment, ...]
    baseline: float | None
    n_hist: int
    peer_avg: float | None
    n_peers: int

    @property
    def pct_vs_own(self) -> float | None:
        if self.baseline is None or self.baseline == 0:
            return None
        return (self.cptk - self.baseline) / self.baseline * 100.0

    @property
    def pct_vs_peers(self) -> float | None:
        if self.peer_avg is None or self.peer_avg == 0:
            return None
        return (self.cptk - self.peer_avg) / self.peer_avg * 100.0

    @property
    def comparable(self) -> bool:
        """True when both baselines exist, so the week can be judged at all."""
        return self.pct_vs_own is not None and self.pct_vs_peers is not None

    def is_anomalous(self, settings: Settings = DEFAULT_SETTINGS) -> bool:
        own, peers = self.pct_vs_own, self.pct_vs_peers
        if own is None or peers is None:
            return False
        return own >= settings.own_history_threshold_pct and peers >= settings.peer_threshold_pct


def compute_route_weeks(shipments: list[Shipment], settings: Settings = DEFAULT_SETTINGS) -> list[RouteWeek]:
    """Aggregate shipments into route-weeks with both baselines attached.

    Returned in (route, week) order.
    """
    by_key: dict[tuple[str, str], list[Shipment]] = defaultdict(list)
    route_type: dict[str, str] = {}
    for s in shipments:
        by_key[(s.route, s.week_of)].append(s)
        route_type[s.route] = s.route_type

    weekly: dict[tuple[str, str], float] = {
        key: sum(s.cost_per_tonne_km for s in group) / len(group) for key, group in by_key.items()
    }

    weeks_by_route: dict[str, list[str]] = defaultdict(list)
    peers_by_slot: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    for (route, week), value in weekly.items():
        weeks_by_route[route].append(week)
        peers_by_slot[(route_type[route], week)].append((route, value))
    for weeks in weeks_by_route.values():
        weeks.sort()

    result = []
    for route in sorted(weeks_by_route):
        weeks = weeks_by_route[route]
        for i, week in enumerate(weeks):
            prior = weeks[max(0, i - settings.rolling_window_weeks) : i][::-1]
            baseline = sum(weekly[(route, w)] for w in prior) / len(prior) if prior else None

            peers = [v for r, v in peers_by_slot[(route_type[route], week)] if r != route]
            peer_avg = sum(peers) / len(peers) if peers else None

            result.append(
                RouteWeek(
                    route=route,
                    route_type=route_type[route],
                    week_of=week,
                    cptk=weekly[(route, week)],
                    shipments=tuple(by_key[(route, week)]),
                    baseline=baseline,
                    n_hist=len(prior),
                    peer_avg=peer_avg,
                    n_peers=len(peers),
                )
            )
    return result


def find_anomalies(route_weeks: list[RouteWeek], settings: Settings = DEFAULT_SETTINGS) -> list[RouteWeek]:
    return [rw for rw in route_weeks if rw.is_anomalous(settings)]
