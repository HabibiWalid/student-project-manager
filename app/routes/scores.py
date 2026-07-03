"""Scoring & leaderboard.

Awards are an append-only positive ledger (points 1..1000, reason required).
Mistakes are removed with an owner-only "void" that deletes a specific Score row
(not counter-balanced with negatives). The leaderboard is a DERIVED ranked
SUM(points) query — never a stored counter. All writes use the DEFAULT engine.

Authorization for award and void is identical: teacher who OWNS the project
(student->403, anon->401, non-owner teacher->403).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import current_user, get_db, require_teacher
from app.models import ROLE_TEACHER, Project, Score, Team, User
from app.routes.projects import _can_view
from app.templating import templates

router = APIRouter()

MIN_POINTS = 1
MAX_POINTS = 1000
MAX_REASON_LEN = 500


def _owned_project_or_error(db: Session, project_id: int, user: User) -> Project:
    """Load the project and require the current teacher owns it."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if project.teacher_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return project


def _team_in_project_or_404(db: Session, team_id: int, project_id: int) -> Team:
    team = db.get(Team, team_id)
    if team is None or team.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return team


def _team_scores(db: Session, team_id: int):
    return (
        db.execute(
            select(Score).where(Score.team_id == team_id).order_by(Score.created_at.desc())
        )
        .scalars()
        .all()
    )


def _render_award_form(request, db, project, team, *, error=None, status_code=200):
    return templates.TemplateResponse(
        request,
        "score_new.html",
        {
            "project": project,
            "team": team,
            "scores": _team_scores(db, team.id),
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/projects/{project_id}/teams/{team_id}/score/new")
def award_form(
    project_id: int,
    team_id: int,
    request: Request,
    user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    project = _owned_project_or_error(db, project_id, user)
    team = _team_in_project_or_404(db, team_id, project_id)
    return _render_award_form(request, db, project, team)


@router.post("/projects/{project_id}/teams/{team_id}/scores")
def award_points(
    project_id: int,
    team_id: int,
    request: Request,
    user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
    points: int = Form(...),
    reason: str = Form(""),
):
    project = _owned_project_or_error(db, project_id, user)
    team = _team_in_project_or_404(db, team_id, project_id)

    reason = reason.strip()
    if not (MIN_POINTS <= points <= MAX_POINTS):
        return _render_award_form(
            request, db, project, team,
            error="分值必须在 1 到 1000 之间",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not reason or len(reason) > MAX_REASON_LEN:
        return _render_award_form(
            request, db, project, team,
            error="请填写评分理由（不超过 500 字）",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    db.add(
        Score(
            team_id=team_id,
            project_id=project_id,
            points=points,
            awarded_by=user.id,
            reason=reason,
        )
    )
    db.commit()
    return RedirectResponse(
        f"/projects/{project_id}/leaderboard", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/projects/{project_id}/scores/{score_id}/void")
def void_score(
    project_id: int,
    score_id: int,
    request: Request,
    user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    _owned_project_or_error(db, project_id, user)
    score = db.get(Score, score_id)
    # Missing, or belongs to a different project than the path -> 404.
    if score is None or score.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    team_id = score.team_id
    db.delete(score)
    db.commit()
    return RedirectResponse(
        f"/projects/{project_id}/teams/{team_id}/score/new",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/projects/{project_id}/leaderboard")
def leaderboard(
    project_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    # Same visibility as the project itself; drafts are not disclosed to students.
    if project is None or not _can_view(user, project):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    total = func.coalesce(func.sum(Score.points), 0).label("total")
    rows = db.execute(
        select(Team.id, Team.name, total)
        .select_from(Team)
        .outerjoin(Score, Score.team_id == Team.id)
        .where(Team.project_id == project_id)
        .group_by(Team.id, Team.name)
        # Deterministic: rank by total desc, break ties by ascending team id.
        .order_by(total.desc(), Team.id.asc())
    ).all()

    is_owner = user.role == ROLE_TEACHER and project.teacher_id == user.id
    return templates.TemplateResponse(
        request,
        "leaderboard.html",
        {"project": project, "rows": rows, "is_owner": is_owner},
    )
