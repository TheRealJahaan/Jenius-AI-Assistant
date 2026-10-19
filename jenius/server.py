"""Zero-dependency HTTP server: a small JSON API plus the static dashboard.

Endpoints
---------
GET  /api/health                        liveness probe
GET  /api/summary                       dataset metadata and anomaly counts
GET  /api/routes                        per-route statistics
GET  /api/anomalies?own=15&peer=15      anomalous weeks at the given thresholds
GET  /api/route-weeks?route=Delhi-Jaipur  weekly series for one route
POST /api/ask      {"question": "..."}  grounded answer (Markdown)

Everything else is served from ``web/``.
"""

from __future__ import annotations

import json
import logging
import os
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import __version__, config
from .assistant import Assistant
from .pipeline import default_knowledge_base

log = logging.getLogger("jenius.server")

MAX_BODY_BYTES = 16 * 1024
MAX_QUESTION_CHARS = 500


def _float_param(params: dict[str, list[str]], name: str, default: float) -> float:
    raw = params.get(name, [None])[0]
    if raw is None:
        return default
    value = float(raw)  # ValueError -> 400 in the caller
    if not 0 <= value <= 1000:
        raise ValueError(f"{name} must be between 0 and 1000")
    return value


class Handler(SimpleHTTPRequestHandler):
    server_version = f"Jenius/{__version__}"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(config.WEB_DIR), **kwargs)

    # ── helpers ──────────────────────────────────────────────────────────────
    @property
    def kb(self) -> dict[str, Any]:
        return default_knowledge_base()

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if self.path.startswith("/api/"):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        log.info("%s %s", self.address_string(), format % args)

    # ── routes ───────────────────────────────────────────────────────────────
    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:
        url = urlparse(self.path)
        params = parse_qs(url.query)
        kb = self.kb

        if url.path == "/api/health":
            return self.send_json({"status": "ok", "version": __version__})
        if url.path == "/api/summary":
            anomalies = [rw for rw in kb["route_weeks"] if rw["anomalous"]]
            justified = sum(1 for rw in anomalies if rw["matched_note_id"])
            return self.send_json(
                {
                    **kb["meta"],
                    "n_anomalies": len(anomalies),
                    "n_justified": justified,
                    "n_needs_review": len(anomalies) - justified,
                }
            )
        if url.path == "/api/routes":
            return self.send_json({"routes": kb["routes"]})
        if url.path == "/api/anomalies":
            meta = kb["meta"]
            try:
                own = _float_param(params, "own", meta["own_history_threshold_pct"])
                peer = _float_param(params, "peer", meta["peer_threshold_pct"])
            except ValueError as exc:
                return self.error_json(HTTPStatus.BAD_REQUEST, str(exc))
            rows = [
                rw
                for rw in kb["route_weeks"]
                if rw["pct_vs_own"] is not None
                and rw["pct_vs_peers"] is not None
                and rw["pct_vs_own"] >= own
                and rw["pct_vs_peers"] >= peer
            ]
            return self.send_json({"own_threshold": own, "peer_threshold": peer, "anomalies": rows})
        if url.path == "/api/route-weeks":
            route = params.get("route", [""])[0]
            if route not in {r["route"] for r in kb["routes"]}:
                return self.error_json(HTTPStatus.NOT_FOUND, f"Unknown route: {route!r}")
            rows = [rw for rw in kb["route_weeks"] if rw["route"] == route]
            return self.send_json({"route": route, "weeks": rows})
        if url.path.startswith("/api/"):
            return self.error_json(HTTPStatus.NOT_FOUND, "Endpoint not found")
        return super().do_GET()

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if url.path != "/api/ask":
            return self.error_json(HTTPStatus.NOT_FOUND, "Endpoint not found")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.error_json(HTTPStatus.BAD_REQUEST, "Invalid Content-Length")
        if length > MAX_BODY_BYTES:
            return self.error_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self.error_json(HTTPStatus.BAD_REQUEST, "Body must be JSON")
        question = data.get("question") if isinstance(data, dict) else None
        if not isinstance(question, str) or not question.strip():
            return self.error_json(HTTPStatus.BAD_REQUEST, "'question' must be a non-empty string")
        if len(question) > MAX_QUESTION_CHARS:
            return self.error_json(
                HTTPStatus.BAD_REQUEST, f"'question' must be at most {MAX_QUESTION_CHARS} characters"
            )
        return self.send_json({"question": question, "answer": _assistant().answer(question)})


_ASSISTANT: Assistant | None = None


def _assistant() -> Assistant:
    global _ASSISTANT
    if _ASSISTANT is None:
        _ASSISTANT = Assistant(default_knowledge_base())
    return _ASSISTANT


def make_server(host: str = "0.0.0.0", port: int = 8000) -> ThreadingHTTPServer:
    default_knowledge_base()  # fail fast on bad input data
    return ThreadingHTTPServer((host, port), Handler)


def serve(host: str | None = None, port: int | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    host = host or os.environ.get("HOST", "0.0.0.0")
    port = port or int(os.environ.get("PORT", "8000"))
    httpd = make_server(host, port)
    log.info("Jenius %s listening on http://%s:%d", __version__, host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
