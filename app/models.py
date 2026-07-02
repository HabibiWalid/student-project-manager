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
    UniqueConstraint,
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


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        # A team name is unique within a project.
        UniqueConstraint("project_id", "name", name="uq_teams_project_name"),
        # THE claim-race backstop: two concurrent claimers compute the same
        # slot_no and exactly one insert can win. Independent of lock/isolation.
        UniqueConstraint("project_id", "slot_no", name="uq_teams_project_slot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    leader_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # slot_no is a per-project, monotonic (MAX+1), NEVER-REUSED claim token — not
    # a dense 0..max-1 index. Capacity is enforced separately by COUNT<max_teams,
    # so slot_no only needs to be collision-free. NOTE: this scheme assumes teams
    # are never hard-deleted in a way that must recycle a slot value; MAX+1 stays
    # collision-free under deletes (it is always greater than every surviving
    # row), but if a delete/re-add feature is ever added, revisit this comment.
    slot_no: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (
        # A user may belong to at most ONE team per project. Blocks joining a
        # second team on the same project (the leader's auto-join hits this too).
        UniqueConstraint(
            "project_id", "user_id", name="uq_team_members_project_user"
        ),
    )

    # PK(team_id, user_id): a user cannot join the SAME team twice.
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), primary_key=True
    )
    # Denormalized from the team so the one-team-per-project UNIQUE can be a DB
    # constraint rather than application logic.
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
