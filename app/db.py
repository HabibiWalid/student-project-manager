"""Database engine, session factory, and schema creation.

Written against the SQLAlchemy 2.x ORM so a later swap from SQLite to Postgres
needs no query rewrites: only the connection URL and the SQLite-specific
connect args change.
"""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# How long a SQLite writer waits for the write lock before raising
# OperationalError ("database is locked"). Callers must map that to a clean 503.
SQLITE_BUSY_TIMEOUT_MS = 5000


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _configure_sqlite(engine: Engine, *, immediate: bool) -> None:
    """SQLite setup.

    Always:
    - busy_timeout: a writer waiting on another's short-lived lock WAITS up to
      this long; only if it still cannot acquire the lock does it raise
      OperationalError (callers map that to a clean 503).
    - foreign_keys=ON: enforce FK integrity (off by default in SQLite).

    When immediate=True (the claim engine only):
    - isolation_level=None hands transaction control to us, and every
      transaction opens with BEGIN IMMEDIATE so the write lock is taken up front.
      This avoids the SQLite SHARED->RESERVED upgrade deadlock in a
      read-then-write claim. We do NOT apply this engine-wide: forcing every
      read to take the write lock would serialize all DB access and deadlock
      overlapping sessions.
    """

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):  # pragma: no cover - trivial setup
        if immediate:
            dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        cur.execute("PRAGMA foreign_keys = ON")
        cur.close()

    if immediate:

        @event.listens_for(engine, "begin")
        def _on_begin(conn):  # pragma: no cover - trivial setup
            conn.exec_driver_sql("BEGIN IMMEDIATE")


def make_engine(
    database_url: str, *, echo: bool = False, sqlite_immediate: bool = False
) -> Engine:
    # check_same_thread is a SQLite-only quirk: FastAPI may touch a connection
    # from a different thread than the one that created it. Harmless on other
    # backends because we only add it for sqlite URLs.
    is_sqlite = database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(
        database_url, echo=echo, connect_args=connect_args, future=True
    )
    if is_sqlite:
        _configure_sqlite(engine, immediate=sqlite_immediate)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, class_=Session
    )


def init_db(engine: Engine) -> None:
    """Create all tables. MVP uses create_all; Alembic migrations come later."""
    # Import models for their side effect of registering with Base.metadata.
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)
