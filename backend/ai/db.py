from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True)
class DbConfig:
    url: str


def _normalize_url(url: str) -> str:
    """Normalize DB URL for SQLAlchemy.

    - Railway may provide `postgres://...` which SQLAlchemy treats as legacy.
    - We use psycopg3 (`psycopg[binary]`), so prefer `postgresql+psycopg://...`.
    """
    url = str(url or "").strip()
    if not url:
        return ""

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]

    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]

    return url


def _build_postgres_url(
    *,
    host: str,
    port: str | int | None,
    user: str,
    password: str,
    database: str,
) -> str:
    host = str(host or "").strip()
    user = str(user or "").strip()
    password = str(password or "")
    database = str(database or "").strip()
    port_s = str(port).strip() if port is not None else ""
    if not (host and user and password and database):
        return ""
    auth = f"{quote_plus(user)}:{quote_plus(password)}"
    hp = f"{host}:{port_s}" if port_s else host
    return _normalize_url(f"postgresql://{auth}@{hp}/{quote_plus(database)}")


def get_db_config() -> DbConfig | None:
    """Return DB config from env, or None if not configured.

    Supported inputs:
    - DATABASE_URL as a full URL (recommended): postgresql://... or sqlite:///...
    - DATABASE_URL as `host:port` + standard Postgres env vars (Railway often sets these):
      PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE
    - PG* env vars only (construct URL)
    """
    raw = str(os.getenv("DATABASE_URL") or "").strip()
    if raw:
        # Full URL (has scheme)
        if "://" in raw:
            url = _normalize_url(raw)
            return DbConfig(url=url) if url else None

        # Allow host:port (Railway-host style) if PG* vars are present.
        if ":" in raw:
            host, port = raw.split(":", 1)
            user = os.getenv("PGUSER") or os.getenv("POSTGRES_USER") or ""
            password = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD") or ""
            database = os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB") or ""
            url = _build_postgres_url(host=host, port=port, user=user, password=password, database=database)
            return DbConfig(url=url) if url else None

    # Fallback: build from PG* vars only.
    host = os.getenv("PGHOST") or ""
    port = os.getenv("PGPORT") or ""
    user = os.getenv("PGUSER") or os.getenv("POSTGRES_USER") or ""
    password = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD") or ""
    database = os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB") or ""
    if host and user and password and database:
        url = _build_postgres_url(host=host, port=port, user=user, password=password, database=database)
        return DbConfig(url=url) if url else None

    return None


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def db_enabled() -> bool:
    return get_db_config() is not None


def get_engine() -> Engine | None:
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine

    cfg = get_db_config()
    if cfg is None:
        return None

    connect_args: dict[str, object] = {}
    if cfg.url.startswith("sqlite"):
        # FastAPI runs in multi-threaded mode locally; SQLite needs this.
        connect_args["check_same_thread"] = False
    elif cfg.url.startswith("postgresql"):
        # Avoid blocking app startup forever if the DB is temporarily unreachable.
        try:
            connect_args["connect_timeout"] = max(1, int(os.getenv("DB_CONNECT_TIMEOUT_S") or 5))
        except Exception:
            connect_args["connect_timeout"] = 5

    _engine = create_engine(cfg.url, pool_pre_ping=True, connect_args=connect_args)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> sessionmaker[Session] | None:
    get_engine()
    return _SessionLocal


def ensure_schema() -> bool:
    """Best-effort: ensure required tables exist.

    We still use Alembic for migrations, but for fresh DBs this keeps the app
    usable immediately (e.g. first boot / local dev) by creating missing tables.
    """
    engine = get_engine()
    if engine is None:
        return False
    try:
        from .models import Base

        Base.metadata.create_all(engine)
        return True
    except Exception:
        return False
