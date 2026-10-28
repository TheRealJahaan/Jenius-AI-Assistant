"""The tools Claude uses to answer from the database.

Every tool is read-only and bound to one dataset by the server, never by the model, so nothing a
note or a question says can reach another person's data. Inputs are validated here and mistakes go
back to Claude as errors it can recover from (an unknown route lists the real ones).
"""
import datetime as dt
import inspect
import json
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analysis import cost_per_tonne_km, week_start
from ..models import Anomaly, ContextNote, Dataset, Shipment
from ..retrieval import NoteIndex

_DATE = {"type": "string", "description": "Inclusive date, YYYY-MM-DD. Omit for no limit."}
CPTK = Shipment.freight_cost_inr / (Shipment.quantity_tonnes * Shipment.distance_km)
_ROUTE = {"type": "string", "description": "Route as 'Origin-Destination', e.g. 'Chennai-Bangalore'."}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_overview",
        "description": "Summary of the dataset: shipment and note counts, date range, total spend, every route with "
                       "its route type and shipment count, and how many anomalies are justified or need review. "
                       "Call this first when you don't yet know which routes or dates exist.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_anomalies",
        "description": "Anomalous route-weeks found by the deterministic engine: cost per tonne-km at least 15% above "
                       "both the route's trailing 8-week average and same-type peer routes that week. Each has the "
                       "matched context note (if one justifies it), the engine's reason, and the reviewer's status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "route": _ROUTE,
                "start_date": _DATE,
                "end_date": _DATE,
                "status": {"type": "string", "enum": ["all", "needs_review", "justified"],
                           "description": "needs_review = no note explains it. Default all."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "route_weekly_costs",
        "description": "Week-by-week mean cost per tonne-km, shipment count and spend for one route (weeks start on "
                       "Monday). Use it to show trends or check a specific week. At most 104 weeks are returned.",
        "input_schema": {
            "type": "object",
            "properties": {"route": _ROUTE, "start_date": _DATE, "end_date": _DATE},
            "required": ["route"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compare_routes",
        "description": "Per-route totals for a period: shipments, tonnes, spend and average cost per tonne-km, "
                       "optionally only one route type (for example 'Short' or 'Long').",
        "input_schema": {
            "type": "object",
            "properties": {"start_date": _DATE, "end_date": _DATE,
                           "route_type": {"type": "string", "description": "Only routes of this type."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "transporter_breakdown",
        "description": "Per-transporter shipments, spend and average cost per tonne-km, optionally for one route and "
                       "period. Use it to see which carriers are expensive.",
        "input_schema": {
            "type": "object",
            "properties": {"route": _ROUTE, "start_date": _DATE, "end_date": _DATE},
            "additionalProperties": False,
        },
    },
    {
        "name": "find_shipments",
        "description": "Individual shipments, most expensive per tonne-km first. Filter by route, transporter and "
                       "dates. Returns at most 50 rows.",
        "input_schema": {
            "type": "object",
            "properties": {
                "route": _ROUTE,
                "transporter": {"type": "string"},
                "start_date": _DATE,
                "end_date": _DATE,
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "search_notes",
        "description": "Search the dataset's context notes (operations notes about floods, surcharges, fuel prices "
                       "and so on) by meaning-bearing words. Returns the best matches with their full text.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 10}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]


class ToolInputError(ValueError):
    """Bad input from the model; the message tells it how to fix the call."""


def _date(value: Any, field: str) -> dt.date | None:
    if value in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ToolInputError(f"{field} must be YYYY-MM-DD, got {value!r}") from None


def _limit(value: Any, default: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        raise ToolInputError(f"limit must be a whole number up to {maximum}") from None


def _r(value: float, places: int = 2) -> float:
    return round(float(value), places)


class ToolBox:
    def __init__(self, db: Session, dataset_id: uuid.UUID):
        self.db = db
        self.dataset_id = dataset_id
        self._routes: dict[str, str] | None = None

    # ── helpers ──────────────────────────────────────────────────────────────

    def routes(self) -> dict[str, str]:
        """Lower-cased route key -> route as stored."""
        if self._routes is None:
            names = self.db.scalars(select(Shipment.route).where(Shipment.dataset_id == self.dataset_id).distinct())
            self._routes = {self._key(n): n for n in names}
        return self._routes

    @staticmethod
    def _key(route: str) -> str:
        return " ".join(route.lower().replace(" to ", "-").replace("–", "-").replace("-", " ").split())

    def resolve_route(self, route: Any) -> str | None:
        if route in (None, ""):
            return None
        found = self.routes().get(self._key(str(route)))
        if found is None:
            known = ", ".join(sorted(self.routes().values()))
            raise ToolInputError(f"No route {route!r} in this dataset. Routes: {known}")
        return found

    # ── dispatch ─────────────────────────────────────────────────────────────

    def run(self, name: str, args: dict[str, Any]) -> str:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None or name not in {t["name"] for t in TOOLS}:
            raise ToolInputError(f"There is no tool called {name}.")
        if not isinstance(args, dict):
            raise ToolInputError("Tool input must be a JSON object.")
        params = inspect.signature(handler).parameters
        unknown = sorted(set(args) - set(params))
        if unknown:
            raise ToolInputError(f"{name} has no input called {', '.join(unknown)}.")
        missing = [p.name for p in params.values() if p.default is inspect.Parameter.empty and p.name not in args]
        if missing:
            raise ToolInputError(f"{name} needs {', '.join(missing)}.")
        return json.dumps(handler(**args), ensure_ascii=False, default=str, separators=(",", ":"))

    # ── tools ────────────────────────────────────────────────────────────────

    def tool_get_overview(self) -> dict:
        dataset = self.db.get(Dataset, self.dataset_id)
        per_route = self.db.execute(
            select(Shipment.route, Shipment.route_type, func.count(), func.sum(Shipment.freight_cost_inr))
            .where(Shipment.dataset_id == self.dataset_id)
            .group_by(Shipment.route, Shipment.route_type)
            .order_by(Shipment.route)
        ).all()
        review = self.db.execute(
            select(Anomaly.needs_review, func.count()).where(Anomaly.dataset_id == self.dataset_id)
            .group_by(Anomaly.needs_review)
        ).all()
        counts = {bool(k): v for k, v in review}
        return {
            "dataset": dataset.name,
            "shipments": dataset.shipment_count,
            "context_notes": dataset.note_count,
            "first_shipment_date": dataset.first_date,
            "last_shipment_date": dataset.last_date,
            "total_freight_cost_inr": _r(sum(r[3] or 0 for r in per_route), 0),
            "routes": [{"route": r[0], "route_type": r[1], "shipments": r[2]} for r in per_route],
            "anomalies": {"total": dataset.anomaly_count, "needs_review": counts.get(True, 0),
                          "justified": counts.get(False, 0)},
        }

    def tool_list_anomalies(self, route=None, start_date=None, end_date=None, status="all") -> dict:
        q = select(Anomaly).where(Anomaly.dataset_id == self.dataset_id)
        if (route := self.resolve_route(route)) is not None:
            q = q.where(Anomaly.route == route)
        if (start := _date(start_date, "start_date")) is not None:
            q = q.where(Anomaly.week_of >= week_start(start))
        if (end := _date(end_date, "end_date")) is not None:
            q = q.where(Anomaly.week_of <= end)
        if status == "needs_review":
            q = q.where(Anomaly.needs_review.is_(True))
        elif status == "justified":
            q = q.where(Anomaly.needs_review.is_(False))
        elif status not in (None, "all"):
            raise ToolInputError("status must be all, needs_review or justified")
        rows = self.db.scalars(q.order_by(Anomaly.week_of, Anomaly.route).limit(100)).all()
        return {"count": len(rows), "anomalies": [{
            "route": a.route, "week_of": a.week_of, "cost_per_tonne_km": _r(a.cost_per_tonne_km),
            "pct_vs_own_history": _r(a.pct_vs_own, 1), "pct_vs_similar_routes": _r(a.pct_vs_similar, 1),
            "history_weeks_used": a.history_weeks,
            "verdict": "needs review (unexplained)" if a.needs_review else "justified by a note",
            "matched_note_id": a.matched_note_code or None, "engine_reason": a.reason,
            "review_status": a.review_status,
        } for a in rows]}

    def tool_route_weekly_costs(self, route, start_date=None, end_date=None) -> dict:
        route = self.resolve_route(route)
        if route is None:
            raise ToolInputError("route is required")
        start, end = _date(start_date, "start_date"), _date(end_date, "end_date")
        weeks: dict[dt.date, list] = defaultdict(lambda: [[], 0.0])
        for s in self.db.scalars(self._filters(select(Shipment), route, start, end).order_by(Shipment.id)):
            bucket = weeks[week_start(s.shipment_date)]
            bucket[0].append(cost_per_tonne_km(s.quantity_tonnes, s.distance_km, s.freight_cost_inr))
            bucket[1] += s.freight_cost_inr
        ordered = sorted(weeks.items())
        truncated = len(ordered) > 104
        return {"route": route, "truncated_to_last_104_weeks": truncated, "weeks": [
            {"week_of": w, "mean_cost_per_tonne_km": _r(sum(c) / len(c), 3), "shipments": len(c),
             "freight_cost_inr": _r(total, 0)}
            for w, (c, total) in ordered[-104:]
        ]}

    def _filters(self, q, route=None, start=None, end=None, transporter=None):
        q = q.where(Shipment.dataset_id == self.dataset_id)
        if route:
            q = q.where(Shipment.route == route)
        if start:
            q = q.where(Shipment.shipment_date >= start)
        if end:
            q = q.where(Shipment.shipment_date <= end)
        if transporter:
            q = q.where(func.lower(Shipment.transporter) == str(transporter).strip().lower())
        return q

    def tool_compare_routes(self, start_date=None, end_date=None, route_type=None) -> dict:
        start, end = _date(start_date, "start_date"), _date(end_date, "end_date")
        q = self._filters(select(Shipment.route, func.min(Shipment.route_type), func.count(),
                                 func.sum(Shipment.quantity_tonnes), func.sum(Shipment.freight_cost_inr),
                                 func.avg(CPTK)), None, start, end)
        if route_type:
            q = q.where(func.lower(Shipment.route_type) == str(route_type).strip().lower())
        rows = self.db.execute(q.group_by(Shipment.route).order_by(Shipment.route)).all()
        return {"routes": [{"route": r, "route_type": t, "shipments": n, "tonnes": _r(tonnes, 1),
                            "freight_cost_inr": _r(cost, 0), "avg_cost_per_tonne_km": _r(cptk, 3)}
                           for r, t, n, tonnes, cost, cptk in rows]}

    def tool_transporter_breakdown(self, route=None, start_date=None, end_date=None) -> dict:
        route = self.resolve_route(route)
        start, end = _date(start_date, "start_date"), _date(end_date, "end_date")
        q = self._filters(select(Shipment.transporter, func.count(), func.sum(Shipment.freight_cost_inr),
                                 func.avg(CPTK)), route, start, end)
        rows = self.db.execute(q.group_by(Shipment.transporter).order_by(func.avg(CPTK).desc())).all()
        return {"route": route or "all routes", "transporters": [
            {"transporter": t or "(not recorded)", "shipments": n, "freight_cost_inr": _r(cost, 0),
             "avg_cost_per_tonne_km": _r(cptk, 3)}
            for t, n, cost, cptk in rows
        ]}

    def tool_find_shipments(self, route=None, transporter=None, start_date=None, end_date=None, limit=None) -> dict:
        route = self.resolve_route(route)
        start, end = _date(start_date, "start_date"), _date(end_date, "end_date")
        take = _limit(limit, 20, 50)
        total = self.db.scalar(self._filters(select(func.count(Shipment.id)), route, start, end, transporter))
        rows = self.db.scalars(self._filters(select(Shipment), route, start, end, transporter)
                               .order_by(CPTK.desc(), Shipment.id).limit(take)).all()
        return {"matching_shipments": total, "shown": len(rows), "shipments": [{
            "shipment_id": s.shipment_code, "date": s.shipment_date, "route": s.route, "transporter": s.transporter,
            "material": s.material, "quantity_tonnes": s.quantity_tonnes, "distance_km": s.distance_km,
            "freight_cost_inr": s.freight_cost_inr,
            "cost_per_tonne_km": _r(cost_per_tonne_km(s.quantity_tonnes, s.distance_km, s.freight_cost_inr), 3),
        } for s in rows]}

    def tool_search_notes(self, query, limit=None) -> dict:
        if not str(query).strip():
            raise ToolInputError("query must not be empty")
        notes = self.db.scalars(select(ContextNote).where(ContextNote.dataset_id == self.dataset_id)).all()
        hits = NoteIndex(notes).search(str(query), _limit(limit, 5, 10))
        return {"notes": [{"note_id": n.note_code, "date": n.note_date, "applies_to": n.applies_to, "text": n.text,
                           "relevance": score} for n, score in hits]}
