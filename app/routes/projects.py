"""Project routes: entry redirect, listing, detail, creation, status changes.

Authorization is enforced server-side on every route:
- Listing/detail visibility depends on the DB-loaded role and project status;
  students never see non-open projects (draft existence is not disclosed).
- Creation and status transitions require the teacher role, and transitions
  additionally require ownership of the target project (least privilege).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import current_user, get_db, require_teacher
from app.models import (
    ALLOWED_STATUS_TRANSITIONS,
    ROLE_TEACHER,
    STATUS_CLOSED,
    STATUS_DRAFT,
    STATUS_OPEN,
    Project,
    User,
)
from app.schemas import ProjectCreate
from app.templating import templates

router = APIRouter()

INVALID_PROJECT_MESSAGE = "项目信息有误，请检查后重试"


def _is_owner(user: User, project: Project) -> bool:
    return user.role == ROLE_TEACHER and project.teacher_id == user.id


def _can_view(user: User, project: Project) -> bool:
    if user.role == ROLE_TEACHER:
        return project.teacher_id == user.id or project.status == STATUS_OPEN
    return project.status == STATUS_OPEN


@router.get("/")
def home(request: Request):
    # Friendly entry point: bounce to the right place based on session state.
    if request.session.get("user_id"):
        return RedirectResponse("/projects", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/projects")
def list_projects(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Project)
    if user.role == ROLE_TEACHER:
        # Own projects (any status) plus everyone's open projects.
        stmt = stmt.where(
            (Project.teacher_id == user.id) | (Project.status == STATUS_OPEN)
        )
    else:
        stmt = stmt.where(Project.status == STATUS_OPEN)
    projects = db.execute(stmt.order_by(Project.created_at.desc())).scalars().all()
    return templates.TemplateResponse(
        request, "projects_list.html", {"projects": projects, "user": user}
    )


@router.post("/projects")
def create_project(
    request: Request,
    user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
    title: str = Form(...),
    description: str = Form(""),
    max_teams: str = Form(""),
    opens_at: str = Form(""),
    closes_at: str = Form(""),
):
    try:
        data = ProjectCreate(
            title=title,
            description=description,
            max_teams=max_teams or None,
            opens_at=opens_at or None,
            closes_at=closes_at or None,
        )
    except ValidationError:
        return templates.TemplateResponse(
            request,
            "project_new.html",
            {"error": INVALID_PROJECT_MESSAGE, "user": user},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    project = Project(
        teacher_id=user.id,
        title=data.title,
        description=data.description,
        status=STATUS_DRAFT,
        max_teams=data.max_teams,
        opens_at=data.opens_at,
        closes_at=data.closes_at,
    )
    db.add(project)
    db.commit()
    return RedirectResponse(
        f"/projects/{project.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/projects/new")
def new_project_form(request: Request, user: User = Depends(require_teacher)):
    # Declared before /projects/{project_id} so "new" is not parsed as an id.
    return templates.TemplateResponse(
        request, "project_new.html", {"error": None, "user": user}
    )


@router.get("/projects/{project_id}")
def project_detail(
    project_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    # Same 404 whether the project is absent or the viewer may not see it, so a
    # student cannot probe for the existence of drafts.
    if project is None or not _can_view(user, project):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "project_detail.html",
        {"project": project, "user": user, "is_owner": _is_owner(user, project)},
    )


def _transition(project_id: int, target: str, user: User, db: Session):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not _is_owner(user, project):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if target not in ALLOWED_STATUS_TRANSITIONS.get(project.status, set()):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)
    project.status = target
    db.commit()
    return RedirectResponse(
        f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/projects/{project_id}/open")
def open_project(
    project_id: int,
    user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    return _transition(project_id, STATUS_OPEN, user, db)


@router.post("/projects/{project_id}/close")
def close_project(
    project_id: int,
    user: User = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    return _transition(project_id, STATUS_CLOSED, user, db)
