"""Phase 5 — Scoring & leaderboard: award authz, points rule, void, derived board."""

from sqlalchemy import func, select

from app.models import ROLE_TEACHER, STATUS_DRAFT, STATUS_OPEN, Score
from tests.conftest import (
    STUDENT,
    TEACHER,
    _create_user,
    award_score,
    create_project,
    create_team,
    make_student,
    user_id,
)

STUDENT_PW = "Student#Pass-123"
TEACHER2 = {"email": "teacher2@example.com", "password": "Teacher2#Pass-1", "name": "陈老师"}


def _teacher_id(session_factory):
    return user_id(session_factory, TEACHER["email"])


def _setup(session_factory, status=STATUS_OPEN):
    tid = _teacher_id(session_factory)
    pid = create_project(
        session_factory, teacher_id=tid, title="项目", status=status, max_teams=5
    )
    leader = make_student(session_factory, "leader@example.com")
    team_id = create_team(session_factory, project_id=pid, name="甲队", leader_id=leader)
    return pid, team_id


def _team_total(session_factory, team_id):
    db = session_factory()
    try:
        return db.execute(
            select(func.coalesce(func.sum(Score.points), 0)).where(
                Score.team_id == team_id
            )
        ).scalar_one()
    finally:
        db.close()


def _second_teacher_client(make_client, login, session_factory):
    _create_user(session_factory, role=ROLE_TEACHER, **TEACHER2)
    c = make_client()
    login(c, TEACHER2["email"], TEACHER2["password"])
    return c


# --- award authz -------------------------------------------------------------


def test_owner_teacher_awards(teacher_client, session_factory, users):
    pid, team_id = _setup(session_factory)
    r = teacher_client.post(
        f"/projects/{pid}/teams/{team_id}/scores",
        data={"points": "10", "reason": "做得好"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert _team_total(session_factory, team_id) == 10


def test_student_cannot_award(student_client, session_factory, users):
    pid, team_id = _setup(session_factory)
    r = student_client.post(
        f"/projects/{pid}/teams/{team_id}/scores",
        data={"points": "10", "reason": "x"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert _team_total(session_factory, team_id) == 0


def test_anonymous_cannot_award(client, session_factory, users):
    pid, team_id = _setup(session_factory)
    r = client.post(
        f"/projects/{pid}/teams/{team_id}/scores",
        data={"points": "10", "reason": "x"},
        follow_redirects=False,
    )
    assert r.status_code == 401


def test_non_owner_teacher_cannot_award(make_client, login, session_factory, users):
    pid, team_id = _setup(session_factory)
    c = _second_teacher_client(make_client, login, session_factory)
    r = c.post(
        f"/projects/{pid}/teams/{team_id}/scores",
        data={"points": "10", "reason": "x"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert _team_total(session_factory, team_id) == 0


def test_award_team_not_in_project_404(teacher_client, session_factory, users):
    pid, _ = _setup(session_factory)
    # A team on a different project.
    other_pid = create_project(
        session_factory, teacher_id=_teacher_id(session_factory), title="别的", status=STATUS_OPEN
    )
    other_leader = make_student(session_factory, "ol@example.com")
    other_team = create_team(
        session_factory, project_id=other_pid, name="乙队", leader_id=other_leader
    )
    r = teacher_client.post(
        f"/projects/{pid}/teams/{other_team}/scores",
        data={"points": "5", "reason": "x"},
        follow_redirects=False,
    )
    assert r.status_code == 404


def test_award_missing_project_404(teacher_client, session_factory, users):
    r = teacher_client.post(
        "/projects/999999/teams/1/scores",
        data={"points": "5", "reason": "x"},
        follow_redirects=False,
    )
    assert r.status_code == 404


# --- points-rule boundary ----------------------------------------------------


def test_zero_points_rejected(teacher_client, session_factory, users):
    pid, team_id = _setup(session_factory)
    r = teacher_client.post(
        f"/projects/{pid}/teams/{team_id}/scores",
        data={"points": "0", "reason": "x"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert _team_total(session_factory, team_id) == 0


def test_negative_points_rejected(teacher_client, session_factory, users):
    pid, team_id = _setup(session_factory)
    r = teacher_client.post(
        f"/projects/{pid}/teams/{team_id}/scores",
        data={"points": "-5", "reason": "x"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_over_max_points_rejected(teacher_client, session_factory, users):
    pid, team_id = _setup(session_factory)
    r = teacher_client.post(
        f"/projects/{pid}/teams/{team_id}/scores",
        data={"points": "1001", "reason": "x"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_non_integer_points_422(teacher_client, session_factory, users):
    pid, team_id = _setup(session_factory)
    r = teacher_client.post(
        f"/projects/{pid}/teams/{team_id}/scores",
        data={"points": "abc", "reason": "x"},
        follow_redirects=False,
    )
    assert r.status_code == 422


def test_empty_reason_rejected(teacher_client, session_factory, users):
    pid, team_id = _setup(session_factory)
    r = teacher_client.post(
        f"/projects/{pid}/teams/{team_id}/scores",
        data={"points": "10", "reason": "   "},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert _team_total(session_factory, team_id) == 0


def test_awards_accumulate(teacher_client, session_factory, users):
    pid, team_id = _setup(session_factory)
    for pts in ("10", "15"):
        teacher_client.post(
            f"/projects/{pid}/teams/{team_id}/scores",
            data={"points": pts, "reason": "x"},
            follow_redirects=False,
        )
    assert _team_total(session_factory, team_id) == 25


# --- void --------------------------------------------------------------------


def test_award_then_void_restores_total(teacher_client, session_factory, users):
    pid, team_id = _setup(session_factory)
    tid = _teacher_id(session_factory)
    keep = award_score(session_factory, team_id=team_id, project_id=pid, points=10, awarded_by=tid)
    drop = award_score(session_factory, team_id=team_id, project_id=pid, points=7, awarded_by=tid)
    assert _team_total(session_factory, team_id) == 17
    r = teacher_client.post(f"/projects/{pid}/scores/{drop}/void", follow_redirects=False)
    assert r.status_code == 303
    assert _team_total(session_factory, team_id) == 10  # back to prior
    # the kept score survives
    db = session_factory()
    try:
        assert db.get(Score, keep) is not None
        assert db.get(Score, drop) is None
    finally:
        db.close()


def test_void_missing_score_404(teacher_client, session_factory, users):
    pid, _ = _setup(session_factory)
    r = teacher_client.post(f"/projects/{pid}/scores/999999/void", follow_redirects=False)
    assert r.status_code == 404


def test_void_other_project_score_404(teacher_client, session_factory, users):
    pid, team_id = _setup(session_factory)
    tid = _teacher_id(session_factory)
    other_pid = create_project(
        session_factory, teacher_id=tid, title="别的", status=STATUS_OPEN
    )
    other_leader = make_student(session_factory, "ol2@example.com")
    other_team = create_team(
        session_factory, project_id=other_pid, name="乙队", leader_id=other_leader
    )
    score_in_other = award_score(
        session_factory, team_id=other_team, project_id=other_pid, points=9, awarded_by=tid
    )
    # Void it via the WRONG project in the path -> 404, and it survives.
    r = teacher_client.post(
        f"/projects/{pid}/scores/{score_in_other}/void", follow_redirects=False
    )
    assert r.status_code == 404
    db = session_factory()
    try:
        assert db.get(Score, score_in_other) is not None
    finally:
        db.close()


def test_student_cannot_void(student_client, session_factory, users):
    pid, team_id = _setup(session_factory)
    tid = _teacher_id(session_factory)
    sid = award_score(session_factory, team_id=team_id, project_id=pid, points=5, awarded_by=tid)
    r = student_client.post(f"/projects/{pid}/scores/{sid}/void", follow_redirects=False)
    assert r.status_code == 403
    assert _team_total(session_factory, team_id) == 5


def test_anonymous_cannot_void(client, session_factory, users):
    pid, team_id = _setup(session_factory)
    tid = _teacher_id(session_factory)
    sid = award_score(session_factory, team_id=team_id, project_id=pid, points=5, awarded_by=tid)
    r = client.post(f"/projects/{pid}/scores/{sid}/void", follow_redirects=False)
    assert r.status_code == 401


def test_non_owner_teacher_cannot_void(make_client, login, session_factory, users):
    pid, team_id = _setup(session_factory)
    tid = _teacher_id(session_factory)
    sid = award_score(session_factory, team_id=team_id, project_id=pid, points=5, awarded_by=tid)
    c = _second_teacher_client(make_client, login, session_factory)
    r = c.post(f"/projects/{pid}/scores/{sid}/void", follow_redirects=False)
    assert r.status_code == 403
    assert _team_total(session_factory, team_id) == 5


# --- leaderboard -------------------------------------------------------------


def test_leaderboard_totals_and_order(student_client, session_factory, users):
    tid = _teacher_id(session_factory)
    pid = create_project(session_factory, teacher_id=tid, title="P", status=STATUS_OPEN, max_teams=5)
    a = create_team(session_factory, project_id=pid, name="ALPHA", leader_id=make_student(session_factory, "a@example.com"))
    b = create_team(session_factory, project_id=pid, name="BETA", leader_id=make_student(session_factory, "b@example.com"))
    award_score(session_factory, team_id=a, project_id=pid, points=5, awarded_by=tid)
    award_score(session_factory, team_id=b, project_id=pid, points=30, awarded_by=tid)
    body = student_client.get(f"/projects/{pid}/leaderboard").text
    # BETA (30) ranked above ALPHA (5)
    assert body.index("BETA") < body.index("ALPHA")


def test_leaderboard_tie_broken_by_team_id(student_client, session_factory, users):
    tid = _teacher_id(session_factory)
    pid = create_project(session_factory, teacher_id=tid, title="P", status=STATUS_OPEN, max_teams=5)
    first = create_team(session_factory, project_id=pid, name="FIRST", leader_id=make_student(session_factory, "f@example.com"))
    second = create_team(session_factory, project_id=pid, name="SECOND", leader_id=make_student(session_factory, "s@example.com"))
    # Equal totals -> deterministic order by ascending team id (first < second).
    award_score(session_factory, team_id=first, project_id=pid, points=10, awarded_by=tid)
    award_score(session_factory, team_id=second, project_id=pid, points=10, awarded_by=tid)
    body = student_client.get(f"/projects/{pid}/leaderboard").text
    assert first < second  # id ordering assumption
    assert body.index("FIRST") < body.index("SECOND")


def test_leaderboard_shows_zero_award_team_last(student_client, session_factory, users):
    tid = _teacher_id(session_factory)
    pid = create_project(session_factory, teacher_id=tid, title="P", status=STATUS_OPEN, max_teams=5)
    scored = create_team(session_factory, project_id=pid, name="SCORED", leader_id=make_student(session_factory, "sc@example.com"))
    zero = create_team(session_factory, project_id=pid, name="ZERO", leader_id=make_student(session_factory, "z@example.com"))
    award_score(session_factory, team_id=scored, project_id=pid, points=12, awarded_by=tid)
    body = student_client.get(f"/projects/{pid}/leaderboard").text
    assert "ZERO" in body  # zero-award team still shown
    assert body.index("SCORED") < body.index("ZERO")


def test_leaderboard_hidden_for_draft_to_student(student_client, session_factory, users):
    pid, _ = _setup(session_factory, status=STATUS_DRAFT)
    assert student_client.get(f"/projects/{pid}/leaderboard").status_code == 404


def test_leaderboard_requires_auth(client, session_factory, users):
    pid, _ = _setup(session_factory)
    assert client.get(f"/projects/{pid}/leaderboard").status_code == 401
