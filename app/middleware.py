"""ASGI middleware that caps the total request body for the upload endpoint.

This is the REAL streaming size guard. Starlette's multipart max_part_size does
NOT bound file parts (it only checks non-file fields; file parts spool to a temp
file unchecked), so without this an attacker could stream an arbitrarily large
file to a temp file before the handler could reject it.

We enforce the cap two ways, both BEFORE the body is fully received:
- reject immediately if a Content-Length header already exceeds the cap;
- otherwise count bytes as they arrive from receive() and raise HTTPException 413
  the moment the running total exceeds the cap. The exception propagates through
  Starlette's ExceptionMiddleware and becomes a clean 413 — the body is never
  fully buffered.

The cap is read from app.state.max_request_bytes at call time so tests can lower
it. Scoped to POST of the submission endpoint so other routes are unaffected.
"""

from __future__ import annotations

from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse

from app.uploads import MAX_REQUEST_BYTES

_TOO_LARGE_MESSAGE = "请求体过大"


def _is_upload_request(scope) -> bool:
    if scope["type"] != "http" or scope["method"] != "POST":
        return False
    path = scope.get("path", "")
    return path.startswith("/teams/") and path.endswith("/submissions")


class LimitUploadBodyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if not _is_upload_request(scope):
            return await self.app(scope, receive, send)

        max_bytes = getattr(
            scope["app"].state, "max_request_bytes", MAX_REQUEST_BYTES
        )

        for key, value in scope.get("headers", []):
            if key == b"content-length" and value.isdigit():
                if int(value) > max_bytes:
                    response = PlainTextResponse(
                        _TOO_LARGE_MESSAGE, status_code=413
                    )
                    return await response(scope, receive, send)

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    # Propagates via ExceptionMiddleware -> clean 413.
                    raise HTTPException(status_code=413, detail=_TOO_LARGE_MESSAGE)
            return message

        await self.app(scope, limited_receive, send)
