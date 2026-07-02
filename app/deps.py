"""Request-scoped dependencies: DB session, current user, role gates.

Identity and role are derived server-side from the signed session cookie's
user_id by loading the User row from the DB. We never trust a role, user id, or
identity supplied in a form field, header, or client-writable cookie value.
"""

from __future__ import annotations

from typing import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.models import ROLE_STUDENT, ROLE_TEACHER, User


def get_db(request: Request) -> Iterator[Session]:
    """DEFAULT (deferred) session — use this for everything EXCEPT the claim/join
    transaction. See the engine rule in app/main.create_app."""
    factory = request.app.state.session_factory
    db = factory()
    try:
        yield db
    finally:
        db.close()


def get_claim_db(request: Request) -> Iterator[Session]:
    """IMMEDIATE session — use ONLY for the claim/join path (create_team /
    join_team). See the engine rule in app/main.create_app.

    Used for BOTH the auth read and the write of a claim request, so the whole
    request runs on ONE connection/transaction — otherwise the request's own open
    read would hold a lock that blocks its own commit."""
    factory = request.app.state.claim_session_factory
    db = factory()
    try:
        yield db
    finally:
        db.close()


def _load_current_user(request: Request, db: Session) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    user = db.get(User, user_id)
    if user is None:
        # Session points at a user that no longer exists: drop it, fail closed.
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Return the authenticated User or raise 401. Loads role from the DB."""
    return _load_current_user(request, db)


def require_teacher(user: User = Depends(current_user)) -> User:
    """Allow only teachers; raise 403 for authenticated non-teachers."""
    if user.role != ROLE_TEACHER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def require_student(user: User = Depends(current_user)) -> User:
    """Allow only students; raise 403 for authenticated non-students."""
    if user.role != ROLE_STUDENT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def require_student_for_write(
    request: Request, db: Session = Depends(get_claim_db)
) -> User:
    """Student gate for claim/join, authenticating on the SAME immediate session
    the write will use (get_claim_db is cached within the request, so the route's
    own Depends(get_claim_db) yields this very session)."""
    user = _load_current_user(request, db)
    if user.role != ROLE_STUDENT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user
