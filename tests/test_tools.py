import json

import pytest

from jenius.assistant.tools import TOOLS, ToolBox, ToolInputError
from jenius.demo import ensure_demo


@pytest.fixture
def box(database):
    with database.sessions() as db:
        yield ToolBox(db, ensure_demo(db).id)


def run(box, name, **args):
    return json.loads(box.run(name, args))


def test_every_tool_has_a_handler(box):
    for tool in TOOLS:
        assert hasattr(box, f"tool_{tool['name']}"), tool["name"]
        assert tool["input_schema"]["additionalProperties"] is False


def test_overview(box):
    overview = run(box, "get_overview")
    assert overview["shipments"] == 2940 and overview["anomalies"] == {"total": 6, "needs_review": 2, "justified": 4}
    assert any(r["route"] == "Chennai-Bangalore" for r in overview["routes"])


def test_anomaly_filters_and_route_names(box):
    assert run(box, "list_anomalies", status="needs_review")["count"] == 2
    feb = run(box, "list_anomalies", route="chennai to bangalore", start_date="2025-02-01", end_date="2025-02-28")
    assert [a["week_of"] for a in feb["anomalies"]] == ["2025-02-24"]
    assert feb["anomalies"][0]["matched_note_id"] == "N001"


def test_weekly_costs_compare_and_transporters(box):
    weeks = run(box, "route_weekly_costs", route="Delhi-Jaipur", start_date="2024-11-01", end_date="2024-11-30")["weeks"]
    assert [w["week_of"] for w in weeks][:2] == ["2024-10-28", "2024-11-04"]
    assert all(w["mean_cost_per_tonne_km"] > 0 for w in weeks)
    routes = run(box, "compare_routes", route_type="Short")["routes"]
    assert routes and {r["route_type"] for r in routes} == {"Short"}
    carriers = run(box, "transporter_breakdown", route="Delhi-Jaipur")["transporters"]
    costs = [c["avg_cost_per_tonne_km"] for c in carriers]
    assert costs == sorted(costs, reverse=True)


def test_find_shipments_and_notes(box):
    found = run(box, "find_shipments", route="Delhi-Jaipur", start_date="2024-11-11", end_date="2024-11-17", limit=3)
    assert found["shown"] <= 3 and all(s["route"] == "Delhi-Jaipur" for s in found["shipments"])
    notes = run(box, "search_notes", query="flooding highway detours")["notes"]
    assert notes[0]["note_id"] == "N001"


def test_bad_inputs_explain_themselves(box):
    with pytest.raises(ToolInputError, match="Routes: Ahmedabad-Mumbai"):
        box.run("route_weekly_costs", {"route": "Nowhere"})
    with pytest.raises(ToolInputError, match="no input called colour"):
        box.run("get_overview", {"colour": "red"})
    with pytest.raises(ToolInputError, match="needs route"):
        box.run("route_weekly_costs", {})
    with pytest.raises(ToolInputError, match="YYYY-MM-DD"):
        box.run("list_anomalies", {"start_date": "last week"})
    with pytest.raises(ToolInputError, match="no tool"):
        box.run("drop_tables", {})
