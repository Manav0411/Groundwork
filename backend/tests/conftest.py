import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def offline_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the LLM client at an unreachable port for every unit test.

    These tests exercise the deterministic fallback paths. Without this they quietly become
    dependent on whether the developer happens to have Ollama running: with it up, the suite made
    real 8B generation calls and took 26s instead of 0.4s, and the test asserting "the fallback is
    used when Ollama is unavailable" passed only by coincidence. Model behaviour is measured by the
    eval harnesses, not here.
    """
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:1", raising=False)
    monkeypatch.setattr(settings, "llm_timeout_seconds", 1.0, raising=False)
    monkeypatch.setattr(settings, "grader_timeout_seconds", 1.0, raising=False)
