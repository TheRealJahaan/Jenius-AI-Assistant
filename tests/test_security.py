from helpers import demo_id, signup

UPLOAD = ("shipment_id,origin,destination,route_type,quantity_tonnes,distance_km,freight_cost_inr,shipment_date\n"
          "S1,Mumbai,Pune,Short,10,150,6000,2025-01-06\n")


def upload(client, name="Mine"):
    res = client.post("/api/datasets", data={"name": name}, files={"shipments": ("s.csv", UPLOAD, "text/csv")})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_security_headers(client):
    for path in ("/", "/api/config"):
        headers = client.get(path).headers
        assert "default-src 'self'" in headers["content-security-policy"]
        assert "script-src 'self'" in headers["content-security-policy"]
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["x-request-id"]


def test_cross_site_requests_are_refused(client):
    evil = client.post("/api/auth/login", json={"email": "a@b.co", "password": "x"}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    fetch_meta = client.post("/api/auth/logout", headers={"Sec-Fetch-Site": "cross-site"})
    assert fetch_meta.status_code == 403
    ours = client.post("/api/auth/logout", headers={"Origin": "http://testserver"})
    assert ours.status_code == 200


def test_people_cannot_see_each_others_data(make_client):
    alice, bob = make_client(), make_client()
    signup(alice, "alice@example.com", "Alice")
    signup(bob, "bob@example.com", "Bob")
    mine = upload(alice)
    assert mine not in [d["id"] for d in bob.get("/api/datasets").json()]
    assert bob.get(f"/api/datasets/{mine}").status_code == 404
    assert bob.get(f"/api/datasets/{mine}/anomalies.csv").status_code == 404
    assert bob.delete(f"/api/datasets/{mine}").status_code == 404
    assert bob.post("/api/chat", json={"dataset_id": mine, "message": "summary"}).status_code == 404


def test_demo_is_public_and_read_only(client):
    demo = demo_id(client)
    assert client.get(f"/api/datasets/{demo}").json()["anomaly_count"] == 6
    anomaly = client.get(f"/api/datasets/{demo}/anomalies").json()[0]["id"]
    assert client.patch(f"/api/datasets/{demo}/anomalies/{anomaly}", json={"review_status": "dismissed"}).status_code == 401
    signup(client)
    assert client.patch(f"/api/datasets/{demo}/anomalies/{anomaly}", json={"review_status": "dismissed"}).status_code == 403
    assert client.delete(f"/api/datasets/{demo}").status_code == 403


def test_guests_cannot_upload(client):
    res = client.post("/api/datasets", data={"name": "x"}, files={"shipments": ("s.csv", UPLOAD, "text/csv")})
    assert res.status_code == 401


def test_errors_never_leak_internals(client):
    res = client.get("/api/datasets/not-a-uuid")
    assert res.status_code == 422 and "Traceback" not in res.text
    assert client.get("/api/nothing-here").status_code == 404
