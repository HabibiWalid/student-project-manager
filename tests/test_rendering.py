"""Systemic guard against raw-template-markup leaks.

This ONE test enumerates every server-rendered GET HTML page in the app and
asserts the response is a real rendered page (status 200, HTML content type) and
contains NO raw Jinja markup ("{{" or "{%"). A template served as a static file
— or a route that bypasses `templates.TemplateResponse` — would leak literal
markup and fail here.

Adding a new HTML page? Add one line to `html_pages()` below. That is the single
place this class of bug is fenced off, so no new page can escape the check.
"""

from __future__ import annotations

import pytest

from app.models import STATUS_DRAFT, STATUS_OPEN
from tests.conftest import STUDENT, TEACHER, create_project, user_id


def _page(desc, role, path):
    return pytest.param(role, path, id=desc)


def html_pages():
    """Every GET-able HTML page: (viewer_role, path). 'anon' = not logged in."""
    return [
        _page("login (anonymous)", "anon", "/login"),
        _page("projects-list (teacher)", "teacher", "/projects"),
        _page("projects-list (student)", "student", "/projects"),
        _page("create-project form (teacher)", "teacher", "/projects/new"),
        _page("project-detail open (student)", "student", "/projects/{open_id}"),
        _page("project-detail draft (owner teacher)", "teacher", "/projects/{draft_id}"),
    ]


@pytest.fixture
def rendered_ctx(session_factory, make_client, users, login):
    """Data + logged-in clients shared by the enumeration."""
    tid = user_id(session_factory, TEACHER["email"])
    ids = {
        "open_id": create_project(
            session_factory, teacher_id=tid, title="渲染开放项目", status=STATUS_OPEN
        ),
        "draft_id": create_project(
            session_factory, teacher_id=tid, title="渲染草稿项目", status=STATUS_DRAFT
        ),
    }

    anon = make_client()
    teacher = make_client()
    login(teacher, TEACHER["email"], TEACHER["password"])
    student = make_client()
    login(student, STUDENT["email"], STUDENT["password"])

    return {"clients": {"anon": anon, "teacher": teacher, "student": student}, "ids": ids}


@pytest.mark.parametrize("role,path", html_pages())
def test_get_html_page_has_no_raw_template_markup(role, path, rendered_ctx):
    client = rendered_ctx["clients"][role]
    resolved = path.format(**rendered_ctx["ids"])

    r = client.get(resolved)

    assert r.status_code == 200, f"{resolved} returned {r.status_code}"
    assert "text/html" in r.headers.get("content-type", ""), (
        f"{resolved} is not HTML"
    )
    # The exact bug class: a template served/emitted without Jinja rendering.
    assert "{{" not in r.text, f"raw '{{{{' leaked in {resolved}"
    assert "{%" not in r.text, f"raw '{{%' leaked in {resolved}"
