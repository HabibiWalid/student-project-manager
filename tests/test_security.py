"""Password hashing and the authenticate() seam."""

from app import security
from tests.conftest import STUDENT, _create_user

PLAIN = "Correct-Horse-Battery-Staple-9"


def test_hash_is_not_plaintext():
    hashed = security.hash_password(PLAIN)
    assert hashed != PLAIN
    assert PLAIN not in hashed


def test_hash_uses_argon2():
    assert security.hash_password(PLAIN).startswith("$argon2")


def test_hash_is_salted_unique():
    # Same password hashed twice must differ (random per-hash salt).
    assert security.hash_password(PLAIN) != security.hash_password(PLAIN)


def test_verify_accepts_correct_password():
    hashed = security.hash_password(PLAIN)
    assert security.verify_password(PLAIN, hashed) is True


def test_verify_rejects_wrong_password():
    hashed = security.hash_password(PLAIN)
    assert security.verify_password("not-the-password", hashed) is False


def test_verify_fails_closed_on_garbage_hash():
    # A malformed stored hash must return False, never raise.
    assert security.verify_password(PLAIN, "not-a-valid-argon2-hash") is False


def test_authenticate_success(session_factory):
    _create_user(session_factory, **STUDENT)
    db = session_factory()
    try:
        user = security.authenticate(db, STUDENT["email"], STUDENT["password"])
        assert user is not None
        assert user.email == STUDENT["email"]
    finally:
        db.close()


def test_authenticate_wrong_password_returns_none(session_factory):
    _create_user(session_factory, **STUDENT)
    db = session_factory()
    try:
        assert security.authenticate(db, STUDENT["email"], "wrong") is None
    finally:
        db.close()


def test_authenticate_unknown_email_returns_none(session_factory):
    db = session_factory()
    try:
        assert security.authenticate(db, "nobody@example.com", "whatever") is None
    finally:
        db.close()


def test_authenticate_normalizes_email(session_factory):
    _create_user(session_factory, **STUDENT)
    db = session_factory()
    try:
        mixed = "  " + STUDENT["email"].upper() + "  "
        user = security.authenticate(db, mixed, STUDENT["password"])
        assert user is not None
    finally:
        db.close()
