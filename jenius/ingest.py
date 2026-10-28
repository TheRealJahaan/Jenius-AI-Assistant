"""Reading uploaded CSVs into the database and running the anomaly engine over them.

Uploads are untrusted: every row is checked, and the first problems are reported back with their
line numbers instead of being silently skipped or stored as zeros.
"""
import csv
import datetime as dt
import io
import math
import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from . import analysis
from .db import utcnow
from .models import Anomaly, ContextNote, Dataset, Shipment

SHIPMENT_COLUMNS = ["shipment_id", "origin", "destination", "route_type",
                    "quantity_tonnes", "distance_km", "freight_cost_inr", "shipment_date"]
OPTIONAL_SHIPMENT_COLUMNS = ["material", "transporter"]
NOTE_COLUMNS = ["note_id", "date", "applies_to", "note"]
MAX_REPORTED_ERRORS = 10


class UploadError(ValueError):
    """The file can't be used. `problems` lists what to fix, with line numbers."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or []


@dataclass
class ParsedUpload:
    shipments: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)


def _reader(raw: bytes, label: str, required: list[str]) -> csv.DictReader:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UploadError(f"The {label} file must be a UTF-8 CSV (save it from Excel as 'CSV UTF-8').") from None
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in required if c not in header]
    if missing:
        raise UploadError(f"The {label} file is missing columns: {', '.join(missing)}.",
                          [f"Expected columns: {', '.join(required)}"])
    reader.fieldnames = header
    return reader


def _text(row: dict, key: str, limit: int) -> str:
    value = (row.get(key) or "").strip()
    if len(value) > limit:
        raise ValueError(f"{key} is longer than {limit} characters")
    return value


def _positive(row: dict, key: str) -> float:
    raw = (row.get(key) or "").strip().replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{key} must be a number (got {raw!r})") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be greater than 0 (got {raw!r})")
    return value


def _date(row: dict, key: str) -> dt.date:
    raw = (row.get(key) or "").strip()
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"{key} must be a date like 2025-02-24 (got {raw!r})") from None


def parse_shipments(raw: bytes, max_rows: int) -> list[dict]:
    reader = _reader(raw, "shipments", SHIPMENT_COLUMNS)
    rows, problems = [], []
    for line, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue  # blank line
        if len(rows) >= max_rows:
            raise UploadError(f"The shipments file has more than {max_rows:,} rows, the limit for your plan.")
        try:
            origin = _text(row, "origin", 80)
            destination = _text(row, "destination", 80)
            if not origin or not destination:
                raise ValueError("origin and destination are required")
            rows.append({
                "shipment_code": _text(row, "shipment_id", 64) or f"row-{line}",
                "origin": origin,
                "destination": destination,
                "route": f"{origin}-{destination}",
                "route_type": _text(row, "route_type", 40) or "Unspecified",
                "material": _text(row, "material", 80),
                "quantity_tonnes": _positive(row, "quantity_tonnes"),
                "distance_km": _positive(row, "distance_km"),
                "freight_cost_inr": _positive(row, "freight_cost_inr"),
                "shipment_date": _date(row, "shipment_date"),
                "transporter": _text(row, "transporter", 120),
            })
        except ValueError as err:
            problems.append(f"Line {line}: {err}")
            if len(problems) >= MAX_REPORTED_ERRORS:
                break
    if problems:
        raise UploadError("Some shipment rows need fixing before Jenius can analyse them.", problems)
    if not rows:
        raise UploadError("The shipments file has no rows.")
    return rows


def parse_notes(raw: bytes, max_rows: int = 5000) -> list[dict]:
    reader = _reader(raw, "context notes", NOTE_COLUMNS)
    rows, problems, seen = [], [], set()
    for line, row in enumerate(reader, start=2):
        if not (row.get("note_id") or "").strip():
            continue  # the case-study file ends with blank rows
        if len(rows) >= max_rows:
            raise UploadError(f"The notes file has more than {max_rows:,} rows.")
        try:
            code = _text(row, "note_id", 40)
            if code in seen:
                raise ValueError(f"note_id {code} appears twice")
            seen.add(code)
            text = _text(row, "note", 4000)
            if not text:
                raise ValueError("note text is empty")
            rows.append({"note_code": code, "note_date": _date(row, "date"),
                         "applies_to": _text(row, "applies_to", 170) or "All Routes", "text": text})
        except ValueError as err:
            problems.append(f"Line {line}: {err}")
            if len(problems) >= MAX_REPORTED_ERRORS:
                break
    if problems:
        raise UploadError("Some context notes need fixing.", problems)
    return rows


def create_dataset(db: Session, *, name: str, owner_id: uuid.UUID | None, shipments: list[dict],
                   notes: list[dict], is_demo: bool = False) -> Dataset:
    """Store a dataset and its rows, then analyse it. Commits."""
    dataset = Dataset(name=name, owner_id=owner_id, is_demo=is_demo)
    db.add(dataset)
    db.flush()
    db.execute(insert(Shipment), [{**s, "dataset_id": dataset.id} for s in shipments])
    if notes:
        db.execute(insert(ContextNote), [{**n, "dataset_id": dataset.id} for n in notes])
    analyze_dataset(db, dataset)
    db.commit()
    return dataset


def load_rows(db: Session, dataset_id: uuid.UUID) -> tuple[list[analysis.ShipmentRow], list[analysis.NoteRow]]:
    """The dataset's rows in upload order (the engine's averages depend on it)."""
    shipments = [
        analysis.ShipmentRow(r.route, r.route_type, r.quantity_tonnes, r.distance_km, r.freight_cost_inr,
                             r.shipment_date)
        for r in db.execute(
            select(Shipment.route, Shipment.route_type, Shipment.quantity_tonnes, Shipment.distance_km,
                   Shipment.freight_cost_inr, Shipment.shipment_date)
            .where(Shipment.dataset_id == dataset_id).order_by(Shipment.id)
        )
    ]
    notes = [
        analysis.NoteRow(n.note_code, n.note_date, n.applies_to, n.text)
        for n in db.scalars(select(ContextNote).where(ContextNote.dataset_id == dataset_id).order_by(ContextNote.id))
    ]
    return shipments, notes


def analyze_dataset(db: Session, dataset: Dataset) -> list[analysis.AnomalyResult]:
    """(Re)run the engine and replace the dataset's anomalies. Review decisions on route-weeks that
    are still anomalous are kept. Does not commit."""
    shipments, notes = load_rows(db, dataset.id)
    results = analysis.detect(shipments, notes)

    previous = {(a.route, a.week_of): a.review_status
                for a in db.scalars(select(Anomaly).where(Anomaly.dataset_id == dataset.id))}
    db.execute(delete(Anomaly).where(Anomaly.dataset_id == dataset.id))
    db.add_all(
        Anomaly(
            dataset_id=dataset.id, route=r.route, week_of=r.week_of, cost_per_tonne_km=r.cost_per_tonne_km,
            pct_vs_own=r.pct_vs_own, pct_vs_similar=r.pct_vs_similar, history_weeks=r.history_weeks,
            vs_own_history=r.vs_own_history, vs_similar_routes=r.vs_similar_routes,
            needs_review=r.needs_review, matched_note_code=r.matched_note_id, reason=r.reason,
            review_status=previous.get((r.route, r.week_of), "open"),
        )
        for r in results
    )
    dates = [s.shipment_date for s in shipments]
    dataset.shipment_count = len(shipments)
    dataset.note_count = len(notes)
    dataset.anomaly_count = len(results)
    dataset.first_date = min(dates) if dates else None
    dataset.last_date = max(dates) if dates else None
    dataset.analyzed_at = utcnow()
    return results


def _cell(value: str) -> str:
    """Stop a value someone uploaded from running as a formula when the CSV is opened in Excel."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def anomalies_csv(rows: list[Anomaly]) -> str:
    """The anomalies in the case study's output format (sample_output_format_v2.csv)."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=analysis.OUTPUT_FIELDS)
    writer.writeheader()
    for a in rows:
        writer.writerow({
            "route": _cell(a.route), "week_of": a.week_of.isoformat(),
            "cost_per_tonne_km": f"{a.cost_per_tonne_km:.2f}",
            "vs_own_history": a.vs_own_history, "vs_similar_routes": a.vs_similar_routes,
            "flagged": "Yes" if a.needs_review else "No (justified)",
            "matched_note_id": _cell(a.matched_note_code), "reason": a.reason,
        })
    return out.getvalue()
