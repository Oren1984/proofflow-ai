from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.config import Settings
from app.models import Commitment, Criterion, EvidenceType, Project
from app.services import Services, build_services


@pytest.fixture
def services(tmp_path) -> Services:
    return build_services(Settings(data_dir=tmp_path / "data", max_upload_bytes=1024 * 1024, seed_demo=False))


@dataclass
class Seeded:
    project: Project
    commitment: Commitment
    criterion: Criterion


@pytest.fixture
def seeded(services: Services) -> Seeded:
    """One project with one commitment and one screenshot criterion that requires human review."""
    project = services.projects.create_project("Test Project", "Test Customer")
    commitment = services.projects.add_commitment(project.id, "Deliver dashboard")
    criterion = services.projects.add_criterion(commitment.id, "Dashboard loads", [EvidenceType.SCREENSHOT])
    return Seeded(project, commitment, criterion)
