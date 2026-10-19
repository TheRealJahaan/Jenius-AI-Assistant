"""Typed loaders for the two input files: shipment records and context notes."""

from __future__ import annotations

import csv
import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SHIPMENT_COLUMNS = (
    "shipment_id",
    "origin",
    "destination",
    "route_type",
    "material",
    "quantity_tonnes",
    "distance_km",
    "freight_cost_inr",
    "shipment_date",
    "transporter",
)
NOTE_COLUMNS = ("note_id", "date", "applies_to", "note")


class DataError(ValueError):
    """Raised when an input file is missing columns or has unparseable values."""


@dataclass(frozen=True)
class Shipment:
    shipment_id: str
    origin: str
    destination: str
    route_type: str
    material: str
    quantity_tonnes: float
    distance_km: float
    freight_cost_inr: float
    shipment_date: dt.date
    transporter: str

    @property
    def route(self) -> str:
        return f"{self.origin}-{self.destination}"

    @property
    def week_of(self) -> str:
        """ISO date of the Monday that starts this shipment's week."""
        monday = self.shipment_date - dt.timedelta(days=self.shipment_date.weekday())
        return monday.isoformat()

    @property
    def cost_per_tonne_km(self) -> float:
        denom = self.quantity_tonnes * self.distance_km
        return self.freight_cost_inr / denom if denom else 0.0


@dataclass(frozen=True)
class Note:
    note_id: str
    date: dt.date
    applies_to: str
    text: str


def _read_rows(path: Path, required: Iterable[str]) -> list[dict[str, str]]:
    if not path.exists():
        raise DataError(f"Input file not found: {path}")
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = set(required) - set(reader.fieldnames or ())
        if missing:
            raise DataError(f"{path.name} is missing columns: {', '.join(sorted(missing))}")
        # Skip fully blank rows (the notes file ends with one).
        return [row for row in reader if any((v or "").strip() for v in row.values())]


def parse_shipments(rows: Iterable[dict[str, str]]) -> list[Shipment]:
    shipments = []
    for line, row in enumerate(rows, start=2):
        try:
            shipments.append(
                Shipment(
                    shipment_id=row["shipment_id"].strip(),
                    origin=row["origin"].strip(),
                    destination=row["destination"].strip(),
                    route_type=row["route_type"].strip(),
                    material=row["material"].strip(),
                    quantity_tonnes=float(row["quantity_tonnes"]),
                    distance_km=float(row["distance_km"]),
                    freight_cost_inr=float(row["freight_cost_inr"]),
                    shipment_date=dt.date.fromisoformat(row["shipment_date"].strip()),
                    transporter=row["transporter"].strip(),
                )
            )
        except (KeyError, ValueError) as exc:
            raise DataError(f"Bad shipment row {line}: {exc}") from exc
    return shipments


def parse_notes(rows: Iterable[dict[str, str]]) -> list[Note]:
    notes = []
    for line, row in enumerate(rows, start=2):
        if not (row.get("note_id") or "").strip():
            continue
        try:
            notes.append(
                Note(
                    note_id=row["note_id"].strip(),
                    date=dt.date.fromisoformat(row["date"].strip()),
                    applies_to=row["applies_to"].strip(),
                    text=row["note"].strip(),
                )
            )
        except (KeyError, ValueError) as exc:
            raise DataError(f"Bad note row {line}: {exc}") from exc
    return notes


def load_shipments(path: Path) -> list[Shipment]:
    return parse_shipments(_read_rows(path, SHIPMENT_COLUMNS))


def load_notes(path: Path) -> list[Note]:
    return parse_notes(_read_rows(path, NOTE_COLUMNS))
