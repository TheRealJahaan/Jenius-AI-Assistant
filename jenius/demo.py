"""The public demo dataset: the FreightTiger case study, readable by everyone, signed in or not."""
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ingest import create_dataset, parse_notes, parse_shipments
from .models import Dataset

DEMO_DIR = Path(__file__).resolve().parent.parent / "AI Intern Case Study"
DEMO_NAME = "Demo: FreightTiger case study"


def ensure_demo(db: Session) -> Dataset | None:
    """Create the demo dataset once. Safe to run on every start."""
    existing = db.scalar(select(Dataset).where(Dataset.is_demo.is_(True)))
    if existing is not None:
        return existing
    shipments_file = DEMO_DIR / "shipment_records.csv"
    if not shipments_file.exists():
        return None
    shipments = parse_shipments(shipments_file.read_bytes(), max_rows=1_000_000)
    notes = parse_notes((DEMO_DIR / "context_notes.csv").read_bytes())
    return create_dataset(db, name=DEMO_NAME, owner_id=None, shipments=shipments, notes=notes, is_demo=True)
