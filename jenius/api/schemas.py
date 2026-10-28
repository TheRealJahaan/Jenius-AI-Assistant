"""Request and response shapes. Every text field has a length limit."""
import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class SignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Please enter your name.")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ResetRequestIn(BaseModel):
    email: EmailStr


class ResetConfirmIn(BaseModel):
    token: str = Field(min_length=20, max_length=100)
    password: str = Field(min_length=1, max_length=128)


class DeleteAccountIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class LimitsOut(BaseModel):
    daily_messages: int
    used_today: int
    max_rows: int
    max_datasets: int


class UserOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    plan: str
    plan_expires_at: dt.datetime | None
    is_admin: bool
    limits: LimitsOut


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    is_demo: bool
    is_mine: bool = False
    shipment_count: int
    note_count: int
    anomaly_count: int
    first_date: dt.date | None
    last_date: dt.date | None
    analyzed_at: dt.datetime | None
    created_at: dt.datetime


class RouteStat(BaseModel):
    route: str
    route_type: str
    shipments: int
    freight_cost_inr: float
    avg_cost_per_tonne_km: float


class DatasetDetail(DatasetOut):
    total_freight_cost_inr: float
    needs_review: int
    justified: int
    routes: list[RouteStat]


class AnomalyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    route: str
    week_of: dt.date
    cost_per_tonne_km: float
    pct_vs_own: float
    pct_vs_similar: float
    history_weeks: int
    vs_own_history: str
    vs_similar_routes: str
    needs_review: bool
    matched_note_code: str
    reason: str
    review_status: str


class ReviewIn(BaseModel):
    review_status: Literal["open", "confirmed", "dismissed"]


class ChatIn(BaseModel):
    dataset_id: uuid.UUID
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None


class ChatOut(BaseModel):
    reply: str
    mode: Literal["ai", "offline"]
    conversation_id: uuid.UUID | None
    tools_used: list[str] = []
    notice: str | None = None
    remaining_today: int | None = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    title: str
    created_at: dt.datetime


class MessageOut(BaseModel):
    role: str
    text: str
    mode: str
    created_at: dt.datetime


class UpiClaimIn(BaseModel):
    cycle: Literal["monthly", "annual"]
    upi_reference: str = Field(min_length=8, max_length=35, pattern=r"^[A-Za-z0-9]+$",
                               description="The UPI transaction / UTR number shown in your payment app.")


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan: str
    cycle: str
    amount_inr: int
    upi_reference: str
    status: str
    note: str
    created_at: dt.datetime
    reviewed_at: dt.datetime | None


class AdminPaymentOut(PaymentOut):
    user_email: str
    user_name: str


class ReviewPaymentIn(BaseModel):
    note: str = Field("", max_length=300)
