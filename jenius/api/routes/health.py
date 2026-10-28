"""Probes for the hosting platform, and the public settings the web page needs."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ... import __version__
from ...billing import upi_link
from ...config import Settings
from ..deps import get_settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz():
    """The process is up (liveness)."""
    return {"ok": True}


@router.get("/readyz")
def readyz(request: Request):
    """The process can reach its database (readiness)."""
    try:
        with request.app.state.db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any failure means not ready
        return JSONResponse({"ok": False, "database": "unreachable"}, status_code=503)
    return {"ok": True, "database": request.app.state.db.engine.dialect.name}


@router.get("/api/config")
def public_config(settings: Settings = Depends(get_settings)):
    return {
        "version": __version__,
        "llm_enabled": settings.llm_enabled,
        "model": settings.anthropic_model if settings.llm_enabled else None,
        "upi_id": settings.upi_id,
        "support_email": settings.support_email,
        "upi_links": {c: upi_link(c, settings) for c in ("monthly", "annual")} if settings.upi_id else None,
        "prices_inr": {"monthly": settings.pro_monthly_inr, "annual": settings.pro_annual_inr},
        "plans": {
            "free": {"daily_messages": settings.free_daily_messages, "max_rows": settings.free_max_rows,
                     "max_datasets": settings.free_max_datasets},
            "pro": {"daily_messages": settings.pro_daily_messages, "max_rows": settings.pro_max_rows,
                    "max_datasets": settings.pro_max_datasets},
        },
        "max_upload_mb": settings.max_upload_mb,
    }
