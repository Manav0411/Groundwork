import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db import session as db_session


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


@pytest.fixture(autouse=True)
def offline_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the database at an unreachable port for every unit test.

    Same hazard as `offline_llm`, and it was still open. `get_optional_session` yields `None` when
    Postgres is unreachable, which is what makes this tier hermetic — but "unreachable" was left to
    chance. With a local Postgres running and DATABASE_URL exported, these tests reached the real
    corpus, and `test_query_on_real_project_never_fabricates_evidence` failed because a project
    with genuine indexed evidence returned genuine citations. The assertion was right; the tier was
    not isolated.

    A test tier whose result depends on what the developer has running is not a test tier. The
    database-backed contract belongs to `tests/integration/`, which asks for a URL explicitly.
    """
    # The engine and factory are module-level singletons built at import time, so changing the
    # setting alone would not move an engine that already exists. Replace the factory instead.
    offline_engine = create_async_engine("postgresql+asyncpg://x:x@127.0.0.1:1/x")
    monkeypatch.setattr(
        db_session, "SessionFactory", async_sessionmaker(offline_engine, expire_on_commit=False)
    )
