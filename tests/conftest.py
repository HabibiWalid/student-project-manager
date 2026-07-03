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
    Score,
    Submission,
    SubmissionFile,
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
        "upload_dir": tmp_path / "uploads",
    }


@pytest.fixture
def session_factory(_db):
    return _db["session_factory"]


@pytest.fixture
def upload_dir(_db):
    return _db["upload_dir"]


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
    slot_no: int | None = None,
    add_leader_member: bool = True,
) -> int:
    db = session_factory()
    try:
        if slot_no is None:
            # Auto-assign the next slot (like the real service) so multiple teams
            # can be seeded on one project without manual bookkeeping.
            from sqlalchemy import func

            slot_no = db.execute(
                select(func.coalesce(func.max(Team.slot_no), -1) + 1).where(
                    Team.project_id == project_id
                )
            ).scalar_one()
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


def write_submission_file(
    session_factory,
    upload_dir,
    *,
    team_id: int,
    project_id: int,
    original_name: str,
    content: bytes,
    note: str = "",
) -> int:
    """Seed a Submission + one SubmissionFile with a real on-disk file. Returns
    the SubmissionFile id. Used for download / header-injection tests."""
    from uuid import uuid4

    stored_name = uuid4().hex + ".bin"
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / stored_name).write_bytes(content)
    db = session_factory()
    try:
        sub = Submission(
            team_id=team_id, project_id=project_id, note=note, status="submitted"
        )
        db.add(sub)
        db.flush()
        sf = SubmissionFile(
            submission_id=sub.id,
            stored_name=stored_name,
            original_name=original_name,
            size_bytes=len(content),
            mime="application/octet-stream",
            sha256="0" * 64,
        )
        db.add(sf)
        db.flush()
        file_id = sf.id
        db.commit()
        return file_id
    finally:
        db.close()


def award_score(
    session_factory,
    *,
    team_id: int,
    project_id: int,
    points: int,
    awarded_by: int,
    reason: str = "奖励",
) -> int:
    db = session_factory()
    try:
        s = Score(
            team_id=team_id,
            project_id=project_id,
            points=points,
            awarded_by=awarded_by,
            reason=reason,
        )
        db.add(s)
        db.commit()
        return s.id
    finally:
        db.close()


@pytest.fixture
def app(_db):
    application = create_app(
        session_factory=_db["session_factory"],
        claim_session_factory=_db["claim_session_factory"],
        upload_dir=str(_db["upload_dir"]),
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
