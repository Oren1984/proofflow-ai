"""Environment-based configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LLM_MODEL = "claude-opus-5-5"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    max_upload_bytes: int = 5 * 1024 * 1024
    ai_mode: str = "mock"  # "mock" (default) or "llm"
    llm_api_key: str | None = field(default=None, repr=False)
    llm_model: str = DEFAULT_LLM_MODEL
    llm_timeout_s: float = 20.0
    seed_demo: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "proofflow.db"

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"


def _positive(value: str, default: float) -> float:
    try:
        number = float(value)
    except ValueError:
        return default
    return number if number > 0 else default


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    ai_mode = env.get("PROOFFLOW_AI_MODE", "mock").strip().lower()
    return Settings(
        data_dir=Path(env.get("PROOFFLOW_DATA_DIR", "data")).resolve(),
        max_upload_bytes=int(_positive(env.get("PROOFFLOW_MAX_UPLOAD_MB", "5"), 5) * 1024 * 1024),
        ai_mode=ai_mode if ai_mode in ("mock", "llm") else "mock",
        llm_api_key=env.get("ANTHROPIC_API_KEY") or None,
        llm_model=env.get("PROOFFLOW_LLM_MODEL", DEFAULT_LLM_MODEL),
        llm_timeout_s=_positive(env.get("PROOFFLOW_LLM_TIMEOUT_S", "20"), 20.0),
        seed_demo=env.get("PROOFFLOW_SEED_DEMO", "true").strip().lower() != "false",
    )
