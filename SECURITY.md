# Security posture

This documents the deliberate security decisions for the MVP so they aren't
silently undone by a future change. It complements the per-phase notes in the
commit history and the checks enforced in the test suite.

## Authentication & sessions

- Passwords are hashed with **argon2id** (`argon2-cffi`); `verify` fails closed
  on a bad or malformed hash. Unknown-email logins still run a dummy verify to
  avoid a timing/enumeration side channel.
- Identity and role are derived **server-side** on every request from the signed
  session cookie's `user_id` by loading the `User` row from the DB. A role, user
  id, or team ownership supplied by the client is never trusted.
- The session cookie is signed (Starlette `SessionMiddleware`), **httponly**, and
  **SameSite=Lax**.

## CSRF

**The app has no CSRF tokens. Its CSRF defense is the `SameSite=Lax` session
cookie**, and that defense is sufficient *only while two invariants hold*:

1. **No GET route mutates state.** Every state change is a `POST`. Browsers send
   a `SameSite=Lax` cookie on top-level GET navigations, so if a GET ever wrote
   state, a cross-site `<img>`/link could trigger it with the victim's cookie.
2. **The session cookie stays `SameSite=Lax` (or stricter).** Lax withholds the
   cookie on cross-site `POST` (and cross-site subrequests), so a cross-site
   auto-submitting form cannot carry the victim's session → the request is
   unauthenticated → rejected (401). Setting `SameSite=None` would break this.

Both invariants are load-bearing and each is one careless commit from silently
disabling CSRF protection. Invariant (1) is enforced by
`tests/test_route_safety.py::test_no_get_route_mutates_state`, which enumerates
every GET route and asserts its handler performs no ORM writes. Invariant (2)
lives in `app/main.create_app` (`same_site="lax"`).

**If either invariant must change** (a GET that writes, or `SameSite=None` for a
cross-site use case), add per-request CSRF tokens on state-changing routes first.

## Transport / cookie `Secure` flag

The session cookie is marked **`Secure` only when `SESSION_COOKIE_SECURE=true`**
(via `https_only` on the session middleware). This defaults to `false` for local
HTTP development. **In production (served over HTTPS) `SESSION_COOKIE_SECURE`
MUST be set to `true`** so the cookie is never sent over plaintext. This is an
operator responsibility and is documented in `.env.example`.

## Authorization model

- **Roles** (`teacher`, `student`) come from the DB user record. Teacher-only and
  student-only routes are gated by server-side dependencies (`require_teacher`,
  `require_student`/`require_student_for_write`).
- **Ownership (least privilege) on mutations:** a teacher may only open/close a
  project, and award/void scores on it, **if they own that project**
  (`project.teacher_id == user.id`). Non-owner teacher → 403.
- **Membership gating:** only a member of a team may submit for it. Cross-team
  access (submitting for, or downloading a file of, a team you're not in) → 403.
- **Draft non-disclosure:** students get 404 (not 403) for projects/leaderboards
  they may not see, so draft existence isn't leaked.

### Deliberate asymmetry: any-teacher read vs. owner-only write

Viewing/downloading a team's submissions is allowed for **that team's members or
any teacher** (per the project brief's access table), while **awarding/voiding
scores is restricted to the project owner**. This is intentional: reads across a
class are expected of any teacher, but the money-like scoring state is held to
least privilege. It is a conscious decision, not an oversight.

## File uploads (recap)

Enforced in `app/uploads.py` + `app/middleware.py`: total request body capped
mid-stream (413), per-file 20 MB streaming cutoff, extension **and** magic-byte
allowlist (declared MIME not trusted), uuid storage names in a non-served dir,
sha256, atomic all-or-nothing storage, and authz-checked streaming download with
forced `attachment` + `nosniff` + sanitized filename. **Uploaded archives are
never decompressed** (guarded by a test); extraction would first require
zip-bomb + zip-traversal defenses.

## Known operational limitation

A hard crash between writing an uploaded file and committing its DB row can leave
an orphaned file on disk (see README "Known limitations"). A periodic GC sweep is
the fix if it ever matters; not built.
