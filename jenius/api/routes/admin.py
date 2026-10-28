"""Admin-only: checking UPI payments and watching what Claude costs."""
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from ...billing import extend_pro
from ...db import utcnow
from ...models import LlmCall, Payment, User
from ..deps import get_db, require_admin
from ..schemas import AdminPaymentOut, PaymentOut, ReviewPaymentIn

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/payments", response_model=list[AdminPaymentOut])
def list_payments(status: str = "pending", _: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    q = select(Payment, User).join(User, User.id == Payment.user_id)
    if status != "all":
        q = q.where(Payment.status == status)
    rows = db.execute(q.order_by(Payment.created_at.desc()).limit(200)).all()
    return [AdminPaymentOut(**PaymentOut.model_validate(p).model_dump(), user_email=u.email, user_name=u.name)
            for p, u in rows]


def review_payment(db: DbSession, payment_id: uuid.UUID, admin: User, approve: bool, note: str) -> Payment:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(404, "Payment not found.")
    if payment.status != "pending":
        raise HTTPException(409, f"This payment was already {payment.status}.")
    payment.status = "approved" if approve else "rejected"
    payment.note = note
    payment.reviewed_at = utcnow()
    payment.reviewed_by = admin.id
    if approve:
        extend_pro(db.get(User, payment.user_id), payment.cycle)
    db.commit()
    return payment


@router.post("/payments/{payment_id}/approve", response_model=PaymentOut)
def approve(payment_id: uuid.UUID, body: ReviewPaymentIn, admin: User = Depends(require_admin),
            db: DbSession = Depends(get_db)):
    return review_payment(db, payment_id, admin, True, body.note)


@router.post("/payments/{payment_id}/reject", response_model=PaymentOut)
def reject(payment_id: uuid.UUID, body: ReviewPaymentIn, admin: User = Depends(require_admin),
           db: DbSession = Depends(get_db)):
    return review_payment(db, payment_id, admin, False, body.note)


@router.get("/usage")
def usage(days: int = 30, _: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    since = utcnow() - timedelta(days=max(1, min(days, 365)))
    day = func.date(LlmCall.created_at)
    rows = db.execute(
        select(day, func.count(), func.sum(LlmCall.input_tokens), func.sum(LlmCall.output_tokens),
               func.sum(LlmCall.cost_usd))
        .where(LlmCall.created_at >= since).group_by(day).order_by(day.desc())
    ).all()
    return {
        "users": db.scalar(select(func.count(User.id))),
        "days": [{"date": str(d), "calls": n, "input_tokens": i or 0, "output_tokens": o or 0,
                  "cost_usd": round(float(c or 0), 4)} for d, n, i, o, c in rows],
        "total_cost_usd": round(sum(float(r[4] or 0) for r in rows), 4),
    }
