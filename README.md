# 学生项目管理系统 · Student Project Management System

一句话：教师发布项目，学生组队认领、提交成果，并按积分在排行榜上排名的团队项目管理系统。

One line: a teacher-run system where students form teams, claim published projects, submit deliverables, and are ranked on a points leaderboard.

## Highlights

- Concurrency-safe team claiming: the "last open slot" race is settled by a DB `UNIQUE(project_id, slot_no)` backstop plus `SELECT … FOR UPDATE` (Postgres) / `BEGIN IMMEDIATE` (SQLite); proven by real threaded tests (exactly one winner, capacity never exceeded) and green on both SQLite and PostgreSQL 16.4.
- Hardened file uploads: request body capped mid-stream (413 before buffering), per-file 20 MB streaming cutoff, extension + magic-byte allowlist (declared MIME not trusted), uuid storage names in a non-served dir, and authorization-gated streaming downloads (forced attachment + `nosniff`).
- Server-side authorization audited across phase seams: role + project-ownership + team-membership checks, no cross-team/cross-project IDOR; the posture is written up in [SECURITY.md](SECURITY.md).
- Regression guards baked into the suite: a render guard (every GET HTML page renders, no raw template markup leaks) and a no-GET-mutation guard (no GET route performs a write — the invariant the CSRF defense relies on).

## Tech stack

- Python 3.11+ · FastAPI 0.139 · SQLAlchemy 2.0 · Jinja2 3.1 (server-rendered, no JS build)
- SQLite (default) / PostgreSQL 16.4 · argon2-cffi 25.1 password hashing · signed httponly SameSite=Lax session cookie
- Tests: pytest 9.1 + FastAPI `TestClient` (httpx)

## Run locally

```bash
python -m venv .venv
source .venv/Scripts/activate          # Windows Git Bash; macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                    # placeholders work out of the box (SESSION_SECRET is preset)
python -m seed                          # seeds 1 teacher + 2 students from .env
uvicorn app.main:create_app --factory --reload
```

Open http://127.0.0.1:8000/login and sign in as `teacher@example.com` with the password from your `.env`.

## Tests

```bash
pytest                                  # 125 tests, SQLite
```

Against Postgres: `TEST_DATABASE_URL=postgresql+psycopg://user@host:5432/db pytest`

## Security

See [SECURITY.md](SECURITY.md): the CSRF posture (SameSite=Lax + the test-guarded "no GET mutates" invariant), the `SESSION_COOKIE_SECURE` production requirement, and the authorization model.

## Known limitations

- No campus SSO in the MVP — login is local email + password, kept behind a single `authenticate()` seam so an SSO adapter can replace it without touching routes.
- A hard crash between an upload's file write and its DB commit can orphan a file on disk; the fix is a periodic GC sweep (files with no DB row), not built.
