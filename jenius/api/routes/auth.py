"""Sign up, log in, log out, password reset and account deletion."""
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from ...billing import effective_plan, limits_for
from ...config import Settings
from ...db import as_utc, utcnow
from ...mailer import send_password_reset
from ...models import Conversation, Message, PasswordReset, Session, User
from ...security import hash_password, hash_token, needs_rehash, new_token, password_problem, verify_password
from ..deps import (
    client_ip,
    end_session,
    get_db,
    get_settings,
    rate_limit,
    require_user,
    start_session,
)
from ..schemas import DeleteAccountIn, LimitsOut, LoginIn, ResetConfirmIn, ResetRequestIn, SignupIn, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger("jenius.auth")


def start_of_day():
    return utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


def messages_used_today(db: DbSession, user: User) -> int:
    """Claude messages this user sent since midnight UTC (offline answers don't count)."""
    return db.scalar(
        select(func.count(Message.id))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.user_id == user.id, Message.role == "user", Message.mode == "ai",
               Message.text.is_not(None), Message.created_at >= start_of_day())
    ) or 0


def user_out(db: DbSession, user: User, settings: Settings) -> UserOut:
    limits = limits_for(user, settings)
    return UserOut(
        id=user.id, name=user.name, email=user.email, plan=effective_plan(user),
        plan_expires_at=user.plan_expires_at if effective_plan(user) == "pro" else None,
        is_admin=user.is_admin,
        limits=LimitsOut(daily_messages=limits.daily_messages, used_today=messages_used_today(db, user),
                         max_rows=limits.max_rows, max_datasets=limits.max_datasets),
    )


@router.post("/signup", response_model=UserOut, status_code=201)
def signup(body: SignupIn, request: Request, response: Response, db: DbSession = Depends(get_db),
           settings: Settings = Depends(get_settings)):
    rate_limit(request, "signup", client_ip(request), 5, 3600)
    email = body.email.lower()
    if problem := password_problem(body.password, email):
        raise HTTPException(422, problem)
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists. Log in instead.")
    user = User(email=email, name=body.name, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError:  # two sign-ups with one email at the same moment
        db.rollback()
        raise HTTPException(409, "An account with this email already exists. Log in instead.") from None
    start_session(db, response, request, user)
    db.commit()
    log.info("signup user=%s", user.id)
    return user_out(db, user, settings)


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response, db: DbSession = Depends(get_db),
          settings: Settings = Depends(get_settings)):
    email = body.email.lower()
    rate_limit(request, "login-ip", client_ip(request), 20, 600)
    rate_limit(request, "login-email", email, 8, 900)
    user = db.scalar(select(User).where(User.email == email))
    if not verify_password(user.password_hash if user else None, body.password) or user is None:
        raise HTTPException(401, "That email and password don't match.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    start_session(db, response, request, user)
    db.commit()
    return user_out(db, user, settings)


@router.post("/logout")
def logout(request: Request, response: Response, db: DbSession = Depends(get_db)):
    end_session(db, request, response)
    db.commit()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(require_user), db: DbSession = Depends(get_db),
       settings: Settings = Depends(get_settings)):
    return user_out(db, user, settings)


@router.post("/password-reset/request")
def request_reset(body: ResetRequestIn, request: Request, db: DbSession = Depends(get_db),
                  settings: Settings = Depends(get_settings)):
    rate_limit(request, "reset-ip", client_ip(request), 5, 900)
    rate_limit(request, "reset-email", body.email.lower(), 3, 3600)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is not None:
        token = new_token()
        db.add(PasswordReset(user_id=user.id, token_hash=hash_token(token), expires_at=utcnow() + timedelta(hours=1)))
        db.commit()
        # The token goes after '#', so it never reaches server logs or other sites' Referer headers.
        try:
            send_password_reset(settings, user.email, user.name, f"{settings.public_url}/#reset={token}")
        except OSError:
            log.exception("could not send the password-reset email")
    # Same answer either way, so this can't be used to find out who has an account.
    return {"ok": True, "message": "If that email has an account, a reset link is on its way."}


@router.post("/password-reset/confirm", response_model=UserOut)
def confirm_reset(body: ResetConfirmIn, request: Request, response: Response, db: DbSession = Depends(get_db),
                  settings: Settings = Depends(get_settings)):
    rate_limit(request, "reset-confirm", client_ip(request), 10, 900)
    reset = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == hash_token(body.token)))
    if reset is None or reset.used_at is not None or as_utc(reset.expires_at) <= utcnow():
        raise HTTPException(400, "This reset link has expired or was already used. Ask for a new one.")
    user = db.get(User, reset.user_id)
    if problem := password_problem(body.password, user.email):
        raise HTTPException(422, problem)
    user.password_hash = hash_password(body.password)
    reset.used_at = utcnow()
    db.execute(delete(Session).where(Session.user_id == user.id))  # sign out everywhere else
    start_session(db, response, request, user)
    db.commit()
    log.info("password reset user=%s", user.id)
    return user_out(db, user, settings)


@router.delete("/account")
def delete_account(body: DeleteAccountIn, request: Request, response: Response,
                   user: User = Depends(require_user), db: DbSession = Depends(get_db)):
    rate_limit(request, "delete-account", str(user.id), 5, 900)
    if not verify_password(user.password_hash, body.password):
        raise HTTPException(401, "That password isn't right.")
    end_session(db, request, response)
    db.delete(user)  # datasets, conversations, sessions and payments go with it (ON DELETE CASCADE)
    db.commit()
    log.info("account deleted user=%s", user.id)
    return {"ok": True}
