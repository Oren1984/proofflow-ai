"""Optional advisory assistance. The verification engine never imports this package."""

from __future__ import annotations

from app.ai.interface import Assistant
from app.ai.llm_adapter import LLMAssistant
from app.ai.mock_adapter import MockAssistant
from app.config import Settings


def build_assistant(settings: Settings) -> Assistant:
    """Mock by default; the LLM assistant only when explicitly configured with a key."""
    if settings.ai_mode == "llm" and settings.llm_api_key:
        return LLMAssistant(settings.llm_api_key, settings.llm_model, settings.llm_timeout_s)
    return MockAssistant()
