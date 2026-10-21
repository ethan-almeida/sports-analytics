from __future__ import annotations

from app.core.settings import Settings
from app.llm.base import LLMClient
from app.llm.fake import FakeLLM


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_provider == "fake":
        return FakeLLM()
    raise ValueError(f"unknown ORC_LLM_PROVIDER: {settings.llm_provider}")
