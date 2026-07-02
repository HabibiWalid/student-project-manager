"""Phase 3 — Teams & claiming: guards, and the claim race under real threads."""

import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app import teams as svc
from app.models import (
    STATUS_CLOSED,
    STATUS_DRAFT,
    STATUS_OPEN,
    Team,
    TeamMember,
    User,
)
from tests.conftest import (
    STUDENT,
    TEACHER,
    create_project,
    create_team,
    make_student,
    user_id,
)

STUDENT_PW = "Student#Pass-123"


def _team_count(session_factory, project_id):
    db = session_factory()
    try:
        return db.execute(
            select(func.count()).select_from(Team).where(Team.project_id == project_id)
        ).scalar_one()
    finally:
        db.close()


def _open_project(session_factory, **kw):
    tid = user_id(session_factory, TEACHER["email"])
    return create_project(
        session_factory, teacher_id=tid, title="项目", status=STATUS_OPEN, **kw
    )


# --- claim: happy path + auth ------------------------------------------------


def test_student_claims_open_project(student_client, session_factory, users):
    pid = _open_project(session_factory, max_teams=5)
    r = student_client.post(
        f"/projects/{pid}/teams", data={"name": "甲队"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/teams/")
    db = session_factory()
    try:
        team = db.execute(select(Team).where(Team.project_id == pid)).scalar_one()
        assert team.name == "甲队"
        members = db.execute(
            select(TeamMember).where(TeamMember.team_id == team.id)
        ).scalars().all()
        assert len(members) == 1  # leader auto-joined
        assert members[0].user_id == user_id(session_factory, STUDENT["email"])
    finally:
        db.close()


def test_teacher_cannot_claim(teacher_client, session_factory, users):
    pid = _open_project(session_factory, max_teams=5)
    r = teacher_client.post(
        f"/projects/{pid}/teams", data={"name": "X"}, follow_redirects=False
    )
    assert r.status_code == 403


def test_anonymous_cannot_claim(client, session_factory, users):
    pid = _open_project(session_factory, max_teams=5)
    r = client.post(
        f"/projects/{pid}/teams", data={"name": "X"}, follow_redirects=False
    )
    assert r.status_code == 401


# --- claim: guards -----------------------------------------------------------


def test_claim_rejected_when_draft(student_client, session_factory, users):
    tid = user_id(session_factory, TEACHER["email"])
    pid = create_project(session_factory, teacher_id=tid, title="草稿", status=STATUS_DRAFT)
    r = student_client.post(
        f"/projects/{pid}/teams", data={"name": "X"}, follow_redirects=False
    )
    assert r.status_code == 409


def test_claim_rejected_when_closed(student_client, session_factory, users):
    tid = user_id(session_factory, TEACHER["email"])
    pid = create_project(session_factory, teacher_id=tid, title="关闭", status=STATUS_CLOSED)
    r = student_client.post(
        f"/projects/{pid}/teams", data={"name": "X"}, follow_redirects=False
    )
    assert r.status_code == 409


def test_claim_rejected_before_opens_at(student_client, session_factory, users):
    future = datetime.now(timezone.utc) + timedelta(days=1)
    pid = _open_project(session_factory, opens_at=future)
    r = student_client.post(
        f"/projects/{pid}/teams", data={"name": "X"}, follow_redirects=False
    )
    assert r.status_code == 409


def test_claim_rejected_after_closes_at(student_client, session_factory, users):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    pid = _open_project(session_factory, closes_at=past)
    r = student_client.post(
        f"/projects/{pid}/teams", data={"name": "X"}, follow_redirects=False
    )
    assert r.status_code == 409


def test_claim_rejected_when_full(student_client, session_factory, users):
    pid = _open_project(session_factory, max_teams=1)
    other = make_student(session_factory, "pre@example.com")
    create_team(session_factory, project_id=pid, name="已存在", leader_id=other, slot_no=0)
    r = student_client.post(
        f"/projects/{pid}/teams", data={"name": "迟到队"}, follow_redirects=False
    )
    assert r.status_code == 409
    assert _team_count(session_factory, pid) == 1


def test_claim_rejected_duplicate_name(student_client, session_factory, users):
    pid = _open_project(session_factory, max_teams=5)
    other = make_student(session_factory, "pre2@example.com")
    create_team(session_factory, project_id=pid, name="甲队", leader_id=other, slot_no=0)
    r = student_client.post(
        f"/projects/{pid}/teams", data={"name": "甲队"}, follow_redirects=False
    )
    assert r.status_code == 409


def test_claim_missing_project_404(student_client, users):
    r = student_client.post(
        "/projects/999999/teams", data={"name": "X"}, follow_redirects=False
    )
    assert r.status_code == 404


def test_claim_rejects_empty_name(student_client, session_factory, users):
    pid = _open_project(session_factory, max_teams=5)
    r = student_client.post(
        f"/projects/{pid}/teams", data={"name": "   "}, follow_redirects=False
    )
    assert r.status_code == 400


# --- join --------------------------------------------------------------------


def test_student_joins_team(make_client, login, session_factory, users):
    pid = _open_project(session_factory, max_teams=5)
    leader = make_student(session_factory, "leader@example.com")
    tid = create_team(session_factory, project_id=pid, name="甲队", leader_id=leader)
    make_student(session_factory, "joiner@example.com")
    c = make_client()
    login(c, "joiner@example.com", STUDENT_PW)
    r = c.post(f"/teams/{tid}/join", follow_redirects=False)
    assert r.status_code == 303
    db = session_factory()
    try:
        cnt = db.execute(
            select(func.count()).select_from(TeamMember).where(TeamMember.team_id == tid)
        ).scalar_one()
        assert cnt == 2
    finally:
        db.close()


def test_double_join_same_team_blocked(student_client, session_factory, users):
    pid = _open_project(session_factory, max_teams=5)
    r = student_client.post(
        f"/projects/{pid}/teams", data={"name": "甲队"}, follow_redirects=False
    )
    team_id = int(r.headers["location"].rsplit("/", 1)[1])
    # STUDENT is already the leader/member; joining again is blocked.
    r2 = student_client.post(f"/teams/{team_id}/join", follow_redirects=False)
    assert r2.status_code == 409


def test_second_team_per_project_blocked(
    student_client, make_client, login, session_factory, users
):
    pid = _open_project(session_factory, max_teams=5)
    # STUDENT claims team A.
    student_client.post(
        f"/projects/{pid}/teams", data={"name": "A队"}, follow_redirects=False
    )
    # Another student makes team B on the same project.
    leader_b = make_student(session_factory, "bleader@example.com")
    team_b = create_team(session_factory, project_id=pid, name="B队", leader_id=leader_b, slot_no=1)
    # STUDENT tries to join B -> already in a team on this project.
    r = student_client.post(f"/teams/{team_b}/join", follow_redirects=False)
    assert r.status_code == 409


def test_join_missing_team_404(student_client, users):
    r = student_client.post("/teams/999999/join", follow_redirects=False)
    assert r.status_code == 404


# --- 503 mapping (deterministic, no reliance on real lock timeout) -----------


def test_claim_route_maps_db_lock_timeout_to_503(
    student_client, session_factory, users, monkeypatch
):
    pid = _open_project(session_factory, max_teams=5)

    def _boom(*args, **kwargs):
        raise OperationalError("BEGIN IMMEDIATE", {}, Exception("database is locked"))

    monkeypatch.setattr("app.teams.create_team", _boom)
    r = student_client.post(
        f"/projects/{pid}/teams", data={"name": "X"}, follow_redirects=False
    )
    assert r.status_code == 503  # not 500


# --- concurrency: the claim race (real threads) ------------------------------


def _race_claims(claim_session_factory, session_factory, pid, student_ids):
    """Fire all claimers simultaneously; return per-worker results."""
    n = len(student_ids)
    barrier = threading.Barrier(n)
    results = [None] * n

    def worker(i):
        db = claim_session_factory()
        try:
            barrier.wait()
            leader = db.get(User, student_ids[i])
            svc.create_team(db, project_id=pid, name=f"队{i}", leader=leader)
            results[i] = "ok"
        except svc.ClaimError as e:
            results[i] = ("claim", e.reason)
        except BaseException as e:  # noqa: BLE001 - want to surface anything odd
            results[i] = ("unexpected", f"{type(e).__name__}: {e}")
        finally:
            db.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_concurrent_claims_last_slot_exactly_one_wins(
    claim_session_factory, session_factory, users
):
    pid = _open_project(session_factory, max_teams=1)
    ids = [make_student(session_factory, f"s{i}@example.com") for i in range(12)]

    results = _race_claims(claim_session_factory, session_factory, pid, ids)

    assert results.count("ok") == 1, results
    # every loser got a clean domain rejection, never an unexpected error
    assert all(
        r == "ok" or (isinstance(r, tuple) and r[0] == "claim") for r in results
    ), results
    assert _team_count(session_factory, pid) == 1


def test_concurrent_claims_for_third_of_three_slots(
    claim_session_factory, session_factory, users
):
    pid = _open_project(session_factory, max_teams=3)
    # Pre-fill two of the three slots.
    for k in range(2):
        lid = make_student(session_factory, f"pre{k}@example.com")
        create_team(session_factory, project_id=pid, name=f"预置{k}", leader_id=lid, slot_no=k)

    ids = [make_student(session_factory, f"q{i}@example.com") for i in range(10)]
    results = _race_claims(claim_session_factory, session_factory, pid, ids)

    assert results.count("ok") == 1, results
    assert _team_count(session_factory, pid) == 3  # never exceeds capacity


def test_claim_route_under_contention_never_500(
    make_client, login, session_factory, users
):
    pid = _open_project(session_factory, max_teams=1)
    n = 8
    for i in range(n):
        make_student(session_factory, f"h{i}@example.com")
    clients = [make_client() for _ in range(n)]
    for i, c in enumerate(clients):
        login(c, f"h{i}@example.com", STUDENT_PW)

    barrier = threading.Barrier(n)
    statuses = [None] * n

    def worker(i):
        barrier.wait()
        r = clients[i].post(
            f"/projects/{pid}/teams", data={"name": f"队{i}"}, follow_redirects=False
        )
        statuses[i] = r.status_code

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert 500 not in statuses, statuses
    assert all(s in (303, 409, 503) for s in statuses), statuses
    assert statuses.count(303) == 1, statuses
    assert _team_count(session_factory, pid) == 1
