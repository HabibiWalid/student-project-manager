"""Phase 4 — Submissions & files: upload validation, atomicity, authz download."""

from hashlib import sha256

from sqlalchemy import func, select

from app.models import STATUS_OPEN, Submission, SubmissionFile
from tests.conftest import (
    STUDENT,
    TEACHER,
    create_project,
    create_team,
    make_student,
    user_id,
    write_submission_file,
)

STUDENT_PW = "Student#Pass-123"
PNG = b"\x89PNG\r\n\x1a\n" + b"real-png-body"
PNG2 = b"\x89PNG\r\n\x1a\n" + b"second-png"
PDF = b"%PDF-1.4\n%%EOF\n"
EXE = b"MZ\x90\x00" + b"\x00" * 16


def _member_team(session_factory):
    """Open project + a team whose leader/member is the seeded STUDENT."""
    tid = user_id(session_factory, TEACHER["email"])
    pid = create_project(
        session_factory, teacher_id=tid, title="P", status=STATUS_OPEN, max_teams=5
    )
    sid = user_id(session_factory, STUDENT["email"])
    team_id = create_team(session_factory, project_id=pid, name="队", leader_id=sid)
    return team_id, pid


def _no_files(upload_dir):
    return not upload_dir.exists() or list(upload_dir.iterdir()) == []


def _submission_count(session_factory):
    db = session_factory()
    try:
        return db.execute(select(func.count()).select_from(Submission)).scalar_one()
    finally:
        db.close()


# --- submit: happy + authz ---------------------------------------------------


def test_member_submits_note_and_file(student_client, session_factory, upload_dir, users):
    team_id, _ = _member_team(session_factory)
    r = student_client.post(
        f"/teams/{team_id}/submissions",
        data={"note": "我的成果"},
        files=[("files", ("report.png", PNG, "image/png"))],
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = session_factory()
    try:
        sub = db.execute(select(Submission).where(Submission.team_id == team_id)).scalar_one()
        assert sub.note == "我的成果"
        sf = db.execute(
            select(SubmissionFile).where(SubmissionFile.submission_id == sub.id)
        ).scalars().all()
        assert len(sf) == 1
        assert sf[0].original_name == "report.png"
        assert sf[0].mime == "image/png"
        assert sf[0].size_bytes == len(PNG)
        assert sf[0].sha256 == sha256(PNG).hexdigest()
        assert (upload_dir / sf[0].stored_name).read_bytes() == PNG
        assert sf[0].stored_name != "report.png"  # generated name, not user's
    finally:
        db.close()


def test_member_submits_multiple_files(student_client, session_factory, upload_dir, users):
    team_id, _ = _member_team(session_factory)
    r = student_client.post(
        f"/teams/{team_id}/submissions",
        data={"note": ""},
        files=[
            ("files", ("a.png", PNG, "image/png")),
            ("files", ("b.pdf", PDF, "application/pdf")),
        ],
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = session_factory()
    try:
        sub = db.execute(select(Submission).where(Submission.team_id == team_id)).scalar_one()
        n = db.execute(
            select(func.count()).select_from(SubmissionFile).where(
                SubmissionFile.submission_id == sub.id
            )
        ).scalar_one()
        assert n == 2
    finally:
        db.close()


def test_non_member_cannot_submit(make_client, login, session_factory, upload_dir, users):
    team_id, _ = _member_team(session_factory)
    make_student(session_factory, "outsider@example.com")
    c = make_client()
    login(c, "outsider@example.com", STUDENT_PW)
    r = c.post(
        f"/teams/{team_id}/submissions",
        data={"note": "x"},
        files=[("files", ("a.png", PNG, "image/png"))],
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert _submission_count(session_factory) == 0
    assert _no_files(upload_dir)


def test_anonymous_cannot_submit(client, session_factory, upload_dir, users):
    team_id, _ = _member_team(session_factory)
    r = client.post(
        f"/teams/{team_id}/submissions",
        files=[("files", ("a.png", PNG, "image/png"))],
        follow_redirects=False,
    )
    assert r.status_code == 401
    assert _no_files(upload_dir)


# --- submit: validation / rejection ------------------------------------------


def test_disallowed_extension_rejected(student_client, session_factory, upload_dir, users):
    team_id, _ = _member_team(session_factory)
    r = student_client.post(
        f"/teams/{team_id}/submissions",
        files=[("files", ("evil.exe", EXE, "application/octet-stream"))],
        follow_redirects=False,
    )
    assert r.status_code == 415
    assert _submission_count(session_factory) == 0
    assert _no_files(upload_dir)


def test_magic_mismatch_rejected(student_client, session_factory, upload_dir, users):
    team_id, _ = _member_team(session_factory)
    # .png extension but the bytes are an EXE.
    r = student_client.post(
        f"/teams/{team_id}/submissions",
        files=[("files", ("fake.png", EXE, "image/png"))],
        follow_redirects=False,
    )
    assert r.status_code == 415
    assert _submission_count(session_factory) == 0
    assert _no_files(upload_dir)


def test_traversal_filename_stored_safely(student_client, session_factory, upload_dir, users):
    team_id, _ = _member_team(session_factory)
    r = student_client.post(
        f"/teams/{team_id}/submissions",
        files=[("files", ("../../etc/passwd.png", PNG, "image/png"))],
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = session_factory()
    try:
        sf = db.execute(select(SubmissionFile)).scalars().one()
        assert sf.original_name == "passwd.png"  # sanitized label
        # Exactly one file, under a generated name, inside the upload dir.
        on_disk = list(upload_dir.iterdir())
        assert len(on_disk) == 1
        assert on_disk[0].name == sf.stored_name
        assert "passwd" not in sf.stored_name
    finally:
        db.close()


def test_partial_submission_leaves_zero_trace(student_client, session_factory, upload_dir, users):
    # file 1 valid, file 2 magic-mismatch: whole submission must vanish.
    team_id, _ = _member_team(session_factory)
    r = student_client.post(
        f"/teams/{team_id}/submissions",
        files=[
            ("files", ("good.png", PNG, "image/png")),
            ("files", ("bad.png", EXE, "image/png")),
        ],
        follow_redirects=False,
    )
    assert r.status_code == 415
    assert _submission_count(session_factory) == 0
    assert _no_files(upload_dir)


def test_db_failure_leaves_zero_trace(
    student_client, session_factory, upload_dir, users, monkeypatch
):
    from sqlalchemy.exc import IntegrityError

    def _boom(*args, **kwargs):
        raise IntegrityError("insert", {}, Exception("boom"))

    monkeypatch.setattr("app.routes.submissions._persist_submission", _boom)
    team_id, _ = _member_team(session_factory)
    r = student_client.post(
        f"/teams/{team_id}/submissions",
        files=[("files", ("a.png", PNG, "image/png"))],
        follow_redirects=False,
    )
    assert r.status_code == 500  # generic failure, but...
    assert _submission_count(session_factory) == 0  # no rows
    assert _no_files(upload_dir)  # and no orphan file on disk


def test_too_many_files_rejected(student_client, session_factory, upload_dir, users):
    team_id, _ = _member_team(session_factory)
    files = [("files", (f"f{i}.png", PNG, "image/png")) for i in range(11)]  # > 10
    r = student_client.post(
        f"/teams/{team_id}/submissions", files=files, follow_redirects=False
    )
    assert r.status_code == 400
    assert _submission_count(session_factory) == 0
    assert _no_files(upload_dir)


def test_empty_file_rejected(student_client, session_factory, upload_dir, users):
    team_id, _ = _member_team(session_factory)
    r = student_client.post(
        f"/teams/{team_id}/submissions",
        files=[("files", ("empty.png", b"", "image/png"))],
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert _submission_count(session_factory) == 0
    assert _no_files(upload_dir)


def test_streaming_body_cap_returns_413_midstream(
    student_client, app, session_factory, upload_dir, users
):
    team_id, _ = _member_team(session_factory)
    app.state.max_request_bytes = 1000  # tiny cap
    big = b"\x89PNG\r\n\x1a\n" + b"x" * 5000  # body well over the cap
    r = student_client.post(
        f"/teams/{team_id}/submissions",
        files=[("files", ("big.png", big, "image/png"))],
        follow_redirects=False,
    )
    assert r.status_code == 413  # clean 413, not 500 and not a success
    assert _submission_count(session_factory) == 0
    assert _no_files(upload_dir)


# --- download: authz + hardened headers --------------------------------------


def _seed_download(session_factory, upload_dir, *, original_name="report.png", content=PNG):
    team_id, pid = _member_team(session_factory)
    file_id = write_submission_file(
        session_factory,
        upload_dir,
        team_id=team_id,
        project_id=pid,
        original_name=original_name,
        content=content,
    )
    return team_id, file_id


def test_member_downloads_file(student_client, session_factory, upload_dir, users):
    _, file_id = _seed_download(session_factory, upload_dir)
    r = student_client.get(f"/files/{file_id}")
    assert r.status_code == 200
    assert r.content == PNG
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in r.headers["content-disposition"]


def test_teacher_downloads_file(teacher_client, session_factory, upload_dir, users):
    _, file_id = _seed_download(session_factory, upload_dir)
    assert teacher_client.get(f"/files/{file_id}").status_code == 200


def test_non_member_cannot_download(make_client, login, session_factory, upload_dir, users):
    _, file_id = _seed_download(session_factory, upload_dir)
    make_student(session_factory, "outsider@example.com")
    c = make_client()
    login(c, "outsider@example.com", STUDENT_PW)
    assert c.get(f"/files/{file_id}").status_code == 403


def test_anonymous_cannot_download(client, session_factory, upload_dir, users):
    _, file_id = _seed_download(session_factory, upload_dir)
    assert client.get(f"/files/{file_id}").status_code == 401


def test_download_missing_file_404(student_client, users):
    assert student_client.get("/files/999999").status_code == 404


def test_download_disposition_has_no_header_injection(
    student_client, session_factory, upload_dir, users
):
    # A nasty stored label must not break the Content-Disposition header.
    _, file_id = _seed_download(
        session_factory, upload_dir, original_name='evil"\r\nSet-Cookie: x.png'
    )
    r = student_client.get(f"/files/{file_id}")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert "\r" not in cd and "\n" not in cd
    assert "Set-Cookie" not in cd or ";" in cd  # not injected as a real header
