"""Shared test fixtures.

Each test gets an isolated SQLite database (a temp file) and a TestClient whose
app is wired to that DB via the create_app() injection seam, so tests never
touch the production database file.
"""

from __future__ import annotations

import os

# Set a valid test secret BEFORE anything imports app.main / load_settings.
os.environ.setdefault("SESSION_SECRET", "test-secret-value-that-is-long-enough-1234")

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app import security
from app.db import init_db, make_engine, make_session_factory
from app.deps import require_teacher
from app.main import create_app
from app.models import ROLE_STUDENT, ROLE_TEACHER, User

TEACHER = {
    "email": "teacher@example.com",
    "password": "Teacher#Pass-123",
    "name": "王老师",
    "role": ROLE_TEACHER,
}
STUDENT = {
    "email": "student@example.com",
    "password": "Student#Pass-123",
    "name": "李同学",
    "role": ROLE_STUDENT,
}


@pytest.fixture
def session_factory(tmp_path):
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    engine = make_engine(url)
    init_db(engine)
    return make_session_factory(engine)


def _create_user(factory, *, email, password, name, role):
    db = factory()
    try:
        db.add(
            User(
                email=email.strip().lower(),
                password_hash=security.hash_password(password),
                name=name,
                role=role,
            )
        )
        db.commit()
    finally:
        db.close()


@pytest.fixture
def users(session_factory):
    """Seed a teacher and a student into the test DB."""
    _create_user(session_factory, **TEACHER)
    _create_user(session_factory, **STUDENT)
    return {"teacher": TEACHER, "student": STUDENT}


@pytest.fixture
def client(session_factory):
    app = create_app(session_factory=session_factory)

    # Test-only probe route exercising the real teacher gate. The first *real*
    # teacher route arrives in Phase 2 and is guarded by this same dependency.
    @app.get("/__probe/teacher")
    def _teacher_probe(user: User = Depends(require_teacher)):
        return {"email": user.email}

    return TestClient(app)


@pytest.fixture
def login():
    def _login(client, email, password):
        return client.post(
            "/login",
            data={"email": email, "password": password},
            follow_redirects=False,
        )

    return _login
