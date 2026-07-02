"""Idempotent user seeding for the MVP.

Creates one teacher and two students. Credentials come ONLY from environment
variables — there are no default passwords. If any required variable is missing
the script reports every missing var and exits non-zero (fail loud) rather than
seeding a guessable account.

Roles are assigned here, server-side, from a fixed table — never taken from
client input.

Run:  python -m seed      (with the seed env vars set, e.g. via your .env)
"""

from __future__ import annotations

import os
import sys
from typing import Mapping

from sqlalchemy import select

from app.config import DEFAULT_DATABASE_URL
from app.db import init_db, make_engine, make_session_factory
from app.models import ROLE_STUDENT, ROLE_TEACHER, User
from app.security import hash_password, normalize_email

# (env-prefix, role, name-default). Emails and passwords are required; names
# fall back to a placeholder since they are not secret.
_SEED_SPEC = [
    ("SEED_TEACHER", ROLE_TEACHER, "教师"),
    ("SEED_STUDENT1", ROLE_STUDENT, "学生一"),
    ("SEED_STUDENT2", ROLE_STUDENT, "学生二"),
]


class SeedConfigError(RuntimeError):
    """Required seed configuration is missing."""


def _collect_users(env: Mapping[str, str]) -> list[dict]:
    """Read seed users from env, accumulating ALL missing required vars."""
    users: list[dict] = []
    missing: list[str] = []

    for prefix, role, name_default in _SEED_SPEC:
        email = env.get(f"{prefix}_EMAIL")
        password = env.get(f"{prefix}_PASSWORD")
        if not email:
            missing.append(f"{prefix}_EMAIL")
        if not password:
            missing.append(f"{prefix}_PASSWORD")
        users.append(
            {
                "email": email,
                "password": password,
                "name": env.get(f"{prefix}_NAME") or name_default,
                "role": role,
            }
        )

    if missing:
        raise SeedConfigError(
            "Missing required seed environment variables: "
            + ", ".join(missing)
        )
    return users


def _upsert(db, *, email: str, password: str, name: str, role: str) -> str:
    """Create or update a user by (normalized) email. Idempotent."""
    norm = normalize_email(email)
    existing = db.execute(
        select(User).where(User.email == norm)
    ).scalar_one_or_none()

    if existing is None:
        db.add(
            User(
                email=norm,
                password_hash=hash_password(password),
                name=name,
                role=role,
            )
        )
        return "created"

    existing.password_hash = hash_password(password)
    existing.name = name
    existing.role = role
    return "updated"


def main(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env

    try:
        users = _collect_users(env)
    except SeedConfigError as exc:
        print(f"seed: {exc}", file=sys.stderr)
        return 1

    engine = make_engine(env.get("DATABASE_URL") or DEFAULT_DATABASE_URL)
    init_db(engine)
    session_factory = make_session_factory(engine)

    db = session_factory()
    try:
        for u in users:
            action = _upsert(db, **u)
            # Log the email and role only — never the password.
            print(f"seed: {action} {normalize_email(u['email'])} ({u['role']})")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
