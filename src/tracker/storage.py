"""Raw document storage. Files are saved exactly as downloaded, named by SHA-256."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    path: Path
    size: int
    content_type: str | None


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def guess_extension(content_type: str | None, url: str | None = None) -> str:
    if url:
        suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
        if suffix in {".pdf", ".html", ".htm", ".txt", ".csv", ".json", ".xml", ".xlsx"}:
            return suffix
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if ext:
            return ext
    return ".bin"


def save_raw(
    content: bytes,
    *,
    source: str,
    raw_dir: Path,
    ext: str,
    content_type: str | None = None,
) -> StoredFile:
    """Write content to raw_dir/source/<sha256><ext>. Idempotent: same bytes, same path."""
    digest = sha256_bytes(content)
    if not ext.startswith("."):
        ext = f".{ext}"
    dest = raw_dir / source / f"{digest}{ext}"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".partial-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
            os.replace(tmp, dest)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return StoredFile(sha256=digest, path=dest, size=len(content), content_type=content_type)
