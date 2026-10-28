"""Plans, their limits, and what someone's plan is right now."""
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

from .config import Settings
from .db import as_utc, utcnow
from .models import User

CYCLE_DAYS = {"monthly": 31, "annual": 366}


@dataclass(frozen=True)
class Limits:
    plan: str
    daily_messages: int
    max_rows: int
    max_datasets: int


def effective_plan(user: User | None) -> str:
    """"pro" while a paid plan is active, otherwise "free". An expired Pro plan is free again."""
    if user is None:
        return "free"
    if user.plan == "pro" and (user.plan_expires_at is None or as_utc(user.plan_expires_at) > utcnow()):
        return "pro"
    return "free"


def limits_for(user: User | None, settings: Settings) -> Limits:
    if effective_plan(user) == "pro":
        return Limits("pro", settings.pro_daily_messages, settings.pro_max_rows, settings.pro_max_datasets)
    return Limits("free", settings.free_daily_messages, settings.free_max_rows, settings.free_max_datasets)


def price_inr(cycle: str, settings: Settings) -> int:
    """The price is always decided here, never taken from the browser."""
    return settings.pro_annual_inr if cycle == "annual" else settings.pro_monthly_inr


def extend_pro(user: User, cycle: str) -> None:
    """Add a paid cycle to the user's Pro plan (on top of any time they still have)."""
    now = utcnow()
    current = as_utc(user.plan_expires_at) if user.plan == "pro" and user.plan_expires_at else now
    user.plan = "pro"
    user.plan_expires_at = max(current, now) + timedelta(days=CYCLE_DAYS[cycle])


def upi_link(cycle: str, settings: Settings) -> str:
    """A upi:// payment link (the format every Indian UPI app reads) with the amount filled in."""
    return "upi://pay?" + urlencode({"pa": settings.upi_id, "pn": "Jenius", "am": f"{price_inr(cycle, settings)}.00",
                                     "cu": "INR", "tn": f"Jenius Pro {cycle}"})
