"""Unit tests for upload internals: sanitization, streaming store atomicity,
and the ASGI body-size middleware's mid-stream cutoff."""

import io
from types import SimpleNamespace

import anyio
import pytest
from starlette.exceptions import HTTPException

from app import uploads
from app.middleware import LimitUploadBodyMiddleware

PNG = b"\x89PNG\r\n\x1a\n" + b"payload-bytes"
EXE = b"MZ\x90\x00" + b"\x00" * 16  # PE header magic


class _Fake:
    """Minimal UploadFile stand-in: has .filename and a binary .file."""

    def __init__(self, filename, content):
        self.filename = filename
        self.file = io.BytesIO(content)


# --- sanitize ---------------------------------------------------------------


def test_sanitize_strips_path_components():
    assert uploads.sanitize_original_name("../../etc/passwd.png") == "passwd.png"
    assert uploads.sanitize_original_name("..\\..\\evil.docx") == "evil.docx"


def test_sanitize_strips_header_injection_chars():
    out = uploads.sanitize_original_name('a"b\r\nSet-Cookie: x.png')
    assert '"' not in out
    assert "\r" not in out and "\n" not in out


def test_sanitize_empty_becomes_placeholder():
    assert uploads.sanitize_original_name("") == "file"
    assert uploads.sanitize_original_name("   ") == "file"


# --- streaming store atomicity ----------------------------------------------


def test_store_files_happy(tmp_path):
    stored = uploads.store_files([_Fake("a.png", PNG)], tmp_path)
    assert len(stored) == 1
    assert stored[0].mime == "image/png"
    assert (tmp_path / stored[0].stored_name).read_bytes() == PNG


def test_store_files_partial_failure_leaves_zero_trace(tmp_path):
    # file 1 valid, file 2 magic-mismatch -> whole batch rejected, nothing left.
    files = [_Fake("a.png", PNG), _Fake("b.png", EXE)]
    with pytest.raises(uploads.UploadError):
        uploads.store_files(files, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_store_files_rejects_bad_extension(tmp_path):
    with pytest.raises(uploads.UploadError):
        uploads.store_files([_Fake("evil.exe", EXE)], tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_store_files_enforces_per_file_size(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_FILE_BYTES", 4)
    with pytest.raises(uploads.UploadError):
        uploads.store_files([_Fake("a.png", PNG)], tmp_path)
    assert list(tmp_path.iterdir()) == []  # partial deleted


def test_store_files_requires_at_least_one(tmp_path):
    with pytest.raises(uploads.UploadError):
        uploads.store_files([], tmp_path)


# --- middleware: mid-stream cutoff ------------------------------------------


def _scope(max_bytes, headers=None):
    return {
        "type": "http",
        "method": "POST",
        "path": "/teams/1/submissions",
        "headers": headers or [],
        "app": SimpleNamespace(state=SimpleNamespace(max_request_bytes=max_bytes)),
    }


def test_middleware_aborts_receive_before_consuming_whole_body():
    chunks = [
        {"type": "http.request", "body": b"x" * 60, "more_body": True},
        {"type": "http.request", "body": b"x" * 60, "more_body": True},
        {"type": "http.request", "body": b"x" * 60, "more_body": False},
    ]
    consumed = {"n": 0}

    async def receive():
        i = consumed["n"]
        consumed["n"] += 1
        return chunks[i]

    async def downstream(scope, receive, send):
        while True:
            msg = await receive()
            if not msg.get("more_body"):
                break

    async def send(msg):
        pass

    mw = LimitUploadBodyMiddleware(downstream)
    with pytest.raises(HTTPException) as exc:
        anyio.run(mw, _scope(100), receive, send)
    assert exc.value.status_code == 413
    # Proof it cut off mid-stream: it did NOT read all three chunks.
    assert consumed["n"] < len(chunks)


def test_middleware_rejects_oversized_content_length_early():
    sent = []

    async def downstream(scope, receive, send):
        raise AssertionError("app must not be called when Content-Length too big")

    async def receive():
        raise AssertionError("receive must not be called")

    async def send(msg):
        sent.append(msg)

    mw = LimitUploadBodyMiddleware(downstream)
    scope = _scope(100, headers=[(b"content-length", b"1000000")])
    anyio.run(mw, scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413


def test_never_decompresses_uploaded_archives():
    """Archive-safety boundary: the app must not extract/decompress uploads.
    If this ever fails, zip-bomb + zip-traversal defenses are required first."""
    import pathlib

    app_dir = pathlib.Path(uploads.__file__).parent
    offenders = []
    for py in app_dir.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for needle in ("import zipfile", "import tarfile", "ZipFile", "extractall", "gzip.open"):
            if needle in text:
                offenders.append((py.name, needle))
    assert offenders == [], offenders
