"""Password hashing and credential authentication.

argon2 (via argon2-cffi) is a vetted, current-best-practice KDF. We never invent
crypto and never store plaintext or fast hashes.

`authenticate()` is the single seam behind which a future SSO adapter can slot
without touching routes.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from sqlalchemy import select

from app.models import User

# One shared hasher; argon2-cffi is thread-safe. Defaults are sane for a login
# flow (Argon2id).
_hasher = PasswordHasher()

# A precomputed hash of a throwaway value. We verify against it for unknown
# emails so that a missing account costs the same time as a wrong password,
# closing the user-enumeration timing side channel.
_DUMMY_HASH = _hasher.hash("dummy-password-for-constant-time-auth")


def normalize_email(email: str) -> str:
    """Canonical form used for both storage and lookup."""
    return email.strip().lower()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True iff `plain` matches `hashed`. Fails closed on any argon2
    error (mismatch, malformed/garbage hash) — never raises to the caller."""
    try:
        return _hasher.verify(hashed, plain)
    except (Argon2Error, InvalidHashError):
        # Argon2Error covers a genuine mismatch; InvalidHashError (a ValueError,
        # not an Argon2Error) covers a malformed/garbage stored hash.
        return False


def authenticate(db, email: str, password: str) -> User | None:
    """Return the matching User for valid credentials, else None.

    Same generic outcome (None) whether the email is unknown or the password is
    wrong — callers must not distinguish the two. For an unknown email we still
    run a verification against a dummy hash so response time does not reveal
    whether the account exists (enumeration / timing side channel).
    """
    normalized = normalize_email(email)
    user = db.execute(
        select(User).where(User.email == normalized)
    ).scalar_one_or_none()

    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None

    if not verify_password(password, user.password_hash):
        return None

    return user
