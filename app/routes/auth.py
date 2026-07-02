"""Login / logout routes. Server-rendered HTML, 简体中文 UI strings.

Pages are rendered through Jinja2Templates (TemplateResponse) with a context
dict — never served as raw template text. On failure we return one generic
message and never reveal whether the email exists (no user enumeration).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.deps import get_db
from app.security import authenticate
from app.templating import templates

router = APIRouter()

# Single generic credential-failure message (no enumeration).
INVALID_CREDENTIALS_MESSAGE = "邮箱或密码错误"

# Upper bounds checked BEFORE any password hashing, so an oversized field can't
# force argon2 to burn CPU on the unauthenticated login route (DoS lever).
# Generous limits: a real email is < 320 chars (RFC), a real password < 1024.
MAX_EMAIL_LEN = 320
MAX_PASSWORD_LEN = 1024


def _render_login(request: Request, *, error: str | None = None, status_code: int = 200):
    return templates.TemplateResponse(
        request, "login.html", {"error": error}, status_code=status_code
    )


@router.get("/login")
def login_form(request: Request):
    return _render_login(request)


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    # Reject oversized input before hashing. Same generic outcome as bad
    # credentials so it reveals nothing.
    if len(email) > MAX_EMAIL_LEN or len(password) > MAX_PASSWORD_LEN:
        return _render_login(
            request,
            error=INVALID_CREDENTIALS_MESSAGE,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user = authenticate(db, email, password)
    if user is None:
        return _render_login(
            request,
            error=INVALID_CREDENTIALS_MESSAGE,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Fresh session on successful auth (guards against session fixation), then
    # store only the user id. Role/identity are re-derived from the DB per
    # request — never trusted from the cookie payload.
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
