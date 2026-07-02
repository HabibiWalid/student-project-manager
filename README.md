# 学生项目管理系统 (Student Project Management System)

A server-rendered FastAPI web app for a teacher managing student project teams.
UI strings are in 简体中文. See [PROJECT_BRIEF.md](PROJECT_BRIEF.md) for the full
spec and phased build plan.

**Status:** Phases 1–2 complete.
- **Phase 1 (Foundation):** config, DB models, argon2 auth, signed session
  cookie, server-side role gating. Login works.
- **Phase 2 (Projects):** teachers create projects (draft→open→closed) and
  manage only their own; students browse/view only open projects; drafts are
  not disclosed to students. `/` bounces to the projects list or login.

## Stack

- Python 3.11+ (developed on 3.14), FastAPI, SQLAlchemy 2.x (SQLite for the MVP)
- Jinja2 server-rendered templates (no JS build step)
- Passwords hashed with argon2 (`argon2-cffi`)
- Session via a signed, httponly, samesite cookie (Starlette `SessionMiddleware`)

## Setup

```bash
python -m venv .venv
# Windows (Git Bash):
source .venv/Scripts/activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements-dev.txt   # runtime + test deps
cp .env.example .env                  # then edit .env with real values
```

### Required environment variables

All config comes from the environment (never hardcoded). See `.env.example`.

| Variable | Required | Purpose |
|---|---|---|
| `SESSION_SECRET` | **yes** (≥ 32 chars) | Signs the session cookie. App refuses to start without it. |
| `DATABASE_URL` | no | SQLAlchemy URL. Defaults to `sqlite:///./app.db`. |
| `SESSION_COOKIE_SECURE` | no | Set `true` in production (HTTPS) to mark the cookie Secure. |
| `SESSION_COOKIE_NAME` | no | Override the cookie name. |
| `SEED_TEACHER_EMAIL` / `SEED_TEACHER_PASSWORD` | for seeding | Teacher account. |
| `SEED_STUDENT1_*`, `SEED_STUDENT2_*` | for seeding | Two student accounts. |

Generate a secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Seed users

There is no public registration route (single-teacher classroom tool). Create
the initial accounts with the idempotent seed script. Passwords come only from
the environment — seeding fails loudly if any is missing.

```bash
python -m seed
```

## Run

```bash
uvicorn app.main:create_app --factory --reload
```

Then open http://127.0.0.1:8000/login and log in with a seeded account.

## Tests

```bash
pytest
```

The suite (43 tests) covers:

- **Phase 1 (25):** config fail-fast, password hashing & `verify` fail-closed
  behavior, `authenticate()` (success / wrong password / unknown email / email
  normalization), login & logout HTTP flow, anti-enumeration (unknown email and
  wrong password return identical responses), oversized-input rejection,
  template rendering (no raw Jinja markup leaks; generic 简体中文 error), and
  server-side role gating (401 unauthenticated, 403 student, 200 teacher).
- **Phase 2 (18):** entry redirect, teacher-only creation as draft, input
  validation (empty title / bad max_teams / close-before-open → 400), status
  transitions (draft→open→closed; invalid → 409), ownership enforcement
  (non-owner teacher → 403), and status-based visibility (students see only open
  projects; drafts return 404).
