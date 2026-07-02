# PROJECT_BRIEF — 学生项目管理系统 (Student Project Management System)

> This file is the source of truth for Claude Code. Read it fully before writing any code.
> Build in the phase order given. Do not build later phases early.

---

## 0. Standing rules (apply to EVERY change, no exceptions)

Working code is not the bar — **correct, secure, maintainable, tested** code is.

1. **ORIENT** — Before each task, state the security-sensitive surfaces it touches
   (auth, data access, external input, file I/O, concurrency). Match existing conventions.
2. **PLAN** — Restate the task, name the trust boundaries and data flows, list edge
   cases and failure modes, outline the approach. Pause and ask if a change is large or ambiguous.
3. **TESTS** — Cover happy path, edge cases, and failure paths. Never weaken a test to
   make code pass — fix the code.
4. **IMPLEMENT** — Simplest solution that fully solves the problem. No speculative
   abstraction, dead code, or unrequested features. Handle every error explicitly and **fail closed**.
5. **SECURITY GATE** — Before "done", confirm: no hardcoded/client-exposed secrets;
   authorization enforced server-side, never trusting client-supplied identity or role;
   all external input validated at the boundary; parameterized queries only (no string-built
   SQL/paths); safe file handling; outbound calls have timeouts; passwords via bcrypt/argon2;
   secure randomness; every dependency verified to actually exist.
6. **SELF-AUDIT** — Re-read your code as an attacker: bad input, concurrency, empty/huge/null
   values. Report real gaps, then fix them.

If the task turns out riskier or bigger than it looked, **stop and say so** instead of pushing through.

---

## 1. What we're building

A web app for a teacher who manages multiple project teams:

- Teacher **publishes projects**; all students can browse open ones and participate.
- Students **form teams** and **claim** a project.
- Teams **submit deliverables** (files + a note) to track completion progress.
- Teacher **awards points**; there is a **team leaderboard** ranking.

**Scope: MVP.** Prioritize a small, fully-correct, fully-tested core over breadth.

---

## 2. Stack (fixed)

- **Language/runtime:** Python 3.11+
- **Web:** FastAPI
- **DB:** SQLite via SQLAlchemy 2.x (ORM). Written so a later swap to Postgres needs no query rewrites.
- **Templates:** Jinja2, server-rendered. No JS build step. **All user-facing UI strings in Chinese (简体中文).**
- **Auth:** Local email + password. Passwords hashed with **argon2** (`argon2-cffi`) or **bcrypt** (`passlib[bcrypt]`). Session via a signed, httponly, samesite cookie.
- **Tests:** pytest + httpx `AsyncClient` (or FastAPI `TestClient`). Each phase ships with its tests.
- **Config:** all secrets (session signing key, etc.) from environment variables via a settings module. Never hardcoded. Ship a `.env.example`, never a real `.env`.

Verify each package exists on PyPI before adding it. Pin versions in `requirements.txt`.

**Auth note:** real campus SSO is out of scope for the MVP. Keep the login logic behind a
single `authenticate(credentials)` function so a future SSO adapter can replace it without
touching routes. Do **not** build a mock-SSO framework now — that's speculative. Just the one clean function.

---

## 3. Roles & trust boundaries

Two roles: `teacher`, `student`. **Role lives in the DB and is read server-side from the session's user record — never from a form field, header, or cookie the client can set.**

| Action | Who |
|---|---|
| Create / open / close a project, award points | `teacher` only |
| Browse open projects | any authenticated user |
| Create a team, join a team, claim a project | `student` only |
| Submit deliverables for a team | only a **member of that team** |
| View a team's submissions/files | that team's members, or any teacher |
| View leaderboard | any authenticated user |

**Core access-control rule:** every team-scoped operation must verify the current user is a
member of the target team (or is a teacher) **inside the server**, by querying membership —
never by trusting a `team_id` the client passed as proof of belonging.

---

## 4. Data model

```
User(id, email UNIQUE, password_hash, name, role['teacher'|'student'], created_at)
Project(id, teacher_id FK->User, title, description, status['draft'|'open'|'closed'],
        max_teams (nullable), opens_at (nullable), closes_at (nullable), created_at)
Team(id, project_id FK->Project, name, leader_id FK->User, created_at)
    UNIQUE(project_id, name)
TeamMember(team_id FK->Team, user_id FK->User, joined_at)
    PRIMARY KEY(team_id, user_id)          -- a user can't join the same team twice
Submission(id, team_id FK, project_id FK, note, status['submitted'], submitted_at)
SubmissionFile(id, submission_id FK, stored_name, original_name, size_bytes,
               mime, sha256, created_at)
Score(id, team_id FK, project_id FK, points INT, awarded_by FK->User,
      reason, created_at)
```

**Leaderboard is a derived query** — `SUM(Score.points)` grouped by team, ordered descending.
Do NOT keep a mutable `total_points` counter on Team (drift + race bugs).

---

## 5. The four things most likely to become security holes — handle explicitly

1. **Role escalation.** Gate teacher-only routes with a server-side dependency that loads the
   user from the session and checks `role == 'teacher'`. Test that a student calling a
   teacher route gets 403.

2. **Cross-team data access.** A student must not read or write another team's submissions/files
   by changing an ID in the URL. Every such route checks membership server-side. Test the
   "student A tries to access team B" case → 403/404.

3. **The claim race.** Two teams claiming the last slot of a project at the same time.
   Enforce with the DB, not read-then-write: use a transaction, and rely on a constraint/atomic
   update so only one claim can win. Test with concurrent claims → exactly one succeeds.
   Also enforce project `status == 'open'` and within `opens_at`/`closes_at` at claim time.

4. **File upload (biggest surface).** On upload:
   - Enforce a **max size** (e.g. 20 MB) — reject streams that exceed it, don't buffer unbounded.
   - **Allowlist** extensions + MIME (e.g. pdf, zip, docx, png, jpg). Reject everything else.
   - Store under a **generated name** (uuid) in a non-executable uploads dir. NEVER build the
     path from the user's filename (path traversal). Keep `original_name` only as a DB label.
   - Compute and store `sha256`.
   - Serve downloads ONLY through an **authorization-checked route** that streams the file after
     verifying the requester may see it — never expose the uploads dir as a static mount.
   - Test: oversized file rejected; disallowed type rejected; `../` filename can't escape;
     non-member download → 403.

---

## 6. Build order — one vertical slice at a time, each runnable + tested

Do NOT start a phase before the previous one is done, tested, and green.

**Phase 1 — Foundation**
FastAPI app skeleton, settings-from-env, SQLAlchemy models + migrations/create_all, the
`authenticate()` login function, registration (or seed users), session cookie, and the
server-side `current_user` / `require_teacher` dependencies. App runs; login works.
Tests: password hashing, login success/failure, role-gating (student blocked from a teacher route).

**Phase 2 — Projects**
Teacher creates a project (draft→open→closed). Students list open projects and view one.
Tests: only teacher can create; students can't; status filtering correct.

**Phase 3 — Teams & claiming**
Student creates a team, others join; team claims an open project. Includes the **claim-race guard**
and open-window/max-teams checks.
Tests: concurrent claim → exactly one wins; closed/full project → claim rejected; double-join blocked.

**Phase 4 — Submissions & files**
Team members submit a note + files. Secure upload + authz-checked download per Section 5.4.
Tests: all the upload/download cases in 5.4, plus non-member cannot submit for a team.

**Phase 5 — Scoring & leaderboard**
Teacher awards points (with reason) to a team on a project. Leaderboard = derived ranked query.
Tests: only teacher can award; leaderboard totals + ordering correct; negative/zero points handled per rule.

---

## 7. Deliverables per phase

- Code following the standing rules.
- Passing pytest suite for that phase.
- A short note: what was built, which security checks were enforced, and any gap you found in self-audit.
- `README` kept current: how to run (`uvicorn ...`), how to run tests, required env vars.

Start with **Phase 1**. Before writing code, do the ORIENT + PLAN steps out loud and wait for my go if anything is ambiguous.
