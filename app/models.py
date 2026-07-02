"""ORM models. Phase 1 defines only User; later phases add their own tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

ROLE_TEACHER = "teacher"
ROLE_STUDENT = "student"
ROLES = (ROLE_TEACHER, ROLE_STUDENT)

STATUS_DRAFT = "draft"
STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
PROJECT_STATUSES = (STATUS_DRAFT, STATUS_OPEN, STATUS_CLOSED)

# Allowed one-way status transitions. Anything not listed is rejected.
ALLOWED_STATUS_TRANSITIONS = {
    STATUS_DRAFT: {STATUS_OPEN},
    STATUS_OPEN: {STATUS_CLOSED},
    STATUS_CLOSED: set(),
}


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('teacher', 'student')", name="ck_users_role_valid"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Email is the login identity; UNIQUE enforces one account per address at
    # the DB level (not just in application code).
    email: Mapped[str] = mapped_column(
        String(320), unique=True, index=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'open', 'closed')",
            name="ck_projects_status_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_DRAFT
    )
    max_teams: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opens_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closes_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
