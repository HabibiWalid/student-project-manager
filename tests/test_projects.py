"""Phase 2 — Projects: creation, status transitions, and role/visibility rules."""

from sqlalchemy import select

from app.models import (
    STATUS_CLOSED,
    STATUS_DRAFT,
    STATUS_OPEN,
    Project,
)
from tests.conftest import STUDENT, TEACHER, create_project, user_id


def _project_status(session_factory, project_id):
    db = session_factory()
    try:
        return db.execute(
            select(Project.status).where(Project.id == project_id)
        ).scalar_one()
    finally:
        db.close()


# --- entry / routing ---------------------------------------------------------


def test_root_redirects_to_login_when_anonymous(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_root_redirects_to_projects_when_authenticated(student_client):
    r = student_client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/projects"


def test_projects_list_requires_auth(client):
    assert client.get("/projects").status_code == 401


# --- creation (teacher only) -------------------------------------------------


def test_teacher_creates_project_as_draft(teacher_client, session_factory):
    r = teacher_client.post(
        "/projects",
        data={"title": "毕业设计", "description": "描述"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = session_factory()
    try:
        p = db.execute(select(Project).where(Project.title == "毕业设计")).scalar_one()
        assert p.status == STATUS_DRAFT
        assert p.teacher_id == user_id(session_factory, TEACHER["email"])
    finally:
        db.close()


def test_student_cannot_create_project(student_client):
    r = student_client.post(
        "/projects", data={"title": "x"}, follow_redirects=False
    )
    assert r.status_code == 403


def test_create_rejects_empty_title(teacher_client):
    r = teacher_client.post(
        "/projects", data={"title": "   "}, follow_redirects=False
    )
    assert r.status_code == 400


def test_create_rejects_nonpositive_max_teams(teacher_client):
    r = teacher_client.post(
        "/projects",
        data={"title": "T", "max_teams": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_create_rejects_close_before_open(teacher_client):
    r = teacher_client.post(
        "/projects",
        data={
            "title": "T",
            "opens_at": "2026-07-10T09:00",
            "closes_at": "2026-07-01T09:00",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400


# --- status transitions ------------------------------------------------------


def test_teacher_opens_then_closes_project(teacher_client, session_factory):
    pid = create_project(
        session_factory,
        teacher_id=user_id(session_factory, TEACHER["email"]),
        title="T",
        status=STATUS_DRAFT,
    )
    assert teacher_client.post(f"/projects/{pid}/open").status_code in (200, 303)
    assert _project_status(session_factory, pid) == STATUS_OPEN
    assert teacher_client.post(f"/projects/{pid}/close").status_code in (200, 303)
    assert _project_status(session_factory, pid) == STATUS_CLOSED


def test_cannot_close_a_draft(teacher_client, session_factory):
    pid = create_project(
        session_factory,
        teacher_id=user_id(session_factory, TEACHER["email"]),
        title="T",
        status=STATUS_DRAFT,
    )
    assert teacher_client.post(f"/projects/{pid}/close").status_code == 409
    assert _project_status(session_factory, pid) == STATUS_DRAFT


def test_student_cannot_open_project(student_client, session_factory):
    pid = create_project(
        session_factory,
        teacher_id=user_id(session_factory, TEACHER["email"]),
        title="T",
        status=STATUS_DRAFT,
    )
    assert student_client.post(f"/projects/{pid}/open").status_code == 403
    assert _project_status(session_factory, pid) == STATUS_DRAFT


def test_teacher_cannot_open_another_teachers_project(
    teacher_client, session_factory
):
    # A second teacher owns this project.
    from tests.conftest import _create_user
    from app.models import ROLE_TEACHER

    _create_user(
        session_factory,
        email="other@example.com",
        password="Other#Pass-123",
        name="他人",
        role=ROLE_TEACHER,
    )
    other_id = user_id(session_factory, "other@example.com")
    pid = create_project(
        session_factory, teacher_id=other_id, title="别人的", status=STATUS_DRAFT
    )
    # Logged-in teacher is NOT the owner.
    assert teacher_client.post(f"/projects/{pid}/open").status_code == 403
    assert _project_status(session_factory, pid) == STATUS_DRAFT


def test_open_missing_project_404(teacher_client):
    assert teacher_client.post("/projects/999999/open").status_code == 404


# --- visibility / status filtering -------------------------------------------


def test_student_list_shows_only_open(student_client, session_factory):
    tid = user_id(session_factory, TEACHER["email"])
    create_project(session_factory, teacher_id=tid, title="草稿A", status=STATUS_DRAFT)
    create_project(session_factory, teacher_id=tid, title="开放B", status=STATUS_OPEN)
    create_project(session_factory, teacher_id=tid, title="关闭C", status=STATUS_CLOSED)

    body = student_client.get("/projects").text
    assert "开放B" in body
    assert "草稿A" not in body
    assert "关闭C" not in body


def test_teacher_list_shows_own_drafts(teacher_client, session_factory):
    tid = user_id(session_factory, TEACHER["email"])
    create_project(session_factory, teacher_id=tid, title="草稿A", status=STATUS_DRAFT)
    body = teacher_client.get("/projects").text
    assert "草稿A" in body


def test_student_cannot_view_draft_returns_404(student_client, session_factory):
    tid = user_id(session_factory, TEACHER["email"])
    pid = create_project(
        session_factory, teacher_id=tid, title="隐藏草稿", status=STATUS_DRAFT
    )
    assert student_client.get(f"/projects/{pid}").status_code == 404


def test_student_can_view_open_project(student_client, session_factory):
    tid = user_id(session_factory, TEACHER["email"])
    pid = create_project(
        session_factory, teacher_id=tid, title="可见项目", status=STATUS_OPEN
    )
    r = student_client.get(f"/projects/{pid}")
    assert r.status_code == 200
    assert "可见项目" in r.text
    # Rendered through Jinja, no raw markup leaks.
    assert "{{" not in r.text and "{%" not in r.text


def test_teacher_can_view_own_draft(teacher_client, session_factory):
    tid = user_id(session_factory, TEACHER["email"])
    pid = create_project(
        session_factory, teacher_id=tid, title="我的草稿", status=STATUS_DRAFT
    )
    r = teacher_client.get(f"/projects/{pid}")
    assert r.status_code == 200
    assert "我的草稿" in r.text
    # Owner view renders the action-button branch; still no raw markup leaks.
    assert "{{" not in r.text and "{%" not in r.text
    assert "开放项目" in r.text  # owner sees the "open" action on a draft
