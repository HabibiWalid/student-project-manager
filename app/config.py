"""Application settings, loaded exclusively from environment variables.

Secrets never live in code. Required config is validated at load time so a
misconfigured deployment fails fast and loud instead of silently running with an
insecure default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

# A signing key shorter than this is rejected — a short/guessable key defeats
# the whole point of a signed session cookie.
MIN_SECRET_LEN = 32

DEFAULT_DATABASE_URL = "sqlite:///./app.db"
DEFAULT_SESSION_COOKIE_NAME = "spm_session"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    session_secret: str
    database_url: str
    session_cookie_secure: bool
    session_cookie_name: str


def _as_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from the environment, failing closed on bad config.

    Raises ConfigError if SESSION_SECRET is absent or too short. All other
    values fall back to safe local-dev defaults.
    """
    env = os.environ if env is None else env

    secret = env.get("SESSION_SECRET")
    if not secret or len(secret) < MIN_SECRET_LEN:
        raise ConfigError(
            "SESSION_SECRET must be set and at least "
            f"{MIN_SECRET_LEN} characters long."
        )

    return Settings(
        session_secret=secret,
        database_url=env.get("DATABASE_URL") or DEFAULT_DATABASE_URL,
        session_cookie_secure=_as_bool(
            env.get("SESSION_COOKIE_SECURE"), default=False
        ),
        session_cookie_name=env.get("SESSION_COOKIE_NAME")
        or DEFAULT_SESSION_COOKIE_NAME,
    )
