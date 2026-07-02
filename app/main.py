"""Application factory and wiring.

create_app() builds the FastAPI app: loads settings (fail-closed on missing
secret), installs the signed session-cookie middleware, wires the DB session
factory onto app.state, and mounts routes.

A session_factory can be injected for tests so they run against an isolated DB
without creating the production database file.
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, load_settings
from app.db import init_db, make_engine, make_session_factory
from app.routes import auth, projects


def create_app(
    settings: Settings | None = None,
    session_factory: "sessionmaker[Session] | None" = None,
) -> FastAPI:
    settings = settings or load_settings()

    app = FastAPI(title="学生项目管理系统")

    if session_factory is None:
        engine = make_engine(settings.database_url)
        init_db(engine)
        session_factory = make_session_factory(engine)
    app.state.session_factory = session_factory
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
    return app
