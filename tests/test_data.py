from pathlib import Path

import pytest

from jenius.data import DataError, load_notes, load_shipments, parse_shipments
from tests.conftest import make_shipment


def test_derived_fields() -> None:
    s = make_shipment(route="Mumbai-Pune", date="2024-01-04", cost=6000, qty=12, dist=150)
    assert s.route == "Mumbai-Pune"
    assert s.week_of == "2024-01-01"  # Thursday -> Monday of that week
    assert s.cost_per_tonne_km == pytest.approx(6000 / (12 * 150))


def test_zero_denominator_is_safe() -> None:
    assert make_shipment(qty=0).cost_per_tonne_km == 0.0


def test_missing_column(tmp_path: Path) -> None:
    f = tmp_path / "s.csv"
    f.write_text("shipment_id,origin\nS1,Mumbai\n")
    with pytest.raises(DataError, match="missing columns"):
        load_shipments(f)


def test_bad_value_reports_line() -> None:
    row = dict.fromkeys(
        ("shipment_id", "origin", "destination", "route_type", "material", "transporter"), "x"
    )
    row.update(quantity_tonnes="ten", distance_km="1", freight_cost_inr="1", shipment_date="2024-01-01")
    with pytest.raises(DataError, match="row 2"):
        parse_shipments([row])


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="not found"):
        load_notes(tmp_path / "nope.csv")


def test_real_files_load(analysis) -> None:  # type: ignore[no-untyped-def]
    assert len(analysis.shipments) == 2940
    assert [n.note_id for n in analysis.notes] == [f"N{i:03d}" for i in range(1, 11)]
