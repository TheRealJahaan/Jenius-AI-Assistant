from pathlib import Path

from helpers import signup

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "AI Intern Case Study"


def upload_case_study(client, name="Case study copy"):
    return client.post(
        "/api/datasets", data={"name": name},
        files={"shipments": ("shipments.csv", (DATA / "shipment_records.csv").read_bytes(), "text/csv"),
               "notes": ("notes.csv", (DATA / "context_notes.csv").read_bytes(), "text/csv")},
    )


def test_upload_analyse_review_export_delete(make_client):
    client = make_client(free_max_rows=10_000)
    signup(client)
    res = upload_case_study(client)
    assert res.status_code == 201, res.text
    dataset = res.json()
    assert dataset["is_mine"] and dataset["shipment_count"] == 2940 and dataset["anomaly_count"] == 6

    detail = client.get(f"/api/datasets/{dataset['id']}").json()
    assert detail["needs_review"] == 2 and detail["justified"] == 4
    assert {r["route"] for r in detail["routes"]} >= {"Chennai-Bangalore", "Delhi-Jaipur"}

    review = client.get(f"/api/datasets/{dataset['id']}/anomalies?status=needs_review").json()
    assert [a["route"] for a in review] == ["Delhi-Jaipur", "Delhi-Jaipur"]
    patched = client.patch(f"/api/datasets/{dataset['id']}/anomalies/{review[0]['id']}",
                           json={"review_status": "confirmed"})
    assert patched.json()["review_status"] == "confirmed"

    export = client.get(f"/api/datasets/{dataset['id']}/anomalies.csv")
    assert export.headers["content-type"].startswith("text/csv")
    assert export.content == (ROOT / "output_anomalies.csv").read_bytes()

    assert client.delete(f"/api/datasets/{dataset['id']}").status_code == 200
    assert client.get(f"/api/datasets/{dataset['id']}").status_code == 404


def test_upload_problems_are_explained(client):
    signup(client)
    bad = "shipment_id,origin,destination,route_type,quantity_tonnes,distance_km,freight_cost_inr,shipment_date\nS1,A,B,Short,ten,1,1,2025-01-01\n"
    res = client.post("/api/datasets", data={"name": "bad"}, files={"shipments": ("s.csv", bad, "text/csv")})
    assert res.status_code == 422
    assert res.json()["problems"] == ["Line 2: quantity_tonnes must be a number (got 'ten')"]


def test_plan_limits(make_client):
    client = make_client(free_max_rows=100, free_max_datasets=1, max_upload_mb=1)
    signup(client)
    too_many_rows = upload_case_study(client)
    assert too_many_rows.status_code == 422 and "more than 100 rows" in too_many_rows.json()["error"]

    small = "shipment_id,origin,destination,route_type,quantity_tonnes,distance_km,freight_cost_inr,shipment_date\nS1,A,B,Short,1,1,1,2025-01-01\n"
    assert client.post("/api/datasets", data={"name": "one"}, files={"shipments": ("s.csv", small, "text/csv")}).status_code == 201
    second = client.post("/api/datasets", data={"name": "two"}, files={"shipments": ("s.csv", small, "text/csv")})
    assert second.status_code == 403 and "1 datasets" in second.json()["error"]


def test_upload_size_limit(make_client):
    client = make_client(max_upload_mb=1)
    signup(client)
    huge = b"x" * (1024 * 1024 + 1)
    res = client.post("/api/datasets", data={"name": "big"}, files={"shipments": ("s.csv", huge, "text/csv")})
    assert res.status_code == 413
