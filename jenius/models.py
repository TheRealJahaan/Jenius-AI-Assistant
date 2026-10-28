"""The tables. Every row that belongs to a person hangs off `users` and is deleted with them."""
import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow

# none_as_null: Python None is stored as SQL NULL (not JSON null), so "content IS NULL" means "never replay".
JsonType = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # always stored lower-case
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    plan: Mapped[str] = mapped_column(String(20), default="free")  # free | pro
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class Session(Base):
    """A signed-in browser. Only a SHA-256 hash of the cookie is stored, so a leaked database
    doesn't hand out working sign-ins."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions")


class PasswordReset(Base):
    __tablename__ = "password_resets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Dataset(Base):
    """One upload of shipments (and optional context notes). The demo dataset has no owner and
    everyone can read it."""

    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    shipment_count: Mapped[int] = mapped_column(Integer, default=0)
    note_count: Mapped[int] = mapped_column(Integer, default=0)
    anomaly_count: Mapped[int] = mapped_column(Integer, default=0)
    first_date: Mapped[date | None] = mapped_column(Date)
    last_date: Mapped[date | None] = mapped_column(Date)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (Index("ix_shipments_dataset_route_date", "dataset_id", "route", "shipment_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    shipment_code: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(80))
    destination: Mapped[str] = mapped_column(String(80))
    route: Mapped[str] = mapped_column(String(170))
    route_type: Mapped[str] = mapped_column(String(40))
    material: Mapped[str] = mapped_column(String(80), default="")
    quantity_tonnes: Mapped[float] = mapped_column(Float)
    distance_km: Mapped[float] = mapped_column(Float)
    freight_cost_inr: Mapped[float] = mapped_column(Float)
    shipment_date: Mapped[date] = mapped_column(Date)
    transporter: Mapped[str] = mapped_column(String(120), default="")


class ContextNote(Base):
    __tablename__ = "context_notes"
    __table_args__ = (UniqueConstraint("dataset_id", "note_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    note_code: Mapped[str] = mapped_column(String(40))
    note_date: Mapped[date] = mapped_column(Date)
    applies_to: Mapped[str] = mapped_column(String(170))
    text: Mapped[str] = mapped_column(Text)


class Anomaly(Base):
    """A route-week whose cost per tonne-km beat both its own history and its peers."""

    __tablename__ = "anomalies"
    __table_args__ = (UniqueConstraint("dataset_id", "route", "week_of"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    route: Mapped[str] = mapped_column(String(170))
    week_of: Mapped[date] = mapped_column(Date)
    cost_per_tonne_km: Mapped[float] = mapped_column(Float)
    pct_vs_own: Mapped[float] = mapped_column(Float)
    pct_vs_similar: Mapped[float] = mapped_column(Float)
    history_weeks: Mapped[int] = mapped_column(Integer)
    vs_own_history: Mapped[str] = mapped_column(String(200))
    vs_similar_routes: Mapped[str] = mapped_column(String(200))
    needs_review: Mapped[bool] = mapped_column(Boolean)  # True = no note explains it
    matched_note_code: Mapped[str] = mapped_column(String(40), default="")
    reason: Mapped[str] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(20), default="open")  # open | confirmed | dismissed


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    """One entry of a conversation.

    `text` is what people see. `content` is the message exactly as Claude saw it (thinking, tool
    calls and tool results included), so the next turn replays history unchanged. Turns that
    failed keep `content` empty and are shown but never replayed.
    """

    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    mode: Mapped[str] = mapped_column(String(10), default="ai")  # ai = Claude answered (counts toward quota)
    text: Mapped[str | None] = mapped_column(Text)
    content: Mapped[list | str | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class LlmCall(Base):
    """Every model call with its tokens and cost, so the bill is never a surprise."""

    __tablename__ = "llm_calls"
    __table_args__ = (Index("ix_llm_calls_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"))
    model: Mapped[str] = mapped_column(String(60))
    stop_reason: Mapped[str] = mapped_column(String(30), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    request_id: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Payment(Base):
    """A UPI payment someone says they made. It upgrades nothing until an admin checks it arrived."""

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan: Mapped[str] = mapped_column(String(20))
    cycle: Mapped[str] = mapped_column(String(10))  # monthly | annual
    amount_inr: Mapped[int] = mapped_column(Integer)
    upi_reference: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending | approved | rejected
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
