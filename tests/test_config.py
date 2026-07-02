"""Settings load from env and fail closed on missing/weak secrets."""

import pytest

from app.config import (
    DEFAULT_DATABASE_URL,
    DEFAULT_SESSION_COOKIE_NAME,
    MIN_SECRET_LEN,
    ConfigError,
    load_settings,
)

_GOOD_SECRET = "x" * MIN_SECRET_LEN


def test_missing_secret_raises():
    with pytest.raises(ConfigError):
        load_settings({})


def test_short_secret_raises():
    with pytest.raises(ConfigError):
        load_settings({"SESSION_SECRET": "too-short"})


def test_valid_secret_with_defaults():
    s = load_settings({"SESSION_SECRET": _GOOD_SECRET})
    assert s.session_secret == _GOOD_SECRET
    assert s.database_url == DEFAULT_DATABASE_URL
    assert s.session_cookie_secure is False
    assert s.session_cookie_name == DEFAULT_SESSION_COOKIE_NAME


def test_overrides_from_env():
    s = load_settings(
        {
            "SESSION_SECRET": _GOOD_SECRET,
            "DATABASE_URL": "sqlite:///./other.db",
            "SESSION_COOKIE_SECURE": "true",
            "SESSION_COOKIE_NAME": "custom",
        }
    )
    assert s.database_url == "sqlite:///./other.db"
    assert s.session_cookie_secure is True
    assert s.session_cookie_name == "custom"
