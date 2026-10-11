"""Small builders for synthetic test data."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.models import Criterion, Evidence, EvidenceType, to_iso
from app.security import sha256_hex

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-image-bytes"
JPEG = b"\xff\xd8\xff\xe0" + b"synthetic-image-bytes"
PDF = b"%PDF-1.4\nsynthetic document\n%%EOF\n"
MAX_BYTES = 1024 * 1024


def suite_json(failed: int = 0, total: int = 10, executed_at: datetime | None = None, **overrides: object) -> bytes:
    record = {"suite": "regression", "total": total, "passed": total - failed, "failed": failed, "skipped": 0,
              "executed_at": to_iso(executed_at or NOW - timedelta(hours=1))}
    record.update(overrides)
    return json.dumps(record).encode("utf-8")


def criterion(**overrides: object) -> Criterion:
    values: dict = {"id": "CRT-1", "commitment_id": "CMT-1", "text": "Dashboard loads",
                    "allowed_types": [EvidenceType.SCREENSHOT]}
    values.update(overrides)
    return Criterion(**values)


def evidence(content: bytes | None = PNG, **overrides: object) -> Evidence:
    values: dict = {
        "id": "EVD-1", "project_id": "PRJ-1", "commitment_id": "CMT-1", "criterion_id": "CRT-1",
        "type": EvidenceType.SCREENSHOT, "description": "home page", "submitted_by": "tester",
        "submitted_at": to_iso(NOW - timedelta(hours=2)), "sha256": sha256_hex(content or b""),
        "original_name": "shot.png", "stored_name": "PRJ-1/EVD-1.png", "size_bytes": len(content or b""),
    }
    values.update(overrides)
    return Evidence(**values)
