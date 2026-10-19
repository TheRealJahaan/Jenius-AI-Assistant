from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from jenius.data import Note, Shipment
from jenius.pipeline import default_knowledge_base, run
from jenius.report import Analysis


@pytest.fixture(scope="session")
def analysis() -> Analysis:
    return run()


@pytest.fixture(scope="session")
def kb() -> dict[str, Any]:
    return default_knowledge_base()


def make_shipment(
    route: str = "A-B",
    route_type: str = "Short",
    date: str = "2024-01-01",
    cost: float = 100.0,
    qty: float = 1.0,
    dist: float = 10.0,
    transporter: str = "T1",
    sid: str = "S",
) -> Shipment:
    origin, destination = route.split("-")
    return Shipment(
        shipment_id=sid,
        origin=origin,
        destination=destination,
        route_type=route_type,
        material="Cement",
        quantity_tonnes=qty,
        distance_km=dist,
        freight_cost_inr=cost,
        shipment_date=dt.date.fromisoformat(date),
        transporter=transporter,
    )


def make_note(note_id: str, date: str, applies_to: str, text: str) -> Note:
    return Note(note_id=note_id, date=dt.date.fromisoformat(date), applies_to=applies_to, text=text)
