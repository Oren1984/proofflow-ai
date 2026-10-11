"""Validation of untrusted uploads, filenames and link references."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from app.models import EvidenceType, ValidationError

ALLOWED_EXTENSIONS: dict[str, EvidenceType] = {
    ".png": EvidenceType.SCREENSHOT,
    ".jpg": EvidenceType.SCREENSHOT,
    ".jpeg": EvidenceType.SCREENSHOT,
    ".pdf": EvidenceType.DOCUMENT,
    ".txt": EvidenceType.TEXT,
    ".md": EvidenceType.TEXT,
    ".json": EvidenceType.TEST_RESULT,
}

MAX_FILENAME_LENGTH = 120
MAX_URL_LENGTH = 2000
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]")


def clean_text(value: str | None, field: str, max_length: int, *, required: bool = False) -> str:
    """Normalize free-text input: drop control characters, trim, enforce presence and length."""
    text = "".join(ch for ch in (value or "") if ch in "\n\t" or ord(ch) >= 32).strip()
    if required and not text:
        raise ValidationError(f"{field} is required.")
    if len(text) > max_length:
        raise ValidationError(f"{field} must be at most {max_length} characters.")
    return text


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sanitize_filename(name: str) -> str:
    """Reduce a client-supplied filename to a safe display name (never used as a path)."""
    base = PurePosixPath(name.replace("\\", "/")).name
    base = _UNSAFE_CHARS.sub("_", base).strip(" .")
    if not base:
        raise ValidationError("The file name is empty or contains no usable characters.")
    if len(base) > MAX_FILENAME_LENGTH:
        stem, dot, ext = base.rpartition(".")
        base = f"{stem[: MAX_FILENAME_LENGTH - len(ext) - 1]}{dot}{ext}" if dot else base[:MAX_FILENAME_LENGTH]
    return base


def extension_of(filename: str) -> str:
    return PurePosixPath(filename).suffix.lower()


def check_signature(extension: str, content: bytes) -> str | None:
    """Return a problem description if the content does not match its extension."""
    if extension == ".png":
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "Content is not a PNG image (signature mismatch)."
    elif extension in (".jpg", ".jpeg"):
        if not content.startswith(b"\xff\xd8\xff"):
            return "Content is not a JPEG image (signature mismatch)."
    elif extension == ".pdf":
        if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-1024:]:
            return "Content is not a complete PDF document (signature mismatch)."
    elif extension in (".txt", ".md", ".json"):
        if b"\x00" in content:
            return "Text content contains binary data."
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return "Text content is not valid UTF-8."
        if extension == ".json":
            try:
                parsed = json.loads(text)
            except ValueError:
                return "Content is not valid JSON."
            if not isinstance(parsed, dict):
                return "A structured test result must be a JSON object."
    else:
        return f"File type '{extension or 'unknown'}' is not allowed."
    return None


def validate_upload(filename: str, content: bytes, max_bytes: int) -> tuple[str, str, EvidenceType]:
    """Validate an uploaded file; return (safe display name, extension, evidence type)."""
    safe_name = sanitize_filename(filename)
    extension = extension_of(safe_name)
    if extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise ValidationError(f"File type '{extension or 'none'}' is not allowed. Allowed types: {allowed}.")
    if not content:
        raise ValidationError("The uploaded file is empty.")
    if len(content) > max_bytes:
        raise ValidationError(
            f"The file is {len(content) / 1_048_576:.1f} MB; the limit is {max_bytes / 1_048_576:.1f} MB."
        )
    problem = check_signature(extension, content)
    if problem:
        raise ValidationError(problem)
    return safe_name, extension, ALLOWED_EXTENSIONS[extension]


def validate_url(url: str) -> str:
    """Validate a link reference. The URL is recorded only - it is never fetched."""
    url = url.strip()
    if not url or len(url) > MAX_URL_LENGTH or any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise ValidationError("Enter a single http(s) URL without spaces.")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValidationError("Only http:// and https:// links can be recorded.")
    return url
