"""Upload validation and streaming storage.

Security properties enforced here:
- Per-file size cap enforced WHILE streaming to disk (never buffer the whole
  file to then reject it); the total-request cap is enforced upstream by the
  ASGI middleware in app/middleware.py.
- Type allowlist by BOTH extension and magic bytes. The declared Content-Type is
  NOT trusted; the stored mime is the one detected from the file's magic bytes.
- Files are stored under a server-generated uuid name in the upload dir; the path
  is never built from the user's filename (path traversal). original_name is a
  sanitized display label only.
- Atomic per-submission storage: store_files() writes all files or, on ANY
  failure, deletes everything it wrote and raises — leaving zero on-disk trace.

ARCHIVE SAFETY BOUNDARY: we store and serve zip/docx bytes verbatim and NEVER
decompress or extract them anywhere. A zip bomb or a zip with internal traversal
paths is only dangerous if something extracts it. If a future feature ever
extracts uploaded archives, it MUST first add zip-bomb (decompressed-size cap)
and zip-traversal (entry-path sanitization) defenses.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB per file
MAX_REQUEST_BYTES = 60 * 1024 * 1024  # total request body (streaming cutoff)
MAX_FILES = 10
CHUNK_SIZE = 64 * 1024

# extension -> list of (magic prefix, canonical mime). Sniffed, not trusted.
_PK = b"PK\x03\x04"
_PK_EMPTY = b"PK\x05\x06"
_PK_SPANNED = b"PK\x07\x08"
_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
MAGIC: dict[str, list[tuple[bytes, str]]] = {
    "pdf": [(b"%PDF-", "application/pdf")],
    "png": [(b"\x89PNG\r\n\x1a\n", "image/png")],
    "jpg": [(b"\xff\xd8\xff", "image/jpeg")],
    "jpeg": [(b"\xff\xd8\xff", "image/jpeg")],
    "zip": [(_PK, "application/zip"), (_PK_EMPTY, "application/zip"),
            (_PK_SPANNED, "application/zip")],
    "docx": [(_PK, _DOCX_MIME), (_PK_EMPTY, _DOCX_MIME),
             (_PK_SPANNED, _DOCX_MIME)],
}
ALLOWED_EXTENSIONS = frozenset(MAGIC)


class UploadError(Exception):
    """A user-caused upload rejection (bad type/size/content). Maps to 4xx."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class StoredFile:
    stored_name: str
    original_name: str
    size_bytes: int
    mime: str
    sha256: str


def sanitize_original_name(name: str) -> str:
    """Reduce a client filename to a safe display label: strip any path
    components and characters that could inject into a Content-Disposition
    header (CR/LF, quotes, control chars). Never used to build a path."""
    name = (name or "").replace("\\", "/")
    name = name.rsplit("/", 1)[-1]  # last path segment only
    name = "".join(
        ch for ch in name if ord(ch) >= 32 and ch not in '"\\'
    ).strip()
    if not name:
        name = "file"
    return name[:255]


def extension_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _check_magic(ext: str, head: bytes) -> str:
    for sig, mime in MAGIC[ext]:
        if head.startswith(sig):
            return mime
    raise UploadError("文件内容与类型不符", status_code=415)


def _store_one(file, upload_dir: Path) -> StoredFile:
    """Stream one upload to disk under a generated name, enforcing size and
    magic mid-stream. On ANY failure the partial file is removed before raising.
    """
    original = sanitize_original_name(getattr(file, "filename", "") or "")
    ext = extension_of(original)
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadError("不支持的文件类型", status_code=415)

    stored_name = f"{uuid4().hex}.{ext}"
    dest = upload_dir / stored_name
    hasher = sha256()
    size = 0
    mime = None
    src = file.file  # binary, synchronous file-like (spooled temp on the server)
    try:
        with open(dest, "wb") as out:
            first = True
            while True:
                chunk = src.read(CHUNK_SIZE)
                if not chunk:
                    break
                if first:
                    # Validate content type from magic bytes before trusting it.
                    mime = _check_magic(ext, chunk)
                    first = False
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise UploadError("文件超过大小限制", status_code=413)
                hasher.update(chunk)
                out.write(chunk)
            if first:  # never read a byte
                raise UploadError("空文件", status_code=400)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise

    return StoredFile(stored_name, original, size, mime, hasher.hexdigest())


def store_files(files, upload_dir: Path) -> list[StoredFile]:
    """Store all files or none. On any failure, everything already written is
    deleted and UploadError is raised — the caller sees zero on-disk trace."""
    if not files:
        raise UploadError("请至少上传一个文件", status_code=400)
    if len(files) > MAX_FILES:
        raise UploadError("文件数量过多", status_code=400)

    stored: list[StoredFile] = []
    try:
        for f in files:
            stored.append(_store_one(f, upload_dir))
    except BaseException:
        delete_stored(upload_dir, stored)  # remove any fully-written earlier files
        raise
    return stored


def delete_stored(upload_dir: Path, stored: list[StoredFile]) -> None:
    for s in stored:
        (upload_dir / s.stored_name).unlink(missing_ok=True)
