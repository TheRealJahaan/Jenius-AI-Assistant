"""The assistant. Signed-in people get Claude (with a daily quota); everyone else, and anyone over the
quota or when Claude is unreachable, gets the free offline engine."""
import logging
import threading
import uuid

import anthropic
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from ...assistant import offline
from ...assistant.agent import CallLog
from ...assistant.tools import ToolBox
from ...billing import limits_for
from ...config import Settings
from ...models import Anomaly, ContextNote, Conversation, Dataset, LlmCall, Message, Shipment, User
from ..deps import client_ip, get_db, get_settings, get_visible_dataset, optional_user, rate_limit, require_user
from ..schemas import ChatIn, ChatOut, ConversationOut, MessageOut
from .auth import messages_used_today

router = APIRouter(prefix="/api", tags=["assistant"])
log = logging.getLogger("jenius.chat")

MAX_TURNS_PER_CONVERSATION = 20


class ConversationLocks:
    """One answer at a time per conversation, so messages stay in order."""

    def __init__(self):
        self._locks: dict[uuid.UUID, threading.Lock] = {}
        self._guard = threading.Lock()

    def get(self, conversation_id: uuid.UUID) -> threading.Lock:
        with self._guard:
            if len(self._locks) > 10_000:
                self._locks = {k: v for k, v in self._locks.items() if v.locked()}
            return self._locks.setdefault(conversation_id, threading.Lock())


def offline_reply(db: DbSession, dataset: Dataset, question: str) -> str:
    anomalies = db.scalars(select(Anomaly).where(Anomaly.dataset_id == dataset.id)
                           .order_by(Anomaly.route, Anomaly.week_of)).all()
    notes = db.scalars(select(ContextNote).where(ContextNote.dataset_id == dataset.id)).all()
    routes = db.scalars(select(Shipment.route).where(Shipment.dataset_id == dataset.id).distinct()).all()
    return offline.answer(question, anomalies, notes, routes, toolbox=ToolBox(db, dataset.id))


def dataset_brief(dataset: Dataset) -> str:
    return (f"The person is asking about the dataset \"{dataset.name}\": {dataset.shipment_count} shipments from "
            f"{dataset.first_date} to {dataset.last_date}, {dataset.note_count} context notes, "
            f"{dataset.anomaly_count} anomalous route-weeks.")


def _next_seq(db: DbSession, conversation_id: uuid.UUID) -> int:
    return (db.scalar(select(func.max(Message.seq)).where(Message.conversation_id == conversation_id)) or 0) + 1


@router.post("/chat", response_model=ChatOut)
def chat(body: ChatIn, request: Request, user: User | None = Depends(optional_user),
         db: DbSession = Depends(get_db), settings: Settings = Depends(get_settings)):
    rate_limit(request, "chat", str(user.id) if user else client_ip(request), 20, 60)
    dataset = get_visible_dataset(db, body.dataset_id, user)
    question = body.message.strip()
    if not question:
        raise HTTPException(422, "Please type a question.")

    if user is None:  # visitors: free answers, nothing stored
        return ChatOut(reply=offline_reply(db, dataset, question), mode="offline", conversation_id=None,
                       notice="Log in to ask Claude follow-up questions in plain English." if settings.llm_enabled
                       else None)

    conversation = _conversation(db, body.conversation_id, user, dataset)
    limits = limits_for(user, settings)
    used = messages_used_today(db, user)
    agent = request.app.state.agent

    if agent is None or used >= limits.daily_messages:
        notice = None
        if agent is not None:
            notice = (f"You've used today's {limits.daily_messages} Claude messages, so this is a quick answer from "
                      f"the built-in engine. Claude is back at midnight UTC"
                      + (", or upgrade to Pro for more." if limits.plan == "free" else "."))
        reply = offline_reply(db, dataset, question)
        _store_display_turn(db, conversation, question, reply, mode="offline")
        return ChatOut(reply=reply, mode="offline", conversation_id=conversation.id, notice=notice,
                       remaining_today=max(0, limits.daily_messages - used) if agent else None)

    lock = request.app.state.conversation_locks.get(conversation.id)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "Still answering your last message in this chat. One moment.")
    try:
        return _ai_turn(db, request, user, dataset, conversation, question, limits.daily_messages - used, agent)
    finally:
        lock.release()


def _conversation(db: DbSession, conversation_id: uuid.UUID | None, user: User, dataset: Dataset) -> Conversation:
    if conversation_id is not None:
        conversation = db.scalar(select(Conversation).where(Conversation.id == conversation_id,
                                                            Conversation.user_id == user.id))
        if conversation is None or conversation.dataset_id != dataset.id:
            raise HTTPException(404, "Conversation not found.")
        turns = db.scalar(select(func.count(Message.id)).where(
            Message.conversation_id == conversation.id, Message.role == "user", Message.text.is_not(None))) or 0
        if turns >= MAX_TURNS_PER_CONVERSATION:
            raise HTTPException(409, "This chat is getting long. Start a new chat to keep answers fast.")
        return conversation
    conversation = Conversation(user_id=user.id, dataset_id=dataset.id)
    db.add(conversation)
    db.commit()
    return conversation


def _store_display_turn(db: DbSession, conversation: Conversation, question: str, reply: str, mode: str) -> None:
    """A turn that is shown in the chat but never replayed to Claude."""
    if not conversation.title:
        conversation.title = question[:80]
    seq = _next_seq(db, conversation.id)
    db.add_all([
        Message(conversation_id=conversation.id, seq=seq, role="user", mode=mode, text=question, content=None),
        Message(conversation_id=conversation.id, seq=seq + 1, role="assistant", mode=mode, text=reply, content=None),
    ])
    db.commit()


def _ai_turn(db, request, user, dataset, conversation, question, remaining, agent) -> ChatOut:
    history = [{"role": m.role, "content": m.content} for m in db.scalars(
        select(Message).where(Message.conversation_id == conversation.id, Message.content.is_not(None))
        .order_by(Message.seq))]
    db.commit()  # don't hold a database transaction open while Claude thinks

    calls: list[CallLog] = []
    notice = None
    try:
        result = agent.run_turn(history=history, user_text=question, toolbox=ToolBox(db, dataset.id),
                                dataset_brief=dataset_brief(dataset), on_call=calls.append)
    except (anthropic.APIConnectionError, anthropic.APIStatusError) as err:
        log.warning("Claude unavailable (%s); answering offline. request_id=%s", type(err).__name__,
                    getattr(err, "request_id", ""))
        result = None
        notice = "Claude is unavailable right now, so this is a quick answer from the built-in engine."
    finally:
        db.rollback()  # end any read transaction the tools opened
        db.add_all(LlmCall(user_id=user.id, conversation_id=conversation.id, **vars(c)) for c in calls)
        db.commit()

    if result is None:
        reply = offline_reply(db, dataset, question)
        spent = bool(calls)  # an attempt that already cost tokens counts toward the quota
        _store_display_turn(db, conversation, question, reply, mode="ai" if spent else "offline")
        return ChatOut(reply=reply, mode="offline", conversation_id=conversation.id, notice=notice,
                       remaining_today=max(0, remaining - (1 if spent else 0)))

    if not conversation.title:
        conversation.title = question[:80]
    seq = _next_seq(db, conversation.id)
    if result.ok:
        rows = []
        for i, msg in enumerate(result.messages):
            first, last = i == 0, i == len(result.messages) - 1
            rows.append(Message(conversation_id=conversation.id, seq=seq + i, role=msg["role"], mode="ai",
                                text=question if first else (result.reply if last else None), content=msg["content"]))
    else:
        rows = [Message(conversation_id=conversation.id, seq=seq, role="user", mode="ai", text=question),
                Message(conversation_id=conversation.id, seq=seq + 1, role="assistant", mode="ai", text=result.reply)]
    db.add_all(rows)
    try:
        db.commit()
    except IntegrityError:  # another server process answered in this chat at the same moment
        db.rollback()
        raise HTTPException(409, "This chat changed while I was answering. Please send your message again.") from None
    return ChatOut(reply=result.reply, mode="ai", conversation_id=conversation.id, tools_used=result.tools_used,
                   remaining_today=max(0, remaining - 1))


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(dataset_id: uuid.UUID | None = None, user: User = Depends(require_user),
                       db: DbSession = Depends(get_db)):
    q = select(Conversation).where(Conversation.user_id == user.id)
    if dataset_id is not None:
        q = q.where(Conversation.dataset_id == dataset_id)
    return db.scalars(q.order_by(Conversation.created_at.desc()).limit(50)).all()


@router.get("/conversations/{conversation_id}", response_model=list[MessageOut])
def get_conversation(conversation_id: uuid.UUID, user: User = Depends(require_user), db: DbSession = Depends(get_db)):
    conversation = db.scalar(select(Conversation).where(Conversation.id == conversation_id,
                                                        Conversation.user_id == user.id))
    if conversation is None:
        raise HTTPException(404, "Conversation not found.")
    rows = db.scalars(select(Message).where(Message.conversation_id == conversation.id, Message.text.is_not(None))
                      .order_by(Message.seq))
    return [MessageOut(role=m.role, text=m.text, mode=m.mode, created_at=m.created_at) for m in rows]


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: uuid.UUID, user: User = Depends(require_user),
                        db: DbSession = Depends(get_db)):
    conversation = db.scalar(select(Conversation).where(Conversation.id == conversation_id,
                                                        Conversation.user_id == user.id))
    if conversation is None:
        raise HTTPException(404, "Conversation not found.")
    db.delete(conversation)
    db.commit()
    return {"ok": True}
