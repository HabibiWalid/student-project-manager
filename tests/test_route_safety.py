"""Guard: no GET route mutates state.

This protects the app's CSRF posture, which relies on SameSite=Lax cookies and
therefore on the invariant that *reads* (GET) never change state — only POSTs do,
and cross-site POSTs don't carry the Lax cookie. If a GET handler ever performed
a write, a cross-site <img>/link could trigger it with the victim's cookie. See
SECURITY.md.

The check inspects each GET handler's source for ORM write calls. It is
handler-scoped (the documented invariant is "GET handlers don't write"); it does
not chase indirect writes through helpers, but no GET handler in this app calls a
mutating helper.
"""

import inspect

# ORM write sinks used anywhere in this codebase. Reads use db.get / select().
FORBIDDEN = (".add(", ".add_all(", ".commit(", ".delete(", ".flush(", ".merge(")


def _iter_routes(app):
    """Yield every concrete route. FastAPI 0.139 stores included routers as
    _IncludedRouter wrappers rather than flattening them into app.routes, so we
    descend into their original_router.routes."""
    for route in app.routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from getattr(included, "routes", [])
        else:
            yield route


def _get_handlers(app):
    for route in _iter_routes(app):
        methods = getattr(route, "methods", None) or set()
        endpoint = getattr(route, "endpoint", None)
        if "GET" not in methods or endpoint is None:
            continue
        # Only our own route handlers (skips /docs, /openapi.json, test probes).
        if not getattr(endpoint, "__module__", "").startswith("app.routes"):
            continue
        yield route.path, endpoint


def test_no_get_route_mutates_state(app):
    offenders = []
    audited = 0
    for path, endpoint in _get_handlers(app):
        audited += 1
        src = inspect.getsource(endpoint)
        for tok in FORBIDDEN:
            if tok in src:
                offenders.append((path, endpoint.__name__, tok))
    assert offenders == [], offenders
    assert audited >= 5, f"expected to audit the GET routes, saw {audited}"

    # Self-check: the detector must actually fire on a known writer, so this test
    # can't silently become a vacuous pass (e.g. wrong tokens).
    from app.routes import scores

    assert any(tok in inspect.getsource(scores.award_points) for tok in FORBIDDEN)
