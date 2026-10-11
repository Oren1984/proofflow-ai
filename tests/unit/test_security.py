"""Upload, filename and link validation (untrusted input)."""

from __future__ import annotations

import pytest

from app import security
from app.models import EvidenceType, ValidationError
from tests.factories import JPEG, PDF, PNG

LIMIT = 1024


@pytest.mark.parametrize("hostile, expected", [
    ("../../etc/passwd.png", "passwd.png"),
    ("..\\..\\windows\\system32\\evil.png", "evil.png"),
    ("/absolute/path/shot.png", "shot.png"),
    ("C:\\Users\\me\\shot.png", "shot.png"),
    ("sh;ot<script>.png", "sh_ot_script_.png"),
    ("  .hidden.png", "hidden.png"),
    ("report\x00.pdf", "report_.pdf"),
])
def test_sanitize_filename_strips_paths_and_unsafe_characters(hostile, expected):
    safe = security.sanitize_filename(hostile)
    assert safe == expected
    assert "/" not in safe and "\\" not in safe and ".." not in safe


@pytest.mark.parametrize("name", ["", "..", "/", "...", "\\"])
def test_sanitize_filename_rejects_names_with_nothing_usable(name):
    with pytest.raises(ValidationError):
        security.sanitize_filename(name)


def test_long_filenames_are_truncated_but_keep_their_extension():
    safe = security.sanitize_filename("a" * 500 + ".png")
    assert len(safe) <= security.MAX_FILENAME_LENGTH and safe.endswith(".png")


@pytest.mark.parametrize("name, content, expected", [
    ("shot.png", PNG, EvidenceType.SCREENSHOT),
    ("shot.JPG", JPEG, EvidenceType.SCREENSHOT),
    ("guide.pdf", PDF, EvidenceType.DOCUMENT),
    ("notes.md", b"# notes", EvidenceType.TEXT),
    ("results.json", b'{"suite": "x"}', EvidenceType.TEST_RESULT),
])
def test_allowed_uploads_map_to_an_evidence_type(name, content, expected):
    assert security.validate_upload(name, content, LIMIT)[2] is expected


@pytest.mark.parametrize("name", ["run.exe", "script.sh", "page.html", "archive.zip", "image.svg", "noextension",
                                  "shot.png.exe"])
def test_unsupported_file_types_are_rejected(name):
    with pytest.raises(ValidationError, match="not allowed"):
        security.validate_upload(name, b"content", LIMIT)


def test_oversized_upload_is_rejected():
    with pytest.raises(ValidationError, match="limit"):
        security.validate_upload("shot.png", PNG + b"x" * LIMIT, LIMIT)


def test_empty_upload_is_rejected():
    with pytest.raises(ValidationError, match="empty"):
        security.validate_upload("shot.png", b"", LIMIT)


@pytest.mark.parametrize("name, content", [
    ("shot.png", b"MZ\x90\x00 executable pretending to be an image"),
    ("shot.jpg", PNG),
    ("guide.pdf", b"%PDF-1.4 truncated without trailer"),
    ("guide.pdf", b"<html>not a pdf</html>"),
    ("notes.txt", b"binary\x00data"),
    ("notes.txt", b"\xff\xfe invalid utf-8 \xff"),
    ("results.json", b"{not json"),
    ("results.json", b"[1, 2, 3]"),
])
def test_content_that_does_not_match_the_extension_is_rejected(name, content):
    with pytest.raises(ValidationError):
        security.validate_upload(name, content, LIMIT)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "ftp://example.com/x", "example.com",
                                 "https://", "https://example.com/a b", "", "data:text/html,<script>"])
def test_only_http_links_are_recorded(url):
    with pytest.raises(ValidationError):
        security.validate_url(url)


def test_valid_link_is_returned_unchanged():
    assert security.validate_url(" https://example.com/review?id=1 ") == "https://example.com/review?id=1"


def test_clean_text_enforces_presence_and_length_and_drops_control_characters():
    assert security.clean_text("  hello\x07 world ", "Field", 50) == "hello world"
    with pytest.raises(ValidationError, match="required"):
        security.clean_text("   ", "Field", 50, required=True)
    with pytest.raises(ValidationError, match="at most"):
        security.clean_text("x" * 51, "Field", 50)


def test_sha256_matches_known_vector():
    assert security.sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
