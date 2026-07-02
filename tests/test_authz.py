"""Server-side role gating: identity/role come from the DB, not the client."""


def test_protected_route_requires_auth(client):
    assert client.get("/__probe/teacher").status_code == 401


def test_student_forbidden_from_teacher_route(client, users, login):
    login(client, users["student"]["email"], users["student"]["password"])
    # Authenticated but wrong role -> 403 (not 401).
    assert client.get("/__probe/teacher").status_code == 403


def test_teacher_allowed_on_teacher_route(client, users, login):
    login(client, users["teacher"]["email"], users["teacher"]["password"])
    r = client.get("/__probe/teacher")
    assert r.status_code == 200
    assert r.json()["email"] == users["teacher"]["email"]
