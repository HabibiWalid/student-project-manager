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
from sqlalchemy import select

from app import security
from app.db import init_db, make_engine, make_session_factory
from app.deps import require_teacher
from app.main import create_app
from app.models import (
    ROLE_STUDENT,
    ROLE_TEACHER,
    STATUS_DRAFT,
    Project,
    Team,
    TeamMember,
    User,
)

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
def _db(tmp_path):
    """One temp SQLite file, exposed via two factories (like production):
    a normal deferred factory and the BEGIN IMMEDIATE claim factory."""
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    engine = make_engine(url)
    init_db(engine)
    claim_engine = make_engine(url, sqlite_immediate=True)
    return {
        "session_factory": make_session_factory(engine),
        "claim_session_factory": make_session_factory(claim_engine),
    }


@pytest.fixture
def session_factory(_db):
    return _db["session_factory"]


@pytest.fixture
def claim_session_factory(_db):
    return _db["claim_session_factory"]


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


def user_id(session_factory, email: str) -> int:
    db = session_factory()
    try:
        return db.execute(
            select(User.id).where(User.email == email.strip().lower())
        ).scalar_one()
    finally:
        db.close()


def create_project(
    session_factory,
    *,
    teacher_id: int,
    title: str,
    status: str = STATUS_DRAFT,
    description: str = "",
    max_teams: int | None = None,
    opens_at=None,
    closes_at=None,
) -> int:
    db = session_factory()
    try:
        p = Project(
            teacher_id=teacher_id,
            title=title,
            status=status,
            description=description,
            max_teams=max_teams,
            opens_at=opens_at,
            closes_at=closes_at,
        )
        db.add(p)
        db.commit()
        return p.id
    finally:
        db.close()


def make_student(session_factory, email: str, name: str = "学生") -> int:
    _create_user(
        session_factory,
        email=email,
        password="Student#Pass-123",
        name=name,
        role=ROLE_STUDENT,
    )
    return user_id(session_factory, email)


def create_team(
    session_factory,
    *,
    project_id: int,
    name: str,
    leader_id: int,
    slot_no: int = 0,
    add_leader_member: bool = True,
) -> int:
    db = session_factory()
    try:
        t = Team(
            project_id=project_id, name=name, leader_id=leader_id, slot_no=slot_no
        )
        db.add(t)
        db.flush()
        if add_leader_member:
            db.add(
                TeamMember(team_id=t.id, user_id=leader_id, project_id=project_id)
            )
        db.commit()
        return t.id
    finally:
        db.close()


@pytest.fixture
def app(_db):
    application = create_app(
        session_factory=_db["session_factory"],
        claim_session_factory=_db["claim_session_factory"],
    )

    # Test-only probe route exercising the real teacher gate directly.
    @application.get("/__probe/teacher")
    def _teacher_probe(user: User = Depends(require_teacher)):
        return {"email": user.email}

    return application


@pytest.fixture
def make_client(app):
    """Return a factory for independent clients (separate cookie jars) sharing
    the same app + DB — so one test can drive a teacher and a student at once."""
    return lambda: TestClient(app)


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def login():
    def _login(client, email, password):
        return client.post(
            "/login",
            data={"email": email, "password": password},
            follow_redirects=False,
        )

    return _login


@pytest.fixture
def teacher_client(make_client, users, login):
    c = make_client()
    login(c, TEACHER["email"], TEACHER["password"])
    return c


@pytest.fixture
def student_client(make_client, users, login):
    c = make_client()
    login(c, STUDENT["email"], STUDENT["password"])
    return c
