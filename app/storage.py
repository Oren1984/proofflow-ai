"""Local persistence: a small SQLite repository and a confined evidence file store."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from app.models import (
    AuditEntry,
    Commitment,
    Criterion,
    CustomerAcceptance,
    Evidence,
    EvidenceType,
    Project,
    ProjectStatus,
    ReportedStatus,
    ReviewDecision,
    ValidationError,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    customer TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    delivery_date TEXT,
    status TEXT NOT NULL,
    acceptance TEXT NOT NULL,
    acceptance_by TEXT,
    acceptance_at TEXT,
    acceptance_note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commitments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    expected_deliverable TEXT NOT NULL DEFAULT '',
    reported_status TEXT NOT NULL,
    due_date TEXT,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS criteria (
    id TEXT PRIMARY KEY,
    commitment_id TEXT NOT NULL REFERENCES commitments(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    allowed_types TEXT NOT NULL,
    min_evidence INTEGER NOT NULL CHECK (min_evidence >= 1),
    requires_passing_test INTEGER NOT NULL,
    requires_human_review INTEGER NOT NULL,
    max_age_days INTEGER,
    decision TEXT NOT NULL,
    decision_by TEXT,
    decision_at TEXT,
    decision_reason TEXT,
    decision_fingerprint TEXT,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    commitment_id TEXT NOT NULL REFERENCES commitments(id) ON DELETE CASCADE,
    criterion_id TEXT NOT NULL REFERENCES criteria(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    description TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    original_name TEXT,
    stored_name TEXT,
    url TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    validation_status TEXT,
    validation_message TEXT,
    UNIQUE (criterion_id, sha256)
);
CREATE TABLE IF NOT EXISTS audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    previous_state TEXT,
    new_state TEXT,
    actor TEXT NOT NULL,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS verification_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    operation_id TEXT NOT NULL,
    run_at TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    rules_version TEXT NOT NULL,
    total INTEGER NOT NULL,
    verified INTEGER NOT NULL,
    readiness_pct REAL NOT NULL,
    counts TEXT NOT NULL
);
-- Local traceability only: history rows cannot be edited, and cannot be removed
-- while their project exists. This is not a tamper-proof ledger.
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
WHEN EXISTS (SELECT 1 FROM projects WHERE id = OLD.project_id)
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""


def _row_values(obj: object) -> dict[str, object]:
    values = asdict(obj)  # type: ignore[call-overload]
    for key, value in values.items():
        if isinstance(value, list):
            values[key] = json.dumps([str(v) for v in value])
        elif isinstance(value, bool):
            values[key] = int(value)
        elif isinstance(value, str):
            values[key] = str(value)
    return values


def _project(row: sqlite3.Row) -> Project:
    data = dict(row)
    data["status"] = ProjectStatus(data["status"])
    data["acceptance"] = CustomerAcceptance(data["acceptance"])
    return Project(**data)


def _commitment(row: sqlite3.Row) -> Commitment:
    data = dict(row)
    data["reported_status"] = ReportedStatus(data["reported_status"])
    return Commitment(**data)


def _criterion(row: sqlite3.Row) -> Criterion:
    data = dict(row)
    data["allowed_types"] = [EvidenceType(t) for t in json.loads(data["allowed_types"])]
    data["requires_passing_test"] = bool(data["requires_passing_test"])
    data["requires_human_review"] = bool(data["requires_human_review"])
    data["decision"] = ReviewDecision(data["decision"])
    return Criterion(**data)


def _evidence(row: sqlite3.Row) -> Evidence:
    data = dict(row)
    data["type"] = EvidenceType(data["type"])
    return Evidence(**data)


class Repository:
    """Minimal persistence layer. All queries are parameterized."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _insert(self, table: str, obj: object) -> None:
        values = _row_values(obj)
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        try:
            with self._connect() as conn:
                conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", list(values.values()))
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"Duplicate or inconsistent record: {exc}") from exc

    def _update(self, table: str, obj: object) -> None:
        values = _row_values(obj)
        record_id = values.pop("id")
        assignments = ", ".join(f"{column} = ?" for column in values)
        try:
            with self._connect() as conn:
                conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", [*values.values(), record_id])
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"Duplicate or inconsistent record: {exc}") from exc

    def _fetch(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(sql, params).fetchall()

    def _delete(self, table: str, record_id: str) -> None:
        with self._connect() as conn:
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))

    def ping(self) -> bool:
        return self._fetch("SELECT 1")[0][0] == 1

    # Projects
    def insert_project(self, project: Project) -> None:
        self._insert("projects", project)

    def update_project(self, project: Project) -> None:
        self._update("projects", project)

    def get_project(self, project_id: str) -> Project:
        rows = self._fetch("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not rows:
            raise ValidationError(f"Project {project_id} does not exist.")
        return _project(rows[0])

    def list_projects(self) -> list[Project]:
        return [_project(r) for r in self._fetch("SELECT * FROM projects ORDER BY created_at, name")]

    def delete_project(self, project_id: str) -> None:
        """Remove a project, its records and its history (the retention boundary is the project)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            conn.execute("DELETE FROM audit_log WHERE project_id = ?", (project_id,))

    # Commitments
    def insert_commitment(self, commitment: Commitment) -> None:
        self._insert("commitments", commitment)

    def update_commitment(self, commitment: Commitment) -> None:
        self._update("commitments", commitment)

    def get_commitment(self, commitment_id: str) -> Commitment:
        rows = self._fetch("SELECT * FROM commitments WHERE id = ?", (commitment_id,))
        if not rows:
            raise ValidationError(f"Commitment {commitment_id} does not exist.")
        return _commitment(rows[0])

    def list_commitments(self, project_id: str) -> list[Commitment]:
        rows = self._fetch("SELECT * FROM commitments WHERE project_id = ? ORDER BY position, rowid", (project_id,))
        return [_commitment(r) for r in rows]

    def delete_commitment(self, commitment_id: str) -> None:
        self._delete("commitments", commitment_id)

    # Criteria
    def insert_criterion(self, criterion: Criterion) -> None:
        self._insert("criteria", criterion)

    def update_criterion(self, criterion: Criterion) -> None:
        self._update("criteria", criterion)

    def get_criterion(self, criterion_id: str) -> Criterion:
        rows = self._fetch("SELECT * FROM criteria WHERE id = ?", (criterion_id,))
        if not rows:
            raise ValidationError(f"Criterion {criterion_id} does not exist.")
        return _criterion(rows[0])

    def list_criteria(self, commitment_id: str) -> list[Criterion]:
        rows = self._fetch("SELECT * FROM criteria WHERE commitment_id = ? ORDER BY position, rowid", (commitment_id,))
        return [_criterion(r) for r in rows]

    def delete_criterion(self, criterion_id: str) -> None:
        self._delete("criteria", criterion_id)

    # Evidence
    def insert_evidence(self, evidence: Evidence) -> None:
        self._insert("evidence", evidence)

    def get_evidence(self, evidence_id: str) -> Evidence:
        rows = self._fetch("SELECT * FROM evidence WHERE id = ?", (evidence_id,))
        if not rows:
            raise ValidationError(f"Evidence {evidence_id} does not exist.")
        return _evidence(rows[0])

    def list_evidence(self, *, project_id: str | None = None, criterion_id: str | None = None) -> list[Evidence]:
        column, value = ("criterion_id", criterion_id) if criterion_id else ("project_id", project_id)
        rows = self._fetch(f"SELECT * FROM evidence WHERE {column} = ? ORDER BY submitted_at, rowid", (value,))
        return [_evidence(r) for r in rows]

    def set_evidence_validation(self, results: list[tuple[str, str, str]]) -> None:
        """Persist (status, message, evidence_id) validation outcomes from a verification run."""
        with self._connect() as conn:
            conn.executemany(
                "UPDATE evidence SET validation_status = ?, validation_message = ? WHERE id = ?", results
            )

    def delete_evidence(self, evidence_id: str) -> None:
        self._delete("evidence", evidence_id)

    # Audit history (append-only)
    def add_audit(
        self,
        *,
        project_id: str,
        at: str,
        action: str,
        entity_type: str,
        entity_id: str,
        actor: str,
        previous_state: str | None = None,
        new_state: str | None = None,
        reason: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (project_id, at, action, entity_type, entity_id, previous_state, "
                "new_state, actor, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, at, action, entity_type, entity_id, previous_state, new_state, actor, reason),
            )

    def list_audit(self, project_id: str, entity_id: str | None = None) -> list[AuditEntry]:
        sql, params = "SELECT * FROM audit_log WHERE project_id = ?", [project_id]
        if entity_id:
            sql += " AND entity_id = ?"
            params.append(entity_id)
        return [AuditEntry(**dict(r)) for r in self._fetch(sql + " ORDER BY seq", tuple(params))]

    # Verification runs (operational metrics)
    def add_run(self, **values: object) -> None:
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with self._connect() as conn:
            conn.execute(f"INSERT INTO verification_runs ({columns}) VALUES ({marks})", list(values.values()))

    def list_runs(self, project_id: str) -> list[dict[str, object]]:
        rows = self._fetch("SELECT * FROM verification_runs WHERE project_id = ? ORDER BY id DESC", (project_id,))
        return [dict(r) for r in rows]


class EvidenceStore:
    """Stores evidence files under generated names inside one directory; never executes or serves them."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, stored_name: str) -> Path:
        path = (self._root / stored_name).resolve()
        if path == self._root or not path.is_relative_to(self._root):
            raise ValidationError("Evidence path is outside the evidence store.")
        return path

    def save(self, project_id: str, evidence_id: str, extension: str, content: bytes) -> str:
        stored_name = f"{project_id}/{evidence_id}{extension}"
        path = self._resolve(stored_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return stored_name

    def read(self, stored_name: str | None) -> bytes | None:
        if not stored_name:
            return None
        try:
            return self._resolve(stored_name).read_bytes()
        except (OSError, ValidationError):
            return None

    def delete(self, stored_name: str | None) -> None:
        if stored_name:
            self._resolve(stored_name).unlink(missing_ok=True)

    def delete_project(self, project_id: str) -> None:
        shutil.rmtree(self._resolve(project_id), ignore_errors=True)

    def is_writable(self) -> bool:
        probe = self._root / ".write-probe"
        try:
            probe.write_bytes(b"")
            probe.unlink()
            return True
        except OSError:
            return False
