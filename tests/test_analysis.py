import csv
import datetime as dt
import io
from pathlib import Path

from jenius import analysis
from jenius.ingest import create_dataset, load_rows, parse_notes, parse_shipments
from solution import load_case_study

ROOT = Path(__file__).resolve().parent.parent

LABELLED = [  # (route, week_of, flagged, matched note) from manual inspection of the case study
    ("Ahmedabad-Mumbai", "2025-01-20", "No (justified)", "N002"),
    ("Chennai-Bangalore", "2025-02-24", "No (justified)", "N001"),
    ("Chennai-Bangalore", "2025-03-03", "No (justified)", "N001"),
    ("Delhi-Jaipur", "2024-11-11", "Yes", ""),
    ("Delhi-Jaipur", "2024-11-18", "Yes", ""),
]


def render(results) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=analysis.OUTPUT_FIELDS)
    writer.writeheader()
    writer.writerows(r.as_output_row() for r in results)
    return out.getvalue().encode()


def test_output_is_byte_identical_to_the_case_study_submission():
    shipments, notes = load_case_study()
    assert render(analysis.detect(shipments, notes)) == (ROOT / "output_anomalies.csv").read_bytes()


def test_labelled_cases():
    shipments, notes = load_case_study()
    rows = {(r.route, r.week_of.isoformat()): r for r in analysis.detect(shipments, notes)}
    for route, week, flagged, note in LABELLED:
        assert rows[(route, week)].flagged == flagged
        assert rows[(route, week)].matched_note_id == note


def test_note_heuristic():
    assert analysis.note_explains_cost_rise("Heavy flooding ... forcing longer detours and higher trip costs. "
                                            "Roads were passable again by Mar 9.")
    assert not analysis.note_explains_cost_rise("The route returned to normal conditions after flood-related repairs.")
    assert not analysis.note_explains_cost_rise("Demand remained stable with no major disruptions reported.")


def test_week_starts_on_monday():
    assert analysis.week_start(dt.date(2025, 3, 2)) == dt.date(2025, 2, 24)  # a Sunday
    assert analysis.week_start(dt.date(2025, 2, 24)) == dt.date(2025, 2, 24)


def test_database_round_trip_gives_the_same_anomalies(database):
    data = ROOT / "AI Intern Case Study"
    with database.sessions() as db:
        dataset = create_dataset(db, name="t", owner_id=None,
                                 shipments=parse_shipments((data / "shipment_records.csv").read_bytes(), 10**6),
                                 notes=parse_notes((data / "context_notes.csv").read_bytes()))
        shipments, notes = load_rows(db, dataset.id)
    assert render(analysis.detect(shipments, notes)) == (ROOT / "output_anomalies.csv").read_bytes()
