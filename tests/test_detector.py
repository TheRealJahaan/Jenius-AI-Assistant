import pytest

from jenius.config import Settings
from jenius.detector import compute_route_weeks, find_anomalies
from tests.conftest import make_shipment

WEEKS = [f"2024-{m:02d}-{d:02d}" for m, d in [(1, 1), (1, 8), (1, 15), (1, 22), (1, 29), (2, 5),
                                              (2, 12), (2, 19), (2, 26), (3, 4), (3, 11)]]  # fmt: skip


def series(route: str, costs: list[float], route_type: str = "Short") -> list:  # type: ignore[type-arg]
    return [make_shipment(route=route, route_type=route_type, date=w, cost=c)
            for w, c in zip(WEEKS, costs, strict=False)]  # fmt: skip


def by_week(rws, route):  # type: ignore[no-untyped-def]
    return {rw.week_of: rw for rw in rws if rw.route == route}


def test_weekly_mean_cptk() -> None:
    ships = [make_shipment(cost=100), make_shipment(date="2024-01-03", cost=300)]
    (rw,) = compute_route_weeks(ships)
    assert rw.cptk == pytest.approx(20.0)  # mean of 10 and 30 per tonne-km
    assert rw.baseline is None and not rw.comparable


def test_baseline_uses_only_strictly_prior_weeks_capped_at_window() -> None:
    costs = [100.0] * 9 + [1000.0, 100.0]
    rws = by_week(compute_route_weeks(series("A-B", costs)), "A-B")
    assert rws["2024-01-08"].baseline == pytest.approx(10.0)
    assert rws["2024-01-08"].n_hist == 1
    spike = rws["2024-03-04"]
    assert spike.n_hist == 8
    assert spike.baseline == pytest.approx(10.0)  # spike week not in its own baseline
    after = rws["2024-03-11"]
    assert after.baseline == pytest.approx((7 * 10 + 100) / 8)  # oldest week dropped


def test_peer_average_excludes_self_and_other_route_types() -> None:
    ships = (series("A-B", [100] * 3) + series("C-D", [200] * 3) + series("E-F", [400] * 3)
             + series("G-H", [9999] * 3, route_type="Long"))  # fmt: skip
    rw = by_week(compute_route_weeks(ships), "A-B")["2024-01-15"]
    assert rw.n_peers == 2
    assert rw.peer_avg == pytest.approx((20 + 40) / 2)


def test_both_thresholds_required() -> None:
    # A-B spikes vs its own history but so does the whole market -> not anomalous.
    market = [100.0] * 9 + [150.0]
    ships = series("A-B", market) + series("C-D", market)
    assert find_anomalies(compute_route_weeks(ships)) == []

    # Only A-B spikes -> beats both baselines.
    ships = series("A-B", market) + series("C-D", [100.0] * 10)
    (hit,) = find_anomalies(compute_route_weeks(ships))
    assert (hit.route, hit.week_of) == ("A-B", "2024-03-04")
    assert hit.pct_vs_own == pytest.approx(50.0)
    assert hit.pct_vs_peers == pytest.approx(50.0)


def test_thresholds_are_configurable() -> None:
    ships = series("A-B", [100.0] * 9 + [110.0]) + series("C-D", [100.0] * 10)
    assert find_anomalies(compute_route_weeks(ships)) == []
    loose = Settings(own_history_threshold_pct=5, peer_threshold_pct=5)
    assert len(find_anomalies(compute_route_weeks(ships, loose), loose)) == 1
