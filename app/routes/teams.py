"""Team routes: claim (create team) and join. Student-only, 简体中文 UI.

Maps domain outcomes to HTTP: ClaimError(not_found)->404, other ClaimError->409,
DB lock timeout (OperationalError)->503. No unhandled 500 escapes the claim path.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import teams as teams_service
from app.deps import (
    current_user,
    get_claim_db,
    get_db,
    require_student,
    require_student_for_write,
)
from app.models import STATUS_OPEN, Project, Team, TeamMember, User
from app.teams import REASON_NOT_FOUND, ClaimError
from app.templating import templates

router = APIRouter()

MAX_TEAM_NAME_LEN = 100
BUSY_MESSAGE = "系统繁忙，请稍后再试"


def _render_team_form(request, db, project_id, *, error, status_code=200):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "team_new.html",
        {"project": project, "error": error},
        status_code=status_code,
    )


@router.get("/projects/{project_id}/teams/new")
def new_team_form(
    project_id: int,
    request: Request,
    user: User = Depends(require_student),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    # Students may only form teams on an open project; hide anything else.
    if project is None or project.status != STATUS_OPEN:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request, "team_new.html", {"project": project, "error": None}
    )


@router.post("/projects/{project_id}/teams")
def claim_project(
    project_id: int,
    request: Request,
    user: User = Depends(require_student_for_write),
    db: Session = Depends(get_claim_db),
    name: str = Form(...),
):
    name = name.strip()
    if not name or len(name) > MAX_TEAM_NAME_LEN:
        return _render_team_form(
            request,
            db,
            project_id,
            error="队伍名称无效",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        team = teams_service.create_team(
            db, project_id=project_id, name=name, leader=user
        )
    except ClaimError as e:
        if e.reason == REASON_NOT_FOUND:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return _render_team_form(
            request, db, project_id, error=e.message,
            status_code=status.HTTP_409_CONFLICT,
        )
    except OperationalError:
        # Waited out busy_timeout and still could not get the write lock.
        return _render_team_form(
            request, db, project_id, error=BUSY_MESSAGE,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return RedirectResponse(
        f"/teams/{team.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/teams/{team_id}/join")
def join_team_route(
    team_id: int,
    request: Request,
    user: User = Depends(require_student_for_write),
    db: Session = Depends(get_claim_db),
):
    try:
        teams_service.join_team(db, team_id=team_id, user=user)
    except ClaimError as e:
        code = (
            status.HTTP_404_NOT_FOUND
            if e.reason == REASON_NOT_FOUND
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=e.message)
    except OperationalError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=BUSY_MESSAGE
        )

    return RedirectResponse(
        f"/teams/{team_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/teams/{team_id}")
def team_detail(
    team_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # Any authenticated user may view a team roster (name + members). The
    # stricter members-or-teacher gate applies to submissions/files in Phase 4.
    project = db.get(Project, team.project_id)
    members = (
        db.execute(
            select(User)
            .join(TeamMember, TeamMember.user_id == User.id)
            .where(TeamMember.team_id == team_id)
            .order_by(TeamMember.joined_at)
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "team_detail.html",
        {"team": team, "project": project, "members": members},
    )
