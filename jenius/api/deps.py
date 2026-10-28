"""What every route needs: the database, the signed-in user, rate limits."""
import uuid
from collections.abc import Iterator
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session as DbSession

from ..config import Settings
from ..db import as_utc, utcnow
from ..models import Dataset, Session, User
from ..security import hash_token, new_token

COOKIE_NAME = "jenius_session"


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[DbSession]:
    yield from request.app.state.db.session()


def client_ip(request: Request) -> str:
    # Behind a proxy, uvicorn's --proxy-headers puts the real address here (see FORWARDED_ALLOW_IPS).
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request, bucket: str, key: str, limit: int, window_seconds: float) -> None:
    wait = request.app.state.limiter.hit(f"{bucket}:{key}", limit, window_seconds)
    if wait is not None:
        raise HTTPException(429, "Too many requests. Please wait a moment and try again.",
                            headers={"Retry-After": str(int(wait) + 1)})


# ── sessions ────────────────────────────────────────────────────────────────

def start_session(db: DbSession, response: Response, request: Request, user: User) -> None:
    settings: Settings = request.app.state.settings
    token = new_token()
    db.add(Session(user_id=user.id, token_hash=hash_token(token),
                   user_agent=(request.headers.get("user-agent") or "")[:300],
                   expires_at=utcnow() + timedelta(days=settings.session_days)))
    response.set_cookie(COOKIE_NAME, token, max_age=settings.session_days * 86400, httponly=True,
                        secure=settings.secure_cookies, samesite="lax", path="/")


def end_session(db: DbSession, request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        session = db.scalar(select(Session).where(Session.token_hash == hash_token(token)))
        if session:
            db.delete(session)
    response.delete_cookie(COOKIE_NAME, path="/", secure=request.app.state.settings.secure_cookies,
                           httponly=True, samesite="lax")


def optional_user(request: Request, db: DbSession = Depends(get_db)) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token or len(token) > 100:
        return None
    session = db.scalar(select(Session).where(Session.token_hash == hash_token(token)))
    if session is None:
        return None
    now = utcnow()
    if as_utc(session.expires_at) <= now:
        db.delete(session)
        db.commit()
        return None
    if now - as_utc(session.last_used_at) > timedelta(minutes=10):  # don't write on every request
        session.last_used_at = now
        db.commit()
    return session.user


def require_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(401, "Please log in first.")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Only admins can do that.")
    return user


# ── datasets ────────────────────────────────────────────────────────────────

def visible_datasets_filter(user: User | None):
    """The demo dataset is public; every other dataset is visible to its owner only."""
    if user is None:
        return Dataset.is_demo.is_(True)
    return or_(Dataset.is_demo.is_(True), Dataset.owner_id == user.id)


def get_visible_dataset(db: DbSession, dataset_id: uuid.UUID, user: User | None) -> Dataset:
    dataset = db.scalar(select(Dataset).where(Dataset.id == dataset_id, visible_datasets_filter(user)))
    if dataset is None:  # 404 either way, so nobody learns which ids exist
        raise HTTPException(404, "Dataset not found.")
    return dataset


def get_owned_dataset(db: DbSession, dataset_id: uuid.UUID, user: User) -> Dataset:
    dataset = get_visible_dataset(db, dataset_id, user)
    if dataset.owner_id != user.id:
        raise HTTPException(403, "The demo dataset is read-only. Upload your own data to change it.")
    return dataset
