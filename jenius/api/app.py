"""The web server: the JSON API under /api and the web app at /.

create_app() takes its settings, database and AI agent as arguments so tests can pass fakes.
"""
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError, ProgrammingError

from .. import __version__
from ..config import Settings, get_settings
from ..db import Database
from ..demo import ensure_demo
from .ratelimit import RateLimiter
from .routes import admin, auth, billing, chat, datasets, health

WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
log = logging.getLogger("jenius")

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "object-src 'none'",
])


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname,
                                 "logger": record.name, "message": record.getMessage()}
        for key in ("request_id", "method", "path", "status", "ms"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging() -> None:
    logger = logging.getLogger("jenius")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False


def _error(status: int, message: str, request: Request, problems: list[str] | None = None,
           headers: dict | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": message, "request_id": getattr(request.state, "request_id", "")}
    if problems:
        body["problems"] = problems
    return JSONResponse(body, status_code=status, headers=headers)


def create_app(settings: Settings | None = None, database: Database | None = None, agent: Any = "auto",
               seed_demo: bool = True) -> FastAPI:
    settings = settings or get_settings()
    database = database or Database(settings.database_url)
    if agent == "auto":
        agent = None
        if settings.llm_enabled:
            from ..assistant.agent import ClaudeAgent

            agent = ClaudeAgent(settings)
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if seed_demo:
            try:
                with database.sessions() as db:
                    ensure_demo(db)
            except (OperationalError, ProgrammingError):
                log.error("The database has no tables yet. Run `python -m jenius init-db` (or `alembic upgrade head`).")
        log.info("Jenius %s ready: database=%s assistant=%s", __version__, database.engine.dialect.name,
                 settings.anthropic_model if agent else "offline engine")
        yield
        database.engine.dispose()

    app = FastAPI(title="Jenius", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs" if settings.jenius_env != "production" else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if settings.jenius_env != "production" else None)
    app.state.settings = settings
    app.state.db = database
    app.state.agent = agent
    app.state.limiter = RateLimiter()
    app.state.conversation_locks = chat.ConversationLocks()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request.state.request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        start = time.monotonic()

        # Cross-site request forgery check: browsers say where a request came from; only our own pages
        # may change things. (Session cookies are also SameSite=Lax.)
        if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin not in settings.origins):
                return _error(403, "Requests from other websites aren't allowed.", request)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        response.headers["Content-Security-Policy"] = CSP
        if settings.public_url.startswith("https://"):
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if request.url.path not in ("/healthz", "/readyz"):
            log.info("request", extra={"request_id": request.state.request_id, "method": request.method,
                                       "path": request.url.path, "status": response.status_code,
                                       "ms": int((time.monotonic() - start) * 1000)})
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return _error(exc.status_code, exc.detail.get("message", "Request failed."), request,
                          exc.detail.get("problems"), exc.headers)
        return _error(exc.status_code, str(exc.detail), request, headers=exc.headers)

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return _error(404, getattr(exc, "detail", None) or "Not found.", request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        problems = []
        for e in exc.errors():
            field = ".".join(str(p) for p in e["loc"] if p not in ("body", "query", "path"))
            if field == "email":
                problems.append("Please enter a valid email address.")
            else:
                msg = e["msg"].removeprefix("Value error, ")
                problems.append(f"{field.replace('_', ' ')}: {msg}" if field else msg)
        return _error(422, problems[0] if problems else "Invalid request.", request, problems)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"request_id": getattr(request.state, "request_id", "")})
        return _error(500, "Something went wrong on our side. Please try again.", request)

    for module in (health, auth, datasets, chat, billing, admin):
        app.include_router(module.router)

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app
