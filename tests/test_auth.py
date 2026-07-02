"""Login / logout HTTP flow, including anti-enumeration behavior."""

from app.routes.auth import INVALID_CREDENTIALS_MESSAGE

# NOTE: the "does every GET HTML page render through Jinja (no raw markup leak)"
# guard lives in tests/test_rendering.py, which enumerates ALL such pages. The
# old login-only render test was replaced by it.


def test_failed_login_renders_generic_error_in_html(client, users, login):
    r = login(client, users["student"]["email"], "wrong-password")
    assert r.status_code == 401
    # The generic 简体中文 error must appear in the rendered page, not just be
    # implied by the status code.
    assert INVALID_CREDENTIALS_MESSAGE in r.text
    assert "{{" not in r.text
    assert "{%" not in r.text


def test_login_success_redirects_and_sets_cookie(client, users, login):
    r = login(client, users["student"]["email"], users["student"]["password"])
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    # A session cookie is issued on success.
    assert "spm_session" in r.headers.get("set-cookie", "")


def test_login_wrong_password_rejected_no_session(client, users, login):
    r = login(client, users["student"]["email"], "wrong-password")
    assert r.status_code == 401
    # No authenticated session established: a protected route stays 401.
    probe = client.get("/__probe/teacher")
    assert probe.status_code == 401


def test_login_unknown_email_same_generic_response(client, users, login):
    wrong_pw = login(client, users["student"]["email"], "wrong-password")
    unknown = login(client, "nobody@example.com", "whatever")
    # Identical status and body: the response must not reveal whether the
    # account exists.
    assert unknown.status_code == wrong_pw.status_code == 401
    assert unknown.text == wrong_pw.text


def test_login_oversized_password_rejected_before_hashing(client, users, login):
    # A huge password must be rejected generically (401), not hashed (which
    # would let an attacker burn argon2 CPU) and not 500.
    r = login(client, users["student"]["email"], "x" * 10_000)
    assert r.status_code == 401
    assert INVALID_CREDENTIALS_MESSAGE in r.text


def test_login_missing_fields_returns_422(client):
    r = client.post("/login", data={"email": "a@b.com"}, follow_redirects=False)
    assert r.status_code == 422


def test_logout_clears_session(client, users, login):
    login(client, users["teacher"]["email"], users["teacher"]["password"])
    assert client.get("/__probe/teacher").status_code == 200
    client.post("/logout")
    assert client.get("/__probe/teacher").status_code == 401
