"""Database engine, session factory, and schema creation.

Written against the SQLAlchemy 2.x ORM so a later swap from SQLite to Postgres
needs no query rewrites: only the connection URL and the SQLite-specific
connect args change.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def make_engine(database_url: str, *, echo: bool = False) -> Engine:
    # check_same_thread is a SQLite-only quirk: FastAPI may touch a connection
    # from a different thread than the one that created it. Harmless on other
    # backends because we only add it for sqlite URLs.
    connect_args = (
        {"check_same_thread": False}
        if database_url.startswith("sqlite")
        else {}
    )
    return create_engine(database_url, echo=echo, connect_args=connect_args, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, class_=Session
    )


def init_db(engine: Engine) -> None:
    """Create all tables. MVP uses create_all; Alembic migrations come later."""
    # Import models for their side effect of registering with Base.metadata.
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)
