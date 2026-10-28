"""Paying for Pro by UPI. Someone pays the QR code and submits the transaction (UTR) number; an
admin checks it arrived in the bank app and approves it. Nothing upgrades on the browser's word."""
import io
import logging
from typing import Literal

import segno
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from ...billing import effective_plan, price_inr, upi_link
from ...config import Settings
from ...models import Payment, User
from ..deps import get_db, get_settings, rate_limit, require_user
from ..schemas import PaymentOut, UpiClaimIn

router = APIRouter(prefix="/api/billing", tags=["billing"])
log = logging.getLogger("jenius.billing")

MAX_PENDING = 3


@router.get("")
def billing(user: User = Depends(require_user), db: DbSession = Depends(get_db),
            settings: Settings = Depends(get_settings)):
    claims = db.scalars(select(Payment).where(Payment.user_id == user.id).order_by(Payment.created_at.desc()).limit(20))
    return {
        "plan": effective_plan(user),
        "plan_expires_at": user.plan_expires_at if effective_plan(user) == "pro" else None,
        "upi_id": settings.upi_id,
        "prices_inr": {"monthly": settings.pro_monthly_inr, "annual": settings.pro_annual_inr},
        "payments": [PaymentOut.model_validate(p) for p in claims],
    }


@router.post("/upi-claims", response_model=PaymentOut, status_code=201)
def submit_upi_claim(body: UpiClaimIn, request: Request, user: User = Depends(require_user),
                     db: DbSession = Depends(get_db), settings: Settings = Depends(get_settings)):
    rate_limit(request, "upi-claim", str(user.id), 5, 3600)
    pending = db.scalar(select(func.count(Payment.id)).where(Payment.user_id == user.id,
                                                             Payment.status == "pending")) or 0
    if pending >= MAX_PENDING:
        raise HTTPException(409, "You already have payments waiting to be checked. We'll confirm them soon.")
    payment = Payment(user_id=user.id, plan="pro", cycle=body.cycle, amount_inr=price_inr(body.cycle, settings),
                      upi_reference=body.upi_reference.upper())
    db.add(payment)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "That UPI reference has already been submitted.") from None
    log.info("upi claim user=%s payment=%s amount=%d", user.id, payment.id, payment.amount_inr)
    return payment


@router.get("/upi-qr.svg")
def upi_qr(cycle: Literal["monthly", "annual"] = "monthly", settings: Settings = Depends(get_settings)):
    """The QR code people scan to pay, made from UPI_ID with the plan's price filled in."""
    if not settings.upi_id:
        raise HTTPException(404, "UPI payments aren't set up yet.")
    out = io.BytesIO()
    segno.make(upi_link(cycle, settings), error="m").save(out, kind="svg", scale=8, border=2, xmldecl=False)
    return Response(out.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=300"})
