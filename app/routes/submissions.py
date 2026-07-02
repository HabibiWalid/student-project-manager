"""Submission routes: submit (note + files) and authorized file download.

Submissions are a normal write -> DEFAULT engine (get_db), NOT the claim engine.
Only a member of the team may submit for it. Downloads are restricted to that
team's members or any teacher, enforced server-side.
"""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import current_user, get_db
from app.models import (
    ROLE_TEACHER,
    SUBMISSION_SUBMITTED,
    Submission,
    SubmissionFile,
    Team,
    TeamMember,
    User,
)
from app.templating import templates
from app.uploads import (
    UploadError,
    delete_stored,
    sanitize_original_name,
    store_files,
)

router = APIRouter()

MAX_NOTE_LEN = 5000


def _is_member(db: Session, team_id: int, user_id: int) -> bool:
    return (
        db.execute(
            select(TeamMember).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
        ).first()
        is not None
    )


def _persist_submission(db, *, team_id, project_id, note, stored):
    """Insert the Submission + all SubmissionFile rows in one transaction.
    Factored out so the atomicity/cleanup path can be exercised in tests."""
    sub = Submission(
        team_id=team_id,
        project_id=project_id,
        note=note,
        status=SUBMISSION_SUBMITTED,
    )
    db.add(sub)
    db.flush()
    for s in stored:
        db.add(
            SubmissionFile(
                submission_id=sub.id,
                stored_name=s.stored_name,
                original_name=s.original_name,
                size_bytes=s.size_bytes,
                mime=s.mime,
                sha256=s.sha256,
            )
        )
    db.commit()
    return sub.id


@router.get("/teams/{team_id}/submissions/new")
def submission_form(
    team_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not _is_member(db, team_id, user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return templates.TemplateResponse(
        request, "submission_new.html", {"team": team, "error": None}
    )


@router.post("/teams/{team_id}/submissions")
def create_submission(
    team_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    note: str = Form(""),
    files: list[UploadFile] = File(default=[]),
):
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not _is_member(db, team_id, user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    if len(note) > MAX_NOTE_LEN:
        return templates.TemplateResponse(
            request,
            "submission_new.html",
            {"team": team, "error": "说明过长"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    upload_dir = request.app.state.upload_dir
    try:
        stored = store_files(files, upload_dir)
    except UploadError as e:
        return templates.TemplateResponse(
            request,
            "submission_new.html",
            {"team": team, "error": e.message},
            status_code=e.status_code,
        )

    # Atomicity: if the DB write fails, remove the just-stored files so nothing
    # is left orphaned on disk (and no half-written rows — one transaction).
    try:
        _persist_submission(
            db,
            team_id=team_id,
            project_id=team.project_id,
            note=note,
            stored=stored,
        )
    except Exception:
        db.rollback()
        delete_stored(upload_dir, stored)
        return templates.TemplateResponse(
            request,
            "submission_new.html",
            {"team": team, "error": "提交失败，请稍后重试"},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return RedirectResponse(
        f"/teams/{team_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/files/{file_id}")
def download_file(
    file_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    sf = db.get(SubmissionFile, file_id)
    if sf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    submission = db.get(Submission, sf.submission_id)
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # Members of the file's team, or any teacher.
    if user.role != ROLE_TEACHER and not _is_member(db, submission.team_id, user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    upload_dir = request.app.state.upload_dir
    resolved = (upload_dir / sf.stored_name).resolve()
    # Defense-in-depth: the resolved path must stay inside the upload dir.
    if upload_dir.resolve() not in resolved.parents or not resolved.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    return FileResponse(
        resolved,
        media_type="application/octet-stream",  # force download, no sniffing
        filename=sanitize_original_name(sf.original_name),
        headers={"X-Content-Type-Options": "nosniff"},
    )
