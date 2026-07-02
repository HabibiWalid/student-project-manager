"""Team domain services: claiming a project (create team) and joining a team.

These are plain functions (no HTTP) so they can be driven directly by concurrent
threads in tests. Each runs a single DB transaction on the passed Session and
either commits and returns, or rolls back and raises ClaimError (a domain
rejection the route maps to 4xx). A DB lock timeout surfaces as SQLAlchemy
OperationalError, which the route maps to 503 — it is NOT swallowed here.

Concurrency correctness does NOT depend on lock/isolation level: slot_no is
MAX(slot_no)+1 per project and UNIQUE(project_id, slot_no) guarantees that two
concurrent claimers computing the same slot_no cannot both insert — exactly one
wins. A bounded retry re-computes slot_no when a benign collision happens while
capacity still exists. Holds identically on SQLite and Postgres.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import STATUS_OPEN, Project, Team, TeamMember, User

# Rejection reason codes -> the route maps not_found to 404, everything else 409.
REASON_NOT_FOUND = "not_found"
REASON_NOT_OPEN = "not_open"
REASON_FULL = "full"
REASON_ALREADY_IN_TEAM = "already_in_team"
REASON_CONFLICT = "conflict"

# Bounded retries for a benign slot_no collision (capacity still available).
_MAX_CLAIM_ATTEMPTS = 5


class ClaimError(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message  # user-safe 简体中文


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _assert_claimable(project: Project) -> None:
    """status==open AND within [opens_at, closes_at] at this moment."""
    if project.status != STATUS_OPEN:
        raise ClaimError(REASON_NOT_OPEN, "项目未开放")
    now = datetime.now(timezone.utc)
    if project.opens_at is not None and now < _as_utc(project.opens_at):
        raise ClaimError(REASON_NOT_OPEN, "项目尚未开放")
    if project.closes_at is not None and now > _as_utc(project.closes_at):
        raise ClaimError(REASON_NOT_OPEN, "项目已截止")


def _name_taken(db: Session, project_id: int, name: str) -> bool:
    return (
        db.execute(
            select(func.count())
            .select_from(Team)
            .where(Team.project_id == project_id, Team.name == name)
        ).scalar_one()
        > 0
    )


def create_team(db: Session, *, project_id: int, name: str, leader: User) -> Team:
    """Claim a project by creating a team; leader auto-joins. One transaction.

    Correctness: slot_no = MAX+1 and UNIQUE(project_id, slot_no) mean two
    concurrent claimers computing the same slot_no cannot both insert. A bounded
    retry handles the benign case where a concurrent claim took our slot_no while
    capacity still remains. OperationalError (lock timeout) is NOT caught here.
    """
    for _ in range(_MAX_CLAIM_ATTEMPTS):
        try:
            # with_for_update: row lock on Postgres; no-op on SQLite (the claim
            # engine's BEGIN IMMEDIATE provides serialization there).
            project = db.execute(
                select(Project).where(Project.id == project_id).with_for_update()
            ).scalar_one_or_none()
            if project is None:
                raise ClaimError(REASON_NOT_FOUND, "项目不存在")
            _assert_claimable(project)

            count = db.execute(
                select(func.count())
                .select_from(Team)
                .where(Team.project_id == project_id)
            ).scalar_one()
            if project.max_teams is not None and count >= project.max_teams:
                raise ClaimError(REASON_FULL, "项目名额已满")

            already = db.execute(
                select(func.count())
                .select_from(TeamMember)
                .where(
                    TeamMember.project_id == project_id,
                    TeamMember.user_id == leader.id,
                )
            ).scalar_one()
            if already:
                raise ClaimError(REASON_ALREADY_IN_TEAM, "你已加入该项目的一个队伍")

            next_slot = db.execute(
                select(func.coalesce(func.max(Team.slot_no), -1) + 1).where(
                    Team.project_id == project_id
                )
            ).scalar_one()

            team = Team(
                project_id=project_id,
                name=name,
                leader_id=leader.id,
                slot_no=next_slot,
            )
            db.add(team)
            db.flush()
            db.add(
                TeamMember(
                    team_id=team.id, user_id=leader.id, project_id=project_id
                )
            )
            db.flush()
            db.commit()
            return team
        except ClaimError:
            db.rollback()
            raise
        except IntegrityError:
            db.rollback()
            # A duplicate team name is a real, non-retryable conflict.
            if _name_taken(db, project_id, name):
                raise ClaimError(REASON_CONFLICT, "队伍名称已被占用")
            # Otherwise a concurrent claimer took our slot_no (or joined as this
            # user). Retry: capacity is re-checked and slot_no recomputed.
            continue

    raise ClaimError(REASON_CONFLICT, "创建队伍失败，请稍后重试")


def join_team(db: Session, *, team_id: int, user: User) -> TeamMember:
    """Join an existing team. Constraints enforce the invariants atomically:
    PK(team_id, user_id) blocks re-joining the same team; UNIQUE(project_id,
    user_id) blocks joining a second team on the same project."""
    try:
        team = db.execute(
            select(Team).where(Team.id == team_id).with_for_update()
        ).scalar_one_or_none()
        if team is None:
            raise ClaimError(REASON_NOT_FOUND, "队伍不存在")
        project = db.get(Project, team.project_id)
        _assert_claimable(project)

        member = TeamMember(
            team_id=team_id, user_id=user.id, project_id=team.project_id
        )
        db.add(member)
        db.flush()
        db.commit()
        return member
    except ClaimError:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise ClaimError(REASON_CONFLICT, "你已加入该队伍或该项目的其他队伍")
