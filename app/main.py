"""Application factory and wiring.

create_app() builds the FastAPI app: loads settings (fail-closed on missing
secret), installs the signed session-cookie middleware, wires the DB session
factory onto app.state, and mounts routes.

The session factories can be injected for tests so they run against an isolated
DB without creating the production database file. There are two:
- session_factory: normal (deferred) transactions for reads and non-claim writes.
- claim_session_factory: SQLite BEGIN IMMEDIATE transactions for the claim/join
  path, so the read-then-write claim takes the write lock up front (avoids the
  SQLite upgrade deadlock). Both point at the same database.
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, load_settings
from app.db import init_db, make_engine, make_session_factory
from app.routes import auth, projects, teams


def create_app(
    settings: Settings | None = None,
    session_factory: "sessionmaker[Session] | None" = None,
    claim_session_factory: "sessionmaker[Session] | None" = None,
) -> FastAPI:
    settings = settings or load_settings()

    app = FastAPI(title="学生项目管理系统")

    # ------------------------------------------------------------------ #
    # TWO ENGINES / TWO FACTORIES — READ THIS BEFORE USING EITHER.
    #
    # THE RULE: only the claim/join path (create_team / join_team) uses the
    # IMMEDIATE engine; EVERYTHING ELSE uses the default engine.
    #
    # - session_factory (DEFAULT, deferred): all reads and all NON-claim writes.
    #   Reads run concurrently here. Use via Depends(get_db).
    # - claim_session_factory (IMMEDIATE): ONLY the read-then-write claim/join
    #   transaction, which must take the SQLite write lock up front to avoid the
    #   SHARED->EXCLUSIVE upgrade deadlock. Use via Depends(get_claim_db).
    #
    # Misusing these is a correctness bug, not a style nit:
    # - Routing a normal write through the IMMEDIATE engine serializes it (and
    #   any overlapping read session can deadlock it) for no benefit.
    # - Routing a claim through the DEFAULT engine reintroduces the upgrade
    #   deadlock under concurrent claims.
    # ------------------------------------------------------------------ #
    if session_factory is None:
        engine = make_engine(settings.database_url)
        init_db(engine)
        session_factory = make_session_factory(engine)
    if claim_session_factory is None:
        claim_engine = make_engine(settings.database_url, sqlite_immediate=True)
        claim_session_factory = make_session_factory(claim_engine)
    app.state.session_factory = session_factory
    app.state.claim_session_factory = claim_session_factory
    app.state.settings = settings

    # Signed, httponly (always, in Starlette), samesite=lax cookie. Marked
    # Secure in production via SESSION_COOKIE_SECURE.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie=settings.session_cookie_name,
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )

    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(teams.router)
    return app
