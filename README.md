# 学生项目管理系统 (Student Project Management System)

A server-rendered FastAPI web app for a teacher managing student project teams.
UI strings are in 简体中文. See [PROJECT_BRIEF.md](PROJECT_BRIEF.md) for the full
spec and phased build plan.

**Status:** Phases 1–3 complete.
- **Phase 1 (Foundation):** config, DB models, argon2 auth, signed session
  cookie, server-side role gating. Login works.
- **Phase 2 (Projects):** teachers create projects (draft→open→closed) and
  manage only their own; students browse/view only open projects; drafts are
  not disclosed to students. `/` bounces to the projects list or login.
- **Phase 3 (Teams & claiming):** students form a team on an open project (the
  claim), others join. The claim race is settled at the DB level by
  `UNIQUE(project_id, slot_no)` (backstop, backend-agnostic) plus a
  BEGIN-IMMEDIATE claim transaction on SQLite / `SELECT … FOR UPDATE` on
  Postgres. Guards: claim only while `open` and within `opens_at/closes_at`,
  `max_teams` respected, one team per project per user, no double-join. A DB
  lock timeout surfaces as 503, never a 500.

### Concurrency note

`slot_no` is a per-project, monotonic (`MAX+1`), never-reused claim token; team
capacity is enforced separately by `COUNT < max_teams`. This is safe under the
MVP (no team deletion). If a delete/re-add feature is ever added, the scheme
stays collision-free (`MAX+1` is always greater than every surviving row), but
revisit the comment on `Team.slot_no` in `app/models.py`.

The threaded tests prove the invariant on **SQLite**; the `UNIQUE(project_id,
slot_no)` constraint is what carries it to **Postgres** (the Postgres path is not
tested here).

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

The suite (69 tests) covers:

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
- **Phase 3 (19 + 2 render-guard pages):** claim happy-path + leader auto-join;
  role/auth gates (teacher→403, anon→401); guards for draft/closed/out-of-window
  /full/duplicate-name/missing (409/404) and empty name (400); join, double-join
  and second-team-per-project blocks; a DB-lock-timeout→503 mapping; and three
  real-thread tests — two service-level claim races (exactly one wins, capacity
  never exceeded) and one HTTP-contention test asserting no 500 escapes.
