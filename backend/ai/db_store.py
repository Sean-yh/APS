from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .db import db_enabled as _db_enabled
from .db import get_sessionmaker
from .models import ChatMessage, ChatSession, Document


def db_enabled() -> bool:
    return _db_enabled()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    sm = get_sessionmaker()
    if sm is None:
        raise RuntimeError("DATABASE_URL is not configured (DB disabled)")
    db = sm()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# -----------------------------------------------------------------------------
# Documents (JSON blobs)
# -----------------------------------------------------------------------------


def get_document(db: Session, key: str) -> Document | None:
    return db.get(Document, str(key))


def get_document_payload(key: str) -> tuple[Any, datetime] | None:
    """Return (payload, updated_at) from DB, or None if missing/DB disabled."""
    if not db_enabled():
        return None
    try:
        with session_scope() as db:
            doc = get_document(db, key)
            if doc is None:
                return None
            updated = doc.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            return doc.payload, updated
    except SQLAlchemyError:
        return None


def upsert_document_payload(key: str, payload: Any) -> bool:
    """Upsert document. Returns True if written; False if DB disabled/error."""
    if not db_enabled():
        return False
    k = str(key or "").strip()
    if not k:
        return False
    try:
        with session_scope() as db:
            doc = db.get(Document, k)
            if doc is None:
                db.add(Document(key=k, payload=payload))
            else:
                doc.payload = payload
        return True
    except SQLAlchemyError:
        return False


# -----------------------------------------------------------------------------
# Chat (sessions + messages)
# -----------------------------------------------------------------------------


def ensure_chat_session(session_id: str) -> bool:
    if not db_enabled():
        return False
    sid = str(session_id or "").strip()
    if not sid:
        return False
    try:
        with session_scope() as db:
            s = db.get(ChatSession, sid)
            if s is None:
                db.add(ChatSession(id=sid))
            else:
                # Touch updated_at via an explicit write (some DBs only update on change)
                s.updated_at = datetime.now(timezone.utc)
        return True
    except SQLAlchemyError:
        return False


def add_chat_message(session_id: str, *, role: str, content: str) -> bool:
    if not db_enabled():
        return False
    sid = str(session_id or "").strip()
    if not sid:
        return False
    role_s = str(role or "").strip().lower()
    if role_s not in ("user", "assistant", "system"):
        role_s = "user"
    try:
        with session_scope() as db:
            s = db.get(ChatSession, sid)
            if s is None:
                s = ChatSession(id=sid)
                db.add(s)
                db.flush()
            db.add(ChatMessage(session_id=sid, role=role_s, content=str(content or "")))
            s.updated_at = datetime.now(timezone.utc)
        return True
    except SQLAlchemyError:
        return False


def list_chat_messages(session_id: str, *, limit: int = 100) -> list[dict[str, str]]:
    """Return messages in chronological order."""
    if not db_enabled():
        return []
    sid = str(session_id or "").strip()
    if not sid:
        return []
    try:
        lim = max(1, min(int(limit), 500))
    except Exception:
        lim = 100

    try:
        with session_scope() as db:
            rows = db.execute(
                select(ChatMessage.role, ChatMessage.content)
                .where(ChatMessage.session_id == sid)
                .order_by(ChatMessage.id.asc())
                .limit(lim)
            ).all()
            return [{"role": str(r[0]), "content": str(r[1])} for r in rows]
    except SQLAlchemyError:
        return []

