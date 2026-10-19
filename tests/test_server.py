import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from jenius.server import make_server


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    httpd = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def call(url: str, body: Any = None) -> tuple[int, Any]:
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            ctype = resp.headers["Content-Type"]
            return resp.status, json.loads(raw) if "json" in ctype else raw.decode()
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_health(base_url: str) -> None:
    assert call(f"{base_url}/api/health") == (200, {"status": "ok", "version": "3.0.0"})


def test_summary(base_url: str) -> None:
    status, body = call(f"{base_url}/api/summary")
    assert status == 200
    assert (body["n_anomalies"], body["n_justified"], body["n_needs_review"]) == (6, 4, 2)


def test_anomalies_respect_thresholds(base_url: str) -> None:
    _, default = call(f"{base_url}/api/anomalies")
    _, loose = call(f"{base_url}/api/anomalies?own=10&peer=10")
    assert len(default["anomalies"]) == 6
    assert len(loose["anomalies"]) > 6
    assert call(f"{base_url}/api/anomalies?own=abc")[0] == 400
    assert call(f"{base_url}/api/anomalies?own=nan")[0] == 400


def test_route_weeks(base_url: str) -> None:
    status, body = call(f"{base_url}/api/route-weeks?route=Delhi-Jaipur")
    assert status == 200
    assert all(w["route"] == "Delhi-Jaipur" for w in body["weeks"])
    assert call(f"{base_url}/api/route-weeks?route=Nowhere")[0] == 404


def test_ask(base_url: str) -> None:
    status, body = call(f"{base_url}/api/ask", {"question": "summary"})
    assert status == 200
    assert "Anomalous route-weeks: **6**" in body["answer"]


@pytest.mark.parametrize(
    "payload",
    [b"not json", {"question": ""}, {"question": 42}, {"question": "x" * 501}, ["question"]],
)
def test_ask_rejects_bad_input(base_url: str, payload: Any) -> None:
    status, body = call(f"{base_url}/api/ask", payload)
    assert status == 400
    assert "error" in body


def test_unknown_api_route_is_json_404(base_url: str) -> None:
    assert call(f"{base_url}/api/nope") == (404, {"error": "Endpoint not found"})


def test_serves_dashboard(base_url: str) -> None:
    status, html = call(f"{base_url}/")
    assert status == 200
    assert "<title>" in html
