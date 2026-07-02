"""Shared Jinja2 template environment.

Kept in its own module so both the app factory and route modules can import it
without a circular dependency. Autoescaping is on by default in Jinja2Templates,
which mitigates XSS when rendering user-influenced context values.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
